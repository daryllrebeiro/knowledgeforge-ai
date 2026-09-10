"""Unit tests for Phase 8 Item 2: Knowledge Graph Visualization View."""

import re
from uuid import uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app

client = TestClient(app)


def test_graph_view_requires_auth():
    # Calling without auth header should fail with 401 or 403
    app.dependency_overrides.clear()
    res = client.get("/graph/view")
    assert res.status_code in (401, 403)


def test_graph_view_renders_html_with_csp_nonce(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    res = client.get("/graph/view")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

    # Check Content-Security-Policy header
    csp_header = res.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp_header
    assert "script-src 'self' 'nonce-" in csp_header
    assert "frame-ancestors 'none'" in csp_header

    # Extract nonce from CSP header
    match = re.search(r"'nonce-([^']+)'", csp_header)
    assert match is not None
    csp_nonce = match.group(1)

    # Check that the HTML body includes script with this nonce
    body = res.text
    assert f'<script nonce="{csp_nonce}">' in body
    assert "graph-viewport" in body
    assert "/graph/query" in body
