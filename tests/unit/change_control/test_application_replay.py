from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mastervault.change_control.application_replay import (
    ApplicationReplayBundleRepository,
    ChangeReplayBundleError,
    ChangeReplayBundleUsageError,
    ChangeReplayBundleV1,
    ChangeReplayEvidenceIntegrityError,
    ChangeReplayStageEvidenceV1,
    ChangeReplayStageV1,
    ReplayArtifactRefV1,
    parse_change_replay_bundle_v1,
    read_change_replay_bundle_v1,
)
from mastervault.change_control.models import canonical_json_bytes

RUN_ID = f"operatorrun:{'a' * 64}"


def _artifact(stage: ChangeReplayStageV1, digest: str) -> ReplayArtifactRefV1:
    extraction = stage == ChangeReplayStageV1.EXTRACTION
    baseline = stage == ChangeReplayStageV1.BASELINE
    kind = (
        "generation-zero-baseline"
        if baseline
        else "generic-extraction"
        if extraction
        else "recorded-inference"
    )
    prefix = "regreceipt" if baseline else "generic-extraction" if extraction else "inference-exec"
    return ReplayArtifactRefV1(
        artifact_kind=kind,
        artifact_id=f"{prefix}:{digest * 64}",
        artifact_sha256=digest * 64,
        artifact_byte_count=1,
        relative_locator=f"replay/{stage.value}/{digest * 64}.json",
        request_sha256=digest * 64,
    )


def _bundle() -> ChangeReplayBundleV1:
    return ChangeReplayBundleV1.create(
        run_id=RUN_ID,
        incoming_bundle_id=f"generic-bundle-v2:{'b' * 64}",
        incoming_bundle_sha256="b" * 64,
        configuration_sha256="c" * 64,
        stages=tuple(
            ChangeReplayStageEvidenceV1(
                stage=stage,
                artifacts=(_artifact(stage, str(index + 1)),)
                if stage in {ChangeReplayStageV1.BASELINE, ChangeReplayStageV1.EXTRACTION}
                else (),
            )
            for index, stage in enumerate(ChangeReplayStageV1)
        ),
    )


def test_replay_bundle_round_trips_only_exact_canonical_json(tmp_path: Path) -> None:
    bundle = _bundle()
    payload = canonical_json_bytes(bundle.model_dump(mode="json"))
    path = tmp_path / "replay.json"
    path.write_bytes(payload)

    assert parse_change_replay_bundle_v1(payload) == bundle
    assert read_change_replay_bundle_v1(path) == bundle
    with pytest.raises(ChangeReplayBundleError, match="canonical"):
        parse_change_replay_bundle_v1(json.dumps(bundle.model_dump(mode="json"), indent=2).encode())


