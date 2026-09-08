"""Stripe integration client for webhook signature verification and checkout sessions."""

import hashlib
import hmac
import logging
import time
from typing import Any
from uuid import uuid4

import httpx

from knowledgeforge.config import get_settings

logger = logging.getLogger("knowledgeforge.billing")

STRIPE_API_BASE = "https://api.stripe.com/v1"


def verify_stripe_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify Stripe webhook signature using HMAC-SHA256.

    Parses timestamp and v1 signatures from Stripe-Signature header:
    `t=1492774577,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd`

    Rejects expired timestamps (replay attack prevention) and verifies signature.
    """
    if not sig_header or not secret:
        return False

    timestamp: int | None = None
    signatures: list[str] = []

    for item in sig_header.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return False

    # Check timestamp freshness against tolerance window
    now = int(time.time())
    if abs(now - timestamp) > tolerance:
        logger.warning(
            "Stripe webhook timestamp out of tolerance window (%ds diff, max %ds)",
            abs(now - timestamp),
            tolerance,
        )
        return False

    # Signed payload is: f"{timestamp}." + raw_body_bytes
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    for sig in signatures:
        if hmac.compare_digest(expected_sig, sig):
            return True

    logger.warning("Stripe webhook signature mismatch")
    return False


def create_checkout_session(
    tenant_id: str,
    tier: str,
    success_url: str,
    cancel_url: str,
    customer_id: str | None = None,
) -> tuple[str, str]:
    """Create a Stripe Checkout Session for subscription tier upgrade.

    Returns (session_id, url).
    If STRIPE_SECRET_KEY is not configured (dev/test), returns mock session.
    """
    settings = get_settings()
    if not settings.stripe_secret_key:
        mock_id = f"cs_test_{uuid4().hex[:16]}"
        mock_url = f"https://checkout.stripe.com/test/{mock_id}?tier={tier}&tenant={tenant_id}"
        return mock_id, mock_url

    price_id = (
        settings.stripe_pro_price_id
        if tier == "pro"
        else settings.stripe_enterprise_price_id
    )
    if not price_id:
        price_id = f"price_{tier}_default"

    data: dict[str, Any] = {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": tenant_id,
        "metadata[tenant_id]": tenant_id,
        "metadata[tier]": tier,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
    }
    if customer_id:
        data["customer"] = customer_id

    response = httpx.post(
        f"{STRIPE_API_BASE}/checkout/sessions",
        data=data,
        auth=(settings.stripe_secret_key, ""),
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload["id"]), str(payload["url"])


def create_portal_session(
    customer_id: str,
    return_url: str,
) -> str:
    """Create a Stripe Customer Billing Portal session for managing subscriptions.

    Returns portal session URL.
    If STRIPE_SECRET_KEY is not configured (dev/test), returns mock portal URL.
    """
    settings = get_settings()
    if not settings.stripe_secret_key:
        return f"https://billing.stripe.com/test/portal_{uuid4().hex[:16]}?customer={customer_id}"

    response = httpx.post(
        f"{STRIPE_API_BASE}/billing_portal/sessions",
        data={"customer": customer_id, "return_url": return_url},
        auth=(settings.stripe_secret_key, ""),
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload["url"])
