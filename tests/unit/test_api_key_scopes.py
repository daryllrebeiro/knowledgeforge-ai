"""Unit tests for Fix 6: API key permission scoping and enforcement."""

import io
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.main import app
from knowledgeforge.security.api_keys import ApiKeyRow, create_api_key, list_api_keys, verify_api_key


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_create_and_verify_scoped_api_key():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    user_id = uuid4()
    key_id = uuid4()
    mock_cursor.fetchone.return_value = (key_id,)

    # 1. Create with specific scopes
    k_id, plaintext = create_api_key(
        mock_conn,
        tenant_id=tenant_id,
        user_id=user_id,
        name="Ingestion Agent",
        scopes=["write:documents", "read:documents"],
    )
    assert plaintext.startswith("kf_")
    query, params = mock_cursor.execute.call_args_list[0][0]
    assert "scopes" in query
    assert params[5] == ["write:documents", "read:documents"]

    # 2. Verify returned scopes
    mock_cursor.fetchone.return_value = (user_id, tenant_id, ["write:documents", "read:documents"])
    ident = verify_api_key(mock_conn, plaintext)
    assert ident is not None
    assert ident[0] == user_id
    assert ident[1] == tenant_id
    assert ident[2] == ["write:documents", "read:documents"]


def test_scoped_api_key_enforcement_in_endpoints(client: TestClient):
    user_id = uuid4()
    tenant_id = uuid4()

    # Remove default test fixture override so real API key authentication flow executes
    from knowledgeforge import api
    if api.get_current_user in app.dependency_overrides:
        del app.dependency_overrides[api.get_current_user]

    # Case A: API key with only 'write:documents' scope
    def mock_verify_write_only(conn, key):
        if key == "kf_write_only":
            return user_id, tenant_id, ["write:documents"]
        elif key == "kf_ask_only":
            return user_id, tenant_id, ["query:ask"]
        elif key == "kf_wildcard":
            return user_id, tenant_id, ["*"]
        return None

    with patch("knowledgeforge.security.auth.verify_api_key", side_effect=mock_verify_write_only), \
         patch("knowledgeforge.security.auth.get_user_role_for_tenant", return_value="owner"), \
         patch("knowledgeforge.security.auth.get_user_platform_admin", return_value=False), \
         patch("knowledgeforge.security.auth.get_connection", lambda: nullcontext(MagicMock())):

        # 1. write-only key attempts /ask -> 403 Forbidden
        r_ask_forbidden = client.post(
            "/ask",
            headers={"x-api-key": "kf_write_only"},
            json={"question": "What is our retention?"},
        )
        assert r_ask_forbidden.status_code == 403
        assert "missing required scope: query:ask" in r_ask_forbidden.json()["detail"]

        # 2. write-only key attempts /admin/schemas -> 403 Forbidden
        r_schema_forbidden = client.get(
            "/admin/schemas",
            headers={"x-api-key": "kf_write_only"},
        )
        assert r_schema_forbidden.status_code == 403
        assert "missing required scope: admin:schemas" in r_schema_forbidden.json()["detail"]

        # 3. ask-only key attempts /documents upload -> 403 Forbidden
        file_data = io.BytesIO(b"Confidential Report Content")
        r_upload_forbidden = client.post(
            "/documents",
            headers={"x-api-key": "kf_ask_only"},
            files={"file": ("report.txt", file_data, "text/plain")},
        )
        assert r_upload_forbidden.status_code == 403
        assert "missing required scope: write:documents" in r_upload_forbidden.json()["detail"]

        # 4. wildcard '*' key is permitted through scope check
        with patch("knowledgeforge.api.get_document_detail", return_value=None), \
             patch("knowledgeforge.api.get_connection", lambda: nullcontext(MagicMock())):
            r_doc_allowed = client.get(
                f"/documents/{uuid4()}",
                headers={"x-api-key": "kf_wildcard"},
            )
            # Not 403 scope error; 404 since document not found
            assert r_doc_allowed.status_code == 404
