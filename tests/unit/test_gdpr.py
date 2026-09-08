"""Unit tests for Item 12: GDPR Article 20 Data Portability Export."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from knowledgeforge import api
from knowledgeforge.ingestion.store import export_tenant_data
from knowledgeforge.main import app
from knowledgeforge.security.auth import purge_unverified_accounts


class MockCursor:
    def __init__(self, db: "MockDB"):
        self.db = db
        self._last_result: list[tuple] = []
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())
        self._idx = 0

        # 1. Tenants
        if "SELECT id, name, created_at, tier, subscription_status FROM tenants WHERE id = %s" in q:
            t_id = params[0]
            if t_id in self.db.tenants:
                t = self.db.tenants[t_id]
                self._last_result = [(t["id"], t["name"], t["created_at"], t["tier"], t["subscription_status"])]
            else:
                self._last_result = []
            return

        # 2. Users and memberships
        if "FROM tenant_memberships tm" in q:
            t_id = params[0]
            rows = []
            for u in self.db.users.get(t_id, []):
                rows.append((u["id"], u["email"], u["role"], u["joined_at"], u["email_verified"]))
            self._last_result = rows
            return

        # 3. Documents
        if "FROM documents WHERE tenant_id = %s" in q:
            t_id = params[0]
            rows = []
            for d in self.db.documents.get(t_id, []):
                rows.append((
                    d["id"], d["title"], d["source_filename"],
                    d.get("storage_uri", "gs://test-bucket/file.pdf"),
                    d["doc_type"], d["status"], d["version"], d["created_at"]
                ))
            self._last_result = rows
            return

        # 3b. Chunks
        if "FROM chunks c" in q:
            t_id = params[0]
            rows = []
            for c in self.db.chunks.get(t_id, []):
                rows.append((c["id"], c["document_id"], c["page"], c["section"], c["chunk_text"], c["created_at"]))
            self._last_result = rows
            return

        # 4. Extractions
        if "FROM document_extractions WHERE tenant_id = %s" in q:
            t_id = params[0]
            rows = []
            for e in self.db.extractions.get(t_id, []):
                rows.append((
                    e["document_id"], e["schema_type"], e["schema_version"], e["model"],
                    e["fields"], e["field_confidence"], e["overall_confidence"], e["created_at"]
                ))
            self._last_result = rows
            return

        # 5. Conversations
        if "FROM conversations WHERE tenant_id = %s" in q:
            t_id = params[0]
            rows = []
            for c in self.db.conversations.get(t_id, []):
                rows.append((c["id"], c["title"], c["created_at"], c["updated_at"]))
            self._last_result = rows
            return

        # 5b. Conversation messages
        if "FROM conversation_messages WHERE conversation_id = %s" in q:
            cid = params[0]
            rows = []
            for m in self.db.messages.get(cid, []):
                rows.append((m["role"], m["content"], m["citations"], m["created_at"]))
            self._last_result = rows
            return

        # 6. API Keys
        if "SELECT id, name, key_prefix, created_at, last_used_at, revoked FROM api_keys WHERE tenant_id = %s" in q:
            t_id = params[0]
            rows = []
            for k in self.db.api_keys.get(t_id, []):
                rows.append((k["id"], k["name"], k["key_prefix"], k["created_at"], k["last_used_at"], k["revoked"]))
            self._last_result = rows
            return

        # 7. Billing events
        if "FROM stripe_events" in q:
            t_id = params[0]
            rows = []
            for b in self.db.billing_events.get(t_id, []):
                rows.append((b["event_id"], b["event_type"], b.get("processed_at"), b["created_at"]))
            self._last_result = rows
            return

        # 8. Invitations
        if "FROM invitations" in q:
            t_id = params[0]
            rows = []
            for inv in self.db.invitations.get(t_id, []):
                rows.append((inv["id"], inv["email"], inv["role"], inv["expires_at"], inv.get("accepted_at"), inv["created_at"]))
            self._last_result = rows
            return

        # 9. User purge
        if "DELETE FROM users" in q and "email_verified = false" in q:
            cutoff = params[0]
            deleted = []
            for uid, u in list(self.db.all_users.items()):
                if not u.get("email_verified") and u.get("created_at") < cutoff:
                    deleted.append((uid,))
                    del self.db.all_users[uid]
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
        res = self._last_result[self._idx:]
        self._idx = len(self._last_result)
        return res


class MockDB:
    def __init__(self):
        self.tenants = {}
        self.users = {}
        self.documents = {}
        self.chunks = {}
        self.extractions = {}
        self.conversations = {}
        self.messages = {}
        self.api_keys = {}
        self.billing_events = {}
        self.invitations = {}
        self.all_users = {}


class MockConnection:
    def __init__(self, db: MockDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass

    def cursor(self):
        return MockCursor(self.db)


def test_export_tenant_data_complete():
    tenant_id = uuid4()
    user_id = uuid4()
    doc_id = uuid4()
    conv_id = uuid4()
    key_id = uuid4()
    now = datetime.now(UTC)

    db = MockDB()
    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme Corp",
        "created_at": now,
        "tier": "scale",
        "subscription_status": "active",
    }
    db.users[tenant_id] = [{
        "id": user_id,
        "email": "owner@acme.com",
        "role": "owner",
        "joined_at": now,
        "email_verified": True,
    }]
    db.documents[tenant_id] = [{
        "id": doc_id,
        "title": "Master Services Agreement",
        "source_filename": "msa.pdf",
        "storage_uri": "gs://kf-docs/msa.pdf",
        "doc_type": "pdf",
        "status": "indexed",
        "version": 1,
        "created_at": now,
    }]
    chunk_id = uuid4()
    db.chunks[tenant_id] = [{
        "id": chunk_id,
        "document_id": doc_id,
        "page": 1,
        "section": "Section 1: Scope",
        "chunk_text": "This Agreement governs the relationship...",
        "created_at": now,
    }]
    db.extractions[tenant_id] = [{
        "document_id": doc_id,
        "schema_type": "contract",
        "schema_version": 1,
        "model": "gemini-2.5-flash",
        "fields": {"counterparty": "Global Tech"},
        "field_confidence": {"counterparty": 0.95},
        "overall_confidence": 0.95,
        "created_at": now,
    }]
    db.conversations[tenant_id] = [{
        "id": conv_id,
        "title": "Contract Analysis",
        "created_at": now,
        "updated_at": now,
    }]
    db.messages[conv_id] = [{
        "role": "user",
        "content": "What is the liability cap?",
        "citations": [],
        "created_at": now,
    }]
    db.api_keys[tenant_id] = [{
        "id": key_id,
        "name": "Production Key",
        "key_prefix": "kf_live_1234",
        "created_at": now,
        "last_used_at": now,
        "revoked": False,
    }]
    db.billing_events[tenant_id] = [{
        "event_id": "evt_test_123",
        "event_type": "customer.subscription.created",
        "processed_at": now,
        "created_at": now,
    }]
    inv_id = uuid4()
    db.invitations[tenant_id] = [{
        "id": inv_id,
        "email": "invitee@acme.com",
        "role": "member",
        "expires_at": now + timedelta(days=7),
        "accepted_at": None,
        "created_at": now,
    }]

    conn = MockConnection(db)
    result = export_tenant_data(conn, tenant_id)

    assert result["tenant"]["name"] == "Acme Corp"
    assert result["tenant"]["tier"] == "scale"
    assert len(result["users"]) == 1
    assert result["users"][0]["email"] == "owner@acme.com"
    assert len(result["documents"]) == 1
    assert result["documents"][0]["source_filename"] == "msa.pdf"
    assert result["documents"][0]["storage_uri"] == "gs://kf-docs/msa.pdf"
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["chunk_text"] == "This Agreement governs the relationship..."
    assert result["chunks"][0]["section"] == "Section 1: Scope"
    assert len(result["extractions"]) == 1
    assert result["extractions"][0]["schema_type"] == "contract"
    assert len(result["conversations"]) == 1
    assert result["conversations"][0]["messages"][0]["content"] == "What is the liability cap?"
    assert len(result["api_keys"]) == 1
    assert result["api_keys"][0]["key_prefix"] == "kf_live_1234"
    assert len(result["billing_events"]) == 1
    assert result["billing_events"][0]["event_id"] == "evt_test_123"
    assert result["billing_events"][0]["event_type"] == "customer.subscription.created"
    assert len(result["invitations"]) == 1
    assert result["invitations"][0]["email"] == "invitee@acme.com"


def test_all_tenant_scoped_categories_accounted_for():
    db = MockDB()
    tenant_id = uuid4()
    now = datetime.now(UTC)
    db.tenants[tenant_id] = {
        "id": tenant_id,
        "name": "Acme",
        "created_at": now,
        "tier": "pro",
        "subscription_status": "active",
    }
    conn = MockConnection(db)
    result = export_tenant_data(conn, tenant_id)
    expected_categories = {
        "tenant", "users", "documents", "chunks", "extractions",
        "conversations", "api_keys", "billing_events", "invitations", "export_metadata"
    }
    assert set(result.keys()) == expected_categories


def test_purge_unverified_accounts():
    db = MockDB()
    now = datetime.now(UTC)
    stale_unverified = uuid4()
    recent_unverified = uuid4()
    verified_user = uuid4()

    # User 1: unverified, created 45 days ago -> should be purged
    db.all_users[stale_unverified] = {
        "id": stale_unverified,
        "email_verified": False,
        "created_at": now - timedelta(days=45),
    }
    # User 2: unverified, created 5 days ago -> should be kept
    db.all_users[recent_unverified] = {
        "id": recent_unverified,
        "email_verified": False,
        "created_at": now - timedelta(days=5),
    }
    # User 3: verified, created 100 days ago -> should be kept
    db.all_users[verified_user] = {
        "id": verified_user,
        "email_verified": True,
        "created_at": now - timedelta(days=100),
    }

    conn = MockConnection(db)
    purged_count = purge_unverified_accounts(conn, max_age_days=30)

    assert purged_count == 1
    assert stale_unverified not in db.all_users
    assert recent_unverified in db.all_users
    assert verified_user in db.all_users


def test_export_tenant_data_nonexistent():
    db = MockDB()
    conn = MockConnection(db)
    result = export_tenant_data(conn, uuid4())
    assert result == {}


from contextlib import contextmanager


def test_export_account_endpoint_owner(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", False)

    mock_export = {
        "tenant": {"id": str(tenant_id), "name": "Test Tenant"},
        "users": [{"email": "test@tenant.com"}],
        "documents": [],
        "extractions": [],
        "conversations": [],
        "api_keys": [],
    }
    monkeypatch.setattr(api, "export_tenant_data", lambda conn, tid: mock_export)

    @contextmanager
    def mock_conn():
        yield None

    monkeypatch.setattr(api, "get_connection", mock_conn)

    client = TestClient(app)
    response = client.get("/auth/account/export")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant"]["name"] == "Test Tenant"
    assert data["users"][0]["email"] == "test@tenant.com"


def test_export_account_endpoint_forbidden_for_member():
    tenant_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    client = TestClient(app)
    response = client.get("/auth/account/export")
    assert response.status_code == 403
    assert "Requires one of roles: owner" in response.json()["detail"]
