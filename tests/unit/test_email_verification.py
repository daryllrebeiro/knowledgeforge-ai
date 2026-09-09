"""Unit tests for email verification: token lifecycle, atomic consumption, rate limits, and budget policy."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.config import get_settings
from knowledgeforge.limits import TokenBucketLimiter
from knowledgeforge.main import app
from knowledgeforge.security.auth import (
    _hash_token,
    consume_email_verification_token,
    create_email_verification_token,
)
from knowledgeforge.security.mailer import (
    clear_sent_emails,
    get_sent_emails,
    send_verification_email,
)

# ---------------------------------------------------------------------------
# Mock DB connection for verification tokens
# ---------------------------------------------------------------------------


class MockVerificationDB:
    def __init__(self):
        self.users: dict[UUID, dict] = {}
        self.tokens: dict[str, dict] = {}  # keyed by token_hash

    def cursor(self):
        return MockVerificationCursor(self)

    def commit(self):
        pass

    def transaction(self):
        @contextmanager
        def _tx():
            yield self

        return _tx()


class MockVerificationCursor:
    def __init__(self, db: MockVerificationDB):
        self.db = db
        self._last_result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())

        # INSERT INTO email_verification_tokens
        if "INSERT INTO email_verification_tokens" in q:
            user_id = params[0]
            token_hash = params[1]
            expires_at = params[2]
            token_id = uuid4()
            self.db.tokens[token_hash] = {
                "id": token_id,
                "user_id": user_id,
                "expires_at": expires_at,
            }
            self._last_result = [(token_id,)]

        # DELETE FROM email_verification_tokens WHERE token_hash = %s AND expires_at > now() RETURNING user_id
        elif "DELETE FROM email_verification_tokens" in q:
            token_hash = params[0]
            token_data = self.db.tokens.get(token_hash)
            now = datetime.now(UTC)
            if token_data and token_data["expires_at"] > now:
                # Atomically delete
                del self.db.tokens[token_hash]
                self._last_result = [(token_data["user_id"],)]
            else:
                self._last_result = []

        # UPDATE users SET email_verified = true WHERE id = %s RETURNING tenant_id
        elif "UPDATE users SET email_verified = true" in q:
            user_id = params[0]
            if user_id in self.db.users:
                self.db.users[user_id]["email_verified"] = True
                self._last_result = [(self.db.users[user_id]["tenant_id"],)]
            else:
                self._last_result = []

        # SELECT id, email_verified FROM users WHERE email = %s
        elif "SELECT id, email_verified FROM users" in q:
            email = params[0]
            found = [
                (uid, u["email_verified"])
                for uid, u in self.db.users.items()
                if u.get("email") == email
            ]
            self._last_result = found if found else []

        # INSERT INTO tenants
        elif "INSERT INTO tenants" in q:
            tenant_id = uuid4()
            self._last_result = [(tenant_id,)]

        # INSERT INTO users
        elif "INSERT INTO users" in q:
            tenant_id = params[0]
            email = params[1]
            user_id = uuid4()
            self.db.users[user_id] = {
                "tenant_id": tenant_id,
                "email": email,
                "email_verified": False,
            }
            self._last_result = [(user_id,)]

        # INSERT INTO tenant_memberships
        elif "INSERT INTO tenant_memberships" in q:
            self._last_result = []

        else:
            self._last_result = []

    def fetchone(self):
        if self._last_result:
            return self._last_result[0]
        return None

    def fetchall(self):
        return self._last_result or []


@contextmanager
def _mock_conn(db: MockVerificationDB):
    yield db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_and_atomic_consume_verification_token():
    db = MockVerificationDB()
    user_id = uuid4()
    tenant_id = uuid4()
    db.users[user_id] = {
        "tenant_id": tenant_id,
        "email": "verify@example.com",
        "email_verified": False,
    }

    # 1. Generate token
    token_id, token = create_email_verification_token(db, user_id, expiry_hours=24)
    token_hash = _hash_token(token)
    assert token_hash in db.tokens
    assert db.users[user_id]["email_verified"] is False

    # 2. First atomic consumption succeeds
    success, consumed_user, consumed_tenant = consume_email_verification_token(db, token)
    assert success is True
    assert consumed_user == user_id
    assert consumed_tenant == tenant_id
    assert db.users[user_id]["email_verified"] is True
    assert token_hash not in db.tokens

    # 3. Second consumption attempt with the exact same token fails (single-use invariant)
    success_replay, _, _ = consume_email_verification_token(db, token)
    assert success_replay is False


def test_verification_token_expires():
    db = MockVerificationDB()
    user_id = uuid4()
    tenant_id = uuid4()
    db.users[user_id] = {
        "tenant_id": tenant_id,
        "email": "expired@example.com",
        "email_verified": False,
    }

    # Create token with negative expiry (already expired)
    token_id, token = create_email_verification_token(db, user_id, expiry_hours=-1)

    success, _, _ = consume_email_verification_token(db, token)
    assert success is False
    assert db.users[user_id]["email_verified"] is False


def test_mailer_service_captures_verification_email():
    clear_sent_emails()
    sent = send_verification_email("user@example.com", "token_xyz_123")
    assert sent is True

    emails = get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["to"] == "user@example.com"
    assert "token_xyz_123" in emails[0]["verify_url"]
    assert emails[0]["subject"] == "Verify your KnowledgeForge AI email"


def test_api_verify_email_endpoint(monkeypatch):
    db = MockVerificationDB()
    user_id = uuid4()
    tenant_id = uuid4()
    db.users[user_id] = {
        "tenant_id": tenant_id,
        "email": "api_verify@example.com",
        "email_verified": False,
    }
    _, token = create_email_verification_token(db, user_id)

    monkeypatch.setattr(api, "get_connection", lambda: _mock_conn(db))
    client = TestClient(app)

    # 1. Valid token verification
    res = client.post("/auth/verify-email", json={"token": token})
    assert res.status_code == 200
    assert res.json()["verified"] is True
    assert db.users[user_id]["email_verified"] is True

    # 2. Second attempt fails with 400 Bad Request
    res_replay = client.post("/auth/verify-email", json={"token": token})
    assert res_replay.status_code == 400
    assert "Invalid, expired, or already-used" in res_replay.json()["detail"]


def test_api_resend_verification_rate_limited(monkeypatch):
    db = MockVerificationDB()
    user_id = uuid4()
    tenant_id = uuid4()
    db.users[user_id] = {
        "tenant_id": tenant_id,
        "email": "spam_target@example.com",
        "email_verified": False,
    }

    monkeypatch.setattr(api, "get_connection", lambda: _mock_conn(db))
    fresh_limiter = TokenBucketLimiter()
    monkeypatch.setattr(api, "limiter", fresh_limiter)

    settings = get_settings().model_copy(update={"email_verification_rate_limit_per_minute": 2})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    payload = {"email": "spam_target@example.com"}

    # Call 1 & 2 pass within capacity=2
    res1 = client.post("/auth/resend-verification", json=payload)
    assert res1.status_code == 200

    res2 = client.post("/auth/resend-verification", json=payload)
    assert res2.status_code == 200

    # Call 3 exceeds rate limit -> 429
    res3 = client.post("/auth/resend-verification", json=payload)
    assert res3.status_code == 429
    assert "Rate limit exceeded" in res3.json()["detail"]


def test_registration_flow_dispatches_verification_email(monkeypatch):
    clear_sent_emails()
    db = MockVerificationDB()
    monkeypatch.setattr(api, "get_connection", lambda: _mock_conn(db))

    client = TestClient(app)
    reg_payload = {
        "email": "new_signup@example.com",
        "password": "SecurePassword123!",
        "tenant_name": "NewCo",
    }

    res = client.post("/auth/register", json=reg_payload)
    assert res.status_code == 201

    emails = get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["to"] == "new_signup@example.com"
    token = emails[0]["token"]

    # The user is initially unverified
    created_user = list(db.users.values())[0]
    assert created_user["email_verified"] is False

    # Clicking the link verifies the user
    verify_res = client.post("/auth/verify-email", json={"token": token})
    assert verify_res.status_code == 200
    assert created_user["email_verified"] is True
