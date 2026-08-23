from fastapi.testclient import TestClient

from knowledgeforge.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_versioned_api_routes_are_documented() -> None:
    response = TestClient(app).get("/openapi.json")
    paths = response.json()["paths"]
    assert "/ask" in paths
    assert "/v1/ask" in paths
    assert "/v1/auth/login" in paths
