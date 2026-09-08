#!/usr/bin/env python3
"""Task-based summary generator (Process Fix B).

Parses docs/task.md to produce an honest, mechanical status summary
using the three-tier status taxonomy: Implemented, Verified, Done,
plus Gated and Retracted.

Usage:
    python scripts/generate_task_summary.py
    python scripts/generate_task_summary.py --json
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

TASK_FILE = Path("docs/task.md")

STATUS_RE = re.compile(r"^-\s+\[(Done|Verified|Implemented|Gated|Retracted)\]\s+(.*)$")
SECTION_RE = re.compile(r"^##\s+(Item\s+\d+.*)$")


def parse_tasks(file_path: Path = TASK_FILE) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(f"Task file {file_path} does not exist.")

    content = file_path.read_text(encoding="utf-8")
    sections = {}
    current_section = None

    for line in content.splitlines():
        line = line.strip()
        sec_match = SECTION_RE.match(line)
        if sec_match:
            current_section = sec_match.group(1)
            sections[current_section] = []
            continue

        item_match = STATUS_RE.match(line)
        if item_match and current_section:
            status = item_match.group(1)
            description = item_match.group(2)
            sections[current_section].append({
                "status": status,
                "description": description,
            })

    return sections


def generate_summary(sections: dict) -> dict:
    total_items = 0
    status_counts = Counter()
    section_summaries = []

    for sec_title, items in sections.items():
        counts = Counter(item["status"] for item in items)
        status_counts.update(counts)
        total_items += len(items)

        # Derive overall section status
        if counts["Gated"] > 0:
            overall = "Gated (External)"
        elif counts["Retracted"] > 0:
            overall = "Retracted / Reprioritized"
        elif counts["Done"] == len(items) and len(items) > 0:
            overall = "Done"
        elif counts["Verified"] + counts["Done"] == len(items) and len(items) > 0:
            overall = "Verified"
        elif counts["Implemented"] > 0:
            overall = "Implemented"
        else:
            overall = "In Progress"

        section_summaries.append({
            "section": sec_title,
            "item_count": len(items),
            "status_breakdown": dict(counts),
            "overall_status": overall,
        })

    return {
        "total_sections": len(sections),
        "total_subtasks": total_items,
        "status_totals": dict(status_counts),
        "sections": section_summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate task-based summary from docs/task.md")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    sections = parse_tasks()
    summary = generate_summary(sections)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("=" * 70)
    print("KnowledgeForge AI -- Phase 4 Engineering Status Summary")
    print("=" * 70)
    print(f"Total Priority Items : {summary['total_sections']}")
    print(f"Total Tracked Tasks  : {summary['total_subtasks']}")
    print("-" * 70)
    print("Subtask Status Breakdown:")
    for status, count in sorted(summary["status_totals"].items()):
        pct = (count / summary["total_subtasks"]) * 100 if summary["total_subtasks"] else 0
        print(f"  - {status:<12}: {count:2d} ({pct:5.1f}%)")
    print("-" * 70)
    print(f"{'Item':<45} | {'Status':<20}")
    print("-" * 70)
    for sec in summary["sections"]:
        title = sec["section"].replace("—", "-")
        if len(title) > 44:
            title = title[:41] + "..."
        print(f"{title:<45} | {sec['overall_status']:<20}")
    print("=" * 70)
    print("Note: Gated items indicate external cloud/API prerequisites, not code gaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
