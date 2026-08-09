"""Recorded LIVE/REPLAY execution and deterministic M4 revision finalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control.impact_inference import RecordedImpactInferenceRun
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_impact_evidence import (
    bind_recorded_impact_inference_run,
)
from mastervault.change_control.managed_review import (
    MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1,
    MAX_MANAGED_HUNKS_PER_BUNDLE_V1,
    MAX_MANAGED_REVISION_PLANS_V1,
    InferenceExecutionMode,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedInferenceContractBinding,
    ManagedRevisionPlan,
    NoChangeImpactCard,
)
from mastervault.change_control.managed_revision_materialization import (
    MaterializedRevisionTarget,
    materialize_revision_planning_response,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningCitationInput,
    RevisionPlanningCitationInputRole,
    RevisionPlanningCitationInputSet,
    RevisionPlanningEligibility,
    RevisionPlanningEligibilityStatus,
    RevisionPlanningInferenceShard,
    RevisionPlanningOutputShard,
    RevisionPlanningWireResponse,
    RevisionPlanningWorkload,
    evaluate_revision_planning_eligibility,
)
from mastervault.change_control.managed_staging_repository import (
    ManagedStagingCompletionBinding,
    ManagedStagingRepository,
    VerifiedManagedStagingCapability,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    InferenceArtifactPayload,
    InferenceInputEnvelope,
    RecordedInferenceOutcome,
    RecordedInferenceProvider,
    run_revision_planning_inference,
)
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class RevisionPlanningPredecessorSnapshot:
    target_key: str
    raw_path: str
    raw_bytes: bytes
    source_note_path: str
    source_note_bytes: bytes


class RevisionPlanningReplaySourceBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    input_shard_id: str = Field(pattern=r"^revisionin:[0-9a-f]{64}$")
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_artifact: ManagedArtifactRef

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if self.input_shard_id != f"revisionin:{self.input_shard_sha256}":
            raise ValueError("revision replay source input ID differs from its SHA")
        if self.receipt_artifact.kind != ManagedArtifactKind.INFERENCE_RECEIPT or (
            self.receipt_artifact.path
            != f"receipts/inference/{self.receipt_artifact.sha256}.json"
        ):
            raise ValueError("revision replay source requires an exact receipt locator")
        return self


RevisionPlanningSubject = ManagedRevisionPlan | NoChangeImpactCard


@dataclass(frozen=True)
class RecordedRevisionPlanningInferenceRun:
    workload: RevisionPlanningWorkload
    subjects: tuple[RevisionPlanningSubject, ...]
    outcomes: tuple[RecordedInferenceOutcome, ...]
    evidence_batch: RepositoryVerifiedInferenceEvidenceBatch | None
    staging_completion: ManagedStagingCompletionBinding | None
    staging_capability: VerifiedManagedStagingCapability | None

    def __post_init__(self) -> None:
        empty = self.workload.eligibility.status == RevisionPlanningEligibilityStatus.NO_WORK
        if empty:
            if any(
                value not in ((), None)
                for value in (
                    self.subjects,
                    self.outcomes,
                    self.evidence_batch,
                    self.staging_completion,
                    self.staging_capability,
                )
            ):
                raise ValueError("NO_WORK revision run must have zero side-effect evidence")
            return
        if not self.subjects or len(self.subjects) != len(self.workload.input_shards):
            raise ValueError("revision run subjects must cover every workload shard")
        if not self.outcomes or type(self.evidence_batch) is not (
            RepositoryVerifiedInferenceEvidenceBatch
        ):
            raise ValueError("eligible revision run requires durable inference evidence")
        if type(self.staging_capability) is not VerifiedManagedStagingCapability:
            raise ValueError("eligible revision run requires verified manifest-last staging")
        if (
            type(self.staging_completion) is not ManagedStagingCompletionBinding
            or self.staging_capability.completion != self.staging_completion
        ):
            raise ValueError("eligible revision run requires its serializable staging completion")
        keys = tuple(item.target_key for item in self.subjects)
        if keys != tuple(sorted(keys)):
            raise ValueError("revision subjects must use canonical target order")
        expected_targets = {item.target.target_key: item for item in self.workload.input_shards}
        subjects_by_target = {item.target_key: item for item in self.subjects}
        if len(subjects_by_target) != len(self.subjects) or set(subjects_by_target) != set(
            expected_targets
        ):
            raise ValueError("revision subjects must cover every exact workload target")
        outcomes_by_input = {
            item.execution.input_envelope.input_shard_id: item for item in self.outcomes
        }
        if len(outcomes_by_input) != len(self.outcomes) or set(outcomes_by_input) != {
            item.shard_id for item in self.workload.input_shards
        }:
            raise ValueError("revision outcomes must cover every exact workload shard")
        for target_key, shard in expected_targets.items():
            subject = subjects_by_target[target_key]
            outcome = outcomes_by_input[shard.shard_id]
            output = outcome.revision_planning_output
            if (
                subject.run_id != shard.run_id
                or subject.predecessor != shard.predecessor
                or subject.analysis.target_result_sha256
                != shard.target.output_shard_sha256
                or subject.analysis.inference_input.sha256 != shard.shard_sha256
                or outcome.execution.input_envelope.workload_id != self.workload.workload_id
                or outcome.execution.input_envelope.workload_sha256
                != self.workload.workload_sha256
                or output is None
                or output.input_shard_id != shard.shard_id
                or output.input_shard_sha256 != shard.shard_sha256
                or output.target_key != target_key
                or output.output_shard_sha256 != subject.validated_output.sha256
            ):
                raise ValueError("revision run substitutes a workload, subject, or outcome binding")
        batch = self.evidence_batch
        outcome_sha256s = tuple(
            hashlib.sha256(canonical_json_bytes(item.model_dump(mode="json"))).hexdigest()
            for item in self.outcomes
        )
        if (
            batch.repository_id != self.staging_completion.repository_id
            or batch.outcome_count != len(self.outcomes)
            or batch.execution_ids
            != tuple(item.execution.execution_id for item in self.outcomes)
            or batch.receipt_artifact_ids
            != tuple(item.execution.receipt_artifact.artifact_id for item in self.outcomes)
            or batch.outcome_sha256s != outcome_sha256s
        ):
            raise ValueError("revision evidence batch differs from exact returned outcomes")
        if self.staging_completion.run_id != self.workload.input_shards[0].run_id:
            raise ValueError("revision staging completion differs from exact run ID")
        staged_ids = {
            item.artifact.artifact_id for item in self.staging_capability.manifest.members
        }
        required_staged = {
            artifact.artifact_id
            for subject in self.subjects
            for artifact in (
                subject.analysis.inference_input,
                subject.validated_output,
                *(
                    (subject.proposed_raw, subject.proposed_note)
                    if isinstance(subject, ManagedRevisionPlan)
                    else ()
                ),
            )
        }
        if not required_staged.issubset(staged_ids):
            raise ValueError("revision staging manifest omits a returned subject artifact")


def _snapshot_map(
    snapshots: tuple[RevisionPlanningPredecessorSnapshot, ...],
) -> dict[str, RevisionPlanningPredecessorSnapshot]:
    if any(type(item) is not RevisionPlanningPredecessorSnapshot for item in snapshots):
        raise TypeError("predecessor snapshots cannot be substituted")
    by_key = {item.target_key: item for item in snapshots}
    if len(by_key) != len(snapshots):
        raise ValueError("predecessor snapshots require unique target keys")
    return by_key


def _citation_inputs(
    *,
    shard: Any,
    output: Any,
) -> RevisionPlanningCitationInputSet:
    # Both objects have already crossed strict Step-10 boundaries; canonical
    # JSON is a compact, complete governing evidence projection for C0.
    governing = canonical_json_bytes(
        {
            "namespace": "mastervault.revision-planning-governing-evidence.v1",
            "questions": [item.model_dump(mode="json") for item in shard.questions],
            "impact_decisions": [item.model_dump(mode="json") for item in output.decisions],
        }
    ).decode("utf-8")
    return RevisionPlanningCitationInputSet(
        inputs=(
            RevisionPlanningCitationInput(
                input_selector="governing-evidence",
                role=RevisionPlanningCitationInputRole.GOVERNING_EVIDENCE,
                text_utf8=governing,
            ),
            RevisionPlanningCitationInput(
                input_selector="target-evidence",
                role=RevisionPlanningCitationInputRole.TARGET_EVIDENCE,
                text_utf8=shard.target_note.source_note_utf8,
            ),
        )
    )


def _build_workload(
    *,
    run_id: str,
    impact_run: RecordedImpactInferenceRun,
    snapshots: tuple[RevisionPlanningPredecessorSnapshot, ...],
    eligibility: RevisionPlanningEligibility,
    analysis_set: ManagedAnalysisSetBinding,
) -> tuple[
    RevisionPlanningWorkload,
    dict[str, RevisionPlanningPredecessorSnapshot],
    dict[str, Any],
]:
    by_snapshot = _snapshot_map(snapshots)
    expected_keys = {item.target_key for item in eligibility.targets}
    if set(by_snapshot) != expected_keys:
        raise ValueError("predecessor snapshots must cover every eligible target exactly once")
    impact_inputs = {item.shard_id: item for item in impact_run.results.workload.input_shards}
    impact_outputs = {
        item.input_shard_id: item for item in impact_run.results.output_shards
    }
    source_by_target: dict[str, Any] = {}
    planning_inputs: list[RevisionPlanningInferenceShard] = []
    for target in eligibility.targets:
        source = impact_inputs[target.input_shard_id]
        output = impact_outputs[target.input_shard_id]
        snapshot = by_snapshot[target.target_key]
        if (
            snapshot.raw_path != source.target_note.document.source_path
            or hashlib.sha256(snapshot.raw_bytes).hexdigest()
            != source.target_note.document.source_sha256
            or snapshot.source_note_path != source.target_note.source_note_path
            or snapshot.source_note_bytes.decode("utf-8") != source.target_note.source_note_utf8
            or hashlib.sha256(snapshot.source_note_bytes).hexdigest()
            != source.target_note.source_note_sha256
        ):
            raise ValueError("caller predecessor snapshot differs from exact Step-10 evidence")
        try:
            raw_utf8 = snapshot.raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("managed predecessor raw source must be UTF-8 Markdown") from exc
        if any(item.source.evidence for item in source.target_claim_revisions):
            raise ValueError("managed revision planning rejects grounded/PDF predecessor claims")
        planning_inputs.append(
            RevisionPlanningInferenceShard.create(
                eligibility=eligibility,
                run_id=run_id,
                analysis_set_id=analysis_set.analysis_set_id,
                analysis_set_sha256=analysis_set.analysis_set_sha256,
                analysis_as_of=analysis_set.analysis_bootstrap.analysis_as_of,
                target=target,
                predecessor=source.target_note.document,
                predecessor_raw_utf8=raw_utf8,
                predecessor_source_note_path=source.target_note.source_note_path,
                predecessor_source_note_utf8=source.target_note.source_note_utf8,
                citation_inputs=_citation_inputs(shard=source, output=output),
                existing_claim_revisions=source.target_claim_revisions,
            )
        )
        source_by_target[target.target_key] = source
    workload = RevisionPlanningWorkload.create(
        eligibility=eligibility,
        input_shards=tuple(planning_inputs),
    )
    return workload, by_snapshot, source_by_target


def _analysis_set(
    *,
    impact_run: RecordedImpactInferenceRun,
    evidence_repository: FilesystemInferenceEvidenceRepository,
) -> ManagedAnalysisSetBinding:
    impact_evidence = bind_recorded_impact_inference_run(impact_run)
    binding = impact_run.results.workload.index.binding
    temporal_bytes = evidence_repository.resolve_temporal_analysis_manifest(
        manifest_id=binding.temporal_analysis_manifest_id,
        manifest_sha256=binding.temporal_analysis_manifest_sha256,
    )
    temporal = TemporalAnalysisEvidence.from_canonical_bytes(temporal_bytes)
    # Classification retains the revision-2 temporal-analysis root, while Step 10
    # regenerates candidates and attention from the reviewed revision-4 snapshot.
    return ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=temporal.proposal.binding.analysis_bootstrap,
        candidate_result_sha256=binding.relationship_candidate_result_sha256,
        classification_result_sha256=temporal.classification_result_index.result_sha256,
        attention_result_sha256=binding.attention_result_sha256,
        impact_evidence=impact_evidence,
        global_relevant_claim_revision_ids=(
            binding.mechanically_relevant_claim_revision_ids
        ),
    )


def _staged_output_artifact(
    shard: RevisionPlanningInferenceShard,
    output: RevisionPlanningOutputShard,
) -> ManagedArtifactRef:
    return ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        path=(
            f"staging/managed-review/{shard.run_id}/{shard.target.target_key}/"
            f"validated-output-{output.output_shard_sha256}.json"
        ),
        sha256=output.output_shard_sha256,
        byte_count=len(output.canonical_bytes()),
    )


def execute_revision_planning(
    *,
    run_id: str,
    impact_run: RecordedImpactInferenceRun,
    predecessor_snapshots: tuple[RevisionPlanningPredecessorSnapshot, ...],
    contract: ManagedInferenceContractBinding,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    staging_repository: ManagedStagingRepository,
    provider: RecordedInferenceProvider | None = None,
    replay_sources: tuple[RevisionPlanningReplaySourceBinding, ...] = (),
) -> RecordedRevisionPlanningInferenceRun:
    """Execute all targets, stage inert outputs, then commit/reopen one evidence batch."""

    if type(impact_run) is not RecordedImpactInferenceRun:
        raise TypeError("revision planning requires the exact recorded impact run")
    eligibility = evaluate_revision_planning_eligibility(impact_run.results)
    if eligibility.status == RevisionPlanningEligibilityStatus.NO_WORK:
        if predecessor_snapshots:
            raise ValueError("NO_WORK revision planning cannot accept predecessor snapshots")
        if replay_sources or provider is not None:
            raise ValueError("NO_WORK revision planning cannot call provider or replay evidence")
        workload = RevisionPlanningWorkload.create(eligibility=eligibility, input_shards=())
        return RecordedRevisionPlanningInferenceRun(
            workload=workload,
            subjects=(),
            outcomes=(),
            evidence_batch=None,
            staging_completion=None,
            staging_capability=None,
        )
    if sum(
        target.required_response_kind == "affected-revision"
        for target in eligibility.targets
    ) > MAX_MANAGED_REVISION_PLANS_V1:
        raise ValueError("revision planning exceeds downstream managed revision plan limit")
    if evidence_repository.root != staging_repository.root or (
        evidence_repository.repository_id != staging_repository.repository_id
    ):
        raise ValueError("inference evidence and managed staging must share one canonical root")
    if (
        impact_run.evidence_batch is None
        or impact_run.evidence_batch.repository_id != evidence_repository.repository_id
    ):
        raise ValueError("Step-10 impact evidence belongs to another repository")
    analysis_set = _analysis_set(
        impact_run=impact_run,
        evidence_repository=evidence_repository,
    )
    workload, snapshots, source_by_target = _build_workload(
        run_id=run_id,
        impact_run=impact_run,
        snapshots=predecessor_snapshots,
        eligibility=eligibility,
        analysis_set=analysis_set,
    )
    replay_by_input = {item.input_shard_id: item for item in replay_sources}
    if len(replay_by_input) != len(replay_sources):
        raise ValueError("revision replay sources require unique input shards")
    expected_input_ids = {item.shard_id for item in workload.input_shards}
    if contract.mode == InferenceExecutionMode.LIVE:
        if provider is None or replay_sources:
            raise ValueError("LIVE revision planning requires only one provider")
    elif provider is not None or set(replay_by_input) != expected_input_ids:
        raise ValueError("REPLAY revision planning requires exact all-target receipt coverage")

    materialized_candidates: dict[
        tuple[str, str], MaterializedRevisionTarget
    ] = {}
    materialized: dict[str, MaterializedRevisionTarget] = {}
    outcomes: list[RecordedInferenceOutcome] = []
    for shard in workload.input_shards:
        source = source_by_target[shard.target.target_key]

        def materializer(
            response: RevisionPlanningWireResponse,
            envelope: InferenceInputEnvelope,
            artifacts: tuple[InferenceArtifactPayload, ...],
            *,
            _shard: RevisionPlanningInferenceShard = shard,
            _source: Any = source,
        ) -> RevisionPlanningOutputShard:
            result = materialize_revision_planning_response(
                workload=workload,
                shard=_shard,
                response=response,
                analysis_set=analysis_set,
                predecessor_claims=_source.target_claim_revisions,
                envelope=envelope,
                inference_artifacts=artifacts,
            )
            candidate_key = (_shard.shard_id, result.output.output_shard_id)
            prior = materialized_candidates.setdefault(candidate_key, result)
            if prior != result:
                raise ValueError("same revision output identity rematerialized differently")
            return result.output

        replay = replay_by_input.get(shard.shard_id)
        outcome = run_revision_planning_inference(
                contract=contract,
                workload=workload,
                input_shard=shard,
                algorithm_manifest_bytes=algorithm_manifest_bytes,
                prompt_bytes=prompt_bytes,
                response_schema_bytes=response_schema_bytes,
                materialize_output=materializer,
                provider=provider,
                replay_resolver=(
                    evidence_repository if contract.mode == InferenceExecutionMode.REPLAY else None
                ),
                replay_source_receipt_artifact=(
                    replay.receipt_artifact if replay is not None else None
                ),
            )
        output = outcome.revision_planning_output
        if output is None:
            raise ValueError("accepted revision inference omits its typed output")
        accepted = materialized_candidates.get((shard.shard_id, output.output_shard_id))
        if accepted is None or accepted.output != output:
            raise ValueError("accepted revision output was not deterministically materialized")
        materialized[shard.shard_id] = accepted
        outcomes.append(outcome)
    canonical_outcomes = tuple(
        sorted(outcomes, key=lambda item: item.execution.execution_id)
    )
    if set(materialized) != expected_input_ids:
        raise ValueError("revision materialization did not cover every workload shard")
    total_hunks = sum(
        len(result.output.validated_response.edits)
        for result in materialized.values()
        if result.output.validated_response.kind == "affected-revision"
    )
    if total_hunks > MAX_MANAGED_HUNKS_PER_BUNDLE_V1:
        raise ValueError("revision planning exceeds downstream aggregate semantic hunk limit")
    proposal_bundle_bytes = canonical_json_bytes(
        {
            "namespace": "mastervault.managed-revision-proposal-set.v1",
            "run_id": run_id,
            "proposals": [
                json.loads(materialized[shard.shard_id].output.proposal_output_utf8)
                for shard in workload.input_shards
            ],
        }
    )
    if len(proposal_bundle_bytes) > MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1:
        raise ValueError("revision planning exceeds downstream proposal bundle byte limit")

    staged_members: list[tuple[ManagedArtifactRef, bytes]] = []
    staged_outputs: dict[str, ManagedArtifactRef] = {}
    for outcome in canonical_outcomes:
        output = outcome.revision_planning_output
        if output is None:
            raise ValueError("revision outcome omits typed proposal output")
        shard = next(item for item in workload.input_shards if item.shard_id == output.input_shard_id)
        for payload in outcome.artifacts:
            if payload.artifact.path.startswith("staging/managed-review/"):
                staged_members.append(
                    (payload.artifact, payload.content_utf8.encode("utf-8"))
                )
        staged_output = _staged_output_artifact(shard, output)
        staged_outputs[shard.shard_id] = staged_output
        staged_members.append((staged_output, output.canonical_bytes()))
        staged_members.extend(materialized[shard.shard_id].staged_artifacts)
    staging_capability = staging_repository.stage(
        run_id=run_id,
        artifacts=tuple(staged_members),
    )
    batch = evidence_repository.persist_batch(canonical_outcomes)
    reopened = batch.verify(repository=evidence_repository, outcomes=canonical_outcomes)

    subjects: list[RevisionPlanningSubject] = []
    for outcome in reopened:
        output = outcome.revision_planning_output
        if output is None:
            raise ValueError("reopened revision evidence omits typed proposal output")
        staged_output = staged_outputs[output.input_shard_id]
        draft = materialized[output.input_shard_id]
        kwargs = {
            **draft.subject_kwargs,
            "inference_receipt": outcome.execution.receipt,
            "validated_output": staged_output,
        }
        subject: RevisionPlanningSubject
        if output.validated_response.kind == "affected-revision":
            subject = ManagedRevisionPlan.create(**kwargs)
            expected = ManagedRevisionPlan.proposal_output_bytes(**kwargs)
        else:
            subject = NoChangeImpactCard.create(**kwargs)
            expected = NoChangeImpactCard.proposal_output_bytes(**kwargs)
        if expected != output.canonical_bytes():
            raise ValueError("finalized managed subject differs from recorded proposal bytes")
        subjects.append(subject)
    canonical_subjects = tuple(sorted(subjects, key=lambda item: item.target_key))
    staging_capability.verify(staging_repository)
    return RecordedRevisionPlanningInferenceRun(
        workload=workload,
        subjects=canonical_subjects,
        outcomes=reopened,
        evidence_batch=batch,
        staging_completion=staging_capability.completion,
        staging_capability=staging_capability,
    )


__all__ = [
    "RecordedRevisionPlanningInferenceRun",
    "RevisionPlanningPredecessorSnapshot",
    "RevisionPlanningReplaySourceBinding",
    "RevisionPlanningSubject",
    "execute_revision_planning",
]
