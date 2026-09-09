from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from pypdf import PdfReader


@dataclass(frozen=True)
class VisualChunk:
    page: int
    caption: str
    image_bytes: bytes
    bounding_box: tuple[float, float, float, float]
    format: str = "png"
    image_storage_uri: str | None = None


def extract_visual_regions(file: BinaryIO) -> list[VisualChunk]:
    """Extract embedded images, figures, and charts from PDF pages as VisualChunks."""
    reader = PdfReader(file)
    visual_chunks: list[VisualChunk] = []

    for page_number, page in enumerate(reader.pages, start=1):
        # Scan for embedded image objects in the page resources
        images = getattr(page, "images", [])

        for img_idx, img in enumerate(images):
            try:
                img_data = img.data
                img_name = getattr(img, "name", f"image_{img_idx}")
                # Estimate normalized placement or default to centered region
                norm_box = (0.1, 0.2, 0.9, 0.8)
                caption = f"Visual diagram / chart figure '{img_name}' on page {page_number}"
                visual_chunks.append(
                    VisualChunk(
                        page=page_number,
                        caption=caption,
                        image_bytes=img_data,
                        bounding_box=norm_box,
                        format="png",
                    )
                )
            except Exception:
                continue

    return visual_chunks


def format_visual_context_block(visuals: list[VisualChunk]) -> str:
    """Renders visual chunks as descriptive multimodal context blocks for prompts."""
    blocks: list[str] = []
    for idx, v in enumerate(visuals, start=1):
        blocks.append(
            f"[Visual Element {idx}, page {v.page}]: {v.caption} "
            f"(bounding box: {[round(c, 2) for c in v.bounding_box]})"
        )
    return "\n\n".join(blocks)
