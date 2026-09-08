"""Transactional email service for user verification and notifications."""

import logging
from typing import Any

import httpx

from knowledgeforge.config import get_settings

logger = logging.getLogger("knowledgeforge.mailer")

# In-memory record of sent emails for local dev, test verification, and inspection
_sent_emails: list[dict[str, Any]] = []


def get_sent_emails() -> list[dict[str, Any]]:
    """Return sent emails captured during tests or local runs."""
    return list(_sent_emails)


def clear_sent_emails() -> None:
    """Clear captured test emails."""
    _sent_emails.clear()


def send_verification_email(
    to_email: str,
    token: str,
    app_url: str = "http://localhost:8000",
) -> bool:
    """Send an email verification link to a user.

    Supports Postmark, SendGrid, and console/in-memory test mode.
    """
    settings = get_settings()
    verify_url = f"{app_url.rstrip('/')}/auth/verify-email?token={token}"
    subject = "Verify your KnowledgeForge AI email"
    body = (
        f"Welcome to KnowledgeForge AI!\n\n"
        f"Please verify your email address by clicking the link below:\n"
        f"{verify_url}\n\n"
        f"This link will expire in 24 hours.\n"
        f"If you did not sign up for an account, you can safely ignore this email."
    )

    record = {
        "to": to_email,
        "subject": subject,
        "token": token,
        "verify_url": verify_url,
        "body": body,
        "provider": settings.email_provider,
    }
    _sent_emails.append(record)

    provider = settings.email_provider.lower()

    if provider == "postmark" and settings.postmark_api_token:
        try:
            res = httpx.post(
                "https://api.postmarkapp.com/email",
                json={
                    "From": settings.email_from_address,
                    "To": to_email,
                    "Subject": subject,
                    "TextBody": body,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": settings.postmark_api_token,
                },
                timeout=10.0,
            )
            res.raise_for_status()
            logger.info("Verification email sent via Postmark to %s", to_email)
            return True
        except Exception:
            logger.exception("Failed to send verification email via Postmark to %s", to_email)
            return False

    elif provider == "sendgrid" and settings.sendgrid_api_key:
        try:
            res = httpx.post(
                "https://api.sendgrid.com/v3/mail/send",
                json={
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": settings.email_from_address},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
                headers={
                    "Authorization": f"Bearer {settings.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            res.raise_for_status()
            logger.info("Verification email sent via SendGrid to %s", to_email)
            return True
        except Exception:
            logger.exception("Failed to send verification email via SendGrid to %s", to_email)
            return False

    else:
        logger.info(
            "Verification email logged (provider=%s) for %s: %s",
            provider,
            to_email,
            verify_url,
        )
        return True
