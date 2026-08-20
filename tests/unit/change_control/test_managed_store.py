from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from mastervault.change_control import managed_store as managed_store_module
from mastervault.change_control import store as store_module
from mastervault.change_control import workspace_bootstrap as workspace_bootstrap_module
from mastervault.change_control.analysis_binding import AnalysisBootstrapIntegrityError
from mastervault.change_control.bootstrap import (
    AnalysisBootstrapResult,
    VerifiedAnalysisBootstrapCapability,
    bootstrap_analysis_aggregate,
)
from mastervault.change_control.legacy_index import LegacyIndexAttestation
from mastervault.change_control.managed_activation_service import (
    activate_reviewed_managed_generation,
)
from mastervault.change_control.managed_generation import ManagedActivationCommand
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ClaimReconciliationAction,
    ClaimReconciliationBinding,
    ClaimReconciliationEntry,
    ContentAddressedInferenceReceipt,
    GroundedArtifactCitation,
    InferenceExecutionMode,
    InferenceUsage,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedBundleOutcome,
    ManagedGenerationManifestBindingV2,
    ManagedGoverningSourceAdoptionBinding,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedImpactBatchMemberBinding,
    ManagedImpactOutputRefBinding,
    ManagedInferenceContractBinding,
    ManagedReviewBaseBinding,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    ManagedRevisionPlanningBatchMemberBinding,
    ManagedRevisionPlanningTargetBinding,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewOutcome,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewTarget,
    ManagedRunBinding,
    ManagedRunBindingV2,
    ManagedSemanticHunk,
    NoChangeImpactCard,
    PatchReconstructionAttestation,
    PublicationDestination,
    PublicationKind,
    SourceNoteProjectionBinding,
    TargetAnalysisBinding,
    TemporalDecisionPrerequisite,
    derive_managed_successor,
)
from mastervault.change_control.managed_serving import (
    ManagedServingGenerationZeroError,
    open_active_managed_sqlite_index,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    ManagedGenerationActivationError,
    ManagedReviewAuthorityError,
    ManagedReviewStaleError,
    ManagedReviewWriteVersionError,
    ManagedRevisionEditDeferredError,
    ManagedRevisionStoreLifecycle,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    VersionedClaimRevision,
    canonical_json_bytes,
)
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    HumanReviewRequestCommand,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectKind,
    ReviewSubjectRef,
)
from mastervault.change_control.store import (
    _DEFAULT_MIGRATIONS_DIR,
    ChangeControlConflictError,
    ChangeControlCorruptionError,
    ChangeControlIdempotencyError,
    ChangeControlReviewAlreadyDecidedError,
    SqliteChangeControlStore,
)
from mastervault.change_control.workspace_bootstrap import (
    LegacyIndexExpectation,
    LegacyIndexReadinessReceipt,
    ManagedSourceNoteBootstrapMetadata,
    WorkspaceBootstrapIntent,
    WorkspaceBootstrapInventory,
    WorkspaceInventoryReceipt,
    WorkspaceNoteKind,
    WorkspaceVaultMember,
)
from mastervault.providers import MockEmbedding

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"


def _bootstrapped_store(path: Path):
    store = SqliteManagedChangeControlStore(path)
    store.init_schema()
    result = bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id="managed-store:prechange",
        analysis_operation_id="managed-store:analysis",
    )
    prechange_head = AggregateHeadBinding.create(
        aggregate_id=result.binding.aggregate_id,
        revision=result.binding.prechange_revision,
        aggregate_sha256=result.binding.prechange_aggregate_sha256,
    )
    return store, result, prechange_head


class _Resolver:
    def __init__(
        self,
        contract,
        manifest: bytes,
        artifacts: dict[str, bytes],
        *,
        approved_projection_ids: set[str] | None = None,
        impact_evidence: ManagedImpactAnalysisEvidenceBinding | None = None,
        revision_admission: ManagedRevisionPlanningAdmissionBinding | None = None,
        governing_source_adoption: ManagedGoverningSourceAdoptionBinding | None = None,
    ) -> None:
        self.contract = contract
        self.manifest = manifest
        self.artifacts = artifacts
        self.approved_projection_ids = approved_projection_ids or set()
        self.impact_evidence = impact_evidence
        self.revision_admission = revision_admission
        self.governing_source_adoption = governing_source_adoption

    def open_algorithm_manifest(self, binding):
        if binding != self.contract:
            raise ValueError("algorithm manifest binding is not approved")
        return self.manifest

    def resolve_approved_inference_contract(self, binding):
        return self.contract

    def resolve_impact_analysis_evidence(self, binding):
        if self.impact_evidence is None:
            raise ValueError("impact evidence is not approved")
        return self.impact_evidence

    def resolve_revision_planning_admission(self, binding):
        if self.revision_admission is None or self.revision_admission != binding:
            raise ValueError("revision admission is not approved")
        return self.revision_admission

    def resolve_governing_source_adoption(self, binding):
        if self.governing_source_adoption is None or self.governing_source_adoption != binding:
            raise ValueError("governing source adoption is not approved")
        return self.governing_source_adoption

    def open_artifact(self, artifact):
        return self.artifacts[artifact.path]

    def verify_patch_reconstruction(
        self, plan, *, base_bytes: bytes, result_bytes: bytes
    ) -> PatchReconstructionAttestation:
        rebuilt = bytearray()
        cursor = 0
        for hunk in plan.hunks:
            before = hunk.before_text.encode("utf-8")
            if base_bytes[hunk.start_byte : hunk.end_byte] != before:
                raise ValueError("patch hunk does not match base bytes")
            rebuilt.extend(base_bytes[cursor : hunk.start_byte])
            rebuilt.extend(hunk.replacement_text.encode("utf-8"))
            cursor = hunk.end_byte
        rebuilt.extend(base_bytes[cursor:])
        if bytes(rebuilt) != result_bytes:
            raise ValueError("patch hunks do not reconstruct result bytes")
        return PatchReconstructionAttestation.create_from_verifier_output(
            base_artifact=plan.predecessor_raw,
            result_artifact=plan.proposed_raw,
            hunks=plan.hunks,
            complete_diff_sha256=plan.patch_attestation.complete_diff_sha256,
        )

    def verify_source_note_projection(
        self, projection, *, raw_bytes: bytes, note_bytes: bytes
    ) -> SourceNoteProjectionBinding:
        if (
            self.approved_projection_ids
            and projection.projection_id not in self.approved_projection_ids
        ):
            raise ValueError("SourceNote projection was not validator-approved")
        assert hashlib.sha256(raw_bytes).hexdigest() == projection.raw_artifact.sha256
        assert hashlib.sha256(note_bytes).hexdigest() == projection.note_artifact.sha256
        return projection

    def verify_revision_plan_source_note(
        self,
        plan,
        *,
        predecessor_note_bytes: bytes,
        result_raw_bytes: bytes,
        proposed_note_bytes: bytes,
    ) -> SourceNoteProjectionBinding:
        del predecessor_note_bytes
        assert hashlib.sha256(result_raw_bytes).hexdigest() == plan.proposed_raw.sha256
        assert hashlib.sha256(proposed_note_bytes).hexdigest() == plan.proposed_note.sha256
        return self.verify_source_note_projection(
            plan.successor_projection,
            raw_bytes=result_raw_bytes,
            note_bytes=proposed_note_bytes,
        )


@dataclass(frozen=True)
class _Scenario:
    store: SqliteManagedChangeControlStore
    bootstrap: AnalysisBootstrapResult
    prechange_head: AggregateHeadBinding
    authority: AuthorityRevisionBinding
    resolver: _Resolver
    bundle: ManagedRevisionReviewBundle
    request_command: ManagedRevisionReviewRequestCommand


def _artifact(kind: ManagedArtifactKind, path: str, payload: bytes) -> ManagedArtifactRef:
    return ManagedArtifactRef.create(
        kind=kind,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
    )


def _synthetic_governing_source_adoption(
    *,
    bootstrap,
    evidence_repository_id: str,
    document,
    raw_artifact: ManagedArtifactRef,
    note_artifact: ManagedArtifactRef,
    source_note_logical_path: str,
    reviewed_head: AggregateHeadBinding,
    temporal_decision_record_sha256: str,
) -> ManagedGoverningSourceAdoptionBinding:
    note_snapshot_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "document": document.model_dump(mode="json"),
                "path": source_note_logical_path,
                "sha256": note_artifact.sha256,
            }
        )
    ).hexdigest()
    reviewed_binding_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "head": reviewed_head.model_dump(mode="json"),
                "temporal_decision_record_sha256": temporal_decision_record_sha256,
            }
        )
    ).hexdigest()
    inventory_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "document_version_id": document.document_version_id,
                "source_note_snapshot_sha256": note_snapshot_sha,
            }
        )
    ).hexdigest()
    return ManagedGoverningSourceAdoptionBinding.create(
        evidence_repository_id=evidence_repository_id,
        analysis_bootstrap_binding_id=bootstrap.binding_id,
        analysis_bootstrap_binding_sha256=bootstrap.binding_sha256,
        incoming_logical_event_id=bootstrap.incoming_event_id,
        incoming_event_identity=bootstrap.incoming_event_identity,
        incoming_manifest_path="datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml",
        incoming_manifest_sha256=bootstrap.incoming_manifest_sha256,
        incoming_manifest_byte_count=1,
        alignment_attestation_id=bootstrap.alignment_attestation_id,
        alignment_attestation_sha256=bootstrap.alignment_attestation_sha256,
        alignment_policy_version=bootstrap.alignment_policy_version,
        alignment_payload_sha256=bootstrap.alignment_payload_sha256,
        incoming_claim_evidence_sha256=bootstrap.incoming_claim_evidence_sha256,
        document=document,
        raw_artifact=raw_artifact,
        source_note_artifact=note_artifact,
        source_note_logical_path=source_note_logical_path,
        source_note_snapshot_id=f"depsource:{note_snapshot_sha}",
        source_note_snapshot_sha256=note_snapshot_sha,
        reviewed_snapshot_binding_id=f"reviewed-snapshot:{reviewed_binding_sha}",
        reviewed_snapshot_binding_sha256=reviewed_binding_sha,
        temporal_decision_record_sha256=temporal_decision_record_sha256,
        reviewed_inventory_sha256=inventory_sha,
        reviewed_head=reviewed_head,
        authoritative_repository_resolution_required=True,
    )


def test_generation_zero_is_repository_resolved_insert_only_and_replayable(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replay = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    loaded = store.get_active_generation(
        authority.aggregate_id,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )

    assert replay == loaded == authority
    assert authority.authority_revision == 0
    assert authority.active_generation.generation_number == 0
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_active_generation").fetchone()[0]
        == 1
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 1
    )
    operation_id = str(
        store.conn.execute(
            "SELECT initialization_operation_id FROM change_control_active_generation"
        ).fetchone()[0]
    )
    snapshot = store.load(authority.aggregate_id)
    assert snapshot is not None
    with pytest.raises(ChangeControlIdempotencyError, match="another authority"):
        store.compare_and_swap(
            snapshot.aggregate,
            expected_revision=snapshot.revision,
            operation_id=operation_id,
        )
    store.close()


def test_generation_zero_read_rejects_normalized_authority_tamper(tmp_path: Path) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_active_generation SET active_pointer_sha256=?",
            ("0" * 64,),
        )
    with pytest.raises(ChangeControlCorruptionError):
        store.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
    store.close()


def test_schema_v2_upgrades_to_v3_and_failed_upgrade_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in (
        "001_change_control_aggregate.sql",
        "002_authoritative_human_review.sql",
    ):
        shutil.copy(_DEFAULT_MIGRATIONS_DIR / name, migrations / name)
    database = tmp_path / "state.sqlite3"
    with monkeypatch.context() as v2:
        v2.setattr(store_module, "_SCHEMA_VERSION", 2)
        old = SqliteChangeControlStore(database, migrations)
        old.init_schema()
        old.close()

    third = migrations / "003_managed_revision_review.sql"
    source = (_DEFAULT_MIGRATIONS_DIR / third.name).read_text(encoding="utf-8")
    third.write_text(source + "\nNOT VALID SQL;\n", encoding="utf-8")
    with monkeypatch.context() as v3:
        v3.setattr(store_module, "_SCHEMA_VERSION", 3)
        broken = SqliteChangeControlStore(database, migrations)
        with pytest.raises(sqlite3.Error):
            broken.init_schema()
        assert broken._read_meta()["schema_version"] == "2"  # type: ignore[index]
        assert broken._user_tables() == store_module._V2_EXPECTED_TABLES
        broken.close()

    third.write_text(source, encoding="utf-8")
    with monkeypatch.context() as v3:
        v3.setattr(store_module, "_SCHEMA_VERSION", 3)
        upgraded = SqliteChangeControlStore(database, migrations)
        upgraded.init_schema()
        assert upgraded._read_meta()["schema_version"] == "3"  # type: ignore[index]
        assert upgraded._user_tables() == store_module._V3_EXPECTED_TABLES
        assert [
            int(row[0])
            for row in upgraded.conn.execute(
                "SELECT version FROM change_control_schema_migrations ORDER BY version"
            )
        ] == [1, 2, 3]
        upgraded.close()


