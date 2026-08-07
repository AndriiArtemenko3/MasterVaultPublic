-- Dedicated MasterVault change-control aggregate store, schema v1.
CREATE TABLE change_control_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE change_control_schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum_sha256 TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE change_control_aggregates (
  aggregate_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  model_schema_version INTEGER NOT NULL CHECK (model_schema_version = 1),
  aggregate_sha256 TEXT NOT NULL CHECK (length(aggregate_sha256) = 64),
  updated_at TEXT NOT NULL
);

CREATE TABLE change_control_operations (
  operation_id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id),
  expected_revision INTEGER,
  aggregate_sha256 TEXT NOT NULL CHECK (length(aggregate_sha256) = 64),
  committed_revision INTEGER NOT NULL CHECK (committed_revision >= 1),
  changed INTEGER NOT NULL CHECK (changed IN (0, 1)),
  committed_at TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
  CHECK (expected_revision IS NULL OR expected_revision >= 1)
);
CREATE INDEX idx_change_control_operations_aggregate
  ON change_control_operations (aggregate_id, committed_revision);

CREATE TABLE change_control_document_versions (
  aggregate_id TEXT NOT NULL REFERENCES change_control_aggregates(aggregate_id) ON DELETE CASCADE,
  document_version_id TEXT NOT NULL,
  identity_namespace TEXT NOT NULL CHECK (identity_namespace = 'mastervault.document-version.v1'),
  document_id TEXT NOT NULL,
  document_family TEXT NOT NULL,
  version_label TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
  declared_effective_from TEXT NOT NULL,
  declared_effective_to TEXT,
  role TEXT NOT NULL CHECK (role IN ('policy','memo','faq','sop','process','proposal','other')),
  authority TEXT NOT NULL CHECK (authority IN ('primary','delegated','transactional','informational')),
  PRIMARY KEY (aggregate_id, document_version_id),
  UNIQUE (aggregate_id, document_family, version_label)
);

CREATE TABLE change_control_claim_identities (
  aggregate_id TEXT NOT NULL,
  claim_identity_id TEXT NOT NULL,
  identity_namespace TEXT NOT NULL CHECK (identity_namespace = 'mastervault.claim-identity.v1'),
  document_version_id TEXT NOT NULL,
  source_claim_id TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, claim_identity_id),
  UNIQUE (aggregate_id, document_version_id, source_claim_id),
  FOREIGN KEY (aggregate_id, document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id)
);

CREATE TABLE change_control_claim_revisions (
  aggregate_id TEXT NOT NULL,
  claim_revision_id TEXT NOT NULL,
  claim_identity_id TEXT NOT NULL,
  revision_namespace TEXT NOT NULL CHECK (revision_namespace = 'mastervault.claim-revision.v1'),
  statement TEXT NOT NULL,
  source_note_path TEXT NOT NULL,
  source_note_sha256 TEXT NOT NULL CHECK (length(source_note_sha256) = 64),
  declared_effective_from TEXT NOT NULL,
  declared_effective_to TEXT,
  PRIMARY KEY (aggregate_id, claim_revision_id),
  FOREIGN KEY (aggregate_id, claim_identity_id)
    REFERENCES change_control_claim_identities(aggregate_id, claim_identity_id)
);
CREATE INDEX idx_change_control_claim_revision_identity
  ON change_control_claim_revisions (aggregate_id, claim_identity_id);

CREATE TABLE change_control_claim_scopes (
  aggregate_id TEXT NOT NULL,
  claim_revision_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, claim_revision_id, scope),
  FOREIGN KEY (aggregate_id, claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id) ON DELETE CASCADE
);
CREATE INDEX idx_change_control_claim_scope
  ON change_control_claim_scopes (aggregate_id, scope, claim_revision_id);

CREATE TABLE change_control_claim_evidence (
  aggregate_id TEXT NOT NULL,
  claim_revision_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  evidence_type TEXT NOT NULL CHECK (evidence_type IN ('evidence-ref-v1','structural-evidence-ref-v2')),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version IN (1, 2)),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, claim_revision_id, ordinal),
  FOREIGN KEY (aggregate_id, claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id) ON DELETE CASCADE
);

