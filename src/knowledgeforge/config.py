from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "KnowledgeForge AI"
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_timeout_seconds: float = 30.0
    gemini_retry_attempts: int = 2
    gemini_breaker_failure_threshold: int = 3
    gemini_breaker_recovery_seconds: float = 30.0
    database_url: str = "postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    chunk_size: int = 500
    chunk_overlap: int = 100
    chunk_section_aware: bool = False
    hybrid_search_enabled: bool = False
    hybrid_lexical_weight: float = 0.15
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    # RS256 support (optional; if set, overrides jwt_secret_key)
    jwt_private_key: str = ""  # PEM-encoded RSA private key
    jwt_public_key: str = ""  # PEM-encoded RSA public key
    jwt_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    gcp_project_id: str = ""
    gcs_bucket: str = ""
    pubsub_topic: str = "knowledgeforge-ingestion"
    pubsub_subscription: str = "knowledgeforge-ingestion-worker"
    # When set, the push worker verifies Pub/Sub OIDC tokens against this
    # audience (the worker's own URL) in addition to Cloud Run invoker IAM.
    worker_oidc_audience: str = ""
    # Phase 2.5 extraction pipeline: separate topic/subscription so extraction
    # backpressure can never block ingestion's own queue.
    extraction_topic: str = "knowledgeforge-extraction"
    extraction_subscription: str = "knowledgeforge-extraction-worker"
    extraction_worker_oidc_audience: str = ""
    extraction_schema_type: str = "invoice"
    extraction_schema_version: int = 1
    extraction_model: str = "gemini-2.0-flash"
    # Any field below 0.5, or overall below 0.75, flags the row for review.
    extraction_field_confidence_threshold: float = 0.5
    extraction_overall_confidence_threshold: float = 0.75
    async_ingestion: bool = False
    ask_rate_limit_per_minute: int = 60
    document_rate_limit_per_minute: int = 10
    auth_rate_limit_per_minute: int = 10
    research_rate_limit_per_minute: int = 10
    schema_rate_limit_per_minute: int = 20
    # Global registration rate limit (per hour) to prevent tenant farming
    registration_rate_limit_per_hour: int = 50
    # Most recent messages fed to follow-up question rewriting (both roles).
    conversation_history_turns: int = 10
    max_documents_per_tenant: int = 100
    max_upload_bytes: int = 10_000_000
    max_batch_files: int = 20
    cors_allowed_origins: str = ""
    redis_url: str = ""
    local_embeddings: bool = False
    # Emulator-only: /ask answers come from a deterministic local generator
    # instead of Gemini, so the full ask pipeline runs without credentials
    # (local stack, chaos drills, load tests). Never in production.
    local_generation: bool = False
    # Emulator-only: extraction (classification + invoice fields + OCR) comes
    # from deterministic local fixtures so the extraction loop runs without
    # credentials. Refused outside development.
    local_extraction: bool = False
    # Admin console cross-tenant access — disabled by default; requires
    # deliberate enablement and platform-admin role (separate from tenant roles).
    admin_console_enabled: bool = False
    # Extractor guard limits (zip bomb / large line protection).
    max_pptx_decompressed_bytes: int = 100_000_000  # 100 MB
    max_csv_line_bytes: int = 10_000_000  # 10 MB per line
    max_csv_total_bytes: int = 200_000_000  # 200 MB total
    # HNSW query-time recall/latency knob (set via SET LOCAL hnsw.ef_search = N).
    hnsw_ef_search: int = 100
    # USD per 1M tokens; 0.0 matches the Gemini free tier. Set per the current
    # pricing page when cost tracking must be real money.
    gemini_input_token_cost: float = 0.0
    gemini_output_token_cost: float = 0.0
    # Per-tenant daily budget limits (enforced when Redis is configured).
    daily_token_budget: int = 1_000_000
    daily_extraction_budget: int = 1000
    # Platform-wide daily token budget ceiling (enforced when Redis is configured).
    # This is a hard limit on total platform spend, independent of per-tenant budgets.
    platform_daily_token_budget: int = 10_000_000
    # Stripe billing configuration
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""
    stripe_enterprise_price_id: str = ""
    stripe_payment_grace_period_days: int = 3
    # Custom Stripe API base URL (e.g. "http://localhost:12111" for stripe-mock)
    stripe_api_base: str = ""
    # When True, Stripe API calls fall back to deterministic local mock sessions.
    # Refused outside development by validate_runtime().
    local_billing: bool = False
    # Transactional email provider configuration
    email_provider: str = "console"  # "console", "postmark", "sendgrid"
    email_from_address: str = "noreply@knowledgeforge.ai"
    postmark_api_token: str = ""
    sendgrid_api_key: str = ""
    email_verification_rate_limit_per_minute: int = 5
    # Distinct secret key for Zero-Knowledge Privacy Vault at-rest encryption.
    vault_master_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    def validate_runtime(self) -> None:
        """Fail closed on unsafe defaults regardless of environment."""
        problems: list[str] = []
        # Check if using RS256 (asymmetric keys)
        using_rs256 = (
            self.jwt_algorithm.upper() == "RS256" and self.jwt_private_key and self.jwt_public_key
        )
        # If not using RS256, enforce HS256 secret strength
        if not using_rs256:
            if self.jwt_secret_key in {
                "",
                "change-me-in-production",
                "REPLACE_WITH_32_CHAR_MIN_SECRET_OR_STARTUP_WILL_FAIL",
            }:
                problems.append("JWT_SECRET_KEY must be set (cannot use default placeholder)")
            elif len(self.jwt_secret_key) < 32:
                problems.append("JWT_SECRET_KEY must be at least 32 characters")
        else:
            # Validate RS256 keys are present and non-empty
            if not self.jwt_private_key.strip():
                problems.append("JWT_PRIVATE_KEY must be set when using RS256")
            if not self.jwt_public_key.strip():
                problems.append("JWT_PUBLIC_KEY must be set when using RS256")
        if self.local_extraction:
            # LOCAL_EXTRACTION is deterministic fixture output; it must
            # never silently run in a real deployment.
            problems.append("LOCAL_EXTRACTION may only be used in development")
        if not (self.local_embeddings and self.local_generation) and self.gemini_api_key in {
            "",
            "replace-me",
        }:
            problems.append(
                "GEMINI_API_KEY must be configured when LOCAL_EMBEDDINGS or "
                "LOCAL_GENERATION is disabled"
            )
        if self.local_billing and self.environment.lower() != "development":
            problems.append("LOCAL_BILLING may only be used in development")
        if self.environment.lower() != "development":
            if self.vault_master_key in {
                "",
                "change-me-in-production",
                "REPLACE_WITH_32_CHAR_MIN_VAULT_KEY",
            }:
                problems.append("VAULT_MASTER_KEY must be set in non-development environments")
            elif len(self.vault_master_key) < 32:
                problems.append("VAULT_MASTER_KEY must be at least 32 characters")
            billing_enabled = bool(self.stripe_secret_key or self.stripe_webhook_secret)
            if billing_enabled:
                if not self.stripe_webhook_secret:
                    problems.append(
                        "STRIPE_WEBHOOK_SECRET must be configured in non-development environments when billing is enabled"
                    )
                if not self.stripe_secret_key:
                    problems.append(
                        "STRIPE_SECRET_KEY must be configured in non-development environments when billing is enabled"
                    )
        if problems:
            raise RuntimeError("Refusing to start: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
