"""Extraction job event payload shared by the outbox, dispatcher, and worker."""

import json
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ExtractionEvent:
    job_id: UUID
    document_id: UUID
    tenant_id: UUID
    content_hash: str
    schema_type: str
    schema_version: int
    model: str
    reason: str = "ready"


def parse_event(payload: bytes) -> ExtractionEvent:
    data = json.loads(payload)
    return ExtractionEvent(
        job_id=UUID(data["job_id"]),
        document_id=UUID(data["document_id"]),
        tenant_id=UUID(data["tenant_id"]),
        content_hash=data["content_hash"],
        schema_type=data["schema_type"],
        schema_version=int(data["schema_version"]),
        model=data["model"],
        reason=str(data.get("reason", "ready")),
    )
