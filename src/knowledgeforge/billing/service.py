"""Billing service: Webhook event processing, atomic idempotency, and tier management."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from knowledgeforge.config import get_settings

logger = logging.getLogger("knowledgeforge.billing")


def claim_webhook_event(
    connection,
    event_id: str,
    event_type: str,
    tenant_id: UUID | str | None = None,
) -> bool:
    """Atomically insert and claim a Stripe webhook event.

    Uses `ON CONFLICT (event_id) DO UPDATE ... WHERE processed_at IS NULL RETURNING id`
    to guarantee single-execution semantics even under concurrent webhook delivery.
    Returns True if this invocation successfully claimed the event, False if already claimed.
    """
    tenant_uuid: UUID | None = None
    if tenant_id:
        try:
            tenant_uuid = UUID(str(tenant_id))
        except (ValueError, TypeError):
            tenant_uuid = None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO stripe_events (event_id, event_type, tenant_id, processed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (event_id) DO UPDATE
            SET processed_at = EXCLUDED.processed_at
            WHERE stripe_events.processed_at IS NULL
            RETURNING id;
            """,
            (event_id, event_type, tenant_uuid),
        )
        row = cursor.fetchone()
        connection.commit()
        return row is not None


def resolve_tenant_id_from_stripe_data(connection, data_object: dict) -> UUID | None:
    """Resolve tenant ID from Stripe event metadata, customer ID, or subscription ID."""
    # 1. Direct metadata or client_reference_id
    raw_tenant_id = data_object.get("client_reference_id") or data_object.get("metadata", {}).get(
        "tenant_id"
    )
    if raw_tenant_id:
        try:
            return UUID(str(raw_tenant_id))
        except (ValueError, TypeError):
            pass

    # 2. Look up by stripe_customer_id
    customer_id = data_object.get("customer")
    if customer_id:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tenants WHERE stripe_customer_id = %s",
                (customer_id,),
            )
            row = cursor.fetchone()
            if row:
                return UUID(str(row[0]))

    # 3. Look up by stripe_subscription_id
    subscription_id = data_object.get("subscription") or data_object.get("id")
    if subscription_id:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tenants WHERE stripe_subscription_id = %s",
                (subscription_id,),
            )
            row = cursor.fetchone()
            if row:
                return UUID(str(row[0]))

    return None


def process_stripe_event(connection, event: dict, redis_client=None) -> dict:
    """Process a verified Stripe webhook event atomically.

    Idempotent: Duplicate/replayed events are identified via `stripe_events`
    table and return status 'already_processed'.
    """
    event_id = event.get("id", "")
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    if not event_id or not event_type:
        return {"status": "ignored", "reason": "missing_event_fields"}

    tenant_id = resolve_tenant_id_from_stripe_data(connection, data_object)

    # Atomic claim: ensures only one worker processes this webhook event
    if not claim_webhook_event(connection, event_id, event_type, tenant_id):
        logger.info("Stripe event %s already claimed/processed; skipping replay", event_id)
        return {"status": "already_processed", "event_id": event_id}

    logger.info(
        "Processing Stripe webhook %s of type %s for tenant %s", event_id, event_type, tenant_id
    )

    with connection.cursor() as cursor:
        if event_type == "checkout.session.completed":
            customer = data_object.get("customer")
            subscription = data_object.get("subscription")
            tier = data_object.get("metadata", {}).get("tier", "pro")
            if tier not in ("free", "pro", "enterprise"):
                tier = "pro"

            if tenant_id:
                cursor.execute(
                    """
                    UPDATE tenants
                    SET tier = %s,
                        stripe_customer_id = COALESCE(%s, stripe_customer_id),
                        stripe_subscription_id = COALESCE(%s, stripe_subscription_id),
                        subscription_status = 'active'
                    WHERE id = %s
                    """,
                    (tier, customer, subscription, tenant_id),
                )
                connection.commit()

        elif event_type == "customer.subscription.updated":
            sub_id = data_object.get("id")
            customer_id = data_object.get("customer")
            status = data_object.get("status", "active")
            period_end_ts = data_object.get("current_period_end")
            period_end = datetime.fromtimestamp(period_end_ts, UTC) if period_end_ts else None
            tier = data_object.get("metadata", {}).get("tier")

            # If canceled, downgrade to free
            target_tier = "free" if status in ("canceled", "unpaid") else tier

            query = """
                UPDATE tenants
                SET subscription_status = %s,
                    current_period_end = COALESCE(%s, current_period_end)
            """
            params: list[object] = [status, period_end]

            if target_tier:
                query += ", tier = %s"
                params.append(target_tier)

            query += " WHERE stripe_subscription_id = %s OR stripe_customer_id = %s"
            params.extend([sub_id, customer_id])

            cursor.execute(query, params)
            connection.commit()

        elif event_type == "customer.subscription.deleted":
            sub_id = data_object.get("id")
            customer_id = data_object.get("customer")
            cursor.execute(
                """
                UPDATE tenants
                SET tier = 'free',
                    subscription_status = 'canceled',
                    stripe_subscription_id = NULL
                WHERE stripe_subscription_id = %s OR stripe_customer_id = %s
                """,
                (sub_id, customer_id),
            )
            connection.commit()

        elif event_type == "invoice.payment_failed":
            customer_id = data_object.get("customer")
            cursor.execute(
                """
                UPDATE tenants
                SET subscription_status = 'past_due'
                WHERE stripe_customer_id = %s
                """,
                (customer_id,),
            )
            connection.commit()

        elif event_type == "invoice.payment_succeeded":
            customer_id = data_object.get("customer")
            cursor.execute(
                """
                UPDATE tenants
                SET subscription_status = 'active'
                WHERE stripe_customer_id = %s
                """,
                (customer_id,),
            )
            connection.commit()

    # Invalidate cached tenant budget in Redis
    if tenant_id:
        from knowledgeforge.security.budget import invalidate_tenant_budget_cache

        invalidate_tenant_budget_cache(tenant_id, redis_client=redis_client)

    return {
        "status": "processed",
        "event_id": event_id,
        "event_type": event_type,
        "tenant_id": str(tenant_id) if tenant_id else None,
    }


