import json
from io import BytesIO
from time import perf_counter
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from google import genai
from pydantic import BaseModel, Field

from knowledgeforge.config import get_settings
from knowledgeforge.generation.gemini import GeminiTextGenerator
from knowledgeforge.generation.generate import generate_answer
from knowledgeforge.ingestion.chunk import chunk_pages
from knowledgeforge.ingestion.dedup import content_hash
from knowledgeforge.ingestion.embed import embed_texts
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_docx import extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.store import (
    count_documents,
    create_pending_document,
    find_document_by_hash,
    find_latest_document_by_filename,
    get_document_status,
    list_failed_ingestions,
    mark_superseded,
    record_failed_ingestion,
    record_request_log,
    store_document,
    tenant_usage,
)
from knowledgeforge.limits import RedisTokenBucketLimiter, TokenBucketLimiter
from knowledgeforge.limits import limiter as default_limiter
from knowledgeforge.observability import request_id
from knowledgeforge.retrieval.retrieve import retrieve_chunks
from knowledgeforge.security.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from knowledgeforge.worker.cloud import CloudStorageClient, PubSubPublisher

router = APIRouter()
limiter: TokenBucketLimiter | RedisTokenBucketLimiter = default_limiter


def _client_subject(request: Request, purpose: str) -> UUID:
    """Create a non-account auth limiter key from the caller address."""
    address = request.client.host if request.client is not None else "unknown"
    return uuid5(NAMESPACE_URL, f"knowledgeforge:{purpose}:{address}")


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    status: str = "ready"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)


class CitationResponse(BaseModel):
    document_id: UUID
    page: int


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]


class FailedIngestionResponse(BaseModel):
    id: UUID
    filename: str
    error_message: str


class BatchUploadResponse(BaseModel):
    filename: str
    status: str
    document_id: UUID | None = None
    error: str | None = None


class DocumentStatusResponse(BaseModel):
    document_id: UUID
    status: str


class UsageResponse(BaseModel):
    tenant_id: UUID
    documents: int
    queries: int
    cost_estimate: float


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    tenant_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, http_request: Request) -> TokenResponse:
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "register"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    try:
        with psycopg.connect(settings.database_url) as connection:
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
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Unable to register account") from exc
    return TokenResponse(access_token=create_access_token(user_id, tenant_id))


@router.post("/auth/login", response_model=TokenResponse)
def login(request: LoginRequest, http_request: Request) -> TokenResponse:
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "login"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, tenant_id, hashed_password FROM users WHERE email = %s",
                (request.email.lower(),),
            )
            row = cursor.fetchone()
    if row is None or not verify_password(request.password, str(row[2])):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(UUID(str(row[0])), UUID(str(row[1]))))


def _gemini_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
        raise HTTPException(status_code=503, detail="Gemini API key is not configured")
    return genai.Client(api_key=settings.gemini_api_key)


