"""Hermetic fixtures for evals.harness tests: explicit Settings, a plain
SqliteBackend (not the postgres-parametrized root `backend` fixture), a mock
embedder, and the repo's existing mini_vault fixture synced in.

Mirrors tests/unit/pipelines/conftest.py's pattern.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mastervault.config import EmbeddingCfg, PathsCfg, RetrievalCfg, Settings
from mastervault.providers.embedding import MockEmbedding
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync import sync_vault

MINI_VAULT = Path(__file__).resolve().parents[2] / "fixtures" / "mini_vault"
TEST_DIM = 8


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "vault").mkdir(parents=True)
    return ws


@pytest.fixture
def settings(workspace: Path) -> Settings:
    return Settings(
        paths=PathsCfg(workspace=workspace),
        embedding=EmbeddingCfg(provider="mock"),
        retrieval=RetrievalCfg(k=10, rrf_k=60, rerank_pool=30),
    )


@pytest.fixture
def embedder() -> MockEmbedding:
    return MockEmbedding(TEST_DIM)


@pytest.fixture
def backend(workspace: Path, embedder: MockEmbedding):
    b = SqliteBackend(workspace / "index.db")
    b.init_schema(embedder.dimensions, embedder.model_version)
    yield b
    b.close()


@pytest.fixture
def vault_copy(tmp_path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, dest)
    return dest


@pytest.fixture
def synced_backend(backend, embedder):
    """mini_vault synced into `backend` with MockEmbedding — the known-content
    fixture used by test_search.py (`restocking fee is 10 percent`, wiki
    `refund-window` alias, sales-crm `discount tier pricing`, etc.).
    """
    sync_vault(MINI_VAULT, backend, embedder)
    return backend
