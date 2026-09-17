import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str, default: bool = True) -> bool:
    if value == "":
        return default
    return value.lower() in ("1", "true", "yes")


def _parse_int(value: str, default: int) -> int:
    if not value or not value.strip():
        return default
    return int(value.strip())


def _load_target_companies() -> list[str]:
    env_companies = os.getenv("TARGET_COMPANIES", "").strip()
    if env_companies:
        return _parse_csv(env_companies)

    companies_path = Path(
        os.getenv("COMPANIES_CONFIG_PATH", str(BASE_DIR / "config" / "companies.json"))
    )
    if not companies_path.is_absolute():
        companies_path = BASE_DIR / companies_path

    if companies_path.exists():
        try:
            data = json.loads(companies_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Invalid companies config at {companies_path}: {exc}") from exc

        if isinstance(data, list):
            return [str(company).strip() for company in data if str(company).strip()]
        if isinstance(data, dict):
            companies = data.get("target_companies", [])
            return [str(company).strip() for company in companies if str(company).strip()]

    example_path = BASE_DIR / "config" / "companies.example.json"
    if example_path.exists():
        data = json.loads(example_path.read_text(encoding="utf-8"))
        return [str(company).strip() for company in data.get("target_companies", []) if str(company).strip()]

    return ["Google", "Microsoft", "Amazon"]


def _resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


EXPERIENCE_YEARS = int(os.getenv("EXPERIENCE_YEARS", "4"))
FILTER_BY_EXPERIENCE = _parse_bool(os.getenv("FILTER_BY_EXPERIENCE", "true"))
EXPERIENCE_MIN = int(os.getenv("EXPERIENCE_MIN", str(max(2, EXPERIENCE_YEARS - 2))))
EXPERIENCE_MAX = int(os.getenv("EXPERIENCE_MAX", str(EXPERIENCE_YEARS)))
MAX_EDUCATION = os.getenv("MAX_EDUCATION", "masters").strip().lower() or "masters"
FILTER_BY_EDUCATION = _parse_bool(os.getenv("FILTER_BY_EDUCATION", "true"))

USER_PROFILE = {
    "name": os.getenv("USER_NAME", "").strip(),
    "target_roles": _parse_csv(
        os.getenv("TARGET_ROLES", "Software Engineer,Senior Software Engineer")
    ),
    "target_companies": _load_target_companies(),
    "location": os.getenv("JOB_LOCATION", "India").strip(),
    "experience_years": EXPERIENCE_YEARS,
    "min_match_score": int(os.getenv("MIN_MATCH_SCORE", "65")),
    "skills": _parse_csv(os.getenv("USER_SKILLS", "")),
    "job_lookback": os.getenv("JOB_LOOKBACK", "r604800").strip(),
    "filter_by_role": _parse_bool(os.getenv("FILTER_BY_ROLE", "true")),
}

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower() or "auto"
GEMINI_SCORE_MODEL = os.getenv("GEMINI_SCORE_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_LOCATION = os.getenv("GEMINI_LOCATION", "us-central1").strip() or "us-central1"
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
FETCH_CONCURRENCY = int(os.getenv("FETCH_CONCURRENCY", "8"))
SCORE_BATCH_SIZE = max(1, _parse_int(os.getenv("SCORE_BATCH_SIZE", "80"), 80))
EMAIL_MIN_SCORE = max(0, _parse_int(os.getenv("EMAIL_MIN_SCORE", "70"), 70))
EMAIL_ATTACH_MATERIALS = _parse_bool(
    os.getenv("EMAIL_ATTACH_MATERIALS", "false"), default=False
)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = _parse_int(os.getenv("SMTP_PORT", "587"), 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Job Agent")
SMTP_USE_TLS = _parse_bool(os.getenv("SMTP_USE_TLS", "true"))
SMTP_USE_SSL = _parse_bool(os.getenv("SMTP_USE_SSL", "false"), default=False)
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
MAILEROO_API_KEY = os.getenv("MAILEROO_API_KEY", "")
RESEND_API_KEY = ""
RESEND_FROM = ""
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

# One-line Gmail setup: set GMAIL_APP_PASSWORD + NOTIFY_EMAIL in .env
if GMAIL_APP_PASSWORD:
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USER = os.getenv("GMAIL_USER", NOTIFY_EMAIL)
    SMTP_PASSWORD = GMAIL_APP_PASSWORD
    SMTP_FROM = SMTP_USER
    SMTP_USE_TLS = True
    SMTP_USE_SSL = False


def is_smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_PASSWORD and SMTP_FROM)


def is_email_configured() -> bool:
    return get_email_config_issue() is None


def get_email_config_issue() -> str | None:
    """Return a human-readable reason email is not ready, or None if configured."""
    if not NOTIFY_EMAIL.strip():
        return "NOTIFY_EMAIL is not set in .env"

    if MAILEROO_API_KEY:
        return None
    if GMAIL_APP_PASSWORD:
        return None
    if is_smtp_configured():
        return None

    return (
        "No email transport configured. Set GMAIL_APP_PASSWORD (recommended), "
        "MAILEROO_API_KEY, or SMTP_HOST + SMTP_USER + SMTP_PASSWORD in .env"
    )


def email_transport_hint() -> str:
    if MAILEROO_API_KEY:
        return "maileroo_api"
    if GMAIL_APP_PASSWORD:
        return "gmail"
    if SMTP_HOST:
        return "smtp"
    return "none"


MASTER_RESUME_PATH = _resolve_path(
    os.getenv("RESUME_PATH", "resume/master_resume.txt")
)
DB_PATH = BASE_DIR / "data" / "jobs.db"
JOBS_PATH = BASE_DIR / "data" / "jobs.json"  # legacy, migrated on first run
RESUMES_OUTPUT_DIR = BASE_DIR / "resumes"
COVER_LETTERS_OUTPUT_DIR = BASE_DIR / "cover_letters"
LOGS_DIR = BASE_DIR / "logs"

CLAUDE_MODEL = "claude-sonnet-4-20250514"


def resolve_ai_provider() -> str | None:
    """Return 'gemini', 'claude', or None if no usable key is configured."""
    provider = AI_PROVIDER
    if provider == "gemini":
        return "gemini" if GEMINI_API_KEY else None
    if provider == "claude":
        return "claude" if CLAUDE_API_KEY else None
    if GEMINI_API_KEY:
        return "gemini"
    if CLAUDE_API_KEY:
        return "claude"
    return None


def is_ai_configured() -> bool:
    return resolve_ai_provider() is not None
