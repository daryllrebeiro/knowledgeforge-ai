"""Unit tests for outbound tenant webhooks, HMAC signing, and SSRF defense."""

import time
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app
from knowledgeforge.security.ssrf import SSRFValidationError, validate_webhook_url
from knowledgeforge.security.webhooks import (
    compute_signature_header,
    generate_webhook_secret,
    verify_webhook_signature,
)


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# ==========================================
# 1. SSRF Defense Unit Tests
# ==========================================


def test_ssrf_rejects_loopback():
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://127.0.0.1:8080/webhook")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://localhost/hook")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://[::1]/hook")


def test_ssrf_rejects_cloud_metadata():
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://169.254.169.254/computeMetadata/v1/")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://metadata.google.internal/computeMetadata/v1/")


def test_ssrf_rejects_private_networks():
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://10.0.0.5:9000/webhook")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://192.168.1.100/webhook")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("http://172.16.50.1/webhook")


def test_ssrf_rejects_invalid_schemes():
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("file:///etc/passwd")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("ftp://example.com/webhook")

    with pytest.raises(SSRFValidationError):
        validate_webhook_url("javascript:alert(1)")


def test_ssrf_accepts_valid_public_url(monkeypatch):
    # Mock DNS resolution to return a public IP (e.g. 93.184.216.34)
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    url = "https://example.com/webhook"
    assert validate_webhook_url(url) == url


# ==========================================
# 2. HMAC Signature Unit Tests
# ==========================================


def test_hmac_signing_and_verification():
    secret = generate_webhook_secret()
    payload = '{"event":"document.ready","document_id":"doc-123"}'
    ts = int(time.time())

    header = compute_signature_header(payload, secret, timestamp=ts)
    assert header.startswith(f"t={ts},v1=")

    # Valid verification
    assert verify_webhook_signature(payload, header, secret, tolerance=300)

    # Tampered payload fails
    tampered = '{"event":"document.ready","document_id":"doc-HACK"}'
    assert not verify_webhook_signature(tampered, header, secret, tolerance=300)

    # Expired timestamp fails
    old_header = compute_signature_header(payload, secret, timestamp=ts - 400)
    assert not verify_webhook_signature(payload, old_header, secret, tolerance=300)

    # Invalid secret fails
    assert not verify_webhook_signature(payload, header, "whsec_wrong_secret", tolerance=300)


# ==========================================
# 3. API Endpoint Tests
# ==========================================


def test_create_webhook_ssrf_rejected(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)

    client = TestClient(app)
    response = client.post(
        "/tenant/webhooks",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert response.status_code == 400
    assert "SSRF violation" in response.json()["detail"]


def test_create_webhook_forbidden_for_member():
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    client = TestClient(app)
    response = client.post(
        "/tenant/webhooks",
        json={"url": "https://example.com/webhook"},
    )
    assert response.status_code == 403


def test_create_and_list_webhooks_owner(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", False)

    fake_webhooks = []

    def mock_register(conn, tid, url, secret, events, allow_private=False):
        wh = {
            "id": str(uuid4()),
            "tenant_id": str(tid),
            "url": url,
            "secret": secret,
            "events": events,
            "active": True,
            "created_at": datetime.now(UTC).isoformat(),
        }
        fake_webhooks.append(wh)
        return wh

    def mock_list(conn, tid):
        return [w for w in fake_webhooks if w["tenant_id"] == str(tid)]

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)
    monkeypatch.setattr(api, "validate_webhook_url", lambda url, **kwargs: url)
    monkeypatch.setattr(api, "register_webhook", mock_register)
    monkeypatch.setattr(api, "list_webhooks", mock_list)

    client = TestClient(app)

    # 1. Create Webhook
    resp = client.post(
        "/tenant/webhooks",
        json={"url": "https://myapp.com/events", "events": ["document.ready"]},
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["url"] == "https://myapp.com/events"
    assert created["events"] == ["document.ready"]

    # 2. List Webhooks
    list_resp = client.get("/tenant/webhooks")
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]


def test_delete_webhook_owner(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    webhook_id = uuid4()
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)
    monkeypatch.setattr(api, "delete_webhook", lambda conn, tid, wid: wid == webhook_id)

    client = TestClient(app)
    del_resp = client.delete(f"/tenant/webhooks/{webhook_id}")
    assert del_resp.status_code == 204

    # 404 for non-existent webhook
    del_404 = client.delete(f"/tenant/webhooks/{uuid4()}")
    assert del_404.status_code == 404
