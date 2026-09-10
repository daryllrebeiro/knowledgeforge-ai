"""Unit tests for Source Highlighting Viewer (Phase 7 Wave 1 Item C)."""

from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from knowledgeforge import api
from knowledgeforge.ingestion.chunk import chunk_pages
from knowledgeforge.ingestion.store import DocumentContentData


def test_chunk_pages_character_offsets():
    text = "The quick brown fox jumps over the lazy dog."
    pages = [(1, text)]

    chunks = chunk_pages(pages, chunk_size=4, overlap=1)

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.start_char is not None
        assert chunk.end_char is not None
        assert chunk.start_char >= 0
        assert chunk.end_char <= len(text)
        # Check that sliced text matches chunk text words
        sliced = text[chunk.start_char : chunk.end_char]
        assert chunk.text.split() == sliced.split()


def test_chunk_pages_multi_page_offsets():
    page_1 = "Alpha beta gamma delta."
    page_2 = "Epsilon zeta eta theta."
    pages = [(1, page_1), (2, page_2)]

    chunks = chunk_pages(pages, chunk_size=2, overlap=0)
    for chunk in chunks:
        source = page_1 if chunk.page == 1 else page_2
        sliced = source[chunk.start_char : chunk.end_char]
        assert chunk.text.split() == sliced.split()


def test_get_document_content_endpoint(monkeypatch):
    tenant_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    fake_data = DocumentContentData(
        document_id=doc_id,
        title="Test Document",
        doc_type="pdf",
        storage_uri="gs://bucket/test.pdf",
        chunks=[
            {
                "id": chunk_id,
                "page": 1,
                "section": "Intro",
                "text": "Hello world from chunk 1.",
                "start_char": 0,
                "end_char": 25,
                "bounding_boxes": [{"page": 1, "box": [0.1, 0.2, 0.8, 0.4]}],
            }
        ],
    )

    def fake_get_content(connection, document_id, tid, **kwargs):
        assert tid == tenant_id
        if document_id == doc_id:
            return fake_data
        return None

    monkeypatch.setattr(api, "get_document_content_and_chunks", fake_get_content)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))
    user = (uuid4(), tenant_id, "user@example.com", False)

    # Success case
    resp = api.document_content(doc_id, current_user=user)
    assert resp.document_id == doc_id
    assert resp.title == "Test Document"
    assert len(resp.pages) == 1
    assert resp.pages[0].page == 1
    assert "Hello world" in resp.pages[0].text
    assert len(resp.chunks) == 1
    assert resp.chunks[0].chunk_id == chunk_id
    assert resp.chunks[0].start_char == 0
    assert resp.chunks[0].end_char == 25

    # 404 case for wrong document or foreign tenant
    with pytest.raises(HTTPException) as exc_info:
        api.document_content(uuid4(), current_user=user)
    assert exc_info.value.status_code == 404


def test_get_document_view_html_endpoint(monkeypatch):
    tenant_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    fake_data = DocumentContentData(
        document_id=doc_id,
        title="Agreement Contract",
        doc_type="markdown",
        storage_uri=None,
        chunks=[
            {
                "id": chunk_id,
                "page": 1,
                "section": "Confidentiality",
                "text": "Both parties agree to maintain strict confidentiality.",
                "start_char": 0,
                "end_char": 55,
                "bounding_boxes": [],
            }
        ],
    )

    def fake_get_content(connection, document_id, tid, **kwargs):
        if document_id == doc_id and tid == tenant_id:
            return fake_data
        return None

    monkeypatch.setattr(api, "get_document_content_and_chunks", fake_get_content)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))
    user = (uuid4(), tenant_id, "user@example.com", False)

    # 1. View with active chunk_id
    resp = api.document_viewer(doc_id, chunk_id=chunk_id, current_user=user)
    assert resp.status_code == 200
    html_body = resp.body.decode("utf-8")
    assert "Agreement Contract" in html_body
    assert "highlight-active" in html_body
    assert "Both parties agree to maintain strict confidentiality." in html_body

    # 2. View with text highlight query
    resp_hl = api.document_viewer(doc_id, highlight="strict confidentiality", current_user=user)
    assert resp_hl.status_code == 200
    html_hl = resp_hl.body.decode("utf-8")
    assert '<mark class="highlight-active" id="active-highlight">strict confidentiality</mark>' in html_hl

    # 3. Cross-tenant request returns 404
    other_tenant_user = (uuid4(), uuid4(), "attacker@example.com", False)
    with pytest.raises(HTTPException) as exc_info:
        api.document_viewer(doc_id, current_user=other_tenant_user)
    assert exc_info.value.status_code == 404
