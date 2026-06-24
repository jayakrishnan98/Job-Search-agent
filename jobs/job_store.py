import time
from datetime import datetime, timezone

from jobs.database import get_connection, init_db
from jobs.experience_filter import experience_matches
from jobs.linkedin_utils import normalize_linkedin_job_url
from config import FILTER_BY_EXPERIENCE

_meta_cache: dict | None = None
_meta_cache_at: float = 0.0
META_CACHE_TTL = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invalidate_meta_cache() -> None:
    global _meta_cache
    _meta_cache = None


def _row_to_dict(row) -> dict:
    source = row["source"] if "source" in row.keys() else "linkedin"
    job_url = row["job_url"]
    if source == "linkedin":
        job_url = normalize_linkedin_job_url(job_url, row["job_id"])

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
        "first_seen_at": row["first_seen_at"],
        "fetched_at": row["fetched_at"],
        "is_new": bool(row["is_new"]),
    }


def get_existing_job_ids() -> set[str]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    return {row["job_id"] for row in rows}


def get_existing_dedup_hashes() -> set[str]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT dedup_hash FROM jobs WHERE dedup_hash != ''"
        ).fetchall()
    return {row["dedup_hash"] for row in rows}


def get_all_jobs(
    company: str | None = None,
    search: str | None = None,
    sort: str = "newest",
    source: str | None = None,
) -> list[dict]:
    init_db()
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []

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
    if FILTER_BY_EXPERIENCE:
        jobs = [job for job in jobs if experience_matches(job)]
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
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN is_new = 1 THEN 1 ELSE 0 END), 0) AS new_count,
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
        "new_count": stats["new_count"],
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
            "SELECT job_id, is_new, dedup_hash FROM jobs"
        ).fetchall()
        existing_by_id = {row["job_id"]: row for row in existing_rows}
        dedup_to_id = {
            row["dedup_hash"]: row["job_id"]
            for row in existing_rows
            if row["dedup_hash"]
        }

        for job in jobs:
            job_id = job.get("job_id")
            if not job_id:
                continue

            if job.get("source") == "linkedin":
                job["job_url"] = normalize_linkedin_job_url(
                    job.get("job_url", ""), job_id
                )

            dedup_hash = job.get("dedup_hash", "")
            if dedup_hash and dedup_hash in dedup_to_id and dedup_to_id[dedup_hash] != job_id:
                skipped += 1
                continue

            existing = existing_by_id.get(job_id)
            if existing:
                is_new = existing["is_new"] or (1 if job_id in new_job_ids else 0)
                conn.execute(
                    """
                    UPDATE jobs SET
                        title = ?, company = ?, location = ?, posted_date = ?,
                        job_url = ?, source = ?, source_company = ?,
                        dedup_hash = ?, fetched_at = ?, is_new = ?
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
                    first_seen_at, fetched_at, is_new
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
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


def mark_jobs_seen() -> int:
    init_db()
    with get_connection() as conn:
        cursor = conn.execute("UPDATE jobs SET is_new = 0 WHERE is_new = 1")
        conn.commit()
        count = cursor.rowcount
    invalidate_meta_cache()
    return count
