import logging

import requests

from jobs.ats.base import make_dedup_hash, normalize_job_id

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}


def fetch_ashby_jobs(company: str, slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning("Ashby fetch failed for %s (%s): %s", company, slug, exc)
        return []

    jobs = []
    for item in data.get("jobs", []):
        location = item.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")
        jobs.append(
            {
                "job_id": normalize_job_id("ash", f"{slug}_{item['id']}"),
                "title": item.get("title", ""),
                "company": company,
                "location": location,
                "posted_date": item.get("publishedAt", "")[:10],
                "job_url": item.get("jobUrl", item.get("applyUrl", "")),
                "source": "ashby",
                "source_company": company,
                "dedup_hash": make_dedup_hash(company, item.get("title", ""), location),
            }
        )
    return jobs
