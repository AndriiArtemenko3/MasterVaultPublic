from __future__ import annotations

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

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceConflictError,
    InferenceEvidenceResolutionError,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
)
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.temporal_commit import (
    TemporalProposalAuthorityError,
    commit_temporal_proposal,
)


@dataclass(frozen=True)
class _AuthorityFixture:
    case: _Case
    evidence: TemporalAnalysisEvidence
    database: Path


@pytest.fixture(scope="module")
def authority_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_AuthorityFixture]:
    case = _build_case(tmp_path_factory.mktemp("temporal-commit-authority"))
    evidence = _build_temporal_evidence(case)
    database = case.store.db_path
    case.store.close()
    yield _AuthorityFixture(case=case, evidence=evidence, database=database)


def _copied_store(authority: _AuthorityFixture, tmp_path: Path) -> SqliteChangeControlStore:
    destination = tmp_path / "change-control.sqlite3"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(authority.database, destination)
    return SqliteChangeControlStore(destination)


def _commit(
    authority: _AuthorityFixture,
    store: SqliteChangeControlStore,
    *,
    classification_batch: RepositoryVerifiedInferenceEvidenceBatch | None = None,
    dependency_batch: RepositoryVerifiedInferenceEvidenceBatch | None = None,
    evidence_repository: FilesystemInferenceEvidenceRepository | None = None,
    source_note_resolver: RepositorySourceNoteInventoryResolver | Any | None = None,
):
    case = authority.case
    return commit_temporal_proposal(
        store,
        case.proposal,
        temporal_analysis=authority.evidence,
        evidence_repository=evidence_repository or case.evidence_repository,
        classification_batch=classification_batch or case.classification_batch,
        dependency_batch=dependency_batch or case.dependency_batch,
        source_note_resolver=(
            source_note_resolver
            if source_note_resolver is not None
            else case.build_inputs["inventory_resolver"]
        ),
    )


def _manifest_path(authority: _AuthorityFixture) -> Path:
    return (
        authority.case.evidence_repository.root
        / "temporal/evidence/analyses"
        / f"{authority.evidence.manifest_sha256}.json"
    )


