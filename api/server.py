import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from config import CHECK_INTERVAL_MINUTES, USER_PROFILE, email_transport_hint, get_email_config_issue, is_email_configured, EXPERIENCE_YEARS, EXPERIENCE_MIN, EXPERIENCE_MAX, FILTER_BY_EXPERIENCE
from jobs.database import init_db, migrate_json_if_needed
from jobs.job_fetcher import fetch_all_jobs
from jobs.job_store import get_all_jobs, get_store_meta, mark_jobs_seen, upsert_jobs
from notifications.dispatch import notify_new_jobs
from notifications.email_notifier import send_test_email
from notifications import email_status

logger = logging.getLogger(__name__)

_fetch_lock = threading.Lock()
_fetch_status = {
    "running": False,
    "last_error": None,
    "last_count": 0,
    "last_new": 0,
    "last_fetch_at": None,
    "next_fetch_at": None,
}
_poll_stop = threading.Event()
_poll_thread: threading.Thread | None = None
_last_fetch_finished_at: float | None = None


def _seconds_until_next_fetch() -> float:
    if _last_fetch_finished_at is None:
        return 0
    elapsed = time.monotonic() - _last_fetch_finished_at
    return max(0, CHECK_INTERVAL_MINUTES * 60 - elapsed)


def run_fetch(*, force: bool = False) -> dict:
    wait = _seconds_until_next_fetch()
    if not force and wait > 0:
        return {
            "status": "skipped",
            "message": f"Fetch runs every {CHECK_INTERVAL_MINUTES} minutes",
            "next_fetch_in_seconds": int(wait),
        }

    with _fetch_lock:
        if _fetch_status["running"]:
            return {"status": "already_running"}

        _fetch_status["running"] = True
        _fetch_status["last_error"] = None

        try:
            raw_jobs = fetch_all_jobs()
            result = upsert_jobs(raw_jobs)
            new_jobs = result.get("new_jobs", [])

            emailed = False
            if new_jobs:
                emailed = notify_new_jobs(new_jobs)

            now = datetime.now(timezone.utc)
            global _last_fetch_finished_at
            _last_fetch_finished_at = time.monotonic()

            _fetch_status["last_count"] = len(raw_jobs)
            _fetch_status["last_new"] = result["inserted"]
            _fetch_status["last_fetch_at"] = now.isoformat()
            _fetch_status["next_fetch_at"] = (
                now + timedelta(minutes=CHECK_INTERVAL_MINUTES)
            ).isoformat()

            logger.info(
                "Fetched %d jobs — %d new, %d updated, %d deduped, email=%s",
                len(raw_jobs),
                result["inserted"],
                result["updated"],
                result.get("skipped", 0),
                emailed,
            )
            return {
                "status": "ok",
                "total": len(raw_jobs),
                "new": result["inserted"],
                "updated": result["updated"],
                "skipped": result.get("skipped", 0),
                "emailed": emailed,
                "next_fetch_in_seconds": CHECK_INTERVAL_MINUTES * 60,
            }
        except Exception as exc:
            _fetch_status["last_error"] = str(exc)
            logger.exception("Fetch failed")
            return {"status": "error", "message": str(exc)}
        finally:
            _fetch_status["running"] = False


def _poll_loop() -> None:
    logger.info("Scheduled job fetch every %d minutes", CHECK_INTERVAL_MINUTES)
    while not _poll_stop.is_set():
        try:
            run_fetch()
        except Exception:
            logger.exception("Scheduled fetch error")

        if _poll_stop.wait(CHECK_INTERVAL_MINUTES * 60):
            break

    logger.info("Scheduled job fetch stopped")


def start_background_polling() -> None:
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="job-poller")
    _poll_thread.start()


def stop_background_polling() -> None:
    _poll_stop.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    init_db()
    migrate_json_if_needed()
    logger.info("SQLite database ready")

    issue = get_email_config_issue()
    if issue:
        email_status.record_failure("none", issue)
        logger.warning("Email alerts disabled: %s", issue)
    elif send_test_email():
        logger.info("Test email sent successfully on startup")
    else:
        logger.warning("Test email failed on startup — check credentials in .env")

    start_background_polling()

    yield

    stop_background_polling()


app = FastAPI(title="Job Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/jobs/status")
def jobs_status():
    meta = get_store_meta()
    return {
        "updated_at": meta["updated_at"],
        "new_count": meta["new_count"],
        "total": meta["total"],
    }


@app.get("/api/jobs")
def list_jobs(
    company: str | None = Query(None, description="Filter by company name"),
    search: str | None = Query(None, description="Search title or company"),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    source: str | None = Query(None, description="Filter by source: linkedin, greenhouse, etc."),
):
    jobs = get_all_jobs(company=company, search=search, sort=sort, source=source)
    meta = get_store_meta()
    return {"jobs": jobs, **meta}


@app.get("/api/companies")
def list_companies():
    return {"companies": get_store_meta()["companies"]}


@app.get("/api/meta")
def meta():
    return {
        **get_store_meta(),
        "profile": {
            "name": USER_PROFILE.get("name"),
            "roles": USER_PROFILE.get("target_roles", []),
            "location": USER_PROFILE.get("location"),
            "companies_count": len(USER_PROFILE.get("target_companies", [])),
            "experience_years": EXPERIENCE_YEARS,
            "experience_range": f"{EXPERIENCE_MIN}-{EXPERIENCE_MAX} years",
            "filter_by_experience": FILTER_BY_EXPERIENCE,
        },
        "fetch": {
            **_fetch_status,
            "next_fetch_in_seconds": int(_seconds_until_next_fetch()),
        },
        "poll_interval_minutes": CHECK_INTERVAL_MINUTES,
        "email_configured": is_email_configured(),
        "email_config_issue": get_email_config_issue(),
        "email_transport": email_transport_hint(),
        "email_status": email_status.get_status(),
    }


@app.post("/api/fetch")
def trigger_fetch(background_tasks: BackgroundTasks):
    if _fetch_status["running"]:
        return {"status": "already_running"}

    background_tasks.add_task(run_fetch)
    return {"status": "started"}


@app.post("/api/fetch/sync")
def trigger_fetch_sync():
    return run_fetch()


@app.post("/api/jobs/mark-read")
def mark_read():
    count = mark_jobs_seen()
    return {"status": "ok", "marked": count}


@app.post("/api/email/test")
def test_email():
    ok = send_test_email()
    status = email_status.get_status()
    return {
        "status": "ok" if ok else "error",
        "transport": status.get("transport"),
        "error": status.get("error"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=True)
