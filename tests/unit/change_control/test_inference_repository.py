from __future__ import annotations

import hashlib
import importlib
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import mastervault.change_control.inference_repository as inference_repository_module
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceConflictError,
    InferenceEvidenceRepositoryError,
    InferenceEvidenceResolutionError,
    InferenceEvidenceUnsupportedPlatformError,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    RecordedInferenceExecution,
    RecordedInferenceOutcome,
    run_classification_inference,
)

_recorded_fixtures: Any = importlib.import_module("test_recorded_inference")
ALGORITHM: bytes = _recorded_fixtures.ALGORITHM
PROMPT: bytes = _recorded_fixtures.PROMPT
SCHEMA: bytes = _recorded_fixtures.SCHEMA
_classification_fixture: Any = _recorded_fixtures._classification_fixture
_contract: Any = _recorded_fixtures._contract
_run_live_classification: Any = _recorded_fixtures._run_live_classification
_Provider: Any = _recorded_fixtures._Provider
_Resolver: Any = _recorded_fixtures._Resolver
_classification_wire: Any = _recorded_fixtures._classification_wire


@pytest.fixture
def live() -> RecordedInferenceOutcome:
    outcome, _provider = _run_live_classification()
    return cast(RecordedInferenceOutcome, outcome)


@pytest.fixture
def evidence_repository(tmp_path: Path) -> FilesystemInferenceEvidenceRepository:
    return FilesystemInferenceEvidenceRepository(tmp_path / "inference-evidence")


def _artifact_path(
    repository: FilesystemInferenceEvidenceRepository,
    outcome: RecordedInferenceOutcome,
) -> Path:
    return repository.root / outcome.artifacts[0].artifact.path


def _replay(
    *,
    repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> RecordedInferenceOutcome:
    workload, shard, _newer, _older = _classification_fixture()
    return run_classification_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=repository,
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )


def _uncommitted_replay(live: RecordedInferenceOutcome) -> RecordedInferenceOutcome:
    workload, shard, _newer, _older = _classification_fixture()
    return run_classification_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=_Resolver(live, []),
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )


def _live_with_inputs(
    *,
    algorithm: bytes = ALGORITHM,
    prompt: bytes = PROMPT,
    schema: bytes = SCHEMA,
) -> RecordedInferenceOutcome:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider([_classification_wire(shard.pairs[0].candidate.pair_id, newer)])
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=hashlib.sha256(algorithm).hexdigest(),
        contract_id="recorded-change-control-v1",
        contract_version=1,
        mode=InferenceExecutionMode.LIVE,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=hashlib.sha256(prompt).hexdigest(),
        response_schema_sha256=hashlib.sha256(schema).hexdigest(),
    )
    return run_classification_inference(
        contract=contract,
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=algorithm,
        prompt_bytes=prompt,
        response_schema_bytes=schema,
        provider=provider,
    )


def _rebind_replay_source_execution(
    replay: RecordedInferenceOutcome,
    source_execution_sha256: str,
) -> RecordedInferenceOutcome:
    values = replay.execution.model_dump(
        mode="json",
        exclude={"execution_id", "execution_sha256"},
    )
    values["replay_source_execution_sha256"] = source_execution_sha256
    digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
    execution = RecordedInferenceExecution.model_validate_json(
        canonical_json_bytes(
            {
                **values,
                "execution_id": f"inference-exec:{digest}",
                "execution_sha256": digest,
            }
        )
    )
    return RecordedInferenceOutcome(
        execution=execution,
        classification_output=replay.classification_output,
        dependency_output=replay.dependency_output,
        artifacts=replay.artifacts,
    )


def test_live_persist_reopens_exact_artifacts_and_stable_batch_reference(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)

    assert capability.batch_id == f"inference-batch:{capability.batch_sha256}"
    assert capability.outcome_count == 1
    assert capability.repository_id == evidence_repository.repository_id
    assert (
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )
        == live
    )
    assert evidence_repository.resolve_batch(
        batch_id=capability.batch_id,
        batch_sha256=capability.batch_sha256,
    ) == (live,)
    for payload in live.artifacts:
        assert (evidence_repository.root / payload.artifact.path).read_bytes() == (
            payload.content_utf8.encode("utf-8")
        )


