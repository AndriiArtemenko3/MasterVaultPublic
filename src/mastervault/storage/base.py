"""Storage layer contract: shared row dataclasses + the StorageBackend Protocol.

Both backends (Postgres+pgvector, SQLite+sqlite-vec+FTS5) implement the same
logical schema (see storage/migrations/pg/001_init.sql) and the same Protocol
below.
Row dataclasses are plain transport types — validation happens upstream in
mastervault.models. The only non-stdlib import is numpy, for the shared vector
guard: that check has to run in the same float32 precision the vector indexes
use, or the two backends disagree about which vectors are indexable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np


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
    """Write transport for a claim. Carries only what the claims table stores."""

    claim_id: str
    doc_id: str
    ordinal: int
    statement: str
    confidence: str  # low | medium | high
    content_hash: str
    affects: list[str] = field(default_factory=list)  # wiki slugs -> claim_affects


@dataclass
class HydratedClaimRow(ClaimRow):
    """A claim joined with its document.

    Read transport returned by get_claims(): rel_path/domain come from the
    documents join and are therefore always populated, unlike on the write
    transport where they do not exist at all. Keeping the two shapes distinct
    means retrieval never has to defensively narrow a value the query already
    guarantees.
    """

    rel_path: str = ""
    domain: str = ""


@dataclass
class ChunkRow:
    """Write transport for a chunk. Carries only what the chunks table stores."""

    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    content_hash: str


@dataclass
class HydratedChunkRow(ChunkRow):
    """A chunk joined with its document; see HydratedClaimRow."""

    rel_path: str = ""
    domain: str = ""


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


def normalized_vector(vector: Sequence[float]) -> np.ndarray:
    """float32 unit vector, or StorageError if the input is not indexable.

    Both backends index by cosine, which needs a direction. Three inputs have
    none, and all three must be refused identically or the backends diverge:

    - the zero vector;
    - a vector whose float32 sum-of-squares underflows to zero (every component
      around 1e-23 or smaller). This is the dangerous one. Testing `any(vector)`
      is not enough: such a vector is non-zero in Python but zero-norm in
      float32, so SQLite rejects it while pgvector accepts it, clamps
      `dot / 0 = inf` to a similarity of 1.0, and turns the row into the top hit
      for *every* query. That is silent corpus-wide corruption, and the demo
      sidecar is a route for it;
    - NaN or infinity, which propagate through every distance computation.

    Refusing all three in one place is what keeps the contract identical: loud
    failure, never silent data loss. Uses numpy (already a hard dependency of
    both backends) precisely because the check has to be done in the same
    precision the index uses.
    """
    arr = np.asarray(vector, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0:
        raise StorageError(f"expected a 1-D non-empty vector, got shape {arr.shape}")
    if not bool(np.all(np.isfinite(arr))):
        raise StorageError("cannot index or query a vector containing NaN or infinity")
    norm = float(np.linalg.norm(arr))
    if not math.isfinite(norm) or norm == 0.0:
        raise StorageError(
            "cannot index or query a zero vector (its float32 norm is zero, so it has"
            " no direction for cosine similarity)"
        )
    return arr / norm


def ensure_indexable_vector(vector: Sequence[float]) -> None:
    """Raise StorageError unless `vector` can be cosine-indexed. See normalized_vector."""
    normalized_vector(vector)


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

    @property
    def name(self) -> str:
        """Stable backend identifier: "sqlite" | "postgres".

        Lets application layers report which backend they acted on without
        importing a concrete backend class or reaching for its driver handle.
        """
        ...

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

    def get_claims(self, claim_ids: list[str]) -> list[HydratedClaimRow]:
        """Claims joined with their documents, in input order; unknown ids dropped."""
        ...

    def get_chunks(self, chunk_ids: list[str]) -> list[HydratedChunkRow]:
        """Chunks joined with their documents, in input order; unknown ids dropped."""
        ...

    def stats(self) -> dict[str, Any]: ...

    def wipe(self) -> None:
        """Truncate all index tables (documents/claims/claim_affects/
        wiki_aliases/chunks/embeddings). Keeps the meta identity rows."""
        ...

    def drop_schema(self) -> None:
        """Remove every schema object this backend owns, meta included.

        The destructive counterpart to wipe(): wipe() empties the content but
        keeps the index identity, drop_schema() takes the schema itself away so
        a later init_schema() rebuilds it from nothing (and may pick a new
        embedding model/dim without tripping SchemaMismatchError). Idempotent.
        """
        ...

    def close(self) -> None: ...


@runtime_checkable
class FileBackedBackend(StorageBackend, Protocol):
    """Capability: the whole index lives in one local file.

    Lets `mvault drop` / `mvault demo delete` remove a file-backed index by
    deleting its file rather than dropping tables, without the application
    layer importing a concrete backend class or touching its driver handle.
    """

    @property
    def db_path(self) -> Path: ...
