import hashlib
import re


def normalize_job_id(source: str, external_id: str) -> str:
    """Build a globally unique job ID: {source}_{external_id}."""
    safe_id = re.sub(r"[^\w-]", "_", str(external_id))
    return f"{source}_{safe_id}"


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().strip())


_LOCATION_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "calcutta": "kolkata",
    "kolkata": "kolkata",
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "delhi": "delhi",
    "new delhi": "delhi",
    "pune": "pune",
    "hyderabad": "hyderabad",
    "chennai": "chennai",
    "madras": "chennai",
}

_REMOTE_RE = re.compile(
    r"\b(remote|home based|home-based|distributed|wfh|work from home)\b",
    re.I,
)
_COUNT_LOCATION_RE = re.compile(r"^\d+\s+locations$", re.I)


def canonical_location(location: str) -> str:
    """Collapse noisy location strings so Bangalore and Bengaluru match."""
    raw = (location or "").strip()
    if not raw or raw.startswith("*") or _COUNT_LOCATION_RE.match(raw):
        return ""

    lower = _norm(raw)
    for alias, canonical in sorted(
        _LOCATION_ALIASES.items(), key=lambda item: -len(item[0])
    ):
        if re.search(rf"(^|[^a-z]){re.escape(alias)}([^a-z]|$)", lower):
            return canonical

    first = re.split(r"[,|/;]", raw, maxsplit=1)[0].strip()
    first = re.sub(r"\bhybrid in\s+", "", first, flags=re.I).strip()
    key = _norm(first)
    if _REMOTE_RE.search(key):
        return "remote"
    return key


def make_dedup_hash(company: str, title: str, location: str = "") -> str:
    """Cross-source dedup key from company + title + canonical location."""
    key = f"{_norm(company)}|{_norm(title)}|{canonical_location(location)}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def listing_fingerprint(company: str, title: str, location: str = "") -> str:
    """Stable hide-key for dismissed jobs across sources and location spellings."""
    return make_dedup_hash(company, title, location)
