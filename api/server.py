import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import (
    CHECK_INTERVAL_MINUTES,
    EXPERIENCE_MAX,
    EXPERIENCE_MIN,
    EXPERIENCE_YEARS,
    FILTER_BY_EDUCATION,
    FILTER_BY_EXPERIENCE,
    MAX_EDUCATION,
    USER_PROFILE,
    email_transport_hint,
    get_email_config_issue,
    is_ai_configured,
    is_email_configured,
    resolve_ai_provider,
)
from jobs.database import init_db, migrate_json_if_needed
from jobs.job_fetcher import fetch_all_jobs
from jobs.job_store import (
    delete_job,
    get_all_jobs,
    get_cover_letter_file,
    get_job,
    get_resume_file,
    get_store_meta,
    mark_jobs_seen,
    purge_mismatched_jobs,
    set_job_applied,
    upsert_jobs,
)
from notifications.dispatch import notify_high_score_new_jobs
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
_ai_locks_guard = threading.Lock()
_ai_locks: dict[str, threading.Lock] = {}
_score_lock = threading.Lock()
_score_status = {
    "running": False,
    "scored": 0,
    "remaining": 0,
    "error": None,
}


def _job_lock(job_id: str) -> threading.Lock:
    with _ai_locks_guard:
        return _ai_locks.setdefault(job_id, threading.Lock())


def _score_status_payload() -> dict:
    return dict(_score_status)


def _run_scoring() -> None:
    if not is_ai_configured():
        return
    if not _score_lock.acquire(blocking=False):
        return
    try:
        from ai.job_scorer import score_unscored_jobs
        from jobs.job_store import count_unscored_jobs

        remaining = count_unscored_jobs()
        if remaining == 0:
            _score_status["running"] = False
            _score_status["remaining"] = 0
            _score_status["error"] = None
            notify_high_score_new_jobs()
            return

        _score_status["running"] = True
        _score_status["error"] = None
        _score_status["remaining"] = remaining
        logger.info("Scoring %d unscored jobs", remaining)
        result = score_unscored_jobs(progress=_score_status)
        logger.info(
            "Scoring finished — scored=%s failed=%s remaining=%s",
            result.get("scored"),
            result.get("failed"),
            result.get("remaining"),
        )
        notify_high_score_new_jobs()
    except Exception:
        logger.exception("Background scoring failed")
        _score_status["error"] = "Scoring failed"
    finally:
        _score_status["running"] = False
        _score_lock.release()


def start_background_scoring() -> None:
    if not is_ai_configured():
        return
    thread = threading.Thread(target=_run_scoring, daemon=True, name="job-scorer")
    thread.start()


def _public_job(job: dict | None) -> dict | None:
    if job is None:
        return None
    cleaned = dict(job)
    cleaned.pop("description", None)
    cleaned.pop("resume_path", None)
    cleaned.pop("cover_letter_path", None)
    cleaned.pop("email_sent", None)
    return cleaned


class AppliedPayload(BaseModel):
    applied: bool


class GenerateFromJdPayload(BaseModel):
    title: str
    company: str
    description: str
    location: str = ""


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
            removed = purge_mismatched_jobs()
            if removed:
                logger.info("Removed %d stored jobs that do not match the profile", removed)
            new_jobs = result.get("new_jobs", [])

            emailed = False
            if new_jobs:
                logger.info(
                    "Deferring email until scoring finishes (%d new job%s)",
                    len(new_jobs),
                    "s" if len(new_jobs) != 1 else "",
                )

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
            start_background_scoring()
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
    removed = purge_mismatched_jobs()
    if removed:
        logger.info("Removed %d stored jobs that do not match the profile", removed)
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
    start_background_scoring()

    yield

    stop_background_polling()


app = FastAPI(title="Job Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-ATS-Score", "Content-Disposition"],
)


@app.get("/api/jobs/status")
def jobs_status():
    meta = get_store_meta()
    return {
        "updated_at": meta["updated_at"],
        "new_count": meta["new_count"],
        "total": meta["total"],
        "applied_count": meta.get("applied_count", 0),
        "scored_count": meta.get("scored_count", 0),
        "unscored_count": meta.get("unscored_count", 0),
        "scoring": _score_status_payload(),
    }


@app.get("/api/jobs")
def list_jobs(
    company: str | None = Query(None, description="Filter by company name"),
    search: str | None = Query(None, description="Search title or company"),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    source: str | None = Query(None, description="Filter by source: linkedin, greenhouse, etc."),
    applied: str = Query("open", pattern="^(open|applied|all)$"),
):
    applied_filter = None if applied == "all" else applied == "applied"
    jobs = get_all_jobs(
        company=company,
        search=search,
        sort=sort,
        source=source,
        applied=applied_filter,
    )
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
            "max_education": MAX_EDUCATION,
            "filter_by_education": FILTER_BY_EDUCATION,
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
        "ai": {
            "configured": is_ai_configured(),
            "provider": resolve_ai_provider(),
        },
        "scoring": _score_status_payload(),
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


@app.post("/api/jobs/{job_id}/applied")
def mark_applied(job_id: str, payload: AppliedPayload):
    job = set_job_applied(job_id, payload.applied)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "ok", "job": _public_job(job)}