CREATE TABLE change_control_claim_pairs (
  aggregate_id TEXT NOT NULL,
  pair_id TEXT NOT NULL,
  identity_namespace TEXT NOT NULL CHECK (identity_namespace = 'mastervault.claim-pair.v1'),
  left_claim_revision_id TEXT NOT NULL,
  right_claim_revision_id TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, pair_id),
  UNIQUE (aggregate_id, left_claim_revision_id, right_claim_revision_id),
  CHECK (left_claim_revision_id < right_claim_revision_id),
  FOREIGN KEY (aggregate_id, left_claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id),
  FOREIGN KEY (aggregate_id, right_claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id)
);
CREATE INDEX idx_change_control_pair_right
  ON change_control_claim_pairs (aggregate_id, right_claim_revision_id);

CREATE TABLE change_control_relation_assessments (
  aggregate_id TEXT NOT NULL,
  pair_id TEXT NOT NULL,
  disposition TEXT NOT NULL CHECK (disposition IN ('SUPERSEDES','CONTRADICTS','COEXISTS','UNRELATED')),
  rationale TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
  relation_type TEXT,
  relation_id TEXT,
  endpoint_first_id TEXT,
  endpoint_second_id TEXT,
  PRIMARY KEY (aggregate_id, pair_id),
  UNIQUE (aggregate_id, relation_id),
  FOREIGN KEY (aggregate_id, pair_id)
    REFERENCES change_control_claim_pairs(aggregate_id, pair_id),
  FOREIGN KEY (aggregate_id, endpoint_first_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id),
  FOREIGN KEY (aggregate_id, endpoint_second_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id),
  CHECK (
    (disposition IN ('COEXISTS','UNRELATED') AND relation_type IS NULL AND relation_id IS NULL
      AND endpoint_first_id IS NULL AND endpoint_second_id IS NULL)
    OR
    (disposition IN ('SUPERSEDES','CONTRADICTS') AND relation_type = disposition
      AND relation_id IS NOT NULL AND endpoint_first_id IS NOT NULL AND endpoint_second_id IS NOT NULL)
  )
);
CREATE INDEX idx_change_control_relation_first
  ON change_control_relation_assessments (aggregate_id, endpoint_first_id);
CREATE INDEX idx_change_control_relation_second
  ON change_control_relation_assessments (aggregate_id, endpoint_second_id);

CREATE TABLE change_control_dependencies (
  aggregate_id TEXT NOT NULL,
  dependency_id TEXT NOT NULL,
  relation_type TEXT NOT NULL CHECK (relation_type = 'DEPENDS_ON'),
  downstream_document_version_id TEXT NOT NULL,
  upstream_claim_revision_id TEXT NOT NULL,
  dependency_kind TEXT NOT NULL CHECK (dependency_kind IN ('quotes','implements','summarizes','historical-reference')),
  rationale TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
  PRIMARY KEY (aggregate_id, dependency_id),
  UNIQUE (aggregate_id, downstream_document_version_id, upstream_claim_revision_id, dependency_kind),
  FOREIGN KEY (aggregate_id, downstream_document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id),
  FOREIGN KEY (aggregate_id, upstream_claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id)
);
CREATE INDEX idx_change_control_dependency_upstream
  ON change_control_dependencies (aggregate_id, upstream_claim_revision_id, dependency_id);
CREATE INDEX idx_change_control_dependency_downstream
  ON change_control_dependencies (aggregate_id, downstream_document_version_id, dependency_id);

CREATE TABLE change_control_dependency_spans (
  aggregate_id TEXT NOT NULL,
  dependency_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  document_version_id TEXT NOT NULL,
  source_note_path TEXT NOT NULL,
  source_note_sha256 TEXT NOT NULL CHECK (length(source_note_sha256) = 64),
  record_id TEXT CHECK (record_id IS NULL),
  quote TEXT NOT NULL,
  start_char INTEGER NOT NULL CHECK (start_char >= 0),
  end_char INTEGER NOT NULL CHECK (end_char > start_char),
  PRIMARY KEY (aggregate_id, dependency_id, ordinal),
  FOREIGN KEY (aggregate_id, dependency_id)
    REFERENCES change_control_dependencies(aggregate_id, dependency_id) ON DELETE CASCADE,
  FOREIGN KEY (aggregate_id, document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id)
);

