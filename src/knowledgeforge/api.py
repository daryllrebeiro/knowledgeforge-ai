import html
import json
import logging
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from time import perf_counter
from typing import Annotated, Any, BinaryIO, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from knowledgeforge.admin_ui import ADMIN_HTML
from knowledgeforge.billing import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalRequest,
    CustomerPortalResponse,
    SubscriptionResponse,
    TenantBillingAdminView,
    UpdateTenantTierRequest,
    create_checkout_session,
    create_portal_session,
    get_tenant_billing_info,
    list_tenants_billing_admin,
    process_stripe_event,
    update_tenant_tier_admin,
    verify_stripe_signature,
)
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
from knowledgeforge.extraction.diff_engine import diff_documents_from_db
from knowledgeforge.extraction.dynamic_schemas import (
    create_tenant_schema,
    get_tenant_schema,
    infer_schema_from_sample,
    list_tenant_schemas,
)
from knowledgeforge.extraction.query_parser import (
    FilterConfidence,
    NaturalFilterResult,
    parse_natural_filter,
)
from knowledgeforge.extraction.store import (
    DocumentExtractionRow,
    correct_document_extraction,
    find_document_ids_by_fields,
    find_document_ids_with_ranges,
    get_document_extraction,
    get_extraction_job,
    insert_extraction_job,
    list_all_extractions_review_admin,
    list_extractions,
    list_extractions_with_ranges,
)
from knowledgeforge.generation.condense import rewrite_followup_question
from knowledgeforge.generation.gemini import GeminiTextGenerator, GeminiTextStream
from knowledgeforge.generation.generate import (
    Citation,
    GeneratedAnswer,
    generate_answer,
    parse_citations,
)
from knowledgeforge.generation.local import local_answer
from knowledgeforge.generation.prompt import LabeledChunk, LabeledExtraction, build_prompt
from knowledgeforge.generation.research_planner import (
    DeepResearchPlanner,
    get_research_job,
    list_research_jobs,
)
from knowledgeforge.generation.verifier import EntailmentVerifier
from knowledgeforge.ingestion.chunk import TextChunk, chunk_pages
from knowledgeforge.ingestion.dedup import content_hash, decide_dedup
from knowledgeforge.ingestion.embed import embed_texts, embed_texts_local
from knowledgeforge.ingestion.embed_cache import embed_texts_cached
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_csv import CSVExtractionError, extract_csv
from knowledgeforge.ingestion.extract_docx import DOCXExtractionError, extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.extract_pptx import PPTXExtractionError, extract_pptx
from knowledgeforge.ingestion.extract_text import extract_html, extract_text
from knowledgeforge.ingestion.store import (
    count_documents,
    create_pending_document,
    delete_document,
    delete_tenant,
    export_tenant_data,
    find_document_by_hash,
    find_latest_document_by_filename,
    get_document_content_and_chunks,
    get_document_detail,
    get_document_ingest_info,
    list_all_failed_ingestions,
    list_all_tenants,
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
from knowledgeforge.reliability import (
    CircuitBreaker,
    CircuitOpenError,
    build_circuit_breaker,
    make_redis_key,
)
from knowledgeforge.retrieval.graph_traversal import traverse_entity_neighborhood
from knowledgeforge.retrieval.retrieve import retrieve_chunks
from knowledgeforge.retrieval.table_qa import (
    QueryIntent,
    TableQASynthesizer,
    classify_query_intent,
)
from knowledgeforge.security.api_keys import create_api_key, list_api_keys, revoke_api_key
from knowledgeforge.security.auth import (
    accept_invitation,
    clear_auth_cookies,
    consume_email_verification_token,
    create_access_token,
    create_email_verification_token,
    create_invitation,
    ensure_owner_remaining,
    get_current_user,
    get_user_platform_admin,
    get_user_role_for_tenant,
    hash_password,
    require_owner,
    require_platform_admin,
    require_scope,
    set_auth_cookies,
    verify_password,
)
from knowledgeforge.security.budget import (
    estimate_research_token_cost,
    estimate_token_cost,
    get_extraction_budget,
    get_platform_token_budget,
    get_tenant_budget_limits,
    get_token_budget,
)
from knowledgeforge.security.audit import record_audit_log
from knowledgeforge.security.mailer import send_verification_email
from knowledgeforge.security.privacy_vault import PrivacyVault
from knowledgeforge.security.refresh import (
    InvalidRefreshToken,
    create_refresh_token,
    revoke_refresh_family,
    rotate_refresh_token,
)
from knowledgeforge.security.sso import (
    build_authorization_url,
    create_sso_state,
    get_sso_config,
    process_sso_claims,
    save_sso_config,
    validate_enterprise_tier,
    verify_sso_state,
)
from knowledgeforge.security.ssrf import SSRFValidationError, validate_webhook_url
from knowledgeforge.security.webhooks import (
    delete_webhook,
    generate_webhook_secret,
    list_webhooks,
    register_webhook,
)
from knowledgeforge.worker.auditor import list_conflicts, resolve_conflict
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
    document_ids: list[UUID] | None = None
    # Restrict retrieval to one document type ("pdf", "docx", "markdown", "text", "html").
    doc_type: str | None = Field(default=None, max_length=20)
    # Optional metadata filters for retrieval scope (F2).
    filename: str | None = Field(default=None, max_length=500)
    created_after: datetime | None = None
    created_before: datetime | None = None
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


class CitationHighlight(BaseModel):
    page: int
    box: list[float]  # [x0, y0, x1, y1] normalized coordinates
    text_snippet: str | None = None


class CitationResponse(BaseModel):
    document_id: UUID
    page: int | None = None
    highlights: list[CitationHighlight] = Field(default_factory=list)


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    conversation_id: UUID | None = None
    grounding_score: float = 1.0
    is_grounded: bool = True
    sql_executed: str | None = None
    table_results: dict[str, Any] | None = None


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


class InvitationCreateRequest(BaseModel):
    role: str = Field(default="member", pattern="^(owner|member)$")
    email: str | None = Field(default=None, max_length=320)


class InvitationCreateResponse(BaseModel):
    invitation_id: UUID
    token: str
    expires_at: str
    role: str


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=1)


class InvitationAcceptResponse(BaseModel):
    invitation_id: UUID
    tenant_id: UUID
    role: str


class AdminTenantResponse(BaseModel):
    tenant_id: UUID
    name: str
    created_at: str
    document_count: int
    query_count: int
    cost_estimate: float


class AdminTenantListResponse(BaseModel):
    tenants: list[AdminTenantResponse]
    limit: int
    offset: int


class AdminFailedIngestionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    filename: str
    error_message: str


class AdminFailedIngestionListResponse(BaseModel):
    failed_ingestions: list[AdminFailedIngestionResponse]
    limit: int
    offset: int


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


class DynamicSchemaCreateRequest(BaseModel):
    schema_name: str = Field(min_length=2, max_length=100)
    json_schema: dict[str, Any]
    description: str = Field(default="", max_length=500)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)


class DynamicSchemaResponse(BaseModel):
    id: UUID
    schema_name: str
    schema_version: int
    description: str
    json_schema: dict[str, Any]
    field_descriptions: dict[str, str]
    created_at: str


class InferSchemaRequest(BaseModel):
    sample_text: str = Field(min_length=10)
    schema_name: str = Field(default="inferred_schema")


class InferSchemaResponse(BaseModel):
    schema_name: str
    json_schema: dict[str, Any]


class GraphQueryRequest(BaseModel):
    seed_entities: list[str] = Field(min_length=1, max_length=20)
    max_depth: int = Field(default=2, ge=1, le=4)
    limit: int = Field(default=25, ge=1, le=100)


class GraphPathResponse(BaseModel):
    source_name: str
    relation_type: str
    target_name: str
    depth: int


class GraphQueryResponse(BaseModel):
    paths: list[GraphPathResponse]


class DocumentDiffRequest(BaseModel):
    source_doc_id: UUID
    target_doc_id: UUID


class ClauseDiffResponse(BaseModel):
    section_name: str
    change_type: str
    risk_severity: str
    source_clause: str | None
    target_clause: str | None
    similarity: float
    delta_summary: str


class DocumentDiffResponse(BaseModel):
    source_doc_id: UUID
    target_doc_id: UUID
    overall_risk_score: str
    added_count: int
    removed_count: int
    modified_count: int
    clauses: list[ClauseDiffResponse]
    executive_summary: str


class ConflictResponse(BaseModel):
    id: UUID
    doc_a_id: UUID
    doc_b_id: UUID
    conflict_category: str
    description: str
    severity: str
    status: str
    created_at: str


class ConflictResolveRequest(BaseModel):
    status: str = Field(default="resolved")


class ResearchJobCreateRequest(BaseModel):
    brief: str = Field(min_length=5, max_length=2000)
    max_iterations: int = Field(default=3, ge=1, le=10)


class ResearchJobResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    query: str
    status: str
    plan: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    sources: list[str] = []
    final_report: str | None = None
    completeness_score: float | None = None
    created_at: str | None = None
    completed_at: str | None = None


class ResearchJobListItem(BaseModel):
    id: UUID
    tenant_id: UUID
    query: str
    status: str
    created_at: str | None = None
    completed_at: str | None = None


class MaskTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    store_in_vault: bool = True


class MaskTextResponse(BaseModel):
    masked_text: str
    tokens_count: int
    entity_types: list[str]


class UnmaskTextRequest(BaseModel):
    masked_text: str


class UnmaskTextResponse(BaseModel):
    unmasked_text: str


class VaultEntryResponse(BaseModel):
    id: str
    surrogate_token: str
    entity_type: str
    created_at: str | None


class ExtractionListResponse(BaseModel):
    extractions: list[ExtractionResponse]
    limit: int
    offset: int


class NaturalFilterRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class NaturalFilterResponse(BaseModel):
    extractions: list[ExtractionResponse]
    limit: int
    offset: int
    clarification_needed: str | None = None
    applied_filters: dict[str, Any] = Field(default_factory=dict)


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


class DocumentPageContent(BaseModel):
    page: int
    text: str


class DocumentChunkItem(BaseModel):
    chunk_id: UUID
    page: int
    section: str | None = None
    text: str
    start_char: int | None = None
    end_char: int | None = None
    bounding_boxes: list[dict[str, Any]] = Field(default_factory=list)


class DocumentContentResponse(BaseModel):
    document_id: UUID
    title: str
    doc_type: str
    storage_uri: str | None = None
    pages: list[DocumentPageContent]
    chunks: list[DocumentChunkItem]


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
    scopes: list[str] = Field(default_factory=lambda: ["*"])


class ApiKeyCreatedResponse(BaseModel):
    key_id: UUID
    name: str
    # The plaintext key is shown exactly once; only its hash is stored.
    key: str
    key_prefix: str
    scopes: list[str] = Field(default_factory=lambda: ["*"])


class ApiKeyListedResponse(BaseModel):
    key_id: UUID
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool
    scopes: list[str] = Field(default_factory=lambda: ["*"])


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    message: str
    verified: bool


