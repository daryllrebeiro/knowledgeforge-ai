"""Unit tests for Fix 4: diff_documents_from_db tenant scoping in SQL."""

from unittest.mock import MagicMock
from uuid import uuid4

from knowledgeforge.extraction.diff_engine import diff_documents_from_db


def test_diff_documents_from_db_scopes_by_tenant_in_sql():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    doc_a = uuid4()
    doc_b = uuid4()

    # Mock fetchall returns
    mock_cursor.fetchall.side_effect = [
        [("Section 1", "Clause A text from tenant doc")],
        [("Section 1", "Clause B text from tenant doc")],
    ]

    result = diff_documents_from_db(
        mock_conn,
        tenant_id=tenant_id,
        source_doc_id=doc_a,
        target_doc_id=doc_b,
    )

    # Verify both SQL queries join documents and filter by tenant_id
    assert mock_cursor.execute.call_count == 2
    for call in mock_cursor.execute.call_args_list:
        query, params = call[0]
        assert "JOIN documents d ON c.document_id = d.id" in query
        assert "WHERE d.tenant_id = %s AND c.document_id = %s" in query
        assert params[0] == tenant_id
        assert params[1] in (doc_a, doc_b)

    assert result.source_doc_id == doc_a
    assert result.target_doc_id == doc_b


def test_diff_documents_from_db_with_mismatched_tenant_returns_empty():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Foreign tenant executes query -> database returns empty rows due to d.tenant_id filter
    mock_cursor.fetchall.side_effect = [[], []]

    foreign_tenant = uuid4()
    doc_a = uuid4()
    doc_b = uuid4()

    result = diff_documents_from_db(
        mock_conn,
        tenant_id=foreign_tenant,
        source_doc_id=doc_a,
        target_doc_id=doc_b,
    )

    assert len(result.clauses) == 0
    assert result.added_count == 0
    assert result.removed_count == 0
    assert result.modified_count == 0
