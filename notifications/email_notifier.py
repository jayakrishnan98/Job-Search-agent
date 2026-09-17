import logging
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from config import (
    NOTIFY_EMAIL,
    SMTP_FROM,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
    SMTP_USER,
    EMAIL_MIN_SCORE,
    get_email_config_issue,
    is_email_configured,
    is_smtp_configured,
)
from notifications.maileroo_api import is_maileroo_api_configured, send_via_api
from notifications import email_status

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "greenhouse": "Company careers page",
    "lever": "Company careers page",
    "ashby": "Company careers page",
    "smartrecruiters": "Company careers page",
    "workday": "Company careers page",
}


def _smtp_connection() -> smtplib.SMTP:
    """Connect using Maileroo's recommended STARTTLS flow on port 587."""
    if SMTP_USE_SSL or SMTP_PORT == 465:
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=context)
        server.ehlo()
        return server

    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    server.ehlo()
    if SMTP_USE_TLS:
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    return server


def _smtp_login(server: smtplib.SMTP) -> None:
    try:
        server.login(SMTP_USER, SMTP_PASSWORD)
    except smtplib.SMTPAuthenticationError:
        raise
    except smtplib.SMTPServerDisconnected as exc:
        # Maileroo closes the connection after 535 instead of returning a clean auth error.
        raise smtplib.SMTPAuthenticationError(
            535,
            b"5.7.8 Account is disabled or credentials are invalid",
        ) from exc


def _format_smtp_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        detail = exc.smtp_error.decode("utf-8", errors="replace") if exc.smtp_error else str(exc)
        if "disabled" in detail.lower():
            return (
                f"{detail} — Enable the SMTP account in Maileroo "
                "(Domains → your domain → SMTP Accounts), or set MAILEROO_API_KEY."
            )
        return f"{detail} — Check SMTP_USER and SMTP_PASSWORD in .env."

    message = str(exc)
    if "Connection unexpectedly closed" in message:
        return (
            f"{message} — Maileroo likely rejected login (account disabled or bad credentials). "
            "Enable the SMTP account in Maileroo, or set MAILEROO_API_KEY."
        )
    return message


def _usable_attachments(
    attachments: list[tuple[str, Path]] | None,
) -> list[tuple[str, Path]]:
    usable: list[tuple[str, Path]] = []
    for filename, path in attachments or []:
        try:
            if path.exists() and path.is_file():
                usable.append((filename, path))
        except OSError:
            logger.warning("Skipping missing attachment %s", path)
    return usable


def _attach_pdfs(message: MIMEMultipart, attachments: list[tuple[str, Path]]) -> None:
    for filename, path in attachments:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            logger.warning("Could not read attachment %s: %s", path, exc)
            continue
        part = MIMEApplication(payload, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        message.attach(part)


def _deliver_email(
    subject: str,
    text_body: str,
    html_body: str,
    attachments: list[tuple[str, Path]] | None = None,
) -> bool:
    files = _usable_attachments(attachments)

    if is_maileroo_api_configured():
        if send_via_api(
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=files,
        ):
            email_status.record_success("maileroo_api")
            return True
        logger.warning("Maileroo API failed — falling back to SMTP.")

    if not is_smtp_configured():
        email_status.record_failure("none", "No email transport configured.")
        logger.error("No working email transport configured.")
        return False

    if files:
        message = MIMEMultipart("mixed")
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(text_body, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        message.attach(alternative)
        _attach_pdfs(message, files)
    else:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(text_body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))

    message["Subject"] = subject
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    message["To"] = NOTIFY_EMAIL
    message["Reply-To"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))

    transport = "gmail" if SMTP_HOST == "smtp.gmail.com" else "smtp"
    try:
        with _smtp_connection() as server:
            if SMTP_USER and SMTP_PASSWORD:
                _smtp_login(server)
            server.sendmail(SMTP_FROM, [NOTIFY_EMAIL], message.as_string())
        logger.info("Email sent via SMTP to %s", NOTIFY_EMAIL)
        email_status.record_success(transport)
        return True
    except Exception as exc:
        error = _format_smtp_error(exc)
        logger.error("SMTP delivery failed: %s", error)
        email_status.record_failure(transport, error)
        return False


def _format_job_text(job: dict, index: int) -> str:
    source = SOURCE_LABELS.get(job.get("source", ""), "Job board")
    score = job.get("ai_score")
    score_line = f"   Match    : {score}/100\n" if score is not None else ""
    return (
        f"{index}. {job.get('title', 'Role not listed')}\n"
        f"   Company  : {job.get('company', 'Not listed')}\n"
        f"   Location : {job.get('location', 'Not listed')}\n"
        f"{score_line}"
        f"   Posted   : {job.get('posted_date', 'Not listed')}\n"
        f"   Source   : {source}\n"
        f"   Link     : {job.get('job_url', '')}\n"
    )


