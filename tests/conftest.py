"""Root conftest — storage-focused: repo .env loading + the `backend` fixture.

Postgres isolation: one throwaway database per pytest session (unique name, so
concurrent pytest processes never collide), plus one throwaway schema per test
inside it. The real `mastervault` database is never touched by tests.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TEST_DIM = 8
TEST_MODEL = "test-embed-v1"


def _load_repo_dotenv() -> None:
    """Populate os.environ from the repo .env when DATABASE_URL is unset.

    Nothing auto-loads .env into the process environment, so tests do it here.
    Existing environment variables always win.
    """
    if os.environ.get("DATABASE_URL"):
        return
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_repo_dotenv()


@pytest.fixture(scope="session")
def dim() -> int:
    return TEST_DIM


@pytest.fixture(scope="session")
def model_version() -> str:
    return TEST_MODEL


def _reachable_pg_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=3):
            pass
    except psycopg.OperationalError:
        return None
    return url


@pytest.fixture(scope="session")
def pg_test_url():
    """URL of a dedicated test database, created for this session and dropped after."""
    admin_url = _reachable_pg_url()
    if admin_url is None:
        pytest.skip("DATABASE_URL unset or Postgres unreachable")
    import psycopg
    from psycopg.conninfo import make_conninfo

    dbname = f"mastervault_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    test_url = make_conninfo(admin_url, dbname=dbname)
    with psycopg.connect(test_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    yield test_url
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path):
    """A schema-initialized StorageBackend, parametrized over both backends."""
    if request.param == "sqlite":
        from mastervault.storage.sqlite import SqliteBackend

        b = SqliteBackend(tmp_path / "index.db")
        b.init_schema(TEST_DIM, TEST_MODEL)
        yield b
        b.close()
        return

    test_url = request.getfixturevalue("pg_test_url")
    from mastervault.storage.postgres import PostgresBackend

    b = PostgresBackend(test_url)
    schema = f"t_{uuid.uuid4().hex[:12]}"
    b.conn.execute(f'CREATE SCHEMA "{schema}"')
    b.conn.execute(f'SET search_path TO "{schema}", public')
    b.init_schema(TEST_DIM, TEST_MODEL)
    yield b
    try:
        b.conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        b.close()
