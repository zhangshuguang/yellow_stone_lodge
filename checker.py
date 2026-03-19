"""Main entry point for the Yellowstone lodge availability checker."""

import hashlib
import logging
import os
import smtplib
import sys
from datetime import date
from pathlib import Path

import yaml

from api import find_available_lodges
from notifier import send_availability_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_SEEN_PATH = ".seen_lodges"


def load_config(path: str = "config.yaml") -> dict:
    """Load and validate config.yaml. Exits with message on error."""
    config_path = Path(path)
    if not config_path.exists():
        sys.exit(f"Error: config file not found: {path}\nCopy config.yaml.example to config.yaml and fill it in.")

    with config_path.open() as f:
        config = yaml.safe_load(f)

    required = ["check_in", "check_out", "lodges", "email"]
    for key in required:
        if key not in config:
            sys.exit(f"Error: missing required config key: '{key}'")

    email_cfg = config["email"]
    for key in ["sender", "recipient"]:
        if key not in email_cfg:
            sys.exit(f"Error: missing required email config key: '{key}'")

    # Validate dates
    try:
        ci = date.fromisoformat(config["check_in"])
        co = date.fromisoformat(config["check_out"])
    except ValueError as exc:
        sys.exit(f"Error: invalid date in config: {exc}")

    today = date.today()
    if ci < today:
        sys.exit(f"Error: check_in date {config['check_in']} is in the past.")
    if co <= ci:
        sys.exit(f"Error: check_out ({config['check_out']}) must be after check_in ({config['check_in']}).")

    return config


def load_seen_hashes(path: str = _SEEN_PATH) -> set:
    """Load previously seen lodge hashes. Returns empty set if file missing."""
    p = Path(path)
    if not p.exists():
        return set()
    return set(p.read_text().splitlines())


def save_seen_hashes(hashes: set, path: str = _SEEN_PATH) -> None:
    """Write seen hashes to file, one per line."""
    Path(path).write_text("\n".join(sorted(hashes)) + "\n" if hashes else "")


def compute_lodge_hash(lodge: dict) -> str:
    """SHA256 hash of hotel_code + check_in to deduplicate notifications."""
    raw = f"{lodge['hotel_code']}|{lodge['check_in']}"
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> None:
    config = load_config()

    password = os.environ.get("YAHOO_SMTP_PASSWORD")
    if not password:
        sys.exit("Error: YAHOO_SMTP_PASSWORD environment variable is not set.")

    check_in = config["check_in"]
    check_out = config["check_out"]
    lodge_filter = config["lodges"]
    email_cfg = config["email"]

    logger.info("Checking availability: %s to %s (lodges=%s)", check_in, check_out, lodge_filter)

    try:
        available = find_available_lodges(check_in, check_out, lodge_filter)
    except Exception as exc:
        from curl_cffi.requests import RequestsError
        if isinstance(exc, RequestsError):
            logger.warning("Network error fetching availability: %s", exc)
            sys.exit(0)
        elif isinstance(exc, ValueError):
            logger.warning("Bad API response: %s", exc)
            sys.exit(0)
        raise

    logger.info("Found %d available lodge(s).", len(available))

    seen = load_seen_hashes()
    new_lodges = [l for l in available if compute_lodge_hash(l) not in seen]

    if not new_lodges:
        logger.info("No new availability (all results already notified). Done.")
        return

    logger.info("New availability found: %d lodge(s). Sending email...", len(new_lodges))
    for lodge in new_lodges:
        logger.info("  %s (%s) — %s", lodge["lodge_name"], lodge["hotel_code"], lodge["booking_url"])

    try:
        send_availability_email(
            sender=email_cfg["sender"],
            recipient=email_cfg["recipient"],
            password=password,
            check_in=check_in,
            check_out=check_out,
            available_lodges=new_lodges,
        )
    except smtplib.SMTPException as exc:
        logger.error("Failed to send email: %s", exc)
        sys.exit(1)

    updated_seen = seen | {compute_lodge_hash(l) for l in new_lodges}
    save_seen_hashes(updated_seen)
    logger.info("Saved %d seen hash(es). Done.", len(updated_seen))


if __name__ == "__main__":
    main()
