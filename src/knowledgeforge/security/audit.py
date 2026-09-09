"""Audit logging service for compliance and security events."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg import Connection


def record_audit_log(
    connection: Connection,
    *,
    tenant_id: UUID,
    user_id: UUID,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an immutable audit log entry in the audit_logs table."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit_logs (tenant_id, user_id, action, details)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (tenant_id, user_id, action, json.dumps(details or {})),
        )
        connection.commit()
