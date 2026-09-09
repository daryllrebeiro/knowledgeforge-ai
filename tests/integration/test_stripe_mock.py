"""Integration test against stripe-mock service (stripe/stripe-mock:v0.190.0)."""

import os
import urllib.error
import urllib.request

import pytest

from knowledgeforge.billing.stripe_client import create_checkout_session
from knowledgeforge.config import Settings

pytestmark = pytest.mark.integration

DEFAULT_STRIPE_MOCK_URL = "http://localhost:12111"


def is_stripe_mock_available(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status in (200, 404)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


@pytest.fixture
def stripe_mock_url() -> str:
    url = os.getenv("STRIPE_API_BASE", DEFAULT_STRIPE_MOCK_URL)
    if not is_stripe_mock_available(url):
        pytest.skip(f"stripe-mock service not running or reachable at {url}")
    return url


def test_stripe_mock_checkout_session(stripe_mock_url: str, monkeypatch):
    test_settings = Settings(
        stripe_api_base=stripe_mock_url,
        stripe_secret_key="sk_test_mock",
        stripe_pro_price_id="price_pro_test",
        environment="development",
    )
    monkeypatch.setattr("knowledgeforge.billing.stripe_client.get_settings", lambda: test_settings)

    session_id, session_url = create_checkout_session(
        tenant_id="tenant-123",
        tier="pro",
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert session_id
    assert session_url