def test_pre_impact_outcome_shape_remains_exactly_reopenable_by_a_fresh_handle(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    manifest = (
        evidence_repository.root
        / "inference/evidence/outcomes"
        / f"{capability.outcome_sha256s[0]}.json"
    ).read_bytes()

    assert b'"impact_output"' not in manifest
    fresh = FilesystemInferenceEvidenceRepository(evidence_repository.root)
    reopened, reminted = fresh.resolve_verified_batch(
        batch_id=capability.batch_id,
        batch_sha256=capability.batch_sha256,
    )
    assert reopened == (live,)
    assert reminted.verify(repository=fresh, outcomes=reopened) == reopened


def test_exact_idempotency_is_a_create_only_noop(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    first = evidence_repository.persist_outcome(live)
    before = {
        path.relative_to(evidence_repository.root): (
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in evidence_repository.root.rglob("*")
        if path.is_file()
    }

    second = evidence_repository.persist_outcome(live)
    after = {
        path.relative_to(evidence_repository.root): (
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in evidence_repository.root.rglob("*")
        if path.is_file()
    }

    assert second.batch_id == first.batch_id
    assert second.batch_sha256 == first.batch_sha256
    assert after == before


def test_exact_retry_resynchronizes_existing_file_and_parent(
    monkeypatch: pytest.MonkeyPatch,
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    content = canonical_json_bytes({"schema_version": 1, "kind": "durability-retry"})
    digest = hashlib.sha256(content).hexdigest()
    manifest_id = f"temporal-analysis:{digest}"
    evidence_repository.persist_temporal_analysis_manifest(
        manifest_id=manifest_id,
        manifest_sha256=digest,
        content=content,
    )
    real_fsync = os.fsync
    synchronized_modes: list[int] = []

    def record_fsync(fd: int) -> None:
        synchronized_modes.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(inference_repository_module.os, "fsync", record_fsync)

    evidence_repository.persist_temporal_analysis_manifest(
        manifest_id=manifest_id,
        manifest_sha256=digest,
        content=content,
    )

    assert any(stat.S_ISREG(mode) for mode in synchronized_modes)
    assert any(stat.S_ISDIR(mode) for mode in synchronized_modes)


def test_temporal_analysis_manifest_is_create_only_hash_bound_and_reopened(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    content = canonical_json_bytes({"schema_version": 1, "kind": "temporal-analysis-test"})
    digest = hashlib.sha256(content).hexdigest()
    manifest_id = f"temporal-analysis:{digest}"

    relative = evidence_repository.persist_temporal_analysis_manifest(
        manifest_id=manifest_id,
        manifest_sha256=digest,
        content=content,
    )
    assert relative == f"temporal/evidence/analyses/{digest}.json"
    assert (
        evidence_repository.resolve_temporal_analysis_manifest(
            manifest_id=manifest_id,
            manifest_sha256=digest,
        )
        == content
    )
    assert (
        evidence_repository.persist_temporal_analysis_manifest(
            manifest_id=manifest_id,
            manifest_sha256=digest,
            content=content,
        )
        == relative
    )

    (evidence_repository.root / relative).write_bytes(b"x" * len(content))
    with pytest.raises(InferenceEvidenceResolutionError, match="persisted bytes"):
        evidence_repository.resolve_temporal_analysis_manifest(
            manifest_id=manifest_id,
            manifest_sha256=digest,
        )
    with pytest.raises(InferenceEvidenceConflictError, match="existing"):
        evidence_repository.persist_temporal_analysis_manifest(
            manifest_id=manifest_id,
            manifest_sha256=digest,
            content=content,
        )


def test_temporal_analysis_manifest_rejects_mismatched_public_identity_before_write(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    content = b"{}"
    digest = hashlib.sha256(content).hexdigest()

    with pytest.raises(InferenceEvidenceRepositoryError, match="ID"):
        evidence_repository.persist_temporal_analysis_manifest(
            manifest_id=f"temporal-analysis:{'f' * 64}",
            manifest_sha256=digest,
            content=content,
        )

    assert not (evidence_repository.root / "temporal").exists()


def test_temporal_analysis_retry_cleans_bounded_crash_residue(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    analysis_dir = evidence_repository.root / "temporal/evidence/analyses"
    analysis_dir.mkdir(parents=True)
    pending = analysis_dir / f"pending-{'d' * 32}"
    pending.write_bytes(b"interrupted-temporal-write")
    content = canonical_json_bytes({"schema_version": 1, "kind": "cleanup-retry"})
    digest = hashlib.sha256(content).hexdigest()

    evidence_repository.persist_temporal_analysis_manifest(
        manifest_id=f"temporal-analysis:{digest}",
        manifest_sha256=digest,
        content=content,
    )

    assert not pending.exists()


def test_temporal_analysis_pending_special_file_fails_closed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    analysis_dir = evidence_repository.root / "temporal/evidence/analyses"
    analysis_dir.mkdir(parents=True)
    pending = analysis_dir / f"pending-{'e' * 32}"
    os.mkfifo(pending)
    content = canonical_json_bytes({"schema_version": 1, "kind": "unsafe-residue"})
    digest = hashlib.sha256(content).hexdigest()

    with pytest.raises(InferenceEvidenceConflictError, match="pending-file residue"):
        evidence_repository.persist_temporal_analysis_manifest(
            manifest_id=f"temporal-analysis:{digest}",
            manifest_sha256=digest,
            content=content,
        )

    assert pending.exists()


def test_retry_cleans_bounded_pending_receipt_residue_before_target_link(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    binding_dir.mkdir(parents=True)
    pending = binding_dir / f"pending-{'a' * 32}"
    pending.write_bytes(b"interrupted-before-link")

    evidence_repository.persist_outcome(live)

    assert not pending.exists()
    assert (
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )
        == live
    )


def test_retry_cleans_pending_receipt_residue_after_target_link(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    first = evidence_repository.persist_outcome(live)
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    pending = binding_dir / f"pending-{'b' * 32}"
    pending.write_bytes(b"interrupted-after-link")

    second = evidence_repository.persist_outcome(live)

    assert not pending.exists()
    assert second.batch_id == first.batch_id


def test_pending_receipt_special_file_is_not_cleaned_or_followed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    binding_dir.mkdir(parents=True)
    pending = binding_dir / f"pending-{'c' * 32}"
    os.mkfifo(pending)

    with pytest.raises(InferenceEvidenceRepositoryError, match="pending-file residue"):
        evidence_repository.persist_outcome(live)

    assert pending.exists()


def test_excessive_pending_receipt_residue_fails_closed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    binding_dir.mkdir(parents=True)
    for ordinal in range(inference_repository_module.MAX_PENDING_FILES_PER_DIRECTORY_V1 + 1):
        (binding_dir / f"pending-{ordinal:032x}").write_bytes(b"partial")

    with pytest.raises(InferenceEvidenceRepositoryError, match="excessive pending-file"):
        evidence_repository.persist_outcome(live)

    assert len(tuple(binding_dir.iterdir())) == (
        inference_repository_module.MAX_PENDING_FILES_PER_DIRECTORY_V1 + 1
    )


def test_replay_runs_provider_free_against_concrete_durable_resolver(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)

    replay = _replay(repository=evidence_repository, live=live)

    assert replay.execution.receipt.mode == InferenceExecutionMode.REPLAY
    assert replay.execution.attempts == ()
    assert replay.classification_output == live.classification_output


def test_replay_persistence_requires_an_already_committed_live_source(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    replay = _uncommitted_replay(live)

    with pytest.raises(InferenceEvidenceRepositoryError, match="already-committed LIVE"):
        evidence_repository.persist_outcome(replay)

    assert not (evidence_repository.root / replay.execution.receipt_artifact.path).exists()
    assert not (evidence_repository.root / "inference/evidence/batches").exists()


def test_recomputed_replay_with_wrong_source_execution_sha_fails_before_write(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    replay = _replay(repository=evidence_repository, live=live)
    forged = _rebind_replay_source_execution(replay, "f" * 64)

    with pytest.raises(InferenceEvidenceRepositoryError, match="already-committed LIVE"):
        evidence_repository.persist_outcome(forged)

    assert not (evidence_repository.root / forged.execution.receipt_artifact.path).exists()


def test_receipt_authority_requires_committed_batch_membership(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    marker = (
        evidence_repository.root / "inference/evidence/batches" / f"{capability.batch_sha256}.json"
    )
    marker.unlink()

    with pytest.raises(InferenceEvidenceResolutionError, match="committed batch"):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )


def test_overlapping_batches_preserve_deterministic_committed_membership(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    first = evidence_repository.persist_outcome(live)
    replay = _replay(repository=evidence_repository, live=live)
    second = evidence_repository.persist_batch((live, replay))
    first_marker = (
        evidence_repository.root / "inference/evidence/batches" / f"{first.batch_sha256}.json"
    )
    first_marker.unlink()

    assert (
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )
        == live
    )
    assert second.batch_sha256 != first.batch_sha256


def test_batch_capability_and_manifest_reverify_exact_canonical_set(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    replay = _replay(repository=evidence_repository, live=live)

    capability = evidence_repository.persist_batch((replay, live))
    reopened = capability.verify(
        repository=evidence_repository,
        outcomes=(live, replay),
    )

    assert reopened == tuple(sorted((live, replay), key=lambda item: item.execution.execution_id))
    assert capability.execution_ids == tuple(item.execution.execution_id for item in reopened)
    assert (
        evidence_repository.resolve_batch(
            batch_id=capability.batch_id,
            batch_sha256=capability.batch_sha256,
        )
        == reopened
    )


def test_fresh_repository_handle_reopens_and_remints_exact_batch_authority(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    persisted = evidence_repository.persist_outcome(live)
    fresh = FilesystemInferenceEvidenceRepository(evidence_repository.root)

    outcomes, capability = fresh.resolve_verified_batch(
        batch_id=persisted.batch_id,
        batch_sha256=persisted.batch_sha256,
    )

    assert outcomes == (live,)
    assert capability.verify(repository=fresh, outcomes=outcomes) == outcomes
    assert capability.repository_id == evidence_repository.repository_id


def test_fresh_batch_remint_synchronizes_commit_marker_and_parent(
    monkeypatch: pytest.MonkeyPatch,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    persisted = evidence_repository.persist_outcome(live)
    fresh = FilesystemInferenceEvidenceRepository(evidence_repository.root)
    real_fsync = os.fsync
    synchronized_modes: list[int] = []

    def record_fsync(fd: int) -> None:
        synchronized_modes.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(inference_repository_module.os, "fsync", record_fsync)

    outcomes, capability = fresh.resolve_verified_batch(
        batch_id=persisted.batch_id,
        batch_sha256=persisted.batch_sha256,
    )

    assert outcomes == (live,)
    assert capability.batch_sha256 == persisted.batch_sha256
    assert any(stat.S_ISREG(mode) for mode in synchronized_modes)
    assert any(stat.S_ISDIR(mode) for mode in synchronized_modes)


def test_fresh_batch_remint_fails_before_authority_when_parent_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    persisted = evidence_repository.persist_outcome(live)
    fresh = FilesystemInferenceEvidenceRepository(evidence_repository.root)
    real_fsync = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated directory synchronization failure")
        real_fsync(fd)

    monkeypatch.setattr(inference_repository_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(InferenceEvidenceConflictError, match="durably synchronized"):
        fresh.resolve_verified_batch(
            batch_id=persisted.batch_id,
            batch_sha256=persisted.batch_sha256,
        )


def test_capability_rejects_mixed_repository_roots(
    tmp_path: Path,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    other = FilesystemInferenceEvidenceRepository(tmp_path / "other-evidence")

    with pytest.raises(InferenceEvidenceResolutionError, match="does not bind"):
        capability.verify(repository=other, outcomes=(live,))


def test_batch_rejects_duplicate_execution_and_receipt_ids(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    with pytest.raises(InferenceEvidenceConflictError, match="duplicate execution"):
        evidence_repository.persist_batch((live, live))


def test_missing_artifact_fails_replay_resolution_closed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    _artifact_path(evidence_repository, live).unlink()

    with pytest.raises(InferenceEvidenceResolutionError):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )


def test_tampered_or_substituted_artifact_bytes_fail_closed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    path = _artifact_path(evidence_repository, live)
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))

    with pytest.raises(InferenceEvidenceResolutionError, match="artifact"):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )


def test_missing_batch_commit_marker_is_partial_and_not_resolvable(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    batch_path = (
        evidence_repository.root / "inference/evidence/batches" / f"{capability.batch_sha256}.json"
    )
    batch_path.unlink()

    with pytest.raises(InferenceEvidenceResolutionError, match="missing"):
        evidence_repository.resolve_batch(
            batch_id=capability.batch_id,
            batch_sha256=capability.batch_sha256,
        )


def test_exact_retry_completes_a_partial_write_without_overwriting_evidence(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    first = evidence_repository.persist_outcome(live)
    batch_path = (
        evidence_repository.root / "inference/evidence/batches" / f"{first.batch_sha256}.json"
    )
    batch_path.unlink()

    recovered = evidence_repository.persist_outcome(live)

    assert recovered.batch_id == first.batch_id
    assert evidence_repository.resolve_batch(
        batch_id=recovered.batch_id,
        batch_sha256=recovered.batch_sha256,
    ) == (live,)


def test_tampered_batch_manifest_is_not_overwritten_or_resolved(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    batch_path = (
        evidence_repository.root / "inference/evidence/batches" / f"{capability.batch_sha256}.json"
    )
    batch_path.write_bytes(b"{}")

    with pytest.raises(InferenceEvidenceResolutionError):
        evidence_repository.resolve_batch(
            batch_id=capability.batch_id,
            batch_sha256=capability.batch_sha256,
        )
    with pytest.raises(InferenceEvidenceResolutionError):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )
    with pytest.raises(InferenceEvidenceConflictError, match="batch manifest differs"):
        evidence_repository.persist_outcome(live)


def test_deleted_receipt_binding_invalidates_an_existing_batch_commit(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    next(binding_dir.iterdir()).unlink()

    with pytest.raises(InferenceEvidenceResolutionError, match="receipt-to-execution"):
        evidence_repository.resolve_batch(
            batch_id=capability.batch_id,
            batch_sha256=capability.batch_sha256,
        )


def test_tampered_outcome_manifest_fails_canonical_revalidation(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    manifest = (
        evidence_repository.root
        / "inference/evidence/outcomes"
        / f"{capability.outcome_sha256s[0]}.json"
    )
    manifest.write_bytes(b"{}")

    with pytest.raises(InferenceEvidenceResolutionError, match="manifest"):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )


def test_path_traversal_is_rejected_even_from_constructed_untrusted_model(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    payload = live.artifacts[0]
    bad_ref = payload.artifact.model_copy(update={"path": "../escaped.json"})
    bad_payload = payload.model_copy(update={"artifact": bad_ref})
    bad_outcome = live.model_copy(update={"artifacts": (bad_payload, *live.artifacts[1:])})

    with pytest.raises(InferenceEvidenceRepositoryError, match="preflight"):
        evidence_repository.persist_outcome(bad_outcome)


def test_symlink_artifact_locator_is_never_followed(
    tmp_path: Path,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    path = _artifact_path(evidence_repository, live)
    path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(live.artifacts[0].content_utf8.encode("utf-8"))
    path.symlink_to(outside)

    with pytest.raises(InferenceEvidenceRepositoryError):
        evidence_repository.persist_outcome(live)


def test_special_file_artifact_locator_is_rejected_without_blocking(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    path = _artifact_path(evidence_repository, live)
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    with pytest.raises(InferenceEvidenceRepositoryError):
        evidence_repository.persist_outcome(live)


def test_existing_artifact_locator_with_different_bytes_is_a_conflict(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    path = _artifact_path(evidence_repository, live)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"substituted")

    with pytest.raises(InferenceEvidenceConflictError, match="existing evidence differs"):
        evidence_repository.persist_outcome(live)


def test_aggregate_batch_byte_bound_fails_incrementally_before_writes(
    monkeypatch: pytest.MonkeyPatch,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    monkeypatch.setattr(
        inference_repository_module,
        "MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1",
        1,
    )

    with pytest.raises(InferenceEvidenceRepositoryError, match="aggregate artifact-byte"):
        evidence_repository.persist_batch((live,))

    assert tuple(evidence_repository.root.iterdir()) == ()


def test_committed_membership_index_bound_rejects_new_marker_before_evidence_write(
    monkeypatch: pytest.MonkeyPatch,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    monkeypatch.setattr(
        inference_repository_module,
        "MAX_COMMITTED_BATCH_MANIFESTS_V1",
        0,
    )

    with pytest.raises(InferenceEvidenceRepositoryError, match="membership index bounds"):
        evidence_repository.persist_outcome(live)

    assert not (evidence_repository.root / live.execution.receipt_artifact.path).exists()


@pytest.mark.parametrize(
    "algorithm",
    [
        b'{"source":"datasets/larkstead/golden/change_impact.yaml"}',
        b'{"expected_answer":"private-label"}',
    ],
)
def test_input_artifact_evaluator_leakage_is_rejected(
    algorithm: bytes,
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    outcome = _live_with_inputs(algorithm=algorithm)

    with pytest.raises(InferenceEvidenceRepositoryError, match="preflight"):
        evidence_repository.persist_outcome(outcome)


def test_prompt_expected_metadata_key_is_rejected(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    outcome = _live_with_inputs(prompt=b"expected_answer: private evaluator label")

    with pytest.raises(InferenceEvidenceRepositoryError, match="preflight"):
        evidence_repository.persist_outcome(outcome)


def test_input_leakage_scan_allows_ordinary_prose_and_output_labels(
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    outcome = _live_with_inputs(
        prompt=(
            b"Explain the expected_result output label and discuss golden evidence "
            b"as ordinary prose."
        ),
        schema=(b'{"type":"object","properties":{"expected_result":{"type":"string"}}}'),
    )

    capability = evidence_repository.persist_outcome(outcome)

    assert capability.outcome_count == 1


def test_duplicate_receipt_bindings_are_ambiguous_and_fail_closed(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    binding_dir = (
        evidence_repository.root
        / "inference/evidence/receipts"
        / live.execution.receipt_artifact.sha256
    )
    existing = next(binding_dir.iterdir())
    (binding_dir / f"{'f' * 64}.json").write_bytes(existing.read_bytes())

    with pytest.raises(InferenceEvidenceResolutionError, match="ambiguous"):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=live.execution.receipt_artifact
        )


def test_receipt_reference_substitution_fails_before_repository_lookup(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    substituted = live.execution.receipt_artifact.model_copy(update={"sha256": "f" * 64})

    with pytest.raises(InferenceEvidenceResolutionError, match="exact"):
        evidence_repository.resolve_replay_evidence(receipt_artifact=substituted)


def test_persisted_replay_outcome_cannot_be_used_as_a_replay_source(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    evidence_repository.persist_outcome(live)
    replay = _replay(repository=evidence_repository, live=live)
    evidence_repository.persist_outcome(replay)

    with pytest.raises(InferenceEvidenceResolutionError, match="LIVE"):
        evidence_repository.resolve_replay_evidence(
            receipt_artifact=replay.execution.receipt_artifact
        )


def test_symlink_repository_root_and_evaluator_metadata_fail_closed(
    tmp_path: Path,
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(InferenceEvidenceRepositoryError):
        FilesystemInferenceEvidenceRepository(alias)

    with pytest.raises(InferenceEvidenceRepositoryError, match="evaluator/golden"):
        evidence_repository._validate_runtime_metadata(  # noqa: SLF001
            {"nested": {"expected_answer": "private evaluator key"}}
        )


def test_unsupported_platform_fails_before_creating_repository_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "unsupported-platform"
    monkeypatch.setattr(inference_repository_module, "_fcntl", None)

    with pytest.raises(InferenceEvidenceUnsupportedPlatformError, match="require POSIX"):
        FilesystemInferenceEvidenceRepository(root)

    assert not root.exists()


def test_capability_is_process_local_and_tamper_evident(
    evidence_repository: FilesystemInferenceEvidenceRepository,
    live: RecordedInferenceOutcome,
) -> None:
    capability = evidence_repository.persist_outcome(live)
    forged = replace(capability, batch_sha256="f" * 64)

    with pytest.raises(InferenceEvidenceResolutionError, match="does not bind"):
        forged.verify(repository=evidence_repository, outcomes=(live,))
