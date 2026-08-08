from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from test_reviewed_snapshot import (
    _copied_store,
    _resolve,
    _ReviewedFixture,
)
from test_temporal_proposal import _build_case, _build_temporal_evidence

import mastervault.change_control as change_control
import mastervault.change_control.impact_analysis as impact_analysis
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.impact_analysis import (
    MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1,
    ExcludedImpactQuestion,
    ImpactAnalysisLimitError,
    ImpactAttentionStatus,
    ImpactExclusionReason,
    ImpactInferenceShard,
    ImpactQuestion,
    ImpactQuestionRef,
    ImpactWorkload,
    ImpactWorkloadIndex,
    build_impact_workload,
    validate_impact_workload,
)
from mastervault.change_control.models import (
    TemporalConstraint,
    TemporalConstraintStatus,
    TemporalResolution,
    TemporalResolutionContext,
    TemporalState,
    TemporalTargetKind,
    canonical_json_bytes,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectEdit,
    ReviewSubjectKind,
)
from mastervault.change_control.reviewed_snapshot import (
    ReviewedTemporalSnapshotAuthority,
    ReviewedTemporalSnapshotAuthorityError,
)
from mastervault.change_control.temporal_commit import commit_temporal_proposal
from mastervault.change_control.temporal_proposal import open_temporal_review

pytest_plugins = ("test_reviewed_snapshot",)


@pytest.fixture(scope="module")
def impact_fixture(
    reviewed_fixture: _ReviewedFixture,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload]:
    store = _copied_store(reviewed_fixture, tmp_path_factory.mktemp("impact-analysis"))
    try:
        authority = _resolve(reviewed_fixture, store)
        return authority, build_impact_workload(authority)
    finally:
        store.close()


@dataclass(frozen=True)
class _AuthorityVariants:
    edited_claim: ReviewedTemporalSnapshotAuthority
    all_rejected: ReviewedTemporalSnapshotAuthority


@pytest.fixture(scope="module")
def authority_variants(
    tmp_path_factory: pytest.TempPathFactory,
) -> _AuthorityVariants:
    case = _build_case(tmp_path_factory.mktemp("impact-authority-variants"))
    evidence = _build_temporal_evidence(case)
    commit = commit_temporal_proposal(
        case.store,
        case.proposal,
        temporal_analysis=evidence,
        evidence_repository=case.evidence_repository,
        classification_batch=case.classification_batch,
        dependency_batch=case.dependency_batch,
        source_note_resolver=case.build_inputs["inventory_resolver"],
    )
    request = open_temporal_review(
        case.store,
        commit,
        requester_id="impact.analysis.requester",
        rationale="Create exact reviewed authorities for impact workload tests.",
        operation_id="impact-analysis-test:review-request",
    )
    base = _ReviewedFixture(
        case=case,
        evidence=evidence,
        commit=commit,
        request_id=request.request.request_id,
        database=case.store.db_path,
    )
    case.store.close()

    def decide_and_resolve(
        *,
        name: str,
        all_rejected: bool,
    ) -> ReviewedTemporalSnapshotAuthority:
        store = _copied_store(base, tmp_path_factory.mktemp(f"impact-{name}"))
        try:
            decision_items: list[ReviewDecisionItem] = []
            for subject in request.request.subjects:
                is_claim_constraint = (
                    subject.kind == ReviewSubjectKind.TEMPORAL_CONSTRAINT
                    and isinstance(subject.subject, TemporalConstraint)
                    and subject.subject.target.kind == TemporalTargetKind.CLAIM_REVISION
                )
                disposition = (
                    ReviewDisposition.REJECTED
                    if all_rejected
                    else (
                        ReviewDisposition.EDITED
                        if is_claim_constraint
                        else ReviewDisposition.ACCEPTED
                    )
                )
                decision_items.append(
                    ReviewDecisionItem(
                        kind=subject.kind,
                        subject_id=subject.subject_id,
                        original_subject_sha256=subject.subject_sha256,
                        disposition=disposition,
                        edit=(
                            ReviewSubjectEdit(
                                kind=subject.kind,
                                subject_id=subject.subject_id,
                                rationale=(
                                    "The reviewed claim supersession is accepted with "
                                    "an explicit impact-analysis rationale."
                                ),
                            )
                            if disposition == ReviewDisposition.EDITED
                            else None
                        ),
                    )
                )
            store.decide_review(
                HumanReviewDecisionCommand(
                    request_id=request.request.request_id,
                    reviewer_id=f"impact.analysis.{name}.approver",
                    rationale=f"Complete the exact {name} impact review variant.",
                    items=tuple(decision_items),
                ),
                operation_id=f"impact-analysis-test:{name}-decision",
            )
            return cast(ReviewedTemporalSnapshotAuthority, _resolve(base, store))
        finally:
            store.close()

    return _AuthorityVariants(
        edited_claim=decide_and_resolve(name="edited-claim", all_rejected=False),
        all_rejected=decide_and_resolve(name="all-rejected", all_rejected=True),
    )


