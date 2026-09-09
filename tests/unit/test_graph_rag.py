from knowledgeforge.api import GraphPathResponse, GraphQueryRequest, GraphQueryResponse
from knowledgeforge.extraction.graph_extractor import (
    extract_entities_and_relations,
)
from knowledgeforge.retrieval.graph_traversal import (
    GraphPath,
    format_graph_context_block,
)


def test_extract_entities_and_relations() -> None:
    text = (
        "Acme Technologies Inc owns Beta Cloud Corp. "
        "Beta Cloud Corp supplies DataFlow Systems LLC. "
        "DataFlow Systems LLC executed Vendor Agreement."
    )
    entities, relationships = extract_entities_and_relations(text)

    ent_names = {e.name for e in entities}
    assert "Acme Technologies Inc" in ent_names
    assert "Beta Cloud Corp" in ent_names

    rel_types = {r.relation_type for r in relationships}
    assert "OWNS" in rel_types
    assert "SUPPLIES" in rel_types


def test_graph_path_rendering() -> None:
    path = GraphPath(
        source_name="Alpha Corp",
        relation_type="OWNS",
        target_name="Beta Sub",
        depth=1,
    )
    assert path.render() == "(Alpha Corp) -[:OWNS]-> (Beta Sub)"

    block = format_graph_context_block([path])
    assert "Knowledge Graph Relational Context:" in block
    assert "(Alpha Corp) -[:OWNS]-> (Beta Sub)" in block


def test_graph_query_request_response_models() -> None:
    req = GraphQueryRequest(seed_entities=["Acme Inc"], max_depth=3, limit=10)
    assert req.seed_entities == ["Acme Inc"]
    assert req.max_depth == 3

    resp = GraphQueryResponse(
        paths=[
            GraphPathResponse(
                source_name="Acme Inc",
                relation_type="SUBSIDIARY_OF",
                target_name="Mega Holdings",
                depth=1,
            )
        ]
    )
    assert len(resp.paths) == 1
    assert resp.paths[0].target_name == "Mega Holdings"
