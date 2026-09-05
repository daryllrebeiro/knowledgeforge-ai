"""The coverage ratchet gate (F6): no PR may decrease coverage."""

import json
import sys

from scripts import coverage_ratchet


def _write_report(path, percent: float) -> None:
    path.write_text(json.dumps({"totals": {"percent_covered": percent}}), encoding="utf-8")


def _prepare(monkeypatch, tmp_path, floor: str, percent: float):
    floor_file = tmp_path / "floor"
    floor_file.write_text(floor, encoding="utf-8")
    monkeypatch.setattr(coverage_ratchet, "FLOOR_PATH", floor_file)
    report = tmp_path / "coverage.json"
    _write_report(report, percent)
    monkeypatch.setattr(sys, "argv", ["coverage_ratchet.py", str(report)])
    return floor_file


def test_fails_when_coverage_drops_below_floor(monkeypatch, tmp_path, capsys) -> None:
    _prepare(monkeypatch, tmp_path, "50.00", 41.2)

    assert coverage_ratchet.main() == 1
    assert "Coverage regression" in capsys.readouterr().err


def test_passes_at_the_floor(monkeypatch, tmp_path) -> None:
    _prepare(monkeypatch, tmp_path, "50.00", 50.0)

    assert coverage_ratchet.main() == 0


def test_tolerance_absorbs_measurement_jitter(monkeypatch, tmp_path) -> None:
    # 49.97 is within the 0.05pp tolerance of a 50.00 floor.
    _prepare(monkeypatch, tmp_path, "50.00", 49.97)

    assert coverage_ratchet.main() == 0


def _update_argv(monkeypatch, report_path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["coverage_ratchet.py", str(report_path), "--update"],
    )


def test_update_raises_the_floor(monkeypatch, tmp_path) -> None:
    floor_file = _prepare(monkeypatch, tmp_path, "50.00", 61.234)
    _update_argv(monkeypatch, tmp_path / "coverage.json")

    assert coverage_ratchet.main() == 0
    assert floor_file.read_text(encoding="utf-8") == "61.23\n"


def test_update_never_lowers_the_floor(monkeypatch, tmp_path) -> None:
    floor_file = _prepare(monkeypatch, tmp_path, "80.00", 61.2)
    _update_argv(monkeypatch, tmp_path / "coverage.json")

    assert coverage_ratchet.main() == 0
    assert floor_file.read_text(encoding="utf-8") == "80.00\n"