def test_schema_v3_upgrades_to_v4_and_failed_upgrade_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in (
        "001_change_control_aggregate.sql",
        "002_authoritative_human_review.sql",
        "003_managed_revision_review.sql",
    ):
        shutil.copy(_DEFAULT_MIGRATIONS_DIR / name, migrations / name)
    database = tmp_path / "state.sqlite3"
    with monkeypatch.context() as v3:
        v3.setattr(store_module, "_SCHEMA_VERSION", 3)
        old = SqliteChangeControlStore(database, migrations)
        old.init_schema()
        with old.conn:
            old.conn.execute(
                "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
                ("aggregate:migration-v3", "a" * 64, "2026-01-01T00:00:00Z"),
            )
            old.conn.execute(
                "INSERT INTO change_control_generation_manifests VALUES "
                "(?, ?, ?, 0, ?, 'generation-zero', 0, NULL, 1, ?, ?)",
                (
                    "manifest:migration-v3",
                    "aggregate:migration-v3",
                    "generation:migration-v3",
                    "b" * 64,
                    '{"fixture":"populated-v3"}',
                    "2026-01-01T00:00:00Z",
                ),
            )
            old.conn.execute(
                "INSERT INTO change_control_active_generation VALUES "
                "(?, ?, ?, 0, 'verified-seed-bootstrap', ?, 0, ?, ?, 1, ?, ?)",
                (
                    "aggregate:migration-v3",
                    "operation:migration-v3",
                    "authority:migration-v3",
                    "generation:migration-v3",
                    "b" * 64,
                    "c" * 64,
                    '{"fixture":"authority-v3"}',
                    "2026-01-01T00:00:00Z",
                ),
            )
        preserved_v3_rows = {
            table: old.conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in (
                "change_control_aggregates",
                "change_control_generation_manifests",
                "change_control_active_generation",
            )
        }
        old.close()

    fourth = migrations / "004_generation_publication_activation.sql"
    source = (_DEFAULT_MIGRATIONS_DIR / fourth.name).read_text(encoding="utf-8")
    fourth.write_text(source + "\nNOT VALID SQL;\n", encoding="utf-8")
    with monkeypatch.context() as v4:
        v4.setattr(store_module, "_SCHEMA_VERSION", 4)
        broken = SqliteChangeControlStore(database, migrations)
        with pytest.raises(sqlite3.Error):
            broken.init_schema()
        assert broken._read_meta()["schema_version"] == "3"  # type: ignore[index]
        assert broken._user_tables() == store_module._V3_EXPECTED_TABLES
        assert {
            table: broken.conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in preserved_v3_rows
        } == preserved_v3_rows
        assert (
            broken.conn.execute(
                "SELECT count(*) FROM change_control_schema_migrations WHERE version=4"
            ).fetchone()[0]
            == 0
        )
        broken.close()

    fourth.write_text(source, encoding="utf-8")
    with monkeypatch.context() as v4:
        v4.setattr(store_module, "_SCHEMA_VERSION", 4)
        upgraded = SqliteChangeControlStore(database, migrations)
        upgraded.init_schema()
        assert upgraded._read_meta()["schema_version"] == "4"  # type: ignore[index]
        assert upgraded._user_tables() == store_module._V4_EXPECTED_TABLES
        assert {
            table: upgraded.conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in preserved_v3_rows
        } == preserved_v3_rows
        assert [
            int(row[0])
            for row in upgraded.conn.execute(
                "SELECT version FROM change_control_schema_migrations ORDER BY version"
            )
        ] == [1, 2, 3, 4]
        upgraded.close()


