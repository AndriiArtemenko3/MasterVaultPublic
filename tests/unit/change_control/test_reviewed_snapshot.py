from __future__ import annotations

import pickle
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from test_temporal_proposal import (
    INCOMING_MANIFEST,
    PRECHANGE_MANIFEST,
    REPO_ROOT,
    _build_case,
    _build_temporal_evidence,
    _Case,
)

from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    DependencyRegistry,
    DocumentReplacementSet,
    DocumentVersionRegistry,
    RelationGraph,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    aggregate_sha256,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectEdit,
    ReviewSubjectKind,
)
from mastervault.change_control.reviewed_snapshot import (
    RepositoryVerifiedReviewedSourceNoteInventoryCapability,
    ReviewedTemporalSnapshotAuthority,
    ReviewedTemporalSnapshotAuthorityError,
    _prove_source_note_continuity,
    resolve_reviewed_temporal_snapshot,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
    SourceNoteInventoryResolutionError,
)
from mastervault.change_control.store import (
    ChangeControlCommit,
    ChangeControlSnapshot,
    SqliteChangeControlStore,
)
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.temporal_commit import commit_temporal_proposal
from mastervault.change_control.temporal_proposal import (
    TemporalProposalCommit,
    open_temporal_review,
)


@dataclass(frozen=True)
class _ReviewedFixture:
    case: _Case
    evidence: TemporalAnalysisEvidence
    commit: TemporalProposalCommit
    request_id: str
    database: Path


@pytest.fixture(scope="module")
def reviewed_fixture(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ReviewedFixture]:
    case = _build_case(tmp_path_factory.mktemp("reviewed-snapshot-authority"))
    evidence = _build_temporal_evidence(case)
    commit = commit_temporal_proposal(
        case.store,
        case.proposal,
        temporal_analysis=evidence,
        evidence_repository=case.evidence_repository,
        classification_batch=case.classification_batch,
        dependency_batch=case.dependency_batch,
        source_note_resolver=case.build_inputs["inventory_resolver"],
    )
    request = open_temporal_review(
        case.store,
        commit,
        requester_id="reviewed.snapshot.requester",
        rationale="Adjudicate the exact temporal proposal before downstream analysis.",
        operation_id="reviewed-snapshot-test:review-request",
    )
    rejected_constraint_id = next(
        subject.subject_id
        for subject in request.request.subjects
        if subject.kind == ReviewSubjectKind.TEMPORAL_CONSTRAINT
    )
    case.store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="reviewed.snapshot.approver",
            rationale="Record a complete mixed temporal decision.",
            items=tuple(
                ReviewDecisionItem(
                    kind=subject.kind,
                    subject_id=subject.subject_id,
                    original_subject_sha256=subject.subject_sha256,
                    disposition=(
                        ReviewDisposition.EDITED
                        if subject.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT
                        else (
                            ReviewDisposition.REJECTED
                            if subject.subject_id == rejected_constraint_id
                            else ReviewDisposition.ACCEPTED
                        )
                    ),
                    edit=(
                        ReviewSubjectEdit(
                            kind=subject.kind,
                            subject_id=subject.subject_id,
                            rationale="The exact evidence supports this reviewed replacement.",
                            confidence=0.91,
                        )
                        if subject.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT
                        else None
                    ),
                )
                for subject in request.request.subjects
            ),
        ),
        operation_id="reviewed-snapshot-test:review-decision",
    )
    database = case.store.db_path
    case.store.close()
    yield _ReviewedFixture(
        case=case,
        evidence=evidence,
        commit=commit,
        request_id=request.request.request_id,
        database=database,
    )


def _copied_store(fixture: _ReviewedFixture, tmp_path: Path) -> SqliteChangeControlStore:
    destination = tmp_path / "change-control.sqlite3"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture.database, destination)
    return SqliteChangeControlStore(destination)


def _resolve(
    fixture: _ReviewedFixture,
    store: SqliteChangeControlStore,
    **overrides: Any,
):
    return resolve_reviewed_temporal_snapshot(
        store,
        temporal_analysis_manifest_id=overrides.get(
            "temporal_analysis_manifest_id", fixture.evidence.manifest_id
        ),
        temporal_analysis_manifest_sha256=overrides.get(
            "temporal_analysis_manifest_sha256", fixture.evidence.manifest_sha256
        ),
        temporal_request_id=overrides.get("temporal_request_id", fixture.request_id),
        evidence_repository=overrides.get("evidence_repository", fixture.case.evidence_repository),
        source_note_resolver=overrides.get(
            "source_note_resolver", fixture.case.build_inputs["inventory_resolver"]
        ),
        read_only=overrides.get("read_only", False),
    )


