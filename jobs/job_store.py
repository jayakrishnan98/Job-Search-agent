import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobs.database import get_connection, init_db
from jobs.linkedin_utils import normalize_linkedin_job_url
from jobs.profile_filter import profile_matches
from jobs.ats.base import listing_fingerprint
from config import FILTER_BY_EDUCATION, FILTER_BY_EXPERIENCE

logger = logging.getLogger(__name__)

_meta_cache: dict | None = None
_meta_cache_at: float = 0.0
META_CACHE_TTL = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aware_now() -> datetime:
    return datetime.now().astimezone()


def _start_of_local_day(now: datetime | None = None) -> datetime:
    current = now or _aware_now()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_posted_at(value) -> datetime | None:
    """Parse a stored posted_date into an aware datetime, or None if unknown."""
    text = str(value or "").strip()
    if not text:
        return None

    now = _aware_now()
    lower = text.lower()

    if re.search(r"today|just posted|just now", lower):
        return _start_of_local_day(now)
    if lower == "yesterday":
        return _start_of_local_day(now) - timedelta(days=1)

    hours = re.search(r"(\d+)\s*hours?\s*ago", lower)
    if hours:
        return now - timedelta(hours=int(hours.group(1)))
    minutes = re.search(r"(\d+)\s*(?:minutes?|mins?|seconds?|secs?)\s*ago", lower)
    if minutes:
        return now
    if re.search(r"\b(?:hour|minute|second)s?\b", lower):
        return now

    plus_days = re.search(r"^(\d+)\+\s*days?\s*ago$", lower)
    if plus_days:
        return _start_of_local_day(now) - timedelta(days=int(plus_days.group(1)) + 1)

    days_ago = re.search(
        r"(?:^|posted\s+)(\d+)\s*d(?:ays?)?\s*ago$",
        lower,
    )
    if days_ago:
        return _start_of_local_day(now) - timedelta(days=int(days_ago.group(1)))

    weeks_ago = re.search(
        r"(?:^|posted\s+)(\d+)\s*w(?:eeks?)?\s*ago$",
        lower,
    )
    if weeks_ago:
        return _start_of_local_day(now) - timedelta(days=int(weeks_ago.group(1)) * 7)

    date_only = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if date_only:
        year, month, day = map(int, date_only.groups())
        try:
            return datetime(year, month, day, tzinfo=now.tzinfo)
        except ValueError:
            return None

    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_posted_within_days(posted_date, days: float) -> bool:
    """True when posted_date falls within the last `days` calendar days.

    With days=1 this includes today and yesterday, and excludes 2+ days ago.
    """
    if days <= 0:
        return True
    posted = parse_posted_at(posted_date)
    if posted is None:
        return False
    now = _aware_now()
    posted_local = posted.astimezone(now.tzinfo)
    return _start_of_local_day(now) - posted_local <= timedelta(days=days)


def invalidate_meta_cache() -> None:
    global _meta_cache
    _meta_cache = None


