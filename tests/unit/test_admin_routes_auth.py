"""Unit tests for Fix 3: Admin routes authorization and rate limiting."""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.main import app
from knowledgeforge.security import auth


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_admin_routes_reject_member_with_403(client: TestClient):
    from knowledgeforge import api
    user_id = uuid4()
    tenant_id = uuid4()
    # User has 'member' role, not 'owner' or 'platform_admin'
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    conflict_id = uuid4()

    # 1. GET /admin/conflicts
    r1 = client.get("/admin/conflicts")
    assert r1.status_code == 403
    assert "Requires one of roles: owner" in r1.json()["detail"]

    # 2. POST /admin/conflicts/{id}/resolve
    r2 = client.post(f"/admin/conflicts/{conflict_id}/resolve", json={"status": "resolved"})
    assert r2.status_code == 403
    assert "Requires one of roles: owner" in r2.json()["detail"]

    # 3. GET /admin/schemas
    r3 = client.get("/admin/schemas")
    assert r3.status_code == 403
    assert "Requires one of roles: owner" in r3.json()["detail"]

    # 4. GET /admin/schemas/{name}
    r4 = client.get("/admin/schemas/invoice")
    assert r4.status_code == 403
    assert "Requires one of roles: owner" in r4.json()["detail"]

    # 5. POST /admin/schemas
    r5 = client.post(
        "/admin/schemas",
        json={
            "schema_name": "contract",
            "json_schema": {"type": "object"},
            "description": "Contracts",
        },
    )
    assert r5.status_code == 403
    assert "Requires one of roles: owner" in r5.json()["detail"]

    # 6. POST /admin/schemas/infer
    r6 = client.post(
        "/admin/schemas/infer",
        json={"schema_name": "memo", "sample_text": "Memo date: 2026-09-09"},
    )
    assert r6.status_code == 403
    assert "Requires one of roles: owner" in r6.json()["detail"]


def test_admin_routes_allow_owner(client: TestClient):
    from knowledgeforge import api
    user_id = uuid4()
    tenant_id = uuid4()
    # User has 'owner' role
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", False)

    with patch("knowledgeforge.api.get_connection", lambda: nullcontext(MagicMock())), \
         patch("knowledgeforge.api.list_conflicts", return_value=[]), \
         patch("knowledgeforge.api.list_tenant_schemas", return_value=[]):
        r1 = client.get("/admin/conflicts")
        assert r1.status_code == 200

        r2 = client.get("/admin/schemas")
        assert r2.status_code == 200

        r3 = client.post(
            "/admin/schemas/infer",
            json={"schema_name": "memo", "sample_text": "Total amount: $500.00 on 2026-09-09"},
        )
        assert r3.status_code == 200
        assert r3.json()["schema_name"] == "memo"


def test_mechanical_check_no_admin_route_gated_by_bare_get_current_user():
    """Repo-wide mechanical audit: No route with an '/admin' path prefix may use bare get_current_user."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/admin"):
            # Check route dependencies
            endpoint = getattr(route, "endpoint", None)
            dependant = getattr(route, "dependant", None)
            if dependant is not None:
                call_dependencies = [d.call for d in dependant.dependencies]
                # Check parameters
                for param in dependant.params:
                    # If any dependency is bare get_current_user (instead of require_owner or require_platform_admin)
                    if hasattr(param, "depends"):
                        call = getattr(param.depends, "dependency", None)
                        assert call is not auth.get_current_user, (
                            f"Route '{path}' uses bare 'get_current_user'! "
                            f"All /admin routes must use 'require_owner' or 'require_platform_admin'."
                        )
