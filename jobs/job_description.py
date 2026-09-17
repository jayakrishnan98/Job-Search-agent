import logging
import re

from bs4 import BeautifulSoup

from jobs.http_client import get_session

logger = logging.getLogger(__name__)

_SELECTORS = (
    "div.show-more-less-html__markup",
    "div.description__text",
    "section.description",
    "div.jobs-description-content__text",
    "div.description__text--rich",
    "article.jobs-description__container",
)


def _numeric_linkedin_id(job: dict) -> str:
    job_id = job.get("job_id", "") or ""
    if job_id.startswith("li_"):
        return job_id[3:]
    match = re.search(r"(\d{8,})", job.get("job_url", "") or "")
    return match.group(1) if match else ""


def _text_from_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for selector in _SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        text = element.get_text(separator="\n", strip=True)
        if text and len(text) > 40:
            return text
    return ""


def fetch_linkedin_description(job: dict) -> str:
    """Load the public LinkedIn posting text so experience/education can be checked."""
    numeric_id = _numeric_linkedin_id(job)
    urls: list[str] = []
    if numeric_id:
        urls.append(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{numeric_id}")
        urls.append(f"https://www.linkedin.com/jobs/view/{numeric_id}")
    if job.get("job_url"):
        urls.append(job["job_url"])

    session = get_session()
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            response = session.get(url, timeout=15)
            if response.status_code in (401, 403, 999):
                continue
            if "authwall" in response.url.lower():
                continue
            text = _text_from_html(response.text)
            if text:
                return text
        except Exception as exc:
            logger.debug("LinkedIn description fetch failed for %s: %s", url, exc)
    return ""


def attach_description(job: dict) -> dict:
    """Fill job['description'] when the listing did not include it."""
    if (job.get("description") or "").strip():
        return job
    source = job.get("source", "")
    url = job.get("job_url", "") or ""
    if source == "linkedin" or "linkedin.com" in url:
        desc = fetch_linkedin_description(job)
        if desc:
            job["description"] = desc
    return job