def _parse_json_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _optional_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_value(row, key, default=""):
    try:
        if key not in row.keys():
            return default
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def _row_to_dict(row) -> dict:
    source = row["source"] if "source" in row.keys() else "linkedin"
    job_url = row["job_url"]
    if source == "linkedin":
        job_url = normalize_linkedin_job_url(job_url, row["job_id"])

    resume_path = str(_row_value(row, "resume_path", "") or "")
    cover_letter_path = str(_row_value(row, "cover_letter_path", "") or "")
    email_sent = bool(_row_value(row, "email_sent", 0))

    return {
        "job_id": row["job_id"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "posted_date": row["posted_date"],
        "job_url": job_url,
        "source": source,
        "source_company": row["source_company"],
        "dedup_hash": row["dedup_hash"] if "dedup_hash" in row.keys() else "",
        "description": row["description"] if "description" in row.keys() else "",
        "first_seen_at": row["first_seen_at"],
        "fetched_at": row["fetched_at"],
        "is_new": bool(row["is_new"]),
        "is_applied": bool(row["is_applied"]) if "is_applied" in row.keys() else False,
        "applied_at": row["applied_at"] if "applied_at" in row.keys() else "",
        "ai_score": _optional_int(_row_value(row, "ai_score", None)),
        "ai_verdict": _row_value(row, "ai_verdict", ""),
        "ai_matched_skills": _parse_json_list(_row_value(row, "ai_matched_skills", "")),
        "ai_missing_skills": _parse_json_list(_row_value(row, "ai_missing_skills", "")),
        "ai_recommendation": _row_value(row, "ai_recommendation", ""),
        "ai_role_score": _optional_int(_row_value(row, "ai_role_score", None)),
        "ai_skills_score": _optional_int(_row_value(row, "ai_skills_score", None)),
        "ai_experience_score": _optional_int(_row_value(row, "ai_experience_score", None)),
        "ai_requirements_score": _optional_int(_row_value(row, "ai_requirements_score", None)),
        "ai_scored_at": _row_value(row, "ai_scored_at", ""),
        "has_resume": bool(resume_path),
        "resume_path": resume_path,
        "ats_score": _optional_int(_row_value(row, "ats_score", None)),
        "has_cover_letter": bool(cover_letter_path),
        "cover_letter_path": cover_letter_path,
        "email_sent": email_sent,
    }


def _dismissed_keys(conn) -> tuple[set[str], set[str], set[str]]:
    rows = conn.execute(
        "SELECT job_id, dedup_hash, fingerprint FROM dismissed_jobs"
    ).fetchall()
    job_ids = {row["job_id"] for row in rows}
    hashes = {row["dedup_hash"] for row in rows if row["dedup_hash"]}
    fingerprints = {
        row["fingerprint"] for row in rows if _row_value(row, "fingerprint", "")
    }
    return job_ids, hashes, fingerprints


def get_existing_job_ids() -> set[str]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
        dismissed_ids, _, _ = _dismissed_keys(conn)
    return {row["job_id"] for row in rows} | dismissed_ids


def get_existing_dedup_hashes() -> set[str]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT dedup_hash FROM jobs WHERE dedup_hash != ''"
        ).fetchall()
        _, dismissed_hashes, _ = _dismissed_keys(conn)
    return {row["dedup_hash"] for row in rows} | dismissed_hashes


def get_existing_fingerprints() -> set[str]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT company, title, location FROM jobs").fetchall()
        _, _, dismissed_fps = _dismissed_keys(conn)
    fingerprints = set(dismissed_fps)
    for row in rows:
        fp = listing_fingerprint(row["company"], row["title"], row["location"])
        if fp:
            fingerprints.add(fp)
    return fingerprints


def get_all_jobs(
    company: str | None = None,
    search: str | None = None,
    sort: str = "newest",
    source: str | None = None,
    applied: bool | None = False,
) -> list[dict]:
    init_db()
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []

    if applied is False:
        query += " AND COALESCE(is_applied, 0) = 0"
    elif applied is True:
        query += " AND is_applied = 1"

    if company and company != "all":
        query += " AND company = ?"
        params.append(company)

    if source and source != "all":
        query += " AND source = ?"
        params.append(source)

    if search:
        query += " AND (LOWER(title) LIKE ? OR LOWER(company) LIKE ?)"
        term = f"%{search.lower()}%"
        params.extend([term, term])

    if sort == "oldest":
        query += " ORDER BY posted_date ASC, first_seen_at ASC"
    else:
        query += " ORDER BY posted_date DESC, first_seen_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    jobs = [_row_to_dict(row) for row in rows]
    if FILTER_BY_EXPERIENCE or FILTER_BY_EDUCATION:
        jobs = [
            job for job in jobs if job.get("is_applied") or profile_matches(job)
        ]
    for job in jobs:
        job.pop("description", None)
        job.pop("resume_path", None)
        job.pop("cover_letter_path", None)
        job.pop("email_sent", None)
    return jobs


