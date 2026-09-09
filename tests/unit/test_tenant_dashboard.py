"""Unit tests for self-service tenant dashboard and usage endpoints."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app


class FakeCursor:
    def __init__(self, tenant_id: UUID, db: "FakeDB"):
        self.tenant_id = tenant_id
        self.db = db
        self._last_result = []
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())
        self._idx = 0

        # Query tenant metadata
        if "FROM tenants WHERE id = %s" in q:
            t_id = params[0]
            if t_id in self.db.tenants:
                t = self.db.tenants[t_id]
                self._last_result = [
                    (t["id"], t["name"], t["tier"], t["subscription_status"], t["created_at"])
                ]
            else:
                self._last_result = []
            return

        # Query chunks count
        if (
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.tenant_id = %s"
            in q
        ):
            t_id = params[0]
            self._last_result = [(self.db.chunks.get(t_id, 0),)]
            return

        # Query extractions count
        if "SELECT count(*) FROM document_extractions WHERE tenant_id = %s" in q:
            t_id = params[0]
            self._last_result = [(self.db.extractions.get(t_id, 0),)]
            return

        # Query conversations count
        if "SELECT count(*) FROM conversations WHERE tenant_id = %s" in q:
            t_id = params[0]
            self._last_result = [(self.db.conversations.get(t_id, 0),)]
            return

        # Query documents count
        if "SELECT count(*) FROM documents WHERE tenant_id = %s" in q:
            t_id = params[0]
            self._last_result = [(self.db.documents.get(t_id, 0),)]
            return

        # Query request logs count and cost
        if "FROM request_logs WHERE tenant_id = %s" in q:
            t_id = params[0]
            logs = self.db.request_logs.get(t_id, [])
            count = len(logs)
            total_cost = sum(log_item.get("cost", 0.0) for log_item in logs)
            self._last_result = [(count, total_cost)]
            return

        self._last_result = [(0,)]

    def fetchone(self):
        if self._idx < len(self._last_result):
            row = self._last_result[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        res = self._last_result[self._idx :]
        self._idx = len(self._last_result)
        return res


class FakeDB:
    def __init__(self):
        self.tenants = {}
        self.documents = {}
        self.chunks = {}
        self.extractions = {}
        self.conversations = {}
        self.request_logs = {}


class FakeConnection:
    def __init__(self, tenant_id: UUID, db: FakeDB):
        self.tenant_id = tenant_id
        self.db = db

    def cursor(self):
        return FakeCursor(self.tenant_id, self.db)


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_tenant_usage_member_allowed(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    db = FakeDB()
    db.documents[tenant_id] = 5
    db.chunks[tenant_id] = 25
    db.extractions[tenant_id] = 3
    db.conversations[tenant_id] = 7
    db.request_logs[tenant_id] = [{"cost": 0.05}, {"cost": 0.02}]

    @contextmanager
    def mock_get_conn():
        yield FakeConnection(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_get_conn)
    monkeypatch.setattr(api, "tenant_usage", lambda conn, tid: (5, 2, 0.07))

    client = TestClient(app)
    response = client.get("/tenant/usage")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_id)
    assert data["documents_count"] == 5
    assert data["chunks_count"] == 25
    assert data["extractions_count"] == 3
    assert data["conversations_count"] == 7
    assert data["queries_count"] == 2
    assert data["cost_estimate_total"] == 0.07


def test_tenant_dashboard_member_allowed(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    now_str = datetime.now(UTC).isoformat()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    db = FakeDB()
    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Widgets",
        "tier": "pro",
        "subscription_status": "active",
        "created_at": now_str,
    }
    db.documents[tenant_id] = 2
    db.chunks[tenant_id] = 10
    db.extractions[tenant_id] = 1
    db.conversations[tenant_id] = 2

    @contextmanager
    def mock_get_conn():
        yield FakeConnection(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_get_conn)
    monkeypatch.setattr(api, "tenant_usage", lambda conn, tid: (2, 0, 0.0))
    monkeypatch.setattr(api, "tenant_usage_daily", lambda conn, tid, days: [])

    client = TestClient(app)
    response = client.get("/tenant/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant"]["id"] == str(tenant_id)
    assert data["tenant"]["name"] == "Acme Widgets"
    assert data["tenant"]["tier"] == "pro"
    assert data["usage"]["documents_count"] == 2
    assert isinstance(data["daily_trends"], list)


def test_tenant_usage_unauthenticated():
    client = TestClient(app)
    response = client.get("/tenant/usage")
    assert response.status_code == 401


def test_tenant_isolation_cross_tenant_rejection(monkeypatch):
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()

    # Logged in as User A of Tenant A
    app.dependency_overrides[api.get_current_user] = lambda: (user_a, tenant_a, "member", False)

    db = FakeDB()
    db.documents[tenant_a] = 1
    db.documents[tenant_b] = 999
    db.chunks[tenant_a] = 5
    db.chunks[tenant_b] = 5000

    @contextmanager
    def mock_get_conn():
        yield FakeConnection(tenant_a, db)

    monkeypatch.setattr(api, "get_connection", mock_get_conn)
    monkeypatch.setattr(
        api, "tenant_usage", lambda conn, tid: (1 if tid == tenant_a else 999, 0, 0.0)
    )

    client = TestClient(app)
    response = client.get("/tenant/usage")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_a)
    assert data["documents_count"] == 1
    assert data["chunks_count"] == 5


def test_v1_versioned_alias_support(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    db = FakeDB()

    @contextmanager
    def mock_get_conn():
        yield FakeConnection(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_get_conn)
    monkeypatch.setattr(api, "tenant_usage", lambda conn, tid: (0, 0, 0.0))

    client = TestClient(app)
    res_v1 = client.get("/v1/tenant/usage")
    assert res_v1.status_code == 200
    assert res_v1.json()["tenant_id"] == str(tenant_id)
