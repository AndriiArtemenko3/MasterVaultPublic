"""Unit tests: pure helpers + the get_backend 'auto' resolution rule.

No live Postgres required — the postgres path is only exercised through an
unreachable URL to prove the sqlite fallback.
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from mastervault.config import Settings
from mastervault.storage import get_backend
from mastervault.storage.base import (
    META_KEY_DIM,
    META_KEY_MODEL,
    META_KEY_SCHEMA,
    SCHEMA_VERSION,
    DocumentRow,
    SchemaMismatchError,
    StorageError,
    UnsupportedSchemaVersionError,
    overfetch_limit,
    validate_schema_meta,
)
from mastervault.storage.sqlite import (
    _DEFAULT_MIGRATIONS_DIR,
    SqliteBackend,
    _normalize,
    fts_match_expr,
    l2_to_cosine,
)

# ---------------------------------------------------------------------------
# base helpers
# ---------------------------------------------------------------------------


def test_overfetch_limit_policy():
    assert overfetch_limit(10, None, None) == 10
    assert overfetch_limit(10, ["claim"], None) == 40
    assert overfetch_limit(10, None, "operations") == 40
    assert overfetch_limit(10, ["claim", "wiki"], None) == 40
    assert overfetch_limit(10, ["wiki"], None) == 200
    assert overfetch_limit(10, ["wiki"], "operations") == 200


# ---------------------------------------------------------------------------
# sqlite helpers
# ---------------------------------------------------------------------------


def test_fts_match_expr_quotes_tokens():
    assert fts_match_expr("refund policy") == '"refund" "policy"'
    assert fts_match_expr('it\'s a "quoted" query!!') == '"it" "s" "a" "quoted" "query"'
    assert fts_match_expr("NEAR(a b) AND OR NOT") == '"NEAR" "a" "b" "AND" "OR" "NOT"'


def test_fts_match_expr_rejects_tokenless_queries():
    assert fts_match_expr("") is None
    assert fts_match_expr('?!,;:()[]"""') is None
    assert fts_match_expr("   ") is None


def test_l2_to_cosine_unit_vector_identities():
    assert l2_to_cosine(0.0) == pytest.approx(1.0)  # identical unit vectors
    assert l2_to_cosine(2.0**0.5) == pytest.approx(0.0)  # orthogonal
    assert l2_to_cosine(2.0) == pytest.approx(-1.0)  # opposite


def test_normalize_rejects_zero_vector():
    with pytest.raises(StorageError):
        _normalize([0.0, 0.0, 0.0])


def test_normalize_produces_unit_vector():
    arr = _normalize([3.0, 4.0])
    assert arr.tolist() == pytest.approx([0.6, 0.8])


# ---------------------------------------------------------------------------
# ordered schema migrations
# ---------------------------------------------------------------------------


def _v1_sqlite(path) -> SqliteBackend:
    backend = SqliteBackend(path)
    sql = (_DEFAULT_MIGRATIONS_DIR / "001_init.sql").read_text().replace("{{DIM}}", "8")
    with backend.conn:
        for statement in backend._sql_statements(sql):
            backend.conn.execute(statement)
        for key, value in (
            (META_KEY_MODEL, "test-embed-v1"),
            (META_KEY_DIM, 8),
            (META_KEY_SCHEMA, 1),
        ):
            backend.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
    return backend


