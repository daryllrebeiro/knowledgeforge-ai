"""Unit tests for billing, tiered access, Stripe webhook idempotency, and dynamic budgets."""

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from knowledgeforge.billing.service import (
    claim_webhook_event,
    get_tenant_billing_info,
    process_stripe_event,
    update_tenant_tier_admin,
)
from knowledgeforge.billing.stripe_client import (
    create_checkout_session,
    create_portal_session,
    verify_stripe_signature,
)
from knowledgeforge.config import get_settings
from knowledgeforge.security.budget import (
    RedisBudgetCounter,
    get_tenant_budget_limits,
)
from tests.unit.test_budget import MockRedisBudget


# ---------------------------------------------------------------------------
# In-memory mock DB connection for billing queries
# ---------------------------------------------------------------------------

class MockCursor:
    def __init__(self, db: "MockDB"):
        self.db = db
        self._last_result: list[tuple] = []
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())
        self._idx = 0

        # INSERT INTO stripe_events ... RETURNING id
        if "INSERT INTO stripe_events" in q:
            event_id = str(params[0])
            event_type = str(params[1])
            tenant_id = params[2]
            if event_id in self.db.stripe_events:
                # Already exists and processed_at is not null -> no rows returned
                if self.db.stripe_events[event_id].get("processed_at"):
                    self._last_result = []
                else:
                    self.db.stripe_events[event_id]["processed_at"] = datetime.now(UTC)
                    self._last_result = [(uuid4(),)]
            else:
                self.db.stripe_events[event_id] = {
                    "event_type": event_type,
                    "tenant_id": tenant_id,
                    "processed_at": datetime.now(UTC),
                }
                self._last_result = [(uuid4(),)]

        # SELECT from tenants for billing info
        elif "SELECT t.tier" in q:
            tenant_id = params[0]
            tenant = self.db.tenants.get(tenant_id)
            if not tenant:
                self._last_result = []
            else:
                tier = tenant.get("tier", "free")
                tier_cfg = self.db.tiers.get(
                    tier,
                    {
                        "daily_token_budget": 10000,
                        "daily_extraction_budget": 5,
                        "unverified_token_budget": 1000,
                        "unverified_extraction_budget": 1,
                    },
                )
                has_verified = any(
                    u.get("email_verified", False)
                    for u in self.db.users.values()
                    if u.get("tenant_id") == tenant_id
                )
                self._last_result = [
                    (
                        tenant.get("tier", "free"),
                        tenant.get("subscription_status", "active"),
                        tenant.get("current_period_end"),
                        tenant.get("stripe_customer_id"),
                        tenant.get("stripe_subscription_id"),
                        tier_cfg["daily_token_budget"],
                        tier_cfg["daily_extraction_budget"],
                        tier_cfg["unverified_token_budget"],
                        tier_cfg["unverified_extraction_budget"],
                        has_verified,
                    )
                ]

        elif "SELECT daily_token_budget" in q and "FROM subscription_tiers" in q:
            tier_key = str(params[0])
            cfg = self.db.tiers.get(tier_key, self.db.tiers["free"])
            self._last_result = [
                (
                    cfg["daily_token_budget"],
                    cfg["daily_extraction_budget"],
                    cfg["unverified_token_budget"],
                    cfg["unverified_extraction_budget"],
                )
            ]

        # SELECT for resolve tenant by customer or subscription
        elif "SELECT id FROM tenants WHERE stripe_customer_id = %s" in q:
            cust_id = params[0]
            found = [tid for tid, t in self.db.tenants.items() if t.get("stripe_customer_id") == cust_id]
            self._last_result = [(found[0],)] if found else []

        elif "SELECT id FROM tenants WHERE stripe_subscription_id = %s" in q:
            sub_id = params[0]
            found = [tid for tid, t in self.db.tenants.items() if t.get("stripe_subscription_id") == sub_id]
            self._last_result = [(found[0],)] if found else []

        # UPDATE tenants SET tier = %s ... WHERE id = %s
        elif "UPDATE tenants SET tier = %s" in q and "subscription_status = 'active'" in q:
            tier, customer, subscription, tenant_id = params[0], params[1], params[2], params[3]
            if tenant_id in self.db.tenants:
                self.db.tenants[tenant_id]["tier"] = tier
                if customer:
                    self.db.tenants[tenant_id]["stripe_customer_id"] = customer
                if subscription:
                    self.db.tenants[tenant_id]["stripe_subscription_id"] = subscription
                self.db.tenants[tenant_id]["subscription_status"] = "active"
            self._last_result = []

        elif "UPDATE tenants SET subscription_status = %s" in q:
            status = str(params[0])
            period_end = params[1]
            idx = 2
            target_tier = None
            if len(params) > 4:
                target_tier = str(params[idx])
                idx += 1
            sub_id = params[idx]
            cust_id = params[idx + 1]
            for t in self.db.tenants.values():
                if t.get("stripe_subscription_id") == sub_id or t.get("stripe_customer_id") == cust_id:
                    t["subscription_status"] = status
                    if period_end:
                        t["current_period_end"] = period_end
                    if target_tier:
                        t["tier"] = target_tier
            self._last_result = []

        elif "UPDATE tenants SET tier = 'free', subscription_status = 'canceled'" in q:
            sub_id, cust_id = params[0], params[1]
            for t in self.db.tenants.values():
                if t.get("stripe_subscription_id") == sub_id or t.get("stripe_customer_id") == cust_id:
                    t["tier"] = "free"
                    t["subscription_status"] = "canceled"
                    t["stripe_subscription_id"] = None
            self._last_result = []

        elif "UPDATE tenants SET subscription_status = 'past_due'" in q:
            cust_id = params[0]
            for t in self.db.tenants.values():
                if t.get("stripe_customer_id") == cust_id:
                    t["subscription_status"] = "past_due"
            self._last_result = []

        elif "UPDATE tenants SET subscription_status = 'active'" in q:
            cust_id = params[0]
            for t in self.db.tenants.values():
                if t.get("stripe_customer_id") == cust_id:
                    t["subscription_status"] = "active"
            self._last_result = []

        # Admin update
        elif "UPDATE tenants SET tier = %s, subscription_status = %s WHERE id = %s RETURNING id" in q:
            tier, status, tenant_id = str(params[0]), str(params[1]), params[2]
            if tenant_id in self.db.tenants:
                self.db.tenants[tenant_id]["tier"] = tier
                self.db.tenants[tenant_id]["subscription_status"] = status
                self._last_result = [(tenant_id,)]
            else:
                self._last_result = []

        # Admin list tenants
        elif "SELECT t.id, t.name, t.tier" in q:
            self._last_result = [
                (
                    tid,
                    t.get("name", "Test"),
                    t.get("tier", "free"),
                    t.get("subscription_status", "active"),
                    t.get("stripe_customer_id"),
                    t.get("stripe_subscription_id"),
                    t.get("current_period_end"),
                    10000,
                    5,
                    True,
                )
                for tid, t in self.db.tenants.items()
            ]

        else:
            self._last_result = []

    def fetchone(self):
        if self._idx < len(self._last_result):
            row = self._last_result[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        return self._last_result


class MockDB:
    def __init__(self):
        self.tenants: dict[UUID, dict] = {}
        self.users: dict[UUID, dict] = {}
        self.stripe_events: dict[str, dict] = {}
        self.tiers = {
            "free": {
                "daily_token_budget": 10000,
                "daily_extraction_budget": 5,
                "unverified_token_budget": 1000,
                "unverified_extraction_budget": 1,
            },
            "pro": {
                "daily_token_budget": 1000000,
                "daily_extraction_budget": 1000,
                "unverified_token_budget": 1000,
                "unverified_extraction_budget": 1,
            },
            "enterprise": {
                "daily_token_budget": 10000000,
                "daily_extraction_budget": 10000,
                "unverified_token_budget": 1000,
                "unverified_extraction_budget": 1,
            },
        }

    def cursor(self):
        return MockCursor(self)

    def commit(self):
        pass


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_stripe_signature_verification_valid():
    secret = "whsec_test_secret_key_12345"
    payload = b'{"id": "evt_test", "type": "checkout.session.completed"}'
    now = int(time.time())

    signed = f"{now}.".encode("utf-8") + payload
    sig = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    sig_header = f"t={now},v1={sig}"

    assert verify_stripe_signature(payload, sig_header, secret) is True


def test_stripe_signature_verification_invalid_secret():
    secret = "whsec_test_secret_key_12345"
    payload = b'{"id": "evt_test"}'
    now = int(time.time())
    signed = f"{now}.".encode("utf-8") + payload
    sig = hmac.new(b"wrong_secret", signed, hashlib.sha256).hexdigest()
    sig_header = f"t={now},v1={sig}"

    assert verify_stripe_signature(payload, sig_header, secret) is False


def test_stripe_signature_verification_expired_timestamp():
    secret = "whsec_test_secret_key_12345"
    payload = b'{"id": "evt_test"}'
    # Timestamp is 10 minutes ago (tolerance is 5 min / 300s)
    old_time = int(time.time()) - 600
    signed = f"{old_time}.".encode("utf-8") + payload
    sig = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    sig_header = f"t={old_time},v1={sig}"

    assert verify_stripe_signature(payload, sig_header, secret, tolerance=300) is False


def test_stripe_signature_multi_v1_rotation():
    secret = "whsec_test_secret_key_12345"
    payload = b'{"id": "evt_test"}'
    now = int(time.time())
    signed = f"{now}.".encode("utf-8") + payload
    valid_sig = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    sig_header = f"t={now},v1=bad_old_sig_abc123,v1={valid_sig}"

    assert verify_stripe_signature(payload, sig_header, secret) is True


def test_atomic_webhook_claim_idempotency():
    db = MockDB()
    event_id = "evt_unique_12345"

    # First claim succeeds
    claimed1 = claim_webhook_event(db, event_id, "checkout.session.completed")
    assert claimed1 is True

    # Immediate second claim (replay) is rejected
    claimed2 = claim_webhook_event(db, event_id, "checkout.session.completed")
    assert claimed2 is False


def test_webhook_checkout_completed_upgrades_tier():
    db = MockDB()
    tenant_id = uuid4()
    db.tenants[tenant_id] = {
        "name": "Acme Corp",
        "tier": "free",
        "subscription_status": "active",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
    }

    event = {
        "id": "evt_checkout_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_stripe_123",
                "subscription": "sub_stripe_abc",
                "client_reference_id": str(tenant_id),
                "metadata": {"tier": "pro"},
            }
        },
    }

    res = process_stripe_event(db, event)
    assert res["status"] == "processed"
    assert db.tenants[tenant_id]["tier"] == "pro"
    assert db.tenants[tenant_id]["stripe_customer_id"] == "cus_stripe_123"
    assert db.tenants[tenant_id]["stripe_subscription_id"] == "sub_stripe_abc"
    assert db.tenants[tenant_id]["subscription_status"] == "active"

    # Replaying same event is a no-op
    replay_res = process_stripe_event(db, event)
    assert replay_res["status"] == "already_processed"


