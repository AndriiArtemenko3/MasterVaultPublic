from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import mastervault.change_control.models as change_control_models
from mastervault.change_control import (
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
    TemporalTarget,
    TemporalTargetKind,
    ValidatedTemporalConstraintSet,
    VersionedClaimRevision,
    resolve_claim_temporality,
    resolve_document_temporality,
    stable_content_id,
)
from mastervault.change_control.models import TemporalResolutionContext
from mastervault.document_intelligence.models import EvidenceRef

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _document(
    *,
    document_id: str,
    version: str,
    declared_from: date,
    family: str = "customer-support.returns-policy",
    declared_to: date | None = None,
    sha: str = SHA_A,
    path: str | None = None,
    role: DocumentRole = DocumentRole.POLICY,
    authority: DocumentAuthority = DocumentAuthority.PRIMARY,
) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=document_id,
        document_family=family,
        version_label=version,
        source_path=path or f"datasets/larkstead/raw/{document_id}.md",
        source_sha256=sha,
        declared_effective_from=declared_from,
        declared_effective_to=declared_to,
        role=role,
        authority=authority,
    )


def _source(
    document: DocumentVersionMetadata,
    claim_id: str,
    *,
    path: str | None = None,
    sha: str = SHA_C,
) -> ClaimSourceReference:
    return ClaimSourceReference(
        source_note_path=path or f"customer-support/sources/{document.document_id}.md",
        source_note_sha256=sha,
        source_claim_id=claim_id,
    )


def _revision(
    document: DocumentVersionMetadata,
    *,
    claim_id: str,
    statement: str,
    declared_from: date | None = None,
    declared_to: date | None = None,
    scopes: tuple[str, ...] = ("return-window",),
    source: ClaimSourceReference | None = None,
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=source or _source(document, claim_id),
        statement=statement,
        declared_effective_from=declared_from or document.declared_effective_from,
        declared_effective_to=declared_to,
        scopes=scopes,
    )


def _versions() -> tuple[VersionedClaimRevision, VersionedClaimRevision]:
    old_document = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
    )
    new_document = _document(
        document_id="returns-v2",
        version="v2",
        declared_from=date(2026, 1, 12),
        sha=SHA_B,
    )
    return (
        _revision(
            old_document,
            claim_id="returns-v1-01",
            statement="Customers may return an item within 30 days of delivery.",
        ),
        _revision(
            new_document,
            claim_id="returns-v2-01",
            statement="Customers may return an item within 45 days of delivery.",
        ),
    )


def _assessment(
    pair: ComparableClaimPair,
    disposition: PairDisposition,
) -> RelationAssessment:
    newer_revision_id = None
    if disposition == PairDisposition.SUPERSEDES:
        newer_revision_id = max(
            pair.claim_revisions,
            key=lambda revision: revision.declared_effective_from,
        ).claim_revision_id
    return RelationAssessment.create(
        pair=pair,
        disposition=disposition,
        rationale=f"The pair is classified as {disposition.value}.",
        confidence=0.9,
        newer_revision_id=newer_revision_id,
    )


def _constraint(
    *,
    target: TemporalTarget,
    bound: date,
    basis_relation_id: str,
    status: TemporalConstraintStatus = TemporalConstraintStatus.ACCEPTED,
) -> TemporalConstraint:
    payload = {
        "namespace": "mastervault.temporal-constraint.v1",
        "resolver_version": "temporal-resolution-v1",
        "target": target.model_dump(mode="json"),
        "inferred_valid_to_exclusive": bound.isoformat(),
        "basis_relation_ids": [basis_relation_id],
    }
    return TemporalConstraint(
        constraint_id=stable_content_id("tempc", payload),
        target=target,
        inferred_valid_to_exclusive=bound,
        basis_relation_ids=(basis_relation_id,),
        status=status,
        rationale="A reviewed relation supplies this temporal boundary.",
    )


def _validated(
    *,
    constraints: tuple[TemporalConstraint, ...] = (),
    relations: tuple[RelationAssessment, ...] = (),
    replacements: tuple[DocumentReplacementAssessment, ...] = (),
) -> ValidatedTemporalConstraintSet:
    return ValidatedTemporalConstraintSet.create(
        constraints=TemporalConstraintSet.create(constraints),
        relation_graph=RelationGraph.create(relations),
        document_replacements=DocumentReplacementSet.create(replacements),
    )


def _span(
    document: DocumentVersionMetadata,
    quote: str,
    *,
    start: int,
    path: str = "operations/sources/showroom-process.md",
    sha: str = SHA_C,
) -> DocumentSpanReference:
    return DocumentSpanReference(
        document_version_id=document.document_version_id,
        source_note_path=path,
        source_note_sha256=sha,
        quote=quote,
        start_char=start,
        end_char=start + len(quote),
    )


def _replacement(
    old: DocumentVersionMetadata,
    new: DocumentVersionMetadata,
    *,
    status: TemporalConstraintStatus = TemporalConstraintStatus.ACCEPTED,
) -> DocumentReplacementAssessment:
    return DocumentReplacementAssessment.create(
        newer_document=new,
        older_document=old,
        status=status,
        rationale="Review confirms that the later document replaces the earlier version.",
        confidence=0.95,
    )


