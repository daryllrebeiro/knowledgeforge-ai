"""Conversation persistence against a real PostgreSQL (F1)."""

import os
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledgeforge.conversations import (
    append_exchange,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_messages,
    list_conversations,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not configured")
    return value


def test_conversations_are_tenant_scoped_and_persist_exchanges(database_url: str) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    try:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, 'A')", (tenant_a,))
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, 'B')", (tenant_b,))

            conversation = create_conversation(connection, tenant_a, "Quarterly review")
            append_exchange(
                connection,
                conversation.conversation_id,
                tenant_a,
                question="What is the upload limit?",
                answer="10 MB. [doc 1, page 4]",
                citations=[{"document_id": str(uuid4()), "page": 4}],
            )

            # Owner sees the exchange...
            messages = get_conversation_messages(
                connection, conversation.conversation_id, tenant_a
            )
            assert messages is not None
            assert [message.role for message in messages] == ["user", "assistant"]
            assistant_citations = messages[1].citations
            assert len(assistant_citations) == 1
            assert assistant_citations[0]["page"] == 4
            assert UUID(str(assistant_citations[0]["document_id"]))

            # ...and the summary list is tenant-scoped.
            assert [row.conversation_id for row in list_conversations(connection, tenant_a)] == [
                conversation.conversation_id
            ]
            assert list_conversations(connection, tenant_b) == []

            # Another tenant cannot read, append to, or delete the conversation.
            assert get_conversation(connection, conversation.conversation_id, tenant_b) is None
            assert (
                get_conversation_messages(connection, conversation.conversation_id, tenant_b)
                is None
            )
            assert not delete_conversation(connection, conversation.conversation_id, tenant_b)
            with pytest.raises(LookupError):
                append_exchange(
                    connection,
                    conversation.conversation_id,
                    tenant_b,
                    question="sneaky",
                    answer="no",
                    citations=[],
                )

            # Deleting as the owner removes the conversation and cascades messages.
            assert delete_conversation(connection, conversation.conversation_id, tenant_a)
            assert get_conversation(connection, conversation.conversation_id, tenant_a) is None
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute(
                    "DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a, tenant_b)
                )
