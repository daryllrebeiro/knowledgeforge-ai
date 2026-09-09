import zipfile
from io import BytesIO

import pytest
from docx import Document
from pptx import Presentation

from knowledgeforge.ingestion.extract_docx import (
    DOCXExtractionError,
    extract_docx,
)
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.extract_pptx import (
    PPTXExtractionError,
    extract_pptx,
)


def test_extract_markdown_normalizes_text() -> None:
    pages = extract_markdown(BytesIO(b"# Heading\n\nA paragraph."))

    assert any("A paragraph." in text for _, text in pages)


def test_extract_docx_normalizes_paragraphs() -> None:
    document = Document()
    document.add_paragraph("First paragraph")
    document.add_paragraph("Second paragraph")
    output = BytesIO()
    document.save(output)
    output.seek(0)

    assert extract_docx(output) == [(1, "First paragraph"), (2, "Second paragraph")]


def test_extract_docx_rejects_xxe_entity() -> None:
    malicious_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE test [
        <!ENTITY xxe SYSTEM "file:///etc/passwd">
    ]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>&xxe;</w:t></w:r></w:p>
        </w:body>
    </w:document>
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", malicious_xml.encode("utf-8"))
    buf.seek(0)

    with pytest.raises(DOCXExtractionError) as exc_info:
        extract_docx(buf)
    assert "rejected for security" in str(exc_info.value)


def test_extract_docx_rejects_dtd() -> None:
    malicious_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE test [
        <!ELEMENT test ANY >
    ]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body><w:p><w:r><w:t>safe text</w:t></w:r></w:p></w:body>
    </w:document>
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", malicious_xml.encode("utf-8"))
    buf.seek(0)

    with pytest.raises(DOCXExtractionError) as exc_info:
        extract_docx(buf)
    assert "rejected for security" in str(exc_info.value)


def test_extract_docx_rejects_oversized_xml_entry(monkeypatch) -> None:
    from knowledgeforge.ingestion import extract_docx as docx_mod

    monkeypatch.setattr(docx_mod, "MAX_XML_ENTRY_BYTES", 100)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", b"<xml>" + b"A" * 200 + b"</xml>")
    buf.seek(0)

    with pytest.raises(DOCXExtractionError) as exc_info:
        extract_docx(buf)
    assert "exceeds max XML size" in str(exc_info.value)


def test_extract_pptx_normalizes_slides() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "First slide title"
    slide.placeholders[1].text = "Slide subtitle"
    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)

    extracted = extract_pptx(buf)
    assert len(extracted) == 1
    assert extracted[0][0] == 1
    assert "First slide title" in extracted[0][1]
    assert "Slide subtitle" in extracted[0][1]


def test_extract_pptx_rejects_xxe_entity() -> None:
    malicious_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE test [
        <!ENTITY xxe SYSTEM "file:///etc/passwd">
    ]>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:cSld><p:spTree><p:sp><p:txBody><p:p><p:r><p:t>&xxe;</p:t></p:r></p:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", malicious_xml.encode("utf-8"))
    buf.seek(0)

    with pytest.raises(PPTXExtractionError) as exc_info:
        extract_pptx(buf)
    assert "rejected for security" in str(exc_info.value)


def test_extract_pptx_rejects_dtd() -> None:
    malicious_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE test [
        <!ELEMENT test ANY >
    ]>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:cSld><p:spTree><p:sp><p:txBody><p:p><p:r><p:t>safe text</p:t></p:r></p:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", malicious_xml.encode("utf-8"))
    buf.seek(0)

    with pytest.raises(PPTXExtractionError) as exc_info:
        extract_pptx(buf)
    assert "rejected for security" in str(exc_info.value)


def test_extract_pptx_rejects_oversized_xml_entry(monkeypatch) -> None:
    from knowledgeforge.ingestion import extract_pptx as pptx_mod

    monkeypatch.setattr(pptx_mod, "MAX_XML_ENTRY_BYTES", 100)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", b"<xml>" + b"A" * 200 + b"</xml>")
    buf.seek(0)

    with pytest.raises(PPTXExtractionError) as exc_info:
        extract_pptx(buf)
    assert "exceeds max XML size" in str(exc_info.value)


def test_extract_pptx_rejects_zip_bomb_compression_ratio() -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # A block of repeating zeros compresses at ~1000:1 ratio, exceeding max_ratio (100)
        zf.writestr("ppt/slides/slide1.xml", b"0" * 200_000)
    buf.seek(0)

    with pytest.raises(PPTXExtractionError) as exc_info:
        extract_pptx(buf)
    assert "implausible compression ratio" in str(exc_info.value)


def test_extract_pptx_rejects_zip_bomb_total_size(monkeypatch) -> None:
    from knowledgeforge.ingestion import extract_pptx as pptx_mod

    settings = pptx_mod.get_settings().model_copy(update={"max_pptx_decompressed_bytes": 500})
    monkeypatch.setattr(pptx_mod, "get_settings", lambda: settings)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", b"<root>content</root>" + b"A" * 1000)
    buf.seek(0)

    with pytest.raises(PPTXExtractionError) as exc_info:
        extract_pptx(buf)
    assert "exceeds limit" in str(exc_info.value)
