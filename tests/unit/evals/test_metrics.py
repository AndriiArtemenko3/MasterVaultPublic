"""recall@k / nDCG@k / MRR against hand-worked examples."""

from __future__ import annotations

import math

import pytest

from mastervault.evals.metrics import mrr, ndcg_at_k, recall_at_k


class TestRecallAtK:
    def test_partial_recall_at_small_k(self):
        retrieved = ["a", "b", "c", "d"]
        relevant = {"b", "d", "e"}  # "e" never retrieved
        assert recall_at_k(retrieved, relevant, 2) == pytest.approx(1 / 3)  # top-2: {a,b} -> b

    def test_full_recall_once_k_covers_all_hits(self):
        retrieved = ["a", "b", "c", "d"]
        relevant = {"b", "d", "e"}
        assert recall_at_k(retrieved, relevant, 4) == pytest.approx(2 / 3)  # top-4: b,d found

    def test_zero_recall_when_nothing_relevant_retrieved(self):
        assert recall_at_k(["x", "y"], {"a", "b"}, 2) == 0.0

    def test_perfect_recall(self):
        assert recall_at_k(["a", "b"], {"a", "b"}, 2) == 1.0

    def test_empty_relevant_set_is_vacuously_satisfied(self):
        assert recall_at_k(["a", "b"], set(), 5) == 1.0

    def test_duplicate_ids_do_not_inflate_recall(self):
        # "b" appears twice in the top-k window; still one relevant id covered.
        assert recall_at_k(["b", "b", "c"], {"b"}, 3) == 1.0

    def test_k_truncates_before_a_later_relevant_hit(self):
        retrieved = ["x", "y", "z", "relevant"]
        assert recall_at_k(retrieved, {"relevant"}, 3) == 0.0
        assert recall_at_k(retrieved, {"relevant"}, 4) == 1.0


class TestNdcgAtK:
    def test_hand_worked_single_relevant_at_rank_two(self):
        # retrieved = [a, b, c], relevant = {b}
        # DCG = 0 (a) + 1/log2(3) (b, rank 2) + 0 (c) = 1/log2(3)
        # IDCG = 1/log2(2) = 1.0 (ideal: the one relevant item at rank 1)
        retrieved = ["a", "b", "c"]
        expected_dcg = 1.0 / math.log2(3)
        expected_ndcg = expected_dcg / 1.0
        assert ndcg_at_k(retrieved, {"b"}, 10) == pytest.approx(expected_ndcg)
        assert ndcg_at_k(retrieved, {"b"}, 10) == pytest.approx(0.6309297535714575)

    def test_perfect_ranking_scores_one(self):
        retrieved = ["a", "b", "c"]
        relevant = {"a", "b"}
        assert ndcg_at_k(retrieved, relevant, 10) == pytest.approx(1.0)

    def test_worst_ranking_scores_below_perfect(self):
        # relevant items both pushed to the bottom of a 4-item list.
        retrieved = ["x", "y", "a", "b"]
        relevant = {"a", "b"}
        ndcg = ndcg_at_k(retrieved, relevant, 10)
        assert 0.0 < ndcg < 1.0

    def test_no_relevant_hits_scores_zero(self):
        assert ndcg_at_k(["x", "y", "z"], {"a"}, 10) == 0.0

    def test_empty_relevant_set_is_vacuously_satisfied(self):
        assert ndcg_at_k(["a", "b"], set(), 10) == 1.0

    def test_k_truncation(self):
        # relevant item sits at rank 5; ndcg@3 must not see it.
        retrieved = ["x", "y", "z", "w", "relevant"]
        assert ndcg_at_k(retrieved, {"relevant"}, 3) == 0.0
        assert ndcg_at_k(retrieved, {"relevant"}, 5) > 0.0

    def test_duplicate_id_scores_gain_once(self):
        # "b" (relevant) appears at rank 1 and rank 2; second occurrence must
        # not earn a second gain, and a distinct second relevant id ("c") at
        # rank 3 still contributes as normal.
        retrieved = ["b", "b", "c"]
        relevant = {"b", "c"}
        dcg = 1.0 / math.log2(2) + 1.0 / math.log2(4)  # b at rank1, c at rank3
        idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)  # ideal: both relevant at rank1,2
        assert ndcg_at_k(retrieved, relevant, 10) == pytest.approx(dcg / idcg)


class TestMrr:
    def test_first_hit_at_rank_two(self):
        assert mrr(["a", "b", "c"], {"b", "c"}) == pytest.approx(0.5)

    def test_first_hit_at_rank_one(self):
        assert mrr(["a", "b", "c"], {"a"}) == pytest.approx(1.0)

    def test_no_relevant_hit_scores_zero(self):
        assert mrr(["a", "b", "c"], {"z"}) == 0.0

    def test_empty_relevant_set_is_vacuously_satisfied(self):
        assert mrr(["a", "b"], set()) == 1.0

    def test_not_truncated_by_any_implicit_k(self):
        retrieved = [f"filler-{i}" for i in range(20)] + ["hit"]
        assert mrr(retrieved, {"hit"}) == pytest.approx(1 / 21)
