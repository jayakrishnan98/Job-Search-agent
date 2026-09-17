from jobs.education_filter import education_matches
from jobs.experience_filter import experience_matches


def profile_matches(job: dict) -> bool:
    """True if the job fits the configured experience and education profile."""
    return experience_matches(job) and education_matches(job)