def test_schema_v4_upgrades_to_v5_preserving_seed_authority_and_restoring_fks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in (
        "001_change_control_aggregate.sql",
        "002_authoritative_human_review.sql",
        "003_managed_revision_review.sql",
        "004_generation_publication_activation.sql",
    ):
        shutil.copy(_DEFAULT_MIGRATIONS_DIR / name, migrations / name)
    database = tmp_path / "state.sqlite3"
    with monkeypatch.context() as v4:
        v4.setattr(store_module, "_SCHEMA_VERSION", 4)
        old = SqliteChangeControlStore(database, migrations)
        old.init_schema()
        with old.conn:
            old.conn.execute(
                "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
                ("aggregate:migration-v4", "a" * 64, "2026-01-01T00:00:00+00:00"),
            )
            old.conn.execute(
                "INSERT INTO change_control_generation_manifests VALUES "
                "(?, ?, ?, 0, ?, 'generation-zero', 0, NULL, 1, ?, ?)",
                (
                    "manifest:migration-v4",
                    "aggregate:migration-v4",
                    "generation:migration-v4",
                    "b" * 64,
                    '{"fixture":"populated-v4"}',
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            old.conn.execute(
                "INSERT INTO change_control_active_generation VALUES "
                "(?, ?, ?, 0, 'verified-seed-bootstrap', ?, 0, ?, ?, 1, ?, ?)",
                (
                    "aggregate:migration-v4",
                    "operation:migration-v4",
                    "authority:migration-v4",
                    "generation:migration-v4",
                    "b" * 64,
                    "c" * 64,
                    '{"fixture":"authority-v4"}',
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            old.conn.execute(
                "INSERT INTO change_control_managed_review_bundles VALUES "
                "(?, ?, ?, 1, ?, ?, 0, ?, ?, 1, ?)",
                (
                    "bundle:migration-v4",
                    "d" * 64,
                    "aggregate:migration-v4",
                    "a" * 64,
                    "authority:migration-v4",
                    "generation:migration-v4",
                    "b" * 64,
                    '{"fixture":"bundle-v4"}',
                ),
            )
        preserved = {
            table: tuple(old.conn.execute(f"SELECT * FROM {table}"))
            for table in (
                "change_control_active_generation",
                "change_control_managed_review_bundles",
            )
        }
        old.close()

    fifth = migrations / "005_workspace_bootstrap_application.sql"
    source = (_DEFAULT_MIGRATIONS_DIR / fifth.name).read_text(encoding="utf-8")
    fifth.write_text(source + "\nNOT VALID SQL;\n", encoding="utf-8")
    broken = SqliteChangeControlStore(database, migrations)
    with pytest.raises(sqlite3.Error):
        broken.init_schema()
    assert broken._read_meta()["schema_version"] == "4"  # type: ignore[index]
    assert broken._user_tables() == store_module._V4_EXPECTED_TABLES
    assert {
        table: tuple(broken.conn.execute(f"SELECT * FROM {table}")) for table in preserved
    } == preserved
    assert int(broken.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    broken.close()

    fifth.write_text(source, encoding="utf-8")
    upgraded = SqliteChangeControlStore(database, migrations)
    upgraded.init_schema()
    assert upgraded._read_meta()["schema_version"] == "5"  # type: ignore[index]
    assert upgraded._user_tables() == store_module._EXPECTED_TABLES
    assert {
        table: tuple(upgraded.conn.execute(f"SELECT * FROM {table}")) for table in preserved
    } == preserved
    assert upgraded.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert int(upgraded.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    upgraded.conn.execute("BEGIN")
    upgraded.conn.execute(
        "UPDATE change_control_active_generation SET origin_kind='verified-workspace-bootstrap'"
    )
    assert (
        str(
            upgraded.conn.execute(
                "SELECT origin_kind FROM change_control_active_generation"
            ).fetchone()[0]
        )
        == "verified-workspace-bootstrap"
    )
    upgraded.conn.execute("ROLLBACK")
    upgraded.close()


def _workspace_bootstrap_fixture():
    raw_sha = "1" * 64
    note_sha = "2" * 64
    unmanaged_sha = "3" * 64
    document = DocumentVersionMetadata.create(
        document_id="workspace-policy-v1",
        document_family="workspace-policy",
        version_label="v1",
        source_path="raw/workspace-policy-v1.md",
        source_sha256=raw_sha,
        declared_effective_from=date(2026, 1, 1),
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )
    inventory = WorkspaceBootstrapInventory.create(
        manifest_schema_version=1,
        manifest_sha256="4" * 64,
        vault_members=(
            WorkspaceVaultMember(
                logical_path="sources/managed.md",
                note_kind=WorkspaceNoteKind.SOURCE,
                content_sha256=note_sha,
                byte_count=128,
            ),
            WorkspaceVaultMember(
                logical_path="sources/unmanaged.md",
                note_kind=WorkspaceNoteKind.SOURCE,
                content_sha256=unmanaged_sha,
                byte_count=64,
            ),
        ),
        managed_source_notes=(
            ManagedSourceNoteBootstrapMetadata(
                logical_path="sources/managed.md",
                source_note_sha256=note_sha,
                source_note_byte_count=128,
                source_root_id="workspace",
                source_relative_path=document.source_path,
                source_note_provenance=document.source_path,
                raw_source_path=document.source_path,
                raw_source_sha256=raw_sha,
                raw_source_byte_count=256,
                document=document,
            ),
        ),
        legacy_index=LegacyIndexExpectation(
            index_file_sha256="5" * 64,
            index_file_byte_count=4096,
            index_schema_version=1,
            embedding_model="test-embedding-v1",
            embedding_dimensions=8,
        ),
    )
    intent = WorkspaceBootstrapIntent.create(
        operation_id="workspace-bootstrap:claim",
        aggregate_id="workspace-bootstrap",
        inventory=inventory,
    )
    aggregate = ChangeControlAggregate.create(
        aggregate_id=intent.aggregate_id,
        documents=DocumentVersionRegistry.create((document,)),
        claims=ClaimRevisionRegistry.create(()),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    return inventory, intent, aggregate


def test_workspace_bootstrap_inventory_lookup_is_exact_and_secure_read_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "change_control" / "state.sqlite3"
    store = SqliteManagedChangeControlStore(database, secure_open=True)
    store.init_schema()
    inventory, intent, _aggregate = _workspace_bootstrap_fixture()
    expected = store.claim_workspace_bootstrap(intent=intent, inventory=inventory)
    store.close()

    before_bytes = database.read_bytes()
    before_names = tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )
    statements: list[str] = []
    read_only = SqliteManagedChangeControlStore(
        database,
        secure_open=True,
        read_only=True,
    )
    try:
        read_only.conn.set_trace_callback(statements.append)
        assert (
            read_only.get_workspace_bootstrap_by_inventory_id(inventory.inventory_id)
            == expected
        )
        assert (
            read_only.get_workspace_bootstrap_by_inventory_id(f"workspaceinventory:{'0' * 64}")
            is None
        )
        assert int(read_only.conn.execute("PRAGMA query_only").fetchone()[0]) == 1
    finally:
        read_only.conn.set_trace_callback(None)
        read_only.close()

    mutating_prefixes = (
        "ALTER ",
        "CREATE ",
        "DELETE ",
        "DROP ",
        "INSERT ",
        "REPLACE ",
        "UPDATE ",
        "VACUUM ",
    )
    assert not any(
        statement.lstrip().upper().startswith(mutating_prefixes) for statement in statements
    )
    assert database.read_bytes() == before_bytes
    assert (
        tuple(sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")))
        == before_names
    )


def test_workspace_bootstrap_inventory_lookup_rejects_corrupt_ownership(
    tmp_path: Path,
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "state.sqlite3")
    store.init_schema()
    inventory, intent, _aggregate = _workspace_bootstrap_fixture()
    store.claim_workspace_bootstrap(intent=intent, inventory=inventory)
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_workspace_bootstrap_intents SET inventory_id=? "
            "WHERE bootstrap_id=?",
            (f"workspaceinventory:{'f' * 64}", intent.bootstrap_id),
        )

    with pytest.raises(ChangeControlCorruptionError, match="workspace bootstrap intent"):
        store.get_workspace_bootstrap_by_inventory_id(inventory.inventory_id)
    assert not store.conn.in_transaction
    store.close()


def test_workspace_bootstrap_stages_replay_concurrently_and_initialize_generic_zero(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = SqliteManagedChangeControlStore(database, secure_open=True)
    store.init_schema()
    inventory, intent, aggregate = _workspace_bootstrap_fixture()
    claimed = store.claim_workspace_bootstrap(intent=intent, inventory=inventory)
    assert claimed.intent == intent
    assert claimed.inventory == inventory
    commit = store.create(aggregate, operation_id="workspace-bootstrap:aggregate")
    store.close()

    inventory_receipts = tuple(
        WorkspaceInventoryReceipt.create(
            operation_id="workspace-bootstrap:inventory",
            bootstrap_id=intent.bootstrap_id,
            aggregate_operation_id="workspace-bootstrap:aggregate",
            aggregate_id=intent.aggregate_id,
            aggregate_revision=commit.revision,
            aggregate_sha256=commit.aggregate_sha256,
            inventory_id=inventory.inventory_id,
            inventory_sha256=inventory.inventory_sha256,
            recorded_at=f"2026-01-01T00:00:0{second}+00:00",
        )
        for second in (1, 2)
    )

    def record_inventory(receipt: WorkspaceInventoryReceipt):
        connection = SqliteManagedChangeControlStore(database)
        try:
            return connection.record_workspace_inventory(receipt)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        inventory_states = tuple(pool.map(record_inventory, inventory_receipts))
    assert inventory_states[0] == inventory_states[1]
    persisted_inventory_receipt = inventory_states[0].inventory_receipt
    assert persisted_inventory_receipt in inventory_receipts
    assert persisted_inventory_receipt is not None

    readiness_receipts = tuple(
        LegacyIndexReadinessReceipt.create(
            operation_id="workspace-bootstrap:index",
            bootstrap_id=intent.bootstrap_id,
            inventory_receipt_id=persisted_inventory_receipt.receipt_id,
            inventory_receipt_sha256=persisted_inventory_receipt.receipt_sha256,
            index_logical_fingerprint="6" * 64,
            index_file_sha256=inventory.legacy_index.index_file_sha256,
            index_file_byte_count=inventory.legacy_index.index_file_byte_count,
            index_schema_version=inventory.legacy_index.index_schema_version,
            embedding_model=inventory.legacy_index.embedding_model,
            embedding_dimensions=inventory.legacy_index.embedding_dimensions,
            ready_at=f"2026-01-01T00:00:0{second}+00:00",
        )
        for second in (3, 4)
    )

    def record_readiness(receipt: LegacyIndexReadinessReceipt):
        connection = SqliteManagedChangeControlStore(database)
        try:
            return connection.record_legacy_index_readiness(receipt)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        complete_states = tuple(pool.map(record_readiness, readiness_receipts))
    assert complete_states[0] == complete_states[1]
    assert complete_states[0].index_readiness_receipt in readiness_receipts

    evidence_is_fresh = True

    def verify_fresh_workspace() -> None:
        if not evidence_is_fresh:
            raise ValueError("simulated workspace/index drift")

    class FreshWorkspaceEvidenceGuard:
        def verify(self) -> None:
            verify_fresh_workspace()

    readiness = complete_states[0].index_readiness_receipt
    assert readiness is not None
    legacy_attestation = LegacyIndexAttestation(
        index_file_sha256=readiness.index_file_sha256,
        index_file_byte_count=readiness.index_file_byte_count,
        projection_fingerprint="7" * 64,
        logical_index_fingerprint=readiness.index_logical_fingerprint,
        storage_schema_version=readiness.index_schema_version,
        embedding_model_version=readiness.embedding_model,
        embedding_dimensions=readiness.embedding_dimensions,
        counts=(("documents", len(inventory.vault_members)),),
    )
    evidence_verifier = workspace_bootstrap_module._mint_verified_workspace_bootstrap_evidence_verifier(
        FreshWorkspaceEvidenceGuard(),
        resolved_inventory=inventory,
        resolved_aggregate=aggregate,
        legacy_attestation=legacy_attestation,
    )
    capability = workspace_bootstrap_module._mint_verified_workspace_bootstrap_capability(
        complete_states[0],
        evidence_verifier=evidence_verifier,
    )
    store = SqliteManagedChangeControlStore(database, secure_open=True)
    authority = store.initialize_workspace_generation_zero(verified_workspace_bootstrap=capability)
    context = AuthorityVerificationContext.workspace(capability)
    assert (
        store.get_active_generation(
            intent.aggregate_id,
            authority_context=context,
        )
        == authority
    )
    assert (
        store.initialize_workspace_generation_zero(verified_workspace_bootstrap=capability)
        == authority
    )
    command_probe = ManagedActivationCommand.model_construct(
        expected_authority=authority,
    )
    ManagedGenerationRepository._require_generation_zero_command(command_probe)

    class WorkspaceContextReached(Exception):
        pass

    class WorkspaceContextProbeStore:
        securely_coordinated = True

        def get_managed_review(self, request_id, *, resolver, authority_context):
            del request_id, resolver
            assert authority_context == context
            raise WorkspaceContextReached

    with pytest.raises(WorkspaceContextReached):
        activate_reviewed_managed_generation(
            request_id="mrequest:" + "a" * 64,
            operation_id="workspace-bootstrap:activation-probe",
            store=WorkspaceContextProbeStore(),  # type: ignore[arg-type]
            resolver=object(),  # type: ignore[arg-type]
            generation_root=tmp_path / "must-not-exist",
            embedder=MockEmbedding(8),
            authority_context=context,
        )
    with pytest.raises(ManagedServingGenerationZeroError):
        open_active_managed_sqlite_index(
            aggregate_id=intent.aggregate_id,
            store=store,
            resolver=object(),  # type: ignore[arg-type]
            authority_context=context,
            generation_root=tmp_path / "must-not-exist",
        )
    assert not (tmp_path / "must-not-exist").exists()
    run_command = OperatorRunCommand.create(
        operation_id="workspace-bootstrap:operator-run",
        aggregate_id=intent.aggregate_id,
        base_authority_id=authority.authority_id,
        base_authority_revision=authority.authority_revision,
        base_active_pointer_sha256=authority.active_pointer_sha256,
    )
    store.create_operator_run(run_command)
    persisted_readiness = complete_states[0].index_readiness_receipt
    assert persisted_readiness is not None
    for operation_id, kind, target_id, target_sha in (
        (
            "workspace-bootstrap:link:intent",
            OperatorRunLinkKind.BOOTSTRAP_INTENT,
            intent.bootstrap_id,
            intent.intent_sha256,
        ),
        (
            "workspace-bootstrap:link:inventory",
            OperatorRunLinkKind.WORKSPACE_INVENTORY,
            persisted_inventory_receipt.receipt_id,
            persisted_inventory_receipt.receipt_sha256,
        ),
        (
            "workspace-bootstrap:link:index",
            OperatorRunLinkKind.LEGACY_INDEX_READINESS,
            persisted_readiness.receipt_id,
            persisted_readiness.receipt_sha256,
        ),
        (
            "workspace-bootstrap:link:authority",
            OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
            authority.authority_id,
            authority.active_pointer_sha256,
        ),
    ):
        command = OperatorRunLinkCommand.create(
            operation_id=operation_id,
            run_id=run_command.run_id,
            kind=kind,
            target_id=target_id,
            target_sha256=target_sha,
        )
        store.record_operator_run_link(command)
    navigation = store.get_operator_run(run_command.run_id)
    assert navigation is not None
    assert tuple(link.command.kind for link in navigation.links) == (
        OperatorRunLinkKind.BOOTSTRAP_INTENT,
        OperatorRunLinkKind.WORKSPACE_INVENTORY,
        OperatorRunLinkKind.LEGACY_INDEX_READINESS,
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
    )
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    evidence_is_fresh = False
    with pytest.raises(ManagedReviewAuthorityError, match="cannot be verified"):
        store.get_active_generation(intent.aggregate_id, authority_context=context)
    store.close()


def test_workspace_bootstrap_inventory_tamper_fails_every_public_reopen(
    tmp_path: Path,
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "state.sqlite3")
    store.init_schema()
    inventory, intent, _aggregate = _workspace_bootstrap_fixture()
    store.claim_workspace_bootstrap(intent=intent, inventory=inventory)
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_workspace_inventories SET payload_json='{}' "
            "WHERE inventory_id=?",
            (inventory.inventory_id,),
        )
    with pytest.raises(ChangeControlCorruptionError, match="workspace bootstrap inventory"):
        store.get_workspace_bootstrap(intent.bootstrap_id)
    store.close()


def test_operator_run_navigation_is_post_authority_typed_and_replayable(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    command = OperatorRunCommand.create(
        operation_id="operator-run:create",
        aggregate_id=authority.aggregate_id,
        base_authority_id=authority.authority_id,
        base_authority_revision=authority.authority_revision,
        base_active_pointer_sha256=authority.active_pointer_sha256,
    )
    created = store.create_operator_run(command)
    assert store.create_operator_run(command) == created
    assert created.links == ()

    link = OperatorRunLinkCommand.create(
        operation_id="operator-run:link:authority",
        run_id=command.run_id,
        kind=OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
        target_id=authority.authority_id,
        target_sha256=authority.active_pointer_sha256,
    )
    linked = store.record_operator_run_link(link)
    assert store.record_operator_run_link(link) == linked
    assert store.get_operator_run(command.run_id) == linked
    assert tuple(item.command.kind for item in linked.links) == (
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
    )

    unsupported = OperatorRunLinkCommand.create(
        operation_id="operator-run:link:incoming-source",
        run_id=command.run_id,
        kind=OperatorRunLinkKind.INCOMING_SOURCE,
        target_id="incoming-source:not-yet-supported",
        target_sha256="a" * 64,
    )
    with pytest.raises(ManagedReviewAuthorityError, match="no authoritative target resolver"):
        store.record_operator_run_link(unsupported)

    conflicting = OperatorRunLinkCommand.create(
        operation_id="operator-run:link:authority:other",
        run_id=command.run_id,
        kind=OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
        target_id=authority.authority_id,
        target_sha256=authority.active_pointer_sha256,
    )
    with pytest.raises(ChangeControlIdempotencyError, match="kind"):
        store.record_operator_run_link(conflicting)
    snapshot = store.load(authority.aggregate_id)
    assert snapshot is not None
    with pytest.raises(ChangeControlIdempotencyError, match="another authority"):
        store.compare_and_swap(
            snapshot.aggregate,
            expected_revision=snapshot.revision,
            operation_id=command.operation_id,
        )
    store.close()


def _no_change_scenario(path: Path) -> _Scenario:
    store, bootstrap, prechange_head = _bootstrapped_store(path)
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    analysis = bootstrap.snapshot.aggregate
    newer = bootstrap.incoming_event.document
    older = next(
        document
        for document in analysis.documents.documents
        if document.document_family == newer.document_family
        and document.document_version_id != newer.document_version_id
    )
    replacement = DocumentReplacementAssessment.create(
        newer_document=newer,
        older_document=older,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The newly effective returns policy replaces the earlier version.",
        confidence=1.0,
    )
    constraint = TemporalConstraint.from_document_replacement(
        replacement.with_status(TemporalConstraintStatus.ACCEPTED),
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The replacement closes the older policy interval.",
    )
    proposed = ChangeControlAggregate.create(
        aggregate_id=analysis.aggregate_id,
        documents=analysis.documents,
        claims=analysis.claims,
        relation_graph=analysis.relation_graph,
        dependencies=analysis.dependencies,
        document_replacements=DocumentReplacementSet.create((replacement,)),
        temporal_constraints=TemporalConstraintSet.create((constraint,)),
    )
    proposed_commit = store.compare_and_swap(
        proposed,
        expected_revision=bootstrap.snapshot.revision,
        operation_id="managed-store:run",
    )
    temporal_request = store.create_review_request(
        HumanReviewRequestCommand(
            aggregate_id=analysis.aggregate_id,
            expected_revision=proposed_commit.revision,
            expected_aggregate_sha256=proposed_commit.aggregate_sha256,
            subjects=(
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
                    subject_id=replacement.relation_id,
                ),
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
                    subject_id=constraint.constraint_id,
                ),
            ),
            requester_id="operator@example.test",
            rationale="Review the exact temporal prerequisite.",
        ),
        operation_id="managed-store:temporal-request",
    )
    temporal_decision = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=temporal_request.request.request_id,
            reviewer_id="reviewer@example.test",
            rationale="Accept the exact replacement and derived temporal bound.",
            items=tuple(
                ReviewDecisionItem(
                    kind=subject.kind,
                    subject_id=subject.subject_id,
                    original_subject_sha256=subject.subject_sha256,
                    disposition=ReviewDisposition.ACCEPTED,
                )
                for subject in temporal_request.request.subjects
            ),
        ),
        operation_id="managed-store:temporal-decision",
    )
    review_head = AggregateHeadBinding.create(
        aggregate_id=analysis.aggregate_id,
        revision=temporal_decision.aggregate_revision,
        aggregate_sha256=temporal_decision.aggregate_sha256,
    )
    temporal_sha = hashlib.sha256(
        canonical_json_bytes(temporal_decision.decision.model_dump(mode="json"))
    ).hexdigest()

    algorithm_manifest = b'{"algorithm":"managed-review-test-v1"}'
    algorithm_sha = hashlib.sha256(algorithm_manifest).hexdigest()
    prompt_sha = hashlib.sha256(b"prompt").hexdigest()
    response_schema_sha = hashlib.sha256(b"schema").hexdigest()
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=algorithm_sha,
        contract_id="managed-revision",
        contract_version=1,
        mode=InferenceExecutionMode.REPLAY,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=prompt_sha,
        response_schema_sha256=response_schema_sha,
    )
    target_key = newer.document_id
    target_result_sha = hashlib.sha256(target_key.encode()).hexdigest()
    impact_result_sha = hashlib.sha256(b"impact").hexdigest()
    impact_batch_sha = hashlib.sha256(b"impact-batch").hexdigest()
    impact_workload_sha = hashlib.sha256(b"impact-workload").hexdigest()
    impact_evidence = ManagedImpactAnalysisEvidenceBinding.create(
        repository_id=hashlib.sha256(b"impact-repository").hexdigest(),
        batch_id=f"inference-batch:{impact_batch_sha}",
        batch_sha256=impact_batch_sha,
        batch_members=(
            ManagedImpactBatchMemberBinding(
                execution_id="inference-exec:" + hashlib.sha256(b"impact-exec").hexdigest(),
                receipt_artifact_id="martifact:" + hashlib.sha256(b"impact-receipt").hexdigest(),
                outcome_sha256=hashlib.sha256(b"impact-outcome").hexdigest(),
            ),
        ),
        workload_id=f"impactwork:{impact_workload_sha}",
        workload_sha256=impact_workload_sha,
        result_id=f"impactresult:{impact_result_sha}",
        result_sha256=impact_result_sha,
        output_shards=(
            ManagedImpactOutputRefBinding(
                document_version_id=newer.document_version_id,
                input_shard_id="impactin:" + hashlib.sha256(b"impact-input").hexdigest(),
                input_shard_sha256=hashlib.sha256(b"impact-input").hexdigest(),
                output_shard_id=f"impactout:{target_result_sha}",
                output_shard_sha256=target_result_sha,
                decision_count=1,
                document_disposition="NO_CHANGE_REQUIRED",
            ),
        ),
    )
    analysis_set = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=bootstrap.binding,
        candidate_result_sha256=hashlib.sha256(b"candidates").hexdigest(),
        classification_result_sha256=hashlib.sha256(b"classifications").hexdigest(),
        attention_result_sha256=hashlib.sha256(b"attention").hexdigest(),
        impact_evidence=impact_evidence,
        global_relevant_claim_revision_ids=bootstrap.binding.changed_claim_revision_ids,
    )
    legacy_run = ManagedRunBinding.create(
        run_id="managed-store-run",
        operation_id="managed-store:run",
        prechange_head=prechange_head,
        analysis_head=AggregateHeadBinding.create(
            aggregate_id=analysis.aggregate_id,
            revision=bootstrap.snapshot.revision,
            aggregate_sha256=bootstrap.snapshot.aggregate_sha256,
        ),
        algorithm_manifest_sha256=algorithm_sha,
        inference_contract=contract,
        analysis_set=analysis_set,
    )
    envelope = {
        "schema_version": 1,
        "target_key": target_key,
        "analysis_set_id": analysis_set.analysis_set_id,
        "analysis_set_sha256": analysis_set.analysis_set_sha256,
        "impact_result_sha256": analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    input_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_INPUT,
        (
            f"staging/managed-review/{legacy_run.run_id}/{target_key}/analysis-input-"
            f"{hashlib.sha256(envelope_bytes).hexdigest()}.json"
        ),
        envelope_bytes,
    )
    target_analysis = TargetAnalysisBinding.create(
        target_key=target_key,
        analysis_set=analysis_set,
        target_result_sha256=target_result_sha,
        inference_input=input_artifact,
    )
    raw_bytes = (REPO_ROOT / newer.source_path).read_bytes()
    predecessor_raw = _artifact(ManagedArtifactKind.RAW_SOURCE, newer.source_path, raw_bytes)
    claims = tuple(
        claim
        for claim in temporal_decision.decision.decided_aggregate.claims.revisions
        if claim.document == newer
    )
    note_path = claims[0].source.source_note_path
    note_bytes = bootstrap.incoming_event.processed_snapshot
    predecessor_note = _artifact(ManagedArtifactKind.SOURCE_NOTE, note_path, note_bytes)
    projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=predecessor_raw,
        note_artifact=predecessor_note,
        canonical_raw_path=newer.source_path,
        canonical_note_path=note_path,
        validator_version="source-note-v1",
        source_note_schema_sha256=hashlib.sha256(b"note-schema").hexdigest(),
        validator_result_sha256=hashlib.sha256(b"note-validation").hexdigest(),
        projected_claims=claims,
    )
    citation = GroundedArtifactCitation.create(
        artifact=input_artifact,
        start_byte=0,
        quote="{",
    )
    semantic = {
        "run_id": legacy_run.run_id,
        "target_key": target_key,
        "predecessor": newer,
        "predecessor_raw": predecessor_raw,
        "predecessor_note": predecessor_note,
        "predecessor_projection": projection,
        "analysis": target_analysis,
        "rationale": "The current policy requires no downstream managed revision.",
        "citations": (citation,),
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{legacy_run.run_id}/{target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:live-request",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(input_artifact,),
        input_envelope_sha256=target_analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(input_artifact,),
        input_envelope_sha256=target_analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    card = NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output_artifact,
    )
    workload_sha = hashlib.sha256(b"managed-store-revision-workload").hexdigest()
    execution_id = (
        "inference-exec:" + hashlib.sha256(b"managed-store-revision-execution").hexdigest()
    )
    outcome_sha = hashlib.sha256(b"managed-store-revision-outcome").hexdigest()
    receipt_artifact = card.inference_receipt.artifact_ref()
    admission_target = ManagedRevisionPlanningTargetBinding(
        target_key=card.target_key,
        document_version_id=card.predecessor.document_version_id,
        input_shard_id=f"revisionin:{input_artifact.sha256}",
        input_shard_sha256=input_artifact.sha256,
        output_shard_id=f"revisionout:{output_artifact.sha256}",
        output_shard_sha256=output_artifact.sha256,
        execution_id=execution_id,
        outcome_sha256=outcome_sha,
        receipt_id=card.inference_receipt.receipt_id,
        receipt_artifact_id=receipt_artifact.artifact_id,
        subject_kind="no-change-impact-card",
        subject_id=card.card_id,
        subject_sha256=card.card_sha256,
        staged_artifacts=tuple(
            sorted((input_artifact, output_artifact), key=lambda item: item.artifact_id)
        ),
    )
    manifest_sha = hashlib.sha256(b"managed-store-staging-manifest").hexdigest()
    manifest_path = f"staging/managed-review/{legacy_run.run_id}/manifests/{manifest_sha}.json"
    completion_path = f"staging/managed-review/{legacy_run.run_id}/COMPLETE.json"
    completion_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "run_id": legacy_run.run_id,
                "repository_id": impact_evidence.repository_id,
                "manifest_id": f"managed-staging:{manifest_sha}",
                "manifest_sha256": manifest_sha,
                "manifest_path": manifest_path,
                "completion_path": completion_path,
            }
        )
    ).hexdigest()
    adoption_note = _artifact(
        ManagedArtifactKind.SOURCE_NOTE,
        bootstrap.incoming_event.manifest.document.processed_path,
        note_bytes,
    )
    adoption = _synthetic_governing_source_adoption(
        bootstrap=analysis_set.analysis_bootstrap,
        evidence_repository_id=impact_evidence.repository_id,
        document=newer,
        raw_artifact=predecessor_raw,
        note_artifact=adoption_note,
        source_note_logical_path=note_path,
        reviewed_head=review_head,
        temporal_decision_record_sha256=temporal_sha,
    )
    admission = ManagedRevisionPlanningAdmissionBinding.create(
        run_id=legacy_run.run_id,
        repository_id=impact_evidence.repository_id,
        workload_id=f"revisionwork:{workload_sha}",
        workload_sha256=workload_sha,
        analysis_set=analysis_set,
        analysis_set_id=analysis_set.analysis_set_id,
        analysis_set_sha256=analysis_set.analysis_set_sha256,
        reviewed_snapshot_binding_id=adoption.reviewed_snapshot_binding_id,
        reviewed_snapshot_binding_sha256=adoption.reviewed_snapshot_binding_sha256,
        temporal_decision_record_sha256=adoption.temporal_decision_record_sha256,
        contract_binding_id=contract.contract_binding_id,
        batch_id="inference-batch:" + hashlib.sha256(b"managed-store-batch").hexdigest(),
        batch_sha256=hashlib.sha256(b"managed-store-batch").hexdigest(),
        batch_members=(
            ManagedRevisionPlanningBatchMemberBinding(
                execution_id=execution_id,
                receipt_artifact_id=receipt_artifact.artifact_id,
                outcome_sha256=outcome_sha,
            ),
        ),
        staging_manifest_id=f"managed-staging:{manifest_sha}",
        staging_manifest_sha256=manifest_sha,
        staging_manifest_path=manifest_path,
        staging_completion_id=f"managed-staging-completion:{completion_sha}",
        staging_completion_sha256=completion_sha,
        staging_completion_path=completion_path,
        targets=(admission_target,),
    )
    run = ManagedRunBindingV2.create(
        run_id=legacy_run.run_id,
        operation_id=legacy_run.operation_id,
        prechange_head=legacy_run.prechange_head,
        analysis_head=legacy_run.analysis_head,
        algorithm_manifest_sha256=legacy_run.algorithm_manifest_sha256,
        inference_contract=legacy_run.inference_contract,
        analysis_set=legacy_run.analysis_set,
        revision_planning_admission=admission,
        governing_source_adoption=adoption,
    )
    bundle = ManagedRevisionReviewBundle.create(
        run_binding=run,
        review_base=ManagedReviewBaseBinding.create(
            review_open_head=review_head,
            authority=authority,
        ),
        temporal_prerequisite=TemporalDecisionPrerequisite(
            review_open_head=review_head,
            temporal_decision_record_sha256=temporal_sha,
        ),
        targets=(ManagedRevisionReviewTarget.create(card),),
    )
    resolver = _Resolver(
        contract,
        algorithm_manifest,
        {
            predecessor_raw.path: raw_bytes,
            predecessor_note.path: note_bytes,
            input_artifact.path: envelope_bytes,
            output_artifact.path: output_bytes,
            replay_artifact.path: live_bytes,
            adoption_note.path: note_bytes,
        },
        approved_projection_ids={projection.projection_id},
        impact_evidence=impact_evidence,
        revision_admission=admission,
        governing_source_adoption=adoption,
    )
    request_command = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id="managed-store:request",
        requester_id="operator@example.test",
        rationale="Review the complete no-change evidence.",
    )
    return _Scenario(
        store=store,
        bootstrap=bootstrap,
        prechange_head=prechange_head,
        authority=authority,
        resolver=resolver,
        bundle=bundle,
        request_command=request_command,
    )


