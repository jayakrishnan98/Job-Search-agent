from jobs.job_store import get_existing_dedup_hashes, get_existing_job_ids


def filter_seen_jobs(jobs: list[dict]) -> list[dict]:
    existing_ids = get_existing_job_ids()
    existing_dedup = get_existing_dedup_hashes()

    new_jobs = []
    for job in jobs:
        job_id = job.get("job_id")
        dedup = job.get("dedup_hash", "")

        if not job_id:
            continue
        if job_id in existing_ids:
            continue
        if dedup and dedup in existing_dedup:
            continue

        new_jobs.append(job)

    return new_jobs


def mark_as_seen(job_id: str) -> None:
    """No-op — jobs are tracked in SQLite via upsert_jobs."""
    pass
