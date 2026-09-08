"""Outbound tenant webhooks with HMAC signatures, outbox dispatch, and SSRF defense."""

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
import urllib.error
import urllib.request
from uuid import UUID, uuid4

from knowledgeforge.security.ssrf import SSRFValidationError, validate_webhook_url

logger = logging.getLogger("knowledgeforge.webhooks")

MAX_DELIVERY_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30  # 30s, 60s, 120s, 240s, 480s


def generate_webhook_secret() -> str:
    """Generate a high-entropy secret for HMAC webhook signing."""
    return f"whsec_{secrets.token_hex(24)}"


def sign_webhook_payload(payload: str, secret: str, timestamp: int) -> str:
    """Generate HMAC SHA-256 signature for timestamp and payload string."""
    to_sign = f"{timestamp}.{payload}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()


def compute_signature_header(payload: str, secret: str, timestamp: int | None = None) -> str:
    """Generate X-KF-Signature header formatted as t=<timestamp>,v1=<signature>."""
    ts = timestamp if timestamp is not None else int(time.time())
    sig = sign_webhook_payload(payload, secret, ts)
    return f"t={ts},v1={sig}"


def verify_webhook_signature(payload: str, header: str, secret: str, tolerance: int = 300) -> bool:
    """Verify an incoming or outgoing webhook signature header."""
    if not header or not secret:
        return False

    parts = dict(pair.split("=", 1) for pair in header.split(",") if "=" in pair)
    ts_str = parts.get("t")
    v1_sig = parts.get("v1")

    if not ts_str or not v1_sig:
        return False

    try:
        ts = int(ts_str)
    except ValueError:
        return False

    current_time = int(time.time())
    if abs(current_time - ts) > tolerance:
        return False

    expected_sig = sign_webhook_payload(payload, secret, ts)
    return hmac.compare_digest(expected_sig, v1_sig)


def register_webhook(
    connection,
    tenant_id: UUID,
    url: str,
    secret: str,
    events: list[str],
    *,
    allow_private: bool = False,
) -> dict[str, Any]:
    """Register a new outbound webhook after verifying URL against SSRF."""
    validated_url = validate_webhook_url(url, allow_private=allow_private)
    webhook_id = uuid4()

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenant_webhooks (id, tenant_id, url, secret, events, active)
            VALUES (%s, %s, %s, %s, %s, true)
            RETURNING id, tenant_id, url, secret, events, active, created_at;
            """,
            (webhook_id, tenant_id, validated_url, secret, events),
        )
        row = cursor.fetchone()
        connection.commit()

    return {
        "id": str(row[0]),
        "tenant_id": str(row[1]),
        "url": row[2],
        "secret": row[3],
        "events": row[4],
        "active": row[5],
        "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
    }


def list_webhooks(connection, tenant_id: UUID) -> list[dict[str, Any]]:
    """List all registered webhooks for a given tenant."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, url, secret, events, active, created_at
            FROM tenant_webhooks
            WHERE tenant_id = %s
            ORDER BY created_at DESC;
            """,
            (tenant_id,),
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(r[0]),
            "tenant_id": str(r[1]),
            "url": r[2],
            "secret": r[3],
            "events": r[4],
            "active": r[5],
            "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
        }
        for r in rows
    ]


def delete_webhook(connection, tenant_id: UUID, webhook_id: UUID) -> bool:
    """Delete a webhook registration scoped to a tenant."""
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM tenant_webhooks WHERE id = %s AND tenant_id = %s RETURNING id;",
            (webhook_id, tenant_id),
        )
        row = cursor.fetchone()
        connection.commit()
    return bool(row)


def queue_webhook_deliveries(
    connection,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[UUID]:
    """Enqueue transactional outbox delivery records for active webhooks subscribed to event."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM tenant_webhooks
            WHERE tenant_id = %s AND active = true AND %s = ANY(events);
            """,
            (tenant_id, event_type),
        )
        webhooks = cursor.fetchall()
        delivery_ids = []

        for (wh_id,) in webhooks:
            delivery_id = uuid4()
            cursor.execute(
                """
                INSERT INTO webhook_deliveries
                    (id, webhook_id, tenant_id, event_type, payload, status, next_attempt_at)
                VALUES (%s, %s, %s, %s, %s, 'pending', now());
                """,
                (delivery_id, wh_id, tenant_id, event_type, json.dumps(payload)),
            )
            delivery_ids.append(delivery_id)

        connection.commit()
    return delivery_ids


def deliver_single_webhook(
    url: str,
    secret: str,
    event_type: str,
    payload_dict: dict[str, Any],
    *,
    timeout_seconds: float = 5.0,
    allow_private: bool = False,
) -> tuple[int, str]:
    """Send a single webhook HTTP POST with SSRF defense and HMAC signature."""
    validate_webhook_url(url, allow_private=allow_private)

    body_json = json.dumps(payload_dict, separators=(",", ":"))
    sig_header = compute_signature_header(body_json, secret)

    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-KF-Signature": sig_header,
            "X-KF-Event": event_type,
            "User-Agent": "KnowledgeForge-Webhook-Dispatcher/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            resp_body = resp.read(2048).decode("utf-8", errors="replace")
            return resp.status, resp_body
    except urllib.error.HTTPError as err:
        err_body = err.read(2048).decode("utf-8", errors="replace")
        return err.code, err_body
    except Exception as exc:
        return 0, str(exc)
