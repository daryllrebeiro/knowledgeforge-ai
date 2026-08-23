from uuid import UUID

import pytest

from knowledgeforge import api
from knowledgeforge.main import app


@pytest.fixture(autouse=True)
def authenticated_test_client(monkeypatch):
    app.dependency_overrides[api.get_current_user] = lambda: (
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    )
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "count_documents", lambda *args, **kwargs: 0)
    yield
    app.dependency_overrides.clear()
