"""Every protected route must reject unauthenticated calls with 401 (R4.9)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app

PROTECTED_ROUTE_CASES = [
    ("post", "/documents", {"files": {"file": ("a.pdf", b"x", "application/pdf")}}),
    ("post", "/documents/batch", {"files": [("files", ("a.pdf", b"x", "application/pdf"))]}),
    ("get", "/documents", None),
    ("get", "/ingestions/failed", None),
    ("get", "/admin/usage", None),
    ("post", "/ask", {"json": {"question": "private"}}),
    ("post", "/ask/stream", {"json": {"question": "private"}}),
    ("post", "/conversations", {"json": {"title": "private"}}),
    ("get", "/conversations", None),
    ("get", "/api-keys", None),
    ("post", "/api-keys", {"json": {"name": "private"}}),
    ("delete", "/auth/account", None),
]


def _unauthenticated(method: str, url: str, kwargs: dict | None):
    """Issue a request with the authenticated-user override removed."""
    app.dependency_overrides.pop(api.get_current_user, None)
    try:
        return getattr(TestClient(app), method)(url, **(kwargs or {}))
    finally:
        app.dependency_overrides[api.get_current_user] = lambda: (None, None)


@pytest.mark.parametrize(("method", "url", "kwargs"), PROTECTED_ROUTE_CASES)
def test_protected_route_requires_authentication(method: str, url: str, kwargs: dict) -> None:
    response = _unauthenticated(method, url, kwargs)
    assert response.status_code == 401


def test_document_scoped_routes_require_authentication() -> None:
    document_id = uuid4()
    response = _unauthenticated("get", f"/documents/{document_id}", None)
    assert response.status_code == 401

    response = _unauthenticated("delete", f"/documents/{document_id}", None)
    assert response.status_code == 401


def test_conversation_scoped_routes_require_authentication() -> None:
    conversation_id = uuid4()
    response = _unauthenticated("get", f"/conversations/{conversation_id}", None)
    assert response.status_code == 401

    response = _unauthenticated("delete", f"/conversations/{conversation_id}", None)
    assert response.status_code == 401
