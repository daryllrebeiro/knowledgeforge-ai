"""PostgreSQL-backed Phase 2.5 tests: idempotency, claims, filters, cascades.

Runs against a real database with migration 014 applied (the same discipline
as test_postgres_tenant_isolation.py). Skipped unless TEST_DATABASE_URL is set.
"""

import json
import os

import psycopg
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured"
    ),
]

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.fixture()
def db():
    with psycopg.connect(DATABASE_URL) as connection:
        yield connection
        connection.rollback()


def _tenant(db) -> str:
    row = db.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", ("t",)).fetchone()
    return str(row[0])


def _document(
    db, tenant_id: str, *, storage_uri: str | None = "gs://b/f.md", content_hash: str = "h1"
) -> str:
    row = db.execute(
        "INSERT INTO documents (title, source_filename, doc_type, content_hash, tenant_id, "
        "status, storage_uri) VALUES (%s, %s, %s, %s, %s, 'ready', %s) RETURNING id",
        ("t", "f.md", "markdown", content_hash, tenant_id, storage_uri),
    ).fetchone()
    return str(row[0])


def test_migration_014_tables_exist(db) -> None:
    for table in (
        "document_extractions",
        "failed_extractions",
        "extraction_jobs",
        "extraction_outbox",
    ):
        exists = db.execute(
            "SELECT to_regclass(%s)", (f"public.{table}",)
        ).fetchone()[0]
        assert exists is not None, f"{table} missing"
    columns = {
        row[0]
        for row in db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'"
        ).fetchall()
    }
    assert {"detected_doc_type", "doc_type_confidence"} <= columns


def test_extraction_unique_key_is_tenant_scoped(db) -> None:
    """Identical bytes in two tenants are two extractions (not one shared row)."""
    from knowledgeforge.extraction.schemas import InvoiceExtraction
    from knowledgeforge.extraction.store import store_document_extraction

    tenant_a, tenant_b = _tenant(db), _tenant(db)
    doc_a = _document(db, tenant_a, content_hash="shared")
    doc_b = _document(db, tenant_b, content_hash="shared")
    for tenant_id, document_id in ((tenant_a, doc_a), (tenant_b, doc_b)):
        store_document_extraction(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            content_hash="shared",
            schema_type="invoice",
            schema_version=1,
            model="m",
            extraction=InvoiceExtraction(vendor_name="Acme", total=10.0),
            field_confidence={"total": 0.9},
            overall_confidence=0.9,
            needs_review=False,
            input_tokens=0,
            output_tokens=0,
        )
    count = db.execute(
        "SELECT count(*) FROM document_extractions WHERE content_hash = 'shared'"
    ).fetchone()[0]
    assert count == 2


