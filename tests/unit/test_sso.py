"""Unit tests for Enterprise OIDC SSO integration, tier enforcement, and JIT provisioning."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from knowledgeforge import api
from knowledgeforge.config import get_settings
from knowledgeforge.main import app
from knowledgeforge.security.sso import (
    SSOValidationError,
    create_sso_state,
    verify_sso_state,
)


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_sso_state_creation_and_verification():
    tenant_id = uuid4()
    state = create_sso_state(tenant_id)
    assert isinstance(state, str)
    assert len(state) > 20

    verified_tid = verify_sso_state(state)
    assert verified_tid == tenant_id


def test_sso_state_tampered_fails():
    with pytest.raises(SSOValidationError):
        verify_sso_state("tampered.state.jwt")


def test_sso_config_rejected_for_pro_tier(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    # Mock database returning pro tier
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params=()):
            pass

        def fetchone(self):
            return ("pro",)

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def mock_conn():
        yield FakeConn()

    monkeypatch.setattr(api, "get_connection", mock_conn)

    client = TestClient(app)
    response = client.put(
        "/tenant/sso/config",
        json={
            "issuer_url": "https://auth.enterprise.com",
            "client_id": "kf-client-123",
            "client_secret": "secret",
        },
    )
    assert response.status_code == 403
    assert "Enterprise" in response.json()["detail"]


def test_sso_config_and_authorize_for_enterprise_tier(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    fake_config = {
        "tenant_id": str(tenant_id),
        "enabled": True,
        "issuer_url": "https://auth.enterprise.corp",
        "client_id": "client-abc",
        "has_client_secret": True,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)
    monkeypatch.setattr(api, "save_sso_config", lambda conn, tid, **kwargs: fake_config)
    monkeypatch.setattr(api, "get_sso_config", lambda conn, tid: fake_config)
    monkeypatch.setattr(api, "validate_enterprise_tier", lambda conn, tid: None)

    client = TestClient(app)

    # 1. Update SSO Config
    put_resp = client.put(
        "/tenant/sso/config",
        json={
            "issuer_url": "https://auth.enterprise.corp",
            "client_id": "client-abc",
            "client_secret": "supersecret",
            "enabled": True,
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["issuer_url"] == "https://auth.enterprise.corp"

    # 2. Authorize SSO Initiation
    auth_resp = client.post(
        "/auth/sso/oidc/authorize",
        json={"tenant_id": str(tenant_id), "redirect_uri": "https://app.example.com/sso/callback"},
    )
    assert auth_resp.status_code == 200
    data = auth_resp.json()
    assert "authorization_url" in data
    assert "state" in data
    assert "auth.enterprise.corp" in data["authorization_url"]


def test_sso_callback_jit_provisioning_and_jwt(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    state = create_sso_state(tenant_id)

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)
    monkeypatch.setattr(api, "validate_enterprise_tier", lambda conn, tid: None)
    monkeypatch.setattr(
        api,
        "process_sso_claims",
        lambda conn, tid, claims: (user_id, tenant_id, "member"),
    )
    monkeypatch.setattr(api, "create_refresh_token", lambda conn, uid: "rt_test_123")

    client = TestClient(app)
    response = client.post(
        "/auth/sso/oidc/callback",
        json={
            "state": state,
            "claims": {
                "sub": "idp-user-123",
                "email": "employee@enterprise.corp",
                "name": "Jane Doe",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["role"] == "member"
    assert data["refresh_token"] == "rt_test_123"

    # Verify access token payload
    settings = get_settings()
    payload = jwt.decode(
        data["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert payload["sub"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["role"] == "member"