def test_real_reviewed_authority_builds_one_deterministic_closed_world_workload(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    authority, workload = impact_fixture
    repeated = build_impact_workload(authority)

    assert repeated == workload
    workload_bytes = canonical_json_bytes(workload.model_dump(mode="json"))
    assert canonical_json_bytes(repeated.model_dump(mode="json")) == workload_bytes
    assert ImpactWorkload.model_validate_json(workload_bytes) == workload
    assert all(
        shard.canonical_bytes()
        == next(
            repeated_shard.canonical_bytes()
            for repeated_shard in repeated.input_shards
            if repeated_shard.shard_id == shard.shard_id
        )
        for shard in workload.input_shards
    )
    assert validate_impact_workload(authority, workload) == workload
    assert workload.index.binding.reviewed_snapshot_binding == authority.binding
    assert workload.index.binding.temporal_analysis_manifest_id == (
        authority.binding.temporal_analysis_manifest_id
    )
    assert workload.index.binding.review_request_id == authority.review_request.request_id
    assert workload.index.binding.review_decision_payload_sha256 == (
        authority.review_decision.decision_payload_sha256
    )
    assert workload.index.binding.aggregate_sha256 == authority.snapshot.aggregate_sha256

    decisions = {(item.kind, item.subject_id): item for item in authority.review_decision.items}
    rev4_constraints = {
        item.constraint_id: item
        for item in authority.snapshot.aggregate.temporal_constraints.constraints
    }
    expected_constraint_ids: set[str] = set()
    for subject in authority.review_request.subjects:
        if subject.kind != ReviewSubjectKind.TEMPORAL_CONSTRAINT:
            continue
        temporal_subject = rev4_constraints[subject.subject_id]
        if (
            temporal_subject.target.kind == TemporalTargetKind.CLAIM_REVISION
            and decisions[(subject.kind, subject.subject_id)].disposition
            in {ReviewDisposition.ACCEPTED, ReviewDisposition.EDITED}
            and temporal_subject.status == TemporalConstraintStatus.ACCEPTED
        ):
            expected_constraint_ids.add(subject.subject_id)
    governing = workload.index.binding.governing_changes
    assert {item.constraint.constraint_id for item in governing} == expected_constraint_ids
    assert governing
    assert all(
        item.changed_temporal_resolution.state == TemporalState.CURRENT for item in governing
    )
    assert all(
        item.upstream_temporal_resolution.state == TemporalState.HISTORICAL for item in governing
    )

    expected_pairs = {
        (root.governing_change_id, document.document_version_id)
        for root in governing
        for document in workload.index.binding.document_versions
    }
    selected_pairs = {
        (item.governing_change_id, item.document_version_id)
        for item in workload.index.question_refs
    }
    excluded_pairs = {
        (item.governing_change_id, item.document_version_id)
        for item in workload.index.exclusion_refs
    }
    assert selected_pairs.isdisjoint(excluded_pairs)
    assert selected_pairs | excluded_pairs == expected_pairs
    assert change_control.AcceptedGoverningChange is impact_analysis.AcceptedGoverningChange
    assert change_control.ImpactExclusionReason is ImpactExclusionReason
    assert change_control.ImpactInferenceShard is ImpactInferenceShard
    assert change_control.build_impact_workload is build_impact_workload


def test_pair_specific_exclusions_do_not_turn_attention_into_a_selection_gate(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    authority, workload = impact_fixture
    aggregate = authority.snapshot.aggregate
    context = TemporalResolutionContext.from_aggregate(
        aggregate,
        as_of=workload.index.binding.analysis_as_of,
    )
    changed_documents = {
        aggregate.claims.get(claim_id).document.document_version_id
        for claim_id in workload.index.binding.changed_claim_revision_ids
    }
    selected_pairs = {
        (
            question.governing_change.governing_change_id,
            question.target_document.document_version_id,
        )
        for question in workload.questions
    }
    for root in workload.index.binding.governing_changes:
        upstream_document_id = root.upstream_claim_revision.document.document_version_id
        for document in workload.index.binding.document_versions:
            key = (root.governing_change_id, document.document_version_id)
            resolution = context.resolve_document(document)
            expected_selected = (
                document.document_version_id not in changed_documents
                and document.document_version_id != upstream_document_id
                and resolution.state == TemporalState.CURRENT
            )
            assert (key in selected_pairs) == expected_selected

    for exclusion in workload.exclusions:
        has_upstream_reason = ImpactExclusionReason.GOVERNING_UPSTREAM_DOCUMENT in exclusion.reasons
        assert has_upstream_reason == (
            exclusion.target_document.document_version_id
            == exclusion.governing_change.upstream_claim_revision.document.document_version_id
        )

    assert any(
        question.attention_status == ImpactAttentionStatus.UNREACHED
        and not question.attention_paths
        for question in workload.questions
    )
    for question in workload.questions:
        expected_status = (
            ImpactAttentionStatus.UNREACHED
            if not question.attention_paths
            else (
                ImpactAttentionStatus.RANKED
                if any(path.eligible_for_attention for path in question.attention_paths)
                else ImpactAttentionStatus.DISCOVERY_EXCLUDED
            )
        )
        assert question.attention_status == expected_status

    no_path = next(question for question in workload.questions if not question.attention_paths)
    with pytest.raises(ValueError, match="root-specific paths"):
        ImpactQuestion.create(
            governing_change=no_path.governing_change,
            target_document=no_path.target_document,
            target_temporal_resolution=no_path.target_temporal_resolution,
            attention_status=ImpactAttentionStatus.RANKED,
            attention_paths=(),
            existing_dependencies=no_path.existing_dependencies,
        )

    reached = next(question for question in workload.questions if question.attention_paths)
    other_changed_id = next(
        claim_id
        for claim_id in workload.index.binding.changed_claim_revision_ids
        if claim_id != reached.governing_change.changed_claim_revision.claim_revision_id
    )
    unrelated_root = reached.governing_change.model_copy(
        update={"changed_claim_revision": authority.snapshot.aggregate.claims.get(other_changed_id)}
    )
    unrelated_paths = tuple(
        path
        for path in reached.attention_paths
        if impact_analysis._path_is_root_relevant(path, unrelated_root)
    )
    assert not unrelated_paths
    assert (
        impact_analysis._root_attention_status(unrelated_paths) == ImpactAttentionStatus.UNREACHED
    )


def test_exact_limit_boundaries_preflight_before_question_or_shard_identity_mint(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, workload = impact_fixture
    selected_questions = len(workload.questions)
    selected_documents = len(workload.input_shards)
    largest_shard = max(len(item.canonical_bytes()) for item in workload.input_shards)
    total_shards = sum(len(item.canonical_bytes()) for item in workload.input_shards)

    monkeypatch.setattr(impact_analysis, "MAX_IMPACT_QUESTIONS_V1", selected_questions)
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_DOCUMENT_SHARDS_V1",
        selected_documents,
    )
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
        largest_shard,
    )
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_TOTAL_INPUT_BYTES_V1",
        total_shards,
    )
    assert build_impact_workload(authority) == workload

    mint_calls = {"question": 0, "shard": 0}

    def fail_question_mint(*args: object, **kwargs: object) -> None:
        mint_calls["question"] += 1
        raise AssertionError("question identity was minted before preflight")

    def fail_shard_mint(*args: object, **kwargs: object) -> None:
        mint_calls["shard"] += 1
        raise AssertionError("shard identity was minted before preflight")

    monkeypatch.setattr(ImpactQuestion, "create", fail_question_mint)
    monkeypatch.setattr(ImpactInferenceShard, "create", fail_shard_mint)

    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_QUESTIONS_V1",
        selected_questions - 1,
    )
    with pytest.raises(ImpactAnalysisLimitError, match="selected-questions"):
        build_impact_workload(authority)

    monkeypatch.setattr(impact_analysis, "MAX_IMPACT_QUESTIONS_V1", selected_questions)
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_DOCUMENT_SHARDS_V1",
        selected_documents - 1,
    )
    with pytest.raises(ImpactAnalysisLimitError, match="document-input-shards"):
        build_impact_workload(authority)

    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_DOCUMENT_SHARDS_V1",
        selected_documents,
    )
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
        largest_shard - 1,
    )
    with pytest.raises(ImpactAnalysisLimitError, match="complete-document-input-bytes"):
        build_impact_workload(authority)

    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
        largest_shard,
    )
    monkeypatch.setattr(
        impact_analysis,
        "MAX_IMPACT_TOTAL_INPUT_BYTES_V1",
        total_shards - 1,
    )
    with pytest.raises(ImpactAnalysisLimitError, match="total-input-bytes"):
        build_impact_workload(authority)
    assert mint_calls == {"question": 0, "shard": 0}


