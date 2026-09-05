"""Refresh-token rotation and replay detection (F4)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledgeforge.security.refresh import (
    InvalidRefreshToken,
    create_refresh_token,
    rotate_refresh_token,
)

USER_ID = uuid4()
TENANT_ID = uuid4()
FAMILY_ID = uuid4()
TOKEN_ID = uuid4()


class FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self.row = row
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple | None:
        return self.row


class FakeConnection:
    """Each cursor() call consumes the next scripted fetchone() result."""

    def __init__(self, rows: list[tuple | None]) -> None:
        self.rows = list(rows)
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        row = self.rows.pop(0) if self.rows else None
        cursor = FakeCursor(row)
        self.cursors.append(cursor)
        return cursor


def _valid_row(revoked_at, expires_at=None):
    return (TOKEN_ID, FAMILY_ID, revoked_at, expires_at or datetime.now(UTC) + timedelta(days=1),
            USER_ID, TENANT_ID)


def test_rotate_returns_new_identity_and_token() -> None:
    connection = FakeConnection([_valid_row(None)])

    user_id, tenant_id, new_token = rotate_refresh_token(connection, "token")

    assert user_id == USER_ID
    assert tenant_id == TENANT_ID
    assert new_token and new_token != "token"
    # The presented token was revoked, and a replacement was inserted.
    assert "UPDATE refresh_tokens SET revoked_at" in connection.cursors[0].executed[1]
    assert "INSERT INTO refresh_tokens" in connection.cursors[1].executed[0]


def test_rotate_rejects_unknown_tokens() -> None:
    connection = FakeConnection([None])

    with pytest.raises(InvalidRefreshToken, match="unknown"):
        rotate_refresh_token(connection, "never-issued")


def test_rotate_rejects_expired_tokens() -> None:
    expired = _valid_row(None, expires_at=datetime.now(UTC) - timedelta(days=1))
    connection = FakeConnection([expired])

    with pytest.raises(InvalidRefreshToken, match="expired"):
        rotate_refresh_token(connection, "old-token")

    # No rotation insert happened.
    assert len(connection.cursors) == 1


def test_rotate_treats_a_revoked_token_as_replay_and_kills_the_family() -> None:
    connection = FakeConnection([_valid_row(datetime.now(UTC) - timedelta(minutes=5))])

    with pytest.raises(InvalidRefreshToken, match="replay"):
        rotate_refresh_token(connection, "stolen-token")

    # The family-wide revocation must have run.
    assert "WHERE family_id = %s AND revoked_at IS NULL" in connection.cursors[0].executed[1]


def test_created_tokens_are_unique_and_hashed() -> None:
    connection = FakeConnection([])

    first = create_refresh_token(connection, USER_ID)
    second = create_refresh_token(connection, USER_ID, FAMILY_ID)

    assert first != second
    insert = connection.cursors[0].executed[0]
    assert "INSERT INTO refresh_tokens" in insert
