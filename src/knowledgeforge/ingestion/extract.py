from typing import Any, BinaryIO

from pypdf import PdfReader


def extract_pdf(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract non-empty page text while preserving the one-based page number."""
    reader = PdfReader(file)
    pages: list[tuple[int, str]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((page_number, text))
    return pages


def extract_pdf_with_boxes(file: BinaryIO) -> list[tuple[int, str, list[dict[str, Any]]]]:
    """Extract page text alongside normalized spatial bounding boxes [x0, y0, x1, y1]."""
    reader = PdfReader(file)
    pages: list[tuple[int, str, list[dict[str, Any]]]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        boxes: list[dict[str, Any]] = []
        mediabox = page.mediabox
        width = float(mediabox.width) if mediabox else 612.0
        height = float(mediabox.height) if mediabox else 792.0

        def visitor(
            text: str,
            cm: Any,
            tm: Any,
            font_dict: Any,
            font_size: float | None,
            _w: float = width,
            _h: float = height,
            _p_num: int = page_number,
            _boxes: list[dict[str, Any]] = boxes,
        ) -> None:
            stripped = text.strip()
            if not stripped:
                return
            x0 = tm[4] if len(tm) > 4 else 0.0
            y0 = tm[5] if len(tm) > 5 else 0.0
            fs = font_size or 10.0
            x1 = x0 + len(stripped) * fs * 0.5
            y1 = y0 + fs
            # Normalize to [0.0, 1.0] relative to mediabox
            norm_x0 = max(0.0, min(1.0, x0 / _w)) if _w else 0.0
            norm_y0 = max(0.0, min(1.0, y0 / _h)) if _h else 0.0
            norm_x1 = max(0.0, min(1.0, x1 / _w)) if _w else 1.0
            norm_y1 = max(0.0, min(1.0, y1 / _h)) if _h else 1.0
            _boxes.append(
                {
                    "page": _p_num,
                    "box": [
                        round(norm_x0, 4),
                        round(norm_y0, 4),
                        round(norm_x1, 4),
                        round(norm_y1, 4),
                    ],
                    "text_snippet": stripped[:100],
                }
            )

        text = (page.extract_text(visitor_text=visitor) or "").strip()
        if text:
            pages.append((page_number, text, boxes))
    return pages