@router.post(
    "/documents", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
def upload_document(
    file: Annotated[UploadFile, File(...)],
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentUploadResponse:
    settings = get_settings()
    content = file.file.read()
    document_hash = content_hash(content)
    _, tenant_id = current_user
    limiter.check(tenant_id, "documents", settings.document_rate_limit_per_minute)
    with psycopg.connect(settings.database_url) as connection:
        if count_documents(connection, tenant_id) >= settings.max_documents_per_tenant:
            raise HTTPException(status_code=402, detail="Document quota exceeded")
    try:
        filename = file.filename or "upload"
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        doc_type = {"pdf": "pdf", "docx": "docx", "md": "markdown", "markdown": "markdown"}.get(
            suffix
        )
        if doc_type is None:
            raise HTTPException(
                status_code=415, detail="Supported file types are PDF, DOCX, and Markdown"
            )
        if settings.async_ingestion:
            if not settings.gcp_project_id or not settings.gcs_bucket:
                raise HTTPException(status_code=503, detail="Cloud ingestion is not configured")
            with psycopg.connect(settings.database_url) as connection:
                existing = find_document_by_hash(connection, document_hash, tenant_id)
                if existing is not None:
                    return DocumentUploadResponse(document_id=existing[0], status="duplicate")
                previous = find_latest_document_by_filename(connection, filename, tenant_id)
                storage_uri = CloudStorageClient(settings.gcs_bucket).upload(
                    f"{tenant_id}/{document_hash}/{filename}", content, file.content_type
                )
                document_id = create_pending_document(
                    connection,
                    title=filename,
                    source_filename=filename,
                    doc_type=doc_type,
                    content_hash=document_hash,
                    storage_uri=storage_uri,
                    tenant_id=tenant_id,
                    version=previous[1] + 1 if previous is not None else 1,
                )
                if previous is not None:
                    mark_superseded(connection, previous[0], document_id)
            PubSubPublisher(settings.gcp_project_id, settings.pubsub_topic).publish(
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
        if file.content_type == "application/pdf" or suffix == "pdf":
            pages = extract_pdf(BytesIO(content))
        elif suffix == "docx":
            pages = extract_docx(BytesIO(content))
        elif suffix in {"md", "markdown"} or file.content_type in {"text/markdown", "text/plain"}:
            pages = extract_markdown(BytesIO(content))
        chunks = chunk_pages(pages, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
        if not chunks:
            raise ValueError("PDF contains no extractable text")
        client = _gemini_client()
        embeddings = embed_texts(
            client,
            [chunk.text for chunk in chunks],
            model=settings.gemini_embedding_model,
        )
        with psycopg.connect(settings.database_url) as connection:
            existing = find_document_by_hash(connection, document_hash, tenant_id)
            if existing is not None:
                return DocumentUploadResponse(document_id=existing[0], status="duplicate")
            previous = find_latest_document_by_filename(connection, filename, tenant_id)
            document_id = store_document(
                connection,
                title=filename,
                source_filename=filename,
                doc_type=doc_type,
                chunks=chunks,
                embeddings=embeddings,
                content_hash=document_hash,
                version=previous[1] + 1 if previous is not None else 1,
                tenant_id=tenant_id,
            )
            if previous is not None:
                mark_superseded(connection, previous[0], document_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to ingest PDF: {exc}") from exc
    return DocumentUploadResponse(document_id=document_id)


@router.get("/ingestions/failed", response_model=list[FailedIngestionResponse])
def failed_ingestions(
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> list[FailedIngestionResponse]:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as connection:
        rows = list_failed_ingestions(connection)
    return [
        FailedIngestionResponse(id=row[0], filename=row[1], error_message=row[2]) for row in rows
    ]


@router.post("/documents/batch", response_model=list[BatchUploadResponse])
def upload_documents_batch(
    files: Annotated[list[UploadFile], File(...)],
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> list[BatchUploadResponse]:
    """Process each file independently so one corrupt document cannot abort the batch."""
    results: list[BatchUploadResponse] = []
    settings = get_settings()
    for file in files:
        filename = file.filename or "upload"
        try:
            result = upload_document(file, current_user)
            results.append(
                BatchUploadResponse(
                    filename=filename,
                    status=result.status,
                    document_id=result.document_id,
                )
            )
        except HTTPException as exc:
            try:
                with psycopg.connect(settings.database_url) as connection:
                    record_failed_ingestion(connection, filename, str(exc.detail))
            except Exception:
                pass
            results.append(
                BatchUploadResponse(filename=filename, status="failed", error=str(exc.detail))
            )
    return results


@router.get("/documents/{document_id}", response_model=DocumentStatusResponse)
def document_status(
    document_id: UUID,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> DocumentStatusResponse:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as connection:
        current_status = get_document_status(connection, document_id, current_user[1])
    if current_status is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentStatusResponse(document_id=document_id, status=current_status)


@router.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    http_request: Request,
    current_user: tuple[UUID, UUID] = Depends(get_current_user),
) -> AskResponse:
    started = perf_counter()
    limiter.check(current_user[1], "ask", get_settings().ask_rate_limit_per_minute)
    settings = get_settings()
    client = _gemini_client()
    query_embedding = embed_texts(
        client,
        [request.question],
        model=settings.gemini_embedding_model,
    )[0]
    with psycopg.connect(settings.database_url) as connection:
        retrieved = retrieve_chunks(connection, query_embedding, limit=5, tenant_id=current_user[1])
    answer = generate_answer(
        GeminiTextGenerator(client, settings.gemini_model),
        request.question,
        [chunk for _, chunk in retrieved],
    )
    citations = [
        CitationResponse(document_id=document_id, page=citation.page)
        for citation in answer.citations
        for document_id, chunk in retrieved
        if chunk.page == citation.page
    ]
    with psycopg.connect(settings.database_url) as connection:
        record_request_log(
            connection,
            request_id=request_id(http_request),
            tenant_id=current_user[1],
            query=request.question,
            retrieved_chunk_ids=[chunk_id for chunk_id, _ in retrieved],
            latency_ms=(perf_counter() - started) * 1000,
        )
    return AskResponse(answer=answer.answer, citations=citations)


@router.get("/admin/usage", response_model=UsageResponse)
def usage(current_user: tuple[UUID, UUID] = Depends(get_current_user)) -> UsageResponse:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as connection:
        documents, queries, cost = tenant_usage(connection, current_user[1])
    return UsageResponse(
        tenant_id=current_user[1], documents=documents, queries=queries, cost_estimate=cost
    )
