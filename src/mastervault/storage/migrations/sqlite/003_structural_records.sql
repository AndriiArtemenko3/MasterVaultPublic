-- Parser-neutral structural retrieval records. ParsedDocument schema stays v2.
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
  resource_limits TEXT NOT NULL,
  page_number INTEGER NOT NULL,
  block_id TEXT NOT NULL,
  section_id TEXT,
  table_id TEXT,
  row_id TEXT,
  cell_ids TEXT NOT NULL,
  evidence TEXT NOT NULL,
  UNIQUE (doc_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_structural_doc ON structural_records (doc_id, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS structural_records_fts USING fts5(record_id UNINDEXED, text);
