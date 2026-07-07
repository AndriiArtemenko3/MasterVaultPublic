"""Hybrid search: alias front-door + four fused channels + optional rerank.

Pipeline:

1. Alias front-door — resolve the query to a wiki entry; it becomes the
   pinned `wiki_card`, excluded from the fused hit list.
2. Channels — lexical claims (30), lexical docs (20), vector k-NN (30), and
   the wiki graph (20) seeded by the alias hit plus wiki records in the
   vector top-10.
3. RRF fusion over the four ranked lists, then hydration into `Hit` models
   with per-channel 1-based ranks.
4. Optional cross-encoder rerank of the top `retrieval.rerank_pool` hits,
   then trim to k.

Every stage tolerates empty channels — an index with no embeddings, no
aliases, or no claims still searches cleanly.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from mastervault.config import Settings
from mastervault.models import ChannelRank, Confidence, Domain, Hit, RecordType
from mastervault.providers import Candidate, EmbeddingProvider, Reranker
from mastervault.retrieval.channels import (
    alias_frontdoor,
    graph_channel,
    lexical_claims,
    lexical_docs,
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

_DOC_BODY_EXCERPT_CHARS = 600


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


def _hydrate(fused_ids: list[str], backend: StorageBackend) -> dict[str, Hit]:
    """Hydrate fused ids into Hit models. Ids that no longer resolve are dropped."""
    claim_ids = [i.removeprefix("claim:") for i in fused_ids if i.startswith("claim:")]
    chunk_ids = [i for i in fused_ids if i.startswith("chunk:")]
    doc_ids = [i for i in fused_ids if not i.startswith(("claim:", "chunk:"))]

    hits: dict[str, Hit] = {}
    for row in backend.get_claims(claim_ids):
        hits[f"claim:{row.claim_id}"] = Hit(
            record_id=f"claim:{row.claim_id}",
            record_type=RecordType.CLAIM,
            doc_id=row.doc_id,
            domain=Domain(row.domain),
            text=row.statement,
            rel_path=row.rel_path,
            confidence=Confidence(row.confidence),
        )
    for row in backend.get_chunks(chunk_ids):
        hits[row.chunk_id] = Hit(
            record_id=row.chunk_id,
            record_type=RecordType.CHUNK,
            doc_id=row.doc_id,
            domain=Domain(row.domain),
            text=row.text,
            rel_path=row.rel_path,
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
) -> SearchResult:
    timings: dict[str, float] = {}

    def timed(name: str, fn):
        start = time.perf_counter()
        out = fn()
        timings[name] = round(time.perf_counter() - start, 6)
        return out

    wiki_doc_id, _matched_alias = timed("alias", lambda: alias_frontdoor(query, backend))
    lex_claims = timed(
        "lexical_claims", lambda: lexical_claims(query, backend, LEXICAL_CLAIMS_K, domain)
    )
    lex_docs = timed("lexical_docs", lambda: lexical_docs(query, backend, LEXICAL_DOCS_K, domain))
    vec = timed("vector", lambda: vector_channel(query, backend, embedder, VECTOR_K, domain))

    seed_slugs: list[str] = []
    if wiki_doc_id is not None:
        seed_slugs.append(_wiki_slug(wiki_doc_id))
    for record_id in vec[:GRAPH_SEED_VECTOR_TOP]:
        if record_id.startswith("wiki:"):
            slug = _wiki_slug(record_id)
            if slug not in seed_slugs:
                seed_slugs.append(slug)
    graph = timed("graph", lambda: graph_channel(seed_slugs, backend, GRAPH_K))

    channel_lists = {
        "lexical_claims": lex_claims,
        "lexical_docs": lex_docs,
        "vector": vec,
        "graph": graph,
    }
    channel_counts = {name: len(ids) for name, ids in channel_lists.items()}

    start = time.perf_counter()
    fused = rrf_fuse(list(channel_lists.values()), k=settings.retrieval.rrf_k)
    if wiki_doc_id is not None:
        fused.pop(wiki_doc_id, None)  # the wiki card is pinned, never a hit
    fused_ids = [i for i, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]

    rank_maps = {
        name: {record_id: rank for rank, record_id in enumerate(ids, start=1)}
        for name, ids in channel_lists.items()
    }
    hydrated = _hydrate(fused_ids, backend)
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
