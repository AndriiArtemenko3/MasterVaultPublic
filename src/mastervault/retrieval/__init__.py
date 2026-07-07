"""Hybrid retrieval: channels -> RRF fusion -> hydration -> optional rerank.

`hybrid_search` is the front door; the channels, fuser, and MMR selectors are
exported for pipelines that compose their own retrieval.
"""

from mastervault.retrieval.channels import (
    alias_frontdoor,
    graph_channel,
    lexical_claims,
    lexical_docs,
    vector_channel,
)
from mastervault.retrieval.fuse import RRF_K, rrf_fuse
from mastervault.retrieval.mmr import mmr_select, mmr_select_texts
from mastervault.retrieval.search import SearchResult, hybrid_search

__all__ = [
    "RRF_K",
    "SearchResult",
    "alias_frontdoor",
    "graph_channel",
    "hybrid_search",
    "lexical_claims",
    "lexical_docs",
    "mmr_select",
    "mmr_select_texts",
    "rrf_fuse",
    "vector_channel",
]