def test_document_identity_is_logical_and_binding_independent():
    original = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
    )
    rebound = _document(
        document_id="renamed-catalog-key",
        version="v1",
        declared_from=date(2024, 2, 1),
        declared_to=date(2027, 1, 1),
        sha=SHA_B,
        path="datasets/larkstead/raw/corrected/location.md",
        role=DocumentRole.FAQ,
        authority=DocumentAuthority.INFORMATIONAL,
    )
    changed_family = _document(
        document_id="returns-v1",
        family="customer-support.refund-policy",
        version="v1",
        declared_from=date(2024, 1, 15),
    )

    assert original.document_version_id == rebound.document_version_id
    assert original.document_version_id != changed_family.document_version_id
    assert original.document_version_id.startswith("docv:")
    assert len(original.document_version_id.removeprefix("docv:")) == 64


def test_document_registry_replays_exactly_and_rejects_conflicting_bindings():
    original = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
    )
    assert DocumentVersionRegistry.create((original, original)).documents == (original,)
    with pytest.raises(ValueError, match="different source bytes"):
        DocumentVersionRegistry.create(
            (original, original.model_copy(update={"source_sha256": SHA_B}))
        )
    with pytest.raises(ValueError, match="multiple non-identical bindings"):
        DocumentVersionRegistry.create(
            (
                original,
                original.model_copy(
                    update={"source_path": "datasets/larkstead/raw/moved/returns-v1.md"}
                ),
            )
        )


def test_standalone_document_and_claim_binding_mutation_is_deferred():
    old, _new = _versions()
    document_registry = DocumentVersionRegistry.create((old.document,))
    claim_registry = ClaimRevisionRegistry.create((old,))

    assert document_registry.get(old.document.document_version_id) == old.document
    assert claim_registry.get(old.claim_revision_id) == old
    assert not hasattr(document_registry, "replace_binding")
    assert not hasattr(claim_registry, "replace_binding")


def test_paths_intervals_and_semantic_text_are_strict():
    with pytest.raises((ValueError, ValidationError), match="safe relative path"):
        _document(
            document_id="returns-v1",
            version="v1",
            declared_from=date(2024, 1, 15),
            path="../escape.md",
        )
    with pytest.raises(ValidationError, match="must follow"):
        _document(
            document_id="returns-v1",
            version="v1",
            declared_from=date(2024, 1, 15),
            declared_to=date(2024, 1, 15),
        )
    document = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
    )
    with pytest.raises((ValueError, ValidationError), match="statement"):
        _revision(
            document,
            claim_id="returns-v1-01",
            statement=" Customers  may return an item within thirty days.",
        )


def test_claim_identity_revision_and_binding_are_separate():
    document = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
    )
    original = _revision(
        document,
        claim_id="returns-v1-01",
        statement="Customers may return an item within 30 days of delivery.",
    )
    evidence = EvidenceRef(
        asset_sha256=SHA_A,
        page_number=1,
        block_id="page-0001-block-0001",
        quote="within 30 days",
        start_char=34,
        end_char=48,
    )
    rebound = _revision(
        document,
        claim_id="returns-v1-01",
        statement=original.statement,
        source=ClaimSourceReference(
            source_note_path="customer-support/sources/moved.md",
            source_note_sha256=SHA_D,
            source_claim_id="returns-v1-01",
            evidence=(evidence,),
        ),
    )
    semantic_edit = _revision(
        document,
        claim_id="returns-v1-01",
        statement="Customers may return an item within 31 days of delivery.",
    )

    assert original.claim_identity_id == rebound.claim_identity_id
    assert original.claim_revision_id == rebound.claim_revision_id
    assert semantic_edit.claim_identity_id == original.claim_identity_id
    assert semantic_edit.claim_revision_id != original.claim_revision_id


def test_claim_registry_rejects_claim_and_document_binding_conflicts():
    old, _new = _versions()
    rebound = old.model_copy(
        update={
            "source": old.source.model_copy(
                update={"source_note_path": "customer-support/sources/moved.md"}
            )
        }
    )
    with pytest.raises(ValueError, match="multiple source bindings"):
        ClaimRevisionRegistry.create((old, rebound))

    conflicting_document = old.document.model_copy(update={"source_sha256": SHA_D})
    second = _revision(
        conflicting_document,
        claim_id="returns-v1-02",
        statement="Clearance products remain final sale without exception.",
        scopes=("clearance-returns",),
    )
    with pytest.raises(ValueError, match="conflicting bindings for document_version_id"):
        ClaimRevisionRegistry.create((old, second))


def test_closed_claim_interval_must_fit_the_half_open_document_interval():
    closed = _document(
        document_id="proposal-v1",
        family="sales.proposal",
        version="v1",
        declared_from=date(2025, 9, 18),
        declared_to=date(2025, 10, 19),
        role=DocumentRole.PROPOSAL,
    )
    revision = _revision(
        closed,
        claim_id="proposal-v1-01",
        statement="The proposal uses a thirty day return window.",
        declared_to=date(2025, 10, 19),
    )
    assert revision.declared_effective_to == date(2025, 10, 19)
    with pytest.raises(ValidationError, match="closed document version"):
        _revision(
            closed,
            claim_id="proposal-v1-02",
            statement="The proposal remains open without a stated end date.",
        )


