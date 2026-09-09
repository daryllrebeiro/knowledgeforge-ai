from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection


@dataclass(frozen=True)
class GraphPath:
    source_name: str
    relation_type: str
    target_name: str
    depth: int

    def render(self) -> str:
        return f"({self.source_name}) -[:{self.relation_type}]-> ({self.target_name})"


def traverse_entity_neighborhood(
    connection: Connection,
    *,
    tenant_id: UUID,
    seed_entity_names: list[str],
    max_depth: int = 2,
    limit: int = 25,
) -> list[GraphPath]:
    """Traverses multi-hop entity relationships using a PostgreSQL Recursive Common Table Expression (CTE)."""
    if not seed_entity_names:
        return []

    query = """
    WITH RECURSIVE graph_cte AS (
        -- Anchor member: relationships originating from seed entities
        SELECT
            r.source_entity_id,
            r.target_entity_id,
            r.relation_type,
            1 AS depth,
            ARRAY[r.source_entity_id, r.target_entity_id] AS visited_ids
        FROM graph_relationships r
        JOIN graph_entities e ON e.id = r.source_entity_id
        WHERE r.tenant_id = %s
          AND e.name = ANY(%s)

        UNION ALL

        -- Recursive member: traverse to target entity's adjacent neighbors
        SELECT
            r.source_entity_id,
            r.target_entity_id,
            r.relation_type,
            g.depth + 1,
            g.visited_ids || r.target_entity_id
        FROM graph_relationships r
        JOIN graph_cte g ON r.source_entity_id = g.target_entity_id
        WHERE r.tenant_id = %s
          AND g.depth < %s
          AND NOT (r.target_entity_id = ANY(g.visited_ids)) -- prevent infinite cycles
    )
    SELECT DISTINCT
        e1.name AS source_name,
        g.relation_type,
        e2.name AS target_name,
        g.depth
    FROM graph_cte g
    JOIN graph_entities e1 ON e1.id = g.source_entity_id
    JOIN graph_entities e2 ON e2.id = g.target_entity_id
    ORDER BY g.depth, source_name
    LIMIT %s;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (tenant_id, seed_entity_names, tenant_id, max_depth, limit),
        )
        rows = cursor.fetchall()

    return [
        GraphPath(
            source_name=str(row[0]),
            relation_type=str(row[1]),
            target_name=str(row[2]),
            depth=int(row[3]),
        )
        for row in rows
    ]


def format_graph_context_block(paths: list[GraphPath]) -> str:
    """Formats traversed graph paths into an injection-safe prompt block for LLM multi-hop reasoning."""
    if not paths:
        return ""
    lines = [f"- {p.render()}" for p in paths]
    return "Knowledge Graph Relational Context:\n" + "\n".join(lines)
