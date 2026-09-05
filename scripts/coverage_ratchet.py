"""Coverage ratchet (F6): no PR may decrease coverage.

Compares the total coverage percentage from a pytest-cov JSON report against
the floor stored in ``.coverage-floor``. Exit 1 when coverage drops below the
floor (with a small tolerance for measurement jitter); ``--update`` raises the
floor to the current value.

Usage:
    uv run pytest -m "not integration and not live" --cov=knowledgeforge \
        --cov-report=json:coverage.json
    uv run python scripts/coverage_ratchet.py coverage.json
    uv run python scripts/coverage_ratchet.py coverage.json --update
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOOR_PATH = ROOT / ".coverage-floor"
# Percent rounding between pytest-cov versions / platforms.
TOLERANCE_PERCENT = 0.05


def read_floor() -> float:
    if not FLOOR_PATH.exists():
        return 0.0
    return float(FLOOR_PATH.read_text(encoding="utf-8").strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="pytest-cov JSON report (coverage.json)")
    parser.add_argument(
        "--update", action="store_true", help="Raise the floor to this report's coverage"
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    coverage = float(report["totals"]["percent_covered"])
    floor = read_floor()

    if args.update:
        if coverage > floor:
            FLOOR_PATH.write_text(f"{coverage:.2f}\n", encoding="utf-8")
            print(f"Coverage floor raised: {floor:.2f}% -> {coverage:.2f}%")
        else:
            print(f"Coverage floor unchanged at {floor:.2f}% (current {coverage:.2f}%)")
        return 0

    print(f"Coverage: {coverage:.2f}% (floor {floor:.2f}%)")
    if coverage < floor - TOLERANCE_PERCENT:
        print(
            f"Coverage regression: {coverage:.2f}% is below the floor {floor:.2f}%. "
            "Add tests for the new code, or lower the floor only with an explicit "
            "justification in the PR.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
