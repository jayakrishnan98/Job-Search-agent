#!/usr/bin/env python3
import argparse
import logging
import sys
import time

from config import (
    CHECK_INTERVAL_MINUTES,
    EMAIL_MIN_SCORE,
    LOGS_DIR,
    USER_PROFILE,
    get_email_config_issue,
    is_ai_configured,
    is_email_configured,
    resolve_ai_provider,
)
from jobs.database import init_db
from jobs.job_fetcher import fetch_all_jobs
from jobs.job_filter import filter_seen_jobs
from jobs.job_store import upsert_jobs
from notifications.dispatch import notify_high_score_new_jobs
from notifications.email_notifier import send_test_email
from notifications import email_status


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "job_agent.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def print_dashboard(with_ai: bool) -> None:
    companies = USER_PROFILE.get("target_companies") or ["All companies"]
    roles = ", ".join(USER_PROFILE.get("target_roles", []))
    mode = "Discover + AI scoring" if with_ai else "Discover + Email alerts"

    print("\n" + "=" * 50)
    print("  JOB AGENT — Job Alert System")
    print("=" * 50)
    print(f"  Profile  : {USER_PROFILE.get('name', 'Unknown')}")
    print(f"  Roles    : {roles}")
    print(f"  Location : {USER_PROFILE.get('location', 'Unknown')}")
    print(f"  Companies: {len(companies)} watched")
    print(f"  Mode     : {mode}")
    print(f"  Email    : {'configured' if is_email_configured() else 'NOT configured'}")
    if with_ai:
        print(f"  AI      : {resolve_ai_provider()}")
        print(f"  Threshold: {USER_PROFILE.get('min_match_score', 65)}/100")
    print(f"  Interval : every {CHECK_INTERVAL_MINUTES} min")
    print("  Press Ctrl+C to stop")
    print("=" * 50 + "\n")


def print_new_job(job: dict) -> None:
    print("\n" + "━" * 40)
    print("NEW JOB")
    print(f"Role    : {job.get('title')}")
    print(f"Company : {job.get('company')}")
    print(f"Location: {job.get('location')}")
    print(f"Posted  : {job.get('posted_date')}")
    print(f"Link    : {job.get('job_url')}")
    print("━" * 40)


def print_match(job: dict, result: dict, resume_path: str | None = None) -> None:
    print("\n" + "━" * 40)
    print("MATCH FOUND")
    print(f"Role    : {job.get('title')}")
    print(f"Company : {job.get('company')}")
    print(f"Score   : {result.get('score')}/100")
    if resume_path:
        print(f"Resume  : {resume_path}")
    print(f"Link    : {job.get('job_url')}")
    print("━" * 40)


def run_discover_cycle() -> None:
    logger = logging.getLogger(__name__)
    logger.info("Checking for new jobs...")

    raw_jobs = fetch_all_jobs()
    new_jobs = filter_seen_jobs(raw_jobs)
    new_ids = {j["job_id"] for j in new_jobs}

    result = upsert_jobs(raw_jobs, new_job_ids=new_ids)
    logger.info(
        "Saved to DB: %d jobs (%d new, %d updated).",
        len(raw_jobs),
        result["inserted"],
        result["updated"],
    )

    if not new_jobs:
        logger.info("No new jobs found.")
        return

    for job in new_jobs:
        logger.info("NEW: %s at %s", job.get("title"), job.get("company"))
        print_new_job(job)

    if is_ai_configured():
        logger.info("Email alerts wait until new jobs are scored (%d+).", EMAIL_MIN_SCORE)
    else:
        logger.info("Skipping email — alerts require AI scores of %d+.", EMAIL_MIN_SCORE)


def run_ai_cycle() -> None:
    from ai.job_scorer import score_unscored_jobs

    logger = logging.getLogger(__name__)
    logger.info("Checking for new jobs (AI mode)...")

    raw_jobs = fetch_all_jobs()
    new_jobs = filter_seen_jobs(raw_jobs)
    new_ids = {j["job_id"] for j in new_jobs}

    result = upsert_jobs(raw_jobs, new_job_ids=new_ids)
    logger.info(
        "Saved to DB: %d jobs (%d new, %d updated).",
        len(raw_jobs),
        result["inserted"],
        result["updated"],
    )

    if new_jobs:
        for job in new_jobs:
            logger.info("NEW: %s at %s", job.get("title"), job.get("company"))
            print_new_job(job)

    logger.info("Scoring unscored jobs...")
    summary = score_unscored_jobs()
    logger.info(
        "Scoring finished — scored=%s failed=%s remaining=%s",
        summary.get("scored"),
        summary.get("failed"),
        summary.get("remaining"),
    )
    notify_high_score_new_jobs()


def run_cycle(with_ai: bool = False) -> None:
    if with_ai:
        run_ai_cycle()
    else:
        run_discover_cycle()


def main() -> None:
    parser = argparse.ArgumentParser(description="Job Alert + ATS Resume System")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check cycle and exit (for testing)",
    )
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="Enable AI scoring and ATS resume rewriting (requires GEMINI_API_KEY or CLAUDE_API_KEY)",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email and exit",
    )
    args = parser.parse_args()

    if args.with_ai and not is_ai_configured():
        print("Error: --with-ai requires GEMINI_API_KEY or CLAUDE_API_KEY in .env")
        sys.exit(1)

    setup_logging()
    init_db()

    if args.test_email:
        if not is_email_configured():
            issue = get_email_config_issue() or "Email not configured"
            print(f"Email not configured: {issue}")
            sys.exit(1)
        ok = send_test_email()
        if ok:
            print("Test email sent.")
        else:
            status = email_status.get_status()
            print("Test email failed.")
            if status.get("error"):
                print(f"Reason: {status['error']}")
            print(
                "\nQuick fixes (add ONE to .env, then restart the API server):\n"
                "  1. Gmail:  GMAIL_APP_PASSWORD=...  (https://myaccount.google.com/apppasswords)\n"
                "  2. Maileroo API: MAILEROO_API_KEY=...  (Maileroo dashboard → Sending Keys)\n"
                "  3. Maileroo SMTP: SMTP_HOST=smtp.maileroo.com + SMTP_USER + SMTP_PASSWORD"
            )
        sys.exit(0 if ok else 1)

    print_dashboard(with_ai=args.with_ai)

    if args.once:
        run_cycle(with_ai=args.with_ai)
        return

    while True:
        try:
            run_cycle(with_ai=args.with_ai)
            logging.info("Sleeping %d minutes...", CHECK_INTERVAL_MINUTES)
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
