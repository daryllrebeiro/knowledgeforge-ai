import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from time import perf_counter
from typing import Annotated, BinaryIO, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from knowledgeforge.config import Settings, get_settings
from knowledgeforge.conversations import (
    ConversationRow,
    append_exchange,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_messages,
    list_conversations,
)
from knowledgeforge.db import get_connection
from knowledgeforge.extraction.store import (
    DocumentExtractionRow,
    find_document_ids_by_fields,
    get_document_extraction,
    get_extraction_job,
    insert_extraction_job,
    list_extractions,
)
from knowledgeforge.generation.condense import rewrite_followup_question
from knowledgeforge.generation.gemini import GeminiTextGenerator, GeminiTextStream
from knowledgeforge.generation.generate import Citation, generate_answer, parse_citations
from knowledgeforge.generation.local import local_answer
from knowledgeforge.generation.prompt import LabeledChunk, LabeledExtraction, build_prompt
from knowledgeforge.ingestion.chunk import TextChunk, chunk_pages
from knowledgeforge.ingestion.dedup import content_hash, decide_dedup
from knowledgeforge.ingestion.embed import embed_texts, embed_texts_local
from knowledgeforge.ingestion.embed_cache import embed_texts_cached
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_docx import extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.extract_text import extract_html, extract_text
from knowledgeforge.ingestion.store import (
    count_documents,
    create_pending_document,
    delete_document,
    delete_tenant,
    find_document_by_hash,
    find_latest_document_by_filename,
    get_document_detail,
    get_document_ingest_info,
    list_document_chunks,
    list_documents,
    list_failed_ingestions,
    mark_superseded,
    queue_reingestion,
    record_failed_ingestion,
    record_request_log,
    store_document,
    tenant_usage,
    tenant_usage_daily,
)
from knowledgeforge.limits import RedisTokenBucketLimiter, TokenBucketLimiter
from knowledgeforge.limits import limiter as default_limiter
from knowledgeforge.observability import request_id
from knowledgeforge.reliability import CircuitBreaker, CircuitOpenError
from knowledgeforge.retrieval.retrieve import retrieve_chunks
from knowledgeforge.security.api_keys import create_api_key, list_api_keys, revoke_api_key
from knowledgeforge.security.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from knowledgeforge.security.refresh import (
    InvalidRefreshToken,
    create_refresh_token,
    revoke_refresh_family,
    rotate_refresh_token,
)
from knowledgeforge.worker.cloud import CloudStorageClient, PubSubPublisher

logger = logging.getLogger("knowledgeforge.api")

router = APIRouter()
limiter: TokenBucketLimiter | RedisTokenBucketLimiter = default_limiter

# Multipart bodies carry more than the file bytes; tolerate that overhead in the
# early Content-Length check so files right at the limit are not pre-rejected.
_MULTIPART_OVERHEAD_BYTES = 65_536
_UPLOAD_READ_BLOCK = 1_048_576


def _client_subject(request: Request, purpose: str) -> UUID:
    """Create a non-account auth limiter key from the caller address."""
    address = request.client.host if request.client is not None else "unknown"
    return uuid5(NAMESPACE_URL, f"knowledgeforge:{purpose}:{address}")


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    status: str = "ready"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    document_id: UUID | None = None
    # Restrict retrieval to one document type ("pdf", "docx", "markdown", "text", "html").
    doc_type: str | None = Field(default=None, max_length=20)
    # When set, the question is treated as a follow-up in that conversation:
    # it is rewritten standalone before retrieval and the exchange is persisted.
    conversation_id: UUID | None = None
    # Structured-filter pre-step (Phase 2.5): retrieval is scoped to documents
    # with a matching extraction and their extracted fields join the prompt.
    structured_filters: "StructuredFilters | None" = None


class StructuredFilters(BaseModel):
    """Allow-listed extracted-field filters for /ask (see extraction store)."""

    schema_type: str | None = Field(default=None, max_length=40)
    vendor_name: str | None = Field(default=None, max_length=300)
    invoice_number: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, max_length=8)

    def field_filters(self) -> dict[str, str]:
        values = {
            "vendor_name": self.vendor_name,
            "invoice_number": self.invoice_number,
            "currency": self.currency,
        }
        return {field: value for field, value in values.items() if value is not None}


class CitationResponse(BaseModel):
    document_id: UUID
    page: int | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    conversation_id: UUID | None = None


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationSummary(BaseModel):
    conversation_id: UUID
    title: str
    updated_at: str
    message_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    limit: int
    offset: int


class MessageResponse(BaseModel):
    role: str
    content: str
    citations: list[CitationResponse]
    created_at: str


class ConversationDetailResponse(BaseModel):
    conversation_id: UUID
    title: str
    updated_at: str
    messages: list[MessageResponse]


class FailedIngestionResponse(BaseModel):
    id: UUID
    filename: str
    error_message: str


class BatchUploadResponse(BaseModel):
    filename: str
    status: str
    document_id: UUID | None = None
    error: str | None = None


