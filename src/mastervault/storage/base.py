"""Storage layer contract: shared row dataclasses + the StorageBackend Protocol.

Both backends (Postgres+pgvector, SQLite+sqlite-vec+FTS5) implement the same
logical schema (see migrations/pg/001_init.sql) and the same Protocol below.
Row dataclasses are plain transport types — validation happens upstream in
mastervault.models; this module stays dependency-light (stdlib only).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class StorageError(RuntimeError):
    """Base error for the storage layer."""


class SchemaMismatchError(StorageError):
    """Existing index was built with a different embedding dim / model.

    Raised by init_schema() instead of silently corrupting the index. The fix
    is an explicit re-index (wipe + re-embed), never an implicit one.
    """


# ---------------------------------------------------------------------------
# Row dataclasses (mirror the logical schema)
# ---------------------------------------------------------------------------


@dataclass
class DocumentRow:
    doc_id: str
    doc_type: str  # source | wiki | decision | strategy
    domain: str
    rel_path: str
    title: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    content_hash: str = ""


@dataclass
class ClaimRow:
    claim_id: str
    doc_id: str
    ordinal: int
    statement: str
    confidence: str  # low | medium | high
    content_hash: str
    affects: list[str] = field(default_factory=list)  # wiki slugs -> claim_affects
    # Hydration-only fields: populated by get_claims() via join; ignored on write.
    rel_path: str | None = None
    domain: str | None = None


@dataclass
class ChunkRow:
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    content_hash: str
    # Hydration-only fields: populated by get_chunks() via join; ignored on write.
    rel_path: str | None = None
    domain: str | None = None


@dataclass
class AliasRow:
    alias: str
    wiki_slug: str
    domain: str


@dataclass
class EmbeddingRow:
    record_id: str  # "claim:<claim-id>" | "wiki:<domain>:<slug>" | "chunk:<doc-id>#<n>"
    record_type: str  # claim | wiki | chunk
    doc_id: str | None
    domain: str | None
    content_hash: str
    model_version: str
    vector: list[float]


# ---------------------------------------------------------------------------
# Shared query policy
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

META_KEY_MODEL = "embedding_model"
META_KEY_DIM = "dimensions"
META_KEY_SCHEMA = "schema_version"


def overfetch_limit(k: int, record_types: list[str] | None, domain: str | None) -> int:
    """How many neighbours to pull from the ANN index before post-filtering.

    Unfiltered queries fetch exactly k. Filtered queries over-fetch 4x so the
    post-filter still yields k survivors; wiki-only filters over-fetch 20x
    because wiki records are a small share of the corpus.
    """
    if record_types is None and domain is None:
        return k
    if record_types is not None and set(record_types) == {"wiki"}:
        return k * 20
    return k * 4


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageBackend(Protocol):
    """One index backend. All methods are synchronous; one connection per instance."""

    def init_schema(self, dim: int, model_version: str) -> None:
        """Create or validate the schema.

        Records (embedding_model, dimensions, schema_version) in the meta
        table on first run; raises SchemaMismatchError if an existing index
        was built with a different dim or embedding model. Idempotent when
        dim/model match.
        """
        ...

    def upsert_document(
        self,
        doc: DocumentRow,
        claims: list[ClaimRow],
        chunks: list[ChunkRow],
        aliases: list[AliasRow],
    ) -> None:
        """Transactional replace: upsert the document row, then delete+reinsert
        its claims (and claim_affects), chunks, and aliases."""
        ...

    def delete_documents_not_in(self, rel_paths: set[str]) -> list[str]:
        """Cascade-remove documents whose rel_path is absent from the given set.

        Returns the removed doc_ids.
        """
        ...

    def needs_embedding(
        self, items: list[tuple[str, str]], model_version: str
    ) -> list[str]:
        """Idempotency gate for paid embedding calls.

        items are (record_id, content_hash) pairs; returns only the record_ids
        whose stored (content_hash, model_version) is absent or stale, in
        input order.
        """
        ...

    def upsert_embeddings(self, rows: list[EmbeddingRow]) -> None: ...

    def knn(
        self,
        vector: Sequence[float],
        k: int,
        record_types: list[str] | None = None,
        domain: str | None = None,
    ) -> list[tuple[str, float]]:
        """Cosine k-NN. Returns (record_id, similarity) sorted descending."""
        ...

    def lexical_claims(self, query: str, k: int, domain: str | None = None) -> list[str]:
        """Full-text search over claim statements; returns claim_ids by rank.
        Must tolerate punctuation/quotes in the query without raising."""
        ...

    def lexical_docs(self, query: str, k: int, domain: str | None = None) -> list[str]:
        """Full-text search over document title+body; returns doc_ids by rank.
        Must tolerate punctuation/quotes in the query without raising."""
        ...

    def claims_for_wiki(self, wiki_slugs: list[str], k: int) -> list[str]:
        """claim_ids affecting any of the given wiki slugs, ordered by
        confidence (high > medium > low) then claim_id."""
        ...

    def alias_index(self) -> dict[str, tuple[str, str]]:
        """{alias_lower: (wiki_slug, domain)} across the whole index."""
        ...

    def get_documents(self, doc_ids: list[str]) -> list[DocumentRow]: ...

    def get_claims(self, claim_ids: list[str]) -> list[ClaimRow]: ...

    def get_chunks(self, chunk_ids: list[str]) -> list[ChunkRow]: ...

    def stats(self) -> dict[str, Any]: ...

    def wipe(self) -> None:
        """Truncate all index tables (documents/claims/claim_affects/
        wiki_aliases/chunks/embeddings). Keeps the meta identity rows."""
        ...

    def close(self) -> None: ...
