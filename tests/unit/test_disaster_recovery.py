"""Unit tests for Item 14: Disaster Recovery and Database Integrity Verification."""

import pytest

from scripts.backup_restore_check import DatabaseIntegrityReport, check_integrity


class MockCursor:
    def __init__(self, table_counts: dict[str, int], all_embeddings_valid: bool = True):
        self.table_counts = table_counts
        self.all_embeddings_valid = all_embeddings_valid
        self._last_result = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split()).lower()
        if "select count(*) from chunks where embedding is not null" in q:
            chunks = self.table_counts.get("chunks", 0)
            self._last_result = chunks if self.all_embeddings_valid else max(0, chunks - 1)
        elif "select count(*) from documents" in q:
            self._last_result = self.table_counts.get("documents", 0)
        elif "select count(*) from chunks" in q:
            self._last_result = self.table_counts.get("chunks", 0)
        elif "select count(*) from document_extractions" in q:
            self._last_result = self.table_counts.get("document_extractions", 0)
        elif "select count(*) from users" in q:
            self._last_result = self.table_counts.get("users", 0)
        elif "select count(*) from conversations" in q:
            self._last_result = self.table_counts.get("conversations", 0)
        elif "select count(*) from tenants" in q:
            self._last_result = self.table_counts.get("tenants", 0)
        else:
            self._last_result = 0

    def fetchone(self):
        return (self._last_result,)


class MockConnection:
    def __init__(self, table_counts: dict[str, int], all_embeddings_valid: bool = True):
        self.table_counts = table_counts
        self.all_embeddings_valid = all_embeddings_valid

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return MockCursor(self.table_counts, self.all_embeddings_valid)


def test_check_integrity_success():
    counts = {
        "documents": 150,
        "chunks": 920,
        "document_extractions": 45,
        "users": 12,
        "conversations": 60,
        "tenants": 4,
    }
    conn = MockConnection(counts, all_embeddings_valid=True)
    report = check_integrity(conn)

    assert report.documents == 150
    assert report.chunks == 920
    assert report.embeddings_valid is True
    assert report.extractions == 45
    assert report.users == 12
    assert report.conversations == 60
    assert report.tenants == 4
    d = report.as_dict()
    assert d["documents"] == 150
    assert d["embeddings_valid"] is True


def test_check_integrity_detects_null_embeddings():
    counts = {
        "documents": 10,
        "chunks": 50,
        "document_extractions": 5,
        "users": 2,
        "conversations": 1,
        "tenants": 1,
    }
    conn = MockConnection(counts, all_embeddings_valid=False)
    report = check_integrity(conn)

    assert report.chunks == 50
    assert report.embeddings_valid is False


def test_database_integrity_report_equality():
    r1 = DatabaseIntegrityReport(10, 50, True, 5, 2, 1, 1)
    r2 = DatabaseIntegrityReport(10, 50, True, 5, 2, 1, 1)
    r3 = DatabaseIntegrityReport(10, 49, True, 5, 2, 1, 1)

    assert r1 == r2
    assert r1 != r3
