import zipfile
from typing import BinaryIO

from defusedxml import ElementTree as DefusedET
from defusedxml.ElementTree import iterparse as DefusedIterparse
from pptx import Presentation

from knowledgeforge.config import get_settings


class PPTXExtractionError(Exception):
    """Raised when PPTX extraction fails a guard check."""


# Maximum size for a single XML entry (10 MB) to prevent memory exhaustion
MAX_XML_ENTRY_BYTES = 10_000_000


def _validate_xml_entry_streaming(entry_file: BinaryIO, filename: str) -> None:
    """Validate XML entry using defusedxml's iterparse for streaming validation.

    This avoids loading the entire XML into memory while still enforcing
    XXE/ENTITY/DTD restrictions.
    """
    try:
        # iterparse still enforces entity/DTD restrictions; we just iterate to trigger validation
        for _event, _elem in DefusedIterparse(entry_file, events=("start",), forbid_dtd=True):
            pass
    except DefusedET.EntitiesForbidden as exc:
        raise PPTXExtractionError(
            f"PPTX entry {filename} contains entity references; rejected for security"
        ) from exc
    except DefusedET.DTDForbidden as exc:
        raise PPTXExtractionError(
            f"PPTX entry {filename} contains DTD; rejected for security"
        ) from exc
    except DefusedET.ExternalReferenceForbidden as exc:
        raise PPTXExtractionError(
            f"PPTX entry {filename} contains external references; rejected for security"
        ) from exc
    except (DefusedET.ParseError, SyntaxError):
        # Malformed XML that isn't an attack — let python-pptx handle it
        pass


def extract_pptx(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract text from PPTX slides as location/text pairs.

    Guards against zip bombs by checking total uncompressed size and
    per-entry compression ratio before handing off to python-pptx.
    Also validates all XML entries with defusedxml to prevent
    XXE and billion-laughs attacks.
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
                if total_uncompressed > max_total:
                    raise PPTXExtractionError(
                        f"PPTX total uncompressed size {total_uncompressed} bytes exceeds limit {max_total}"
                    )
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > max_ratio:
                        raise PPTXExtractionError(
                            f"PPTX entry {info.filename} has implausible compression ratio "
                            f"{ratio:.0f}:1 (max {max_ratio}:1)"
                        )
                # Validate XML entries with defusedxml (streaming, size-limited)
                if info.filename.endswith(".xml") or info.filename.endswith(".rels"):
                    if info.file_size > MAX_XML_ENTRY_BYTES:
                        raise PPTXExtractionError(
                            f"PPTX entry {info.filename} exceeds max XML size "
                            f"({info.file_size} > {MAX_XML_ENTRY_BYTES} bytes)"
                        )
                    with zf.open(info) as entry_file:
                        _validate_xml_entry_streaming(entry_file, info.filename)
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
