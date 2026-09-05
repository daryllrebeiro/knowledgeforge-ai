"""Refresh tokens and API keys (F4)."""

from contextlib import nullcontext
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app
from knowledgeforge.security.refresh import InvalidRefreshToken

USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
KEY_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def test_refresh_rotates_and_returns_new_tokens(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))

    def fake_rotate(connection, token):
        assert token == "presented-token"
        return USER_ID, TENANT_ID, "new-refresh-token"

    monkeypatch.setattr(api, "rotate_refresh_token", fake_rotate)

    response = TestClient(app).post("/auth/refresh", json={"refresh_token": "presented-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] == "new-refresh-token"
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_refresh_rejects_replayed_tokens(monkeypatch) -> None:
    def fail(connection, token):
        raise InvalidRefreshToken("replay")

    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "rotate_refresh_token", fail)

    response = TestClient(app).post("/auth/refresh", json={"refresh_token": "replayed-token"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid refresh token"}


def test_logout_revokes_the_token_family(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    revoked: list[str] = []
    monkeypatch.setattr(
        api, "revoke_refresh_family", lambda connection, token: revoked.append(token)
    )

    response = TestClient(app).post("/auth/logout", json={"refresh_token": "some-token"})

    assert response.status_code == 204
    assert revoked == ["some-token"]


def test_create_api_key_returns_plaintext_once(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "create_api_key", lambda *args, **kwargs: (KEY_ID, "kf_secret"))

    response = TestClient(app).post("/api-keys", json={"name": "CI pipeline"})

    assert response.status_code == 201
    assert response.json() == {
        "key_id": str(KEY_ID),
        "name": "CI pipeline",
        "key": "kf_secret",
        "key_prefix": "kf_secret"[:12],
    }


def test_list_api_keys_hides_the_secret(monkeypatch) -> None:
    from knowledgeforge.security.api_keys import ApiKeyRow

    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api,
        "list_api_keys",
        lambda *args, **kwargs: [
            ApiKeyRow(
                key_id=KEY_ID,
                name="CI pipeline",
                key_prefix="kf_secret"[:4],
                created_at="2026-09-03T00:00:00+00:00",
                last_used_at=None,
                revoked=False,
            )
        ],
    )

    response = TestClient(app).get("/api-keys")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["key_id"] == str(KEY_ID)
    assert "key" not in body[0]
    assert body[0]["key_prefix"] == "kf_s"
    assert body[0]["revoked"] is False


def test_delete_api_key_returns_404_for_missing(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "revoke_api_key", lambda *args, **kwargs: False)

    response = TestClient(app).delete(f"/api-keys/{uuid4()}")

    assert response.status_code == 404


def test_api_key_authenticates_protected_routes(monkeypatch) -> None:
    """The X-API-Key header is a full alternative to the bearer token."""
    from knowledgeforge.security import auth

    monkeypatch.setattr(auth, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(auth, "verify_api_key", lambda connection, key: (USER_ID, TENANT_ID))
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "list_documents", lambda *args, **kwargs: [])
    app.dependency_overrides.pop(api.get_current_user, None)

    try:
        response = TestClient(app).get("/documents", headers={"X-API-Key": "kf_secret"})
    finally:
        app.dependency_overrides[api.get_current_user] = lambda: (None, None)

    assert response.status_code == 200
    assert response.json() == {"documents": [], "limit": 50, "offset": 0}


def test_invalid_api_key_is_rejected(monkeypatch) -> None:
    from knowledgeforge.security import auth

    monkeypatch.setattr(auth, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(auth, "verify_api_key", lambda connection, key: None)
    app.dependency_overrides.pop(api.get_current_user, None)

    try:
        response = TestClient(app).get("/documents", headers={"X-API-Key": "kf_wrong"})
    finally:
        app.dependency_overrides[api.get_current_user] = lambda: (None, None)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
