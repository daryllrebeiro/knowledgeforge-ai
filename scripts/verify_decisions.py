#!/usr/bin/env python3
"""Mechanical integrity check for docs/decisions.md (Process Fix A).

Enforces that every numeric benchmark, accuracy, or latency claim in
docs/decisions.md is either:
1. Accompanied by an explicit script/command citation or evaluation artifact, OR
2. Explicitly marked as 'NOT YET MEASURED', 'PENDING', or 'RETRACTED'.

Fails with exit code 1 if ungrounded numeric claims are detected.
"""

import re
import sys
from pathlib import Path

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
                repo_root = (
                    target_path.parent.parent if target_path.parent.name == "docs" else Path(".")
                )
                resolved_file = repo_root / cited_file
                if not resolved_file.exists() and not Path(cited_file).exists():
                    violations.append(
                        f"Phantom citation in section '{header}': "
                        f"Cited file '{cited_file}' does not exist on disk."
                    )

    return violations


QUALITATIVE_ABSOLUTE_PATTERNS = [
    re.compile(r"\bstructurally\s+impossible\b", re.IGNORECASE),
    re.compile(r"\bzero\s+risk\b", re.IGNORECASE),
    re.compile(r"\b100%\s+secure\b", re.IGNORECASE),
    re.compile(r"\bcompletely\s+bulletproof\b", re.IGNORECASE),
    re.compile(r"\bimpossible\s+to\s+(?:hack|bypass|leak|exploit)\b", re.IGNORECASE),
]


def verify_qualitative_absolutes(paths: list[Path] | None = None) -> list[str]:
    """Scan markdown documents for unhedged qualitative absolutes.

    Flags qualitative overconfidence (e.g. 'zero risk', 'structurally impossible')
    that lacks qualification or evidence.
    """
    if paths is None:
        paths = list(Path("docs").glob("*.md"))
        roadmap = Path("REVIEW_AND_ROADMAP.md")
        if roadmap.exists():
            paths.append(roadmap)

    violations = []
    for path in paths:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip quotations/disclaimers or code blocks
            if stripped.startswith(">") or stripped.startswith("`") or stripped.startswith("#"):
                continue
            for pattern in QUALITATIVE_ABSOLUTE_PATTERNS:
                match = pattern.search(line)
                if match:
                    violations.append(
                        f"Unhedged qualitative absolute '{match.group(0)}' in {path}:{line_num}: "
                        f"Prose claims must be hedged defensively and grounded rather than asserting absolute security."
                    )
    return violations


def main() -> int:
    numeric_violations = verify_decisions_integrity()
    qualitative_violations = verify_qualitative_absolutes()
    all_violations = numeric_violations + qualitative_violations

    if all_violations:
        print("[-] Process Integrity Check Failure:", file=sys.stderr)
        for v in all_violations:
            print(f"    - {v}", file=sys.stderr)
        return 1

    print(
        "[+] Process integrity check passed: all numeric claims grounded, zero unhedged absolutes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
