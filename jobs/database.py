import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from config import DB_PATH
from jobs.linkedin_utils import normalize_linkedin_job_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    company        TEXT NOT NULL,
    location       TEXT DEFAULT '',
    posted_date    TEXT DEFAULT '',
    job_url        TEXT DEFAULT '',
    source_company TEXT DEFAULT '',
    description    TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS dismissed_jobs (
    job_id         TEXT PRIMARY KEY,
    dedup_hash     TEXT DEFAULT '',
    fingerprint    TEXT DEFAULT '',
    dismissed_at   TEXT NOT NULL
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
        _normalize_stored_linkedin_urls(conn)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
            CREATE INDEX IF NOT EXISTS idx_jobs_posted_date ON jobs(posted_date);
            CREATE INDEX IF NOT EXISTS idx_jobs_is_new ON jobs(is_new);
            CREATE INDEX IF NOT EXISTS idx_jobs_is_applied ON jobs(is_applied);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_dedup_hash ON jobs(dedup_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_company_posted
                ON jobs(company, posted_date DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_source_posted
                ON jobs(source, posted_date DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_ai_score ON jobs(ai_score);
            CREATE INDEX IF NOT EXISTS idx_dismissed_dedup
                ON dismissed_jobs(dedup_hash);
            CREATE INDEX IF NOT EXISTS idx_dismissed_fingerprint
                ON dismissed_jobs(fingerprint);
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
    if "description" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN description TEXT DEFAULT ''")
    if "is_applied" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN is_applied INTEGER NOT NULL DEFAULT 0")
    if "applied_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN applied_at TEXT DEFAULT ''")
    if "ai_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_score INTEGER")
    if "ai_verdict" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_verdict TEXT DEFAULT ''")
    if "ai_matched_skills" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_matched_skills TEXT DEFAULT ''")
    if "ai_missing_skills" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_missing_skills TEXT DEFAULT ''")
    if "ai_recommendation" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_recommendation TEXT DEFAULT ''")
    if "ai_role_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_role_score INTEGER")
    if "ai_skills_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_skills_score INTEGER")
    if "ai_experience_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_experience_score INTEGER")
    if "ai_requirements_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_requirements_score INTEGER")
    if "ai_scored_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ai_scored_at TEXT DEFAULT ''")
    if "resume_path" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT DEFAULT ''")
    if "cover_letter_path" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN cover_letter_path TEXT DEFAULT ''")
    if "email_sent" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN email_sent INTEGER NOT NULL DEFAULT 1")
    if "ats_score" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN ats_score INTEGER")

    dismissed_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(dismissed_jobs)").fetchall()
    }
    if dismissed_columns and "fingerprint" not in dismissed_columns:
        conn.execute(
            "ALTER TABLE dismissed_jobs ADD COLUMN fingerprint TEXT DEFAULT ''"
        )

    conn.execute(
        "UPDATE jobs SET job_id = 'li_' || job_id "
        "WHERE job_id GLOB '[0-9]*' AND job_id NOT LIKE 'li_%'"
    )
    conn.execute(
        "UPDATE jobs SET source = 'linkedin' WHERE source IS NULL OR source = ''"
    )


def _normalize_stored_linkedin_urls(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT job_id, job_url FROM jobs WHERE source = 'linkedin'"
    ).fetchall()
    for row in rows:
        normalized = normalize_linkedin_job_url(row["job_url"], row["job_id"])
        if normalized != row["job_url"]:
            conn.execute(
                "UPDATE jobs SET job_url = ? WHERE job_id = ?",
                (normalized, row["job_id"]),
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
