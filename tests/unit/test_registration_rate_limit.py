from contextlib import contextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from knowledgeforge import api, config
from knowledgeforge.limits import TokenBucketLimiter
from knowledgeforge.main import app


def test_registration_rate_limiter_rejects_after_hourly_capacity() -> None:
    limiter = TokenBucketLimiter()
    # Capacity 2 per hour
    limiter.check("global", "register", capacity=2, window_seconds=3600)
    limiter.check("global", "register", capacity=2, window_seconds=3600)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check("global", "register", capacity=2, window_seconds=3600)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Rate limit exceeded"


@contextmanager
def _mock_db_connection():
    conn = MagicMock()
    cursor = MagicMock()
    tenant_id = uuid4()
    user_id = uuid4()
    # Mock RETURNING id for tenant, user, and verification token
    cursor.fetchone.side_effect = [(tenant_id,), (user_id,), (uuid4(),)]
    conn.transaction.return_value.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    yield conn


def test_api_register_endpoint_enforces_global_rate_limit(monkeypatch) -> None:
    fresh_limiter = TokenBucketLimiter()
    monkeypatch.setattr(api, "limiter", fresh_limiter)
    monkeypatch.setattr(api, "get_connection", _mock_db_connection)

    settings = config.get_settings().model_copy(
        update={
            "registration_rate_limit_per_hour": 1,
            "auth_rate_limit_per_minute": 100,
        }
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    payload = {
        "email": "rate_test@example.com",
        "password": "Password123!",
        "tenant_name": "RateTestTenant",
    }

    # First attempt passes rate limit check and succeeds
    res1 = client.post("/auth/register", json=payload)
    assert res1.status_code == 201

    # Second attempt MUST be blocked by the global registration rate limiter with 429
    res2 = client.post("/auth/register", json=payload)
    assert res2.status_code == 429
    assert res2.json()["detail"] == "Rate limit exceeded"


def test_api_register_endpoint_enforces_per_ip_rate_limit(monkeypatch) -> None:
    fresh_limiter = TokenBucketLimiter()
    monkeypatch.setattr(api, "limiter", fresh_limiter)
    monkeypatch.setattr(api, "get_connection", _mock_db_connection)

    settings = config.get_settings().model_copy(
        update={
            "registration_rate_limit_per_hour": 100,
            "auth_rate_limit_per_minute": 1,
        }
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    payload = {
        "email": "ip_test@example.com",
        "password": "Password123!",
        "tenant_name": "IpTestTenant",
    }

    res1 = client.post("/auth/register", json=payload)
    assert res1.status_code == 201

    # Second attempt from same IP is blocked by per-IP limit
    res2 = client.post("/auth/register", json=payload)
    assert res2.status_code == 429
    assert res2.json()["detail"] == "Rate limit exceeded"
