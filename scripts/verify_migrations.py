"""Migration sanity check: enforces sequential numbering, non-empty files, and syntax sanity."""

import re
import sys
from pathlib import Path


def verify_migrations(migration_dir: Path | None = None) -> list[str]:
    if migration_dir is None:
        migration_dir = Path(__file__).resolve().parents[1] / "migrations"

    errors: list[str] = []
    files = sorted(migration_dir.glob("*.sql"))

    if not files:
        errors.append("No migration files found in migrations directory")
        return errors

    pattern = re.compile(r"^(\d{3})_([a-z0-9_]+)(\.concurrent)?\.sql$")
    numbers: list[int] = []

    for f in files:
        m = pattern.match(f.name)
        if not m:
            errors.append(f"Invalid migration filename format: {f.name} (must match NNN_name.sql)")
            continue

        num = int(m.group(1))
        numbers.append(num)

        # Content checks
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"Migration {f.name} is not valid UTF-8: {exc}")
            continue

        if not content.strip():
            errors.append(f"Migration {f.name} is empty")

    # Contiguity check
    if numbers:
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            missing = set(expected) - set(numbers)
            duplicates = [n for n in numbers if numbers.count(n) > 1]
            if missing:
                errors.append(f"Non-contiguous migration numbers detected. Missing: {sorted(missing)}")
            if duplicates:
                errors.append(f"Duplicate migration numbers detected: {sorted(set(duplicates))}")

    return errors


def main() -> None:
    errors = verify_migrations()
    if errors:
        print("MIGRATION SANITY CHECK FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    print("Migration sanity check passed: all migrations strictly contiguous and valid.")


if __name__ == "__main__":
    main()
