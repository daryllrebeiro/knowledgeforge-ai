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
    database_url: str = "postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge"
    chunk_size: int = 500
    chunk_overlap: int = 100
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    gcp_project_id: str = ""
    gcs_bucket: str = ""
    pubsub_topic: str = "knowledgeforge-ingestion"
    pubsub_subscription: str = "knowledgeforge-ingestion-worker"
    async_ingestion: bool = False
    ask_rate_limit_per_minute: int = 60
    document_rate_limit_per_minute: int = 10
    max_documents_per_tenant: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