@app.delete("/api/jobs/{job_id:path}")
def remove_job(job_id: str):
    job = delete_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    removed_ids = job.pop("removed_ids", [job_id])
    return {"status": "ok", "job": _public_job(job), "removed_ids": removed_ids}


def _require_ai() -> None:
    if not is_ai_configured():
        raise HTTPException(
            status_code=400,
            detail="AI is not configured. Set GEMINI_API_KEY, OPENAI_API_KEY, or CLAUDE_API_KEY in .env",
        )


@app.post("/api/jobs/{job_id}/score")
def score_one_job(job_id: str):
    _require_ai()
    job = get_job(job_id, include_description=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    from ai.job_scorer import score_job
    from ai.llm import AINotConfiguredError

    with _job_lock(job_id):
        try:
            score_job(job, persist=True)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AINotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=400 if "401" in str(exc) else 502,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.exception("Scoring failed for %s", job_id)
            raise HTTPException(status_code=502, detail=f"Scoring failed: {exc}") from exc

    return {"status": "ok", "job": _public_job(get_job(job_id))}


@app.post("/api/jobs/{job_id}/resume")
def generate_resume(job_id: str):
    _require_ai()
    job = get_job(job_id, include_description=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    from ai.job_scorer import score_job
    from ai.llm import AINotConfiguredError
    from ai.resume_builder import build_resume

    with _job_lock(job_id):
        try:
            if job.get("ai_score") is None:
                score_result = score_job(job, persist=True)
            else:
                score_result = {
                    "score": job.get("ai_score"),
                    "job_description": job.get("description") or "",
                }
            build_resume(job, score_result, persist=True)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AINotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=400 if "401" in str(exc) else 502,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.exception("Resume generation failed for %s", job_id)
            raise HTTPException(
                status_code=502, detail=f"Resume generation failed: {exc}"
            ) from exc

    return {"status": "ok", "job": _public_job(get_job(job_id))}


@app.get("/api/jobs/{job_id}/resume")
def download_resume(job_id: str, format: str = Query("pdf", pattern="^pdf$")):
    path = get_resume_file(job_id, format)
    if path is None:
        job = get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(
            status_code=404,
            detail="No tailored resume yet. Generate one first.",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
    )


@app.post("/api/jobs/{job_id}/cover-letter")
def generate_cover_letter(job_id: str):
    _require_ai()
    job = get_job(job_id, include_description=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    from ai.cover_letter import build_cover_letter
    from ai.llm import AINotConfiguredError

    with _job_lock(job_id):
        try:
            build_cover_letter(job, persist=True)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AINotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=400 if "401" in str(exc) else 502,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.exception("Cover letter generation failed for %s", job_id)
            raise HTTPException(
                status_code=502, detail=f"Cover letter generation failed: {exc}"
            ) from exc

    return {"status": "ok", "job": _public_job(get_job(job_id))}


@app.get("/api/jobs/{job_id}/cover-letter")
def download_cover_letter(job_id: str, format: str = Query("pdf", pattern="^pdf$")):
    path = get_cover_letter_file(job_id, format)
    if path is None:
        job = get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(
            status_code=404,
            detail="No tailored cover letter yet. Generate one first.",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
    )


def _adhoc_job(payload: GenerateFromJdPayload) -> dict:
    title = payload.title.strip()
    company = payload.company.strip()
    description = payload.description.strip()
    if not title or not company or not description:
        raise HTTPException(
            status_code=400,
            detail="Title, company, and job description are required",
        )
    return {
        "title": title,
        "company": company,
        "location": (payload.location or "").strip(),
        "description": description,
        "job_url": "",
        "source": "adhoc",
    }


def _adhoc_http_error(exc: Exception, *, action: str, logger_label: str) -> NoReturn:
    from ai.llm import AINotConfiguredError

    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, AINotConfiguredError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(
            status_code=400 if "401" in str(exc) else 502,
            detail=str(exc),
        ) from exc
    logger.exception("%s failed", logger_label)
    raise HTTPException(status_code=502, detail=f"{action} failed: {exc}") from exc


@app.post("/api/generate/resume")
def generate_resume_from_jd(payload: GenerateFromJdPayload):
    _require_ai()
    job = _adhoc_job(payload)

    from ai.resume_builder import build_resume, read_resume_meta

    try:
        path = build_resume(
            job,
            {"score": None, "job_description": job["description"]},
            persist=False,
            use_cache=False,
        )
    except Exception as exc:
        _adhoc_http_error(
            exc,
            action="Resume generation",
            logger_label="Ad-hoc resume generation",
        )

    headers = {}
    ats_score = read_resume_meta(path).get("ats_score")
    if ats_score is not None:
        headers["X-ATS-Score"] = str(ats_score)

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=Path(path).name,
        headers=headers,
    )


@app.post("/api/generate/cover-letter")
def generate_cover_letter_from_jd(payload: GenerateFromJdPayload):
    _require_ai()
    job = _adhoc_job(payload)

    from ai.cover_letter import build_cover_letter

    try:
        path = build_cover_letter(job, persist=False, use_cache=False)
    except Exception as exc:
        _adhoc_http_error(
            exc,
            action="Cover letter generation",
            logger_label="Ad-hoc cover letter generation",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=Path(path).name,
    )


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
