import hashlib
import re


def normalize_job_id(source: str, external_id: str) -> str:
    """Build a globally unique job ID: {source}_{external_id}."""
    safe_id = re.sub(r"[^\w-]", "_", str(external_id))
    return f"{source}_{safe_id}"


def make_dedup_hash(company: str, title: str, location: str = "") -> str:
    """Cross-source dedup key from company + title + location."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower().strip())

    key = f"{norm(company)}|{norm(title)}|{norm(location)}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]
