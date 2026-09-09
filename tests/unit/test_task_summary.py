"""Unit tests for Process Fix B: Mechanical task summary generator."""

from pathlib import Path

from scripts.generate_task_summary import generate_summary, parse_tasks


def test_parse_real_task_file():
    sections = parse_tasks()
    assert len(sections) >= 10
    first_item = list(sections.keys())[0]
    assert "Item 1" in first_item
    summary = generate_summary(sections)
    assert summary["total_sections"] >= 10
    assert summary["total_subtasks"] >= 36
    assert summary["status_totals"]["Verified"] >= 10


def test_status_derivation_logic(tmp_path: Path):
    fake_task = tmp_path / "task.md"
    fake_task.write_text(
        "# Tasks\n\n"
        "## Item 1 — Done Item\n"
        "- [Done] Task 1A (Signed-off-by: lead-auditor)\n"
        "- [Done] Task 1B (Signed-off-by: security-engineer)\n\n"
        "## Item 2 — Verified Item\n"
        "- [Done] Task 2A (Signed-off-by: reviewer)\n"
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


def test_unsigned_done_claim_demoted_and_flagged(tmp_path: Path):
    fake_task = tmp_path / "task.md"
    fake_task.write_text(
        "# Tasks\n\n## Item 1 — Self-Certified Done\n- [Done] Self certified without signoff\n",
        encoding="utf-8",
    )
    from scripts.generate_task_summary import validate_task_integrity

    raw_sections = {"Item 1": [{"status": "Done", "description": "Self certified without signoff"}]}
    violations = validate_task_integrity(raw_sections)
    assert len(violations) == 1
    assert "Ground Rule 5 violation" in violations[0]

    parsed = parse_tasks(fake_task)
    assert parsed["Item 1 — Self-Certified Done"][0]["status"] == "Verified"
