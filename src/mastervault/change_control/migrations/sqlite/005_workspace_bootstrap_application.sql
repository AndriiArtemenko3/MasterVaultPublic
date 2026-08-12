-- Generic workspace bootstrap, operator navigation, and workspace generation zero.
--
-- The migration runner disables foreign-key enforcement around this migration
-- and performs an explicit foreign_key_check before commit.  That is required
-- to rebuild the active-generation parent table while preserving all v3/v4
-- child rows and references.

CREATE TABLE change_control_workspace_bootstrap_intents (
  bootstrap_id TEXT PRIMARY KEY,
  intent_sha256 TEXT NOT NULL UNIQUE CHECK (length(intent_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  aggregate_id TEXT NOT NULL,
  inventory_id TEXT NOT NULL UNIQUE,
  inventory_sha256 TEXT NOT NULL UNIQUE CHECK (length(inventory_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE change_control_workspace_inventories (
  inventory_id TEXT PRIMARY KEY,
  inventory_sha256 TEXT NOT NULL UNIQUE CHECK (length(inventory_sha256) = 64),
  bootstrap_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_workspace_bootstrap_intents(bootstrap_id),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  stored_at TEXT NOT NULL
);

CREATE TABLE change_control_workspace_inventory_receipts (
  bootstrap_id TEXT PRIMARY KEY
    REFERENCES change_control_workspace_bootstrap_intents(bootstrap_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  aggregate_operation_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_operations(operation_id),
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  aggregate_revision INTEGER NOT NULL CHECK (aggregate_revision > 0),
  aggregate_sha256 TEXT NOT NULL CHECK (length(aggregate_sha256) = 64),
  inventory_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_workspace_inventories(inventory_id),
  inventory_sha256 TEXT NOT NULL UNIQUE CHECK (length(inventory_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE change_control_legacy_index_readiness_receipts (
  bootstrap_id TEXT PRIMARY KEY
    REFERENCES change_control_workspace_inventory_receipts(bootstrap_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  inventory_receipt_id TEXT NOT NULL UNIQUE,
  inventory_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(inventory_receipt_sha256) = 64),
  index_logical_fingerprint TEXT NOT NULL CHECK (length(index_logical_fingerprint) = 64),
  index_file_sha256 TEXT NOT NULL CHECK (length(index_file_sha256) = 64),
  index_file_byte_count INTEGER NOT NULL CHECK (index_file_byte_count > 0),
  index_schema_version INTEGER NOT NULL CHECK (index_schema_version > 0),
  embedding_model TEXT NOT NULL,
  embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions > 0),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  ready_at TEXT NOT NULL,
  FOREIGN KEY (inventory_receipt_id)
    REFERENCES change_control_workspace_inventory_receipts(receipt_id)
);

CREATE TABLE change_control_active_generation_v5 (
  aggregate_id TEXT PRIMARY KEY REFERENCES change_control_aggregates(aggregate_id),
  initialization_operation_id TEXT NOT NULL UNIQUE,
  authority_id TEXT NOT NULL UNIQUE,
  authority_revision INTEGER NOT NULL CHECK (authority_revision >= 0),
  origin_kind TEXT NOT NULL
    CHECK (origin_kind IN (
      'verified-seed-bootstrap',
      'verified-workspace-bootstrap',
      'managed-decision'
    )),
  active_generation_id TEXT NOT NULL,
  active_generation_number INTEGER NOT NULL CHECK (active_generation_number >= 0),
  active_manifest_sha256 TEXT NOT NULL CHECK (length(active_manifest_sha256) = 64),
  active_pointer_sha256 TEXT NOT NULL CHECK (length(active_pointer_sha256) = 64),
  authority_schema_version INTEGER NOT NULL CHECK (authority_schema_version = 1),
  authority_json TEXT NOT NULL,
  initialized_at TEXT NOT NULL,
  CHECK (
    (origin_kind IN ('verified-seed-bootstrap','verified-workspace-bootstrap')
      AND authority_revision = 0 AND active_generation_number = 0)
    OR
    (origin_kind = 'managed-decision'
      AND authority_revision >= 1 AND active_generation_number >= 1)
  ),
  FOREIGN KEY (active_generation_id)
    REFERENCES change_control_generation_manifests(generation_id)
);

INSERT INTO change_control_active_generation_v5
SELECT * FROM change_control_active_generation;

DROP TABLE change_control_active_generation;
ALTER TABLE change_control_active_generation_v5 RENAME TO change_control_active_generation;

CREATE TABLE change_control_operator_runs (
  run_id TEXT PRIMARY KEY,
  run_sha256 TEXT NOT NULL UNIQUE CHECK (length(run_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  base_authority_id TEXT NOT NULL,
  base_authority_revision INTEGER NOT NULL CHECK (base_authority_revision >= 0),
  base_active_pointer_sha256 TEXT NOT NULL CHECK (length(base_active_pointer_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE change_control_operator_run_links (
  run_id TEXT NOT NULL REFERENCES change_control_operator_runs(run_id),
  sequence INTEGER NOT NULL CHECK (sequence >= 0),
  link_id TEXT NOT NULL UNIQUE,
  link_sha256 TEXT NOT NULL UNIQUE CHECK (length(link_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  link_kind TEXT NOT NULL CHECK (link_kind IN (
    'bootstrap-intent',
    'workspace-inventory',
    'legacy-index-readiness',
    'generation-zero-authority',
    'incoming-source',
    'temporal-proposal',
    'temporal-review-request',
    'temporal-review-decision',
    'impact-evidence',
    'revision-planning',
    'managed-review-request',
    'managed-review-decision',
    'activation-operation',
    'regression',
    'report'
  )),
  target_id TEXT NOT NULL,
  target_sha256 TEXT NOT NULL CHECK (length(target_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (run_id, sequence),
  UNIQUE (run_id, link_kind)
);

CREATE INDEX idx_change_control_operator_links_run
  ON change_control_operator_run_links (run_id, sequence, link_kind);
