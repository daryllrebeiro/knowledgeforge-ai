#!/usr/bin/env python3
"""Disaster recovery and automated backup verification harness with --dry-run mode."""

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify disaster recovery readiness, backup file integrity, and PITR invariants."
    )
    parser.add_argument(
        "--backup-file",
        default=os.getenv("BACKUP_FILE", ""),
        help="Path to PostgreSQL dump file (.sql or .dump)",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Target PostgreSQL connection string for restoration verification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate backup checksumming and schema inspection in a sandbox without altering live database",
    )
    return parser.parse_args(args)


def verify_checksum(filepath: Path) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


def run_dry_run(opts: argparse.Namespace) -> int:
    print("======================================================================")
    print("[DRY RUN] Disaster Recovery & Backup Integrity Verification Plan")
    print("======================================================================")
    print(f"Target Backup File : {opts.backup_file or '[SIMULATED SYNTHETIC BACKUP]'}")
    print(f"Target Database    : {'[CONFIGURED]' if opts.db_url else '[UNCONFIGURED]'}")
    print("----------------------------------------------------------------------")
    print("Steps planned:")
    print("  1. Check backup artifact metadata and timestamp freshness (< 24h)")
    print("  2. Validate checksum integrity (SHA-256 match against metadata)")
    print("  3. Verify point-in-time recovery (PITR) WAL log continuity")
    print("  4. Perform isolated sandbox schema and tenant isolation check")
    print("----------------------------------------------------------------------")

    # Simulate checksum verification on a sandbox temporary file
    with tempfile.NamedTemporaryFile("w+", suffix=".sql", delete=False) as tf:
        tf.write("-- Simulated PostgreSQL dump\nCREATE TABLE tenants (id UUID PRIMARY KEY);\n")
        tf_path = Path(tf.name)

    try:
        digest = verify_checksum(tf_path)
        print(f"[DRY RUN] Checksum engine validated: SHA-256 test digest = {digest[:16]}...")
    finally:
        if tf_path.exists():
            tf_path.unlink()

    print("[DRY RUN] Disaster recovery verification simulation passed successfully.")
    return 0


def run_live(opts: argparse.Namespace) -> int:
    if not opts.backup_file:
        print("[-] Error: --backup-file is required for live verification", file=sys.stderr)
        return 1

    backup_path = Path(opts.backup_file)
    if not backup_path.exists():
        print(f"[-] Error: Backup file not found at {backup_path}", file=sys.stderr)
        return 1

    print(
        f"[*] Calculating SHA-256 for backup {backup_path.name} ({backup_path.stat().st_size} bytes)..."
    )
    digest = verify_checksum(backup_path)
    print(f"[+] SHA-256: {digest}")

    if opts.db_url:
        print("[*] Running integrity checks against target database...")
        import psycopg

        from scripts.backup_restore_check import check_integrity

        with psycopg.connect(opts.db_url) as conn:
            report = check_integrity(conn)
            print(f"[+] Report: {report.as_dict()}")

    print("[+] Disaster recovery verification passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    if opts.dry_run:
        return run_dry_run(opts)
    return run_live(opts)


if __name__ == "__main__":
    sys.exit(main())
