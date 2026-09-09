"""Unit tests for tenant retention policies, data residency settings, and document purge job."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app
from knowledgeforge.security import purge_job


class MockTenantSettingsCursor:
    def __init__(self, tenant_id: UUID, db: "MockTenantSettingsDB"):
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

        # Read tenant settings
        if "SELECT retention_days, data_residency FROM tenants WHERE id = %s" in q:
            t_id = params[0]
            if t_id in self.db.tenants:
                t = self.db.tenants[t_id]
                self._last_result = [(t.get("retention_days", 0), t.get("data_residency", "us"))]
            else:
                self._last_result = []
            return

        # Update tenant settings
        if (
            "UPDATE tenants" in q
            and "SET retention_days = %s, data_residency = %s WHERE id = %s" in q
        ):
            retention_days, residency, t_id = params
            if t_id in self.db.tenants:
                self.db.tenants[t_id]["retention_days"] = retention_days
                self.db.tenants[t_id]["data_residency"] = residency
                self._last_result = [(retention_days, residency)]
            else:
                self._last_result = []
            return

        # Count expired documents (dry run)
        if (
            "SELECT count(*) FROM documents d JOIN tenants t ON d.tenant_id = t.id WHERE t.retention_days > 0"
            in q
        ):
            now = datetime.now(UTC)
            count = 0
            for doc in self.db.documents.values():
                t = self.db.tenants.get(doc["tenant_id"])
                if t and t.get("retention_days", 0) > 0:
                    retention_delta = timedelta(days=t["retention_days"])
                    if doc["created_at"] < now - retention_delta:
                        count += 1
            self._last_result = [(count,)]
            return

        # Purge expired documents
        if (
            "DELETE FROM documents d USING tenants t WHERE d.tenant_id = t.id AND t.retention_days > 0"
            in q
        ):
            now = datetime.now(UTC)
            deleted = []
            for doc_id, doc in list(self.db.documents.items()):
                t = self.db.tenants.get(doc["tenant_id"])
                if t and t.get("retention_days", 0) > 0:
                    retention_delta = timedelta(days=t["retention_days"])
                    if doc["created_at"] < now - retention_delta:
                        deleted.append((doc_id,))
                        del self.db.documents[doc_id]
            self._last_result = deleted
            return

        self._last_result = []

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


class MockTenantSettingsDB:
    def __init__(self):
        self.tenants = {}
        self.documents = {}


class MockTenantSettingsConn:
    def __init__(self, tenant_id: UUID, db: MockTenantSettingsDB):
        self.tenant_id = tenant_id
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass

    def cursor(self):
        return MockTenantSettingsCursor(self.tenant_id, self.db)


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def test_setup():
    db = MockTenantSettingsDB()
    client = TestClient(app)
    return db, client


def test_get_tenant_settings_default(test_setup, monkeypatch):
    db, client = test_setup
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    response = client.get("/tenant/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_id)
    assert data["retention_days"] == 0
    assert data["data_residency"] == "us"


def test_update_tenant_settings_owner_success(test_setup, monkeypatch):
    db, client = test_setup
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    response = client.put(
        "/tenant/settings",
        json={"retention_days": 90, "data_residency": "eu"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_id)
    assert data["retention_days"] == 90
    assert data["data_residency"] == "eu"

    # Verify state was saved
    assert db.tenants[tenant_id]["retention_days"] == 90
    assert db.tenants[tenant_id]["data_residency"] == "eu"


def test_update_tenant_settings_member_forbidden(test_setup, monkeypatch):
    db, client = test_setup
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    response = client.put(
        "/tenant/settings",
        json={"retention_days": 60, "data_residency": "eu"},
    )
    assert response.status_code == 403
    assert "owner" in response.json()["detail"].lower()


def test_update_tenant_settings_invalid_residency(test_setup, monkeypatch):
    db, client = test_setup
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    response = client.put(
        "/tenant/settings",
        json={"retention_days": 30, "data_residency": "mars"},
    )
    assert response.status_code in (400, 422)


def test_update_tenant_settings_negative_days(test_setup, monkeypatch):
    db, client = test_setup
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_id, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    response = client.put(
        "/tenant/settings",
        json={"retention_days": -10, "data_residency": "us"},
    )
    assert response.status_code == 422


def test_tenant_settings_scoping(test_setup, monkeypatch):
    db, client = test_setup
    user_a = uuid4()
    tenant_a = uuid4()
    tenant_b = uuid4()

    app.dependency_overrides[api.require_owner] = lambda: (user_a, tenant_a, "owner", False)

    db.tenants[tenant_a] = {
        "id": tenant_a,
        "name": "Tenant A",
        "retention_days": 0,
        "data_residency": "us",
    }
    db.tenants[tenant_b] = {
        "id": tenant_b,
        "name": "Tenant B",
        "retention_days": 0,
        "data_residency": "us",
    }

    @contextmanager
    def mock_conn():
        yield MockTenantSettingsConn(tenant_a, db)

    monkeypatch.setattr(api, "get_connection", mock_conn)

    # Tenant A updates to 180 days, apac
    resp_a = client.put(
        "/tenant/settings",
        json={"retention_days": 180, "data_residency": "apac"},
    )
    assert resp_a.status_code == 200

    # Tenant B settings must remain unaffected
    assert db.tenants[tenant_b]["retention_days"] == 0
    assert db.tenants[tenant_b]["data_residency"] == "us"


def test_purge_expired_documents(monkeypatch):
    db = MockTenantSettingsDB()
    now = datetime.now(UTC)

    # Tenant 1: indefinite retention (retention_days = 0)
    t1 = uuid4()
    db.tenants[t1] = {"retention_days": 0}
    doc_t1_old = uuid4()
    db.documents[doc_t1_old] = {"tenant_id": t1, "created_at": now - timedelta(days=120)}

    # Tenant 2: 30 days retention
    t2 = uuid4()
    db.tenants[t2] = {"retention_days": 30}
    doc_t2_stale = uuid4()
    doc_t2_fresh = uuid4()
    db.documents[doc_t2_stale] = {"tenant_id": t2, "created_at": now - timedelta(days=45)}
    db.documents[doc_t2_fresh] = {"tenant_id": t2, "created_at": now - timedelta(days=10)}

    conn = MockTenantSettingsConn(t1, db)

    # Dry run
    dry_count = purge_job.purge_expired_documents(conn, dry_run=True)
    assert dry_count == 1
    assert doc_t2_stale in db.documents  # dry run does not delete

    # Real run
    purged_count = purge_job.purge_expired_documents(conn, dry_run=False)
    assert purged_count == 1
    assert doc_t2_stale not in db.documents
    assert doc_t2_fresh in db.documents
    assert doc_t1_old in db.documents