def get_tenant_billing_info(connection, tenant_id: UUID) -> dict:
    """Retrieve full subscription, limits, and email verification status for a tenant."""
    settings = get_settings()
    now = datetime.now(UTC)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.tier,
                   t.subscription_status,
                   t.current_period_end,
                   t.stripe_customer_id,
                   t.stripe_subscription_id,
                   st.daily_token_budget,
                   st.daily_extraction_budget,
                   st.unverified_token_budget,
                   st.unverified_extraction_budget,
                   EXISTS (
                       SELECT 1 FROM users u
                       WHERE u.tenant_id = t.id AND u.email_verified = true
                   ) AS has_verified_user
            FROM tenants t
            LEFT JOIN subscription_tiers st ON st.tier = t.tier
            WHERE t.id = %s
            """,
            (tenant_id,),
        )
        row = cursor.fetchone()

    if not row:
        return {
            "tenant_id": tenant_id,
            "tier": "free",
            "subscription_status": "active",
            "current_period_end": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "daily_token_budget": settings.daily_token_budget,
            "daily_extraction_budget": settings.daily_extraction_budget,
            "is_email_verified": False,
            "in_grace_period": False,
        }

    (
        tier,
        sub_status,
        period_end,
        cust_id,
        sub_id,
        daily_token,
        daily_extract,
        unver_token,
        unver_extract,
        has_verified_user,
    ) = row

    effective_tier = tier or "free"
    in_grace_period = False

    # Check failed payment policy:
    # If past_due, check if within grace period (e.g. 3 days)
    if sub_status == "past_due":
        grace_days = settings.stripe_payment_grace_period_days
        if period_end:
            grace_expiry = period_end + timedelta(days=grace_days)
            if now <= grace_expiry:
                in_grace_period = True
            else:
                effective_tier = "free"
        else:
            in_grace_period = True
    elif sub_status in ("canceled", "unpaid"):
        effective_tier = "free"

    # If effective tier was downgraded from paid tier, update limits to effective tier
    if effective_tier != tier:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT daily_token_budget, daily_extraction_budget, unverified_token_budget, unverified_extraction_budget FROM subscription_tiers WHERE tier = %s",
                (effective_tier,),
            )
            tier_row = cursor.fetchone()
            if tier_row:
                daily_token, daily_extract, unver_token, unver_extract = tier_row
            else:
                daily_token = 10000
                daily_extract = 5
                unver_token = 1000
                unver_extract = 1

    # Email verification limit application
    if not has_verified_user:
        token_budget = unver_token or 1000
        extraction_budget = unver_extract or 1
    else:
        token_budget = daily_token or 10000
        extraction_budget = daily_extract or 5

    return {
        "tenant_id": tenant_id,
        "tier": effective_tier,
        "subscription_status": sub_status,
        "current_period_end": period_end,
        "stripe_customer_id": cust_id,
        "stripe_subscription_id": sub_id,
        "daily_token_budget": token_budget,
        "daily_extraction_budget": extraction_budget,
        "is_email_verified": bool(has_verified_user),
        "in_grace_period": in_grace_period,
    }


def list_tenants_billing_admin(connection) -> list[dict]:
    """List billing, tier, and verification overview across all tenants for platform admin."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.id,
                   t.name,
                   t.tier,
                   t.subscription_status,
                   t.stripe_customer_id,
                   t.stripe_subscription_id,
                   t.current_period_end,
                   st.daily_token_budget,
                   st.daily_extraction_budget,
                   EXISTS (
                       SELECT 1 FROM users u
                       WHERE u.tenant_id = t.id AND u.email_verified = true
                   ) AS has_verified_user
            FROM tenants t
            LEFT JOIN subscription_tiers st ON st.tier = t.tier
            ORDER BY t.created_at DESC
            """
        )
        rows = cursor.fetchall()

    results: list[dict] = []
    for row in rows:
        results.append(
            {
                "tenant_id": UUID(str(row[0])),
                "tenant_name": row[1],
                "tier": row[2] or "free",
                "subscription_status": row[3] or "active",
                "stripe_customer_id": row[4],
                "stripe_subscription_id": row[5],
                "current_period_end": row[6],
                "token_limit": row[7] or 10000,
                "extraction_limit": row[8] or 5,
                "is_email_verified": bool(row[9]),
            }
        )
    return results


def update_tenant_tier_admin(
    connection,
    tenant_id: UUID,
    tier: str,
    status: str = "active",
    redis_client=None,
) -> bool:
    """Manually update a tenant's subscription tier (Platform Admin only)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE tenants
            SET tier = %s,
                subscription_status = %s
            WHERE id = %s
            RETURNING id;
            """,
            (tier, status, tenant_id),
        )
        row = cursor.fetchone()
        connection.commit()

    if row and redis_client is not None:
        from knowledgeforge.security.budget import invalidate_tenant_budget_cache

        invalidate_tenant_budget_cache(tenant_id, redis_client=redis_client)

    return row is not None
