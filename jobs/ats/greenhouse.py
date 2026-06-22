import logging

import requests

from jobs.ats.base import make_dedup_hash, normalize_job_id
from jobs.http_client import get_session

logger = logging.getLogger(__name__)


def fetch_greenhouse_jobs(company: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        response = get_session().get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning("Greenhouse fetch failed for %s (%s): %s", company, slug, exc)
        return []

    jobs = []
    for item in data.get("jobs", []):
        loc = item.get("location", {})
        location = loc.get("name", "") if isinstance(loc, dict) else str(loc)
        jobs.append(
            {
                "job_id": normalize_job_id("gh", f"{slug}_{item['id']}"),
                "title": item.get("title", ""),
                "company": company,
                "location": location,
                "posted_date": item.get("updated_at", item.get("created_at", ""))[:10],
                "job_url": item.get("absolute_url", ""),
                "source": "greenhouse",
                "source_company": company,
                "dedup_hash": make_dedup_hash(company, item.get("title", ""), location),
            }
        )
    return jobs
