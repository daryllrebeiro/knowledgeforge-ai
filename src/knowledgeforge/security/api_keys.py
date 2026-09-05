"""API keys for programmatic access (F4).

Keys are opaque (``kf_`` + 32 bytes of entropy) and stored only as SHA-256
hashes — the plaintext is returned exactly once at creation. A short prefix is
kept in the clear so listings can identify keys without the secret.
"""

import hashlib
import secrets
from typing import NamedTuple
from uuid import UUID

from psycopg import Connection


class ApiKeyRow(NamedTuple):
    key_id: UUID
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(
    connection: Connection, tenant_id: UUID, user_id: UUID, name: str
) -> tuple[UUID, str]:
    """Create a key and return (key_id, plaintext_key); the key is shown once."""
    key = f"kf_{secrets.token_urlsafe(32)}"
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO api_keys (tenant_id, user_id, name, key_hash, key_prefix) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (tenant_id, user_id, name, _hash_key(key), key[:12]),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("api key insert did not return an ID")
    return UUID(str(row[0])), key


def verify_api_key(connection: Connection, presented: str) -> tuple[UUID, UUID] | None:
    """Return (user_id, tenant_id) for a live key, or None; records last use."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id, tenant_id FROM api_keys "
            "WHERE key_hash = %s AND revoked_at IS NULL",
            (_hash_key(presented),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        cursor.execute(
            "UPDATE api_keys SET last_used_at = now() WHERE key_hash = %s",
            (_hash_key(presented),),
        )
    return UUID(str(row[0])), UUID(str(row[1]))


def list_api_keys(connection: Connection, tenant_id: UUID) -> list[ApiKeyRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, key_prefix, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC",
            (tenant_id,),
        )
        rows = cursor.fetchall()
    return [
        ApiKeyRow(
            key_id=UUID(str(row[0])),
            name=str(row[1]),
            key_prefix=str(row[2]),
            created_at=str(row[3]),
            last_used_at=None if row[4] is None else str(row[4]),
            revoked=row[5] is not None,
        )
        for row in rows
    ]


def revoke_api_key(connection: Connection, key_id: UUID, tenant_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL",
            (key_id, tenant_id),
        )
        if cursor.rowcount == 1:
            return True
        # Distinguish "already revoked" (idempotent success) from "not yours".
        cursor.execute(
            "SELECT 1 FROM api_keys WHERE id = %s AND tenant_id = %s", (key_id, tenant_id)
        )
        return cursor.fetchone() is not None
