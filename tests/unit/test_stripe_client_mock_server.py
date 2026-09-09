"""Unit tests for stripe_api_base configuration and mock server interaction."""

from unittest.mock import MagicMock, patch

import pytest
import stripe

from knowledgeforge.billing.stripe_client import (
    _configure_stripe,
    create_checkout_session,
    create_portal_session,
)
from knowledgeforge.config import Settings


@pytest.fixture(autouse=True)
def restore_stripe_globals():
    orig_api_base = stripe.api_base
    orig_api_key = stripe.api_key
    yield
    stripe.api_base = orig_api_base
    stripe.api_key = orig_api_key


def test_configure_stripe_sets_api_base(monkeypatch):
    test_settings = Settings(
        stripe_api_base="http://localhost:12111",
        stripe_secret_key="sk_test_123",
    )
    monkeypatch.setattr("knowledgeforge.billing.stripe_client.get_settings", lambda: test_settings)

    _configure_stripe()

    assert stripe.api_base == "http://localhost:12111"
    assert stripe.api_key == "sk_test_123"


def test_create_checkout_session_with_custom_api_base(monkeypatch):
    test_settings = Settings(
        stripe_api_base="http://localhost:12111",
        stripe_secret_key="",  # defaults to sk_test_mock when api_base is present
        environment="development",
    )
    monkeypatch.setattr("knowledgeforge.billing.stripe_client.get_settings", lambda: test_settings)

    mock_session = MagicMock()
    mock_session.id = "cs_mock_12345"
    mock_session.url = "http://localhost:12111/checkout/cs_mock_12345"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        session_id, session_url = create_checkout_session(
            tenant_id="test-tenant",
            tier="pro",
            success_url="https://app.example.com/success",
            cancel_url="https://app.example.com/cancel",
        )

        assert session_id == "cs_mock_12345"
        assert session_url == "http://localhost:12111/checkout/cs_mock_12345"
        assert mock_create.called
        assert mock_create.call_args.kwargs["api_key"] == "sk_test_mock"
        assert stripe.api_base == "http://localhost:12111"


def test_create_portal_session_with_custom_api_base(monkeypatch):
    test_settings = Settings(
        stripe_api_base="http://localhost:12111",
        stripe_secret_key="sk_test_custom",
        environment="development",
    )
    monkeypatch.setattr("knowledgeforge.billing.stripe_client.get_settings", lambda: test_settings)

    mock_portal = MagicMock()
    mock_portal.url = "http://localhost:12111/portal/session_abc"

    with patch("stripe.billing_portal.Session.create", return_value=mock_portal) as mock_create:
        portal_url = create_portal_session(
            customer_id="cus_12345",
            return_url="https://app.example.com/return",
        )

        assert portal_url == "http://localhost:12111/portal/session_abc"
        assert mock_create.called
        assert mock_create.call_args.kwargs["api_key"] == "sk_test_custom"
        assert stripe.api_base == "http://localhost:12111"
