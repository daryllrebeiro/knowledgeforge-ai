"""Unit tests for Phase 8 Item 10: Mobile App Device Lifecycle, Push Notifications, and Token Rotation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.main import app
from knowledgeforge.mobile.notifications import (
    MockPushNotificationProvider,
    clear_dispatched_push_notifications,
    get_dispatched_push_notifications,
    notify_user_devices,
    register_user_device,
    unregister_user_device,
)
from knowledgeforge.security.refresh import (
    InvalidRefreshToken,
    rotate_refresh_token,
)

client = TestClient(app)


def test_register_and_unregister_device():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()
    dev_id = uuid4()

    mock_cursor.fetchone.return_value = (
        dev_id,
        user_id,
        tenant_id,
        "ios",
        "apns-device-token-12345",
        True,
        datetime.now(UTC),
        datetime.now(UTC),
    )

    # 1. Successful registration
    dev = register_user_device(
        mock_conn,
        user_id=user_id,
        tenant_id=tenant_id,
        platform="ios",
        device_token="apns-device-token-12345",
    )
    assert dev.id == dev_id
    assert dev.platform == "ios"
    assert dev.is_active is True

    # 2. Unsupported platform rejected
    with pytest.raises(ValueError) as exc:
        register_user_device(
            mock_conn,
            user_id=user_id,
            tenant_id=tenant_id,
            platform="windows_phone",
            device_token="token-xyz",
        )
    assert "Unsupported mobile platform" in str(exc.value)

    # 3. Unregistration
    mock_cursor.rowcount = 1
    unregistered = unregister_user_device(
        mock_conn,
        user_id=user_id,
        tenant_id=tenant_id,
        device_token="apns-device-token-12345",
    )
    assert unregistered is True


def test_notify_user_devices():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()

    clear_dispatched_push_notifications()

    # User has 2 active devices: iPhone and Android tablet
    mock_cursor.fetchall.return_value = [
        ("ios", "apns-token-iphone"),
        ("android", "fcm-token-pixel"),
    ]

    provider = MockPushNotificationProvider()
    delivered = notify_user_devices(
        mock_conn,
        user_id=user_id,
        tenant_id=tenant_id,
        title="Document Approved",
        body="Vendor agreement has reached final sign-off.",
        data={"doc_id": "12345"},
        provider=provider,
    )

    assert delivered == 2
    dispatched = get_dispatched_push_notifications()
    assert len(dispatched) == 2
    assert dispatched[0]["platform"] == "ios"
    assert dispatched[0]["title"] == "Document Approved"
    assert dispatched[1]["platform"] == "android"


def test_mobile_session_refresh_token_rotation():
    """Simulate mobile app backgrounding and waking up to rotate refresh tokens."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()
    token_id = uuid4()
    family_id = uuid4()

    valid_token = "rt_valid_initial_token_hash"
    # Mock lookup returning active token
    mock_cursor.fetchone.side_effect = [
        # SELECT rt...
        (token_id, family_id, None, datetime.now(UTC) + timedelta(days=30), user_id, tenant_id),
        # INSERT INTO refresh_tokens... in create_refresh_token RETURNING
        (uuid4(),),
    ]

    u_id, t_id, new_token = rotate_refresh_token(mock_conn, valid_token)
    assert u_id == user_id
    assert t_id == tenant_id
    assert isinstance(new_token, str) and len(new_token) >= 32

    # Verify old token marked revoked_at = now()
    revoke_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE refresh_tokens SET revoked_at = now() WHERE id = %s" in c[0][0]
    ]
    assert len(revoke_calls) == 1
    assert revoke_calls[0][0][1] == (token_id,)


def test_mobile_session_refresh_token_replay_attack_revokes_family():
    """If an attacker captures and replays an already-used refresh token, revoke family."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()
    token_id = uuid4()
    family_id = uuid4()

    stolen_token = "rt_stolen_used_token"
    # revoked_at is not None -> replay detected!
    revoked_at = datetime.now(UTC) - timedelta(minutes=5)
    mock_cursor.fetchone.return_value = (
        token_id,
        family_id,
        revoked_at,
        datetime.now(UTC) + timedelta(days=30),
        user_id,
        tenant_id,
    )

    with pytest.raises(InvalidRefreshToken) as exc_info:
        rotate_refresh_token(mock_conn, stolen_token)

    assert "token replay detected; family revoked" in str(exc_info.value)
    # Verify family revocation query executed
    family_revocations = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = %s" in c[0][0]
    ]
    assert len(family_revocations) == 1
    assert family_revocations[0][0][1] == (family_id,)
