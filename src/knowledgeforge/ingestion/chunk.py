from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    text: str
    page: int
    section: str | None = None


def chunk_pages(
    pages: list[tuple[int, str]],
    *,
    chunk_size: int = 500,
    overlap: int = 100,
    section_aware: bool = False,
) -> list[TextChunk]:
    """Split page text into deterministic whitespace-token chunks.

    Keeping chunks within their source page makes citations precise. The tokenization
    is intentionally dependency-free for Phase 1; it can be replaced by the selected
    model tokenizer once retrieval evaluation is in place.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size - 1")

    chunks: list[TextChunk] = []
    step = chunk_size - overlap
    for page, text in pages:
        section: str | None = None
        if section_aware:
            sections: list[tuple[str | None, str]] = []
            current_lines: list[str] = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    if current_lines:
                        sections.append((section, " ".join(current_lines)))
                        current_lines = []
                    section = stripped.lstrip("# ").strip() or section
                elif stripped:
                    current_lines.append(stripped)
            if current_lines:
                sections.append((section, " ".join(current_lines)))
        else:
            sections = [(None, text)]

        for section, section_text in sections:
            tokens = section_text.split()
            for start in range(0, len(tokens), step):
                window = tokens[start : start + chunk_size]
                if not window:
                    continue
                chunks.append(TextChunk(text=" ".join(window), page=page, section=section))
                if start + chunk_size >= len(tokens):
                    break
    return chunks