def test_managed_no_change_request_decision_replay_and_reopen(tmp_path: Path) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    store = scenario.store
    bootstrap = scenario.bootstrap
    prechange_head = scenario.prechange_head
    authority = scenario.authority
    resolver = scenario.resolver
    bundle = scenario.bundle
    request_command = scenario.request_command
    first_request = store.create_managed_review_request(
        request_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replayed_request = store.create_managed_review_request(
        request_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert not first_request.replayed and replayed_request.replayed
    open_view = store.get_managed_review(
        request_command.request_id,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert open_view.lifecycle == ManagedRevisionStoreLifecycle.OPEN
    outcome = ManagedRevisionReviewOutcome(
        target_id=bundle.targets[0].target_id,
        original_target_sha256=bundle.targets[0].target_sha256,
        disposition=ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
    )
    decision_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:decision",
        request_record=open_view.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Confirm the evidence-backed no-change result.",
        items=(outcome,),
    )
    first_decision = store.decide_managed_review(
        decision_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replayed_decision = store.decide_managed_review(
        decision_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert not first_decision.replayed and replayed_decision.replayed
    assert first_decision.activation_required
    assert isinstance(decision_command.generation_manifest, ManagedGenerationManifestBindingV2)
    assert isinstance(bundle.run_binding, ManagedRunBindingV2)
    assert decision_command.generation_manifest.publication_delta == ()
    assert (
        decision_command.generation_manifest.governing_source_adoption
        == bundle.run_binding.governing_source_adoption
    )
    decided = store.get_managed_review(
        request_command.request_id,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert decided.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
    assert decided.authoritative_view is not None
    assert (
        store.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
        == authority
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 2
    )
    overlay_row = store.conn.execute(
        "SELECT payload_schema_version, payload_json "
        "FROM change_control_generation_manifests WHERE manifest_kind='managed-overlay'"
    ).fetchone()
    assert overlay_row is not None and int(overlay_row["payload_schema_version"]) == 1
    assert json.loads(str(overlay_row["payload_json"]))["schema_version"] == 2
    store.close()


def test_impact_evidence_v1_canonical_identity_remains_frozen(tmp_path: Path) -> None:
    scenario = _no_change_scenario(tmp_path / "impact-v1.sqlite3")
    try:
        evidence = scenario.bundle.run_binding.analysis_set.impact_evidence
        assert evidence is not None
        canonical = canonical_json_bytes(evidence.model_dump(mode="json"))
        assert evidence.evidence_binding_id == (
            "mimpactevidence:7ce3941a793b27363a5802fedcd9a594166c3a22da255327b87dc89a97de82e1"
        )
        assert hashlib.sha256(canonical).hexdigest() == (
            "991561d3d9a71aa7f43daebc95256b74ef89e4b03a6dd1ee05647e3c0c84981a"
        )
        assert b'"repository_id"' in canonical
        assert b'"evidence_repository_id"' not in canonical
        assert b'"source_repository' not in canonical
    finally:
        scenario.store.close()


def _create_managed_request(scenario: _Scenario):
    return scenario.store.create_managed_review_request(
        scenario.request_command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )


def _advance_non_review_aggregate(scenario: _Scenario) -> None:
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    old_claim = next(
        item
        for item in snapshot.aggregate.claims.revisions
        if item.document.document_id == "sl2-policy-returns-v1" and "return-policy" in item.scopes
    )
    new_claim = next(
        item
        for item in snapshot.aggregate.claims.revisions
        if item.document.document_id == "sl2-policy-returns-v2" and "return-policy" in item.scopes
    )
    relation = RelationAssessment.create(
        pair=ComparableClaimPair.create(old_claim, new_claim),
        disposition=PairDisposition.COEXISTS,
        rationale="A later analysis pass records an independent relation.",
        confidence=0.75,
    )
    replacement = ChangeControlAggregate.create(
        aggregate_id=snapshot.aggregate.aggregate_id,
        documents=snapshot.aggregate.documents,
        claims=snapshot.aggregate.claims,
        relation_graph=RelationGraph.create((relation,)),
        dependencies=snapshot.aggregate.dependencies,
        document_replacements=snapshot.aggregate.document_replacements,
        temporal_constraints=snapshot.aggregate.temporal_constraints,
    )
    scenario.store.compare_and_swap(
        replacement,
        expected_revision=snapshot.revision,
        operation_id="managed-store:post-open-analysis",
    )


def _no_change_variant_scenario(scenario: _Scenario) -> _Scenario:
    card = scenario.bundle.targets[0].subject
    assert isinstance(card, NoChangeImpactCard)
    semantic = {
        "run_id": card.run_id,
        "target_key": card.target_key,
        "predecessor": card.predecessor,
        "predecessor_raw": card.predecessor_raw,
        "predecessor_note": card.predecessor_note,
        "predecessor_projection": card.predecessor_projection,
        "analysis": card.analysis,
        "rationale": "A second independently validated no-change explanation.",
        "citations": card.citations,
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:no-change-variant",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    variant = NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output,
    )
    scenario.resolver.artifacts[output.path] = output_bytes
    scenario.resolver.artifacts[replay_artifact.path] = live_bytes
    return _rebind_scenario_impact_subjects(
        scenario,
        (variant,),
        ("NO_CHANGE_REQUIRED",),
        request_operation_id="managed-store:overlapping-request",
        request_rationale="Attempt a second request over the same still-open target.",
    )


def _additional_no_change_card(
    scenario: _Scenario, *, document_id: str = "sl2-faq-returns"
) -> NoChangeImpactCard:
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    document = next(
        item for item in snapshot.aggregate.documents.documents if item.document_id == document_id
    )
    claims = tuple(
        item for item in snapshot.aggregate.claims.revisions if item.document == document
    )
    raw_bytes = (REPO_ROOT / document.source_path).read_bytes()
    raw = _artifact(ManagedArtifactKind.RAW_SOURCE, document.source_path, raw_bytes)
    note_path = claims[0].source.source_note_path
    note_bytes = (REPO_ROOT / "datasets/larkstead/processed" / note_path).read_bytes()
    note = _artifact(ManagedArtifactKind.SOURCE_NOTE, note_path, note_bytes)
    projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=raw,
        note_artifact=note,
        canonical_raw_path=document.source_path,
        canonical_note_path=note_path,
        validator_version="source-note-v1",
        source_note_schema_sha256=hashlib.sha256(b"note-schema").hexdigest(),
        validator_result_sha256=hashlib.sha256(b"faq-note-validation").hexdigest(),
        projected_claims=claims,
    )
    analysis_set = scenario.bundle.run_binding.analysis_set
    target_result_sha = hashlib.sha256(document_id.encode()).hexdigest()
    envelope = {
        "schema_version": 1,
        "target_key": document_id,
        "analysis_set_id": analysis_set.analysis_set_id,
        "analysis_set_sha256": analysis_set.analysis_set_sha256,
        "impact_result_sha256": analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    inference_input = _artifact(
        ManagedArtifactKind.INFERENCE_INPUT,
        (
            f"staging/managed-review/{scenario.bundle.run_binding.run_id}/{document_id}/"
            f"analysis-input-{hashlib.sha256(envelope_bytes).hexdigest()}.json"
        ),
        envelope_bytes,
    )
    analysis = TargetAnalysisBinding.create(
        target_key=document_id,
        analysis_set=analysis_set,
        target_result_sha256=target_result_sha,
        inference_input=inference_input,
    )
    citation = GroundedArtifactCitation.create(
        artifact=inference_input,
        start_byte=0,
        quote="{",
    )
    semantic = {
        "run_id": scenario.bundle.run_binding.run_id,
        "target_key": document_id,
        "predecessor": document,
        "predecessor_raw": raw,
        "predecessor_note": note,
        "predecessor_projection": projection,
        "analysis": analysis,
        "rationale": "The FAQ is reviewed in the same bundle and requires no revision.",
        "citations": (citation,),
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{scenario.bundle.run_binding.run_id}/{document_id}/"
            f"validated-output-{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:additional-no-change",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    scenario.resolver.artifacts.update(
        {
            raw.path: raw_bytes,
            note.path: note_bytes,
            inference_input.path: envelope_bytes,
            output.path: output_bytes,
            replay_artifact.path: live_bytes,
        }
    )
    scenario.resolver.approved_projection_ids.add(projection.projection_id)
    return NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output,
    )


def _rebind_scenario_impact_subjects(
    scenario: _Scenario,
    subjects: tuple[ManagedRevisionPlan | NoChangeImpactCard, ...],
    dispositions: tuple[str, ...],
    *,
    request_operation_id: str,
    request_rationale: str,
) -> _Scenario:
    """Test-only reproduction of subjects after the exact Step 10b evidence changes."""

    assert len(subjects) == len(dispositions)
    original_analysis = scenario.bundle.run_binding.analysis_set
    result_sha = original_analysis.impact_result_sha256
    batch_sha = hashlib.sha256(
        canonical_json_bytes(
            [
                (subject.target_key, disposition)
                for subject, disposition in zip(subjects, dispositions, strict=True)
            ]
        )
    ).hexdigest()
    workload_sha = hashlib.sha256(("workload:" + batch_sha).encode()).hexdigest()
    members = tuple(
        ManagedImpactBatchMemberBinding(
            execution_id="inference-exec:"
            + hashlib.sha256(f"execution:{subject.target_key}".encode()).hexdigest(),
            receipt_artifact_id="martifact:"
            + hashlib.sha256(f"receipt:{subject.target_key}".encode()).hexdigest(),
            outcome_sha256=hashlib.sha256(f"outcome:{subject.target_key}".encode()).hexdigest(),
        )
        for subject in subjects
    )
    outputs = tuple(
        ManagedImpactOutputRefBinding(
            document_version_id=subject.predecessor.document_version_id,
            input_shard_id="impactin:"
            + hashlib.sha256(f"input:{subject.target_key}".encode()).hexdigest(),
            input_shard_sha256=hashlib.sha256(f"input:{subject.target_key}".encode()).hexdigest(),
            output_shard_id=f"impactout:{subject.analysis.target_result_sha256}",
            output_shard_sha256=subject.analysis.target_result_sha256,
            decision_count=1,
            document_disposition=disposition,
        )
        for subject, disposition in zip(subjects, dispositions, strict=True)
    )
    evidence = ManagedImpactAnalysisEvidenceBinding.create(
        repository_id=hashlib.sha256(b"impact-repository").hexdigest(),
        batch_id=f"inference-batch:{batch_sha}",
        batch_sha256=batch_sha,
        batch_members=members,
        workload_id=f"impactwork:{workload_sha}",
        workload_sha256=workload_sha,
        result_id=f"impactresult:{result_sha}",
        result_sha256=result_sha,
        output_shards=outputs,
    )
    analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=original_analysis.analysis_bootstrap,
        candidate_result_sha256=original_analysis.candidate_result_sha256,
        classification_result_sha256=original_analysis.classification_result_sha256,
        attention_result_sha256=original_analysis.attention_result_sha256,
        impact_evidence=evidence,
        global_relevant_claim_revision_ids=original_analysis.global_relevant_claim_revision_ids,
    )
    old_run = scenario.bundle.run_binding
    assert isinstance(old_run, ManagedRunBindingV2)
    run = ManagedRunBinding.create(
        run_id=old_run.run_id,
        operation_id=old_run.operation_id,
        prechange_head=old_run.prechange_head,
        analysis_head=old_run.analysis_head,
        algorithm_manifest_sha256=old_run.algorithm_manifest_sha256,
        inference_contract=old_run.inference_contract,
        analysis_set=analysis,
    )
    artifacts = dict(scenario.resolver.artifacts)
    rebound: list[ManagedRevisionPlan | NoChangeImpactCard] = []
    for subject in subjects:
        target_result_sha = subject.analysis.target_result_sha256
        envelope = {
            "schema_version": 1,
            "target_key": subject.target_key,
            "analysis_set_id": analysis.analysis_set_id,
            "analysis_set_sha256": analysis.analysis_set_sha256,
            "impact_result_sha256": analysis.impact_result_sha256,
            "target_result_sha256": target_result_sha,
        }
        envelope_bytes = canonical_json_bytes(envelope)
        envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
        inference_input = _artifact(
            ManagedArtifactKind.INFERENCE_INPUT,
            (
                f"staging/managed-review/{run.run_id}/{subject.target_key}/"
                f"analysis-input-{envelope_sha}.json"
            ),
            envelope_bytes,
        )
        target_analysis = TargetAnalysisBinding.create(
            target_key=subject.target_key,
            analysis_set=analysis,
            target_result_sha256=target_result_sha,
            inference_input=inference_input,
        )
        artifacts[inference_input.path] = envelope_bytes

        def rebound_citation(
            citation: GroundedArtifactCitation,
            *,
            expected_artifact_id: str = subject.analysis.inference_input.artifact_id,
            rebound_artifact: ManagedArtifactRef = inference_input,
        ) -> GroundedArtifactCitation:
            assert citation.artifact_id == expected_artifact_id
            return GroundedArtifactCitation.create(
                artifact=rebound_artifact,
                start_byte=citation.start_byte,
                quote=citation.quote,
            )

        if isinstance(subject, NoChangeImpactCard):
            semantic = {
                "run_id": subject.run_id,
                "target_key": subject.target_key,
                "predecessor": subject.predecessor,
                "predecessor_raw": subject.predecessor_raw,
                "predecessor_note": subject.predecessor_note,
                "predecessor_projection": subject.predecessor_projection,
                "analysis": target_analysis,
                "rationale": subject.rationale,
                "citations": tuple(rebound_citation(item) for item in subject.citations),
            }
            output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
            output_kind = NoChangeImpactCard
        else:
            hunks = tuple(
                ManagedSemanticHunk.create(
                    semantic_key=hunk.semantic_key,
                    base_artifact=subject.predecessor_raw,
                    result_artifact=subject.proposed_raw,
                    start_byte=hunk.start_byte,
                    before_text=hunk.before_text,
                    replacement_text=hunk.replacement_text,
                    citations=tuple(rebound_citation(item) for item in hunk.citations),
                )
                for hunk in subject.hunks
            )
            patch = PatchReconstructionAttestation.create_from_verifier_output(
                base_artifact=subject.predecessor_raw,
                result_artifact=subject.proposed_raw,
                hunks=hunks,
                complete_diff_sha256=subject.patch_attestation.complete_diff_sha256,
            )
            semantic = {
                "run_id": subject.run_id,
                "target_key": subject.target_key,
                "predecessor": subject.predecessor,
                "predecessor_raw": subject.predecessor_raw,
                "predecessor_note": subject.predecessor_note,
                "successor": subject.successor,
                "proposed_raw": subject.proposed_raw,
                "proposed_note": subject.proposed_note,
                "raw_destination": subject.raw_destination,
                "note_destination": subject.note_destination,
                "analysis": target_analysis,
                "predecessor_projection": subject.predecessor_projection,
                "successor_projection": subject.successor_projection,
                "patch_attestation": patch,
                "claim_reconciliation": subject.claim_reconciliation,
                "rationale": subject.rationale,
                "hunks": hunks,
            }
            output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
            output_kind = ManagedRevisionPlan
        output = _artifact(
            ManagedArtifactKind.INFERENCE_OUTPUT,
            (
                f"staging/managed-review/{run.run_id}/{subject.target_key}/validated-output-"
                f"{hashlib.sha256(output_bytes).hexdigest()}.json"
            ),
            output_bytes,
        )
        contract = run.inference_contract
        live = ContentAddressedInferenceReceipt.create(
            contract_id=contract.contract_id,
            contract_version=contract.contract_version,
            mode=InferenceExecutionMode.LIVE,
            provider=contract.provider,
            model=contract.model,
            provider_request_id=f"fixture:rebound:{subject.target_key}",
            replay_source_receipt_sha256=None,
            replay_source_receipt_artifact=None,
            prompt_sha256=contract.prompt_sha256,
            response_schema_sha256=contract.response_schema_sha256,
            input_artifacts=(inference_input,),
            input_envelope_sha256=target_analysis.input_envelope_sha256,
            raw_output_sha256=output.sha256,
            validated_output_sha256=output.sha256,
            usage=InferenceUsage(
                input_tokens=1,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd_micros=1,
                latency_ms=1,
            ),
        )
        live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
        replay_artifact = _artifact(
            ManagedArtifactKind.INFERENCE_RECEIPT,
            f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
            live_bytes,
        )
        replay = ContentAddressedInferenceReceipt.create(
            contract_id=contract.contract_id,
            contract_version=contract.contract_version,
            mode=InferenceExecutionMode.REPLAY,
            provider=contract.provider,
            model=contract.model,
            provider_request_id=None,
            replay_source_receipt_sha256=replay_artifact.sha256,
            replay_source_receipt_artifact=replay_artifact,
            prompt_sha256=contract.prompt_sha256,
            response_schema_sha256=contract.response_schema_sha256,
            input_artifacts=(inference_input,),
            input_envelope_sha256=target_analysis.input_envelope_sha256,
            raw_output_sha256=output.sha256,
            validated_output_sha256=output.sha256,
            usage=InferenceUsage(
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                cost_usd_micros=0,
                latency_ms=0,
            ),
        )
        rebound.append(
            output_kind.create(
                **semantic,
                inference_receipt=replay,
                validated_output=output,
            )
        )
        artifacts[output.path] = output_bytes
        artifacts[replay_artifact.path] = live_bytes

    revision_members: list[ManagedRevisionPlanningBatchMemberBinding] = []
    revision_targets: list[ManagedRevisionPlanningTargetBinding] = []
    for subject in rebound:
        execution_id = (
            "inference-exec:"
            + hashlib.sha256(f"revision-execution:{subject.target_key}".encode()).hexdigest()
        )
        outcome_sha = hashlib.sha256(f"revision-outcome:{subject.target_key}".encode()).hexdigest()
        receipt_artifact = subject.inference_receipt.artifact_ref()
        revision_members.append(
            ManagedRevisionPlanningBatchMemberBinding(
                execution_id=execution_id,
                receipt_artifact_id=receipt_artifact.artifact_id,
                outcome_sha256=outcome_sha,
            )
        )
        revision_targets.append(
            ManagedRevisionPlanningTargetBinding(
                target_key=subject.target_key,
                document_version_id=subject.predecessor.document_version_id,
                input_shard_id=f"revisionin:{subject.analysis.inference_input.sha256}",
                input_shard_sha256=subject.analysis.inference_input.sha256,
                output_shard_id=f"revisionout:{subject.validated_output.sha256}",
                output_shard_sha256=subject.validated_output.sha256,
                execution_id=execution_id,
                outcome_sha256=outcome_sha,
                receipt_id=subject.inference_receipt.receipt_id,
                receipt_artifact_id=receipt_artifact.artifact_id,
                subject_kind=(
                    "managed-revision-plan"
                    if isinstance(subject, ManagedRevisionPlan)
                    else "no-change-impact-card"
                ),
                subject_id=(
                    subject.plan_id if isinstance(subject, ManagedRevisionPlan) else subject.card_id
                ),
                subject_sha256=(
                    subject.plan_sha256
                    if isinstance(subject, ManagedRevisionPlan)
                    else subject.card_sha256
                ),
                staged_artifacts=tuple(
                    sorted(
                        (subject.analysis.inference_input, subject.validated_output),
                        key=lambda item: item.artifact_id,
                    )
                ),
            )
        )
    revision_workload_sha = hashlib.sha256(("revision:" + batch_sha).encode()).hexdigest()
    revision_batch_sha = hashlib.sha256(("revision-batch:" + batch_sha).encode()).hexdigest()
    repository_id = evidence.repository_id
    manifest_sha = hashlib.sha256(("revision-manifest:" + batch_sha).encode()).hexdigest()
    manifest_path = f"staging/managed-review/{run.run_id}/manifests/{manifest_sha}.json"
    completion_path = f"staging/managed-review/{run.run_id}/COMPLETE.json"
    completion_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "repository_id": repository_id,
                "manifest_id": f"managed-staging:{manifest_sha}",
                "manifest_sha256": manifest_sha,
                "manifest_path": manifest_path,
                "completion_path": completion_path,
            }
        )
    ).hexdigest()
    admission = ManagedRevisionPlanningAdmissionBinding.create(
        run_id=run.run_id,
        repository_id=repository_id,
        workload_id=f"revisionwork:{revision_workload_sha}",
        workload_sha256=revision_workload_sha,
        analysis_set=analysis,
        analysis_set_id=analysis.analysis_set_id,
        analysis_set_sha256=analysis.analysis_set_sha256,
        reviewed_snapshot_binding_id=old_run.governing_source_adoption.reviewed_snapshot_binding_id,
        reviewed_snapshot_binding_sha256=old_run.governing_source_adoption.reviewed_snapshot_binding_sha256,
        temporal_decision_record_sha256=old_run.governing_source_adoption.temporal_decision_record_sha256,
        contract_binding_id=run.inference_contract.contract_binding_id,
        batch_id=f"inference-batch:{revision_batch_sha}",
        batch_sha256=revision_batch_sha,
        batch_members=tuple(revision_members),
        staging_manifest_id=f"managed-staging:{manifest_sha}",
        staging_manifest_sha256=manifest_sha,
        staging_manifest_path=manifest_path,
        staging_completion_id=f"managed-staging-completion:{completion_sha}",
        staging_completion_sha256=completion_sha,
        staging_completion_path=completion_path,
        targets=tuple(revision_targets),
    )
    admitted_run = ManagedRunBindingV2.create(
        run_id=run.run_id,
        operation_id=run.operation_id,
        prechange_head=run.prechange_head,
        analysis_head=run.analysis_head,
        algorithm_manifest_sha256=run.algorithm_manifest_sha256,
        inference_contract=run.inference_contract,
        analysis_set=analysis,
        revision_planning_admission=admission,
        governing_source_adoption=old_run.governing_source_adoption,
    )
    bundle = ManagedRevisionReviewBundle.create(
        run_binding=admitted_run,
        review_base=scenario.bundle.review_base,
        temporal_prerequisite=scenario.bundle.temporal_prerequisite,
        targets=tuple(ManagedRevisionReviewTarget.create(item) for item in rebound),
    )
    projection_ids = {item.predecessor_projection.projection_id for item in rebound} | {
        item.successor_projection.projection_id
        for item in rebound
        if isinstance(item, ManagedRevisionPlan)
    }
    resolver = _Resolver(
        run.inference_contract,
        scenario.resolver.manifest,
        artifacts,
        approved_projection_ids=projection_ids,
        impact_evidence=evidence,
        revision_admission=admission,
        governing_source_adoption=old_run.governing_source_adoption,
    )
    request = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id=request_operation_id,
        requester_id="operator@example.test",
        rationale=request_rationale,
    )
    return _Scenario(
        store=scenario.store,
        bootstrap=scenario.bootstrap,
        prechange_head=scenario.prechange_head,
        authority=scenario.authority,
        resolver=resolver,
        bundle=bundle,
        request_command=request,
    )