def get_store_meta() -> dict:
    global _meta_cache, _meta_cache_at

    now = time.monotonic()
    if _meta_cache is not None and (now - _meta_cache_at) < META_CACHE_TTL:
        return dict(_meta_cache)

    init_db()
    with get_connection() as conn:
        stats = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN COALESCE(is_applied, 0) = 0 THEN 1 ELSE 0 END), 0) AS total,
                COALESCE(SUM(CASE WHEN is_applied = 1 THEN 1 ELSE 0 END), 0) AS applied_count,
                COALESCE(SUM(CASE WHEN is_new = 1 AND COALESCE(is_applied, 0) = 0 THEN 1 ELSE 0 END), 0) AS new_count,
                COALESCE(SUM(CASE WHEN ai_score IS NOT NULL AND COALESCE(is_applied, 0) = 0 THEN 1 ELSE 0 END), 0) AS scored_count,
                COALESCE(SUM(CASE WHEN ai_score IS NULL AND COALESCE(is_applied, 0) = 0 THEN 1 ELSE 0 END), 0) AS unscored_count,
                MAX(fetched_at) AS updated_at
            FROM jobs
            """
        ).fetchone()
        companies = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT company FROM jobs WHERE company != '' ORDER BY company"
            ).fetchall()
        ]
        sources = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source FROM jobs WHERE source != '' ORDER BY source"
            ).fetchall()
        ]

    result = {
        "updated_at": stats["updated_at"],
        "total": stats["total"],
        "applied_count": stats["applied_count"],
        "new_count": stats["new_count"],
        "scored_count": stats["scored_count"],
        "unscored_count": stats["unscored_count"],
        "companies": companies,
        "sources": sources,
    }
    _meta_cache = result
    _meta_cache_at = now
    return dict(result)


def upsert_jobs(jobs: list[dict], new_job_ids: set[str] | None = None) -> dict:
    init_db()
    if not jobs:
        return {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "total_processed": 0,
            "new_jobs": [],
        }

    now = _now()
    inserted = 0
    updated = 0
    skipped = 0
    new_jobs: list[dict] = []
    new_job_ids = new_job_ids or set()

    with get_connection() as conn:
        existing_rows = conn.execute(
            "SELECT job_id, is_new, dedup_hash, description FROM jobs"
        ).fetchall()
        existing_by_id = {row["job_id"]: row for row in existing_rows}
        dedup_to_id = {
            row["dedup_hash"]: row["job_id"]
            for row in existing_rows
            if row["dedup_hash"]
        }
        dismissed_ids, dismissed_hashes, dismissed_fps = _dismissed_keys(conn)

        for job in jobs:
            job_id = job.get("job_id")
            if not job_id:
                continue

            if job.get("source") == "linkedin":
                job["job_url"] = normalize_linkedin_job_url(
                    job.get("job_url", ""), job_id
                )

            dedup_hash = job.get("dedup_hash", "") or listing_fingerprint(
                job.get("company", ""),
                job.get("title", ""),
                job.get("location", ""),
            )
            job["dedup_hash"] = dedup_hash
            fingerprint = listing_fingerprint(
                job.get("company", ""),
                job.get("title", ""),
                job.get("location", ""),
            )
            existing = existing_by_id.get(job_id)
            if job_id in dismissed_ids:
                skipped += 1
                continue
            if (
                not existing
                and dedup_hash
                and dedup_hash in dismissed_hashes
            ):
                skipped += 1
                continue
            if not existing and fingerprint and fingerprint in dismissed_fps:
                skipped += 1
                continue
            if dedup_hash and dedup_hash in dedup_to_id and dedup_to_id[dedup_hash] != job_id:
                skipped += 1
                continue

            incoming_desc = (job.get("description") or "").strip()
            if existing:
                is_new = existing["is_new"] or (1 if job_id in new_job_ids else 0)
                description = incoming_desc or (existing["description"] or "")
                conn.execute(
                    """
                    UPDATE jobs SET
                        title = ?, company = ?, location = ?, posted_date = ?,
                        job_url = ?, source = ?, source_company = ?,
                        dedup_hash = ?, description = ?, fetched_at = ?, is_new = ?
                    WHERE job_id = ?
                    """,
                    (
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("location", ""),
                        job.get("posted_date", ""),
                        job.get("job_url", ""),
                        job.get("source", "linkedin"),
                        job.get("source_company", ""),
                        dedup_hash,
                        description,
                        now,
                        is_new,
                        job_id,
                    ),
                )
                updated += 1
                if dedup_hash:
                    dedup_to_id[dedup_hash] = job_id
                continue

            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, title, company, location, posted_date,
                    job_url, source, source_company, dedup_hash,
                    description, first_seen_at, fetched_at, is_new, email_sent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                """,
                (
                    job_id,
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("posted_date", ""),
                    job.get("job_url", ""),
                    job.get("source", "linkedin"),
                    job.get("source_company", ""),
                    dedup_hash,
                    job.get("description", "") or "",
                    now,
                    now,
                ),
            )
            inserted += 1
            new_jobs.append(job)
            existing_by_id[job_id] = {"job_id": job_id, "is_new": 1, "dedup_hash": dedup_hash}
            if dedup_hash:
                dedup_to_id[dedup_hash] = job_id

        conn.commit()

    invalidate_meta_cache()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total_processed": len(jobs),
        "new_jobs": new_jobs,
    }


