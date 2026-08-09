from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_impact_analysis import _AuthorityVariants
from test_recorded_inference import (
    ALGORITHM,
    PROMPT,
    SCHEMA,
    _contract,
    _Provider,
    _Resolver,
    _run_live_classification,
)

import mastervault.change_control as change_control
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.impact_analysis import (
    ImpactInferenceShard,
    ImpactQuestionRef,
    ImpactWorkload,
    ImpactWorkloadIndex,
    build_impact_workload,
)
from mastervault.change_control.impact_inference import (
    ImpactReplaySourceBinding,
    RecordedImpactInferenceRun,
    execute_impact_workload,
)
from mastervault.change_control.impact_results import (
    ImpactDecision,
    ImpactDisposition,
    ImpactResultSet,
)
from mastervault.change_control.inference_repository import FilesystemInferenceEvidenceRepository
from mastervault.change_control.managed_impact_evidence import (
    bind_recorded_impact_inference_run,
)
from mastervault.change_control.managed_review import InferenceExecutionMode
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    InferenceExecutionFailed,
    RecordedInferenceOutcome,
    RecordedInferenceTask,
    run_impact_inference,
)
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.temporal_proposal import InferenceExecutionRef

pytest_plugins = ("test_impact_analysis",)


def test_public_package_exports_recorded_impact_execution() -> None:
    assert change_control.run_impact_inference is run_impact_inference
    assert change_control.execute_impact_workload is execute_impact_workload
    assert change_control.ImpactReplaySourceBinding is ImpactReplaySourceBinding


def _body_offsets(shard: ImpactInferenceShard) -> tuple[int, int]:
    note = shard.target_note
    start = note.body_start_char
    while start < len(note.source_note_utf8) and note.source_note_utf8[start].isspace():
        start += 1
    assert start < len(note.source_note_utf8)
    return start, min(start + 80, len(note.source_note_utf8))


def _impact_wire(
    shard: ImpactInferenceShard,
    *,
    disposition: ImpactDisposition = ImpactDisposition.AFFECTED,
) -> str:
    start, end = _body_offsets(shard)
    return json.dumps(
        {
            "schema_version": 1,
            "task": "impact",
            "decisions": [
                {
                    "question_id": question.question_id,
                    "disposition": disposition.value,
                    "spans": (
                        [{"start_char": start, "end_char": end}]
                        if disposition == ImpactDisposition.AFFECTED
                        else []
                    ),
                    "attention_path_context_ids": [
                        item.path_id for item in question.attention_paths
                    ],
                    "dependency_context_ids": [
                        item.dependency_id for item in question.existing_dependencies
                    ],
                    "rationale": "The semantic decision is grounded in the supplied target note.",
                }
                for question in shard.questions
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _workload_with_replacement_shard(
    workload: ImpactWorkload,
    *,
    original: ImpactInferenceShard,
    replacement: ImpactInferenceShard,
) -> ImpactWorkload:
    shards = tuple(
        replacement if item.shard_id == original.shard_id else item
        for item in workload.input_shards
    )
    refs = tuple(
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
                for shard in shards
                for question in shard.questions
            ),
            key=lambda item: (item.governing_change_id, item.document_version_id),
        )
    )
    values = {
        "binding": workload.index.binding.model_dump(mode="json"),
        "question_refs": [item.model_dump(mode="json") for item in refs],
        "exclusion_refs": [
            item.model_dump(mode="json") for item in workload.index.exclusion_refs
        ],
    }
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.reviewed-impact-workload-index.v1",
                "schema_version": 1,
                **values,
            }
        )
    ).hexdigest()
    index = ImpactWorkloadIndex.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **values,
                "workload_id": f"impactwork:{digest}",
                "workload_sha256": digest,
            }
        )
    )
    return ImpactWorkload(index=index, input_shards=shards, exclusions=workload.exclusions)