class ExtractionResponse(BaseModel):
    document_id: UUID
    schema_type: str
    schema_version: int
    model: str
    fields: dict[str, object]
    field_confidence: dict[str, float]
    overall_confidence: float
    needs_review: bool
    created_at: str


class ExtractionListResponse(BaseModel):
    extractions: list[ExtractionResponse]
    limit: int
    offset: int


class ExtractionJobResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    reason: str
    schema_type: str
    schema_version: int
    model: str
    detail: str | None
    attempt_count: int
    created_at: str
    updated_at: str


class ReprocessRequest(BaseModel):
    schema_version: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=100)


class ReprocessResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str = "queued"


class DocumentSummary(BaseModel):
    document_id: UUID
    title: str
    doc_type: str
    status: str
    version: int
    superseded_by: UUID | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]
    limit: int
    offset: int


class DocumentDetailResponse(BaseModel):
    document_id: UUID
    title: str
    filename: str
    doc_type: str
    status: str
    version: int
    superseded_by: UUID | None = None
    chunk_count: int


class ChunkPreviewItem(BaseModel):
    page: int
    section: str | None
    text: str


class ChunkPreviewResponse(BaseModel):
    chunks: list[ChunkPreviewItem]
    limit: int
    offset: int


class UsageDayItem(BaseModel):
    day: str
    queries: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class UsageResponse(BaseModel):
    tenant_id: UUID
    documents: int
    queries: int
    cost_estimate: float
    input_tokens: int = 0
    output_tokens: int = 0
    daily: list[UsageDayItem] = []


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8)
    tenant_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyCreatedResponse(BaseModel):
    key_id: UUID
    name: str
    # The plaintext key is shown exactly once; only its hash is stored.
    key: str
    key_prefix: str