def backfill_linkedin_descriptions() -> dict:
    """Fetch posting text for stored LinkedIn jobs that have no description."""
    from jobs.job_description import attach_description
    from jobs.linkedin_client import begin_linkedin_cycle, linkedin_is_limited

    init_db()
    begin_linkedin_cycle()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT job_id, title, job_url, source, description
            FROM jobs
            WHERE source = 'linkedin' AND (description IS NULL OR description = '')
            """
        ).fetchall()

    filled = 0
    checked = 0
    for row in rows:
        if linkedin_is_limited():
            logger.warning(
                "LinkedIn description backfill stopped early (rate limited)"
            )
            break
        checked += 1
        job = {
            "job_id": row["job_id"],
            "title": row["title"],
            "job_url": row["job_url"],
            "source": "linkedin",
            "description": "",
        }
        attach_description(job)
        desc = (job.get("description") or "").strip()
        if desc:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE jobs SET description = ? WHERE job_id = ?",
                    (desc, row["job_id"]),
                )
                conn.commit()
            filled += 1

    invalidate_meta_cache()
    removed = purge_mismatched_jobs()
    return {"checked": checked, "filled": filled, "removed": removed}


def purge_mismatched_jobs() -> int:
    """Delete stored jobs that fail the experience or education profile."""
    if not FILTER_BY_EXPERIENCE and not FILTER_BY_EDUCATION:
        return 0

    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT job_id, title, description FROM jobs WHERE COALESCE(is_applied, 0) = 0"
        ).fetchall()
        to_delete = [
            row["job_id"]
            for row in rows
            if not profile_matches(
                {"title": row["title"], "description": row["description"] or ""}
            )
        ]
        if not to_delete:
            return 0
        conn.executemany(
            "DELETE FROM jobs WHERE job_id = ?",
            [(job_id,) for job_id in to_delete],
        )
        conn.commit()

    invalidate_meta_cache()
    return len(to_delete)


def purge_over_experience_jobs() -> int:
    return purge_mismatched_jobs()


def mark_jobs_seen() -> int:
    init_db()
    with get_connection() as conn:
        cursor = conn.execute("UPDATE jobs SET is_new = 0 WHERE is_new = 1")
        conn.commit()
        count = cursor.rowcount
    invalidate_meta_cache()
    return count


def delete_job(job_id: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None

        job = _row_to_dict(row)
        fingerprint = listing_fingerprint(
            job.get("company", ""),
            job.get("title", ""),
            job.get("location", ""),
        )
        now = _now()
        removed_ids = [job_id]
        dismissed_rows = [
            (
                job_id,
                job.get("dedup_hash") or fingerprint,
                fingerprint,
                now,
            )
        ]

        for other in conn.execute("SELECT * FROM jobs WHERE job_id != ?", (job_id,)):
            other_job = _row_to_dict(other)
            other_fp = listing_fingerprint(
                other_job.get("company", ""),
                other_job.get("title", ""),
                other_job.get("location", ""),
            )
            if not fingerprint or other_fp != fingerprint:
                continue
            if other_job.get("is_applied"):
                continue
            dismissed_rows.append(
                (
                    other_job["job_id"],
                    other_job.get("dedup_hash") or other_fp,
                    other_fp,
                    now,
                )
            )
            removed_ids.append(other_job["job_id"])

        conn.executemany(
            """
            INSERT INTO dismissed_jobs (job_id, dedup_hash, fingerprint, dismissed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                dedup_hash = excluded.dedup_hash,
                fingerprint = excluded.fingerprint,
                dismissed_at = excluded.dismissed_at
            """,
            dismissed_rows,
        )
        conn.executemany(
            "DELETE FROM jobs WHERE job_id = ?",
            [(rid,) for rid in removed_ids],
        )
        conn.commit()

    invalidate_meta_cache()
    job.pop("description", None)
    job.pop("resume_path", None)
    job.pop("cover_letter_path", None)
    job.pop("email_sent", None)
    job["removed_ids"] = removed_ids
    return job


def get_job(job_id: str, *, include_description: bool = False) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    job = _row_to_dict(row)
    if not include_description:
        job.pop("description", None)
    return job


def get_unscored_jobs(limit: int | None = None) -> list[dict]:
    init_db()
    query = """
        SELECT * FROM jobs
        WHERE ai_score IS NULL AND COALESCE(is_applied, 0) = 0
        ORDER BY posted_date DESC, first_seen_at DESC
    """
    params: list = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_unscored_jobs() -> int:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM jobs
            WHERE ai_score IS NULL AND COALESCE(is_applied, 0) = 0
            """
        ).fetchone()
    return int(row["n"] if row else 0)


