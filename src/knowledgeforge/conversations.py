"""Conversation and message persistence (F1).

Tenant scoping follows the rest of the store layer: every read and write
filters on ``tenant_id`` so one tenant can never read another's history.
"""

import json
from typing import Any, NamedTuple
from uuid import UUID

from psycopg import Connection


class ConversationRow(NamedTuple):
    conversation_id: UUID
    title: str
    updated_at: str
    message_count: int = 0


class MessageRow(NamedTuple):
    role: str
    content: str
    citations: list[dict[str, Any]]
    created_at: str


def create_conversation(connection: Connection, tenant_id: UUID, title: str) -> ConversationRow:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO conversations (tenant_id, title) "
            "VALUES (%s, %s) RETURNING id, title, updated_at",
            (tenant_id, title),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("conversation insert did not return an ID")
    return ConversationRow(
        conversation_id=UUID(str(row[0])),
        title=str(row[1]),
        updated_at=str(row[2]),
        message_count=0,
    )


def list_conversations(
    connection: Connection, tenant_id: UUID, *, limit: int = 50, offset: int = 0
) -> list[ConversationRow]:
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id, c.title, c.updated_at,
                   (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id)
            FROM conversations AS c
            WHERE c.tenant_id = %s
            ORDER BY c.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        rows = cursor.fetchall()
    return [
        ConversationRow(
            conversation_id=UUID(str(row[0])),
            title=str(row[1]),
            updated_at=str(row[2]),
            message_count=int(row[3]),
        )
        for row in rows
    ]


def get_conversation(
    connection: Connection, conversation_id: UUID, tenant_id: UUID
) -> ConversationRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, title, updated_at FROM conversations "
            "WHERE id = %s AND tenant_id = %s",
            (conversation_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return ConversationRow(
        conversation_id=UUID(str(row[0])),
        title=str(row[1]),
        updated_at=str(row[2]),
    )


def get_conversation_messages(
    connection: Connection, conversation_id: UUID, tenant_id: UUID
) -> list[MessageRow] | None:
    """Return the conversation's messages oldest first, or None if not tenant-owned."""
    if get_conversation(connection, conversation_id, tenant_id) is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT role, content, citations, created_at FROM messages "
            "WHERE conversation_id = %s ORDER BY created_at, id",
            (conversation_id,),
        )
        rows = cursor.fetchall()
    return [
        MessageRow(
            role=str(row[0]),
            content=str(row[1]),
            citations=list(row[2] or []),
            created_at=str(row[3]),
        )
        for row in rows
    ]


def append_exchange(
    connection: Connection,
    conversation_id: UUID,
    tenant_id: UUID,
    *,
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
) -> None:
    """Persist a user question and its assistant answer, then touch the conversation.

    Raises LookupError when the conversation does not belong to the tenant.
    """
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE conversations SET updated_at = now() "
                "WHERE id = %s AND tenant_id = %s",
                (conversation_id, tenant_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("conversation not found for tenant")
            cursor.execute(
                "INSERT INTO messages (conversation_id, role, content, citations) "
                "VALUES (%s, 'user', %s, '[]')",
                (conversation_id, question),
            )
            cursor.execute(
                "INSERT INTO messages (conversation_id, role, content, citations) "
                "VALUES (%s, 'assistant', %s, %s)",
                (conversation_id, answer, json.dumps(citations)),
            )


def delete_conversation(connection: Connection, conversation_id: UUID, tenant_id: UUID) -> bool:
    """Delete a tenant-owned conversation (messages cascade)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM conversations WHERE id = %s AND tenant_id = %s",
            (conversation_id, tenant_id),
        )
        return cursor.rowcount == 1