def test_open_request_becomes_stale_but_exact_replay_survives_fresh_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    scenario = _no_change_scenario(database)
    initial = _create_managed_request(scenario)
    _advance_non_review_aggregate(scenario)

    stale = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert stale.lifecycle == ManagedRevisionStoreLifecycle.STALE
    replay = _create_managed_request(scenario)
    assert replay.replayed
    assert replay.request_record_sha256 == initial.request_record_sha256
    scenario.store.close()

    reopened = SqliteManagedChangeControlStore(database)
    reopened.init_schema()
    replay_after_reopen = reopened.create_managed_review_request(
        scenario.request_command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert replay_after_reopen.replayed
    assert replay_after_reopen.request_record_sha256 == initial.request_record_sha256
    assert (
        reopened.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        ).lifecycle
        == ManagedRevisionStoreLifecycle.STALE
    )
    reopened.close()


@pytest.mark.parametrize("failure", ("impact", "contract", "manifest", "missing", "tampered"))
def test_repository_resolution_failure_creates_no_managed_review_rows(
    tmp_path: Path, failure: str
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    resolver = _Resolver(
        scenario.resolver.contract,
        scenario.resolver.manifest,
        dict(scenario.resolver.artifacts),
        approved_projection_ids=set(scenario.resolver.approved_projection_ids),
        impact_evidence=scenario.resolver.impact_evidence,
    )
    if failure == "impact":
        evidence = scenario.resolver.impact_evidence
        assert evidence is not None
        resolver.impact_evidence = ManagedImpactAnalysisEvidenceBinding.create(
            repository_id="0" * 64,
            batch_id=evidence.batch_id,
            batch_sha256=evidence.batch_sha256,
            batch_members=evidence.batch_members,
            workload_id=evidence.workload_id,
            workload_sha256=evidence.workload_sha256,
            result_id=evidence.result_id,
            result_sha256=evidence.result_sha256,
            output_shards=evidence.output_shards,
        )
    elif failure == "contract":
        resolver.contract = resolver.contract.model_copy(update={"provider": "unapproved-provider"})
    elif failure == "manifest":
        resolver.manifest = b"tampered algorithm manifest"
    else:
        path = scenario.bundle.targets[0].subject.validated_output.path
        if failure == "missing":
            del resolver.artifacts[path]
        else:
            resolver.artifacts[path] = b"tampered validated output"

    with pytest.raises(ManagedReviewAuthorityError):
        scenario.store.create_managed_review_request(
            scenario.request_command,
            resolver=resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    for table in (
        "change_control_managed_review_bundles",
        "change_control_managed_review_targets",
        "change_control_managed_review_request_records",
        "change_control_managed_review_request_delivery_receipts",
    ):
        assert scenario.store.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    scenario.store.close()


def test_generation_zero_rejects_wrong_head_and_forged_capability_before_insert(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    wrong_head = AggregateHeadBinding.create(
        aggregate_id=prechange_head.aggregate_id,
        revision=prechange_head.revision,
        aggregate_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="exact bootstrap prechange head"):
        store.initialize_generation_zero(
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=wrong_head,
        )

    forged = object.__new__(VerifiedAnalysisBootstrapCapability)
    object.__setattr__(forged, "binding", bootstrap.binding)
    object.__setattr__(forged, "prechange_aggregate_sha256", prechange_head.aggregate_sha256)
    object.__setattr__(forged, "_token", object())
    with pytest.raises(AnalysisBootstrapIntegrityError, match="not repository verified"):
        store.initialize_generation_zero(
            verified_bootstrap=forged,
            prechange_head=prechange_head,
        )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_active_generation").fetchone()[0]
        == 0
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 0
    )
    store.close()


def test_operation_collision_and_overlapping_open_target_fail_closed(tmp_path: Path) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    with pytest.raises(ChangeControlIdempotencyError, match="another authority"):
        scenario.store.compare_and_swap(
            snapshot.aggregate,
            expected_revision=snapshot.revision,
            operation_id=scenario.request_command.operation_id,
        )

    overlapping_scenario = _no_change_variant_scenario(scenario)
    overlapping = overlapping_scenario.request_command
    with pytest.raises(ChangeControlConflictError, match="overlaps an open target"):
        scenario.store.create_managed_review_request(
            overlapping,
            resolver=overlapping_scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0]
        == 1
    )
    scenario.store.close()


def test_temporal_prerequisite_rejects_unrelated_proposal_transition_lineage(
    tmp_path: Path,
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    run = scenario.bundle.run_binding
    assert isinstance(run, ManagedRunBindingV2)
    unrelated_run = ManagedRunBindingV2.create(
        run_id=run.run_id,
        operation_id="managed-store:unrelated-proposal-transition",
        prechange_head=run.prechange_head,
        analysis_head=run.analysis_head,
        algorithm_manifest_sha256=run.algorithm_manifest_sha256,
        inference_contract=run.inference_contract,
        analysis_set=run.analysis_set,
        revision_planning_admission=run.revision_planning_admission,
        governing_source_adoption=run.governing_source_adoption,
    )
    unrelated_bundle = ManagedRevisionReviewBundle.create(
        run_binding=unrelated_run,
        review_base=scenario.bundle.review_base,
        temporal_prerequisite=scenario.bundle.temporal_prerequisite,
        targets=scenario.bundle.targets,
    )
    command = ManagedRevisionReviewRequestCommand.create(
        bundle=unrelated_bundle,
        operation_id="managed-store:unrelated-lineage-request",
        requester_id="operator@example.test",
        rationale="This request deliberately binds an unrelated proposal transition.",
    )
    with pytest.raises(ManagedReviewAuthorityError, match="does not authorize"):
        scenario.store.create_managed_review_request(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def _activating_scenario(path: Path) -> _Scenario:
    scenario = _no_change_scenario(path)
    card = scenario.bundle.targets[0].subject
    assert isinstance(card, NoChangeImpactCard)
    raw_bytes = scenario.resolver.artifacts[card.predecessor_raw.path]
    note_bytes = scenario.resolver.artifacts[card.predecessor_note.path]
    before = b"45 days"
    start = raw_bytes.index(before)
    replacement = b"46 days"
    proposed_raw_bytes = raw_bytes[:start] + replacement + raw_bytes[start + len(before) :]
    proposed_note_bytes = note_bytes + b"\n<!-- managed persistence fixture -->\n"
    proposed_raw = _artifact(
        ManagedArtifactKind.RAW_SOURCE,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/raw-"
            f"{hashlib.sha256(proposed_raw_bytes).hexdigest()}.md"
        ),
        proposed_raw_bytes,
    )
    proposed_note = _artifact(
        ManagedArtifactKind.SOURCE_NOTE,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/note-"
            f"{hashlib.sha256(proposed_note_bytes).hexdigest()}.md"
        ),
        proposed_note_bytes,
    )
    raw_destination = PublicationDestination.create(
        target_key=card.target_key,
        kind=PublicationKind.RAW_SOURCE,
        expected_sha256=proposed_raw.sha256,
        expected_byte_count=proposed_raw.byte_count,
    )
    note_destination = PublicationDestination.create(
        target_key=card.target_key,
        kind=PublicationKind.SOURCE_NOTE,
        expected_sha256=proposed_note.sha256,
        expected_byte_count=proposed_note.byte_count,
    )
    successor = derive_managed_successor(
        predecessor=card.predecessor,
        target_key=card.target_key,
        proposed_raw=proposed_raw,
        raw_destination=raw_destination,
        effective_from=card.predecessor.declared_effective_from,
    )
    successor_claims = tuple(
        VersionedClaimRevision.create(
            document=successor,
            source=ClaimSourceReference(
                source_note_path=note_destination.path,
                source_note_sha256=proposed_note.sha256,
                source_claim_id=f"managed-{index:02d}",
                evidence=(),
            ),
            statement=claim.statement,
            declared_effective_from=successor.declared_effective_from,
            scopes=claim.scopes,
        )
        for index, claim in enumerate(card.predecessor_projection.projected_claims)
    )
    successor_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=proposed_raw,
        note_artifact=proposed_note,
        canonical_raw_path=raw_destination.path,
        canonical_note_path=note_destination.path,
        validator_version="source-note-v1",
        source_note_schema_sha256=card.predecessor_projection.source_note_schema_sha256,
        validator_result_sha256=hashlib.sha256(b"managed-note-validation").hexdigest(),
        projected_claims=successor_claims,
    )
    reconciliation = ClaimReconciliationBinding.create(
        predecessor_projection=card.predecessor_projection,
        successor_projection=successor_projection,
        entries=tuple(
            ClaimReconciliationEntry(
                action=ClaimReconciliationAction.CARRIED_FORWARD,
                predecessor=old,
                successor=next(
                    new
                    for new in successor_projection.projected_claims
                    if new.statement == old.statement and new.scopes == old.scopes
                ),
            )
            for old in card.predecessor_projection.projected_claims
        ),
    )
    citation = GroundedArtifactCitation.create(
        artifact=card.analysis.inference_input,
        start_byte=0,
        quote="{",
    )
    hunk = ManagedSemanticHunk.create(
        semantic_key="return-window",
        base_artifact=card.predecessor_raw,
        result_artifact=proposed_raw,
        start_byte=start,
        before_text=before.decode(),
        replacement_text=replacement.decode(),
        citations=(citation,),
    )
    patch = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=card.predecessor_raw,
        result_artifact=proposed_raw,
        hunks=(hunk,),
        complete_diff_sha256=hashlib.sha256(
            canonical_json_bytes(
                {"start": start, "before": before.decode(), "replacement": replacement.decode()}
            )
        ).hexdigest(),
    )
    semantic = {
        "run_id": card.run_id,
        "target_key": card.target_key,
        "predecessor": card.predecessor,
        "predecessor_raw": card.predecessor_raw,
        "predecessor_note": card.predecessor_note,
        "successor": successor,
        "proposed_raw": proposed_raw,
        "proposed_note": proposed_note,
        "raw_destination": raw_destination,
        "note_destination": note_destination,
        "analysis": card.analysis,
        "predecessor_projection": card.predecessor_projection,
        "successor_projection": successor_projection,
        "patch_attestation": patch,
        "claim_reconciliation": reconciliation,
        "rationale": "Propose one evidence-grounded create-only policy revision.",
        "hunks": (hunk,),
    }
    output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
    output_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:activating-live-request",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    plan = ManagedRevisionPlan.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output_artifact,
    )
    scenario.resolver.artifacts.update(
        {
            proposed_raw.path: proposed_raw_bytes,
            proposed_note.path: proposed_note_bytes,
        }
    )
    return _rebind_scenario_impact_subjects(
        scenario,
        (plan,),
        ("AFFECTED",),
        request_operation_id="managed-store:activating-request",
        request_rationale="Review one exact create-only managed revision.",
    )