def _format_job_html(job: dict, index: int) -> str:
    source = SOURCE_LABELS.get(job.get("source", ""), "Job board")
    title = job.get("title", "Role not listed")
    company = job.get("company", "Not listed")
    location = job.get("location", "Not listed")
    posted = job.get("posted_date", "Not listed")
    url = job.get("job_url", "")
    score = job.get("ai_score")
    score_html = (
        f'<p style="margin:0 0 4px;font-size:14px;color:#374151;">Match: <strong>{score}/100</strong></p>'
        if score is not None
        else ""
    )

    return f"""
    <tr>
      <td style="padding:16px 0;border-bottom:1px solid #e5e7eb;">
        <p style="margin:0 0 6px;font-size:15px;font-weight:600;color:#111827;">{index}. {title}</p>
        <p style="margin:0 0 4px;font-size:14px;color:#374151;">Company: {company}</p>
        <p style="margin:0 0 4px;font-size:14px;color:#374151;">Location: {location}</p>
        {score_html}
        <p style="margin:0 0 4px;font-size:14px;color:#374151;">Posted: {posted}</p>
        <p style="margin:0 0 8px;font-size:14px;color:#374151;">Source: {source}</p>
        <p style="margin:0;font-size:14px;"><a href="{url}" style="color:#1d4ed8;">View job listing</a></p>
      </td>
    </tr>
    """


def send_new_jobs_email(jobs: list[dict]) -> bool:
    if not jobs:
        return False

    if not is_email_configured():
        issue = get_email_config_issue() or "Email not configured"
        logger.warning("Email not configured — skipping notification: %s", issue)
        email_status.record_failure("none", issue)
        return False

    count = len(jobs)
    subject = (
        f"Job update: {count} new listings scored {EMAIL_MIN_SCORE}+"
        if count != 1
        else f"Job update: 1 new listing scored {EMAIL_MIN_SCORE}+"
    )

    attachments: list[tuple[str, Path]] = []
    for job in jobs:
        attachments.extend(job.get("_email_attachments") or [])
    files = _usable_attachments(attachments)
    attach_note_text = (
        "Tailored resume and cover letter PDFs are attached for these listings.\n\n"
        if files
        else ""
    )
    attach_note_html = (
        '<p style="margin:0 0 16px;font-size:15px;color:#374151;">'
        "Tailored resume and cover letter PDFs are attached for these listings."
        "</p>"
        if files
        else ""
    )

    text_body = (
        "Hello,\n\n"
        f"This is your scheduled job alert. {count} new listing"
        f"{'s were' if count != 1 else ' was'} found with a match score of "
        f"{EMAIL_MIN_SCORE} or higher.\n\n"
        + attach_note_text
        + "\n".join(_format_job_text(job, i + 1) for i, job in enumerate(jobs))
        + "\n\nYou are receiving this message because job alerts are enabled "
        "on your Job Agent instance.\n\n"
        "Regards,\nJob Agent\n"
    )

    html_rows = "".join(_format_job_html(job, i + 1) for i, job in enumerate(jobs))
    html_body = f"""
    <html>
      <body style="font-family:Arial,Helvetica,sans-serif;color:#111827;line-height:1.5;margin:0;padding:0;">
        <div style="max-width:640px;margin:0 auto;padding:24px;">
          <p style="margin:0 0 16px;font-size:15px;">Hello,</p>
          <p style="margin:0 0 16px;font-size:15px;color:#374151;">
            This is your scheduled job alert. <strong>{count}</strong> new listing
            {'s were' if count != 1 else ' was'} found with a match score of
            <strong>{EMAIL_MIN_SCORE}</strong> or higher.
          </p>
          {attach_note_html}
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:16px 0;">
            {html_rows}
          </table>
          <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
            You are receiving this message because job alerts are enabled on your Job Agent instance.
          </p>
          <p style="margin:8px 0 0;font-size:13px;color:#6b7280;">Regards,<br>Job Agent</p>
        </div>
      </body>
    </html>
    """

    ok = _deliver_email(subject, text_body, html_body, attachments=files)
    if ok:
        logger.info("Job alert email sent to %s (%d jobs)", NOTIFY_EMAIL, count)
    return ok


def send_test_email() -> bool:
    """Send a simple test message to verify email delivery."""
    if not is_email_configured():
        issue = get_email_config_issue() or "Email not configured"
        email_status.record_failure("none", issue)
        return False

    subject = "Job Agent: email configuration test"
    text = (
        "Hello,\n\n"
        "This is a test message from your Job Agent application. "
        "If you received this email, delivery is working correctly.\n\n"
        "Regards,\nJob Agent\n"
    )
    html = (
        "<html><body style='font-family:Arial,sans-serif;color:#111827;'>"
        "<p>Hello,</p>"
        "<p>This is a test message from your Job Agent application. "
        "If you received this email, delivery is working correctly.</p>"
        "<p>Regards,<br>Job Agent</p>"
        "</body></html>"
    )

    ok = _deliver_email(subject, text, html)
    if ok:
        logger.info("Test email sent to %s", NOTIFY_EMAIL)
    return ok