class ResendVerificationRequest(BaseModel):
    email: str


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, http_request: Request, response: Response) -> TokenResponse:
    settings = get_settings()
    # Global registration rate limit (prevents tenant farming)
    limiter.check(
        "global", "register", settings.registration_rate_limit_per_hour, window_seconds=3600
    )
    # Per-IP rate limit
    limiter.check(
        _client_subject(http_request, "register"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    verify_token = ""
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
                        "INSERT INTO users (tenant_id, email, hashed_password, is_platform_admin, email_verified) "
                        "VALUES (%s, %s, %s, FALSE, FALSE) RETURNING id",
                        (tenant_id, request.email.lower(), hash_password(request.password)),
                    )
                    user_row = cursor.fetchone()
                    if user_row is None:
                        raise RuntimeError("user insert did not return an ID")
                    user_id = UUID(str(user_row[0]))
                    # Add owner membership
                    cursor.execute(
                        "INSERT INTO tenant_memberships (tenant_id, user_id, role) "
                        "VALUES (%s, %s, 'owner')",
                        (tenant_id, user_id),
                    )
                    # Create email verification token
                    _, verify_token = create_email_verification_token(connection, user_id)
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
    access_token = create_access_token(user_id, tenant_id, "owner", False)
    set_auth_cookies(response, access_token, refresh_token, settings)
    if verify_token:
        try:
            send_verification_email(request.email.lower(), verify_token)
        except Exception:
            logger.warning(
                "Failed to dispatch verification email to %s", request.email, exc_info=True
            )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/login", response_model=TokenResponse)
def login(request: LoginRequest, http_request: Request, response: Response) -> TokenResponse:
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "login"),
        "auth",
        settings.auth_rate_limit_per_minute,
    )
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, tenant_id, hashed_password, is_platform_admin FROM users WHERE email = %s",
                (request.email.lower(),),
            )
            row = cursor.fetchone()
    if row is None or not verify_password(request.password, str(row[2])):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user_id, tenant_id = UUID(str(row[0])), UUID(str(row[1]))
    is_platform_admin = row[3] if row[3] is not None else False
    # Fetch user's role for this tenant
    role = get_user_role_for_tenant(user_id, tenant_id) or "member"
    with get_connection() as connection:
        refresh_token = create_refresh_token(connection, user_id)
    access_token = create_access_token(user_id, tenant_id, role, is_platform_admin)
    set_auth_cookies(response, access_token, refresh_token, settings)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, http_request: Request, response: Response) -> TokenResponse:
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
    # Re-fetch current role and platform_admin status at refresh time
    role = get_user_role_for_tenant(user_id, tenant_id) or "member"
    is_platform_admin = get_user_platform_admin(user_id)
    access_token = create_access_token(user_id, tenant_id, role, is_platform_admin)
    set_auth_cookies(response, access_token, new_refresh, settings)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: RefreshRequest,
    http_request: Request,
    response: Response,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> None:
    """Revoke the refresh-token family behind the presented token."""
    settings = get_settings()
    limiter.check(
        _client_subject(http_request, "logout"), "auth", settings.auth_rate_limit_per_minute
    )
    with get_connection() as connection:
        revoke_refresh_family(connection, request.refresh_token)
    clear_auth_cookies(response, settings)


@router.post("/auth/verify-email", response_model=VerifyEmailResponse)
def verify_email_endpoint(request: VerifyEmailRequest) -> VerifyEmailResponse:
    """Atomically consume an email verification token and mark user verified."""
    with get_connection() as connection:
        success, user_id, tenant_id = consume_email_verification_token(connection, request.token)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or already-used verification token",
        )

    # Invalidate tenant budget cache so verified tier limits take effect immediately
    if tenant_id:
        from knowledgeforge.security.budget import invalidate_tenant_budget_cache

        invalidate_tenant_budget_cache(tenant_id)

    return VerifyEmailResponse(message="Email successfully verified", verified=True)


@router.post("/auth/resend-verification")
def resend_verification_endpoint(
    request: ResendVerificationRequest,
    http_request: Request,
) -> dict:
    """Resend verification email to an unverified user. Rate-limited per-IP and per-email."""
    settings = get_settings()
    email = request.email.lower().strip()

    # Rate-limit per IP
    limiter.check(
        _client_subject(http_request, "resend_verification"),
        "auth",
        settings.email_verification_rate_limit_per_minute,
        window_seconds=60,
    )
    # Rate-limit per email
    limiter.check(
        f"email:{email}",
        "auth",
        settings.email_verification_rate_limit_per_minute,
        window_seconds=60,
    )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email_verified FROM users WHERE email = %s", (email,))
            row = cursor.fetchone()

        if row and not row[1]:
            user_id = UUID(str(row[0]))
            _, verify_token = create_email_verification_token(connection, user_id)
            connection.commit()
            send_verification_email(email, verify_token)

    return {
        "message": "If the account exists and is unverified, a new verification link has been sent"
    }


@router.get("/.well-known/jwks.json", tags=["auth"])
def jwks() -> dict:
    """JSON Web Key Set endpoint for RS256 public key discovery.

    Returns the public key(s) used to verify JWT signatures when using RS256.
    """
    settings = get_settings()
    if settings.jwt_algorithm.upper() != "RS256" or not settings.jwt_public_key:
        raise HTTPException(status_code=404, detail="JWKS not available (not using RS256)")
    # Extract key parameters from PEM
    import base64

    # Parse PEM to extract modulus and exponent
    pem = settings.jwt_public_key.strip()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    public_key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise HTTPException(status_code=500, detail="Unsupported key type")
    numbers = public_key.public_numbers()

    # JWK format requires base64url encoding without padding
    def b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    n = b64url_encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
    e = b64url_encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": "kf-rsa-1",
                "alg": "RS256",
                "n": n,
                "e": e,
            }
        ]
    }


