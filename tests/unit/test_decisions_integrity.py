from pathlib import Path
import pytest

from scripts.verify_decisions import verify_decisions_integrity


def test_decisions_numeric_claims_are_grounded():
    violations = verify_decisions_integrity()
    assert not violations, f"docs/decisions.md has ungrounded numeric claims: {violations}"


def test_decisions_flags_uncited_numeric_claim(tmp_path: Path):
    fake_decisions = tmp_path / "decisions.md"
    fake_decisions.write_text(
        "# Decisions\n\n"
        "## 2026-09-08 — Fabricated Metric Benchmark\n\n"
        "We achieved Hit@5 = 0.950 and latency in 25 ms across all queries.\n",
        encoding="utf-8",
    )
    violations = verify_decisions_integrity(fake_decisions)
    assert len(violations) == 1
    assert "Uncertified numeric claim in section '## 2026-09-08 — Fabricated Metric Benchmark'" in violations[0]


def test_decisions_accepts_grounded_citation(tmp_path: Path):
    fake_decisions = tmp_path / "decisions.md"
    fake_decisions.write_text(
        "# Decisions\n\n"
        "## 2026-09-08 — Grounded Benchmark\n\n"
        "Measured Hit@5 = 0.825 evaluated by `python evaluation/run_phase12_eval.py`.\n",
        encoding="utf-8",
    )
    violations = verify_decisions_integrity(fake_decisions)
    assert not violations


def test_decisions_accepts_unmeasured_or_retracted_marker(tmp_path: Path):
    fake_decisions = tmp_path / "decisions.md"
    fake_decisions.write_text(
        "# Decisions\n\n"
        "## 2026-09-08 — Pending Decision\n\n"
        "Status: NOT YET MEASURED. Previous Hit@5 = 0.950 claim was RETRACTED.\n",
        encoding="utf-8",
    )
    violations = verify_decisions_integrity(fake_decisions)
    assert not violations


def test_decisions_flags_phantom_citation(tmp_path: Path):
    fake_decisions = tmp_path / "decisions.md"
    fake_decisions.write_text(
        "# Decisions\n\n"
        "## 2026-09-08 — Phantom Citation Benchmark\n\n"
        "Hit@5 = 0.999 verified by `python evaluation/phantom_runner_never_existed.py`.\n",
        encoding="utf-8",
    )
    violations = verify_decisions_integrity(fake_decisions)
    assert len(violations) == 1
    assert "Phantom citation in section '## 2026-09-08 — Phantom Citation Benchmark'" in violations[0]
    assert "phantom_runner_never_existed.py" in violations[0]

