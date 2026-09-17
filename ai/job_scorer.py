import logging
import re

from bs4 import BeautifulSoup

from config import MASTER_RESUME_PATH, SCORE_BATCH_SIZE, USER_PROFILE
from ai.llm import extract_json, generate

logger = logging.getLogger(__name__)

SCORE_SYSTEM = (
    "Score candidate-job fit 0-100. Return JSON {\"s\":[int,...]} in listing order. "
    "No extra keys. Do not invent experience."
)

_REQ_HEADING = re.compile(
    r"(requirements|qualifications|what you.?ll need|must[- ]have|you have|what we.?re looking)",
    re.I,
)
_KEEP_HEADING = re.compile(
    r"(responsibilities|requirements|qualifications|what you.?ll (?:need|do)|"
    r"what you will (?:need|do)|must[- ]have|you have|what we.?re looking|"
    r"about the (?:role|job|position)|the role|day[- ]to[- ]day|you will)",
    re.I,
)
_DROP_HEADING = re.compile(
    r"(?:^|\n)\s*(?:equal opportunity|eeo\b|benefits\b|what we offer|perks\b|"
    r"about us|about the company|how to apply|to apply\b|visa\b|"
    r"work authorization|reasonable accommodation|diversity\b|inclusion\b|"
    r"life at |our values|who we are|compensation\b|salary range|"
    r"interview process)",
    re.I,
)
_JD_CHARS = 800
_RESUME_JD_CHARS = 1600
_PROFILE_CHARS = 800


def _read_master_resume() -> str:
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(f"Master resume not found at {MASTER_RESUME_PATH}")
    return MASTER_RESUME_PATH.read_text(encoding="utf-8").strip()


def fetch_job_description(job: dict) -> str:
    existing = (job.get("description") or "").strip()
    if existing and existing != "Job description unavailable.":
        return existing

    from jobs.job_description import attach_description

    attach_description(job)
    filled = (job.get("description") or "").strip()
    if filled:
        return filled
    return job.get("description_snippet", "") or "Job description unavailable."


def _clamp(value) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


