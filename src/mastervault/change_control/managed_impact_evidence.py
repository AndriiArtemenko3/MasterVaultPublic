"""Adapter from a verified non-empty Stage B run to managed-review evidence."""

from __future__ import annotations

from mastervault.change_control.impact_inference import RecordedImpactInferenceRun
from mastervault.change_control.inference_repository import (
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    ManagedImpactAnalysisEvidenceBinding,
    ManagedImpactBatchMemberBinding,
    ManagedImpactOutputRefBinding,
)


def bind_recorded_impact_inference_run(
    run: RecordedImpactInferenceRun,
) -> ManagedImpactAnalysisEvidenceBinding:
    """Bind exact durable identities without serializing process-local capability state."""

    if type(run) is not RecordedImpactInferenceRun:
        raise TypeError("managed impact evidence requires the exact recorded impact run")
    if not run.results.workload.questions:
        raise ValueError("empty impact inference has no durable batch to bind")
    batch = run.evidence_batch
    if type(batch) is not RepositoryVerifiedInferenceEvidenceBatch:
        raise TypeError("non-empty managed impact evidence requires a verified batch")
    members = tuple(
        ManagedImpactBatchMemberBinding(
            execution_id=execution_id,
            receipt_artifact_id=receipt_artifact_id,
            outcome_sha256=outcome_sha256,
        )
        for execution_id, receipt_artifact_id, outcome_sha256 in zip(
            batch.execution_ids,
            batch.receipt_artifact_ids,
            batch.outcome_sha256s,
            strict=True,
        )
    )
    outputs = tuple(
        ManagedImpactOutputRefBinding(
            document_version_id=item.document_version_id,
            input_shard_id=item.input_shard_id,
            input_shard_sha256=item.input_shard_sha256,
            output_shard_id=item.output_shard_id,
            output_shard_sha256=item.output_shard_sha256,
            decision_count=item.decision_count,
            document_disposition=item.document_disposition.value,
        )
        for item in run.results.result_index.output_shards
    )
    return ManagedImpactAnalysisEvidenceBinding.create(
        repository_id=batch.repository_id,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        batch_members=members,
        workload_id=run.results.workload.index.workload_id,
        workload_sha256=run.results.workload.index.workload_sha256,
        result_id=run.results.result_id,
        result_sha256=run.results.result_sha256,
        output_shards=outputs,
    )


__all__ = ["bind_recorded_impact_inference_run"]