def test_pair_relation_semantics_and_no_edge_dispositions_are_closed():
    old, new = _versions()
    pair = ComparableClaimPair.create(old, new)
    supersedes = _assessment(pair, PairDisposition.SUPERSEDES)
    contradiction = _assessment(pair, PairDisposition.CONTRADICTS)

    assert ComparableClaimPair.create(new, old) == pair
    assert supersedes.endpoint_ids == (new.claim_revision_id, old.claim_revision_id)
    assert contradiction.endpoint_ids == tuple(sorted(contradiction.endpoint_ids or ()))
    assert _assessment(pair, PairDisposition.COEXISTS).relation_id is None
    assert _assessment(pair, PairDisposition.UNRELATED).relation_id is None

    coexists = _assessment(pair, PairDisposition.COEXISTS)
    payload = coexists.model_dump(mode="json")
    payload.update(
        relation_type="CONTRADICTS",
        relation_id=contradiction.relation_id,
        endpoint_ids=list(contradiction.endpoint_ids or ()),
    )
    with pytest.raises(ValidationError, match="never persisted edges"):
        RelationAssessment.model_validate(payload)


def test_relations_require_shared_scope_and_newer_direction():
    old, new = _versions()
    disjoint = _revision(
        new.document,
        claim_id="returns-v2-02",
        statement="Premium customers pay no restocking fee.",
        scopes=("restocking-fee",),
    )
    pair = ComparableClaimPair.create(old, disjoint)
    with pytest.raises(ValidationError, match="shared semantic scope"):
        _assessment(pair, PairDisposition.SUPERSEDES)

    pair = ComparableClaimPair.create(old, new)
    with pytest.raises(ValidationError, match="newer to older"):
        RelationAssessment.create(
            pair=pair,
            disposition=PairDisposition.SUPERSEDES,
            rationale="This direction is intentionally incorrect.",
            confidence=0.5,
            newer_revision_id=old.claim_revision_id,
        )


def test_relation_graph_rejects_duplicate_pairs_and_cross_pair_binding_conflicts():
    old, new = _versions()
    pair = ComparableClaimPair.create(old, new)
    with pytest.raises(ValidationError, match="exactly one current assessment per pair_id"):
        RelationGraph(
            assessments=(
                _assessment(pair, PairDisposition.SUPERSEDES),
                _assessment(pair, PairDisposition.CONTRADICTS),
            )
        )

    newest_document = _document(
        document_id="returns-v3",
        version="v3",
        declared_from=date(2027, 1, 12),
        sha=SHA_D,
    )
    newest = _revision(
        newest_document,
        claim_id="returns-v3-01",
        statement="Customers may return an item within 60 days of delivery.",
    )
    forged_old = old.model_copy(
        update={"document": old.document.model_copy(update={"source_sha256": SHA_D})}
    )
    first = _assessment(pair, PairDisposition.SUPERSEDES)
    second = _assessment(
        ComparableClaimPair.create(forged_old, newest),
        PairDisposition.SUPERSEDES,
    )
    serialized = sorted((first, second), key=lambda item: item.pair.pair_id)
    with pytest.raises(ValidationError, match="conflicting bindings for claim_revision_id"):
        RelationGraph.model_validate(
            {"assessments": [item.model_dump(mode="json") for item in serialized]}
        )


def test_relation_graph_allows_one_exact_endpoint_binding_across_pairs():
    old, new = _versions()
    newest_document = _document(
        document_id="returns-v3",
        version="v3",
        declared_from=date(2027, 1, 12),
        sha=SHA_D,
    )
    newest = _revision(
        newest_document,
        claim_id="returns-v3-01",
        statement="Customers may return an item within 60 days of delivery.",
    )
    graph = RelationGraph.create(
        (
            _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES),
            _assessment(
                ComparableClaimPair.create(old, newest),
                PairDisposition.SUPERSEDES,
            ),
        )
    )
    assert len(graph.assessments) == 2


def test_relation_graph_factory_is_order_independent_and_direct_rows_are_canonical():
    old, new = _versions()
    newest_document = _document(
        document_id="returns-v3",
        version="v3",
        declared_from=date(2027, 1, 12),
        sha=SHA_D,
    )
    newest = _revision(
        newest_document,
        claim_id="returns-v3-01",
        statement="Customers may return an item within 60 days of delivery.",
    )
    first = _assessment(
        ComparableClaimPair.create(old, new),
        PairDisposition.SUPERSEDES,
    )
    second = _assessment(
        ComparableClaimPair.create(old, newest),
        PairDisposition.SUPERSEDES,
    )
    canonical = RelationGraph.create((first, second, first))
    assert RelationGraph.create((second, first)) == canonical

    reversed_rows = tuple(reversed(canonical.assessments))
    with pytest.raises(ValidationError, match="canonical pair_id order"):
        RelationGraph(assessments=reversed_rows)


def test_dependency_retains_all_exact_spans_and_downstream_claim_revisions():
    upstream, _new = _versions()
    downstream = _document(
        document_id="showroom-process",
        family="operations.showroom-process",
        version="v1",
        declared_from=date(2025, 7, 1),
        sha=SHA_B,
        role=DocumentRole.PROCESS,
    )
    first_span = _span(downstream, "standard 30-day refund window", start=100)
    second_span = _span(downstream, "apply the same window to open-box sales", start=200)
    first_claim = _revision(
        downstream,
        claim_id="showroom-process-01",
        statement="Open-box sales use the standard return window.",
        source=_source(
            downstream,
            "showroom-process-01",
            path=first_span.source_note_path,
            sha=first_span.source_note_sha256,
        ),
    )
    second_claim = _revision(
        downstream,
        claim_id="showroom-process-02",
        statement="Showroom staff apply the policy to demo units.",
        source=_source(
            downstream,
            "showroom-process-02",
            path=first_span.source_note_path,
            sha=first_span.source_note_sha256,
        ),
    )

    assessment = DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        downstream_spans=(second_span, first_span, first_span),
        downstream_claim_revisions=(second_claim, first_claim, first_claim),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The process copies the policy window in two exact locations.",
        confidence=0.9,
    )
    assert set(assessment.downstream_spans) == {first_span, second_span}
    assert set(assessment.downstream_claim_revisions) == {first_claim, second_claim}
    assert tuple(span.start_char for span in assessment.downstream_spans) in {
        (100, 200),
        (200, 100),
    }


