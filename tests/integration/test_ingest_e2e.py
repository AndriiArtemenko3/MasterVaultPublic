"""End-to-end ingest over the real tests/fixtures/raw_docs corpus.

Deliberately self-contained (no shared `backend`/`settings` fixtures): the
root conftest's `backend` fixture is parametrized sqlite/postgres and shared
by sibling integration tests this task doesn't own, so this module builds its
own hermetic sqlite-backed environment via plain helper functions instead of
fixtures that could collide with (or accidentally shadow) that fixture.

Covers: notes written + validated, claims indexed via one sync_vault pass, an
alias-exact wikilink auto-inserted at tier 1, a tier-3 new-wiki-page item
queued with the correct pattern_key once a brand-new concept crosses the
run's occurrence threshold, and summary counts.
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
from mastervault.contracts.claims import ClaimCandidate, ClaimExtractionOut
from mastervault.contracts.wiki_draft import WikiDraftOut
from mastervault.core.errors import EXIT_CODES
from mastervault.ingest.validate import validate_source_note
from mastervault.models import ChangeType, Domain, ReviewStatus
from mastervault.pipelines.ingest import run_ingest
from mastervault.providers.embedding import MockEmbedding
from mastervault.providers.llm import MockLLM
from mastervault.review.queue import ReviewQueue
from mastervault.storage.sqlite import SqliteBackend

pytestmark = pytest.mark.integration

RAW_DOCS = Path(__file__).resolve().parents[1] / "fixtures" / "raw_docs"
DIM = 8

WIKI_ENTRY = """---
domain: customer-support
type: wiki
title: Shipping Credit
aliases:
  - shipping credit
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Shipping Credit

## Definition

A shipping credit is a partial refund of shipping cost issued for a delayed
or mishandled order.
"""

NEW_ENTRY_BODY = """## Definition

**Operating:** The loyalty tier program rewards repeat purchases with points-based tiers.

## Main Insights

- Silver tier unlocks at 500 points and includes free standard shipping.
- Gold tier unlocks at 1500 points and adds a 10 percent order discount.

## Why It Compounds

Higher tiers raise repeat-purchase rate, which compounds program ROI as the
member base ages into higher tiers over time.

## Cross-Refs

