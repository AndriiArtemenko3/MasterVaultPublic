"""Dedicated transactional SQLite persistence for change-control aggregates."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyAssessment,
    DependencyKind,
    DependencyRegistry,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentSpanReference,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalTargetKind,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
    normalize_logical_key,
)
from mastervault.change_control.review import (
    HumanReviewDecision,
    HumanReviewDecisionCommand,
    HumanReviewDecisionReceipt,
    HumanReviewRequest,
    HumanReviewRequestCommand,
    HumanReviewRequestReceipt,
    HumanReviewRequestView,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewLifecycle,
    ReviewSubjectEdit,
    ReviewSubjectKind,
    ReviewSubjectRef,
    ReviewSubjectSnapshot,
    apply_human_review_decision,
    human_review_decision_payload_sha256,
    human_review_request_id,
    human_review_request_payload_sha256,
    snapshot_from_payload,
    snapshot_payload_json,
    subject_from_aggregate,
)
from mastervault.document_intelligence.models import EvidenceRef, StructuralEvidenceRef

_STORE_IDENTITY = "mastervault.change-control.sqlite"
_SCHEMA_VERSION = 3
_MODEL_SCHEMA_VERSION = 1
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations" / "sqlite"
_MIGRATION_RE = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")

_AGGREGATE_TABLES_CHILD_FIRST = (
    "change_control_temporal_constraint_bases",
    "change_control_temporal_constraints",
    "change_control_dependency_span_evidence",
    "change_control_dependency_claims",
    "change_control_dependency_spans",
    "change_control_dependencies",
    "change_control_relation_assessments",
    "change_control_claim_pairs",
    "change_control_document_replacements",
    "change_control_claim_evidence",
    "change_control_claim_scopes",
    "change_control_claim_revisions",
    "change_control_claim_identities",
    "change_control_document_versions",
)
_V1_EXPECTED_TABLES = {
    "change_control_meta",
    "change_control_schema_migrations",
    "change_control_aggregates",
    "change_control_operations",
    *_AGGREGATE_TABLES_CHILD_FIRST,
}
_REVIEW_TABLES = {
    "change_control_review_requests",
    "change_control_review_request_subjects",
    "change_control_review_decisions",
    "change_control_review_decision_items",
}
_V2_EXPECTED_TABLES = _V1_EXPECTED_TABLES | _REVIEW_TABLES
_MANAGED_REVIEW_TABLES = {
    "change_control_generation_manifests",
    "change_control_active_generation",
    "change_control_managed_review_bundles",
    "change_control_managed_review_targets",
    "change_control_managed_review_request_records",
    "change_control_managed_review_request_delivery_receipts",
    "change_control_managed_review_decisions",
    "change_control_managed_review_decision_items",
    "change_control_managed_review_decision_delivery_receipts",
}
_EXPECTED_TABLES = _V2_EXPECTED_TABLES | _MANAGED_REVIEW_TABLES
_EXPECTED_TABLES_BY_VERSION = {
    1: _V1_EXPECTED_TABLES,
    2: _V2_EXPECTED_TABLES,
    3: _EXPECTED_TABLES,
}


class ChangeControlStoreError(RuntimeError):
    """Base error for the dedicated change-control store."""


class ChangeControlConflictError(ChangeControlStoreError):
    """The aggregate revision differs from the caller's expected revision."""


class ChangeControlIdempotencyError(ChangeControlStoreError):
    """An operation ID was reused for different inputs."""


class ChangeControlCorruptionError(ChangeControlStoreError):
    """Persisted state failed schema, relational, or canonical-domain validation."""


class ChangeControlBusyError(ChangeControlStoreError):
    """SQLite could not acquire or retain the requested lock before timeout."""


class ChangeControlReviewMissingError(ChangeControlStoreError):
    """The requested authoritative review record does not exist."""


class ChangeControlReviewStaleError(ChangeControlStoreError):
    """The review request no longer binds the live aggregate head."""


class ChangeControlReviewTransitionError(ChangeControlStoreError):
    """A reviewed-state transition is absent, unsupported, or impermissible."""


class ChangeControlReviewAlreadyDecidedError(ChangeControlStoreError):
    """The review request already has an immutable decision."""


@dataclass(frozen=True)
class ChangeControlSnapshot:
    aggregate: ChangeControlAggregate
    revision: int
    aggregate_sha256: str


@dataclass(frozen=True)
class ChangeControlCommit:
    aggregate_id: str
    revision: int
    aggregate_sha256: str
    changed: bool
    committed_at: str
    replayed: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require_operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "operation_id must be 1-200 ASCII characters: an alphanumeric first "
            "character followed only by alphanumerics, dot, underscore, colon, slash, or hyphen"
        )
    return value


def _receipt_sha256(
    *,
    operation_id: str,
    aggregate_id: str,
    expected_revision: int | None,
    aggregate_digest: str,
    committed_revision: int,
    changed: bool,
    committed_at: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "aggregate_id": aggregate_id,
                "aggregate_sha256": aggregate_digest,
                "changed": changed,
                "committed_at": committed_at,
                "committed_revision": committed_revision,
                "expected_revision": expected_revision,
                "operation_id": operation_id,
            }
        )
    ).hexdigest()


def _canonical_aggregate_json(aggregate: ChangeControlAggregate) -> str:
    return canonical_json_bytes(aggregate.model_dump(mode="json")).decode("utf-8")


def _decode_aggregate_json(payload_json: str) -> ChangeControlAggregate:
    payload = json.loads(payload_json)
    if canonical_json_bytes(payload).decode("utf-8") != payload_json:
        raise ValueError("persisted aggregate audit snapshot is not canonical JSON")
    aggregate = ChangeControlAggregate.model_validate(payload)
    if _canonical_aggregate_json(aggregate) != payload_json:
        raise ValueError("persisted aggregate audit snapshot is not typed-canonical JSON")
    return aggregate


def _require_canonical_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("timestamp is not canonical UTC")
    return value


def _is_busy_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.Error):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


def _evidence_payload(item: EvidenceRef | StructuralEvidenceRef) -> tuple[str, int, str]:
    if isinstance(item, StructuralEvidenceRef):
        kind, version = "structural-evidence-ref-v2", 2
    else:
        kind, version = "evidence-ref-v1", 1
    payload = canonical_json_bytes(item.model_dump(mode="json")).decode("utf-8")
    return kind, version, payload


def _decode_evidence(
    *, kind: str, version: int, payload_json: str
) -> EvidenceRef | StructuralEvidenceRef:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChangeControlCorruptionError("persisted evidence JSON is malformed") from exc
    if canonical_json_bytes(payload).decode("utf-8") != payload_json:
        raise ChangeControlCorruptionError("persisted evidence JSON is not canonical")
    try:
        if (kind, version) == ("evidence-ref-v1", 1):
            return EvidenceRef.model_validate(payload)
        if (kind, version) == ("structural-evidence-ref-v2", 2):
            return StructuralEvidenceRef.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ChangeControlCorruptionError("persisted evidence payload is invalid") from exc
    raise ChangeControlCorruptionError("persisted evidence discriminator is invalid")


def _require_contiguous(rows: list[sqlite3.Row], field: str = "ordinal") -> None:
    if [int(row[field]) for row in rows] != list(range(len(rows))):
        raise ChangeControlCorruptionError("persisted ordered rows are not contiguous from zero")


