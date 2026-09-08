import zipfile
from typing import BinaryIO

from defusedxml import ElementTree as DefusedET
from defusedxml.ElementTree import iterparse as DefusedIterparse
from docx import Document


class DOCXExtractionError(Exception):
    """Raised when DOCX extraction fails a guard check."""


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
        raise DOCXExtractionError(
            f"DOCX entry {filename} contains entity references; rejected for security"
        ) from exc
    except DefusedET.DTDForbidden as exc:
        raise DOCXExtractionError(
            f"DOCX entry {filename} contains DTD; rejected for security"
        ) from exc
    except DefusedET.ExternalReferenceForbidden as exc:
        raise DOCXExtractionError(
            f"DOCX entry {filename} contains external references; rejected for security"
        ) from exc
    except (DefusedET.ParseError, SyntaxError):
        # Malformed XML that isn't an attack — let python-docx handle it
        pass


def extract_docx(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract non-empty DOCX paragraphs as location/text pairs.

    Validates all XML entries in the OOXML package with defusedxml
    to prevent XXE and billion-laughs attacks before handing off to python-docx.
    """
    # Validate zip structure and XML entries (streaming, size-limited)
    try:
        with zipfile.ZipFile(file) as zf:
            for info in zf.infolist():
                if info.filename.endswith(".xml") or info.filename.endswith(".rels"):
                    if info.file_size > MAX_XML_ENTRY_BYTES:
                        raise DOCXExtractionError(
                            f"DOCX entry {info.filename} exceeds max XML size "
                            f"({info.file_size} > {MAX_XML_ENTRY_BYTES} bytes)"
                        )
                    with zf.open(info) as entry_file:
                        _validate_xml_entry_streaming(entry_file, info.filename)
    except zipfile.BadZipFile as exc:
        raise DOCXExtractionError("Invalid DOCX file (not a valid ZIP)") from exc

    # Reset file position and extract
    file.seek(0)
    document = Document(file)
    paragraphs = [
        paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    return [(index, text) for index, text in enumerate(paragraphs, start=1)]