"""Reciprocal Rank Fusion for the hybrid retrieval merge.

Fuses ranked candidate lists from the retrieval channels (lexical claims,
lexical docs, vector k-NN, wiki graph) into one scale-invariant ordering.

Reference: Cormack, G.V.; Clarke, C.L.A.; Buettcher, S. (2009).
"Reciprocal Rank Fusion outperforms Condorcet and individual rank learning
methods." SIGIR 2009. k=60 is the standard from the paper.
"""

from __future__ import annotations

RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(d) = sum_r 1 / (k + rank_r(d)).

    Each input is an ordered list of ids from one ranker, best first. Empty
    lists contribute nothing. Returned dict maps id -> fused score (higher
    is better).
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_idx, doc_id in enumerate(ranked):
            if not doc_id:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank_idx + 1)
    return scores
