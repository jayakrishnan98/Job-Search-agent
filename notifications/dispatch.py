import logging

from config import is_email_configured, get_email_config_issue
from notifications.email_notifier import send_new_jobs_email

logger = logging.getLogger(__name__)


def notify_new_jobs(jobs: list[dict]) -> bool:
    if not jobs:
        return False
    if not is_email_configured():
        issue = get_email_config_issue() or "Email not configured"
        logger.warning("Email not configured — skipping notification: %s", issue)
        return False
    return send_new_jobs_email(jobs)