class ApiKeyListedResponse(BaseModel):
    key_id: UUID
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, http_request: Request) -> TokenResponse:
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "register"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    try:
        with get_connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO tenants (name) VALUES (%s) RETURNING id",
                        (request.tenant_name,),
                    )
                    tenant_row = cursor.fetchone()
                    if tenant_row is None:
                        raise RuntimeError("tenant insert did not return an ID")
                    tenant_id = UUID(str(tenant_row[0]))
                    cursor.execute(
                        "INSERT INTO users (tenant_id, email, hashed_password) "
                        "VALUES (%s, %s, %s) RETURNING id",
                        (tenant_id, request.email.lower(), hash_password(request.password)),
                    )
                    user_row = cursor.fetchone()
                    if user_row is None:
                        raise RuntimeError("user insert did not return an ID")
                    user_id = UUID(str(user_row[0]))
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Registration failed for %s", request.email, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration temporarily unavailable",
        ) from exc
    with get_connection() as connection:
        refresh_token = create_refresh_token(connection, user_id)
    return TokenResponse(
        access_token=create_access_token(user_id, tenant_id), refresh_token=refresh_token
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(request: LoginRequest, http_request: Request) -> TokenResponse:
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "login"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, tenant_id, hashed_password FROM users WHERE email = %s",
                (request.email.lower(),),
            )
            row = cursor.fetchone()
    if row is None or not verify_password(request.password, str(row[2])):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user_id, tenant_id = UUID(str(row[0])), UUID(str(row[1]))
    with get_connection() as connection:
        refresh_token = create_refresh_token(connection, user_id)
    return TokenResponse(
        access_token=create_access_token(user_id, tenant_id), refresh_token=refresh_token
    )


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, http_request: Request) -> TokenResponse:
    """Rotate a refresh token: the old one dies, a new one is returned.

    Presenting an already-rotated token is treated as replay — the whole token
    family is revoked and the caller must log in again.
    """
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "refresh"), "auth", settings.auth_rate_limit_per_minute
    )
    with get_connection() as connection:
        try:
            user_id, tenant_id, new_refresh = rotate_refresh_token(
                connection, request.refresh_token
            )
        except InvalidRefreshToken as exc:
            raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    return TokenResponse(
        access_token=create_access_token(user_id, tenant_id), refresh_token=new_refresh
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: RefreshRequest,
    http_request: Request,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> None:
    """Revoke the refresh-token family behind the presented token."""
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "logout"), "auth", settings.auth_rate_limit_per_minute
    )
    with get_connection() as connection:
        revoke_refresh_family(connection, request.refresh_token)


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_new_api_key(
    request: ApiKeyCreateRequest,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ApiKeyCreatedResponse:
    """Create an API key. The plaintext key is returned once and never again."""
    with get_connection() as connection:
        key_id, key = create_api_key(connection, current_user[1], current_user[0], request.name)
    return ApiKeyCreatedResponse(key_id=key_id, name=request.name, key=key, key_prefix=key[:12])


@router.get("/api-keys", response_model=list[ApiKeyListedResponse])
def api_keys(
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> list[ApiKeyListedResponse]:
    with get_connection() as connection:
        rows = list_api_keys(connection, current_user[1])
    return [
        ApiKeyListedResponse(
            key_id=row.key_id,
            name=row.name,
            key_prefix=row.key_prefix,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            revoked=row.revoked,
        )
        for row in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_api_key(
    key_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> None:
    with get_connection() as connection:
        found = revoke_api_key(connection, key_id, current_user[1])
    if not found:
        raise HTTPException(status_code=404, detail="API key not found")


_cached_gemini_client: tuple[str, genai.Client] | None = None


def _gemini_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
        raise HTTPException(status_code=503, detail="Gemini API key is not configured")
    global _cached_gemini_client
    if _cached_gemini_client is None or _cached_gemini_client[0] != settings.gemini_api_key:
        _cached_gemini_client = (
            settings.gemini_api_key,
            genai.Client(
                api_key=settings.gemini_api_key,
                # http_options timeout is in milliseconds; without it a hung provider
                # call would pin the request indefinitely.
                http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
            ),
        )
    return _cached_gemini_client[1]


_gemini_breaker: CircuitBreaker | None = None


def gemini_breaker() -> CircuitBreaker:
    """Process-wide Gemini circuit breaker, configured from settings."""
    global _gemini_breaker
    if _gemini_breaker is None:
        settings = get_settings()
        _gemini_breaker = CircuitBreaker(
            failure_threshold=settings.gemini_breaker_failure_threshold,
            recovery_seconds=settings.gemini_breaker_recovery_seconds,
        )
    return _gemini_breaker


def _raise_provider_unavailable(exc: CircuitOpenError) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Model provider temporarily unavailable",
    ) from exc


_publisher: PubSubPublisher | None = None


def _pubsub_publisher(settings: Settings) -> PubSubPublisher:
    """Cache the publisher; constructing a client per upload is pure overhead."""
    global _publisher
    if _publisher is None:
        _publisher = PubSubPublisher(settings.gcp_project_id, settings.pubsub_topic)
    return _publisher


def _read_upload(stream: BinaryIO, request: Request | None, *, max_bytes: int) -> bytes:
    """Read an upload stream with an early Content-Length check and a hard cutoff.

    The chunked read is authoritative: a lying or missing Content-Length still
    cannot push more than ``max_bytes`` into memory. Batch uploads pass
    ``request=None`` because their Content-Length covers every file, not one.
    """
    if request is not None:
        declared = request.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > max_bytes + _MULTIPART_OVERHEAD_BYTES
        ):
            raise HTTPException(status_code=413, detail="Upload exceeds configured size limit")
    parts: list[bytes] = []
    total = 0
    while True:
        part = stream.read(_UPLOAD_READ_BLOCK)
        if not part:
            break
        parts.append(part)
        total += len(part)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Upload exceeds configured size limit")
    return b"".join(parts)


def _ingest_upload(
    content: bytes,
    filename: str,
    content_type: str | None,
    current_user: tuple[UUID, UUID],
    settings: Settings,
) -> DocumentUploadResponse:
    """Shared ingestion core for single and batch uploads (rate limiting is the caller's)."""
    document_hash = content_hash(content)
    _, tenant_id = current_user
    with get_connection() as connection:
        if count_documents(connection, tenant_id) >= settings.max_documents_per_tenant:
            raise HTTPException(status_code=402, detail="Document quota exceeded")
    try:
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        doc_type = {
            "pdf": "pdf",
            "docx": "docx",
            "md": "markdown",
            "markdown": "markdown",
            "txt": "text",
            "text": "text",
            "html": "html",
            "htm": "html",
            "png": "image",
            "jpg": "image",
            "jpeg": "image",
            "tif": "image",
            "tiff": "image",
        }.get(suffix)
        if doc_type is None:
            raise HTTPException(
                status_code=415,
                detail="Supported file types are PDF, DOCX, Markdown, TXT, HTML, "
                "and images (PNG, JPEG, TIFF)",
            )
        if doc_type == "image" and not settings.async_ingestion:
            # OCR runs in the ingestion worker, which only exists for the async
            # path; a synchronous upload has no worker to OCR it.
            raise HTTPException(
                status_code=415,
                detail="Image uploads require async ingestion; OCR runs in the worker",
            )
        if settings.async_ingestion:
            if not settings.gcp_project_id or not settings.gcs_bucket:
                raise HTTPException(status_code=503, detail="Cloud ingestion is not configured")
            with get_connection() as connection:
                existing = find_document_by_hash(connection, document_hash, tenant_id)
                previous = find_latest_document_by_filename(connection, filename, tenant_id)
                decision = decide_dedup(existing, previous)
                if decision.action == "duplicate" and existing is not None:
                    return DocumentUploadResponse(document_id=existing[0], status="duplicate")
                storage_uri = CloudStorageClient(
                    settings.gcs_bucket, settings.gcp_project_id
                ).upload(f"{tenant_id}/{document_hash}/{filename}", content, content_type)
                document_id = create_pending_document(
                    connection,
                    title=filename,
                    source_filename=filename,
                    doc_type=doc_type,
                    content_hash=document_hash,
                    storage_uri=storage_uri,
                    tenant_id=tenant_id,
                    version=decision.version,
                )
                if previous is not None:
                    mark_superseded(connection, previous[0], document_id)
            _pubsub_publisher(settings).publish(
                json.dumps(
                    {
                        "document_id": str(document_id),
                        "tenant_id": str(tenant_id),
                        "storage_uri": storage_uri,
                        "content_hash": document_hash,
                    }
                ).encode()
            )
            return DocumentUploadResponse(document_id=document_id, status="pending")
        if content_type == "application/pdf" or suffix == "pdf":
            pages = extract_pdf(BytesIO(content))
        elif suffix == "docx":
            pages = extract_docx(BytesIO(content))
        elif suffix in {"html", "htm"} or content_type == "text/html":
            pages = extract_html(BytesIO(content))
        elif suffix in {"md", "markdown"} or content_type == "text/markdown":
            pages = extract_markdown(BytesIO(content))
        else:
            # .txt (and any text/plain upload): one paragraph per location.
            pages = extract_text(BytesIO(content))
        chunks = chunk_pages(
            pages,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            section_aware=settings.chunk_section_aware,
        )
        if not chunks:
            raise ValueError("Document contains no extractable text")
        client = _gemini_client()
        with get_connection() as connection:
            # Cached: identical chunk text (a re-upload, or the same content
            # under a new version) skips the embedding call entirely.
            try:
                embeddings = gemini_breaker().call(
                    lambda: embed_texts_cached(
                        connection,
                        client,
                        [chunk.text for chunk in chunks],
                        model=settings.gemini_embedding_model,
                    )
                ).vectors
            except CircuitOpenError as exc:
                _raise_provider_unavailable(exc)
            existing = find_document_by_hash(connection, document_hash, tenant_id)
            previous = find_latest_document_by_filename(connection, filename, tenant_id)
            decision = decide_dedup(existing, previous)
            if decision.action == "duplicate" and existing is not None:
                return DocumentUploadResponse(document_id=existing[0], status="duplicate")
            document_id = store_document(
                connection,
                title=filename,
                source_filename=filename,
                doc_type=doc_type,
                chunks=chunks,
                embeddings=embeddings,
                content_hash=document_hash,
                version=decision.version,
                tenant_id=tenant_id,
            )
            if previous is not None:
                mark_superseded(connection, previous[0], document_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Ingestion failed for %s", filename, exc_info=True)
        raise HTTPException(status_code=422, detail="Unable to ingest document") from exc
    return DocumentUploadResponse(document_id=document_id)


@router.post(
    "/documents", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentUploadResponse:
    settings = get_settings()
    content = _read_upload(file.file, request, max_bytes=settings.max_upload_bytes)
    _, tenant_id = current_user
    limiter.check(tenant_id, "documents", settings.document_rate_limit_per_minute)
    return _ingest_upload(
        content, file.filename or "upload", file.content_type, current_user, settings
    )


@router.post("/documents/batch", response_model=list[BatchUploadResponse])
def upload_documents_batch(
    files: Annotated[list[UploadFile], File(...)],
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> list[BatchUploadResponse]:
    """Process each file independently so one corrupt document cannot abort the batch."""
    settings = get_settings()
    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=413,
            detail=f"Batch uploads are limited to {settings.max_batch_files} files",
        )
    if not files:
        return []
    # One rate-limit token for the whole batch, not one per file.
    limiter.check(current_user[1], "documents", settings.document_rate_limit_per_minute)
    results: list[BatchUploadResponse] = []
    for file in files:
        filename = file.filename or "upload"
        try:
            content = _read_upload(file.file, None, max_bytes=settings.max_upload_bytes)
            result = _ingest_upload(content, filename, file.content_type, current_user, settings)
            results.append(
                BatchUploadResponse(
                    filename=filename,
                    status=result.status,
                    document_id=result.document_id,
                )
            )
        except HTTPException as exc:
            try:
                with get_connection() as connection:
                    record_failed_ingestion(connection, filename, str(exc.detail), current_user[1])
            except Exception:
                logger.error("Failed to record failed ingestion for %s", filename, exc_info=True)
            results.append(
                BatchUploadResponse(filename=filename, status="failed", error=str(exc.detail))
            )
    return results


@router.get("/documents", response_model=DocumentListResponse)
def documents(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentListResponse:
    with get_connection() as connection:
        rows = list_documents(connection, current_user[1], limit=limit, offset=offset)
    return DocumentListResponse(
        documents=[
            DocumentSummary(
                document_id=row.document_id,
                title=row.title,
                doc_type=row.doc_type,
                status=row.status,
                version=row.version,
                superseded_by=UUID(row.superseded_by) if row.superseded_by is not None else None,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.get("/ingestions/failed", response_model=list[FailedIngestionResponse])
def failed_ingestions(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> list[FailedIngestionResponse]:
    with get_connection() as connection:
        rows = list_failed_ingestions(connection, current_user[1], limit=limit, offset=offset)
    return [
        FailedIngestionResponse(id=row[0], filename=row[1], error_message=row[2]) for row in rows
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def document_detail(
    document_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentDetailResponse:
    with get_connection() as connection:
        detail = get_document_detail(connection, document_id, current_user[1])
    if detail is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailResponse(
        document_id=detail.document_id,
        title=detail.title,
        filename=detail.filename,
        doc_type=detail.doc_type,
        status=detail.status,
        version=detail.version,
        superseded_by=UUID(detail.superseded_by) if detail.superseded_by is not None else None,
        chunk_count=detail.chunk_count,
    )


@router.get("/documents/{document_id}/chunks", response_model=ChunkPreviewResponse)
def document_chunks(
    document_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ChunkPreviewResponse:
    """Preview what was indexed for a document, chunk by chunk (F3)."""
    with get_connection() as connection:
        rows = list_document_chunks(
            connection, document_id, current_user[1], limit=limit, offset=offset
        )
    if rows is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return ChunkPreviewResponse(
        chunks=[
            ChunkPreviewItem(page=row.page, section=row.section, text=row.text) for row in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.post(
    "/documents/{document_id}/reingest",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reingest_document(
    document_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentUploadResponse:
    """Re-process a finished document from its stored original (F3).

    Re-chunking and re-embedding with the current settings — without uploading
    the file again. The embedding cache means unchanged chunk text is not
    re-embedded. Only meaningful with async ingestion; sync mode has no stored
    original to re-process.
    """
    settings = get_settings()
    if not settings.async_ingestion:
        raise HTTPException(
            status_code=409,
            detail="Re-ingestion requires async ingestion; upload the document again instead",
        )
    limiter.check(current_user[1], "documents", settings.document_rate_limit_per_minute)
    with get_connection() as connection:
        info = get_document_ingest_info(connection, document_id, current_user[1])
    if info is None:
        raise HTTPException(status_code=404, detail="Document not found")
    status_value, storage_uri, document_hash = info
    if storage_uri is None:
        raise HTTPException(
            status_code=409, detail="Document has no stored original to re-ingest"
        )
    if status_value not in {"ready", "failed"}:
        raise HTTPException(
            status_code=409,
            detail=f"Document is {status_value}; only ready/failed documents can be re-ingested",
        )
    with get_connection() as connection:
        if not queue_reingestion(connection, document_id):
            # State changed between the check and the re-queue (concurrent
            # re-ingest or a worker claim) — report it rather than double-queue.
            raise HTTPException(status_code=409, detail="Document is already queued")
    _pubsub_publisher(settings).publish(
        json.dumps(
            {
                "document_id": str(document_id),
                "tenant_id": str(current_user[1]),
                "storage_uri": storage_uri,
                "content_hash": document_hash,
            }
        ).encode()
    )
    return DocumentUploadResponse(document_id=document_id, status="pending")


def _extraction_response(row: DocumentExtractionRow) -> ExtractionResponse:
    return ExtractionResponse(
        document_id=row.document_id,
        schema_type=row.schema_type,
        schema_version=row.schema_version,
        model=row.model,
        fields=row.fields,
        field_confidence=row.field_confidence,
        overall_confidence=row.overall_confidence,
        needs_review=row.needs_review,
        created_at=row.created_at,
    )


@router.get("/documents/{document_id}/extraction", response_model=ExtractionResponse)
def document_extraction(
    document_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ExtractionResponse:
    """Latest extraction for a document, or 404 when none exists.

    A 404 covers not-yet-processed, unclassified, and synchronous-path
    (not extraction-eligible) documents alike.
    """
    with get_connection() as connection:
        row = get_document_extraction(connection, document_id, current_user[1])
    if row is None:
        raise HTTPException(status_code=404, detail="No extraction for this document")
    return _extraction_response(row)


@router.get("/extractions", response_model=ExtractionListResponse)
def extractions(
    schema_type: Annotated[str | None, Query(max_length=40)] = None,
    vendor_name: Annotated[str | None, Query(max_length=300)] = None,
    invoice_number: Annotated[str | None, Query(max_length=200)] = None,
    currency: Annotated[str | None, Query(max_length=8)] = None,
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ExtractionListResponse:
    """Tenant-scoped extraction list with allow-listed JSONB field filters."""
    field_filters = {
        field: value
        for field, value in {
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "currency": currency,
        }.items()
        if value is not None
    }
    with get_connection() as connection:
        rows = list_extractions(
            connection,
            current_user[1],
            schema_type=schema_type,
            field_filters=field_filters,
            needs_review=needs_review,
            limit=limit,
            offset=offset,
        )
    return ExtractionListResponse(
        extractions=[_extraction_response(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/admin/extractions/review-queue", response_model=ExtractionListResponse)
def extraction_review_queue(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ExtractionListResponse:
    """Low-confidence extractions (``needs_review = true``), newest first."""
    with get_connection() as connection:
        rows = list_extractions(
            connection,
            current_user[1],
            needs_review=True,
            limit=limit,
            offset=offset,
        )
    return ExtractionListResponse(
        extractions=[_extraction_response(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionJobResponse)
def extraction_job_status(
    job_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ExtractionJobResponse:
    """Extraction lifecycle view; raw model output is never exposed here."""
    with get_connection() as connection:
        row = get_extraction_job(connection, job_id, current_user[1])
    if row is None:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    return ExtractionJobResponse(
        job_id=row.job_id,
        document_id=row.document_id,
        status=row.status,
        reason=row.reason,
        schema_type=row.schema_type,
        schema_version=row.schema_version,
        model=row.model,
        detail=row.detail,
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/documents/{document_id}/extraction/reprocess",
    response_model=ReprocessResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_extraction(
    document_id: UUID,
    request: ReprocessRequest,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ReprocessResponse:
    """Queue a forced re-extraction (async; the worker performs the model call).

    The forced run bypasses the extraction cache and replaces the existing
    successful row only after validation succeeds; a failed forced run
    preserves the last successful row and records a failed extraction. An
    active job for this document maps to 409.
    """
    settings = get_settings()
    limiter.check(current_user[1], "documents", settings.document_rate_limit_per_minute)
    with get_connection() as connection:
        info = get_document_ingest_info(connection, document_id, current_user[1])
    if info is None:
        raise HTTPException(status_code=404, detail="Document not found")
    status_value, storage_uri, document_hash = info
    if storage_uri is None:
        raise HTTPException(
            status_code=422,
            detail="Document is not extraction-eligible (no stored original)",
        )
    if status_value != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Document is {status_value}; extraction requires a ready document",
        )
    with get_connection() as connection:
        job_id = insert_extraction_job(
            connection,
            document_id=document_id,
            tenant_id=current_user[1],
            content_hash=document_hash,
            schema_type=settings.extraction_schema_type,
            schema_version=request.schema_version or settings.extraction_schema_version,
            model=request.model or settings.extraction_model,
            reason="reprocess",
        )
    if job_id is None:
        # The partial unique index admitted no second active job.
        raise HTTPException(
            status_code=409,
            detail="An extraction job is already queued or processing for this document",
        )
    return ReprocessResponse(job_id=job_id, document_id=document_id)


@dataclass
class AskContext:
    """Everything both the plain and streaming ask endpoints need after retrieval."""

    question: str  # the original user question (persisted, logged)
    standalone_question: str  # follow-up-rewritten version used for retrieval
    conversation_id: UUID | None
    retrieved: list[tuple[UUID, UUID, TextChunk]]
    labeled_chunks: list[LabeledChunk]
    document_numbers: dict[UUID, int]
    labeled_extractions: list[LabeledExtraction]
    embed_input_tokens: int


def _prepare_ask(
    request: AskRequest, current_user: tuple[UUID, UUID], settings: Settings
) -> AskContext:
    """Load history, rewrite follow-ups, embed, and retrieve — shared by /ask and /ask/stream."""
    _, tenant_id = current_user
    history: list[tuple[str, str]] = []
    if request.conversation_id is not None:
        with get_connection() as connection:
            messages = get_conversation_messages(
                connection, request.conversation_id, tenant_id
            )
        if messages is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = [
            (message.role, message.content)
            for message in messages[-settings.conversation_history_turns :]
        ]
    # Structured-filter pre-step: resolve matched documents BEFORE embedding so
    # an empty match short-circuits to a grounded refusal without a paid call
    # — and never silently reverts to an unfiltered tenant search.
    structured_document_ids: list[UUID] | None = None
    if request.structured_filters is not None:
        with get_connection() as connection:
            structured_document_ids = find_document_ids_by_fields(
                connection,
                tenant_id,
                schema_type=request.structured_filters.schema_type,
                field_filters=request.structured_filters.field_filters(),
            )
        if not structured_document_ids:
            return AskContext(
                question=request.question,
                standalone_question=request.question,
                conversation_id=request.conversation_id,
                retrieved=[],
                labeled_chunks=[],
                document_numbers={},
                labeled_extractions=[],
                embed_input_tokens=0,
            )
    standalone_question = request.question
    # Local mode (LOCAL_GENERATION=true) skips the rewrite call; retrieval just
    # uses the raw question.
    if history and not settings.local_generation:
        try:
            standalone_question = gemini_breaker().call(
                lambda: rewrite_followup_question(
                    GeminiTextGenerator(_gemini_client(), settings.gemini_model),
                    request.question,
                    history,
                )
            )
        except CircuitOpenError as exc:
            _raise_provider_unavailable(exc)
    if settings.local_embeddings:
        # Same deterministic vectors the worker embeds with in local mode.
        query_embedding = embed_texts_local([standalone_question])[0]
        embed_input_tokens = 0
    else:
        try:
            embed_result = gemini_breaker().call(
                lambda: embed_texts(
                    _gemini_client(),
                    [standalone_question],
                    model=settings.gemini_embedding_model,
                )
            )
            query_embedding = embed_result.vectors[0]
            embed_input_tokens = embed_result.input_tokens
        except CircuitOpenError as exc:
            _raise_provider_unavailable(exc)
    with get_connection() as connection:
        retrieved = retrieve_chunks(
            connection,
            query_embedding,
            tenant_id=tenant_id,
            question=standalone_question,
            limit=5,
            document_id=request.document_id,
            document_ids=structured_document_ids,
            doc_type=request.doc_type,
            hybrid=settings.hybrid_search_enabled,
            hybrid_lexical_weight=settings.hybrid_lexical_weight,
        )
    document_numbers: dict[UUID, int] = {}
    labeled_chunks: list[LabeledChunk] = []
    for _, document_id, chunk in retrieved:
        if document_id not in document_numbers:
            document_numbers[document_id] = len(document_numbers) + 1
        labeled_chunks.append(
            LabeledChunk(label=f"doc {document_numbers[document_id]}", chunk=chunk)
        )
    # Extracted fields join the prompt as additional labeled blocks for the
    # same document numbering, so [doc N, extracted fields] citations resolve
    # against the retrieved set exactly like page citations.
    labeled_extractions: list[LabeledExtraction] = []
    if request.structured_filters is not None and document_numbers:
        with get_connection() as connection:
            for document_id, number in document_numbers.items():
                row = get_document_extraction(connection, document_id, tenant_id)
                if row is not None:
                    labeled_extractions.append(
                        LabeledExtraction(label=f"doc {number}", fields=row.fields)
                    )
    return AskContext(
        question=request.question,
        standalone_question=standalone_question,
        conversation_id=request.conversation_id,
        retrieved=retrieved,
        labeled_chunks=labeled_chunks,
        document_numbers=document_numbers,
        labeled_extractions=labeled_extractions,
        embed_input_tokens=embed_input_tokens,
    )


def _citations_for(
    parsed: list[Citation], document_numbers: dict[UUID, int]
) -> list[CitationResponse]:
    documents_by_number = {number: document_id for document_id, number in document_numbers.items()}
    return [
        CitationResponse(
            document_id=documents_by_number[citation.document_index], page=citation.page
        )
        for citation in parsed
        if citation.document_index in documents_by_number
    ]


def _record_ask(
    http_request: Request,
    current_user: tuple[UUID, UUID],
    context: AskContext,
    *,
    input_tokens: int,
    output_tokens: int,
    answer: str,
    citations: list[CitationResponse],
    settings: Settings,
    started: float,
) -> None:
    cost = (
        input_tokens * settings.gemini_input_token_cost
        + output_tokens * settings.gemini_output_token_cost
    ) / 1_000_000
    try:
        with get_connection() as connection:
            record_request_log(
                connection,
                request_id=request_id(http_request),
                tenant_id=current_user[1],
                query=context.question,
                retrieved_chunk_ids=[chunk_id for chunk_id, _, _ in context.retrieved],
                latency_ms=(perf_counter() - started) * 1000,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
            )
            if context.conversation_id is not None:
                append_exchange(
                    connection,
                    context.conversation_id,
                    current_user[1],
                    question=context.question,
                    answer=answer,
                    citations=[citation.model_dump(mode="json") for citation in citations],
                )
    except Exception:
        # Telemetry and history persistence must never fail the answer itself.
        logger.error("Failed to record ask telemetry/history", exc_info=True)


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _ask_done_event(
    answer_text: str, citations: list[CitationResponse], conversation_id: UUID | None
) -> str:
    return _sse_event(
        "done",
        {
            "answer": answer_text,
            "citations": [citation.model_dump(mode="json") for citation in citations],
            "conversation_id": str(conversation_id) if conversation_id is not None else None,
        },
    )


def _ask_sse_response(generator: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _local_ask_stream(
    http_request: Request,
    current_user: tuple[UUID, UUID],
    context: AskContext,
    settings: Settings,
    started: float,
) -> Iterator[str]:
    """SSE stream for LOCAL_GENERATION mode: deterministic tokens, no Gemini call."""
    answer_text = local_answer(
        context.standalone_question, context.labeled_chunks, context.labeled_extractions
    )
    for word in answer_text.split(" "):
        yield _sse_event("token", {"text": f"{word} "})
    citations = _citations_for(parse_citations(answer_text), context.document_numbers)
    _record_ask(
        http_request,
        current_user,
        context,
        input_tokens=context.embed_input_tokens,
        output_tokens=0,
        answer=answer_text,
        citations=citations,
        settings=settings,
        started=started,
    )
    yield _ask_done_event(answer_text, citations, context.conversation_id)


@router.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    http_request: Request,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> AskResponse:
    started = perf_counter()
    settings = get_settings()
    limiter.check(current_user[1], "ask", settings.ask_rate_limit_per_minute)
    context = _prepare_ask(request, current_user, settings)
    if settings.local_generation:
        answer_text = local_answer(
            context.standalone_question, context.labeled_chunks, context.labeled_extractions
        )
        parsed = parse_citations(answer_text)
        input_tokens = context.embed_input_tokens
        output_tokens = 0
    else:
        try:
            answer = gemini_breaker().call(
                lambda: generate_answer(
                    GeminiTextGenerator(_gemini_client(), settings.gemini_model),
                    context.standalone_question,
                    context.labeled_chunks,
                    context.labeled_extractions,
                )
            )
        except CircuitOpenError as exc:
            _raise_provider_unavailable(exc)
        answer_text = answer.answer
        parsed = answer.citations
        input_tokens = answer.input_tokens + context.embed_input_tokens
        output_tokens = answer.output_tokens
    citations = _citations_for(parsed, context.document_numbers)
    _record_ask(
        http_request,
        current_user,
        context,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        answer=answer_text,
        citations=citations,
        settings=settings,
        started=started,
    )
    return AskResponse(
        answer=answer_text, citations=citations, conversation_id=context.conversation_id
    )


@router.post("/ask/stream")
def ask_stream(
    request: AskRequest,
    http_request: Request,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> StreamingResponse:
    """Server-sent-events variant of /ask: ``token`` deltas, then a final ``done``.

    Events: ``token`` (``{"text": ...}``), ``done`` (``{"answer", "citations",
    "conversation_id"}``), ``error`` (``{"detail"}`` — emitted only after the
    stream has started, so failures the client can retry arrive as a normal
    HTTP error status instead).
    """
    started = perf_counter()
    settings = get_settings()
    limiter.check(current_user[1], "ask", settings.ask_rate_limit_per_minute)
    # Everything before the first token (auth, history, rewrite, retrieval) can
    # still surface as a regular HTTP error.
    context = _prepare_ask(request, current_user, settings)
    if settings.local_generation:
        return _ask_sse_response(
            _local_ask_stream(http_request, current_user, context, settings, started)
        )
    prompt = build_prompt(
        context.standalone_question, context.labeled_chunks, context.labeled_extractions
    )
    breaker = gemini_breaker()
    breaker.ensure_available()

    def stream() -> Iterator[str]:
        generation = GeminiTextStream(_gemini_client(), settings.gemini_model, prompt)
        parts: list[str] = []
        try:
            for delta in generation:
                parts.append(delta)
                yield _sse_event("token", {"text": delta})
            if not parts:
                raise RuntimeError("Gemini returned an empty response")
            breaker.record_success()
        except Exception:
            breaker.record_failure()
            logger.error("Streaming answer generation failed", exc_info=True)
            yield _sse_event("error", {"detail": "Answer generation failed"})
            return
        full_answer = "".join(parts).strip()
        citations = _citations_for(parse_citations(full_answer), context.document_numbers)
        _record_ask(
            http_request,
            current_user,
            context,
            input_tokens=generation.input_tokens + context.embed_input_tokens,
            output_tokens=generation.output_tokens,
            answer=full_answer,
            citations=citations,
            settings=settings,
            started=started,
        )
        yield _ask_done_event(full_answer, citations, context.conversation_id)

    return _ask_sse_response(stream())


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_new_conversation(
    request: ConversationCreateRequest,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ConversationSummary:
    with get_connection() as connection:
        row = create_conversation(connection, current_user[1], request.title)
    return _conversation_summary(row)


def _conversation_summary(row: ConversationRow) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row.conversation_id,
        title=row.title,
        updated_at=row.updated_at,
        message_count=row.message_count,
    )


@router.get("/conversations", response_model=ConversationListResponse)
def conversations(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ConversationListResponse:
    with get_connection() as connection:
        rows = list_conversations(connection, current_user[1], limit=limit, offset=offset)
    return ConversationListResponse(
        conversations=[_conversation_summary(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def conversation_detail(
    conversation_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> ConversationDetailResponse:
    with get_connection() as connection:
        row = get_conversation(connection, conversation_id, current_user[1])
        if row is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = get_conversation_messages(connection, conversation_id, current_user[1])
    assert messages is not None  # the conversation exists and is tenant-owned
    return ConversationDetailResponse(
        conversation_id=row.conversation_id,
        title=row.title,
        updated_at=row.updated_at,
        messages=[
            MessageResponse(
                role=message.role,
                content=message.content,
                citations=[
                    CitationResponse(
                        document_id=UUID(str(citation["document_id"])), page=int(citation["page"])
                    )
                    for citation in message.citations
                ],
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_conversation(
    conversation_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> None:
    with get_connection() as connection:
        found = delete_conversation(connection, conversation_id, current_user[1])
    if not found:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/admin/usage", response_model=UsageResponse)
def usage(
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> UsageResponse:
    """Tenant usage dashboard: totals plus a per-day series (F5)."""
    with get_connection() as connection:
        documents_count, queries, cost = tenant_usage(connection, current_user[1])
        daily = tenant_usage_daily(connection, current_user[1], days=days)
    return UsageResponse(
        tenant_id=current_user[1],
        documents=documents_count,
        queries=queries,
        cost_estimate=cost,
        input_tokens=sum(day.input_tokens for day in daily),
        output_tokens=sum(day.output_tokens for day in daily),
        daily=[
            UsageDayItem(
                day=day.day,
                queries=day.queries,
                input_tokens=day.input_tokens,
                output_tokens=day.output_tokens,
                cost_estimate=day.cost_estimate,
            )
            for day in daily
        ],
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(
    document_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> None:
    settings = get_settings()
    with get_connection() as connection:
        found, storage_uri = delete_document(connection, document_id, current_user[1])
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    if storage_uri:
        CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id).delete(storage_uri)


@router.delete("/auth/account", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(current_user: tuple[UUID, UUID] = Depends(get_current_user)) -> None:
    settings = get_settings()
    with get_connection() as connection:
        storage_uris = delete_tenant(connection, current_user[1])
    if storage_uris:
        storage = CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id)
        for storage_uri in storage_uris:
            storage.delete(storage_uri)
