from io import BytesIO

import psycopg
from google import genai
from google.genai import types

from knowledgeforge.config import Settings
from knowledgeforge.extraction.store import insert_extraction_job
from knowledgeforge.ingestion.chunk import chunk_pages
from knowledgeforge.ingestion.embed import embed_texts_local
from knowledgeforge.ingestion.embed_cache import embed_texts_cached
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_docx import extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.extract_ocr import build_ocr_provider, is_image_upload, mime_type_for
from knowledgeforge.ingestion.extract_text import extract_html, extract_text
from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.ingestion.store import record_request_log, store_chunks
from knowledgeforge.reliability import CircuitBreaker
from knowledgeforge.worker.cloud import CloudStorageClient

_worker_breaker: CircuitBreaker | None = None


def _gemini_breaker(settings: Settings) -> CircuitBreaker:
    global _worker_breaker
    if _worker_breaker is None:
        _worker_breaker = CircuitBreaker(
            failure_threshold=settings.gemini_breaker_failure_threshold,
            recovery_seconds=settings.gemini_breaker_recovery_seconds,
        )
    return _worker_breaker


def process_ingestion_job(job: IngestionJob, settings: Settings) -> None:
    storage = CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id)
    content = storage.download(job.storage_uri)
    filename = job.storage_uri.rsplit("/", 1)[-1].lower()
    if filename.endswith(".pdf"):
        pages = extract_pdf(BytesIO(content))
        if not pages:
            # Scanned PDF: no text layer, so OCR before chunking — the document
            # must become searchable before the extraction stage can run.
            pages = _ocr_pages(content, filename, settings, job.tenant_id)
    elif is_image_upload(filename):
        pages = _ocr_pages(content, filename, settings, job.tenant_id)
    elif filename.endswith(".docx"):
        pages = extract_docx(BytesIO(content))
    elif filename.endswith((".html", ".htm")):
        pages = extract_html(BytesIO(content))
    elif filename.endswith((".txt", ".text")):
        pages = extract_text(BytesIO(content))
    else:
        pages = extract_markdown(BytesIO(content))
    if not pages:
        raise ValueError("Document contains no extractable text")
    chunks = chunk_pages(pages, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
    with psycopg.connect(settings.database_url) as connection:
        if settings.local_embeddings:
            embeddings = embed_texts_local([chunk.text for chunk in chunks])
        else:
            client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
            )
            # Cached: identical chunk text (a re-ingestion, or the same content
            # under a new version) skips the embedding call entirely.
            embeddings = _gemini_breaker(settings).call(
                lambda: embed_texts_cached(
                    connection,
                    client,
                    [chunk.text for chunk in chunks],
                    model=settings.gemini_embedding_model,
                )
            ).vectors
        # Delete-and-insert runs as one transaction so a crash cannot leave a
        # claimed document with zero chunks, and the claim lease (see
        # store.claim_document) prevents concurrent double-inserts.
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM chunks WHERE document_id = %s", (job.document_id,))
            store_chunks(connection, job.document_id, chunks, embeddings)
            # Same transaction: the ready state and the extraction job/outbox
            # event commit together, so a document can never be ready without
            # its extraction trigger (transactional outbox). Only documents
            # with a stored original are extraction-eligible; the insert is a
            # no-op when an active job already exists (duplicate delivery).
            insert_extraction_job(
                connection,
                document_id=job.document_id,
                tenant_id=job.tenant_id,
                content_hash=job.content_hash,
                schema_type=settings.extraction_schema_type,
                schema_version=settings.extraction_schema_version,
                model=settings.extraction_model,
                reason="ready",
            )


def _ocr_pages(
    content: bytes, filename: str, settings: Settings, tenant_id: object
) -> list[tuple[int, str]]:
    """OCR scans/images through the breaker; token usage flows to request_logs."""
    from uuid import uuid4

    provider = build_ocr_provider(settings)
    result = _gemini_breaker(settings).call(
        lambda: provider.ocr(content, mime_type_for(filename))
    )
    try:
        with psycopg.connect(settings.database_url) as connection:
            record_request_log(
                connection,
                request_id=uuid4(),
                tenant_id=tenant_id,  # type: ignore[arg-type]
                query="[extraction:ocr]",
                retrieved_chunk_ids=[],
                latency_ms=0.0,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
    except Exception:
        # Telemetry must never fail ingestion itself.
        pass
    return result.pages
