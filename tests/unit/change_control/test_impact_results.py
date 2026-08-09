from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_impact_analysis import (
    _AuthorityVariants,
    _forged_workload_with_rehashed_attention,
)

import mastervault.change_control as change_control
import mastervault.change_control.impact_results as impact_results
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.impact_analysis import (
    AcceptedGoverningChange,
    ImpactInferenceShard,
    ImpactQuestion,
    ImpactQuestionRef,
    ImpactWorkload,
    ImpactWorkloadBinding,
    ImpactWorkloadIndex,
)
from mastervault.change_control.impact_results import (
    MAX_IMPACT_RATIONALE_UTF8_BYTES_V1,
    ImpactDecision,
    ImpactDisposition,
    ImpactOutputShard,
    ImpactOutputShardRef,
    ImpactResultIndex,
    ImpactResultLimitError,
    ImpactResultSet,
    validate_impact_results,
)
from mastervault.change_control.models import DocumentSpanReference, canonical_json_bytes
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority

pytest_plugins = ("test_impact_analysis",)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _rehashed_decision(data: dict[str, Any]) -> ImpactDecision:
    values = {
        key: value
        for key, value in data.items()
        if key not in {"schema_version", "decision_id", "decision_sha256"}
    }
    digest = _sha256(
        {
            "namespace": "mastervault.actual-impact-decision.v1",
            "schema_version": 1,
            **values,
        }
    )
    return ImpactDecision.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **values,
                "decision_id": f"impactdecision:{digest}",
                "decision_sha256": digest,
            }
        )
    )


def _rehashed_output(data: dict[str, Any]) -> ImpactOutputShard:
    values = {
        key: value
        for key, value in data.items()
        if key not in {"schema_version", "output_shard_id", "output_shard_sha256"}
    }
    digest = _sha256(
        {
            "namespace": "mastervault.actual-impact-output-shard.v1",
            "schema_version": 1,
            **values,
        }
    )
    return ImpactOutputShard.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **values,
                "output_shard_id": f"impactout:{digest}",
                "output_shard_sha256": digest,
            }
        )
    )


def _rehashed_index(data: dict[str, Any]) -> ImpactResultIndex:
    values = {
        key: value
        for key, value in data.items()
        if key not in {"schema_version", "result_id", "result_sha256"}
    }
    digest = _sha256(
        {
            "namespace": "mastervault.actual-impact-result-index.v1",
            "schema_version": 1,
            **values,
        }
    )
    return ImpactResultIndex.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **values,
                "result_id": f"impactresult:{digest}",
                "result_sha256": digest,
            }
        )
    )


def _body_span(shard: ImpactInferenceShard) -> DocumentSpanReference:
    note = shard.target_note
    start = note.body_start_char
    while start < len(note.source_note_utf8) and note.source_note_utf8[start].isspace():
        start += 1
    assert start < len(note.source_note_utf8)
    end = min(start + 80, len(note.source_note_utf8))
    return DocumentSpanReference(
        document_version_id=note.document.document_version_id,
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
        quote=note.source_note_utf8[start:end],
        start_char=start,
        end_char=end,
    )


def _two_small_body_spans(
    shard: ImpactInferenceShard,
) -> tuple[DocumentSpanReference, DocumentSpanReference]:
    note = shard.target_note
    positions = [
        index
        for index in range(note.body_start_char, len(note.source_note_utf8))
        if not note.source_note_utf8[index].isspace()
    ]
    assert len(positions) >= 2
    spans = tuple(
        DocumentSpanReference(
            document_version_id=note.document.document_version_id,
            source_note_path=note.source_note_path,
            source_note_sha256=note.source_note_sha256,
            quote=note.source_note_utf8[index],
            start_char=index,
            end_char=index + 1,
        )
        for index in positions[:2]
    )
    return spans[0], spans[1]


def _decision(
    shard: ImpactInferenceShard,
    *,
    disposition: ImpactDisposition,
    question_index: int = 0,
) -> ImpactDecision:
    question = shard.questions[question_index]
    return ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=disposition,
        evidence_spans=(_body_span(shard),),
        attention_path_context_ids=tuple(item.path_id for item in question.attention_paths),
        dependency_context_ids=tuple(item.dependency_id for item in question.existing_dependencies),
        rationale="The exact SourceNote body evidence supports this actual-impact disposition.",
    )


