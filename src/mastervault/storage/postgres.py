"""Postgres + pgvector backend.

psycopg3 sync, one connection per backend instance. The connection runs in
autocommit mode; every write path opens an explicit transaction block, so a
failure mid-upsert never leaves a half-replaced document behind.

Schema comes from storage/migrations/pg/*.sql (package data, so a wheel and a
source checkout behave alike) with {{DIM}} substituted at init time.
The meta table pins (embedding_model, dimensions, schema_version); a re-init
with a different dim or model refuses instead of corrupting the index.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

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
    HydratedChunkRow,
    HydratedClaimRow,
    SchemaMismatchError,
    StorageError,
    ensure_indexable_vector,
    overfetch_limit,
    validate_schema_meta,
)

# Package-relative, like the prompt files: an installed wheel and an editable
# checkout must resolve the schema identically. Resolving it from the repo root
# meant `mvault init` against Postgres worked from a clone and failed from a
# wheel with "no migrations found".
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations" / "pg"
_MIGRATION_FILE_RE = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")
_SCHEMA_MIGRATION_LOCK_ID = 0x4D_56_4C_54  # "MVLT", transaction-scoped

_CONFIDENCE_ORDER_SQL = "CASE c.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"

_INDEX_TABLES = (
    "schema_migrations",
    "meta",
    "documents",
    "claims",
    "claim_affects",
    "wiki_aliases",
    "chunks",
    "embeddings",
)


class PostgresBackend:
    name = "postgres"

    def __init__(
        self,
        url: str,
        migrations_dir: Path | None = None,
        connect_timeout: int = 10,
    ) -> None:
        self.migrations_dir = migrations_dir or _DEFAULT_MIGRATIONS_DIR
        self.conn = psycopg.connect(url, connect_timeout=connect_timeout)
        self.conn.autocommit = True
        self._vector_registered = False
        self._maybe_register_vector()

    # -- schema -------------------------------------------------------------

    def _maybe_register_vector(self) -> None:
        """Register the pgvector adapter once the vector type exists."""
        if self._vector_registered:
            return
        if self.conn.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'").fetchone():
            register_vector(self.conn)
            self._vector_registered = True

    def _scalar(self, sql: str) -> Any:
        """First column of the first row of a query that returns exactly one row."""
        row = self.conn.execute(sql).fetchone()
        if row is None:  # pragma: no cover — the callers below always yield a row
            raise StorageError(f"expected exactly one row from: {sql}")
        return row[0]

    def _read_meta(self) -> dict[str, Any] | None:
        """meta rows as a dict, or None when the meta table does not exist yet."""
        if self._scalar("SELECT to_regclass('meta')") is None:
            return None
        rows = self.conn.execute("SELECT key, value FROM meta").fetchall()
        return dict(rows)

    def _refuse_unidentified_schema(self) -> None:
        existing = [
            table
            for table in _INDEX_TABLES
            if table != "meta" and self._scalar(f"SELECT to_regclass('{table}')") is not None
        ]
        if existing:
            raise SchemaMismatchError(
                "index tables exist without a meta table; refusing to adopt an "
                f"unidentified schema ({existing}). Rebuild the index explicitly."
            )

    def _migration_files(self) -> dict[int, Path]:
        files: dict[int, Path] = {}
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILE_RE.fullmatch(path.name)
            if match is None:
                raise StorageError(f"invalid PostgreSQL migration filename: {path.name}")
            version = int(match.group("version"))
            if version in files:
                raise StorageError(f"duplicate PostgreSQL migration version {version:03d}")
            files[version] = path
        expected = list(range(1, SCHEMA_VERSION + 1))
        if sorted(files) != expected:
            raise StorageError(
                f"PostgreSQL migrations must be contiguous {expected}, found {sorted(files)} "
                f"in {self.migrations_dir}"
            )
        return files

    @staticmethod
    def _migration_checksum(path: Path) -> str:
        """SHA-256 of the unrendered migration template bytes."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _validate_migration_ledger(self, migrations: dict[int, Path], version: int) -> None:
        exists = self._scalar("SELECT to_regclass('schema_migrations')") is not None
        if version < 2:
            if exists:
                raise SchemaMismatchError(
                    f"schema_version={version} has an unexpected schema_migrations table; "
                    "refusing to mutate conflicting migration history"
                )
            return
        if not exists:
            raise SchemaMismatchError(
                "schema_version is current but schema_migrations is missing; "
                "refusing to infer migration history"
            )
        rows = self.conn.execute(
            "SELECT version, name, checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        actual = [(int(row[0]), str(row[1]), str(row[2])) for row in rows]
        expected = [
            (item_version, migrations[item_version].stem, self._migration_checksum(migrations[item_version]))
            for item_version in range(1, version + 1)
        ]
        if actual != expected:
            raise SchemaMismatchError(
                "schema_migrations does not exactly match the packaged migration history: "
                f"expected {expected}, found {actual}"
            )

    def init_schema(self, dim: int, model_version: str) -> None:
        migrations = self._migration_files()
        with self.conn.transaction():
            # Serialize version selection and execution, then read/recheck
            # metadata only after the transaction-scoped lock is held.
            self.conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_MIGRATION_LOCK_ID,))
            meta = self._read_meta()
            if meta is None:
                self._refuse_unidentified_schema()
            version = validate_schema_meta(meta, dim=dim, model_version=model_version)
            if version:
                self._validate_migration_ledger(migrations, version)

            for next_version in range(version + 1, SCHEMA_VERSION + 1):
                path = migrations[next_version]
                sql = path.read_bytes().decode("utf-8").replace("{{DIM}}", str(dim))
                self.conn.execute(sql)
                if next_version == 1:
                    for key, value in (
                        (META_KEY_MODEL, model_version),
                        (META_KEY_DIM, dim),
                        (META_KEY_SCHEMA, 1),
                    ):
                        self.conn.execute(
                            "INSERT INTO meta (key, value) VALUES (%s, %s)",
                            (key, Jsonb(value)),
                        )
                else:
                    cursor = self.conn.execute(
                        "UPDATE meta SET value = %s WHERE key = %s",
                        (Jsonb(next_version), META_KEY_SCHEMA),
                    )
                    if cursor.rowcount != 1:
                        raise SchemaMismatchError(
                            "schema_version metadata disappeared during migration"
                        )
                    if next_version == 2:
                        for history_version in range(1, next_version + 1):
                            history_path = migrations[history_version]
                            self.conn.execute(
                                "INSERT INTO schema_migrations "
                                "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                                (
                                    history_version,
                                    history_path.stem,
                                    self._migration_checksum(history_path),
                                ),
                            )
                    elif next_version > 2:
                        self.conn.execute(
                            "INSERT INTO schema_migrations "
                            "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                            (next_version, path.stem, self._migration_checksum(path)),
                        )
            if SCHEMA_VERSION >= 2:
                self._validate_migration_ledger(migrations, SCHEMA_VERSION)
        self._maybe_register_vector()

    # -- documents ------------------------------------------------------------

    def upsert_document(
        self,
        doc: DocumentRow,
        claims: list[ClaimRow],
        chunks: list[ChunkRow],
        aliases: list[AliasRow],
    ) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents
                    (doc_id, doc_type, domain, rel_path, title, frontmatter, body,
                     content_hash, indexed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (doc_id) DO UPDATE SET
                    doc_type = EXCLUDED.doc_type,
                    domain = EXCLUDED.domain,
                    rel_path = EXCLUDED.rel_path,
                    title = EXCLUDED.title,
                    frontmatter = EXCLUDED.frontmatter,
                    body = EXCLUDED.body,
                    content_hash = EXCLUDED.content_hash,
                    indexed_at = now()
                """,
                (
                    doc.doc_id,
                    doc.doc_type,
                    doc.domain,
                    doc.rel_path,
                    doc.title,
                    Jsonb(doc.frontmatter),
                    doc.body,
                    doc.content_hash,
                ),
            )
            cur.execute("DELETE FROM claims WHERE doc_id = %s", (doc.doc_id,))
            cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc.doc_id,))
            cur.execute("DELETE FROM wiki_aliases WHERE doc_id = %s", (doc.doc_id,))
            # Embeddings cascade on doc_id, so a document that *shrinks* would
            # otherwise strand its removed claims'/chunks' vectors: the doc_id
            # still exists, nothing deletes them, needs_embedding() keeps
            # calling them fresh, and they occupy k-NN slots forever while
            # hydrating to nothing. Only claim:/chunk: records are dropped -- a
            # wiki record's vector belongs to the document itself.
            cur.execute(
                "DELETE FROM embeddings WHERE doc_id = %s"
                " AND record_type IN ('claim', 'chunk')"
                " AND NOT (record_id = ANY(%s))",
                (
                    doc.doc_id,
                    [f"claim:{c.claim_id}" for c in claims] + [ch.chunk_id for ch in chunks],
                ),
            )
            if claims:
                cur.executemany(
                    "INSERT INTO claims (claim_id, doc_id, ordinal, statement, confidence,"
                    " content_hash) VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (
                            c.claim_id,
                            doc.doc_id,
                            c.ordinal,
                            c.statement,
                            c.confidence,
                            c.content_hash,
                        )
                        for c in claims
                    ],
                )
                affect_rows = [
                    (c.claim_id, slug) for c in claims for slug in dict.fromkeys(c.affects)
                ]
                if affect_rows:
                    cur.executemany(
                        "INSERT INTO claim_affects (claim_id, wiki_slug) VALUES (%s, %s)",
                        affect_rows,
                    )
            if chunks:
                cur.executemany(
                    "INSERT INTO chunks (chunk_id, doc_id, ordinal, text, content_hash)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    [
                        (ch.chunk_id, doc.doc_id, ch.ordinal, ch.text, ch.content_hash)
                        for ch in chunks
                    ],
                )
            if aliases:
                cur.executemany(
                    "INSERT INTO wiki_aliases (alias, wiki_slug, domain, doc_id)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (alias, wiki_slug) DO UPDATE SET"
                    " domain = EXCLUDED.domain, doc_id = EXCLUDED.doc_id",
                    [(a.alias, a.wiki_slug, a.domain, doc.doc_id) for a in aliases],
                )

    def delete_documents_not_in(self, rel_paths: set[str]) -> list[str]:
        with self.conn.transaction():
            if rel_paths:
                rows = self.conn.execute(
                    "DELETE FROM documents WHERE NOT (rel_path = ANY(%s)) RETURNING doc_id",
                    (sorted(rel_paths),),
                ).fetchall()
            else:
                rows = self.conn.execute("DELETE FROM documents RETURNING doc_id").fetchall()
        return [r[0] for r in rows]

    # -- embeddings -----------------------------------------------------------

    def needs_embedding(self, items: list[tuple[str, str]], model_version: str) -> list[str]:
        if not items:
            return []
        rows = self.conn.execute(
            "SELECT record_id, content_hash, model_version FROM embeddings"
            " WHERE record_id = ANY(%s)",
            ([rid for rid, _ in items],),
        ).fetchall()
        stored = {r[0]: (r[1], r[2]) for r in rows}
        return [rid for rid, h in items if stored.get(rid) != (h, model_version)]

    def upsert_embeddings(self, rows: list[EmbeddingRow]) -> None:
        if not rows:
            return
        # Validate the whole batch before writing any of it, so a bad row later
        # in the batch cannot depend on transaction rollback to stay invisible.
        for r in rows:
            ensure_indexable_vector(r.vector)
        self._maybe_register_vector()
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO embeddings
                    (record_id, record_type, doc_id, domain, content_hash,
                     model_version, embedding, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (record_id) DO UPDATE SET
                    record_type = EXCLUDED.record_type,
                    doc_id = EXCLUDED.doc_id,
                    domain = EXCLUDED.domain,
                    content_hash = EXCLUDED.content_hash,
                    model_version = EXCLUDED.model_version,
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                """,
                [
                    (
                        r.record_id,
                        r.record_type,
                        r.doc_id,
                        r.domain,
                        r.content_hash,
                        r.model_version,
                        np.asarray(r.vector, dtype=np.float32),
                    )
                    for r in rows
                ],
            )

    def knn(
        self,
        vector: Sequence[float],
        k: int,
        record_types: list[str] | None = None,
        domain: str | None = None,
    ) -> list[tuple[str, float]]:
        ensure_indexable_vector(vector)
        self._maybe_register_vector()
        vec = np.asarray(vector, dtype=np.float32)
        n_fetch = overfetch_limit(k, record_types, domain)
        rows = self.conn.execute(
            "SELECT record_id, record_type, domain, 1 - (embedding <=> %s) AS similarity"
            " FROM embeddings ORDER BY embedding <=> %s LIMIT %s",
            (vec, vec, n_fetch),
        ).fetchall()
        hits: list[tuple[str, float]] = []
        for record_id, record_type, row_domain, similarity in rows:
            if record_types is not None and record_type not in record_types:
                continue
            if domain is not None and row_domain != domain:
                continue
            hits.append((record_id, float(similarity)))
            if len(hits) >= k:
                break
        return hits

    # -- lexical ----------------------------------------------------------------

    def lexical_claims(self, query: str, k: int, domain: str | None = None) -> list[str]:
        sql = (
            "SELECT c.claim_id"
            " FROM claims c"
            " JOIN documents d ON d.doc_id = c.doc_id,"
            " websearch_to_tsquery('english', %(q)s) AS q"
            " WHERE c.search_tsv @@ q"
        )
        params: dict[str, Any] = {"q": query, "k": k}
        if domain is not None:
            sql += " AND d.domain = %(domain)s"
            params["domain"] = domain
        sql += " ORDER BY ts_rank_cd(c.search_tsv, q) DESC, c.claim_id LIMIT %(k)s"
        return [r[0] for r in self.conn.execute(sql, params).fetchall()]

    def lexical_docs(self, query: str, k: int, domain: str | None = None) -> list[str]:
        sql = (
            "SELECT d.doc_id"
            " FROM documents d,"
            " websearch_to_tsquery('english', %(q)s) AS q"
            " WHERE d.search_tsv @@ q"
        )
        params: dict[str, Any] = {"q": query, "k": k}
        if domain is not None:
            sql += " AND d.domain = %(domain)s"
            params["domain"] = domain
        sql += " ORDER BY ts_rank_cd(d.search_tsv, q) DESC, d.doc_id LIMIT %(k)s"
        return [r[0] for r in self.conn.execute(sql, params).fetchall()]

    # -- graph / hydration --------------------------------------------------------

    def claims_for_wiki(self, wiki_slugs: list[str], k: int) -> list[str]:
        if not wiki_slugs:
            return []
        rows = self.conn.execute(
            "SELECT c.claim_id"
            " FROM claims c JOIN claim_affects a ON a.claim_id = c.claim_id"
            " WHERE a.wiki_slug = ANY(%s)"
            " GROUP BY c.claim_id, c.confidence"
            f" ORDER BY {_CONFIDENCE_ORDER_SQL}, c.claim_id LIMIT %s",
            (wiki_slugs, k),
        ).fetchall()
        return [r[0] for r in rows]

    def alias_index(self) -> dict[str, tuple[str, str]]:
        rows = self.conn.execute("SELECT alias, wiki_slug, domain FROM wiki_aliases").fetchall()
        return {alias.lower(): (slug, dom) for alias, slug, dom in rows}

    def get_documents(self, doc_ids: list[str]) -> list[DocumentRow]:
        if not doc_ids:
            return []
        rows = self.conn.execute(
            "SELECT doc_id, doc_type, domain, rel_path, title, frontmatter, body,"
            " content_hash FROM documents WHERE doc_id = ANY(%s)",
            (doc_ids,),
        ).fetchall()
        by_id = {
            r[0]: DocumentRow(
                doc_id=r[0],
                doc_type=r[1],
                domain=r[2],
                rel_path=r[3],
                title=r[4],
                frontmatter=r[5],
                body=r[6],
                content_hash=r[7],
            )
            for r in rows
        }
        return [by_id[d] for d in doc_ids if d in by_id]

    def get_claims(self, claim_ids: list[str]) -> list[HydratedClaimRow]:
        if not claim_ids:
            return []
        rows = self.conn.execute(
            "SELECT c.claim_id, c.doc_id, c.ordinal, c.statement, c.confidence,"
            " c.content_hash, d.rel_path, d.domain,"
            " COALESCE(array_agg(a.wiki_slug ORDER BY a.wiki_slug)"
            "          FILTER (WHERE a.wiki_slug IS NOT NULL), '{}')"
            " FROM claims c"
            " JOIN documents d ON d.doc_id = c.doc_id"
            " LEFT JOIN claim_affects a ON a.claim_id = c.claim_id"
            " WHERE c.claim_id = ANY(%s)"
            " GROUP BY c.claim_id, d.doc_id",
            (claim_ids,),
        ).fetchall()
        by_id = {
            r[0]: HydratedClaimRow(
                claim_id=r[0],
                doc_id=r[1],
                ordinal=r[2],
                statement=r[3],
                confidence=r[4],
                content_hash=r[5],
                affects=list(r[8]),
                rel_path=r[6],
                domain=r[7],
            )
            for r in rows
        }
        return [by_id[c] for c in claim_ids if c in by_id]

    def get_chunks(self, chunk_ids: list[str]) -> list[HydratedChunkRow]:
        if not chunk_ids:
            return []
        rows = self.conn.execute(
            "SELECT ch.chunk_id, ch.doc_id, ch.ordinal, ch.text, ch.content_hash,"
            " d.rel_path, d.domain"
            " FROM chunks ch JOIN documents d ON d.doc_id = ch.doc_id"
            " WHERE ch.chunk_id = ANY(%s)",
            (chunk_ids,),
        ).fetchall()
        by_id = {
            r[0]: HydratedChunkRow(
                chunk_id=r[0],
                doc_id=r[1],
                ordinal=r[2],
                text=r[3],
                content_hash=r[4],
                rel_path=r[5],
                domain=r[6],
            )
            for r in rows
        }
        return [by_id[c] for c in chunk_ids if c in by_id]

    # -- maintenance ---------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        meta = self._read_meta()
        if meta is None:
            # No meta table means the schema was never created; querying the
            # content tables would raise UndefinedTable. Report it cleanly.
            return {"backend": "postgres", "initialized": False}
        counts = {
            table: self._scalar(f"SELECT count(*) FROM {table}")
            for table in (
                "documents",
                "claims",
                "claim_affects",
                "wiki_aliases",
                "chunks",
                "embeddings",
            )
        }
        return {
            "backend": "postgres",
            "initialized": True,
            "counts": counts,
            "embedding_model": meta.get(META_KEY_MODEL),
            "dimensions": meta.get(META_KEY_DIM),
            "schema_version": meta.get(META_KEY_SCHEMA),
        }

    def wipe(self) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "TRUNCATE documents, claims, claim_affects, wiki_aliases, chunks,"
                " embeddings CASCADE"
            )

    def drop_schema(self) -> None:
        with self.conn.transaction():
            self.conn.execute(f"DROP TABLE IF EXISTS {', '.join(_INDEX_TABLES)} CASCADE")

    def close(self) -> None:
        self.conn.close()