def test_dependency_identity_excludes_evidence_aggregate_but_bindings_are_exact():
    upstream, _new = _versions()
    downstream = _document(
        document_id="showroom-process",
        family="operations.showroom-process",
        version="v1",
        declared_from=date(2025, 7, 1),
        role=DocumentRole.PROCESS,
    )
    first = DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        downstream_spans=(_span(downstream, "thirty day window", start=10),),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The process implements the policy window.",
        confidence=0.9,
    )
    corrected = DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        downstream_spans=(
            _span(
                downstream,
                "thirty day window",
                start=20,
                path="operations/sources/moved-showroom.md",
                sha=SHA_D,
            ),
        ),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The process implements the policy window.",
        confidence=0.9,
    )
    assert first.dependency_id == corrected.dependency_id

    wrong_owner = first.downstream_spans[0].model_copy(
        update={"document_version_id": upstream.document.document_version_id}
    )
    with pytest.raises(ValidationError, match="downstream document version"):
        DependencyAssessment.create(
            downstream=downstream,
            upstream=upstream,
            downstream_spans=(wrong_owner,),
            dependency_kind=DependencyKind.IMPLEMENTS,
            rationale="This evidence owner is deliberately wrong.",
            confidence=0.5,
        )


def test_dependency_downstream_claim_must_match_every_span_path_and_full_sha():
    upstream, _new = _versions()
    downstream = _document(
        document_id="returns-faq",
        family="customer-support.returns-faq",
        version="v1",
        declared_from=date(2025, 1, 1),
        role=DocumentRole.FAQ,
    )
    span = _span(
        downstream,
        "Customers have thirty days.",
        start=10,
        path="customer-support/sources/returns-faq.md",
        sha=SHA_C,
    )
    wrong_sha_claim = _revision(
        downstream,
        claim_id="returns-faq-01",
        statement="Customers have thirty days to return an item.",
        source=_source(
            downstream,
            "returns-faq-01",
            path=span.source_note_path,
            sha=SHA_D,
        ),
    )
    with pytest.raises(ValidationError, match="same note path and SHA"):
        DependencyAssessment.create(
            downstream=downstream,
            upstream=upstream,
            downstream_spans=(span,),
            downstream_claim_revisions=(wrong_sha_claim,),
            dependency_kind=DependencyKind.SUMMARIZES,
            rationale="The FAQ summarizes the policy window.",
            confidence=0.8,
        )


def test_dependency_registry_replay_conflict_and_compare_and_swap():
    upstream, _new = _versions()
    downstream = _document(
        document_id="showroom-process",
        family="operations.showroom-process",
        version="v1",
        declared_from=date(2025, 7, 1),
        role=DocumentRole.PROCESS,
    )
    first = DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        downstream_spans=(_span(downstream, "thirty day window", start=10),),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The process implements the policy window.",
        confidence=0.9,
    )
    corrected = DependencyAssessment.create(
        downstream=downstream,
        upstream=upstream,
        downstream_spans=(_span(downstream, "thirty day window", start=20),),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The process implements the policy window.",
        confidence=0.9,
    )
    registry = DependencyRegistry.create((first, first))
    assert registry.assessments == (first,)
    with pytest.raises(ValueError, match="multiple non-identical bindings"):
        DependencyRegistry.create((first, corrected))

    replaced = registry.replace_binding(expected=first, replacement=corrected)
    assert replaced.get(first.dependency_id) == corrected
    with pytest.raises(ValueError, match="expected snapshot"):
        replaced.replace_binding(expected=first, replacement=corrected)


def test_dependency_registry_rejects_cross_role_document_binding_conflicts():
    upstream, _new = _versions()
    shared = _document(
        document_id="shared-process",
        family="operations.shared-process",
        version="v1",
        declared_from=date(2025, 1, 1),
        role=DocumentRole.PROCESS,
    )
    first = DependencyAssessment.create(
        downstream=shared,
        upstream=upstream,
        downstream_spans=(_span(shared, "thirty day window", start=10),),
        dependency_kind=DependencyKind.IMPLEMENTS,
        rationale="The shared process implements the return policy.",
        confidence=0.9,
    )
    conflicting_shared = shared.model_copy(update={"source_sha256": SHA_D})
    conflicting_upstream = _revision(
        conflicting_shared,
        claim_id="shared-process-01",
        statement="The shared process mandates the thirty day window.",
    )
    other_downstream = _document(
        document_id="returns-faq",
        family="customer-support.returns-faq",
        version="v1",
        declared_from=date(2025, 2, 1),
        role=DocumentRole.FAQ,
    )
    second = DependencyAssessment.create(
        downstream=other_downstream,
        upstream=conflicting_upstream,
        downstream_spans=(
            _span(
                other_downstream,
                "the shared process applies",
                start=20,
                path="customer-support/sources/returns-faq.md",
            ),
        ),
        dependency_kind=DependencyKind.SUMMARIZES,
        rationale="The FAQ summarizes the shared process.",
        confidence=0.8,
    )
    serialized = sorted((first, second), key=lambda item: item.dependency_id)
    with pytest.raises(ValidationError, match="conflicting bindings for document_version_id"):
        DependencyRegistry.model_validate(
            {"assessments": [item.model_dump(mode="json") for item in serialized]}
        )


