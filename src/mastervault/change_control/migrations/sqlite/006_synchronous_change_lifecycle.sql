-- Synchronous generic admission, regression-suite, and generation-zero baseline authority.

CREATE TABLE change_control_incoming_admission_intents (
  intent_id TEXT PRIMARY KEY,
  intent_sha256 TEXT NOT NULL UNIQUE CHECK (length(intent_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL UNIQUE REFERENCES change_control_operator_runs(run_id),
  bundle_id TEXT NOT NULL UNIQUE,
  bundle_sha256 TEXT NOT NULL UNIQUE CHECK (length(bundle_sha256) = 64),
  admission_sha256 TEXT NOT NULL UNIQUE CHECK (length(admission_sha256) = 64),
  source_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(source_receipt_sha256) = 64),
  projection_sha256 TEXT NOT NULL UNIQUE CHECK (length(projection_sha256) = 64),
  inference_sha256 TEXT NOT NULL UNIQUE CHECK (length(inference_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL
);

CREATE TABLE change_control_incoming_admission_receipts (
  intent_id TEXT PRIMARY KEY REFERENCES change_control_incoming_admission_intents(intent_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  admitted_at TEXT NOT NULL
);

CREATE TABLE change_control_regression_suite_admission_intents (
  intent_id TEXT PRIMARY KEY,
  intent_sha256 TEXT NOT NULL UNIQUE CHECK (length(intent_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL UNIQUE REFERENCES change_control_operator_runs(run_id),
  suite_id TEXT NOT NULL,
  suite_version INTEGER NOT NULL CHECK (suite_version > 0),
  original_sha256 TEXT NOT NULL CHECK (length(original_sha256) = 64),
  original_byte_count INTEGER NOT NULL CHECK (original_byte_count BETWEEN 1 AND 1048576),
  canonical_sha256 TEXT NOT NULL CHECK (length(canonical_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  UNIQUE (suite_id, suite_version),
  UNIQUE (canonical_sha256)
);

CREATE TABLE change_control_regression_suite_admission_receipts (
  intent_id TEXT PRIMARY KEY REFERENCES change_control_regression_suite_admission_intents(intent_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  admitted_at TEXT NOT NULL
);

CREATE TABLE change_control_generation_zero_baseline_receipts (
  receipt_id TEXT PRIMARY KEY,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  record_id TEXT NOT NULL UNIQUE,
  record_sha256 TEXT NOT NULL UNIQUE CHECK (length(record_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  baseline_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL UNIQUE REFERENCES change_control_operator_runs(run_id),
  suite_admission_receipt_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_regression_suite_admission_receipts(receipt_id),
  suite_admission_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(suite_admission_receipt_sha256) = 64),
  incoming_admission_receipt_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_incoming_admission_receipts(receipt_id),
  suite_id TEXT NOT NULL,
  suite_version INTEGER NOT NULL CHECK (suite_version > 0),
  suite_original_sha256 TEXT NOT NULL CHECK (length(suite_original_sha256) = 64),
  suite_canonical_sha256 TEXT NOT NULL CHECK (length(suite_canonical_sha256) = 64),
  incoming_admission_receipt_sha256 TEXT NOT NULL CHECK (length(incoming_admission_receipt_sha256) = 64),
  workspace_inventory_receipt_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_workspace_inventory_receipts(receipt_id),
  workspace_inventory_receipt_sha256 TEXT NOT NULL CHECK (length(workspace_inventory_receipt_sha256) = 64),
  legacy_readiness_receipt_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_legacy_index_readiness_receipts(receipt_id),
  legacy_readiness_receipt_sha256 TEXT NOT NULL CHECK (length(legacy_readiness_receipt_sha256) = 64),
  generation_id TEXT NOT NULL REFERENCES change_control_generation_manifests(generation_id),
  generation_number INTEGER NOT NULL CHECK (generation_number = 0),
  authority_revision INTEGER NOT NULL CHECK (authority_revision = 0),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  inventory_count INTEGER NOT NULL CHECK (inventory_count BETWEEN 1 AND 128),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE change_control_generation_zero_baseline_cases (
  receipt_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  case_id TEXT NOT NULL,
  case_kind TEXT NOT NULL CHECK (case_kind IN ('search','ask')),
  artifact_locator TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  artifact_byte_count INTEGER NOT NULL CHECK (artifact_byte_count > 0),
  PRIMARY KEY (receipt_id, ordinal),
  UNIQUE (receipt_id, case_id),
  UNIQUE (receipt_id, artifact_locator),
  FOREIGN KEY (receipt_id)
    REFERENCES change_control_generation_zero_baseline_receipts(receipt_id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE change_control_activation_baseline_bindings (
  activation_id TEXT PRIMARY KEY
    REFERENCES change_control_managed_activation_intents(activation_id),
  binding_id TEXT NOT NULL UNIQUE,
  binding_sha256 TEXT NOT NULL UNIQUE CHECK (length(binding_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  activation_sha256 TEXT NOT NULL UNIQUE CHECK (length(activation_sha256) = 64),
  run_id TEXT NOT NULL UNIQUE REFERENCES change_control_operator_runs(run_id),
  baseline_receipt_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_generation_zero_baseline_receipts(receipt_id),
  baseline_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(baseline_receipt_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  bound_at TEXT NOT NULL
);

CREATE TABLE change_control_operator_run_links_v6 (
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
    'regression-suite',
    'generation-zero-baseline',
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

INSERT INTO change_control_operator_run_links_v6
SELECT * FROM change_control_operator_run_links;

DROP TABLE change_control_operator_run_links;
ALTER TABLE change_control_operator_run_links_v6 RENAME TO change_control_operator_run_links;

CREATE INDEX idx_change_control_operator_links_run
  ON change_control_operator_run_links (run_id, sequence, link_kind);
CREATE INDEX idx_change_control_operator_runs_listing
  ON change_control_operator_runs (created_at DESC, run_id DESC);
