"""Storage layer: backend selection + public re-exports.

Backend resolution ('auto' rule): Postgres when DATABASE_URL is set and
connectable, otherwise SQLite at settings.paths.sqlite_path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg

from mastervault.storage.base import (
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    FileBackedBackend,
    HydratedChunkRow,
    HydratedClaimRow,
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
    "FileBackedBackend",
    "HydratedChunkRow",
    "HydratedClaimRow",
    "PostgresBackend",
    "SchemaMismatchError",
    "SqliteBackend",
    "StorageBackend",
    "StorageError",
    "get_backend",
]


def _connect_postgres(url: str, **kwargs: Any) -> PostgresBackend:
    """Connect, converting any driver error into a StorageError WITHOUT the URL.

    psycopg echoes the whole connection string back in some messages -- notably
    ProgrammingError on a malformed DSN -- which puts the DATABASE_URL password
    straight onto the user's terminal and into any log that catches it. The
    message here names the variable, never its value.
    """
    try:
        return PostgresBackend(url, **kwargs)
    except psycopg.Error as exc:
        raise StorageError(
            f"could not connect to the database in DATABASE_URL: {type(exc).__name__}."
            " Check the value of DATABASE_URL (it is not echoed here because it may"
            " contain a password)."
        ) from None


def get_backend(settings: Settings) -> StorageBackend:
    """Resolve the configured storage backend.

    - "postgres": requires DATABASE_URL; connection failures raise StorageError.
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
        return _connect_postgres(url)
    if mode == "sqlite":
        return SqliteBackend(settings.paths.sqlite_path)
    # auto: any failure to reach Postgres falls back to SQLite rather than
    # surfacing a driver error (and never surfaces the URL either).
    url = settings.database_url
    if url:
        try:
            return _connect_postgres(url, connect_timeout=5)
        except StorageError:
            pass
    return SqliteBackend(settings.paths.sqlite_path)
