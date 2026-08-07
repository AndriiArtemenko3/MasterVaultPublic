# src/mastervault/storage — Persistence behind one Protocol

This folder holds the index: documents, claims, chunks, wiki aliases, embeddings, and derived schema-v2 structural records, plus the queries that read them back. The legacy contract remains the `StorageBackend` Protocol in `base.py`; both official backends also expose the same structural persistence/search/hydration capability. Keeping that capability optional lets existing duck-typed backends retain their original four-argument document-upsert path. SQLite and Postgres implement the same logical schema-v3 structural design.

## Files

| File | Responsibility |
|------|----------------|
| `base.py` | The `StorageBackend` Protocol, row transports, storage error hierarchy, schema-version bounds/metadata validator, vector guard, and over-fetch policy. |
| `sqlite.py` | `SqliteBackend`: sqlite-vec `vec0` plus FTS5. Applies the ordered package migrations, normalizes vectors before insert, converts vec0 L2 distance to cosine, and atomically replaces a changed document with its structural projection. |
| `postgres.py` | `PostgresBackend`: psycopg3 sync + pgvector/`tsvector`. Applies the matching ordered package migrations, with `{{DIM}}` substitution, inside explicit transactions, and implements the matching atomic document/structural write. It was not acceptance-tested in the reported environment; that suite requires `DATABASE_URL`. |
| `migrations/{sqlite,pg}/001_init.sql` | Backend-specific schema-v1 definitions. Both are package data; `{{DIM}}` is substituted at init time. |
| `migrations/{sqlite,pg}/002_migration_ledger.sql` | Schema v2: the explicit ordered migration ledger used to prove which migrations were applied. |
| `migrations/{sqlite,pg}/003_structural_records.sql` | Storage schema v3: parser-neutral structural rows and lexical indexes; ParsedDocument remains schema-v2. |
| `__init__.py` | `get_backend(settings)` resolution (`postgres` / `sqlite` / `auto`) plus public re-exports of the rows, Protocol, and error types. |

## How it fits

Ingestion ([../ingest](../ingest)) builds the row dataclasses from parsed Markdown; sync calls the legacy document/embedding methods and, on official backends, the optional atomic document-plus-structural replacement. Embedding vectors come from [../embeddings](../embeddings). On the read side, retrieval ([../retrieval](../retrieval)) fans out across `knn`, `lexical_claims`, `lexical_docs`, `claims_for_wiki`, `alias_index`, and optional `lexical_structural`, then hydrates ordinary and structural results through the corresponding getters. The CLI ([../cli](../cli)) drives `init_schema`, `stats`, and `wipe`.

## Key concepts / entry points

- `StorageBackend` Protocol (`base.py`) — the preserved legacy contract both official and duck-typed backends satisfy. Structural methods are an optional capability discovered by sync/retrieval so legacy implementations continue to work.
- `get_backend()` (`__init__.py:44`) — resolves the backend from settings; `auto` returns Postgres when `DATABASE_URL` is set and connectable, else SQLite.
- Embedding idempotency rule — `needs_embedding` (`sqlite.py:339`, `postgres.py:189`) returns only the `record_id`s whose stored `(content_hash, model_version)` differs from the requested pair, so re-running ingest on unchanged content makes zero paid embedding calls.
- Ordered migrations — each backend requires a contiguous `001` through the current schema version, upgrades a representative v1 index in place, and records the exact version, name, and raw-template SHA-256 in `schema_migrations`. SQLite locks with `BEGIN IMMEDIATE`; PostgreSQL uses a transaction-scoped advisory lock before reading metadata. Missing, extra, renamed, or checksum-tampered history is corruption rejected before mutation.
- Schema identity guard — `init_schema` pins `(embedding_model, dimensions, schema_version)`. Versions 1 and 2 upgrade through migration 3; corrupt/pre-v1 and future-version metadata are refused without overwriting it, as are model/dimension changes that require an explicit rebuild.
- Atomic structural replacement — `upsert_document_with_structural` replaces one changed document's document/claim/chunk/alias rows and its structural rows inside one official-backend transaction. A structural insert failure rolls everything back. For unchanged documents, `sync_vault` still replaces the deterministic structural projection so interrupted and v2-to-v3 upgrade runs converge.
- `overfetch_limit()` (`base.py:100`) — shared ANN over-fetch policy: 1x unfiltered, 4x for domain/type filters, 20x for wiki-only, so post-filtering still yields `k` survivors.
- `fts_match_expr()` (`sqlite.py:115`) and `l2_to_cosine()` (`sqlite.py:128`) — the SQLite-only helpers that make FTS5 tolerate raw punctuation/quotes and turn vec0 L2 distance into cosine similarity.