def _all_decisions(workload: ImpactWorkload) -> tuple[ImpactDecision, ...]:
    dispositions = tuple(ImpactDisposition)
    return tuple(
        _decision(
            shard,
            disposition=dispositions[index % len(dispositions)],
            question_index=question_index,
        )
        for index, shard in enumerate(workload.input_shards)
        for question_index, _question in enumerate(shard.questions)
    )


def _multi_question_workload(workload: ImpactWorkload) -> ImpactWorkload:
    """Build a structurally valid public-model workload with two roots for one document."""

    source_shard = workload.input_shards[0]
    first = source_shard.questions[0]
    root = first.governing_change
    alternate_subject_sha = "1" * 64 if root.original_subject_sha256 != "1" * 64 else "2" * 64
    alternate_root = AcceptedGoverningChange.create(
        constraint=root.constraint,
        original_subject_sha256=alternate_subject_sha,
        review_disposition=root.review_disposition,
        relation=root.relation,
        changed_claim_revision=root.changed_claim_revision,
        upstream_claim_revision=root.upstream_claim_revision,
        changed_temporal_resolution=root.changed_temporal_resolution,
        upstream_temporal_resolution=root.upstream_temporal_resolution,
    )
    second = ImpactQuestion.create(
        governing_change=alternate_root,
        target_document=first.target_document,
        target_temporal_resolution=first.target_temporal_resolution,
        attention_status=first.attention_status,
        attention_paths=first.attention_paths,
        existing_dependencies=first.existing_dependencies,
    )
    shard = ImpactInferenceShard.create(
        target_note=source_shard.target_note,
        target_claim_revisions=source_shard.target_claim_revisions,
        target_temporal_resolution=source_shard.target_temporal_resolution,
        questions=(first, second),
    )

    original_binding = workload.index.binding.model_dump(mode="json")
    binding_values = {
        key: value
        for key, value in original_binding.items()
        if key not in {"binding_id", "binding_sha256"}
    }
    binding_values["governing_changes"] = [
        item.model_dump(mode="json")
        for item in sorted((root, alternate_root), key=lambda item: item.governing_change_id)
    ]
    binding_values["document_versions"] = [first.target_document.model_dump(mode="json")]
    binding_digest = _sha256(
        {
            "namespace": "mastervault.reviewed-impact-workload-binding.v1",
            **binding_values,
        }
    )
    binding = ImpactWorkloadBinding.model_validate_json(
        canonical_json_bytes(
            {
                **binding_values,
                "binding_id": f"impactbinding:{binding_digest}",
                "binding_sha256": binding_digest,
            }
        )
    )
    refs = tuple(
        sorted(
            (
                ImpactQuestionRef(
                    document_version_id=first.target_document.document_version_id,
                    governing_change_id=question.governing_change.governing_change_id,
                    question_id=question.question_id,
                    question_sha256=question.question_sha256,
                    input_shard_id=shard.shard_id,
                    input_shard_sha256=shard.shard_sha256,
                )
                for question in shard.questions
            ),
            key=lambda item: (item.governing_change_id, item.document_version_id),
        )
    )
    index_values = {
        "binding": binding.model_dump(mode="json"),
        "question_refs": [item.model_dump(mode="json") for item in refs],
        "exclusion_refs": [],
    }
    index_digest = _sha256(
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
                "workload_id": f"impactwork:{index_digest}",
                "workload_sha256": index_digest,
            }
        )
    )
    return ImpactWorkload(index=index, input_shards=(shard,), exclusions=())