def test_resolves_exact_mixed_review_and_mints_distinct_rev4_authority(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(reviewed_fixture, tmp_path)
    try:
        resolved = _resolve(reviewed_fixture, store)
        reminted = _resolve(reviewed_fixture, store)
        assert resolved.snapshot.revision == 4
        assert resolved.temporal_prerequisite.review_open_head.revision == 4
        assert resolved.binding.analysis_head.revision == 2
        assert resolved.binding.committed_head.revision == 3
        assert resolved.binding.reviewed_head.revision == 4
        assert resolved.temporal_analysis == reviewed_fixture.evidence
        assert resolved.temporal_commit.proposal == reviewed_fixture.commit.proposal
        assert resolved.temporal_commit.committed_at == reviewed_fixture.commit.committed_at
        assert resolved.temporal_commit.replayed
        assert resolved.review_request.request_id == reviewed_fixture.request_id
        assert resolved.review_decision.request_id == reviewed_fixture.request_id
        assert resolved.classification_outcomes == (
            reviewed_fixture.case.evidence_repository.resolve_batch(
                batch_id=reviewed_fixture.evidence.classification_evidence_batch_id,
                batch_sha256=(reviewed_fixture.evidence.classification_evidence_batch_sha256),
            )
        )
        assert resolved.dependency_outcomes == (
            reviewed_fixture.case.evidence_repository.resolve_batch(
                batch_id=reviewed_fixture.evidence.dependency_evidence_batch_id,
                batch_sha256=reviewed_fixture.evidence.dependency_evidence_batch_sha256,
            )
        )
        assert resolved.source_note_capability is not reminted.source_note_capability
        inventory = resolved.source_note_capability.verify(snapshot=resolved.snapshot)
        assert inventory.snapshot_revision == 4
        assert inventory.aggregate_sha256 == resolved.snapshot.aggregate_sha256
        assert inventory.notes == reviewed_fixture.evidence.source_note_inventory.notes

        replacements = resolved.snapshot.aggregate.document_replacements.assessments
        constraints = resolved.snapshot.aggregate.temporal_constraints.constraints
        assert replacements[0].status == TemporalConstraintStatus.ACCEPTED
        assert replacements[0].rationale == (
            "The exact evidence supports this reviewed replacement."
        )
        assert any(item.status == TemporalConstraintStatus.REJECTED for item in constraints)
        assert any(item.status == TemporalConstraintStatus.ACCEPTED for item in constraints)

        old_capability = reviewed_fixture.case.build_inputs["inventory_capability"]
        with pytest.raises(SourceNoteInventoryResolutionError):
            old_capability.verify(snapshot=resolved.snapshot)
    finally:
        store.close()


def test_secure_read_only_replay_uses_existing_commit_without_cas(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "secure-authority" / "change-control.sqlite3"
    database.parent.mkdir(mode=0o700)
    shutil.copy2(reviewed_fixture.database, database)
    database.chmod(0o600)
    before = database.read_bytes()
    store = SqliteChangeControlStore(database, secure_open=True, read_only=True)

    def reject_cas(*args: object, **kwargs: object) -> ChangeControlCommit:
        del args, kwargs
        raise AssertionError("read-only resolution must not invoke compare_and_swap")

    monkeypatch.setattr(store, "compare_and_swap", reject_cas)
    try:
        resolved = _resolve(reviewed_fixture, store, read_only=True)
        receipt = store.get_operation_commit(reviewed_fixture.commit.operation_id)
        assert receipt is not None
        assert receipt.replayed and receipt.changed and receipt.revision == 3
        assert receipt.aggregate_id == reviewed_fixture.commit.aggregate_id
        assert receipt.aggregate_sha256 == reviewed_fixture.commit.aggregate_sha256
        assert resolved.temporal_commit.committed_at == receipt.committed_at
        assert resolved.temporal_commit.replayed
        assert resolved.snapshot.revision == 4
        assert int(store.conn.execute("PRAGMA query_only").fetchone()[0]) == 1

        monkeypatch.setattr(store, "get_operation_commit", lambda operation_id: None)
        with pytest.raises(
            ReviewedTemporalSnapshotAuthorityError,
            match="exact temporal proposal commit receipt",
        ):
            _resolve(reviewed_fixture, store, read_only=True)
    finally:
        store.close()

    assert database.read_bytes() == before


def test_capability_rejects_serialization_snapshot_substitution_and_seal_tamper(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(reviewed_fixture, tmp_path)
    try:
        resolved = _resolve(reviewed_fixture, store)
        capability = resolved.source_note_capability
        with pytest.raises(TypeError, match="process-local"):
            pickle.dumps(resolved)
        with pytest.raises(TypeError, match="process-local"):
            pickle.dumps(capability)

        with pytest.raises(TypeError, match="service-created"):
            ReviewedTemporalSnapshotAuthority(
                snapshot=resolved.snapshot,
                temporal_analysis=resolved.temporal_analysis,
                temporal_commit=resolved.temporal_commit,
                review_request=resolved.review_request,
                review_decision=resolved.review_decision,
                classification_outcomes=resolved.classification_outcomes,
                dependency_outcomes=resolved.dependency_outcomes,
                temporal_prerequisite=resolved.temporal_prerequisite,
                binding=resolved.binding,
                source_note_capability=resolved.source_note_capability,
                _token=object(),
                _seal=resolved._seal,  # noqa: SLF001
            )

        wrong_sha = ChangeControlSnapshot(
            aggregate=resolved.snapshot.aggregate,
            revision=4,
            aggregate_sha256="0" * 64,
        )
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="exact revision-4"):
            capability.verify(snapshot=wrong_sha)

        object.__setattr__(capability, "_seal", "0" * 64)
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="seal"):
            capability.verify(snapshot=resolved.snapshot)
    finally:
        store.close()


def test_authority_outcome_substitution_breaks_complete_lineage_seal(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(reviewed_fixture, tmp_path)
    try:
        resolved = _resolve(reviewed_fixture, store)
        object.__setattr__(
            resolved,
            "classification_outcomes",
            resolved.dependency_outcomes,
        )
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="seal"):
            resolved.verify()
    finally:
        store.close()


def test_continuity_drift_and_capability_subclass_fail_closed(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(reviewed_fixture, tmp_path)
    try:
        resolved = _resolve(reviewed_fixture, store)
        analysis_snapshot = ChangeControlSnapshot(
            aggregate=reviewed_fixture.evidence.analysis_aggregate,
            revision=reviewed_fixture.evidence.analysis_head.revision,
            aggregate_sha256=reviewed_fixture.evidence.analysis_head.aggregate_sha256,
        )
        analysis_inventory = reviewed_fixture.evidence.source_note_inventory
        drifted_aggregate = ChangeControlAggregate.create(
            aggregate_id=resolved.snapshot.aggregate.aggregate_id,
            documents=DocumentVersionRegistry.create(()),
            claims=ClaimRevisionRegistry.create(()),
            relation_graph=RelationGraph.create(()),
            dependencies=DependencyRegistry.create(()),
            document_replacements=DocumentReplacementSet.create(()),
            temporal_constraints=TemporalConstraintSet.create(()),
        )
        drifted = ChangeControlSnapshot(
            aggregate=drifted_aggregate,
            revision=4,
            aggregate_sha256=aggregate_sha256(drifted_aggregate),
        )
        with pytest.raises(
            ReviewedTemporalSnapshotAuthorityError,
            match="document registry",
        ):
            _prove_source_note_continuity(
                analysis_snapshot=analysis_snapshot,
                reviewed_snapshot=drifted,
                analysis_inventory=analysis_inventory,
                persisted_analysis_inventory=analysis_inventory,
            )

        claim_drift_aggregate = ChangeControlAggregate.create(
            aggregate_id=resolved.snapshot.aggregate.aggregate_id,
            documents=analysis_snapshot.aggregate.documents,
            claims=ClaimRevisionRegistry.create(()),
            relation_graph=RelationGraph.create(()),
            dependencies=DependencyRegistry.create(()),
            document_replacements=DocumentReplacementSet.create(()),
            temporal_constraints=TemporalConstraintSet.create(()),
        )
        claim_drift = ChangeControlSnapshot(
            aggregate=claim_drift_aggregate,
            revision=4,
            aggregate_sha256=aggregate_sha256(claim_drift_aggregate),
        )
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="claim registry"):
            _prove_source_note_continuity(
                analysis_snapshot=analysis_snapshot,
                reviewed_snapshot=claim_drift,
                analysis_inventory=analysis_inventory,
                persisted_analysis_inventory=analysis_inventory,
            )

        original_note = analysis_inventory.notes[0]
        note_variants = (
            CanonicalSourceNoteSnapshot.create(
                document=original_note.document,
                source_note_path=f"tampered/{original_note.source_note_path}",
                source_note_utf8=original_note.source_note_utf8,
                body_start_char=original_note.body_start_char,
            ),
            CanonicalSourceNoteSnapshot.create(
                document=original_note.document,
                source_note_path=original_note.source_note_path,
                source_note_utf8=f"{original_note.source_note_utf8}\nTampered bytes.",
                body_start_char=original_note.body_start_char,
            ),
            CanonicalSourceNoteSnapshot.create(
                document=original_note.document,
                source_note_path=original_note.source_note_path,
                source_note_utf8=original_note.source_note_utf8,
                body_start_char=original_note.body_start_char + 1,
            ),
        )
        for tampered_note in note_variants:
            tampered_inventory = SourceNoteInventory.create(
                snapshot=analysis_snapshot,
                notes=(tampered_note, *analysis_inventory.notes[1:]),
            )
            with pytest.raises(
                ReviewedTemporalSnapshotAuthorityError,
                match="fresh repository SourceNote inventory",
            ):
                _prove_source_note_continuity(
                    analysis_snapshot=analysis_snapshot,
                    reviewed_snapshot=resolved.snapshot,
                    analysis_inventory=tampered_inventory,
                    persisted_analysis_inventory=analysis_inventory,
                )

        class _FakeCapability(RepositoryVerifiedReviewedSourceNoteInventoryCapability):
            pass

        fake = object.__new__(_FakeCapability)
        for name in (
            "_binding",
            "_analysis_snapshot",
            "_reviewed_snapshot",
            "_analysis_inventory",
            "_reviewed_inventory",
            "_token",
            "_seal",
        ):
            object.__setattr__(fake, name, getattr(resolved.source_note_capability, name))
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="type"):
            fake.verify(snapshot=resolved.snapshot)
    finally:
        store.close()


