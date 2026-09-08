#!/usr/bin/env python3
"""Mechanical integrity check for docs/decisions.md (Process Fix A).

Enforces that every numeric benchmark, accuracy, or latency claim in
docs/decisions.md is either:
1. Accompanied by an explicit script/command citation or evaluation artifact, OR
2. Explicitly marked as 'NOT YET MEASURED', 'PENDING', or 'RETRACTED'.

Fails with exit code 1 if ungrounded numeric claims are detected.
"""

from pathlib import Path
import re
import sys

DECISIONS_PATH = Path("docs/decisions.md")

# Patterns indicating numeric performance claims
CLAIM_PATTERNS = [
    re.compile(r"\bHit@\d+\s*=", re.IGNORECASE),
    re.compile(r"\bMRR@\d+\s*[:=]", re.IGNORECASE),
    re.compile(r"\bP(?:50|90|95|99)\s*=", re.IGNORECASE),
    re.compile(r"\bconcurrency\s+ceiling\b.*?\d+", re.IGNORECASE),
    re.compile(r"\b(?:executed?|latency)\s+in\s+\d+\s*(?:ms|s)\b", re.IGNORECASE),
]

# Patterns that certify the claim is either cited or honestly labeled
GROUNDING_PATTERNS = [
    re.compile(r"`(?:uv\s+run|python|locust|pytest|run_phase12_eval|gcloud)[^`]+`"),
    re.compile(r"(?:RETRACTED|NOT YET MEASURED|PENDING|LOCAL DETERMINISTIC)", re.IGNORECASE),
    re.compile(r"`docs/phase\d+-evaluation\.json`"),
    re.compile(r"`evaluation/[^`]+`"),
]


def verify_decisions_integrity(path: Path = DECISIONS_PATH) -> list[str]:
    target_path = Path(path)
    if not target_path.exists():
        return [f"File {target_path} does not exist"]

    content = target_path.read_text(encoding="utf-8")
    sections = re.split(r"\n(?=##\s+)", content)
    violations = []

    for idx, section in enumerate(sections):
        lines = section.strip().splitlines()
        header = lines[0] if lines else f"Section {idx}"

        # Check for numeric claims
        has_claim = any(pattern.search(section) for pattern in CLAIM_PATTERNS)
        if not has_claim:
            continue

        # Check for citation or unmeasured marker
        is_grounded = any(pattern.search(section) for pattern in GROUNDING_PATTERNS)

        if not is_grounded:
            violations.append(
                f"Uncertified numeric claim in section '{header}': "
                f"Numeric benchmarks require either an explicit script/eval command citation "
                f"or an explicit 'NOT YET MEASURED' / 'PENDING' / 'RETRACTED' status."
            )
            continue

        # If grounded by citation, verify that cited scripts or evaluation artifacts exist on disk
        is_explicitly_unmeasured = bool(
            re.search(r"\b(?:RETRACTED|NOT YET MEASURED|PENDING)\b", section, re.IGNORECASE)
        )
        if not is_explicitly_unmeasured:
            for match in re.finditer(
                r"`(?:[a-zA-Z0-9_\-]+\s+)?((?:evaluation|scripts|docs|tests|src)/[a-zA-Z0-9_\-\.\/]+)`",
                section,
            ):
                cited_path = match.group(1).strip()
                cited_file = cited_path.split()[0]
                repo_root = target_path.parent.parent if target_path.parent.name == "docs" else Path(".")
                resolved_file = repo_root / cited_file
                if not resolved_file.exists() and not Path(cited_file).exists():
                    violations.append(
                        f"Phantom citation in section '{header}': "
                        f"Cited file '{cited_file}' does not exist on disk."
                    )

    return violations


def main() -> int:
    violations = verify_decisions_integrity()
    if violations:
        print("[-] Process Fix A - docs/decisions.md integrity failure:", file=sys.stderr)
        for v in violations:
            print(f"    - {v}", file=sys.stderr)
        return 1

    print("[+] Process Fix A - docs/decisions.md integrity check passed: all numeric claims verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