def _result_with_replacement_output(
    *,
    results: ImpactResultSet,
    replacement: ImpactOutputShard,
) -> ImpactResultSet:
    outputs = tuple(
        replacement if item.document_version_id == replacement.document_version_id else item
        for item in results.output_shards
    )
    refs = tuple(ImpactOutputShardRef.create(item) for item in outputs)
    index = _rehashed_index(
        {
            "schema_version": 1,
            "workload_id": results.result_index.workload_id,
            "workload_sha256": results.result_index.workload_sha256,
            "decision_count": sum(item.decision_count for item in refs),
            "output_shards": [item.model_dump(mode="json") for item in refs],
        }
    )
    return ImpactResultSet.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "workload": results.workload.model_dump(mode="json"),
                "result_index": index.model_dump(mode="json"),
                "output_shards": [item.model_dump(mode="json") for item in outputs],
            }
        )
    )


def test_results_are_complete_grounded_and_deterministically_regenerated(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    authority, workload = impact_fixture
    decisions = _all_decisions(workload)
    results = ImpactResultSet.create(workload=workload, decisions=tuple(reversed(decisions)))
    repeated = ImpactResultSet.create(workload=workload, decisions=decisions)

    assert repeated == results
    assert validate_impact_results(authority, workload=workload, results=results) == results
    encoded = canonical_json_bytes(results.model_dump(mode="json"))
    assert ImpactResultSet.model_validate_json(encoded) == results
    assert {item.question_id for item in results.decisions} == {
        item.question_id for item in workload.questions
    }
    assert results.result_index.decision_count == len(workload.questions)
    assert len(results.output_shards) == len(workload.input_shards)
    assert all(item.decision_id.startswith("impactdecision:") for item in results.decisions)
    assert all(item.output_shard_id.startswith("impactout:") for item in results.output_shards)
    assert results.result_id.startswith("impactresult:")
    first_input = workload.input_shards[0]
    first_output = next(
        item for item in results.output_shards if item.input_shard_id == first_input.shard_id
    )
    assert (
        ImpactOutputShard.create(
            workload=workload,
            input_shard=first_input,
            decisions=first_output.decisions,
        )
        == first_output
    )
    assert change_control.ImpactDecision is ImpactDecision
    assert change_control.ImpactDisposition is ImpactDisposition
    assert change_control.ImpactResultSet is ImpactResultSet
    assert change_control.validate_impact_results is validate_impact_results


def test_missing_duplicate_and_surplus_question_outputs_fail_closed(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    authority_variants: _AuthorityVariants,
) -> None:
    _, workload = impact_fixture
    decisions = _all_decisions(workload)
    assert decisions

    with pytest.raises(ValueError, match="exactly cover selected questions"):
        ImpactResultSet.create(workload=workload, decisions=decisions[:-1])
    with pytest.raises(ValueError, match="duplicate question decisions"):
        ImpactResultSet.create(workload=workload, decisions=(*decisions, decisions[0]))

    zero_workload = change_control.build_impact_workload(authority_variants.all_rejected)
    with pytest.raises(ValueError, match="surplus"):
        ImpactResultSet.create(workload=zero_workload, decisions=(decisions[0],))


def test_source_note_body_spans_are_authority_and_context_is_only_optional(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    question = shard.questions[0]
    valid = _decision(shard, disposition=ImpactDisposition.AFFECTED)
    assert valid.evidence_spans == (_body_span(shard),)

    with pytest.raises(ValueError, match="AFFECTED.*exact SourceNote body evidence"):
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.AFFECTED,
            evidence_spans=(),
            attention_path_context_ids=tuple(item.path_id for item in question.attention_paths),
            dependency_context_ids=tuple(
                item.dependency_id for item in question.existing_dependencies
            ),
            rationale="Graph context alone is not authoritative evidence.",
        )

    no_change = ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=ImpactDisposition.NO_CHANGE_REQUIRED,
        evidence_spans=(),
        attention_path_context_ids=tuple(item.path_id for item in question.attention_paths),
        dependency_context_ids=tuple(item.dependency_id for item in question.existing_dependencies),
        rationale="No body span is required for this complete no-change decision.",
    )
    unresolved = ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=ImpactDisposition.UNRESOLVED,
        evidence_spans=(),
        attention_path_context_ids=no_change.attention_path_context_ids,
        dependency_context_ids=no_change.dependency_context_ids,
        rationale="The same advisory context cannot determine a disposition.",
    )
    assert not no_change.evidence_spans
    assert not unresolved.evidence_spans
    assert no_change.disposition != unresolved.disposition

    note = shard.target_note
    assert note.body_start_char > 0
    frontmatter = DocumentSpanReference(
        document_version_id=note.document.document_version_id,
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
        quote=note.source_note_utf8[0:1],
        start_char=0,
        end_char=1,
    )
    with pytest.raises(ValueError, match="body evidence"):
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.UNRESOLVED,
            evidence_spans=(frontmatter,),
            rationale="The cited location is outside the authoritative note body.",
        )

    exact = _body_span(shard)
    wrong_quote = "Z" if exact.quote[0] != "Z" else "Y"
    mismatched = exact.model_copy(update={"quote": wrong_quote, "end_char": exact.start_char + 1})
    with pytest.raises(ValueError, match="exact character slice"):
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.UNRESOLVED,
            evidence_spans=(mismatched,),
            rationale="The quote must match the exact SourceNote slice.",
        )

    with pytest.raises(ValueError, match="dependency context absent"):
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.NO_CHANGE_REQUIRED,
            evidence_spans=(exact,),
            dependency_context_ids=("dep:not-authoritative",),
            rationale="Unknown dependency context is rejected.",
        )


