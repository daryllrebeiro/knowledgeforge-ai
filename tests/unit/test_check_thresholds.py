"""The eval regression gate (F6): golden-set metrics may not regress."""

import json

from evaluation import check_thresholds


def _prepare(monkeypatch, tmp_path, floors: dict, profiles: dict):
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(json.dumps(floors), encoding="utf-8")
    monkeypatch.setattr(check_thresholds, "THRESHOLDS_PATH", thresholds)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"mode": "local", "profiles": profiles}), encoding="utf-8")
    return thresholds, report


def test_fails_when_a_metric_regresses(monkeypatch, tmp_path, capsys) -> None:
    _, report = _prepare(
        monkeypatch, tmp_path, {"baseline": {"hit_at_5": 0.5}}, {"baseline": {"hit_at_5": 0.4}}
    )

    assert check_thresholds.check(report) == 1
    error = capsys.readouterr().err
    assert "Quality regression detected" in error
    assert "baseline hit_at_5: 0.400 < floor 0.500" in error


def test_passes_when_floors_hold(monkeypatch, tmp_path, capsys) -> None:
    _, report = _prepare(
        monkeypatch, tmp_path, {"baseline": {"hit_at_5": 0.5}}, {"baseline": {"hit_at_5": 0.5}}
    )

    assert check_thresholds.check(report) == 0
    assert "All floors held." in capsys.readouterr().out


def test_skips_metrics_without_a_value_or_floor(monkeypatch, tmp_path) -> None:
    # Local (retrieval-only) reports leave correctness/refusal as None.
    _, report = _prepare(
        monkeypatch,
        tmp_path,
        {"baseline": {"hit_at_5": 0.5}},
        {"baseline": {"hit_at_5": 0.9, "answer_correctness": None, "refusal_accuracy": None}},
    )

    assert check_thresholds.check(report) == 0


def test_ignores_profiles_without_floors(monkeypatch, tmp_path) -> None:
    _, report = _prepare(monkeypatch, tmp_path, {}, {"brand-new-profile": {"hit_at_5": 0.1}})

    assert check_thresholds.check(report) == 0


def test_update_raises_floors_and_adds_profiles(monkeypatch, tmp_path) -> None:
    thresholds, report = _prepare(
        monkeypatch,
        tmp_path,
        {"baseline": {"hit_at_5": 0.8}},
        {"baseline": {"hit_at_5": 0.5}, "section-aware": {"hit_at_5": 0.6}},
    )

    assert check_thresholds.update_from(report) == 0
    floors = json.loads(thresholds.read_text(encoding="utf-8"))
    # Existing floor never drops; new profiles are added.
    assert floors["baseline"]["hit_at_5"] == 0.8
    assert floors["section-aware"]["hit_at_5"] == 0.6
