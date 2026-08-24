"""Run a local PostgreSQL backup/restore integrity check."""

import os
import subprocess
from pathlib import Path

import psycopg


def integrity(connection: psycopg.Connection) -> tuple[int, int, bool]:
    document_row = connection.execute("SELECT count(*) FROM documents").fetchone()
    chunk_row = connection.execute("SELECT count(*) FROM chunks").fetchone()
    if document_row is None or chunk_row is None:
        raise RuntimeError("integrity query returned no count row")
    documents = document_row[0]
    chunks = chunk_row[0]
    embedding = connection.execute(
        "SELECT embedding IS NOT NULL FROM chunks ORDER BY created_at LIMIT 1"
    ).fetchone()
    return int(documents), int(chunks), bool(embedding and embedding[0])


def main() -> None:
    source = os.environ["DATABASE_URL"]
    target = os.environ["RESTORE_DATABASE_URL"]
    backup = Path(os.getenv("BACKUP_FILE", "knowledgeforge-local.dump"))
    subprocess.run(["pg_dump", "--format=custom", "--file", str(backup), source], check=True)
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--dbname", target, str(backup)], check=True
    )
    with psycopg.connect(source) as connection:
        source_integrity = integrity(connection)
    with psycopg.connect(target) as connection:
        restored_integrity = integrity(connection)
    if source_integrity != restored_integrity:
        raise SystemExit(
            "restore integrity mismatch: "
            f"source={source_integrity} restored={restored_integrity}"
        )
    print(
        "restore verified: "
        f"documents={restored_integrity[0]}, chunks={restored_integrity[1]}, "
        f"embedding_present={restored_integrity[2]}"
    )


if __name__ == "__main__":
    main()
