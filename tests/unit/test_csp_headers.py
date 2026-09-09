"""Unit tests for Fix 5: Content-Security-Policy headers on document viewer and admin console."""

import re
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.main import app
from knowledgeforge.ingestion.store import DocumentContentData
from knowledgeforge.security import auth


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_document_viewer_csp_header_and_nonce_matching(client: TestClient, monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    monkeypatch.setattr(auth, "get_current_user", lambda: (user_id, tenant_id, "member", False))

    mock_data = DocumentContentData(
        document_id=doc_id,
        title="Security Policy",
        doc_type="policy",
        storage_uri=None,
        chunks=[
            {
                "id": chunk_id,
                "text": "Data retention policy text.",
                "page": 1,
                "section": "Retention",
                "start_char": 0,
                "end_char": 27,
                "bounding_boxes": [],
            }
        ],
    )

    with patch("knowledgeforge.api.get_connection", lambda: nullcontext(MagicMock())), \
         patch("knowledgeforge.api.get_document_content_and_chunks", return_value=mock_data):
        response = client.get(f"/documents/{doc_id}/view")
        assert response.status_code == 200

        csp = response.headers.get("Content-Security-Policy")
        assert csp is not None
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

        # Extract nonce from CSP header
        nonce_match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)
        assert nonce_match is not None, f"Nonce missing from CSP: {csp}"
        header_nonce = nonce_match.group(1)

        # Verify nonce in <script> tag matches header
        html = response.text
        script_match = re.search(r'<script nonce="([^"]+)">', html)
        assert script_match is not None, "Script tag missing nonce in document viewer"
        assert script_match.group(1) == header_nonce

        # Verify zero inline event handlers (onclick, onchange, onerror, onload)
        assert not re.search(r'\bon[a-z]+\s*=', html, re.IGNORECASE)


def test_admin_console_csp_header_and_nonce_matching(client: TestClient, monkeypatch):
    from knowledgeforge.config import get_settings
    monkeypatch.setattr(get_settings(), "admin_console_enabled", True)

    response = client.get("/admin")
    assert response.status_code == 200

    csp = response.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp

    nonce_match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)
    assert nonce_match is not None, f"Nonce missing from CSP: {csp}"
    header_nonce = nonce_match.group(1)

    html = response.text
    script_match = re.search(r'<script nonce="([^"]+)">', html)
    assert script_match is not None, "Script tag missing nonce in admin console"
    assert script_match.group(1) == header_nonce

    # Verify zero inline event handlers (onclick, onchange, onerror, onload)
    assert not re.search(r'\bon[a-z]+\s*=', html, re.IGNORECASE)
