"""Document auto-clustering and tag suggestion engine with human-in-the-loop confirmation (Phase 8 Item 5).

Aggregates chunk embeddings into document-level representations using mean-pooling,
clusters similar documents by cosine similarity, and surfaces suggested collections
and tags for human review.

CRITICAL INVARIANT: Clusters and tags are NEVER applied automatically. They require
explicit confirmation via human-in-the-loop review.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from knowledgeforge.collections.service import add_document_to_collection, create_collection

logger = logging.getLogger("knowledgeforge.clustering")


@dataclass(frozen=True)
class DocumentClusterRow:
    id: UUID
    tenant_id: UUID
    name: str
    document_ids: list[UUID]
    suggested_tags: list[str]
    status: str
    confirmed_collection_id: UUID | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_tenant_document_embeddings(
    connection: Connection,
    tenant_id: UUID,
) -> dict[UUID, tuple[str, str, list[float]]]:
    """Calculate mean-pooled document embeddings and metadata for a tenant."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.id, d.title, d.doc_type, c.embedding::text
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            WHERE d.tenant_id = %s AND d.status = 'ready'
            ORDER BY d.id
            """,
            (tenant_id,),
        )
        rows = cursor.fetchall()

    doc_chunks: dict[UUID, tuple[str, str, list[list[float]]]] = {}
    for doc_id_raw, title, doc_type, embedding_str in rows:
        doc_id = UUID(str(doc_id_raw))
        # Parse pgvector string representation: "[0.1,0.2,...]"
        clean_str = embedding_str.strip("[]")
        vec = [float(x) for x in clean_str.split(",") if x.strip()]
        if doc_id not in doc_chunks:
            doc_chunks[doc_id] = (str(title), str(doc_type), [])
        doc_chunks[doc_id][2].append(vec)

    result: dict[UUID, tuple[str, str, list[float]]] = {}
    for doc_id, (title, doc_type, vectors) in doc_chunks.items():
        if not vectors:
            continue
        dim = len(vectors[0])
        mean_vec = [
            sum(v[i] for v in vectors) / len(vectors)
            for i in range(dim)
        ]
        result[doc_id] = (title, doc_type, mean_vec)
    return result


def generate_suggested_clusters(
    connection: Connection,
    tenant_id: UUID,
    *,
    similarity_threshold: float = 0.70,
    min_cluster_size: int = 2,
) -> list[DocumentClusterRow]:
    """Cluster documents by cosine proximity and store pending suggestions."""
    doc_embeddings = compute_tenant_document_embeddings(connection, tenant_id)
    doc_ids = list(doc_embeddings.keys())
    if len(doc_ids) < min_cluster_size:
        return []

    visited = set()
    clusters: list[list[UUID]] = []

    for i, doc_id_a in enumerate(doc_ids):
        if doc_id_a in visited:
            continue
        current_cluster = [doc_id_a]
        vec_a = doc_embeddings[doc_id_a][2]

        for j in range(i + 1, len(doc_ids)):
            doc_id_b = doc_ids[j]
            if doc_id_b in visited:
                continue
            vec_b = doc_embeddings[doc_id_b][2]
            sim = _cosine_similarity(vec_a, vec_b)
            if sim >= similarity_threshold:
                current_cluster.append(doc_id_b)

        if len(current_cluster) >= min_cluster_size:
            clusters.append(current_cluster)
            visited.update(current_cluster)

    created_clusters: list[DocumentClusterRow] = []
    with connection.cursor() as cursor:
        for idx, cluster_docs in enumerate(clusters):
            # Derive cluster name and tags from constituent documents
            titles = [doc_embeddings[d][0] for d in cluster_docs]
            doc_types = list(set(doc_embeddings[d][1] for d in cluster_docs))
            
            cluster_name = f"Cluster {idx + 1}: {titles[0]} & related ({len(cluster_docs)} docs)"
            suggested_tags = [f"type:{t}" for t in doc_types]
            suggested_tags.append("auto-clustered")

            doc_uuids_json = json.dumps([str(d) for d in cluster_docs])
            tags_json = json.dumps(suggested_tags)

            cursor.execute(
                """
                INSERT INTO document_clusters (tenant_id, name, document_ids, suggested_tags, status)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, 'suggested')
                RETURNING id, tenant_id, name, document_ids, suggested_tags, status,
                          confirmed_collection_id, reviewed_by, reviewed_at, created_at
                """,
                (tenant_id, cluster_name, doc_uuids_json, tags_json),
            )
            r = cursor.fetchone()
            if r is not None:
                created_clusters.append(
                    DocumentClusterRow(
                        id=UUID(str(r[0])),
                        tenant_id=UUID(str(r[1])),
                        name=str(r[2]),
                        document_ids=[UUID(x) for x in r[3]],
                        suggested_tags=list(r[4]),
                        status=str(r[5]),
                        confirmed_collection_id=UUID(str(r[6])) if r[6] else None,
                        reviewed_by=UUID(str(r[7])) if r[7] else None,
                        reviewed_at=r[8],
                        created_at=r[9],
                    )
                )
    return created_clusters


def list_document_clusters(
    connection: Connection,
    tenant_id: UUID,
    status_filter: str | None = None,
) -> list[DocumentClusterRow]:
    with connection.cursor() as cursor:
        if status_filter:
            cursor.execute(
                """
                SELECT id, tenant_id, name, document_ids, suggested_tags, status,
                       confirmed_collection_id, reviewed_by, reviewed_at, created_at
                FROM document_clusters
                WHERE tenant_id = %s AND status = %s
                ORDER BY created_at DESC
                """,
                (tenant_id, status_filter),
            )
        else:
            cursor.execute(
                """
                SELECT id, tenant_id, name, document_ids, suggested_tags, status,
                       confirmed_collection_id, reviewed_by, reviewed_at, created_at
                FROM document_clusters
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            )
        rows = cursor.fetchall()

    return [
        DocumentClusterRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            name=str(r[2]),
            document_ids=[UUID(x) for x in r[3]],
            suggested_tags=list(r[4]),
            status=str(r[5]),
            confirmed_collection_id=UUID(str(r[6])) if r[6] else None,
            reviewed_by=UUID(str(r[7])) if r[7] else None,
            reviewed_at=r[8],
            created_at=r[9],
        )
        for r in rows
    ]


