import logging

from config import is_email_configured
from notifications.email_notifier import send_new_jobs_email

logger = logging.getLogger(__name__)


def notify_new_jobs(jobs: list[dict]) -> bool:
    if not jobs:
        return False
    if not is_email_configured():
        logger.warning("Email not configured — skipping notification.")
        return False
    return send_new_jobs_email(jobs)
