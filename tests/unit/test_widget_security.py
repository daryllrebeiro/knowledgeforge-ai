"""Unit tests for Phase 8 Item 9: Embeddable White-Label Widget Security & CORS Isolation."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from knowledgeforge.widget.service import (
    TenantWidgetRow,
    WidgetOriginForbiddenError,
    _normalize_origin,
    check_widget_rate_limit,
    generate_embed_js,
    validate_widget_origin,
)


def test_normalize_origin_prohibits_wildcard():
    with pytest.raises(ValueError) as exc_info:
        _normalize_origin("*")
    assert "Wildcard '*' origins are strictly prohibited" in str(exc_info.value)


def test_validate_widget_origin_strict_matching():
    widget = TenantWidgetRow(
        id=uuid4(),
        tenant_id=uuid4(),
        collection_id=uuid4(),
        api_key_id=uuid4(),
        name="Support Bot",
        allowed_origins=["https://help.acme.com", "https://app.acme.com"],
        primary_color="#2563eb",
        rate_limit_per_minute=30,
        daily_budget_tokens=50000,
        is_active=True,
        created_at=datetime.now(UTC),
    )

    # 1. Exact matches pass
    validate_widget_origin(widget, "https://help.acme.com")
    validate_widget_origin(widget, "https://app.acme.com/")

    # 2. Suffix / Subdomain attack fails
    with pytest.raises(WidgetOriginForbiddenError) as exc:
        validate_widget_origin(widget, "https://help.acme.com.attacker.net")
    assert "is not authorized" in str(exc.value)

    # 3. Protocol downgrade fails
    with pytest.raises(WidgetOriginForbiddenError):
        validate_widget_origin(widget, "http://help.acme.com")

    # 4. Port spoofing fails
    with pytest.raises(WidgetOriginForbiddenError):
        validate_widget_origin(widget, "https://help.acme.com:8443")

    # 5. Missing origin header fails
    with pytest.raises(WidgetOriginForbiddenError):
        validate_widget_origin(widget, None)


def test_check_widget_rate_limit_exceeded():
    widget = TenantWidgetRow(
        id=uuid4(),
        tenant_id=uuid4(),
        collection_id=uuid4(),
        api_key_id=uuid4(),
        name="Support Bot",
        allowed_origins=["https://help.acme.com"],
        primary_color="#2563eb",
        rate_limit_per_minute=2,
        daily_budget_tokens=50000,
        is_active=True,
        created_at=datetime.now(UTC),
    )

    client_ip = f"203.0.113.{uuid4().hex[:4]}"
    # 2 requests succeed
    check_widget_rate_limit(widget, client_ip)
    check_widget_rate_limit(widget, client_ip)

    # 3rd request in same minute triggers 429
    with pytest.raises(HTTPException) as exc_info:
        check_widget_rate_limit(widget, client_ip)

    assert exc_info.value.status_code == 429
    assert "Rate limit exceeded" in exc_info.value.detail


def test_generate_embed_js_omits_api_key_secret():
    widget_id = uuid4()
    script = generate_embed_js(widget_id, "https://api.knowledgeforge.io")

    assert str(widget_id) in script
    assert "https://api.knowledgeforge.io" in script
    assert "knowledgeforge-widget-container" in script
    # Verify no secret key embedded in public script
    assert "kf_secret" not in script
    assert "api_key_secret" not in script