def test_missing_batch_and_capability_substitution_fail_before_cas(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(authority_fixture, tmp_path)
    case = authority_fixture.case
    marker = (
        case.evidence_repository.root
        / "inference/evidence/batches"
        / f"{case.classification_batch.batch_sha256}.json"
    )
    marker_bytes = marker.read_bytes()
    marker.unlink()
    try:
        with pytest.raises(InferenceEvidenceResolutionError, match="missing"):
            _commit(authority_fixture, store)
        snapshot = store.load(case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        marker.write_bytes(marker_bytes)
        store.close()

    swapped = _copied_store(authority_fixture, tmp_path / "swapped")
    try:
        with pytest.raises(TemporalProposalAuthorityError, match="capability differs"):
            _commit(
                authority_fixture,
                swapped,
                classification_batch=case.dependency_batch,
                dependency_batch=case.classification_batch,
            )
        snapshot = swapped.load(case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        swapped.close()

    foreign_repository = FilesystemInferenceEvidenceRepository(tmp_path / "foreign-evidence")
    foreign_batch = foreign_repository.persist_batch(case.build_inputs["classification_outcomes"])
    foreign = _copied_store(authority_fixture, tmp_path / "foreign")
    try:
        with pytest.raises(TemporalProposalAuthorityError, match="capability differs"):
            _commit(
                authority_fixture,
                foreign,
                classification_batch=foreign_batch,
            )
        snapshot = foreign.load(case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        foreign.close()


def test_override_capable_source_inventory_resolver_subclass_is_rejected_before_cas(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    class _FakeResolver(RepositorySourceNoteInventoryResolver):
        def resolve_source_note_inventory(self, *, snapshot: object) -> object:
            del snapshot
            return authority_fixture.case.build_inputs["inventory_capability"]

    fake = _FakeResolver(
        repo_root=tmp_path / "missing-repository",
        prechange_manifest_path=tmp_path / "missing-prechange.yaml",
        incoming_manifest_path=tmp_path / "missing-incoming.yaml",
        verified_bootstrap=authority_fixture.case.build_inputs["verified_bootstrap"],
    )

    store = _copied_store(authority_fixture, tmp_path)
    try:
        with pytest.raises(TemporalProposalAuthorityError, match="repository backed"):
            _commit(
                authority_fixture,
                store,
                source_note_resolver=fake,
            )
        snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        store.close()


def test_override_capable_evidence_repository_subclass_is_rejected_before_cas(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    class _FakeEvidenceRepository(FilesystemInferenceEvidenceRepository):
        def resolve_batch(self, **kwargs: Any) -> tuple[Any, ...]:
            del kwargs
            return authority_fixture.case.build_inputs["classification_outcomes"]

        def resolve_temporal_analysis_manifest(self, **kwargs: Any) -> bytes:
            del kwargs
            return authority_fixture.evidence.canonical_bytes()

        def persist_temporal_analysis_manifest(self, **kwargs: Any) -> str:
            del kwargs
            return "temporal/evidence/analyses/forged.json"

    fake_repository = _FakeEvidenceRepository(tmp_path / "fake-evidence")
    store = _copied_store(authority_fixture, tmp_path / "fake-store")
    try:
        with pytest.raises(TemporalProposalAuthorityError, match="filesystem evidence"):
            _commit(
                authority_fixture,
                store,
                evidence_repository=fake_repository,
            )
        snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        store.close()


def test_override_capable_batch_capability_subclass_is_rejected_before_cas(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    class _FakeBatchCapability(RepositoryVerifiedInferenceEvidenceBatch):
        def verify(self, **kwargs: Any) -> tuple[Any, ...]:
            del kwargs
            return authority_fixture.case.build_inputs["classification_outcomes"]

    original = authority_fixture.case.classification_batch
    fake = _FakeBatchCapability(
        batch_id=original.batch_id,
        batch_sha256=original.batch_sha256,
        repository_id=original.repository_id,
        repository_root=original.repository_root,
        execution_ids=original.execution_ids,
        receipt_artifact_ids=original.receipt_artifact_ids,
        outcome_sha256s=original.outcome_sha256s,
        _token=original._token,  # noqa: SLF001
        _seal=original._seal,  # noqa: SLF001
    )
    store = _copied_store(authority_fixture, tmp_path)
    try:
        with pytest.raises(TemporalProposalAuthorityError, match="repository verified"):
            _commit(
                authority_fixture,
                store,
                classification_batch=fake,
            )
        snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        store.close()


def test_temporal_manifest_is_verified_before_cas_and_orphan_is_retryable(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _copied_store(authority_fixture, tmp_path)
    manifest_path = _manifest_path(authority_fixture)
    original_compare_and_swap = store.compare_and_swap

    def fail_after_manifest(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        assert manifest_path.read_bytes() == authority_fixture.evidence.canonical_bytes()
        raise RuntimeError("injected failure after durable evidence")

    monkeypatch.setattr(store, "compare_and_swap", fail_after_manifest)
    with pytest.raises(RuntimeError, match="after durable evidence"):
        _commit(authority_fixture, store)
    snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
    assert snapshot is not None and snapshot.revision == 2

    monkeypatch.setattr(store, "compare_and_swap", original_compare_and_swap)
    committed = _commit(authority_fixture, store)
    assert committed.revision == 3
    assert committed.temporal_analysis_manifest_path == (
        f"temporal/evidence/analyses/{authority_fixture.evidence.manifest_sha256}.json"
    )
    store.close()


def test_conflicting_manifest_blocks_commit_and_is_never_overwritten(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    store = _copied_store(authority_fixture, tmp_path)
    content = authority_fixture.evidence.canonical_bytes()
    path = _manifest_path(authority_fixture)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * len(content))
    try:
        with pytest.raises(InferenceEvidenceConflictError, match="existing"):
            _commit(authority_fixture, store)
        assert path.read_bytes() == b"x" * len(content)
        snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 2
    finally:
        path.write_bytes(content)
        store.close()


def test_lost_ack_replays_exact_commit_but_missing_manifest_blocks_later_authority(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _copied_store(authority_fixture, tmp_path)
    original_delivery = store._deliver_commit  # noqa: SLF001

    def lose_ack(receipt: object) -> object:
        del receipt
        raise RuntimeError("injected lost acknowledgement")

    monkeypatch.setattr(store, "_deliver_commit", lose_ack)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        _commit(authority_fixture, store)
    snapshot = store.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
    assert snapshot is not None and snapshot.revision == 3

    monkeypatch.setattr(store, "_deliver_commit", original_delivery)
    replay = _commit(authority_fixture, store)
    assert replay.replayed

    manifest_path = _manifest_path(authority_fixture)
    manifest_bytes = manifest_path.read_bytes()
    manifest_path.unlink()
    try:
        with pytest.raises(InferenceEvidenceResolutionError, match="missing"):
            _commit(authority_fixture, store)
    finally:
        manifest_path.write_bytes(manifest_bytes)
        store.close()


def test_fresh_process_reopens_all_authorities_before_commit(
    authority_fixture: _AuthorityFixture,
    tmp_path: Path,
) -> None:
    database = tmp_path / "change-control.sqlite3"
    shutil.copy2(authority_fixture.database, database)
    request = tmp_path / "temporal-analysis.json"
    request.write_bytes(authority_fixture.evidence.canonical_bytes())
    script = """
from pathlib import Path
import sys
from mastervault.change_control.bootstrap import bootstrap_analysis_aggregate
from mastervault.change_control.inference_repository import FilesystemInferenceEvidenceRepository
from mastervault.change_control.source_note_inventory import RepositorySourceNoteInventoryResolver
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.temporal_commit import commit_temporal_proposal

db, evidence_root, repo_root, prechange, incoming, request = map(Path, sys.argv[1:])
store = SqliteChangeControlStore(db)
bootstrap = bootstrap_analysis_aggregate(
    repo_root=repo_root,
    prechange_manifest_path=prechange,
    incoming_manifest_path=incoming,
    store=store,
    prechange_operation_id="temporal-test:prechange",
    analysis_operation_id="temporal-test:analysis",
)
analysis = TemporalAnalysisEvidence.from_canonical_bytes(request.read_bytes())
repository = FilesystemInferenceEvidenceRepository(evidence_root)
_, classification = repository.resolve_verified_batch(
    batch_id=analysis.classification_evidence_batch_id,
    batch_sha256=analysis.classification_evidence_batch_sha256,
)
_, dependency = repository.resolve_verified_batch(
    batch_id=analysis.dependency_evidence_batch_id,
    batch_sha256=analysis.dependency_evidence_batch_sha256,
)
resolver = RepositorySourceNoteInventoryResolver(
    repo_root=repo_root,
    prechange_manifest_path=prechange,
    incoming_manifest_path=incoming,
    verified_bootstrap=bootstrap.verification_capability,
)
receipt = commit_temporal_proposal(
    store,
    analysis.proposal,
    temporal_analysis=analysis,
    evidence_repository=repository,
    classification_batch=classification,
    dependency_batch=dependency,
    source_note_resolver=resolver,
)
assert receipt.revision == 3
store.close()
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(database),
            str(authority_fixture.case.evidence_repository.root),
            str(REPO_ROOT),
            str(PRECHANGE_MANIFEST),
            str(INCOMING_MANIFEST),
            str(request),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr

    reopened = SqliteChangeControlStore(database)
    try:
        snapshot = reopened.load(authority_fixture.case.proposal.proposed_aggregate.aggregate_id)
        assert snapshot is not None and snapshot.revision == 3
        assert snapshot.aggregate == authority_fixture.case.proposal.proposed_aggregate
    finally:
        reopened.close()