def test_document_replacement_is_explicit_reviewed_and_status_stable():
    old, new = _versions()
    proposed = _replacement(
        old.document,
        new.document,
        status=TemporalConstraintStatus.PROPOSED,
    )
    accepted = proposed.with_status(TemporalConstraintStatus.ACCEPTED)
    assert proposed.relation_id == accepted.relation_id
    assert DocumentReplacementSet.create((proposed, proposed)).assessments == (proposed,)
    with pytest.raises(ValueError, match="conflicting current rows"):
        DocumentReplacementSet.create((proposed, accepted))

    transitioned = DocumentReplacementSet.create((proposed,)).transition_status(
        proposed.relation_id,
        expected_status=TemporalConstraintStatus.PROPOSED,
        new_status=TemporalConstraintStatus.ACCEPTED,
    )
    assert transitioned.assessments == (accepted,)
    with pytest.raises(ValueError, match="expected snapshot"):
        transitioned.transition_status(
            proposed.relation_id,
            expected_status=TemporalConstraintStatus.PROPOSED,
            new_status=TemporalConstraintStatus.REJECTED,
        )


def test_document_replacement_requires_same_family_distinct_version_and_later_start():
    old, new = _versions()
    with pytest.raises(ValidationError, match="distinct document versions"):
        _replacement(old.document, old.document)
    other_family = new.document.model_copy(update={"document_family": "sales.returns-policy"})
    other_family = DocumentVersionMetadata.create(
        document_id=other_family.document_id,
        document_family=other_family.document_family,
        version_label=other_family.version_label,
        source_path=other_family.source_path,
        source_sha256=other_family.source_sha256,
        declared_effective_from=other_family.declared_effective_from,
        role=other_family.role,
        authority=other_family.authority,
    )
    with pytest.raises(ValidationError, match="one document family"):
        _replacement(old.document, other_family)
    with pytest.raises(ValidationError, match="newer to older"):
        _replacement(new.document, old.document)


def test_claim_supersession_can_only_derive_a_claim_constraint():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the claim-level supersession boundary.",
    )
    assert constraint.target == TemporalTarget(
        kind=TemporalTargetKind.CLAIM_REVISION,
        target_id=old.claim_revision_id,
    )
    assert constraint.inferred_valid_to_exclusive == new.declared_effective_from


def test_only_accepted_document_replacement_can_derive_document_constraint():
    old, new = _versions()
    proposed = _replacement(
        old.document,
        new.document,
        status=TemporalConstraintStatus.PROPOSED,
    )
    with pytest.raises(ValueError, match="accepted replacement"):
        TemporalConstraint.from_document_replacement(
            proposed,
            status=TemporalConstraintStatus.PROPOSED,
            rationale="This unreviewed replacement cannot close the document.",
        )
    accepted = proposed.with_status(TemporalConstraintStatus.ACCEPTED)
    constraint = TemporalConstraint.from_document_replacement(
        accepted,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the document replacement boundary.",
    )
    assert constraint.target.target_id == old.document.document_version_id
    assert constraint.inferred_valid_to_exclusive == new.document.declared_effective_from


def test_temporal_constraint_set_replay_conflict_and_status_compare_and_swap():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    proposed = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The inferred boundary awaits review.",
    )
    accepted = proposed.with_status(TemporalConstraintStatus.ACCEPTED)
    assert TemporalConstraintSet.create((proposed, proposed)).constraints == (proposed,)
    with pytest.raises(ValueError, match="conflicting current rows"):
        TemporalConstraintSet.create((proposed, accepted))

    transitioned = TemporalConstraintSet.create((proposed,)).transition_status(
        proposed.constraint_id,
        expected_status=TemporalConstraintStatus.PROPOSED,
        new_status=TemporalConstraintStatus.ACCEPTED,
    )
    assert transitioned.constraints == (accepted,)


def test_accepted_claim_constraint_requires_current_genuine_target_specific_basis():
    old, new = _versions()
    pair = ComparableClaimPair.create(old, new)
    relation = _assessment(pair, PairDisposition.SUPERSEDES)
    accepted = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts this exact supersession boundary.",
    )
    assert _validated(constraints=(accepted,), relations=(relation,)).constraints.constraints

    with pytest.raises(ValidationError, match="absent from current graph"):
        _validated(constraints=(accepted,))

    current_coexists = _assessment(pair, PairDisposition.COEXISTS)
    with pytest.raises(ValidationError, match="absent from current graph"):
        _validated(constraints=(accepted,), relations=(current_coexists,))

    contradiction = _assessment(pair, PairDisposition.CONTRADICTS)
    non_super = _constraint(
        target=accepted.target,
        bound=accepted.inferred_valid_to_exclusive,
        basis_relation_id=contradiction.relation_id or "",
    )
    with pytest.raises(ValidationError, match="requires SUPERSEDES"):
        _validated(constraints=(non_super,), relations=(contradiction,))

    wrong_target = _constraint(
        target=TemporalTarget(
            kind=TemporalTargetKind.CLAIM_REVISION,
            target_id=new.claim_revision_id,
        ),
        bound=accepted.inferred_valid_to_exclusive,
        basis_relation_id=relation.relation_id or "",
    )
    with pytest.raises(ValidationError, match="wrong older endpoint"):
        _validated(constraints=(wrong_target,), relations=(relation,))

    wrong_bound = _constraint(
        target=accepted.target,
        bound=date(2026, 1, 13),
        basis_relation_id=relation.relation_id or "",
    )
    with pytest.raises(ValidationError, match="wrong inferred bound"):
        _validated(constraints=(wrong_bound,), relations=(relation,))


