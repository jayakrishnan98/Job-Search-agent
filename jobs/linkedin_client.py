"""Serialized LinkedIn guest HTTP with conservative rate limits.

LinkedIn's unofficial guest job endpoints return 429 / 999 when an IP
bursts requests. All LinkedIn traffic in this app goes through this
module so search, description, and backfill share one budget.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import requests

from config import (
    LINKEDIN_MAX_REQUESTS_PER_HOUR,
    LINKEDIN_MAX_REQUESTS_PER_MINUTE,
    LINKEDIN_MAX_SEARCHES_PER_CYCLE,
    LINKEDIN_MIN_DELAY_SECONDS,
)
from jobs.http_client import get_session

logger = logging.getLogger(__name__)

LINKEDIN_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/search",
    "Connection": "keep-alive",
}

_RATE_LIMIT_STATUSES = {429, 999}
_MAX_RETRY_AFTER = 180.0
_MINUTE = 60.0
_HOUR = 3600.0

_lock = threading.Lock()
_last_request_at = 0.0
_request_times: deque[float] = deque()
_pause_until = 0.0
_consecutive_blocks = 0
_cycle_limited = False
_cycle_requests = 0
_MAX_REQUESTS_PER_CYCLE = LINKEDIN_MAX_SEARCHES_PER_CYCLE + 20


def begin_linkedin_cycle() -> None:
    """Allow a new fetch cycle to try LinkedIn again after a previous skip."""
    global _cycle_limited, _consecutive_blocks, _cycle_requests
    with _lock:
        _cycle_limited = False
        _cycle_requests = 0
        if time.monotonic() >= _pause_until:
            _consecutive_blocks = 0


def linkedin_is_limited() -> bool:
    with _lock:
        return _cycle_limited


def _prune_request_times(now: float) -> None:
    while _request_times and now - _request_times[0] > _HOUR:
        _request_times.popleft()


def _retry_after_seconds(response: requests.Response, status: int) -> float:
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return min(float(raw), _MAX_RETRY_AFTER)
    if status == 999:
        return min(120.0, _MAX_RETRY_AFTER)
    return min(60.0 * max(1, _consecutive_blocks), _MAX_RETRY_AFTER)


def _is_rate_limited(response: requests.Response) -> bool:
    if response.status_code in _RATE_LIMIT_STATUSES:
        return True
    snippet = (response.text or "")[:500].lower()
    return "too many requests" in snippet or "rate limit" in snippet


def _wait_for_slot() -> bool:
    """Sleep until a request is allowed. Returns False if this cycle should stop."""

    while True:
        now = time.monotonic()
        if _cycle_limited:
            return False

        if _cycle_requests >= _MAX_REQUESTS_PER_CYCLE:
            logger.info(
                "LinkedIn: cycle request budget reached (%d). Saving remaining quota for later",
                _MAX_REQUESTS_PER_CYCLE,
            )
            _mark_cycle_limited()
            return False

        if now < _pause_until:
            time.sleep(min(_pause_until - now, 5.0))
            continue

        _prune_request_times(now)
        recent_minute = sum(1 for stamp in _request_times if now - stamp < _MINUTE)
        if recent_minute >= LINKEDIN_MAX_REQUESTS_PER_MINUTE:
            oldest = next(stamp for stamp in _request_times if now - stamp < _MINUTE)
            time.sleep(max(0.05, _MINUTE - (now - oldest) + 0.05))
            continue

        if len(_request_times) >= LINKEDIN_MAX_REQUESTS_PER_HOUR:
            wait = _HOUR - (now - _request_times[0]) + 0.05
            logger.warning(
                "LinkedIn hourly budget reached (%d/%d). Waiting %.0fs",
                len(_request_times),
                LINKEDIN_MAX_REQUESTS_PER_HOUR,
                wait,
            )
            if wait > 30:
                _mark_cycle_limited()
                return False
            time.sleep(wait)
            continue

        elapsed = now - _last_request_at if _last_request_at else LINKEDIN_MIN_DELAY_SECONDS
        if elapsed < LINKEDIN_MIN_DELAY_SECONDS:
            time.sleep(LINKEDIN_MIN_DELAY_SECONDS - elapsed)
            continue

        return True


def _mark_cycle_limited() -> None:
    global _cycle_limited
    _cycle_limited = True


def linkedin_get(url: str, timeout: int = 20) -> requests.Response | None:
    """GET a LinkedIn URL, or None if skipped / blocked / failed."""
    global _last_request_at, _pause_until, _consecutive_blocks, _cycle_requests

    with _lock:
        if not _wait_for_slot():
            return None

        try:
            response = get_session().get(
                url,
                timeout=timeout,
                headers=LINKEDIN_HEADERS,
            )
        except requests.RequestException as exc:
            logger.warning("LinkedIn request failed for %s: %s", url[:80], exc)
            _last_request_at = time.monotonic()
            return None

        now = time.monotonic()
        _last_request_at = now
        _request_times.append(now)
        _cycle_requests += 1

        if _is_rate_limited(response):
            _consecutive_blocks += 1
            wait = _retry_after_seconds(response, response.status_code)
            _pause_until = now + wait
            _mark_cycle_limited()
            logger.warning(
                "LinkedIn rate-limited (HTTP %s). Pausing %.0fs and skipping "
                "remaining LinkedIn requests this cycle",
                response.status_code,
                wait,
            )
            return None

        _consecutive_blocks = 0
        if response.status_code >= 400:
            logger.warning(
                "LinkedIn HTTP %s for %s", response.status_code, url[:80]
            )
            return None
        return response