def test_replay_bundle_rejects_duplicate_keys_and_non_finite_numbers() -> None:
    with pytest.raises(ChangeReplayBundleError, match="duplicate"):
        parse_change_replay_bundle_v1(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(ChangeReplayBundleError, match="non-finite"):
        parse_change_replay_bundle_v1(b'{"schema_version":NaN}')


def test_replay_stage_coverage_is_exact() -> None:
    bundle = _bundle()
    extraction = next(item for item in bundle.stages if item.stage == "extraction")
    request = extraction.artifacts[0].request_sha256
    assert bundle.require_exact_stage(ChangeReplayStageV1.EXTRACTION, (request,)) == (
        extraction.artifacts
    )
    for derived in ((), ("f" * 64,), (request, request)):
        with pytest.raises(ChangeReplayBundleError, match="locally derived workload"):
            bundle.require_exact_stage(ChangeReplayStageV1.EXTRACTION, derived)


def test_replay_rejects_missing_surplus_and_wrong_stage_artifacts() -> None:
    stages = list(_bundle().stages)
    with pytest.raises(ValueError, match="at least 6|every stage"):
        ChangeReplayBundleV1.create(
            run_id=RUN_ID,
            incoming_bundle_id=f"generic-bundle-v2:{'b' * 64}",
            incoming_bundle_sha256="b" * 64,
            configuration_sha256="c" * 64,
            stages=tuple(stages[:-1]),
        )
    with pytest.raises(ValueError, match="kind differs"):
        ChangeReplayStageEvidenceV1(
            stage=ChangeReplayStageV1.IMPACT,
            artifacts=(_artifact(ChangeReplayStageV1.EXTRACTION, "d"),),
        )


def test_replay_artifact_locator_is_path_free_and_relative() -> None:
    for locator in ("/tmp/replay.json", "../replay.json", "safe/../replay.json"):
        with pytest.raises(ValueError):
            ReplayArtifactRefV1(
                artifact_kind="recorded-inference",
                artifact_id=f"inference-exec:{'e' * 64}",
                artifact_sha256="e" * 64,
                artifact_byte_count=1,
                relative_locator=locator,
                request_sha256="e" * 64,
            )


def test_replay_repository_claims_exact_bytes_and_reopens_by_run(tmp_path: Path) -> None:
    bundle = _bundle()
    payload = canonical_json_bytes(bundle.model_dump(mode="json"))
    repository = ApplicationReplayBundleRepository(tmp_path)
    assert repository.claim(
        run_id=RUN_ID,
        start_command_id=f"start-command:{'f' * 64}",
        bundle=bundle,
        canonical_bytes=payload,
    ) == bundle
    reopened = ApplicationReplayBundleRepository(
        tmp_path, create=False, read_only=True
    ).reopen_by_run(RUN_ID)
    assert reopened == bundle


def test_replay_repository_repairs_lost_start_owner_without_substitution(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    payload = canonical_json_bytes(bundle.model_dump(mode="json"))
    start_command_id = f"start-command:{'f' * 64}"
    repository = ApplicationReplayBundleRepository(tmp_path)
    repository.claim(
        run_id=RUN_ID,
        start_command_id=start_command_id,
        bundle=bundle,
        canonical_bytes=payload,
    )
    start_owner = (
        tmp_path
        / "application"
        / "replay-bundles-v1"
        / "by-start"
        / f"{hashlib.sha256(start_command_id.encode()).hexdigest()}.json"
    )
    start_owner.unlink()

    repaired = ApplicationReplayBundleRepository(tmp_path, create=False).claim(
        run_id=RUN_ID,
        start_command_id=start_command_id,
        bundle=bundle,
        canonical_bytes=payload,
    )

    assert repaired == bundle
    assert start_owner.is_file()
    assert ApplicationReplayBundleRepository(
        tmp_path, create=False, read_only=True
    ).reopen_by_run(RUN_ID) == bundle


def test_replay_repository_tamper_and_substitution_fail_closed(tmp_path: Path) -> None:
    bundle = _bundle()
    repository = ApplicationReplayBundleRepository(tmp_path)
    repository.claim(
        run_id=RUN_ID,
        start_command_id=f"start-command:{'f' * 64}",
        bundle=bundle,
        canonical_bytes=canonical_json_bytes(bundle.model_dump(mode="json")),
    )
    bundle_path = (
        tmp_path / "application" / "replay-bundles-v1" / "bundles"
        / f"{bundle.bundle_sha256}.json"
    )
    bundle_path.write_bytes(b"{}")
    with pytest.raises(ChangeReplayEvidenceIntegrityError, match="altered"):
        ApplicationReplayBundleRepository(
            tmp_path, create=False, read_only=True
        ).reopen_by_run(RUN_ID)

    other = _bundle().model_copy(update={"configuration_sha256": "d" * 64})
    with pytest.raises(ChangeReplayBundleError):
        repository.claim(
            run_id=RUN_ID,
            start_command_id=f"start-command:{'e' * 64}",
            bundle=other,
            canonical_bytes=canonical_json_bytes(other.model_dump(mode="json")),
        )


def test_replay_repository_rejects_different_current_run(tmp_path: Path) -> None:
    bundle = _bundle()
    with pytest.raises(ChangeReplayBundleError, match="run differs"):
        ApplicationReplayBundleRepository(tmp_path).claim(
            run_id=f"operatorrun:{'e' * 64}",
            start_command_id=f"start-command:{'f' * 64}",
            bundle=bundle,
            canonical_bytes=canonical_json_bytes(bundle.model_dump(mode="json")),
        )


def test_user_replay_read_error_is_usage_but_captured_absence_is_integrity(
    tmp_path: Path,
) -> None:
    with pytest.raises(ChangeReplayBundleUsageError, match="read safely"):
        read_change_replay_bundle_v1(tmp_path / "missing.json")

    root = tmp_path / "captured"
    root.mkdir(mode=0o700)
    with pytest.raises(
        ChangeReplayEvidenceIntegrityError, match="established|owner|reopened"
    ):
        ApplicationReplayBundleRepository(
            root, create=False, read_only=True
        ).reopen_by_run(RUN_ID)
