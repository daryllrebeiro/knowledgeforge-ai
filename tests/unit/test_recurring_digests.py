"""Unit tests for Phase 8 Item 3: Recurring Activity Digests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.main import app
from knowledgeforge.worker.digest_job import get_tenant_activity_stats, run_digest_job

client = TestClient(app)


def test_get_tenant_activity_stats():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    since = datetime.now(UTC) - timedelta(days=7)

    # 4 queries: ingested, extracted, failed, queries
    mock_cursor.fetchone.side_effect = [
        (12,),  # ingested
        (8,),   # extracted
        (1,),   # failed
        (45,),  # queries
    ]

    stats = get_tenant_activity_stats(mock_conn, tenant_id, since)
    assert stats["ingested_count"] == 12
    assert stats["extracted_count"] == 8
    assert stats["failed_count"] == 1
    assert stats["queries_count"] == 45


def test_run_digest_job_dispatches_email_and_updates_timestamp():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    # Mock settings row: tenant_id, frequency, recipients, last_sent_at, tenant_name
    mock_cursor.fetchall.return_value = [
        (tenant_id, "daily", ["admin@acme.corp", "sec@acme.corp"], None, "Acme Corp")
    ]
    # Mock stats counts
    mock_cursor.fetchone.side_effect = [(5,), (3,), (0,), (20,)]

    with patch("knowledgeforge.worker.digest_job.send_digest_email") as mock_send_email:
        sent = run_digest_job(mock_conn, dry_run=False)
        assert sent == 1
        assert mock_send_email.call_count == 2
        mock_send_email.assert_any_call(
            "admin@acme.corp",
            tenant_name="Acme Corp",
            period="daily",
            ingested_count=5,
            extracted_count=3,
            failed_count=0,
            queries_count=20,
        )

    # Verify last_sent_at update query executed
    update_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "UPDATE tenant_digest_settings" in call[0][0]
    ]
    assert len(update_calls) == 1
    assert update_calls[0][0][1][1] == tenant_id


def test_run_digest_job_skips_when_recent_and_not_forced():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    # Last sent 1 hour ago for a daily digest
    recent_sent = datetime.now(UTC) - timedelta(hours=1)
    mock_cursor.fetchall.return_value = [
        (tenant_id, "daily", ["admin@acme.corp"], recent_sent, "Acme Corp")
    ]

    with patch("knowledgeforge.worker.digest_job.send_digest_email") as mock_send_email:
        sent = run_digest_job(mock_conn, force=False)
        assert sent == 0
        mock_send_email.assert_not_called()

        # With force=True, it should send
        mock_cursor.fetchone.side_effect = [(2,), (1,), (0,), (10,)]
        sent_forced = run_digest_job(mock_conn, force=True)
        assert sent_forced == 1
        assert mock_send_email.call_count == 1


def test_digest_settings_api_routes(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    class FakeConnection:
        def __enter__(self):
            return mock_conn
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(api, "get_connection", lambda: FakeConnection())

    # 1. GET /tenant/digest
    mock_cursor.fetchone.return_value = (
        tenant_id,
        "weekly",
        True,
        ["lead@example.com"],
        datetime.now(UTC),
        datetime.now(UTC),
    )

    res = client.get("/tenant/digest")
    assert res.status_code == 200
    data = res.json()
    assert data["frequency"] == "weekly"
    assert data["enabled"] is True
    assert data["recipient_emails"] == ["lead@example.com"]

    # 2. PUT /tenant/digest
    mock_cursor.fetchone.return_value = (
        tenant_id,
        "daily",
        True,
        ["lead@example.com", "ops@example.com"],
        None,
        datetime.now(UTC),
    )
    res = client.put(
        "/tenant/digest",
        json={
            "frequency": "daily",
            "enabled": True,
            "recipient_emails": ["lead@example.com", "ops@example.com"],
        },
    )
    assert res.status_code == 200
    assert res.json()["frequency"] == "daily"
