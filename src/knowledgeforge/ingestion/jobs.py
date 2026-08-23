from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class IngestionJob:
    document_id: UUID
    tenant_id: UUID
    storage_uri: str
    content_hash: str


def should_process(status: str) -> bool:
    """Return whether a Pub/Sub delivery should run the ingestion pipeline."""
    return status in {"pending", "failed"}
