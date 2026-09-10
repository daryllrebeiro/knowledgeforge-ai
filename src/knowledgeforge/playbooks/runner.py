"""Saved Playbooks execution engine with upfront budget reservation (Phase 8 Item 6).

Executes pre-configured reusable question sets automatically against matching newly
ingested documents.

CRITICAL INVARIANT: Each playbook question reserves token budget BEFORE execution
via the standard RedisBudgetCounter path. If budget is exceeded, execution fails
closed without partial untracked spend.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from knowledgeforge.generation.generate import generate_answer
from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.retrieval.retrieve import retrieve_chunks
from knowledgeforge.security.budget import RedisBudgetCounter

logger = logging.getLogger("knowledgeforge.playbooks")


class PlaybookBudgetExceededError(Exception):
    """Raised when tenant daily token budget prevents playbook execution."""


@dataclass(frozen=True)
class PlaybookRow:
    id: UUID
    tenant_id: UUID
    name: str
    doc_type: str | None
    schema_type: str | None
    questions: list[str]
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class PlaybookRunRow:
    id: UUID
    tenant_id: UUID
    playbook_id: UUID
    document_id: UUID
    question: str
    answer: str | None
    citations: list[dict[str, object]]
    status: str
    tokens_used: int
    created_at: datetime


def create_playbook(
    connection: Connection,
    *,
    tenant_id: UUID,
    name: str,
    questions: list[str],
    doc_type: str | None = None,
    schema_type: str | None = None,
) -> PlaybookRow:
    if not questions:
        raise ValueError("Playbook must contain at least one question")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO playbooks (tenant_id, name, doc_type, schema_type, questions)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id, tenant_id, name, doc_type, schema_type, questions, is_active, created_at
            """,
            (tenant_id, name, doc_type, schema_type, json.dumps(questions)),
        )
        r = cursor.fetchone()
        if r is None:
            raise RuntimeError("Failed to create playbook")
    return PlaybookRow(
        id=UUID(str(r[0])),
        tenant_id=UUID(str(r[1])),
        name=str(r[2]),
        doc_type=str(r[3]) if r[3] else None,
        schema_type=str(r[4]) if r[4] else None,
        questions=list(r[5]),
        is_active=bool(r[6]),
        created_at=r[7],
    )


def list_playbooks(
    connection: Connection,
    tenant_id: UUID,
) -> list[PlaybookRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, name, doc_type, schema_type, questions, is_active, created_at
            FROM playbooks
            WHERE tenant_id = %s
            ORDER BY created_at DESC
            """,
            (tenant_id,),
        )
        rows = cursor.fetchall()
    return [
        PlaybookRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            name=str(r[2]),
            doc_type=str(r[3]) if r[3] else None,
            schema_type=str(r[4]) if r[4] else None,
            questions=list(r[5]),
            is_active=bool(r[6]),
            created_at=r[7],
        )
        for r in rows
    ]


def run_playbook_question(
    connection: Connection,
    *,
    tenant_id: UUID,
    playbook_id: UUID,
    document_id: UUID,
    question: str,
    generator: Any,
    embedding_provider: Any,
    token_budget: RedisBudgetCounter | None = None,
    estimated_tokens: int = 500,
) -> PlaybookRunRow:
    """Run a single playbook question with upfront token budget reservation."""
    reserved = False
    if token_budget is not None:
        allowed, current_usage, _ = token_budget.check_and_reserve(str(tenant_id), estimated_tokens)
        if not allowed:
            logger.warning("Playbook run rejected: budget exceeded for tenant %s", tenant_id)
            raise PlaybookBudgetExceededError(
                f"Tenant daily token budget exceeded: {current_usage}"
            )
        reserved = True

    try:
        # 1. Embed query
        query_embedding = embedding_provider.embed(question)

        # 2. Retrieve chunks for the document
        chunks = retrieve_chunks(
            connection,
            query_embedding,
            tenant_id=tenant_id,
            limit=5,
        )
        # Filter chunks to only this document
        doc_chunks = [c for c in chunks if c[1] == document_id]
        labeled_chunks = [
            LabeledChunk(label="doc 1", chunk=c[2])
            for c in doc_chunks
        ]

        # 3. Generate answer
        answer_result = generate_answer(generator, question, labeled_chunks)
        citations_json = [
            {"document_index": c.document_index, "page": c.page}
            for c in answer_result.citations
        ]

        # 4. Token reconciliation
        actual_tokens = (
            getattr(answer_result, "input_tokens", estimated_tokens // 2)
            + getattr(answer_result, "output_tokens", estimated_tokens // 2)
        )
        if token_budget is not None and reserved:
            token_budget.reconcile(str(tenant_id), actual_tokens)
            reserved = False  # reconciled

        # 5. Persist run record
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO playbook_runs (
                    tenant_id, playbook_id, document_id, question, answer, citations, tokens_used, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed')
                RETURNING id, tenant_id, playbook_id, document_id, question, answer, citations, status, tokens_used, created_at
                """,
                (
                    tenant_id,
                    playbook_id,
                    document_id,
                    question,
                    answer_result.answer,
                    json.dumps(citations_json),
                    actual_tokens,
                ),
            )
            r = cursor.fetchone()
            if r is None:
                raise RuntimeError("Failed to insert playbook run")

        return PlaybookRunRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            playbook_id=UUID(str(r[2])),
            document_id=UUID(str(r[3])),
            question=str(r[4]),
            answer=str(r[5]),
            citations=list(r[6]),
            status=str(r[7]),
            tokens_used=int(r[8]),
            created_at=r[9],
        )

    except Exception:
        if token_budget is not None and reserved:
            token_budget.release_reservation(str(tenant_id), estimated_tokens)
        raise


def trigger_matching_playbooks(
    connection: Connection,
    *,
    tenant_id: UUID,
    document_id: UUID,
    doc_type: str,
    generator: Any,
    embedding_provider: Any,
    token_budget: RedisBudgetCounter | None = None,
) -> list[PlaybookRunRow]:
    """Find active playbooks matching doc_type and run all questions."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, questions FROM playbooks
            WHERE tenant_id = %s AND is_active = true
              AND (doc_type IS NULL OR doc_type = %s)
            """,
            (tenant_id, doc_type),
        )
        matching = cursor.fetchall()

    results: list[PlaybookRunRow] = []
    for playbook_id_raw, questions_raw in matching:
        playbook_id = UUID(str(playbook_id_raw))
        questions = list(questions_raw)
        for q in questions:
            run_row = run_playbook_question(
                connection,
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                document_id=document_id,
                question=q,
                generator=generator,
                embedding_provider=embedding_provider,
                token_budget=token_budget,
            )
            results.append(run_row)
    return results


def get_document_playbook_results(
    connection: Connection,
    document_id: UUID,
    tenant_id: UUID,
) -> list[PlaybookRunRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, playbook_id, document_id, question, answer, citations, status, tokens_used, created_at
            FROM playbook_runs
            WHERE document_id = %s AND tenant_id = %s
            ORDER BY created_at ASC
            """,
            (document_id, tenant_id),
        )
        rows = cursor.fetchall()

    return [
        PlaybookRunRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            playbook_id=UUID(str(r[2])),
            document_id=UUID(str(r[3])),
            question=str(r[4]),
            answer=str(r[5]) if r[5] else None,
            citations=list(r[6]),
            status=str(r[7]),
            tokens_used=int(r[8]),
            created_at=r[9],
        )
        for r in rows
    ]
