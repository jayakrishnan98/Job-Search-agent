import json
import re
import time

import anthropic
import requests
from bs4 import BeautifulSoup

from config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    MASTER_RESUME_PATH,
    USER_PROFILE,
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SCORING_PROMPT = """Here is the candidate's resume:
---
{master_resume_content}
---

Here is the job description:
---
{job_description}
---

Evaluate the fit and return this exact JSON:
{{
  "score": <integer 0-100>,
  "verdict": "<STRONG_MATCH | GOOD_MATCH | WEAK_MATCH | NO_MATCH>",
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill1", "skill2"],
  "match_reasons": ["reason1", "reason2"],
  "red_flags": ["flag1"],
  "recommendation": "<one sentence on whether to apply>"
}}

Scoring guide:
- 80-100: Role almost perfectly matches experience and skills
- 65-79: Good match, minor gaps
- 40-64: Partial match, significant gaps
- 0-39: Poor match, do not apply"""


def _read_master_resume() -> str:
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(f"Master resume not found at {MASTER_RESUME_PATH}")
    return MASTER_RESUME_PATH.read_text(encoding="utf-8").strip()


def fetch_job_description(job: dict) -> str:
    fallback = job.get("description_snippet", "")
    job_url = job.get("job_url", "")

    if not job_url:
        return fallback

    try:
        response = requests.get(job_url, headers=REQUEST_HEADERS, timeout=15)
        final_url = response.url.lower()

        if "authwall" in final_url or response.status_code in (401, 403, 999):
            return fallback or "Job description unavailable."

        soup = BeautifulSoup(response.text, "lxml")

        for selector in (
            "div.show-more-less-html__markup",
            "div.description__text",
            "section.description",
            "div.jobs-description-content__text",
        ):
            element = soup.select_one(selector)
            if element:
                text = element.get_text(separator="\n", strip=True)
                if text:
                    return text

        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            return meta["content"]

    except requests.RequestException:
        pass

    return fallback or "Job description unavailable."


def _extract_json(text: str) -> dict:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError("No valid JSON found in Claude response")


def _call_claude(prompt: str, max_tokens: int = 2000) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    for attempt in range(1, 4):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=(
                    "You are a professional job-fit analyst. You evaluate how well a "
                    "candidate's profile matches a job description. Be strict and realistic. "
                    "Return ONLY a valid JSON object, nothing else."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except anthropic.APIError:
            if attempt == 3:
                raise
            time.sleep(5)


def score_job(job: dict) -> dict | None:
    master_resume = _read_master_resume()
    job_description = fetch_job_description(job)

    prompt = SCORING_PROMPT.format(
        master_resume_content=master_resume,
        job_description=job_description,
    )

    response_text = _call_claude(prompt, max_tokens=2000)
    time.sleep(1)

    result = _extract_json(response_text)
    result["job_description"] = job_description

    score = result.get("score", 0)
    if score < USER_PROFILE.get("min_match_score", 65):
        return None

    return result
