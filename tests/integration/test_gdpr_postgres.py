"""PostgreSQL integration tests for GDPR Article 20 export and Article 5(1)(e) account purge."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from knowledgeforge.ingestion.store import export_tenant_data
from knowledgeforge.security.auth import purge_unverified_accounts

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL / TEST_DATABASE_URL is not configured")
    return url


def test_export_tenant_data_postgres_isolated(database_url: str) -> None:
    """Validate that export_tenant_data extracts complete tenant records and isolates cross-tenant data."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    doc_a = uuid4()
    doc_b = uuid4()
    conv_a = uuid4()

    try:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                # Setup Tenants
                connection.execute(
                    "INSERT INTO tenants (id, name, tier, subscription_status) VALUES (%s, 'Tenant A', 'pro', 'active')",
                    (tenant_a,),
                )
                connection.execute(
                    "INSERT INTO tenants (id, name, tier, subscription_status) VALUES (%s, 'Tenant B', 'free', 'active')",
                    (tenant_b,),
                )

                # Setup Users & Memberships
                connection.execute(
                    "INSERT INTO users (id, email, hashed_password, email_verified) VALUES (%s, 'a@example.com', 'hash', true)",
                    (user_a,),
                )
                connection.execute(
                    "INSERT INTO users (id, email, hashed_password, email_verified) VALUES (%s, 'b@example.com', 'hash', true)",
                    (user_b,),
                )
                connection.execute(
                    "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')",
                    (tenant_a, user_a),
                )
                connection.execute(
                    "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')",
                    (tenant_b, user_b),
                )

                # Setup Documents
                connection.execute(
                    """
                    INSERT INTO documents (id, tenant_id, title, source_filename, storage_uri, doc_type, status, version)
                    VALUES (%s, %s, 'Doc A', 'doc_a.pdf', 'gs://bucket/doc_a.pdf', 'pdf', 'ready', 1)
                    """,
                    (doc_a, tenant_a),
                )
                connection.execute(
                    """
                    INSERT INTO documents (id, tenant_id, title, source_filename, storage_uri, doc_type, status, version)
                    VALUES (%s, %s, 'Doc B', 'doc_b.pdf', 'gs://bucket/doc_b.pdf', 'pdf', 'ready', 1)
                    """,
                    (doc_b, tenant_b),
                )

                # Setup Chunks
                connection.execute(
                    """
                    INSERT INTO chunks (document_id, chunk_index, chunk_text, page, section)
                    VALUES (%s, 0, 'Confidential text for A', 1, 'Intro')
                    """,
                    (doc_a,),
                )

                # Setup Conversations and Messages
                connection.execute(
                    "INSERT INTO conversations (conversation_id, tenant_id, title) VALUES (%s, %s, 'Conv A')",
                    (conv_a, tenant_a),
                )
                connection.execute(
                    """
                    INSERT INTO conversation_messages (conversation_id, sender, content)
                    VALUES (%s, 'user', 'Hello A')
                    """,
                    (conv_a,),
                )

                # Setup Audit Logs
                connection.execute(
                    """
                    INSERT INTO audit_log (tenant_id, action, actor_id, details)
                    VALUES (%s, 'document.upload', %s, '{"doc": "doc_a.pdf"}'::jsonb)
                    """,
                    (tenant_a, user_a),
                )

            # Perform export on Tenant A
            export_a = export_tenant_data(connection, tenant_a)

            assert export_a["tenant"]["id"] == str(tenant_a)
            assert export_a["tenant"]["name"] == "Tenant A"
            assert export_a["tenant"]["tier"] == "pro"

            # Check users
            assert len(export_a["users"]) == 1
            assert export_a["users"][0]["email"] == "a@example.com"
            assert export_a["users"][0]["role"] == "owner"

            # Check documents and chunks
            assert len(export_a["documents"]) == 1
            assert export_a["documents"][0]["id"] == str(doc_a)
            assert export_a["documents"][0]["title"] == "Doc A"
            assert len(export_a["document_chunks"]) == 1
            assert export_a["document_chunks"][0]["document_id"] == str(doc_a)
            assert export_a["document_chunks"][0]["chunk_text"] == "Confidential text for A"

            # Check conversations
            assert len(export_a["conversations"]) == 1
            assert export_a["conversations"][0]["id"] == str(conv_a)
            assert len(export_a["conversations"][0]["messages"]) == 1
            assert export_a["conversations"][0]["messages"][0]["content"] == "Hello A"

            # Check audit logs
            assert len(export_a["audit_events"]) == 1
            assert export_a["audit_events"][0]["action"] == "document.upload"

            # Cross-tenant check: no Tenant B data present
            export_b = export_tenant_data(connection, tenant_b)
            assert export_b["tenant"]["id"] == str(tenant_b)
            assert len(export_b["documents"]) == 1
            assert export_b["documents"][0]["id"] == str(doc_b)
            assert len(export_b["document_chunks"]) == 0
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a, tenant_b))
                connection.execute("DELETE FROM users WHERE id IN (%s, %s)", (user_a, user_b))


def test_purge_unverified_accounts_postgres(database_url: str) -> None:
    """Validate that purge_unverified_accounts removes old unverified users and retains verified/recent users."""
    old_unverified = uuid4()
    recent_unverified = uuid4()
    old_verified = uuid4()

    cutoff_old = datetime.now(UTC) - timedelta(days=35)
    cutoff_recent = datetime.now(UTC) - timedelta(days=5)

    try:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO users (id, email, hashed_password, email_verified, created_at)
                    VALUES (%s, 'stale@example.com', 'hash', false, %s)
                    """,
                    (old_unverified, cutoff_old),
                )
                connection.execute(
                    """
                    INSERT INTO users (id, email, hashed_password, email_verified, created_at)
                    VALUES (%s, 'fresh@example.com', 'hash', false, %s)
                    """,
                    (recent_unverified, cutoff_recent),
                )
                connection.execute(
                    """
                    INSERT INTO users (id, email, hashed_password, email_verified, created_at)
                    VALUES (%s, 'active@example.com', 'hash', true, %s)
                    """,
                    (old_verified, cutoff_old),
                )

            purged = purge_unverified_accounts(connection, max_age_days=30)
            assert purged >= 1

            # Verify survival states
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM users WHERE id = %s", (old_unverified,))
                assert cursor.fetchone() is None

                cursor.execute("SELECT id FROM users WHERE id = %s", (recent_unverified,))
                assert cursor.fetchone() is not None

                cursor.execute("SELECT id FROM users WHERE id = %s", (old_verified,))
                assert cursor.fetchone() is not None
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute(
                    "DELETE FROM users WHERE id IN (%s, %s, %s)",
                    (old_unverified, recent_unverified, old_verified),
                )