def test_reprocess_replaces_same_unique_key(db) -> None:
    from knowledgeforge.extraction.schemas import InvoiceExtraction
    from knowledgeforge.extraction.store import store_document_extraction

    tenant_id = _tenant(db)
    document_id = _document(db, tenant_id)
    kwargs = {
        "tenant_id": tenant_id,
        "document_id": document_id,
        "content_hash": "h1",
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "m",
        "field_confidence": {"total": 0.9},
        "overall_confidence": 0.9,
        "needs_review": False,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    store_document_extraction(
        db, extraction=InvoiceExtraction(vendor_name="Old", total=10.0), **kwargs
    )
    store_document_extraction(
        db, extraction=InvoiceExtraction(vendor_name="New", total=20.0), **kwargs
    )
    row = db.execute(
        "SELECT fields->>'vendor_name' FROM document_extractions WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    assert row[0] == "New"


def test_active_job_uniqueness(db) -> None:
    from knowledgeforge.extraction.store import insert_extraction_job

    tenant_id = _tenant(db)
    document_id = _document(db, tenant_id)
    first = insert_extraction_job(
        db,
        document_id=document_id,
        tenant_id=tenant_id,
        content_hash="h1",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    assert first is not None
    # A second active job for the same document is a no-op.
    second = insert_extraction_job(
        db,
        document_id=document_id,
        tenant_id=tenant_id,
        content_hash="h1",
        schema_type="invoice",
        schema_version=1,
        model="m",
        reason="reprocess",
    )
    assert second is None
    # Sync-path documents (no storage_uri) never get extraction jobs.
    sync_document = _document(db, tenant_id, storage_uri=None, content_hash="h2")
    assert (
        insert_extraction_job(
            db,
            document_id=sync_document,
            tenant_id=tenant_id,
            content_hash="h2",
            schema_type="invoice",
            schema_version=1,
            model="m",
        )
        is None
    )


def test_claim_and_outbox_lifecycle(db) -> None:
    from knowledgeforge.extraction.store import (
        claim_extraction_job,
        claim_outbox_batch,
        insert_extraction_job,
        mark_outbox_sent,
    )

    tenant_id = _tenant(db)
    document_id = _document(db, tenant_id)
    job_id = insert_extraction_job(
        db,
        document_id=document_id,
        tenant_id=tenant_id,
        content_hash="h1",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    assert job_id is not None
    # Outbox row was created alongside the job, with the job payload.
    rows = claim_outbox_batch(db, batch_size=10, lease_seconds=60)
    assert len(rows) == 1
    payload = rows[0].payload if isinstance(rows[0].payload, dict) else json.loads(rows[0].payload)
    assert payload["job_id"] == str(job_id)
    # First claim wins; a second claim of the same job is a no-op.
    assert claim_extraction_job(db, job_id) is True
    assert claim_extraction_job(db, job_id) is False
    # Sent rows are never re-claimed.
    mark_outbox_sent(db, rows[0].outbox_id)
    db.commit()
    assert claim_outbox_batch(db, batch_size=10, lease_seconds=60) == []


def test_field_filters_are_allow_listed(db) -> None:
    from knowledgeforge.extraction.schemas import InvoiceExtraction
    from knowledgeforge.extraction.store import (
        find_document_ids_by_fields,
        list_extractions,
        store_document_extraction,
    )

    tenant_id = _tenant(db)
    document_id = _document(db, tenant_id)
    other_id = _document(db, tenant_id, content_hash="h2")
    for document_id_, vendor in ((document_id, "Acme"), (other_id, "Globex")):
        store_document_extraction(
            db,
            tenant_id=tenant_id,
            document_id=document_id_,
            content_hash=f"hash-{vendor}",
            schema_type="invoice",
            schema_version=1,
            model="m",
            extraction=InvoiceExtraction(vendor_name=vendor, total=10.0),
            field_confidence={"total": 0.9},
            overall_confidence=0.9,
            needs_review=False,
            input_tokens=0,
            output_tokens=0,
        )
    matches = find_document_ids_by_fields(
        db, tenant_id, schema_type="invoice", field_filters={"vendor_name": "Acme"}
    )
    assert [str(document_id)] == [str(m) for m in matches]
    rows = list_extractions(db, tenant_id, field_filters={"vendor_name": "Globex"})
    assert [r.fields["vendor_name"] for r in rows] == ["Globex"]
    with pytest.raises(ValueError, match="unsupported extraction filter"):
        find_document_ids_by_fields(db, tenant_id, field_filters={"total": "10"})


def test_tenant_isolation_on_extractions(db) -> None:
    from knowledgeforge.extraction.schemas import InvoiceExtraction
    from knowledgeforge.extraction.store import (
        find_document_ids_by_fields,
        store_document_extraction,
    )

    tenant_a, tenant_b = _tenant(db), _tenant(db)
    document_a = _document(db, tenant_a, content_hash="ta")
    store_document_extraction(
        db,
        tenant_id=tenant_a,
        document_id=document_a,
        content_hash="ta",
        schema_type="invoice",
        schema_version=1,
        model="m",
        extraction=InvoiceExtraction(vendor_name="Acme", total=10.0),
        field_confidence={"total": 0.9},
        overall_confidence=0.9,
        needs_review=False,
        input_tokens=0,
        output_tokens=0,
    )
    # Tenant B cannot see or resolve tenant A's extraction by any filter.
    assert find_document_ids_by_fields(db, tenant_b, schema_type="invoice") == []
    assert find_document_ids_by_fields(db, tenant_b, field_filters={"vendor_name": "Acme"}) == []


def test_document_deletion_cascades(db) -> None:
    from knowledgeforge.extraction.schemas import InvoiceExtraction
    from knowledgeforge.extraction.store import (
        insert_extraction_job,
        record_failed_extraction,
        store_document_extraction,
    )

    tenant_id = _tenant(db)
    document_id = _document(db, tenant_id)
    store_document_extraction(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        content_hash="h1",
        schema_type="invoice",
        schema_version=1,
        model="m",
        extraction=InvoiceExtraction(vendor_name="Acme", total=10.0),
        field_confidence={"total": 0.9},
        overall_confidence=0.9,
        needs_review=False,
        input_tokens=0,
        output_tokens=0,
    )
    record_failed_extraction(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        schema_type="invoice",
        raw_output="bad json",
        error="validation failed",
        attempt_count=2,
    )
    insert_extraction_job(
        db,
        document_id=document_id,
        tenant_id=tenant_id,
        content_hash="h1",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    db.commit()
    db.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    db.commit()
    for table in ("document_extractions", "failed_extractions", "extraction_jobs"):
        count = db.execute(
            f"SELECT count(*) FROM {table} WHERE document_id = %s", (document_id,)
        ).fetchone()[0]
        assert count == 0, f"{table} rows survived document deletion"
    # Outbox rows cascade via the job.
    count = db.execute(
        "SELECT count(*) FROM extraction_outbox WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()[0]
    assert count == 0
