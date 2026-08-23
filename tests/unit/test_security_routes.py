from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app


def test_ask_requires_authentication() -> None:
    app.dependency_overrides.pop(api.get_current_user, None)
    try:
        response = TestClient(app).post("/ask", json={"question": "private"})
    finally:
        app.dependency_overrides[api.get_current_user] = lambda: (None, None)

    assert response.status_code == 401
