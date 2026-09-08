"""Unit test for migration sequence, naming format, and content sanity."""

from pathlib import Path
from scripts.verify_migrations import verify_migrations


def test_migrations_are_valid_and_contiguous():
    errors = verify_migrations()
    assert not errors, f"Migration sanity errors: {errors}"


def test_verify_migrations_detects_non_contiguous(tmp_path):
    # Create test directory with non-contiguous migrations
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "003_third.sql").write_text("SELECT 3;", encoding="utf-8")

    errors = verify_migrations(tmp_path)
    assert any("Non-contiguous migration numbers" in e for e in errors)


def test_verify_migrations_detects_invalid_naming(tmp_path):
    (tmp_path / "bad_name.sql").write_text("SELECT 1;", encoding="utf-8")
    errors = verify_migrations(tmp_path)
    assert any("Invalid migration filename format" in e for e in errors)


def test_verify_migrations_detects_empty_file(tmp_path):
    (tmp_path / "001_empty.sql").write_text("   \n", encoding="utf-8")
    errors = verify_migrations(tmp_path)
    assert any("is empty" in e for e in errors)
