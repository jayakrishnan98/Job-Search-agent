import re


def normalize_linkedin_job_url(job_url: str, job_id: str = "") -> str:
    """Return a canonical LinkedIn job URL using the numeric posting ID."""
    numeric_id = ""
    if job_id.startswith("li_"):
        numeric_id = job_id[3:]
    if not numeric_id:
        match = re.search(r"(\d{8,})", job_url or "")
        if match:
            numeric_id = match.group(1)
    if numeric_id:
        return f"https://www.linkedin.com/jobs/view/{numeric_id}"
    return job_url
