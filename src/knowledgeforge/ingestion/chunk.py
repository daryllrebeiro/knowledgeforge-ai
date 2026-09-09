import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextChunk:
    text: str
    page: int
    section: str | None = None
    bounding_boxes: tuple[dict[str, Any], ...] = ()
    modality: str = "text"
    image_storage_uri: str | None = None
    start_char: int | None = None
    end_char: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.bounding_boxes, list):
            object.__setattr__(self, "bounding_boxes", tuple(self.bounding_boxes))


def chunk_pages(
    pages: list[tuple[int, str]] | list[tuple[int, str, list[dict[str, Any]]]],
    *,
    chunk_size: int = 500,
    overlap: int = 100,
    section_aware: bool = False,
) -> list[TextChunk]:
    """Split page text into deterministic whitespace-token chunks with character offsets.

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
    for page_item in pages:
        if len(page_item) == 3:
            page, text, page_boxes = page_item  # type: ignore[misc]
        else:
            page, text = page_item  # type: ignore[misc]
            page_boxes = []

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
            token_spans = [
                (m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", section_text)
            ]
            if not token_spans:
                continue
            sec_offset = 0
            if section_aware and text:
                find_pos = text.find(section_text)
                if find_pos != -1:
                    sec_offset = find_pos

            for start in range(0, len(token_spans), step):
                window = token_spans[start : start + chunk_size]
                if not window:
                    continue
                start_char = sec_offset + window[0][0]
                end_char = sec_offset + window[-1][1]
                # If page-level bounding boxes are supplied, associate relevant boxes
                chunk_boxes = tuple(page_boxes) if page_boxes else ()
                chunks.append(
                    TextChunk(
                        text=" ".join(w[2] for w in window),
                        page=page,
                        section=section,
                        bounding_boxes=chunk_boxes,
                        start_char=start_char,
                        end_char=end_char,
                    )
                )
                if start + chunk_size >= len(token_spans):
                    break
    return chunks