def confirm_cluster(
    connection: Connection,
    cluster_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    *,
    create_collection_flag: bool = True,
    apply_tags_flag: bool = True,
) -> UUID | None:
    """Explicit human confirmation of a cluster. Creates collection and applies tags."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, document_ids, suggested_tags, status
            FROM document_clusters
            WHERE id = %s AND tenant_id = %s
            FOR UPDATE
            """,
            (cluster_id, tenant_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        _, name, doc_ids_raw, tags_raw, status = row
        if status != "suggested":
            raise ValueError(f"Cluster already in status '{status}'")

        doc_ids = [UUID(x) for x in doc_ids_raw]
        tags = list(tags_raw)

        new_collection_id = None
        if create_collection_flag:
            col = create_collection(
                connection,
                tenant_id=tenant_id,
                name=name,
                description=f"Collection created from confirmed cluster: {name}",
                is_private=True,
                creator_user_id=user_id,
            )
            new_collection_id = col.id
            for d_id in doc_ids:
                add_document_to_collection(connection, col.id, d_id, tenant_id)

        if apply_tags_flag:
            for d_id in doc_ids:
                for tag in tags:
                    cursor.execute(
                        """
                        INSERT INTO document_tags (tenant_id, document_id, tag)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (tenant_id, d_id, tag),
                    )

        cursor.execute(
            """
            UPDATE document_clusters
            SET status = 'confirmed', confirmed_collection_id = %s,
                reviewed_by = %s, reviewed_at = now()
            WHERE id = %s
            """,
            (new_collection_id, user_id, cluster_id),
        )
        return new_collection_id


def dismiss_cluster(
    connection: Connection,
    cluster_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE document_clusters
            SET status = 'dismissed', reviewed_by = %s, reviewed_at = now()
            WHERE id = %s AND tenant_id = %s AND status = 'suggested'
            """,
            (user_id, cluster_id, tenant_id),
        )
        return cursor.rowcount > 0
