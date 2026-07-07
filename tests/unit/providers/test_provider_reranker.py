"""Rerankers: auto-selection, mock Jaccard ordering, null passthrough, cohere mapping."""

from __future__ import annotations

import pytest
from provider_doubles import FakeCohereClient

from mastervault.providers.reranker import (
    Candidate,
    CohereReranker,
    MockReranker,
    NullReranker,
    RerankerUnavailable,
    get_reranker,
)

CANDIDATES = [
    Candidate("a", "alpha beta"),
    Candidate("b", "alpha gamma"),
    Candidate("c", "delta epsilon"),
]


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_auto_selects_cohere_when_key_present(
    make_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "co-test-not-real")
    reranker = get_reranker(make_settings(reranker={"backend": "auto"}))
    assert isinstance(reranker, CohereReranker)
    assert reranker.name == "cohere"


def test_auto_falls_back_to_null_without_key(
    make_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    reranker = get_reranker(make_settings(reranker={"backend": "auto"}))
    assert isinstance(reranker, NullReranker)


def test_explicit_cohere_without_key_fails_fast(
    make_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    with pytest.raises(RerankerUnavailable):
        get_reranker(make_settings(reranker={"backend": "cohere"}))


def test_explicit_mock_and_null(make_settings) -> None:
    assert isinstance(get_reranker(make_settings(reranker={"backend": "mock"})), MockReranker)
    assert isinstance(get_reranker(make_settings(reranker={"backend": "null"})), NullReranker)


def test_local_bge_raises_hint(make_settings) -> None:
    # torch/transformers are not installed here, and even if they were the
    # stub refuses rather than downloading a model.
    with pytest.raises(RerankerUnavailable):
        get_reranker(make_settings(reranker={"backend": "local-bge"}))


# ---------------------------------------------------------------------------
# MockReranker — deterministic Jaccard
# ---------------------------------------------------------------------------


def test_mock_orders_by_token_overlap() -> None:
    result = MockReranker().rerank("alpha beta", CANDIDATES, top_k=3)
    assert [candidate_id for candidate_id, _ in result] == ["a", "b", "c"]
    scores = dict(result)
    assert scores["a"] == pytest.approx(1.0)  # {alpha,beta} / {alpha,beta}
    assert scores["b"] == pytest.approx(1 / 3)  # {alpha} / {alpha,beta,gamma}
    assert scores["c"] == pytest.approx(0.0)
    # Descending by construction.
    assert [s for _, s in result] == sorted((s for _, s in result), reverse=True)


def test_mock_is_deterministic_and_truncates() -> None:
    reranker = MockReranker()
    first = reranker.rerank("alpha beta", CANDIDATES, top_k=2)
    second = reranker.rerank("alpha beta", CANDIDATES, top_k=2)
    assert first == second
    assert len(first) == 2


def test_mock_ties_preserve_input_order() -> None:
    tied = [Candidate("x", "zeta eta"), Candidate("y", "theta iota")]
    result = MockReranker().rerank("alpha", tied, top_k=2)
    assert [candidate_id for candidate_id, _ in result] == ["x", "y"]


def test_mock_empty_query_scores_zero() -> None:
    result = MockReranker().rerank("", CANDIDATES, top_k=3)
    assert all(score == 0.0 for _, score in result)


# ---------------------------------------------------------------------------
# NullReranker — passthrough
# ---------------------------------------------------------------------------


def test_null_preserves_order_scores_zero_and_truncates() -> None:
    reranker = NullReranker()
    result = reranker.rerank("whatever", CANDIDATES, top_k=2)
    assert result == [("a", 0.0), ("b", 0.0)]
    assert reranker.name == "null"


# ---------------------------------------------------------------------------
# CohereReranker — id mapping through a fake client
# ---------------------------------------------------------------------------


def test_cohere_maps_result_indices_back_to_ids(make_settings) -> None:
    settings = make_settings(reranker={"backend": "cohere"})
    fake = FakeCohereClient(results=[(2, 0.9), (0, 0.4)])
    reranker = CohereReranker(settings, client=fake)

    result = reranker.rerank("query", CANDIDATES, top_k=2)

    assert result == [("c", 0.9), ("a", 0.4)]
    call = fake.calls[0]
    assert call["model"] == "rerank-v3.5"
    assert call["documents"] == ["alpha beta", "alpha gamma", "delta epsilon"]
    assert call["top_n"] == 2


def test_cohere_empty_candidates_short_circuit(make_settings) -> None:
    settings = make_settings(reranker={"backend": "cohere"})
    fake = FakeCohereClient(results=[])
    reranker = CohereReranker(settings, client=fake)
    assert reranker.rerank("query", [], top_k=5) == []
    assert fake.calls == []