def _open_activating_decision(
    scenario: _Scenario, *, operation_id: str = "managed-store:activating-decision"
) -> ManagedRevisionDecisionCommand:
    _create_managed_request(scenario)
    opened = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    target = scenario.bundle.targets[0]
    return ManagedRevisionDecisionCommand.create(
        operation_id=operation_id,
        request_record=opened.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Approve the exact staged revision without publishing it in PR-A.",
        items=(
            ManagedRevisionReviewOutcome(
                target_id=target.target_id,
                original_target_sha256=target.target_sha256,
                disposition=ManagedRevisionDisposition.APPROVE,
            ),
        ),
    )


def _multi_target_scenario(path: Path) -> _Scenario:
    scenario = _activating_scenario(path)
    additional = _additional_no_change_card(scenario)
    return _rebind_scenario_impact_subjects(
        scenario,
        (scenario.bundle.targets[0].subject, additional),
        ("AFFECTED", "NO_CHANGE_REQUIRED"),
        request_operation_id="managed-store:multi-target-request",
        request_rationale=("Review the activating plan and explicit no-change result atomically."),
    )


def test_all_targets_round_trip_in_one_atomic_activating_decision(tmp_path: Path) -> None:
    scenario = _multi_target_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    opened = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    outcomes = tuple(
        sorted(
            (
                ManagedRevisionReviewOutcome(
                    target_id=target.target_id,
                    original_target_sha256=target.target_sha256,
                    disposition=(
                        ManagedRevisionDisposition.APPROVE
                        if isinstance(target.subject, ManagedRevisionPlan)
                        else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                    ),
                )
                for target in scenario.bundle.targets
            ),
            key=lambda item: item.target_id,
        )
    )
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:multi-target-decision",
        request_record=opened.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Approve or confirm every target as one all-or-none decision.",
        items=outcomes,
    )
    scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    view = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert view.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
    assert view.decision_record is not None
    assert view.decision_record.command.items == outcomes
    assert len(command.generation_manifest.publication_delta) == 2
    assert command.generation_manifest.retained_review_target_keys == ("sl2-faq-returns",)
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decision_items"
        ).fetchone()[0]
        == 2
    )
    scenario.store.close()


