from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mastervault.change_control.inference_repository import InferenceEvidenceConflictError
from mastervault.change_control.managed_review import ManagedArtifactKind, ManagedArtifactRef
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository


def _artifact(*, run_id: str, name: str, content: bytes) -> ManagedArtifactRef:
    return ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        path=f"staging/managed-review/{run_id}/target/{name}.json",
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
    )


def test_stage_persists_complete_manifest_and_reopens_from_fresh_repository(
    tmp_path: Path,
) -> None:
    run_id = "stage-complete"
    content = b'{"value":"exact"}'
    artifact = _artifact(run_id=run_id, name="value", content=content)
    repository = ManagedStagingRepository(tmp_path / "stage")

    capability = repository.stage(run_id=run_id, artifacts=((artifact, content),))

    manifest_path = (
        repository.root
        / f"staging/managed-review/{run_id}/manifests/"
        / f"{capability.manifest.manifest_sha256}.json"
    )
    persisted = json.loads(manifest_path.read_bytes())
    assert persisted["manifest_id"] == capability.manifest.manifest_id
    assert persisted["manifest_sha256"] == capability.manifest.manifest_sha256
    fresh = ManagedStagingRepository(repository.root)
    assert fresh.reopen(capability.manifest) == capability.manifest
    assert fresh.resolve_completed_run(capability.completion).manifest == capability.manifest
    assert fresh.resolve_completed_run(run_id).completion == capability.completion
    assert capability.verify(fresh) == capability.manifest


def test_manifest_last_interruption_is_incomplete_then_idempotently_recoverable(
    tmp_path: Path,
) -> None:
    run_id = "stage-interrupted"
    first = b'{"ordinal":1}'
    second = b'{"ordinal":2}'
    artifacts = (
        (_artifact(run_id=run_id, name="first", content=first), first),
        (_artifact(run_id=run_id, name="second", content=second), second),
    )
    repository = ManagedStagingRepository(tmp_path / "stage")

    with pytest.raises(RuntimeError, match="injected managed staging interruption"):
        repository.stage(run_id=run_id, artifacts=artifacts, fail_after_step=1)

    run_root = repository.root / f"staging/managed-review/{run_id}"
    assert not (run_root / "COMPLETE.json").exists()
    assert not (run_root / "manifests").exists()
    with pytest.raises(InferenceEvidenceConflictError, match="incomplete"):
        repository.resolve_completed_run(run_id)

    recovered = repository.stage(run_id=run_id, artifacts=artifacts)
    assert recovered.verify(repository) == recovered.manifest
    assert (run_root / "COMPLETE.json").is_file()


def test_create_only_member_conflict_and_tampered_reopen_fail_closed(tmp_path: Path) -> None:
    run_id = "stage-conflict"
    original = b'{"value":"original"}'
    artifact = _artifact(run_id=run_id, name="value", content=original)
    repository = ManagedStagingRepository(tmp_path / "stage")
    capability = repository.stage(run_id=run_id, artifacts=((artifact, original),))

    replacement = b'{"value":"changed!"}'
    conflicting = _artifact(run_id=run_id, name="value", content=replacement)
    with pytest.raises(InferenceEvidenceConflictError, match="bytes differ"):
        repository.stage(run_id=run_id, artifacts=((conflicting, replacement),))

    member_path = repository.root / artifact.path
    member_path.write_bytes(b'{"value":"tampered"}')
    with pytest.raises(ValueError, match="absent or substituted"):
        repository.reopen(capability.manifest)


def test_stage_rejects_one_path_with_conflicting_artifact_kinds(tmp_path: Path) -> None:
    run_id = "stage-path-kind-conflict"
    content = b'{"value":"exact"}'
    artifact = _artifact(run_id=run_id, name="value", content=content)
    conflicting = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path=artifact.path,
        sha256=artifact.sha256,
        byte_count=artifact.byte_count,
    )
    repository = ManagedStagingRepository(tmp_path / "stage")

    with pytest.raises(ValueError, match="path maps to a conflicting artifact or bytes"):
        repository.stage(
            run_id=run_id,
            artifacts=((artifact, content), (conflicting, content)),
        )


def test_completion_pointer_manifest_and_repository_substitution_fail_closed(
    tmp_path: Path,
) -> None:
    run_id = "stage-substitution"
    content = b'{"value":"exact"}'
    artifact = _artifact(run_id=run_id, name="value", content=content)
    repository = ManagedStagingRepository(tmp_path / "stage")
    capability = repository.stage(run_id=run_id, artifacts=((artifact, content),))
    completion_path = repository.root / capability.completion.completion_path
    manifest_path = repository.root / capability.completion.manifest_path

    completion_bytes = completion_path.read_bytes()
    completion_path.write_bytes(b"{}")
    with pytest.raises(InferenceEvidenceConflictError, match="names another manifest"):
        repository.resolve_completed_run(capability.completion)
    completion_path.write_bytes(completion_bytes)

    manifest_bytes = manifest_path.read_bytes()
    manifest_path.write_bytes(b"{}")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        repository.resolve_completed_run(capability.completion)
    manifest_path.write_bytes(manifest_bytes)

    other = ManagedStagingRepository(tmp_path / "other")
    with pytest.raises(ValueError, match="another repository"):
        other.resolve_completed_run(capability.completion)
