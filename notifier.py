"""Yahoo SMTP email sender for lodge availability notifications."""

import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.mail.yahoo.com"
_SMTP_PORT = 587


def send_availability_email(
    sender: str,
    recipient: str,
    password: str,
    check_in: str,
    check_out: str,
    available_lodges: list,
) -> None:
    """Send an email listing available lodges.

    Raises smtplib.SMTPException on failure.
    """
    subject = f"Yellowstone Lodge Available: {check_in} to {check_out}"

    lines = [
        f"Available Yellowstone lodges for {check_in} to {check_out}:",
        "",
    ]
    for lodge in available_lodges:
        lines.append(f"- {lodge['lodge_name']} ({lodge['hotel_code']})")
        lines.append(f"  Booking: {lodge['booking_url']}")
        lines.append("")

    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    logger.info("Sending availability email to %s...", recipient)
    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())
    logger.info("Email sent successfully.")
