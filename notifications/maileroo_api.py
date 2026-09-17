import base64
import logging
from pathlib import Path

import requests

from config import MAILEROO_API_KEY, NOTIFY_EMAIL, SMTP_FROM, SMTP_FROM_NAME

logger = logging.getLogger(__name__)

MAILEROO_API_URL = "https://smtp.maileroo.com/api/v2/emails"


def is_maileroo_api_configured() -> bool:
    return bool(MAILEROO_API_KEY and SMTP_FROM and NOTIFY_EMAIL)


def send_via_api(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    attachments: list[tuple[str, Path]] | None = None,
) -> bool:
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

    files = attachments or []
    if files:
        encoded = []
        for filename, path in files:
            try:
                content = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError as exc:
                logger.warning("Could not read attachment %s: %s", path, exc)
                continue
            encoded.append(
                {
                    "file_name": filename,
                    "content_type": "application/pdf",
                    "content": content,
                    "inline": False,
                }
            )
        if encoded:
            payload["attachments"] = encoded

    timeout = 90 if files else 30
    try:
        response = requests.post(
            MAILEROO_API_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": MAILEROO_API_KEY,
            },
            timeout=timeout,
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
