"""Pydantic schemas for structured document extraction (Phase 2.5)."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None

    @field_validator("quantity", "unit_price", "amount")
    @classmethod
    def _not_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("line-item values must be non-negative")
        return value


class InvoiceExtraction(BaseModel):
    """The invoice field set for this phase.

    ``total`` is deliberately non-negative: credit notes and refunds are a
    separate document schema, out of scope until invoice accuracy is proven.
    """

    vendor_name: str = Field(min_length=1, max_length=300)
    invoice_number: str | None = Field(default=None, max_length=200)
    invoice_date: date | None = None
    due_date: date | None = None
    total: float = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    line_items: list[LineItem] = []


class ExtractionWithConfidence(BaseModel):
    """What the provider is asked to return: fields plus per-field confidence.

    Confidence rides in the same structured-output call as the fields so the
    common case costs one call, not two.
    """

    invoice: InvoiceExtraction
    field_confidence: dict[str, float] = Field(default_factory=dict)


def render_fields(fields: dict[str, Any]) -> str:
    """Render extracted fields as ``key: value`` lines for the ask prompt."""
    lines: list[str] = []
    for key, value in fields.items():
        if key == "line_items":
            if not value:
                continue
            lines.append("line_items:")
            for item in value:
                parts = [str(item.get("description", ""))]
                if item.get("quantity") is not None:
                    parts.append(f"qty={item['quantity']}")
                if item.get("unit_price") is not None:
                    parts.append(f"unit_price={item['unit_price']}")
                if item.get("amount") is not None:
                    parts.append(f"amount={item['amount']}")
                lines.append("  - " + " ".join(parts))
        elif value is not None:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)
