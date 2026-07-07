"""Maximal Marginal Relevance selection.

MMR(d_i) = lambda * relevance(d_i, q) - (1 - lambda) * max_{d_j in selected} sim(d_i, d_j)

Two variants share one greedy core:

- `mmr_select`       — vector candidates; relevance and similarity are cosine.
- `mmr_select_texts` — text candidates; relevance and similarity are Jaccard
  token overlap (the mock / keyless path).

lambda defaults to 0.7 (relevance-leaning; diversity is a corrective, not the
dominant signal). Ties keep input order, so selection is deterministic.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence

DEFAULT_LAMBDA = 0.7

_TOKEN_RE = re.compile(r"\w+")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _mmr_core(
    relevance: list[float],
    similarity: Callable[[int, int], float],
    lambda_: float,
    n: int,
) -> list[tuple[int, float]]:
    """Greedy MMR over candidate indices. Returns (index, mmr_score) in pick order."""
    total = len(relevance)
    selected: list[tuple[int, float]] = []
    selected_idx: list[int] = []
    available = list(range(total))
    while available and len(selected) < n:
        best_i = -1
        best_mmr = -math.inf
        for i in available:
            if not selected_idx:
                mmr = lambda_ * relevance[i]
            else:
                max_sim = max(similarity(i, j) for j in selected_idx)
                mmr = lambda_ * relevance[i] - (1.0 - lambda_) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_i = i
        selected.append((best_i, best_mmr))
        selected_idx.append(best_i)
        available.remove(best_i)
    return selected


def mmr_select(
    query_vec: Sequence[float],
    candidates: list[tuple[str, Sequence[float]]],
    lambda_: float = DEFAULT_LAMBDA,
    n: int = 10,
) -> list[tuple[str, float]]:
    """Select up to n (id, mmr_score) pairs from (id, vector) candidates, cosine both ways."""
    if not candidates:
        return []
    relevance = [_cosine(query_vec, vec) for _, vec in candidates]

    def similarity(i: int, j: int) -> float:
        return _cosine(candidates[i][1], candidates[j][1])

    picks = _mmr_core(relevance, similarity, lambda_, n)
    return [(candidates[i][0], round(score, 6)) for i, score in picks]


def mmr_select_texts(
    query_tokens: Iterable[str],
    candidates: list[tuple[str, str]],
    lambda_: float = DEFAULT_LAMBDA,
    n: int = 10,
) -> list[tuple[str, float]]:
    """Text fallback: select up to n (id, mmr_score) pairs using Jaccard token overlap."""
    if not candidates:
        return []
    query_set = {t.lower() for t in query_tokens}
    token_sets = [_tokens(text) for _, text in candidates]
    relevance = [_jaccard(query_set, tokens) for tokens in token_sets]

    def similarity(i: int, j: int) -> float:
        return _jaccard(token_sets[i], token_sets[j])

    picks = _mmr_core(relevance, similarity, lambda_, n)
    return [(candidates[i][0], round(score, 6)) for i, score in picks]
