import json
import re
import time
from datetime import datetime

import anthropic

from config import CLAUDE_API_KEY, CLAUDE_MODEL, MASTER_RESUME_PATH, RESUMES_OUTPUT_DIR
from ai.job_scorer import fetch_job_description

RESUME_PROMPT = """MASTER RESUME:
---
{master_resume_content}
---

JOB DESCRIPTION:
---
{job_description}
---

JOB TITLE: {job_title}
COMPANY: {company_name}

Rewrite the resume to maximize ATS score for this specific role.
Start with a tailored Professional Summary (3-4 lines) that mirrors
the job's language. Then optimize each section.

At the end, add a section called "ATS KEYWORDS INJECTED" listing
the top 10 keywords you added from the job description."""


def _read_master_resume() -> str:
    return MASTER_RESUME_PATH.read_text(encoding="utf-8").strip()


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text)
    cleaned = re.sub(r"[\s]+", "_", cleaned.strip())
    return cleaned[:80] or "unknown"


def build_resume(job: dict, score_result: dict) -> str:
    master_resume = _read_master_resume()
    job_description = score_result.get("job_description") or fetch_job_description(job)

    prompt = RESUME_PROMPT.format(
        master_resume_content=master_resume,
        job_description=job_description,
        job_title=job.get("title", "Unknown"),
        company_name=job.get("company", "Unknown"),
    )

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    for attempt in range(1, 4):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                system=(
                    "You are an expert ATS resume optimizer. Your job is to rewrite a "
                    "candidate's resume to maximize ATS score for a specific job posting.\n\n"
                    "Rules:\n"
                    "- NEVER invent experience, skills, or education the candidate doesn't have\n"
                    "- ONLY reuse and restructure what is already in the master resume\n"
                    "- Mirror the exact keywords and phrases from the job description naturally\n"
                    "- Prioritize the most relevant experience for THIS specific role\n"
                    "- Use strong action verbs\n"
                    "- Quantify achievements wherever the original resume has numbers\n"
                    "- Keep the same sections: Summary, Experience, Skills, Education\n"
                    "- Format in clean plain text with clear section headers\n"
                    "- The resume must pass ATS systems like Workday, Greenhouse, and Taleo"
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            resume_text = message.content[0].text
            break
        except anthropic.APIError:
            if attempt == 3:
                raise
            time.sleep(5)
    else:
        resume_text = ""

    time.sleep(1)

    date_str = datetime.now().strftime("%Y-%m-%d")
    company = _safe_filename(job.get("company", "Unknown"))
    title = _safe_filename(job.get("title", "Unknown"))
    base_name = f"{company}_{title}_{date_str}"

    RESUMES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    resume_path = RESUMES_OUTPUT_DIR / f"{base_name}.txt"
    resume_path.write_text(resume_text, encoding="utf-8")

    meta = {
        "job_url": job.get("job_url", ""),
        "score": score_result.get("score"),
        "matched_skills": score_result.get("matched_skills", []),
        "missing_skills": score_result.get("missing_skills", []),
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "date": date_str,
        "verdict": score_result.get("verdict", ""),
        "recommendation": score_result.get("recommendation", ""),
    }
    meta_path = RESUMES_OUTPUT_DIR / f"{base_name}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return str(resume_path)