def _plain_text(html: str) -> str:
    text = BeautifulSoup(html or "", "lxml").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _pipe_field(value: str, max_len: int | None = None) -> str:
    text = re.sub(r"[\r\n|]+", " ", value or "").strip()
    if max_len is not None and len(text) > max_len:
        cut = text[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut
    return text


def _jd_snippet(description: str, limit: int = _JD_CHARS) -> str:
    plain = _plain_text(description)
    match = _REQ_HEADING.search(plain)
    if match:
        plain = plain[match.start() :]
    return _pipe_field(plain, limit)


def _plain_blocks(html: str) -> str:
    text = BeautifulSoup(html or "", "lxml").get_text(separator="\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def jd_for_resume(description: str, limit: int = _RESUME_JD_CHARS) -> str:
    """Plain requirements/duties only. Drop About-us / EEO / benefits boilerplate."""
    plain = _plain_blocks(description)
    if not plain:
        return ""
    keep = _KEEP_HEADING.search(plain)
    snippet = plain[keep.start() :] if keep else plain
    drop = _DROP_HEADING.search(snippet)
    if drop and drop.start() > 80:
        snippet = snippet[: drop.start()]
    snippet = snippet.strip()
    if len(snippet) > limit:
        cut = snippet[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        snippet = cut
    return snippet


def _section(resume: str, heading: str) -> str:
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*\n(.*?)(?=\n[A-Z][A-Z /]{{2,}}\n|\Z)",
        re.I | re.S | re.M,
    )
    match = pattern.search(resume)
    return (match.group(1) if match else "").strip()


def distilled_profile(resume: str | None = None) -> str:
    text = resume if resume is not None else _read_master_resume()
    name = USER_PROFILE.get("name") or (text.splitlines()[0].strip() if text else "")
    years = USER_PROFILE.get("experience_years", "")
    location = USER_PROFILE.get("location", "")
    roles = ", ".join((USER_PROFILE.get("target_roles") or [])[:4])
    summary = _pipe_field(_section(text, "SUMMARY"), 280)
    skills = _pipe_field(_section(text, "SKILLS"), 220)
    experience = _pipe_field(_section(text, "EXPERIENCE"), 240)
    parts = [
        f"{name}, {location}, {years}y.",
        f"Roles: {roles}." if roles else "",
        summary,
        skills,
        experience,
    ]
    return _pipe_field(" ".join(part for part in parts if part), _PROFILE_CHARS)


def _listing_line(index: int, job: dict, jd_limit: int = _JD_CHARS) -> str:
    title = _pipe_field(job.get("title") or "", 80)
    company = _pipe_field(job.get("company") or "", 40)
    location = _pipe_field(job.get("location") or "", 40)
    snippet = _jd_snippet(fetch_job_description(job), jd_limit)
    return f"{index}|{title}|{company}|{location}|{snippet}"


def _parse_scores(text: str, expected: int) -> list[int]:
    data = extract_json(text)
    raw = data.get("s") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("Score response was not a list")
    if len(raw) != expected:
        raise ValueError(f"Expected {expected} scores, got {len(raw)}")
    return [_clamp(item) for item in raw]


def _persist_score(job: dict, score: int, description: str | None = None) -> dict:
    result = {"score": score, "job_description": description or ""}
    if job.get("job_id"):
        from jobs.job_store import save_job_score

        save_job_score(job["job_id"], result, description=description)
    return result


def _score_jobs_once(jobs: list[dict], *, persist: bool) -> list[dict]:
    profile = distilled_profile()
    lines = [_listing_line(index, job) for index, job in enumerate(jobs, start=1)]
    prompt = (
        f"Candidate:\n{profile}\n\n"
        "Jobs (index|title|company|location|jd):\n"
        + "\n".join(lines)
        + f"\n\nReturn {{\"s\":[exactly {len(jobs)} integers 0-100]}} with length {len(jobs)}."
    )
    response_text = generate(
        SCORE_SYSTEM,
        prompt,
        max_tokens=512,
        json_mode=True,
        purpose="score",
    )
    scores = _parse_scores(response_text, len(jobs))
    results = []
    for job, score in zip(jobs, scores):
        description = (job.get("description") or "").strip() or None
        result = _persist_score(job, score, description) if persist else {"score": score}
        results.append(result)
    return results


def score_jobs_batch(jobs: list[dict], *, persist: bool = True) -> list[dict]:
    if not jobs:
        return []
    if len(jobs) == 1:
        try:
            return [_score_jobs_once(jobs, persist=persist)[0]]
        except Exception:
            logger.exception("Scoring failed for %s", jobs[0].get("job_id"))
            raise
    try:
        return _score_jobs_once(jobs, persist=persist)
    except Exception as exc:
        logger.warning("Batch of %d failed (%s); splitting", len(jobs), exc)
        mid = max(1, len(jobs) // 2)
        if mid >= len(jobs):
            return score_jobs_batch(jobs[:1], persist=persist)
        return score_jobs_batch(jobs[:mid], persist=persist) + score_jobs_batch(
            jobs[mid:], persist=persist
        )


def score_job(job: dict, *, persist: bool = True) -> dict:
    return _score_jobs_once([job], persist=persist)[0]


def score_unscored_jobs(progress: dict | None = None) -> dict:
    from jobs.job_store import count_unscored_jobs, get_unscored_jobs

    scored = 0
    failed = 0
    batch_size = max(1, SCORE_BATCH_SIZE)

    while True:
        remaining = count_unscored_jobs()
        if progress is not None:
            progress["remaining"] = remaining
        if remaining == 0:
            break

        jobs = get_unscored_jobs(limit=batch_size)
        if not jobs:
            break
        try:
            score_jobs_batch(jobs, persist=True)
            scored += len(jobs)
            from notifications.dispatch import notify_high_score_new_jobs

            notify_high_score_new_jobs()
            if progress is not None:
                progress["scored"] = progress.get("scored", 0) + len(jobs)
                progress["remaining"] = count_unscored_jobs()
                progress["error"] = None
        except Exception as exc:
            failed += len(jobs)
            logger.exception("Scoring batch failed")
            if progress is not None:
                progress["error"] = str(exc)
            break

    return {
        "scored": scored,
        "failed": max(0, failed),
        "remaining": count_unscored_jobs(),
    }
