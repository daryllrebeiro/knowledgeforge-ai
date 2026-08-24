import os
from pathlib import Path

import psycopg


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT filename FROM schema_migrations"
                ).fetchall()
            }
            if not applied and connection.execute(
                "SELECT to_regclass('public.documents')"
            ).fetchone()[0]:
                connection.cursor().executemany(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    [(migration.name,) for migration in migrations],
                )
                print("baselined existing schema")
                return
            for migration in migrations:
                if migration.name in applied:
                    continue
                connection.execute(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (migration.name,),
                )
                print(f"applied {migration.name}")


if __name__ == "__main__":
    main()
