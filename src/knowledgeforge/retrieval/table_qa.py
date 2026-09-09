from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import Connection


class QueryIntent(StrEnum):
    NARRATIVE_RAG = "NARRATIVE_RAG"
    STRUCTURED_AGGREGATE = "STRUCTURED_AGGREGATE"
    HYBRID = "HYBRID"


_QUANTITATIVE_PATTERNS = [
    r"\b(?:total|sum|aggregate|combined)\b",
    r"\b(?:average|mean|avg)\b",
    r"\b(?:count|how many|number of)\b",
    r"\b(?:highest|maximum|max|most expensive)\b",
    r"\b(?:lowest|minimum|min|least expensive)\b",
    r"\b(?:total spend|total cost|total expenditure)\b",
]

_DISALLOWED_SQL = re.compile(
    r"\b(?:insert|update|delete|drop|truncate|alter|create|grant|revoke|commit|rollback)\b",
    re.IGNORECASE,
)


def classify_query_intent(question: str) -> QueryIntent:
    """Classifies user question into narrative, structured aggregate, or hybrid."""
    q_lower = question.lower()
    has_quant = any(re.search(pat, q_lower) for pat in _QUANTITATIVE_PATTERNS)
    has_invoice_or_contract = any(
        w in q_lower
        for w in (
            "invoice",
            "invoices",
            "vendor",
            "vendors",
            "spend",
            "cost",
            "contract",
            "amount",
            "paid",
            "fee",
        )
    )

    if has_quant and has_invoice_or_contract:
        if any(w in q_lower for w in ("why", "explain", "describe", "clause", "policy")):
            return QueryIntent.HYBRID
        return QueryIntent.STRUCTURED_AGGREGATE
    return QueryIntent.NARRATIVE_RAG


@dataclass(frozen=True)
class TableQAResult:
    query_intent: QueryIntent
    sql_executed: str | None
    data: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    is_applicable: bool = True


class TableQASynthesizer:
    """Translates analytical natural language intents into safe, parameterized SQL over structured JSONB extractions."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def build_aggregation_query(self, question: str) -> tuple[str, tuple[Any, ...], str]:
        """Constructs a parameterized read-only aggregate query over document_extractions."""
        q_lower = question.lower()
        params: list[Any] = [self.tenant_id]

        # Check vendor filtering
        vendor_match = re.search(
            r"(?:from|for|vendor|to)\s+([a-zA-Z0-9_\s]{2,20}?)(?:\s+in\b|\s+for\b|\s+during\b|\?|$)",
            question,
            re.IGNORECASE,
        )
        vendor_filter = ""
        target_vendor = None
        if vendor_match:
            candidate = vendor_match.group(1).strip()
            if candidate.lower() not in ("all", "each", "total", "any", "the", "my"):
                target_vendor = candidate
                vendor_filter = (
                    "AND (fields->>'vendor_name' ILIKE %s OR fields->>'vendor' ILIKE %s)"
                )
                params.extend([f"%{target_vendor}%", f"%{target_vendor}%"])

        # Decide operation
        if "average" in q_lower or "avg" in q_lower or "mean" in q_lower:
            op_name = "average"
            sql = f"""
                SELECT
                    COALESCE(AVG(NULLIF(regexp_replace(fields->>'total_amount', '[^0-9.]', '', 'g'), '')::numeric), 0),
                    COUNT(*)
                FROM document_extractions
                WHERE tenant_id = %s {vendor_filter}
            """
        elif "count" in q_lower or "how many" in q_lower or "number of" in q_lower:
            op_name = "count"
            sql = f"""
                SELECT
                    COUNT(*),
                    COALESCE(SUM(NULLIF(regexp_replace(fields->>'total_amount', '[^0-9.]', '', 'g'), '')::numeric), 0)
                FROM document_extractions
                WHERE tenant_id = %s {vendor_filter}
            """
        elif "max" in q_lower or "highest" in q_lower or "most expensive" in q_lower:
            op_name = "maximum"
            sql = f"""
                SELECT
                    COALESCE(MAX(NULLIF(regexp_replace(fields->>'total_amount', '[^0-9.]', '', 'g'), '')::numeric), 0),
                    COUNT(*)
                FROM document_extractions
                WHERE tenant_id = %s {vendor_filter}
            """
        elif "min" in q_lower or "lowest" in q_lower or "least expensive" in q_lower:
            op_name = "minimum"
            sql = f"""
                SELECT
                    COALESCE(MIN(NULLIF(regexp_replace(fields->>'total_amount', '[^0-9.]', '', 'g'), '')::numeric), 0),
                    COUNT(*)
                FROM document_extractions
                WHERE tenant_id = %s {vendor_filter}
            """
        else:
            op_name = "total"
            sql = f"""
                SELECT
                    COALESCE(SUM(NULLIF(regexp_replace(fields->>'total_amount', '[^0-9.]', '', 'g'), '')::numeric), 0),
                    COUNT(*)
                FROM document_extractions
                WHERE tenant_id = %s {vendor_filter}
            """

        return sql.strip(), tuple(params), op_name

    def execute(self, connection: Connection, question: str) -> TableQAResult:
        """Safely executes the aggregation query and returns structured analytical results."""
        if _DISALLOWED_SQL.search(question):
            raise ValueError("Disallowed SQL operation in TableQA synthesizer")

        intent = classify_query_intent(question)
        sql, params, op_name = self.build_aggregation_query(question)

        # Security check: forbid non-SELECT statements
        if _DISALLOWED_SQL.search(sql):
            raise ValueError("Disallowed SQL operation in TableQA synthesizer")

        if intent == QueryIntent.NARRATIVE_RAG:
            return TableQAResult(
                query_intent=intent, sql_executed=None, data={}, answer="", is_applicable=False
            )

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()

        if row is None:
            return TableQAResult(
                query_intent=intent,
                sql_executed=sql,
                data={"result": 0, "count": 0},
                answer="No matching records found in structured document extractions.",
            )

        primary_val = float(row[0]) if row[0] is not None else 0.0
        record_count = int(row[1]) if len(row) > 1 and row[1] is not None else 0

        data = {
            "operation": op_name,
            "metric_value": primary_val,
            "record_count": record_count,
        }

        if op_name == "count":
            answer = f"Found {int(primary_val)} matching structured documents in the system."
        elif op_name == "average":
            answer = f"The average amount across {record_count} matching documents is ${primary_val:,.2f}."
        elif op_name in ("maximum", "minimum"):
            answer = f"The {op_name} amount across {record_count} matching documents is ${primary_val:,.2f}."
        else:
            answer = f"The total aggregated amount across {record_count} matching documents is ${primary_val:,.2f}."

        return TableQAResult(
            query_intent=intent,
            sql_executed=sql,
            data=data,
            answer=answer,
            is_applicable=True,
        )