class SqliteChangeControlStore:
    """One synchronous SQLite connection for durable change-control state."""

    def __init__(
        self,
        db_path: Path | str,
        migrations_dir: Path | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        self.db_path = Path(db_path)
        self.migrations_dir = migrations_dir or _DEFAULT_MIGRATIONS_DIR
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=timeout_seconds)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def _migration_files(self) -> dict[int, Path]:
        files: dict[int, Path] = {}
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _MIGRATION_RE.fullmatch(path.name)
            if match is None:
                raise ChangeControlStoreError(f"invalid change-control migration: {path.name}")
            version = int(match.group("version"))
            if version in files:
                raise ChangeControlStoreError(f"duplicate change-control migration {version:03d}")
            files[version] = path
        expected = list(range(1, _SCHEMA_VERSION + 1))
        if sorted(files) != expected:
            raise ChangeControlStoreError(
                f"change-control migrations must be contiguous {expected}, found {sorted(files)}"
            )
        return files

    @staticmethod
    def _migration_checksum(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _sql_statements(sql: str) -> list[str]:
        statements: list[str] = []
        buffer = ""
        for line in sql.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                if statement:
                    statements.append(statement)
                buffer = ""
        if buffer.strip():
            raise ChangeControlStoreError("change-control migration has incomplete SQL")
        return statements

    def _user_tables(self) -> set[str]:
        return {
            str(row[0])
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _schema_sha256(self) -> str:
        try:
            rows = self.conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type, name"
            ).fetchall()
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError("cannot inspect change-control schema") from exc
        payload = [{"type": str(row[0]), "name": str(row[1]), "sql": str(row[2])} for row in rows]
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def _read_meta(self) -> dict[str, str] | None:
        if "change_control_meta" not in self._user_tables():
            return None
        try:
            return {
                str(row["key"]): str(row["value"])
                for row in self.conn.execute("SELECT key, value FROM change_control_meta")
            }
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError(
                "change-control schema metadata is unreadable"
            ) from exc

    def _validate_identity(self, *, require_current: bool = True) -> int:
        actual_tables = self._user_tables()
        if actual_tables - _EXPECTED_TABLES:
            raise ChangeControlCorruptionError(
                "change-control database object inventory does not match its schema version"
            )
        meta = self._read_meta()
        if meta is None or set(meta) != {
            "store_identity",
            "schema_version",
            "schema_sha256",
        }:
            raise ChangeControlCorruptionError(
                "change-control database has missing or incompatible schema identity"
            )
        try:
            version = int(meta["schema_version"])
        except (KeyError, ValueError) as exc:
            raise ChangeControlCorruptionError("change-control schema version is invalid") from exc
        if meta["store_identity"] != _STORE_IDENTITY or not 1 <= version <= _SCHEMA_VERSION:
            raise ChangeControlCorruptionError(
                "change-control database has missing or incompatible schema identity"
            )
        if actual_tables != _EXPECTED_TABLES_BY_VERSION[version]:
            raise ChangeControlCorruptionError(
                "change-control database object inventory does not match its schema version"
            )
        if _SHA256_RE.fullmatch(meta["schema_sha256"]) is None:
            raise ChangeControlCorruptionError("change-control schema fingerprint is invalid")
        if meta["schema_sha256"] != self._schema_sha256():
            raise ChangeControlCorruptionError("change-control live schema fingerprint is invalid")
        migrations = self._migration_files()
        try:
            rows = self.conn.execute(
                "SELECT version, name, checksum_sha256 FROM change_control_schema_migrations "
                "ORDER BY version"
            ).fetchall()
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError(
                "change-control migration ledger is missing"
            ) from exc
        try:
            actual = [(int(row[0]), str(row[1]), str(row[2])) for row in rows]
            expected = [
                (
                    item_version,
                    migrations[item_version].stem,
                    self._migration_checksum(migrations[item_version]),
                )
                for item_version in range(1, version + 1)
            ]
        except (IndexError, TypeError, ValueError) as exc:
            raise ChangeControlCorruptionError(
                "change-control migration ledger contains malformed values"
            ) from exc
        if actual != expected:
            raise ChangeControlCorruptionError(
                "change-control migration ledger does not match packaged history"
            )
        if require_current and version != _SCHEMA_VERSION:
            raise ChangeControlCorruptionError(
                "change-control database schema is not initialized to the current version"
            )
        return version

    def _begin(self, statement: str) -> None:
        try:
            self.conn.execute(statement)
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise ChangeControlBusyError("change-control SQLite database is busy") from exc
            raise ChangeControlCorruptionError(
                "change-control SQLite transaction could not start"
            ) from exc

    def _rollback_operation_error(self, exc: BaseException) -> None:
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
        if _is_busy_error(exc):
            raise ChangeControlBusyError("change-control SQLite database is busy") from exc
        if isinstance(exc, sqlite3.OperationalError):
            raise ChangeControlCorruptionError(
                "change-control SQLite schema or persisted state is unreadable"
            ) from exc

    def init_schema(self) -> None:
        if self.conn.in_transaction:
            raise ChangeControlStoreError("cannot initialize inside an existing transaction")
        migrations = self._migration_files()
        self._begin("BEGIN IMMEDIATE")
        try:
            meta = self._read_meta()
            if meta is None:
                existing = self._user_tables()
                if existing:
                    raise ChangeControlCorruptionError(
                        "refusing to adopt an unidentified SQLite database"
                    )
                version = 0
            else:
                version = self._validate_identity(require_current=False)
            for next_version in range(version + 1, _SCHEMA_VERSION + 1):
                path = migrations[next_version]
                for statement in self._sql_statements(path.read_text(encoding="utf-8")):
                    self.conn.execute(statement)
                if next_version == 1:
                    self.conn.executemany(
                        "INSERT INTO change_control_meta (key, value) VALUES (?, ?)",
                        (
                            ("store_identity", _STORE_IDENTITY),
                            ("schema_version", "1"),
                            ("schema_sha256", self._schema_sha256()),
                        ),
                    )
                else:
                    cursor = self.conn.execute(
                        "UPDATE change_control_meta SET value=? WHERE key='schema_version'",
                        (str(next_version),),
                    )
                    if cursor.rowcount != 1:
                        raise ChangeControlCorruptionError(
                            "change-control schema identity disappeared during migration"
                        )
                    cursor = self.conn.execute(
                        "UPDATE change_control_meta SET value=? WHERE key='schema_sha256'",
                        (self._schema_sha256(),),
                    )
                    if cursor.rowcount != 1:
                        raise ChangeControlCorruptionError(
                            "change-control schema fingerprint disappeared during migration"
                        )
                self.conn.execute(
                    "INSERT INTO change_control_schema_migrations "
                    "(version, name, checksum_sha256, applied_at) VALUES (?, ?, ?, ?)",
                    (next_version, path.stem, self._migration_checksum(path), _now()),
                )
            self._validate_identity()
            self.conn.execute("COMMIT")
        except BaseException as exc:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            if _is_busy_error(exc):
                raise ChangeControlBusyError("change-control SQLite database is busy") from exc
            raise

    def _require_ready(self) -> None:
        if self.conn.in_transaction:
            raise ChangeControlStoreError("change-control operations cannot join a transaction")

    def _deliver_commit(self, result: ChangeControlCommit) -> ChangeControlCommit:
        """Return a result only after its transaction has committed."""

        return result

    def _deliver_review_request(
        self, result: HumanReviewRequestReceipt
    ) -> HumanReviewRequestReceipt:
        """Return an authoritative request only after its transaction commits."""

        return result

    def _deliver_review_decision(
        self, result: HumanReviewDecisionReceipt
    ) -> HumanReviewDecisionReceipt:
        """Return an authoritative decision only after its transaction commits."""

        return result

    def create(
        self, aggregate: ChangeControlAggregate, *, operation_id: str
    ) -> ChangeControlCommit:
        return self.compare_and_swap(
            aggregate,
            expected_revision=None,
            operation_id=operation_id,
        )

    def load(self, aggregate_id: str) -> ChangeControlSnapshot | None:
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            head = self.conn.execute(
                "SELECT * FROM change_control_aggregates WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()
            rows = self._capture_rows(aggregate_id)
            self.conn.execute("COMMIT")
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise
        if head is None:
            if any(rows.values()):
                raise ChangeControlCorruptionError("aggregate children exist without a head")
            return None
        return self._hydrate_snapshot(head, rows)

    def _capture_rows(self, aggregate_id: str) -> dict[str, list[sqlite3.Row]]:
        tables = {
            "documents": ("change_control_document_versions", "document_version_id"),
            "identities": ("change_control_claim_identities", "claim_identity_id"),
            "claims": ("change_control_claim_revisions", "claim_revision_id"),
            "scopes": ("change_control_claim_scopes", "claim_revision_id, scope"),
            "claim_evidence": ("change_control_claim_evidence", "claim_revision_id, ordinal"),
            "pairs": ("change_control_claim_pairs", "pair_id"),
            "relations": ("change_control_relation_assessments", "pair_id"),
            "dependencies": ("change_control_dependencies", "dependency_id"),
            "spans": ("change_control_dependency_spans", "dependency_id, ordinal"),
            "span_evidence": (
                "change_control_dependency_span_evidence",
                "dependency_id, span_ordinal, ordinal",
            ),
            "dependency_claims": (
                "change_control_dependency_claims",
                "dependency_id, claim_revision_id",
            ),
            "replacements": ("change_control_document_replacements", "relation_id"),
            "constraints": ("change_control_temporal_constraints", "constraint_id"),
            "bases": (
                "change_control_temporal_constraint_bases",
                "constraint_id, ordinal",
            ),
        }
        captured: dict[str, list[sqlite3.Row]] = {}
        try:
            for key, (table, order) in tables.items():
                captured[key] = self.conn.execute(
                    f"SELECT * FROM {table} WHERE aggregate_id = ? ORDER BY {order}",
                    (aggregate_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError(
                "change-control aggregate tables are incomplete"
            ) from exc
        return captured

    def compare_and_swap(
        self,
        replacement: ChangeControlAggregate,
        *,
        expected_revision: int | None,
        operation_id: str,
    ) -> ChangeControlCommit:
        operation_id = _require_operation_id(operation_id)
        if expected_revision is not None and expected_revision < 1:
            raise ValueError("expected_revision must be positive or None")
        replacement = ChangeControlAggregate.model_validate(replacement.model_dump(mode="json"))
        digest = aggregate_sha256(replacement)
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            operation_owner = self._global_operation_owner(operation_id)
            if operation_owner is not None and operation_owner[0] != "legacy":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another authority"
                )
            head = self.conn.execute(
                "SELECT * FROM change_control_aggregates WHERE aggregate_id = ?",
                (replacement.aggregate_id,),
            ).fetchone()
            current = (
                self._hydrate_snapshot(
                    head,
                    self._capture_rows(replacement.aggregate_id),
                )
                if head is not None
                else None
            )
            receipt = self.conn.execute(
                "SELECT * FROM change_control_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if receipt is not None:
                if self._review_operation_owner(operation_id) is not None:
                    raise ChangeControlIdempotencyError(
                        "operation_id is owned by an authoritative review write"
                    )
                if (
                    str(receipt["aggregate_id"]) != replacement.aggregate_id
                    or receipt["expected_revision"] != expected_revision
                    or str(receipt["aggregate_sha256"]) != digest
                ):
                    raise ChangeControlIdempotencyError(
                        "operation_id was already used for different aggregate inputs"
                    )
                result = ChangeControlCommit(
                    aggregate_id=replacement.aggregate_id,
                    revision=int(receipt["committed_revision"]),
                    aggregate_sha256=digest,
                    changed=bool(receipt["changed"]),
                    committed_at=str(receipt["committed_at"]),
                    replayed=True,
                )
                self.conn.execute("COMMIT")
                return self._deliver_commit(result)

            if head is None:
                if expected_revision is not None:
                    raise ChangeControlConflictError("aggregate does not exist")
                self._assert_generic_proposed_only(replacement)
                current_revision = None
            else:
                current_revision = int(head["revision"])
                if current_revision != expected_revision:
                    raise ChangeControlConflictError(
                        f"expected revision {expected_revision}, found {current_revision}"
                    )
                assert current is not None
                self._assert_non_review_transition(current, replacement, current_revision)
                if current.aggregate_sha256 == digest:
                    committed_at = self._insert_receipt(
                        operation_id=operation_id,
                        aggregate_id=replacement.aggregate_id,
                        expected_revision=expected_revision,
                        digest=digest,
                        revision=current_revision,
                        changed=False,
                    )
                    self.conn.execute("COMMIT")
                    return self._deliver_commit(
                        ChangeControlCommit(
                            aggregate_id=replacement.aggregate_id,
                            revision=current_revision,
                            aggregate_sha256=digest,
                            changed=False,
                            committed_at=committed_at,
                        )
                    )

            revision = 1 if current_revision is None else current_revision + 1
            if current_revision is None:
                self.conn.execute(
                    "INSERT INTO change_control_aggregates "
                    "(aggregate_id, revision, model_schema_version, aggregate_sha256, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (replacement.aggregate_id, revision, _MODEL_SCHEMA_VERSION, digest, _now()),
                )
            else:
                for table in _AGGREGATE_TABLES_CHILD_FIRST:
                    self.conn.execute(
                        f"DELETE FROM {table} WHERE aggregate_id = ?",
                        (replacement.aggregate_id,),
                    )
            self._insert_aggregate_rows(replacement)
            if current_revision is not None:
                cursor = self.conn.execute(
                    "UPDATE change_control_aggregates SET revision=?, model_schema_version=?, "
                    "aggregate_sha256=?, updated_at=? WHERE aggregate_id=? AND revision=?",
                    (
                        revision,
                        _MODEL_SCHEMA_VERSION,
                        digest,
                        _now(),
                        replacement.aggregate_id,
                        current_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ChangeControlConflictError("aggregate revision changed during CAS")
            committed_at = self._insert_receipt(
                operation_id=operation_id,
                aggregate_id=replacement.aggregate_id,
                expected_revision=expected_revision,
                digest=digest,
                revision=revision,
                changed=True,
            )
            self.conn.execute("COMMIT")
            return self._deliver_commit(
                ChangeControlCommit(
                    aggregate_id=replacement.aggregate_id,
                    revision=revision,
                    aggregate_sha256=digest,
                    changed=True,
                    committed_at=committed_at,
                )
            )
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def _review_operation_owner(self, operation_id: str) -> str | None:
        request = self.conn.execute(
            "SELECT request_id FROM change_control_review_requests WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        decision = self.conn.execute(
            "SELECT request_id FROM change_control_review_decisions WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if request is not None and decision is not None:
            raise ChangeControlCorruptionError("review operation ID has multiple owners")
        if request is not None:
            return "request"
        if decision is not None:
            return "decision"
        return None

    def _global_operation_owner(self, operation_id: str) -> tuple[str, str] | None:
        """Resolve one operation ID across every v3 write authority."""

        matches: list[tuple[str, str]] = []
        queries = (
            ("legacy", "change_control_operations", "operation_id", "operation_id"),
            (
                "generation-zero",
                "change_control_active_generation",
                "initialization_operation_id",
                "aggregate_id",
            ),
            (
                "managed-request",
                "change_control_managed_review_request_records",
                "operation_id",
                "request_id",
            ),
            (
                "managed-decision",
                "change_control_managed_review_decisions",
                "operation_id",
                "request_id",
            ),
        )
        tables = self._user_tables()
        for owner, table, operation_column, identity_column in queries:
            if table not in tables:
                continue
            row = self.conn.execute(
                f"SELECT {identity_column} AS identity FROM {table} WHERE {operation_column}=?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                matches.append((owner, str(row["identity"])))
        if len(matches) > 1:
            raise ChangeControlCorruptionError("operation_id has multiple authoritative owners")
        return matches[0] if matches else None

    def _validate_global_operation_ownership(self) -> None:
        tables = self._user_tables()
        selects = ["SELECT operation_id, 'legacy' AS owner FROM change_control_operations"]
        if "change_control_active_generation" in tables:
            selects.append(
                "SELECT initialization_operation_id, 'generation-zero' "
                "FROM change_control_active_generation"
            )
        if "change_control_managed_review_request_records" in tables:
            selects.append(
                "SELECT operation_id, 'managed-request' "
                "FROM change_control_managed_review_request_records"
            )
        if "change_control_managed_review_decisions" in tables:
            selects.append(
                "SELECT operation_id, 'managed-decision' "
                "FROM change_control_managed_review_decisions"
            )
        rows = self.conn.execute(
            "SELECT operation_id, owner FROM ("
            + " UNION ALL ".join(selects)
            + ") ORDER BY operation_id, owner"
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            operation_id = str(row["operation_id"])
            if _require_operation_id(operation_id) != operation_id or operation_id in seen:
                raise ChangeControlCorruptionError(
                    "operation ownership is invalid or non-unique across authorities"
                )
            seen.add(operation_id)

    @staticmethod
    def _review_subject_map(aggregate: ChangeControlAggregate) -> dict[tuple[str, str], Any]:
        subjects: dict[tuple[str, str], Any] = {
            (ReviewSubjectKind.DOCUMENT_REPLACEMENT.value, item.relation_id): item
            for item in aggregate.document_replacements.assessments
        }
        subjects.update(
            {
                (ReviewSubjectKind.TEMPORAL_CONSTRAINT.value, item.constraint_id): item
                for item in aggregate.temporal_constraints.constraints
            }
        )
        return subjects

    @staticmethod
    def _assert_generic_proposed_only(aggregate: ChangeControlAggregate) -> None:
        if any(
            subject.status != TemporalConstraintStatus.PROPOSED
            for subject in SqliteChangeControlStore._review_subject_map(aggregate).values()
        ):
            raise ChangeControlReviewTransitionError(
                "generic aggregate writes may contain proposed review subjects only; "
                "accepted or rejected state requires an authoritative decision"
            )

    def _assert_non_review_transition(
        self,
        current: ChangeControlSnapshot,
        replacement: ChangeControlAggregate,
        current_revision: int,
    ) -> None:
        before = self._review_subject_map(current.aggregate)
        after = self._review_subject_map(replacement)
        reviewed = {TemporalConstraintStatus.ACCEPTED, TemporalConstraintStatus.REJECTED}
        for key, subject in before.items():
            next_subject = after.get(key)
            if subject.status in reviewed and next_subject != subject:
                raise ChangeControlReviewTransitionError(
                    "reviewed subjects are immutable outside an authoritative decision"
                )
            if (
                subject.status == TemporalConstraintStatus.PROPOSED
                and next_subject is not None
                and next_subject.status in reviewed
            ):
                raise ChangeControlReviewTransitionError(
                    "proposed subjects require an authoritative decision before review"
                )
        for key, subject in after.items():
            if key not in before and subject.status in reviewed:
                raise ChangeControlReviewTransitionError(
                    "new reviewed-state subjects require an authoritative decision"
                )

        bound = {
            (str(row["subject_kind"]), str(row["subject_id"]))
            for row in self.conn.execute(
                "SELECT s.subject_kind, s.subject_id "
                "FROM change_control_review_request_subjects s "
                "JOIN change_control_review_requests r ON r.request_id=s.request_id "
                "LEFT JOIN change_control_review_decisions d ON d.request_id=r.request_id "
                "WHERE r.aggregate_id=? AND r.base_revision=? AND d.request_id IS NULL",
                (replacement.aggregate_id, current_revision),
            )
        }
        if any(before.get(key) != after.get(key) for key in bound):
            raise ChangeControlReviewTransitionError(
                "an open review subject cannot be modified or removed by ordinary CAS"
            )

    def _snapshot_in_transaction(self, aggregate_id: str) -> ChangeControlSnapshot | None:
        head = self.conn.execute(
            "SELECT * FROM change_control_aggregates WHERE aggregate_id=?", (aggregate_id,)
        ).fetchone()
        rows = self._capture_rows(aggregate_id)
        if head is None:
            if any(rows.values()):
                raise ChangeControlCorruptionError("aggregate children exist without a head")
            return None
        return self._hydrate_snapshot(head, rows)

    def _read_review_request(self, request_id: str) -> HumanReviewRequest | None:
        row = self.conn.execute(
            "SELECT * FROM change_control_review_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        subject_rows = self.conn.execute(
            "SELECT * FROM change_control_review_request_subjects "
            "WHERE request_id=? ORDER BY ordinal",
            (request_id,),
        ).fetchall()
        _require_contiguous(subject_rows)
        subjects: list[ReviewSubjectSnapshot] = []
        for subject_row in subject_rows:
            payload_json = str(subject_row["payload_json"])
            payload = json.loads(payload_json)
            if canonical_json_bytes(payload).decode("utf-8") != payload_json:
                raise ValueError("review subject snapshot JSON is not canonical")
            subjects.append(
                snapshot_from_payload(
                    kind=ReviewSubjectKind(str(subject_row["subject_kind"])),
                    subject_id=str(subject_row["subject_id"]),
                    payload_schema_version=int(subject_row["payload_schema_version"]),
                    payload=payload,
                    sha256=str(subject_row["subject_sha256"]),
                )
            )
            if snapshot_payload_json(subjects[-1]) != payload_json:
                raise ValueError("review subject snapshot is not typed-canonical JSON")
        return HumanReviewRequest(
            request_id=str(row["request_id"]),
            operation_id=str(row["operation_id"]),
            aggregate_id=str(row["aggregate_id"]),
            base_revision=int(row["base_revision"]),
            base_aggregate_sha256=str(row["base_aggregate_sha256"]),
            base_aggregate=_decode_aggregate_json(str(row["base_aggregate_json"])),
            subjects=tuple(subjects),
            requester_id=str(row["requester_id"]),
            rationale=str(row["rationale"]),
            requested_at=_require_canonical_utc(str(row["requested_at"])),
            request_payload_sha256=str(row["request_payload_sha256"]),
        )

    def _read_review_decision(self, request_id: str) -> HumanReviewDecision | None:
        row = self.conn.execute(
            "SELECT * FROM change_control_review_decisions WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        item_rows = self.conn.execute(
            "SELECT * FROM change_control_review_decision_items "
            "WHERE request_id=? ORDER BY ordinal",
            (request_id,),
        ).fetchall()
        _require_contiguous(item_rows)
        items = tuple(
            ReviewDecisionItem(
                kind=ReviewSubjectKind(str(item["subject_kind"])),
                subject_id=str(item["subject_id"]),
                original_subject_sha256=str(item["original_subject_sha256"]),
                disposition=ReviewDisposition(str(item["disposition"])),
                edit=(
                    ReviewSubjectEdit(
                        kind=ReviewSubjectKind(str(item["subject_kind"])),
                        subject_id=str(item["subject_id"]),
                        rationale=(
                            str(item["edit_rationale"])
                            if item["edit_rationale"] is not None
                            else None
                        ),
                        confidence=(
                            float(item["edit_confidence"])
                            if item["edit_confidence"] is not None
                            else None
                        ),
                    )
                    if item["disposition"] == ReviewDisposition.EDITED.value
                    else None
                ),
            )
            for item in item_rows
        )
        return HumanReviewDecision(
            request_id=str(row["request_id"]),
            operation_id=str(row["operation_id"]),
            reviewer_id=str(row["reviewer_id"]),
            rationale=str(row["rationale"]),
            items=items,
            decision_payload_sha256=str(row["decision_payload_sha256"]),
            decided_revision=int(row["decided_revision"]),
            decided_aggregate_sha256=str(row["decided_aggregate_sha256"]),
            decided_aggregate=_decode_aggregate_json(str(row["decided_aggregate_json"])),
            decided_at=_require_canonical_utc(str(row["decided_at"])),
        )

    def _review_lifecycle(self, request: HumanReviewRequest) -> ReviewLifecycle:
        if (
            self.conn.execute(
                "SELECT 1 FROM change_control_review_decisions WHERE request_id=?",
                (request.request_id,),
            ).fetchone()
            is not None
        ):
            return ReviewLifecycle.DECIDED
        head = self.conn.execute(
            "SELECT revision, aggregate_sha256 FROM change_control_aggregates WHERE aggregate_id=?",
            (request.aggregate_id,),
        ).fetchone()
        if (
            head is not None
            and int(head["revision"]) == request.base_revision
            and str(head["aggregate_sha256"]) == request.base_aggregate_sha256
        ):
            return ReviewLifecycle.OPEN
        return ReviewLifecycle.STALE

    @staticmethod
    def _request_matches_command(
        request: HumanReviewRequest, command: HumanReviewRequestCommand
    ) -> bool:
        return (
            request.aggregate_id == command.aggregate_id
            and request.base_revision == command.expected_revision
            and request.base_aggregate_sha256 == command.expected_aggregate_sha256
            and request.requester_id == command.requester_id
            and request.rationale == command.rationale
            and tuple((item.kind, item.subject_id) for item in request.subjects)
            == tuple((item.kind, item.subject_id) for item in command.subjects)
        )

    def create_review_request(
        self,
        command: HumanReviewRequestCommand,
        *,
        operation_id: str,
    ) -> HumanReviewRequestReceipt:
        operation_id = _require_operation_id(operation_id)
        command = HumanReviewRequestCommand.model_validate_json(command.model_dump_json())
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            operation_owner = self._global_operation_owner(operation_id)
            if operation_owner is not None and operation_owner[0] != "legacy":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another authority"
                )
            receipt = self.conn.execute(
                "SELECT * FROM change_control_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if receipt is not None:
                owner = self._review_operation_owner(operation_id)
                if owner != "request":
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                row = self.conn.execute(
                    "SELECT request_id FROM change_control_review_requests WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                assert row is not None
                existing = self._read_review_request(str(row["request_id"]))
                assert existing is not None
                if not self._request_matches_command(existing, command):
                    raise ChangeControlIdempotencyError(
                        "review request operation_id was reused for different inputs"
                    )
                result = HumanReviewRequestReceipt(
                    request=existing,
                    lifecycle=self._review_lifecycle(existing),
                    replayed=True,
                )
                self.conn.execute("COMMIT")
                return self._deliver_review_request(result)

            current = self._snapshot_in_transaction(command.aggregate_id)
            if current is None:
                raise ChangeControlReviewMissingError("review aggregate does not exist")
            if (
                current.revision != command.expected_revision
                or current.aggregate_sha256 != command.expected_aggregate_sha256
            ):
                raise ChangeControlConflictError(
                    "review request base revision or aggregate SHA differs from the live head"
                )
            snapshots: list[ReviewSubjectSnapshot] = []
            for ref in command.subjects:
                subject = subject_from_aggregate(current.aggregate, ref)
                if subject is None:
                    raise ChangeControlReviewMissingError(
                        f"review subject does not exist: {ref.kind.value}/{ref.subject_id}"
                    )
                if subject.status != TemporalConstraintStatus.PROPOSED:
                    raise ChangeControlReviewTransitionError(
                        "only proposed document replacements and temporal constraints are reviewable"
                    )
                snapshots.append(ReviewSubjectSnapshot.create(ref.kind, subject))
            ordered_snapshots = tuple(
                sorted(snapshots, key=lambda item: (item.kind.value, item.subject_id))
            )
            request_id = human_review_request_id(
                aggregate_id=command.aggregate_id,
                base_revision=current.revision,
                base_aggregate_sha256=current.aggregate_sha256,
                subjects=ordered_snapshots,
            )
            if (
                self.conn.execute(
                    "SELECT 1 FROM change_control_review_requests WHERE request_id=?", (request_id,)
                ).fetchone()
                is not None
            ):
                raise ChangeControlIdempotencyError(
                    "logical review request already exists under another operation_id"
                )
            for snapshot in ordered_snapshots:
                if (
                    self.conn.execute(
                        "SELECT 1 FROM change_control_review_request_subjects "
                        "WHERE aggregate_id=? AND base_revision=? AND subject_kind=? AND subject_id=?",
                        (
                            command.aggregate_id,
                            current.revision,
                            snapshot.kind.value,
                            snapshot.subject_id,
                        ),
                    ).fetchone()
                    is not None
                ):
                    raise ChangeControlReviewTransitionError(
                        "review request overlaps an existing subject at the same revision"
                    )
            requested_at = self._insert_receipt(
                operation_id=operation_id,
                aggregate_id=command.aggregate_id,
                expected_revision=current.revision,
                digest=current.aggregate_sha256,
                revision=current.revision,
                changed=False,
            )
            payload_sha = human_review_request_payload_sha256(
                request_id=request_id,
                operation_id=operation_id,
                requester_id=command.requester_id,
                rationale=command.rationale,
            )
            self.conn.execute(
                "INSERT INTO change_control_review_requests VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    operation_id,
                    command.aggregate_id,
                    current.revision,
                    current.aggregate_sha256,
                    1,
                    _canonical_aggregate_json(current.aggregate),
                    command.requester_id,
                    command.rationale,
                    requested_at,
                    payload_sha,
                ),
            )
            self.conn.executemany(
                "INSERT INTO change_control_review_request_subjects VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        request_id,
                        command.aggregate_id,
                        current.revision,
                        ordinal,
                        item.kind.value,
                        item.subject_id,
                        item.payload_schema_version,
                        snapshot_payload_json(item),
                        item.subject_sha256,
                    )
                    for ordinal, item in enumerate(ordered_snapshots)
                ],
            )
            request = HumanReviewRequest(
                request_id=request_id,
                operation_id=operation_id,
                aggregate_id=command.aggregate_id,
                base_revision=current.revision,
                base_aggregate_sha256=current.aggregate_sha256,
                base_aggregate=current.aggregate,
                subjects=ordered_snapshots,
                requester_id=command.requester_id,
                rationale=command.rationale,
                requested_at=requested_at,
                request_payload_sha256=payload_sha,
            )
            result = HumanReviewRequestReceipt(request=request, lifecycle=ReviewLifecycle.OPEN)
            self.conn.execute("COMMIT")
            return self._deliver_review_request(result)
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def get_review_request(self, request_id: str) -> HumanReviewRequestView:
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            request = self._read_review_request(request_id)
            if request is None:
                raise ChangeControlReviewMissingError("review request does not exist")
            decision = self._read_review_decision(request_id)
            view = HumanReviewRequestView(
                request=request,
                lifecycle=self._review_lifecycle(request),
                decision=decision,
            )
            self.conn.execute("COMMIT")
            return view
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    @staticmethod
    def _apply_review_decision(
        request: HumanReviewRequest,
        command: HumanReviewDecisionCommand,
    ) -> ChangeControlAggregate:
        try:
            return apply_human_review_decision(request, command)
        except ValueError as exc:
            raise ChangeControlReviewTransitionError(
                "review outcomes do not form one valid final aggregate"
            ) from exc

    def _replace_aggregate_in_transaction(
        self,
        replacement: ChangeControlAggregate,
        *,
        expected_revision: int,
        digest: str,
    ) -> int:
        for table in _AGGREGATE_TABLES_CHILD_FIRST:
            self.conn.execute(
                f"DELETE FROM {table} WHERE aggregate_id=?", (replacement.aggregate_id,)
            )
        self._insert_aggregate_rows(replacement)
        revision = expected_revision + 1
        cursor = self.conn.execute(
            "UPDATE change_control_aggregates SET revision=?, model_schema_version=?, "
            "aggregate_sha256=?, updated_at=? WHERE aggregate_id=? AND revision=?",
            (
                revision,
                _MODEL_SCHEMA_VERSION,
                digest,
                _now(),
                replacement.aggregate_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ChangeControlConflictError("aggregate revision changed during review decision")
        return revision

    def decide_review(
        self,
        command: HumanReviewDecisionCommand,
        *,
        operation_id: str,
    ) -> HumanReviewDecisionReceipt:
        operation_id = _require_operation_id(operation_id)
        command = HumanReviewDecisionCommand.model_validate_json(command.model_dump_json())
        payload_sha = human_review_decision_payload_sha256(command)
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            operation_owner = self._global_operation_owner(operation_id)
            if operation_owner is not None and operation_owner[0] != "legacy":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another authority"
                )
            receipt = self.conn.execute(
                "SELECT * FROM change_control_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if receipt is not None:
                owner = self._review_operation_owner(operation_id)
                if owner != "decision":
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                row = self.conn.execute(
                    "SELECT request_id FROM change_control_review_decisions WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                assert row is not None
                decision = self._read_review_decision(str(row["request_id"]))
                assert decision is not None
                if (
                    decision.request_id != command.request_id
                    or decision.decision_payload_sha256 != payload_sha
                ):
                    raise ChangeControlIdempotencyError(
                        "review decision operation_id was reused for different human inputs"
                    )
                result = HumanReviewDecisionReceipt(
                    decision=decision,
                    aggregate_revision=decision.decided_revision,
                    aggregate_sha256=decision.decided_aggregate_sha256,
                    replayed=True,
                )
                self.conn.execute("COMMIT")
                return self._deliver_review_decision(result)

            request = self._read_review_request(command.request_id)
            if request is None:
                raise ChangeControlReviewMissingError("review request does not exist")
            if self._read_review_decision(command.request_id) is not None:
                raise ChangeControlReviewAlreadyDecidedError(
                    "review request already has an immutable decision"
                )
            current = self._snapshot_in_transaction(request.aggregate_id)
            if (
                current is None
                or current.revision != request.base_revision
                or current.aggregate_sha256 != request.base_aggregate_sha256
            ):
                raise ChangeControlReviewStaleError(
                    "review request base no longer matches the live aggregate head"
                )
            replacement = self._apply_review_decision(request, command)
            replacement = ChangeControlAggregate.model_validate(replacement.model_dump(mode="json"))
            digest = aggregate_sha256(replacement)
            revision = self._replace_aggregate_in_transaction(
                replacement,
                expected_revision=request.base_revision,
                digest=digest,
            )
            decided_at = self._insert_receipt(
                operation_id=operation_id,
                aggregate_id=request.aggregate_id,
                expected_revision=request.base_revision,
                digest=digest,
                revision=revision,
                changed=True,
            )
            self.conn.execute(
                "INSERT INTO change_control_review_decisions VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.request_id,
                    operation_id,
                    request.aggregate_id,
                    request.base_revision,
                    command.reviewer_id,
                    command.rationale,
                    payload_sha,
                    revision,
                    digest,
                    1,
                    _canonical_aggregate_json(replacement),
                    decided_at,
                ),
            )
            self.conn.executemany(
                "INSERT INTO change_control_review_decision_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        request.request_id,
                        ordinal,
                        item.kind.value,
                        item.subject_id,
                        item.original_subject_sha256,
                        item.disposition.value,
                        item.edit.rationale if item.edit is not None else None,
                        item.edit.confidence if item.edit is not None else None,
                    )
                    for ordinal, item in enumerate(command.items)
                ],
            )
            decision = HumanReviewDecision(
                request_id=request.request_id,
                operation_id=operation_id,
                reviewer_id=command.reviewer_id,
                rationale=command.rationale,
                items=command.items,
                decision_payload_sha256=payload_sha,
                decided_revision=revision,
                decided_aggregate_sha256=digest,
                decided_aggregate=replacement,
                decided_at=decided_at,
            )
            result = HumanReviewDecisionReceipt(
                decision=decision,
                aggregate_revision=revision,
                aggregate_sha256=digest,
            )
            self.conn.execute("COMMIT")
            return self._deliver_review_decision(result)
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def _insert_receipt(
        self,
        *,
        operation_id: str,
        aggregate_id: str,
        expected_revision: int | None,
        digest: str,
        revision: int,
        changed: bool,
    ) -> str:
        committed_at = _now()
        receipt_digest = _receipt_sha256(
            operation_id=operation_id,
            aggregate_id=aggregate_id,
            expected_revision=expected_revision,
            aggregate_digest=digest,
            committed_revision=revision,
            changed=changed,
            committed_at=committed_at,
        )
        self.conn.execute(
            "INSERT INTO change_control_operations "
            "(operation_id, aggregate_id, expected_revision, aggregate_sha256, "
            "committed_revision, changed, committed_at, receipt_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                aggregate_id,
                expected_revision,
                digest,
                revision,
                int(changed),
                committed_at,
                receipt_digest,
            ),
        )
        return committed_at

    def _insert_aggregate_rows(self, aggregate: ChangeControlAggregate) -> None:
        aggregate_id = aggregate.aggregate_id
        self.conn.executemany(
            "INSERT INTO change_control_document_versions VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.document_version_id,
                    item.identity_namespace,
                    item.document_id,
                    item.document_family,
                    item.version_label,
                    item.source_path,
                    item.source_sha256,
                    item.declared_effective_from.isoformat(),
                    item.declared_effective_to.isoformat() if item.declared_effective_to else None,
                    item.role.value,
                    item.authority.value,
                )
                for item in aggregate.documents.documents
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_claim_identities VALUES (?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.claim_identity_id,
                    item.identity_namespace,
                    item.document.document_version_id,
                    item.source.source_claim_id,
                )
                for item in {
                    revision.claim_identity_id: revision for revision in aggregate.claims.revisions
                }.values()
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_claim_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.claim_revision_id,
                    item.claim_identity_id,
                    item.revision_namespace,
                    item.statement,
                    item.source.source_note_path,
                    item.source.source_note_sha256,
                    item.declared_effective_from.isoformat(),
                    item.declared_effective_to.isoformat() if item.declared_effective_to else None,
                )
                for item in aggregate.claims.revisions
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_claim_scopes VALUES (?, ?, ?)",
            [
                (aggregate_id, item.claim_revision_id, scope)
                for item in aggregate.claims.revisions
                for scope in item.scopes
            ],
        )
        claim_evidence: list[tuple[Any, ...]] = []
        for revision in aggregate.claims.revisions:
            for ordinal, evidence in enumerate(revision.source.evidence):
                kind, version, payload = _evidence_payload(evidence)
                claim_evidence.append(
                    (aggregate_id, revision.claim_revision_id, ordinal, kind, version, payload)
                )
        self.conn.executemany(
            "INSERT INTO change_control_claim_evidence VALUES (?, ?, ?, ?, ?, ?)",
            claim_evidence,
        )

        self.conn.executemany(
            "INSERT INTO change_control_claim_pairs VALUES (?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.pair.pair_id,
                    item.pair.identity_namespace,
                    item.pair.claim_revisions[0].claim_revision_id,
                    item.pair.claim_revisions[1].claim_revision_id,
                )
                for item in aggregate.relation_graph.assessments
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_relation_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.pair.pair_id,
                    item.disposition.value,
                    item.rationale,
                    item.confidence,
                    item.relation_type.value if item.relation_type else None,
                    item.relation_id,
                    item.endpoint_ids[0] if item.endpoint_ids else None,
                    item.endpoint_ids[1] if item.endpoint_ids else None,
                )
                for item in aggregate.relation_graph.assessments
            ],
        )

        self.conn.executemany(
            "INSERT INTO change_control_dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.dependency_id,
                    item.relation_type.value,
                    item.downstream.document_version_id,
                    item.upstream.claim_revision_id,
                    item.dependency_kind.value,
                    item.rationale,
                    item.confidence,
                )
                for item in aggregate.dependencies.assessments
            ],
        )
        span_rows: list[tuple[Any, ...]] = []
        span_evidence_rows: list[tuple[Any, ...]] = []
        for dependency in aggregate.dependencies.assessments:
            for span_ordinal, span in enumerate(dependency.downstream_spans):
                span_rows.append(
                    (
                        aggregate_id,
                        dependency.dependency_id,
                        span_ordinal,
                        span.document_version_id,
                        span.source_note_path,
                        span.source_note_sha256,
                        span.record_id,
                        span.quote,
                        span.start_char,
                        span.end_char,
                    )
                )
                for evidence_ordinal, evidence in enumerate(span.evidence):
                    kind, version, payload = _evidence_payload(evidence)
                    span_evidence_rows.append(
                        (
                            aggregate_id,
                            dependency.dependency_id,
                            span_ordinal,
                            evidence_ordinal,
                            kind,
                            version,
                            payload,
                        )
                    )
        self.conn.executemany(
            "INSERT INTO change_control_dependency_spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            span_rows,
        )
        self.conn.executemany(
            "INSERT INTO change_control_dependency_span_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
            span_evidence_rows,
        )
        self.conn.executemany(
            "INSERT INTO change_control_dependency_claims VALUES (?, ?, ?)",
            [
                (aggregate_id, item.dependency_id, revision.claim_revision_id)
                for item in aggregate.dependencies.assessments
                for revision in item.downstream_claim_revisions
            ],
        )

        self.conn.executemany(
            "INSERT INTO change_control_document_replacements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.relation_id,
                    item.identity_namespace,
                    item.relation_type.value,
                    item.newer_document.document_version_id,
                    item.older_document.document_version_id,
                    item.status.value,
                    item.rationale,
                    item.confidence,
                )
                for item in aggregate.document_replacements.assessments
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_temporal_constraints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.constraint_id,
                    item.identity_namespace,
                    item.resolver_version,
                    item.target.kind.value,
                    item.target.target_id
                    if item.target.kind == TemporalTargetKind.DOCUMENT_VERSION
                    else None,
                    item.target.target_id
                    if item.target.kind == TemporalTargetKind.CLAIM_REVISION
                    else None,
                    item.inferred_valid_to_exclusive.isoformat(),
                    item.status.value,
                    item.rationale,
                )
                for item in aggregate.temporal_constraints.constraints
            ],
        )
        self.conn.executemany(
            "INSERT INTO change_control_temporal_constraint_bases VALUES (?, ?, ?, ?, ?)",
            [
                (
                    aggregate_id,
                    item.constraint_id,
                    item.target.kind.value,
                    ordinal,
                    relation_id,
                )
                for item in aggregate.temporal_constraints.constraints
                for ordinal, relation_id in enumerate(item.basis_relation_ids)
            ],
        )

    def _hydrate_snapshot(
        self,
        head: sqlite3.Row,
        rows: dict[str, list[sqlite3.Row]],
    ) -> ChangeControlSnapshot:
        try:
            if int(head["model_schema_version"]) != _MODEL_SCHEMA_VERSION:
                raise ChangeControlCorruptionError("unsupported aggregate model schema version")
            aggregate_id = str(head["aggregate_id"])
            documents = self._hydrate_documents(rows["documents"])
            document_map = {item.document_version_id: item for item in documents}
            claims = self._hydrate_claims(rows, document_map)
            claim_map = {item.claim_revision_id: item for item in claims}
            relations = self._hydrate_relations(rows, claim_map)
            dependencies = self._hydrate_dependencies(rows, document_map, claim_map)
            replacements = self._hydrate_replacements(rows["replacements"], document_map)
            constraints = self._hydrate_constraints(rows, document_map, claim_map)
            aggregate = ChangeControlAggregate.create(
                aggregate_id=aggregate_id,
                documents=DocumentVersionRegistry.create(tuple(documents)),
                claims=ClaimRevisionRegistry.create(tuple(claims)),
                relation_graph=RelationGraph.create(tuple(relations)),
                dependencies=DependencyRegistry.create(tuple(dependencies)),
                document_replacements=DocumentReplacementSet.create(tuple(replacements)),
                temporal_constraints=TemporalConstraintSet.create(tuple(constraints)),
            )
            digest = aggregate_sha256(aggregate)
            if digest != str(head["aggregate_sha256"]):
                raise ChangeControlCorruptionError(
                    "aggregate digest does not match normalized rows"
                )
            revision = int(head["revision"])
            if revision < 1:
                raise ChangeControlCorruptionError("aggregate revision is invalid")
            return ChangeControlSnapshot(
                aggregate=aggregate,
                revision=revision,
                aggregate_sha256=digest,
            )
        except ChangeControlCorruptionError:
            raise
        except (IndexError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            raise ChangeControlCorruptionError(
                "persisted change-control rows violate canonical domain contracts"
            ) from exc

    @staticmethod
    def _hydrate_documents(rows: list[sqlite3.Row]) -> list[DocumentVersionMetadata]:
        documents: list[DocumentVersionMetadata] = []
        for row in rows:
            item = DocumentVersionMetadata.model_validate(
                {
                    "identity_namespace": row["identity_namespace"],
                    "document_version_id": row["document_version_id"],
                    "document_id": row["document_id"],
                    "document_family": row["document_family"],
                    "version_label": row["version_label"],
                    "source_path": row["source_path"],
                    "source_sha256": row["source_sha256"],
                    "declared_effective_from": row["declared_effective_from"],
                    "declared_effective_to": row["declared_effective_to"],
                    "role": row["role"],
                    "authority": row["authority"],
                }
            )
            documents.append(item)
        return documents

    @staticmethod
    def _hydrate_claims(
        rows: dict[str, list[sqlite3.Row]],
        documents: dict[str, DocumentVersionMetadata],
    ) -> list[VersionedClaimRevision]:
        identities = {str(row["claim_identity_id"]): row for row in rows["identities"]}
        scopes: dict[str, list[str]] = defaultdict(list)
        for row in rows["scopes"]:
            scopes[str(row["claim_revision_id"])].append(str(row["scope"]))
        evidence_rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows["claim_evidence"]:
            evidence_rows[str(row["claim_revision_id"])].append(row)

        revisions: list[VersionedClaimRevision] = []
        used_identities: set[str] = set()
        for row in rows["claims"]:
            identity_id = str(row["claim_identity_id"])
            identity = identities.get(identity_id)
            if identity is None:
                raise ChangeControlCorruptionError("claim revision has no identity row")
            used_identities.add(identity_id)
            document = documents.get(str(identity["document_version_id"]))
            if document is None:
                raise ChangeControlCorruptionError("claim identity has no document root")
            persisted_evidence = evidence_rows.get(str(row["claim_revision_id"]), [])
            _require_contiguous(persisted_evidence)
            evidence = tuple(
                _decode_evidence(
                    kind=str(item["evidence_type"]),
                    version=int(item["payload_schema_version"]),
                    payload_json=str(item["payload_json"]),
                )
                for item in persisted_evidence
            )
            source = ClaimSourceReference(
                source_note_path=str(row["source_note_path"]),
                source_note_sha256=str(row["source_note_sha256"]),
                source_claim_id=str(identity["source_claim_id"]),
                evidence=evidence,
            )
            created = VersionedClaimRevision.create(
                document=document,
                source=source,
                statement=str(row["statement"]),
                declared_effective_from=date.fromisoformat(str(row["declared_effective_from"])),
                declared_effective_to=(
                    date.fromisoformat(str(row["declared_effective_to"]))
                    if row["declared_effective_to"] is not None
                    else None
                ),
                scopes=tuple(scopes.get(str(row["claim_revision_id"]), [])),
            )
            if (
                created.claim_identity_id != identity_id
                or created.claim_revision_id != row["claim_revision_id"]
                or created.identity_namespace != identity["identity_namespace"]
                or created.revision_namespace != row["revision_namespace"]
            ):
                raise ChangeControlCorruptionError("claim stable ID or namespace is invalid")
            revisions.append(created)
        if used_identities != set(identities):
            raise ChangeControlCorruptionError("unreferenced claim identity rows exist")
        known_claim_ids = {item.claim_revision_id for item in revisions}
        if set(scopes) - known_claim_ids or set(evidence_rows) - known_claim_ids:
            raise ChangeControlCorruptionError("claim child rows reference absent revisions")
        return revisions

    @staticmethod
    def _hydrate_relations(
        rows: dict[str, list[sqlite3.Row]],
        claims: dict[str, VersionedClaimRevision],
    ) -> list[RelationAssessment]:
        pair_rows = {str(row["pair_id"]): row for row in rows["pairs"]}
        assessments: list[RelationAssessment] = []
        used_pairs: set[str] = set()
        for row in rows["relations"]:
            pair_id = str(row["pair_id"])
            pair_row = pair_rows.get(pair_id)
            if pair_row is None:
                raise ChangeControlCorruptionError("relation assessment has no pair row")
            used_pairs.add(pair_id)
            first = claims.get(str(pair_row["left_claim_revision_id"]))
            second = claims.get(str(pair_row["right_claim_revision_id"]))
            if first is None or second is None:
                raise ChangeControlCorruptionError("claim pair has an absent claim root")
            pair = ComparableClaimPair.create(first, second)
            if pair.pair_id != pair_id or pair.identity_namespace != pair_row["identity_namespace"]:
                raise ChangeControlCorruptionError("claim pair stable ID or namespace is invalid")
            disposition = PairDisposition(str(row["disposition"]))
            newer = (
                str(row["endpoint_first_id"]) if disposition == PairDisposition.SUPERSEDES else None
            )
            created = RelationAssessment.create(
                pair=pair,
                disposition=disposition,
                rationale=str(row["rationale"]),
                confidence=float(row["confidence"]),
                newer_revision_id=newer,
            )
            persisted_endpoints = (
                (str(row["endpoint_first_id"]), str(row["endpoint_second_id"]))
                if row["endpoint_first_id"] is not None
                else None
            )
            if (
                (created.relation_type.value if created.relation_type else None)
                != row["relation_type"]
                or created.relation_id != row["relation_id"]
                or created.endpoint_ids != persisted_endpoints
            ):
                raise ChangeControlCorruptionError("relation assessment columns are inconsistent")
            assessments.append(created)
        if used_pairs != set(pair_rows):
            raise ChangeControlCorruptionError("unreferenced claim pair rows exist")
        return assessments

    @staticmethod
    def _hydrate_dependencies(
        rows: dict[str, list[sqlite3.Row]],
        documents: dict[str, DocumentVersionMetadata],
        claims: dict[str, VersionedClaimRevision],
    ) -> list[DependencyAssessment]:
        spans: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows["spans"]:
            spans[str(row["dependency_id"])].append(row)
        span_evidence: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
        for row in rows["span_evidence"]:
            span_evidence[(str(row["dependency_id"]), int(row["span_ordinal"]))].append(row)
        downstream_claim_ids: dict[str, list[str]] = defaultdict(list)
        for row in rows["dependency_claims"]:
            downstream_claim_ids[str(row["dependency_id"])].append(str(row["claim_revision_id"]))

        dependencies: list[DependencyAssessment] = []
        known_dependency_ids: set[str] = set()
        for row in rows["dependencies"]:
            dependency_id = str(row["dependency_id"])
            known_dependency_ids.add(dependency_id)
            downstream = documents.get(str(row["downstream_document_version_id"]))
            upstream = claims.get(str(row["upstream_claim_revision_id"]))
            if downstream is None or upstream is None:
                raise ChangeControlCorruptionError("dependency has an absent endpoint root")
            persisted_spans = spans.get(dependency_id, [])
            _require_contiguous(persisted_spans)
            hydrated_spans: list[DocumentSpanReference] = []
            for span_row in persisted_spans:
                key = (dependency_id, int(span_row["ordinal"]))
                evidence_rows = span_evidence.get(key, [])
                _require_contiguous(evidence_rows)
                hydrated_spans.append(
                    DocumentSpanReference(
                        document_version_id=span_row["document_version_id"],
                        source_note_path=span_row["source_note_path"],
                        source_note_sha256=span_row["source_note_sha256"],
                        record_id=span_row["record_id"],
                        quote=span_row["quote"],
                        start_char=span_row["start_char"],
                        end_char=span_row["end_char"],
                        evidence=tuple(
                            _decode_evidence(
                                kind=str(item["evidence_type"]),
                                version=int(item["payload_schema_version"]),
                                payload_json=str(item["payload_json"]),
                            )
                            for item in evidence_rows
                        ),
                    )
                )
            downstream_claims: list[VersionedClaimRevision] = []
            for claim_id in downstream_claim_ids.get(dependency_id, []):
                claim = claims.get(claim_id)
                if claim is None:
                    raise ChangeControlCorruptionError(
                        "dependency binding has an absent claim root"
                    )
                downstream_claims.append(claim)
            created = DependencyAssessment.create(
                downstream=downstream,
                upstream=upstream,
                dependency_kind=DependencyKind(str(row["dependency_kind"])),
                downstream_spans=tuple(hydrated_spans),
                downstream_claim_revisions=tuple(downstream_claims),
                rationale=str(row["rationale"]),
                confidence=float(row["confidence"]),
            )
            if (
                created.dependency_id != dependency_id
                or created.relation_type.value != row["relation_type"]
                or created.downstream_spans != tuple(hydrated_spans)
                or created.downstream_claim_revisions != tuple(downstream_claims)
            ):
                raise ChangeControlCorruptionError("dependency columns or ordering are invalid")
            dependencies.append(created)
        child_ids = set(spans) | set(downstream_claim_ids) | {key[0] for key in span_evidence}
        if child_ids - known_dependency_ids:
            raise ChangeControlCorruptionError("dependency child rows reference absent roots")
        return dependencies

    @staticmethod
    def _hydrate_replacements(
        rows: list[sqlite3.Row],
        documents: dict[str, DocumentVersionMetadata],
    ) -> list[DocumentReplacementAssessment]:
        replacements: list[DocumentReplacementAssessment] = []
        for row in rows:
            newer = documents.get(str(row["newer_document_version_id"]))
            older = documents.get(str(row["older_document_version_id"]))
            if newer is None or older is None:
                raise ChangeControlCorruptionError("document replacement has an absent root")
            created = DocumentReplacementAssessment.create(
                newer_document=newer,
                older_document=older,
                status=TemporalConstraintStatus(str(row["status"])),
                rationale=str(row["rationale"]),
                confidence=float(row["confidence"]),
            )
            if (
                created.relation_id != row["relation_id"]
                or created.identity_namespace != row["identity_namespace"]
                or created.relation_type.value != row["relation_type"]
            ):
                raise ChangeControlCorruptionError(
                    "document replacement stable columns are invalid"
                )
            replacements.append(created)
        return replacements

    @staticmethod
    def _hydrate_constraints(
        rows: dict[str, list[sqlite3.Row]],
        documents: dict[str, DocumentVersionMetadata],
        claims: dict[str, VersionedClaimRevision],
    ) -> list[TemporalConstraint]:
        bases: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows["bases"]:
            bases[str(row["constraint_id"])].append(row)
        constraints: list[TemporalConstraint] = []
        known_ids: set[str] = set()
        for row in rows["constraints"]:
            constraint_id = str(row["constraint_id"])
            known_ids.add(constraint_id)
            target_kind = TemporalTargetKind(str(row["target_kind"]))
            if target_kind == TemporalTargetKind.DOCUMENT_VERSION:
                target_id = str(row["target_document_version_id"])
                if row["target_claim_revision_id"] is not None or target_id not in documents:
                    raise ChangeControlCorruptionError("temporal document target is invalid")
            else:
                target_id = str(row["target_claim_revision_id"])
                if row["target_document_version_id"] is not None or target_id not in claims:
                    raise ChangeControlCorruptionError("temporal claim target is invalid")
            basis_rows = bases.get(constraint_id, [])
            _require_contiguous(basis_rows)
            if any(str(item["target_kind"]) != target_kind.value for item in basis_rows):
                raise ChangeControlCorruptionError("temporal basis target kind is inconsistent")
            constraint = TemporalConstraint.model_validate(
                {
                    "identity_namespace": row["identity_namespace"],
                    "resolver_version": row["resolver_version"],
                    "constraint_id": constraint_id,
                    "target": {"kind": target_kind.value, "target_id": target_id},
                    "inferred_valid_to_exclusive": row["inferred_valid_to_exclusive"],
                    "basis_relation_ids": tuple(
                        str(item["basis_relation_id"]) for item in basis_rows
                    ),
                    "status": row["status"],
                    "rationale": row["rationale"],
                }
            )
            constraints.append(constraint)
        if set(bases) - known_ids:
            raise ChangeControlCorruptionError("temporal bases reference absent constraints")
        return constraints

    def close(self) -> None:
        self.conn.close()

    def _assert_foreign_keys(self) -> None:
        if self.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ChangeControlCorruptionError("change-control database has broken foreign keys")

    def _validate_receipts(self) -> None:
        try:
            heads = {
                str(row["aggregate_id"]): int(row["revision"])
                for row in self.conn.execute(
                    "SELECT aggregate_id, revision FROM change_control_aggregates"
                )
            }
            receipts = self.conn.execute(
                "SELECT * FROM change_control_operations ORDER BY operation_id"
            ).fetchall()
            for row in receipts:
                operation_id = str(row["operation_id"])
                if _require_operation_id(operation_id) != operation_id:
                    raise ValueError("noncanonical operation ID")
                aggregate_id = str(row["aggregate_id"])
                if normalize_logical_key(aggregate_id) != aggregate_id:
                    raise ValueError("noncanonical aggregate ID")
                aggregate_digest = str(row["aggregate_sha256"])
                receipt_digest = str(row["receipt_sha256"])
                if (
                    _SHA256_RE.fullmatch(aggregate_digest) is None
                    or _SHA256_RE.fullmatch(receipt_digest) is None
                ):
                    raise ValueError("invalid receipt SHA")
                expected = row["expected_revision"]
                expected_revision = int(expected) if expected is not None else None
                committed_revision = int(row["committed_revision"])
                changed_raw = row["changed"]
                if changed_raw not in (0, 1):
                    raise ValueError("invalid changed flag")
                changed = bool(changed_raw)
                committed_at = str(row["committed_at"])
                parsed_at = datetime.fromisoformat(committed_at)
                utc_offset = parsed_at.utcoffset()
                if (
                    utc_offset is None
                    or utc_offset.total_seconds() != 0
                    or parsed_at.isoformat(timespec="seconds") != committed_at
                ):
                    raise ValueError("receipt timestamp is not canonical UTC")
                head_revision = heads.get(aggregate_id)
                if head_revision is None or committed_revision > head_revision:
                    raise ValueError("receipt does not resolve to a current aggregate head")
                if expected_revision is None:
                    valid_shape = changed and committed_revision == 1
                elif changed:
                    valid_shape = committed_revision == expected_revision + 1
                else:
                    valid_shape = committed_revision == expected_revision
                if not valid_shape:
                    raise ValueError("receipt revision transition is invalid")
                expected_digest = _receipt_sha256(
                    operation_id=operation_id,
                    aggregate_id=aggregate_id,
                    expected_revision=expected_revision,
                    aggregate_digest=aggregate_digest,
                    committed_revision=committed_revision,
                    changed=changed,
                    committed_at=committed_at,
                )
                if receipt_digest != expected_digest:
                    raise ValueError("receipt digest does not match immutable fields")
        except ChangeControlCorruptionError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError("persisted operation receipt is invalid") from exc
        except (IndexError, TypeError, ValueError) as exc:
            raise ChangeControlCorruptionError("persisted operation receipt is invalid") from exc

    def _validate_reviews(self) -> None:
        try:
            request_rows = self.conn.execute(
                "SELECT * FROM change_control_review_requests ORDER BY request_id"
            ).fetchall()
            heads = {
                str(row["aggregate_id"]): (
                    int(row["revision"]),
                    str(row["aggregate_sha256"]),
                )
                for row in self.conn.execute(
                    "SELECT aggregate_id, revision, aggregate_sha256 FROM change_control_aggregates"
                )
            }
            live_cache: dict[str, ChangeControlSnapshot] = {}
            for row in request_rows:
                request_id = str(row["request_id"])
                if int(row["base_aggregate_schema_version"]) != _MODEL_SCHEMA_VERSION:
                    raise ValueError("unsupported review request aggregate schema version")
                request = self._read_review_request(request_id)
                if request is None:
                    raise ValueError("review request disappeared during validation")
                for snapshot in request.subjects:
                    ref = ReviewSubjectRef(kind=snapshot.kind, subject_id=snapshot.subject_id)
                    subject = subject_from_aggregate(request.base_aggregate, ref)
                    if subject != snapshot.subject:
                        raise ValueError(
                            "review subject snapshot does not match the base aggregate"
                        )
                    if subject.status != TemporalConstraintStatus.PROPOSED:
                        raise ValueError("review request snapshot is not proposed")

                request_receipt = self.conn.execute(
                    "SELECT * FROM change_control_operations WHERE operation_id=?",
                    (request.operation_id,),
                ).fetchone()
                if request_receipt is None or not (
                    str(request_receipt["aggregate_id"]) == request.aggregate_id
                    and int(request_receipt["expected_revision"]) == request.base_revision
                    and str(request_receipt["aggregate_sha256"]) == request.base_aggregate_sha256
                    and int(request_receipt["committed_revision"]) == request.base_revision
                    and request_receipt["changed"] == 0
                    and str(request_receipt["committed_at"]) == request.requested_at
                ):
                    raise ValueError("review request does not match its generic receipt")

                decision = self._read_review_decision(request_id)
                head_binding = heads.get(request.aggregate_id)
                if decision is None:
                    if head_binding == (
                        request.base_revision,
                        request.base_aggregate_sha256,
                    ):
                        live = live_cache.get(request.aggregate_id)
                        if live is None:
                            loaded = self._snapshot_in_transaction(request.aggregate_id)
                            if loaded is None:
                                raise ValueError("review request live aggregate is absent")
                            live_cache[request.aggregate_id] = loaded
                            live = loaded
                        if live.aggregate != request.base_aggregate:
                            raise ValueError(
                                "open review base snapshot differs from the live aggregate"
                            )
                    continue

                decision_row = self.conn.execute(
                    "SELECT decided_aggregate_schema_version "
                    "FROM change_control_review_decisions WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if (
                    decision_row is None
                    or int(decision_row["decided_aggregate_schema_version"])
                    != _MODEL_SCHEMA_VERSION
                ):
                    raise ValueError("unsupported review decision aggregate schema version")
                if decision.decided_aggregate.aggregate_id != request.aggregate_id:
                    raise ValueError("review decision aggregate ID differs from its request")
                command = HumanReviewDecisionCommand(
                    request_id=decision.request_id,
                    reviewer_id=decision.reviewer_id,
                    rationale=decision.rationale,
                    items=decision.items,
                )
                expected_aggregate = self._apply_review_decision(request, command)
                if expected_aggregate != decision.decided_aggregate:
                    raise ValueError(
                        "review decision aggregate contains an unrelated or missing mutation"
                    )
                if decision.decided_revision != request.base_revision + 1:
                    raise ValueError("review decision revision transition is invalid")
                decision_receipt = self.conn.execute(
                    "SELECT * FROM change_control_operations WHERE operation_id=?",
                    (decision.operation_id,),
                ).fetchone()
                if decision_receipt is None or not (
                    str(decision_receipt["aggregate_id"]) == request.aggregate_id
                    and int(decision_receipt["expected_revision"]) == request.base_revision
                    and str(decision_receipt["aggregate_sha256"])
                    == decision.decided_aggregate_sha256
                    and int(decision_receipt["committed_revision"]) == decision.decided_revision
                    and decision_receipt["changed"] == 1
                    and str(decision_receipt["committed_at"]) == decision.decided_at
                ):
                    raise ValueError("review decision does not match its generic receipt")
                if head_binding == (
                    decision.decided_revision,
                    decision.decided_aggregate_sha256,
                ):
                    live = live_cache.get(request.aggregate_id)
                    if live is None:
                        loaded = self._snapshot_in_transaction(request.aggregate_id)
                        if loaded is None:
                            raise ValueError("review decision live aggregate is absent")
                        live_cache[request.aggregate_id] = loaded
                        live = loaded
                    if live.aggregate != decision.decided_aggregate:
                        raise ValueError(
                            "current review decision snapshot differs from the live aggregate"
                        )
        except ChangeControlCorruptionError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise
            raise ChangeControlCorruptionError(
                "persisted authoritative review audit is invalid"
            ) from exc
        except (
            ChangeControlReviewTransitionError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ChangeControlCorruptionError(
                "persisted authoritative review audit is invalid"
            ) from exc
