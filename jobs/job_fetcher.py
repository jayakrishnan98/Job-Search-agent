import logging
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from config import LINKEDIN_MAX_SEARCHES_PER_CYCLE, LINKEDIN_MIN_DELAY_SECONDS, USER_PROFILE
from jobs.linkedin_utils import normalize_linkedin_job_url
from jobs.ats.base import make_dedup_hash, normalize_job_id
from jobs.career_fetcher import fetch_all_career_jobs
from jobs.company_utils import company_matches, role_matches
from jobs.job_description import attach_description
from jobs.linkedin_client import begin_linkedin_cycle, linkedin_get, linkedin_is_limited
from jobs.profile_filter import profile_matches

logger = logging.getLogger(__name__)

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings"
_company_offset = 0
_known_job_ids: set[str] = set()


def _extract_job_id(href: str, card) -> str | None:
    if href:
        match = re.search(r"(\d{8,})", href)
        if match:
            return match.group(1)

    urn = card.get("data-entity-urn", "")
    match = re.search(r"jobPosting:(\d+)", urn)
    if match:
        return match.group(1)

    return None


def _parse_job_cards(html: str, source_company: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    for card in soup.select("div.base-search-card"):
        link = card.select_one("a.base-card__full-link")
        title_el = card.select_one("h3.base-search-card__title")
        company_el = card.select_one("h4.base-search-card__subtitle")
        location_el = card.select_one("span.job-search-card__location")
        time_el = card.select_one("time")

        href = link.get("href", "") if link else ""
        job_id = _extract_job_id(href, card)
        if not job_id:
            continue

        title = title_el.get_text(strip=True) if title_el else "Unknown Title"
        company = company_el.get_text(strip=True) if company_el else source_company or "Unknown Company"
        location = location_el.get_text(strip=True) if location_el else USER_PROFILE.get("location", "")
        posted_date = time_el.get("datetime", "") if time_el else ""
        if not posted_date and time_el:
            posted_date = time_el.get_text(strip=True)

        job_url = normalize_linkedin_job_url(
            href.split("?")[0] if href else f"https://www.linkedin.com/jobs/view/{job_id}",
            normalize_job_id("li", job_id),
        )
        li_job_id = normalize_job_id("li", job_id)

        jobs.append(
            {
                "job_id": li_job_id,
                "title": title,
                "company": company,
                "location": location,
                "posted_date": posted_date,
                "job_url": job_url,
                "source": "linkedin",
                "source_company": source_company,
                "dedup_hash": make_dedup_hash(company, title, location),
            }
        )

    return jobs


def _fetch_html(path_and_query: str) -> str:
    if linkedin_is_limited():
        return ""
    response = linkedin_get(f"{GUEST_API}/{path_and_query}", timeout=20)
    return response.text if response is not None else ""


def _search_params(keywords: str, location: str, lookback: str, start: int = 0, count: int = 25) -> str:
    return "&".join(
        [
            f"keywords={quote(keywords)}",
            f"location={quote(location)}",
            f"f_TPR={lookback}",
            "sortBy=DD",
            f"start={start}",
            f"count={count}",
        ]
    )


def _fetch_search(keywords: str, location: str, lookback: str) -> list[dict]:
    query = _search_params(keywords, location, lookback)
    html = _fetch_html(f"search?{query}")
    return _parse_job_cards(html) if html else []


def _is_relevant(job: dict, company_name: str, roles: list[str]) -> bool:
    if company_name and not company_matches(job.get("company", ""), company_name):
        return False
    if USER_PROFILE.get("filter_by_role", True) and roles and not role_matches(job.get("title", ""), roles):
        return False
    already_stored = job.get("job_id") in _known_job_ids
    if not already_stored and not linkedin_is_limited():
        attach_description(job)
        if not profile_matches(job):
            return False
    return True


def fetch_jobs_for_company(company_name: str, roles: list[str], location: str, lookback: str) -> list[dict]:
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    for job in _fetch_search(company_name, location, lookback):
        if job["job_id"] in seen_ids:
            continue
        if not _is_relevant(job, company_name, roles):
            continue
        seen_ids.add(job["job_id"])
        jobs.append(job)

    logger.info("Fetched %d jobs for company %s", len(jobs), company_name)
    return jobs


def fetch_jobs_for_role(role: str, location: str, lookback: str) -> list[dict]:
    jobs = _fetch_search(role, location, lookback)
    if role:
        jobs = [job for job in jobs if role_matches(job.get("title", ""), [role])]
    kept: list[dict] = []
    for job in jobs:
        if job.get("job_id") not in _known_job_ids:
            attach_description(job)
            if not profile_matches(job):
                continue
        kept.append(job)
    return kept


def _rotate_companies(companies: list[str]) -> list[str]:
    """Take the next slice so a large shortlist is covered across cycles."""
    global _company_offset
    limit = LINKEDIN_MAX_SEARCHES_PER_CYCLE
    if limit <= 0 or len(companies) <= limit:
        return list(companies)
    start = _company_offset % len(companies)
    selected = companies[start:start + limit]
    if len(selected) < limit:
        selected = selected + companies[: limit - len(selected)]
    _company_offset = start + len(selected)
    return selected


def _fetch_linkedin_jobs(
    companies: list[str],
    roles: list[str],
    location: str,
    lookback: str,
) -> list[dict]:
    global _known_job_ids

    begin_linkedin_cycle()
    try:
        from jobs.job_store import get_existing_job_ids

        _known_job_ids = get_existing_job_ids()
    except Exception:
        _known_job_ids = set()

    if not companies:
        jobs: list[dict] = []
        for role in roles[:LINKEDIN_MAX_SEARCHES_PER_CYCLE]:
            if linkedin_is_limited():
                logger.warning("LinkedIn: stopping remaining role searches this cycle")
                break
            jobs.extend(fetch_jobs_for_role(role, location, lookback))
        return jobs

    selected = _rotate_companies(companies)
    logger.info(
        "LinkedIn: searching %d of %d companies this cycle (%.0fs between requests)",
        len(selected),
        len(companies),
        LINKEDIN_MIN_DELAY_SECONDS,
    )

    all_jobs: list[dict] = []
    for company in selected:
        if linkedin_is_limited():
            logger.warning("LinkedIn: stopping remaining companies this cycle (rate limited)")
            break
        try:
            all_jobs.extend(fetch_jobs_for_company(company, roles, location, lookback))
        except Exception:
            logger.exception("LinkedIn fetch failed for %s", company)

    return all_jobs


def fetch_all_jobs() -> list[dict]:
    location = USER_PROFILE.get("location", "")
    roles = USER_PROFILE.get("target_roles", [])
    companies = USER_PROFILE.get("target_companies", [])
    lookback = USER_PROFILE.get("job_lookback", "r604800")

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()
    seen_dedup: set[str] = set()

    def add_job(job: dict) -> None:
        job_id = job.get("job_id")
        dedup = job.get("dedup_hash", "")
        if not job_id or job_id in seen_ids:
            return
        if dedup and dedup in seen_dedup:
            return
        seen_ids.add(job_id)
        if dedup:
            seen_dedup.add(dedup)
        all_jobs.append(job)

    for job in fetch_all_career_jobs():
        add_job(job)

    for job in _fetch_linkedin_jobs(companies, roles, location, lookback):
        add_job(job)

    career_count = sum(1 for j in all_jobs if j.get("source") != "linkedin")
    logger.info(
        "Total jobs fetched: %d (%d from career sites, %d from LinkedIn)",
        len(all_jobs),
        career_count,
        len(all_jobs) - career_count,
    )
    return all_jobs
