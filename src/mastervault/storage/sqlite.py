"""SQLite backend: sqlite-vec (vec0) for vectors, FTS5 for lexical search.

Mirrors the logical schema of migrations/pg/001_init.sql. Three extras that
Postgres gets for free are handled by hand here:

- FTS5 virtual tables (claims_fts, documents_fts) are rebuilt for the touched
  document inside every upsert/delete transaction.
- vec0 rows cannot be updated in place; embedding upserts delete+reinsert.
- Vectors are L2-normalized before insert, and queries convert vec0's L2
  distance to cosine similarity via cos = 1 - l2^2 / 2 (exact for unit vectors).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec

from mastervault.storage.base import (
    META_KEY_DIM,
    META_KEY_MODEL,
    META_KEY_SCHEMA,
    SCHEMA_VERSION,
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    SchemaMismatchError,
    StorageError,
    overfetch_limit,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_CONFIDENCE_ORDER_SQL = "CASE c.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id       TEXT PRIMARY KEY,
  doc_type     TEXT NOT NULL CHECK (doc_type IN ('source','wiki','decision','strategy')),
  domain       TEXT NOT NULL,
  rel_path     TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  frontmatter  TEXT NOT NULL,
  body         TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents (domain, doc_type);

CREATE TABLE IF NOT EXISTS claims (
  claim_id     TEXT PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  statement    TEXT NOT NULL,
  confidence   TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims (doc_id);

CREATE TABLE IF NOT EXISTS claim_affects (
  claim_id  TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  wiki_slug TEXT NOT NULL,
  PRIMARY KEY (claim_id, wiki_slug)
);
CREATE INDEX IF NOT EXISTS idx_affects_slug ON claim_affects (wiki_slug);

CREATE TABLE IF NOT EXISTS wiki_aliases (
  alias     TEXT NOT NULL,
  wiki_slug TEXT NOT NULL,
  domain    TEXT NOT NULL,
  doc_id    TEXT REFERENCES documents(doc_id) ON DELETE CASCADE,
  PRIMARY KEY (alias, wiki_slug)
);
CREATE INDEX IF NOT EXISTS idx_aliases_doc ON wiki_aliases (doc_id);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     TEXT PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);

CREATE TABLE IF NOT EXISTS embeddings (
  record_id     TEXT PRIMARY KEY,
  record_type   TEXT NOT NULL CHECK (record_type IN ('claim','wiki','chunk')),
  doc_id        TEXT REFERENCES documents(doc_id) ON DELETE CASCADE,
  domain        TEXT,
  content_hash  TEXT NOT NULL,
  model_version TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_type ON embeddings (record_type, domain);

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim_id UNINDEXED, statement);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(doc_id UNINDEXED, title, body);
"""


