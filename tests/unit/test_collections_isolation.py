"""Unit tests for Phase 8 Item 1: Collections and Sub-Tenant Isolation."""

from unittest.mock import MagicMock
from uuid import uuid4

from knowledgeforge.collections.service import (
    add_document_to_collection,
    check_user_collection_access,
    create_collection,
    list_collections,
    remove_document_from_collection,
)
from knowledgeforge.ingestion.store import list_documents
from knowledgeforge.retrieval.retrieve import retrieve_chunks


def test_create_collection_with_creator_admin():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    creator_id = uuid4()
    col_id = uuid4()

    mock_cursor.fetchone.return_value = (
        col_id,
        tenant_id,
        "Confidential Project",
        "Secret docs",
        True,
        "2026-09-09T00:00:00Z",
        "2026-09-09T00:00:00Z",
    )

    col = create_collection(
        mock_conn,
        tenant_id=tenant_id,
        name="Confidential Project",
        description="Secret docs",
        is_private=True,
        creator_user_id=creator_id,
    )

    assert col.id == col_id
    assert col.is_private is True
    assert mock_cursor.execute.call_count == 2
    # Verify creator membership inserted
    membership_call = mock_cursor.execute.call_args_list[1]
    assert "INSERT INTO collection_memberships" in membership_call[0][0]
    assert membership_call[0][1] == (col_id, creator_id, tenant_id)


def test_check_user_collection_access_queries():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()
    col_id = uuid4()

    # Case 1: user has access
    mock_cursor.fetchone.return_value = (1,)
    has_access = check_user_collection_access(mock_conn, col_id, user_id, tenant_id)
    assert has_access is True
    query, params = mock_cursor.execute.call_args[0]
    assert "collections c" in query
    assert "c.is_private = false" in query
    assert "collection_memberships cm" in query
    assert params == (col_id, tenant_id, user_id)

    # Case 2: user lacks access
    mock_cursor.fetchone.return_value = None
    has_access = check_user_collection_access(mock_conn, col_id, user_id, tenant_id)
    assert has_access is False


def test_list_collections_filters_by_user_membership():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()

    mock_cursor.fetchall.return_value = []
    list_collections(mock_conn, tenant_id, user_id=user_id)

    query, params = mock_cursor.execute.call_args[0]
    assert "c.tenant_id = %s" in query
    assert "c.is_private = false" in query
    assert "EXISTS (" in query
    assert "collection_memberships" in query
    assert params == (tenant_id, user_id)


def test_retrieve_chunks_enforces_collection_in_sql(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    monkeypatch.setattr("knowledgeforge.retrieval.retrieve.register_vector", lambda connection: None)

    tenant_id = uuid4()
    col_id = uuid4()
    user_id = uuid4()
    mock_cursor.fetchall.return_value = []

    retrieve_chunks(
        mock_conn,
        query_embedding=[0.1] * 128,
        tenant_id=tenant_id,
        collection_id=col_id,
        user_id=user_id,
        limit=5,
    )

    query, params = mock_cursor.execute.call_args[0]
    assert "collection_documents cd" in query
    assert "cd.collection_id = %s" in query
    assert "collection_memberships cm" in query
    assert tenant_id in params
    assert col_id in params
    assert user_id in params


def test_list_documents_enforces_collection_in_sql():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    col_id = uuid4()
    user_id = uuid4()
    mock_cursor.fetchall.return_value = []

    list_documents(
        mock_conn,
        tenant_id=tenant_id,
        collection_id=col_id,
        user_id=user_id,
    )

    query, params = mock_cursor.execute.call_args[0]
    assert "collection_documents cd" in query
    assert "cd.collection_id = %s" in query
    assert "collection_memberships cm" in query
    assert tenant_id in params
    assert col_id in params
    assert user_id in params


def test_add_and_remove_document_from_collection():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    col_id = uuid4()
    doc_id = uuid4()

    add_document_to_collection(mock_conn, col_id, doc_id, tenant_id)
    query, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO collection_documents" in query
    assert "WHERE EXISTS (SELECT 1 FROM collections WHERE id = %s AND tenant_id = %s)" in query
    assert params == (col_id, doc_id, tenant_id, col_id, tenant_id, doc_id, tenant_id)

    mock_cursor.rowcount = 1
    removed = remove_document_from_collection(mock_conn, col_id, doc_id, tenant_id)
    assert removed is True
