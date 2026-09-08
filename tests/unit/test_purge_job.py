"""Unit tests for unverified accounts scheduled purge job."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledgeforge.security import purge_job


class MockPurgeCursor:
    def __init__(self, db: "MockPurgeDB"):
        self.db = db
        self._last_result = []
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())
        self._idx = 0

        if "SELECT count(*) FROM users" in q and "email_verified = false" in q:
            cutoff = params[0]
            count = sum(
                1 for u in self.db.users.values()
                if not u["email_verified"] and u["created_at"] < cutoff
            )
            self._last_result = [(count,)]
            return

        if "DELETE FROM users" in q and "email_verified = false" in q:
            cutoff = params[0]
            deleted = []
            for uid, u in list(self.db.users.items()):
                if not u["email_verified"] and u["created_at"] < cutoff:
                    deleted.append((uid,))
                    del self.db.users[uid]
            self._last_result = deleted
            return

        self._last_result = []

    def fetchone(self):
        if self._idx < len(self._last_result):
            row = self._last_result[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        res = self._last_result[self._idx:]
        self._idx = len(self._last_result)
        return res


class MockPurgeDB:
    def __init__(self):
        self.users = {}


class MockPurgeConnection:
    def __init__(self, db: MockPurgeDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass

    def cursor(self):
        return MockPurgeCursor(self.db)


def test_purge_job_dry_run(monkeypatch):
    db = MockPurgeDB()
    now = datetime.now(UTC)
    stale_user = uuid4()
    recent_user = uuid4()

    db.users[stale_user] = {"email_verified": False, "created_at": now - timedelta(days=40)}
    db.users[recent_user] = {"email_verified": False, "created_at": now - timedelta(days=5)}

    @contextmanager
    def mock_conn():
        yield MockPurgeConnection(db)

    monkeypatch.setattr(purge_job, "get_connection", mock_conn)

    count = purge_job.run_purge(max_age_days=30, dry_run=True)
    assert count == 1
    # Dry run must not mutate database
    assert stale_user in db.users
    assert recent_user in db.users


def test_purge_job_execution(monkeypatch):
    db = MockPurgeDB()
    now = datetime.now(UTC)
    stale_user = uuid4()
    recent_user = uuid4()
    verified_user = uuid4()

    db.users[stale_user] = {"email_verified": False, "created_at": now - timedelta(days=40)}
    db.users[recent_user] = {"email_verified": False, "created_at": now - timedelta(days=5)}
    db.users[verified_user] = {"email_verified": True, "created_at": now - timedelta(days=60)}

    @contextmanager
    def mock_conn():
        yield MockPurgeConnection(db)

    monkeypatch.setattr(purge_job, "get_connection", mock_conn)

    purged = purge_job.run_purge(max_age_days=30, dry_run=False)
    assert purged == 1
    assert stale_user not in db.users
    assert recent_user in db.users
    assert verified_user in db.users


def test_purge_job_invalid_days():
    with pytest.raises(ValueError, match="max_age_days must be positive"):
        purge_job.run_purge(max_age_days=0)


def test_purge_job_main_success(monkeypatch, capsys):
    db = MockPurgeDB()
    now = datetime.now(UTC)
    stale_user = uuid4()
    db.users[stale_user] = {"email_verified": False, "created_at": now - timedelta(days=40)}

    @contextmanager
    def mock_conn():
        yield MockPurgeConnection(db)

    monkeypatch.setattr(purge_job, "get_connection", mock_conn)

    exit_code = purge_job.main(["--max-age-days", "30"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Unverified account purge completed: purged 1 user(s)" in captured.out


def test_purge_job_main_failure(monkeypatch, capsys):
    @contextmanager
    def failing_conn():
        raise RuntimeError("Database connection timed out")
        yield None

    monkeypatch.setattr(purge_job, "get_connection", failing_conn)

    exit_code = purge_job.main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "ERROR: unverified account purge failed" in captured.err
