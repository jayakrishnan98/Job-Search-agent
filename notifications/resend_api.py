import logging

import requests

from config import NOTIFY_EMAIL, RESEND_API_KEY, RESEND_FROM, SMTP_FROM_NAME

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def is_resend_configured() -> bool:
    return bool(RESEND_API_KEY and NOTIFY_EMAIL)


def send_via_resend(*, subject: str, text_body: str, html_body: str) -> bool:
    if not is_resend_configured():
        return False

    try:
        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{SMTP_FROM_NAME} <{RESEND_FROM}>",
                "to": [NOTIFY_EMAIL],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=30,
        )
        if response.ok:
            logger.info("Email sent via Resend to %s", NOTIFY_EMAIL)
            return True

        logger.error("Resend API error %s: %s", response.status_code, response.text[:500])
        return False
    except requests.RequestException as exc:
        logger.error("Resend API request failed: %s", exc)
        return False
