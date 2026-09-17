import re

from config import FILTER_BY_EDUCATION

_PHD = re.compile(
    r"\b("
    r"ph\.?\s*d\.?s?|"
    r"dphil|"
    r"doctorate|"
    r"doctoral|"
    r"post[\s-]?doc(?:toral)?"
    r")\b",
    re.I,
)

_MASTERS_OR_BELOW = re.compile(
    r"\b("
    r"masters?|"
    r"master'?s|"
    r"m\.?\s*s\.?|"
    r"m\.?\s*sc\.?|"
    r"m\.?\s*tech\.?|"
    r"mtech|"
    r"mba|"
    r"bachelors?|"
    r"bachelor'?s|"
    r"b\.?\s*s\.?|"
    r"b\.?\s*tech\.?|"
    r"btech|"
    r"undergraduate|"
    r"bs/?ms"
    r")\b",
    re.I,
)

_PREFERRED = re.compile(
    r"\b("
    r"preferred|"
    r"preferably|"
    r"nice\s+to\s+have|"
    r"a\s+plus|"
    r"is\s+a\s+plus|"
    r"optional|"
    r"bonus|"
    r"desired|"
    r"ideally"
    r")\b",
    re.I,
)

_EQUIVALENT = re.compile(r"\b(or\s+equivalent|equivalent\s+experience)\b", re.I)


def _plain_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _window(text: str, start: int, end: int, radius: int = 100) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def education_matches(job: dict) -> bool:
    """True if the job does not require more than a master's degree."""
    if not FILTER_BY_EDUCATION:
        return True

    title = job.get("title", "") or ""
    description = _plain_text(job.get("description", "") or "")
    combined = f"{title} {description}".strip()

    if not _PHD.search(combined):
        return True

    if _PHD.search(title) and not _MASTERS_OR_BELOW.search(title) and not _EQUIVALENT.search(title):
        return False

    for match in _PHD.finditer(combined):
        window = _window(combined, match.start(), match.end())
        if _MASTERS_OR_BELOW.search(window) or _EQUIVALENT.search(window):
            continue
        if _PREFERRED.search(window):
            continue
        return False

    return True
