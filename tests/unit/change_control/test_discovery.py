from __future__ import annotations

import ast
import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import mastervault.change_control.discovery as discovery_module
import mastervault.change_control.models as change_control_models
from mastervault.change_control.discovery import (
    MAX_CHANGED_ROOTS_V1,
    MAX_RELATIONSHIP_CANDIDATES_V1,
    MAX_SPAN_CANONICAL_BYTES_V1,
    MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1,
    AnalysisMode,
    AttentionAnchorKind,
    AttentionExclusionReason,
    AttentionPath,
    DependencyPathStep,
    DiscoveryLimitError,
    DocumentAttentionCandidate,
    DocumentAttentionRanking,
    RelationPathStep,
    RelationshipCandidate,
    RelationshipCandidateSet,
    RelationTraversalDirection,
    TemporalOverlap,
    generate_relationship_candidates,
    rank_document_attention,
    validate_document_attention_ranking,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyAssessment,
    DependencyKind,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentRole,
    DocumentSpanReference,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    PersistedRelationType,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalState,
    TemporalTargetKind,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.store import ChangeControlSnapshot

SHA_A = "a" * 64
SHA_B = "b" * 64


def _document(
    document_id: str,
    *,
    family: str | None = None,
    start: date = date(2025, 1, 1),
    end: date | None = None,
    role: DocumentRole = DocumentRole.POLICY,
) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=document_id,
        document_family=family or f"family.{document_id}",
        version_label=document_id,
        source_path=f"datasets/runtime/{document_id}.md",
        source_sha256=SHA_A,
        declared_effective_from=start,
        declared_effective_to=end,
        role=role,
        authority=DocumentAuthority.PRIMARY,
    )


def _claim(
    document: DocumentVersionMetadata,
    local_id: str,
    *,
    statement: str | None = None,
    scopes: tuple[str, ...] = ("shared-scope",),
    start: date | None = None,
    end: date | None = None,
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=f"runtime/sources/{document.document_id}.md",
            source_note_sha256=SHA_B,
            source_claim_id=local_id,
        ),
        statement=statement or f"Synthetic statement for {local_id} remains valid.",
        declared_effective_from=start or document.declared_effective_from,
        declared_effective_to=end if end is not None else document.declared_effective_to,
        scopes=scopes,
    )


def _span(
    document: DocumentVersionMetadata,
    quote: str = "Exact downstream evidence.",
    *,
    source_note_path: str | None = None,
):
    return DocumentSpanReference(
        document_version_id=document.document_version_id,
        source_note_path=source_note_path or f"runtime/sources/{document.document_id}.md",
        source_note_sha256=SHA_B,
        quote=quote,
        start_char=10,
        end_char=10 + len(quote),
    )


def _dependency(
    *,
    downstream: DocumentVersionMetadata,
    upstream: VersionedClaimRevision,
    kind: DependencyKind = DependencyKind.IMPLEMENTS,
    exposed: tuple[VersionedClaimRevision, ...] = (),
    quote: str = "Exact downstream evidence.",
    source_note_path: str | None = None,
) -> DependencyAssessment:
    return DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        dependency_kind=kind,
        downstream_spans=(_span(downstream, quote, source_note_path=source_note_path),),
        downstream_claim_revisions=exposed,
        rationale="Canonical dependency fact for a synthetic runtime fixture.",
        confidence=0.9,
    )


def _relation(
    first: VersionedClaimRevision,
    second: VersionedClaimRevision,
    disposition: PairDisposition,
) -> RelationAssessment:
    pair = ComparableClaimPair.create(first, second)
    newer_id = None
    if disposition == PairDisposition.SUPERSEDES:
        newer_id = max(
            pair.claim_revisions,
            key=lambda revision: revision.declared_effective_from,
        ).claim_revision_id
    return RelationAssessment.create(
        pair=pair,
        disposition=disposition,
        rationale="Canonical relationship fact for a synthetic runtime fixture.",
        confidence=0.9,
        newer_revision_id=newer_id,
    )


def _aggregate(
    *,
    documents: tuple[DocumentVersionMetadata, ...],
    claims: tuple[VersionedClaimRevision, ...],
    relations: tuple[RelationAssessment, ...] = (),
    dependencies: tuple[DependencyAssessment, ...] = (),
    replacements: tuple[DocumentReplacementAssessment, ...] = (),
    constraints: tuple[TemporalConstraint, ...] = (),
) -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id="synthetic-workspace",
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(relations),
        dependencies=DependencyRegistry.create(dependencies),
        document_replacements=DocumentReplacementSet.create(replacements),
        temporal_constraints=TemporalConstraintSet.create(constraints),
    )


def _snapshot(aggregate: ChangeControlAggregate, revision: int = 1) -> ChangeControlSnapshot:
    return ChangeControlSnapshot(
        aggregate=aggregate,
        revision=revision,
        aggregate_sha256=aggregate_sha256(aggregate),
    )


def _simple_snapshot() -> tuple[
    ChangeControlSnapshot, VersionedClaimRevision, VersionedClaimRevision
]:
    changed_doc = _document("changed-v2", family="policy.returns", start=date(2026, 1, 12))
    incumbent_doc = _document("incumbent-v1", family="policy.returns", start=date(2024, 1, 1))
    changed = _claim(changed_doc, "changed-01")
    incumbent = _claim(incumbent_doc, "incumbent-01")
    aggregate = _aggregate(
        documents=(changed_doc, incumbent_doc),
        claims=(changed, incumbent),
    )
    return _snapshot(aggregate), changed, incumbent