def test_exact_concrete_authorities_and_request_binding_are_required(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    class _FakeStore(SqliteChangeControlStore):
        pass

    class _FakeResolver(RepositorySourceNoteInventoryResolver):
        pass

    class _FakeRepository(FilesystemInferenceEvidenceRepository):
        pass

    fake_store = _FakeStore(tmp_path / "fake-store.sqlite3")
    fake_resolver = _FakeResolver(
        repo_root=tmp_path / "wrong-root",
        prechange_manifest_path=tmp_path / "wrong-prechange.yaml",
        incoming_manifest_path=tmp_path / "wrong-incoming.yaml",
        verified_bootstrap=reviewed_fixture.case.build_inputs["verified_bootstrap"],
    )
    store = _copied_store(reviewed_fixture, tmp_path / "exact")
    fake_repository = _FakeRepository(tmp_path / "fake-evidence")
    foreign_repository = FilesystemInferenceEvidenceRepository(tmp_path / "foreign-evidence")
    try:
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="exact SQLite"):
            _resolve(reviewed_fixture, fake_store)
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="exact repository"):
            _resolve(reviewed_fixture, store, source_note_resolver=fake_resolver)
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="exact filesystem"):
            _resolve(reviewed_fixture, store, evidence_repository=fake_repository)
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(reviewed_fixture, store, evidence_repository=foreign_repository)
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="locator"):
            _resolve(
                reviewed_fixture,
                store,
                temporal_analysis_manifest_id=f"temporal-analysis:{'0' * 64}",
            )
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(
                reviewed_fixture,
                store,
                temporal_request_id=f"reviewreq:{'0' * 64}",
            )
    finally:
        fake_store.close()
        store.close()