def test_live_impact_derives_provenance_and_all_content_identities_locally(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    provider = _Provider([_impact_wire(shard)])

    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert provider.calls == 1
    assert outcome.execution.task == RecordedInferenceTask.IMPACT
    assert outcome.classification_output is None
    assert outcome.dependency_output is None
    assert outcome.impact_output is not None
    assert "impact_output" in outcome.model_dump(mode="json")
    decision = outcome.impact_output.decisions[0]
    span = decision.evidence_spans[0]
    assert decision.question_sha256 == shard.questions[0].question_sha256
    assert span.document_version_id == shard.target_note.document.document_version_id
    assert span.source_note_path == shard.target_note.source_note_path
    assert span.source_note_sha256 == shard.target_note.source_note_sha256
    assert span.quote == shard.target_note.source_note_utf8[span.start_char : span.end_char]

    classification, _classification_provider = _run_live_classification()
    assert classification.classification_output is not None
    mixed = outcome.model_dump(mode="json")
    mixed["classification_output"] = classification.classification_output.model_dump(mode="json")
    with pytest.raises(ValueError, match="requires only its exact typed output"):
        RecordedInferenceOutcome.model_validate_json(canonical_json_bytes(mixed))


@pytest.mark.parametrize(
    ("forbidden_field", "forbidden_value"),
    (
        ("quote", "provider-authored evidence is forbidden"),
        ("source_note_path", "provider/authored.md"),
        ("source_note_sha256", "f" * 64),
        ("document_summary", "Provider-authored summaries are not evidence."),
        ("confidence", 0.9),
        ("decision_id", f"impactdecision:{'f' * 64}"),
    ),
)
def test_provider_authored_provenance_is_rejected_then_corrected_once(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    forbidden_field: str,
    forbidden_value: object,
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    invalid = json.loads(_impact_wire(shard))
    invalid["decisions"][0][forbidden_field] = forbidden_value
    provider = _Provider([json.dumps(invalid), _impact_wire(shard)])

    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert provider.calls == 2
    assert len(outcome.execution.attempts) == 2
    assert not outcome.execution.attempts[0].accepted
    assert outcome.execution.attempts[1].accepted
    assert provider.requests[1].correction is not None


@pytest.mark.parametrize(
    ("forbidden_field", "forbidden_value"),
    (
        ("quote", "provider-authored nested quote"),
        ("source_note_path", "provider/nested.md"),
        ("source_note_sha256", "f" * 64),
        ("document_version_id", f"docv:{'f' * 64}"),
    ),
)
def test_nested_span_provenance_is_rejected_then_corrected(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    forbidden_field: str,
    forbidden_value: str,
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    invalid = json.loads(_impact_wire(shard))
    invalid["decisions"][0]["spans"][0][forbidden_field] = forbidden_value
    provider = _Provider([json.dumps(invalid), _impact_wire(shard)])

    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert provider.calls == 2
    assert [attempt.accepted for attempt in outcome.execution.attempts] == [False, True]


@pytest.mark.parametrize("coverage_error", ("duplicate", "missing", "surplus"))
def test_impact_wire_requires_every_question_exactly_once(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    coverage_error: str,
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    invalid = json.loads(_impact_wire(shard))
    if coverage_error == "duplicate":
        invalid["decisions"].append(dict(invalid["decisions"][0]))
    elif coverage_error == "missing":
        invalid["decisions"].pop()
    else:
        surplus = dict(invalid["decisions"][0])
        surplus["question_id"] = f"impactq:{'f' * 64}"
        invalid["decisions"].append(surplus)
    raw = json.dumps(invalid)
    provider = _Provider([raw, raw])

    with pytest.raises(InferenceExecutionFailed):
        run_impact_inference(
            contract=_contract(InferenceExecutionMode.LIVE),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=provider,
        )

    assert provider.calls == 2


def test_multibyte_source_note_offsets_are_python_character_indices(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    original = workload.input_shards[0]
    note_text = "---\ntitle: Emoji\n---\nPréface café 😀 policy applies.\n"
    note = CanonicalSourceNoteSnapshot.create(
        document=original.target_note.document,
        source_note_path=original.target_note.source_note_path,
        source_note_utf8=note_text,
        body_start_char=note_text.index("Préface"),
    )
    shard = ImpactInferenceShard.create(
        target_note=note,
        target_claim_revisions=(),
        target_temporal_resolution=original.target_temporal_resolution,
        questions=original.questions,
    )
    unicode_workload = _workload_with_replacement_shard(
        workload,
        original=original,
        replacement=shard,
    )
    expected_quote = "café 😀"
    start = note_text.index(expected_quote)
    end = start + len(expected_quote)
    assert len(note_text[:start].encode("utf-8")) > start
    assert len(expected_quote.encode("utf-8")) > end - start
    wire = json.loads(_impact_wire(shard))
    for decision in wire["decisions"]:
        decision["spans"] = [{"start_char": start, "end_char": end}]

    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=unicode_workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=_Provider([json.dumps(wire)]),
    )

    assert outcome.impact_output is not None
    span = outcome.impact_output.decisions[0].evidence_spans[0]
    assert (span.start_char, span.end_char, span.quote) == (start, end, expected_quote)


def test_temporal_execution_refs_reject_the_new_impact_task_explicitly(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=_Provider([_impact_wire(shard)]),
    )

    with pytest.raises(ValueError, match="cannot bind actual-impact"):
        InferenceExecutionRef.create(outcome)


def test_frontmatter_offsets_are_semantically_rejected_then_corrected(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    invalid = json.loads(_impact_wire(shard))
    invalid["decisions"][0]["spans"] = [{"start_char": 0, "end_char": 1}]
    provider = _Provider([json.dumps(invalid), _impact_wire(shard)])

    outcome = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert provider.calls == 2
    assert "body" in (outcome.execution.attempts[0].validation_error or "")


def test_impact_replay_is_provider_free_and_rehydrates_local_authority(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    repository = FilesystemInferenceEvidenceRepository(tmp_path / "impact-replay")
    live = run_impact_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=_Provider([_impact_wire(shard)]),
    )
    repository.persist_outcome(live)

    replay = run_impact_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=repository,
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )

    assert replay.impact_output == live.impact_output
    assert replay.execution.attempts == ()
    assert replay.execution.receipt.mode == InferenceExecutionMode.REPLAY


def test_impact_replay_rejects_cross_task_live_evidence(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    shard = workload.input_shards[0]
    classification, _provider = _run_live_classification()

    with pytest.raises(ValueError, match="replay task/input differs"):
        run_impact_inference(
            contract=_contract(InferenceExecutionMode.REPLAY),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            replay_resolver=_Resolver(classification, []),
            replay_source_receipt_artifact=classification.execution.receipt_artifact,
        )


def test_live_workload_result_is_reconstructed_from_freshly_reopened_batch(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    authority_variants: _AuthorityVariants,
    tmp_path: Path,
) -> None:
    authority, workload = impact_fixture
    repository = FilesystemInferenceEvidenceRepository(tmp_path / "impact-live-batch")
    provider = _Provider([_impact_wire(shard) for shard in workload.input_shards])

    run = execute_impact_workload(
        authority,
        workload=workload,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=repository,
        provider=provider,
    )

    assert provider.calls == len(workload.input_shards)
    assert run.evidence_batch is not None
    assert run.evidence_batch.outcome_count == len(workload.input_shards)
    assert len(run.outcomes) == len(workload.input_shards)
    assert len(run.results.output_shards) == len(workload.input_shards)
    managed_binding = bind_recorded_impact_inference_run(run)
    assert managed_binding.batch_id == run.evidence_batch.batch_id
    assert managed_binding.repository_id == run.evidence_batch.repository_id
    assert managed_binding.workload_id == workload.index.workload_id
    assert managed_binding.result_id == run.results.result_id
    assert tuple(item.output_shard_id for item in managed_binding.output_shards) == tuple(
        item.output_shard_id for item in run.results.result_index.output_shards
    )
    fresh = FilesystemInferenceEvidenceRepository(repository.root)
    reopened, reminted = fresh.resolve_verified_batch(
        batch_id=run.evidence_batch.batch_id,
        batch_sha256=run.evidence_batch.batch_sha256,
    )
    assert reminted.verify(repository=fresh, outcomes=reopened) == run.outcomes
    reopened_run = RecordedImpactInferenceRun(
        results=run.results,
        outcomes=reopened,
        evidence_batch=reminted,
    )
    assert reopened_run.outcomes == run.outcomes

    classification, _classification_provider = _run_live_classification()
    unrelated = repository.persist_outcome(classification)
    with pytest.raises(ValueError, match="does not bind the exact recorded outcomes"):
        RecordedImpactInferenceRun(
            results=run.results,
            outcomes=run.outcomes,
            evidence_batch=unrelated,
        )

    by_input = {item.shard_id: item for item in workload.input_shards}
    first_output = run.results.output_shards[0]
    first_decision = first_output.decisions[0]
    input_shard = by_input[first_output.input_shard_id]
    question = next(
        item for item in input_shard.questions if item.question_id == first_decision.question_id
    )
    substituted_decision = ImpactDecision.create(
        input_shard=input_shard,
        question=question,
        disposition=ImpactDisposition.NO_CHANGE_REQUIRED,
        evidence_spans=(),
        attention_path_context_ids=first_decision.attention_path_context_ids,
        dependency_context_ids=first_decision.dependency_context_ids,
        rationale="A substituted but independently canonical impact disposition.",
    )
    decisions = tuple(
        substituted_decision if item.decision_id == first_decision.decision_id else item
        for output in run.results.output_shards
        for item in output.decisions
    )
    substituted_results = ImpactResultSet.create(workload=workload, decisions=decisions)
    with pytest.raises(ValueError, match="do not bind the returned results"):
        RecordedImpactInferenceRun(
            results=substituted_results,
            outcomes=run.outcomes,
            evidence_batch=run.evidence_batch,
        )

    other_workload = build_impact_workload(authority_variants.edited_claim)
    assert other_workload.index.workload_id != workload.index.workload_id
    mismatched_workload_results = run.results.model_copy(update={"workload": other_workload})
    with pytest.raises(ValueError, match="results are not canonical"):
        RecordedImpactInferenceRun(
            results=mismatched_workload_results,
            outcomes=run.outcomes,
            evidence_batch=run.evidence_batch,
        )


def test_zero_workload_calls_no_provider_and_creates_no_evidence_batch(
    authority_variants: _AuthorityVariants,
    tmp_path: Path,
) -> None:
    authority = authority_variants.all_rejected
    workload = build_impact_workload(authority)
    repository = FilesystemInferenceEvidenceRepository(tmp_path / "impact-zero")
    provider = _Provider([])

    run = execute_impact_workload(
        authority,
        workload=workload,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=repository,
        provider=provider,
    )

    assert provider.calls == 0
    assert run.outcomes == ()
    assert run.evidence_batch is None
    assert run.results.result_index.decision_count == 0
    assert not (repository.root / "inference/evidence/batches").exists()
    with pytest.raises(ValueError, match="empty impact inference has no durable batch"):
        bind_recorded_impact_inference_run(run)
    with pytest.raises(ValueError, match=r"exactly outcomes=\(\)"):
        RecordedImpactInferenceRun(
            results=run.results,
            outcomes=[],  # type: ignore[arg-type]
            evidence_batch=None,
        )


def test_two_invalid_impact_attempts_commit_no_evidence_batch(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, workload = impact_fixture
    repository = FilesystemInferenceEvidenceRepository(tmp_path / "impact-invalid-batch")
    provider = _Provider(["not-json", "still-not-json"])

    with pytest.raises(InferenceExecutionFailed) as error:
        execute_impact_workload(
            authority,
            workload=workload,
            contract=_contract(InferenceExecutionMode.LIVE),
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            evidence_repository=repository,
            provider=provider,
        )

    assert provider.calls == 2
    assert len(error.value.attempts) == 2
    assert not any(item.accepted for item in error.value.attempts)
    assert not (repository.root / "inference/evidence/batches").exists()
    assert not (repository.root / "inference/evidence/outcomes").exists()


def test_replay_workload_requires_exact_source_coverage_and_commits_new_batch(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    tmp_path: Path,
) -> None:
    authority, workload = impact_fixture
    repository = FilesystemInferenceEvidenceRepository(tmp_path / "impact-workload-replay")
    live_provider = _Provider([_impact_wire(shard) for shard in workload.input_shards])
    live = execute_impact_workload(
        authority,
        workload=workload,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=repository,
        provider=live_provider,
    )
    sources = tuple(
        sorted(
            (
                ImpactReplaySourceBinding(
                    input_shard_id=outcome.execution.input_envelope.input_shard_id,
                    input_shard_sha256=outcome.execution.input_envelope.input_shard_sha256,
                    receipt_artifact=outcome.execution.receipt_artifact,
                )
                for outcome in live.outcomes
            ),
            key=lambda item: item.input_shard_id,
        )
    )

    replay = execute_impact_workload(
        authority,
        workload=workload,
        contract=_contract(InferenceExecutionMode.REPLAY),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=repository,
        replay_sources=sources,
    )

    assert replay.results == live.results
    assert replay.evidence_batch is not None
    assert live.evidence_batch is not None
    assert replay.evidence_batch.batch_id != live.evidence_batch.batch_id
    assert all(
        outcome.execution.receipt.mode == InferenceExecutionMode.REPLAY
        for outcome in replay.outcomes
    )
