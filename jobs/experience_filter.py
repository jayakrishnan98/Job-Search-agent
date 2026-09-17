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
    r"(?<!member of technical )staff\b|"
    r"principal\b|"
    r"distinguished\s+engineer|"
    r"director|"
    r"vice\s+president|\bvp\b|"
    r"head\s+of|"
    r"chief\s+|"
    r"engineering\s+manager|"
    r"(technical|tech(nical)?|engineering)\s+lead(er)?|"
    r"(senior\s+)?lead(er)?\b|"
    r"architect\b|"
    r"(software\s+engineer|engineer|sde)[\s-]*(iii|iv|v|[3-9])|"
    r"\bl[5-9]\b"
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

_RANGE_PATTERN = re.compile(r"(\d+)\s*(?:-|to|–|—)\s*(\d+)\s*(?:years?|yrs?)", re.I)
_PLUS_PATTERN = re.compile(r"(\d+)\s*\+\s*(?:years?|yrs?)", re.I)
_SINGLE_PATTERNS = [
    re.compile(r"(?:minimum|min\.?|at\s+least|requires?|requiring)\s+(?:of\s+)?(\d+)\s+(?:years?|yrs?)", re.I),
    # "8 years of experience" / "8 years of relevant experience" / "8 years' experience"
    re.compile(
        r"(\d+)\s+(?:years?|yrs?)(?:['’]s)?"
        r"(?:\s+\w+){0,6}\s+(?:experience|exp\.?)",
        re.I,
    ),
    re.compile(r"(?:experience|exp\.?)\s*(?:of|:)?\s*(\d+)\s*(?:\+|plus)?\s*(?:years?|yrs?)", re.I),
]
_WORD_YEARS = {
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
}
_WORD_YEAR_PATTERN = re.compile(
    r"\b(five|six|seven|eight|nine|ten|eleven|twelve|fifteen)\s+"
    r"(?:\+|plus\s+)?(?:years?|yrs?)",
    re.I,
)


def _plain_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_title(title: str) -> str:
    """Treat underscores and slashes as word breaks (e.g. Engineer_Vice President_Lead)."""
    return re.sub(r"[_|/]+", " ", title or "")


def _valid_years(n: int) -> bool:
    return 0 < n <= 20


def _extract_year_requirements(text: str) -> tuple[int | None, int | None]:
    """Return (required_min_years, stated_max_years) parsed from job text."""
    if not text:
        return None, None

    mins: list[int] = []
    maxs: list[int] = []
    masked = text

    # Consume ranges first so "3-5 years of experience" is not also read as "5 years"
    pieces: list[str] = []
    last = 0
    for match in _RANGE_PATTERN.finditer(text):
        low, high = int(match.group(1)), int(match.group(2))
        if _valid_years(low) and _valid_years(high):
            mins.append(low)
            maxs.append(high)
        pieces.append(text[last:match.start()])
        last = match.end()
    pieces.append(text[last:])
    masked = " ".join(pieces)

    for match in _PLUS_PATTERN.finditer(masked):
        n = int(match.group(1))
        if _valid_years(n):
            mins.append(n)

    for pattern in _SINGLE_PATTERNS:
        for match in pattern.finditer(masked):
            n = int(match.group(1))
            if _valid_years(n):
                mins.append(n)

    for match in _WORD_YEAR_PATTERN.finditer(masked):
        n = _WORD_YEARS.get(match.group(1).lower())
        if n is not None and _valid_years(n):
            mins.append(n)

    if not mins:
        return None, None

    # Use the strictest stated minimum so "5+ years" wins over "2 years of AWS"
    return max(mins), max(maxs) if maxs else None


def experience_matches(job: dict) -> bool:
    """True if the job is suitable for someone with EXPERIENCE_YEARS years.

    Jobs that require more than EXPERIENCE_MAX years are rejected.
    """
    if not FILTER_BY_EXPERIENCE:
        return True

    title = _norm_title(job.get("title", "") or "")
    description = _plain_text(job.get("description", "") or "")
    combined = f"{title} {description}".strip()

    if _TOO_JUNIOR.search(title):
        return False

    if _TOO_SENIOR.search(title):
        return False

    min_years, max_years = _extract_year_requirements(combined)

    if min_years is not None and min_years > EXPERIENCE_MAX:
        return False

    if min_years is not None and min_years < EXPERIENCE_MIN and _TOO_JUNIOR.search(combined):
        return False

    if max_years is not None and max_years < EXPERIENCE_YEARS - 1:
        return False

    return True