def test_sqlite_upgrades_a_representative_v1_workspace_without_data_loss(tmp_path):
    db_path = tmp_path / "v02-index.db"
    legacy = _v1_sqlite(db_path)
    doc = DocumentRow(
        doc_id="source:operations/sources/legacy.md",
        doc_type="source",
        domain="operations",
        rel_path="operations/sources/legacy.md",
        title="Legacy v0.2 note",
        body="Preserve this row through migration.",
        content_hash="legacy-hash",
    )
    legacy.upsert_document(doc, [], [], [])
    legacy.close()

    upgraded = SqliteBackend(db_path)
    try:
        upgraded.init_schema(8, "test-embed-v1")
        assert upgraded.stats()["schema_version"] == SCHEMA_VERSION
        assert upgraded.get_documents([doc.doc_id]) == [doc]
        versions = [
            row[0]
            for row in upgraded.conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        assert versions == [1, 2]
    finally:
        upgraded.close()


def test_sqlite_failed_fresh_migration_is_atomic_and_retryable(tmp_path):
    migrations = tmp_path / "migrations"
    shutil.copytree(_DEFAULT_MIGRATIONS_DIR, migrations)
    first = migrations / "001_init.sql"
    original = first.read_text(encoding="utf-8")
    first.write_text(
        original + "\nCREATE TABLE late_statement_was_reached (id INTEGER);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "atomic.db"
    backend = SqliteBackend(db_path, migrations_dir=migrations)
    try:
        with pytest.raises(Exception, match="syntax error"):
            backend.init_schema(8, "test-embed-v1")
        names = {
            row[0]
            for row in backend.conn.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        assert "meta" not in names
        assert "documents" not in names
        assert "late_statement_was_reached" not in names

        first.write_text(original, encoding="utf-8")
        backend.init_schema(8, "test-embed-v1")
        assert backend.stats()["schema_version"] == SCHEMA_VERSION
    finally:
        backend.close()


def test_two_sqlite_connections_serialize_fresh_initialization(tmp_path):
    db_path = tmp_path / "concurrent.db"

    def initialize() -> None:
        backend = SqliteBackend(db_path)
        try:
            backend.init_schema(8, "test-embed-v1")
        finally:
            backend.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)

    backend = SqliteBackend(db_path)
    try:
        backend.init_schema(8, "test-embed-v1")
        rows = backend.conn.execute(
            "SELECT version, name, checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [int(row[0]) for row in rows] == [1, 2]
        assert all(len(str(row[2])) == 64 for row in rows)
    finally:
        backend.close()


def test_sqlite_refuses_newer_schema_without_overwriting_metadata(tmp_path):
    backend = SqliteBackend(tmp_path / "future.db")
    try:
        backend.init_schema(8, "test-embed-v1")
        with backend.conn:
            backend.conn.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (json.dumps(SCHEMA_VERSION + 1), META_KEY_SCHEMA),
            )
        with pytest.raises(UnsupportedSchemaVersionError, match="newer"):
            backend.init_schema(8, "test-embed-v1")
        assert backend._read_meta()[META_KEY_SCHEMA] == SCHEMA_VERSION + 1
    finally:
        backend.close()


def test_sqlite_refuses_tables_without_schema_identity(tmp_path):
    backend = SqliteBackend(tmp_path / "unidentified.db")
    try:
        with backend.conn:
            backend.conn.execute("CREATE TABLE documents (doc_id TEXT PRIMARY KEY)")
        with pytest.raises(SchemaMismatchError, match="without a meta table"):
            backend.init_schema(8, "test-embed-v1")
        assert backend.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'documents'"
        ).fetchone()
    finally:
        backend.close()


def test_validate_schema_meta_refuses_unsupported_or_ambiguous_states():
    base = {
        META_KEY_MODEL: "test-embed-v1",
        META_KEY_DIM: 8,
        META_KEY_SCHEMA: 1,
    }
    assert validate_schema_meta(base, dim=8, model_version="test-embed-v1") == 1
    with pytest.raises(UnsupportedSchemaVersionError, match="older"):
        validate_schema_meta({**base, META_KEY_SCHEMA: 0}, dim=8, model_version="test-embed-v1")
    with pytest.raises(UnsupportedSchemaVersionError, match="newer"):
        validate_schema_meta(
            {**base, META_KEY_SCHEMA: SCHEMA_VERSION + 1},
            dim=8,
            model_version="test-embed-v1",
        )
    with pytest.raises(SchemaMismatchError, match="missing schema_version"):
        validate_schema_meta(
            {META_KEY_MODEL: "test-embed-v1", META_KEY_DIM: 8},
            dim=8,
            model_version="test-embed-v1",
        )


# ---------------------------------------------------------------------------
# get_backend resolution
# ---------------------------------------------------------------------------


def _settings(tmp_path, backend: str) -> Settings:
    return Settings(
        storage={"backend": backend},
        paths={"workspace": tmp_path / "nested" / "workspace"},
    )


def test_get_backend_explicit_sqlite_creates_parent_dirs(tmp_path):
    settings = _settings(tmp_path, "sqlite")
    backend = get_backend(settings)
    try:
        assert isinstance(backend, SqliteBackend)
        assert backend.db_path == settings.paths.sqlite_path
        assert backend.db_path.parent.is_dir()
    finally:
        backend.close()


def test_get_backend_explicit_postgres_requires_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(StorageError, match="DATABASE_URL"):
        get_backend(_settings(tmp_path, "postgres"))


def test_get_backend_auto_without_database_url_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = get_backend(_settings(tmp_path, "auto"))
    try:
        assert isinstance(backend, SqliteBackend)
    finally:
        backend.close()


def test_get_backend_auto_falls_back_when_postgres_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nope@127.0.0.1:9/absent")
    backend = get_backend(_settings(tmp_path, "auto"))
    try:
        assert isinstance(backend, SqliteBackend)
    finally:
        backend.close()
