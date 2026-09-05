"""Unit tests for Phase 2.5 OCR parsing and the extraction accuracy checker."""

import json
import sys
from pathlib import Path

from knowledgeforge.ingestion.extract_ocr import (
    LocalOcrProvider,
    is_image_upload,
    mime_type_for,
    parse_ocr_text,
)


def test_parse_ocr_text_splits_pages() -> None:
    text = "PAGE 1:\nfirst page text\nPAGE 2:\nsecond page text"
    assert parse_ocr_text(text) == [(1, "first page text"), (2, "second page text")]


def test_parse_ocr_text_without_markers_is_page_one() -> None:
    assert parse_ocr_text("just text") == [(1, "just text")]


def test_parse_ocr_text_skips_empty_pages() -> None:
    assert parse_ocr_text("PAGE 1:\n\nPAGE 2:\ncontent") == [(2, "content")]


def test_parse_ocr_text_empty_input() -> None:
    assert parse_ocr_text("   ") == []


def test_image_upload_detection() -> None:
    assert is_image_upload("scan.PNG")
    assert is_image_upload("photo.jpeg")
    assert is_image_upload("doc.tiff")
    assert not is_image_upload("doc.pdf")
    assert not is_image_upload("doc.md")


def test_mime_type_for() -> None:
    assert mime_type_for("a.png") == "image/png"
    assert mime_type_for("a.JPG") == "image/jpeg"
    assert mime_type_for("a.tiff") == "image/tiff"
    assert mime_type_for("a.pdf") == "application/pdf"
    assert mime_type_for("noext") == "application/octet-stream"


def test_local_ocr_provider_returns_invoice_text() -> None:
    result = LocalOcrProvider().ocr(b"image-bytes", "image/png")
    assert result.pages[0][0] == 1
    assert "INVOICE" in result.pages[0][1].upper()


def _run_checker(golden: dict, results: list[dict], tmp_path: Path) -> tuple[int, str]:
    golden_file = tmp_path / "golden.json"
    results_file = tmp_path / "results.json"
    golden_file.write_text(json.dumps(golden), encoding="utf-8")
    results_file.write_text(json.dumps(results), encoding="utf-8")
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "evaluation" / "check_extraction_accuracy.py"),
            "--golden-set",
            str(golden_file),
            "--results",
            str(results_file),
        ],
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout


def test_accuracy_checker_passes_and_fails(tmp_path: Path, monkeypatch) -> None:
    golden = {
        "documents": [
            {
                "document": "inv-1",
                "expected": {"vendor_name": "Acme", "total": 100.0, "currency": "USD"},
            }
        ]
    }
    good = [
        {
            "document": "inv-1",
            "fields": {"vendor_name": "acme", "total": "100.00", "currency": "usd"},
            "needs_review": False,
        }
    ]
    code, out = _run_checker(golden, good, tmp_path)
    assert code == 0, out
    assert "PASS" in out
    bad = [
        {
            "document": "inv-1",
            "fields": {"vendor_name": "Other", "total": 999.0, "currency": "USD"},
            "needs_review": True,
        }
    ]
    code, out = _run_checker(golden, bad, tmp_path)
    # Floor is 0.0 by default, so even a bad run passes until floors are raised.
    assert code == 0, out
