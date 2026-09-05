"""Eval regression gate (F6): fail when golden-set metrics regress.

Reads a ``run_phase12_eval.py`` output file and compares each profile's
metrics against the floors in ``evaluation/eval-thresholds.json``. Exits 1
with a diff-style report on any regression.

Floors start at zero and are raised deliberately: after a verified improvement,
run with ``--update`` against the new reference output and review the diff of
``evaluation/eval-thresholds.json`` like any other code change. The floor file
is the ratchet — a PR that lowers quality cannot pass, and a PR that raises it
bumps the floor so quality can only move up.

Usage:
    uv run python -m evaluation.check_thresholds docs/phase12-evaluation.json
    uv run python -m evaluation.check_thresholds --update reference.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_PATH = Path(__file__).parent / "eval-thresholds.json"

# Metrics a floor can ratchet. answer_correctness/refusal_accuracy are None in
# local (retrieval-only) mode and are skipped there.
METRICS = ("hit_at_5", "answer_correctness", "refusal_accuracy")


def load_floors() -> dict[str, Any]:
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def metric_value(result: dict[str, Any], metric: str) -> float | None:
    value = result.get(metric)
    return None if value is None else float(value)


def check(report_path: Path) -> int:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    floors = load_floors()
    failures: list[str] = []
    print(f"Eval gate ({report.get('mode', 'unknown')} mode, {report_path.name}):")
    for name, result in sorted(report.get("profiles", {}).items()):
        profile_floors = floors.get(name)
        if profile_floors is None:
            continue
        for metric in METRICS:
            actual = metric_value(result, metric)
            floor = profile_floors.get(metric)
            if actual is None or floor is None:
                continue
            status = "ok" if actual >= floor else "REGRESSION"
            print(f"  {name} {metric}: {actual:.1%} (floor {floor:.1%}) {status}")
            if actual < floor:
                failures.append(f"{name} {metric}: {actual:.3f} < floor {floor:.3f}")
    if failures:
        print("\nQuality regression detected:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("All floors held.")
    return 0


def update_from(report_path: Path) -> int:
    """Rewrite the floors from a reference run (floors only ever rise)."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    floors = load_floors()
    for name, result in report.get("profiles", {}).items():
        profile_floors = floors.setdefault(name, {})
        for metric in METRICS:
            actual = metric_value(result, metric)
            if actual is None:
                continue
            previous = float(profile_floors.get(metric, 0.0))
            profile_floors[metric] = max(previous, actual)
    THRESHOLDS_PATH.write_text(json.dumps(floors, indent=2, sort_keys=True) + "\n")
    print(f"Floors updated in {THRESHOLDS_PATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="run_phase12_eval.py output file")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Raise floors to this report's values instead of checking",
    )
    args = parser.parse_args()
    return update_from(args.report) if args.update else check(args.report)


if __name__ == "__main__":
    raise SystemExit(main())
