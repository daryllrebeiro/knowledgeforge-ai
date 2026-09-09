"""Natural-language query parser for extraction field filters (Phase 7 Wave 1 Item A).

Translates constrained natural-language filter requests (amount thresholds,
date ranges, field equality, combinations) into the existing allowlisted
JSONB filter parameters.  The output space is bounded to the
``EXTRACTION_FIELD_FILTERS`` allowlist and a fixed set of numeric/date
fields so this inherits the JSONB-path-injection protection already
verified in prior audits.

Where the translation is ambiguous or low-confidence, falls back to
asking a clarifying question rather than guessing a filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class FilterConfidence(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"
    AMBIGUOUS = "AMBIGUOUS"


# Fields that may be matched by equality (allowlisted).
EQUALITY_FIELDS = frozenset({
    "vendor_name",
    "invoice_number",
    "currency",
    "counterparty",
    "governing_law",
})

# Numeric fields that support range comparisons.
NUMERIC_FIELDS = frozenset({"total", "total_value"})

# Date fields that support range comparisons.
DATE_FIELDS = frozenset({
    "invoice_date",
    "due_date",
    "effective_date",
    "termination_date",
})

# Schema type keywords.
_SCHEMA_KEYWORDS: dict[str, str] = {
    "invoice": "invoice",
    "invoices": "invoice",
    "bill": "invoice",
    "bills": "invoice",
    "contract": "contract",
    "contracts": "contract",
    "agreement": "contract",
    "agreements": "contract",
    "nda": "contract",
    "ndas": "contract",
    "sow": "contract",
}


@dataclass(frozen=True)
class NaturalFilterResult:
    """Parsed filter output from a natural-language query."""

    schema_type: str | None = None
    field_filters: dict[str, str] = field(default_factory=dict)
    numeric_ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    date_ranges: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    confidence: FilterConfidence = FilterConfidence.HIGH
    clarification_needed: str | None = None
    applied_filters_summary: dict[str, Any] = field(default_factory=dict)


# ── Amount parsing ──────────────────────────────────────────────────────

_MONEY_RE = re.compile(
    r"\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kKmM])?\b"
)

_MULTIPLIERS = {"k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000}


def _parse_money(text: str) -> float | None:
    """Parse a money string like '$10k', '10,000', '$1.5M' into a float."""
    match = _MONEY_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    value = float(raw)
    suffix = match.group(2)
    if suffix:
        value *= _MULTIPLIERS.get(suffix, 1)
    return value


# ── Quarter / date parsing ──────────────────────────────────────────────

_QUARTER_RE = re.compile(r"\bQ([1-4])\s*(\d{4})\b", re.IGNORECASE)
_YEAR_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+(\d{4})\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_QUARTER_MONTHS = {
    1: (1, 3),
    2: (4, 6),
    3: (7, 9),
    4: (10, 12),
}


def _last_day_of_month(year: int, month: int) -> int:
    """Return the last day of the given month."""
    import calendar
    return calendar.monthrange(year, month)[1]


def _parse_date_range(text: str) -> tuple[str | None, str | None]:
    """Extract a date range from natural language.

    Returns (start_date_iso, end_date_iso) or (None, None).
    """
    # Try quarter: "Q1 2026"
    qm = _QUARTER_RE.search(text)
    if qm:
        quarter = int(qm.group(1))
        year = int(qm.group(2))
        start_month, end_month = _QUARTER_MONTHS[quarter]
        start = f"{year}-{start_month:02d}-01"
        end_day = _last_day_of_month(year, end_month)
        end = f"{year}-{end_month:02d}-{end_day:02d}"
        return start, end

    # Try "March 2026"
    ym = _YEAR_MONTH_RE.search(text)
    if ym:
        month_num = _MONTH_NAMES[ym.group(1).lower()]
        year = int(ym.group(2))
        start = f"{year}-{month_num:02d}-01"
        end_day = _last_day_of_month(year, month_num)
        end = f"{year}-{month_num:02d}-{end_day:02d}"
        return start, end

    # Try "after YYYY-MM-DD" or "before YYYY-MM-DD" or "between ... and ..."
    after_match = re.search(r"\b(?:after|from|since)\s+(\d{4}-\d{2}-\d{2})\b", text, re.IGNORECASE)
    before_match = re.search(r"\b(?:before|until|to)\s+(\d{4}-\d{2}-\d{2})\b", text, re.IGNORECASE)
    between_match = re.search(
        r"\bbetween\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})\b",
        text, re.IGNORECASE,
    )
    if between_match:
        return between_match.group(1), between_match.group(2)

    start_date = after_match.group(1) if after_match else None
    end_date = before_match.group(1) if before_match else None
    if start_date or end_date:
        return start_date, end_date

    return None, None


# ── Vendor / counterparty extraction ────────────────────────────────────

_VENDOR_PATTERNS = [
    re.compile(
        r"\b(?:from|for|vendor|by|to)\s+([A-Z][A-Za-z0-9\s&.,'-]{1,40}?)(?:\s+in\b|\s+for\b|\s+during\b|\s+over\b|\s+under\b|\s+above\b|\s+between\b|\s+from\b|\?|$)",
        re.IGNORECASE,
    ),
]

_STOP_WORDS = frozenset({
    "all", "each", "total", "any", "the", "my", "our", "their",
    "q1", "q2", "q3", "q4", "january", "february", "march", "april",
    "may", "june", "july", "august", "september", "october", "november",
    "december", "this", "last", "next",
})


def _extract_vendor_or_counterparty(
    text: str, schema_type: str | None,
) -> tuple[str | None, str | None]:
    """Return (field_name, value) for vendor/counterparty matching."""
    for pat in _VENDOR_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = m.group(1).strip().rstrip(".,")
            cand_lower = candidate.lower()
            tokens = cand_lower.split()
            if any(t in _STOP_WORDS for t in tokens):
                continue
            if _QUARTER_RE.search(candidate) or _YEAR_MONTH_RE.search(candidate) or _ISO_DATE_RE.search(candidate):
                continue
            if re.match(r"^\d{4}$", candidate):
                continue
            if len(candidate) < 2:
                continue
            if schema_type == "contract":
                return "counterparty", candidate
            return "vendor_name", candidate
    return None, None


# ── Amount range extraction ─────────────────────────────────────────────

_OVER_RE = re.compile(
    r"\b(?:over|above|greater\s+than|more\s+than|exceeding|>=?)\s*"
    r"\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kKmM])?\b",
    re.IGNORECASE,
)
_UNDER_RE = re.compile(
    r"\b(?:under|below|less\s+than|<=?)\s*"
    r"\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kKmM])?\b",
    re.IGNORECASE,
)
_BETWEEN_AMOUNT_RE = re.compile(
    r"\bbetween\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kKmM])?\s*"
    r"and\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kKmM])?\b",
    re.IGNORECASE,
)


def _parse_amount_value(raw: str, suffix: str | None) -> float:
    value = float(raw.replace(",", ""))
    if suffix:
        value *= _MULTIPLIERS.get(suffix, 1)
    return value


def _extract_amount_range(text: str) -> tuple[float | None, float | None]:
    """Extract (min_amount, max_amount) from natural language."""
    between = _BETWEEN_AMOUNT_RE.search(text)
    if between:
        lo = _parse_amount_value(between.group(1), between.group(2))
        hi = _parse_amount_value(between.group(3), between.group(4))
        return lo, hi

    min_val: float | None = None
    max_val: float | None = None

    over = _OVER_RE.search(text)
    if over:
        min_val = _parse_amount_value(over.group(1), over.group(2))

    under = _UNDER_RE.search(text)
    if under:
        max_val = _parse_amount_value(under.group(1), under.group(2))

    return min_val, max_val


# ── Currency extraction ─────────────────────────────────────────────────

_CURRENCY_RE = re.compile(
    r"\b(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR)\b", re.IGNORECASE
)


# ── Governing law extraction ───────────────────────────────────────────

_GOV_LAW_RE = re.compile(
    r"\b(?:governed?\s+by|jurisdiction|governing\s+law)\s+(?:the\s+)?(?:state\s+of\s+)?"
    r"([A-Z][A-Za-z\s]+?)(?:\.|,|\s+and\b|\s+with\b|\?|$)",
    re.IGNORECASE,
)


# ── Main parser ─────────────────────────────────────────────────────────

def parse_natural_filter(query: str) -> NaturalFilterResult:
    """Parse a natural-language filter request into structured filter parameters.

    Constrains output to the existing allowlisted filter parameters.
    Returns a clarifying question when the query is ambiguous or out of scope.
    """
    if not query or not query.strip():
        return NaturalFilterResult(
            confidence=FilterConfidence.AMBIGUOUS,
            clarification_needed=(
                "Please provide a filter query specifying the field you would like "
                "to filter by (e.g., vendor name, amount threshold, date range, or currency)."
            ),
        )

    q = query.strip()
    q_lower = q.lower()

    # Detect schema type
    schema_type: str | None = None
    for keyword, st in _SCHEMA_KEYWORDS.items():
        if keyword in q_lower:
            schema_type = st
            break

    field_filters: dict[str, str] = {}
    numeric_ranges: dict[str, tuple[float | None, float | None]] = {}
    date_ranges: dict[str, tuple[str | None, str | None]] = {}
    summary: dict[str, Any] = {}

    if schema_type:
        summary["schema_type"] = schema_type

    # Extract vendor / counterparty
    vendor_field, vendor_value = _extract_vendor_or_counterparty(q, schema_type)
    if vendor_field and vendor_value:
        field_filters[vendor_field] = vendor_value
        summary[vendor_field] = vendor_value

    # Extract amount range
    amount_min, amount_max = _extract_amount_range(q)
    if amount_min is not None or amount_max is not None:
        amount_field = "total_value" if schema_type == "contract" else "total"
        numeric_ranges[amount_field] = (amount_min, amount_max)
        summary[amount_field] = {
            "min": amount_min,
            "max": amount_max,
        }

    # Extract date range
    date_start, date_end = _parse_date_range(q)
    if date_start is not None or date_end is not None:
        # Pick the most likely date field based on schema type
        if schema_type == "contract":
            date_field = "effective_date"
        else:
            date_field = "invoice_date"
        date_ranges[date_field] = (date_start, date_end)
        summary[date_field] = {"start": date_start, "end": date_end}

    # Extract currency
    currency_match = _CURRENCY_RE.search(q)
    if currency_match:
        field_filters["currency"] = currency_match.group(1).upper()
        summary["currency"] = currency_match.group(1).upper()

    # Extract governing law
    gov_law_match = _GOV_LAW_RE.search(q)
    if gov_law_match:
        field_filters["governing_law"] = gov_law_match.group(1).strip()
        summary["governing_law"] = gov_law_match.group(1).strip()

    # Check if we extracted anything meaningful
    has_filters = bool(field_filters or numeric_ranges or date_ranges or schema_type)

    if not has_filters:
        return NaturalFilterResult(
            confidence=FilterConfidence.AMBIGUOUS,
            clarification_needed=(
                "I couldn't determine a specific filter from your query. "
                "Please clarify the field you would like to filter by, such as: "
                "vendor name, amount threshold (e.g., 'over $10k'), "
                "date range (e.g., 'Q1 2026', 'March 2026'), "
                "currency (e.g., 'EUR'), or governing law."
            ),
        )

    return NaturalFilterResult(
        schema_type=schema_type,
        field_filters=field_filters,
        numeric_ranges=numeric_ranges,
        date_ranges=date_ranges,
        confidence=FilterConfidence.HIGH,
        applied_filters_summary=summary,
    )
