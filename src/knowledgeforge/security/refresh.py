"""Refresh tokens with rotation and replay detection (F4).

Tokens are opaque (48 bytes of entropy, shown to the client once) and stored
only as SHA-256 hashes. Refreshing rotates the token: the presented token is
revoked and a new one is issued in the same *family*. Presenting an already
revoked token is treated as replay — the whole family is revoked, because a
stolen token can only be detected by someone using it after its owner rotated.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import Connection

from knowledgeforge.config import get_settings


class InvalidRefreshToken(Exception):
    """Raised for unknown, expired, or replayed refresh tokens."""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_refresh_token(
    connection: Connection, user_id: UUID, family_id: UUID | None = None
) -> str:
    """Issue a refresh token in ``family_id`` (or a new family) and return it."""
    token = secrets.token_urlsafe(48)
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, _hash_token(token), family_id or uuid4(), expires),
        )
    return token


def rotate_refresh_token(connection: Connection, token: str) -> tuple[UUID, UUID, str]:
    """Validate and rotate a refresh token.

    Returns ``(user_id, tenant_id, new_refresh_token)``. Raises
    InvalidRefreshToken for unknown, expired, or replayed tokens; a replay
    revokes the token's whole family.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rt.id, rt.family_id, rt.revoked_at, rt.expires_at, u.id, u.tenant_id
            FROM refresh_tokens AS rt JOIN users AS u ON u.id = rt.user_id
            WHERE rt.token_hash = %s
            """,
            (_hash_token(token),),
        )
        row = cursor.fetchone()
        if row is None:
            raise InvalidRefreshToken("unknown token")
        token_id, family_id, revoked_at, expires_at, user_id, tenant_id = row
        if revoked_at is not None:
            # Replay: revoke the family so everything issued after the theft is dead.
            cursor.execute(
                "UPDATE refresh_tokens SET revoked_at = now() "
                "WHERE family_id = %s AND revoked_at IS NULL",
                (family_id,),
            )
            raise InvalidRefreshToken("token replay detected; family revoked")
        if datetime.now(UTC) >= expires_at:
            raise InvalidRefreshToken("token expired")
        cursor.execute(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (token_id,),
        )
    new_token = create_refresh_token(connection, UUID(str(user_id)), UUID(str(family_id)))
    return UUID(str(user_id)), UUID(str(tenant_id)), new_token


def revoke_refresh_family(connection: Connection, token: str) -> bool:
    """Revoke the family behind a presented token (logout). False if unknown."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE refresh_tokens SET revoked_at = now()
            WHERE family_id = (SELECT family_id FROM refresh_tokens WHERE token_hash = %s)
              AND revoked_at IS NULL
            """,
            (_hash_token(token),),
        )
        revoked = cursor.rowcount
        cursor.execute("SELECT 1 FROM refresh_tokens WHERE token_hash = %s", (_hash_token(token),))
        return revoked > 0 or cursor.fetchone() is not None
