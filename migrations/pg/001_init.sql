-- MasterVault Postgres schema v1.
-- {{DIM}} is substituted by `mvault init` with the configured embedding dimension
-- (384 for local/mock, 1536 for OpenAI text-embedding-3-small).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id       TEXT PRIMARY KEY,
  doc_type     TEXT NOT NULL CHECK (doc_type IN ('source','wiki','decision','strategy')),
  domain       TEXT NOT NULL CHECK (domain IN ('customer-support','sales-crm','operations','internal-admin')),
  rel_path     TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  frontmatter  JSONB NOT NULL,
  body         TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  search_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED
);
CREATE INDEX IF NOT EXISTS idx_documents_tsv    ON documents USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents (domain, doc_type);

CREATE TABLE IF NOT EXISTS claims (
  claim_id     TEXT PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
  ordinal      INT  NOT NULL,
  statement    TEXT NOT NULL,
  confidence   TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
  content_hash TEXT NOT NULL,
  search_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', statement)) STORED
);
CREATE INDEX IF NOT EXISTS idx_claims_tsv ON claims USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims (doc_id);

CREATE TABLE IF NOT EXISTS claim_affects (
  claim_id  TEXT NOT NULL REFERENCES claims ON DELETE CASCADE,
  wiki_slug TEXT NOT NULL,
  PRIMARY KEY (claim_id, wiki_slug)
);
CREATE INDEX IF NOT EXISTS idx_affects_slug ON claim_affects (wiki_slug);

CREATE TABLE IF NOT EXISTS wiki_aliases (
  alias     TEXT NOT NULL,
  wiki_slug TEXT NOT NULL,
  domain    TEXT NOT NULL,
  PRIMARY KEY (alias, wiki_slug)
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     TEXT PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
  ordinal      INT NOT NULL,
  text         TEXT NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);

CREATE TABLE IF NOT EXISTS embeddings (
  record_id     TEXT PRIMARY KEY,
  record_type   TEXT NOT NULL CHECK (record_type IN ('claim','wiki','chunk')),
  doc_id        TEXT REFERENCES documents ON DELETE CASCADE,
  domain        TEXT,
  content_hash  TEXT NOT NULL,
  model_version TEXT NOT NULL,
  embedding     vector({{DIM}}) NOT NULL,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_emb_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_emb_type ON embeddings (record_type, domain);
