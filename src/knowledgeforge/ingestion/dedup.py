from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DedupDecision:
    action: str
    version: int


def content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


def decide_dedup(
    existing_hash: str | None, existing_version: int | None, incoming_hash: str
) -> DedupDecision:
    if existing_hash is None:
        return DedupDecision("new", 1)
    if existing_hash == incoming_hash:
        return DedupDecision("duplicate", existing_version or 1)
    return DedupDecision("new_version", (existing_version or 1) + 1)
