"""Unit tests for Process Fix B: Mechanical task summary generator."""

from pathlib import Path
import pytest

from scripts.generate_task_summary import generate_summary, parse_tasks


def test_parse_real_task_file():
    sections = parse_tasks()
    assert len(sections) == 10
    first_item = list(sections.keys())[0]
    assert "Item 1" in first_item
    summary = generate_summary(sections)
    assert summary["total_sections"] == 10
    assert summary["total_subtasks"] == 36
    assert summary["status_totals"]["Done"] >= 6
    assert summary["status_totals"]["Verified"] >= 6


def test_status_derivation_logic(tmp_path: Path):
    fake_task = tmp_path / "task.md"
    fake_task.write_text(
        "# Tasks\n\n"
        "## Item 1 — Done Item\n"
        "- [Done] Task 1A\n"
        "- [Done] Task 1B\n\n"
        "## Item 2 — Verified Item\n"
        "- [Done] Task 2A\n"
        "- [Verified] Task 2B\n\n"
        "## Item 3 — Gated Item\n"
        "- [Verified] Task 3A\n"
        "- [Gated] Task 3B\n\n"
        "## Item 4 — Retracted Item\n"
        "- [Retracted] Task 4A\n"
        "- [Implemented] Task 4B\n\n"
        "## Item 5 — Implemented Item\n"
        "- [Implemented] Task 5A\n",
        encoding="utf-8",
    )

    sections = parse_tasks(fake_task)
    assert len(sections) == 5
    summary = generate_summary(sections)

    sec_map = {s["section"]: s["overall_status"] for s in summary["sections"]}
    assert sec_map["Item 1 — Done Item"] == "Done"
    assert sec_map["Item 2 — Verified Item"] == "Verified"
    assert sec_map["Item 3 — Gated Item"] == "Gated (External)"
    assert sec_map["Item 4 — Retracted Item"] == "Retracted / Reprioritized"
    assert sec_map["Item 5 — Implemented Item"] == "Implemented"
