"""Unit tests for Phase 8 Item 5: Auto-Clustering and Tagging Engine."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.clustering.engine import (
    _cosine_similarity,
    compute_tenant_document_embeddings,
    confirm_cluster,
    dismiss_cluster,
    generate_suggested_clusters,
)
from knowledgeforge.main import app

client = TestClient(app)


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(_cosine_similarity(v1, v2)) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(_cosine_similarity(v1, v3)) == 0.0

    v4 = [0.0, 0.0, 0.0]
    assert _cosine_similarity(v1, v4) == 0.0


def test_compute_tenant_document_embeddings_mean_pools():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    doc_id = uuid4()

    # 2 chunks for doc_id: [1.0, 2.0] and [3.0, 4.0]
    mock_cursor.fetchall.return_value = [
        (doc_id, "Sample Doc", "pdf", "[1.0, 2.0]"),
        (doc_id, "Sample Doc", "pdf", "[3.0, 4.0]"),
    ]

    res = compute_tenant_document_embeddings(mock_conn, tenant_id)
    assert doc_id in res
    title, doc_type, mean_vec = res[doc_id]
    assert title == "Sample Doc"
    assert doc_type == "pdf"
    assert mean_vec == [2.0, 3.0]


def test_generate_suggested_clusters():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    doc1 = uuid4()
    doc2 = uuid4()
    doc3 = uuid4()

    # doc1 and doc2 are identical vectors [1.0, 0.0], doc3 is [0.0, 1.0]
    mock_cursor.fetchall.return_value = [
        (doc1, "Doc 1", "policy", "[1.0, 0.0]"),
        (doc2, "Doc 2", "policy", "[1.0, 0.0]"),
        (doc3, "Doc 3", "contract", "[0.0, 1.0]"),
    ]
    cluster_id = uuid4()
    mock_cursor.fetchone.return_value = (
        cluster_id,
        tenant_id,
        "Cluster 1",
        [str(doc1), str(doc2)],
        ["tag1"],
        "suggested",
        None,
        None,
        None,
        "2026-09-09T00:00:00Z",
    )

    clusters = generate_suggested_clusters(
        mock_conn,
        tenant_id=tenant_id,
        similarity_threshold=0.8,
        min_cluster_size=2,
    )

    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {doc1, doc2}
    assert clusters[0].status == "suggested"

    # Verify insert query inserted status = 'suggested'
    insert_call = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO document_clusters" in c[0][0]
    ]
    assert len(insert_call) == 1
    assert "'suggested'" in insert_call[0][0][0]


def test_confirm_cluster_creates_collection_and_tags(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    cluster_id = uuid4()
    user_id = uuid4()
    doc1 = uuid4()
    doc2 = uuid4()
    created_col_id = uuid4()

    mock_cursor.fetchone.return_value = (
        cluster_id,
        "Finance Cluster",
        [str(doc1), str(doc2)],
        ["finance", "q3"],
        "suggested",
    )

    # Mock collection creation
    mock_col = MagicMock()
    mock_col.id = created_col_id
    monkeypatch.setattr("knowledgeforge.clustering.engine.create_collection", lambda *a, **kw: mock_col)
    monkeypatch.setattr("knowledgeforge.clustering.engine.add_document_to_collection", lambda *a, **kw: None)

    col_id = confirm_cluster(
        mock_conn,
        cluster_id=cluster_id,
        tenant_id=tenant_id,
        user_id=user_id,
        create_collection_flag=True,
        apply_tags_flag=True,
    )

    assert col_id == created_col_id
    update_call = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE document_clusters" in c[0][0] and "status = 'confirmed'" in c[0][0]
    ]
    assert len(update_call) == 1

    # Verify tags inserted for each document
    tag_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO document_tags" in c[0][0]
    ]
    assert len(tag_calls) == 4  # 2 docs x 2 tags


def test_dismiss_cluster_marks_dismissed_without_tagging():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    cluster_id = uuid4()
    user_id = uuid4()

    mock_cursor.rowcount = 1
    success = dismiss_cluster(
        mock_conn,
        cluster_id=cluster_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    assert success is True
    update_call = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE document_clusters" in c[0][0] and "status = 'dismissed'" in c[0][0]
    ]
    assert len(update_call) == 1

    tag_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO document_tags" in c[0][0]
    ]
    assert len(tag_calls) == 0
