-- Managed generation publication, isolated index readiness, and activation, schema v4.

CREATE TABLE change_control_managed_activation_intents (
  activation_id TEXT PRIMARY KEY,
  activation_sha256 TEXT NOT NULL UNIQUE CHECK (length(activation_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_managed_review_decisions(request_id),
  decision_id TEXT NOT NULL UNIQUE,
  decision_record_sha256 TEXT NOT NULL UNIQUE CHECK (length(decision_record_sha256) = 64),
  manifest_id TEXT NOT NULL UNIQUE
    REFERENCES change_control_generation_manifests(manifest_id),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  generation_id TEXT NOT NULL UNIQUE,
  expected_authority_id TEXT NOT NULL,
  expected_authority_revision INTEGER NOT NULL CHECK (expected_authority_revision >= 0),
  expected_active_pointer_sha256 TEXT NOT NULL CHECK (length(expected_active_pointer_sha256) = 64),
  projection_id TEXT NOT NULL UNIQUE,
  projection_sha256 TEXT NOT NULL UNIQUE CHECK (length(projection_sha256) = 64),
  generation_repository_id TEXT NOT NULL CHECK (length(generation_repository_id) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE change_control_revision_publication_events (
  activation_id TEXT NOT NULL
    REFERENCES change_control_managed_activation_intents(activation_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  event_id TEXT NOT NULL UNIQUE,
  event_sha256 TEXT NOT NULL UNIQUE CHECK (length(event_sha256) = 64),
  destination_id TEXT NOT NULL,
  repository_relative_path TEXT NOT NULL UNIQUE,
  published_sha256 TEXT NOT NULL CHECK (length(published_sha256) = 64),
  published_byte_count INTEGER NOT NULL CHECK (published_byte_count > 0),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (activation_id, ordinal),
  UNIQUE (activation_id, destination_id)
);

CREATE TABLE change_control_index_generation_receipts (
  activation_id TEXT PRIMARY KEY
    REFERENCES change_control_managed_activation_intents(activation_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  generation_id TEXT NOT NULL UNIQUE,
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  projection_id TEXT NOT NULL UNIQUE,
  projection_sha256 TEXT NOT NULL UNIQUE CHECK (length(projection_sha256) = 64),
  index_relative_path TEXT NOT NULL UNIQUE,
  index_file_sha256 TEXT NOT NULL CHECK (length(index_file_sha256) = 64),
  logical_index_fingerprint TEXT NOT NULL CHECK (length(logical_index_fingerprint) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  ready_at TEXT NOT NULL
);

CREATE TABLE change_control_generation_activation_receipts (
  activation_id TEXT PRIMARY KEY
    REFERENCES change_control_managed_activation_intents(activation_id),
  receipt_id TEXT NOT NULL UNIQUE,
  receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
  generation_id TEXT NOT NULL UNIQUE,
  authority_id TEXT NOT NULL UNIQUE,
  authority_revision INTEGER NOT NULL CHECK (authority_revision > 0),
  publication_set_sha256 TEXT NOT NULL CHECK (length(publication_set_sha256) = 64),
  publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
  index_receipt_id TEXT NOT NULL UNIQUE,
  index_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(index_receipt_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  activated_at TEXT NOT NULL,
  FOREIGN KEY (generation_id)
    REFERENCES change_control_generation_manifests(generation_id),
  FOREIGN KEY (index_receipt_id)
    REFERENCES change_control_index_generation_receipts(receipt_id)
);
