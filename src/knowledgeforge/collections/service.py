"""Collections service: sub-tenant isolation boundaries, collection documents, and memberships."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection


@dataclass(frozen=True)
class CollectionRow:
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CollectionMemberRow:
    collection_id: UUID
    user_id: UUID
    tenant_id: UUID
    role: str
    created_at: datetime


def create_collection(
    connection: Connection,
    *,
    tenant_id: UUID,
    name: str,
    description: str | None = None,
    is_private: bool = True,
    creator_user_id: UUID | None = None,
) -> CollectionRow:
    """Create a new collection within a tenant, optionally enrolling creator as admin."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO collections (tenant_id, name, description, is_private)
            VALUES (%s, %s, %s, %s)
            RETURNING id, tenant_id, name, description, is_private, created_at, updated_at
            """,
            (tenant_id, name, description, is_private),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Failed to create collection")
        col = CollectionRow(
            id=UUID(str(row[0])),
            tenant_id=UUID(str(row[1])),
            name=str(row[2]),
            description=None if row[3] is None else str(row[3]),
            is_private=bool(row[4]),
            created_at=row[5],
            updated_at=row[6],
        )
        if creator_user_id is not None:
            cursor.execute(
                """
                INSERT INTO collection_memberships (collection_id, user_id, tenant_id, role)
                VALUES (%s, %s, %s, 'admin')
                ON CONFLICT (collection_id, user_id) DO NOTHING
                """,
                (col.id, creator_user_id, tenant_id),
            )
    return col


def get_collection(
    connection: Connection,
    collection_id: UUID,
    tenant_id: UUID,
) -> CollectionRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, name, description, is_private, created_at, updated_at
            FROM collections
            WHERE id = %s AND tenant_id = %s
            """,
            (collection_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return CollectionRow(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])),
        name=str(row[2]),
        description=None if row[3] is None else str(row[3]),
        is_private=bool(row[4]),
        created_at=row[5],
        updated_at=row[6],
    )


def list_collections(
    connection: Connection,
    tenant_id: UUID,
    user_id: UUID | None = None,
) -> list[CollectionRow]:
    """List collections accessible to a tenant user.
    
    If user_id is provided, returns public collections plus collections where
    user has explicit membership.
    """
    with connection.cursor() as cursor:
        if user_id is None:
            cursor.execute(
                """
                SELECT id, tenant_id, name, description, is_private, created_at, updated_at
                FROM collections
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            )
        else:
            cursor.execute(
                """
                SELECT c.id, c.tenant_id, c.name, c.description, c.is_private, c.created_at, c.updated_at
                FROM collections c
                WHERE c.tenant_id = %s
                  AND (
                      c.is_private = false
                      OR EXISTS (
                          SELECT 1 FROM collection_memberships cm
                          WHERE cm.collection_id = c.id AND cm.user_id = %s
                      )
                  )
                ORDER BY c.created_at DESC
                """,
                (tenant_id, user_id),
            )
        rows = cursor.fetchall()
    return [
        CollectionRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            name=str(r[2]),
            description=None if r[3] is None else str(r[3]),
            is_private=bool(r[4]),
            created_at=r[5],
            updated_at=r[6],
        )
        for r in rows
    ]


def add_document_to_collection(
    connection: Connection,
    collection_id: UUID,
    document_id: UUID,
    tenant_id: UUID,
) -> None:
    """Link a document to a collection under the same tenant."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO collection_documents (collection_id, document_id, tenant_id)
            SELECT %s, %s, %s
            WHERE EXISTS (SELECT 1 FROM collections WHERE id = %s AND tenant_id = %s)
              AND EXISTS (SELECT 1 FROM documents WHERE id = %s AND tenant_id = %s)
            ON CONFLICT (collection_id, document_id) DO NOTHING
            """,
            (collection_id, document_id, tenant_id, collection_id, tenant_id, document_id, tenant_id),
        )


def remove_document_from_collection(
    connection: Connection,
    collection_id: UUID,
    document_id: UUID,
    tenant_id: UUID,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM collection_documents
            WHERE collection_id = %s AND document_id = %s AND tenant_id = %s
            """,
            (collection_id, document_id, tenant_id),
        )
        return cursor.rowcount > 0


def add_collection_member(
    connection: Connection,
    collection_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
    role: str = "viewer",
) -> None:
    if role not in ("viewer", "editor", "admin"):
        raise ValueError("Invalid collection role")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO collection_memberships (collection_id, user_id, tenant_id, role)
            SELECT %s, %s, %s, %s
            WHERE EXISTS (SELECT 1 FROM collections WHERE id = %s AND tenant_id = %s)
              AND EXISTS (SELECT 1 FROM users WHERE id = %s AND tenant_id = %s)
            ON CONFLICT (collection_id, user_id)
            DO UPDATE SET role = EXCLUDED.role
            """,
            (collection_id, user_id, tenant_id, role, collection_id, tenant_id, user_id, tenant_id),
        )


def remove_collection_member(
    connection: Connection,
    collection_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM collection_memberships
            WHERE collection_id = %s AND user_id = %s AND tenant_id = %s
            """,
            (collection_id, user_id, tenant_id),
        )
        return cursor.rowcount > 0


def check_user_collection_access(
    connection: Connection,
    collection_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
) -> bool:
    """Return True if user can access collection (public in tenant or has membership)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM collections c
            WHERE c.id = %s AND c.tenant_id = %s
              AND (
                  c.is_private = false
                  OR EXISTS (
                      SELECT 1 FROM collection_memberships cm
                      WHERE cm.collection_id = c.id AND cm.user_id = %s
                  )
              )
            """,
            (collection_id, tenant_id, user_id),
        )
        return cursor.fetchone() is not None