def test_missing_temporal_manifest_and_corrupt_decision_fail_closed(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    manifest = (
        reviewed_fixture.case.evidence_repository.root
        / reviewed_fixture.commit.temporal_analysis_manifest_path
    )
    content = manifest.read_bytes()
    store = _copied_store(reviewed_fixture, tmp_path / "manifest")
    manifest.unlink()
    try:
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(reviewed_fixture, store)
    finally:
        manifest.write_bytes(content)
        store.close()

    corrupt = _copied_store(reviewed_fixture, tmp_path / "decision")
    try:
        corrupt.conn.execute(
            "UPDATE change_control_review_decisions "
            "SET decision_payload_sha256=? WHERE request_id=?",
            ("0" * 64, reviewed_fixture.request_id),
        )
        corrupt.conn.commit()
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(reviewed_fixture, corrupt)
    finally:
        corrupt.close()


def test_wrong_store_and_advanced_head_fail_closed(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    wrong_store = SqliteChangeControlStore(tmp_path / "wrong-store.sqlite3")
    wrong_store.init_schema()
    try:
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(reviewed_fixture, wrong_store)
    finally:
        wrong_store.close()

    advanced_store = _copied_store(reviewed_fixture, tmp_path / "advanced")
    try:
        current = advanced_store.load(reviewed_fixture.commit.aggregate_id)
        assert current is not None and current.revision == 4
        payload = current.aggregate.model_dump(mode="json")
        payload["relation_graph"]["assessments"][0]["rationale"] = (
            "A later non-review annotation advanced the aggregate head."
        )
        advanced_aggregate = ChangeControlAggregate.model_validate(payload)
        receipt = advanced_store.compare_and_swap(
            advanced_aggregate,
            expected_revision=4,
            operation_id="reviewed-snapshot-test:intervening-revision",
        )
        assert receipt.revision == 5
        with pytest.raises(ReviewedTemporalSnapshotAuthorityError):
            _resolve(reviewed_fixture, advanced_store)
    finally:
        advanced_store.close()


def test_fresh_process_reopens_durable_authorities_and_remints_capability(
    reviewed_fixture: _ReviewedFixture,
    tmp_path: Path,
) -> None:
    database = tmp_path / "change-control.sqlite3"
    shutil.copy2(reviewed_fixture.database, database)
    script = """
from pathlib import Path
import sys

from mastervault.change_control.bootstrap import (
    build_verified_prechange_aggregate,
    create_verified_analysis_bootstrap_capability,
)
from mastervault.change_control.incoming import load_verified_incoming_event
from mastervault.change_control.inference_repository import FilesystemInferenceEvidenceRepository
from mastervault.change_control.reviewed_snapshot import resolve_reviewed_temporal_snapshot
from mastervault.change_control.seed import load_verified_prechange_seed_manifest_from_repository
from mastervault.change_control.source_note_inventory import RepositorySourceNoteInventoryResolver
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence

db, evidence_root, repo_root, prechange_path, incoming_path = map(Path, sys.argv[1:6])
manifest_id, manifest_sha256, request_id = sys.argv[6:9]
repository = FilesystemInferenceEvidenceRepository(evidence_root)
analysis = TemporalAnalysisEvidence.from_canonical_bytes(
    repository.resolve_temporal_analysis_manifest(
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
    )
)
seed = load_verified_prechange_seed_manifest_from_repository(
    repo_root=repo_root,
    manifest_path=prechange_path,
)
incoming = load_verified_incoming_event(repo_root=repo_root, manifest_path=incoming_path)
prechange = build_verified_prechange_aggregate(repo_root=repo_root, manifest_context=seed)
bootstrap = analysis.proposal.binding.analysis_bootstrap
verified_bootstrap = create_verified_analysis_bootstrap_capability(
    repo_root=repo_root,
    seed_context=seed,
    incoming_event=incoming,
    prechange_aggregate=prechange,
    analysis_aggregate=analysis.analysis_aggregate,
    prechange_operation_id=bootstrap.prechange_operation_id,
    analysis_operation_id=bootstrap.analysis_operation_id,
)
resolver = RepositorySourceNoteInventoryResolver(
    repo_root=repo_root,
    prechange_manifest_path=prechange_path,
    incoming_manifest_path=incoming_path,
    verified_bootstrap=verified_bootstrap,
)
store = SqliteChangeControlStore(db)
resolved = resolve_reviewed_temporal_snapshot(
    store,
    temporal_analysis_manifest_id=manifest_id,
    temporal_analysis_manifest_sha256=manifest_sha256,
    temporal_request_id=request_id,
    evidence_repository=repository,
    source_note_resolver=resolver,
)
assert resolved.snapshot.revision == 4
assert resolved.source_note_capability.verify(snapshot=resolved.snapshot).snapshot_revision == 4
store.close()
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(database),
            str(reviewed_fixture.case.evidence_repository.root),
            str(REPO_ROOT),
            str(PRECHANGE_MANIFEST),
            str(INCOMING_MANIFEST),
            reviewed_fixture.evidence.manifest_id,
            reviewed_fixture.evidence.manifest_sha256,
            reviewed_fixture.request_id,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
