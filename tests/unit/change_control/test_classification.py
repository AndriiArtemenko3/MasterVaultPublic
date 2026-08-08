from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import mastervault.change_control as change_control_package
import mastervault.change_control.classification as classification_module
from mastervault.change_control.bootstrap import bootstrap_analysis_aggregate
from mastervault.change_control.classification import (
    MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1,
    MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1,
    MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
    CandidateExclusionReason,
    CandidateSelectionReason,
    ClaimPairClassification,
    ClassificationLimitError,
    ClassificationResultIndex,
    ClassificationResultSet,
    ClassificationWorkload,
    GraphMaterializationStatus,
    SelectedCandidateRef,
    materialize_relation_assessments,
    select_classification_workload,
    validate_classification_results,
    validate_classification_workload,
)
from mastervault.change_control.discovery import (
    RelationshipCandidate,
    RelationshipCandidateSet,
    generate_relationship_candidates,
)
from mastervault.change_control.incoming import MANIFEST_RELATIVE_PATH
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    PersistedRelationType,
    RelationGraph,
    TemporalConstraintSet,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.store import ChangeControlSnapshot, SqliteChangeControlStore

SHA_A = "a" * 64
SHA_B = "b" * 64
PACKAGE_PATH = (
    Path(__file__).resolve().parents[3] / "src/mastervault/change_control/classification.py"
)


def _document(
    document_id: str,
    *,
    family: str,
    start: date,
) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=document_id,
        document_family=family,
        version_label=document_id,
        source_path=f"datasets/runtime/{document_id}.md",
        source_sha256=SHA_A,
        declared_effective_from=start,
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )


def _claim(
    document: DocumentVersionMetadata,
    local_id: str,
    *,
    scopes: tuple[str, ...],
    statement: str | None = None,
) -> VersionedClaimRevision:
    source_claim_id = (
        local_id if local_id[-3:-2] == "-" and local_id[-2:].isdigit() else f"{local_id}-01"
    )
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=f"runtime/sources/{document.document_id}.md",
            source_note_sha256=SHA_B,
            source_claim_id=source_claim_id,
        ),
        statement=statement or f"Canonical policy statement for {local_id} remains applicable.",
        declared_effective_from=document.declared_effective_from,
        scopes=scopes,
    )


