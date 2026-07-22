# src/mastervault/storage — Persistence behind one Protocol

This folder holds the index: documents, claims, chunks, wiki aliases, and embeddings, plus the queries that read them back. Everything the rest of the app touches goes through the `StorageBackend` Protocol in `base.py`, so the two concrete backends (SQLite and Postgres) are swappable at runtime. Both implement the same logical schema and the same method set; callers never branch on which one they got.

## Files

| File | Responsibility |
|------|----------------|
| `base.py` | The `StorageBackend` Protocol (including `drop_schema()` and the `name` identity), the `FileBackedBackend` capability protocol, the write transports (`DocumentRow`, `ClaimRow`, `ChunkRow`, `AliasRow`, `EmbeddingRow`) and their hydrated read counterparts (`HydratedClaimRow`, `HydratedChunkRow`, where the documents join makes `rel_path`/`domain` non-optional), the `StorageError`/`SchemaMismatchError` hierarchy, meta-key constants, `ensure_indexable_vector()` and `overfetch_limit()`. Stdlib-only, no DB driver imports. |
| `sqlite.py` | `SqliteBackend`: sqlite-vec `vec0` for vectors, FTS5 virtual tables for lexical search. Creates its schema inline (`_SCHEMA_SQL`), normalizes vectors before insert, and converts vec0 L2 distance to cosine via `l2_to_cosine`. |
| `postgres.py` | `PostgresBackend`: psycopg3 sync + pgvector for vectors, `tsvector` columns for lexical search. Loads schema from `migrations/pg/*.sql` (shipped as package data under `storage/migrations/`) with `{{DIM}}` substituted at init, runs every write inside an explicit transaction. |
| `migrations/pg/001_init.sql` | The Postgres schema, shipped as package data so a wheel and a source checkout resolve it identically. `{{DIM}}` is substituted at init time. |
| `__init__.py` | `get_backend(settings)` resolution (`postgres` / `sqlite` / `auto`) plus public re-exports of the rows, Protocol, and error types. |

## How it fits

Ingestion ([../ingest](../ingest)) builds the row dataclasses from parsed Markdown and calls `upsert_document`, `needs_embedding`, and `upsert_embeddings`; embedding vectors come from [../embeddings](../embeddings). On the read side, retrieval ([../retrieval](../retrieval)) fans out across `knn`, `lexical_claims`, `lexical_docs`, `claims_for_wiki`, and `alias_index`, then hydrates results through `get_documents` / `get_claims` / `get_chunks`. The CLI ([../cli](../cli)) drives `init_schema`, `stats`, and `wipe`.

## Key concepts / entry points

- `StorageBackend` Protocol (`base.py:120`) — the single contract both backends satisfy; read this to know what persistence can do without reading either implementation.
- `get_backend()` (`__init__.py:44`) — resolves the backend from settings; `auto` returns Postgres when `DATABASE_URL` is set and connectable, else SQLite.
- Embedding idempotency rule — `needs_embedding` (`sqlite.py:339`, `postgres.py:189`) returns only the `record_id`s whose stored `(content_hash, model_version)` differs from the requested pair, so re-running ingest on unchanged content makes zero paid embedding calls.
- Schema-mismatch guard — `init_schema` (`sqlite.py:171`, `postgres.py:73`) pins `(embedding_model, dimensions, schema_version)` in the `meta` table on first run and raises `SchemaMismatchError` (`base.py:20`) if a later init requests a different dim or model, forcing an explicit wipe + re-embed instead of a silently corrupted index.
- `overfetch_limit()` (`base.py:100`) — shared ANN over-fetch policy: 1x unfiltered, 4x for domain/type filters, 20x for wiki-only, so post-filtering still yields `k` survivors.
- `fts_match_expr()` (`sqlite.py:115`) and `l2_to_cosine()` (`sqlite.py:128`) — the SQLite-only helpers that make FTS5 tolerate raw punctuation/quotes and turn vec0 L2 distance into cosine similarity.
