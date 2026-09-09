import io

from pypdf import PdfWriter

from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.ingestion.extract_visuals import (
    VisualChunk,
    extract_visual_regions,
    format_visual_context_block,
)


def test_visual_chunk_dataclass() -> None:
    chunk = VisualChunk(
        page=1,
        caption="System Architecture Diagram",
        image_bytes=b"\x89PNG\r\n\x1a\nfakeimagebytes",
        bounding_box=(0.1, 0.2, 0.9, 0.8),
        format="png",
        image_storage_uri="gs://bucket/tenant/diagram1.png",
    )
    assert chunk.page == 1
    assert chunk.caption == "System Architecture Diagram"
    assert chunk.format == "png"
    assert chunk.image_storage_uri == "gs://bucket/tenant/diagram1.png"


def test_text_chunk_modality() -> None:
    chunk = TextChunk(
        text="[Chart: Monthly Revenue Breakdown]",
        page=2,
        modality="image",
        image_storage_uri="gs://bucket/tenant/chart.webp",
    )
    assert chunk.modality == "image"
    assert chunk.image_storage_uri == "gs://bucket/tenant/chart.webp"


def test_extract_visual_regions_blank_pdf() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    visuals = extract_visual_regions(buf)
    assert isinstance(visuals, list)
    assert len(visuals) == 0


def test_format_visual_context_block() -> None:
    visuals = [
        VisualChunk(
            page=3,
            caption="AWS Infrastructure Network Topology",
            image_bytes=b"bytes",
            bounding_box=(0.05, 0.15, 0.95, 0.85),
        )
    ]
    block = format_visual_context_block(visuals)
    assert "Visual Element 1, page 3" in block
    assert "AWS Infrastructure Network Topology" in block
