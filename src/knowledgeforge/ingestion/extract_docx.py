from typing import BinaryIO

from docx import Document


def extract_docx(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract non-empty DOCX paragraphs as location/text pairs."""
    document = Document(file)
    paragraphs = [
        paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    return [(index, text) for index, text in enumerate(paragraphs, start=1)]
