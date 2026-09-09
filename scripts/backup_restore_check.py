"""Run a PostgreSQL backup/restore integrity and disaster recovery verification check."""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg


@dataclass
class DatabaseIntegrityReport:
    documents: int
    chunks: int
    embeddings_valid: bool
    extractions: int
    users: int
    conversations: int
    tenants: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "embeddings_valid": self.embeddings_valid,
            "extractions": self.extractions,
            "users": self.users,
            "conversations": self.conversations,
            "tenants": self.tenants,
        }


def check_integrity(connection: psycopg.Connection) -> DatabaseIntegrityReport:
    """Verify row counts and structural invariants across core production tables."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM documents")
        documents = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM chunks")
        chunks = int(cursor.fetchone()[0])

        # Verify embeddings exist and are non-null when chunks exist
        embeddings_valid = True
        if chunks > 0:
            cursor.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
            non_null_embeddings = int(cursor.fetchone()[0])
            embeddings_valid = non_null_embeddings == chunks

        cursor.execute("SELECT count(*) FROM document_extractions")
        extractions = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM users")
        users = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM conversations")
        conversations = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM tenants")
        tenants = int(cursor.fetchone()[0])

    return DatabaseIntegrityReport(
        documents=documents,
        chunks=chunks,
        embeddings_valid=embeddings_valid,
        extractions=extractions,
        users=users,
        conversations=conversations,
        tenants=tenants,
    )


def verify_restore(source_url: str, target_url: str) -> bool:
    """Compare source and restored database integrity reports."""
    start_time = time.perf_counter()

    with psycopg.connect(source_url) as src_conn:
        source_report = check_integrity(src_conn)

    with psycopg.connect(target_url) as tgt_conn:
        target_report = check_integrity(tgt_conn)

    elapsed = time.perf_counter() - start_time

    if source_report != target_report:
        print(f"FAILED: Integrity mismatch after {elapsed:.2f}s:")
        print(f"  Source:   {source_report.as_dict()}")
        print(f"  Restored: {target_report.as_dict()}")
        return False

    print(f"SUCCESS: Database restore integrity verified in {elapsed:.2f}s:")
    for k, v in target_report.as_dict().items():
        print(f"  {k}: {v}")
    return True


def main() -> None:
    source = os.environ.get("DATABASE_URL")
    target = os.environ.get("RESTORE_DATABASE_URL")
    backup_file = Path(os.getenv("BACKUP_FILE", "knowledgeforge-backup.dump"))

    if not source:
        raise SystemExit("ERROR: DATABASE_URL environment variable is required")

    # If RESTORE_DATABASE_URL is provided, perform dump, restore, and parity verification
    if target:
        print(f"Dumping source database to {backup_file}...")
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(backup_file), source], check=True
        )
        print("Restoring backup to target database...")
        subprocess.run(
            ["pg_restore", "--clean", "--if-exists", "--dbname", target, str(backup_file)],
            check=True,
        )

        if not verify_restore(source, target):
            raise SystemExit(1)
    else:
        # Standalone verification of current database health
        with psycopg.connect(source) as conn:
            report = check_integrity(conn)
            print("Current Database Integrity Report:")
            for k, v in report.as_dict().items():
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
