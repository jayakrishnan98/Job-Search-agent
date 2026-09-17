import logging
import re

import requests

from jobs.http_client import get_session

logger = logging.getLogger(__name__)

LOCALE_SEGMENTS = {"en-us", "en-gb", "en", "fr-fr", "de-de", "ja-jp"}

DISCOVERY_PATTERNS = [
    (r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)", "greenhouse"),
    (r"job-boards\.greenhouse\.io/([a-zA-Z0-9_-]+)", "greenhouse"),
    (r"boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)", "greenhouse"),
    (r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", "ashby"),
    (r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)", "smartrecruiters"),
    (r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", "smartrecruiters"),
]

WORKDAY_PATTERN = re.compile(
    r"(https?://)?([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_-]+))?",
    re.I,
)


def _workday_from_text(text: str) -> dict | None:
    match = WORKDAY_PATTERN.search(text)
    if not match:
        return None
    host, first, second = match.group(2), match.group(3), match.group(4)
    site = second if first.lower() in LOCALE_SEGMENTS and second else first
    if site.lower() in LOCALE_SEGMENTS:
        return None
    slug = f"{host}/{site}"
    return {"ats": "workday", "slug": slug}


def discover_ats(career_url: str) -> dict | None:
    if not career_url:
        return None

    from_url = _workday_from_text(career_url)
    if from_url:
        logger.info("Discovered workday board '%s' from URL %s", from_url["slug"], career_url)
        return from_url

    try:
        response = get_session().get(career_url, timeout=15, allow_redirects=True)
        response.raise_for_status()
        html = response.text
        final_url = str(response.url)
    except requests.RequestException as exc:
        logger.debug("Career page fetch failed for %s: %s", career_url, exc)
        return None

    from_redirect = _workday_from_text(final_url)
    if from_redirect:
        logger.info("Discovered workday board '%s' from %s", from_redirect["slug"], career_url)
        return from_redirect

    for pattern, ats in DISCOVERY_PATTERNS:
        match = re.search(pattern, html)
        if match:
            slug = match.group(1)
            if slug.lower() == "embed":
                continue
            logger.info("Discovered %s board '%s' from %s", ats, slug, career_url)
            return {"ats": ats, "slug": slug}

    from_html = _workday_from_text(html)
    if from_html:
        logger.info("Discovered workday board '%s' from %s", from_html["slug"], career_url)
        return from_html

    return None
