"""Pure retrieval metrics: recall@k, nDCG@k, MRR.

Every function takes an ordered `retrieved` id list (rank order, index 0 =
top hit) and a `relevant` set of ids in the same id space, and returns a
float in [0, 1]. Callers own the id space — the harness maps `Hit`s to ids
(a claim's `record_id` or a doc's `rel_path`; see `mastervault.evals.harness`)
before calling in here. These functions know nothing about `Hit`, `Settings`,
or storage, and do no I/O.

Convention for an empty `relevant` set: there is nothing to recall, so every
metric here returns 1.0 (vacuously satisfied) rather than raising or
returning 0.0. The harness never scores recall/nDCG/MRR for the golden set's
negative-class queries (which have empty relevant sets by construction) —
those are graded separately via abstention — but the pure functions still
need well-defined behavior for that input, which the unit tests lock down.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Collection[str], k: int) -> float:
    """|top-k(retrieved) ∩ relevant| / |relevant|."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant_set) / len(relevant_set)


def ndcg_at_k(retrieved: Sequence[str], relevant: Collection[str], k: int) -> float:
    """Binary-relevance nDCG@k. A duplicate id in `retrieved` (e.g. two chunks
    from the same document both mapped to that document's rel_path) earns
    gain only on its first occurrence within the top k, mirroring how
    `recall_at_k`'s set intersection naturally avoids double-counting.
    """
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0

    seen: set[str] = set()
    dcg = 0.0
    for rank, item in enumerate(retrieved[:k], start=1):
        if item in relevant_set and item not in seen:
            seen.add(item)
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 1.0
    return dcg / idcg


def mrr(retrieved: Sequence[str], relevant: Collection[str]) -> float:
    """1 / rank of the first relevant id in `retrieved` (1-based); 0.0 if none
    of `retrieved` is relevant. Not truncated by any k — pass an already
    length-limited `retrieved` list if that's the intent.
    """
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / rank
    return 0.0