def test_rehashed_standalone_artifacts_reject_every_locally_knowable_binding_error(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    decision = _decision(shard, disposition=ImpactDisposition.AFFECTED)
    results = ImpactResultSet.create(workload=workload, decisions=_all_decisions(workload))
    output = next(
        item for item in results.output_shards if item.input_shard_id == shard.shard_id
    )

    wrong_question = decision.model_dump(mode="json")
    wrong_question["question_id"] = f"impactq:{'f' * 64}"
    with pytest.raises(ValidationError, match="question ID suffix"):
        _rehashed_decision(wrong_question)

    wrong_workload = output.model_dump(mode="json")
    wrong_workload["workload_id"] = f"impactwork:{'e' * 64}"
    with pytest.raises(ValidationError, match="workload ID suffix"):
        _rehashed_output(wrong_workload)

    wrong_input = output.model_dump(mode="json")
    wrong_input["input_shard_id"] = f"impactin:{'d' * 64}"
    with pytest.raises(ValidationError, match="input shard ID suffix"):
        _rehashed_output(wrong_input)

    wrong_document = decision.model_dump(mode="json")
    wrong_document["evidence_spans"][0]["document_version_id"] = f"docv:{'c' * 64}"
    rehashed_wrong_document = _rehashed_decision(wrong_document)
    wrong_document_output = output.model_dump(mode="json")
    wrong_document_output["decisions"] = [
        (
            rehashed_wrong_document.model_dump(mode="json")
            if item["question_id"] == decision.question_id
            else item
        )
        for item in wrong_document_output["decisions"]
    ]
    with pytest.raises(ValidationError, match="different document"):
        _rehashed_output(wrong_document_output)

    multi = _multi_question_workload(workload)
    multi_shard = multi.input_shards[0]
    multi_decisions = (
        _decision(multi_shard, disposition=ImpactDisposition.AFFECTED, question_index=0),
        _decision(multi_shard, disposition=ImpactDisposition.UNRESOLVED, question_index=1),
    )
    multi_output = ImpactOutputShard.create(
        workload=multi,
        input_shard=multi_shard,
        decisions=multi_decisions,
    )
    incoherent_decision_data = multi_decisions[1].model_dump(mode="json")
    incoherent_decision_data["evidence_spans"][0]["source_note_path"] = "foreign/source.md"
    incoherent_decision = _rehashed_decision(incoherent_decision_data)
    incoherent_output_data = multi_output.model_dump(mode="json")
    incoherent_output_data["decisions"] = [
        (
            incoherent_decision.model_dump(mode="json")
            if item["question_id"] == incoherent_decision.question_id
            else item
        )
        for item in incoherent_output_data["decisions"]
    ]
    with pytest.raises(ValidationError, match="incoherent SourceNote"):
        _rehashed_output(incoherent_output_data)

    ref_data = ImpactOutputShardRef.create(output).model_dump(mode="json")
    ref_data["output_shard_id"] = f"impactout:{'b' * 64}"
    with pytest.raises(ValidationError, match="output shard ref ID suffix"):
        ImpactOutputShardRef.model_validate_json(canonical_json_bytes(ref_data))

    index_data = results.result_index.model_dump(mode="json")
    index_data["workload_id"] = f"impactwork:{'a' * 64}"
    with pytest.raises(ValidationError, match="result workload ID suffix"):
        _rehashed_index(index_data)


def test_rehashed_source_and_context_corruption_requires_enclosing_exact_input(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    decision = _decision(shard, disposition=ImpactDisposition.AFFECTED)
    all_decisions = _all_decisions(workload)

    corruptions = (
        ("source_note_path", "foreign/source.md", "different SourceNote snapshot"),
        ("source_note_sha256", "f" * 64, "different SourceNote snapshot"),
        ("quote", "Z" * len(decision.evidence_spans[0].quote), "exact character slice"),
    )
    for field, value, message in corruptions:
        data = decision.model_dump(mode="json")
        data["evidence_spans"][0][field] = value
        forged = _rehashed_decision(data)
        assert forged.decision_sha256 != decision.decision_sha256
        supplied = tuple(
            forged if item.question_id == decision.question_id else item
            for item in all_decisions
        )
        with pytest.raises(ValueError, match=message):
            ImpactResultSet.create(workload=workload, decisions=supplied)

    foreign_context_data = decision.model_dump(mode="json")
    foreign_context_data["attention_path_context_ids"] = ["attention:foreign"]
    foreign_context = _rehashed_decision(foreign_context_data)
    with pytest.raises(ValueError, match="attention context absent"):
        ImpactResultSet.create(
            workload=workload,
            decisions=tuple(
                foreign_context if item.question_id == decision.question_id else item
                for item in all_decisions
            ),
        )


def test_rehashed_substituted_shard_bindings_are_structural_not_authoritative(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    results = ImpactResultSet.create(workload=workload, decisions=_all_decisions(workload))
    original = results.output_shards[0]
    substituted_data = original.model_dump(mode="json")
    substituted_data.update(
        {
            "workload_id": f"impactwork:{'a' * 64}",
            "workload_sha256": "a" * 64,
            "input_shard_id": f"impactin:{'b' * 64}",
            "input_shard_sha256": "b" * 64,
        }
    )
    substituted = _rehashed_output(substituted_data)
    assert substituted.workload_id != original.workload_id
    assert substituted.input_shard_id != original.input_shard_id

    with pytest.raises(ValidationError, match="different workload|exactly cover workload"):
        _result_with_replacement_output(results=results, replacement=substituted)


def test_result_index_rejects_duplicate_dimensions_and_envelope_rejects_substituted_ref(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    results = ImpactResultSet.create(workload=workload, decisions=_all_decisions(workload))
    refs = [item.model_dump(mode="json") for item in results.result_index.output_shards]
    assert len(refs) >= 2

    duplicate_document = results.result_index.model_dump(mode="json")
    duplicate_document["output_shards"][1]["document_version_id"] = refs[0][
        "document_version_id"
    ]
    with pytest.raises(ValidationError, match="unique canonical document"):
        _rehashed_index(duplicate_document)

    reordered = results.result_index.model_dump(mode="json")
    reordered["output_shards"] = list(reversed(reordered["output_shards"]))
    with pytest.raises(ValidationError, match="unique canonical document"):
        _rehashed_index(reordered)

    duplicate_input = results.result_index.model_dump(mode="json")
    duplicate_input["output_shards"][1]["input_shard_id"] = refs[0]["input_shard_id"]
    duplicate_input["output_shards"][1]["input_shard_sha256"] = refs[0][
        "input_shard_sha256"
    ]
    with pytest.raises(ValidationError, match="unique input shard"):
        _rehashed_index(duplicate_input)

    duplicate_output = results.result_index.model_dump(mode="json")
    duplicate_output["output_shards"][1]["output_shard_id"] = refs[0]["output_shard_id"]
    duplicate_output["output_shards"][1]["output_shard_sha256"] = refs[0][
        "output_shard_sha256"
    ]
    with pytest.raises(ValidationError, match="unique output shard"):
        _rehashed_index(duplicate_output)

    substituted_ref_data = results.result_index.model_dump(mode="json")
    substituted_ref_data["output_shards"][0]["output_shard_id"] = f"impactout:{'f' * 64}"
    substituted_ref_data["output_shards"][0]["output_shard_sha256"] = "f" * 64
    substituted_index = _rehashed_index(substituted_ref_data)
    envelope_data = results.model_dump(mode="json")
    envelope_data["result_index"] = substituted_index.model_dump(mode="json")
    with pytest.raises(ValidationError, match="substituted output shard"):
        ImpactResultSet.model_validate_json(canonical_json_bytes(envelope_data))


def test_document_precedence_is_affected_then_unresolved_then_no_change(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    multi = _multi_question_workload(workload)
    shard = multi.input_shards[0]
    no_change_first = _decision(
        shard,
        disposition=ImpactDisposition.NO_CHANGE_REQUIRED,
        question_index=0,
    )
    no_change_second = _decision(
        shard,
        disposition=ImpactDisposition.NO_CHANGE_REQUIRED,
        question_index=1,
    )
    no_change = ImpactOutputShard.create(
        workload=multi,
        input_shard=shard,
        decisions=(no_change_second, no_change_first),
    )
    assert no_change.document_disposition == ImpactDisposition.NO_CHANGE_REQUIRED

    unresolved = _decision(
        shard,
        disposition=ImpactDisposition.UNRESOLVED,
        question_index=1,
    )
    unresolved_output = ImpactOutputShard.create(
        workload=multi,
        input_shard=shard,
        decisions=(no_change_first, unresolved),
    )
    assert unresolved_output.document_disposition == ImpactDisposition.UNRESOLVED
    reordered_output = unresolved_output.model_dump(mode="json")
    reordered_output["decisions"] = list(reversed(reordered_output["decisions"]))
    with pytest.raises(ValidationError, match="unique canonical question order"):
        _rehashed_output(reordered_output)

    affected = _decision(
        shard,
        disposition=ImpactDisposition.AFFECTED,
        question_index=0,
    )
    affected_results = ImpactResultSet.create(
        workload=multi,
        decisions=(unresolved, affected),
    )
    affected_output = affected_results.output_shards[0]
    assert affected_output.document_disposition == ImpactDisposition.AFFECTED
    assert affected_results.result_index.output_shards[0].document_disposition == (
        ImpactDisposition.AFFECTED
    )

    flipped = affected_output.model_dump(mode="json")
    flipped["document_disposition"] = ImpactDisposition.NO_CHANGE_REQUIRED.value
    with pytest.raises(ValidationError, match="frozen precedence"):
        _rehashed_output(flipped)


def test_zero_root_zero_question_workload_has_a_canonical_empty_result(
    authority_variants: _AuthorityVariants,
) -> None:
    workload = change_control.build_impact_workload(authority_variants.all_rejected)
    assert not workload.questions

    results = ImpactResultSet.create(workload=workload, decisions=())
    assert not results.decisions
    assert not results.output_shards
    assert not results.result_index.output_shards
    assert results.result_index.decision_count == 0
    assert (
        validate_impact_results(
            authority_variants.all_rejected,
            workload=workload,
            results=results,
        )
        == results
    )


def test_multibyte_body_offsets_boundaries_ordering_and_exact_rationale_limit(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    source_shard = workload.input_shards[0]
    prefix = "---\ntitle: café\n---\n"
    body = "🙂é policy body"
    note = CanonicalSourceNoteSnapshot.create(
        document=source_shard.target_note.document,
        source_note_path="synthetic/multibyte.md",
        source_note_utf8=f"{prefix}{body}",
        body_start_char=len(prefix),
    )
    assert note.body_start_char < len(prefix.encode("utf-8"))
    shard = ImpactInferenceShard.create(
        target_note=note,
        target_claim_revisions=(),
        target_temporal_resolution=source_shard.target_temporal_resolution,
        questions=source_shard.questions,
    )
    question = shard.questions[0]
    at_boundary = DocumentSpanReference(
        document_version_id=note.document.document_version_id,
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
        quote="🙂",
        start_char=note.body_start_char,
        end_char=note.body_start_char + 1,
    )
    second = at_boundary.model_copy(
        update={
            "quote": "é",
            "start_char": note.body_start_char + 1,
            "end_char": note.body_start_char + 2,
        }
    )
    decision = ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=ImpactDisposition.AFFECTED,
        evidence_spans=(second, at_boundary),
        rationale="x" * MAX_IMPACT_RATIONALE_UTF8_BYTES_V1,
    )
    assert set(decision.evidence_spans) == {at_boundary, second}
    assert decision.evidence_spans == tuple(
        sorted(
            decision.evidence_spans,
            key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
        )
    )

    duplicate = (at_boundary, at_boundary)
    with pytest.raises(ValidationError, match="unique and canonical"):
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.AFFECTED,
            evidence_spans=duplicate,
            rationale="Duplicate evidence is not canonical.",
        )

    reversed_data = decision.model_dump(mode="json")
    reversed_data["evidence_spans"] = list(reversed(reversed_data["evidence_spans"]))
    with pytest.raises(ValidationError, match="unique and canonical"):
        _rehashed_decision(reversed_data)

    wrong_document = at_boundary.model_copy(
        update={"document_version_id": f"docv:{'f' * 64}"}
    )
    wrong_path = at_boundary.model_copy(update={"source_note_path": "foreign.md"})
    wrong_sha = at_boundary.model_copy(update={"source_note_sha256": "f" * 64})
    out_of_bounds = DocumentSpanReference(
        document_version_id=note.document.document_version_id,
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
        quote="x",
        start_char=len(note.source_note_utf8),
        end_char=len(note.source_note_utf8) + 1,
    )
    for span, message in (
        (wrong_document, "different document"),
        (wrong_path, "different SourceNote snapshot"),
        (wrong_sha, "different SourceNote snapshot"),
        (out_of_bounds, "ends beyond"),
    ):
        with pytest.raises(ValueError, match=message):
            ImpactDecision.create(
                input_shard=shard,
                question=question,
                disposition=ImpactDisposition.UNRESOLVED,
                evidence_spans=(span,),
                rationale="Every supplied span must match the exact target SourceNote.",
            )


def test_evidence_count_and_span_byte_limits_accept_exact_and_reject_observed_plus_one(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    question = shard.questions[0]
    first, second = _two_small_body_spans(shard)
    rationale = "Exercise the exact evidence resource boundary."

    original_count_limit = impact_results.MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1
    monkeypatch.setattr(impact_results, "MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1", 1)
    exact_count = ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=ImpactDisposition.AFFECTED,
        evidence_spans=(first,),
        rationale=rationale,
    )
    assert len(exact_count.evidence_spans) == 1
    with pytest.raises(ImpactResultLimitError) as count_error:
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.AFFECTED,
            evidence_spans=(first, second),
            rationale=rationale,
        )
    assert count_error.value.category == "evidence-spans-per-decision"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1",
        original_count_limit,
    )

    observed_span_bytes = len(canonical_json_bytes(first.model_dump(mode="json")))
    original_span_limit = impact_results.MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
        observed_span_bytes,
    )
    exact_span = ImpactDecision.create(
        input_shard=shard,
        question=question,
        disposition=ImpactDisposition.AFFECTED,
        evidence_spans=(first,),
        rationale=rationale,
    )
    assert exact_span.evidence_spans == (first,)
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
        observed_span_bytes - 1,
    )
    with pytest.raises(ImpactResultLimitError) as span_error:
        ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=ImpactDisposition.AFFECTED,
            evidence_spans=(first,),
            rationale=rationale,
        )
    assert span_error.value.category == "evidence-span-canonical-bytes"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
        original_span_limit,
    )