CREATE TABLE change_control_dependency_span_evidence (
  aggregate_id TEXT NOT NULL,
  dependency_id TEXT NOT NULL,
  span_ordinal INTEGER NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  evidence_type TEXT NOT NULL CHECK (evidence_type IN ('evidence-ref-v1','structural-evidence-ref-v2')),
  payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version IN (1, 2)),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, dependency_id, span_ordinal, ordinal),
  FOREIGN KEY (aggregate_id, dependency_id, span_ordinal)
    REFERENCES change_control_dependency_spans(aggregate_id, dependency_id, ordinal) ON DELETE CASCADE
);

CREATE TABLE change_control_dependency_claims (
  aggregate_id TEXT NOT NULL,
  dependency_id TEXT NOT NULL,
  claim_revision_id TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, dependency_id, claim_revision_id),
  FOREIGN KEY (aggregate_id, dependency_id)
    REFERENCES change_control_dependencies(aggregate_id, dependency_id) ON DELETE CASCADE,
  FOREIGN KEY (aggregate_id, claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id)
);

CREATE TABLE change_control_document_replacements (
  aggregate_id TEXT NOT NULL,
  relation_id TEXT NOT NULL,
  identity_namespace TEXT NOT NULL CHECK (identity_namespace = 'mastervault.document-replacement.v1'),
  relation_type TEXT NOT NULL CHECK (relation_type = 'SUPERSEDES'),
  newer_document_version_id TEXT NOT NULL,
  older_document_version_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected')),
  rationale TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
  PRIMARY KEY (aggregate_id, relation_id),
  UNIQUE (aggregate_id, newer_document_version_id, older_document_version_id),
  FOREIGN KEY (aggregate_id, newer_document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id),
  FOREIGN KEY (aggregate_id, older_document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id)
);
CREATE INDEX idx_change_control_replacement_older
  ON change_control_document_replacements (aggregate_id, older_document_version_id);

CREATE TABLE change_control_temporal_constraints (
  aggregate_id TEXT NOT NULL,
  constraint_id TEXT NOT NULL,
  identity_namespace TEXT NOT NULL CHECK (identity_namespace = 'mastervault.temporal-constraint.v1'),
  resolver_version TEXT NOT NULL CHECK (resolver_version = 'temporal-resolution-v1'),
  target_kind TEXT NOT NULL CHECK (target_kind IN ('document-version','claim-revision')),
  target_document_version_id TEXT,
  target_claim_revision_id TEXT,
  inferred_valid_to_exclusive TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected')),
  rationale TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, constraint_id),
  UNIQUE (aggregate_id, constraint_id, target_kind),
  FOREIGN KEY (aggregate_id, target_document_version_id)
    REFERENCES change_control_document_versions(aggregate_id, document_version_id),
  FOREIGN KEY (aggregate_id, target_claim_revision_id)
    REFERENCES change_control_claim_revisions(aggregate_id, claim_revision_id),
  CHECK (
    (target_kind = 'document-version' AND target_document_version_id IS NOT NULL
      AND target_claim_revision_id IS NULL)
    OR
    (target_kind = 'claim-revision' AND target_claim_revision_id IS NOT NULL
      AND target_document_version_id IS NULL)
  )
);
CREATE INDEX idx_change_control_temporal_document
  ON change_control_temporal_constraints (aggregate_id, target_document_version_id, status);
CREATE INDEX idx_change_control_temporal_claim
  ON change_control_temporal_constraints (aggregate_id, target_claim_revision_id, status);

CREATE TABLE change_control_temporal_constraint_bases (
  aggregate_id TEXT NOT NULL,
  constraint_id TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  basis_relation_id TEXT NOT NULL,
  PRIMARY KEY (aggregate_id, constraint_id, ordinal),
  UNIQUE (aggregate_id, constraint_id, basis_relation_id),
  FOREIGN KEY (aggregate_id, constraint_id, target_kind)
    REFERENCES change_control_temporal_constraints(aggregate_id, constraint_id, target_kind) ON DELETE CASCADE
);
CREATE INDEX idx_change_control_temporal_basis_relation
  ON change_control_temporal_constraint_bases (aggregate_id, basis_relation_id);
