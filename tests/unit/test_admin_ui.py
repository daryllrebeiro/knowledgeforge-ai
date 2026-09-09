"""Unit tests for Item 7 & 8: Admin Console UI, DLQ Inspector, and Human-in-the-loop Review Queue."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.config import get_settings
from knowledgeforge.main import app

# ---------------------------------------------------------------------------
# In-memory mock DB connection for extraction and DLQ tests
# ---------------------------------------------------------------------------


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

        # DLQ queries
        if "SELECT count(*) FROM failed_ingestions" in q:
            self._last_result = [(len(self.db.failed_ingestions),)]
            return

        if "SELECT f.id, f.tenant_id, t.name, f.filename, f.error_message" in q:
            rows = []
            for fi in self.db.failed_ingestions:
                tname = self.db.tenants.get(fi["tenant_id"], {}).get("name", "Unknown")
                rows.append(
                    (
                        fi["id"],
                        fi["tenant_id"],
                        tname,
                        fi["filename"],
                        fi["error_message"],
                        fi["created_at"],
                    )
                )
            self._last_result = rows
            return

        if "SELECT count(*) FROM failed_extractions" in q:
            self._last_result = [(len(self.db.failed_extractions),)]
            return

        if "SELECT f.id, f.tenant_id, t.name, f.document_id, f.schema_type, f.error" in q:
            rows = []
            for fe in self.db.failed_extractions:
                tname = self.db.tenants.get(fe["tenant_id"], {}).get("name", "Unknown")
                rows.append(
                    (
                        fe["id"],
                        fe["tenant_id"],
                        tname,
                        fe["document_id"],
                        fe["schema_type"],
                        fe["error"],
                        fe["created_at"],
                    )
                )
            self._last_result = rows
            return

        # Review queue query
        if "WHERE e.needs_review = true" in q:
            rows = []
            for doc_id, ext in self.db.extractions.items():
                if ext.get("needs_review"):
                    tname = self.db.tenants.get(ext["tenant_id"], {}).get("name", "Unknown")
                    rows.append(
                        (
                            doc_id,
                            ext["tenant_id"],
                            tname,
                            ext.get("schema_type", "invoice"),
                            ext.get("schema_version", 1),
                            ext.get("model", "gemini-2.0-flash"),
                            ext.get("fields", {}),
                            ext.get("field_confidence", {}),
                            ext.get("overall_confidence", 0.5),
                            ext.get("needs_review", True),
                            ext.get("created_at", datetime.now(UTC).isoformat()),
                            ext.get("extraction_history", []),
                        )
                    )
            self._last_result = rows
            return

        # Correction SELECT query
        if (
            "SELECT fields, field_confidence, overall_confidence, extraction_history FROM document_extractions WHERE document_id = %s"
            in q
        ):
            doc_id = params[0]
            ext = self.db.extractions.get(doc_id)
            if ext:
                # Check tenant match if passed
                if len(params) > 1 and ext["tenant_id"] != params[1]:
                    self._last_result = []
                    return
                self._last_result = [
                    (
                        ext.get("fields", {}),
                        ext.get("field_confidence", {}),
                        ext.get("overall_confidence", 0.5),
                        ext.get("extraction_history", []),
                    )
                ]
            else:
                self._last_result = []
            return

        # Correction UPDATE query
        if "UPDATE document_extractions SET fields = %s" in q:
            # params: (merged_fields, merged_conf, reviewed_by, updated_history, doc_id, [tenant_id])
            doc_id = params[4]
            ext = self.db.extractions.get(doc_id)
            if ext:
                if len(params) > 5 and ext["tenant_id"] != params[5]:
                    self.rowcount = 0
                    return

                def _unwrap(val):
                    return val.obj if hasattr(val, "obj") else getattr(val, "adapted", val)

                fields = _unwrap(params[0])
                confs = _unwrap(params[1])
                reviewed_by = params[2]
                history = _unwrap(params[3])

                ext["fields"] = fields
                ext["field_confidence"] = confs
                ext["overall_confidence"] = 1.0
                ext["needs_review"] = False
                ext["reviewed_by"] = reviewed_by
                ext["reviewed_at"] = datetime.now(UTC).isoformat()
                ext["extraction_history"] = history
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        self._last_result = []

    def fetchone(self):
        if self._idx < len(self._last_result):
            row = self._last_result[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        return self._last_result


class MockDB:
    def __init__(self):
        self.tenants: dict[UUID, dict] = {}
        self.extractions: dict[UUID, dict] = {}
        self.failed_ingestions: list[dict] = []
        self.failed_extractions: list[dict] = []

    def cursor(self):
        return MockCursor(self)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_admin_console_page_disabled_by_default(monkeypatch):
    settings = get_settings().model_copy(update={"admin_console_enabled": False})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    res = client.get("/admin")
    assert res.status_code == 403
    assert "Admin console is disabled" in res.json()["detail"]


def test_admin_console_page_enabled_returns_html(monkeypatch):
    settings = get_settings().model_copy(update={"admin_console_enabled": True})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)
    res = client.get("/admin")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "KnowledgeForge AI" in res.text
    assert "Platform Admin Console" in res.text
    assert (
        "Review &amp; Correct Extraction" in res.text or "Review & Correct Extraction" in res.text
    )


def test_admin_dlq_inspector(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    user_id = uuid4()
    doc_id = uuid4()

    db.tenants[tenant_id] = {"name": "Test Tenant"}
    db.failed_ingestions.append(
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "filename": "broken.pdf",
            "error_message": "Corrupted header",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    db.failed_extractions.append(
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "document_id": doc_id,
            "schema_type": "invoice",
            "error": "Model hallucinated non-JSON response",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    monkeypatch.setattr(api, "get_connection", lambda: db)
    settings = get_settings().model_copy(update={"admin_console_enabled": True})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)

    # Regular user -> 403 Forbidden
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)
    res_user = client.get("/admin/dlq")
    assert res_user.status_code == 403

    # Platform admin -> 200 OK
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "owner", True)
    res_admin = client.get("/admin/dlq")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["ingestion_dlq_depth"] == 1
    assert data["extraction_dlq_depth"] == 1
    assert len(data["recent_failed_ingestions"]) == 1
    assert data["recent_failed_ingestions"][0]["filename"] == "broken.pdf"
    assert len(data["recent_failed_extractions"]) == 1
    assert data["recent_failed_extractions"][0]["error"] == "Model hallucinated non-JSON response"


def test_tenant_extraction_correction_workflow(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    user_id = uuid4()
    doc_id = uuid4()

    # Low-confidence extraction requiring review
    db.extractions[doc_id] = {
        "tenant_id": tenant_id,
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "fields": {"vendor_name": "Initech?", "total": 100.0},
        "field_confidence": {"vendor_name": 0.45, "total": 0.95},
        "overall_confidence": 0.45,
        "needs_review": True,
        "extraction_history": [],
    }

    monkeypatch.setattr(api, "get_connection", lambda: db)
    client = TestClient(app)

    # 1. Tenant user submits correction
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)
    res = client.post(
        f"/documents/{doc_id}/extraction/correct",
        json={"corrected_fields": {"vendor_name": "Initech Corp."}},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "corrected"

    # Verify state in DB
    updated = db.extractions[doc_id]
    assert updated["fields"]["vendor_name"] == "Initech Corp."
    assert updated["fields"]["total"] == 100.0  # preserved unedited field
    assert updated["field_confidence"]["vendor_name"] == 1.0  # human-verified
    assert updated["overall_confidence"] == 1.0
    assert updated["needs_review"] is False
    assert len(updated["extraction_history"]) == 1
    assert updated["extraction_history"][0]["previous_fields"]["vendor_name"] == "Initech?"
    assert updated["extraction_history"][0]["reviewed_by"] == str(user_id)

    # 2. User from other tenant cannot correct this document (tenant isolation)
    app.dependency_overrides[api.get_current_user] = lambda: (
        user_id,
        other_tenant_id,
        "member",
        False,
    )
    res_cross = client.post(
        f"/documents/{doc_id}/extraction/correct",
        json={"corrected_fields": {"vendor_name": "Hacked"}},
    )
    assert res_cross.status_code == 404


def test_admin_extraction_correction_cross_tenant(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    admin_user_id = uuid4()
    doc_id = uuid4()

    db.extractions[doc_id] = {
        "tenant_id": tenant_id,
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "fields": {"invoice_number": "INV-???"},
        "field_confidence": {"invoice_number": 0.3},
        "overall_confidence": 0.3,
        "needs_review": True,
        "extraction_history": [],
    }

    monkeypatch.setattr(api, "get_connection", lambda: db)
    settings = get_settings().model_copy(update={"admin_console_enabled": True})
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    client = TestClient(app)

    # Non-admin rejected
    app.dependency_overrides[api.get_current_user] = lambda: (uuid4(), tenant_id, "owner", False)
    res_fail = client.put(
        f"/admin/extractions/{doc_id}/correct",
        json={"corrected_fields": {"invoice_number": "INV-12345"}},
    )
    assert res_fail.status_code == 403

    # Platform admin accepted across tenants
    app.dependency_overrides[api.get_current_user] = lambda: (admin_user_id, uuid4(), "owner", True)
    res_ok = client.put(
        f"/admin/extractions/{doc_id}/correct",
        json={"corrected_fields": {"invoice_number": "INV-12345"}},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "corrected"

    assert db.extractions[doc_id]["fields"]["invoice_number"] == "INV-12345"
    assert db.extractions[doc_id]["needs_review"] is False
    assert db.extractions[doc_id]["overall_confidence"] == 1.0


def test_admin_review_queue_endpoint(monkeypatch):
    db = MockDB()
    tenant_id = uuid4()
    admin_id = uuid4()
    doc_id = uuid4()

    db.tenants[tenant_id] = {"name": "Globex Corp"}
    db.extractions[doc_id] = {
        "tenant_id": tenant_id,
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "fields": {"vendor_name": "Globex"},
        "field_confidence": {"vendor_name": 0.4},
        "overall_confidence": 0.4,
        "needs_review": True,
        "extraction_history": [],
    }

    monkeypatch.setattr(api, "get_connection", lambda: db)
    client = TestClient(app)

    # Platform admin requesting all_tenants=True
    app.dependency_overrides[api.get_current_user] = lambda: (admin_id, uuid4(), "owner", True)
    res = client.get("/admin/extractions/review-queue?all_tenants=true")
    assert res.status_code == 200
    data = res.json()
    assert len(data["extractions"]) == 1
    assert data["extractions"][0]["document_id"] == str(doc_id)
    assert data["extractions"][0]["needs_review"] is True