def test_accepted_document_constraint_requires_current_accepted_exact_replacement():
    old, new = _versions()
    replacement = _replacement(old.document, new.document)
    constraint = TemporalConstraint.from_document_replacement(
        replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the document replacement boundary.",
    )
    assert _validated(
        constraints=(constraint,), replacements=(replacement,)
    ).constraints.constraints

    with pytest.raises(ValidationError, match="absent from current set"):
        _validated(constraints=(constraint,))
    with pytest.raises(ValidationError, match="accepted replacement"):
        _validated(
            constraints=(constraint,),
            replacements=(replacement.with_status(TemporalConstraintStatus.REJECTED),),
        )


def test_validated_wrapper_rejects_cross_graph_document_binding_conflicts_from_json():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    conflicting_new = new.document.model_copy(update={"source_sha256": SHA_D})
    replacement = _replacement(old.document, conflicting_new)
    payload = {
        "constraints": {"constraints": []},
        "relation_graph": {"assessments": [relation.model_dump(mode="json")]},
        "document_replacements": {"assessments": [replacement.model_dump(mode="json")]},
    }
    with pytest.raises(ValidationError, match="conflicting bindings for document_version_id"):
        ValidatedTemporalConstraintSet.model_validate(payload)


def test_claim_supersession_does_not_close_the_whole_document():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    claim_constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the claim-level supersession boundary.",
    )
    validated = _validated(constraints=(claim_constraint,), relations=(relation,))

    assert (
        resolve_claim_temporality(old, validated, as_of=date(2026, 1, 12)).state
        == TemporalState.HISTORICAL
    )
    assert (
        resolve_document_temporality(
            old.document,
            validated,
            as_of=date(2026, 1, 12),
        ).state
        == TemporalState.CURRENT
    )


def test_accepted_document_replacement_closes_only_the_exact_old_document():
    old, new = _versions()
    replacement = _replacement(old.document, new.document)
    constraint = TemporalConstraint.from_document_replacement(
        replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the document replacement boundary.",
    )
    validated = _validated(constraints=(constraint,), replacements=(replacement,))

    before = resolve_document_temporality(
        old.document,
        validated,
        as_of=date(2026, 1, 11),
    )
    after = resolve_document_temporality(
        old.document,
        validated,
        as_of=date(2026, 1, 12),
    )
    assert before.state == TemporalState.CURRENT
    assert after.state == TemporalState.HISTORICAL
    assert after.valid_to_exclusive == date(2026, 1, 12)
    assert after.basis_relation_ids == (replacement.relation_id,)
    assert (
        resolve_document_temporality(
            new.document,
            validated,
            as_of=date(2026, 1, 12),
        ).state
        == TemporalState.CURRENT
    )


@pytest.mark.parametrize(
    "status",
    (TemporalConstraintStatus.PROPOSED, TemporalConstraintStatus.REJECTED),
)
def test_unaccepted_fake_constraints_do_not_influence_resolution(
    status: TemporalConstraintStatus,
):
    old, _new = _versions()
    constraint = _constraint(
        target=TemporalTarget(
            kind=TemporalTargetKind.DOCUMENT_VERSION,
            target_id=old.document.document_version_id,
        ),
        bound=date(2026, 1, 12),
        basis_relation_id="rel:" + "e" * 64,
        status=status,
    )
    validated = _validated(constraints=(constraint,))
    resolution = resolve_document_temporality(
        old.document,
        validated,
        as_of=date(2026, 1, 12),
    )
    assert resolution.state == TemporalState.CURRENT
    assert resolution.applied_constraint_ids == ()


def test_resolver_revalidates_model_copy_bypass_and_exact_target_binding():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the exact claim supersession boundary.",
    )
    validated = _validated(constraints=(constraint,), relations=(relation,))
    bypassed = validated.model_copy(update={"relation_graph": RelationGraph(assessments=())})
    with pytest.raises(ValidationError, match="absent from current graph"):
        resolve_claim_temporality(old, bypassed, as_of=date(2026, 1, 12))

    forged_old = old.model_copy(
        update={"source": old.source.model_copy(update={"source_note_sha256": SHA_D})}
    )
    with pytest.raises(ValueError, match="binding differs"):
        resolve_claim_temporality(forged_old, validated, as_of=date(2026, 1, 12))


