"""Run a local PostgreSQL backup/restore integrity check."""

import os
import subprocess
from pathlib import Path

import psycopg


def main() -> None:
    source = os.environ["DATABASE_URL"]
    target = os.environ["RESTORE_DATABASE_URL"]
    backup = Path(os.getenv("BACKUP_FILE", "knowledgeforge-local.dump"))
    subprocess.run(["pg_dump", "--format=custom", "--file", str(backup), source], check=True)
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--dbname", target, str(backup)], check=True
    )
    with psycopg.connect(target) as connection:
        documents = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
        chunks = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
        embedding = connection.execute(
            "SELECT embedding IS NOT NULL FROM chunks LIMIT 1"
        ).fetchone()
    embedding_present = bool(embedding)
    print(
        f"restore verified: documents={documents}, chunks={chunks}, "
        f"embedding_present={embedding_present}"
    )


if __name__ == "__main__":
    main()
