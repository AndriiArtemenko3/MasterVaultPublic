# migrations — Postgres schema DDL

This folder holds the SQL schema for the Postgres/pgvector backend. It is the on-disk source of truth for the logical model that both storage backends implement: documents, extracted claims, wiki aliases, chunks, and embeddings. The SQLite backend does not read these files; it builds the same logical schema in code (`src/mastervault/storage/sqlite.py`), so this DDL exists specifically for the Postgres path.

## Files

| File | Responsibility |
|------|----------------|
| `pg/001_init.sql` | Version 1 Postgres schema. Enables the `vector` extension and creates `meta`, `documents`, `claims`, `claim_affects`, `wiki_aliases`, `chunks`, and `embeddings`. Adds `tsvector GENERATED ALWAYS ... STORED` columns with GIN indexes on `documents` and `claims` for lexical search, and an HNSW `vector_cosine_ops` index (`m = 16, ef_construction = 64`) on `embeddings`. The `{{DIM}}` token in `vector({{DIM}})` is replaced at init time with the embedding dimension (384 for local/mock, 1536 for OpenAI text-embedding-3-small). |

## How it fits

`mvault init` drives `PostgresBackend.init_schema` (`src/mastervault/storage/postgres.py:73`), which globs `migrations/pg/*.sql` in sorted order, substitutes `{{DIM}}`, and runs each file inside one transaction before pinning `embedding_model`, `dimensions`, and `schema_version` into the `meta` table. Ingestion then writes rows here, and retrieval reads them: the GIN `search_tsv` indexes back the lexical channel and the HNSW index backs the vector channel that [../src/mastervault/storage](../src/mastervault/storage) exposes to the fusion layer. A re-init with a different dim or model is rejected against the pinned `meta` values rather than silently rebuilding the index.

## Key concepts / entry points

- `{{DIM}}` substitution — placeholder in `pg/001_init.sql:71` replaced with the configured embedding dimension so one schema file serves both 384-d and 1536-d models.
- Generated `tsvector` columns — `documents.search_tsv` (`pg/001_init.sql:22`) and `claims.search_tsv` (`pg/001_init.sql:34`) are `STORED` columns computed from title/body/statement, indexed with GIN for FTS.
- HNSW vector index — `idx_emb_hnsw` (`pg/001_init.sql:74`) on `embeddings.embedding` using `vector_cosine_ops`, the approximate-nearest-neighbor index for the vector channel.
- `embeddings.record_type` — `'claim' | 'wiki' | 'chunk'` (`pg/001_init.sql:66`) lets one embeddings table hold vectors for all three retrievable record kinds, filtered alongside `domain` via `idx_emb_type`.
- `claim_affects` and `wiki_aliases` — join and alias tables (`pg/001_init.sql:39`, `pg/001_init.sql:46`) that map claims and surface forms to wiki slugs, feeding the wiki-alias-graph retrieval channel.
- `ON DELETE CASCADE` from `documents` — claims, chunks, embeddings, and aliases all reference `documents` with cascade delete, so removing a source document clears its derived rows in one step.