def test_activating_decision_persists_only_inactive_manifest_and_replays(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    scenario = _activating_scenario(database)
    aggregate_before = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    authority_before = scenario.store.get_active_generation(
        scenario.bootstrap.binding.aggregate_id,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    command = _open_activating_decision(scenario)
    first = scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert not first.replayed
    assert command.generation_manifest.requires_activation
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 1
    )
    assert scenario.store.load(scenario.bootstrap.binding.aggregate_id) == aggregate_before
    assert (
        scenario.store.get_active_generation(
            scenario.bootstrap.binding.aggregate_id,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
        == authority_before
    )
    scenario.store.close()

    reopened = SqliteManagedChangeControlStore(database)
    reopened.init_schema()
    replay = reopened.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert replay.replayed
    assert replay.decision_record_sha256 == first.decision_record_sha256
    second_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:second-decision",
        request_record=command.request_record,
        bundle_outcome=command.bundle_outcome,
        reviewer_id=command.reviewer_id,
        rationale=command.rationale,
        items=command.items,
    )
    with pytest.raises(ChangeControlReviewAlreadyDecidedError):
        reopened.decide_managed_review(
            second_command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    reopened.close()


@pytest.mark.parametrize(
    ("decided", "mutation"),
    (
        (
            False,
            "UPDATE change_control_managed_review_targets SET ordinal=7 WHERE ordinal=0",
        ),
        (False, "UPDATE change_control_managed_review_bundles SET payload_json='{}'"),
        (
            False,
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivery_sequence=4 WHERE delivery_sequence=0",
        ),
        (
            False,
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivered_at='2000-01-01T00:00:00+00:00' WHERE delivery_sequence=0",
        ),
        (
            True,
            "UPDATE change_control_managed_review_decision_items SET ordinal=9 WHERE ordinal=0",
        ),
        (
            True,
            "UPDATE change_control_managed_review_decision_delivery_receipts "
            "SET payload_json='{}' WHERE delivery_sequence=0",
        ),
        (
            True,
            "UPDATE change_control_generation_manifests SET manifest_sha256='"
            + "0" * 64
            + "' WHERE manifest_kind='managed-overlay'",
        ),
        (
            True,
            "UPDATE change_control_generation_manifests "
            "SET created_at='2000-01-01T00:00:00+00:00' "
            "WHERE manifest_kind='managed-overlay'",
        ),
    ),
)
def test_normalized_managed_evidence_tamper_fails_closed(
    tmp_path: Path, decided: bool, mutation: str
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    if decided:
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    with scenario.store.conn:
        scenario.store.conn.execute(mutation)
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    scenario.store.close()


@pytest.mark.parametrize("nested_schema_version", (None, 3, 1))
def test_persisted_overlay_manifest_nested_discriminator_fails_closed(
    tmp_path: Path,
    nested_schema_version: int | None,
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    row = scenario.store.conn.execute(
        "SELECT payload_schema_version, manifest_kind, payload_json "
        "FROM change_control_generation_manifests WHERE manifest_kind='managed-overlay'"
    ).fetchone()
    assert row is not None
    assert int(row["payload_schema_version"]) == 1
    assert str(row["manifest_kind"]) == "managed-overlay"
    payload = json.loads(str(row["payload_json"]))
    assert payload["schema_version"] == 2
    if nested_schema_version is None:
        del payload["schema_version"]
    else:
        payload["schema_version"] = nested_schema_version
    with scenario.store.conn:
        scenario.store.conn.execute(
            "UPDATE change_control_generation_manifests SET payload_json=? "
            "WHERE manifest_kind='managed-overlay'",
            (canonical_json_bytes(payload).decode("utf-8"),),
        )
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    scenario.store.close()


def test_persisted_overlay_storage_envelope_and_decision_nested_manifest_fail_closed(
    tmp_path: Path,
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    manifest_row = scenario.store.conn.execute(
        "SELECT payload_json FROM change_control_generation_manifests "
        "WHERE manifest_kind='managed-overlay'"
    ).fetchone()
    decision_row = scenario.store.conn.execute(
        "SELECT payload_json FROM change_control_managed_review_decisions"
    ).fetchone()
    assert manifest_row is not None and decision_row is not None
    original_manifest = str(manifest_row["payload_json"])
    original_decision = str(decision_row["payload_json"])

    scenario.store.conn.execute("PRAGMA ignore_check_constraints=ON")
    try:
        with scenario.store.conn:
            scenario.store.conn.execute(
                "UPDATE change_control_generation_manifests SET payload_schema_version=2 "
                "WHERE manifest_kind='managed-overlay'"
            )
    finally:
        scenario.store.conn.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    with scenario.store.conn:
        scenario.store.conn.execute(
            "UPDATE change_control_generation_manifests "
            "SET payload_schema_version=1, payload_json=? "
            "WHERE manifest_kind='managed-overlay'",
            (original_manifest,),
        )

    for nested_schema_version in (None, 3, 1):
        payload = json.loads(original_decision)
        if nested_schema_version is None:
            del payload["command"]["generation_manifest"]["schema_version"]
        else:
            payload["command"]["generation_manifest"]["schema_version"] = nested_schema_version
        with scenario.store.conn:
            scenario.store.conn.execute(
                "UPDATE change_control_managed_review_decisions SET payload_json=?",
                (canonical_json_bytes(payload).decode("utf-8"),),
            )
        with pytest.raises(ChangeControlCorruptionError):
            scenario.store.get_managed_review(
                scenario.request_command.request_id,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.bootstrap.verification_capability,
                prechange_head=scenario.prechange_head,
            )
    with scenario.store.conn:
        scenario.store.conn.execute(
            "UPDATE change_control_managed_review_decisions SET payload_json=?",
            (original_decision,),
        )
    scenario.store.close()


@pytest.mark.parametrize(
    ("trigger", "table"),
    (
        (
            "CREATE TEMP TRIGGER fail_managed_request BEFORE INSERT ON "
            "change_control_managed_review_request_records BEGIN "
            "SELECT RAISE(ABORT, 'request rollback'); END",
            "request",
        ),
        (
            "CREATE TEMP TRIGGER fail_managed_decision_item BEFORE INSERT ON "
            "change_control_managed_review_decision_items BEGIN "
            "SELECT RAISE(ABORT, 'decision rollback'); END",
            "decision",
        ),
    ),
)
def test_managed_request_and_decision_failures_roll_back_atomically(
    tmp_path: Path, trigger: str, table: str
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    if table == "request":
        scenario.store.conn.execute(trigger)
        with pytest.raises(sqlite3.IntegrityError, match="request rollback"):
            _create_managed_request(scenario)
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_bundles"
            ).fetchone()[0]
            == 0
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_targets"
            ).fetchone()[0]
            == 0
        )
    else:
        command = _open_activating_decision(scenario)
        scenario.store.conn.execute(trigger)
        with pytest.raises(sqlite3.IntegrityError, match="decision rollback"):
            scenario.store.decide_managed_review(
                command,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.bootstrap.verification_capability,
                prechange_head=scenario.prechange_head,
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_decisions"
            ).fetchone()[0]
            == 0
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
            ).fetchone()[0]
            == 0
        )
        view = scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
        assert view.lifecycle == ManagedRevisionStoreLifecycle.OPEN
    scenario.store.close()


def test_undecided_request_cannot_commit_after_aggregate_head_moves(tmp_path: Path) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    _advance_non_review_aggregate(scenario)
    with pytest.raises(ManagedReviewStaleError, match="aggregate head is stale"):
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def test_decision_reopens_and_revalidates_staged_plan_before_writing(tmp_path: Path) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    plan = scenario.bundle.targets[0].subject
    assert isinstance(plan, ManagedRevisionPlan)
    scenario.resolver.artifacts[plan.proposed_raw.path] = b"tampered after request opened"
    with pytest.raises(ManagedReviewAuthorityError):
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def test_generation_and_delivery_timestamps_are_cross_bound_and_monotonic(
    tmp_path: Path,
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    _create_managed_request(scenario)
    with scenario.store.conn:
        scenario.store.conn.execute(
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivered_at='1999-01-01T00:00:00+00:00' WHERE delivery_sequence=1"
        )
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    scenario.store.close()

    generation, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "generation.sqlite3")
    authority = generation.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    with generation.conn:
        generation.conn.execute(
            "UPDATE change_control_generation_manifests "
            "SET created_at='2000-01-01T00:00:00+00:00' WHERE manifest_kind='generation-zero'"
        )
    with pytest.raises(ChangeControlCorruptionError):
        generation.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
    generation.close()


def test_active_generation_schema_allows_future_managed_shape_but_rejects_mixed_shape(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    store.conn.execute("BEGIN")
    store.conn.execute(
        "UPDATE change_control_active_generation SET "
        "origin_kind='managed-decision', authority_revision=1, active_generation_number=1"
    )
    row = store.conn.execute(
        "SELECT origin_kind, authority_revision, active_generation_number "
        "FROM change_control_active_generation"
    ).fetchone()
    assert tuple(row) == ("managed-decision", 1, 1)
    store.conn.execute("ROLLBACK")

    with pytest.raises(sqlite3.IntegrityError), store.conn:
        store.conn.execute(
            "UPDATE change_control_active_generation SET origin_kind='managed-decision'"
        )
    store.close()


class _ExplodingManagedResolver:
    def __getattr__(self, name: str):
        raise AssertionError(f"managed write gate accessed resolver method {name}")


def _legacy_bundle(bundle: ManagedRevisionReviewBundle) -> ManagedRevisionReviewBundle:
    run = bundle.run_binding
    assert isinstance(run, ManagedRunBindingV2)
    legacy = ManagedRunBinding.create(
        run_id=run.run_id,
        operation_id=run.operation_id,
        prechange_head=run.prechange_head,
        analysis_head=run.analysis_head,
        algorithm_manifest_sha256=run.algorithm_manifest_sha256,
        inference_contract=run.inference_contract,
        analysis_set=run.analysis_set,
    )
    return ManagedRevisionReviewBundle.create(
        run_binding=legacy,
        review_base=bundle.review_base,
        temporal_prerequisite=bundle.temporal_prerequisite,
        targets=bundle.targets,
    )


def _edited_decision_command(scenario: _Scenario) -> ManagedRevisionDecisionCommand:
    approved = _open_activating_decision(
        scenario, operation_id="managed-store:unused-approved-command"
    )
    target = approved.bundle.targets[0]
    original = target.subject
    assert isinstance(original, ManagedRevisionPlan)
    excluded = {
        "plan_id",
        "plan_sha256",
        "proposal_id",
        "proposal_sha256",
        "kind",
        "schema_version",
        "inference_receipt",
        "validated_output",
    }
    semantic = {
        name: getattr(original, name)
        for name in type(original).model_fields
        if name not in excluded
    }
    semantic["rationale"] = (
        "A human-reviewed wording correction that preserves the admitted target and interval."
    )
    output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    validated_output = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        path=(
            f"staging/managed-review/{original.run_id}/{original.target_key}/"
            f"validated-output-{output_sha}.json"
        ),
        sha256=output_sha,
        byte_count=len(output_bytes),
    )
    prior = original.inference_receipt
    receipt = ContentAddressedInferenceReceipt.create(
        contract_id=prior.contract_id,
        contract_version=prior.contract_version,
        mode=prior.mode,
        provider=prior.provider,
        model=prior.model,
        provider_request_id=prior.provider_request_id,
        replay_source_receipt_sha256=prior.replay_source_receipt_sha256,
        replay_source_receipt_artifact=prior.replay_source_receipt_artifact,
        prompt_sha256=prior.prompt_sha256,
        response_schema_sha256=prior.response_schema_sha256,
        input_artifacts=prior.input_artifacts,
        input_envelope_sha256=prior.input_envelope_sha256,
        raw_output_sha256=output_sha,
        validated_output_sha256=output_sha,
        usage=prior.usage,
    )
    edited = ManagedRevisionPlan.create(
        **semantic,
        inference_receipt=receipt,
        validated_output=validated_output,
    )
    return ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:edited-decision",
        request_record=approved.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Attempt a deferred edited-plan decision at the store boundary.",
        items=(
            ManagedRevisionReviewOutcome(
                target_id=target.target_id,
                original_target_sha256=target.target_sha256,
                disposition=ManagedRevisionDisposition.EDIT,
                edited_plan=edited,
            ),
        ),
    )


