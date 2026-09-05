from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID


@dataclass(frozen=True)
class DedupDecision:
    action: str
    version: int


def content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


def decide_dedup(
    existing: tuple[UUID, int] | None,
    previous: tuple[UUID, int] | None,
) -> DedupDecision:
    """Decide what an upload should do given the tenant's prior lookups.

    ``existing`` is the tenant's document with the same content hash (any hit is
    a byte-identical duplicate); ``previous`` is the latest document with the
    same filename (re-uploaded content becomes its next version).
    """
    if existing is not None:
        return DedupDecision("duplicate", existing[1])
    if previous is not None:
        return DedupDecision("new_version", previous[1] + 1)
    return DedupDecision("new", 1)