def _attention_fixture(*, with_relation: bool = False):
    changed_doc = _document("fixture-changed", family="fixture.policy")
    incumbent_doc = _document("fixture-incumbent", family="fixture.policy")
    target_doc = _document("fixture-target", role=DocumentRole.SOP)
    changed = _claim(changed_doc, "fixture-changed-01")
    incumbent = _claim(incumbent_doc, "fixture-incumbent-01")
    relation = _relation(changed, incumbent, PairDisposition.CONTRADICTS) if with_relation else None
    dependency = _dependency(downstream=target_doc, upstream=incumbent)
    aggregate = _aggregate(
        documents=(changed_doc, incumbent_doc, target_doc),
        claims=(changed, incumbent),
        relations=(relation,) if relation is not None else (),
        dependencies=(dependency,),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    return snapshot, candidates, ranking


def _multi_target_attention_fixture(count: int = 3):
    changed_doc = _document("prefix-changed", family="prefix.policy")
    incumbent_doc = _document("prefix-incumbent", family="prefix.policy")
    changed = _claim(changed_doc, "prefix-changed-01")
    incumbent = _claim(incumbent_doc, "prefix-incumbent-01")
    targets = tuple(
        _document(f"prefix-target-{index}", role=DocumentRole.SOP) for index in range(count)
    )
    snapshot = _snapshot(
        _aggregate(
            documents=(changed_doc, incumbent_doc, *targets),
            claims=(changed, incumbent),
            dependencies=tuple(
                _dependency(downstream=target, upstream=incumbent) for target in targets
            ),
        )
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    assert len(ranking.attention_candidates) == count
    return snapshot, candidates, ranking


def _multi_candidate_fixture(count: int = 3) -> RelationshipCandidateSet:
    changed_doc = _document("candidate-prefix-changed", family="candidate-prefix.policy")
    changed = _claim(changed_doc, "candidate-prefix-changed-01")
    incumbent_documents = tuple(
        _document(
            f"candidate-prefix-incumbent-{index}",
            family="candidate-prefix.policy",
        )
        for index in range(count)
    )
    incumbents = tuple(
        _claim(document, f"candidate-prefix-incumbent-{index}-01")
        for index, document in enumerate(incumbent_documents)
    )
    result = generate_relationship_candidates(
        _snapshot(
            _aggregate(
                documents=(changed_doc, *incumbent_documents),
                claims=(changed, *incumbents),
            )
        ),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    assert len(result.candidates) == count
    return result


def test_relationship_candidates_are_exhaustive_unassessed_and_canonical():
    changed_doc = _document("changed", family="policy.shared")
    incumbent_a_doc = _document("incumbent-a", family="policy.shared")
    incumbent_b_doc = _document("incumbent-b", family="policy.other")
    changed = _claim(changed_doc, "changed-01", scopes=("alpha", "shared-scope"))
    incumbent_a = _claim(incumbent_a_doc, "incumbent-a-01", scopes=("shared-scope",))
    incumbent_b = _claim(incumbent_b_doc, "incumbent-b-01", scopes=("beta",))
    assessed = _relation(changed, incumbent_b, PairDisposition.UNRELATED)
    aggregate = _aggregate(
        documents=(incumbent_b_doc, changed_doc, incumbent_a_doc),
        claims=(incumbent_b, changed, incumbent_a),
        relations=(assessed,),
    )
    snapshot = _snapshot(aggregate, revision=7)

    result = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.incumbent_claim_revision_id == incumbent_a.claim_revision_id
    assert candidate.status.value == "unassessed"
    assert not hasattr(candidate, "disposition")
    assert not hasattr(candidate, "confidence")
    assert candidate.score.same_document_family == 1
    assert candidate.score.shared_scope_count == 1
    assert result.binding.snapshot_revision == 7
    assert result.binding.mode == AnalysisMode.LIVE

    replay = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    assert replay == result
    assert canonical_json_bytes(replay.model_dump(mode="json")) == canonical_json_bytes(
        result.model_dump(mode="json")
    )


def test_candidate_output_is_invariant_to_constructor_input_permutations_and_ties():
    changed_doc = _document("changed")
    first_doc = _document("first")
    second_doc = _document("second")
    changed = _claim(changed_doc, "changed-01")
    first = _claim(first_doc, "first-01")
    second = _claim(second_doc, "second-01")
    aggregate_a = _aggregate(
        documents=(changed_doc, first_doc, second_doc),
        claims=(changed, first, second),
    )
    aggregate_b = _aggregate(
        documents=(second_doc, first_doc, changed_doc),
        claims=(second, first, changed),
    )
    assert aggregate_a == aggregate_b
    first_result = generate_relationship_candidates(
        _snapshot(aggregate_a),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    second_result = generate_relationship_candidates(
        _snapshot(aggregate_b),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    assert first_result == second_result
    assert [candidate.pair_id for candidate in first_result.candidates] == sorted(
        candidate.pair_id for candidate in first_result.candidates
    )


def test_candidate_score_detects_two_revisions_of_the_same_claim_identity():
    document = _document("one-document")
    incumbent = _claim(
        document,
        "stable-local-01",
        statement="The stable claim has its earlier semantic wording.",
    )
    changed = _claim(
        document,
        "stable-local-01",
        statement="The stable claim has materially revised semantic wording.",
    )
    aggregate = _aggregate(
        documents=(document,),
        claims=(incumbent, changed),
    )
    result = generate_relationship_candidates(
        _snapshot(aggregate),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].score.same_claim_identity == 1
    assert result.candidates[0].score.same_document_family == 1


@pytest.mark.parametrize(
    "changed_ids,revision,sha",
    [
        ((), 1, None),
        (("duplicate", "duplicate"), 1, None),
        (("missing",), 1, None),
        (("valid",), 0, None),
        (("valid",), 1, "0" * 64),
    ],
)
def test_changed_root_and_snapshot_validation(changed_ids, revision, sha):
    snapshot, changed, _ = _simple_snapshot()
    aliases = {
        "valid": changed.claim_revision_id,
        "duplicate": changed.claim_revision_id,
        "missing": "claimrev:" + "0" * 64,
    }
    resolved_ids = tuple(aliases[value] for value in changed_ids)
    bad_snapshot = ChangeControlSnapshot(
        aggregate=snapshot.aggregate,
        revision=revision,
        aggregate_sha256=sha or snapshot.aggregate_sha256,
    )
    with pytest.raises(ValueError):
        generate_relationship_candidates(
            bad_snapshot,
            changed_claim_revision_ids=resolved_ids,
            as_of=date(2026, 2, 1),
        )


def test_changed_ids_must_be_sorted_and_changed_roots_cannot_be_closed_or_unresolved():
    current_doc = _document("current-a")
    current_b_doc = _document("current-b")
    current_a = _claim(current_doc, "current-a-01")
    current_b = _claim(current_b_doc, "current-b-01")
    aggregate = _aggregate(
        documents=(current_doc, current_b_doc),
        claims=(current_a, current_b),
    )
    unsorted_ids = tuple(
        sorted((current_a.claim_revision_id, current_b.claim_revision_id), reverse=True)
    )
    with pytest.raises(ValueError, match="sorted and unique"):
        generate_relationship_candidates(
            _snapshot(aggregate),
            changed_claim_revision_ids=unsorted_ids,
            as_of=date(2025, 2, 1),
        )

    expired_doc = _document("expired", end=date(2025, 2, 1))
    expired = _claim(expired_doc, "expired-01")
    expired_aggregate = _aggregate(documents=(expired_doc,), claims=(expired,))
    with pytest.raises(ValueError, match="current or future"):
        generate_relationship_candidates(
            _snapshot(expired_aggregate),
            changed_claim_revision_ids=(expired.claim_revision_id,),
            as_of=date(2025, 2, 1),
        )

    old_doc = _document("old", family="policy.conflict", end=date(2025, 4, 1))
    new_doc = _document("new", family="policy.conflict", start=date(2025, 3, 1))
    old = _claim(old_doc, "old-01", end=date(2025, 4, 1))
    new = _claim(new_doc, "new-01")
    supersedes = _relation(old, new, PairDisposition.SUPERSEDES)
    conflict = TemporalConstraint.from_supersession(
        supersedes,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted inference conflicts with the declared bound.",
    )
    unresolved = _aggregate(
        documents=(old_doc, new_doc),
        claims=(old, new),
        relations=(supersedes,),
        constraints=(conflict,),
    )
    with pytest.raises(ValueError, match="current or future"):
        generate_relationship_candidates(
            _snapshot(unresolved),
            changed_claim_revision_ids=(old.claim_revision_id,),
            as_of=date(2025, 2, 1),
        )

    historical_old_doc = _document("historical-old", family="policy.historical")
    historical_new_doc = _document(
        "historical-new", family="policy.historical", start=date(2025, 3, 1)
    )
    historical_old = _claim(historical_old_doc, "historical-old-01")
    historical_new = _claim(historical_new_doc, "historical-new-01")
    historical_relation = _relation(
        historical_old,
        historical_new,
        PairDisposition.SUPERSEDES,
    )
    historical_constraint = TemporalConstraint.from_supersession(
        historical_relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted inference makes the older changed root historical.",
    )
    historical_aggregate = _aggregate(
        documents=(historical_old_doc, historical_new_doc),
        claims=(historical_old, historical_new),
        relations=(historical_relation,),
        constraints=(historical_constraint,),
    )
    with pytest.raises(ValueError, match="current or future"):
        generate_relationship_candidates(
            _snapshot(historical_aggregate),
            changed_claim_revision_ids=(historical_old.claim_revision_id,),
            as_of=date(2025, 3, 1),
        )


def test_future_changed_root_is_an_explicit_preview():
    snapshot, changed, _ = _simple_snapshot()
    result = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 1, 11),
    )
    assert result.binding.mode == AnalysisMode.PREVIEW
    assert tuple(
        resolution.state for resolution in result.binding.changed_temporal_resolutions
    ) == (TemporalState.FUTURE,)


def test_half_open_overlap_proposed_constraints_and_unknown_are_distinct():
    changed_doc = _document("changed", start=date(2025, 3, 1))
    changed = _claim(changed_doc, "changed-01")
    closed_doc = _document("closed", start=date(2025, 1, 1), end=date(2025, 3, 1))
    closed = _claim(closed_doc, "closed-01")

    old_doc = _document("old", family="policy.unresolved", end=date(2025, 5, 1))
    newer_doc = _document("newer", family="policy.unresolved", start=date(2025, 4, 1))
    old = _claim(old_doc, "old-01", end=date(2025, 5, 1))
    newer = _claim(newer_doc, "newer-01")
    relation = _relation(old, newer, PairDisposition.SUPERSEDES)
    accepted_conflict = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted inference intentionally conflicts with declared time.",
    )

    proposed_doc = _document("proposed-old", family="policy.proposed")
    proposed_new_doc = _document("proposed-new", family="policy.proposed", start=date(2025, 2, 1))
    proposed_old = _claim(proposed_doc, "proposed-old-01")
    proposed_new = _claim(proposed_new_doc, "proposed-new-01")
    proposed_relation = _relation(proposed_old, proposed_new, PairDisposition.SUPERSEDES)
    proposed_constraint = TemporalConstraint.from_supersession(
        proposed_relation,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="This proposal must not alter temporal resolution.",
    )
    rejected_doc = _document("rejected-old", family="policy.rejected")
    rejected_new_doc = _document("rejected-new", family="policy.rejected", start=date(2025, 2, 1))
    rejected_old = _claim(rejected_doc, "rejected-old-01")
    rejected_new = _claim(rejected_new_doc, "rejected-new-01")
    rejected_relation = _relation(rejected_old, rejected_new, PairDisposition.SUPERSEDES)
    rejected_constraint = TemporalConstraint.from_supersession(
        rejected_relation,
        status=TemporalConstraintStatus.REJECTED,
        rationale="This rejected inference must not alter temporal resolution.",
    )

    aggregate = _aggregate(
        documents=(
            changed_doc,
            closed_doc,
            old_doc,
            newer_doc,
            proposed_doc,
            proposed_new_doc,
            rejected_doc,
            rejected_new_doc,
        ),
        claims=(
            changed,
            closed,
            old,
            newer,
            proposed_old,
            proposed_new,
            rejected_old,
            rejected_new,
        ),
        relations=(relation, proposed_relation, rejected_relation),
        constraints=(accepted_conflict, proposed_constraint, rejected_constraint),
    )
    result = generate_relationship_candidates(
        _snapshot(aggregate),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 3, 1),
    )
    by_incumbent = {
        candidate.incumbent_claim_revision_id: candidate for candidate in result.candidates
    }
    assert by_incumbent[closed.claim_revision_id].score.temporal_overlap == TemporalOverlap.FALSE
    assert (
        by_incumbent[old.claim_revision_id].incumbent_temporal_resolution.state
        == TemporalState.UNRESOLVED
    )
    assert by_incumbent[old.claim_revision_id].score.temporal_overlap == TemporalOverlap.UNKNOWN
    unresolved_resolution = by_incumbent[old.claim_revision_id].incumbent_temporal_resolution
    assert unresolved_resolution.applied_constraint_ids == (accepted_conflict.constraint_id,)
    assert unresolved_resolution.basis_relation_ids == (relation.relation_id,)
    assert unresolved_resolution.conflicts
    assert (
        by_incumbent[proposed_old.claim_revision_id].incumbent_temporal_resolution.state
        == TemporalState.CURRENT
    )
    assert (
        by_incumbent[rejected_old.claim_revision_id].incumbent_temporal_resolution.state
        == TemporalState.CURRENT
    )
    order = [candidate.incumbent_claim_revision_id for candidate in result.candidates]
    assert order.index(proposed_old.claim_revision_id) < order.index(closed.claim_revision_id)
    assert order.index(closed.claim_revision_id) < order.index(old.claim_revision_id)


def test_ranking_regenerates_candidates_and_rejects_stale_or_self_consistent_tampering():
    snapshot, changed, _ = _simple_snapshot()
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    omitted = RelationshipCandidateSet.create(
        binding=candidates.binding,
        candidates=candidates.candidates[:-1],
    )
    with pytest.raises(ValueError, match="complete deterministic set"):
        rank_document_attention(snapshot, candidates=omitted)

    changed_snapshot = ChangeControlSnapshot(
        aggregate=snapshot.aggregate,
        revision=2,
        aggregate_sha256=snapshot.aggregate_sha256,
    )
    with pytest.raises(ValueError, match="stale"):
        rank_document_attention(changed_snapshot, candidates=candidates)

    tampered_digest = candidates.model_copy(update={"result_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="result SHA"):
        rank_document_attention(snapshot, candidates=tampered_digest)


def test_only_route_through_persisted_supersedes_reaches_current_downstream():
    old_doc = _document("policy-v1", family="policy.returns", start=date(2024, 1, 1))
    changed_doc = _document("policy-v2", family="policy.returns", start=date(2026, 1, 12))
    downstream_doc = _document("support-guide", role=DocumentRole.SOP, start=date(2025, 1, 1))
    heuristic_doc = _document("heuristic-anchor", start=date(2025, 1, 1))
    old = _claim(old_doc, "old-01")
    changed = _claim(changed_doc, "changed-01")
    downstream_claim = _claim(downstream_doc, "support-01")
    heuristic_claim = _claim(heuristic_doc, "heuristic-01")
    supersedes = _relation(old, changed, PairDisposition.SUPERSEDES)
    dependency = _dependency(
        downstream=downstream_doc,
        upstream=old,
        exposed=(downstream_claim,),
    )
    heuristic_dependency = _dependency(
        downstream=downstream_doc,
        upstream=heuristic_claim,
        kind=DependencyKind.QUOTES,
        exposed=(downstream_claim,),
    )
    aggregate = _aggregate(
        documents=(old_doc, changed_doc, downstream_doc, heuristic_doc),
        claims=(old, changed, downstream_claim, heuristic_claim),
        relations=(supersedes,),
        dependencies=(dependency, heuristic_dependency),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    assert supersedes.pair.pair_id not in {candidate.pair_id for candidate in candidates.candidates}

    ranking = rank_document_attention(snapshot, candidates=candidates)

    assert [item.document_id for item in ranking.attention_candidates] == ["support-guide"]
    path = next(
        path
        for path in ranking.attention_candidates[0].paths
        if path.anchor_kind == AttentionAnchorKind.CANONICAL_RELATION
    )
    assert len(path.relation_steps) == 1
    assert path.relation_steps[0].traversal_direction == RelationTraversalDirection.FORWARD
    assert path.dependency_steps[0].upstream_claim_revision_id == old.claim_revision_id
    assert ranking.attention_candidates[0].score.anchor_kind_rank == 0
    assert any(
        path.anchor_kind == AttentionAnchorKind.UNASSESSED_CANDIDATE
        for path in ranking.attention_candidates[0].paths
    )


def test_supersedes_discovery_can_traverse_reverse_without_reversing_edge_semantics():
    older_doc = _document("older", family="policy.reverse", start=date(2024, 1, 1))
    newer_doc = _document("newer", family="policy.reverse", start=date(2025, 1, 1))
    target_doc = _document("reverse-target", role=DocumentRole.FAQ)
    older = _claim(older_doc, "older-01")
    newer = _claim(newer_doc, "newer-01")
    relation = _relation(older, newer, PairDisposition.SUPERSEDES)
    dependency = _dependency(downstream=target_doc, upstream=newer)
    aggregate = _aggregate(
        documents=(older_doc, newer_doc, target_doc),
        claims=(older, newer),
        relations=(relation,),
        dependencies=(dependency,),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(older.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    path = next(
        path
        for item in ranking.attention_candidates
        if item.document_id == "reverse-target"
        for path in item.paths
        if path.anchor_kind == AttentionAnchorKind.CANONICAL_RELATION
    )
    assert path.relation_steps[0].canonical_endpoint_ids == (
        newer.claim_revision_id,
        older.claim_revision_id,
    )
    assert path.relation_steps[0].traversal_direction == RelationTraversalDirection.REVERSE


def test_reverse_dependency_bfs_is_transitive_body_safe_and_does_not_reenter_relations():
    changed_doc = _document("changed")
    incumbent_doc = _document("incumbent")
    middle_doc = _document("middle", role=DocumentRole.SOP)
    leaf_doc = _document("leaf", role=DocumentRole.PROCESS)
    relation_only_doc = _document("relation-only")
    unwanted_doc = _document("unwanted", role=DocumentRole.FAQ)
    body_only_doc = _document("body-only", role=DocumentRole.FAQ)
    changed = _claim(changed_doc, "changed-01")
    incumbent = _claim(incumbent_doc, "incumbent-01")
    middle = _claim(middle_doc, "middle-01", scopes=("middle-only",))
    relation_only = _claim(
        relation_only_doc,
        "relation-only-01",
        scopes=("middle-only",),
    )
    middle_dep = _dependency(downstream=middle_doc, upstream=incumbent, exposed=(middle,))
    leaf_dep = _dependency(downstream=leaf_doc, upstream=middle)
    body_dep = _dependency(downstream=body_only_doc, upstream=incumbent)
    relation = _relation(middle, relation_only, PairDisposition.CONTRADICTS)
    unwanted_dep = _dependency(downstream=unwanted_doc, upstream=relation_only)
    aggregate = _aggregate(
        documents=(
            changed_doc,
            incumbent_doc,
            middle_doc,
            leaf_doc,
            relation_only_doc,
            unwanted_doc,
            body_only_doc,
        ),
        claims=(changed, incumbent, middle, relation_only),
        relations=(relation,),
        dependencies=(middle_dep, leaf_dep, body_dep, unwanted_dep),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    by_id = {item.document_id: item for item in ranking.attention_candidates}

    assert "middle" in by_id
    assert "leaf" in by_id
    assert by_id["leaf"].score.dependency_depth == 2
    assert "body-only" in by_id
    body_step = by_id["body-only"].paths[0].dependency_steps[-1]
    assert body_step.exposed_downstream_claim_revision_ids == ()
    assert "unwanted" not in by_id
    assert "unwanted" not in {item.document_id for item in ranking.excluded_targets}


def test_historical_reference_only_is_excluded_but_eligible_path_rescues_document():
    changed_doc = _document("changed")
    incumbent_doc = _document("incumbent")
    target_doc = _document("target", role=DocumentRole.FAQ)
    changed = _claim(changed_doc, "changed-01")
    incumbent = _claim(incumbent_doc, "incumbent-01")
    historical = _dependency(
        downstream=target_doc,
        upstream=incumbent,
        kind=DependencyKind.HISTORICAL_REFERENCE,
    )
    base = _aggregate(
        documents=(changed_doc, incumbent_doc, target_doc),
        claims=(changed, incumbent),
        dependencies=(historical,),
    )
    candidates = generate_relationship_candidates(
        _snapshot(base),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(_snapshot(base), candidates=candidates)
    assert not ranking.attention_candidates
    assert ranking.excluded_targets[0].reasons == (
        AttentionExclusionReason.HISTORICAL_REFERENCE_ONLY,
    )

    eligible = _dependency(
        downstream=target_doc,
        upstream=incumbent,
        kind=DependencyKind.QUOTES,
    )
    rescued = _aggregate(
        documents=(changed_doc, incumbent_doc, target_doc),
        claims=(changed, incumbent),
        dependencies=(historical, eligible),
    )
    rescued_candidates = generate_relationship_candidates(
        _snapshot(rescued),
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    rescued_ranking = rank_document_attention(_snapshot(rescued), candidates=rescued_candidates)
    assert [item.document_id for item in rescued_ranking.attention_candidates] == ["target"]
    assert len(rescued_ranking.attention_candidates[0].paths) == 2
    assert rescued_ranking.attention_candidates[0].score.supporting_dependency_count == 1


def test_temporal_and_changed_document_exclusions_are_typed():
    changed_doc = _document("changed")
    incumbent_doc = _document("incumbent")
    future_doc = _document("future", start=date(2026, 1, 1))
    expired_doc = _document("expired", end=date(2025, 1, 15))
    historical_doc = _document("historical", family="target.history")
    replacement_doc = _document("replacement", family="target.history", start=date(2025, 1, 20))
    unresolved_doc = _document("unresolved", family="target.unresolved", end=date(2025, 1, 25))
    unresolved_replacement_doc = _document(
        "unresolved-replacement",
        family="target.unresolved",
        start=date(2025, 1, 20),
    )
    changed = _claim(changed_doc, "changed-01")
    incumbent = _claim(incumbent_doc, "incumbent-01")
    history_replacement = DocumentReplacementAssessment.create(
        newer_document=replacement_doc,
        older_document=historical_doc,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Canonical document replacement for synthetic history.",
        confidence=0.9,
    )
    history_constraint = TemporalConstraint.from_document_replacement(
        history_replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted historical document bound.",
    )
    unresolved_replacement = DocumentReplacementAssessment.create(
        newer_document=unresolved_replacement_doc,
        older_document=unresolved_doc,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Canonical replacement with a conflicting source bound.",
        confidence=0.9,
    )
    unresolved_constraint = TemporalConstraint.from_document_replacement(
        unresolved_replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted inferred bound conflicts with source time.",
    )
    dependencies = tuple(
        _dependency(downstream=document, upstream=incumbent)
        for document in (
            changed_doc,
            future_doc,
            expired_doc,
            historical_doc,
            unresolved_doc,
        )
    )
    aggregate = _aggregate(
        documents=(
            changed_doc,
            incumbent_doc,
            future_doc,
            expired_doc,
            historical_doc,
            replacement_doc,
            unresolved_doc,
            unresolved_replacement_doc,
        ),
        claims=(changed, incumbent),
        dependencies=dependencies,
        replacements=(history_replacement, unresolved_replacement),
        constraints=(history_constraint, unresolved_constraint),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    reasons = {target.document_id: target.reasons for target in ranking.excluded_targets}
    assert reasons["changed"] == (AttentionExclusionReason.CHANGED_DOCUMENT,)
    assert reasons["future"] == (AttentionExclusionReason.FUTURE,)
    assert reasons["expired"] == (AttentionExclusionReason.EXPIRED,)
    assert reasons["historical"] == (AttentionExclusionReason.HISTORICAL,)
    assert reasons["unresolved"] == (AttentionExclusionReason.UNRESOLVED,)
    historical_target = next(
        target for target in ranking.excluded_targets if target.document_id == "historical"
    )
    assert historical_target.temporal_resolution.applied_constraint_ids == (
        history_constraint.constraint_id,
    )
    assert historical_target.temporal_resolution.basis_relation_ids == (
        history_replacement.relation_id,
    )
    assert all(
        path.target_temporal_resolution == historical_target.temporal_resolution
        for path in historical_target.paths
    )
    unresolved_target = next(
        target for target in ranking.excluded_targets if target.document_id == "unresolved"
    )
    assert unresolved_target.temporal_resolution.conflicts


def test_historical_upstream_still_reaches_current_stale_document():
    changed_doc = _document("changed", family="policy.history", start=date(2025, 2, 1))
    old_doc = _document("old", family="policy.history", start=date(2024, 1, 1))
    target_doc = _document("current-target", role=DocumentRole.SOP)
    changed = _claim(changed_doc, "changed-01")
    old = _claim(old_doc, "old-01")
    supersedes = _relation(old, changed, PairDisposition.SUPERSEDES)
    closes_old = TemporalConstraint.from_supersession(
        supersedes,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Accepted supersession closes only the historical upstream claim.",
    )
    dependency = _dependency(downstream=target_doc, upstream=old)
    aggregate = _aggregate(
        documents=(changed_doc, old_doc, target_doc),
        claims=(changed, old),
        relations=(supersedes,),
        dependencies=(dependency,),
        constraints=(closes_old,),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 3, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    assert [item.document_id for item in ranking.attention_candidates] == ["current-target"]


def test_multiple_paths_dedupe_document_retain_all_paths_and_cycles_terminate():
    changed_doc = _document("changed")
    first_doc = _document("first")
    second_doc = _document("second")
    target_doc = _document("target", role=DocumentRole.SOP)
    final_doc = _document("final", role=DocumentRole.PROCESS)
    changed = _claim(changed_doc, "changed-01")
    first = _claim(first_doc, "first-01")
    second = _claim(second_doc, "second-01")
    target_claim = _claim(target_doc, "target-01", scopes=("target-only",))
    first_to_target = _dependency(
        downstream=target_doc,
        upstream=first,
        kind=DependencyKind.QUOTES,
        exposed=(target_claim,),
    )
    second_to_target = _dependency(
        downstream=target_doc,
        upstream=second,
        kind=DependencyKind.SUMMARIZES,
        exposed=(target_claim,),
    )
    cycle = _dependency(downstream=first_doc, upstream=target_claim, exposed=(first,))
    final_dependency = _dependency(downstream=final_doc, upstream=target_claim)
    aggregate = _aggregate(
        documents=(changed_doc, first_doc, second_doc, target_doc, final_doc),
        claims=(changed, first, second, target_claim),
        dependencies=(first_to_target, second_to_target, cycle, final_dependency),
    )
    snapshot = _snapshot(aggregate)
    before = aggregate.model_dump(mode="json")
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    target = next(item for item in ranking.attention_candidates if item.document_id == "target")
    assert len(target.paths) >= 2
    assert len({path.path_id for path in target.paths}) == len(target.paths)
    assert target.score.supporting_dependency_count == 2
    final = next(item for item in ranking.attention_candidates if item.document_id == "final")
    assert len(final.paths) == 2
    assert all(
        path.dependency_steps[-1].dependency_id == final_dependency.dependency_id
        for path in final.paths
    )
    replay = rank_document_attention(snapshot, candidates=candidates)
    assert ranking == replay
    assert canonical_json_bytes(ranking.model_dump(mode="json")) == canonical_json_bytes(
        replay.model_dump(mode="json")
    )
    assert aggregate.model_dump(mode="json") == before


def test_result_models_reject_digest_tampering():
    snapshot, changed, _ = _simple_snapshot()
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)
    with pytest.raises(ValidationError, match="result SHA"):
        RelationshipCandidateSet.model_validate(
            {**candidates.model_dump(mode="json"), "result_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="result SHA"):
        type(ranking).model_validate({**ranking.model_dump(mode="json"), "result_sha256": "0" * 64})


def test_canonical_anchor_categorically_precedes_shorter_heuristic_target():
    changed_doc = _document("priority-changed")
    canonical_doc = _document("priority-canonical")
    middle_doc = _document(
        "priority-middle",
        start=date(2025, 3, 1),
        role=DocumentRole.SOP,
    )
    canonical_target_doc = _document("priority-canonical-target", role=DocumentRole.PROCESS)
    heuristic_doc = _document("priority-heuristic")
    heuristic_target_doc = _document("priority-heuristic-target", role=DocumentRole.FAQ)
    changed = _claim(changed_doc, "priority-changed-01")
    canonical = _claim(canonical_doc, "priority-canonical-01")
    middle = _claim(middle_doc, "priority-middle-01", scopes=("internal",))
    heuristic = _claim(heuristic_doc, "priority-heuristic-01")
    relation = _relation(changed, canonical, PairDisposition.CONTRADICTS)
    to_middle = _dependency(
        downstream=middle_doc,
        upstream=canonical,
        exposed=(middle,),
    )
    to_canonical_target = _dependency(
        downstream=canonical_target_doc,
        upstream=middle,
    )
    to_heuristic_target = _dependency(
        downstream=heuristic_target_doc,
        upstream=heuristic,
    )
    aggregate = _aggregate(
        documents=(
            changed_doc,
            canonical_doc,
            middle_doc,
            canonical_target_doc,
            heuristic_doc,
            heuristic_target_doc,
        ),
        claims=(changed, canonical, middle, heuristic),
        relations=(relation,),
        dependencies=(to_middle, to_canonical_target, to_heuristic_target),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    ranking = rank_document_attention(snapshot, candidates=candidates)

    assert [item.document_id for item in ranking.attention_candidates] == [
        "priority-canonical-target",
        "priority-heuristic-target",
    ]
    canonical_target = ranking.attention_candidates[0]
    heuristic_target = ranking.attention_candidates[1]
    assert canonical_target.score.anchor_kind_rank == 0
    assert canonical_target.score.dependency_depth == 2
    assert heuristic_target.score.anchor_kind_rank == 1
    assert heuristic_target.score.dependency_depth == 1
    assert all(
        path.anchor_kind == AttentionAnchorKind.CANONICAL_RELATION
        for path in canonical_target.paths
    )


def test_changed_root_contradicts_traversal_is_symmetric_and_direct():
    _, _, ranking = _attention_fixture(with_relation=True)
    path = ranking.attention_candidates[0].paths[0]
    assert path.anchor_kind == AttentionAnchorKind.CANONICAL_RELATION
    assert path.relation_steps[0].relation_type == PersistedRelationType.CONTRADICTS
    assert path.relation_steps[0].traversal_direction == RelationTraversalDirection.SYMMETRIC


def test_changed_root_and_candidate_limits_fail_closed():
    changed_documents = tuple(
        _document(f"limit-changed-{index}") for index in range(MAX_CHANGED_ROOTS_V1 + 1)
    )
    changed_claims = tuple(
        _claim(document, f"limit-changed-{index}-01")
        for index, document in enumerate(changed_documents)
    )
    changed_aggregate = _aggregate(
        documents=changed_documents,
        claims=changed_claims,
    )
    with pytest.raises(DiscoveryLimitError) as changed_error:
        generate_relationship_candidates(
            _snapshot(changed_aggregate),
            changed_claim_revision_ids=tuple(
                sorted(claim.claim_revision_id for claim in changed_claims)
            ),
            as_of=date(2025, 2, 1),
        )
    assert changed_error.value.category == "changed-roots"
    assert changed_error.value.observed == MAX_CHANGED_ROOTS_V1 + 1

    bounded_changed_documents = tuple(
        _document(f"candidate-changed-{index}") for index in range(MAX_CHANGED_ROOTS_V1)
    )
    bounded_changed_claims = tuple(
        _claim(document, f"candidate-changed-{index}-01")
        for index, document in enumerate(bounded_changed_documents)
    )
    incumbent_count = MAX_RELATIONSHIP_CANDIDATES_V1 // MAX_CHANGED_ROOTS_V1 + 1
    incumbent_documents = tuple(
        _document(f"candidate-incumbent-{index}") for index in range(incumbent_count)
    )
    incumbents = tuple(
        _claim(document, f"candidate-incumbent-{index}-01")
        for index, document in enumerate(incumbent_documents)
    )
    candidate_aggregate = _aggregate(
        documents=(*bounded_changed_documents, *incumbent_documents),
        claims=(*bounded_changed_claims, *incumbents),
    )
    with pytest.raises(DiscoveryLimitError) as candidate_error:
        generate_relationship_candidates(
            _snapshot(candidate_aggregate),
            changed_claim_revision_ids=tuple(
                sorted(claim.claim_revision_id for claim in bounded_changed_claims)
            ),
            as_of=date(2025, 2, 1),
        )
    assert candidate_error.value.category == "relationship-candidates"
    assert candidate_error.value.observed == MAX_RELATIONSHIP_CANDIDATES_V1 + 1


def test_dependency_depth_limit_fails_closed():
    changed_doc = _document("depth-changed")
    incumbent_doc = _document("depth-incumbent")
    changed = _claim(changed_doc, "depth-changed-01")
    incumbent = _claim(incumbent_doc, "depth-incumbent-01")
    documents = [changed_doc, incumbent_doc]
    claims = [changed, incumbent]
    dependencies = []
    upstream = incumbent
    for index in range(9):
        downstream = _document(f"depth-{index}", role=DocumentRole.PROCESS)
        exposed = _claim(downstream, f"depth-{index}-01", scopes=("internal",))
        documents.append(downstream)
        claims.append(exposed)
        dependencies.append(
            _dependency(downstream=downstream, upstream=upstream, exposed=(exposed,))
        )
        upstream = exposed
    aggregate = _aggregate(
        documents=tuple(documents),
        claims=tuple(claims),
        dependencies=tuple(dependencies),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    with pytest.raises(DiscoveryLimitError) as error:
        rank_document_attention(snapshot, candidates=candidates)
    assert error.value.category == "dependency-depth"
    assert error.value.observed == 9


def test_layered_dag_path_explosion_fails_closed_before_partial_ranking(monkeypatch):
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1",
        100_000_000,
    )
    changed_doc = _document("diamond-changed")
    incumbent_doc = _document("diamond-incumbent")
    changed = _claim(changed_doc, "diamond-changed-01")
    incumbent = _claim(incumbent_doc, "diamond-incumbent-01")
    documents = [changed_doc, incumbent_doc]
    claims = [changed, incumbent]
    dependencies = []
    upstream = incumbent
    for layer in range(4):
        branch_claims = []
        for branch in range(4):
            branch_doc = _document(
                f"diamond-{layer}-branch-{branch}",
                role=DocumentRole.PROCESS,
            )
            branch_claim = _claim(
                branch_doc,
                f"diamond-{layer}-branch-{branch}-01",
                scopes=("internal",),
            )
            documents.append(branch_doc)
            claims.append(branch_claim)
            branch_claims.append(branch_claim)
            dependencies.append(
                _dependency(
                    downstream=branch_doc,
                    upstream=upstream,
                    exposed=(branch_claim,),
                )
            )
        merge_doc = _document(f"diamond-{layer}-merge", role=DocumentRole.SOP)
        merge_claim = _claim(
            merge_doc,
            f"diamond-{layer}-merge-01",
            scopes=("internal",),
        )
        documents.append(merge_doc)
        claims.append(merge_claim)
        for branch_claim in branch_claims:
            dependencies.append(
                _dependency(
                    downstream=merge_doc,
                    upstream=branch_claim,
                    exposed=(merge_claim,),
                )
            )
        upstream = merge_claim
    aggregate = _aggregate(
        documents=tuple(documents),
        claims=tuple(claims),
        dependencies=tuple(dependencies),
    )
    snapshot = _snapshot(aggregate)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    with pytest.raises(DiscoveryLimitError) as error:
        rank_document_attention(snapshot, candidates=candidates)
    assert error.value.category == "paths-per-document"
    assert error.value.observed == 129


def test_projection_steps_reject_forged_ids_and_wrong_span_document():
    snapshot, _, ranking = _attention_fixture(with_relation=True)
    path = ranking.attention_candidates[0].paths[0]

    forged_relation = path.relation_steps[0].model_dump(mode="json")
    forged_relation["relation_id"] = "rel:" + "0" * 64
    with pytest.raises(ValidationError, match="relation_id"):
        RelationPathStep.model_validate(forged_relation)

    dependency_step = path.dependency_steps[0]
    forged_dependency = dependency_step.model_dump(mode="json")
    forged_dependency["dependency_id"] = "dep:" + "0" * 64
    with pytest.raises(ValidationError, match="dependency_id"):
        DependencyPathStep.model_validate(forged_dependency)

    wrong_span = dependency_step.model_dump(mode="json")
    changed_id = ranking.binding.changed_claim_revision_ids[0]
    changed_document_id = snapshot.aggregate.claims.get(changed_id).document.document_version_id
    wrong_span["downstream_spans"][0]["document_version_id"] = changed_document_id
    with pytest.raises(ValidationError, match="downstream spans"):
        DependencyPathStep.model_validate(wrong_span)


def test_projection_collections_reject_duplicate_paths_and_documents():
    _, _, ranking = _attention_fixture()
    candidate = ranking.attention_candidates[0]
    duplicate_paths = candidate.model_dump(mode="json")
    duplicate_paths["paths"] = [
        candidate.paths[0].model_dump(mode="json"),
        candidate.paths[0].model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="unique path IDs"):
        DocumentAttentionCandidate.model_validate(duplicate_paths)

    duplicate_documents = ranking.model_dump(mode="json")
    duplicate_documents["attention_candidates"] = [
        candidate.model_dump(mode="json"),
        candidate.model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="unique document-version IDs"):
        DocumentAttentionRanking.model_validate(duplicate_documents)


@pytest.mark.parametrize("missing_field", ("pair_id", "score"))
def test_candidate_set_raw_shape_failures_remain_pydantic_validation_errors(missing_field):
    candidate_set = _multi_candidate_fixture()
    payload = candidate_set.model_dump()
    del payload["candidates"][0][missing_field]
    with pytest.raises(ValidationError, match=missing_field):
        RelationshipCandidateSet.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    ("paths", "dependency_steps"),
)
def test_ranking_raw_shape_failures_remain_pydantic_validation_errors(missing_field):
    _, _, ranking = _attention_fixture()
    payload = ranking.model_dump(mode="json")
    if missing_field == "paths":
        del payload["attention_candidates"][0]["paths"]
    else:
        del payload["attention_candidates"][0]["paths"][0]["dependency_steps"]
    with pytest.raises(ValidationError, match=missing_field):
        DocumentAttentionRanking.model_validate(payload)


def test_snapshot_aware_verifier_rejects_self_consistent_forged_ranking():
    snapshot, candidates, ranking = _attention_fixture()
    assert (
        validate_document_attention_ranking(
            snapshot,
            candidates=candidates,
            ranking=ranking,
        )
        == ranking
    )
    forged = DocumentAttentionRanking.create(
        binding=ranking.binding,
        attention_candidates=(),
        excluded_targets=(),
    )
    assert forged.result_sha256 != ranking.result_sha256
    with pytest.raises(ValueError, match="not the deterministic result"):
        validate_document_attention_ranking(
            snapshot,
            candidates=candidates,
            ranking=forged,
        )


def test_candidate_generation_revalidates_once_and_resolves_each_claim_once(monkeypatch):
    changed_documents = tuple(
        _document(f"batch-changed-{index}", family="batch.changed") for index in range(2)
    )
    incumbent_documents = tuple(
        _document(f"batch-incumbent-{index}", family=f"batch.incumbent-{index}")
        for index in range(3)
    )
    changed = tuple(
        _claim(document, f"batch-changed-{index}-01")
        for index, document in enumerate(changed_documents)
    )
    incumbents = tuple(
        _claim(document, f"batch-incumbent-{index}-01", scopes=("incumbent",))
        for index, document in enumerate(incumbent_documents)
    )
    snapshot = _snapshot(
        _aggregate(
            documents=(*changed_documents, *incumbent_documents),
            claims=(*changed, *incumbents),
        )
    )

    revalidations = 0
    resolutions: list[tuple[TemporalTargetKind, str]] = []
    original_revalidate = change_control_models._revalidate_temporal_constraints
    original_resolve = change_control_models._resolve_temporality

    def counted_revalidate(constraints):
        nonlocal revalidations
        revalidations += 1
        return original_revalidate(constraints)

    def counted_resolve(**kwargs):
        target = kwargs["target"]
        resolutions.append((target.kind, target.target_id))
        return original_resolve(**kwargs)

    monkeypatch.setattr(
        change_control_models,
        "_revalidate_temporal_constraints",
        counted_revalidate,
    )
    monkeypatch.setattr(change_control_models, "_resolve_temporality", counted_resolve)
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=tuple(sorted(item.claim_revision_id for item in changed)),
        as_of=date(2025, 2, 1),
    )

    assert len(candidates.candidates) == 6
    assert revalidations == 1
    claim_resolutions = [
        item for item in resolutions if item[0] == TemporalTargetKind.CLAIM_REVISION
    ]
    assert len(claim_resolutions) == 5
    assert len(set(claim_resolutions)) == 5
    assert not any(item[0] == TemporalTargetKind.DOCUMENT_VERSION for item in resolutions)


def test_ranking_uses_one_context_and_resolves_only_reached_documents(monkeypatch):
    changed_doc = _document("batch-rank-changed", family="batch.rank")
    incumbent_doc = _document("batch-rank-incumbent", family="batch.rank")
    reached_doc = _document("batch-rank-reached", role=DocumentRole.SOP)
    unreachable_doc = _document("batch-rank-unreachable", role=DocumentRole.FAQ)
    changed = _claim(changed_doc, "batch-rank-changed-01")
    incumbent = _claim(incumbent_doc, "batch-rank-incumbent-01")
    snapshot = _snapshot(
        _aggregate(
            documents=(changed_doc, incumbent_doc, reached_doc, unreachable_doc),
            claims=(changed, incumbent),
            dependencies=(_dependency(downstream=reached_doc, upstream=incumbent),),
        )
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )

    contexts = []
    revalidations = 0
    resolutions: list[tuple[TemporalTargetKind, str]] = []
    original_context_factory = discovery_module.TemporalResolutionContext.from_aggregate
    original_revalidate = change_control_models._revalidate_temporal_constraints
    original_resolve = change_control_models._resolve_temporality

    def context_factory(aggregate, *, as_of):
        context = original_context_factory(aggregate, as_of=as_of)
        contexts.append(context)
        return context

    def counted_revalidate(constraints):
        nonlocal revalidations
        revalidations += 1
        return original_revalidate(constraints)

    def counted_resolve(**kwargs):
        target = kwargs["target"]
        resolutions.append((target.kind, target.target_id))
        return original_resolve(**kwargs)

    monkeypatch.setattr(
        discovery_module.TemporalResolutionContext,
        "from_aggregate",
        context_factory,
    )
    monkeypatch.setattr(
        change_control_models,
        "_revalidate_temporal_constraints",
        counted_revalidate,
    )
    monkeypatch.setattr(change_control_models, "_resolve_temporality", counted_resolve)
    ranking = rank_document_attention(snapshot, candidates=candidates)

    assert ranking.attention_candidates[0].document_version_id == reached_doc.document_version_id
    assert len(contexts) == 1
    assert revalidations == 1
    assert contexts[0].resolved_claim_count == 2
    assert contexts[0].resolved_document_count == 1
    document_targets = {
        target_id for kind, target_id in resolutions if kind == TemporalTargetKind.DOCUMENT_VERSION
    }
    assert document_targets == {reached_doc.document_version_id}
    assert unreachable_doc.document_version_id not in document_targets


def test_oversized_span_and_dependency_projection_fail_closed():
    changed_doc = _document("bytes-changed", family="bytes.policy")
    incumbent_doc = _document("bytes-incumbent", family="bytes.policy")
    target_doc = _document("bytes-target", role=DocumentRole.SOP)
    changed = _claim(changed_doc, "bytes-changed-01")
    incumbent = _claim(incumbent_doc, "bytes-incumbent-01")
    oversized = _dependency(
        downstream=target_doc,
        upstream=incumbent,
        quote='é"\\' * MAX_SPAN_CANONICAL_BYTES_V1,
    )
    snapshot = _snapshot(
        _aggregate(
            documents=(changed_doc, incumbent_doc, target_doc),
            claims=(changed, incumbent),
            dependencies=(oversized,),
        )
    )
    with pytest.raises(DiscoveryLimitError) as span_error:
        generate_relationship_candidates(
            snapshot,
            changed_claim_revision_ids=(changed.claim_revision_id,),
            as_of=date(2025, 2, 1),
        )
    assert span_error.value.category == "span-canonical-bytes"
    assert span_error.value.observed > MAX_SPAN_CANONICAL_BYTES_V1

    step_payload = {
        "dependency_id": oversized.dependency_id,
        "dependency_kind": oversized.dependency_kind,
        "upstream_claim_revision_id": incumbent.claim_revision_id,
        "downstream_document_version_id": target_doc.document_version_id,
        "downstream_spans": oversized.downstream_spans,
        "exposed_downstream_claim_revision_ids": (),
    }
    with pytest.raises(DiscoveryLimitError) as model_error:
        DependencyPathStep.model_validate(step_payload)
    assert model_error.value.category == "span-canonical-bytes"

    spans = tuple(
        sorted(
            (_span(target_doc, f"{index:02d}" + "x" * 14_000) for index in range(10)),
            key=lambda span: canonical_json_bytes(span.model_dump(mode="json")),
        )
    )
    dependency = DependencyAssessment.create(
        downstream=target_doc,
        upstream=incumbent,
        dependency_kind=DependencyKind.IMPLEMENTS,
        downstream_spans=spans,
        rationale="Canonical dependency fact with bounded individual spans.",
        confidence=0.9,
    )
    step_payload["dependency_id"] = dependency.dependency_id
    step_payload["downstream_spans"] = dependency.downstream_spans
    with pytest.raises(DiscoveryLimitError) as dependency_error:
        DependencyPathStep.model_validate(step_payload)
    assert dependency_error.value.category == "dependency-projection-bytes"


def test_caller_authored_candidate_and_cumulative_projection_bytes_fail_closed():
    snapshot, changed, _ = _simple_snapshot()
    candidate_set = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    original = candidate_set.candidates[0]
    oversized_payload = original.model_dump(mode="json")
    escaped_multibyte = 'é"\\' * 3_000
    oversized_payload["changed_document_family"] = escaped_multibyte
    oversized_payload["incumbent_document_family"] = escaped_multibyte
    with pytest.raises(DiscoveryLimitError) as candidate_error:
        RelationshipCandidate.model_validate(oversized_payload)
    assert candidate_error.value.category == "relationship-candidate-bytes"

    candidates = []
    shared_scope = "x" * 4_000
    for index in range(500):
        payload = original.model_dump(mode="json")
        incumbent_id = f"claimrev:{index + 1:064x}"
        endpoint_ids = tuple(sorted((changed.claim_revision_id, incumbent_id)))
        pair_payload = {
            "namespace": "mastervault.claim-pair.v1",
            "claim_revision_ids": list(endpoint_ids),
        }
        payload["pair_id"] = (
            "pair:" + hashlib.sha256(canonical_json_bytes(pair_payload)).hexdigest()
        )
        payload["claim_revision_ids"] = endpoint_ids
        payload["incumbent_claim_revision_id"] = incumbent_id
        payload["incumbent_claim_identity_id"] = f"claim:{index + 1:064x}"
        payload["shared_scopes"] = [shared_scope]
        payload["score"]["shared_scope_count"] = 1
        payload["incumbent_temporal_resolution"]["target"]["target_id"] = incumbent_id
        candidates.append(RelationshipCandidate.model_validate(payload))
    with pytest.raises(DiscoveryLimitError) as cumulative_error:
        RelationshipCandidateSet.create(
            binding=candidate_set.binding,
            candidates=tuple(candidates),
        )
    assert cumulative_error.value.category == "total-relationship-candidate-bytes"

    result_payload = {
        "namespace": "mastervault.relationship-candidate-set.v1",
        "schema_version": 1,
        "binding": candidate_set.binding.model_dump(mode="json"),
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }
    serialized = {
        "schema_version": 1,
        "binding": result_payload["binding"],
        "candidates": result_payload["candidates"],
        "result_sha256": hashlib.sha256(canonical_json_bytes(result_payload)).hexdigest(),
    }
    with pytest.raises(DiscoveryLimitError) as validation_error:
        RelationshipCandidateSet.model_validate(serialized)
    assert validation_error.value.category == "total-relationship-candidate-bytes"


def test_generator_charges_candidate_bytes_before_retention(monkeypatch):
    snapshot, changed, _ = _simple_snapshot()
    baseline = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    candidate_charge = discovery_module._canonical_projection_charge(
        baseline.candidates[0].model_dump(mode="json")
    )
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1",
        candidate_charge - 1,
    )
    with pytest.raises(DiscoveryLimitError) as error:
        generate_relationship_candidates(
            snapshot,
            changed_claim_revision_ids=(changed.claim_revision_id,),
            as_of=date(2025, 2, 1),
        )
    assert error.value.category == "total-relationship-candidate-bytes"
    assert error.value.observed == candidate_charge


def test_python_mode_candidate_lists_stop_at_first_over_budget_prefix(monkeypatch):
    candidate_set = _multi_candidate_fixture()
    payload = candidate_set.model_dump()
    payload["candidates"] = list(payload["candidates"])
    ordered_candidates = candidate_set.candidates
    original_charge = discovery_module._canonical_projection_charge
    candidate_charges = tuple(
        original_charge(candidate.model_dump(mode="json")) for candidate in ordered_candidates
    )
    original_count_limit = discovery_module.MAX_RELATIONSHIP_CANDIDATES_V1
    original_candidate_limit = discovery_module.MAX_RELATIONSHIP_CANDIDATE_BYTES_V1
    visited_pair_ids: list[str] = []

    def counted_charge(candidate_payload):
        if (
            isinstance(candidate_payload, dict)
            and {"pair_id", "claim_revision_ids", "score"} <= candidate_payload.keys()
        ):
            visited_pair_ids.append(candidate_payload["pair_id"])
        return original_charge(candidate_payload)

    monkeypatch.setattr(discovery_module, "_canonical_projection_charge", counted_charge)

    monkeypatch.setattr(discovery_module, "MAX_RELATIONSHIP_CANDIDATES_V1", 2)
    with pytest.raises(DiscoveryLimitError) as count_error:
        RelationshipCandidateSet.model_validate(payload)
    assert count_error.value.category == "relationship-candidates"
    assert count_error.value.observed == 3
    assert visited_pair_ids == []

    monkeypatch.setattr(
        discovery_module,
        "MAX_RELATIONSHIP_CANDIDATES_V1",
        original_count_limit,
    )
    monkeypatch.setattr(
        discovery_module,
        "MAX_RELATIONSHIP_CANDIDATE_BYTES_V1",
        candidate_charges[0] - 1,
    )
    with pytest.raises(DiscoveryLimitError) as candidate_error:
        RelationshipCandidateSet.model_validate(payload)
    assert candidate_error.value.category == "relationship-candidate-bytes"
    assert candidate_error.value.observed == candidate_charges[0]
    assert visited_pair_ids == [ordered_candidates[0].pair_id]

    visited_pair_ids.clear()
    monkeypatch.setattr(
        discovery_module,
        "MAX_RELATIONSHIP_CANDIDATE_BYTES_V1",
        original_candidate_limit,
    )
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1",
        candidate_charges[0],
    )
    with pytest.raises(DiscoveryLimitError) as cumulative_error:
        RelationshipCandidateSet.model_validate(payload)
    assert cumulative_error.value.category == "total-relationship-candidate-bytes"
    assert cumulative_error.value.observed == candidate_charges[0] + candidate_charges[1]
    assert visited_pair_ids == [
        ordered_candidates[0].pair_id,
        ordered_candidates[1].pair_id,
    ]


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    (
        ("MAX_RELATIONSHIP_CANDIDATES_V1", 0),
        ("MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1", 0),
    ),
)
def test_generator_candidate_containers_are_rejected_without_consumption(
    monkeypatch,
    limit_name,
    limit_value,
):
    candidate_set = _multi_candidate_fixture()
    candidate_payloads = tuple(candidate.model_dump() for candidate in candidate_set.candidates)
    yielded_pair_ids: list[str] = []

    def candidate_generator():
        for candidate_payload in candidate_payloads:
            yielded_pair_ids.append(candidate_payload["pair_id"])
            yield candidate_payload

    payload = candidate_set.model_dump()
    payload["candidates"] = candidate_generator()
    monkeypatch.setattr(discovery_module, limit_name, limit_value)

    with pytest.raises(ValidationError, match="list or tuple"):
        RelationshipCandidateSet.model_validate(payload)
    assert yielded_pair_ids == []


def test_nested_candidate_generators_are_validation_errors_without_consumption():
    candidate_set = _multi_candidate_fixture()
    yielded_scopes: list[str] = []

    def scope_generator():
        yielded_scopes.append("shared-scope")
        yield "shared-scope"

    payload = candidate_set.model_dump()
    payload["candidates"] = list(payload["candidates"])
    payload["candidates"][0]["shared_scopes"] = scope_generator()

    with pytest.raises(ValidationError, match="bounded JSON-compatible"):
        RelationshipCandidateSet.model_validate(payload)
    assert yielded_scopes == []


def test_malformed_candidate_cannot_disable_later_prefix_limits(monkeypatch):
    candidate_set = _multi_candidate_fixture()
    payload = candidate_set.model_dump()
    payload["candidates"] = list(payload["candidates"])
    payload["candidates"][0]["shared_scopes"] = iter(("shared-scope",))
    later_candidate = candidate_set.candidates[1]
    original_charge = discovery_module._canonical_projection_charge
    later_charge = original_charge(later_candidate.model_dump(mode="json"))
    visited_pair_ids: list[str] = []

    def counted_charge(candidate_payload):
        if isinstance(candidate_payload, dict) and "pair_id" in candidate_payload:
            visited_pair_ids.append(candidate_payload["pair_id"])
        return original_charge(candidate_payload)

    monkeypatch.setattr(discovery_module, "_canonical_projection_charge", counted_charge)
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1",
        later_charge - 1,
    )

    with pytest.raises(DiscoveryLimitError) as error:
        RelationshipCandidateSet.model_validate(payload)
    assert error.value.category == "total-relationship-candidate-bytes"
    assert error.value.observed == later_charge
    assert visited_pair_ids == [later_candidate.pair_id]


def test_final_payload_limits_use_exact_canonical_json_boundaries(monkeypatch):
    _, candidates, ranking = _attention_fixture()
    candidate_payload_bytes = len(canonical_json_bytes(candidates._result_payload()))
    monkeypatch.setattr(
        discovery_module,
        "MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1",
        candidate_payload_bytes,
    )
    assert (
        RelationshipCandidateSet.create(
            binding=candidates.binding,
            candidates=candidates.candidates,
        )
        == candidates
    )
    monkeypatch.setattr(
        discovery_module,
        "MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1",
        candidate_payload_bytes - 1,
    )
    with pytest.raises(DiscoveryLimitError) as candidate_error:
        RelationshipCandidateSet.create(
            binding=candidates.binding,
            candidates=candidates.candidates,
        )
    assert candidate_error.value.category == "candidate-set-payload-bytes"
    assert candidate_error.value.observed == candidate_payload_bytes

    digest_calls = 0
    original_sha = discovery_module._canonical_sha256

    def counted_sha(payload):
        nonlocal digest_calls
        digest_calls += 1
        return original_sha(payload)

    monkeypatch.setattr(discovery_module, "_canonical_sha256", counted_sha)
    with pytest.raises(DiscoveryLimitError) as candidate_validation_error:
        RelationshipCandidateSet.model_validate(candidates.model_dump(mode="json"))
    assert candidate_validation_error.value.category == "candidate-set-payload-bytes"
    assert candidate_validation_error.value.observed == candidate_payload_bytes
    assert digest_calls == 0
    monkeypatch.setattr(discovery_module, "_canonical_sha256", original_sha)

    ranking_payload_bytes = len(canonical_json_bytes(ranking._result_payload()))
    monkeypatch.setattr(
        discovery_module,
        "MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1",
        ranking_payload_bytes,
    )
    assert (
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=ranking.attention_candidates,
            excluded_targets=ranking.excluded_targets,
        )
        == ranking
    )
    monkeypatch.setattr(
        discovery_module,
        "MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1",
        ranking_payload_bytes - 1,
    )
    with pytest.raises(DiscoveryLimitError) as ranking_error:
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=ranking.attention_candidates,
            excluded_targets=ranking.excluded_targets,
        )
    assert ranking_error.value.category == "attention-ranking-payload-bytes"
    assert ranking_error.value.observed == ranking_payload_bytes
    with pytest.raises(DiscoveryLimitError) as validation_error:
        DocumentAttentionRanking.model_validate(ranking.model_dump(mode="json"))
    assert validation_error.value.category == "attention-ranking-payload-bytes"
    assert validation_error.value.observed == ranking_payload_bytes


def test_self_consistent_oversized_ranking_rejected_by_create_and_model_validate():
    _, _, ranking = _attention_fixture()
    candidate_payload = ranking.attention_candidates[0].model_dump(mode="json")
    candidate_payload["document_id"] = 'é"\\' * 100_000
    with pytest.raises(DiscoveryLimitError) as target_error:
        DocumentAttentionCandidate.model_validate(candidate_payload)
    assert target_error.value.category == "attention-target-record-bytes"

    oversized_candidate = ranking.attention_candidates[0].model_copy(
        update={"document_id": candidate_payload["document_id"]}
    )

    with pytest.raises(DiscoveryLimitError) as create_error:
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=(oversized_candidate,),
            excluded_targets=ranking.excluded_targets,
        )
    assert create_error.value.category == "attention-target-record-bytes"

    payload = ranking.model_dump(mode="json")
    payload["attention_candidates"] = [oversized_candidate.model_dump(mode="json")]
    result_payload = {
        "namespace": "mastervault.document-attention-ranking.v1",
        "schema_version": 1,
        "binding": payload["binding"],
        "attention_candidates": payload["attention_candidates"],
        "excluded_targets": payload["excluded_targets"],
    }
    payload["result_sha256"] = hashlib.sha256(canonical_json_bytes(result_payload)).hexdigest()
    with pytest.raises(DiscoveryLimitError) as validation_error:
        DocumentAttentionRanking.model_validate(payload)
    assert validation_error.value.category == "attention-target-record-bytes"


def test_generator_rejects_large_valid_document_id_before_target_retention():
    changed_doc = _document("target-budget-changed", family="target-budget.policy")
    incumbent_doc = _document("target-budget-incumbent", family="target-budget.policy")
    target_doc = _document("x" * 400_000, family="target-budget.downstream")
    changed = _claim(changed_doc, "target-budget-changed-01")
    incumbent = _claim(incumbent_doc, "target-budget-incumbent-01")
    snapshot = _snapshot(
        _aggregate(
            documents=(changed_doc, incumbent_doc, target_doc),
            claims=(changed, incumbent),
            dependencies=(
                _dependency(
                    downstream=target_doc,
                    upstream=incumbent,
                    source_note_path="runtime/sources/large-target.md",
                ),
            ),
        )
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    with pytest.raises(DiscoveryLimitError) as error:
        rank_document_attention(snapshot, candidates=candidates)
    assert error.value.category == "attention-target-record-bytes"


def test_path_budget_boundaries_stop_create_validation_and_verifier_at_prefix(monkeypatch):
    snapshot, candidates, ranking = _multi_target_attention_fixture()
    ordered_paths = tuple(candidate.paths[0] for candidate in ranking.attention_candidates)
    original_charge = discovery_module._canonical_projection_charge
    first_charge = original_charge(ordered_paths[0].model_dump(mode="json"))
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1",
        first_charge,
    )
    visited_path_ids: list[str] = []

    def counted_charge(payload):
        if (
            isinstance(payload, dict)
            and {
                "path_id",
                "dependency_steps",
                "anchor_kind",
            }
            <= payload.keys()
        ):
            visited_path_ids.append(payload["path_id"])
        return original_charge(payload)

    monkeypatch.setattr(discovery_module, "_canonical_projection_charge", counted_charge)

    with pytest.raises(DiscoveryLimitError) as create_error:
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=ranking.attention_candidates,
            excluded_targets=ranking.excluded_targets,
        )
    assert create_error.value.category == "total-attention-path-bytes"
    assert visited_path_ids == [ordered_paths[0].path_id, ordered_paths[1].path_id]

    visited_path_ids.clear()
    with pytest.raises(DiscoveryLimitError) as validation_error:
        DocumentAttentionRanking.model_validate(ranking.model_dump(mode="json"))
    assert validation_error.value.category == "total-attention-path-bytes"
    assert visited_path_ids == [ordered_paths[0].path_id, ordered_paths[1].path_id]

    visited_path_ids.clear()
    with pytest.raises(DiscoveryLimitError) as verifier_error:
        validate_document_attention_ranking(
            snapshot,
            candidates=candidates,
            ranking=ranking,
        )
    assert verifier_error.value.category == "total-attention-path-bytes"
    assert visited_path_ids == [ordered_paths[0].path_id, ordered_paths[1].path_id]


def test_target_budget_boundaries_stop_create_validation_and_verifier_at_prefix(monkeypatch):
    snapshot, candidates, ranking = _multi_target_attention_fixture()
    ordered_targets = ranking.attention_candidates
    original_charge = discovery_module._canonical_projection_charge
    first_charge = original_charge(ordered_targets[0].model_dump(mode="json"))
    monkeypatch.setattr(
        discovery_module,
        "MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1",
        first_charge,
    )
    visited_target_ids: list[str] = []

    def counted_charge(payload):
        if (
            isinstance(payload, dict)
            and "paths" in payload
            and ("score" in payload or "reasons" in payload)
        ):
            visited_target_ids.append(payload["document_version_id"])
        return original_charge(payload)

    monkeypatch.setattr(discovery_module, "_canonical_projection_charge", counted_charge)

    with pytest.raises(DiscoveryLimitError) as create_error:
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=ranking.attention_candidates,
            excluded_targets=ranking.excluded_targets,
        )
    assert create_error.value.category == "total-attention-target-record-bytes"
    assert visited_target_ids == [
        ordered_targets[0].document_version_id,
        ordered_targets[1].document_version_id,
    ]

    visited_target_ids.clear()
    with pytest.raises(DiscoveryLimitError) as validation_error:
        DocumentAttentionRanking.model_validate(ranking.model_dump(mode="json"))
    assert validation_error.value.category == "total-attention-target-record-bytes"
    assert visited_target_ids == [
        ordered_targets[0].document_version_id,
        ordered_targets[1].document_version_id,
    ]

    visited_target_ids.clear()
    with pytest.raises(DiscoveryLimitError) as verifier_error:
        validate_document_attention_ranking(
            snapshot,
            candidates=candidates,
            ranking=ranking,
        )
    assert verifier_error.value.category == "total-attention-target-record-bytes"
    assert visited_target_ids == [
        ordered_targets[0].document_version_id,
        ordered_targets[1].document_version_id,
    ]


def test_retained_path_count_create_stops_before_projection_accounting(monkeypatch):
    _, _, ranking = _multi_target_attention_fixture()
    projection_calls = 0
    original_charge = discovery_module._canonical_projection_charge

    def counted_charge(payload):
        nonlocal projection_calls
        projection_calls += 1
        return original_charge(payload)

    monkeypatch.setattr(discovery_module, "MAX_TOTAL_GENERATED_PATHS_V1", 1)
    monkeypatch.setattr(discovery_module, "_canonical_projection_charge", counted_charge)
    with pytest.raises(DiscoveryLimitError) as error:
        DocumentAttentionRanking.create(
            binding=ranking.binding,
            attention_candidates=ranking.attention_candidates,
            excluded_targets=ranking.excluded_targets,
        )
    assert error.value.category == "total-retained-paths"
    assert error.value.observed == 2
    assert projection_calls == 0


def test_oversized_attention_path_is_rejected_before_path_hash_work(monkeypatch):
    snapshot, candidates, ranking = _attention_fixture()
    path = ranking.attention_candidates[0].paths[0]
    path_charge = discovery_module._canonical_projection_charge(path.model_dump(mode="json"))
    path_hash_calls = 0
    original_sha = discovery_module._canonical_sha256

    def counted_sha(payload):
        nonlocal path_hash_calls
        if isinstance(payload, dict) and payload.get("namespace") == (
            "mastervault.attention-path.v1"
        ):
            path_hash_calls += 1
        return original_sha(payload)

    monkeypatch.setattr(discovery_module, "MAX_ATTENTION_PATH_BYTES_V1", path_charge - 1)
    monkeypatch.setattr(discovery_module, "_canonical_sha256", counted_sha)

    with pytest.raises(DiscoveryLimitError) as model_error:
        AttentionPath.model_validate(path.model_dump(mode="json"))
    assert model_error.value.category == "attention-path-bytes"
    assert path_hash_calls == 0

    with pytest.raises(DiscoveryLimitError) as generator_error:
        rank_document_attention(snapshot, candidates=candidates)
    assert generator_error.value.category == "attention-path-bytes"
    assert path_hash_calls == 0


def test_width_two_layered_dag_with_repeated_large_spans_hits_byte_budget():
    changed_doc = _document("bytes-dag-changed", family="bytes.dag")
    incumbent_doc = _document("bytes-dag-incumbent", family="bytes.dag")
    changed = _claim(changed_doc, "bytes-dag-changed-01")
    incumbent = _claim(incumbent_doc, "bytes-dag-incumbent-01")
    documents = [changed_doc, incumbent_doc]
    claims = [changed, incumbent]
    dependencies = []
    previous: tuple[VersionedClaimRevision, ...] = (incumbent,)
    large_quote = "Repeated canonical downstream evidence " + "x" * 6_000
    for layer in range(7):
        current = []
        for branch in range(2):
            document = _document(
                f"bytes-dag-{layer}-{branch}",
                role=DocumentRole.PROCESS,
            )
            claim = _claim(
                document,
                f"bytes-dag-{layer}-{branch}-01",
                scopes=("internal",),
            )
            documents.append(document)
            claims.append(claim)
            current.append(claim)
        for upstream in previous:
            for downstream_claim in current:
                dependencies.append(
                    _dependency(
                        downstream=downstream_claim.document,
                        upstream=upstream,
                        exposed=(downstream_claim,),
                        quote=large_quote,
                    )
                )
        previous = tuple(current)
    terminal = _document("bytes-dag-terminal", role=DocumentRole.SOP)
    documents.append(terminal)
    for upstream in previous:
        dependencies.append(
            _dependency(
                downstream=terminal,
                upstream=upstream,
                quote=large_quote,
            )
        )
    assert len(dependencies) == 28
    snapshot = _snapshot(
        _aggregate(
            documents=tuple(documents),
            claims=tuple(claims),
            dependencies=tuple(dependencies),
        )
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2025, 2, 1),
    )
    with pytest.raises(DiscoveryLimitError) as error:
        rank_document_attention(snapshot, candidates=candidates)
    assert error.value.category == "total-attention-path-bytes"
    assert error.value.limit == MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1
    assert MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1 < error.value.observed < 2_000_000


def test_package_root_exports_only_top_level_discovery_surface():
    import mastervault.change_control as package

    expected = {
        "DiscoveryLimitError",
        "DocumentAttentionCandidate",
        "DocumentAttentionRanking",
        "RelationshipCandidate",
        "RelationshipCandidateSet",
        "generate_relationship_candidates",
        "rank_document_attention",
        "validate_document_attention_ranking",
    }
    assert all(hasattr(package, name) for name in expected)
    assert not any(
        hasattr(package, name)
        for name in (
            "AnalysisBinding",
            "AttentionAnchorKind",
            "AttentionPath",
            "AttentionScoreV1",
            "DependencyPathStep",
            "RelationPathStep",
        )
    )


def test_discovery_internal_imports_are_dependency_neutral():
    module_path = (
        Path(__file__).resolve().parents[3] / "src/mastervault/change_control/discovery.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    runtime_imports: set[str] = set()
    type_only_imports: set[str] = set()
    all_internal_imports: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("mastervault.")
        ):
            all_internal_imports.add(node.module)
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("mastervault.")
        ):
            runtime_imports.add(node.module)
        elif (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            type_only_imports.update(
                child.module
                for child in node.body
                if isinstance(child, ast.ImportFrom)
                and child.module
                and child.module.startswith("mastervault.")
            )
    assert runtime_imports == {"mastervault.change_control.models"}
    assert type_only_imports == {"mastervault.change_control.store"}
    assert all_internal_imports == runtime_imports | type_only_imports
