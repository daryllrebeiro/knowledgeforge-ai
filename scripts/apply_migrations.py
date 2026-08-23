import os
from pathlib import Path

import psycopg


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            for migration in migrations:
                connection.execute(migration.read_text(encoding="utf-8"))
                print(f"applied {migration.name}")


if __name__ == "__main__":
    main()