def test_webhook_subscription_deleted_downgrades_to_free():
    db = MockDB()
    tenant_id = uuid4()
    db.tenants[tenant_id] = {
        "name": "Acme Corp",
        "tier": "pro",
        "subscription_status": "active",
        "stripe_customer_id": "cus_stripe_123",
        "stripe_subscription_id": "sub_stripe_abc",
    }

    event = {
        "id": "evt_cancel_1",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_stripe_abc",
                "customer": "cus_stripe_123",
            }
        },
    }

    res = process_stripe_event(db, event)
    assert res["status"] == "processed"
    assert db.tenants[tenant_id]["tier"] == "free"
    assert db.tenants[tenant_id]["subscription_status"] == "canceled"
    assert db.tenants[tenant_id]["stripe_subscription_id"] is None


def test_failed_payment_policy_and_grace_period():
    db = MockDB()
    tenant_id = uuid4()
    now = datetime.now(UTC)

    # 1. Past due within 3 days: in grace period, tier remains Pro
    db.tenants[tenant_id] = {
        "name": "Acme",
        "tier": "pro",
        "subscription_status": "past_due",
        "current_period_end": now - timedelta(days=1),  # 1 day expired < 3 days grace
        "stripe_customer_id": "cus_1",
        "stripe_subscription_id": "sub_1",
    }
    db.users[uuid4()] = {"tenant_id": tenant_id, "email_verified": True}

    info = get_tenant_billing_info(db, tenant_id)
    assert info["in_grace_period"] is True
    assert info["tier"] == "pro"
    assert info["daily_token_budget"] == 1_000_000

    # 2. Past due expired beyond 3 days grace period: downgraded to free
    db.tenants[tenant_id]["current_period_end"] = now - timedelta(days=5)
    info_expired = get_tenant_billing_info(db, tenant_id)
    assert info_expired["in_grace_period"] is False
    assert info_expired["tier"] == "free"
    assert info_expired["daily_token_budget"] == 10_000