def test_decision_and_output_shard_byte_limits_accept_exact_and_reject_plus_one(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    decision = _decision(shard, disposition=ImpactDisposition.AFFECTED)

    observed_decision_bytes = len(decision.canonical_bytes())
    original_decision_limit = impact_results.MAX_IMPACT_DECISION_CANONICAL_BYTES_V1
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
        observed_decision_bytes,
    )
    assert _decision(shard, disposition=ImpactDisposition.AFFECTED) == decision
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
        observed_decision_bytes - 1,
    )
    with pytest.raises(ImpactResultLimitError) as decision_error:
        _decision(shard, disposition=ImpactDisposition.AFFECTED)
    assert decision_error.value.category == "decision-canonical-bytes"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
        original_decision_limit,
    )

    decisions = _all_decisions(workload)
    results = ImpactResultSet.create(workload=workload, decisions=decisions)
    output = next(item for item in results.output_shards if item.input_shard_id == shard.shard_id)
    observed_output_bytes = len(output.canonical_bytes())
    original_output_limit = impact_results.MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
        observed_output_bytes,
    )
    assert (
        ImpactOutputShard.create(
            workload=workload,
            input_shard=shard,
            decisions=output.decisions,
        )
        == output
    )
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
        observed_output_bytes - 1,
    )
    with pytest.raises(ImpactResultLimitError) as output_error:
        ImpactOutputShard.create(
            workload=workload,
            input_shard=shard,
            decisions=output.decisions,
        )
    assert output_error.value.category == "complete-document-output-bytes"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
        original_output_limit,
    )


