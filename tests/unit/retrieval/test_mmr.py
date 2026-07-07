"""MMR selection: both the cosine-vector and the Jaccard-text variant."""

from __future__ import annotations

import pytest

from mastervault.retrieval.mmr import mmr_select, mmr_select_texts

QUERY_VEC = [1.0, 1.0, 0.0]
VEC_CANDIDATES = [
    ("a", [1.0, 1.0, 0.0]),  # relevance 1.0
    ("b", [1.0, 1.0, 0.0]),  # exact duplicate of a
    ("c", [1.0, 0.0, 0.0]),  # relevance ~0.707, less similar to a
]

QUERY_TOKENS = ["refund", "window"]
TEXT_CANDIDATES = [
    ("a", "refund window"),
    ("b", "refund window"),  # exact duplicate of a
    ("c", "refund policy"),
]


def test_vectors_pure_relevance_keeps_the_duplicate():
    picked = mmr_select(QUERY_VEC, VEC_CANDIDATES, lambda_=1.0, n=2)
    assert [record_id for record_id, _ in picked] == ["a", "b"]


def test_vectors_low_lambda_prefers_diversity_over_duplicate():
    picked = mmr_select(QUERY_VEC, VEC_CANDIDATES, lambda_=0.3, n=2)
    assert [record_id for record_id, _ in picked] == ["a", "c"]
    # first pick score is lambda * relevance; a is a perfect match
    assert picked[0][1] == pytest.approx(0.3)


def test_vectors_edge_cases():
    assert mmr_select(QUERY_VEC, [], n=3) == []
    picked = mmr_select(QUERY_VEC, VEC_CANDIDATES, n=10)
    assert len(picked) == 3  # n larger than the pool returns everything


def test_texts_pure_relevance_keeps_the_duplicate():
    picked = mmr_select_texts(QUERY_TOKENS, TEXT_CANDIDATES, lambda_=1.0, n=2)
    assert [record_id for record_id, _ in picked] == ["a", "b"]


def test_texts_low_lambda_prefers_diversity_over_duplicate():
    picked = mmr_select_texts(QUERY_TOKENS, TEXT_CANDIDATES, lambda_=0.3, n=2)
    assert [record_id for record_id, _ in picked] == ["a", "c"]


def test_texts_case_insensitive_and_empty():
    assert mmr_select_texts(QUERY_TOKENS, [], n=2) == []
    picked = mmr_select_texts(["REFUND", "WINDOW"], TEXT_CANDIDATES, lambda_=1.0, n=1)
    assert picked[0][0] == "a"
    assert picked[0][1] == pytest.approx(1.0)
