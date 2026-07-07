"""Concept matching: route one new claim to an existing wiki concept, a
corpus-check candidate pairing, or a tally toward a brand-new concept.

Cheapest-first resolution:

1. Alias-exact — one of the claim's `affects_candidates`, or the claim
   statement itself, contains a known wiki alias verbatim (case-insensitive,
   word-boundary, longest alias wins). This is free (no embedding call).
2. KNN band — no exact alias; embed the statement and compare against
   `record_type='wiki'` rows. `sim >= band_exists` -> exists (attach
   directly); `band_candidate <= sim < band_exists` -> a corpus-check
   candidate pairing; below `band_candidate` -> no match at all (the caller
   tallies this claim toward a NEW concept).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from mastervault.config import IngestionCfg
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.storage.base import StorageBackend

MatchKind = Literal["exists", "candidate", "new"]


@dataclass(frozen=True)
class MatchResult:
    kind: MatchKind
    wiki_slug: str | None = None
    domain: str | None = None
    similarity: float | None = None
    matched_alias: str | None = None


def _wiki_slug_domain(record_id: str) -> tuple[str, str]:
    """("wiki", domain, slug) -> (domain, slug); record_id is always well-formed here."""
    _prefix, domain, slug = record_id.split(":", 2)
    return domain, slug


def _alias_exact(
    haystacks: list[str], alias_index: dict[str, tuple[str, str]]
) -> MatchResult | None:
    if not alias_index:
        return None
    for alias in sorted(alias_index, key=lambda a: (-len(a), a)):
        if not alias:
            continue
        pattern = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
        if any(pattern.search(hay) for hay in haystacks):
            slug, domain = alias_index[alias]
            return MatchResult(kind="exists", wiki_slug=slug, domain=domain, matched_alias=alias)
    return None


def match_claim(
    statement: str,
    affects_candidates: list[str],
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    cfg: IngestionCfg,
) -> MatchResult:
    """Resolve one claim to an existing concept, a candidate pairing, or 'new'."""
    haystacks = [c.lower() for c in affects_candidates] + [statement.lower()]
    exact = _alias_exact(haystacks, backend.alias_index())
    if exact is not None:
        return exact

    vectors = embedder.embed([statement])
    if not vectors or not any(vectors[0]):
        return MatchResult(kind="new")
    hits = backend.knn(vectors[0], k=1, record_types=["wiki"])
    if not hits:
        return MatchResult(kind="new")

    record_id, similarity = hits[0]
    domain, slug = _wiki_slug_domain(record_id)
    if similarity >= cfg.band_exists:
        return MatchResult(kind="exists", wiki_slug=slug, domain=domain, similarity=similarity)
    if similarity >= cfg.band_candidate:
        return MatchResult(kind="candidate", wiki_slug=slug, domain=domain, similarity=similarity)
    return MatchResult(kind="new", similarity=similarity)