None yet."""


def _build_env(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "vault").mkdir(parents=True)
    (workspace / "review" / "pending").mkdir(parents=True)
    (workspace / "review" / "archive").mkdir(parents=True)
    (workspace / "runs").mkdir(parents=True)

    settings = Settings(
        paths=PathsCfg(workspace=workspace),
        embedding=EmbeddingCfg(provider="mock"),
        llm=LLMCfg(provider="mock"),
        ingestion=IngestionCfg(band_exists=0.80, band_candidate=0.60, max_claims_per_doc=10),
        ask=AskCfg(max_rounds=3, budget_usd=1.0),
        budgets=BudgetsCfg(ingest=5.0, lint=1.0, deliberate=1.0),
    )
    embedder = MockEmbedding(DIM)
    backend = SqliteBackend(workspace / "index.db")
    backend.init_schema(embedder.dimensions, embedder.model_version)
    llm = MockLLM()
    review_queue = ReviewQueue.from_settings(settings)
    return settings, backend, embedder, llm, review_queue


def _seed_shipping_credit_wiki(vault_dir: Path) -> None:
    path = vault_dir / "customer-support" / "wiki" / "shipping-credit.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WIKI_ENTRY, encoding="utf-8")


def _push_claim_extraction_responses(llm: MockLLM) -> None:
    """One push per raw doc, in the alphabetical order `discover_units` walks them."""
    # faq-refund-policy.md
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Refunds are issued within 30 days of delivery for unused items.",
                    confidence="high",
                    affects_candidates=["refund-policy"],
                )
            ]
        ),
    )
    # policy-loyalty-rewards.md — two claims tagged with the same brand-new
    # concept label, so the run's occurrence tally reaches 2 and drafts a page.
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Members reach Silver tier at 500 points and get free standard shipping.",
                    confidence="high",
                    affects_candidates=["loyalty-tier-program"],
                ),
                ClaimCandidate(
                    statement="Members reach Gold tier at 1500 points and get a 10 percent discount.",
                    confidence="high",
                    affects_candidates=["loyalty-tier-program"],
                ),
            ]
        ),
    )
    # sop-restock-checklist.txt
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Any SKU that falls below 20 units on hand must be reordered.",
                    confidence="high",
                    affects_candidates=["restock-policy"],
                )
            ]
        ),
    )
    # ticket-shipping-delay-1042.txt — statement literally contains the
    # "shipping credit" alias, so concept_match resolves it alias-exact.
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Driftwood issued a $10 shipping credit for the delayed order 58821.",
                    confidence="high",
                    affects_candidates=["shipping-credit"],
                )
            ]
        ),
    )


def test_ingest_over_raw_docs_writes_indexes_links_and_queues(tmp_path):
    settings, backend, embedder, llm, review_queue = _build_env(tmp_path)
    vault_dir = settings.paths.vault_dir
    _seed_shipping_credit_wiki(vault_dir)
    _push_claim_extraction_responses(llm)
    llm.push("wiki_draft", WikiDraftOut(body_markdown=NEW_ENTRY_BODY, aliases=["loyalty program", "tier rewards"]))

    outcome = run_ingest(RAW_DOCS, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)

    # -- exit + summary shape --------------------------------------------------
    assert outcome.exit_code == EXIT_CODES["ok"]
    assert outcome.summary["units_total"] == 4
    assert outcome.summary["units_completed"] == 4
    assert outcome.summary["claims_routed"] == 5  # 1 + 2 + 1 + 1
    assert outcome.summary["docs_upserted"] >= 4

    # -- notes written + validated ---------------------------------------------
    sources_dir = vault_dir / "customer-support" / "sources"
    note_paths = sorted(sources_dir.glob("*.md"))
    assert len(note_paths) == 4
    for note_path in note_paths:
        report = validate_source_note(note_path, fix=False, settings=settings)
        assert report.status in ("pass", "fixed")
        assert report.hard_fails == []

    # -- claims indexed ----------------------------------------------------------
    counts = backend.stats()["counts"]
    assert counts["claims"] == 5
    assert counts["documents"] >= 5  # 4 sources + the pre-seeded wiki entry

    # -- alias-exact tier-1 wikilink inserted -----------------------------------
    ticket_note = next(p for p in note_paths if "ticket" in p.name.lower() or "delay" in p.name.lower())
    assert "[[shipping-credit]]" in ticket_note.read_text(encoding="utf-8")

    # -- tier-3 new-wiki-page queued with the correct pattern_key ----------------
    pending = review_queue.list_items(status=ReviewStatus.PENDING)
    new_wiki_items = [item for _, item in pending if item.change_type == ChangeType.NEW_WIKI_PAGE]
    assert len(new_wiki_items) == 1
    item = new_wiki_items[0]
    assert item.tier == 3
    assert item.pattern_key == "ingest-new-wiki::loyalty-tier-program"
    assert item.target == "customer-support/wiki/loyalty-tier-program.md"

    stub_path = vault_dir / item.target
    assert stub_path.is_file()
    assert "domain: customer-support" in stub_path.read_text(encoding="utf-8")

    backend.close()


def test_reingest_same_directory_is_a_full_dedupe(tmp_path):
    settings, backend, embedder, llm, review_queue = _build_env(tmp_path)
    _seed_shipping_credit_wiki(settings.paths.vault_dir)
    _push_claim_extraction_responses(llm)
    llm.push("wiki_draft", WikiDraftOut(body_markdown=NEW_ENTRY_BODY, aliases=[]))

    first = run_ingest(RAW_DOCS, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert first.exit_code == EXIT_CODES["ok"]

    second = run_ingest(RAW_DOCS, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert second.exit_code == EXIT_CODES["ok"]
    assert second.summary["units_total"] == 0

    backend.close()