def _snapshot(
    documents: tuple[DocumentVersionMetadata, ...],
    claims: tuple[VersionedClaimRevision, ...],
) -> ChangeControlSnapshot:
    aggregate = ChangeControlAggregate.create(
        aggregate_id="classification-fixture",
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    return ChangeControlSnapshot(
        aggregate=aggregate,
        revision=2,
        aggregate_sha256=aggregate_sha256(aggregate),
    )


def _fixture() -> tuple[
    ChangeControlSnapshot,
    RelationshipCandidateSet,
    VersionedClaimRevision,
    VersionedClaimRevision,
    VersionedClaimRevision,
    VersionedClaimRevision,
]:
    changed_doc = _document(
        "returns-v2",
        family="policy.returns",
        start=date(2026, 1, 1),
    )
    older_doc = _document(
        "returns-v1",
        family="policy.returns",
        start=date(2024, 1, 1),
    )
    shared_cross_family_doc = _document(
        "support-macro-v1",
        family="support.returns",
        start=date(2025, 1, 1),
    )
    unrelated_doc = _document(
        "security-v1-0",
        family="policy.security",
        start=date(2025, 1, 1),
    )
    changed = _claim(
        changed_doc,
        "return-window",
        scopes=("policy.returns", "returns.window"),
        statement="Premium customers have 45 days to return an eligible item.",
    )
    older = _claim(
        older_doc,
        "return-window",
        scopes=("policy.returns", "returns.window"),
        statement="Premium customers have 30 days to return an eligible item.",
    )
    cross_family = _claim(
        shared_cross_family_doc,
        "return-window-copy",
        scopes=("returns.window", "support.returns"),
        statement="The customer-facing macro states a 30 day premium return window.",
    )
    unrelated = _claim(
        unrelated_doc,
        "password-length",
        scopes=("policy.security",),
        statement="Employees must use passwords containing at least fourteen characters.",
    )
    other_unrelated_documents = tuple(
        _document(
            f"unrelated-v1-{index}",
            family=f"policy.unrelated-{index}",
            start=date(2025, 1, 1),
        )
        for index in range(1, 6)
    )
    other_unrelated_claims = tuple(
        _claim(
            document,
            f"unrelated-topic-{index}",
            scopes=(f"policy.unrelated-{index}",),
            statement=f"Distinct operational topic {index} has a separate documented procedure.",
        )
        for index, document in enumerate(other_unrelated_documents, start=1)
    )
    snapshot = _snapshot(
        (
            changed_doc,
            older_doc,
            shared_cross_family_doc,
            unrelated_doc,
            *other_unrelated_documents,
        ),
        (changed, older, cross_family, unrelated, *other_unrelated_claims),
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    return snapshot, candidates, changed, older, cross_family, unrelated


def _candidate_for(
    candidates: RelationshipCandidateSet,
    incumbent: VersionedClaimRevision,
) -> RelationshipCandidate:
    return next(
        item
        for item in candidates.candidates
        if item.incumbent_claim_revision_id == incumbent.claim_revision_id
    )


def _classification(
    candidate: RelationshipCandidate,
    snapshot: ChangeControlSnapshot,
    disposition: PairDisposition,
    *,
    newer_revision_id: str | None = None,
) -> ClaimPairClassification:
    by_id = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    return ClaimPairClassification.create(
        candidate=candidate,
        endpoint_revisions=(
            by_id[candidate.claim_revision_ids[0]],
            by_id[candidate.claim_revision_ids[1]],
        ),
        disposition=disposition,
        rationale="Deterministic synthetic classification rationale.",
        confidence=0.9,
        newer_revision_id=newer_revision_id,
    )


def _complete_classifications(
    workload: ClassificationWorkload,
    candidates: RelationshipCandidateSet,
    snapshot: ChangeControlSnapshot,
    *,
    overrides: dict[str, PairDisposition] | None = None,
) -> tuple[ClaimPairClassification, ...]:
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    overrides = overrides or {}
    return tuple(
        _classification(
            candidate_by_id[selected.pair_id],
            snapshot,
            overrides.get(selected.pair_id, PairDisposition.UNRELATED),
        )
        for selected in workload.selected
    )


def _result_fixture(
    *,
    two_changed_roots: bool = False,
) -> tuple[
    ChangeControlSnapshot,
    RelationshipCandidateSet,
    ClassificationWorkload,
    ClassificationResultSet,
]:
    snapshot, _candidates, changed, older, *_ = _fixture()
    changed_ids = (
        (changed.claim_revision_id, older.claim_revision_id)
        if two_changed_roots
        else (changed.claim_revision_id,)
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=changed_ids,
        as_of=date(2026, 2, 1),
    )
    workload = select_classification_workload(snapshot, candidates=candidates)
    results = ClassificationResultSet.create(
        workload=workload,
        classifications=_complete_classifications(workload, candidates, snapshot),
    )
    return snapshot, candidates, workload, results


def test_selector_partitions_exhaustive_candidates_without_silent_top_k() -> None:
    snapshot, candidates, _changed, older, cross_family, unrelated = _fixture()

    workload = select_classification_workload(snapshot, candidates=candidates)

    assert workload.source_candidate_count == 8
    assert len(workload.selected) == 6
    assert len(workload.excluded) == 2
    selected_by_id = {item.pair_id: item for item in workload.selected}
    older_selection = selected_by_id[_candidate_for(candidates, older).pair_id]
    assert older_selection.reasons == (
        CandidateSelectionReason.SAME_DOCUMENT_FAMILY,
        CandidateSelectionReason.SHARED_SCOPE,
    )
    cross_selection = selected_by_id[_candidate_for(candidates, cross_family).pair_id]
    assert cross_selection.reasons == (CandidateSelectionReason.SHARED_SCOPE,)
    assert (
        len(
            [
                item
                for item in workload.selected
                if item.reasons == (CandidateSelectionReason.DETERMINISTIC_COVERAGE_SAMPLE,)
            ]
        )
        == 4
    )
    assert all(
        item.reason == CandidateExclusionReason.COVERAGE_SAMPLE_QUOTA for item in workload.excluded
    )
    assert _candidate_for(candidates, unrelated).pair_id in {
        item.pair_id for item in (*workload.selected, *workload.excluded)
    }


def test_selector_and_content_addresses_replay_deterministically() -> None:
    snapshot, candidates, *_ = _fixture()

    first = select_classification_workload(snapshot, candidates=candidates)
    replayed_candidates = RelationshipCandidateSet.model_validate(
        candidates.model_dump(mode="json")
    )
    second = select_classification_workload(snapshot, candidates=replayed_candidates)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.inference_shards) == 1
    assert tuple(pair.candidate.pair_id for pair in first.inference_shards[0].pairs) == tuple(
        sorted(item.pair_id for item in first.selected)
    )
    candidates_by_id = {item.pair_id: item for item in candidates.candidates}
    revisions_by_id = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    for pair in first.inference_shards[0].pairs:
        assert pair.candidate == candidates_by_id[pair.candidate.pair_id]
        assert pair.endpoint_revisions == tuple(
            revisions_by_id[item] for item in pair.candidate.claim_revision_ids
        )
        assert pair.candidate.changed_temporal_resolution == (
            candidates_by_id[pair.candidate.pair_id].changed_temporal_resolution
        )
        assert pair.candidate.incumbent_temporal_resolution == (
            candidates_by_id[pair.candidate.pair_id].incumbent_temporal_resolution
        )
    assert len(canonical_json_bytes(first.inference_shards[0].model_dump(mode="json"))) < (
        MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1
    )


def test_cross_family_lexical_policy_slot_is_selected_without_shared_scope() -> None:
    changed_doc = _document("lexical-v2", family="policy.returns", start=date(2026, 1, 1))
    incumbent_doc = _document("lexical-v1", family="support.macro", start=date(2025, 1, 1))
    changed = _claim(
        changed_doc,
        "window-new",
        scopes=("policy.returns",),
        statement="Premium return window is 45 days for eligible orders.",
    )
    incumbent = _claim(
        incumbent_doc,
        "window-old",
        scopes=("support.macro",),
        statement="Premium return window is 30 days for eligible orders.",
    )
    snapshot = _snapshot((changed_doc, incumbent_doc), (changed, incumbent))
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )

    workload = select_classification_workload(snapshot, candidates=candidates)

    assert workload.selected[0].reasons == (CandidateSelectionReason.LEXICAL_POLICY_SLOT,)


