"""Unit tests: pure helpers + the get_backend 'auto' resolution rule.

No live Postgres required — the postgres path is only exercised through an
unreachable URL to prove the sqlite fallback.
"""

from __future__ import annotations

import pytest

from mastervault.config import Settings
from mastervault.storage import get_backend
from mastervault.storage.base import StorageError, overfetch_limit
from mastervault.storage.sqlite import (
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
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://nobody:nope@127.0.0.1:9/absent"
    )
    backend = get_backend(_settings(tmp_path, "auto"))
    try:
        assert isinstance(backend, SqliteBackend)
    finally:
        backend.close()
