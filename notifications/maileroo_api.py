import logging

import requests

from config import MAILEROO_API_KEY, NOTIFY_EMAIL, SMTP_FROM, SMTP_FROM_NAME

logger = logging.getLogger(__name__)

MAILEROO_API_URL = "https://smtp.maileroo.com/api/v2/emails"


def is_maileroo_api_configured() -> bool:
    return bool(MAILEROO_API_KEY and SMTP_FROM and NOTIFY_EMAIL)


def send_via_api(*, subject: str, text_body: str, html_body: str) -> bool:
    if not is_maileroo_api_configured():
        return False

    payload = {
        "from": {"address": SMTP_FROM, "display_name": SMTP_FROM_NAME},
        "to": [{"address": NOTIFY_EMAIL}],
        "reply_to": {"address": SMTP_FROM, "display_name": SMTP_FROM_NAME},
        "subject": subject,
        "plain": text_body,
        "html": html_body,
        "tracking": False,
    }

    try:
        response = requests.post(
            MAILEROO_API_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": MAILEROO_API_KEY,
            },
            timeout=30,
        )
        if response.ok:
            logger.info("Email sent via Maileroo API to %s", NOTIFY_EMAIL)
            return True

        logger.error(
            "Maileroo API error %s: %s",
            response.status_code,
            response.text[:500],
        )
        return False
    except requests.RequestException as exc:
        logger.error("Maileroo API request failed: %s", exc)
        return False