@router.post(
    "/invitations",
    response_model=InvitationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_new_invitation(
    request: InvitationCreateRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> InvitationCreateResponse:
    """Create an invitation for the current user's tenant (owner only).

    Returns the plaintext token exactly once; only its hash is stored.
    """
    _, tenant_id, _ = current_user
    with get_connection() as connection:
        invitation_id, token = create_invitation(connection, tenant_id, request.role, request.email)
    # We need to fetch the expires_at for the response
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT expires_at FROM invitations WHERE id = %s",
                (invitation_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("invitation not found after creation")
    expires_at = str(row[0])
    return InvitationCreateResponse(
        invitation_id=invitation_id, token=token, expires_at=expires_at, role=request.role
    )


@router.post("/invitations/accept", response_model=InvitationAcceptResponse)
def accept_invitation_endpoint(
    request: InvitationAcceptRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> InvitationAcceptResponse:
    """Accept an invitation token and join the tenant."""
    user_id, _, _, _ = current_user
    # Fetch user's email for invitation email enforcement
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
    user_email = row[0] if row else None
    with get_connection() as connection:
        invitation_id, tenant_id, role = accept_invitation(
            connection, request.token, user_id, user_email
        )
    return InvitationAcceptResponse(invitation_id=invitation_id, tenant_id=tenant_id, role=role)


class MemberResponse(BaseModel):
    user_id: UUID
    email: str
    role: str
    created_at: str


class MemberListResponse(BaseModel):
    members: list[MemberResponse]


@router.get("/members", response_model=MemberListResponse)
def list_members(
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> MemberListResponse:
    """List all members of the current user's tenant (owner only)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, tm.role, tm.created_at
                FROM tenant_memberships tm
                JOIN users u ON u.id = tm.user_id
                WHERE tm.tenant_id = %s
                ORDER BY tm.created_at
                """,
                (tenant_id,),
            )
            rows = cursor.fetchall()
    return MemberListResponse(
        members=[
            MemberResponse(
                user_id=UUID(str(row[0])),
                email=row[1],
                role=row[2],
                created_at=str(row[3]),
            )
            for row in rows
        ]
    )


class MemberRoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(owner|member)$")


@router.patch("/members/{user_id}", response_model=MemberResponse)
def update_member_role(
    user_id: UUID,
    request: MemberRoleUpdateRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> MemberResponse:
    """Change a member's role (owner only).

    Uses advisory lock to prevent TOCTOU races on last-owner removal.
    """
    requester_id, tenant_id, _, _ = current_user
    if user_id == requester_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )
    # Ensure we don't remove the last owner (advisory lock + check)
    with get_connection() as connection:
        with connection.transaction():
            ensure_owner_remaining(
                tenant_id, exclude_user_id=user_id if request.role == "member" else None
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE tenant_memberships SET role = %s
                    WHERE tenant_id = %s AND user_id = %s
                    RETURNING user_id
                    """,
                    (request.role, tenant_id, user_id),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Member not found in this tenant")
                cursor.execute(
                    "SELECT u.email, tm.role, tm.created_at FROM users u JOIN tenant_memberships tm ON tm.user_id = u.id WHERE tm.tenant_id = %s AND tm.user_id = %s",
                    (tenant_id, user_id),
                )
                row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return MemberResponse(
        user_id=user_id,
        email=row[0],
        role=row[1],
        created_at=str(row[2]),
    )


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> None:
    """Remove a member from the tenant (owner only).

    Uses advisory lock to prevent TOCTOU races on last-owner removal.
    """
    requester_id, tenant_id, _, _ = current_user
    if user_id == requester_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove yourself from the tenant",
        )
    # Ensure we don't remove the last owner (advisory lock + check)
    with get_connection() as connection:
        with connection.transaction():
            ensure_owner_remaining(tenant_id, exclude_user_id=user_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM tenant_memberships WHERE tenant_id = %s AND user_id = %s",
                    (tenant_id, user_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Member not found in this tenant")


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_new_api_key(
    request: ApiKeyCreateRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ApiKeyCreatedResponse:
    """Create an API key. The plaintext key is returned once and never again."""
    with get_connection() as connection:
        key_id, key = create_api_key(
            connection, current_user[1], current_user[0], request.name, scopes=request.scopes
        )
    return ApiKeyCreatedResponse(
        key_id=key_id,
        name=request.name,
        key=key,
        key_prefix=key[:12],
        scopes=request.scopes or ["*"],
    )


@router.get("/api-keys", response_model=list[ApiKeyListedResponse])
def api_keys(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
            scopes=getattr(row, "scopes", ["*"]),
        )
        for row in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_api_key(
    key_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
_gemini_rewrite_breaker: CircuitBreaker | None = None
_gemini_generation_breaker: CircuitBreaker | None = None
_gemini_embedding_breaker: CircuitBreaker | None = None


def _build_redis_client() -> object | None:
    """Build Redis client for shared circuit breaker state."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis  # type: ignore[import-not-found]

        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except ImportError:
        logger.warning(
            "REDIS_URL configured but redis package unavailable; using per-process breakers"
        )
        return None


def _get_redis_client() -> object | None:
    """Cached Redis client for circuit breakers."""
    global _redis_client
    if _redis_client is None:
        _redis_client = _build_redis_client()
    return _redis_client


_redis_client: object | None = None


def gemini_breaker() -> CircuitBreaker:
    """Backward-compatible alias; returns the generation breaker."""
    return gemini_generation_breaker()


def gemini_rewrite_breaker() -> CircuitBreaker:
    """Circuit breaker for rewrite calls, with shared Redis state when configured."""
    global _gemini_rewrite_breaker
    if _gemini_rewrite_breaker is None:
        settings = get_settings()
        redis_client = _get_redis_client()
        _gemini_rewrite_breaker = build_circuit_breaker(
            redis_client,
            make_redis_key("breaker:rewrite"),
            settings.gemini_breaker_failure_threshold,
            settings.gemini_breaker_recovery_seconds,
        )
    return _gemini_rewrite_breaker


def gemini_generation_breaker() -> CircuitBreaker:
    """Circuit breaker for generation calls, with shared Redis state when configured."""
    global _gemini_generation_breaker
    if _gemini_generation_breaker is None:
        settings = get_settings()
        redis_client = _get_redis_client()
        _gemini_generation_breaker = build_circuit_breaker(
            redis_client,
            make_redis_key("breaker:generation"),
            settings.gemini_breaker_failure_threshold,
            settings.gemini_breaker_recovery_seconds,
        )
    return _gemini_generation_breaker


def gemini_embedding_breaker() -> CircuitBreaker:
    """Circuit breaker for embedding calls, with shared Redis state when configured."""
    global _gemini_embedding_breaker
    if _gemini_embedding_breaker is None:
        settings = get_settings()
        redis_client = _get_redis_client()
        _gemini_embedding_breaker = build_circuit_breaker(
            redis_client,
            make_redis_key("breaker:embedding"),
            settings.gemini_breaker_failure_threshold,
            settings.gemini_breaker_recovery_seconds,
        )
    return _gemini_embedding_breaker


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


# Magic byte signatures for file type validation (Fix R9 - defense in depth)
_MAGIC_BYTES = {
    "pdf": b"%PDF",
    "pptx": b"PK\x03\x04",  # ZIP-based (also DOCX)
    "docx": b"PK\x03\x04",  # ZIP-based
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "tif": b"II*\x00",
    "tiff": b"MM\x00*",
}


def _detect_file_type(content: bytes, filename: str) -> str | None:
    """Detect actual file type from magic bytes (first 8 bytes).

    Returns the detected type or None if unknown/mismatched.
    This is a defense-in-depth measure; the primary routing is still by extension.
    """
    if len(content) < 8:
        return None

    header = content[:8]
    for ftype, magic in _MAGIC_BYTES.items():
        if header.startswith(magic):
            # For ZIP-based formats (PPTX, DOCX), we can't distinguish without
            # deeper inspection, so accept either if extension matches one of them
            if ftype in {"pptx", "docx"}:
                suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
                if suffix in {"pptx", "docx"}:
                    return suffix  # trust the extension for zip-based
                return None
            return ftype
    # For text-based formats (CSV, TXT, MD, HTML), check if valid UTF-8
    try:
        content[:1024].decode("utf-8")
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if suffix in {"csv", "txt", "text", "md", "markdown", "html", "htm"}:
            return suffix
    except UnicodeDecodeError:
        pass
    return None


def _ingest_upload(
    content: bytes,
    filename: str,
    content_type: str | None,
    current_user: tuple[UUID, UUID] | tuple[UUID, UUID, str, bool],
    settings: Settings,
) -> DocumentUploadResponse:
    """Shared ingestion core for single and batch uploads (rate limiting is the caller's)."""
    document_hash = content_hash(content)
    tenant_id = current_user[1]
    with get_connection() as connection:
        if count_documents(connection, tenant_id) >= settings.max_documents_per_tenant:
            raise HTTPException(status_code=402, detail="Document quota exceeded")
    try:
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        doc_type = {
            "pdf": "pdf",
            "docx": "docx",
            "pptx": "pptx",
            "csv": "csv",
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
                detail="Supported file types are PDF, DOCX, PPTX, CSV, Markdown, TXT, HTML, "
                "and images (PNG, JPEG, TIFF)",
            )
        # Defense-in-depth: verify file content matches extension via magic bytes
        detected = _detect_file_type(content, filename)
        if (
            detected is not None
            and detected != doc_type
            and not (doc_type in {"pptx", "docx"} and detected in {"pptx", "docx"})
        ):
            raise HTTPException(
                status_code=415,
                detail=f"File content does not match extension: detected {detected}, expected {doc_type}",
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
            try:
                pages = extract_docx(BytesIO(content))
            except DOCXExtractionError as exc:
                raise HTTPException(status_code=413, detail=f"DOCX guard rejection: {exc}") from exc
        elif suffix == "pptx":
            try:
                pages = extract_pptx(BytesIO(content))
            except PPTXExtractionError as exc:
                raise HTTPException(status_code=413, detail=f"PPTX guard rejection: {exc}") from exc
        elif suffix == "csv":
            try:
                pages = extract_csv(BytesIO(content))
            except CSVExtractionError as exc:
                raise HTTPException(status_code=413, detail=f"CSV guard rejection: {exc}") from exc
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
                embeddings = (
                    gemini_breaker()
                    .call(
                        lambda: embed_texts_cached(
                            connection,
                            client,
                            [chunk.text for chunk in chunks],
                            model=settings.gemini_embedding_model,
                        )
                    )
                    .vectors
                )
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("write:documents")),
) -> DocumentUploadResponse:
    settings = get_settings()
    content = _read_upload(file.file, request, max_bytes=settings.max_upload_bytes)
    tenant_id = current_user[1]
    limiter.check(tenant_id, "documents", settings.document_rate_limit_per_minute)
    return _ingest_upload(
        content, file.filename or "upload", file.content_type, current_user, settings
    )


@router.post("/documents/batch", response_model=list[BatchUploadResponse])
def upload_documents_batch(
    files: Annotated[list[UploadFile], File(...)],
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("write:documents")),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("read:documents")),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> list[FailedIngestionResponse]:
    with get_connection() as connection:
        rows = list_failed_ingestions(connection, current_user[1], limit=limit, offset=offset)
    return [
        FailedIngestionResponse(id=row[0], filename=row[1], error_message=row[2]) for row in rows
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def document_detail(
    document_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("read:documents")),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("read:documents")),
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
            ChunkPreviewItem(
                page=row.page,
                section=row.section,
                text=row.text,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{document_id}/pages/{page_number}/highlights",
    response_model=list[CitationHighlight],
)
def get_page_highlights(
    document_id: UUID,
    page_number: int,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> list[CitationHighlight]:
    """Return all bounding-box highlight geometries for a given document page."""
    with get_connection() as connection:
        info = get_document_detail(connection, document_id, current_user[1])
        if info is None:
            raise HTTPException(status_code=404, detail="Document not found")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT bounding_boxes FROM chunks
                WHERE document_id = %s AND page = %s
                """,
                (document_id, page_number),
            )
            rows = cursor.fetchall()
    highlights: list[CitationHighlight] = []
    for (boxes_raw,) in rows:
        if isinstance(boxes_raw, list):
            for b in boxes_raw:
                if isinstance(b, dict) and "box" in b:
                    highlights.append(
                        CitationHighlight(
                            page=b.get("page", page_number),
                            box=b.get("box", [0.0, 0.0, 1.0, 1.0]),
                            text_snippet=b.get("text_snippet"),
                        )
                    )
    return highlights


@router.get("/documents/{document_id}/content", response_model=DocumentContentResponse)
def document_content(
    document_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> DocumentContentResponse:
    """Retrieve structured content, page texts, chunks, offsets, and bounding boxes for document viewer."""
    with get_connection() as connection:
        data = get_document_content_and_chunks(connection, document_id, current_user[1])
    if data is None:
        raise HTTPException(status_code=404, detail="Document not found")

    pages_dict: dict[int, list[str]] = {}
    chunks_items: list[DocumentChunkItem] = []
    for c in data.chunks:
        pg = c["page"]
        if pg not in pages_dict:
            pages_dict[pg] = []
        pages_dict[pg].append(c["text"])
        chunks_items.append(
            DocumentChunkItem(
                chunk_id=c["id"],
                page=c["page"],
                section=c["section"],
                text=c["text"],
                start_char=c["start_char"],
                end_char=c["end_char"],
                bounding_boxes=c["bounding_boxes"],
            )
        )

    pages: list[DocumentPageContent] = [
        DocumentPageContent(page=pg, text="\n\n".join(texts))
        for pg, texts in sorted(pages_dict.items())
    ]
    if not pages:
        pages = [DocumentPageContent(page=1, text="")]

    return DocumentContentResponse(
        document_id=data.document_id,
        title=data.title,
        doc_type=data.doc_type,
        storage_uri=data.storage_uri,
        pages=pages,
        chunks=chunks_items,
    )


def _render_document_viewer_html(
    document_id: UUID,
    title: str,
    doc_type: str,
    pages: list[tuple[int, str]],
    chunks: list[dict[str, Any]],
    active_page: int,
    active_chunk_id: UUID | None,
    highlight_text: str | None,
    nonce: str = "",
) -> str:
    escaped_title = html.escape(title)
    escaped_doc_type = html.escape(doc_type.upper())
    nonce_attr = f' nonce="{nonce}"' if nonce else ""

    # Build chunk sidebar items
    chunk_items_html: list[str] = []
    for idx, c in enumerate(chunks, 1):
        is_active = active_chunk_id is not None and c["id"] == active_chunk_id
        active_cls = " active" if is_active else ""
        c_id = c["id"]
        c_page = c["page"]
        c_snippet = html.escape(c["text"][:90] + ("..." if len(c["text"]) > 90 else ""))
        chunk_items_html.append(
            f'<div class="chunk-card{active_cls}" data-href="?page={c_page}&chunk_id={c_id}">'
            f'<div class="chunk-card-meta"><span>Chunk {idx}</span><span>Page {c_page}</span></div>'
            f'<div class="chunk-card-text">{c_snippet}</div>'
            f'</div>'
        )

    # Build page cards
    page_cards_html: list[str] = []
    for pg, text in pages:
        escaped_text = html.escape(text)
        # Apply highlight
        has_highlight = False
        if highlight_text and highlight_text.strip():
            target = html.escape(highlight_text.strip())
            if target.lower() in escaped_text.lower():
                pattern = re.compile(re.escape(target), re.IGNORECASE)
                escaped_text = pattern.sub(
                    lambda m: f'<mark class="highlight-active" id="active-highlight">{m.group(0)}</mark>',
                    escaped_text,
                    count=1,
                )
                has_highlight = True

        if not has_highlight and active_chunk_id:
            for c in chunks:
                if c["id"] == active_chunk_id and c["page"] == pg:
                    chunk_esc = html.escape(c["text"].strip())
                    if chunk_esc in escaped_text:
                        escaped_text = escaped_text.replace(
                            chunk_esc,
                            f'<mark class="highlight-active" id="active-highlight">{chunk_esc}</mark>',
                            1,
                        )
                        has_highlight = True
                        break
                    elif len(chunk_esc) > 40:
                        sub_snippet = chunk_esc[:40]
                        if sub_snippet in escaped_text:
                            escaped_text = escaped_text.replace(
                                sub_snippet,
                                f'<mark class="highlight-active" id="active-highlight">{sub_snippet}</mark>',
                                1,
                            )
                            has_highlight = True
                            break

        # Render bounding boxes if any for active chunk on this page
        overlay_html = ""
        if active_chunk_id:
            for c in chunks:
                if c["id"] == active_chunk_id and c["page"] == pg and c.get("bounding_boxes"):
                    boxes_rendered = []
                    for b in c["bounding_boxes"]:
                        coords = b.get("box", [])
                        if len(coords) == 4:
                            x0, y0, x1, y1 = coords
                            boxes_rendered.append(
                                f'<div class="bbox-rect" style="left:{x0*100:.1f}%; top:{y0*100:.1f}%; width:{(x1-x0)*100:.1f}%; height:{(y1-y0)*100:.1f}%;"></div>'
                            )
                    if boxes_rendered:
                        overlay_html = f'<div class="bbox-overlay">{"".join(boxes_rendered)}</div>'

        page_cards_html.append(
            f'<div class="page-card" id="page-{pg}">'
            f'{overlay_html}'
            f'<div class="page-header"><span>Page {pg}</span><span>{escaped_doc_type}</span></div>'
            f'<div class="page-body">{escaped_text}</div>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Source Viewer — {escaped_title}</title>
<style>
  :root {{
    --bg-primary: #0f172a;
    --bg-surface: #1e293b;
    --bg-card: #334155;
    --border: #475569;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --accent: #38bdf8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    background: var(--bg-primary);
    color: var(--text-main);
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }}
  header {{
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    z-index: 10;
  }}
  .doc-title {{ font-size: 1.1rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .badge {{ background: #0284c7; color: #fff; font-size: 0.75rem; font-weight: 700; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; margin-left: 8px; }}
  .viewer-layout {{ flex: 1; display: flex; overflow: hidden; }}
  .sidebar {{
    width: 320px;
    background: var(--bg-surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 16px;
    gap: 12px;
  }}
  .sidebar-header {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 700; }}
  .chunk-card {{
    background: var(--bg-card);
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 10px 12px;
    cursor: pointer;
    transition: all 0.15s ease;
  }}
  .chunk-card:hover {{ border-color: var(--accent); }}
  .chunk-card.active {{ border-color: var(--accent); background: rgba(56, 189, 248, 0.15); }}
  .chunk-card-meta {{ display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--accent); margin-bottom: 4px; font-weight: 600; }}
  .chunk-card-text {{ font-size: 0.82rem; color: var(--text-muted); line-height: 1.4; }}
  .page-viewport {{
    flex: 1;
    overflow-y: auto;
    padding: 32px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 32px;
  }}
  .page-card {{
    position: relative;
    background: #ffffff;
    color: #0f172a;
    width: 100%;
    max-width: 840px;
    min-height: 480px;
    border-radius: 8px;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
    padding: 40px;
  }}
  .page-header {{
    font-size: 0.8rem;
    font-weight: 600;
    color: #64748b;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 8px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
  }}
  .page-body {{
    font-size: 0.95rem;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  mark.highlight-active {{
    background: #fef08a;
    color: #854d0e;
    padding: 2px 4px;
    border-radius: 3px;
    animation: highlight-pulse 2s infinite alternate;
  }}
  @keyframes highlight-pulse {{
    0% {{ background: #fef08a; box-shadow: 0 0 0 rgba(234, 179, 8, 0); }}
    100% {{ background: #fde047; box-shadow: 0 0 14px rgba(234, 179, 8, 0.7); }}
  }}
  .bbox-overlay {{
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    pointer-events: none;
  }}
  .bbox-rect {{
    position: absolute;
    border: 2px solid #ef4444;
    background: rgba(239, 68, 68, 0.15);
    border-radius: 2px;
  }}
</style>
</head>
<body>
  <header>
    <div class="doc-title">{escaped_title} <span class="badge">{escaped_doc_type}</span></div>
    <div><a href="/documents/{document_id}" style="color: var(--accent); font-size: 0.85rem; text-decoration: none;">View Details &rarr;</a></div>
  </header>
  <div class="viewer-layout">
    <aside class="sidebar">
      <div class="sidebar-header">Passages ({len(chunks)})</div>
      {"".join(chunk_items_html)}
    </aside>
    <main class="page-viewport">
      {"".join(page_cards_html)}
    </main>
  </div>
  <script{nonce_attr}>
    window.addEventListener('DOMContentLoaded', () => {{
      const target = document.getElementById('active-highlight') || document.getElementById('page-{active_page}');
      if (target) {{
        target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      }}
      document.querySelectorAll('.chunk-card[data-href]').forEach(card => {{
        card.addEventListener('click', () => {{
          const href = card.getAttribute('data-href');
          if (href) location.href = href;
        }});
      }});
    }});
  </script>
</body>
</html>"""


@router.get("/documents/{document_id}/view", response_class=HTMLResponse)
def document_viewer(
    document_id: UUID,
    page: Annotated[int, Query(ge=1)] = 1,
    chunk_id: UUID | None = None,
    highlight: Annotated[str | None, Query(max_length=500)] = None,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> HTMLResponse:
    """Interactive document viewer with bounding box overlays and text passage highlighting."""
    with get_connection() as connection:
        data = get_document_content_and_chunks(connection, document_id, current_user[1])
    if data is None:
        raise HTTPException(status_code=404, detail="Document not found")

    pages_dict: dict[int, list[str]] = {}
    for c in data.chunks:
        pg = c["page"]
        if pg not in pages_dict:
            pages_dict[pg] = []
        pages_dict[pg].append(c["text"])

    pages_list = [
        (pg, "\n\n".join(texts))
        for pg, texts in sorted(pages_dict.items())
    ]
    if not pages_list:
        pages_list = [(1, "")]

    nonce = secrets.token_urlsafe(16)
    html_content = _render_document_viewer_html(
        document_id=data.document_id,
        title=data.title,
        doc_type=data.doc_type,
        pages=pages_list,
        chunks=data.chunks,
        active_page=page,
        active_chunk_id=chunk_id,
        highlight_text=highlight,
        nonce=nonce,
    )
    csp = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';"
    )
    return HTMLResponse(content=html_content, headers={"Content-Security-Policy": csp})


@router.post("/documents/diff", response_model=DocumentDiffResponse)
def diff_documents(
    request: DocumentDiffRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> DocumentDiffResponse:
    """Perform semantic clause alignment, redline delta analysis, and risk scoring between two documents (Feature 7)."""
    with get_connection() as connection:
        doc_a = get_document_detail(connection, request.source_doc_id, current_user[1])
        if doc_a is None:
            raise HTTPException(status_code=404, detail="Source document not found")
        doc_b = get_document_detail(connection, request.target_doc_id, current_user[1])
        if doc_b is None:
            raise HTTPException(status_code=404, detail="Target document not found")

        diff_res = diff_documents_from_db(
            connection,
            tenant_id=current_user[1],
            source_doc_id=request.source_doc_id,
            target_doc_id=request.target_doc_id,
        )

    return DocumentDiffResponse(
        source_doc_id=diff_res.source_doc_id,
        target_doc_id=diff_res.target_doc_id,
        overall_risk_score=diff_res.overall_risk_score,
        added_count=diff_res.added_count,
        removed_count=diff_res.removed_count,
        modified_count=diff_res.modified_count,
        clauses=[
            ClauseDiffResponse(
                section_name=c.section_name,
                change_type=c.change_type.value,
                risk_severity=c.risk_severity.value,
                source_clause=c.source_clause,
                target_clause=c.target_clause,
                similarity=c.similarity,
                delta_summary=c.delta_summary,
            )
            for c in diff_res.clauses
        ],
        executive_summary=diff_res.executive_summary,
    )


@router.post(
    "/documents/{document_id}/reingest",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reingest_document(
    document_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
        raise HTTPException(status_code=409, detail="Document has no stored original to re-ingest")
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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


@router.post("/extractions/filter", response_model=NaturalFilterResponse)
def filter_extractions_natural(
    request: NaturalFilterRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> NaturalFilterResponse:
    """Filter extractions using a natural-language query translated to allowlisted field constraints."""
    parsed = parse_natural_filter(request.query)
    if parsed.confidence == FilterConfidence.AMBIGUOUS:
        return NaturalFilterResponse(
            extractions=[],
            limit=request.limit,
            offset=request.offset,
            clarification_needed=parsed.clarification_needed,
            applied_filters={},
        )
    with get_connection() as connection:
        rows = list_extractions_with_ranges(
            connection,
            current_user[1],
            schema_type=parsed.schema_type,
            field_filters=parsed.field_filters,
            numeric_ranges=parsed.numeric_ranges,
            date_ranges=parsed.date_ranges,
            limit=request.limit,
            offset=request.offset,
        )
    return NaturalFilterResponse(
        extractions=[_extraction_response(row) for row in rows],
        limit=request.limit,
        offset=request.offset,
        clarification_needed=None,
        applied_filters=parsed.applied_filters_summary,
    )


@router.get("/extractions", response_model=ExtractionListResponse)
def extractions(
    schema_type: Annotated[str | None, Query(max_length=40)] = None,
    vendor_name: Annotated[str | None, Query(max_length=300)] = None,
    invoice_number: Annotated[str | None, Query(max_length=200)] = None,
    currency: Annotated[str | None, Query(max_length=8)] = None,
    counterparty: Annotated[str | None, Query(max_length=300)] = None,
    governing_law: Annotated[str | None, Query(max_length=100)] = None,
    natural_query: Annotated[str | None, Query(max_length=500)] = None,
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ExtractionListResponse:
    """Tenant-scoped extraction list with allow-listed JSONB field filters."""
    field_filters = {
        field: value
        for field, value in {
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "currency": currency,
            "counterparty": counterparty,
            "governing_law": governing_law,
        }.items()
        if value is not None
    }
    numeric_ranges = None
    date_ranges = None
    if natural_query:
        parsed = parse_natural_filter(natural_query)
        if parsed.confidence == FilterConfidence.AMBIGUOUS:
            return ExtractionListResponse(extractions=[], limit=limit, offset=offset)
        if parsed.schema_type and not schema_type:
            schema_type = parsed.schema_type
        for k, v in parsed.field_filters.items():
            if k not in field_filters:
                field_filters[k] = v
        numeric_ranges = parsed.numeric_ranges
        date_ranges = parsed.date_ranges

    with get_connection() as connection:
        if numeric_ranges or date_ranges:
            rows = list_extractions_with_ranges(
                connection,
                current_user[1],
                schema_type=schema_type,
                field_filters=field_filters,
                numeric_ranges=numeric_ranges,
                date_ranges=date_ranges,
                needs_review=needs_review,
                limit=limit,
                offset=offset,
            )
        else:
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


class ExtractionCorrectionRequest(BaseModel):
    corrected_fields: dict[str, Any]


class ExtractionCorrectionResponse(BaseModel):
    document_id: UUID
    status: str
    message: str


class AdminDLQResponse(BaseModel):
    ingestion_dlq_depth: int
    extraction_dlq_depth: int
    recent_failed_ingestions: list[dict[str, Any]]
    recent_failed_extractions: list[dict[str, Any]]


@router.get("/admin/extractions/review-queue", response_model=ExtractionListResponse)
def extraction_review_queue(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    all_tenants: Annotated[bool, Query()] = False,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ExtractionListResponse:
    """Low-confidence extractions (``needs_review = true``), newest first."""
    is_admin = current_user[3] if len(current_user) > 3 else False
    with get_connection() as connection:
        if is_admin and all_tenants:
            raw_rows = list_all_extractions_review_admin(connection, limit=limit, offset=offset)
            return ExtractionListResponse(
                extractions=[
                    ExtractionResponse(
                        document_id=r["document_id"],
                        schema_type=r["schema_type"],
                        schema_version=r["schema_version"],
                        model=r["model"],
                        fields=r["fields"],
                        field_confidence=r["field_confidence"],
                        overall_confidence=r["overall_confidence"],
                        needs_review=r["needs_review"],
                        created_at=r["created_at"],
                    )
                    for r in raw_rows
                ],
                limit=limit,
                offset=offset,
            )
        else:
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


@router.post(
    "/documents/{document_id}/extraction/correct", response_model=ExtractionCorrectionResponse
)
def correct_extraction_endpoint(
    document_id: UUID,
    body: ExtractionCorrectionRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ExtractionCorrectionResponse:
    """Submit human correction for an extraction while preserving model extraction history."""
    user_id = current_user[0]
    tenant_id = current_user[1]
    with get_connection() as connection:
        success = correct_document_extraction(
            connection,
            document_id=document_id,
            corrected_fields=body.corrected_fields,
            reviewed_by=user_id,
            tenant_id=tenant_id,
        )
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Document extraction not found for this tenant",
        )
    return ExtractionCorrectionResponse(
        document_id=document_id,
        status="corrected",
        message="Extraction successfully corrected and review flag cleared",
    )


@router.put("/admin/extractions/{document_id}/correct", response_model=ExtractionCorrectionResponse)
def admin_correct_extraction_endpoint(
    document_id: UUID,
    body: ExtractionCorrectionRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> ExtractionCorrectionResponse:
    """Submit human correction for an extraction across any tenant (Platform Admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")

    user_id, _, _, _ = current_user
    with get_connection() as connection:
        success = correct_document_extraction(
            connection,
            document_id=document_id,
            corrected_fields=body.corrected_fields,
            reviewed_by=user_id,
            tenant_id=None,
        )
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Document extraction not found",
        )
    return ExtractionCorrectionResponse(
        document_id=document_id,
        status="corrected",
        message="Extraction successfully corrected by platform admin",
    )


@router.post(
    "/admin/schemas",
    response_model=DynamicSchemaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("admin:schemas"))],
)
def create_schema(
    request: DynamicSchemaCreateRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> DynamicSchemaResponse:
    """Create or update a dynamic structured extraction schema for the tenant (Feature 3)."""
    with get_connection() as connection:
        row = create_tenant_schema(
            connection,
            tenant_id=current_user[1],
            schema_name=request.schema_name,
            json_schema=request.json_schema,
            description=request.description,
            field_descriptions=request.field_descriptions,
            version=request.version,
        )
    return DynamicSchemaResponse(
        id=row.id,
        schema_name=row.schema_name,
        schema_version=row.schema_version,
        description=row.description,
        json_schema=row.json_schema,
        field_descriptions=row.field_descriptions,
        created_at=row.created_at.isoformat(),
    )


@router.get(
    "/admin/schemas",
    response_model=list[DynamicSchemaResponse],
    dependencies=[Depends(require_scope("admin:schemas"))],
)
def list_schemas(
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> list[DynamicSchemaResponse]:
    """List all active extraction schemas configured for the tenant (Feature 3)."""
    with get_connection() as connection:
        rows = list_tenant_schemas(connection, current_user[1])
    return [
        DynamicSchemaResponse(
            id=row.id,
            schema_name=row.schema_name,
            schema_version=row.schema_version,
            description=row.description,
            json_schema=row.json_schema,
            field_descriptions=row.field_descriptions,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.get(
    "/admin/schemas/{schema_name}",
    response_model=DynamicSchemaResponse,
    dependencies=[Depends(require_scope("admin:schemas"))],
)
def get_schema(
    schema_name: str,
    version: int | None = None,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> DynamicSchemaResponse:
    """Get detail for a specific extraction schema (Feature 3)."""
    with get_connection() as connection:
        row = get_tenant_schema(connection, current_user[1], schema_name, version=version)
    if row is None:
        raise HTTPException(status_code=404, detail="Schema not found")
    return DynamicSchemaResponse(
        id=row.id,
        schema_name=row.schema_name,
        schema_version=row.schema_version,
        description=row.description,
        json_schema=row.json_schema,
        field_descriptions=row.field_descriptions,
        created_at=row.created_at.isoformat(),
    )


@router.post(
    "/admin/schemas/infer",
    response_model=InferSchemaResponse,
    dependencies=[Depends(require_scope("admin:schemas"))],
)
def infer_schema(
    request: InferSchemaRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> InferSchemaResponse:
    """Infer a candidate extraction schema from sample document text (Feature 3)."""
    settings = get_settings()
    limiter.check(current_user[1], "schemas_infer", settings.schema_rate_limit_per_minute)
    schema = infer_schema_from_sample(request.sample_text, default_name=request.schema_name)
    return InferSchemaResponse(schema_name=request.schema_name, json_schema=schema)


@router.post("/graph/query", response_model=GraphQueryResponse)
def query_graph(
    request: GraphQueryRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> GraphQueryResponse:
    """Traverse multi-hop relationships starting from seed entities (Feature 6: GraphRAG)."""
    with get_connection() as connection:
        paths = traverse_entity_neighborhood(
            connection,
            tenant_id=current_user[1],
            seed_entity_names=request.seed_entities,
            max_depth=request.max_depth,
            limit=request.limit,
        )
    return GraphQueryResponse(
        paths=[
            GraphPathResponse(
                source_name=p.source_name,
                relation_type=p.relation_type,
                target_name=p.target_name,
                depth=p.depth,
            )
            for p in paths
        ]
    )


@router.get("/admin/conflicts", response_model=list[ConflictResponse])
def get_conflicts(
    status_filter: str = "unresolved",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> list[ConflictResponse]:
    """List detected cross-document knowledge conflicts and policy contradictions (Feature 8)."""
    with get_connection() as connection:
        rows = list_conflicts(connection, current_user[1], status=status_filter, limit=limit)
    return [
        ConflictResponse(
            id=r.id,
            doc_a_id=r.doc_a_id,
            doc_b_id=r.doc_b_id,
            conflict_category=r.conflict_category,
            description=r.description,
            severity=r.severity,
            status=r.status,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/admin/conflicts/{conflict_id}/resolve")
def resolve_conflict_endpoint(
    conflict_id: UUID,
    request: ConflictResolveRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> dict[str, str]:
    """Resolve or dismiss a detected knowledge conflict (Feature 8)."""
    with get_connection() as connection:
        success = resolve_conflict(
            connection,
            conflict_id=conflict_id,
            tenant_id=current_user[1],
            user_id=current_user[0],
            status=request.status,
        )
    if not success:
        raise HTTPException(status_code=404, detail="Conflict not found")
    return {"status": request.status, "message": "Conflict status updated successfully"}


@router.post("/research/jobs", response_model=ResearchJobResponse)
def create_research_job_endpoint(
    request: ResearchJobCreateRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ResearchJobResponse:
    """Execute autonomous agentic multi-round research across corpus (Feature 9)."""
    settings = get_settings()
    limiter.check(current_user[1], "research", settings.research_rate_limit_per_minute)

    # Token budget reservation scaling with max_iterations
    token_budget = get_token_budget()
    platform_budget = get_platform_token_budget()
    reserved = False
    platform_reserved = False
    estimated = estimate_research_token_cost(request.brief, request.max_iterations)

    if token_budget is not None:
        token_limit, _, tier, _ = get_tenant_budget_limits(current_user[1])
        allowed, current_usage, _ = token_budget.check_and_reserve(
            str(current_user[1]), estimated, limit=token_limit
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily token budget exceeded: {current_usage}/{token_limit}",
            )
        reserved = True

    if platform_budget is not None:
        allowed, current_usage, _ = platform_budget.check_and_reserve("platform", estimated)
        if not allowed:
            if token_budget is not None and reserved:
                token_budget.release_reservation(str(current_user[1]), estimated)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Platform daily token budget exceeded: {current_usage}/{settings.platform_daily_token_budget}",
            )
        platform_reserved = True

    try:
        planner = DeepResearchPlanner()
        with get_connection() as connection:
            dossier = planner.run_research(
                tenant_id=current_user[1],
                brief=request.brief,
                conn=connection,
                max_iterations=request.max_iterations,
            )

        # Reconcile actual consumption
        summary_len = len(dossier.executive_summary) + sum(len(f) for f in dossier.key_findings)
        sources_len = sum(len(s) for s in dossier.sources)
        actual_cost = max(100, (len(request.brief) + sources_len + summary_len) // 4)

        if token_budget is not None and reserved:
            token_budget.reconcile(str(current_user[1]), actual_cost)
        if platform_budget is not None and platform_reserved:
            platform_budget.reconcile("platform", actual_cost)
    except Exception:
        if token_budget is not None and reserved:
            token_budget.release_reservation(str(current_user[1]), estimated)
        if platform_budget is not None and platform_reserved:
            platform_budget.release_reservation("platform", estimated)
        raise
    return ResearchJobResponse(
        id=UUID(dossier.job_id),
        tenant_id=UUID(dossier.tenant_id),
        query=dossier.brief,
        status=dossier.status,
        plan=[
            {
                "id": g.id,
                "description": g.description,
                "strategy": g.strategy,
                "status": g.status,
                "findings": g.findings,
            }
            for g in dossier.plan
        ],
        steps=[
            {
                "step_number": s.step_number,
                "sub_goal_id": s.sub_goal_id,
                "action": s.action,
                "query": s.query,
                "evidence_collected": s.evidence_collected,
            }
            for s in dossier.steps
        ],
        sources=dossier.sources,
        final_report=dossier.executive_summary,
        completeness_score=dossier.completeness_score,
    )


@router.get("/research/jobs", response_model=list[ResearchJobListItem])
def list_research_jobs_endpoint(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> list[ResearchJobListItem]:
    """List recent research jobs for current tenant (Feature 9)."""
    with get_connection() as connection:
        rows = list_research_jobs(connection, current_user[1], limit=limit)
    return [
        ResearchJobListItem(
            id=UUID(r["id"]),
            tenant_id=UUID(r["tenant_id"]),
            query=r["query"],
            status=r["status"],
            created_at=r["created_at"],
            completed_at=r["completed_at"],
        )
        for r in rows
    ]


@router.get("/research/jobs/{job_id}", response_model=ResearchJobResponse)
def get_research_job_endpoint(
    job_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ResearchJobResponse:
    """Get research dossier and execution history for a job (Feature 9)."""
    with get_connection() as connection:
        row = get_research_job(connection, current_user[1], job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    return ResearchJobResponse(
        id=UUID(row["id"]),
        tenant_id=UUID(row["tenant_id"]),
        query=row["query"],
        status=row["status"],
        plan=row.get("plan", []) or [],
        steps=row.get("steps", []) or [],
        sources=row.get("sources", []) or [],
        final_report=row.get("final_report"),
        created_at=row.get("created_at"),
        completed_at=row.get("completed_at"),
    )


@router.post("/privacy/mask", response_model=MaskTextResponse)
def mask_text_endpoint(
    request: MaskTextRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> MaskTextResponse:
    """Detect and replace PII/PHI with deterministic surrogate tokens (Feature 10: Privacy Vault)."""
    vault = PrivacyVault()
    with get_connection() as connection:
        masked_text, token_map = vault.mask_text(
            text=request.text,
            tenant_id=current_user[1],
            conn=connection,
            store=request.store_in_vault,
        )
    entity_types = list(set(t.split("_")[0].strip("[]") for t in token_map.keys()))
    return MaskTextResponse(
        masked_text=masked_text,
        tokens_count=len(token_map),
        entity_types=entity_types,
    )


@router.post("/privacy/unmask", response_model=UnmaskTextResponse)
def unmask_text_endpoint(
    request: UnmaskTextRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> UnmaskTextResponse:
    """Re-hydrate surrogate tokens back into original plaintext values (Feature 10: Privacy Vault)."""
    vault = PrivacyVault()
    surrogate_tokens = sorted(set(re.findall(r"\[[A-Z]+_[a-f0-9]{8}\]", request.masked_text)))
    with get_connection() as connection:
        unmasked = vault.unmask_text(
            text=request.masked_text,
            tenant_id=current_user[1],
            conn=connection,
        )
        record_audit_log(
            connection,
            tenant_id=current_user[1],
            user_id=current_user[0],
            action="privacy.unmask",
            details={
                "surrogate_tokens": surrogate_tokens,
                "tokens_count": len(surrogate_tokens),
            },
        )
    return UnmaskTextResponse(unmasked_text=unmasked)


@router.get("/privacy/vault", response_model=list[VaultEntryResponse])
def list_vault_endpoint(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> list[VaultEntryResponse]:
    """Audit pseudonym tokens and classified sensitive entity types (Feature 10: Privacy Vault)."""
    vault = PrivacyVault()
    with get_connection() as connection:
        records = vault.list_vault_records(connection, current_user[1], limit=limit)
    return [
        VaultEntryResponse(
            id=r["id"],
            surrogate_token=r["surrogate_token"],
            entity_type=r["entity_type"],
            created_at=r["created_at"],
        )
        for r in records
    ]


@router.get("/admin/dlq", response_model=AdminDLQResponse)
def admin_dlq_inspect(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> AdminDLQResponse:
    """Inspect dead letters and permanent processing failures (platform admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Failed ingestions count and recent rows
            cursor.execute("SELECT count(*) FROM failed_ingestions")
            ingest_count = int(cursor.fetchone()[0])

            cursor.execute(
                """
                SELECT f.id, f.tenant_id, t.name, f.filename, f.error_message, f.created_at
                FROM failed_ingestions f
                JOIN tenants t ON t.id = f.tenant_id
                ORDER BY f.created_at DESC LIMIT %s
                """,
                (limit,),
            )
            ingest_rows = cursor.fetchall()

            # Failed extractions count and recent rows
            cursor.execute("SELECT count(*) FROM failed_extractions")
            extract_count = int(cursor.fetchone()[0])

            cursor.execute(
                """
                SELECT f.id, f.tenant_id, t.name, f.document_id, f.schema_type, f.error, f.created_at
                FROM failed_extractions f
                JOIN tenants t ON t.id = f.tenant_id
                ORDER BY f.created_at DESC LIMIT %s
                """,
                (limit,),
            )
            extract_rows = cursor.fetchall()

    return AdminDLQResponse(
        ingestion_dlq_depth=ingest_count,
        extraction_dlq_depth=extract_count,
        recent_failed_ingestions=[
            {
                "id": str(r[0]),
                "tenant_id": str(r[1]),
                "tenant_name": str(r[2]),
                "filename": str(r[3]),
                "error": str(r[4]),
                "created_at": str(r[5]),
            }
            for r in ingest_rows
        ],
        recent_failed_extractions=[
            {
                "id": str(r[0]),
                "tenant_id": str(r[1]),
                "tenant_name": str(r[2]),
                "document_id": str(r[3]),
                "schema_type": str(r[4]),
                "error": str(r[5]),
                "created_at": str(r[6]),
            }
            for r in extract_rows
        ],
    )


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionJobResponse)
def extraction_job_status(
    job_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> ReprocessResponse:
    """Queue a forced re-extraction (async; the worker performs the model call).

    The forced run bypasses the extraction cache and replaces the existing
    successful row only after validation succeeds; a failed forced run
    preserves the last successful row and records a failed extraction. An
    active job for this document maps to 409.
    """
    settings = get_settings()
    limiter.check(current_user[1], "documents", settings.document_rate_limit_per_minute)
    # Per-tenant daily extraction budget check
    extraction_budget = get_extraction_budget()
    if extraction_budget is not None:
        _, extraction_limit, _, _ = get_tenant_budget_limits(current_user[1])
        allowed, current_usage, _ = extraction_budget.check_and_reserve(
            str(current_user[1]), 1, limit=extraction_limit
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily extraction budget exceeded: {current_usage}/{extraction_limit}",
            )
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
    request: AskRequest,
    current_user: tuple[UUID, UUID] | tuple[UUID, UUID, str, bool],
    settings: Settings,
) -> AskContext:
    """Load history, rewrite follow-ups, embed, and retrieve — shared by /ask and /ask/stream."""
    tenant_id = current_user[1]
    history: list[tuple[str, str]] = []
    if request.conversation_id is not None:
        with get_connection() as connection:
            messages = get_conversation_messages(connection, request.conversation_id, tenant_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = [
            (message.role, message.content)
            for message in messages[-settings.conversation_history_turns :]
        ]
    target_document_ids: list[UUID] | None = None
    if request.document_ids is not None:
        if not request.document_ids:
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
        target_document_ids = list(request.document_ids)
        if request.document_id is not None:
            if request.document_id in target_document_ids:
                target_document_ids = [request.document_id]
            else:
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

    # Structured-filter pre-step: resolve matched documents BEFORE embedding so
    # an empty match short-circuits to a grounded refusal without a paid call
    # — and never silently reverts to an unfiltered tenant search.
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
        if target_document_ids is not None:
            target_document_ids = [
                did for did in target_document_ids if did in set(structured_document_ids)
            ]
            if not target_document_ids:
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
        else:
            target_document_ids = structured_document_ids
    standalone_question = request.question
    # Local mode (LOCAL_GENERATION=true) skips the rewrite call; retrieval just
    # uses the raw question.
    if history and not settings.local_generation:
        try:
            standalone_question = gemini_rewrite_breaker().call(
                lambda: rewrite_followup_question(
                    GeminiTextGenerator(_gemini_client(), settings.gemini_model),
                    request.question,
                    history,
                )
            )
        except CircuitOpenError as exc:
            _raise_provider_unavailable(exc)
        except Exception:
            # Rewrite failed (provider error, timeout, etc.) — degrade to raw
            # question rather than failing the ask. The rewrite is best-effort.
            logger.warning(
                "Follow-up rewrite failed; retrieving with the raw question", exc_info=True
            )
            standalone_question = request.question
    if settings.local_embeddings:
        # Same deterministic vectors the worker embeds with in local mode.
        query_embedding = embed_texts_local([standalone_question])[0]
        embed_input_tokens = 0
    else:
        try:
            embed_result = gemini_embedding_breaker().call(
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
    retrieve_limit = 5
    if target_document_ids and len(target_document_ids) > 1:
        retrieve_limit = max(5, min(20, len(target_document_ids) * 3))
    with get_connection() as connection:
        retrieved = retrieve_chunks(
            connection,
            query_embedding,
            tenant_id=tenant_id,
            question=standalone_question,
            limit=retrieve_limit,
            document_id=request.document_id if target_document_ids is None else None,
            document_ids=target_document_ids,
            doc_type=request.doc_type,
            filename=request.filename,
            created_after=request.created_after,
            created_before=request.created_before,
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
    if (request.structured_filters is not None or (target_document_ids and len(target_document_ids) > 1)) and document_numbers:
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
    parsed: list[Citation],
    document_numbers: dict[UUID, int],
    retrieved: list[tuple[UUID, UUID, TextChunk]] | None = None,
) -> list[CitationResponse]:
    documents_by_number = {number: document_id for document_id, number in document_numbers.items()}
    results: list[CitationResponse] = []
    for citation in parsed:
        if citation.document_index not in documents_by_number:
            continue
        doc_id = documents_by_number[citation.document_index]
        highlights: list[CitationHighlight] = []
        if retrieved:
            for _, r_doc_id, chunk in retrieved:
                if r_doc_id == doc_id and (citation.page is None or chunk.page == citation.page):
                    for box_item in chunk.bounding_boxes:
                        if isinstance(box_item, dict) and "box" in box_item:
                            highlights.append(
                                CitationHighlight(
                                    page=box_item.get("page", chunk.page),
                                    box=box_item.get("box", [0.0, 0.0, 1.0, 1.0]),
                                    text_snippet=box_item.get("text_snippet"),
                                )
                            )
        results.append(
            CitationResponse(document_id=doc_id, page=citation.page, highlights=highlights)
        )
    return results


def _record_ask(
    http_request: Request,
    current_user: tuple[UUID, UUID] | tuple[UUID, UUID, str, bool],
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
                c_payload = []
                for citation in citations:
                    c_dict: dict[str, Any] = {
                        "document_id": str(citation.document_id),
                        "page": citation.page,
                    }
                    if citation.highlights:
                        c_dict["highlights"] = [
                            h.model_dump(mode="json") for h in citation.highlights
                        ]
                    c_payload.append(c_dict)
                append_exchange(
                    connection,
                    context.conversation_id,
                    current_user[1],
                    question=context.question,
                    answer=answer,
                    citations=c_payload,
                )
    except Exception:
        # Telemetry and history persistence must never fail the answer itself.
        logger.error("Failed to record ask telemetry/history", exc_info=True)


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _ask_done_event(
    answer_text: str, citations: list[CitationResponse], conversation_id: UUID | None
) -> str:
    c_payload = []
    for citation in citations:
        c_dict: dict[str, Any] = {
            "document_id": str(citation.document_id),
            "page": citation.page,
        }
        if citation.highlights:
            c_dict["highlights"] = [h.model_dump(mode="json") for h in citation.highlights]
        c_payload.append(c_dict)
    return _sse_event(
        "done",
        {
            "answer": answer_text,
            "citations": c_payload,
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
    current_user: tuple[UUID, UUID] | tuple[UUID, UUID, str, bool],
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
    citations = _citations_for(
        parse_citations(answer_text), context.document_numbers, context.retrieved
    )
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("query:ask")),
) -> AskResponse:
    started = perf_counter()
    settings = get_settings()
    limiter.check(current_user[1], "ask", settings.ask_rate_limit_per_minute)
    # Per-tenant daily token budget check (pre-flight estimate)
    token_budget = get_token_budget()
    # Platform-wide daily token budget check (hard ceiling)
    platform_budget = get_platform_token_budget()
    reserved = False
    platform_reserved = False
    estimated = 0
    if token_budget is not None:
        estimated = estimate_token_cost(request.question)
        token_limit, _, tier, _ = get_tenant_budget_limits(current_user[1])
        allowed, current_usage, _ = token_budget.check_and_reserve(
            str(current_user[1]), estimated, limit=token_limit
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily token budget exceeded: {current_usage}/{token_limit}",
            )
        reserved = True
    if platform_budget is not None:
        # Use same estimate for platform budget
        if estimated == 0:
            estimated = estimate_token_cost(request.question)
        allowed, current_usage, _ = platform_budget.check_and_reserve("platform", estimated)
        if not allowed:
            # Release tenant reservation if platform budget exceeded
            if token_budget is not None and reserved:
                token_budget.release_reservation(str(current_user[1]), estimated)
            import logging

            logging.getLogger("knowledgeforge.budget").warning(
                "Platform daily token budget exceeded: %d/%d",
                current_usage,
                settings.platform_daily_token_budget,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Platform daily token budget exceeded: {current_usage}/{settings.platform_daily_token_budget}",
            )
        platform_reserved = True
    context = _prepare_ask(request, current_user, settings)
    try:
        if settings.local_generation:
            answer_text = local_answer(
                context.standalone_question, context.labeled_chunks, context.labeled_extractions
            )
            v_res = EntailmentVerifier().verify(answer_text, context.labeled_chunks)
            answer_text = v_res.verified_answer
            grounding_score = v_res.grounding_score
            is_grounded = v_res.is_grounded
            parsed = parse_citations(answer_text)
            input_tokens = context.embed_input_tokens
            output_tokens = 0
        else:
            try:
                verifier = EntailmentVerifier(_gemini_client(), settings.gemini_model)

                def _run_gen() -> GeneratedAnswer:
                    try:
                        return generate_answer(
                            GeminiTextGenerator(_gemini_client(), settings.gemini_model),
                            context.standalone_question,
                            context.labeled_chunks,
                            context.labeled_extractions,
                            verifier=verifier,
                        )
                    except TypeError:
                        return generate_answer(
                            GeminiTextGenerator(_gemini_client(), settings.gemini_model),
                            context.standalone_question,
                            context.labeled_chunks,
                            context.labeled_extractions,
                        )

                answer = gemini_generation_breaker().call(_run_gen)
            except CircuitOpenError as exc:
                _raise_provider_unavailable(exc)
            answer_text = answer.answer
            grounding_score = answer.grounding_score
            is_grounded = answer.is_grounded
            parsed = answer.citations
            input_tokens = answer.input_tokens + context.embed_input_tokens
            output_tokens = answer.output_tokens
        citations = _citations_for(parsed, context.document_numbers, context.retrieved)
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
        # Reconcile token budget with actual usage on success
        if token_budget is not None and reserved:
            actual_cost = input_tokens + output_tokens
            token_budget.reconcile(str(current_user[1]), actual_cost)
        if platform_budget is not None and platform_reserved:
            actual_cost = input_tokens + output_tokens
            platform_budget.reconcile("platform", actual_cost)
        # TableQA synthesis for quantitative/aggregation intents (Feature 4)
        sql_executed = None
        table_results = None
        if classify_query_intent(request.question) != QueryIntent.NARRATIVE_RAG:
            try:
                with get_connection() as connection:
                    table_qa_res = TableQASynthesizer(current_user[1]).execute(
                        connection, request.question
                    )
                if table_qa_res.is_applicable and table_qa_res.data.get("record_count", 0) > 0:
                    sql_executed = table_qa_res.sql_executed
                    table_results = table_qa_res.data
                    if (
                        "I don't have enough information" in answer_text
                        or len(answer_text.strip()) < 10
                    ):
                        answer_text = table_qa_res.answer
                    else:
                        answer_text = f"{table_qa_res.answer}\n\n{answer_text}"
            except Exception:
                pass

        return AskResponse(
            answer=answer_text,
            citations=citations,
            conversation_id=context.conversation_id,
            grounding_score=grounding_score,
            is_grounded=is_grounded,
            sql_executed=sql_executed,
            table_results=table_results,
        )
    except Exception:
        # Release reservations on any failure so failed calls don't consume budget
        if token_budget is not None and reserved:
            token_budget.release_reservation(str(current_user[1]), estimated)
        if platform_budget is not None and platform_reserved:
            platform_budget.release_reservation("platform", estimated)
        raise


@router.post("/ask/stream")
def ask_stream(
    request: AskRequest,
    http_request: Request,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_scope("query:ask")),
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
    # Per-tenant daily token budget check (pre-flight estimate)
    token_budget = get_token_budget()
    # Platform-wide daily token budget check (hard ceiling)
    platform_budget = get_platform_token_budget()
    reserved = False
    platform_reserved = False
    estimated = 0
    if token_budget is not None:
        estimated = estimate_token_cost(request.question)
        token_limit, _, tier, _ = get_tenant_budget_limits(current_user[1])
        allowed, current_usage, _ = token_budget.check_and_reserve(
            str(current_user[1]), estimated, limit=token_limit
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily token budget exceeded: {current_usage}/{token_limit}",
            )
        reserved = True
    if platform_budget is not None:
        if estimated == 0:
            estimated = estimate_token_cost(request.question)
        allowed, current_usage, _ = platform_budget.check_and_reserve("platform", estimated)
        if not allowed:
            if token_budget is not None and reserved:
                token_budget.release_reservation(str(current_user[1]), estimated)
            import logging

            logging.getLogger("knowledgeforge.budget").warning(
                "Platform daily token budget exceeded: %d/%d",
                current_usage,
                settings.platform_daily_token_budget,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Platform daily token budget exceeded: {current_usage}/{settings.platform_daily_token_budget}",
            )
        platform_reserved = True
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
    breaker = gemini_generation_breaker()
    breaker.ensure_available()

    def stream() -> Iterator[str]:
        generation = GeminiTextStream(_gemini_client(), settings.gemini_model, prompt)
        parts: list[str] = []
        stream_succeeded = False
        try:
            for delta in generation:
                parts.append(delta)
                yield _sse_event("token", {"text": delta})
            if not parts:
                raise RuntimeError("Gemini returned an empty response")
            breaker.record_success()
            stream_succeeded = True
        except Exception:
            breaker.record_failure()
            logger.error("Streaming answer generation failed", exc_info=True)
            yield _sse_event("error", {"detail": "Answer generation failed"})
            return
        finally:
            # Handle budget reconciliation/release after stream completes or fails
            if token_budget is not None and reserved:
                if stream_succeeded:
                    actual_cost = (
                        generation.input_tokens
                        + context.embed_input_tokens
                        + generation.output_tokens
                    )
                    token_budget.reconcile(str(current_user[1]), actual_cost)
                else:
                    token_budget.release_reservation(str(current_user[1]), estimated)
            if platform_budget is not None and platform_reserved:
                if stream_succeeded:
                    actual_cost = (
                        generation.input_tokens
                        + context.embed_input_tokens
                        + generation.output_tokens
                    )
                    platform_budget.reconcile("platform", actual_cost)
                else:
                    platform_budget.release_reservation("platform", estimated)
        full_answer = "".join(parts).strip()
        citations = _citations_for(
            parse_citations(full_answer), context.document_numbers, context.retrieved
        )
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> None:
    with get_connection() as connection:
        found = delete_conversation(connection, conversation_id, current_user[1])
    if not found:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/admin/usage", response_model=UsageResponse)
def usage(
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> UsageResponse:
    """Tenant usage dashboard: totals plus a per-day series (F5).

    Gated to tenant owners only; cost/token/spend data is billing-adjacent
    and restricted to owners by least-privilege default.
    """
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
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> None:
    settings = get_settings()
    with get_connection() as connection:
        found, storage_uri = delete_document(connection, document_id, current_user[1])
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    if storage_uri:
        CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id).delete(storage_uri)


@router.get("/auth/account/export")
def export_account(
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> dict[str, Any]:
    """Export complete machine-readable tenant data for GDPR Article 20 data portability.

    Restricted to tenant owner. Returns tenant metadata, users, documents, extractions,
    conversations, and redacted API keys.
    """
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        return export_tenant_data(connection, tenant_id)


@router.delete("/auth/account", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user)) -> None:
    settings = get_settings()
    with get_connection() as connection:
        storage_uris = delete_tenant(connection, current_user[1])
    if storage_uris:
        storage = CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id)
        for storage_uri in storage_uris:
            storage.delete(storage_uri)


# Self-Service Tenant Dashboard & Usage Endpoints (F5)


class TenantUsageResponse(BaseModel):
    tenant_id: str
    documents_count: int
    chunks_count: int
    extractions_count: int
    conversations_count: int
    queries_count: int
    cost_estimate_total: float
    daily_token_usage: int
    daily_token_budget: int
    daily_extraction_usage: int
    daily_extraction_budget: int


class TenantMetadataResponse(BaseModel):
    id: str
    name: str
    tier: str
    subscription_status: str
    created_at: str


class TenantDailyTrendResponse(BaseModel):
    day: str
    queries: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class TenantDashboardResponse(BaseModel):
    tenant: TenantMetadataResponse
    usage: TenantUsageResponse
    daily_trends: list[TenantDailyTrendResponse]


@router.get("/tenant/usage", response_model=TenantUsageResponse, tags=["tenant"])
def get_tenant_usage_endpoint(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> TenantUsageResponse:
    """Self-service usage metrics strictly scoped to the caller's tenant."""
    _, tenant_id, _, _ = current_user
    settings = get_settings()

    with get_connection() as connection:
        doc_count, query_count, cost = tenant_usage(connection, tenant_id)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.tenant_id = %s
                """,
                (tenant_id,),
            )
            chunks_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT count(*) FROM document_extractions WHERE tenant_id = %s",
                (tenant_id,),
            )
            extractions_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT count(*) FROM conversations WHERE tenant_id = %s",
                (tenant_id,),
            )
            conversations_count = cursor.fetchone()[0]

    token_budget = get_token_budget()
    extraction_budget = get_extraction_budget()
    token_usage = token_budget.get_usage(str(tenant_id))[0] if token_budget else 0
    extraction_usage = extraction_budget.get_usage(str(tenant_id))[0] if extraction_budget else 0

    return TenantUsageResponse(
        tenant_id=str(tenant_id),
        documents_count=doc_count,
        chunks_count=chunks_count,
        extractions_count=extractions_count,
        conversations_count=conversations_count,
        queries_count=query_count,
        cost_estimate_total=cost,
        daily_token_usage=token_usage,
        daily_token_budget=settings.daily_token_budget,
        daily_extraction_usage=extraction_usage,
        daily_extraction_budget=settings.daily_extraction_budget,
    )


@router.get("/tenant/dashboard", response_model=TenantDashboardResponse, tags=["tenant"])
def get_tenant_dashboard_endpoint(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> TenantDashboardResponse:
    """Self-service comprehensive dashboard strictly scoped to the caller's tenant."""
    _, tenant_id, _, _ = current_user
    settings = get_settings()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, tier, subscription_status, created_at FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            tenant_row = cursor.fetchone()
            if not tenant_row:
                raise HTTPException(status_code=404, detail="Tenant not found")

            doc_count, query_count, cost = tenant_usage(connection, tenant_id)

            cursor.execute(
                """
                SELECT count(*)
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.tenant_id = %s
                """,
                (tenant_id,),
            )
            chunks_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT count(*) FROM document_extractions WHERE tenant_id = %s",
                (tenant_id,),
            )
            extractions_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT count(*) FROM conversations WHERE tenant_id = %s",
                (tenant_id,),
            )
            conversations_count = cursor.fetchone()[0]

        daily_rows = tenant_usage_daily(connection, tenant_id, days=14)

    token_budget = get_token_budget()
    extraction_budget = get_extraction_budget()
    token_usage = token_budget.get_usage(str(tenant_id))[0] if token_budget else 0
    extraction_usage = extraction_budget.get_usage(str(tenant_id))[0] if extraction_budget else 0

    return TenantDashboardResponse(
        tenant=TenantMetadataResponse(
            id=str(tenant_row[0]),
            name=tenant_row[1],
            tier=str(tenant_row[2]),
            subscription_status=str(tenant_row[3]),
            created_at=str(tenant_row[4]),
        ),
        usage=TenantUsageResponse(
            tenant_id=str(tenant_id),
            documents_count=doc_count,
            chunks_count=chunks_count,
            extractions_count=extractions_count,
            conversations_count=conversations_count,
            queries_count=query_count,
            cost_estimate_total=cost,
            daily_token_usage=token_usage,
            daily_token_budget=settings.daily_token_budget,
            daily_extraction_usage=extraction_usage,
            daily_extraction_budget=settings.daily_extraction_budget,
        ),
        daily_trends=[
            TenantDailyTrendResponse(
                day=r.day,
                queries=r.queries,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_estimate=r.cost_estimate,
            )
            for r in daily_rows
        ],
    )


# Outbound Tenant Webhooks Endpoints (F6)


class CreateWebhookRequest(BaseModel):
    url: str
    events: list[str] = Field(default_factory=lambda: ["document.ready", "extraction.ready"])
    secret: str | None = None


class WebhookResponse(BaseModel):
    id: str
    tenant_id: str
    url: str
    events: list[str]
    active: bool
    created_at: str


@router.post(
    "/tenant/webhooks",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["webhooks"],
)
def create_tenant_webhook_endpoint(
    body: CreateWebhookRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> WebhookResponse:
    """Register an outbound webhook with SSRF validation (owner only)."""
    _, tenant_id, _, _ = current_user
    allow_private = False

    try:
        validate_webhook_url(body.url, allow_private=allow_private)
    except SSRFValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    secret = body.secret or generate_webhook_secret()
    with get_connection() as connection:
        webhook = register_webhook(
            connection,
            tenant_id,
            body.url,
            secret,
            body.events,
            allow_private=allow_private,
        )

    return WebhookResponse(
        id=webhook["id"],
        tenant_id=webhook["tenant_id"],
        url=webhook["url"],
        events=webhook["events"],
        active=webhook["active"],
        created_at=webhook["created_at"],
    )


@router.get(
    "/tenant/webhooks",
    response_model=list[WebhookResponse],
    tags=["webhooks"],
)
def list_tenant_webhooks_endpoint(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> list[WebhookResponse]:
    """List all registered webhooks for caller's tenant."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        webhooks = list_webhooks(connection, tenant_id)

    return [
        WebhookResponse(
            id=w["id"],
            tenant_id=w["tenant_id"],
            url=w["url"],
            events=w["events"],
            active=w["active"],
            created_at=w["created_at"],
        )
        for w in webhooks
    ]


@router.delete(
    "/tenant/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["webhooks"],
)
def delete_tenant_webhook_endpoint(
    webhook_id: UUID,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> None:
    """Delete a registered webhook (owner only)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        deleted = delete_webhook(connection, tenant_id, webhook_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")


# Enterprise OIDC SSO Endpoints (F7)


class UpdateSSOConfigRequest(BaseModel):
    issuer_url: str
    client_id: str
    client_secret: str | None = None
    enabled: bool = True


class SSOConfigResponse(BaseModel):
    tenant_id: str
    enabled: bool
    issuer_url: str
    client_id: str
    has_client_secret: bool
    created_at: str
    updated_at: str


class SSOAuthorizeRequest(BaseModel):
    tenant_id: UUID
    redirect_uri: str


class SSOAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class SSOCallbackRequest(BaseModel):
    state: str
    claims: dict[str, Any]


class SSOLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str


@router.put("/tenant/sso/config", response_model=SSOConfigResponse, tags=["sso"])
def update_sso_config_endpoint(
    body: UpdateSSOConfigRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> SSOConfigResponse:
    """Configure Enterprise OIDC Single Sign-On (owner only, Enterprise tier required)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        config = save_sso_config(
            connection,
            tenant_id,
            issuer_url=body.issuer_url,
            client_id=body.client_id,
            client_secret=body.client_secret,
            enabled=body.enabled,
        )
    return SSOConfigResponse(**config)


@router.get("/tenant/sso/config", response_model=SSOConfigResponse, tags=["sso"])
def get_sso_config_endpoint(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> SSOConfigResponse:
    """View tenant OIDC SSO configuration (Enterprise tier required)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        validate_enterprise_tier(connection, tenant_id)
        config = get_sso_config(connection, tenant_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found"
        )
    return SSOConfigResponse(**config)


@router.post("/auth/sso/oidc/authorize", response_model=SSOAuthorizeResponse, tags=["sso"])
def sso_authorize_endpoint(body: SSOAuthorizeRequest) -> SSOAuthorizeResponse:
    """Initiate an OIDC SSO authorization flow for a tenant."""
    with get_connection() as connection:
        validate_enterprise_tier(connection, body.tenant_id)
        config = get_sso_config(connection, body.tenant_id)
        if not config or not config["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="SSO is not enabled for this tenant"
            )

    state = create_sso_state(body.tenant_id)
    auth_url = build_authorization_url(config, body.redirect_uri, state)
    return SSOAuthorizeResponse(authorization_url=auth_url, state=state)


@router.post("/auth/sso/oidc/callback", response_model=SSOLoginResponse, tags=["sso"])
def sso_callback_endpoint(body: SSOCallbackRequest) -> SSOLoginResponse:
    """Handle OIDC authorization callback, verify claims, provision user, and mint JWT."""
    tenant_id = verify_sso_state(body.state)

    with get_connection() as connection:
        validate_enterprise_tier(connection, tenant_id)
        user_id, _, role = process_sso_claims(connection, tenant_id, body.claims)
        access_token = create_access_token(user_id, tenant_id, role)
        refresh_token = create_refresh_token(connection, user_id)

    return SSOLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        role=role,
    )


# Tenant Retention and Data Residency Settings (F8)

VALID_RESIDENCY_REGIONS = {"us", "eu", "apac"}


class TenantSettingsResponse(BaseModel):
    tenant_id: str
    retention_days: int
    data_residency: str


class UpdateTenantSettingsRequest(BaseModel):
    retention_days: Annotated[int, Field(ge=0, le=3650)] = 0
    data_residency: str = Field(default="us", pattern="^(us|eu|apac)$")


@router.get("/tenant/settings", response_model=TenantSettingsResponse, tags=["tenant"])
def get_tenant_settings_endpoint(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> TenantSettingsResponse:
    """Retrieve tenant retention policy and data residency region."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT retention_days, data_residency FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
                )
    return TenantSettingsResponse(
        tenant_id=str(tenant_id),
        retention_days=int(row[0] or 0),
        data_residency=str(row[1] or "us"),
    )


@router.put("/tenant/settings", response_model=TenantSettingsResponse, tags=["tenant"])
def update_tenant_settings_endpoint(
    body: UpdateTenantSettingsRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> TenantSettingsResponse:
    """Update tenant retention days and data residency (owner only)."""
    _, tenant_id, _, _ = current_user
    residency = body.data_residency.lower().strip()
    if residency not in VALID_RESIDENCY_REGIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid data_residency '{body.data_residency}'; must be one of {sorted(VALID_RESIDENCY_REGIONS)}",
        )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tenants
                SET retention_days = %s,
                    data_residency = %s
                WHERE id = %s
                RETURNING retention_days, data_residency;
                """,
                (body.retention_days, residency, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
                )
            connection.commit()

    return TenantSettingsResponse(
        tenant_id=str(tenant_id),
        retention_days=int(row[0]),
        data_residency=str(row[1]),
    )


# Admin console endpoints (platform admin only)


@router.get("/admin", response_class=HTMLResponse)
def admin_console_page() -> HTMLResponse:
    """Serve the single-page admin console UI."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")
    nonce = secrets.token_urlsafe(16)
    from knowledgeforge.admin_ui import render_admin_html

    content = render_admin_html(nonce=nonce)
    csp = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';"
    )
    return HTMLResponse(content=content, headers={"Content-Security-Policy": csp})


@router.get("/admin/tenants", response_model=AdminTenantListResponse)
def admin_list_tenants(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> AdminTenantListResponse:
    """List all tenants with usage stats (platform admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")
    with get_connection() as connection:
        rows = list_all_tenants(connection, limit=limit, offset=offset)
    return AdminTenantListResponse(
        tenants=[
            AdminTenantResponse(
                tenant_id=row.tenant_id,
                name=row.name,
                created_at=row.created_at,
                document_count=row.document_count,
                query_count=row.query_count,
                cost_estimate=row.cost_estimate,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.get("/admin/ingestions/failed", response_model=AdminFailedIngestionListResponse)
def admin_failed_ingestions(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> AdminFailedIngestionListResponse:
    """List failed ingestions across all tenants (platform admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")
    with get_connection() as connection:
        rows = list_all_failed_ingestions(connection, limit=limit, offset=offset)
    return AdminFailedIngestionListResponse(
        failed_ingestions=[
            AdminFailedIngestionResponse(
                id=row[0],
                tenant_id=row[1],
                tenant_name=row[2],
                filename=row[3],
                error_message=row[4],
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


# Billing and Subscription endpoints


@router.post("/billing/webhook")
async def stripe_webhook(request: Request) -> dict:
    """Process Stripe webhook events with HMAC signature verification and atomic idempotency."""
    settings = get_settings()
    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Fail closed: reject every request if webhook secret is unconfigured
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook endpoint is not configured (missing webhook secret)",
        )

    valid = verify_stripe_signature(
        payload_bytes,
        sig_header,
        settings.stripe_webhook_secret,
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature",
        )

    try:
        import json

        event_data = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    with get_connection() as connection:
        result = process_stripe_event(connection, event_data)
    return result


@router.post("/billing/create-checkout-session", response_model=CheckoutSessionResponse)
def checkout_session(
    body: CheckoutSessionRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> CheckoutSessionResponse:
    """Create a Stripe Checkout Session for upgrading tenant tier (tenant owner only)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        info = get_tenant_billing_info(connection, tenant_id)
    session_id, url = create_checkout_session(
        tenant_id=str(tenant_id),
        tier=body.tier,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        customer_id=info.get("stripe_customer_id"),
    )
    return CheckoutSessionResponse(session_id=session_id, url=url)


@router.post("/billing/create-portal-session", response_model=CustomerPortalResponse)
def portal_session(
    body: CustomerPortalRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_owner),
) -> CustomerPortalResponse:
    """Create a Stripe Customer Portal session (tenant owner only)."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        info = get_tenant_billing_info(connection, tenant_id)
    customer_id = info.get("stripe_customer_id")
    if not customer_id:
        settings = get_settings()
        if not settings.stripe_secret_key:
            customer_id = f"cus_mock_{tenant_id.hex[:12]}"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active Stripe customer found for this tenant",
            )
    url = create_portal_session(customer_id, body.return_url)
    return CustomerPortalResponse(url=url)


@router.get("/billing/subscription", response_model=SubscriptionResponse)
def get_subscription(
    current_user: tuple[UUID, UUID, str, bool] = Depends(get_current_user),
) -> SubscriptionResponse:
    """Get current tenant's subscription status, tier, usage, and budget limits."""
    _, tenant_id, _, _ = current_user
    with get_connection() as connection:
        info = get_tenant_billing_info(connection, tenant_id)

    token_budget = get_token_budget()
    extraction_budget = get_extraction_budget()
    token_usage = token_budget.get_usage(str(tenant_id))[0] if token_budget else 0
    extract_usage = extraction_budget.get_usage(str(tenant_id))[0] if extraction_budget else 0

    return SubscriptionResponse(
        tenant_id=tenant_id,
        tier=info["tier"],
        subscription_status=info["subscription_status"],
        current_period_end=info["current_period_end"],
        stripe_customer_id=info["stripe_customer_id"],
        stripe_subscription_id=info["stripe_subscription_id"],
        daily_token_budget=info["daily_token_budget"],
        daily_extraction_budget=info["daily_extraction_budget"],
        current_token_usage=token_usage,
        current_extraction_usage=extract_usage,
        is_email_verified=info["is_email_verified"],
        in_grace_period=info["in_grace_period"],
    )


@router.get("/admin/billing/tenants", response_model=list[TenantBillingAdminView])
def admin_billing_tenants(
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> list[TenantBillingAdminView]:
    """List billing, tier, limits, and usage across all tenants (platform admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")

    token_budget = get_token_budget()
    extraction_budget = get_extraction_budget()

    with get_connection() as connection:
        tenants = list_tenants_billing_admin(connection)

    views = []
    for t in tenants:
        tid_str = str(t["tenant_id"])
        token_usage = token_budget.get_usage(tid_str)[0] if token_budget else 0
        extract_usage = extraction_budget.get_usage(tid_str)[0] if extraction_budget else 0
        views.append(
            TenantBillingAdminView(
                tenant_id=t["tenant_id"],
                tenant_name=t["tenant_name"],
                tier=t["tier"],
                subscription_status=t["subscription_status"],
                stripe_customer_id=t["stripe_customer_id"],
                stripe_subscription_id=t["stripe_subscription_id"],
                current_period_end=t["current_period_end"],
                token_usage=token_usage,
                extraction_usage=extract_usage,
                token_limit=t["token_limit"],
                extraction_limit=t["extraction_limit"],
                is_email_verified=t["is_email_verified"],
            )
        )
    return views


@router.put("/admin/billing/tenants/{tenant_id}/tier")
def admin_update_tenant_tier(
    tenant_id: UUID,
    body: UpdateTenantTierRequest,
    current_user: tuple[UUID, UUID, str, bool] = Depends(require_platform_admin),
) -> dict:
    """Manually update a tenant's subscription tier (platform admin only)."""
    settings = get_settings()
    if not settings.admin_console_enabled:
        raise HTTPException(status_code=403, detail="Admin console is disabled")

    with get_connection() as connection:
        updated = update_tenant_tier_admin(
            connection,
            tenant_id,
            body.tier,
            body.subscription_status,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"status": "updated", "tenant_id": str(tenant_id), "tier": body.tier}