def test_batch_temporal_context_matches_single_target_resolvers_for_all_states():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the exact claim supersession boundary.",
    )
    historical_constraints = _validated(
        constraints=(constraint,),
        relations=(relation,),
    )
    empty = _validated()
    expiring_document = _document(
        document_id="returns-expiring",
        family="customer-support.expiring-policy",
        version="v1",
        declared_from=date(2024, 1, 1),
        declared_to=date(2025, 1, 15),
    )
    expiring = _revision(
        expiring_document,
        claim_id="returns-expiring-01",
        statement="This temporary returns rule expires on its declared boundary.",
        declared_to=date(2025, 1, 15),
    )

    newest_document = _document(
        document_id="returns-v3",
        version="v3",
        declared_from=date(2026, 2, 1),
        sha=SHA_D,
    )
    newest = _revision(
        newest_document,
        claim_id="returns-v3-01",
        statement="Customers may return an item within 60 days of delivery.",
    )
    second_relation = _assessment(
        ComparableClaimPair.create(old, newest),
        PairDisposition.SUPERSEDES,
    )
    second_constraint = TemporalConstraint.from_supersession(
        second_relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts another exact claim supersession boundary.",
    )
    unresolved_constraints = _validated(
        constraints=(constraint, second_constraint),
        relations=(relation, second_relation),
    )

    claim_cases = (
        (new, empty, date(2026, 1, 20), TemporalState.CURRENT),
        (newest, empty, date(2026, 1, 20), TemporalState.FUTURE),
        (expiring, empty, date(2025, 1, 15), TemporalState.EXPIRED),
        (old, historical_constraints, date(2026, 1, 20), TemporalState.HISTORICAL),
        (old, unresolved_constraints, date(2026, 1, 20), TemporalState.UNRESOLVED),
    )
    for revision, validated, as_of, expected_state in claim_cases:
        context = TemporalResolutionContext(validated, as_of=as_of)
        batch = context.resolve_claim(revision)
        assert batch == resolve_claim_temporality(revision, validated, as_of=as_of)
        assert batch.state == expected_state
        assert context.resolve_claim(revision) is batch
        assert context.resolved_claim_count == 1

    document_cases = (
        (new.document, empty, date(2026, 1, 20), TemporalState.CURRENT),
        (newest.document, empty, date(2026, 1, 20), TemporalState.FUTURE),
        (expiring.document, empty, date(2025, 1, 15), TemporalState.EXPIRED),
    )
    for document, validated, as_of, expected_state in document_cases:
        context = TemporalResolutionContext(validated, as_of=as_of)
        batch = context.resolve_document(document)
        assert batch == resolve_document_temporality(document, validated, as_of=as_of)
        assert batch.state == expected_state
        assert context.resolve_document(document) is batch
        assert context.resolved_document_count == 1


def test_batch_temporal_cache_keys_bind_complete_typed_target_payloads():
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the exact claim supersession boundary.",
    )
    validated = _validated(constraints=(constraint,), relations=(relation,))
    context = TemporalResolutionContext(validated, as_of=date(2026, 1, 12))
    context.resolve_claim(old)

    forged_old = old.model_copy(
        update={"source": old.source.model_copy(update={"source_note_sha256": SHA_D})}
    )
    with pytest.raises(ValueError, match="binding differs"):
        context.resolve_claim(forged_old)
    with pytest.raises(ValueError, match="binding differs"):
        resolve_claim_temporality(forged_old, validated, as_of=date(2026, 1, 12))
    assert context.resolved_claim_count == 1

    replacement = _replacement(old.document, new.document)
    document_constraint = TemporalConstraint.from_document_replacement(
        replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts the exact document replacement boundary.",
    )
    document_validated = _validated(
        constraints=(document_constraint,),
        replacements=(replacement,),
    )
    document_context = TemporalResolutionContext(
        document_validated,
        as_of=date(2026, 1, 12),
    )
    document_context.resolve_document(old.document)
    forged_document = old.document.model_copy(update={"source_sha256": SHA_D})
    with pytest.raises(ValueError, match="binding differs"):
        document_context.resolve_document(forged_document)
    with pytest.raises(ValueError, match="binding differs"):
        resolve_document_temporality(
            forged_document,
            document_validated,
            as_of=date(2026, 1, 12),
        )
    assert document_context.resolved_document_count == 1


def test_irrelevant_constraints_are_indexed_once_and_exact_target_cache_hits(monkeypatch):
    old, _ = _versions()
    irrelevant = tuple(
        _constraint(
            target=TemporalTarget(
                kind=TemporalTargetKind.DOCUMENT_VERSION,
                target_id=f"docv:{index + 1:064x}",
            ),
            bound=date(2026, 1, 12),
            basis_relation_id=f"rel:{index + 1:064x}",
            status=TemporalConstraintStatus.PROPOSED,
        )
        for index in range(64)
    )
    validated = _validated(constraints=irrelevant)
    revalidations = 0
    resolutions = 0
    original_revalidate = change_control_models._revalidate_temporal_constraints
    original_resolve = change_control_models._resolve_temporality

    def counted_revalidate(constraints):
        nonlocal revalidations
        revalidations += 1
        return original_revalidate(constraints)

    def counted_resolve(**kwargs):
        nonlocal resolutions
        resolutions += 1
        return original_resolve(**kwargs)

    monkeypatch.setattr(
        change_control_models,
        "_revalidate_temporal_constraints",
        counted_revalidate,
    )
    monkeypatch.setattr(change_control_models, "_resolve_temporality", counted_resolve)
    context = TemporalResolutionContext(validated, as_of=date(2026, 1, 12))
    first = context.resolve_document(old.document)
    for _ in range(8):
        assert context.resolve_document(old.document) is first
    assert revalidations == 1
    assert resolutions == 1
    assert context.resolved_document_count == 1


