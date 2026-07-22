"""Root conftest — storage-focused: repo .env loading + the `backend` fixture.

Postgres isolation: one throwaway database per pytest session (unique name, so
concurrent pytest processes never collide), plus one throwaway schema per test
inside it. The real `mastervault` database is never touched by tests.

Postgres coverage is opt-out locally and mandatory in CI. Without a reachable
DATABASE_URL the postgres half of every parametrized test skips, which keeps a
laptop run fast but would also let the dedicated CI backend job pass while
testing nothing. Setting MV_REQUIRE_POSTGRES=1 turns that silence into a hard
failure: an unreachable database errors out, and any postgres-parametrized test
that still ends up skipped fails the session at the end.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TEST_DIM = 8
TEST_MODEL = "test-embed-v1"

REQUIRE_POSTGRES_ENV = "MV_REQUIRE_POSTGRES"
_TRUTHY = {"1", "true", "yes", "on"}


def require_postgres() -> bool:
    """True when postgres coverage is mandatory (the dedicated CI backend job)."""
    return os.environ.get(REQUIRE_POSTGRES_ENV, "").strip().lower() in _TRUTHY


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
        if require_postgres():
            pytest.fail(
                f"{REQUIRE_POSTGRES_ENV} is set but DATABASE_URL is unset or Postgres is"
                " unreachable — the backend job must actually exercise postgres",
                pytrace=False,
            )
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


# ---------------------------------------------------------------------------
# "postgres must not silently skip" guard
# ---------------------------------------------------------------------------

_POSTGRES_SKIPS: list[str] = []


def _is_postgres_item(item: pytest.Item) -> bool:
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "postgres" in callspec.params.values():
        return True
    return "pg_test_url" in getattr(item, "fixturenames", ())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and _is_postgres_item(item):
        _POSTGRES_SKIPS.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run when postgres coverage was demanded but quietly skipped."""
    if not require_postgres() or not _POSTGRES_SKIPS:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    if not require_postgres() or not _POSTGRES_SKIPS:
        return
    terminalreporter.section("postgres coverage", red=True)
    terminalreporter.write_line(
        f"{REQUIRE_POSTGRES_ENV} is set, but {len(_POSTGRES_SKIPS)} postgres-backed"
        " test(s) skipped instead of running:"
    )
    for nodeid in _POSTGRES_SKIPS:
        terminalreporter.write_line(f"  - {nodeid}")
