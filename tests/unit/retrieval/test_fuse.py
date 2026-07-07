"""RRF math: known ranks -> known scores, correct ordering."""

from __future__ import annotations

import pytest

from mastervault.retrieval.fuse import RRF_K, rrf_fuse


def test_known_ranks_give_known_scores():
    scores = rrf_fuse([["a", "b", "c"], ["c", "a"]])
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)


def test_orders_multi_channel_ids_above_single_channel():
    scores = rrf_fuse([["a", "b", "c"], ["c", "a"]])
    ordered = sorted(scores, key=scores.get, reverse=True)
    assert ordered == ["a", "c", "b"]


def test_custom_k():
    scores = rrf_fuse([["x", "y"]], k=0)
    assert scores["x"] == pytest.approx(1.0)
    assert scores["y"] == pytest.approx(0.5)


def test_empty_lists_contribute_nothing():
    assert rrf_fuse([]) == {}
    assert rrf_fuse([[], []]) == {}
    scores = rrf_fuse([["a"], []])
    assert list(scores) == ["a"]


def test_empty_string_ids_are_skipped():
    scores = rrf_fuse([["", "a"]])
    assert list(scores) == ["a"]
    assert scores["a"] == pytest.approx(1 / (RRF_K + 2))
