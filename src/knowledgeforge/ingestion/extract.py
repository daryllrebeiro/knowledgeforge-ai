from typing import BinaryIO

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