def fts_match_expr(query: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Extracts word tokens and quotes each one (implicit AND), so punctuation,
    quotes, and FTS5 operators in the raw query can never raise a syntax
    error. Returns None when the query has no indexable tokens.
    """
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return None
    return " ".join(f'"{t}"' for t in tokens)


def l2_to_cosine(distance: float) -> float:
    """Cosine similarity from L2 distance between unit vectors."""
    return 1.0 - (distance * distance) / 2.0


def _normalize(vector: Sequence[float]) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        raise StorageError("cannot index or query a zero vector")
    return arr / norm


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


class SqliteBackend:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self.conn.execute("PRAGMA foreign_keys = ON")

    # -- schema -------------------------------------------------------------

    def _read_meta(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if row is None:
            return None
        rows = self.conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def init_schema(self, dim: int, model_version: str) -> None:
        meta = self._read_meta()
        if meta and (META_KEY_DIM in meta or META_KEY_MODEL in meta):
            stored_dim = meta.get(META_KEY_DIM)
            stored_model = meta.get(META_KEY_MODEL)
            if stored_dim != dim or stored_model != model_version:
                raise SchemaMismatchError(
                    f"index was built with model={stored_model!r} dim={stored_dim}, "
                    f"requested model={model_version!r} dim={dim}. "
                    "Re-index explicitly (wipe + re-embed) before changing either."
                )
        with self.conn:
            self.conn.executescript(_SCHEMA_SQL)
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_records USING vec0("
                f"record_id TEXT PRIMARY KEY, embedding float[{dim}])"
            )
            for key, value in (
                (META_KEY_MODEL, model_version),
                (META_KEY_DIM, dim),
                (META_KEY_SCHEMA, SCHEMA_VERSION),
            ):
                self.conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

    # -- documents ------------------------------------------------------------

    def upsert_document(
        self,
        doc: DocumentRow,
        claims: list[ClaimRow],
        chunks: list[ChunkRow],
        aliases: list[AliasRow],
    ) -> None:
        with self.conn:
            old_claim_ids = [
                r["claim_id"]
                for r in self.conn.execute(
                    "SELECT claim_id FROM claims WHERE doc_id = ?", (doc.doc_id,)
                )
            ]
            self._delete_claims_fts(old_claim_ids)
            self.conn.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc.doc_id,))
            self.conn.execute(
                """
                INSERT INTO documents
                    (doc_id, doc_type, domain, rel_path, title, frontmatter, body,
                     content_hash, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (doc_id) DO UPDATE SET
                    doc_type = excluded.doc_type,
                    domain = excluded.domain,
                    rel_path = excluded.rel_path,
                    title = excluded.title,
                    frontmatter = excluded.frontmatter,
                    body = excluded.body,
                    content_hash = excluded.content_hash,
                    indexed_at = excluded.indexed_at
                """,
                (
                    doc.doc_id,
                    doc.doc_type,
                    doc.domain,
                    doc.rel_path,
                    doc.title,
                    json.dumps(doc.frontmatter),
                    doc.body,
                    doc.content_hash,
                    _now(),
                ),
            )
            self.conn.execute("DELETE FROM claims WHERE doc_id = ?", (doc.doc_id,))
            self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc.doc_id,))
            self.conn.execute("DELETE FROM wiki_aliases WHERE doc_id = ?", (doc.doc_id,))
            if claims:
                self.conn.executemany(
                    "INSERT INTO claims (claim_id, doc_id, ordinal, statement, confidence,"
                    " content_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (c.claim_id, doc.doc_id, c.ordinal, c.statement, c.confidence,
                         c.content_hash)
                        for c in claims
                    ],
                )
                affect_rows = [
                    (c.claim_id, slug) for c in claims for slug in dict.fromkeys(c.affects)
                ]
                if affect_rows:
                    self.conn.executemany(
                        "INSERT INTO claim_affects (claim_id, wiki_slug) VALUES (?, ?)",
                        affect_rows,
                    )
                self.conn.executemany(
                    "INSERT INTO claims_fts (claim_id, statement) VALUES (?, ?)",
                    [(c.claim_id, c.statement) for c in claims],
                )
            if chunks:
                self.conn.executemany(
                    "INSERT INTO chunks (chunk_id, doc_id, ordinal, text, content_hash)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [(ch.chunk_id, doc.doc_id, ch.ordinal, ch.text, ch.content_hash)
                     for ch in chunks],
                )
            if aliases:
                self.conn.executemany(
                    "INSERT INTO wiki_aliases (alias, wiki_slug, domain, doc_id)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (alias, wiki_slug) DO UPDATE SET"
                    " domain = excluded.domain, doc_id = excluded.doc_id",
                    [(a.alias, a.wiki_slug, a.domain, doc.doc_id) for a in aliases],
                )
            self.conn.execute(
                "INSERT INTO documents_fts (doc_id, title, body) VALUES (?, ?, ?)",
                (doc.doc_id, doc.title, doc.body),
            )

    def _delete_claims_fts(self, claim_ids: list[str]) -> None:
        if claim_ids:
            self.conn.execute(
                f"DELETE FROM claims_fts WHERE claim_id IN ({_placeholders(len(claim_ids))})",
                claim_ids,
            )

    def delete_documents_not_in(self, rel_paths: set[str]) -> list[str]:
        with self.conn:
            if rel_paths:
                paths = sorted(rel_paths)
                doc_ids = [
                    r["doc_id"]
                    for r in self.conn.execute(
                        "SELECT doc_id FROM documents WHERE rel_path NOT IN"
                        f" ({_placeholders(len(paths))})",
                        paths,
                    )
                ]
            else:
                doc_ids = [r["doc_id"] for r in self.conn.execute("SELECT doc_id FROM documents")]
            if not doc_ids:
                return []
            ph = _placeholders(len(doc_ids))
            claim_ids = [
                r["claim_id"]
                for r in self.conn.execute(
                    f"SELECT claim_id FROM claims WHERE doc_id IN ({ph})", doc_ids
                )
            ]
            emb_ids = [
                r["record_id"]
                for r in self.conn.execute(
                    f"SELECT record_id FROM embeddings WHERE doc_id IN ({ph})", doc_ids
                )
            ]
            self._delete_claims_fts(claim_ids)
            self.conn.execute(f"DELETE FROM documents_fts WHERE doc_id IN ({ph})", doc_ids)
            if emb_ids:
                self.conn.execute(
                    f"DELETE FROM vec_records WHERE record_id IN ({_placeholders(len(emb_ids))})",
                    emb_ids,
                )
            # FK cascade removes claims, claim_affects, chunks, aliases, embeddings.
            self.conn.execute(f"DELETE FROM documents WHERE doc_id IN ({ph})", doc_ids)
            return doc_ids

    # -- embeddings -----------------------------------------------------------

    def needs_embedding(self, items: list[tuple[str, str]], model_version: str) -> list[str]:
        if not items:
            return []
        ids = [rid for rid, _ in items]
        rows = self.conn.execute(
            "SELECT record_id, content_hash, model_version FROM embeddings"
            f" WHERE record_id IN ({_placeholders(len(ids))})",
            ids,
        ).fetchall()
        stored = {r["record_id"]: (r["content_hash"], r["model_version"]) for r in rows}
        return [rid for rid, h in items if stored.get(rid) != (h, model_version)]

    def upsert_embeddings(self, rows: list[EmbeddingRow]) -> None:
        if not rows:
            return
        now = _now()
        with self.conn:
            for r in rows:
                blob = sqlite_vec.serialize_float32(_normalize(r.vector).tolist())
                self.conn.execute(
                    """
                    INSERT INTO embeddings
                        (record_id, record_type, doc_id, domain, content_hash,
                         model_version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (record_id) DO UPDATE SET
                        record_type = excluded.record_type,
                        doc_id = excluded.doc_id,
                        domain = excluded.domain,
                        content_hash = excluded.content_hash,
                        model_version = excluded.model_version,
                        updated_at = excluded.updated_at
                    """,
                    (r.record_id, r.record_type, r.doc_id, r.domain, r.content_hash,
                     r.model_version, now),
                )
                # vec0 rows cannot be updated in place: delete + reinsert.
                self.conn.execute(
                    "DELETE FROM vec_records WHERE record_id = ?", (r.record_id,)
                )
                self.conn.execute(
                    "INSERT INTO vec_records (record_id, embedding) VALUES (?, ?)",
                    (r.record_id, blob),
                )

    def knn(
        self,
        vector: Sequence[float],
        k: int,
        record_types: list[str] | None = None,
        domain: str | None = None,
    ) -> list[tuple[str, float]]:
        blob = sqlite_vec.serialize_float32(_normalize(vector).tolist())
        n_fetch = overfetch_limit(k, record_types, domain)
        rows = self.conn.execute(
            "SELECT v.record_id AS record_id, v.distance AS distance,"
            " e.record_type AS record_type, e.domain AS domain"
            " FROM vec_records v JOIN embeddings e ON e.record_id = v.record_id"
            " WHERE v.embedding MATCH ? AND k = ?"
            " ORDER BY v.distance",
            (blob, n_fetch),
        ).fetchall()
        hits: list[tuple[str, float]] = []
        for row in rows:
            if record_types is not None and row["record_type"] not in record_types:
                continue
            if domain is not None and row["domain"] != domain:
                continue
            hits.append((row["record_id"], l2_to_cosine(float(row["distance"]))))
            if len(hits) >= k:
                break
        return hits

    # -- lexical ----------------------------------------------------------------

    def lexical_claims(self, query: str, k: int, domain: str | None = None) -> list[str]:
        match = fts_match_expr(query)
        if match is None:
            return []
        sql = (
            "SELECT f.claim_id AS claim_id"
            " FROM claims_fts f"
            " JOIN claims c ON c.claim_id = f.claim_id"
            " JOIN documents d ON d.doc_id = c.doc_id"
            " WHERE claims_fts MATCH ?"
        )
        params: list[Any] = [match]
        if domain is not None:
            sql += " AND d.domain = ?"
            params.append(domain)
        sql += " ORDER BY bm25(claims_fts), f.claim_id LIMIT ?"
        params.append(k)
        return [r["claim_id"] for r in self.conn.execute(sql, params)]

    def lexical_docs(self, query: str, k: int, domain: str | None = None) -> list[str]:
        match = fts_match_expr(query)
        if match is None:
            return []
        sql = (
            "SELECT f.doc_id AS doc_id"
            " FROM documents_fts f"
            " JOIN documents d ON d.doc_id = f.doc_id"
            " WHERE documents_fts MATCH ?"
        )
        params: list[Any] = [match]
        if domain is not None:
            sql += " AND d.domain = ?"
            params.append(domain)
        sql += " ORDER BY bm25(documents_fts), f.doc_id LIMIT ?"
        params.append(k)
        return [r["doc_id"] for r in self.conn.execute(sql, params)]

    # -- graph / hydration --------------------------------------------------------

    def claims_for_wiki(self, wiki_slugs: list[str], k: int) -> list[str]:
        if not wiki_slugs:
            return []
        rows = self.conn.execute(
            "SELECT c.claim_id AS claim_id"
            " FROM claims c JOIN claim_affects a ON a.claim_id = c.claim_id"
            f" WHERE a.wiki_slug IN ({_placeholders(len(wiki_slugs))})"
            " GROUP BY c.claim_id"
            f" ORDER BY MIN({_CONFIDENCE_ORDER_SQL}), c.claim_id LIMIT ?",
            [*wiki_slugs, k],
        ).fetchall()
        return [r["claim_id"] for r in rows]

    def alias_index(self) -> dict[str, tuple[str, str]]:
        rows = self.conn.execute("SELECT alias, wiki_slug, domain FROM wiki_aliases").fetchall()
        return {r["alias"].lower(): (r["wiki_slug"], r["domain"]) for r in rows}

    def get_documents(self, doc_ids: list[str]) -> list[DocumentRow]:
        if not doc_ids:
            return []
        rows = self.conn.execute(
            "SELECT doc_id, doc_type, domain, rel_path, title, frontmatter, body,"
            f" content_hash FROM documents WHERE doc_id IN ({_placeholders(len(doc_ids))})",
            doc_ids,
        ).fetchall()
        by_id = {
            r["doc_id"]: DocumentRow(
                doc_id=r["doc_id"], doc_type=r["doc_type"], domain=r["domain"],
                rel_path=r["rel_path"], title=r["title"],
                frontmatter=json.loads(r["frontmatter"]), body=r["body"],
                content_hash=r["content_hash"],
            )
            for r in rows
        }
        return [by_id[d] for d in doc_ids if d in by_id]

    def get_claims(self, claim_ids: list[str]) -> list[ClaimRow]:
        if not claim_ids:
            return []
        ph = _placeholders(len(claim_ids))
        rows = self.conn.execute(
            "SELECT c.claim_id AS claim_id, c.doc_id AS doc_id, c.ordinal AS ordinal,"
            " c.statement AS statement, c.confidence AS confidence,"
            " c.content_hash AS content_hash, d.rel_path AS rel_path, d.domain AS domain"
            " FROM claims c JOIN documents d ON d.doc_id = c.doc_id"
            f" WHERE c.claim_id IN ({ph})",
            claim_ids,
        ).fetchall()
        affects: dict[str, list[str]] = {}
        for r in self.conn.execute(
            f"SELECT claim_id, wiki_slug FROM claim_affects WHERE claim_id IN ({ph})"
            " ORDER BY wiki_slug",
            claim_ids,
        ):
            affects.setdefault(r["claim_id"], []).append(r["wiki_slug"])
        by_id = {
            r["claim_id"]: ClaimRow(
                claim_id=r["claim_id"], doc_id=r["doc_id"], ordinal=r["ordinal"],
                statement=r["statement"], confidence=r["confidence"],
                content_hash=r["content_hash"], affects=affects.get(r["claim_id"], []),
                rel_path=r["rel_path"], domain=r["domain"],
            )
            for r in rows
        }
        return [by_id[c] for c in claim_ids if c in by_id]

    def get_chunks(self, chunk_ids: list[str]) -> list[ChunkRow]:
        if not chunk_ids:
            return []
        rows = self.conn.execute(
            "SELECT ch.chunk_id AS chunk_id, ch.doc_id AS doc_id, ch.ordinal AS ordinal,"
            " ch.text AS text, ch.content_hash AS content_hash,"
            " d.rel_path AS rel_path, d.domain AS domain"
            " FROM chunks ch JOIN documents d ON d.doc_id = ch.doc_id"
            f" WHERE ch.chunk_id IN ({_placeholders(len(chunk_ids))})",
            chunk_ids,
        ).fetchall()
        by_id = {
            r["chunk_id"]: ChunkRow(
                chunk_id=r["chunk_id"], doc_id=r["doc_id"], ordinal=r["ordinal"],
                text=r["text"], content_hash=r["content_hash"],
                rel_path=r["rel_path"], domain=r["domain"],
            )
            for r in rows
        }
        return [by_id[c] for c in chunk_ids if c in by_id]

    # -- maintenance ---------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        meta = self._read_meta()
        if meta is None:
            # No meta table means the schema was never created; querying the
            # content tables would raise "no such table". Report it cleanly.
            return {"backend": "sqlite", "initialized": False, "db_path": str(self.db_path)}
        counts = {
            table: self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("documents", "claims", "claim_affects", "wiki_aliases",
                          "chunks", "embeddings")
        }
        return {
            "backend": "sqlite",
            "initialized": True,
            "counts": counts,
            "embedding_model": meta.get(META_KEY_MODEL),
            "dimensions": meta.get(META_KEY_DIM),
            "schema_version": meta.get(META_KEY_SCHEMA),
            "db_path": str(self.db_path),
        }

    def wipe(self) -> None:
        with self.conn:
            for table in ("claim_affects", "claims", "chunks", "wiki_aliases",
                          "embeddings", "documents", "vec_records", "claims_fts",
                          "documents_fts"):
                self.conn.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        self.conn.close()