def test_numeric_unit_without_lexical_context_is_only_a_neutral_coverage_sample() -> None:
    changed_doc = _document("numeric-v2", family="policy.returns", start=date(2026, 1, 1))
    incumbent_doc = _document("numeric-v1", family="facilities.showroom", start=date(2025, 1, 1))
    changed = _claim(
        changed_doc,
        "window-new",
        scopes=("policy.returns",),
        statement="Threshold is 45 days.",
    )
    incumbent = _claim(
        incumbent_doc,
        "capacity-old",
        scopes=("facilities.showroom",),
        statement="Capacity is 30 days.",
    )
    snapshot = _snapshot((changed_doc, incumbent_doc), (changed, incumbent))
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )

    workload = select_classification_workload(snapshot, candidates=candidates)

    assert workload.selected[0].reasons == (CandidateSelectionReason.DETERMINISTIC_COVERAGE_SAMPLE,)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_workload_validation_rejects_missing_duplicate_and_unknown_selection(
    mutation: str,
) -> None:
    snapshot, candidates, *_ = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    selected = list(workload.selected)
    excluded = list(workload.excluded)
    if mutation == "missing":
        selected.pop()
    elif mutation == "duplicate":
        selected.append(selected[0])
    else:
        selected.append(selected[0].model_copy(update={"pair_id": excluded[0].pair_id}))
        excluded.pop()
    altered = workload.model_copy(update={"selected": tuple(selected), "excluded": tuple(excluded)})

    with pytest.raises(ValidationError):
        validate_classification_workload(
            snapshot,
            candidates=candidates,
            workload=altered,
        )


def test_workload_factory_rejects_unknown_balanced_and_cross_swapped_references() -> None:
    snapshot, candidates, *_ = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    revisions = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}

    unknown_excluded = workload.excluded[0].model_copy(update={"pair_id": f"pair:{'0' * 64}"})
    with pytest.raises(ValueError, match="exactly partition"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=workload.selected,
            excluded=(*workload.excluded, unknown_excluded),
        )

    with pytest.raises(ValueError, match="exactly partition"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=workload.selected,
            excluded=(*workload.excluded[:-1], unknown_excluded),
        )

    swapped_selected = workload.selected[0].model_copy(
        update={"candidate_sha256": workload.excluded[0].candidate_sha256}
    )
    swapped_excluded = workload.excluded[0].model_copy(
        update={"candidate_sha256": workload.selected[0].candidate_sha256}
    )
    with pytest.raises(ValueError, match="reference SHA"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=(swapped_selected, *workload.selected[1:]),
            excluded=(swapped_excluded, *workload.excluded[1:]),
        )


