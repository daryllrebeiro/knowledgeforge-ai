"""Stripe integration client for webhook signature verification and checkout sessions."""

import logging
from typing import Any
from uuid import uuid4

import stripe

from knowledgeforge.config import get_settings

logger = logging.getLogger("knowledgeforge.billing")


def verify_stripe_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify Stripe webhook signature using the official stripe SDK.

    Parses timestamp and v1 signatures from Stripe-Signature header.
    Rejects expired timestamps (replay attack prevention) and invalid signatures.
    """
    if not sig_header or not secret:
        return False

    try:
        stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=secret,
            tolerance=tolerance,
        )
        return True
    except (stripe.SignatureVerificationError, ValueError, Exception) as exc:
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        return False


def construct_stripe_event(
    payload: bytes,
    sig_header: str,
    secret: str,
    tolerance: int = 300,
) -> dict[str, Any]:
    """Construct verified Stripe event from raw payload and signature."""
    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=sig_header,
        secret=secret,
        tolerance=tolerance,
    )
    return dict(event)


def create_checkout_session(
    tenant_id: str,
    tier: str,
    success_url: str,
    cancel_url: str,
    customer_id: str | None = None,
) -> tuple[str, str]:
    """Create a Stripe Checkout Session for subscription tier upgrade.

    Returns (session_id, url).
    If LOCAL_BILLING is enabled (or in development without keys), returns mock session.
    Outside development, requires STRIPE_SECRET_KEY.
    """
    settings = get_settings()
    if settings.local_billing or (not settings.stripe_secret_key and settings.environment.lower() == "development"):
        mock_id = f"cs_test_{uuid4().hex[:16]}"
        mock_url = f"https://checkout.stripe.com/test/{mock_id}?tier={tier}&tenant={tenant_id}"
        return mock_id, mock_url

    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY must be configured when LOCAL_BILLING is disabled")

    price_id = (
        settings.stripe_pro_price_id
        if tier == "pro"
        else settings.stripe_enterprise_price_id
    )
    if not price_id:
        price_id = f"price_{tier}_default"

    params: dict[str, Any] = {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": tenant_id,
        "metadata": {"tenant_id": tenant_id, "tier": tier},
        "line_items": [{"price": price_id, "quantity": 1}],
    }
    if customer_id:
        params["customer"] = customer_id

    session = stripe.checkout.Session.create(
        api_key=settings.stripe_secret_key,
        **params,
    )
    return str(session.id), str(session.url)


def create_portal_session(
    customer_id: str,
    return_url: str,
) -> str:
    """Create a Stripe Customer Billing Portal session for managing subscriptions.

    Returns portal session URL.
    If LOCAL_BILLING is enabled (or in development without keys), returns mock portal URL.
    Outside development, requires STRIPE_SECRET_KEY.
    """
    settings = get_settings()
    if settings.local_billing or (not settings.stripe_secret_key and settings.environment.lower() == "development"):
        return f"https://billing.stripe.com/test/portal_{uuid4().hex[:16]}?customer={customer_id}"

    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY must be configured when LOCAL_BILLING is disabled")

    session = stripe.billing_portal.Session.create(
        api_key=settings.stripe_secret_key,
        customer=customer_id,
        return_url=return_url,
    )
    return str(session.url)
