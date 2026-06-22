import logging
import re

import requests

from jobs.http_client import get_session

logger = logging.getLogger(__name__)

DISCOVERY_PATTERNS = [
    (r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)", "greenhouse"),
    (r"boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)", "greenhouse"),
    (r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", "ashby"),
    (r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)", "smartrecruiters"),
    (r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", "smartrecruiters"),
]


def discover_ats(career_url: str) -> dict | None:
    if not career_url:
        return None

    try:
        response = get_session().get(career_url, timeout=15, allow_redirects=True)
        response.raise_for_status()
        html = response.text
    except requests.RequestException as exc:
        logger.debug("Career page fetch failed for %s: %s", career_url, exc)
        return None

    for pattern, ats in DISCOVERY_PATTERNS:
        match = re.search(pattern, html)
        if match:
            slug = match.group(1)
            logger.info("Discovered %s board '%s' from %s", ats, slug, career_url)
            return {"ats": ats, "slug": slug}

    return None
