"""Real-path execution tests for recorded M4 revision planning."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_impact_inference import _impact_wire
from test_recorded_inference import ALGORITHM, PROMPT, SCHEMA, _contract, _Provider
from test_temporal_proposal import REPO_ROOT

from mastervault.change_control.impact_analysis import (
    ImpactInferenceShard,
    ImpactQuestionRef,
    ImpactWorkload,
    ImpactWorkloadBinding,
    ImpactWorkloadIndex,
)
from mastervault.change_control.impact_inference import RecordedImpactInferenceRun
from mastervault.change_control.impact_results import ImpactDisposition, ImpactResultSet
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.managed_impact_evidence import (
    bind_recorded_impact_inference_run,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedAnalysisSetBinding,
    ManagedRevisionPlan,
    NoChangeImpactCard,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningEligibilityStatus,
    RevisionPlanningTarget,
    UnresolvedImpactForRevisionPlanningError,
    evaluate_revision_planning_eligibility,
)
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    InferenceExecutionFailed,
    RecordedInferenceTask,
    run_impact_inference,
)
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.revision_planning_inference import (
    RecordedRevisionPlanningInferenceRun,
    RevisionPlanningPredecessorSnapshot,
    RevisionPlanningReplaySourceBinding,
    execute_revision_planning,
)

pytest_plugins = ("test_impact_analysis",)

_AFFECTED_TARGET = "sl2-faq-returns"
_NO_CHANGE_TARGET = "process-showroom-demo-unit-rotation"


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _subworkload(
    workload: ImpactWorkload,
    *,
    target_keys: tuple[str, ...],
) -> ImpactWorkload:
    """Re-root an exact Step-10 workload over a selected document universe."""

    selected_keys = set(target_keys)
    selected_shards = tuple(
        sorted(
            (
                shard
                for shard in workload.input_shards
                if shard.target_note.document.document_id in selected_keys
            ),
            key=lambda shard: shard.target_note.document.document_version_id,
        )
    )
    if {item.target_note.document.document_id for item in selected_shards} != selected_keys:
        raise AssertionError("requested real-path impact target is absent")
    selected_document_ids = {
        item.target_note.document.document_version_id for item in selected_shards
    }

    binding_values = workload.index.binding.model_dump(
        mode="json",
        exclude={"binding_id", "binding_sha256"},
    )
    binding_values["document_versions"] = [
        item.model_dump(mode="json")
        for item in workload.index.binding.document_versions
        if item.document_version_id in selected_document_ids
    ]
    binding_sha256 = _digest(
        {
            "namespace": "mastervault.reviewed-impact-workload-binding.v1",
            **binding_values,
        }
    )
    binding = ImpactWorkloadBinding.model_validate_json(
        canonical_json_bytes(
            {
                **binding_values,
                "binding_id": f"impactbinding:{binding_sha256}",
                "binding_sha256": binding_sha256,
            }
        )
    )

    exclusions = tuple(
        item
        for item in workload.exclusions
        if item.target_document.document_version_id in selected_document_ids
    )
    question_refs = tuple(
        sorted(
            (
                ImpactQuestionRef(
                    document_version_id=shard.target_note.document.document_version_id,
                    governing_change_id=question.governing_change.governing_change_id,
                    question_id=question.question_id,
                    question_sha256=question.question_sha256,
                    input_shard_id=shard.shard_id,
                    input_shard_sha256=shard.shard_sha256,
                )
                for shard in selected_shards
                for question in shard.questions
            ),
            key=lambda item: (item.governing_change_id, item.document_version_id),
        )
    )
    exclusion_refs = tuple(
        item
        for item in workload.index.exclusion_refs
        if item.document_version_id in selected_document_ids
    )
    index_values = {
        "binding": binding.model_dump(mode="json"),
        "question_refs": [item.model_dump(mode="json") for item in question_refs],
        "exclusion_refs": [item.model_dump(mode="json") for item in exclusion_refs],
    }
    workload_sha256 = _digest(
        {
            "namespace": "mastervault.reviewed-impact-workload-index.v1",
            "schema_version": 1,
            **index_values,
        }
    )
    index = ImpactWorkloadIndex.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **index_values,
                "workload_id": f"impactwork:{workload_sha256}",
                "workload_sha256": workload_sha256,
            }
        )
    )
    return ImpactWorkload(
        index=index,
        input_shards=selected_shards,
        exclusions=exclusions,
    )


def _persist_temporal_authority(
    authority: ReviewedTemporalSnapshotAuthority,
    repository: FilesystemInferenceEvidenceRepository,
) -> None:
    evidence = authority.temporal_analysis
    repository.persist_temporal_analysis_manifest(
        manifest_id=evidence.manifest_id,
        manifest_sha256=evidence.manifest_sha256,
        content=evidence.canonical_bytes(),
    )


def _recorded_impact_run(
    *,
    workload: ImpactWorkload,
    repository: FilesystemInferenceEvidenceRepository,
    dispositions: dict[str, ImpactDisposition],
) -> RecordedImpactInferenceRun:
    provider = _Provider(
        [
            _impact_wire(
                shard,
                disposition=dispositions[shard.target_note.document.document_id],
            )
            for shard in workload.input_shards
        ]
    )
    outcomes = tuple(
        sorted(
            (
                run_impact_inference(
                    contract=_contract(InferenceExecutionMode.LIVE),
                    workload=workload,
                    input_shard=shard,
                    algorithm_manifest_bytes=ALGORITHM,
                    prompt_bytes=PROMPT,
                    response_schema_bytes=SCHEMA,
                    provider=provider,
                )
                for shard in workload.input_shards
            ),
            key=lambda item: item.execution.execution_id,
        )
    )
    impact_outputs = tuple(item.impact_output for item in outcomes)
    assert all(item is not None for item in impact_outputs)
    decisions = tuple(
        decision for output in impact_outputs if output is not None for decision in output.decisions
    )
    results = ImpactResultSet.create(workload=workload, decisions=decisions)
    batch = repository.persist_batch(outcomes)
    reopened = batch.verify(repository=repository, outcomes=outcomes)
    assert provider.calls == len(workload.input_shards)
    return RecordedImpactInferenceRun(
        results=results,
        outcomes=reopened,
        evidence_batch=batch,
    )


def _predecessor_snapshots(
    workload: ImpactWorkload,
) -> tuple[RevisionPlanningPredecessorSnapshot, ...]:
    snapshots: list[RevisionPlanningPredecessorSnapshot] = []
    for shard in workload.input_shards:
        document = shard.target_note.document
        raw_bytes = (REPO_ROOT / document.source_path).read_bytes()
        # The reviewed Step-10 authority carries the canonical SourceNote bytes;
        # its logical vault path deliberately need not exist in this test checkout.
        note_bytes = shard.target_note.source_note_utf8.encode("utf-8")
        assert hashlib.sha256(raw_bytes).hexdigest() == document.source_sha256
        assert hashlib.sha256(note_bytes).hexdigest() == shard.target_note.source_note_sha256
        assert note_bytes.decode("utf-8") == shard.target_note.source_note_utf8
        snapshots.append(
            RevisionPlanningPredecessorSnapshot(
                target_key=document.document_id,
                raw_path=document.source_path,
                raw_bytes=raw_bytes,
                source_note_path=shard.target_note.source_note_path,
                source_note_bytes=note_bytes,
            )
        )
    return tuple(sorted(snapshots, key=lambda item: item.target_key))


def _affected_response(
    *,
    target: RevisionPlanningTarget,
    source: ImpactInferenceShard,
    snapshot: RevisionPlanningPredecessorSnapshot,
) -> str:
    claim = next(item for item in source.target_claim_revisions if "30 days" in item.statement)
    raw_utf8 = snapshot.raw_bytes.decode("utf-8")
    before = "30 days"
    replacement = "45 days"
    assert raw_utf8.count(before) == 1
    assert claim.statement.count(before) == 1
    start_char = raw_utf8.index(before)
    return json.dumps(
        {
            "kind": "affected-revision",
            "target_key": target.target_key,
            "question_ids": list(target.question_ids),
            "edits": [
                {
                    "start_char": start_char,
                    "end_char": start_char + len(before),
                    "replacement_text": replacement,
                    "citations": [
                        {
                            "input_selector": "governing-evidence",
                            "start_char": 0,
                            "end_char": 1,
                        }
                    ],
                }
            ],
            "source_claim_statement_rewrites": [
                {
                    "source_claim_id": claim.source.source_claim_id,
                    "replacement_statement": claim.statement.replace(before, replacement),
                    "edit_ordinals": [0],
                }
            ],
            "rationale": "The accepted policy change requires one grounded semantic edit.",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _no_change_response(
    *,
    target: RevisionPlanningTarget,
    source: ImpactInferenceShard,
) -> str:
    note = source.target_note.source_note_utf8
    start = next(index for index, value in enumerate(note) if not value.isspace())
    return json.dumps(
        {
            "kind": "no-change",
            "target_key": target.target_key,
            "question_ids": list(target.question_ids),
            "citations": [
                {
                    "input_selector": "target-evidence",
                    "start_char": start,
                    "end_char": start + 1,
                }
            ],
            "rationale": "The target evidence does not require a downstream revision.",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _revision_outputs(
    impact_run: RecordedImpactInferenceRun,
    snapshots: tuple[RevisionPlanningPredecessorSnapshot, ...],
) -> list[str]:
    eligibility = evaluate_revision_planning_eligibility(impact_run.results)
    sources = {
        item.target_note.document.document_id: item
        for item in impact_run.results.workload.input_shards
    }
    snapshots_by_target = {item.target_key: item for item in snapshots}
    outputs: list[str] = []
    for target in eligibility.targets:
        source = sources[target.target_key]
        if target.required_response_kind == "affected-revision":
            outputs.append(
                _affected_response(
                    target=target,
                    source=source,
                    snapshot=snapshots_by_target[target.target_key],
                )
            )
        else:
            outputs.append(_no_change_response(target=target, source=source))
    return outputs


def _two_target_impact_run(
    *,
    full_workload: ImpactWorkload,
    repository: FilesystemInferenceEvidenceRepository,
) -> RecordedImpactInferenceRun:
    workload = _subworkload(
        full_workload,
        target_keys=(_AFFECTED_TARGET, _NO_CHANGE_TARGET),
    )
    return _recorded_impact_run(
        workload=workload,
        repository=repository,
        dispositions={
            _AFFECTED_TARGET: ImpactDisposition.AFFECTED,
            _NO_CHANGE_TARGET: ImpactDisposition.NO_CHANGE_REQUIRED,
        },
    )


def _repository_state(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _replay_sources(
    recorded: RecordedRevisionPlanningInferenceRun,
) -> tuple[RevisionPlanningReplaySourceBinding, ...]:
    workload = recorded.workload
    outcomes = recorded.outcomes
    by_input = {item.execution.input_envelope.input_shard_id: item for item in outcomes}
    return tuple(
        RevisionPlanningReplaySourceBinding(
            input_shard_id=shard.shard_id,
            input_shard_sha256=shard.shard_sha256,
            receipt_artifact=by_input[shard.shard_id].execution.receipt_artifact,
        )
        for shard in workload.input_shards
    )


def test_live_two_target_revision_planning_executes_and_stages_exact_outputs(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / "recorded-revision-live"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    temporal = authority.temporal_analysis
    impact_binding = impact_run.results.workload.index.binding
    assert temporal.relationship_candidates.result_sha256 != (
        impact_binding.relationship_candidate_result_sha256
    )
    expected_analysis_set = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=temporal.proposal.binding.analysis_bootstrap,
        candidate_result_sha256=impact_binding.relationship_candidate_result_sha256,
        classification_result_sha256=temporal.classification_result_index.result_sha256,
        attention_result_sha256=impact_binding.attention_result_sha256,
        impact_evidence=bind_recorded_impact_inference_run(impact_run),
        global_relevant_claim_revision_ids=(
            impact_binding.mechanically_relevant_claim_revision_ids
        ),
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    provider = _Provider(_revision_outputs(impact_run, snapshots))

    recorded = execute_revision_planning(
        run_id="m4-live-two-target",
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
        provider=provider,
    )

    assert provider.calls == 2
    assert len(recorded.subjects) == len(recorded.outcomes) == 2
    assert sum(isinstance(item, ManagedRevisionPlan) for item in recorded.subjects) == 1
    assert sum(isinstance(item, NoChangeImpactCard) for item in recorded.subjects) == 1
    assert all(
        item.execution.task == RecordedInferenceTask.REVISION_PLANNING for item in recorded.outcomes
    )
    assert recorded.evidence_batch is not None
    assert recorded.evidence_batch.outcome_count == 2
    assert recorded.staging_completion is not None
    assert recorded.staging_capability is not None
    manifest = recorded.staging_capability.verify(staging_repository)
    assert manifest.run_id == "m4-live-two-target"

    outcomes_by_target = {
        item.revision_planning_output.target_key: item
        for item in recorded.outcomes
        if item.revision_planning_output is not None
    }
    shards_by_target = {item.target.target_key: item for item in recorded.workload.input_shards}
    assert tuple(item.target_key for item in recorded.subjects) == tuple(
        sorted((_AFFECTED_TARGET, _NO_CHANGE_TARGET))
    )
    for subject in recorded.subjects:
        outcome = outcomes_by_target[subject.target_key]
        output = outcome.revision_planning_output
        assert output is not None
        shard = shards_by_target[subject.target_key]
        assert subject.analysis.schema_version == 2
        assert subject.analysis.analysis_set_id == expected_analysis_set.analysis_set_id
        assert subject.analysis.analysis_set_sha256 == expected_analysis_set.analysis_set_sha256
        assert subject.analysis.staged_input_sha256 == shard.shard_sha256
        assert subject.analysis.input_envelope_sha256 == (
            outcome.execution.input_envelope.envelope_sha256
        )
        assert subject.validated_output.sha256 == output.output_shard_sha256
        assert (root / subject.validated_output.path).read_bytes() == output.canonical_bytes()
        paths = {item.artifact.path for item in outcome.artifacts}
        assert any(path.startswith("inference/citations/governing-evidence/") for path in paths)
        assert any(path.startswith("inference/citations/target-evidence/") for path in paths)

    assert not (root / "datasets").exists()
    assert not (root / "vault").exists()


def test_replay_is_provider_free_and_reproduces_exact_live_proposals(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / "recorded-revision-replay"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    live = execute_revision_planning(
        run_id="m4-live-then-replay",
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
        provider=_Provider(_revision_outputs(impact_run, snapshots)),
    )

    replay = execute_revision_planning(
        run_id="m4-live-then-replay",
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.REPLAY),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
        replay_sources=_replay_sources(live),
    )

    assert all(not item.execution.attempts for item in replay.outcomes)
    live_outputs = {
        item.execution.input_envelope.input_shard_id: item.revision_planning_output
        for item in live.outcomes
    }
    replay_outputs = {
        item.execution.input_envelope.input_shard_id: item.revision_planning_output
        for item in replay.outcomes
    }
    assert replay_outputs == live_outputs
    assert replay.evidence_batch is not None
    assert live.evidence_batch is not None
    assert replay.evidence_batch.batch_id != live.evidence_batch.batch_id
    assert replay.staging_completion == live.staging_completion
    assert replay.staging_capability is not None
    assert live.staging_capability is not None
    assert replay.staging_capability.manifest == live.staging_capability.manifest
    reopened, capability = evidence_repository.resolve_verified_batch(
        batch_id=replay.evidence_batch.batch_id,
        batch_sha256=replay.evidence_batch.batch_sha256,
    )
    assert capability.verify(repository=evidence_repository, outcomes=reopened) == replay.outcomes


def test_live_invalid_response_is_corrected_once_then_committed(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / "recorded-revision-retry"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    valid = _revision_outputs(impact_run, snapshots)
    provider = _Provider(["{}", valid[0], valid[1]])

    recorded = execute_revision_planning(
        run_id="m4-corrected-live",
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
        provider=provider,
    )

    assert provider.calls == 3
    corrected = next(item for item in recorded.outcomes if len(item.execution.attempts) == 2)
    assert tuple(item.accepted for item in corrected.execution.attempts) == (False, True)
    assert provider.requests[1].correction is not None
    assert recorded.evidence_batch is not None
    assert recorded.staging_capability is not None


@pytest.mark.parametrize("failure_target", ("first", "second"))
def test_exhausted_retry_writes_no_batch_or_staging_for_any_partial_target(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
    failure_target: str,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / f"recorded-revision-exhausted-{failure_target}"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    valid = _revision_outputs(impact_run, snapshots)
    outputs = ["{}", "{}"] if failure_target == "first" else [valid[0], "{}", "{}"]
    provider = _Provider(outputs)
    before = _repository_state(root)

    with pytest.raises(InferenceExecutionFailed) as captured:
        execute_revision_planning(
            run_id=f"m4-exhausted-{failure_target}",
            impact_run=impact_run,
            predecessor_snapshots=snapshots,
            contract=_contract(InferenceExecutionMode.LIVE),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            provider=provider,
        )

    assert len(captured.value.attempts) == 2
    assert provider.calls == (2 if failure_target == "first" else 3)
    assert _repository_state(root) == before


@pytest.mark.parametrize("tampered_part", ("raw", "source-note"))
def test_predecessor_snapshot_substitution_fails_before_provider_or_side_effect(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
    tampered_part: str,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / f"recorded-revision-snapshot-{tampered_part}"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    original = snapshots[0]
    tampered = (
        replace(original, raw_bytes=original.raw_bytes + b"\n")
        if tampered_part == "raw"
        else replace(original, source_note_bytes=original.source_note_bytes + b"\n")
    )
    substituted = (tampered, *snapshots[1:])
    provider = _Provider(_revision_outputs(impact_run, snapshots))
    before = _repository_state(root)

    with pytest.raises(
        ValueError,
        match="caller predecessor snapshot differs from exact Step-10 evidence",
    ):
        execute_revision_planning(
            run_id=f"m4-snapshot-{tampered_part}",
            impact_run=impact_run,
            predecessor_snapshots=substituted,
            contract=_contract(InferenceExecutionMode.LIVE),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            provider=provider,
        )

    assert provider.calls == 0
    assert _repository_state(root) == before


def test_replay_rejects_missing_and_substituted_receipts_without_new_effects(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / "recorded-revision-replay-substitution"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    impact_run = _two_target_impact_run(
        full_workload=full_workload,
        repository=evidence_repository,
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    live = execute_revision_planning(
        run_id="m4-replay-substitution",
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
        provider=_Provider(_revision_outputs(impact_run, snapshots)),
    )
    sources = _replay_sources(live)
    before = _repository_state(root)

    with pytest.raises(
        ValueError,
        match="REPLAY revision planning requires exact all-target receipt coverage",
    ):
        execute_revision_planning(
            run_id="m4-replay-substitution",
            impact_run=impact_run,
            predecessor_snapshots=snapshots,
            contract=_contract(InferenceExecutionMode.REPLAY),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            replay_sources=sources[:-1],
        )
    assert _repository_state(root) == before

    swapped = tuple(
        RevisionPlanningReplaySourceBinding(
            input_shard_id=source.input_shard_id,
            input_shard_sha256=source.input_shard_sha256,
            receipt_artifact=sources[1 - index].receipt_artifact,
        )
        for index, source in enumerate(sources)
    )
    with pytest.raises(ValueError, match="replay task/input differs from prior LIVE execution"):
        execute_revision_planning(
            run_id="m4-replay-substitution",
            impact_run=impact_run,
            predecessor_snapshots=snapshots,
            contract=_contract(InferenceExecutionMode.REPLAY),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            replay_sources=swapped,
        )
    assert _repository_state(root) == before


def test_no_work_returns_explicit_zero_effect_run_without_provider(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    _authority, full_workload = impact_fixture
    workload = _subworkload(full_workload, target_keys=())
    impact_run = RecordedImpactInferenceRun(
        results=ImpactResultSet.create(workload=workload, decisions=()),
        outcomes=(),
        evidence_batch=None,
    )
    root = tmp_path / "recorded-revision-no-work"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    before = _repository_state(root)

    recorded = execute_revision_planning(
        run_id="m4-no-work",
        impact_run=impact_run,
        predecessor_snapshots=(),
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence_repository,
        staging_repository=staging_repository,
    )

    assert recorded.workload.eligibility.status == RevisionPlanningEligibilityStatus.NO_WORK
    assert recorded.subjects == recorded.outcomes == ()
    assert recorded.evidence_batch is None
    assert recorded.staging_completion is None
    assert recorded.staging_capability is None
    assert _repository_state(root) == before == {}

    forbidden_provider = _Provider([])
    with pytest.raises(ValueError, match="NO_WORK revision planning cannot call provider"):
        execute_revision_planning(
            run_id="m4-no-work",
            impact_run=impact_run,
            predecessor_snapshots=(),
            contract=_contract(InferenceExecutionMode.LIVE),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            provider=forbidden_provider,
        )
    assert forbidden_provider.calls == 0
    assert _repository_state(root) == before


def test_unresolved_impact_blocks_all_planning_effects_before_provider(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, full_workload = impact_fixture
    root = tmp_path / "recorded-revision-unresolved"
    evidence_repository = FilesystemInferenceEvidenceRepository(root)
    staging_repository = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence_repository)
    workload = _subworkload(
        full_workload,
        target_keys=(_AFFECTED_TARGET, _NO_CHANGE_TARGET),
    )
    impact_run = _recorded_impact_run(
        workload=workload,
        repository=evidence_repository,
        dispositions={
            _AFFECTED_TARGET: ImpactDisposition.UNRESOLVED,
            _NO_CHANGE_TARGET: ImpactDisposition.NO_CHANGE_REQUIRED,
        },
    )
    provider = _Provider([])
    before = _repository_state(root)

    with pytest.raises(UnresolvedImpactForRevisionPlanningError) as captured:
        execute_revision_planning(
            run_id="m4-unresolved",
            impact_run=impact_run,
            predecessor_snapshots=_predecessor_snapshots(workload),
            contract=_contract(InferenceExecutionMode.LIVE),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=evidence_repository,
            staging_repository=staging_repository,
            provider=provider,
        )

    assert captured.value.target_keys == (_AFFECTED_TARGET,)
    assert provider.calls == 0
    assert _repository_state(root) == before