def test_unverified_account_restricted_budget():
    db = MockDB()
    tenant_id = uuid4()
    # Pro tier tenant, but owner email is NOT verified
    db.tenants[tenant_id] = {
        "name": "Unverified Co",
        "tier": "pro",
        "subscription_status": "active",
        "current_period_end": datetime.now(UTC) + timedelta(days=30),
    }
    db.users[uuid4()] = {"tenant_id": tenant_id, "email_verified": False}

    info = get_tenant_billing_info(db, tenant_id)
    assert info["is_email_verified"] is False
    # Capped at unverified limits (1000 tokens, 1 extraction) despite Pro tier
    assert info["daily_token_budget"] == 1000
    assert info["daily_extraction_budget"] == 1

    # Once verified, receives full Pro budget
    for u in db.users.values():
        u["email_verified"] = True

    info_verified = get_tenant_billing_info(db, tenant_id)
    assert info_verified["is_email_verified"] is True
    assert info_verified["daily_token_budget"] == 1_000_000
    assert info_verified["daily_extraction_budget"] == 1000


def test_redis_budget_counter_dynamic_limit_enforcement():
    mock_redis = MockRedisBudget()
    counter = RedisBudgetCounter(mock_redis, "tokens", daily_limit=1_000_000)
    tenant_id = "tenant_free_1"

    # Free tier limit: 10,000
    free_limit = 10_000

    # First request: 6,000 tokens (within 10,000 limit) -> allowed
    allowed, current, _ = counter.check_and_reserve(tenant_id, 6000, limit=free_limit)
    assert allowed is True
    assert current == 6000

    # Second request: 5,000 tokens (6,000 + 5,000 = 11,000 > 10,000) -> denied
    allowed, current, _ = counter.check_and_reserve(tenant_id, 5000, limit=free_limit)
    assert allowed is False
    assert current == 6000

    # If tenant upgrades to Pro (1,000,000 limit): 5,000 tokens is now allowed!
    pro_limit = 1_000_000
    allowed_pro, current_pro, _ = counter.check_and_reserve(tenant_id, 5000, limit=pro_limit)
    assert allowed_pro is True
    assert current_pro == 11000


