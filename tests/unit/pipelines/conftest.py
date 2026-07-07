"""Shared fixtures for pipeline tests: hermetic settings, sqlite backend,
mock embedder/llm, and a tmp workspace with vault/review/runs pre-created.

Everything here is explicit-Settings (never env/toml-derived) so tests never
touch the real repo workspace, and a fresh SqliteBackend per test keeps tests
isolated from each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.config import (
    AskCfg,
    BudgetsCfg,
    EmbeddingCfg,
    IngestionCfg,
    LLMCfg,
    PathsCfg,
    Settings,
)
from mastervault.providers.embedding import MockEmbedding
from mastervault.providers.llm import MockLLM
from mastervault.review.queue import ReviewQueue
from mastervault.storage.sqlite import SqliteBackend

TEST_DIM = 8


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "vault").mkdir(parents=True)
    (ws / "review" / "pending").mkdir(parents=True)
    (ws / "review" / "archive").mkdir(parents=True)
    (ws / "runs").mkdir(parents=True)
    return ws


@pytest.fixture
def settings(workspace: Path) -> Settings:
    return Settings(
        paths=PathsCfg(workspace=workspace),
        embedding=EmbeddingCfg(provider="mock"),
        llm=LLMCfg(provider="mock"),
        ingestion=IngestionCfg(band_exists=0.80, band_candidate=0.60, max_claims_per_doc=10),
        ask=AskCfg(max_rounds=3, budget_usd=1.0),
        budgets=BudgetsCfg(ingest=5.0, lint=1.0, deliberate=1.0),
    )


@pytest.fixture
def embedder() -> MockEmbedding:
    return MockEmbedding(TEST_DIM)


@pytest.fixture
def llm() -> MockLLM:
    return MockLLM()


@pytest.fixture
def backend(workspace: Path, embedder: MockEmbedding):
    b = SqliteBackend(workspace / "index.db")
    b.init_schema(embedder.dimensions, embedder.model_version)
    yield b
    b.close()


@pytest.fixture
def review_queue(settings: Settings) -> ReviewQueue:
    return ReviewQueue.from_settings(settings)


@pytest.fixture
def write_raw(tmp_path: Path):
    """Write one small raw ingest-source file under a fresh `raw/` dir."""

    def _write(name: str, text: str) -> Path:
        p = tmp_path / "raw" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    return _write


def _write_wiki_note(
    vault_dir: Path,
    domain: str,
    slug: str,
    title: str,
    aliases: list[str],
    body: str = "Definition body.",
) -> Path:
    """Seed a hand-authored wiki note directly on disk (bypasses the pipeline)."""
    alias_yaml = "\n".join(f"  - {a}" for a in aliases)
    text = (
        "---\n"
        f"domain: {domain}\n"
        "type: wiki\n"
        f"title: {title}\n"
        f"aliases:\n{alias_yaml}\n"
        "tags: []\n"
        "status: processed\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Definition\n\n"
        f"{body}\n"
    )
    path = vault_dir / domain / "wiki" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def write_wiki_note():
    """`write_wiki_note(vault_dir, domain, slug, title, aliases, body=...)` fixture."""
    return _write_wiki_note
