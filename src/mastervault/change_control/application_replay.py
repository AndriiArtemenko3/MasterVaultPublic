"""Private strict replay-envelope contract for synchronous change execution.

The envelope is navigation, never authority: each artifact reference must be
reopened through its owning repository and re-derived against the live source,
suite, run, configuration, and workload before it may be consumed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.managed_review import (
    ManagedArtifactKind,
    ManagedArtifactRef,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    read_regular_file,
)

MAX_CHANGE_REPLAY_BUNDLE_BYTES_V1 = 16 * 1024 * 1024
_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_REPLAY_ROOT = "application/replay-bundles-v1"


class ChangeReplayBundleError(ValueError):
    """Replay input is incomplete, non-canonical, surplus, or corrupt."""


class ChangeReplayBundleUsageError(ChangeReplayBundleError):
    """Operator-supplied replay JSON/path is malformed or unsafe."""


class ChangeReplayEvidenceIntegrityError(ChangeReplayBundleError):
    """Repository-captured replay evidence is absent, altered, or substituted."""


class ChangeReplayStageV1(StrEnum):
    BASELINE = "baseline"
    EXTRACTION = "extraction"
    CLASSIFICATION = "classification"
    DEPENDENCY = "dependency"
    IMPACT = "impact"
    PLANNING = "planning"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReplayArtifactRefV1(_StrictFrozenModel):
    """Exact reference to one repository-owned recorded result."""

    artifact_kind: Literal[
        "generation-zero-baseline", "generic-extraction", "recorded-inference"
    ]
    artifact_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]*:[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_byte_count: int = Field(ge=1, le=16 * 1024 * 1024)
    relative_locator: str
    request_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("relative_locator")
    @classmethod
    def _locator(cls, value: str) -> str:
        return canonical_repo_relative(value)

    def recorded_inference_receipt(self) -> ManagedArtifactRef:
        """Return the exact recorded-inference receipt descriptor, if applicable."""

        if self.artifact_kind != "recorded-inference":
            raise ChangeReplayEvidenceIntegrityError(
                "generic extraction reference is not a recorded inference receipt"
            )
        try:
            receipt = ManagedArtifactRef.create(
                kind=ManagedArtifactKind.INFERENCE_RECEIPT,
                path=self.relative_locator,
                sha256=self.artifact_sha256,
                byte_count=self.artifact_byte_count,
            )
        except (TypeError, ValueError) as exc:
            raise ChangeReplayEvidenceIntegrityError(
                "recorded inference reference is not an exact receipt descriptor"
            ) from exc
        if receipt.artifact_id != self.artifact_id:
            raise ChangeReplayEvidenceIntegrityError(
                "recorded inference artifact ID differs from its exact descriptor"
            )
        return receipt


class ChangeReplayStageEvidenceV1(_StrictFrozenModel):
    stage: ChangeReplayStageV1
    artifacts: tuple[ReplayArtifactRefV1, ...] = Field(max_length=4096)

    @field_validator("artifacts")
    @classmethod
    def _artifacts(
        cls, value: tuple[ReplayArtifactRefV1, ...]
    ) -> tuple[ReplayArtifactRefV1, ...]:
        keys = tuple(
            (item.request_sha256, item.artifact_id, item.artifact_sha256, item.relative_locator)
            for item in value
        )
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("replay artifacts must be unique and canonically ordered")
        return value

    @model_validator(mode="after")
    def _kind(self) -> Self:
        expected = {
            ChangeReplayStageV1.BASELINE: "generation-zero-baseline",
            ChangeReplayStageV1.EXTRACTION: "generic-extraction",
        }.get(self.stage, "recorded-inference")
        if any(item.artifact_kind != expected for item in self.artifacts):
            raise ValueError("replay artifact kind differs from its exact stage")
        if self.stage in {
            ChangeReplayStageV1.BASELINE,
            ChangeReplayStageV1.EXTRACTION,
        } and len(self.artifacts) != 1:
            raise ValueError(
                f"replay requires exactly one {self.stage.value} artifact"
            )
        return self


class ChangeReplayBundleV1(_StrictFrozenModel):
    """Canonical all-stage replay input bound to one run and runtime identity."""

    schema_version: Literal[1] = 1
    bundle_id: str = Field(pattern=r"^change-replay:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    incoming_bundle_id: str = Field(pattern=r"^generic-bundle-v2:[0-9a-f]{64}$")
    incoming_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    stages: tuple[ChangeReplayStageEvidenceV1, ...] = Field(min_length=6, max_length=6)

    @field_validator("stages")
    @classmethod
    def _stages(
        cls, value: tuple[ChangeReplayStageEvidenceV1, ...]
    ) -> tuple[ChangeReplayStageEvidenceV1, ...]:
        keys = tuple(item.stage.value for item in value)
        expected = tuple(sorted(item.value for item in ChangeReplayStageV1))
        if keys != expected:
            raise ValueError("replay bundle requires every stage exactly once in canonical order")
        return value

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.incoming_bundle_id != f"generic-bundle-v2:{self.incoming_bundle_sha256}":
            raise ValueError("replay incoming bundle identity differs from its SHA")
        payload = self.model_dump(mode="json", exclude={"bundle_id", "bundle_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.bundle_sha256 != digest or self.bundle_id != f"change-replay:{digest}":
            raise ValueError("replay bundle identity differs from its exact canonical payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        stages = tuple(sorted(values.pop("stages"), key=lambda item: item.stage.value))
        payload = {
            "schema_version": 1,
            **values,
            "stages": [item.model_dump(mode="json") for item in stages],
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "bundle_id": f"change-replay:{digest}",
                    "bundle_sha256": digest,
                    **payload,
                }
            )
        )

    def require_exact_stage(
        self,
        stage: ChangeReplayStageV1,
        request_sha256s: tuple[str, ...],
    ) -> tuple[ReplayArtifactRefV1, ...]:
        """Reject missing/surplus/reordered stage artifacts before repository use."""

        evidence = next(item for item in self.stages if item.stage == stage)
        observed = tuple(item.request_sha256 for item in evidence.artifacts)
        expected = tuple(sorted(request_sha256s))
        if observed != expected or len(set(expected)) != len(expected):
            raise ChangeReplayEvidenceIntegrityError(
                f"replay {stage.value} artifacts differ from the locally derived workload"
            )
        return evidence.artifacts


def parse_change_replay_bundle_v1(payload: bytes) -> ChangeReplayBundleV1:
    """Parse exact canonical JSON, rejecting duplicates and non-finite values."""

    if type(payload) is not bytes:
        raise TypeError("replay bundle payload must be exact bytes")
    if not payload or len(payload) > MAX_CHANGE_REPLAY_BUNDLE_BYTES_V1:
        raise ChangeReplayBundleUsageError(
            "replay bundle is empty or exceeds its fixed byte limit"
        )

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ChangeReplayBundleUsageError(f"duplicate replay JSON key {key!r}")
            result[key] = value
        return result

    def non_finite(value: str) -> Any:
        raise ChangeReplayBundleUsageError(f"non-finite replay JSON number {value!r}")

    try:
        json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=non_finite,
        )
        exact = ChangeReplayBundleV1.model_validate_json(payload, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ChangeReplayBundleUsageError):
            raise
        raise ChangeReplayBundleUsageError("replay bundle is invalid") from exc
    if canonical_json_bytes(exact.model_dump(mode="json")) != payload:
        raise ChangeReplayBundleUsageError("replay bundle is not canonical JSON")
    return exact


def read_change_replay_bundle_v1(path: Path) -> ChangeReplayBundleV1:
    """Read one stable regular replay file without following symbolic links."""

    try:
        payload = read_regular_file(
            Path(path),
            limit=MAX_CHANGE_REPLAY_BUNDLE_BYTES_V1,
            label="change replay bundle",
        )
    except (RepositoryFileBoundaryError, RepositoryFileIntegrityError, OSError) as exc:
        raise ChangeReplayBundleUsageError("replay bundle cannot be read safely") from exc
    return parse_change_replay_bundle_v1(payload)


class _ReplayBundleOwnerV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    start_command_id: str = Field(pattern=r"^start-command:[0-9a-f]{64}$")
    bundle_id: str = Field(pattern=r"^change-replay:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle_locator: str

    @field_validator("bundle_locator")
    @classmethod
    def _bundle_locator(cls, value: str) -> str:
        return canonical_repo_relative(value)

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if not (
            self.bundle_id == f"change-replay:{self.bundle_sha256}"
            and self.bundle_locator
            == f"{_REPLAY_ROOT}/bundles/{self.bundle_sha256}.json"
        ):
            raise ValueError("replay bundle owner differs from its exact bundle locator")
        return self


class ApplicationReplayBundleRepository:
    """Create-only capture of canonical replay bytes by current run/start owner."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        try:
            self._backend = FilesystemInferenceEvidenceRepository(
                Path(root), create=create, read_only=read_only
            )
        except InferenceEvidenceRepositoryError as exc:
            raise ChangeReplayEvidenceIntegrityError(
                "replay repository cannot be established"
            ) from exc

    @staticmethod
    def _name(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _bundle_path(sha256: str) -> str:
        return f"{_REPLAY_ROOT}/bundles/{sha256}.json"

    @classmethod
    def _run_path(cls, run_id: str) -> str:
        return f"{_REPLAY_ROOT}/by-run/{cls._name(run_id)}.json"

    @classmethod
    def _start_path(cls, start_command_id: str) -> str:
        return f"{_REPLAY_ROOT}/by-start/{cls._name(start_command_id)}.json"

    def _read_owner(self, path: str) -> _ReplayBundleOwnerV1 | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            path, limit=64 * 1024, label="replay bundle owner"
        )
        if payload is None:
            return None
        try:
            owner = _ReplayBundleOwnerV1.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise ChangeReplayEvidenceIntegrityError("replay bundle owner is invalid") from exc
        if canonical_json_bytes(owner.model_dump(mode="json")) != payload:
            raise ChangeReplayEvidenceIntegrityError("replay bundle owner is not canonical")
        return owner

    def _reopen_owner(self, owner: _ReplayBundleOwnerV1) -> ChangeReplayBundleV1:
        payload = self._backend._read_optional(  # noqa: SLF001
            owner.bundle_locator,
            limit=MAX_CHANGE_REPLAY_BUNDLE_BYTES_V1,
            label="captured replay bundle",
        )
        if payload is None or hashlib.sha256(payload).hexdigest() != owner.bundle_bytes_sha256:
            raise ChangeReplayEvidenceIntegrityError(
                "captured replay bundle is absent or altered"
            )
        try:
            bundle = parse_change_replay_bundle_v1(payload)
        except ChangeReplayBundleUsageError as exc:
            raise ChangeReplayEvidenceIntegrityError(
                "captured replay bundle is invalid"
            ) from exc
        if bundle.bundle_id != owner.bundle_id or bundle.bundle_sha256 != owner.bundle_sha256:
            raise ChangeReplayEvidenceIntegrityError(
                "captured replay bundle differs from its owner"
            )
        return bundle

    def claim(
        self,
        *,
        run_id: str,
        start_command_id: str,
        bundle: ChangeReplayBundleV1,
        canonical_bytes: bytes,
    ) -> ChangeReplayBundleV1:
        """Capture original canonical bytes before any replay stage consumes them."""

        if bundle.run_id != run_id:
            raise ChangeReplayEvidenceIntegrityError(
                "replay bundle run differs from its current start-command owner"
            )
        try:
            parsed = parse_change_replay_bundle_v1(canonical_bytes)
        except ChangeReplayBundleUsageError as exc:
            raise ChangeReplayEvidenceIntegrityError(
                "replay capture bytes are invalid"
            ) from exc
        if parsed != bundle:
            raise ChangeReplayEvidenceIntegrityError(
                "replay bytes differ from the parsed exact bundle"
            )
        owner = _ReplayBundleOwnerV1(
            run_id=run_id,
            start_command_id=start_command_id,
            bundle_id=bundle.bundle_id,
            bundle_sha256=bundle.bundle_sha256,
            bundle_bytes_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            bundle_locator=self._bundle_path(bundle.bundle_sha256),
        )
        owner_bytes = canonical_json_bytes(owner.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing_run = self._read_owner(self._run_path(run_id))
                existing_start = self._read_owner(self._start_path(start_command_id))
                if any(
                    existing is not None and existing != owner
                    for existing in (existing_run, existing_start)
                ):
                    raise ChangeReplayEvidenceIntegrityError(
                        "replay run/start is already bound to another exact bundle"
                    )
                self._backend._create_only(  # noqa: SLF001
                    owner.bundle_locator, canonical_bytes, label="captured replay bundle"
                )
                for path in (self._run_path(run_id), self._start_path(start_command_id)):
                    self._backend._create_only(  # noqa: SLF001
                        path, owner_bytes, label="replay bundle owner"
                    )
                reopened = self._read_owner(self._run_path(run_id))
                if reopened != owner:
                    raise ChangeReplayEvidenceIntegrityError(
                        "replay bundle owner did not reopen exactly"
                    )
                return self._reopen_owner(owner)
        except ChangeReplayBundleError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ChangeReplayEvidenceIntegrityError("replay bundle capture failed") from exc

    def reopen_by_run(self, run_id: str) -> ChangeReplayBundleV1:
        """Path-free downstream reopen of the exact current run's replay bundle."""

        try:
            with self._backend._read_lock():  # noqa: SLF001
                owner = self._read_owner(self._run_path(run_id))
                if owner is None or owner.run_id != run_id:
                    raise ChangeReplayEvidenceIntegrityError(
                        "replay run owner does not exist"
                    )
                return self._reopen_owner(owner)
        except ChangeReplayBundleError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ChangeReplayEvidenceIntegrityError(
                "replay run cannot be reopened"
            ) from exc


@dataclass(frozen=True, slots=True)
class CapturedChangeReplayBundleV1:
    """Freshly reopened six-stage replay navigation and canonical bytes."""

    bundle: ChangeReplayBundleV1
    canonical_bytes: bytes


def capture_completed_live_replay_bundle(
    *,
    evidence_root: Path,
    source_run_id: str,
    current_run_id: str,
    current_incoming_bundle_id: str,
    current_incoming_bundle_sha256: str,
    configuration_sha256: str,
) -> CapturedChangeReplayBundleV1:
    """Capture exact refs from a completed LIVE run for one current REPLAY run.

    The caller must first persist the locally grounded current REPLAY generic
    bundle.  This helper freshly reopens that bundle and every source authority;
    it performs no provider call and does not claim the returned bundle for a
    start command.
    """

    # Imports remain local because replay references are a low-level dependency
    # of extraction and baseline repositories.
    from mastervault.change_control.application_extraction_calls import (  # noqa: PLC0415
        ApplicationExtractionCallRepository,
    )
    from mastervault.change_control.application_lifecycle_evidence import (  # noqa: PLC0415
        FilesystemLifecycleEvidenceIndex,
        LifecycleEvidenceStageV1,
    )
    from mastervault.change_control.application_no_work import (  # noqa: PLC0415
        NoWorkPlanningEvidenceRepository,
    )
    from mastervault.change_control.application_stage_evidence import (  # noqa: PLC0415
        ApplicationStageEvidenceRepository,
    )
    from mastervault.change_control.application_start_command import (  # noqa: PLC0415
        ApplicationStartCommandRepository,
    )
    from mastervault.change_control.generic_incoming import (  # noqa: PLC0415
        GenericExtractionModeV2,
    )
    from mastervault.change_control.generic_incoming_repository import (  # noqa: PLC0415
        FilesystemGenericIncomingRepositoryV2,
    )
    from mastervault.change_control.recorded_inference import (  # noqa: PLC0415
        RecordedInferenceTask,
    )
    from mastervault.change_control.regression_baseline import (  # noqa: PLC0415
        GenerationZeroBaselineRepository,
    )

    root = Path(evidence_root)
    try:
        command = ApplicationStartCommandRepository(
            root, create=False, read_only=True
        ).reopen_run(source_run_id)
        if not (
            command.mode.value == "live"
            and command.run_id == source_run_id
            and command.configuration_sha256 == configuration_sha256
        ):
            raise ChangeReplayEvidenceIntegrityError(
                "replay source run is not an exact configuration-bound LIVE start"
            )
        lifecycle = FilesystemLifecycleEvidenceIndex(
            root, create=False, read_only=True
        )
        incoming_index = lifecycle.reopen(
            source_run_id, LifecycleEvidenceStageV1.INCOMING
        )
        if len(incoming_index.owners) != 1:
            raise ChangeReplayEvidenceIntegrityError(
                "replay source incoming stage has ambiguous ownership"
            )
        incoming_owner = incoming_index.owners[0]
        if not (
            incoming_owner.owner_kind == "generic-bundle"
            and incoming_owner.owner_id == f"generic-bundle-v2:{incoming_owner.owner_sha256}"
            and incoming_owner.relative_locator
            == f"generic-incoming/v2/bundles/{incoming_owner.owner_sha256}.json"
        ):
            raise ChangeReplayEvidenceIntegrityError(
                "replay source incoming stage has a non-canonical owner"
            )
        generic = FilesystemGenericIncomingRepositoryV2(
            root, create=False, read_only=True
        )
        source_incoming = generic.resolve_verified_evidence(
            generic.reopen(incoming_owner.owner_id)
        )
        current_incoming = generic.resolve_verified_evidence(
            generic.reopen(current_incoming_bundle_id)
        )
        if not (
            source_incoming.bundle.bundle_sha256 == incoming_owner.owner_sha256
            and source_incoming.inference.mode is GenericExtractionModeV2.LIVE
            and source_incoming.admission.source_sha256 == command.source_sha256
            and source_incoming.admission.source_byte_count == command.source_byte_count
            and hashlib.sha256(
                canonical_json_bytes(
                    source_incoming.admission.metadata.model_dump(mode="json")
                )
            ).hexdigest()
            == command.source_metadata_sha256
            and current_incoming.bundle.bundle_sha256 == current_incoming_bundle_sha256
            and current_incoming.inference.mode is GenericExtractionModeV2.REPLAY
            and current_incoming.admission == source_incoming.admission
            and current_incoming.source == source_incoming.source
            and current_incoming.projection == source_incoming.projection
            and current_incoming.raw_source == source_incoming.raw_source
            and current_incoming.source_note == source_incoming.source_note
            and current_incoming.inference.request_sha256
            == source_incoming.inference.request_sha256
            and current_incoming.inference.provider_result_sha256
            == source_incoming.inference.provider_result_sha256
            and current_incoming.inference.provider_contract
            == source_incoming.inference.provider_contract
            and current_incoming.inference.claims == source_incoming.inference.claims
        ):
            raise ChangeReplayEvidenceIntegrityError(
                "current REPLAY generic bundle differs from source LIVE authority"
            )
        extraction = ApplicationExtractionCallRepository(
            root, create=False, read_only=True
        ).reopen_completed(
            start_command_id=command.command_id,
            extraction_request_sha256=source_incoming.inference.request_sha256,
        )
        if not (
            extraction.source_sha256 == command.source_sha256
            and extraction.extraction_request_sha256
            == source_incoming.inference.request_sha256
            and extraction.provider_result_sha256
            == source_incoming.inference.provider_result_sha256
            and extraction.provider_contract == source_incoming.inference.provider_contract
            and extraction.grounded_extraction.claims == source_incoming.inference.claims
        ):
            raise ChangeReplayEvidenceIntegrityError(
                "source extraction differs from its exact LIVE generic bundle"
            )
        baseline = GenerationZeroBaselineRepository(
            root, create=False, read_only=True
        ).open(source_run_id)
        baseline_reference = baseline.replay_ref
        baseline_index = lifecycle.reopen(
            source_run_id, LifecycleEvidenceStageV1.BASELINE
        )
        if len(baseline_index.owners) != 1:
            raise ChangeReplayEvidenceIntegrityError(
                "replay source baseline stage has ambiguous ownership"
            )
        baseline_owner = baseline_index.owners[0]
        if not (
            baseline.replay_source is None
            and baseline_owner.owner_kind == "generation-zero-baseline"
            and baseline_owner.owner_id == baseline_reference.artifact_id
            and baseline_owner.owner_sha256 == baseline_reference.artifact_sha256
            and baseline_owner.relative_locator == baseline_reference.relative_locator
            and baseline.suite_id == command.suite_id
            and baseline.suite_version == command.suite_version
            and baseline.suite_original_sha256 == command.suite_original_sha256
            and baseline.suite_original_byte_count
            == command.suite_original_byte_count
            and baseline.suite_canonical_sha256 == command.suite_canonical_sha256
        ):
            raise ChangeReplayEvidenceIntegrityError(
                "replay source baseline differs from its exact LIVE run owner"
            )
        inference = FilesystemInferenceEvidenceRepository(
            root, create=False, read_only=True
        )

        def batch_refs(
            *,
            batch_id: str,
            batch_sha256: str,
            task: RecordedInferenceTask,
        ) -> tuple[ReplayArtifactRefV1, ...]:
            outcomes = inference.resolve_batch(
                batch_id=batch_id, batch_sha256=batch_sha256
            )
            refs: list[ReplayArtifactRefV1] = []
            for outcome in outcomes:
                execution = outcome.execution
                artifact = execution.receipt_artifact
                if execution.task is not task or execution.contract.mode.value != "live":
                    raise ChangeReplayEvidenceIntegrityError(
                        f"source {task.value} batch is not exact LIVE evidence"
                    )
                refs.append(
                    ReplayArtifactRefV1(
                        artifact_kind="recorded-inference",
                        artifact_id=artifact.artifact_id,
                        artifact_sha256=artifact.sha256,
                        artifact_byte_count=artifact.byte_count,
                        relative_locator=artifact.path,
                        request_sha256=(
                            execution.input_envelope.input_shard_sha256
                        ),
                    )
                )
            return tuple(
                sorted(
                    refs,
                    key=lambda item: (
                        item.request_sha256,
                        item.artifact_id,
                        item.artifact_sha256,
                        item.relative_locator,
                    ),
                )
            )

        def indexed_batch(
            *,
            stage: LifecycleEvidenceStageV1,
            owner_kind: str,
            task: RecordedInferenceTask,
        ) -> tuple[ReplayArtifactRefV1, ...]:
            index = lifecycle.reopen(source_run_id, stage)
            if len(index.owners) != 1:
                raise ChangeReplayEvidenceIntegrityError(
                    f"source {stage.value} stage has ambiguous ownership"
                )
            owner = index.owners[0]
            if not (
                owner.owner_kind == owner_kind
                and owner.owner_id == f"inference-batch:{owner.owner_sha256}"
                and owner.relative_locator
                == f"inference/evidence/batches/{owner.owner_sha256}.json"
            ):
                raise ChangeReplayEvidenceIntegrityError(
                    f"source {stage.value} batch owner is not canonical"
                )
            return batch_refs(
                batch_id=owner.owner_id,
                batch_sha256=owner.owner_sha256,
                task=task,
            )

        classification = indexed_batch(
            stage=LifecycleEvidenceStageV1.CLASSIFICATION,
            owner_kind="classification-batch",
            task=RecordedInferenceTask.CLASSIFICATION,
        )
        dependency = indexed_batch(
            stage=LifecycleEvidenceStageV1.DEPENDENCY,
            owner_kind="dependency-batch",
            task=RecordedInferenceTask.DEPENDENCY,
        )
        stages = ApplicationStageEvidenceRepository(
            root, create=False, read_only=True
        )
        impact_index = lifecycle.reopen_optional(
            source_run_id, LifecycleEvidenceStageV1.IMPACT
        )
        impact: tuple[ReplayArtifactRefV1, ...] = ()
        if impact_index is not None:
            if len(impact_index.owners) != 1:
                raise ChangeReplayEvidenceIntegrityError(
                    "source impact stage has ambiguous ownership"
                )
            owner = impact_index.owners[0]
            impact_evidence = stages.reopen_impact(source_run_id)
            if not (
                owner.owner_kind == "impact-stage-evidence"
                and owner.owner_id == impact_evidence.evidence_id
                and owner.owner_sha256 == impact_evidence.evidence_sha256
                and owner.relative_locator
                == stages.relative_locator(source_run_id, "impact")
                and impact_evidence.configuration_sha256 == configuration_sha256
            ):
                raise ChangeReplayEvidenceIntegrityError(
                    "source impact stage differs from its exact owner"
                )
            impact = batch_refs(
                batch_id=impact_evidence.binding.batch_id,
                batch_sha256=impact_evidence.binding.batch_sha256,
                task=RecordedInferenceTask.IMPACT,
            )
        planning_index = lifecycle.reopen(
            source_run_id, LifecycleEvidenceStageV1.PLANNING
        )
        if len(planning_index.owners) != 1:
            raise ChangeReplayEvidenceIntegrityError(
                "source planning stage has ambiguous ownership"
            )
        planning_owner = planning_index.owners[0]
        planning: tuple[ReplayArtifactRefV1, ...]
        if planning_owner.owner_kind == "no-work-planning":
            planning_locator = planning_owner.relative_locator
            if planning_locator is None:
                raise ChangeReplayEvidenceIntegrityError(
                    "source no-work planning has no exact locator"
                )
            workload_sha256 = PurePosixPath(planning_locator).stem
            no_work = NoWorkPlanningEvidenceRepository(
                root, create=False, read_only=True
            ).reopen(
                source_run_id,
                f"revisionwork:{workload_sha256}",
                workload_sha256,
            )
            if not (
                planning_owner.owner_id == no_work.evidence_id
                and planning_owner.owner_sha256 == no_work.evidence_sha256
                and planning_owner.relative_locator
                == NoWorkPlanningEvidenceRepository.relative_locator(
                    source_run_id,
                    no_work.workload.workload_id,
                    no_work.workload.workload_sha256,
                )
                and no_work.configuration_sha256 == configuration_sha256
            ):
                raise ChangeReplayEvidenceIntegrityError(
                    "source no-work planning differs from its exact owner"
                )
            planning = ()
        elif planning_owner.owner_kind == "planning-stage-evidence":
            planning_evidence = stages.reopen_planning(source_run_id)
            if not (
                planning_owner.owner_id == planning_evidence.evidence_id
                and planning_owner.owner_sha256 == planning_evidence.evidence_sha256
                and planning_owner.relative_locator
                == stages.relative_locator(source_run_id, "planning")
            ):
                raise ChangeReplayEvidenceIntegrityError(
                    "source planning stage differs from its exact owner"
                )
            planning = batch_refs(
                batch_id=planning_evidence.binding.batch_id,
                batch_sha256=planning_evidence.binding.batch_sha256,
                task=RecordedInferenceTask.REVISION_PLANNING,
            )
        else:
            raise ChangeReplayEvidenceIntegrityError(
                "source planning stage has an unsupported exact owner"
            )
        bundle = ChangeReplayBundleV1.create(
            run_id=current_run_id,
            incoming_bundle_id=current_incoming.bundle.bundle_id,
            incoming_bundle_sha256=current_incoming.bundle.bundle_sha256,
            configuration_sha256=configuration_sha256,
            stages=(
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.BASELINE,
                    artifacts=(baseline_reference,),
                ),
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.EXTRACTION,
                    artifacts=(extraction.replay_ref,),
                ),
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.CLASSIFICATION,
                    artifacts=classification,
                ),
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.DEPENDENCY,
                    artifacts=dependency,
                ),
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.IMPACT,
                    artifacts=impact,
                ),
                ChangeReplayStageEvidenceV1(
                    stage=ChangeReplayStageV1.PLANNING,
                    artifacts=planning,
                ),
            ),
        )
        payload = canonical_json_bytes(bundle.model_dump(mode="json"))
        return CapturedChangeReplayBundleV1(bundle=bundle, canonical_bytes=payload)
    except ChangeReplayBundleError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ChangeReplayEvidenceIntegrityError(
            "completed LIVE replay capture failed closed"
        ) from exc


__all__ = [
    "ChangeReplayBundleError",
    "ChangeReplayBundleUsageError",
    "ChangeReplayEvidenceIntegrityError",
    "ChangeReplayBundleV1",
    "ChangeReplayStageEvidenceV1",
    "ChangeReplayStageV1",
    "MAX_CHANGE_REPLAY_BUNDLE_BYTES_V1",
    "ReplayArtifactRefV1",
    "ApplicationReplayBundleRepository",
    "CapturedChangeReplayBundleV1",
    "parse_change_replay_bundle_v1",
    "capture_completed_live_replay_bundle",
    "read_change_replay_bundle_v1",
]