def test_preflight_rejects_duplicate_logical_current_targets_before_projection(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    first, second = workload.questions[:2]
    duplicate_document = second.target_document.model_copy(
        update={"document_id": first.target_document.document_id}
    )
    first_draft = impact_analysis._ImpactQuestionDraft.create(
        governing_change=first.governing_change,
        target_document=first.target_document,
        target_temporal_resolution=first.target_temporal_resolution,
        attention_status=first.attention_status,
        attention_paths=first.attention_paths,
        existing_dependencies=first.existing_dependencies,
    )
    duplicate_draft = impact_analysis._ImpactQuestionDraft.create(
        governing_change=second.governing_change,
        target_document=duplicate_document,
        target_temporal_resolution=second.target_temporal_resolution,
        attention_status=second.attention_status,
        attention_paths=second.attention_paths,
        existing_dependencies=second.existing_dependencies,
    )
    with pytest.raises(ValueError, match="duplicate logical document IDs"):
        impact_analysis._preflight_impact_inference(
            drafts_by_document={
                first.target_document.document_version_id: [first_draft],
                second.target_document.document_version_id: [duplicate_draft],
            },
            notes={},
            claims_by_document={},
        )


def test_body_only_shards_and_each_exact_temporal_exclusion_remain_explicit(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    question = workload.questions[0]
    original_shard = next(
        shard
        for shard in workload.input_shards
        if shard.target_note.document == question.target_document
    )
    body_only = ImpactInferenceShard.create(
        target_note=original_shard.target_note,
        target_claim_revisions=(),
        target_temporal_resolution=question.target_temporal_resolution,
        questions=(question,),
    )
    assert not body_only.target_claim_revisions
    assert body_only.target_note.source_note_utf8 == original_shard.target_note.source_note_utf8

    root = question.governing_change
    assert root.relation.relation_id is not None
    base = question.target_temporal_resolution
    before = base.as_of - timedelta(days=2)
    closed = base.as_of - timedelta(days=1)
    resolutions = {
        TemporalState.FUTURE: TemporalResolution(
            target=base.target,
            as_of=base.as_of,
            state=TemporalState.FUTURE,
            valid_from_inclusive=base.as_of + timedelta(days=1),
        ),
        TemporalState.HISTORICAL: TemporalResolution(
            target=base.target,
            as_of=base.as_of,
            state=TemporalState.HISTORICAL,
            valid_from_inclusive=before,
            valid_to_exclusive=closed,
            applied_constraint_ids=(root.constraint.constraint_id,),
            basis_relation_ids=(root.relation.relation_id,),
        ),
        TemporalState.EXPIRED: TemporalResolution(
            target=base.target,
            as_of=base.as_of,
            state=TemporalState.EXPIRED,
            valid_from_inclusive=before,
            valid_to_exclusive=closed,
        ),
        TemporalState.UNRESOLVED: TemporalResolution(
            target=base.target,
            as_of=base.as_of,
            state=TemporalState.UNRESOLVED,
            valid_from_inclusive=before,
            conflicts=("conflicting temporal bounds",),
        ),
    }
    for state, resolution in resolutions.items():
        reason = ImpactExclusionReason(state.value)
        excluded = ExcludedImpactQuestion.create(
            governing_change=root,
            target_document=question.target_document,
            target_temporal_resolution=resolution,
            reasons=(reason,),
        )
        assert excluded.reasons == (reason,)
        with pytest.raises(ValueError, match="temporal exclusion reason"):
            ExcludedImpactQuestion.create(
                governing_change=root,
                target_document=question.target_document,
                target_temporal_resolution=resolution,
                reasons=(ImpactExclusionReason.CHANGED_DOCUMENT,),
            )


def test_complete_source_note_limit_fails_without_truncation(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    _, workload = impact_fixture
    shard = workload.input_shards[0]
    oversized_note = CanonicalSourceNoteSnapshot.create(
        document=shard.target_note.document,
        source_note_path=shard.target_note.source_note_path,
        source_note_utf8="x" * (MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1 + 1),
        body_start_char=0,
    )
    with pytest.raises(ImpactAnalysisLimitError, match="complete-document-input-bytes"):
        ImpactInferenceShard.create(
            target_note=oversized_note,
            target_claim_revisions=(),
            target_temporal_resolution=shard.target_temporal_resolution,
            questions=shard.questions,
        )


def test_builder_derives_edited_claim_root_and_real_all_rejected_zero_root(
    authority_variants: _AuthorityVariants,
) -> None:
    edited = build_impact_workload(authority_variants.edited_claim)
    assert len(edited.index.binding.governing_changes) == 1
    edited_root = edited.index.binding.governing_changes[0]
    assert edited_root.review_disposition == ReviewDisposition.EDITED
    assert edited_root.constraint.status == TemporalConstraintStatus.ACCEPTED
    assert "impact-analysis rationale" in edited_root.constraint.rationale

    zero = build_impact_workload(authority_variants.all_rejected)
    assert not zero.index.binding.governing_changes
    assert not zero.questions
    assert not zero.input_shards
    assert not zero.exclusions
    assert zero.index.binding.document_versions
    assert validate_impact_workload(authority_variants.all_rejected, zero) == zero


def test_module_is_a_pure_workload_seam_without_evaluation_or_runtime_side_effects() -> None:
    source = Path("src/mastervault/change_control/impact_analysis.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden_fragments = (
        ".eval",
        "provider",
        "inference_repository",
        "managed_store",
        "staging",
        "workflow",
    )
    assert not any(fragment in module for module in imported for fragment in forbidden_fragments)
    forbidden_calls = {"open", "write", "unlink", "mkdir", "replace", "complete"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(forbidden_calls)


def _forged_workload_with_rehashed_attention(workload: ImpactWorkload) -> ImpactWorkload:
    original_shard, original_question = next(
        (shard, question)
        for shard in workload.input_shards
        for question in shard.questions
        if question.attention_paths or question.existing_dependencies
    )
    forged_paths = () if original_question.attention_paths else original_question.attention_paths
    forged_dependencies = (
        original_question.existing_dependencies if original_question.attention_paths else ()
    )
    forged_question = ImpactQuestion.create(
        governing_change=original_question.governing_change,
        target_document=original_question.target_document,
        target_temporal_resolution=original_question.target_temporal_resolution,
        attention_status=(
            ImpactAttentionStatus.UNREACHED
            if original_question.attention_paths
            else original_question.attention_status
        ),
        attention_paths=forged_paths,
        existing_dependencies=forged_dependencies,
    )
    forged_shard = ImpactInferenceShard.create(
        target_note=original_shard.target_note,
        target_claim_revisions=original_shard.target_claim_revisions,
        target_temporal_resolution=original_shard.target_temporal_resolution,
        questions=(forged_question, *original_shard.questions[1:]),
    )
    shards = tuple(
        forged_shard if shard.shard_id == original_shard.shard_id else shard
        for shard in workload.input_shards
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
        "exclusion_refs": [item.model_dump(mode="json") for item in workload.index.exclusion_refs],
    }
    payload = {
        "namespace": "mastervault.reviewed-impact-workload-index.v1",
        "schema_version": 1,
        **values,
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
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


def test_exact_authority_and_regeneration_reject_tamper_even_after_rehash(
    impact_fixture: tuple[ReviewedTemporalSnapshotAuthority, ImpactWorkload],
) -> None:
    authority, workload = impact_fixture
    forged = _forged_workload_with_rehashed_attention(workload)
    assert forged.index.workload_sha256 != workload.index.workload_sha256
    with pytest.raises(ValueError, match="authoritative derivation"):
        validate_impact_workload(authority, forged)

    class _FakeAuthority(ReviewedTemporalSnapshotAuthority):
        pass

    fake = object.__new__(_FakeAuthority)
    for name in ReviewedTemporalSnapshotAuthority.__dataclass_fields__:
        object.__setattr__(fake, name, getattr(authority, name))
    with pytest.raises(TypeError, match="exact reviewed temporal authority"):
        build_impact_workload(fake)

    tampered_authority = replace(authority)
    object.__setattr__(tampered_authority, "_seal", "0" * 64)
    with pytest.raises(ReviewedTemporalSnapshotAuthorityError, match="seal"):
        build_impact_workload(tampered_authority)