def test_legacy_v1_request_is_readable_but_new_write_and_replay_fail_before_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    legacy = _legacy_bundle(scenario.bundle)
    command = ManagedRevisionReviewRequestCommand.create(
        bundle=legacy,
        operation_id="managed-store:legacy-request",
        requester_id="operator@example.test",
        rationale="Represent one request persisted by the legacy managed-review writer.",
    )
    before = scenario.store.conn.execute(
        "SELECT count(*) FROM change_control_managed_review_request_records"
    ).fetchone()[0]
    with pytest.raises(ManagedReviewWriteVersionError):
        scenario.store.create_managed_review_request(
            command,
            resolver=_ExplodingManagedResolver(),
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0]
        == before
    )

    with monkeypatch.context() as legacy_writer:
        legacy_writer.setattr(
            managed_store_module, "_require_v2_managed_review_write", lambda bundle: None
        )
        scenario.store.create_managed_review_request(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    reopened = scenario.store.get_managed_review(
        command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert type(reopened.request_record.command.bundle.run_binding) is ManagedRunBinding
    with pytest.raises(ManagedReviewWriteVersionError):
        scenario.store.create_managed_review_request(
            command,
            resolver=_ExplodingManagedResolver(),
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
        ).fetchone()[0]
        == 1
    )
    scenario.store.close()


def test_valid_edit_decision_is_rejected_before_resolver_transaction_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _edited_decision_command(scenario)
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(ManagedRevisionEditDeferredError):
        scenario.store.decide_managed_review(
            command,
            resolver=_ExplodingManagedResolver(),
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )

    with monkeypatch.context() as prior_writer:
        prior_writer.setattr(
            managed_store_module, "_reject_deferred_managed_edits", lambda value: None
        )
        prior_writer.setattr(
            SqliteManagedChangeControlStore,
            "_resolve_contract_and_artifacts",
            staticmethod(lambda value, resolver: None),
        )
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    with pytest.raises(ManagedRevisionEditDeferredError):
        scenario.store.decide_managed_review(
            command,
            resolver=_ExplodingManagedResolver(),
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 1
    )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decision_delivery_receipts"
        ).fetchone()[0]
        == 1
    )
    scenario.store.close()


def test_managed_authority_cas_requires_secure_coordination(tmp_path: Path) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "uncoordinated.sqlite3")
    store.init_schema()
    try:
        with pytest.raises(ManagedGenerationActivationError, match="secure coordinated"):
            store.activate_managed_generation(
                ManagedActivationCommand.model_construct(),
                capability=object(),  # type: ignore[arg-type]
                resolver=object(),  # type: ignore[arg-type]
            )
    finally:
        store.close()
