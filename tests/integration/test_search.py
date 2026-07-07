"""hybrid_search over a synced mini_vault (both backends) + CLI end-to-end."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.config import RetrievalCfg, Settings
from mastervault.models import Domain, RecordType
from mastervault.providers import MockEmbedding, MockReranker
from mastervault.retrieval import hybrid_search
from mastervault.sync import sync_vault

pytestmark = pytest.mark.integration

MINI_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "mini_vault"
DIM = 8  # must match the backend fixture's schema dim

EXACT_PHRASE = "restocking fee is 10 percent"


@pytest.fixture
def embedder() -> MockEmbedding:
    return MockEmbedding(DIM)


@pytest.fixture
def settings() -> Settings:
    return Settings(retrieval=RetrievalCfg(k=10, rrf_k=60, rerank_pool=30))


@pytest.fixture
def synced(backend, embedder):
    sync_vault(MINI_VAULT, backend, embedder)
    return backend


def test_exact_phrase_surfaces_the_claim_first(synced, embedder, settings):
    result = hybrid_search(EXACT_PHRASE, settings, synced, embedder)
    assert result.hits
    top = result.hits[0]
    assert top.record_id == "claim:policy-returns-02"
    assert top.record_type is RecordType.CLAIM
    assert top.channels.lexical_claims == 1
    assert top.rrf_score > 0
    assert EXACT_PHRASE in top.text
    assert top.rel_path == "customer-support/sources/policy-returns-and-refunds.md"


def test_alias_query_pins_the_wiki_card(synced, embedder, settings):
    result = hybrid_search("what is the refund window", settings, synced, embedder)
    card = result.wiki_card
    assert card is not None
    assert card.doc_id == "wiki:customer-support:refund-window"
    assert card.record_type is RecordType.WIKI
    assert "refund window" in card.text.lower()
    # pinned, never duplicated into the hit list
    assert all(hit.record_id != card.record_id for hit in result.hits)
    assert result.hits  # claims about the concept still rank


def test_paraphrase_query_returns_results_via_vector(synced, embedder, settings):
    result = hybrid_search(
        "how long do buyers have to send an item back", settings, synced, embedder
    )
    assert result.hits
    assert result.channel_counts["vector"] > 0


def test_domain_filter(synced, embedder, settings):
    result = hybrid_search(
        "discount tier pricing", settings, synced, embedder, domain="sales-crm"
    )
    assert result.hits
    assert all(hit.domain is Domain.SALES_CRM for hit in result.hits)


def test_record_type_filter(synced, embedder, settings):
    result = hybrid_search(
        "refund", settings, synced, embedder, record_types=["claim"], k=20
    )
    assert result.hits
    assert all(hit.record_type is RecordType.CLAIM for hit in result.hits)


def test_rerank_off_and_on(synced, embedder, settings):
    off = hybrid_search(EXACT_PHRASE, settings, synced, embedder, rerank=False)
    assert all(hit.rerank_score is None for hit in off.hits)

    on = hybrid_search(
        EXACT_PHRASE, settings, synced, embedder, reranker=MockReranker(), rerank=True
    )
    assert on.hits
    assert on.hits[0].rerank_score is not None
    assert "rerank" in on.timings


def test_k_trims_the_hit_list(synced, embedder, settings):
    result = hybrid_search("refund", settings, synced, embedder, k=3)
    assert len(result.hits) <= 3


def test_empty_index_searches_cleanly(backend, embedder, settings):
    result = hybrid_search("refund window restocking fee", settings, backend, embedder)
    assert result.hits == []
    assert result.wiki_card is None
    assert set(result.channel_counts) == {"lexical_claims", "lexical_docs", "vector", "graph"}


# ---------------------------------------------------------------------------
# CLI end-to-end (sqlite + mock providers via MV_* env)
# ---------------------------------------------------------------------------


def test_cli_init_sync_status_search(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_RERANKER__BACKEND", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(workspace))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from mastervault.cli.app import app

    runner = CliRunner()

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    for sub in ("vault", "review/pending", "review/archive", "runs"):
        assert (workspace / sub).is_dir()
    # idempotent
    assert runner.invoke(app, ["init"]).exit_code == 0

    shutil.copytree(MINI_VAULT, workspace / "vault", dirs_exist_ok=True)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "docs upserted" in result.output

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "sqlite" in result.output
    assert "documents" in result.output

    result = runner.invoke(app, ["search", EXACT_PHRASE, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["wiki_card"]["doc_id"] == "wiki:customer-support:restocking-fee"
    assert payload["hits"]
    top = payload["hits"][0]
    assert top["record_id"] == "claim:policy-returns-02"
    assert top["record_type"] == "claim"
    assert top["confidence"] == "high"
    assert set(top["channels"]) == {"lexical_claims", "lexical_docs", "vector", "graph"}
    assert top["channels"]["lexical_claims"] == 1
    assert top["rrf_score"] > 0
    assert set(payload["channel_counts"]) == {
        "lexical_claims",
        "lexical_docs",
        "vector",
        "graph",
    }

    # human rendering: wiki card header + one line per hit
    result = runner.invoke(app, ["search", EXACT_PHRASE])
    assert result.exit_code == 0, result.output
    assert "wiki:customer-support:restocking-fee" in result.output
    assert "[claim] (high)" in result.output

    result = runner.invoke(app, ["claims", "restocking", "--affects", "restocking-fee"])
    assert result.exit_code == 0, result.output
    assert "policy-returns-02" in result.output

    result = runner.invoke(app, ["wiki"])
    assert result.exit_code == 0, result.output
    assert "refund-window" in result.output

    result = runner.invoke(app, ["wiki", "show", "restocking-fee"])
    assert result.exit_code == 0, result.output
    assert "Restocking Fee" in result.output
