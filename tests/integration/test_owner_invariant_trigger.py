import os
from uuid import uuid4

import psycopg
import psycopg.errors
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not configured")
    return value


def test_trigger_prevents_deleting_last_owner(database_url: str) -> None:
    tenant_id = uuid4()
    user_id = uuid4()

    with psycopg.connect(database_url) as connection:
        try:
            with connection.transaction():
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "TriggerTest"))
                connection.execute(
                    "INSERT INTO users (id, tenant_id, email, hashed_password) VALUES (%s, %s, %s, %s)",
                    (user_id, tenant_id, f"owner-{user_id}@example.com", "hash"),
                )
                connection.execute(
                    "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')",
                    (tenant_id, user_id),
                )

            # Attempting to delete the last owner must be rejected by the trigger at commit time
            with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                with connection.transaction():
                    connection.execute(
                        "DELETE FROM tenant_memberships WHERE tenant_id = %s AND user_id = %s",
                        (tenant_id, user_id),
                    )
            assert "Cannot remove or demote the last owner of a tenant" in str(exc_info.value)
        finally:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_trigger_prevents_demoting_last_owner(database_url: str) -> None:
    tenant_id = uuid4()
    user_id = uuid4()

    with psycopg.connect(database_url) as connection:
        try:
            with connection.transaction():
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "DemoteTest"))
                connection.execute(
                    "INSERT INTO users (id, tenant_id, email, hashed_password) VALUES (%s, %s, %s, %s)",
                    (user_id, tenant_id, f"owner-{user_id}@example.com", "hash"),
                )
                connection.execute(
                    "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')",
                    (tenant_id, user_id),
                )

            # Attempting to demote the last owner to 'member' must be rejected
            with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                with connection.transaction():
                    connection.execute(
                        "UPDATE tenant_memberships SET role = 'member' WHERE tenant_id = %s AND user_id = %s",
                        (tenant_id, user_id),
                    )
            assert "Cannot remove or demote the last owner of a tenant" in str(exc_info.value)
        finally:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_trigger_allows_demoting_owner_when_another_owner_exists(database_url: str) -> None:
    tenant_id = uuid4()
    owner_1 = uuid4()
    owner_2 = uuid4()

    with psycopg.connect(database_url) as connection:
        try:
            with connection.transaction():
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "MultiOwnerTest"))
                for uid in (owner_1, owner_2):
                    connection.execute(
                        "INSERT INTO users (id, tenant_id, email, hashed_password) VALUES (%s, %s, %s, %s)",
                        (uid, tenant_id, f"user-{uid}@example.com", "hash"),
                    )
                    connection.execute(
                        "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')",
                        (tenant_id, uid),
                    )

            # Demoting owner_1 succeeds because owner_2 remains
            with connection.transaction():
                connection.execute(
                    "UPDATE tenant_memberships SET role = 'member' WHERE tenant_id = %s AND user_id = %s",
                    (tenant_id, owner_1),
                )

            # Verify owner_1 is member and owner_2 is owner
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT role FROM tenant_memberships WHERE tenant_id = %s AND user_id = %s",
                    (tenant_id, owner_1),
                )
                assert cursor.fetchone()[0] == "member"
        finally:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_trigger_prevents_inserting_sole_member_without_owner(database_url: str) -> None:
    tenant_id = uuid4()
    user_id = uuid4()

    with psycopg.connect(database_url) as connection:
        try:
            with connection.transaction():
                connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "NoOwnerTest"))
                connection.execute(
                    "INSERT INTO users (id, tenant_id, email, hashed_password) VALUES (%s, %s, %s, %s)",
                    (user_id, tenant_id, f"member-{user_id}@example.com", "hash"),
                )

            # Inserting only a 'member' role must be rejected by trg_enforce_owner_on_insert
            with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (%s, %s, 'member')",
                        (tenant_id, user_id),
                    )
            assert "Tenant must have at least one owner after membership creation" in str(exc_info.value)
        finally:
            with connection.transaction():
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
