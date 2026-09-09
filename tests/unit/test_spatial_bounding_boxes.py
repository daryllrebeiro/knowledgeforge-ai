import io
from uuid import uuid4

from pypdf import PdfWriter

from knowledgeforge.api import _citations_for
from knowledgeforge.generation.generate import Citation
from knowledgeforge.ingestion.chunk import TextChunk, chunk_pages
from knowledgeforge.ingestion.extract import extract_pdf_with_boxes


def test_text_chunk_bounding_boxes_default_and_custom() -> None:
    chunk_default = TextChunk(text="Sample clause", page=1)
    assert chunk_default.bounding_boxes == ()

    box = {"page": 1, "box": [0.1, 0.2, 0.8, 0.3], "text_snippet": "Sample clause"}
    chunk_with_boxes = TextChunk(text="Sample clause", page=1, bounding_boxes=[box])
    assert len(chunk_with_boxes.bounding_boxes) == 1
    assert chunk_with_boxes.bounding_boxes[0]["box"] == [0.1, 0.2, 0.8, 0.3]


def test_chunk_pages_propagates_bounding_boxes() -> None:
    box1 = {"page": 2, "box": [0.05, 0.1, 0.95, 0.2], "text_snippet": "First section"}
    pages = [
        (2, "word1 word2 word3 word4", [box1]),
    ]
    chunks = chunk_pages(pages, chunk_size=3, overlap=1)
    assert len(chunks) == 2
    for c in chunks:
        assert c.page == 2
        assert len(c.bounding_boxes) == 1
        assert c.bounding_boxes[0]["box"] == [0.05, 0.1, 0.95, 0.2]


def test_extract_pdf_with_boxes_smoke() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    pages = extract_pdf_with_boxes(buf)
    # Blank PDF has no text
    assert isinstance(pages, list)


def test_citations_for_attaches_spatial_highlights() -> None:
    doc_id = uuid4()
    box = {"page": 3, "box": [0.15, 0.25, 0.75, 0.35], "text_snippet": "Grounding fact"}
    chunk = TextChunk(text="Grounding fact in doc", page=3, bounding_boxes=[box])
    retrieved = [(uuid4(), doc_id, chunk)]

    parsed = [Citation(document_index=1, page=3)]
    document_numbers = {doc_id: 1}

    citations = _citations_for(parsed, document_numbers, retrieved)
    assert len(citations) == 1
    citation = citations[0]
    assert citation.document_id == doc_id
    assert citation.page == 3
    assert len(citation.highlights) == 1
    highlight = citation.highlights[0]
    assert highlight.page == 3
    assert highlight.box == [0.15, 0.25, 0.75, 0.35]
    assert highlight.text_snippet == "Grounding fact"
