from uuid import uuid4

import pytest

from knowledgeforge.retrieval.table_qa import (
    QueryIntent,
    TableQASynthesizer,
    classify_query_intent,
)


def test_classify_query_intent() -> None:
    # Narrative questions
    assert (
        classify_query_intent("What is the termination clause in the contract?")
        == QueryIntent.NARRATIVE_RAG
    )
    assert classify_query_intent("Who is the primary contact person?") == QueryIntent.NARRATIVE_RAG

    # Structured aggregate questions
    assert (
        classify_query_intent("What is our total spend on cloud vendor invoices?")
        == QueryIntent.STRUCTURED_AGGREGATE
    )
    assert (
        classify_query_intent("What is the average amount paid across all invoices?")
        == QueryIntent.STRUCTURED_AGGREGATE
    )
    assert (
        classify_query_intent("How many vendor invoices were processed?")
        == QueryIntent.STRUCTURED_AGGREGATE
    )

    # Hybrid questions
    assert (
        classify_query_intent("Explain why the total invoice spend increased in Q3?")
        == QueryIntent.HYBRID
    )


def test_build_aggregation_query_structure_and_isolation() -> None:
    tenant_id = uuid4()
    synthesizer = TableQASynthesizer(tenant_id)

    # Test SUM aggregation
    sql, params, op = synthesizer.build_aggregation_query(
        "What is the total spend on Acme invoices?"
    )
    assert "SUM(" in sql
    assert "WHERE tenant_id = %s" in sql
    assert params[0] == tenant_id
    assert op == "total"

    # Test AVG aggregation
    sql_avg, params_avg, op_avg = synthesizer.build_aggregation_query(
        "What is the average invoice amount?"
    )
    assert "AVG(" in sql_avg
    assert params_avg[0] == tenant_id
    assert op_avg == "average"

    # Test COUNT aggregation
    sql_count, params_count, op_count = synthesizer.build_aggregation_query(
        "How many invoices do we have?"
    )
    assert "COUNT(*)" in sql_count
    assert params_count[0] == tenant_id
    assert op_count == "count"


def test_table_qa_blocks_disallowed_sql() -> None:
    tenant_id = uuid4()
    synthesizer = TableQASynthesizer(tenant_id)

    # Overridden malicious query string
    class FakeConn:
        def cursor(self):
            raise RuntimeError("Should not be reached")

    synthesizer.build_aggregation_query = lambda q: (
        "DROP TABLE document_extractions;",
        (),
        "malicious",
    )  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Disallowed SQL"):
        synthesizer.execute(FakeConn(), "drop tables")
