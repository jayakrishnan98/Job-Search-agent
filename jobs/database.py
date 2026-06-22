import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    company        TEXT NOT NULL,
    location       TEXT DEFAULT '',
    posted_date    TEXT DEFAULT '',
    job_url        TEXT DEFAULT '',
    source_company TEXT DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    is_new         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ats_cache (
    company        TEXT PRIMARY KEY,
    ats            TEXT NOT NULL,
    slug           TEXT NOT NULL,
    discovered_at  TEXT NOT NULL
);
"""

_schema_ready = False


def init_db() -> None:
    global _schema_ready
    if _schema_ready:
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
            CREATE INDEX IF NOT EXISTS idx_jobs_posted_date ON jobs(posted_date);
            CREATE INDEX IF NOT EXISTS idx_jobs_is_new ON jobs(is_new);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_dedup_hash ON jobs(dedup_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_company_posted
                ON jobs(company, posted_date DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_source_posted
                ON jobs(source, posted_date DESC);
        """)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.commit()

    _schema_ready = True


def _migrate_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    if "source" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'linkedin'")
    if "dedup_hash" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN dedup_hash TEXT DEFAULT ''")

    conn.execute(
        "UPDATE jobs SET job_id = 'li_' || job_id "
        "WHERE job_id GLOB '[0-9]*' AND job_id NOT LIKE 'li_%'"
    )
    conn.execute(
        "UPDATE jobs SET source = 'linkedin' WHERE source IS NULL OR source = ''"
    )


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_cached_ats(company: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT ats, slug FROM ats_cache WHERE company = ?",
            (company,),
        ).fetchone()
    if not row:
        return None
    return {"ats": row["ats"], "slug": row["slug"]}


def set_cached_ats(company: str, ats: str, slug: str) -> None:
    init_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ats_cache (company, ats, slug, discovered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(company) DO UPDATE SET
                ats = excluded.ats,
                slug = excluded.slug,
                discovered_at = excluded.discovered_at
            """,
            (company, ats, slug, now),
        )
        conn.commit()


def migrate_json_if_needed() -> None:
    """One-time import from legacy jobs.json if the DB is empty."""
    from config import JOBS_PATH
    import json

    if not JOBS_PATH.exists():
        return

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if count > 0:
            return

        try:
            data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        jobs = data.get("jobs", [])
        if not jobs:
            return

        from jobs.job_store import upsert_jobs

        upsert_jobs(jobs)
