"""Retrieval channels. Each returns an ordered list of ids for the fuser.

Id vocabulary per channel:

- alias_frontdoor  -> one wiki doc_id ("wiki:<domain>:<slug>") or None
- lexical_claims   -> record_ids ("claim:<claim-id>")
- lexical_docs     -> doc_ids as stored ("source:<rel>", "wiki:<d>:<s>", ...)
- vector_channel   -> record_ids straight from the embeddings table
- graph_channel    -> record_ids ("claim:<claim-id>")

Every channel degrades to empty output instead of raising: a vault with zero
embeddings, no aliases, or no claims must still search cleanly.
"""

from __future__ import annotations

import re

from mastervault.providers import EmbeddingProvider
from mastervault.storage.base import StorageBackend, StorageError


def alias_frontdoor(query: str, backend: StorageBackend) -> tuple[str | None, str | None]:
    """Resolve the query to a wiki entry via the alias index.

    Case-insensitive; an alias must appear in the query as an exact phrase on
    word boundaries (regex \\b), and the longest matching alias wins. Returns
    (wiki_doc_id, matched_alias) or (None, None).
    """
    index = backend.alias_index()
    if not index:
        return (None, None)
    q = query.lower()
    for alias in sorted(index, key=lambda a: (-len(a), a)):
        if alias and re.search(rf"\b{re.escape(alias)}\b", q):
            slug, domain = index[alias]
            return (f"wiki:{domain}:{slug}", alias)
    return (None, None)


def lexical_claims(
    query: str, backend: StorageBackend, k: int, domain: str | None = None
) -> list[str]:
    """Full-text claim search, mapped to record_ids."""
    return [f"claim:{claim_id}" for claim_id in backend.lexical_claims(query, k, domain)]


def lexical_docs(
    query: str, backend: StorageBackend, k: int, domain: str | None = None
) -> list[str]:
    """Full-text document search; doc_ids pass through unchanged."""
    return list(backend.lexical_docs(query, k, domain))


def vector_channel(
    query: str,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    k: int,
    domain: str | None = None,
) -> list[str]:
    """Embed the query once and k-NN over all record types.

    Returns [] silently for un-embeddable queries (zero vector) or an index
    without embeddings.
    """
    vectors = embedder.embed([query])
    if not vectors:
        return []
    vector = vectors[0]
    if not any(vector):
        return []
    try:
        hits = backend.knn(vector, k, record_types=None, domain=domain)
    except StorageError:
        return []
    return [record_id for record_id, _similarity in hits]


def graph_channel(seed_wiki_slugs: list[str], backend: StorageBackend, k: int) -> list[str]:
    """Claims affecting any seed wiki slug, best-confidence first, as record_ids."""
    if not seed_wiki_slugs:
        return []
    return [f"claim:{claim_id}" for claim_id in backend.claims_for_wiki(seed_wiki_slugs, k)]