def test_workload_factory_rejects_duplicate_missing_and_cross_partition_ids() -> None:
    snapshot, candidates, *_ = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    revisions = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}

    with pytest.raises(ValueError, match="selected candidate references must be unique"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=(*workload.selected, workload.selected[0]),
            excluded=workload.excluded,
        )

    with pytest.raises(ValueError, match="exactly partition"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=workload.selected[:-1],
            excluded=workload.excluded,
        )

    cross_partition = workload.excluded[0].model_copy(
        update={
            "pair_id": workload.selected[0].pair_id,
            "candidate_sha256": workload.selected[0].candidate_sha256,
        }
    )
    with pytest.raises(ValueError, match="must be disjoint"):
        ClassificationWorkload.create(
            candidates=candidates,
            revisions_by_id=revisions,
            selected=workload.selected,
            excluded=(cross_partition, *workload.excluded[1:]),
        )


def test_selector_rejects_reordered_or_tampered_candidate_sets() -> None:
    snapshot, candidates, *_ = _fixture()
    reordered = candidates.model_copy(update={"candidates": tuple(reversed(candidates.candidates))})
    with pytest.raises(ValidationError):
        select_classification_workload(snapshot, candidates=reordered)

    first = candidates.candidates[0]
    tampered_candidate = first.model_copy(update={"changed_document_family": "forged.family"})
    tampered = candidates.model_copy(
        update={"candidates": (tampered_candidate, *candidates.candidates[1:])}
    )
    with pytest.raises(ValidationError):
        select_classification_workload(snapshot, candidates=tampered)


def test_selector_fails_closed_above_256_instead_of_returning_a_prefix() -> None:
    changed_doc = _document("bulk-v2", family="policy.bulk", start=date(2026, 1, 1))
    changed = _claim(changed_doc, "bulk-change", scopes=("policy.bulk",))
    incumbent_documents = tuple(
        _document(
            f"bulk-incumbent-{index}",
            family=f"support.bulk-{index}",
            start=date(2025, 1, 1),
        )
        for index in range(MAX_CLASSIFIER_WORKLOAD_PAIRS_V1 + 1)
    )
    incumbents = tuple(
        _claim(
            document,
            f"bulk-{index}",
            scopes=("policy.bulk",),
        )
        for index, document in enumerate(incumbent_documents)
    )
    snapshot = _snapshot((changed_doc, *incumbent_documents), (changed, *incumbents))
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    assert len(candidates.candidates) == MAX_CLASSIFIER_WORKLOAD_PAIRS_V1 + 1

    with pytest.raises(ClassificationLimitError) as error:
        select_classification_workload(snapshot, candidates=candidates)

    assert error.value.observed == MAX_CLASSIFIER_WORKLOAD_PAIRS_V1 + 1


