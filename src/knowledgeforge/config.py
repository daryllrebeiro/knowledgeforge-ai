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
    # USD per 1M tokens; 0.0 matches the Gemini free tier. Set per the current
    # pricing page when cost tracking must be real money.
    gemini_input_token_cost: float = 0.0
    gemini_output_token_cost: float = 0.0

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    def validate_runtime(self) -> None:
        """Fail closed outside development rather than running on unsafe defaults."""
        problems: list[str] = []
        if self.environment != "development":
            if self.jwt_secret_key in {"", "change-me-in-production"}:
                problems.append("JWT_SECRET_KEY must be set outside development")
            elif len(self.jwt_secret_key) < 32:
                problems.append("JWT_SECRET_KEY must be at least 32 characters")
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
        if problems:
            raise RuntimeError("Refusing to start: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
