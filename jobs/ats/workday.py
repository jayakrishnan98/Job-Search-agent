import logging
import re

import requests

from jobs.ats.base import make_dedup_hash, normalize_job_id
from jobs.http_client import get_session

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_JOBS = 400
SEARCH_TEXT = "Software"


def _parse_slug(slug: str) -> tuple[str, str, str] | None:
    """Parse '{host}/{site}' into host, tenant, site."""
    if not slug or "/" not in slug:
        return None
    host, site = slug.split("/", 1)
    host = host.strip()
    site = site.strip().strip("/")
    if not host or not site:
        return None
    tenant = host.split(".")[0]
    return host, tenant, site


def fetch_workday_jobs(company: str, slug: str) -> list[dict]:
    parsed = _parse_slug(slug)
    if not parsed:
        logger.warning("Invalid Workday slug for %s: %s", company, slug)
        return []

    host, tenant, site = parsed
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    session = get_session()
    jobs: list[dict] = []
    offset = 0

    while offset < MAX_JOBS:
        payload = {
            "appliedFacets": {},
            "limit": PAGE_SIZE,
            "offset": offset,
            "searchText": SEARCH_TEXT,
        }
        try:
            response = session.post(url, json=payload, headers=HEADERS, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.warning("Workday fetch failed for %s (%s): %s", company, slug, exc)
            break

        postings = data.get("jobPostings") or []
        if not postings:
            break

        for item in postings:
            title = item.get("title", "")
            location = item.get("locationsText", "") or ""
            path = item.get("externalPath", "") or ""
            posted = item.get("postedOn", "") or ""
            posted_date = _normalize_posted(posted)
            job_url = f"https://{host}/{site}{path}" if path else f"https://{host}/{site}"
            external_id = path or f"{title}-{location}"
            jobs.append(
                {
                    "job_id": normalize_job_id("wd", f"{tenant}_{external_id}"),
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_date": posted_date,
                    "job_url": job_url,
                    "source": "workday",
                    "source_company": company,
                    "dedup_hash": make_dedup_hash(company, title, location),
                }
            )

        offset += PAGE_SIZE
        if offset >= int(data.get("total") or 0):
            break

    return jobs


def _normalize_posted(posted_on: str) -> str:
    if not posted_on:
        return ""
    match = re.search(r"(\d+)\s+day", posted_on, re.I)
    if match:
        return f"{match.group(1)}d ago"
    if re.search(r"today|just posted|hour", posted_on, re.I):
        return "today"
    return posted_on.replace("Posted ", "").strip()
