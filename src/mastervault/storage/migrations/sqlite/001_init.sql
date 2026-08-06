-- MasterVault SQLite schema v1. {{DIM}} is the configured embedding dimension.
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('source','wiki','decision','strategy')),
  domain TEXT NOT NULL,
  rel_path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  frontmatter TEXT NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents (domain, doc_type);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  statement TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims (doc_id);

CREATE TABLE IF NOT EXISTS claim_affects (
  claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  wiki_slug TEXT NOT NULL,
  PRIMARY KEY (claim_id, wiki_slug)
);
CREATE INDEX IF NOT EXISTS idx_affects_slug ON claim_affects (wiki_slug);

CREATE TABLE IF NOT EXISTS wiki_aliases (
  alias TEXT NOT NULL,
  wiki_slug TEXT NOT NULL,
  domain TEXT NOT NULL,
  doc_id TEXT REFERENCES documents(doc_id) ON DELETE CASCADE,
  PRIMARY KEY (alias, wiki_slug)
);
CREATE INDEX IF NOT EXISTS idx_aliases_doc ON wiki_aliases (doc_id);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  text TEXT NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);

CREATE TABLE IF NOT EXISTS embeddings (
  record_id TEXT PRIMARY KEY,
  record_type TEXT NOT NULL CHECK (record_type IN ('claim','wiki','chunk')),
  doc_id TEXT REFERENCES documents(doc_id) ON DELETE CASCADE,
  domain TEXT,
  content_hash TEXT NOT NULL,
  model_version TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_type ON embeddings (record_type, domain);

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim_id UNINDEXED, statement);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(doc_id UNINDEXED, title, body);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_records USING vec0(
  record_id TEXT PRIMARY KEY,
  embedding float[{{DIM}}]
);
