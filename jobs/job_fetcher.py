import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from bs4 import BeautifulSoup

from config import FETCH_CONCURRENCY, USER_PROFILE
from jobs.linkedin_utils import normalize_linkedin_job_url
from jobs.ats.base import make_dedup_hash, normalize_job_id
from jobs.career_fetcher import fetch_all_career_jobs
from jobs.company_utils import company_matches, company_to_slug, role_matches
from jobs.experience_filter import experience_matches
from jobs.http_client import get_session

logger = logging.getLogger(__name__)

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings"


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
    url = f"{GUEST_API}/{path_and_query}"
    try:
        response = get_session().get(url, timeout=20)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        logger.warning("LinkedIn fetch failed for %s: %s", path_and_query[:80], exc)
        return ""


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


def _fetch_company_board(company_name: str, location: str, lookback: str) -> list[dict]:
    slug = company_to_slug(company_name)
    query = "&".join(
        [
            f"location={quote(location)}" if location else "",
            f"f_TPR={lookback}",
            "sortBy=DD",
            "start=0",
            "count=25",
        ]
    )
    query = query.strip("&")
    html = _fetch_html(f"{slug}-jobs?{query}")
    return _parse_job_cards(html, source_company=company_name) if html else []


def _is_relevant(job: dict, company_name: str, roles: list[str]) -> bool:
    if company_name and not company_matches(job.get("company", ""), company_name):
        return False
    if USER_PROFILE.get("filter_by_role", True) and roles and not role_matches(job.get("title", ""), roles):
        return False
    if not experience_matches(job):
        return False
    return True


def fetch_jobs_for_company(company_name: str, roles: list[str], location: str, lookback: str) -> list[dict]:
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    def add_jobs(candidates: list[dict], filter_company: str) -> None:
        for job in candidates:
            if job["job_id"] in seen_ids:
                continue
            if not _is_relevant(job, filter_company, roles):
                continue
            seen_ids.add(job["job_id"])
            jobs.append(job)

    combined_keywords = " ".join(f"{role} {company_name}".strip() for role in (roles or [""]))
    add_jobs(_fetch_search(combined_keywords, location, lookback), company_name)
    add_jobs(_fetch_search(company_name, location, lookback), company_name)

    board_jobs = _fetch_company_board(company_name, location, lookback)
    add_jobs(board_jobs, company_name)

    logger.info("Fetched %d jobs for company %s", len(jobs), company_name)
    return jobs


def fetch_jobs_for_role(role: str, location: str, lookback: str) -> list[dict]:
    jobs = _fetch_search(role, location, lookback)
    if role:
        jobs = [job for job in jobs if role_matches(job.get("title", ""), [role])]
    jobs = [job for job in jobs if experience_matches(job)]
    return jobs


def _fetch_linkedin_jobs(
    companies: list[str],
    roles: list[str],
    location: str,
    lookback: str,
) -> list[dict]:
    if not companies:
        jobs: list[dict] = []
        for role in roles:
            jobs.extend(fetch_jobs_for_role(role, location, lookback))
        return jobs

    all_jobs: list[dict] = []
    workers = min(FETCH_CONCURRENCY, max(1, len(companies)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_jobs_for_company, company, roles, location, lookback): company
            for company in companies
        }
        for future in as_completed(futures):
            company = futures[future]
            try:
                all_jobs.extend(future.result())
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
