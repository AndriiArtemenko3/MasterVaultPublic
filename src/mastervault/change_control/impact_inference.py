"""Recorded execution and durable reconstruction for actual-impact workloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control.impact_analysis import ImpactWorkload, validate_impact_workload
from mastervault.change_control.impact_results import (
    ImpactResultSet,
    validate_impact_results,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    RecordedInferenceOutcome,
    RecordedInferenceProvider,
    RecordedInferenceTask,
    run_impact_inference,
)
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ImpactReplaySourceBinding(_StrictFrozenModel):
    """One exact committed LIVE receipt selected for one impact input shard."""

    schema_version: Literal[1] = 1
    input_shard_id: str = Field(pattern=r"^impactin:[0-9a-f]{64}$")
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_artifact: ManagedArtifactRef

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if self.input_shard_id != f"impactin:{self.input_shard_sha256}":
            raise ValueError("impact replay source shard ID differs from its SHA")
        receipt = self.receipt_artifact
        if (
            receipt.kind != ManagedArtifactKind.INFERENCE_RECEIPT
            or receipt.path != f"receipts/inference/{receipt.sha256}.json"
        ):
            raise ValueError("impact replay source requires an exact receipt locator")
        return self


@dataclass(frozen=True)
class RecordedImpactInferenceRun:
    """Process result; durable authority remains the repository batch capability."""

    results: ImpactResultSet
    outcomes: tuple[RecordedInferenceOutcome, ...]
    evidence_batch: RepositoryVerifiedInferenceEvidenceBatch | None

    def __post_init__(self) -> None:
        if type(self.results) is not ImpactResultSet:
            raise TypeError("recorded impact runs require the exact impact result type")
        try:
            exact_results = ImpactResultSet.model_validate_json(
                canonical_json_bytes(self.results.model_dump(mode="json"))
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("recorded impact results are not canonical") from exc
        if exact_results != self.results:
            raise ValueError("recorded impact results differ from canonical reconstruction")

        empty = not exact_results.workload.questions
        if empty:
            if type(self.outcomes) is not tuple or self.outcomes != ():
                raise ValueError("empty impact results require exactly outcomes=()")
            if self.evidence_batch is not None:
                raise ValueError("empty impact results require evidence_batch=None")
            return

        if type(self.outcomes) is not tuple or not self.outcomes:
            raise ValueError("non-empty impact results require a non-empty exact outcome tuple")
        if any(type(item) is not RecordedInferenceOutcome for item in self.outcomes):
            raise TypeError("recorded impact run outcomes cannot be substituted")
        try:
            exact_outcomes = tuple(
                RecordedInferenceOutcome.model_validate_json(
                    canonical_json_bytes(item.model_dump(mode="json"))
                )
                for item in self.outcomes
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("recorded impact run outcomes are not canonical") from exc
        canonical_outcomes = tuple(
            sorted(exact_outcomes, key=lambda item: item.execution.execution_id)
        )
        if exact_outcomes != self.outcomes or self.outcomes != canonical_outcomes:
            raise ValueError("recorded impact run outcomes must use canonical execution-ID order")

        batch = self.evidence_batch
        if type(batch) is not RepositoryVerifiedInferenceEvidenceBatch:
            raise TypeError("non-empty impact results require an exact verified evidence batch")
        execution_ids = tuple(item.execution.execution_id for item in canonical_outcomes)
        receipt_ids = tuple(
            item.execution.receipt_artifact.artifact_id for item in canonical_outcomes
        )
        outcome_sha256s = tuple(
            hashlib.sha256(canonical_json_bytes(item.model_dump(mode="json"))).hexdigest()
            for item in canonical_outcomes
        )
        if (
            batch.outcome_count != len(canonical_outcomes)
            or batch.execution_ids != execution_ids
            or batch.receipt_artifact_ids != receipt_ids
            or batch.outcome_sha256s != outcome_sha256s
        ):
            raise ValueError("impact evidence batch does not bind the exact recorded outcomes")

        outputs = tuple(item.impact_output for item in canonical_outcomes)
        if any(
            item.execution.task != RecordedInferenceTask.IMPACT or output is None
            for item, output in zip(canonical_outcomes, outputs, strict=True)
        ):
            raise ValueError("recorded impact run contains a non-impact outcome")
        exact_outputs = tuple(output for output in outputs if output is not None)
        canonical_outputs = tuple(
            sorted(
                exact_outputs,
                key=lambda item: (item.document_version_id, item.input_shard_id),
            )
        )
        if canonical_outputs != exact_results.output_shards:
            raise ValueError("reopened impact outputs do not bind the returned results")


def _reconstruct_reopened_results(
    authority: ReviewedTemporalSnapshotAuthority,
    *,
    workload: ImpactWorkload,
    outcomes: tuple[RecordedInferenceOutcome, ...],
) -> ImpactResultSet:
    validated = tuple(
        RecordedInferenceOutcome.model_validate_json(
            canonical_json_bytes(item.model_dump(mode="json"))
        )
        for item in outcomes
    )
    if any(item.execution.task != RecordedInferenceTask.IMPACT for item in validated):
        raise ValueError("impact evidence batch contains another inference task")
    outputs = tuple(item.impact_output for item in validated)
    if any(item is None for item in outputs):
        raise ValueError("impact evidence omits a typed output shard")
    exact_outputs = tuple(item for item in outputs if item is not None)
    expected_inputs = {item.shard_id: item for item in workload.input_shards}
    by_input = {item.input_shard_id: item for item in exact_outputs}
    if len(by_input) != len(exact_outputs) or set(by_input) != set(expected_inputs):
        raise ValueError("impact evidence must cover every workload shard exactly once")
    for input_id, output in by_input.items():
        input_shard = expected_inputs[input_id]
        if (
            output.workload_id != workload.index.workload_id
            or output.workload_sha256 != workload.index.workload_sha256
            or output.input_shard_sha256 != input_shard.shard_sha256
            or output.document_version_id
            != input_shard.target_note.document.document_version_id
        ):
            raise ValueError("impact evidence substitutes its exact workload or input shard")
    reconstructed = ImpactResultSet.create(
        workload=workload,
        decisions=tuple(
            decision for output in exact_outputs for decision in output.decisions
        ),
    )
    reopened_outputs = tuple(
        sorted(
            exact_outputs,
            key=lambda item: (item.document_version_id, item.input_shard_id),
        )
    )
    if reconstructed.output_shards != reopened_outputs:
        raise ValueError("reconstructed impact results differ from reopened output shards")
    return validate_impact_results(
        authority,
        workload=workload,
        results=reconstructed,
    )


def execute_impact_workload(
    authority: ReviewedTemporalSnapshotAuthority,
    *,
    workload: ImpactWorkload,
    contract: ManagedInferenceContractBinding,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    provider: RecordedInferenceProvider | None = None,
    replay_sources: tuple[ImpactReplaySourceBinding, ...] = (),
) -> RecordedImpactInferenceRun:
    """Execute, commit, reopen, and reconstruct one exact Step 10a workload."""

    exact_workload = validate_impact_workload(authority, workload)
    if not exact_workload.questions:
        if exact_workload.input_shards or replay_sources:
            raise ValueError("zero-question impact execution cannot carry shards or replay sources")
        empty = ImpactResultSet.create(workload=exact_workload, decisions=())
        validated_empty = validate_impact_results(
            authority,
            workload=exact_workload,
            results=empty,
        )
        return RecordedImpactInferenceRun(
            results=validated_empty,
            outcomes=(),
            evidence_batch=None,
        )

    if type(evidence_repository) is not FilesystemInferenceEvidenceRepository:
        raise TypeError("impact execution requires the filesystem evidence repository")
    if contract.mode == InferenceExecutionMode.LIVE:
        if provider is None or replay_sources:
            raise ValueError("LIVE impact execution requires only one provider")
        replay_by_input: dict[str, ImpactReplaySourceBinding] = {}
    else:
        if provider is not None:
            raise ValueError("REPLAY impact execution cannot call a provider")
        source_ids = tuple(item.input_shard_id for item in replay_sources)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("impact replay sources must be unique and canonical")
        replay_by_input = {item.input_shard_id: item for item in replay_sources}
        expected_ids = {item.shard_id for item in exact_workload.input_shards}
        if set(replay_by_input) != expected_ids:
            raise ValueError("impact replay sources must exactly cover workload shards")

    transient: list[RecordedInferenceOutcome] = []
    for input_shard in exact_workload.input_shards:
        if contract.mode == InferenceExecutionMode.LIVE:
            outcome = run_impact_inference(
                contract=contract,
                workload=exact_workload,
                input_shard=input_shard,
                algorithm_manifest_bytes=algorithm_manifest_bytes,
                prompt_bytes=prompt_bytes,
                response_schema_bytes=response_schema_bytes,
                provider=provider,
            )
        else:
            source = replay_by_input[input_shard.shard_id]
            if source.input_shard_sha256 != input_shard.shard_sha256:
                raise ValueError("impact replay source binds a substituted input shard")
            outcome = run_impact_inference(
                contract=contract,
                workload=exact_workload,
                input_shard=input_shard,
                algorithm_manifest_bytes=algorithm_manifest_bytes,
                prompt_bytes=prompt_bytes,
                response_schema_bytes=response_schema_bytes,
                replay_resolver=evidence_repository,
                replay_source_receipt_artifact=source.receipt_artifact,
            )
        transient.append(outcome)

    persisted = evidence_repository.persist_batch(tuple(transient))
    reopened, capability = evidence_repository.resolve_verified_batch(
        batch_id=persisted.batch_id,
        batch_sha256=persisted.batch_sha256,
    )
    reopened = capability.verify(repository=evidence_repository, outcomes=reopened)
    results = _reconstruct_reopened_results(
        authority,
        workload=exact_workload,
        outcomes=reopened,
    )
    return RecordedImpactInferenceRun(
        results=results,
        outcomes=reopened,
        evidence_batch=capability,
    )


__all__ = [
    "ImpactReplaySourceBinding",
    "RecordedImpactInferenceRun",
    "execute_impact_workload",
]
