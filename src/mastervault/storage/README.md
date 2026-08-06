# src/mastervault/storage — Persistence behind one Protocol

This folder holds the index: documents, claims, chunks, wiki aliases, and embeddings, plus the queries that read them back. Everything the rest of the app touches goes through the `StorageBackend` Protocol in `base.py`, so the two concrete backends (SQLite and Postgres) are swappable at runtime. Both implement the same logical schema and the same method set; callers never branch on which one they got.

## Files

| File | Responsibility |
|------|----------------|
| `base.py` | The `StorageBackend` Protocol, row transports, storage error hierarchy, schema-version bounds/metadata validator, vector guard, and over-fetch policy. |
| `sqlite.py` | `SqliteBackend`: sqlite-vec `vec0` plus FTS5. Applies the ordered package migrations, normalizes vectors before insert, and converts vec0 L2 distance to cosine. |
| `postgres.py` | `PostgresBackend`: psycopg3 sync + pgvector/`tsvector`. Applies the matching ordered package migrations, with `{{DIM}}` substitution, inside explicit transactions. |
| `migrations/{sqlite,pg}/001_init.sql` | Backend-specific schema-v1 definitions. Both are package data; `{{DIM}}` is substituted at init time. |
| `migrations/{sqlite,pg}/002_migration_ledger.sql` | Schema v2: the explicit ordered migration ledger used to prove which migrations were applied. |
| `__init__.py` | `get_backend(settings)` resolution (`postgres` / `sqlite` / `auto`) plus public re-exports of the rows, Protocol, and error types. |

## How it fits

Ingestion ([../ingest](../ingest)) builds the row dataclasses from parsed Markdown and calls `upsert_document`, `needs_embedding`, and `upsert_embeddings`; embedding vectors come from [../embeddings](../embeddings). On the read side, retrieval ([../retrieval](../retrieval)) fans out across `knn`, `lexical_claims`, `lexical_docs`, `claims_for_wiki`, and `alias_index`, then hydrates results through `get_documents` / `get_claims` / `get_chunks`. The CLI ([../cli](../cli)) drives `init_schema`, `stats`, and `wipe`.

## Key concepts / entry points

- `StorageBackend` Protocol (`base.py:120`) — the single contract both backends satisfy; read this to know what persistence can do without reading either implementation.
- `get_backend()` (`__init__.py:44`) — resolves the backend from settings; `auto` returns Postgres when `DATABASE_URL` is set and connectable, else SQLite.
- Embedding idempotency rule — `needs_embedding` (`sqlite.py:339`, `postgres.py:189`) returns only the `record_id`s whose stored `(content_hash, model_version)` differs from the requested pair, so re-running ingest on unchanged content makes zero paid embedding calls.
- Ordered migrations — each backend requires a contiguous `001` through the current schema version, upgrades a representative v1 index in place, and records the exact version, name, and raw-template SHA-256 in `schema_migrations`. SQLite locks with `BEGIN IMMEDIATE`; PostgreSQL uses a transaction-scoped advisory lock before reading metadata. Missing, extra, renamed, or checksum-tampered history is corruption rejected before mutation.
- Schema identity guard — `init_schema` pins `(embedding_model, dimensions, schema_version)`. Version 1 upgrades to 2; corrupt/pre-v1 and future-version metadata are refused without overwriting it, as are model/dimension changes that require an explicit rebuild.
- `overfetch_limit()` (`base.py:100`) — shared ANN over-fetch policy: 1x unfiltered, 4x for domain/type filters, 20x for wiki-only, so post-filtering still yields `k` survivors.
- `fts_match_expr()` (`sqlite.py:115`) and `l2_to_cosine()` (`sqlite.py:128`) — the SQLite-only helpers that make FTS5 tolerate raw punctuation/quotes and turn vec0 L2 distance into cosine similarity.
