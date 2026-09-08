"""Document classification: cheap keyword pre-filter, Gemini fallback."""

import re
from dataclasses import dataclass

# Strong invoice indicators; two or more in the excerpt is a confident hit.
_INVOICE_KEYWORDS = (
    "invoice",
    "invoice number",
    "invoice date",
    "total due",
    "amount due",
    "bill to",
    "payment terms",
    "remit to",
)

# Strong contract/agreement indicators
_CONTRACT_KEYWORDS = (
    "agreement",
    "contract",
    "terms and conditions",
    "counterparty",
    "parties hereto",
    "effective date",
    "termination date",
    "governing law",
    "indemnification",
    "confidentiality agreement",
    "master services agreement",
    "statement of work",
    "non-disclosure",
)

# Documents longer than this are not invoices in practice (invoices are 1-3
# pages); paying to classify them is the cost the pre-filter exists to avoid.
_LONG_DOCUMENT_CHARS = 15_000

_EXCERPT_CHARS = 2_000


@dataclass(frozen=True)
class Classification:
    doc_type: str  # 'invoice', 'contract', or 'unclassified'
    confidence: float
    used_provider: bool = False


def classify_locally(filename: str, text: str) -> Classification | None:
    """Return a confident local verdict, or None when Gemini should classify.

    - filename mentions an invoice -> invoice (0.95)
    - filename mentions contract/agreement/msa/sow/nda -> contract (0.95)
    - two or more invoice keywords in the excerpt -> invoice (0.9)
    - two or more contract keywords in the excerpt -> contract (0.9)
    - obviously-too-long document without contract keywords -> unclassified (0.9): skip the paid call
    """
    lowered = (filename or "").lower()
    if "invoice" in lowered:
        return Classification(doc_type="invoice", confidence=0.95)
    if any(k in lowered for k in ("contract", "agreement", "msa", "sow", "nda")):
        return Classification(doc_type="contract", confidence=0.95)

    excerpt = text[:_EXCERPT_CHARS].lower()
    invoice_hits = sum(1 for keyword in _INVOICE_KEYWORDS if keyword in excerpt)
    contract_hits = sum(1 for keyword in _CONTRACT_KEYWORDS if keyword in excerpt)

    if invoice_hits >= 2 and invoice_hits > contract_hits:
        return Classification(doc_type="invoice", confidence=0.9)
    if contract_hits >= 2:
        return Classification(doc_type="contract", confidence=0.9)
    if len(text) > _LONG_DOCUMENT_CHARS and contract_hits == 0:
        return Classification(doc_type="unclassified", confidence=0.9)
    return None


def looks_like_invoice_text(text: str) -> bool:
    """Loose single-keyword check used on OCR output before extraction."""
    excerpt = text[:_EXCERPT_CHARS].lower()
    return any(keyword in excerpt for keyword in _INVOICE_KEYWORDS)


def looks_like_contract_text(text: str) -> bool:
    """Loose check for contract keywords on document text."""
    excerpt = text[:_EXCERPT_CHARS].lower()
    return any(keyword in excerpt for keyword in _CONTRACT_KEYWORDS)


class ClassificationParseError(ValueError):
    pass


_CLASSIFICATION_PATTERN = re.compile(
    r'\{\s*"doc_type"\s*:\s*"(?P<doc_type>[a-z]+)"\s*,\s*'
    r'"confidence"\s*:\s*(?P<confidence>[01](?:\.\d+)?)\s*\}'
)


def parse_classification(text: str) -> Classification:
    """Parse the provider's classification JSON ({"doc_type", "confidence"})."""
    match = _CLASSIFICATION_PATTERN.search(text)
    if match is None:
        raise ClassificationParseError(f"unparseable classification output: {text[:200]!r}")
    doc_type = match.group("doc_type")
    if doc_type not in {"invoice", "contract", "unclassified"}:
        raise ClassificationParseError(f"unknown doc_type: {doc_type}")
    return Classification(doc_type=doc_type, confidence=float(match.group("confidence")))
