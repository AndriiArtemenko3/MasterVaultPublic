"""Hybrid search: alias front-door + legacy and structural channels + optional rerank.

Pipeline:

1. Alias front-door — resolve the query to a wiki entry; it becomes the
   pinned `wiki_card`, excluded from the fused hit list.
2. Channels — lexical claims (30), lexical docs (20), vector k-NN (30), the
   wiki graph (20) seeded by the alias hit plus wiki records in the vector
   top-10, and parser-neutral structural FTS (30).
3. RRF fusion over the populated ranked lists, then hydration into `Hit`
   models with per-channel 1-based ranks. An empty structural channel is
   omitted so legacy fusion scores remain identical.
4. Optional cross-encoder rerank of the top `retrieval.rerank_pool` hits,
   then trim to k.

Every stage tolerates empty channels — an index with no embeddings, no
aliases, or no claims still searches cleanly. The same tolerance backs the
`channels` / `use_alias` ablation knobs below: excluding a channel is just
skipping its computation and leaving its ranked list empty, no special-casing
needed downstream in fuse/hydrate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

from mastervault.config import Settings
from mastervault.evidence import evidence_by_claim, validate_structural_hits
from mastervault.models import ChannelRank, Confidence, Domain, Hit, RecordType
from mastervault.providers import Candidate, EmbeddingProvider, Reranker
from mastervault.retrieval.channels import (
    alias_frontdoor,
    graph_channel,
    lexical_claims,
    lexical_docs,
    structural_channel,
    vector_channel,
)
from mastervault.retrieval.fuse import rrf_fuse
from mastervault.storage.base import StorageBackend
from mastervault.sync.indexer import wiki_definition_text

LEXICAL_CLAIMS_K = 30
LEXICAL_DOCS_K = 20
VECTOR_K = 30
GRAPH_K = 20
GRAPH_SEED_VECTOR_TOP = 10
STRUCTURAL_K = 30

_DOC_BODY_EXCERPT_CHARS = 600

CHANNELS = ("lexical_claims", "lexical_docs", "vector", "graph", "structural")

T = TypeVar("T")


class SearchResult(BaseModel):
    wiki_card: Hit | None = None
    hits: list[Hit] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    channel_counts: dict[str, int] = Field(default_factory=dict)


def _wiki_slug(record_id: str) -> str:
    """Slug from a "wiki:<domain>:<slug>" id."""
    return record_id.split(":", 2)[2]


def _doc_hit(doc_id: str, backend: StorageBackend) -> Hit | None:
    rows = backend.get_documents([doc_id])
    if not rows:
        return None
    row = rows[0]
    if row.doc_type == "wiki":
        record_type = RecordType.WIKI
        text = wiki_definition_text(row.body)
    else:
        # RecordType is a closed enum without a doc-level member; a document
        # hit's retrievable text is its body, so it surfaces as a chunk.
        record_type = RecordType.CHUNK
        text = row.body.strip()[:_DOC_BODY_EXCERPT_CHARS]
    return Hit(
        record_id=doc_id,
        record_type=record_type,
        doc_id=doc_id,
        domain=Domain(row.domain),
        text=text,
        rel_path=row.rel_path,
    )


def _hydrate(
    fused_ids: list[str], backend: StorageBackend, workspace: Path | str
) -> dict[str, Hit]:
    """Hydrate fused ids into Hit models. Ids that no longer resolve are dropped."""
    claim_ids = [i.removeprefix("claim:") for i in fused_ids if i.startswith("claim:")]
    chunk_ids = [i for i in fused_ids if i.startswith("chunk:")]
    structural_ids = [i for i in fused_ids if i.startswith("struct:")]
    doc_ids = [
        i for i in fused_ids if not i.startswith(("claim:", "chunk:", "struct:"))
    ]

    hits: dict[str, Hit] = {}
    hydrated_claims = backend.get_claims(claim_ids)
    evidence = evidence_by_claim(hydrated_claims, backend, workspace)
    for claim in hydrated_claims:
        hits[f"claim:{claim.claim_id}"] = Hit(
            record_id=f"claim:{claim.claim_id}",
            record_type=RecordType.CLAIM,
            doc_id=claim.doc_id,
            domain=Domain(claim.domain),
            text=claim.statement,
            rel_path=claim.rel_path,
            confidence=Confidence(claim.confidence),
            evidence=evidence.get(claim.claim_id, []),
        )
    for chunk in backend.get_chunks(chunk_ids):
        hits[chunk.chunk_id] = Hit(
            record_id=chunk.chunk_id,
            record_type=RecordType.CHUNK,
            doc_id=chunk.doc_id,
            domain=Domain(chunk.domain),
            text=chunk.text,
            rel_path=chunk.rel_path,
        )
    structural_get = getattr(backend, "get_structural_records", None)
    structural_rows = list(structural_get(structural_ids)) if callable(structural_get) else []
    validate_structural_hits(structural_rows, backend, workspace)
    for row in structural_rows:
        hits[row.record_id] = Hit(
            record_id=row.record_id,
            record_type=RecordType.STRUCTURAL,
            doc_id=row.doc_id,
            domain=Domain(row.domain),
            text=row.text,
            rel_path=row.rel_path,
            evidence=row.evidence,
            structural_kind=row.record_kind,
            source_identity={
                "asset_sha256": row.asset_sha256,
                "parsed_artifact_sha256": row.parsed_artifact_sha256,
                "parser": row.parser,
                "parser_version": row.parser_version,
                "parser_core_version": row.parser_core_version,
                "parser_profile": row.parser_profile,
                "normalization_profile": row.normalization_profile,
                "model_identity": row.model_identity,
                "resource_limits": row.resource_limits,
            },
        )
    for doc_id in doc_ids:
        hit = _doc_hit(doc_id, backend)
        if hit is not None:
            hits[doc_id] = hit
    return hits


def hybrid_search(
    query: str,
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    reranker: Reranker | None = None,
    *,
    k: int | None = None,
    domain: str | None = None,
    record_types: list[str] | None = None,
    rerank: bool = False,
    channels: Iterable[str] | None = None,
    use_alias: bool = True,
) -> SearchResult:
    """`channels` and `use_alias` are ablation knobs for the retrieval eval
    harness (`mastervault.evals`): `channels` restricts which fused lists get
    computed (default: all available channels), and `use_alias=False`
    disables the alias front-door (no
    `wiki_card`, no alias-seeded graph entry). Both default to the full
    pipeline, so existing callers are unaffected.
    """
    timings: dict[str, float] = {}
    active = set(CHANNELS) if channels is None else set(channels)
    unknown = active - set(CHANNELS)
    if unknown:
        raise ValueError(f"unknown channel(s) {sorted(unknown)}; choose from {CHANNELS}")

    def timed(name: str, fn: Callable[[], T]) -> T:
        start = time.perf_counter()
        out = fn()
        timings[name] = round(time.perf_counter() - start, 6)
        return out

    wiki_doc_id, _matched_alias = (
        timed("alias", lambda: alias_frontdoor(query, backend)) if use_alias else (None, None)
    )
    lex_claims = (
        timed("lexical_claims", lambda: lexical_claims(query, backend, LEXICAL_CLAIMS_K, domain))
        if "lexical_claims" in active
        else []
    )
    lex_docs = (
        timed("lexical_docs", lambda: lexical_docs(query, backend, LEXICAL_DOCS_K, domain))
        if "lexical_docs" in active
        else []
    )
    vec = (
        timed("vector", lambda: vector_channel(query, backend, embedder, VECTOR_K, domain))
        if "vector" in active
        else []
    )

    seed_slugs: list[str] = []
    if wiki_doc_id is not None:
        seed_slugs.append(_wiki_slug(wiki_doc_id))
    for record_id in vec[:GRAPH_SEED_VECTOR_TOP]:
        if record_id.startswith("wiki:"):
            slug = _wiki_slug(record_id)
            if slug not in seed_slugs:
                seed_slugs.append(slug)
    graph = (
        timed("graph", lambda: graph_channel(seed_slugs, backend, GRAPH_K))
        if "graph" in active
        else []
    )
    structural = (
        timed("structural", lambda: structural_channel(query, backend, STRUCTURAL_K, domain))
        if "structural" in active
        else []
    )

    channel_lists = {
        "lexical_claims": lex_claims,
        "lexical_docs": lex_docs,
        "vector": vec,
        "graph": graph,
    }
    if structural:
        channel_lists["structural"] = structural
    channel_counts = {name: len(ids) for name, ids in channel_lists.items()}

    start = time.perf_counter()
    # Preserve the exact legacy RRF input list when the additive structural
    # channel is empty; existing indexes therefore retain byte-for-byte ranks.
    fusion_lists = [lex_claims, lex_docs, vec, graph]
    if structural:
        fusion_lists.append(structural)
    fused = rrf_fuse(fusion_lists, k=settings.retrieval.rrf_k)
    if wiki_doc_id is not None:
        fused.pop(wiki_doc_id, None)  # the wiki card is pinned, never a hit
    fused_ids = [i for i, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]

    rank_maps = {
        name: {record_id: rank for rank, record_id in enumerate(ids, start=1)}
        for name, ids in channel_lists.items()
    }
    hydrated = _hydrate(fused_ids, backend, settings.paths.workspace)
    hits: list[Hit] = []
    for record_id in fused_ids:
        hit = hydrated.get(record_id)
        if hit is None:
            continue
        if domain is not None and hit.domain.value != domain:
            continue
        if record_types is not None and hit.record_type.value not in record_types:
            continue
        hit.rrf_score = round(fused[record_id], 6)
        hit.channels = ChannelRank(
            lexical_claims=rank_maps["lexical_claims"].get(record_id),
            lexical_docs=rank_maps["lexical_docs"].get(record_id),
            vector=rank_maps["vector"].get(record_id),
            graph=rank_maps["graph"].get(record_id),
            structural=rank_maps.get("structural", {}).get(record_id),
        )
        hits.append(hit)
    timings["fuse_hydrate"] = round(time.perf_counter() - start, 6)

    if rerank and reranker is not None and hits:
        start = time.perf_counter()
        pool = hits[: settings.retrieval.rerank_pool]
        scored = reranker.rerank(
            query, [Candidate(h.record_id, h.text) for h in pool], top_k=len(pool)
        )
        score_by_id = dict(scored)
        original_order = {h.record_id: i for i, h in enumerate(pool)}
        for hit in pool:
            hit.rerank_score = score_by_id.get(hit.record_id)
        pool.sort(
            key=lambda h: (
                -(h.rerank_score if h.rerank_score is not None else float("-inf")),
                original_order[h.record_id],
            )
        )
        hits = pool + hits[settings.retrieval.rerank_pool :]
        timings["rerank"] = round(time.perf_counter() - start, 6)

    hits = hits[: k if k is not None else settings.retrieval.k]

    wiki_card: Hit | None = None
    if wiki_doc_id is not None:
        wiki_card = _doc_hit(wiki_doc_id, backend)

    return SearchResult(
        wiki_card=wiki_card,
        hits=hits,
        timings=timings,
        channel_counts=channel_counts,
    )
