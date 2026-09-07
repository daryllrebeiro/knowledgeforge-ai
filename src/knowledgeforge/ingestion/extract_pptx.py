import zipfile
from typing import BinaryIO

from pptx import Presentation

from knowledgeforge.config import get_settings


class PPTXExtractionError(Exception):
    """Raised when PPTX extraction fails a guard check."""


def _scan_xml_for_entity_declarations(xml_bytes: bytes) -> bool:
    """Scan XML content for DOCTYPE or ENTITY declarations that could trigger XXE or billion-laughs.

    Returns True if suspicious declarations are found.
    """
    # Check for DOCTYPE or ENTITY declarations (case-insensitive)
    # Legitimate PPTX files from PowerPoint/Google Slides never contain these.
    upper = xml_bytes.upper()
    return b"<!DOCTYPE" in upper or b"<!ENTITY" in upper


def extract_pptx(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract text from PPTX slides as location/text pairs.

    Guards against zip bombs by checking total uncompressed size and
    per-entry compression ratio before handing off to python-pptx.
    Also pre-scans XML entries for DOCTYPE/ENTITY declarations to prevent
    XXE and billion-laughs attacks (lxml entity resolution is enabled by default).
    """
    settings = get_settings()
    max_total = settings.max_pptx_decompressed_bytes
    max_ratio = 100  # reject entries with decompression ratio > 100:1

    # Check zip structure before full decompression
    try:
        with zipfile.ZipFile(file) as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                total_uncompressed += info.file_size
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > max_ratio:
                        raise PPTXExtractionError(
                            f"PPTX entry {info.filename} has implausible compression ratio "
                            f"{ratio:.0f}:1 (max {max_ratio}:1)"
                        )
                # Pre-scan XML entries for entity declarations
                if info.filename.endswith(".xml") or info.filename.endswith(".rels"):
                    # Read the entry content for scanning
                    with zf.open(info) as entry_file:
                        content = entry_file.read()
                        if _scan_xml_for_entity_declarations(content):
                            raise PPTXExtractionError(
                                f"PPTX entry {info.filename} contains DOCTYPE or ENTITY declaration; rejected for security"
                            )
            if total_uncompressed > max_total:
                raise PPTXExtractionError(
                    f"PPTX total uncompressed size {total_uncompressed} bytes exceeds limit {max_total}"
                )
    except zipfile.BadZipFile as exc:
        raise PPTXExtractionError("Invalid PPTX file (not a valid ZIP)") from exc

    # Reset file position and extract
    file.seek(0)
    presentation = Presentation(file)
    slides_text = []
    for index, slide in enumerate(presentation.slides, start=1):
        slide_texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        slide_texts.append(text)
        if slide_texts:
            slides_text.append((index, "\n".join(slide_texts)))
    return slides_text