def test_two_real_supersession_relations_can_surface_conflicting_bounds():
    old, new = _versions()
    newest_document = _document(
        document_id="returns-v3",
        version="v3",
        declared_from=date(2026, 2, 1),
        sha=SHA_D,
    )
    newest = _revision(
        newest_document,
        claim_id="returns-v3-01",
        statement="Customers may return an item within 60 days of delivery.",
    )
    first_relation = _assessment(
        ComparableClaimPair.create(old, new),
        PairDisposition.SUPERSEDES,
    )
    second_relation = _assessment(
        ComparableClaimPair.create(old, newest),
        PairDisposition.SUPERSEDES,
    )
    first_constraint = TemporalConstraint.from_supersession(
        first_relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepted the first candidate supersession.",
    )
    second_constraint = TemporalConstraint.from_supersession(
        second_relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepted the second candidate supersession.",
    )
    validated = _validated(
        constraints=(first_constraint, second_constraint),
        relations=(first_relation, second_relation),
    )
    resolution = resolve_claim_temporality(old, validated, as_of=date(2026, 1, 20))
    assert resolution.state == TemporalState.UNRESOLVED
    assert "accepted constraints infer different valid-to bounds" in resolution.conflicts


def test_declared_and_inferred_document_bounds_can_surface_conflict():
    old_document = _document(
        document_id="returns-v1",
        version="v1",
        declared_from=date(2024, 1, 15),
        declared_to=date(2026, 1, 10),
    )
    new_document = _document(
        document_id="returns-v2",
        version="v2",
        declared_from=date(2026, 1, 12),
        sha=SHA_B,
    )
    replacement = _replacement(old_document, new_document)
    constraint = TemporalConstraint.from_document_replacement(
        replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review accepts an inferred bound that conflicts with source metadata.",
    )
    resolution = resolve_document_temporality(
        old_document,
        _validated(constraints=(constraint,), replacements=(replacement,)),
        as_of=date(2026, 1, 9),
    )
    assert resolution.state == TemporalState.UNRESOLVED
    assert "declared and inferred valid-to bounds disagree" in resolution.conflicts


def test_temporal_resolver_distinguishes_future_current_and_declared_expiry():
    old, new = _versions()
    empty = _validated()
    assert (
        resolve_document_temporality(new.document, empty, as_of=date(2026, 1, 11)).state
        == TemporalState.FUTURE
    )
    expiring = old.document.model_copy(update={"declared_effective_to": date(2025, 1, 15)})
    assert (
        resolve_document_temporality(expiring, empty, as_of=date(2025, 1, 14)).state
        == TemporalState.CURRENT
    )
    assert (
        resolve_document_temporality(expiring, empty, as_of=date(2025, 1, 15)).state
        == TemporalState.EXPIRED
    )


def test_temporal_resolution_shape_rejects_inconsistent_state():
    old, _new = _versions()
    current = resolve_document_temporality(
        old.document,
        _validated(),
        as_of=date(2026, 1, 11),
    )
    payload = current.model_dump(mode="json")
    payload["state"] = TemporalState.FUTURE.value
    with pytest.raises(ValidationError, match="future temporal state"):
        type(current).model_validate(payload)


@pytest.mark.parametrize(
    "rationale",
    (" Leading whitespace.", "Repeated  internal whitespace.", "\t", "Trailing whitespace. "),
)
def test_review_rationales_reject_noncanonical_whitespace(rationale: str):
    old, new = _versions()
    relation = _assessment(ComparableClaimPair.create(old, new), PairDisposition.SUPERSEDES)
    with pytest.raises((ValueError, ValidationError), match="rationale"):
        TemporalConstraint.from_supersession(
            relation,
            status=TemporalConstraintStatus.PROPOSED,
            rationale=rationale,
        )
    with pytest.raises((ValueError, ValidationError), match="rationale"):
        DocumentReplacementAssessment.create(
            newer_document=new.document,
            older_document=old.document,
            status=TemporalConstraintStatus.PROPOSED,
            rationale=rationale,
            confidence=0.5,
        )


def test_relation_graph_cycle_guard_remains_collection_level():
    documents = [
        _document(
            document_id=f"returns-v{index}",
            version=f"v{index}",
            declared_from=date(2024 + index, 1, 1),
            sha=character * 64,
        )
        for index, character in ((1, "a"), (2, "b"), (3, "c"))
    ]
    revisions = [
        _revision(
            document,
            claim_id=f"returns-v{index}-01",
            statement=f"Version {index} defines a distinct return window.",
        )
        for index, document in enumerate(documents, start=1)
    ]
    assessments: list[RelationAssessment] = []
    for source, target in ((0, 1), (1, 2), (2, 0)):
        pair = ComparableClaimPair.create(revisions[source], revisions[target])
        assessments.append(
            RelationAssessment.model_construct(
                pair=pair,
                disposition=PairDisposition.SUPERSEDES,
                rationale="An untrusted store supplied this edge.",
                confidence=1.0,
                relation_type=PersistedRelationType.SUPERSEDES,
                relation_id="rel:" + f"{source + 1}" * 64,
                endpoint_ids=(
                    revisions[source].claim_revision_id,
                    revisions[target].claim_revision_id,
                ),
            )
        )
    untrusted_graph = RelationGraph.model_construct(
        assessments=tuple(sorted(assessments, key=lambda item: item.pair.pair_id))
    )
    with pytest.raises(ValueError, match="acyclic"):
        untrusted_graph._graph_integrity()  # type: ignore[operator]
