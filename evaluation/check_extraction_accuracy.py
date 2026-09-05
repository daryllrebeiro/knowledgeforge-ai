"""Phase 2.5 extraction accuracy check: golden set -> per-field accuracy ratchet.

Mirrors check_thresholds.py's ratchet pattern: floors live in
extraction-thresholds.json, only rise via explicit --update, and never lower
in a passing run. Metrics are deterministic (normalized exact-match), never
LLM-judged; confidence calibration is reported separately.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

THRESHOLDS_PATH = Path(__file__).parent / "extraction-thresholds.json"


def _normalize_date(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            try:
                return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
            except ValueError:
                return str(value)
    return str(value)


def _normalize_number(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_text(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip().casefold()


def normalize_field(field: str, value: object) -> object:
    """Light normalization before comparison: ISO dates, numeric totals, text."""
    if field.endswith("_date"):
        return _normalize_date(value)
    if field in {"total", "quantity", "unit_price", "amount"}:
        return _normalize_number(value)
    return _normalize_text(value)


def _load_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _extract_line_items(fields: dict) -> list[dict]:
    items = fields.get("line_items") or []
    return [item for item in items if isinstance(item, dict)]
    items = fields.get("line_items") or []
    return [item for item in items if isinstance(item, dict)]


def score_fields(actual: dict, expected: dict) -> tuple[int, int]:
    """Return (correct, total) over the top-level fields plus line-item count."""
    correct = 0
    total = 0
    for field in ("vendor_name", "invoice_number", "invoice_date", "due_date",
                  "total", "currency"):
        if field not in expected:
            continue
        total += 1
        if normalize_field(field, actual.get(field)) == normalize_field(field, expected[field]):
            correct += 1
    expected_items = expected.get("line_items") or []
    if isinstance(expected_items, list) and expected_items:
        total += 1
        if len(_extract_line_items(actual)) == len(expected_items):
            correct += 1
    return correct, total


def calibration(extractions: list[dict]) -> dict[str, float]:
    """Fraction of needs_review rows that were wrong, and high-conf rows wrong."""
    flagged = [e for e in extractions if e.get("needs_review")]
    unflagged = [e for e in extractions if not e.get("needs_review")]
    flagged_wrong = _wrong_rate(flagged)
    unflagged_wrong = _wrong_rate(unflagged)
    return {
        "flagged_wrong_rate": round(flagged_wrong, 3),
        "unflagged_wrong_rate": round(unflagged_wrong, 3),
    }


def _wrong_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    wrong = sum(1 for e in rows if e["score"] < e["total"])
    return wrong / len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extraction accuracy ratchet")
    parser.add_argument("--golden-set", default="evaluation/extraction-golden-set.json")
    parser.add_argument("--results", help="Model output: [{document, fields, needs_review}]")
    parser.add_argument("--update", action="store_true",
                        help="Raise floors to the current run's values (never lower).")
    args = parser.parse_args(argv)

    golden_data = _load_json(args.golden_set)
    if isinstance(golden_data, dict):
        golden = golden_data.get("documents", [])
    else:
        golden = golden_data
    by_document = {entry["document"]: entry for entry in golden}
    results = _load_json(args.results)

    scored: list[dict] = []
    for result in results:
        entry = by_document.get(result["document"])
        if entry is None:
            print(f"unknown document in results: {result['document']}")
            return 1
        correct, total = score_fields(result.get("fields", {}), entry["expected"])
        scored.append({
            "document": result["document"],
            "score": correct,
            "total": total,
            "needs_review": bool(result.get("needs_review")),
        })

    if not scored:
        print("no scored results")
        return 1
    accuracy = sum(e["score"] for e in scored) / sum(e["total"] for e in scored)
    current = {"extraction_field_accuracy": round(accuracy, 3)}
    if args.update or not THRESHOLDS_PATH.exists():
        THRESHOLDS_PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"thresholds written: {current}")
        return 0

    thresholds = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8-sig"))
    floor = float(thresholds.get("extraction_field_accuracy", 0.0))
    print(f"field accuracy: {accuracy:.3f} (floor {floor:.3f})")
    print(f"calibration: {json.dumps(calibration(scored))}")
    per_document = [f"{e['document']}: {e['score']}/{e['total']}" for e in scored]
    print("per-document: " + "; ".join(per_document))
    if accuracy < floor:
        print("FAIL: extraction accuracy below the ratchet floor")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
