-- Authoritative managed-revision review and generation-zero authority, schema v3.
-- PR-A stores immutable review evidence only. Publication, activation, index,
-- regression, and audit-effect receipts belong to a later migration.

CREATE TABLE change_control_generation_manifests (
  manifest_id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  generation_id TEXT NOT NULL UNIQUE,
  generation_number INTEGER NOT NULL CHECK (generation_number >= 0),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  manifest_kind TEXT NOT NULL CHECK (manifest_kind IN ('generation-zero','managed-overlay')),
  created_inactive INTEGER NOT NULL CHECK (created_inactive IN (0, 1)),
  source_request_id TEXT,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (aggregate_id, generation_number, manifest_sha256),
  CHECK (
    (manifest_kind = 'generation-zero' AND generation_number = 0
      AND created_inactive = 0 AND source_request_id IS NULL)
    OR
    (manifest_kind = 'managed-overlay' AND generation_number >= 1
      AND created_inactive = 1 AND source_request_id IS NOT NULL)
  ),
  FOREIGN KEY (source_request_id)
    REFERENCES change_control_managed_review_request_records(request_id)
);
CREATE INDEX idx_change_control_generation_manifests_aggregate
  ON change_control_generation_manifests (aggregate_id, generation_number, manifest_id);

CREATE TABLE change_control_active_generation (
  aggregate_id TEXT PRIMARY KEY REFERENCES change_control_aggregates(aggregate_id),
  initialization_operation_id TEXT NOT NULL UNIQUE,
  authority_id TEXT NOT NULL UNIQUE,
  authority_revision INTEGER NOT NULL CHECK (authority_revision >= 0),
  origin_kind TEXT NOT NULL
    CHECK (origin_kind IN ('verified-seed-bootstrap','managed-decision')),
  active_generation_id TEXT NOT NULL,
  active_generation_number INTEGER NOT NULL CHECK (active_generation_number >= 0),
  active_manifest_sha256 TEXT NOT NULL CHECK (length(active_manifest_sha256) = 64),
  active_pointer_sha256 TEXT NOT NULL CHECK (length(active_pointer_sha256) = 64),
  authority_schema_version INTEGER NOT NULL CHECK (authority_schema_version = 1),
  authority_json TEXT NOT NULL,
  initialized_at TEXT NOT NULL,
  CHECK (
    (origin_kind = 'verified-seed-bootstrap'
      AND authority_revision = 0 AND active_generation_number = 0)
    OR
    (origin_kind = 'managed-decision'
      AND authority_revision >= 1 AND active_generation_number >= 1)
  ),
  FOREIGN KEY (active_generation_id)
    REFERENCES change_control_generation_manifests(generation_id)
);

