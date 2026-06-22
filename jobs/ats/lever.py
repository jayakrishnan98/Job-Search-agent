import logging

import requests

from jobs.ats.base import make_dedup_hash, normalize_job_id

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}


def fetch_lever_jobs(company: str, slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        postings = response.json()
    except requests.RequestException as exc:
        logger.warning("Lever fetch failed for %s (%s): %s", company, slug, exc)
        return []

    jobs = []
    for item in postings:
        location = item.get("categories", {}).get("location", "")
        created = item.get("createdAt", "")
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone
            posted_date = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            posted_date = str(created)[:10]

        jobs.append(
            {
                "job_id": normalize_job_id("lv", f"{slug}_{item['id']}"),
                "title": item.get("text", ""),
                "company": company,
                "location": location,
                "posted_date": posted_date,
                "job_url": item.get("hostedUrl", item.get("applyUrl", "")),
                "source": "lever",
                "source_company": company,
                "dedup_hash": make_dedup_hash(company, item.get("text", ""), location),
            }
        )
    return jobs
