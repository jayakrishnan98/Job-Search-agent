import re

from config import (
    EXPERIENCE_MAX,
    EXPERIENCE_MIN,
    EXPERIENCE_YEARS,
    FILTER_BY_EXPERIENCE,
)

# Titles that typically require well above 4 years of experience
_TOO_SENIOR = re.compile(
    r"\b("
    r"staff\s+(software\s+)?engineer|"
    r"principal\s+(software\s+)?engineer|"
    r"distinguished\s+engineer|"
    r"director|"
    r"vice\s+president|\bvp\b|"
    r"head\s+of|"
    r"chief\s+|"
    r"engineering\s+manager|"
    r"tech(nical)?\s+lead\s+manager|"
    r"architect\b"
    r")\b",
    re.I,
)

# Entry-level roles below ~4 years
_TOO_JUNIOR = re.compile(
    r"\b("
    r"intern(ship)?|"
    r"apprentice|"
    r"new\s+grad(uate)?|"
    r"campus|"
    r"fresher|"
    r"entry[\s-]?level|"
    r"junior|"
    r"associate\s+(software\s+)?engineer|"
    r"trainee|"
    r"co[\s-]?op\b"
    r")\b",
    re.I,
)

_YEAR_PATTERNS = [
    re.compile(r"(\d+)\s*\+\s*years?", re.I),
    re.compile(r"(\d+)\s*-\s*(\d+)\s*years?", re.I),
    re.compile(r"(?:minimum|min\.?|at\s+least)\s+(?:of\s+)?(\d+)\s+years?", re.I),
    re.compile(r"(\d+)\s+years?\s+(?:of\s+)?(?:experience|exp\.?)", re.I),
]


def _extract_year_requirements(text: str) -> tuple[int | None, int | None]:
    """Return (min_years, max_years) parsed from job text, if any."""
    if not text:
        return None, None

    mins: list[int] = []
    maxs: list[int] = []

    for pattern in _YEAR_PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g is not None]
            if not groups:
                continue
            if len(groups) == 1:
                mins.append(int(groups[0]))
            elif len(groups) >= 2:
                mins.append(int(groups[0]))
                maxs.append(int(groups[1]))

    if not mins:
        return None, None

    return min(mins), max(maxs) if maxs else None


def experience_matches(job: dict) -> bool:
    """True if the job's experience requirement fits the configured profile."""
    if not FILTER_BY_EXPERIENCE:
        return True

    title = job.get("title", "")
    description = job.get("description", "")
    combined = f"{title} {description}".strip()

    if _TOO_JUNIOR.search(title):
        return False

    if _TOO_SENIOR.search(title):
        return False

    min_years, max_years = _extract_year_requirements(combined)

    if min_years is not None:
        if min_years > EXPERIENCE_MAX:
            return False
        if min_years < EXPERIENCE_MIN and _TOO_JUNIOR.search(combined):
            return False

    if max_years is not None and max_years < EXPERIENCE_YEARS - 1:
        return False

    return True
