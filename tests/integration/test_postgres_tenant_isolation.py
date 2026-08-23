import os
from uuid import uuid4

import psycopg
import pytest
from pgvector.psycopg import register_vector

from knowledgeforge.retrieval.retrieve import retrieve_chunks

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not configured")
    return value


def test_real_pgvector_retrieval_is_tenant_scoped(database_url: str) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    document_a = uuid4()
    document_b = uuid4()
    embedding = [0.1] * 768
    try:
        with psycopg.connect(database_url) as connection:
            register_vector(connection)
            with connection.transaction():
                connection.execute(
                    "INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_a, "A")
                )
                connection.execute(
                    "INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_b, "B")
                )
                for document_id, tenant_id, title in (
                    (document_a, tenant_a, "private-a"),
                    (document_b, tenant_b, "private-b"),
                ):
                    connection.execute(
                        """
                        INSERT INTO documents
                            (id, title, source_filename, doc_type, content_hash, tenant_id)
                        VALUES (%s, %s, %s, 'markdown', %s, %s)
                        """,
                        (document_id, title, title, str(document_id), tenant_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO chunks (document_id, page, chunk_text, embedding)
                        VALUES (%s, 1, %s, %s)
                        """,
                        (document_id, f"secret-{title}", embedding),
                    )

            results = retrieve_chunks(connection, embedding, tenant_id=tenant_a, limit=10)
            assert [document_id for document_id, _ in results] == [document_a]
            assert results[0][1].text == "secret-private-a"
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a, tenant_b))
