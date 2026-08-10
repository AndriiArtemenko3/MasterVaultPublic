"""Durable admission of one complete recorded revision-planning run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from mastervault.change_control.impact_analysis import (
    ImpactInferenceShard,
    build_impact_workload,
)
from mastervault.change_control.impact_results import ImpactOutputShard
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedImpactOutputRefBinding,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    ManagedRevisionPlanningBatchMemberBinding,
    ManagedRevisionPlanningTargetBinding,
    NoChangeImpactCard,
)
from mastervault.change_control.managed_revision_materialization import (
    materialize_revision_planning_response,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningEligibility,
    RevisionPlanningInferenceShard,
    RevisionPlanningWorkload,
    parse_revision_planning_wire_response,
    validate_revision_planning_wire_response,
)
from mastervault.change_control.managed_staging_repository import (
    ManagedStagingCompletionBinding,
    ManagedStagingRepository,
)
from mastervault.change_control.models import VersionedClaimRevision, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    RecordedInferenceOutcome,
    RecordedInferenceTask,
)
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.revision_planning_inference import (
    RecordedRevisionPlanningInferenceRun,
    RevisionPlanningPredecessorSnapshot,
    build_revision_planning_workload_from_impact_evidence,
    derive_revision_planning_eligibility_from_impact_evidence,
)


@dataclass(frozen=True)
class _ReopenedImpactEvidence:
    input_shards: tuple[ImpactInferenceShard, ...]
    output_shards: tuple[ImpactOutputShard, ...]
    eligibility: RevisionPlanningEligibility
    predecessor_claims: dict[str, tuple[VersionedClaimRevision, ...]]


def _subject_identity(
    subject: ManagedRevisionPlan | NoChangeImpactCard,
) -> tuple[Literal["managed-revision-plan", "no-change-impact-card"], str, str]:
    if isinstance(subject, ManagedRevisionPlan):
        return "managed-revision-plan", subject.plan_id, subject.plan_sha256
    return "no-change-impact-card", subject.card_id, subject.card_sha256


def revision_planning_staging_completion(
    binding: ManagedRevisionPlanningAdmissionBinding,
) -> ManagedStagingCompletionBinding:
    return ManagedStagingCompletionBinding.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "run_id": binding.run_id,
                "repository_id": binding.repository_id,
                "manifest_id": binding.staging_manifest_id,
                "manifest_sha256": binding.staging_manifest_sha256,
                "manifest_path": binding.staging_manifest_path,
                "completion_path": binding.staging_completion_path,
                "completion_id": binding.staging_completion_id,
                "completion_sha256": binding.staging_completion_sha256,
            }
        )
    )


def _batch_members(
    batch: RepositoryVerifiedInferenceEvidenceBatch,
) -> tuple[ManagedRevisionPlanningBatchMemberBinding, ...]:
    return tuple(
        ManagedRevisionPlanningBatchMemberBinding(
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


def _require_repositories(
    *,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    staging_repository: ManagedStagingRepository,
) -> None:
    if evidence_repository.root != staging_repository.root or (
        evidence_repository.repository_id != staging_repository.repository_id
    ):
        raise ValueError("revision admission repositories must share one exact authority root")


def _parse_planning_input(content: bytes) -> RevisionPlanningInferenceShard:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("revision admission staged input is not exact canonical JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.pop("namespace", None) != "mastervault.recorded-revision-planning-input.v1"
    ):
        raise ValueError("revision admission staged input has the wrong namespace")
    digest = hashlib.sha256(content).hexdigest()
    shard = RevisionPlanningInferenceShard.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "shard_id": f"revisionin:{digest}",
                "shard_sha256": digest,
            }
        )
    )
    if shard.canonical_bytes() != content:
        raise ValueError("revision admission staged input is not canonical")
    return shard


def _parse_impact_input(content: bytes) -> ImpactInferenceShard:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("revision admission impact input is not exact canonical JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.pop("namespace", None) != "mastervault.impact-input-shard.v1"
    ):
        raise ValueError("revision admission impact input has the wrong namespace")
    digest = hashlib.sha256(content).hexdigest()
    shard = ImpactInferenceShard.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "shard_id": f"impactin:{digest}",
                "shard_sha256": digest,
            }
        )
    )
    if shard.canonical_bytes() != content:
        raise ValueError("revision admission impact input is not canonical")
    return shard


def _planning_input_artifact(
    target: ManagedRevisionPlanningTargetBinding, *, run_id: str
) -> ManagedArtifactRef:
    expected_path = (
        f"staging/managed-review/{run_id}/{target.target_key}/"
        f"analysis-input-{target.input_shard_sha256}.json"
    )
    matches = tuple(
        item
        for item in target.staged_artifacts
        if item.kind == ManagedArtifactKind.INFERENCE_INPUT
        and item.path == expected_path
        and item.sha256 == target.input_shard_sha256
    )
    if len(matches) != 1:
        raise ValueError("revision admission omits its exact staged planning input")
    return matches[0]


def _reopen_impact_evidence(
    *,
    analysis_set: ManagedAnalysisSetBinding,
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> _ReopenedImpactEvidence:
    binding = analysis_set.impact_evidence
    if binding is None or analysis_set.schema_version != 2:
        raise ValueError("revision admission requires exact durable impact evidence")
    outcomes, batch = evidence_repository.resolve_verified_batch(
        batch_id=binding.batch_id,
        batch_sha256=binding.batch_sha256,
    )
    observed_members = tuple(
        sorted(
            (
                item.execution.execution_id,
                item.execution.receipt_artifact.artifact_id,
                hashlib.sha256(canonical_json_bytes(item.model_dump(mode="json"))).hexdigest(),
            )
            for item in outcomes
        )
    )
    expected_members = tuple(
        (item.execution_id, item.receipt_artifact_id, item.outcome_sha256)
        for item in binding.batch_members
    )
    if batch.repository_id != binding.repository_id or observed_members != expected_members:
        raise ValueError("revision admission impact evidence batch changed on reopen")
    claims: dict[str, tuple[VersionedClaimRevision, ...]] = {}
    input_shards: list[ImpactInferenceShard] = []
    output_shards: list[ImpactOutputShard] = []
    output_bindings: list[ManagedImpactOutputRefBinding] = []
    for outcome in outcomes:
        output = outcome.impact_output
        envelope = outcome.execution.input_envelope
        if outcome.execution.task != RecordedInferenceTask.IMPACT or output is None:
            raise ValueError("revision admission impact evidence contains another task")
        path = f"inference/inputs/{envelope.input_shard_sha256}.json"
        refs = tuple(
            item
            for item in envelope.input_artifacts
            if item.kind == ManagedArtifactKind.INFERENCE_INPUT
            and item.path == path
            and item.sha256 == envelope.input_shard_sha256
        )
        if len(refs) != 1:
            raise ValueError("revision admission impact evidence omits exact input")
        shard = _parse_impact_input(evidence_repository.open_artifact(refs[0]))
        document_version_id = shard.target_note.document.document_version_id
        if (
            envelope.workload_id != binding.workload_id
            or envelope.workload_sha256 != binding.workload_sha256
            or output.workload_id != binding.workload_id
            or output.workload_sha256 != binding.workload_sha256
            or output.input_shard_id != shard.shard_id
            or output.input_shard_sha256 != shard.shard_sha256
            or output.document_version_id != document_version_id
            or document_version_id in claims
        ):
            raise ValueError("revision admission impact input/output binding changed")
        claims[document_version_id] = shard.target_claim_revisions
        input_shards.append(shard)
        output_shards.append(output)
        output_bindings.append(
            ManagedImpactOutputRefBinding(
                document_version_id=output.document_version_id,
                input_shard_id=output.input_shard_id,
                input_shard_sha256=output.input_shard_sha256,
                output_shard_id=output.output_shard_id,
                output_shard_sha256=output.output_shard_sha256,
                decision_count=len(output.decisions),
                document_disposition=output.document_disposition.value,
            )
        )
    ordered_bindings = tuple(
        sorted(output_bindings, key=lambda item: (item.document_version_id, item.input_shard_id))
    )
    if ordered_bindings != binding.output_shards:
        raise ValueError("revision admission impact output references changed on reopen")
    exact_inputs = tuple(
        sorted(
            input_shards,
            key=lambda item: (
                item.target_note.document.document_version_id,
                item.shard_id,
            ),
        )
    )
    exact_outputs = tuple(
        sorted(
            output_shards,
            key=lambda item: (item.document_version_id, item.input_shard_id),
        )
    )
    eligibility = derive_revision_planning_eligibility_from_impact_evidence(
        workload_id=binding.workload_id,
        workload_sha256=binding.workload_sha256,
        result_id=binding.result_id,
        result_sha256=binding.result_sha256,
        input_shards=exact_inputs,
        output_shards=exact_outputs,
    )
    return _ReopenedImpactEvidence(
        input_shards=exact_inputs,
        output_shards=exact_outputs,
        eligibility=eligibility,
        predecessor_claims=claims,
    )


def _require_exact_reviewed_impact_lineage(
    *,
    binding: ManagedRevisionPlanningAdmissionBinding | None,
    analysis_set: ManagedAnalysisSetBinding,
    repository_id: str,
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    impact: _ReopenedImpactEvidence,
) -> ReviewedTemporalSnapshotAuthority:
    """Prove Step 10 and admission lineage against one exact reviewed authority."""

    if type(reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
        raise TypeError("revision admission requires exact reviewed temporal authority")
    reviewed = reviewed_snapshot.verify()
    reviewed_binding = reviewed.binding
    bootstrap = analysis_set.analysis_bootstrap
    evidence = analysis_set.impact_evidence
    if evidence is None or analysis_set.schema_version != 2:
        raise ValueError("revision admission requires durable Step-10 evidence")
    expected = build_impact_workload(reviewed)
    checks = {
        "reviewed repository": reviewed_binding.evidence_repository_id == repository_id,
        "impact repository": evidence.repository_id == repository_id,
        "analysis bootstrap": (
            bootstrap
            == reviewed.temporal_analysis.proposal.binding.analysis_bootstrap
        ),
        "analysis aggregate": (
            reviewed_binding.analysis_head.aggregate_id == bootstrap.aggregate_id
        ),
        "analysis revision": reviewed_binding.analysis_head.revision == bootstrap.analysis_revision,
        "analysis SHA": (
            reviewed_binding.analysis_head.aggregate_sha256
            == bootstrap.analysis_aggregate_sha256
        ),
        "impact workload ID": expected.index.workload_id == evidence.workload_id,
        "impact workload SHA": expected.index.workload_sha256 == evidence.workload_sha256,
        "candidate result": (
            expected.index.binding.relationship_candidate_result_sha256
            == analysis_set.candidate_result_sha256
        ),
        "classification result": (
            reviewed.temporal_analysis.classification_result_index.result_sha256
            == analysis_set.classification_result_sha256
        ),
        "attention result": (
            expected.index.binding.attention_result_sha256
            == analysis_set.attention_result_sha256
        ),
        "mechanically relevant claims": (
            expected.index.binding.mechanically_relevant_claim_revision_ids
            == analysis_set.global_relevant_claim_revision_ids
        ),
        "impact input shards": expected.input_shards == impact.input_shards,
    }
    failures = tuple(label for label, passed in checks.items() if not passed)
    if failures:
        raise ValueError(
            "revision admission Step-10 evidence differs from reviewed authority: "
            + ", ".join(failures)
        )
    if binding is not None and (
        binding.reviewed_snapshot_binding_id != reviewed_binding.binding_id
        or binding.reviewed_snapshot_binding_sha256 != reviewed_binding.binding_sha256
        or binding.temporal_decision_record_sha256
        != reviewed_binding.temporal_decision_record_sha256
    ):
        raise ValueError("revision admission names another reviewed authority")
    return reviewed


def _reconstruct_exact_planning_workload(
    *,
    run_id: str,
    planning_inputs: tuple[RevisionPlanningInferenceShard, ...],
    analysis_set: ManagedAnalysisSetBinding,
    impact: _ReopenedImpactEvidence,
    workload_id: str,
    workload_sha256: str,
) -> RevisionPlanningWorkload:
    """Rebuild ADR 0013 inputs without trusting their staged projection fields."""

    by_document = {item.predecessor.document_version_id: item for item in planning_inputs}
    expected_documents = {item.document_version_id for item in impact.eligibility.targets}
    if len(by_document) != len(planning_inputs) or set(by_document) != expected_documents:
        raise ValueError("revision planning inputs do not cover exact eligible impact targets")
    impact_inputs = {item.shard_id: item for item in impact.input_shards}
    snapshots: list[RevisionPlanningPredecessorSnapshot] = []
    for target in impact.eligibility.targets:
        source = impact_inputs[target.input_shard_id]
        planned = by_document[target.document_version_id]
        snapshots.append(
            RevisionPlanningPredecessorSnapshot(
                target_key=source.target_note.document.document_id,
                raw_path=planned.predecessor.source_path,
                raw_bytes=planned.predecessor_raw_utf8.encode("utf-8"),
                source_note_path=source.target_note.source_note_path,
                source_note_bytes=source.target_note.source_note_utf8.encode("utf-8"),
            )
        )
    expected, _snapshots, _sources = build_revision_planning_workload_from_impact_evidence(
        run_id=run_id,
        impact_workload_id=impact.eligibility.workload_id,
        impact_workload_sha256=impact.eligibility.workload_sha256,
        impact_result_id=impact.eligibility.result_id,
        impact_result_sha256=impact.eligibility.result_sha256,
        impact_input_shards=impact.input_shards,
        impact_output_shards=impact.output_shards,
        snapshots=tuple(snapshots),
        analysis_set=analysis_set,
    )
    try:
        observed = RevisionPlanningWorkload.create(
            eligibility=impact.eligibility,
            input_shards=planning_inputs,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "revision planning workload differs from exact Step-10 batch derivation"
        ) from exc
    if (
        observed.workload_id != workload_id
        or observed.workload_sha256 != workload_sha256
        or observed != expected
        or tuple(item.canonical_bytes() for item in observed.input_shards)
        != tuple(item.canonical_bytes() for item in expected.input_shards)
    ):
        raise ValueError("revision planning workload differs from exact Step-10 batch derivation")
    return expected


def _require_target_output(
    target: ManagedRevisionPlanningTargetBinding,
    *,
    outcome: RecordedInferenceOutcome,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    staging_repository: ManagedStagingRepository,
    completion: ManagedStagingCompletionBinding,
    run_id: str,
    workload_id: str,
    workload_sha256: str,
    contract_binding_id: str,
    analysis_set: ManagedAnalysisSetBinding,
    planning_workload: RevisionPlanningWorkload,
    target_input: RevisionPlanningInferenceShard,
    predecessor_claims: tuple[VersionedClaimRevision, ...],
) -> None:
    execution = outcome.execution
    output = outcome.revision_planning_output
    if execution.task != RecordedInferenceTask.REVISION_PLANNING or output is None:
        raise ValueError("revision admission batch contains another inference task")
    envelope = execution.input_envelope
    if (
        execution.contract.contract_binding_id != contract_binding_id
        or envelope.workload_id != workload_id
        or envelope.workload_sha256 != workload_sha256
        or envelope.input_shard_id != target.input_shard_id
        or envelope.input_shard_sha256 != target.input_shard_sha256
        or output.workload_id != workload_id
        or output.workload_sha256 != workload_sha256
        or output.input_shard_id != target.input_shard_id
        or output.input_shard_sha256 != target.input_shard_sha256
        or output.target_key != target.target_key
        or output.document_version_id != target.document_version_id
        or output.impact_output_shard_id != target_input.target.output_shard_id
        or output.impact_output_shard_sha256 != target_input.target.output_shard_sha256
        or output.output_shard_id != target.output_shard_id
        or output.output_shard_sha256 != target.output_shard_sha256
        or execution.execution_id != target.execution_id
        or execution.receipt.receipt_id != target.receipt_id
        or execution.receipt_artifact.artifact_id != target.receipt_artifact_id
    ):
        raise ValueError("revision admission target differs from its reopened exact outcome")
    outcome_sha256 = hashlib.sha256(
        canonical_json_bytes(outcome.model_dump(mode="json"))
    ).hexdigest()
    if outcome_sha256 != target.outcome_sha256:
        raise ValueError("revision admission target differs from reopened outcome bytes")
    expected_output_path = (
        f"staging/managed-review/{run_id}/{target.target_key}/"
        f"validated-output-{target.output_shard_sha256}.json"
    )
    input_matches = (_planning_input_artifact(target, run_id=run_id),)
    output_matches = tuple(
        item
        for item in target.staged_artifacts
        if item.kind == ManagedArtifactKind.INFERENCE_OUTPUT
        and item.path == expected_output_path
        and item.sha256 == target.output_shard_sha256
    )
    if len(input_matches) != 1 or len(output_matches) != 1:
        raise ValueError("revision admission omits exact staged input/output artifacts")
    input_artifact = input_matches[0]
    output_artifact = output_matches[0]
    input_bytes = staging_repository.open_member(
        completion=completion,
        artifact=input_artifact,
    )
    output_bytes = staging_repository.open_member(
        completion=completion,
        artifact=output_artifact,
    )
    staged_payload = next(
        (item for item in outcome.artifacts if item.artifact == input_artifact),
        None,
    )
    if staged_payload is None or input_bytes != staged_payload.content_utf8.encode("utf-8"):
        raise ValueError("revision admission staged input differs from recorded evidence")
    if _parse_planning_input(input_bytes) != target_input:
        raise ValueError("revision admission staged input changed during exact reopen")
    if (
        target_input.run_id != run_id
        or target_input.shard_id != target.input_shard_id
        or target_input.shard_sha256 != target.input_shard_sha256
        or target_input.target.target_key != target.target_key
        or target_input.target.document_version_id != target.document_version_id
        or target_input.analysis_set_id != analysis_set.analysis_set_id
        or target_input.analysis_set_sha256 != analysis_set.analysis_set_sha256
        or output.impact_output_shard_id != target_input.target.output_shard_id
        or output.impact_output_shard_sha256 != target_input.target.output_shard_sha256
    ):
        raise ValueError("revision admission staged input differs from admitted analysis/target")
    if output_bytes != output.canonical_bytes():
        raise ValueError("revision admission staged output differs from recorded proposal bytes")
    if evidence_repository.open_artifact(execution.receipt_artifact) != canonical_json_bytes(
        execution.receipt.model_dump(mode="json")
    ):
        raise ValueError("revision admission receipt artifact differs from reopened receipt")
    raw_output = evidence_repository.open_artifact(execution.raw_output_artifact)
    try:
        parsed_response = parse_revision_planning_wire_response(raw_output)
    except (TypeError, ValueError) as exc:
        raise ValueError("revision admission raw provider output is invalid") from exc
    validated_response = validate_revision_planning_wire_response(
        parsed_response,
        target=target_input.target,
        predecessor_raw_utf8=target_input.predecessor_raw_utf8,
        citation_inputs=target_input.citation_inputs,
        existing_claim_statements={
            item.source_claim_id: item.statement for item in target_input.existing_claims
        },
    )
    if validated_response != output.validated_response:
        raise ValueError("revision typed response differs from exact raw provider output")
    materialized = materialize_revision_planning_response(
        workload=planning_workload,
        shard=target_input,
        response=validated_response,
        analysis_set=analysis_set,
        predecessor_claims=predecessor_claims,
        envelope=envelope,
        inference_artifacts=outcome.artifacts,
    )
    if materialized.output != output:
        raise ValueError("revision proposal was not derived from its exact validated response")
    subject_kwargs = {
        **materialized.subject_kwargs,
        "inference_receipt": execution.receipt,
        "validated_output": output_artifact,
    }
    subject: ManagedRevisionPlan | NoChangeImpactCard
    if output.validated_response.kind == "affected-revision":
        subject = ManagedRevisionPlan.create(**subject_kwargs)
    else:
        subject = NoChangeImpactCard.create(**subject_kwargs)
    subject_kind, exact_subject_id, exact_subject_sha256 = _subject_identity(subject)
    if (
        subject_kind != target.subject_kind
        or exact_subject_id != target.subject_id
        or exact_subject_sha256 != target.subject_sha256
        or subject.analysis.analysis_set_id != analysis_set.analysis_set_id
        or subject.analysis.analysis_set_sha256 != analysis_set.analysis_set_sha256
        or subject.analysis.inference_input != input_artifact
        or subject.predecessor != target_input.predecessor
    ):
        raise ValueError("revision admission subject differs from exact durable inputs")
    expected_bytes = {
        input_artifact.artifact_id: input_bytes,
        output_artifact.artifact_id: output_bytes,
        **{artifact.artifact_id: content for artifact, content in materialized.staged_artifacts},
    }
    expected_artifacts = {
        subject.analysis.inference_input.artifact_id,
        subject.validated_output.artifact_id,
    }
    if isinstance(subject, ManagedRevisionPlan):
        expected_artifacts.update(
            (subject.proposed_raw.artifact_id, subject.proposed_note.artifact_id)
        )
        expected_report_path = (
            f"staging/managed-review/{subject.run_id}/{subject.target_key}/"
            f"source-note-validation-{subject.successor_projection.validator_result_sha256}.json"
        )
        report = next(
            (item for item in target.staged_artifacts if item.path == expected_report_path),
            None,
        )
        if report is None or report.sha256 != (
            subject.successor_projection.validator_result_sha256
        ):
            raise ValueError("revision admission subject omits exact projection report")
        expected_artifacts.add(report.artifact_id)
    if expected_artifacts != {item.artifact_id for item in target.staged_artifacts}:
        raise ValueError("revision admission subject does not explain exact staged artifacts")
    for artifact in target.staged_artifacts:
        expected = expected_bytes.get(artifact.artifact_id)
        if (
            expected is None
            or staging_repository.open_member(
                completion=completion,
                artifact=artifact,
            )
            != expected
        ):
            raise ValueError("revision admission staged member was not deterministically derived")


def reopen_revision_planning_admission(
    binding: ManagedRevisionPlanningAdmissionBinding,
    *,
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    staging_repository: ManagedStagingRepository,
) -> ManagedRevisionPlanningAdmissionBinding:
    """Reopen a durable admission without trusting any process-local capability."""

    if type(binding) is not ManagedRevisionPlanningAdmissionBinding:
        raise TypeError("revision planning admission reopen requires the exact v2 binding")
    exact_binding = ManagedRevisionPlanningAdmissionBinding.model_validate_json(
        canonical_json_bytes(binding.model_dump(mode="json"))
    )
    if exact_binding != binding:
        raise ValueError("revision planning admission is not an exact validated binding")
    binding = exact_binding
    _require_repositories(
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
    )
    if binding.repository_id != evidence_repository.repository_id:
        raise ValueError("revision planning admission belongs to another repository")
    outcomes, batch = evidence_repository.resolve_verified_batch(
        batch_id=binding.batch_id,
        batch_sha256=binding.batch_sha256,
    )
    if _batch_members(batch) != binding.batch_members:
        raise ValueError("revision planning admission batch membership changed on reopen")
    completion = revision_planning_staging_completion(binding)
    staging = staging_repository.resolve_completed_run(completion)
    manifest = staging.manifest
    if (
        manifest.manifest_id != binding.staging_manifest_id
        or manifest.manifest_sha256 != binding.staging_manifest_sha256
        or manifest.repository_id != binding.repository_id
        or manifest.run_id != binding.run_id
    ):
        raise ValueError("revision planning admission staging authority changed on reopen")
    admitted_artifacts = tuple(
        sorted(
            (artifact for target in binding.targets for artifact in target.staged_artifacts),
            key=lambda item: item.artifact_id,
        )
    )
    manifest_artifacts = tuple(item.artifact for item in manifest.members)
    if admitted_artifacts != manifest_artifacts:
        raise ValueError("revision planning admission does not exactly cover staging manifest")
    by_execution = {item.execution.execution_id: item for item in outcomes}
    if len(by_execution) != len(outcomes) or set(by_execution) != {
        item.execution_id for item in binding.targets
    }:
        raise ValueError("revision planning admission targets do not cover reopened outcomes")
    planning_inputs = tuple(
        sorted(
            (
                _parse_planning_input(
                    staging_repository.open_member(
                        completion=completion,
                        artifact=_planning_input_artifact(target, run_id=binding.run_id),
                    )
                )
                for target in binding.targets
            ),
            key=lambda item: (item.target.target_key, item.target.document_version_id),
        )
    )
    impact = _reopen_impact_evidence(
        analysis_set=binding.analysis_set,
        evidence_repository=evidence_repository,
    )
    _require_exact_reviewed_impact_lineage(
        binding=binding,
        analysis_set=binding.analysis_set,
        repository_id=binding.repository_id,
        reviewed_snapshot=reviewed_snapshot,
        impact=impact,
    )
    planning_workload = _reconstruct_exact_planning_workload(
        run_id=binding.run_id,
        planning_inputs=planning_inputs,
        analysis_set=binding.analysis_set,
        impact=impact,
        workload_id=binding.workload_id,
        workload_sha256=binding.workload_sha256,
    )
    inputs_by_target = {item.target.target_key: item for item in planning_workload.input_shards}
    for target in binding.targets:
        target_input = inputs_by_target[target.target_key]
        claims = impact.predecessor_claims.get(target.document_version_id)
        if claims is None:
            raise ValueError("revision admission target lacks exact impact predecessor claims")
        _require_target_output(
            target,
            outcome=by_execution[target.execution_id],
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            completion=completion,
            run_id=binding.run_id,
            workload_id=binding.workload_id,
            workload_sha256=binding.workload_sha256,
            contract_binding_id=binding.contract_binding_id,
            analysis_set=binding.analysis_set,
            planning_workload=planning_workload,
            target_input=target_input,
            predecessor_claims=claims,
        )
    return binding


def bind_recorded_revision_planning_run(
    run: RecordedRevisionPlanningInferenceRun,
    *,
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    staging_repository: ManagedStagingRepository,
) -> ManagedRevisionPlanningAdmissionBinding:
    """Freshly reopen and content-bind one eligible complete PR13 run."""

    if type(run) is not RecordedRevisionPlanningInferenceRun:
        raise TypeError("revision admission requires the exact recorded planning run")
    if not run.workload.input_shards:
        raise ValueError("NO_WORK revision planning cannot enter managed review")
    if run.analysis_set is None:
        raise ValueError("revision admission requires the exact retained analysis set")
    batch_ref = run.evidence_batch
    completion = run.staging_completion
    if type(batch_ref) is not RepositoryVerifiedInferenceEvidenceBatch or type(completion) is not (
        ManagedStagingCompletionBinding
    ):
        raise ValueError("revision admission requires committed batch and completed staging")
    _require_repositories(
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
    )
    if batch_ref.repository_id != evidence_repository.repository_id or (
        completion.repository_id != evidence_repository.repository_id
    ):
        raise ValueError("revision planning run belongs to another repository")
    reopened, fresh_batch = evidence_repository.resolve_verified_batch(
        batch_id=batch_ref.batch_id,
        batch_sha256=batch_ref.batch_sha256,
    )
    if reopened != run.outcomes or _batch_members(fresh_batch) != _batch_members(batch_ref):
        raise ValueError("revision planning run differs from its freshly reopened batch")
    staging = staging_repository.resolve_completed_run(completion)
    manifest = staging.manifest
    subjects = {item.target_key: item for item in run.subjects}
    shards = {item.target.target_key: item for item in run.workload.input_shards}
    outputs = {
        item.revision_planning_output.target_key: item
        for item in reopened
        if item.revision_planning_output is not None
    }
    if (
        len(subjects) != len(run.subjects)
        or set(subjects) != set(shards)
        or set(outputs) != set(shards)
    ):
        raise ValueError("revision admission requires exact target/subject/output coverage")
    contracts = {
        item.execution.contract.contract_binding_id: item.execution.contract for item in reopened
    }
    if len(contracts) != 1:
        raise ValueError("revision admission requires one exact inference contract")
    contract = next(iter(contracts.values()))
    impact = _reopen_impact_evidence(
        analysis_set=run.analysis_set,
        evidence_repository=evidence_repository,
    )
    reviewed = _require_exact_reviewed_impact_lineage(
        binding=None,
        analysis_set=run.analysis_set,
        repository_id=evidence_repository.repository_id,
        reviewed_snapshot=reviewed_snapshot,
        impact=impact,
    )
    planning_workload = _reconstruct_exact_planning_workload(
        run_id=completion.run_id,
        planning_inputs=run.workload.input_shards,
        analysis_set=run.analysis_set,
        impact=impact,
        workload_id=run.workload.workload_id,
        workload_sha256=run.workload.workload_sha256,
    )
    shards = {item.target.target_key: item for item in planning_workload.input_shards}
    manifest_by_target: dict[str, list[ManagedArtifactRef]] = {
        target_key: [] for target_key in subjects
    }
    prefix = ("staging", "managed-review", completion.run_id)
    for member in manifest.members:
        parts = tuple(member.artifact.path.split("/"))
        if parts[:3] != prefix or len(parts) < 5 or parts[3] not in manifest_by_target:
            raise ValueError("revision staging manifest contains a surplus or foreign target")
        manifest_by_target[parts[3]].append(member.artifact)
    targets: list[ManagedRevisionPlanningTargetBinding] = []
    for target_key in sorted(subjects):
        subject = subjects[target_key]
        shard = shards[target_key]
        outcome = outputs[target_key]
        output = outcome.revision_planning_output
        assert output is not None
        actual = tuple(sorted(manifest_by_target[target_key], key=lambda item: item.artifact_id))
        expected = {
            subject.analysis.inference_input.artifact_id,
            subject.validated_output.artifact_id,
        }
        if isinstance(subject, ManagedRevisionPlan):
            expected.update((subject.proposed_raw.artifact_id, subject.proposed_note.artifact_id))
            report_path = (
                f"staging/managed-review/{subject.run_id}/{subject.target_key}/"
                f"source-note-validation-"
                f"{subject.successor_projection.validator_result_sha256}.json"
            )
            report = next((item for item in actual if item.path == report_path), None)
            if report is None or (
                report.kind != ManagedArtifactKind.INFERENCE_OUTPUT
                or report.sha256 != subject.successor_projection.validator_result_sha256
            ):
                raise ValueError("revision plan staging omits exact SourceNote validation report")
            expected.add(report.artifact_id)
        if {item.artifact_id for item in actual} != expected:
            raise ValueError("revision subject does not exactly explain its staged target members")
        if (
            subject.analysis.inference_input.sha256 != shard.shard_sha256
            or subject.validated_output.sha256 != output.output_shard_sha256
            or outcome.execution.receipt != subject.inference_receipt
            or outcome.execution.receipt_artifact != subject.inference_receipt.artifact_ref()
        ):
            raise ValueError("revision subject differs from its exact input/output/receipt")
        subject_kind, subject_id, subject_sha256 = _subject_identity(subject)
        outcome_sha256 = hashlib.sha256(
            canonical_json_bytes(outcome.model_dump(mode="json"))
        ).hexdigest()
        target = ManagedRevisionPlanningTargetBinding(
            target_key=target_key,
            document_version_id=subject.predecessor.document_version_id,
            input_shard_id=shard.shard_id,
            input_shard_sha256=shard.shard_sha256,
            output_shard_id=output.output_shard_id,
            output_shard_sha256=output.output_shard_sha256,
            execution_id=outcome.execution.execution_id,
            outcome_sha256=outcome_sha256,
            receipt_id=subject.inference_receipt.receipt_id,
            receipt_artifact_id=outcome.execution.receipt_artifact.artifact_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_sha256=subject_sha256,
            staged_artifacts=actual,
        )
        _require_target_output(
            target,
            outcome=outcome,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            completion=completion,
            run_id=completion.run_id,
            workload_id=run.workload.workload_id,
            workload_sha256=run.workload.workload_sha256,
            contract_binding_id=contract.contract_binding_id,
            analysis_set=run.analysis_set,
            planning_workload=planning_workload,
            target_input=shard,
            predecessor_claims=impact.predecessor_claims[subject.predecessor.document_version_id],
        )
        targets.append(target)
    binding = ManagedRevisionPlanningAdmissionBinding.create(
        run_id=completion.run_id,
        repository_id=evidence_repository.repository_id,
        workload_id=planning_workload.workload_id,
        workload_sha256=planning_workload.workload_sha256,
        analysis_set=run.analysis_set,
        analysis_set_id=run.analysis_set.analysis_set_id,
        analysis_set_sha256=run.analysis_set.analysis_set_sha256,
        reviewed_snapshot_binding_id=reviewed.binding.binding_id,
        reviewed_snapshot_binding_sha256=reviewed.binding.binding_sha256,
        temporal_decision_record_sha256=reviewed.binding.temporal_decision_record_sha256,
        contract_binding_id=contract.contract_binding_id,
        batch_id=fresh_batch.batch_id,
        batch_sha256=fresh_batch.batch_sha256,
        batch_members=_batch_members(fresh_batch),
        staging_manifest_id=manifest.manifest_id,
        staging_manifest_sha256=manifest.manifest_sha256,
        staging_manifest_path=completion.manifest_path,
        staging_completion_id=completion.completion_id,
        staging_completion_sha256=completion.completion_sha256,
        staging_completion_path=completion.completion_path,
        targets=tuple(targets),
    )
    return reopen_revision_planning_admission(
        binding,
        reviewed_snapshot=reviewed,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
    )


__all__ = [
    "bind_recorded_revision_planning_run",
    "reopen_revision_planning_admission",
    "revision_planning_staging_completion",
]