CREATE TABLE change_control_managed_review_bundles (
  bundle_id TEXT PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL UNIQUE CHECK (length(bundle_sha256) = 64),
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
  base_aggregate_sha256 TEXT NOT NULL CHECK (length(base_aggregate_sha256) = 64),
  authority_id TEXT NOT NULL,
  authority_revision INTEGER NOT NULL CHECK (authority_revision >= 0),
  active_generation_id TEXT NOT NULL,
  active_manifest_sha256 TEXT NOT NULL CHECK (length(active_manifest_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  FOREIGN KEY (aggregate_id) REFERENCES change_control_active_generation(aggregate_id)
);
CREATE INDEX idx_change_control_managed_review_bundles_base
  ON change_control_managed_review_bundles
    (aggregate_id, base_revision, authority_revision, bundle_id);

CREATE TABLE change_control_managed_review_targets (
  bundle_id TEXT NOT NULL REFERENCES change_control_managed_review_bundles(bundle_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  target_id TEXT NOT NULL,
  target_key TEXT NOT NULL,
  target_sha256 TEXT NOT NULL CHECK (length(target_sha256) = 64),
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('proposed-revision','no-change')),
  subject_identity TEXT NOT NULL,
  subject_sha256 TEXT NOT NULL CHECK (length(subject_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (bundle_id, ordinal),
  UNIQUE (bundle_id, target_id),
  UNIQUE (bundle_id, target_key)
);
CREATE INDEX idx_change_control_managed_review_target_lookup
  ON change_control_managed_review_targets (target_key, bundle_id);

CREATE TABLE change_control_managed_review_request_records (
  request_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,
  record_sha256 TEXT NOT NULL UNIQUE CHECK (length(record_sha256) = 64),
  bundle_id TEXT NOT NULL UNIQUE REFERENCES change_control_managed_review_bundles(bundle_id),
  operation_id TEXT NOT NULL UNIQUE,
  request_payload_sha256 TEXT NOT NULL CHECK (length(request_payload_sha256) = 64),
  requested_at TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL
);

CREATE TABLE change_control_managed_review_request_delivery_receipts (
  request_id TEXT NOT NULL REFERENCES change_control_managed_review_request_records(request_id),
  delivery_sequence INTEGER NOT NULL CHECK (delivery_sequence >= 0),
  receipt_id TEXT NOT NULL,
  replayed INTEGER NOT NULL CHECK (replayed IN (0, 1)),
  delivered_at TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (request_id, delivery_sequence)
);
CREATE INDEX idx_change_control_managed_request_deliveries
  ON change_control_managed_review_request_delivery_receipts
    (request_id, delivery_sequence);

CREATE TABLE change_control_managed_review_decisions (
  request_id TEXT PRIMARY KEY
    REFERENCES change_control_managed_review_request_records(request_id),
  decision_id TEXT NOT NULL UNIQUE,
  record_id TEXT NOT NULL UNIQUE,
  record_sha256 TEXT NOT NULL UNIQUE CHECK (length(record_sha256) = 64),
  operation_id TEXT NOT NULL UNIQUE,
  decision_payload_sha256 TEXT NOT NULL CHECK (length(decision_payload_sha256) = 64),
  expected_authority_id TEXT NOT NULL,
  expected_authority_revision INTEGER NOT NULL CHECK (expected_authority_revision >= 0),
  resulting_manifest_id TEXT,
  activation_plan_id TEXT,
  decided_at TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  CHECK (
    (resulting_manifest_id IS NULL AND activation_plan_id IS NULL)
    OR
    (resulting_manifest_id IS NOT NULL AND activation_plan_id IS NOT NULL)
  ),
  FOREIGN KEY (resulting_manifest_id)
    REFERENCES change_control_generation_manifests(manifest_id)
);

CREATE TABLE change_control_managed_review_decision_items (
  request_id TEXT NOT NULL REFERENCES change_control_managed_review_decisions(request_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  target_id TEXT NOT NULL,
  original_target_sha256 TEXT NOT NULL CHECK (length(original_target_sha256) = 64),
  disposition TEXT NOT NULL
    CHECK (disposition IN ('approve','edit','reject','confirm-no-change')),
  final_plan_id TEXT,
  final_plan_sha256 TEXT CHECK (final_plan_sha256 IS NULL OR length(final_plan_sha256) = 64),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (request_id, ordinal),
  UNIQUE (request_id, target_id),
  CHECK (
    (disposition = 'edit' AND final_plan_id IS NOT NULL AND final_plan_sha256 IS NOT NULL)
    OR
    (disposition != 'edit' AND final_plan_id IS NULL AND final_plan_sha256 IS NULL)
  )
);

CREATE TABLE change_control_managed_review_decision_delivery_receipts (
  request_id TEXT NOT NULL REFERENCES change_control_managed_review_decisions(request_id),
  delivery_sequence INTEGER NOT NULL CHECK (delivery_sequence >= 0),
  receipt_id TEXT NOT NULL,
  replayed INTEGER NOT NULL CHECK (replayed IN (0, 1)),
  delivered_at TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (request_id, delivery_sequence)
);
CREATE INDEX idx_change_control_managed_decision_deliveries
  ON change_control_managed_review_decision_delivery_receipts
    (request_id, delivery_sequence);
