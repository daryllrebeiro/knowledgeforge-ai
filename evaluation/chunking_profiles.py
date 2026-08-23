from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkingProfile:
    name: str
    chunk_size: int
    overlap: int
    section_aware: bool = False


PROFILES = (
    ChunkingProfile("baseline-500-100", 500, 100),
    ChunkingProfile("large-800-150", 800, 150),
    ChunkingProfile("section-aware-500-100", 500, 100, section_aware=True),
)
