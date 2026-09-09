"""Unit tests for live verification harnesses in dry-run mode."""

import sys
from io import StringIO

from scripts.verify_alerts_live import main as alerts_main
from scripts.verify_dr_live import main as dr_main
from scripts.verify_infra_live import main as infra_main


def test_verify_infra_live_dry_run(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    code = infra_main(["--dry-run", "--api-url", "http://test-api:8000"])
    assert code == 0
    output = captured.getvalue()
    assert "[DRY RUN] Live Infrastructure Verification Plan" in output
    assert "http://test-api:8000" in output
    assert "Configuration syntax and plan verified successfully" in output


def test_verify_alerts_live_dry_run(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    code = alerts_main(["--dry-run", "--project", "test-project-123"])
    assert code == 0
    output = captured.getvalue()
    assert "[DRY RUN] Cloud Monitoring Alert Verification Plan" in output
    assert "test-project-123" in output
    assert "KnowledgeForge: Gemini Circuit Breaker Tripped" in output
    assert "All policy schemas, metric descriptors, and thresholds verified" in output


def test_verify_dr_live_dry_run(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    code = dr_main(["--dry-run"])
    assert code == 0
    output = captured.getvalue()
    assert "[DRY RUN] Disaster Recovery & Backup Integrity Verification Plan" in output
    assert "Checksum engine validated: SHA-256 test digest" in output
    assert "Disaster recovery verification simulation passed successfully" in output
