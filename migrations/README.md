# migrations — packaged SQLite and PostgreSQL schema history

> **Packaged schema.** PostgreSQL SQL moved into the package in 0.2.0; schema
> v2 adds ordered package migrations for both PostgreSQL and SQLite. The paths
> below are relative to `src/mastervault/storage/migrations/`.

This folder points to the packaged SQL migrations used by both storage
backends. The on-disk source of truth lives under
`src/mastervault/storage/migrations/{sqlite,pg}/`; SQLite and PostgreSQL now
advance through the same explicit schema versions instead of one building
inline while the other re-runs every SQL file.

## Files

| File | Responsibility |
|------|----------------|
| `{sqlite,pg}/001_init.sql` | Version 1 logical schema: metadata, documents, claims, affects, aliases, chunks, embeddings, and backend-specific full-text/vector structures. |
| `{sqlite,pg}/002_migration_ledger.sql` | Version 2 migration ledger. It records every applied version before later v0.3 work introduces evidence tables. |

## How it fits

`mvault init` discovers a contiguous, numerically ordered set of migrations,
validates the pinned embedding model/dimensions/schema version, and applies
only newer versions transactionally after serializing version selection. A schema-v1 workspace upgrades in place
to v2 with rows preserved. Pre-v1/corrupt and future versions are rejected,
and existing schema metadata is never silently overwritten. The two backends
share this behavior; PostgreSQL keeps its GIN/HNSW details and SQLite keeps its
FTS5/sqlite-vec details.

## Key concepts / entry points

- `{{DIM}}` substitution — placeholder in `pg/001_init.sql:71` replaced with the configured embedding dimension so one schema file serves both 384-d and 1536-d models.
- `schema_migrations` — ordered evidence that versions 1 and 2 were applied, binding each exact migration name and raw-template SHA-256; the version stored in `meta` is not accepted as sufficient when this ledger is missing, incomplete, extra, renamed, or checksum-tampered.
- Generated `tsvector` columns — `documents.search_tsv` (`pg/001_init.sql:22`) and `claims.search_tsv` (`pg/001_init.sql:34`) are `STORED` columns computed from title/body/statement, indexed with GIN for FTS.
- HNSW vector index — `idx_emb_hnsw` (`pg/001_init.sql:74`) on `embeddings.embedding` using `vector_cosine_ops`, the approximate-nearest-neighbor index for the vector channel.
- `embeddings.record_type` — `'claim' | 'wiki' | 'chunk'` (`pg/001_init.sql:66`) lets one embeddings table hold vectors for all three retrievable record kinds, filtered alongside `domain` via `idx_emb_type`.
- `claim_affects` and `wiki_aliases` — join and alias tables (`pg/001_init.sql:39`, `pg/001_init.sql:46`) that map claims and surface forms to wiki slugs, feeding the wiki-alias-graph retrieval channel.
- `ON DELETE CASCADE` from `documents` — claims, chunks, embeddings, and aliases all reference `documents` with cascade delete, so removing a source document clears its derived rows in one step.