def test_checkout_and_portal_session_mock_mode():
    session_id, url = create_checkout_session(
        tenant_id="test_tenant_1",
        tier="pro",
        success_url="https://app.com/success",
        cancel_url="https://app.com/cancel",
    )
    assert session_id.startswith("cs_test_")
    assert "tier=pro" in url

    portal_url = create_portal_session(
        customer_id="cus_123",
        return_url="https://app.com/settings",
    )
    assert "billing.stripe.com" in portal_url
    assert "customer=cus_123" in portal_url


# ---------------------------------------------------------------------------
# API Route Tests (TestClient)
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from fastapi.testclient import TestClient
from knowledgeforge import api
from knowledgeforge.main import app
from knowledgeforge.security.auth import create_access_token


@contextmanager
def _mock_billing_db(db: MockDB):
    yield db


def test_api_stripe_webhook_rejects_invalid_signature(monkeypatch):
    db = MockDB()
    monkeypatch.setattr(api, "get_connection", lambda: _mock_billing_db(db))
    settings = get_settings().model_copy(
        update={"stripe_webhook_secret": "whsec_test_secret_123"}
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    # Post with invalid signature header
    res = client.post(
        "/billing/webhook",
        content=b'{"id": "evt_1", "type": "checkout.session.completed"}',
        headers={"stripe-signature": "t=12345,v1=invalid_sig"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid Stripe signature"


def test_api_stripe_webhook_accepts_valid_signature(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    db.tenants[tenant_id] = {
        "name": "Acme",
        "tier": "free",
        "subscription_status": "active",
    }
    monkeypatch.setattr(api, "get_connection", lambda: _mock_billing_db(db))
    secret = "whsec_test_secret_123"
    settings = get_settings().model_copy(
        update={"stripe_webhook_secret": secret}
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    raw_payload = (
        f'{{"id": "evt_api_1", "type": "checkout.session.completed", '
        f'"data": {{"object": {{"client_reference_id": "{tenant_id}", "customer": "cus_1", "subscription": "sub_1", "metadata": {{"tier": "pro"}}}}}}}}'
    ).encode("utf-8")

    now = int(time.time())
    sig = hmac.new(secret.encode("utf-8"), f"{now}.".encode("utf-8") + raw_payload, hashlib.sha256).hexdigest()

    client = TestClient(app)
    res = client.post(
        "/billing/webhook",
        content=raw_payload,
        headers={"stripe-signature": f"t={now},v1={sig}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "processed"
    assert db.tenants[tenant_id]["tier"] == "pro"


def test_api_billing_subscription_endpoint(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    user_id = uuid4()
    db.tenants[tenant_id] = {
        "name": "Acme",
        "tier": "pro",
        "subscription_status": "active",
        "current_period_end": datetime.now(UTC) + timedelta(days=20),
        "stripe_customer_id": "cus_sub_1",
        "stripe_subscription_id": "sub_sub_1",
    }
    db.users[user_id] = {"tenant_id": tenant_id, "email_verified": True}

    monkeypatch.setattr(api, "get_connection", lambda: _mock_billing_db(db))
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    client = TestClient(app)
    res = client.get("/billing/subscription")
    assert res.status_code == 200
    data = res.json()
    assert data["tier"] == "pro"
    assert data["daily_token_budget"] == 1_000_000
    assert data["daily_extraction_budget"] == 1000
    assert data["is_email_verified"] is True


def test_api_billing_checkout_requires_owner(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    user_id = uuid4()
    db.tenants[tenant_id] = {"name": "Acme", "tier": "free"}
    monkeypatch.setattr(api, "get_connection", lambda: _mock_billing_db(db))

    client = TestClient(app)
    payload = {
        "tier": "pro",
        "success_url": "https://app.com/success",
        "cancel_url": "https://app.com/cancel",
    }

    # Member role is rejected with 403
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)
    res_member = client.post("/billing/create-checkout-session", json=payload)
    assert res_member.status_code == 403

    # Owner role is accepted with 200
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", False)
    res_owner = client.post("/billing/create-checkout-session", json=payload)
    assert res_owner.status_code == 200
    assert res_owner.json()["url"].startswith("https://checkout.stripe.com")


def test_api_admin_billing_requires_platform_admin(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    user_id = uuid4()
    db.tenants[tenant_id] = {"name": "Acme", "tier": "free"}
    monkeypatch.setattr(api, "get_connection", lambda: _mock_billing_db(db))

    settings = get_settings().model_copy(update={"admin_console_enabled": True})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)

    # Regular owner (not platform admin) -> 403
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", False)
    res_forbidden = client.get("/admin/billing/tenants")
    assert res_forbidden.status_code == 403

    # Platform admin -> 200
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", True)
    res_admin = client.get("/admin/billing/tenants")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert len(data) == 1
    assert data[0]["tier"] == "free"

    # Admin manually updates tier to enterprise
    update_res = client.put(
        f"/admin/billing/tenants/{tenant_id}/tier",
        json={"tier": "enterprise", "subscription_status": "active"},
    )
    assert update_res.status_code == 200
    assert db.tenants[tenant_id]["tier"] == "enterprise"

