from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    entity_type: str
    description: str = ""


@dataclass(frozen=True)
class ExtractedRelationship:
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    confidence: float = 1.0


_RELATION_PATTERNS = [
    (
        r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+(?:owns|acquired|purchased)\s+([A-Z][a-zA-Z0-9_\s]{2,25}?)\b",
        "OWNS",
    ),
    (
        r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+is a subsidiary of\s+([A-Z][a-zA-Z0-9_\s]{2,25}?)\b",
        "SUBSIDIARY_OF",
    ),
    (
        r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+(?:supplies|provides services to|vendors for)\s+([A-Z][a-zA-Z0-9_\s]{2,25}?)\b",
        "SUPPLIES",
    ),
    (
        r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+(?:entered into|signed|executed)\s+([A-Z][a-zA-Z0-9_\s]{2,25}?(?:Agreement|Contract|NDA|SLA))\b",
        "EXECUTED_CONTRACT",
    ),
    (
        r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+is governed by\s+([A-Z][a-zA-Z0-9_\s]{2,25}?)\b",
        "GOVERNED_BY",
    ),
    (r"\b([A-Z][a-zA-Z0-9_\s]{2,25}?)\s+located in\s+([A-Z][a-zA-Z0-9_\s]{2,25}?)\b", "LOCATED_IN"),
]


def extract_entities_and_relations(
    text: str,
) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
    """Rule-based and heuristic extraction of entities and relational triples from document text."""
    entities: dict[tuple[str, str], ExtractedEntity] = {}
    relationships: list[ExtractedRelationship] = []

    # Detect entity mentions
    for match in re.finditer(
        r"\b([A-Z][a-zA-Z0-9_]{1,15}(?:\s+[A-Z][a-zA-Z0-9_]{1,15}){0,3})\b", text
    ):
        candidate = match.group(1).strip()
        if candidate.lower() in (
            "this agreement",
            "the company",
            "the vendor",
            "section",
            "article",
            "page",
        ):
            continue
        # Classify entity type
        if any(
            term in candidate for term in ("Inc", "LLC", "Corp", "Ltd", "Company", "Technologies")
        ):
            etype = "ORGANIZATION"
        elif any(
            term in candidate for term in ("Agreement", "Contract", "NDA", "Policy", "License")
        ):
            etype = "DOCUMENT"
        elif any(
            term in candidate
            for term in (
                "City",
                "State",
                "County",
                "USA",
                "Europe",
                "London",
                "New York",
                "Seattle",
            )
        ):
            etype = "LOCATION"
        else:
            etype = "CONCEPT"

        key = (candidate, etype)
        if key not in entities and len(candidate) > 2:
            entities[key] = ExtractedEntity(name=candidate, entity_type=etype)

    # Detect relational triples
    for pattern, rel_type in _RELATION_PATTERNS:
        for match in re.finditer(pattern, text):
            src_name = match.group(1).strip()
            tgt_name = match.group(2).strip()
            if src_name and tgt_name and src_name != tgt_name:
                src_ent = ExtractedEntity(name=src_name, entity_type="ORGANIZATION")
                tgt_ent = ExtractedEntity(name=tgt_name, entity_type="ORGANIZATION")
                entities[(src_name, "ORGANIZATION")] = src_ent
                entities[(tgt_name, "ORGANIZATION")] = tgt_ent
                relationships.append(
                    ExtractedRelationship(
                        source_name=src_name,
                        source_type="ORGANIZATION",
                        target_name=tgt_name,
                        target_type="ORGANIZATION",
                        relation_type=rel_type,
                        confidence=0.9,
                    )
                )

    return list(entities.values()), relationships


def store_graph_triples(
    connection: Connection,
    *,
    tenant_id: UUID,
    entities: list[ExtractedEntity],
    relationships: list[ExtractedRelationship],
    source_doc_id: UUID | None = None,
) -> tuple[int, int]:
    """Persists entities and relationships into PostgreSQL graph tables."""
    entity_id_map: dict[tuple[str, str], UUID] = {}

    with connection.cursor() as cursor:
        # Upsert entities
        for ent in entities:
            cursor.execute(
                """
                INSERT INTO graph_entities (tenant_id, name, entity_type, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, name, entity_type)
                DO UPDATE SET description = EXCLUDED.description
                RETURNING id
                """,
                (tenant_id, ent.name, ent.entity_type, ent.description),
            )
            row = cursor.fetchone()
            if row is not None:
                entity_id_map[(ent.name, ent.entity_type)] = UUID(str(row[0]))

        # Insert relationships
        inserted_rels = 0
        for rel in relationships:
            src_id = entity_id_map.get((rel.source_name, rel.source_type))
            tgt_id = entity_id_map.get((rel.target_name, rel.target_type))
            if src_id and tgt_id and src_id != tgt_id:
                cursor.execute(
                    """
                    INSERT INTO graph_relationships (
                        tenant_id, source_entity_id, target_entity_id, relation_type, source_doc_id, confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (tenant_id, src_id, tgt_id, rel.relation_type, source_doc_id, rel.confidence),
                )
                inserted_rels += 1

    return len(entity_id_map), inserted_rels