def save_job_score(job_id: str, result: dict, description: str | None = None) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_id FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None

        params = [
            result.get("score"),
            "",
            "[]",
            "[]",
            "",
            None,
            None,
            None,
            None,
            _now(),
        ]
        if description is not None and description.strip():
            conn.execute(
                """
                UPDATE jobs SET
                    ai_score = ?, ai_verdict = ?, ai_matched_skills = ?,
                    ai_missing_skills = ?, ai_recommendation = ?,
                    ai_role_score = ?, ai_skills_score = ?,
                    ai_experience_score = ?, ai_requirements_score = ?,
                    ai_scored_at = ?, description = ?
                WHERE job_id = ?
                """,
                (*params, description.strip(), job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs SET
                    ai_score = ?, ai_verdict = ?, ai_matched_skills = ?,
                    ai_missing_skills = ?, ai_recommendation = ?,
                    ai_role_score = ?, ai_skills_score = ?,
                    ai_experience_score = ?, ai_requirements_score = ?,
                    ai_scored_at = ?
                WHERE job_id = ?
                """,
                (*params, job_id),
            )
        conn.commit()

    invalidate_meta_cache()
    return get_job(job_id)


def save_job_resume(
    job_id: str, resume_path: str, ats_score: int | None = None
) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_id FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        if ats_score is None:
            conn.execute(
                "UPDATE jobs SET resume_path = ? WHERE job_id = ?",
                (resume_path, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET resume_path = ?, ats_score = ? WHERE job_id = ?",
                (resume_path, int(ats_score), job_id),
            )
        conn.commit()
    invalidate_meta_cache()
    return get_job(job_id)


def get_resume_file(job_id: str, fmt: str = "pdf") -> Path | None:
    job = get_job(job_id)
    if not job:
        return None
    stored = (job.get("resume_path") or "").strip()
    if not stored:
        return None
    path = Path(stored)
    pdf_path = path if path.suffix.lower() == ".pdf" else path.with_suffix(".pdf")
    if fmt != "pdf":
        return None
    return pdf_path if pdf_path.exists() else None


def save_job_cover_letter(job_id: str, cover_letter_path: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_id FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE jobs SET cover_letter_path = ? WHERE job_id = ?",
            (cover_letter_path, job_id),
        )
        conn.commit()
    invalidate_meta_cache()
    return get_job(job_id)


def get_cover_letter_file(job_id: str, fmt: str = "pdf") -> Path | None:
    job = get_job(job_id)
    if not job:
        return None
    stored = (job.get("cover_letter_path") or "").strip()
    if not stored:
        return None
    path = Path(stored)
    pdf_path = path if path.suffix.lower() == ".pdf" else path.with_suffix(".pdf")
    if fmt != "pdf":
        return None
    return pdf_path if pdf_path.exists() else None


def get_unnotified_high_score_jobs(
    min_score: int, max_posted_days: int | None = None
) -> list[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE COALESCE(email_sent, 0) = 0
              AND COALESCE(is_applied, 0) = 0
              AND COALESCE(is_new, 0) = 1
              AND ai_score IS NOT NULL
              AND ai_score >= ?
            ORDER BY ai_score DESC, posted_date DESC, first_seen_at DESC
            """,
            (int(min_score),),
        ).fetchall()
    jobs = [_row_to_dict(row) for row in rows]
    for job in jobs:
        job.pop("description", None)
        job.pop("resume_path", None)
        job.pop("cover_letter_path", None)
    if max_posted_days and max_posted_days > 0:
        fresh = [
            job
            for job in jobs
            if is_posted_within_days(job.get("posted_date"), max_posted_days)
        ]
        skipped = len(jobs) - len(fresh)
        if skipped:
            logger.info(
                "Skipping %d high-score job(s) older than %s day(s)",
                skipped,
                max_posted_days,
            )
        return fresh
    return jobs


def mark_jobs_emailed(job_ids: list[str]) -> None:
    ids = [job_id for job_id in job_ids if job_id]
    if not ids:
        return
    init_db()
    with get_connection() as conn:
        conn.executemany(
            "UPDATE jobs SET email_sent = 1 WHERE job_id = ?",
            [(job_id,) for job_id in ids],
        )
        conn.commit()
    invalidate_meta_cache()


def set_job_applied(job_id: str, applied: bool) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None

        if applied:
            conn.execute(
                """
                UPDATE jobs
                SET is_applied = 1, applied_at = ?, is_new = 0
                WHERE job_id = ?
                """,
                (_now(), job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET is_applied = 0, applied_at = ''
                WHERE job_id = ?
                """,
                (job_id,),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    invalidate_meta_cache()
    job = _row_to_dict(row)
    job.pop("description", None)
    job.pop("resume_path", None)
    job.pop("cover_letter_path", None)
    job.pop("email_sent", None)
    return job
