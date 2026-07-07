"""Storage layer: backend selection + public re-exports.

Backend resolution ('auto' rule): Postgres when DATABASE_URL is set and
connectable, otherwise SQLite at settings.paths.sqlite_path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg

from mastervault.storage.base import (
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    SchemaMismatchError,
    StorageBackend,
    StorageError,
)
from mastervault.storage.postgres import PostgresBackend
from mastervault.storage.sqlite import SqliteBackend

if TYPE_CHECKING:
    from mastervault.config import Settings

__all__ = [
    "AliasRow",
    "ChunkRow",
    "ClaimRow",
    "DocumentRow",
    "EmbeddingRow",
    "PostgresBackend",
    "SchemaMismatchError",
    "SqliteBackend",
    "StorageBackend",
    "StorageError",
    "get_backend",
]


def get_backend(settings: Settings) -> StorageBackend:
    """Resolve the configured storage backend.

    - "postgres": requires DATABASE_URL; connection errors propagate.
    - "sqlite": opens settings.paths.sqlite_path (parent dirs created).
    - "auto": Postgres when DATABASE_URL is set and connectable, else SQLite.
    """
    mode = settings.storage.backend
    if mode == "postgres":
        url = settings.database_url
        if not url:
            raise StorageError(
                "storage.backend='postgres' but DATABASE_URL is not set in the environment"
            )
        return PostgresBackend(url)
    if mode == "sqlite":
        return SqliteBackend(settings.paths.sqlite_path)
    # auto
    url = settings.database_url
    if url:
        try:
            return PostgresBackend(url, connect_timeout=5)
        except psycopg.OperationalError:
            pass
    return SqliteBackend(settings.paths.sqlite_path)
