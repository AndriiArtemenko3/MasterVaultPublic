-- Minimal PostgreSQL parity for parser-neutral structural retrieval records.
CREATE TABLE IF NOT EXISTS structural_records (
  record_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  record_kind TEXT NOT NULL CHECK (record_kind IN ('section','block','table_row')),
  text TEXT NOT NULL,
  asset_sha256 TEXT NOT NULL,
  parsed_artifact_sha256 TEXT NOT NULL,
  parser TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  parser_core_version TEXT NOT NULL,
  parser_profile TEXT NOT NULL,
  normalization_profile TEXT NOT NULL,
  model_identity TEXT NOT NULL,
  resource_limits JSONB NOT NULL,
  page_number INTEGER NOT NULL,
  block_id TEXT NOT NULL,
  section_id TEXT,
  table_id TEXT,
  row_id TEXT,
  cell_ids JSONB NOT NULL,
  evidence JSONB NOT NULL,
  search_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  UNIQUE (doc_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_structural_doc ON structural_records (doc_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_structural_search ON structural_records USING GIN (search_tsv);
