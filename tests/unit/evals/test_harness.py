"""evals.harness: golden-set resolution, per-query scoring, channel-toggle
ablation, and baseline comparison.

Two corpora feed these tests: the repo's small `tests/fixtures/mini_vault`
(synced with a MockEmbedding — fast, hermetic, no network) for scoring /
channel-toggle mechanics, and the real shipped
`datasets/larkstead/{golden,processed}` for the resolver regression test that
locks the golden query set to the live corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.evals.harness import (
    HYBRID,
    LEXICAL_ONLY,
    VECTOR_ONLY,
    ConfigReport,
    QueryScore,
    available_configs,
    compare_to_baseline,
    load_golden_queries,
    resolve_golden_set,
    run_config,
    score_query,
)
from mastervault.providers.reranker import MockReranker
from mastervault.retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "datasets" / "larkstead" / "golden"
PROCESSED_DIR = REPO_ROOT / "datasets" / "larkstead" / "processed"
# Same path conftest.py in this directory resolves independently (avoids a
# cross-file relative import, matching this repo's test-module convention).
MINI_VAULT = Path(__file__).resolve().parents[2] / "fixtures" / "mini_vault"

EXACT_PHRASE_QUERY = {
    "id": "t1",
    "class": "easy-lexical",
    "query": "restocking fee is 10 percent",
    "relevant_docs": ["customer-support/sources/policy-returns-and-refunds.md"],
    "relevant_claims": ["policy-returns-02"],
    "notes": "",
}

NEGATIVE_QUERY = {
    "id": "n1",
    "class": "negative-no-answer",
    "query": "does Larkstead accept cryptocurrency payments",
    "relevant_docs": [],
    "relevant_claims": [],
    "notes": "",
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TestResolveGoldenSet:
    def test_all_ids_resolve_cleanly(self):
        queries = [
            {
                "id": "q1",
                "class": "easy-lexical",
                "relevant_docs": ["customer-support/sources/policy-returns-and-refunds.md"],
                "relevant_claims": ["policy-returns-02"],
            }
        ]
        report = resolve_golden_set(queries, MINI_VAULT)
        assert report.ok
        assert report.errors == []
        assert report.relevant_docs_resolved == report.relevant_docs_total == 1
        assert report.relevant_claims_resolved == report.relevant_claims_total == 1

    def test_missing_doc_and_claim_are_flagged(self):
        queries = [
            {
                "id": "q1",
                "class": "easy-lexical",
                "relevant_docs": ["customer-support/sources/does-not-exist.md"],
                "relevant_claims": ["ghost-claim-99"],
            }
        ]
        report = resolve_golden_set(queries, MINI_VAULT)
        assert not report.ok
        assert len(report.errors) == 2
        assert any("does-not-exist.md" in e for e in report.errors)
        assert any("ghost-claim-99" in e for e in report.errors)

    def test_negative_class_with_empty_relevant_sets_always_resolves(self):
        report = resolve_golden_set([NEGATIVE_QUERY], MINI_VAULT)
        assert report.ok
        assert report.relevant_docs_total == 0
        assert report.relevant_claims_total == 0

    def test_real_golden_query_set_resolves_100_percent(self):
        """Regression lock: every relevant_docs/relevant_claims entry in the
        shipped golden set must exist in the live datasets/larkstead/processed
        corpus. A non-empty errors list here is a build error."""
        queries = load_golden_queries(GOLDEN_DIR / "queries.yaml")
        assert len(queries) == 52
        report = resolve_golden_set(queries, PROCESSED_DIR)
        assert report.ok, report.errors
        assert report.relevant_docs_total > 0
        assert report.relevant_claims_total > 0
        assert report.class_counts == {
            "easy-lexical": 14,
            "semantic-paraphrase": 12,
            "cross-domain-multi-hop": 10,
            "contradiction": 8,
            "negative-no-answer": 8,
        }


# ---------------------------------------------------------------------------
# Per-query scoring against mini_vault
# ---------------------------------------------------------------------------


class TestScoreQuery:
    def test_hybrid_finds_exact_phrase_within_top_5(self, settings, synced_backend, embedder):
        score = score_query(EXACT_PHRASE_QUERY, settings, synced_backend, embedder, HYBRID)
        # The alias front-door also pins the restocking-fee wiki card ahead of
        # the fused hit list for this exact phrase (see test_search.py's own
        # CLI assertion for the same query), which this harness grades as
        # rank 1 and isn't itself in relevant_docs — so the golden document
        # can legitimately land at rank 2 rather than rank 1.
        assert score.recall_at_5 == pytest.approx(1.0)
        assert score.recall_at_10 == pytest.approx(1.0)
        assert score.mrr == pytest.approx(0.5)
        assert score.ndcg_at_10 > 0.0
        assert score.abstained is None  # only graded for negative-class queries

    def test_lexical_only_still_finds_the_exact_phrase(self, settings, synced_backend, embedder):
        score = score_query(EXACT_PHRASE_QUERY, settings, synced_backend, embedder, LEXICAL_ONLY)
        assert score.recall_at_5 == pytest.approx(1.0)

    def test_negative_query_is_scored_via_abstention_not_recall(
        self, settings, synced_backend, embedder
    ):
        score = score_query(NEGATIVE_QUERY, settings, synced_backend, embedder, LEXICAL_ONLY)
        assert score.recall_at_5 is None
        assert score.recall_at_10 is None
        assert score.ndcg_at_10 is None
        assert score.mrr is None
        assert score.abstained is True  # no lexical token overlap at all in mini_vault

    def test_abstention_floor_is_respected(self, settings, synced_backend, embedder):
        high_floor = score_query(
            NEGATIVE_QUERY, settings, synced_backend, embedder, HYBRID, abstention_floor=1.0
        )
        assert high_floor.abstained is True
        low_floor = score_query(
            NEGATIVE_QUERY, settings, synced_backend, embedder, HYBRID, abstention_floor=-1.0
        )
        assert low_floor.abstained is False


# ---------------------------------------------------------------------------
# Channel-toggle ablation correctness (search.py's channels/use_alias knobs)
# ---------------------------------------------------------------------------


class TestChannelToggle:
    def test_vector_only_hits_carry_no_lexical_rank(self, settings, synced_backend, embedder):
        result = hybrid_search(
            EXACT_PHRASE_QUERY["query"],
            settings,
            synced_backend,
            embedder,
            channels=VECTOR_ONLY.channels,
            use_alias=VECTOR_ONLY.use_alias,
        )
        assert result.hits
        assert all(h.channels.lexical_claims is None for h in result.hits)
        assert all(h.channels.lexical_docs is None for h in result.hits)
        assert result.wiki_card is None  # use_alias=False

    def test_lexical_only_hits_carry_no_vector_rank(self, settings, synced_backend, embedder):
        result = hybrid_search(
            EXACT_PHRASE_QUERY["query"],
            settings,
            synced_backend,
            embedder,
            channels=LEXICAL_ONLY.channels,
            use_alias=LEXICAL_ONLY.use_alias,
        )
        assert result.hits
        assert all(h.channels.vector is None for h in result.hits)

    def test_unknown_channel_name_raises(self, settings, synced_backend, embedder):
        with pytest.raises(ValueError, match="unknown channel"):
            hybrid_search(
                "anything", settings, synced_backend, embedder, channels={"not-a-real-channel"}
            )

    def test_default_channels_match_full_pipeline(self, settings, synced_backend, embedder):
        """channels=None must behave exactly like omitting the argument."""
        explicit = hybrid_search(EXACT_PHRASE_QUERY["query"], settings, synced_backend, embedder)
        implicit = hybrid_search(
            EXACT_PHRASE_QUERY["query"], settings, synced_backend, embedder, channels=None
        )
        assert [h.record_id for h in explicit.hits] == [h.record_id for h in implicit.hits]


# ---------------------------------------------------------------------------
# Config availability
# ---------------------------------------------------------------------------


class TestAvailableConfigs:
    def test_excludes_rerank_config_without_a_reranker(self, settings):
        configs, notes = available_configs(settings, None)
        assert [c.name for c in configs] == ["lexical-only", "vector-only", "hybrid"]
        assert any("N/A" in n for n in notes)

    def test_excludes_rerank_config_for_the_null_passthrough(self, settings):
        from mastervault.providers.reranker import NullReranker

        configs, notes = available_configs(settings, NullReranker())
        assert "hybrid+rerank" not in [c.name for c in configs]
        assert notes

    def test_includes_rerank_config_with_a_real_reranker(self, settings):
        configs, notes = available_configs(settings, MockReranker())
        assert "hybrid+rerank" in [c.name for c in configs]
        assert notes == []


# ---------------------------------------------------------------------------
# Config-level run + aggregation
# ---------------------------------------------------------------------------


class TestRunConfig:
    def test_produces_one_score_per_query_and_sane_aggregates(
        self, settings, synced_backend, embedder
    ):
        report = run_config(
            HYBRID, [EXACT_PHRASE_QUERY, NEGATIVE_QUERY], settings, synced_backend, embedder
        )
        assert len(report.scores) == 2
        overall = report.overall()
        assert 0.0 <= overall["recall_at_5"] <= 1.0
        assert 0.0 <= overall["recall_at_10"] <= 1.0
        assert 0.0 <= overall["ndcg_at_10"] <= 1.0
        assert 0.0 <= overall["mrr"] <= 1.0
        assert "abstention_rate" in overall

        per_class = report.per_class()
        assert per_class["easy-lexical"]["n"] == 1
        assert per_class["negative-no-answer"]["n"] == 1

        as_dict = report.to_dict()
        assert as_dict["config"] == "hybrid"
        assert len(as_dict["queries"]) == 2


# ---------------------------------------------------------------------------
# Baseline comparison (pure, hand-built reports — no search involved)
# ---------------------------------------------------------------------------


class TestCompareToBaseline:
    def _report(self, recall: float) -> ConfigReport:
        score = QueryScore(
            id="q1", cls="easy-lexical", recall_at_5=recall, recall_at_10=recall,
            ndcg_at_10=recall, mrr=recall,
        )
        return ConfigReport(config="hybrid", scores=[score])

    def test_matching_current_and_baseline_has_no_regression(self):
        good = self._report(1.0)
        baseline = {"configs": {"hybrid": good.to_dict()}}
        result = compare_to_baseline({"hybrid": good}, baseline, tolerance=0.02)
        assert result["regressed"] == []

    def test_regression_beyond_tolerance_is_flagged(self):
        good = self._report(1.0)
        bad = self._report(0.5)
        baseline = {"configs": {"hybrid": good.to_dict()}}
        result = compare_to_baseline({"hybrid": bad}, baseline, tolerance=0.02)
        assert result["regressed"]
        assert any("recall_at_5" in r for r in result["regressed"])

    def test_small_drop_within_tolerance_is_not_flagged(self):
        good = self._report(1.0)
        slightly_worse = self._report(0.99)
        baseline = {"configs": {"hybrid": good.to_dict()}}
        result = compare_to_baseline({"hybrid": slightly_worse}, baseline, tolerance=0.02)
        assert result["regressed"] == []

    def test_config_missing_from_baseline_is_noted_not_crashed(self):
        good = self._report(1.0)
        baseline = {"configs": {"hybrid": good.to_dict()}}
        result = compare_to_baseline({"vector-only": good}, baseline, tolerance=0.02)
        assert result["deltas"]["vector-only"] == {"note": "no baseline for this config"}
        assert result["regressed"] == []
