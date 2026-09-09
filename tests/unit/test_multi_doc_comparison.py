"""Unit tests for Multi-Document Comparison (Phase 7 Wave 1 Item B)."""

from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from knowledgeforge import api
from knowledgeforge.config import Settings
from knowledgeforge.generation.local import local_answer
from knowledgeforge.generation.prompt import (
    COMPARISON_INSTRUCTION,
    LabeledChunk,
    LabeledExtraction,
    build_prompt,
)
from knowledgeforge.ingestion.chunk import TextChunk


def test_build_prompt_includes_comparison_instruction_when_multiple_documents():
    chunk_1 = LabeledChunk(
        label="doc 1",
        chunk=TextChunk(text="Acme clause: termination in 30 days.", page=1),
    )
    chunk_2 = LabeledChunk(
        label="doc 2",
        chunk=TextChunk(text="Globex clause: termination in 60 days.", page=1),
    )

    prompt = build_prompt("Compare the termination clauses", [chunk_1, chunk_2])

    assert COMPARISON_INSTRUCTION in prompt
    assert "[doc 1, page 1]" in prompt
    assert "[doc 2, page 1]" in prompt
    assert "Compare the termination clauses" in prompt


def test_build_prompt_single_document_no_comparison_instruction():
    chunk_1 = LabeledChunk(
        label="doc 1",
        chunk=TextChunk(text="Single doc content.", page=1),
    )
    prompt = build_prompt("Summarize", [chunk_1])
    assert COMPARISON_INSTRUCTION not in prompt
    assert "[doc 1, page 1]" in prompt


def test_local_answer_cites_all_compared_documents():
    chunks = [
        LabeledChunk(label="doc 1", chunk=TextChunk(text="Term A", page=2)),
        LabeledChunk(label="doc 2", chunk=TextChunk(text="Term B", page=5)),
    ]
    ans = local_answer("Compare contract durations", chunks)
    assert "[doc 1, page 2]" in ans
    assert "[doc 2, page 5]" in ans


def test_prepare_ask_with_multi_document_ids(monkeypatch):
    tenant_id = uuid4()
    doc_1 = uuid4()
    doc_2 = uuid4()

    settings = Settings(local_generation=True, local_embeddings=True)
    req = api.AskRequest(
        question="Compare doc 1 and doc 2",
        document_ids=[doc_1, doc_2],
    )

    retrieved_chunks = [
        (uuid4(), doc_1, TextChunk(text="Text 1", page=1)),
        (uuid4(), doc_2, TextChunk(text="Text 2", page=2)),
    ]

    def fake_retrieve(*args, **kwargs):
        assert kwargs.get("document_ids") == [doc_1, doc_2]
        return retrieved_chunks

    monkeypatch.setattr(api, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))
    monkeypatch.setattr(api, "get_document_extraction", lambda *args, **kwargs: None)

    user = (uuid4(), tenant_id, "user@example.com", False)
    ctx = api._prepare_ask(req, user, settings)

    assert len(ctx.retrieved) == 2
    assert len(ctx.labeled_chunks) == 2
    assert ctx.document_numbers[doc_1] == 1
    assert ctx.document_numbers[doc_2] == 2
    assert ctx.labeled_chunks[0].label == "doc 1"
    assert ctx.labeled_chunks[1].label == "doc 2"


def test_prepare_ask_cross_tenant_document_exclusion(monkeypatch):
    """Negative test: another tenant's doc ID is provided in document_ids.
    retrieve_chunks is tenant-scoped and drops the other tenant's document;
    if none belong to caller, empty context is returned with zero leakage.
    """
    my_tenant_id = uuid4()
    foreign_doc_id = uuid4()

    settings = Settings(local_generation=True, local_embeddings=True)
    req = api.AskRequest(
        question="What is in this other tenant document?",
        document_ids=[foreign_doc_id],
    )

    # retrieve_chunks returns empty list because foreign_doc_id is not in my_tenant_id
    def fake_retrieve_scoped(*args, **kwargs):
        assert kwargs.get("tenant_id") == my_tenant_id
        return []

    monkeypatch.setattr(api, "retrieve_chunks", fake_retrieve_scoped)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))

    user = (uuid4(), my_tenant_id, "user@example.com", False)
    ctx = api._prepare_ask(req, user, settings)

    assert ctx.retrieved == []
    assert ctx.labeled_chunks == []
    assert ctx.document_numbers == {}


def test_prepare_ask_empty_document_ids_returns_refusal_immediately():
    tenant_id = uuid4()
    settings = Settings(local_generation=True, local_embeddings=True)
    req = api.AskRequest(
        question="Compare empty list",
        document_ids=[],
    )
    user = (uuid4(), tenant_id, "user@example.com", False)
    ctx = api._prepare_ask(req, user, settings)
    assert ctx.retrieved == []
    assert ctx.labeled_chunks == []
