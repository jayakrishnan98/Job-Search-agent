import logging
from pathlib import Path

from config import (
    EMAIL_ATTACH_MATERIALS,
    EMAIL_MAX_POSTED_DAYS,
    EMAIL_MIN_SCORE,
    MASTER_RESUME_PATH,
    get_email_config_issue,
    is_ai_configured,
    is_email_configured,
)
from notifications.email_notifier import send_new_jobs_email

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS_PER_EMAIL = 32
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def notify_new_jobs(jobs: list[dict]) -> bool:
    if not jobs:
        return False
    if not is_email_configured():
        issue = get_email_config_issue() or "Email not configured"
        logger.warning("Email not configured — skipping notification: %s", issue)
        return False
    return send_new_jobs_email(jobs)


def _attachment_filename(job: dict, kind: str, used: set[str]) -> str:
    from ai.resume_builder import _safe_filename

    company = _safe_filename(job.get("company", "Unknown"))
    title = _safe_filename(job.get("title", "Unknown"))
    base = f"{company}_{title}_{kind}.pdf"
    if base not in used:
        used.add(base)
        return base
    job_id = str(job.get("job_id") or "job").replace("/", "_")[:16]
    name = f"{company}_{title}_{job_id}_{kind}.pdf"
    suffix = 2
    while name in used:
        name = f"{company}_{title}_{job_id}_{suffix}_{kind}.pdf"
        suffix += 1
    used.add(name)
    return name


def _existing_pdf(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        if path.exists() and path.is_file():
            return path
    except OSError:
        return None
    return None


def _prepare_email_materials(jobs: list[dict]) -> None:
    if not is_ai_configured():
        logger.warning(
            "EMAIL_ATTACH_MATERIALS is on but AI is not configured — sending without attachments"
        )
        return

    if not MASTER_RESUME_PATH.exists():
        logger.warning(
            "EMAIL_ATTACH_MATERIALS is on but master resume was not found at %s — sending without attachments",
            MASTER_RESUME_PATH,
        )
        return

    from ai.cover_letter import build_cover_letter
    from ai.resume_builder import build_resume
    from jobs.job_store import get_cover_letter_file, get_job, get_resume_file

    used_names: set[str] = set()
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue

        full = get_job(job_id, include_description=True) or job
        attachments: list[tuple[str, Path]] = []

        resume_path = _existing_pdf(get_resume_file(job_id))
        if resume_path is None:
            try:
                score_result = {
                    "score": full.get("ai_score"),
                    "job_description": full.get("description") or "",
                }
                generated = build_resume(full, score_result, persist=True)
                resume_path = _existing_pdf(Path(generated) if generated else None)
            except Exception as exc:
                logger.warning(
                    "Failed to build resume for %s (%s): %s",
                    full.get("title"),
                    job_id,
                    exc,
                )

        cover_path = _existing_pdf(get_cover_letter_file(job_id))
        if cover_path is None:
            try:
                generated = build_cover_letter(full, persist=True)
                cover_path = _existing_pdf(Path(generated) if generated else None)
            except Exception as exc:
                logger.warning(
                    "Failed to build cover letter for %s (%s): %s",
                    full.get("title"),
                    job_id,
                    exc,
                )

        if resume_path is not None:
            attachments.append(
                (_attachment_filename(full, "resume", used_names), resume_path)
            )
        if cover_path is not None:
            attachments.append(
                (_attachment_filename(full, "cover_letter", used_names), cover_path)
            )

        job["_email_attachments"] = attachments

    attached = sum(len(job.get("_email_attachments") or []) for job in jobs)
    logger.info(
        "Prepared %d attachment(s) for %d high-score job alert(s)",
        attached,
        len(jobs),
    )


def _attachment_stats(job: dict) -> tuple[int, int]:
    attachments = job.get("_email_attachments") or []
    total = 0
    count = 0
    for _name, path in attachments:
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


def _chunk_jobs_for_email(jobs: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_count = 0
    current_bytes = 0
    for job in jobs:
        count, size = _attachment_stats(job)
        would_overflow = current and (
            current_count + count > MAX_ATTACHMENTS_PER_EMAIL
            or current_bytes + size > MAX_ATTACHMENT_BYTES
        )
        if would_overflow:
            chunks.append(current)
            current = []
            current_count = 0
            current_bytes = 0
        current.append(job)
        current_count += count
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def notify_high_score_new_jobs() -> bool:
    """Email newly listed jobs that scored at or above EMAIL_MIN_SCORE."""
    if not is_email_configured():
        return False

    from jobs.job_store import get_unnotified_high_score_jobs, mark_jobs_emailed

    jobs = get_unnotified_high_score_jobs(
        EMAIL_MIN_SCORE, max_posted_days=EMAIL_MAX_POSTED_DAYS
    )
    if not jobs:
        return False

    if EMAIL_ATTACH_MATERIALS:
        _prepare_email_materials(jobs)

    any_ok = False
    for chunk in _chunk_jobs_for_email(jobs):
        ok = notify_new_jobs(chunk)
        if ok:
            mark_jobs_emailed([job["job_id"] for job in chunk if job.get("job_id")])
            age_note = (
                f" posted within {EMAIL_MAX_POSTED_DAYS} day(s)"
                if EMAIL_MAX_POSTED_DAYS > 0
                else ""
            )
            logger.info(
                "Emailed %d new job(s) scoring %d+%s",
                len(chunk),
                EMAIL_MIN_SCORE,
                age_note,
            )
            any_ok = True
    return any_ok