def test_shipped_scenario_has_fixed_exhaustive_and_bounded_v1_counts(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    store = SqliteChangeControlStore(tmp_path / "classification-scenario.sqlite3")
    store.init_schema()
    try:
        bootstrap = bootstrap_analysis_aggregate(
            repo_root=repo_root,
            prechange_manifest_path=(
                repo_root / "datasets/larkstead/change_control/sl2_prechange.yaml"
            ),
            incoming_manifest_path=repo_root / MANIFEST_RELATIVE_PATH,
            store=store,
            prechange_operation_id="classification-test:prechange",
            analysis_operation_id="classification-test:analysis",
        )
        candidates = generate_relationship_candidates(
            bootstrap.snapshot,
            changed_claim_revision_ids=bootstrap.binding.changed_claim_revision_ids,
            as_of=bootstrap.binding.analysis_as_of,
        )
        workload = select_classification_workload(
            bootstrap.snapshot,
            candidates=candidates,
        )
    finally:
        store.close()

    assert len(candidates.candidates) == 690
    assert len(workload.selected) == 176
    assert len(workload.excluded) == 514
    assert len(workload.inference_shards) == 10
    assert (
        sum(
            item.reason == CandidateExclusionReason.LEXICAL_POLICY_SLOT_QUOTA
            for item in workload.excluded
        )
        == 0
    )
    assert (
        sum(
            item.reason == CandidateExclusionReason.COVERAGE_SAMPLE_QUOTA
            for item in workload.excluded
        )
        == 514
    )
    assert len(canonical_json_bytes(workload.model_dump(mode="json"))) == 953_791
    assert len(canonical_json_bytes(workload.model_dump(mode="json"))) < (
        MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1
    )
    assert (
        max(
            len(canonical_json_bytes(shard.model_dump(mode="json")))
            for shard in workload.inference_shards
        )
        < MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1
    )

    claims_by_source_id = {
        claim.source.source_claim_id: claim
        for claim in bootstrap.snapshot.aggregate.claims.revisions
    }
    candidates_by_endpoints = {
        (
            item.changed_claim_revision_id,
            item.incumbent_claim_revision_id,
        ): item
        for item in candidates.candidates
    }
    selection_by_pair = {item.pair_id: item for item in workload.selected}

    def selection_for(
        changed_source_id: str,
        incumbent_source_id: str,
    ) -> SelectedCandidateRef | None:
        changed = claims_by_source_id[changed_source_id]
        incumbent = claims_by_source_id[incumbent_source_id]
        candidate = candidates_by_endpoints[
            (changed.claim_revision_id, incumbent.claim_revision_id)
        ]
        return selection_by_pair.get(candidate.pair_id)

    for incumbent_source_id in (
        "memo-sl2-memo-holiday-exception-01",
        "faq-sl2-faq-returns-01",
        "sop-sl2-macros-returns-helprise-07",
    ):
        selection = selection_for(
            "policy-sl2-policy-returns-v2-01",
            incumbent_source_id,
        )
        assert selection is not None
        assert selection.reasons == (CandidateSelectionReason.LEXICAL_POLICY_SLOT,)

    unrelated_same_unit = selection_for(
        "policy-sl2-policy-returns-v2-01",
        "process-process-showroom-demo-unit-rotation-05",
    )
    assert unrelated_same_unit is None or unrelated_same_unit.reasons == (
        CandidateSelectionReason.DETERMINISTIC_COVERAGE_SAMPLE,
    )

    classifications = _complete_classifications(
        workload,
        candidates,
        bootstrap.snapshot,
    )
    results = ClassificationResultSet.create(
        workload=workload,
        classifications=classifications,
    )
    assert results.result_sha256 == results.result_index.result_sha256
    assert len(results.result_index.canonical_bytes()) < (
        MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1
    )
    assert len(results.output_shards) == len(workload.inference_shards) == 10
    assert all(
        len(shard.canonical_bytes()) <= MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1
        for shard in results.output_shards
    )


def test_same_family_newer_to_older_supersedes_is_graph_valid() -> None:
    snapshot, candidates, changed, older, *_ = _fixture()
    candidate = _candidate_for(candidates, older)

    classification = _classification(
        candidate,
        snapshot,
        PairDisposition.SUPERSEDES,
        newer_revision_id=changed.claim_revision_id,
    )

    assert classification.materialization_status == GraphMaterializationStatus.GRAPH_VALID
    assert classification.relation_assessment is not None
    assert classification.relation_assessment.relation_type == PersistedRelationType.SUPERSEDES
    assert classification.relation_assessment.endpoint_ids == (
        changed.claim_revision_id,
        older.claim_revision_id,
    )


def test_supersedes_rejects_reversed_and_equal_date_direction() -> None:
    snapshot, candidates, _changed, older, *_ = _fixture()
    candidate = _candidate_for(candidates, older)

    with pytest.raises(ValueError, match="strictly newer"):
        _classification(
            candidate,
            snapshot,
            PairDisposition.SUPERSEDES,
            newer_revision_id=older.claim_revision_id,
        )

    changed_doc = _document(
        "equal-date-v2",
        family="policy.equal-date",
        start=date(2026, 1, 1),
    )
    incumbent_doc = _document(
        "equal-date-v1",
        family="policy.equal-date",
        start=date(2026, 1, 1),
    )
    changed = _claim(changed_doc, "equal-new", scopes=("policy.equal-date",))
    incumbent = _claim(incumbent_doc, "equal-old", scopes=("policy.equal-date",))
    equal_snapshot = _snapshot((changed_doc, incumbent_doc), (changed, incumbent))
    equal_candidates = generate_relationship_candidates(
        equal_snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    with pytest.raises(ValueError, match="strictly newer"):
        _classification(
            equal_candidates.candidates[0],
            equal_snapshot,
            PairDisposition.SUPERSEDES,
            newer_revision_id=changed.claim_revision_id,
        )


def test_cross_family_supersedes_is_rejected_as_an_invalid_disposition() -> None:
    snapshot, candidates, changed, _older, cross_family, _unrelated = _fixture()

    with pytest.raises(ValueError, match="cross-family SUPERSEDES"):
        _classification(
            _candidate_for(candidates, cross_family),
            snapshot,
            PairDisposition.SUPERSEDES,
            newer_revision_id=changed.claim_revision_id,
        )


def test_cross_family_coexists_without_scope_is_advisory_only() -> None:
    snapshot, candidates, _changed, _older, _cross_family, unrelated = _fixture()
    classification = _classification(
        _candidate_for(candidates, unrelated),
        snapshot,
        PairDisposition.COEXISTS,
    )

    assert classification.materialization_status == GraphMaterializationStatus.ADVISORY_ONLY
    assert classification.relation_assessment is None


def test_cross_family_contradiction_remains_advisory_and_is_not_materialized() -> None:
    snapshot, candidates, _changed, _older, cross_family, _unrelated = _fixture()
    candidate = _candidate_for(candidates, cross_family)
    classification = _classification(candidate, snapshot, PairDisposition.CONTRADICTS)
    workload = select_classification_workload(snapshot, candidates=candidates)
    overrides = {
        candidate.pair_id: PairDisposition.CONTRADICTS,
        _candidate_for(candidates, _older).pair_id: PairDisposition.COEXISTS,
    }
    results = ClassificationResultSet.create(
        workload=workload,
        classifications=_complete_classifications(
            workload,
            candidates,
            snapshot,
            overrides=overrides,
        ),
    )

    assert classification.materialization_status == GraphMaterializationStatus.ADVISORY_ONLY
    assert classification.relation_assessment is None
    materialized = materialize_relation_assessments(
        snapshot,
        candidates=candidates,
        results=results,
    )
    assert all(item.pair.pair_id != candidate.pair_id for item in materialized)
    assert tuple(item.pair.pair_id for item in materialized) == tuple(
        sorted(item.pair.pair_id for item in materialized)
    )


@pytest.mark.parametrize(
    "disposition",
    [PairDisposition.COEXISTS, PairDisposition.UNRELATED],
)
def test_coexists_and_unrelated_are_complete_no_edge_results(
    disposition: PairDisposition,
) -> None:
    snapshot, candidates, _changed, older, *_ = _fixture()
    classification = _classification(
        _candidate_for(candidates, older),
        snapshot,
        disposition,
    )

    assert classification.materialization_status == GraphMaterializationStatus.NO_EDGE
    assert classification.relation_assessment is not None
    assert classification.relation_assessment.relation_type is None


def test_result_set_requires_complete_selected_classification_coverage() -> None:
    snapshot, candidates, _changed, older, *_ = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    only_one = _classification(
        _candidate_for(candidates, older),
        snapshot,
        PairDisposition.UNRELATED,
    )

    with pytest.raises(ValueError, match="coverage"):
        ClassificationResultSet.create(workload=workload, classifications=(only_one,))


def test_result_validation_rejects_tampered_endpoint_evidence_and_candidate_binding() -> None:
    snapshot, candidates, _changed, older, cross_family, _unrelated = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    classifications = _complete_classifications(
        workload,
        candidates,
        snapshot,
    )
    results = ClassificationResultSet.create(
        workload=workload,
        classifications=classifications,
    )
    assert (
        validate_classification_results(
            snapshot,
            candidates=candidates,
            results=results,
        )
        == results
    )

    endpoint = classifications[0].endpoints[0]
    tampered_endpoint = endpoint.model_copy(update={"source_evidence_sha256": "0" * 64})
    tampered_classification = classifications[0].model_copy(
        update={"endpoints": (tampered_endpoint, classifications[0].endpoints[1])}
    )
    first_shard = results.output_shards[0]
    first_item = first_shard.items[0]
    tampered_item = first_item.model_copy(update={"classification": tampered_classification})
    tampered_shard = first_shard.model_copy(
        update={"items": (tampered_item, *first_shard.items[1:])}
    )
    tampered_results = results.model_copy(
        update={"output_shards": (tampered_shard, *results.output_shards[1:])}
    )
    with pytest.raises(ValidationError):
        validate_classification_results(
            snapshot,
            candidates=candidates,
            results=tampered_results,
        )

    wrong_candidate = classifications[0].model_copy(
        update={"candidate": classifications[1].candidate}
    )
    wrong_item = first_item.model_copy(update={"classification": wrong_candidate})
    wrong_shard = first_shard.model_copy(update={"items": (wrong_item, *first_shard.items[1:])})
    wrong_results = results.model_copy(
        update={"output_shards": (wrong_shard, *results.output_shards[1:])}
    )
    with pytest.raises(ValidationError):
        validate_classification_results(
            snapshot,
            candidates=candidates,
            results=wrong_results,
        )


def test_result_index_factory_rejects_incomplete_duplicate_and_substituted_shards() -> None:
    _snapshot_value, _candidates, workload, results = _result_fixture(two_changed_roots=True)
    assert len(results.output_shards) == 2

    with pytest.raises(ValueError, match="exactly cover"):
        ClassificationResultIndex.create(
            workload=workload,
            output_shards=results.output_shards[:-1],
        )
    with pytest.raises(ValueError, match="exactly cover"):
        ClassificationResultIndex.create(
            workload=workload,
            output_shards=(results.output_shards[0], results.output_shards[0]),
        )
    substituted = results.output_shards[0].model_copy(update={"workload_sha256": "0" * 64})
    with pytest.raises(ValueError):
        ClassificationResultIndex.create(
            workload=workload,
            output_shards=(substituted, results.output_shards[1]),
        )

    foreign_changed_doc = _document(
        "foreign-v2",
        family="policy.foreign",
        start=date(2026, 1, 1),
    )
    foreign_incumbent_doc = _document(
        "foreign-v1",
        family="policy.foreign",
        start=date(2025, 1, 1),
    )
    foreign_changed = _claim(
        foreign_changed_doc,
        "foreign-change",
        scopes=("policy.foreign",),
    )
    foreign_incumbent = _claim(
        foreign_incumbent_doc,
        "foreign-prior",
        scopes=("policy.foreign",),
    )
    foreign_snapshot = _snapshot(
        (foreign_changed_doc, foreign_incumbent_doc),
        (foreign_changed, foreign_incumbent),
    )
    foreign_candidates = generate_relationship_candidates(
        foreign_snapshot,
        changed_claim_revision_ids=(foreign_changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    foreign_workload = select_classification_workload(
        foreign_snapshot,
        candidates=foreign_candidates,
    )
    foreign_results = ClassificationResultSet.create(
        workload=foreign_workload,
        classifications=_complete_classifications(
            foreign_workload,
            foreign_candidates,
            foreign_snapshot,
        ),
    )
    with pytest.raises(ValueError, match="exactly cover"):
        ClassificationResultIndex.create(
            workload=workload,
            output_shards=(foreign_results.output_shards[0], results.output_shards[1]),
        )


def test_result_envelope_rejects_shard_reorder_ref_tamper_and_pair_substitution() -> None:
    snapshot, candidates, workload, results = _result_fixture(two_changed_roots=True)

    reordered = results.model_dump(mode="json")
    reordered["output_shards"] = list(reversed(reordered["output_shards"]))
    with pytest.raises(ValidationError, match="changed-root order"):
        ClassificationResultSet.model_validate(reordered)

    item_reordered = results.model_dump(mode="json")
    item_reordered["output_shards"][0]["items"] = list(
        reversed(item_reordered["output_shards"][0]["items"])
    )
    with pytest.raises(ValidationError, match="canonically ordered"):
        ClassificationResultSet.model_validate(item_reordered)

    tampered_index = results.result_index.model_dump(mode="json")
    tampered_index["output_shards"][0]["output_shard_sha256"] = "0" * 64
    tampered = results.model_dump(mode="json")
    tampered["result_index"] = tampered_index
    with pytest.raises(ValidationError):
        ClassificationResultSet.model_validate(tampered)

    classifications = list(results.classifications)
    with pytest.raises(ValueError, match="coverage"):
        ClassificationResultSet.create(
            workload=workload,
            classifications=tuple(classifications[:-1]),
        )
    with pytest.raises(ValueError, match="coverage"):
        ClassificationResultSet.create(
            workload=workload,
            classifications=tuple((*classifications[:-1], classifications[0])),
        )

    selected_ids = {item.pair_id for item in workload.selected}
    unknown_candidate = next(
        item for item in candidates.candidates if item.pair_id not in selected_ids
    )
    unknown = _classification(
        unknown_candidate,
        snapshot,
        PairDisposition.UNRELATED,
    )
    with pytest.raises(ValueError, match="coverage"):
        ClassificationResultSet.create(
            workload=workload,
            classifications=tuple((*classifications[:-1], unknown)),
        )


def test_rationale_limit_counts_utf8_bytes_not_python_characters() -> None:
    snapshot, candidates, _changed, older, *_ = _fixture()
    candidate = _candidate_for(candidates, older)
    by_id = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    endpoints = tuple(by_id[item] for item in candidate.claim_revision_ids)
    typed_endpoints = (endpoints[0], endpoints[1])

    accepted = ClaimPairClassification.create(
        candidate=candidate,
        endpoint_revisions=typed_endpoints,
        disposition=PairDisposition.UNRELATED,
        rationale="é" * (MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1 // 2),
        confidence=0.9,
    )
    assert len(accepted.rationale.encode("utf-8")) == (MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1)

    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        ClaimPairClassification.create(
            candidate=candidate,
            endpoint_revisions=typed_endpoints,
            disposition=PairDisposition.UNRELATED,
            rationale="é" * (MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1 // 2 + 1),
            confidence=0.9,
        )


def test_oversized_output_shard_fails_closed_without_a_monolithic_result() -> None:
    changed_doc = _document(
        "output-limit-v2",
        family="policy.output-limit",
        start=date(2026, 1, 1),
    )
    changed = _claim(
        changed_doc,
        "output-limit-change",
        scopes=("policy.output-limit",),
    )
    incumbent_documents = tuple(
        _document(
            f"output-limit-v1-{index}",
            family=f"support.output-limit-{index}",
            start=date(2025, 1, 1),
        )
        for index in range(32)
    )
    incumbents = tuple(
        _claim(
            document,
            f"output-limit-{index}",
            scopes=("policy.output-limit",),
        )
        for index, document in enumerate(incumbent_documents)
    )
    snapshot = _snapshot(
        (changed_doc, *incumbent_documents),
        (changed, *incumbents),
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    workload = select_classification_workload(snapshot, candidates=candidates)
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    revision_by_id = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    classifications = tuple(
        ClaimPairClassification.create(
            candidate=candidate_by_id[item.pair_id],
            endpoint_revisions=tuple(
                revision_by_id[revision_id]
                for revision_id in candidate_by_id[item.pair_id].claim_revision_ids
            ),  # type: ignore[arg-type]
            disposition=PairDisposition.UNRELATED,
            rationale="x" * MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1,
            confidence=0.9,
        )
        for item in workload.selected
    )

    with pytest.raises(ClassificationLimitError) as error:
        ClassificationResultSet.create(
            workload=workload,
            classifications=classifications,
        )
    assert error.value.category == "output-shard-bytes"


def test_classification_runtime_has_no_evaluator_or_scenario_specific_dependency() -> None:
    source = PACKAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PACKAGE_PATH))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not any(
        name == "mastervault.evals" or name.startswith("mastervault.evals.") for name in imported
    )
    assert "larkstead" not in source.casefold()
    assert "sl2" not in source.casefold()


def test_package_root_reexports_every_public_classification_contract() -> None:
    assert set(classification_module.__all__) <= set(change_control_package.__all__)
    for name in classification_module.__all__:
        assert getattr(change_control_package, name) is getattr(classification_module, name)


def test_contracts_are_strict_and_frozen() -> None:
    snapshot, candidates, *_ = _fixture()
    workload = select_classification_workload(snapshot, candidates=candidates)
    payload = workload.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        ClassificationWorkload.model_validate(payload)
    with pytest.raises(ValidationError):
        ClassificationWorkload.model_validate(
            {**workload.model_dump(mode="json"), "selected": [*workload.selected] * 129}
        )
    with pytest.raises(ValidationError):
        workload.snapshot_revision = 9
