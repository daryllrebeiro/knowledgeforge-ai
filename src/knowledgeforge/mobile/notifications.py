"""Mobile push notification channels and device token registration (Phase 8 Item 10)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection

logger = logging.getLogger("knowledgeforge.mobile")

# Test inspection store
_dispatched_push_notifications: list[dict[str, Any]] = []


def get_dispatched_push_notifications() -> list[dict[str, Any]]:
    return list(_dispatched_push_notifications)


def clear_dispatched_push_notifications() -> None:
    _dispatched_push_notifications.clear()


@dataclass(frozen=True)
class DeviceRegistrationRow:
    id: UUID
    user_id: UUID
    tenant_id: UUID
    platform: str
    device_token: str
    is_active: bool
    last_seen_at: datetime
    created_at: datetime


class PushNotificationProvider(Protocol):
    def send(
        self,
        *,
        device_token: str,
        platform: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        ...


class MockPushNotificationProvider:
    """Mock push provider capturing notifications for CI verification and local tests."""

    def send(
        self,
        *,
        device_token: str,
        platform: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        record = {
            "device_token": device_token,
            "platform": platform,
            "title": title,
            "body": body,
            "data": data or {},
        }
        _dispatched_push_notifications.append(record)
        logger.info("push_notification.dispatched platform=%s token=%s title=%s", platform, device_token, title)
        return True


def register_user_device(
    connection: Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    platform: str,
    device_token: str,
) -> DeviceRegistrationRow:
    if platform not in ("ios", "android", "web_push"):
        raise ValueError(f"Unsupported mobile platform '{platform}'")
    if not device_token.strip():
        raise ValueError("device_token cannot be empty")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO user_devices (user_id, tenant_id, platform, device_token, is_active, last_seen_at)
            VALUES (%s, %s, %s, %s, true, now())
            ON CONFLICT (tenant_id, device_token)
            DO UPDATE SET
                user_id = EXCLUDED.user_id,
                platform = EXCLUDED.platform,
                is_active = true,
                last_seen_at = now()
            RETURNING id, user_id, tenant_id, platform, device_token, is_active, last_seen_at, created_at
            """,
            (user_id, tenant_id, platform, device_token),
        )
        r = cursor.fetchone()
        if r is None:
            raise RuntimeError("Failed to register user device")

    return DeviceRegistrationRow(
        id=UUID(str(r[0])),
        user_id=UUID(str(r[1])),
        tenant_id=UUID(str(r[2])),
        platform=str(r[3]),
        device_token=str(r[4]),
        is_active=bool(r[5]),
        last_seen_at=r[6],
        created_at=r[7],
    )


def unregister_user_device(
    connection: Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    device_token: str,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE user_devices
            SET is_active = false
            WHERE user_id = %s AND tenant_id = %s AND device_token = %s
            """,
            (user_id, tenant_id, device_token),
        )
        return cursor.rowcount > 0


def notify_user_devices(
    connection: Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    provider: PushNotificationProvider | None = None,
) -> int:
    """Send push notification to all active devices for a user."""
    provider = provider or MockPushNotificationProvider()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT platform, device_token FROM user_devices
            WHERE user_id = %s AND tenant_id = %s AND is_active = true
            """,
            (user_id, tenant_id),
        )
        rows = cursor.fetchall()

    delivered = 0
    for platform, token in rows:
        if provider.send(device_token=token, platform=platform, title=title, body=body, data=data):
            delivered += 1
    return delivered