def test_result_index_and_total_output_limits_accept_exact_and_reject_plus_one(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workload = impact_fixture
    decisions = _all_decisions(workload)
    results = ImpactResultSet.create(workload=workload, decisions=decisions)

    observed_index_bytes = len(results.result_index.canonical_bytes())
    original_index_limit = impact_results.MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
        observed_index_bytes,
    )
    assert ImpactResultSet.create(workload=workload, decisions=decisions) == results
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
        observed_index_bytes - 1,
    )
    with pytest.raises(ImpactResultLimitError) as index_error:
        ImpactResultSet.create(workload=workload, decisions=decisions)
    assert index_error.value.category == "result-index-bytes"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
        original_index_limit,
    )

    observed_total_bytes = sum(len(item.canonical_bytes()) for item in results.output_shards)
    original_total_limit = impact_results.MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
        observed_total_bytes,
    )
    assert ImpactResultSet.create(workload=workload, decisions=decisions) == results
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
        observed_total_bytes - 1,
    )
    with pytest.raises(ImpactResultLimitError) as total_error:
        ImpactResultSet.create(workload=workload, decisions=decisions)
    assert total_error.value.category == "total-output-bytes"
    monkeypatch.setattr(
        impact_results,
        "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
        original_total_limit,
    )


def test_frozen_vocabulary_content_ids_bounds_and_pure_imports(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    assert {item.value for item in ImpactDisposition} == {
        "AFFECTED",
        "NO_CHANGE_REQUIRED",
        "UNRESOLVED",
    }
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        ImpactDecision.create(
            input_shard=shard,
            question=shard.questions[0],
            disposition=ImpactDisposition.AFFECTED,
            evidence_spans=(_body_span(shard),),
            rationale="x" * (MAX_IMPACT_RATIONALE_UTF8_BYTES_V1 + 1),
        )

    decision = _decision(shard, disposition=ImpactDisposition.AFFECTED)
    with pytest.raises(ValidationError, match="ID/SHA"):
        ImpactDecision.model_validate(
            decision.model_copy(update={"decision_sha256": "0" * 64}).model_dump()
        )

    source = Path("src/mastervault/change_control/impact_results.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden_fragments = (
        "provider",
        "inference_repository",
        "managed_store",
        "workflow",
        "langgraph",
    )
    assert not any(fragment in module for module in imported for fragment in forbidden_fragments)
    forbidden_calls = {"open", "write", "unlink", "mkdir", "replace", "complete"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(forbidden_calls)


def test_authoritative_validation_rejects_a_rehashed_forged_step_10a_workload(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    authority, workload = impact_fixture
    forged = _forged_workload_with_rehashed_attention(workload)
    forged_results = ImpactResultSet.create(
        workload=forged,
        decisions=_all_decisions(forged),
    )

    with pytest.raises(ValueError, match="authoritative derivation"):
        validate_impact_results(authority, workload=forged, results=forged_results)
