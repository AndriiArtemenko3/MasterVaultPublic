-- Authoritative human-review requests and immutable decisions, schema v2.
CREATE TABLE change_control_review_requests (
  request_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES change_control_operations(operation_id),
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
  base_aggregate_sha256 TEXT NOT NULL CHECK (length(base_aggregate_sha256) = 64),
  base_aggregate_schema_version INTEGER NOT NULL CHECK (base_aggregate_schema_version = 1),
  base_aggregate_json TEXT NOT NULL,
  requester_id TEXT NOT NULL CHECK (length(requester_id) BETWEEN 1 AND 128),
  rationale TEXT NOT NULL CHECK (length(rationale) BETWEEN 1 AND 4000),
  requested_at TEXT NOT NULL,
  request_payload_sha256 TEXT NOT NULL CHECK (length(request_payload_sha256) = 64),
  UNIQUE (request_id, aggregate_id, base_revision)
);
CREATE INDEX idx_change_control_review_requests_head
  ON change_control_review_requests (aggregate_id, base_revision, request_id);

CREATE TABLE change_control_review_request_subjects (
  request_id TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('document-replacement','temporal-constraint')),
  subject_id TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version = 1),
  payload_json TEXT NOT NULL,
  subject_sha256 TEXT NOT NULL CHECK (length(subject_sha256) = 64),
  PRIMARY KEY (request_id, ordinal),
  UNIQUE (request_id, subject_kind, subject_id),
  UNIQUE (aggregate_id, base_revision, subject_kind, subject_id),
  FOREIGN KEY (request_id, aggregate_id, base_revision)
    REFERENCES change_control_review_requests(request_id, aggregate_id, base_revision)
);
CREATE INDEX idx_change_control_review_subject_lookup
  ON change_control_review_request_subjects (aggregate_id, subject_kind, subject_id, base_revision);

CREATE TABLE change_control_review_decisions (
  request_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES change_control_operations(operation_id),
  aggregate_id TEXT NOT NULL,
  base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
  reviewer_id TEXT NOT NULL CHECK (length(reviewer_id) BETWEEN 1 AND 128),
  rationale TEXT NOT NULL CHECK (length(rationale) BETWEEN 1 AND 4000),
  decision_payload_sha256 TEXT NOT NULL CHECK (length(decision_payload_sha256) = 64),
  decided_revision INTEGER NOT NULL CHECK (decided_revision = base_revision + 1),
  decided_aggregate_sha256 TEXT NOT NULL CHECK (length(decided_aggregate_sha256) = 64),
  decided_aggregate_schema_version INTEGER NOT NULL CHECK (decided_aggregate_schema_version = 1),
  decided_aggregate_json TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  FOREIGN KEY (request_id, aggregate_id, base_revision)
    REFERENCES change_control_review_requests(request_id, aggregate_id, base_revision)
);
CREATE INDEX idx_change_control_review_decisions_operation
  ON change_control_review_decisions (operation_id, request_id);

CREATE TABLE change_control_review_decision_items (
  request_id TEXT NOT NULL REFERENCES change_control_review_decisions(request_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('document-replacement','temporal-constraint')),
  subject_id TEXT NOT NULL,
  original_subject_sha256 TEXT NOT NULL CHECK (length(original_subject_sha256) = 64),
  disposition TEXT NOT NULL CHECK (disposition IN ('accepted','edited','rejected')),
  edit_rationale TEXT CHECK (edit_rationale IS NULL OR length(edit_rationale) BETWEEN 1 AND 4000),
  edit_confidence REAL CHECK (edit_confidence IS NULL OR (edit_confidence >= 0.0 AND edit_confidence <= 1.0)),
  PRIMARY KEY (request_id, ordinal),
  UNIQUE (request_id, subject_kind, subject_id),
  FOREIGN KEY (request_id, subject_kind, subject_id)
    REFERENCES change_control_review_request_subjects(request_id, subject_kind, subject_id),
  CHECK (
    (disposition IN ('accepted','rejected') AND edit_rationale IS NULL AND edit_confidence IS NULL)
    OR
    (disposition = 'edited' AND subject_kind = 'document-replacement'
      AND (edit_rationale IS NOT NULL OR edit_confidence IS NOT NULL))
    OR
    (disposition = 'edited' AND subject_kind = 'temporal-constraint'
      AND edit_rationale IS NOT NULL AND edit_confidence IS NULL)
  )
);
