"""Process-wide database connection pooling (roadmap R4.1).

The API uses ``get_connection()`` everywhere instead of inline ``psycopg.connect``.
When the pool has been initialized (application startup) connections are
borrowed from it; otherwise the module falls back to a direct connection, which
keeps ad-hoc scripts and tests working unchanged.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg_pool import ConnectionPool

from knowledgeforge.config import get_settings

_pool: ConnectionPool | None = None


def init_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> None:
    """Open the process-wide pool (idempotent; ignores smaller re-configurations)."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url, min_size=min_size, max_size=max_size, open=True, name="knowledgeforge"
        )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection() -> Iterator[Connection]:
    if _pool is not None:
        with _pool.connection() as connection:
            yield connection
    else:
        with psycopg.connect(get_settings().database_url) as connection:
            yield connection
