from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.change_control import (
    ChangeControlAggregate,
    ChangeControlBusyError,
    ChangeControlCommit,
    ChangeControlConflictError,
    ChangeControlCorruptionError,
    ChangeControlIdempotencyError,
    ChangeControlReviewAlreadyDecidedError,
    ChangeControlReviewMissingError,
    ChangeControlReviewStaleError,
    ChangeControlReviewTransitionError,
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
    HumanReviewDecisionCommand,
    HumanReviewDecisionReceipt,
    HumanReviewRequestCommand,
    HumanReviewRequestReceipt,
    HumanReviewRequestView,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewLifecycle,
    ReviewSubjectEdit,
    ReviewSubjectKind,
    ReviewSubjectRef,
    ReviewSubjectSnapshot,
    SqliteChangeControlStore,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalTarget,
    TemporalTargetKind,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
    human_review_decision_payload_sha256,
    stable_content_id,
)
from mastervault.change_control import store as store_module
from mastervault.change_control.store import _DEFAULT_MIGRATIONS_DIR
from mastervault.document_intelligence.models import EvidenceRef, StructuralEvidenceRef

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _document(
    document_id: str,
    family: str,
    version: str,
    effective_from: date,
    sha: str,
    *,
    role: DocumentRole = DocumentRole.POLICY,
) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=document_id,
        document_family=family,
        version_label=version,
        source_path=f"datasets/larkstead/raw/{document_id}.md",
        source_sha256=sha,
        declared_effective_from=effective_from,
        role=role,
        authority=DocumentAuthority.PRIMARY,
    )


def _claim(
    document: DocumentVersionMetadata,
    local_id: str,
    statement: str,
    *,
    source_sha: str,
    evidence: tuple[EvidenceRef | StructuralEvidenceRef, ...] = (),
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=f"customer-support/sources/{document.document_id}.md",
            source_note_sha256=source_sha,
            source_claim_id=local_id,
            evidence=evidence,
        ),
        statement=statement,
        declared_effective_from=document.declared_effective_from,
        scopes=("return-window",),
    )


def empty_aggregate(aggregate_id: str = "workspace") -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id=aggregate_id,
        documents=DocumentVersionRegistry.create(()),
        claims=ClaimRevisionRegistry.create(()),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )


def full_aggregate(
    *, relation_rationale: str = "The newer policy replaces the old rule."
) -> ChangeControlAggregate:
    old_doc = _document(
        "returns-v1",
        "customer-support.returns-policy",
        "v1",
        date(2024, 1, 15),
        SHA_A,
    )
    new_doc = _document(
        "returns-v2",
        "customer-support.returns-policy",
        "v2",
        date(2026, 1, 12),
        SHA_B,
    )
    downstream_doc = _document(
        "returns-process",
        "customer-support.returns-process",
        "v1",
        date(2025, 1, 1),
        SHA_C,
        role=DocumentRole.PROCESS,
    )
    evidence_v1 = EvidenceRef(
        asset_sha256=SHA_A,
        page_number=1,
        block_id="page-0001-block-0001",
        quote="within 30 days",
        start_char=10,
        end_char=24,
    )
    old_claim = _claim(
        old_doc,
        "returns-v1-01",
        "Customers may return an item within 30 days of delivery.",
        source_sha=SHA_D,
        evidence=(evidence_v1,),
    )
    new_claim = _claim(
        new_doc,
        "returns-v2-01",
        "Customers may return an item within 45 days of delivery.",
        source_sha=SHA_C,
    )
    downstream_claim = _claim(
        downstream_doc,
        "returns-process-01",
        "Agents quote the current return window when approving a return.",
        source_sha=SHA_B,
    )
    pair = ComparableClaimPair.create(old_claim, new_claim)
    relation = RelationAssessment.create(
        pair=pair,
        disposition=PairDisposition.SUPERSEDES,
        rationale=relation_rationale,
        confidence=0.97,
        newer_revision_id=new_claim.claim_revision_id,
    )
    replacement = DocumentReplacementAssessment.create(
        newer_document=new_doc,
        older_document=old_doc,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="Review confirms replacement of the whole policy version.",
        confidence=0.96,
    )
    span_quote = "quote the current return window"
    structural = StructuralEvidenceRef(
        target_type="block",
        asset_sha256=SHA_C,
        page_number=1,
        block_id="block-0001",
        bbox={"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.3},
        quote=span_quote,
        start_char=0,
        end_char=len(span_quote),
    )
    span = DocumentSpanReference(
        document_version_id=downstream_doc.document_version_id,
        source_note_path=downstream_claim.source.source_note_path,
        source_note_sha256=downstream_claim.source.source_note_sha256,
        quote=span_quote,
        start_char=100,
        end_char=100 + len(span_quote),
        evidence=(structural,),
    )
    dependency = DependencyAssessment.create(
        downstream=downstream_doc,
        upstream=new_claim,
        dependency_kind=DependencyKind.QUOTES,
        downstream_spans=(span,),
        downstream_claim_revisions=(downstream_claim,),
        rationale="The process quotes the upstream policy rule.",
        confidence=0.91,
    )
    claim_constraint = TemporalConstraint.from_supersession(
        relation,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="The accepted supersession closes the older claim.",
    )
    document_constraint = TemporalConstraint.from_document_replacement(
        replacement,
        status=TemporalConstraintStatus.ACCEPTED,
        rationale="The accepted replacement closes the older document.",
    )
    return ChangeControlAggregate.create(
        aggregate_id="workspace",
        documents=DocumentVersionRegistry.create((old_doc, new_doc, downstream_doc)),
        claims=ClaimRevisionRegistry.create((old_claim, new_claim, downstream_claim)),
        relation_graph=RelationGraph.create((relation,)),
        dependencies=DependencyRegistry.create((dependency,)),
        document_replacements=DocumentReplacementSet.create((replacement,)),
        temporal_constraints=TemporalConstraintSet.create((claim_constraint, document_constraint)),
    )


def proposed_full_aggregate(
    *, relation_rationale: str = "The newer policy replaces the old rule."
) -> ChangeControlAggregate:
    aggregate = full_aggregate(relation_rationale=relation_rationale)
    return ChangeControlAggregate.create(
        aggregate_id=aggregate.aggregate_id,
        documents=aggregate.documents,
        claims=aggregate.claims,
        relation_graph=aggregate.relation_graph,
        dependencies=aggregate.dependencies,
        document_replacements=DocumentReplacementSet.create(
            tuple(
                item.with_status(TemporalConstraintStatus.PROPOSED)
                for item in aggregate.document_replacements.assessments
            )
        ),
        temporal_constraints=TemporalConstraintSet.create(
            tuple(
                item.with_status(TemporalConstraintStatus.PROPOSED)
                for item in aggregate.temporal_constraints.constraints
            )
        ),
    )


def _replace_review_subjects(
    aggregate: ChangeControlAggregate,
    *,
    replacements: DocumentReplacementSet | None = None,
    constraints: TemporalConstraintSet | None = None,
    relation_graph: RelationGraph | None = None,
) -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id=aggregate.aggregate_id,
        documents=aggregate.documents,
        claims=aggregate.claims,
        relation_graph=relation_graph or aggregate.relation_graph,
        dependencies=aggregate.dependencies,
        document_replacements=replacements or aggregate.document_replacements,
        temporal_constraints=constraints or aggregate.temporal_constraints,
    )


def _review_refs(
    aggregate: ChangeControlAggregate,
    *,
    include_replacement: bool = True,
    constraint_kind: TemporalTargetKind | None = None,
) -> tuple[ReviewSubjectRef, ...]:
    refs: list[ReviewSubjectRef] = []
    if include_replacement:
        refs.extend(
            ReviewSubjectRef(
                kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
                subject_id=item.relation_id,
            )
            for item in aggregate.document_replacements.assessments
        )
    refs.extend(
        ReviewSubjectRef(
            kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
            subject_id=item.constraint_id,
        )
        for item in aggregate.temporal_constraints.constraints
        if constraint_kind is None or item.target.kind == constraint_kind
    )
    return tuple(refs)


def _request_review(
    store: SqliteChangeControlStore,
    aggregate: ChangeControlAggregate,
    *,
    operation_id: str,
    refs: tuple[ReviewSubjectRef, ...] | None = None,
) -> HumanReviewRequestReceipt:
    snapshot = store.load(aggregate.aggregate_id)
    assert snapshot is not None
    return store.create_review_request(
        HumanReviewRequestCommand(
            aggregate_id=aggregate.aggregate_id,
            expected_revision=snapshot.revision,
            expected_aggregate_sha256=snapshot.aggregate_sha256,
            subjects=refs or _review_refs(aggregate),
            requester_id="operator@example.com",
            rationale="Review the proposed temporal changes as one authoritative batch.",
        ),
        operation_id=operation_id,
    )


def _decision_item(
    snapshot,
    disposition: ReviewDisposition,
    *,
    edit: ReviewSubjectEdit | None = None,
) -> ReviewDecisionItem:
    return ReviewDecisionItem(
        kind=snapshot.kind,
        subject_id=snapshot.subject_id,
        original_subject_sha256=snapshot.subject_sha256,
        disposition=disposition,
        edit=edit,
    )


def audit_coverage_aggregate() -> ChangeControlAggregate:
    base = proposed_full_aggregate()
    extra_document = _document(
        "shipping-faq",
        "customer-support.shipping-faq",
        "v1",
        date(2025, 6, 1),
        "e" * 64,
        role=DocumentRole.FAQ,
    )
    extra_claim = _claim(
        extra_document,
        "shipping-faq-01",
        "Shipping questions do not change the customer return window.",
        source_sha="f" * 64,
    )
    old_claim = next(
        item for item in base.claims.revisions if item.document.document_id == "returns-v1"
    )
    unrelated = RelationAssessment.create(
        pair=ComparableClaimPair.create(old_claim, extra_claim),
        disposition=PairDisposition.UNRELATED,
        rationale="The shipping guidance is unrelated to the policy duration.",
        confidence=0.88,
    )

    stale_constraints: list[TemporalConstraint] = []
    for target, bound, basis, status, rationale in (
        (
            TemporalTarget(
                kind=TemporalTargetKind.CLAIM_REVISION,
                target_id=extra_claim.claim_revision_id,
            ),
            date(2027, 1, 1),
            "rel:" + "1" * 64,
            TemporalConstraintStatus.PROPOSED,
            "The proposal may retain a basis that is no longer current.",
        ),
        (
            TemporalTarget(
                kind=TemporalTargetKind.DOCUMENT_VERSION,
                target_id=extra_document.document_version_id,
            ),
            date(2028, 1, 1),
            "rel:" + "2" * 64,
            TemporalConstraintStatus.PROPOSED,
            "A second proposal may retain a basis that is no longer current.",
        ),
    ):
        payload = {
            "namespace": "mastervault.temporal-constraint.v1",
            "resolver_version": "temporal-resolution-v1",
            "target": target.model_dump(mode="json"),
            "inferred_valid_to_exclusive": bound.isoformat(),
            "basis_relation_ids": [basis],
        }
        stale_constraints.append(
            TemporalConstraint(
                constraint_id=stable_content_id("tempc", payload),
                target=target,
                inferred_valid_to_exclusive=bound,
                basis_relation_ids=(basis,),
                status=status,
                rationale=rationale,
            )
        )
    return ChangeControlAggregate.create(
        aggregate_id="workspace",
        documents=DocumentVersionRegistry.create((*base.documents.documents, extra_document)),
        claims=ClaimRevisionRegistry.create((*base.claims.revisions, extra_claim)),
        relation_graph=RelationGraph.create((*base.relation_graph.assessments, unrelated)),
        dependencies=base.dependencies,
        document_replacements=base.document_replacements,
        temporal_constraints=TemporalConstraintSet.create(
            (*base.temporal_constraints.constraints, *stale_constraints)
        ),
    )


def _store(path: Path) -> SqliteChangeControlStore:
    store = SqliteChangeControlStore(path)
    store.init_schema()
    return store


def test_aggregate_rejects_unpropagated_document_binding() -> None:
    aggregate = full_aggregate()
    original = aggregate.documents.documents[0]
    corrected = original.model_copy(update={"source_sha256": "f" * 64})
    with pytest.raises(ValidationError, match="differs from document root"):
        ChangeControlAggregate.create(
            aggregate_id="workspace",
            documents=DocumentVersionRegistry.create(
                tuple(
                    corrected if item.document_version_id == original.document_version_id else item
                    for item in aggregate.documents.documents
                )
            ),
            claims=aggregate.claims,
            relation_graph=aggregate.relation_graph,
            dependencies=aggregate.dependencies,
            document_replacements=aggregate.document_replacements,
            temporal_constraints=aggregate.temporal_constraints,
        )


def test_save_revalidates_an_unchecked_model_copy_before_opening_a_transaction(
    tmp_path: Path,
) -> None:
    aggregate = full_aggregate()
    original = aggregate.documents.documents[0]
    corrected = original.model_copy(update={"source_sha256": "f" * 64})
    invalid = aggregate.model_copy(
        update={
            "documents": DocumentVersionRegistry.create(
                tuple(
                    corrected if item.document_version_id == original.document_version_id else item
                    for item in aggregate.documents.documents
                )
            )
        }
    )
    store = _store(tmp_path / "state.sqlite3")
    with pytest.raises(ValidationError, match="differs from document root"):
        store.create(invalid, operation_id="invalid")
    assert not store.conn.in_transaction
    assert store.load("workspace") is None
    store.close()


def test_aggregate_rejects_absent_claim_root_and_temporal_target() -> None:
    aggregate = full_aggregate()
    with pytest.raises(ValidationError, match="absent claim root"):
        ChangeControlAggregate.create(
            aggregate_id="workspace",
            documents=aggregate.documents,
            claims=ClaimRevisionRegistry.create(aggregate.claims.revisions[1:]),
            relation_graph=aggregate.relation_graph,
            dependencies=aggregate.dependencies,
            document_replacements=aggregate.document_replacements,
            temporal_constraints=aggregate.temporal_constraints,
        )

    target = TemporalTarget(
        kind=TemporalTargetKind.DOCUMENT_VERSION,
        target_id="docv:" + "0" * 64,
    )
    identity_payload = {
        "namespace": "mastervault.temporal-constraint.v1",
        "resolver_version": "temporal-resolution-v1",
        "target": target.model_dump(mode="json"),
        "inferred_valid_to_exclusive": "2026-01-01",
        "basis_relation_ids": ["rel:" + "0" * 64],
    }
    orphan = TemporalConstraint(
        constraint_id=stable_content_id("tempc", identity_payload),
        target=target,
        inferred_valid_to_exclusive=date(2026, 1, 1),
        basis_relation_ids=("rel:" + "0" * 64,),
        status=TemporalConstraintStatus.REJECTED,
        rationale="The rejected row still requires a live target root.",
    )
    with pytest.raises(ValidationError, match="absent document target"):
        ChangeControlAggregate.create(
            aggregate_id="workspace",
            documents=DocumentVersionRegistry.create(()),
            claims=ClaimRevisionRegistry.create(()),
            relation_graph=RelationGraph.create(()),
            dependencies=DependencyRegistry.create(()),
            document_replacements=DocumentReplacementSet.create(()),
            temporal_constraints=TemporalConstraintSet.create((orphan,)),
        )


def test_empty_and_full_round_trip_reopen_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "change_control" / "state.sqlite3"
    store = _store(path)
    empty = empty_aggregate()
    created = store.create(empty, operation_id="create-empty")
    assert created.revision == 1
    assert store.load("workspace") == store.load("workspace")
    full = proposed_full_aggregate()
    updated = store.compare_and_swap(full, expected_revision=1, operation_id="load-full")
    assert updated.revision == 2
    assert store.load("workspace").aggregate == full  # type: ignore[union-attr]
    store.close()

    reopened = _store(path)
    snapshot = reopened.load("workspace")
    assert snapshot is not None
    assert snapshot.aggregate == full
    assert snapshot.aggregate_sha256 == aggregate_sha256(full)
    reopened.close()


def test_no_edge_and_nonaccepted_stale_constraints_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    aggregate = audit_coverage_aggregate()
    store.create(aggregate, operation_id="audit-coverage")
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.aggregate == aggregate
    assert {item.disposition for item in snapshot.aggregate.relation_graph.assessments} == {
        PairDisposition.SUPERSEDES,
        PairDisposition.UNRELATED,
    }
    assert {item.status for item in snapshot.aggregate.temporal_constraints.constraints} == {
        TemporalConstraintStatus.PROPOSED,
    }
    store.close()


def test_multiple_normalized_aggregate_ids_are_independent(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    store.create(empty_aggregate("workspace"), operation_id="workspace-create")
    store.create(empty_aggregate("evaluation.run-1"), operation_id="evaluation-create")
    assert store.load("workspace").revision == 1  # type: ignore[union-attr]
    assert store.load("evaluation.run-1").aggregate.aggregate_id == "evaluation.run-1"  # type: ignore[union-attr]
    store.close()


def test_noop_replay_idempotency_conflict_and_stale_cas(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    aggregate = proposed_full_aggregate()
    first = store.create(aggregate, operation_id="first")
    noop = store.compare_and_swap(aggregate, expected_revision=1, operation_id="noop")
    assert not noop.changed and noop.revision == 1
    replay = store.compare_and_swap(aggregate, expected_revision=1, operation_id="noop")
    assert replay.replayed and replay == noop.__class__(**{**noop.__dict__, "replayed": True})
    with pytest.raises(ChangeControlIdempotencyError):
        store.compare_and_swap(
            proposed_full_aggregate(relation_rationale="A different reviewed rationale."),
            expected_revision=1,
            operation_id="noop",
        )
    assert first.revision == 1
    with pytest.raises(ChangeControlConflictError):
        store.compare_and_swap(aggregate, expected_revision=None, operation_id="stale")
    assert store.load("workspace").revision == 1  # type: ignore[union-attr]
    store.close()


def test_historical_receipt_replay_survives_replacements_and_preserves_timestamp(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "state.sqlite3")
    created = store.create(empty_aggregate(), operation_id="create-lost-ack")
    changed = store.compare_and_swap(
        proposed_full_aggregate(), expected_revision=1, operation_id="change-lost-ack"
    )
    store.compare_and_swap(
        proposed_full_aggregate(relation_rationale="A later reviewed explanation."),
        expected_revision=2,
        operation_id="later-change",
    )

    create_replay = store.create(empty_aggregate(), operation_id="create-lost-ack")
    change_replay = store.compare_and_swap(
        proposed_full_aggregate(), expected_revision=1, operation_id="change-lost-ack"
    )
    assert create_replay.replayed and create_replay.revision == 1
    assert create_replay.committed_at == created.committed_at
    assert change_replay.replayed and change_replay.revision == 2
    assert change_replay.committed_at == changed.committed_at
    assert store.conn.execute("SELECT count(*) FROM change_control_operations").fetchone()[0] == 3
    store.close()


def test_review_accepts_replacement_and_dependent_document_constraint_atomically(
    tmp_path: Path,
) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(
        store,
        aggregate,
        operation_id="request-coupled",
        refs=_review_refs(
            aggregate,
            constraint_kind=TemporalTargetKind.DOCUMENT_VERSION,
        ),
    )
    receipt = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="The replacement and its derived bound are jointly supported.",
            items=tuple(
                _decision_item(item, ReviewDisposition.ACCEPTED)
                for item in request.request.subjects
            ),
        ),
        operation_id="decision-coupled",
    )
    assert receipt.aggregate_revision == 2 and not receipt.replayed
    current = store.load("workspace")
    assert current is not None and current.revision == 2
    assert current.aggregate.document_replacements.assessments[0].status == (
        TemporalConstraintStatus.ACCEPTED
    )
    document_constraint = next(
        item
        for item in current.aggregate.temporal_constraints.constraints
        if item.target.kind == TemporalTargetKind.DOCUMENT_VERSION
    )
    assert document_constraint.status == TemporalConstraintStatus.ACCEPTED
    view = store.get_review_request(request.request.request_id)
    assert view.lifecycle == ReviewLifecycle.DECIDED and view.decision == receipt.decision
    store.close()


def test_review_rejects_constraint_only_acceptance_and_rolls_back(tmp_path: Path) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    seeded = store.create(aggregate, operation_id="seed")
    request = _request_review(
        store,
        aggregate,
        operation_id="request-constraint-only",
        refs=_review_refs(
            aggregate,
            include_replacement=False,
            constraint_kind=TemporalTargetKind.DOCUMENT_VERSION,
        ),
    )
    with pytest.raises(ChangeControlReviewTransitionError, match="valid final aggregate"):
        store.decide_review(
            HumanReviewDecisionCommand(
                request_id=request.request.request_id,
                reviewer_id="reviewer@example.com",
                rationale="This cannot outrun the proposed replacement basis.",
                items=(_decision_item(request.request.subjects[0], ReviewDisposition.ACCEPTED),),
            ),
            operation_id="invalid-constraint-decision",
        )
    current = store.load("workspace")
    assert current is not None and current.revision == seeded.revision
    assert current.aggregate == aggregate
    assert store.get_review_request(request.request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_operations "
            "WHERE operation_id='invalid-constraint-decision'"
        ).fetchone()[0]
        == 0
    )
    store.close()


def test_review_mixed_accepted_edited_rejected_outcomes_and_exact_replay(
    tmp_path: Path,
) -> None:
    aggregate = audit_coverage_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(
        store,
        aggregate,
        operation_id="request-mixed",
        refs=_review_refs(proposed_full_aggregate()),
    )
    items: list[ReviewDecisionItem] = []
    for subject in request.request.subjects:
        if subject.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT:
            items.append(_decision_item(subject, ReviewDisposition.ACCEPTED))
        elif subject.subject.target.kind == TemporalTargetKind.CLAIM_REVISION:
            items.append(
                _decision_item(
                    subject,
                    ReviewDisposition.EDITED,
                    edit=ReviewSubjectEdit(
                        kind=subject.kind,
                        subject_id=subject.subject_id,
                        rationale="The reviewer clarified the claim closure rationale.",
                    ),
                )
            )
        else:
            items.append(_decision_item(subject, ReviewDisposition.REJECTED))
    command = HumanReviewDecisionCommand(
        request_id=request.request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="Each proposed subject received its own explicit outcome.",
        items=tuple(items),
    )
    first = store.decide_review(command, operation_id="decision-mixed")
    requested_keys = {(item.kind, item.subject_id) for item in request.request.subjects}
    remaining_refs = tuple(
        item
        for item in _review_refs(first.decision.decided_aggregate)
        if (item.kind, item.subject_id) not in requested_keys
    )
    later_request = _request_review(
        store,
        first.decision.decided_aggregate,
        operation_id="request-later",
        refs=remaining_refs,
    )
    store.decide_review(
        HumanReviewDecisionCommand(
            request_id=later_request.request.request_id,
            reviewer_id="later-reviewer@example.com",
            rationale="A later decision advances the aggregate before replay.",
            items=tuple(
                _decision_item(item, ReviewDisposition.REJECTED)
                for item in later_request.request.subjects
            ),
        ),
        operation_id="decision-later",
    )
    replay = store.decide_review(command, operation_id="decision-mixed")
    assert replay.replayed and replay.aggregate_revision == 2
    assert store.load("workspace").revision == 3  # type: ignore[union-attr]
    assert replay.decision.decided_at == first.decision.decided_at
    decided = first.decision.decided_aggregate
    assert decided.document_replacements.assessments[0].status == (
        TemporalConstraintStatus.ACCEPTED
    )
    requested_temporal = {
        item.subject_id: item
        for item in request.request.subjects
        if item.kind == ReviewSubjectKind.TEMPORAL_CONSTRAINT
    }
    statuses = {
        item.constraint_id: item.status for item in decided.temporal_constraints.constraints
    }
    for subject_id, subject in requested_temporal.items():
        expected = (
            TemporalConstraintStatus.ACCEPTED
            if subject.subject.target.kind == TemporalTargetKind.CLAIM_REVISION
            else TemporalConstraintStatus.REJECTED
        )
        assert statuses[subject_id] == expected
    store.close()


def test_review_request_replay_staleness_and_open_subject_guard(tmp_path: Path) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(
        store,
        aggregate,
        operation_id="request-replay",
        refs=_review_refs(
            aggregate,
            include_replacement=False,
            constraint_kind=TemporalTargetKind.CLAIM_REVISION,
        ),
    )
    replay = _request_review(
        store,
        aggregate,
        operation_id="request-replay",
        refs=_review_refs(
            aggregate,
            include_replacement=False,
            constraint_kind=TemporalTargetKind.CLAIM_REVISION,
        ),
    )
    assert replay.replayed and replay.request.requested_at == request.request.requested_at

    bound = request.request.subjects[0].subject
    modified_constraint = TemporalConstraint.model_validate(
        {**bound.model_dump(mode="json"), "rationale": "A bypassed rationale edit."}
    )
    modified = _replace_review_subjects(
        aggregate,
        constraints=TemporalConstraintSet.create(
            tuple(
                modified_constraint if item.constraint_id == bound.constraint_id else item
                for item in aggregate.temporal_constraints.constraints
            )
        ),
    )
    with pytest.raises(ChangeControlReviewTransitionError, match="open review subject"):
        store.compare_and_swap(modified, expected_revision=1, operation_id="open-bypass")

    unrelated = _replace_review_subjects(
        aggregate,
        relation_graph=RelationGraph.create(
            tuple(
                RelationAssessment.create(
                    pair=item.pair,
                    disposition=item.disposition,
                    rationale="An unrelated relation explanation changed.",
                    confidence=item.confidence,
                    newer_revision_id=item.endpoint_ids[0] if item.endpoint_ids else None,
                )
                for item in aggregate.relation_graph.assessments
            )
        ),
    )
    store.compare_and_swap(unrelated, expected_revision=1, operation_id="unrelated-cas")
    assert store.get_review_request(request.request.request_id).lifecycle == ReviewLifecycle.STALE
    with pytest.raises(ChangeControlReviewStaleError):
        store.decide_review(
            HumanReviewDecisionCommand(
                request_id=request.request.request_id,
                reviewer_id="reviewer@example.com",
                rationale="This request is stale after the unrelated revision.",
                items=tuple(
                    _decision_item(item, ReviewDisposition.ACCEPTED)
                    for item in request.request.subjects
                ),
            ),
            operation_id="stale-decision",
        )
    store.close()


def test_review_request_rejects_absent_nonproposed_and_overlapping_subjects(
    tmp_path: Path,
) -> None:
    proposed = proposed_full_aggregate()
    store = _store(tmp_path / "negative.sqlite3")
    store.create(proposed, operation_id="seed")
    digest = aggregate_sha256(proposed)
    with pytest.raises(ChangeControlReviewMissingError, match="subject does not exist"):
        store.create_review_request(
            HumanReviewRequestCommand(
                aggregate_id="workspace",
                expected_revision=1,
                expected_aggregate_sha256=digest,
                subjects=(
                    ReviewSubjectRef(
                        kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
                        subject_id="tempc:" + "0" * 64,
                    ),
                ),
                requester_id="operator@example.com",
                rationale="An absent subject cannot enter authoritative review.",
            ),
            operation_id="request-absent",
        )
    first_ref = _review_refs(
        proposed,
        include_replacement=False,
        constraint_kind=TemporalTargetKind.CLAIM_REVISION,
    )
    _request_review(
        store,
        proposed,
        operation_id="request-overlap-first",
        refs=first_ref,
    )
    with pytest.raises(ChangeControlReviewTransitionError, match="overlaps"):
        store.create_review_request(
            HumanReviewRequestCommand(
                aggregate_id="workspace",
                expected_revision=1,
                expected_aggregate_sha256=digest,
                subjects=tuple((*first_ref, _review_refs(proposed)[0])),
                requester_id="operator@example.com",
                rationale="A different batch still cannot overlap the open subject.",
            ),
            operation_id="request-overlap-second",
        )
    store.close()

    reviewed = proposed_full_aggregate()
    store = _store(tmp_path / "nonproposed.sqlite3")
    store.create(reviewed, operation_id="seed-reviewed")
    first_review = _request_review(store, reviewed, operation_id="request-to-review")
    reviewed = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=first_review.request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="Accept subjects before testing non-proposed request rejection.",
            items=tuple(
                _decision_item(item, ReviewDisposition.ACCEPTED)
                for item in first_review.request.subjects
            ),
        ),
        operation_id="decision-to-review",
    ).decision.decided_aggregate
    with pytest.raises(ChangeControlReviewTransitionError, match="only proposed"):
        _request_review(store, reviewed, operation_id="request-reviewed")
    store.close()


def test_two_exact_review_requests_race_to_create_then_replay(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    aggregate = proposed_full_aggregate()
    owner = _store(path)
    owner.create(aggregate, operation_id="seed")
    snapshot = owner.load("workspace")
    assert snapshot is not None
    command = HumanReviewRequestCommand(
        aggregate_id="workspace",
        expected_revision=1,
        expected_aggregate_sha256=snapshot.aggregate_sha256,
        subjects=_review_refs(aggregate),
        requester_id="operator@example.com",
        rationale="Concurrent exact creation must converge on one request.",
    )
    owner.close()

    def create_exact() -> bool:
        contender = _store(path)
        try:
            return contender.create_review_request(
                command, operation_id="request-race-exact"
            ).replayed
        finally:
            contender.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        replayed = list(pool.map(lambda _index: create_exact(), (1, 2)))
    assert sorted(replayed) == [False, True]
    final = _store(path)
    assert (
        final.conn.execute("SELECT count(*) FROM change_control_review_requests").fetchone()[0] == 1
    )
    assert (
        final.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='request-race-exact'"
        ).fetchone()[0]
        == 1
    )
    final.close()


def test_review_request_operation_is_cross_aggregate_global_and_lock_typed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    workspace = proposed_full_aggregate()
    evaluation = ChangeControlAggregate.create(
        aggregate_id="evaluation.run-2",
        documents=workspace.documents,
        claims=workspace.claims,
        relation_graph=workspace.relation_graph,
        dependencies=workspace.dependencies,
        document_replacements=workspace.document_replacements,
        temporal_constraints=workspace.temporal_constraints,
    )
    owner = _store(path)
    owner.create(workspace, operation_id="seed-workspace")
    owner.create(evaluation, operation_id="seed-evaluation")
    _request_review(owner, workspace, operation_id="request-cross-aggregate")
    evaluation_snapshot = owner.load(evaluation.aggregate_id)
    assert evaluation_snapshot is not None
    evaluation_command = HumanReviewRequestCommand(
        aggregate_id=evaluation.aggregate_id,
        expected_revision=1,
        expected_aggregate_sha256=evaluation_snapshot.aggregate_sha256,
        subjects=_review_refs(evaluation),
        requester_id="operator@example.com",
        rationale="The operation ID cannot be rebound to another aggregate.",
    )
    with pytest.raises(ChangeControlIdempotencyError, match="different inputs"):
        owner.create_review_request(evaluation_command, operation_id="request-cross-aggregate")

    contender = SqliteChangeControlStore(path, timeout_seconds=0)
    owner.conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ChangeControlBusyError):
            contender.create_review_request(evaluation_command, operation_id="request-while-locked")
    finally:
        owner.conn.execute("ROLLBACK")
        contender.close()
        owner.close()

    final = _store(path)
    assert (
        final.conn.execute(
            "SELECT count(*) FROM change_control_operations "
            "WHERE operation_id='request-while-locked'"
        ).fetchone()[0]
        == 0
    )
    final.close()


def test_public_cas_cannot_create_transition_or_mutate_reviewed_state(tmp_path: Path) -> None:
    proposed = proposed_full_aggregate()
    accepted_replacement = proposed.document_replacements.assessments[0].with_status(
        TemporalConstraintStatus.ACCEPTED
    )
    transitioned = _replace_review_subjects(
        proposed,
        replacements=DocumentReplacementSet.create((accepted_replacement,)),
    )
    store = _store(tmp_path / "transition.sqlite3")
    store.create(proposed, operation_id="seed")
    with pytest.raises(ChangeControlReviewTransitionError, match="authoritative decision"):
        store.compare_and_swap(transitioned, expected_revision=1, operation_id="bypass-transition")
    store.close()

    store = _store(tmp_path / "immutable.sqlite3")
    store.create(proposed, operation_id="seed-reviewed")
    reviewed_request = _request_review(store, proposed, operation_id="request-reviewed")
    reviewed = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=reviewed_request.request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="Accept all subjects before testing generic immutability.",
            items=tuple(
                _decision_item(item, ReviewDisposition.ACCEPTED)
                for item in reviewed_request.request.subjects
            ),
        ),
        operation_id="decision-reviewed",
    ).decision.decided_aggregate
    current = reviewed.document_replacements.assessments[0]
    mutated = DocumentReplacementAssessment.model_validate(
        {
            **current.model_dump(mode="json"),
            "rationale": "An ordinary CAS must not rewrite reviewed rationale.",
        }
    )
    with pytest.raises(ChangeControlReviewTransitionError, match="authoritative decision"):
        store.compare_and_swap(
            _replace_review_subjects(
                reviewed,
                replacements=DocumentReplacementSet.create((mutated,)),
            ),
            expected_revision=2,
            operation_id="bypass-mutation",
        )
    store.close()

    without_reviewed = _replace_review_subjects(
        proposed,
        replacements=DocumentReplacementSet.create(()),
        constraints=TemporalConstraintSet.create(()),
    )
    store = _store(tmp_path / "addition.sqlite3")
    store.create(without_reviewed, operation_id="seed-without")
    with pytest.raises(ChangeControlReviewTransitionError, match="authoritative decision"):
        store.compare_and_swap(reviewed, expected_revision=1, operation_id="bypass-addition")
    store.close()


def test_public_cas_preserves_unchanged_reviewed_state_and_audit_history(tmp_path: Path) -> None:
    proposed = proposed_full_aggregate()
    store = _store(tmp_path / "reviewed-cas.sqlite3")
    store.create(proposed, operation_id="seed-reviewed-cas")
    request = _request_review(store, proposed, operation_id="request-reviewed-cas")
    reviewed = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="Accept all subjects before exercising ordinary aggregate writes.",
            items=tuple(
                _decision_item(item, ReviewDisposition.ACCEPTED)
                for item in request.request.subjects
            ),
        ),
        operation_id="decision-reviewed-cas",
    ).decision.decided_aggregate
    audit_before = store.get_review_request(request.request.request_id)

    noop = store.compare_and_swap(
        reviewed,
        expected_revision=2,
        operation_id="noop-after-review",
    )
    assert not noop.changed and not noop.replayed and noop.revision == 2
    replay = store.compare_and_swap(
        reviewed,
        expected_revision=2,
        operation_id="noop-after-review",
    )
    assert replay.replayed and not replay.changed and replay.revision == noop.revision
    assert replay.committed_at == noop.committed_at
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.aggregate == reviewed and snapshot.revision == 2
    assert store.get_review_request(request.request.request_id) == audit_before

    unrelated = _replace_review_subjects(
        reviewed,
        relation_graph=RelationGraph.create(
            tuple(
                RelationAssessment.create(
                    pair=item.pair,
                    disposition=item.disposition,
                    rationale="An unrelated relation explanation changed after review.",
                    confidence=item.confidence,
                    newer_revision_id=item.endpoint_ids[0] if item.endpoint_ids else None,
                )
                for item in reviewed.relation_graph.assessments
            )
        ),
    )
    changed = store.compare_and_swap(
        unrelated,
        expected_revision=2,
        operation_id="unrelated-after-review",
    )
    assert changed.changed and changed.revision == 3
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.aggregate == unrelated and snapshot.revision == 3
    assert snapshot.aggregate.document_replacements == reviewed.document_replacements
    assert snapshot.aggregate.temporal_constraints == reviewed.temporal_constraints
    assert store.get_review_request(request.request.request_id) == audit_before
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 1
    )
    store.close()


@pytest.mark.parametrize(
    "reviewed_status",
    [TemporalConstraintStatus.ACCEPTED, TemporalConstraintStatus.REJECTED],
)
@pytest.mark.parametrize("write_kind", ["create", "compare-and-swap"])
def test_generic_writes_reject_initial_reviewed_state_atomically(
    tmp_path: Path,
    reviewed_status: TemporalConstraintStatus,
    write_kind: str,
) -> None:
    proposed = proposed_full_aggregate()
    reviewed = _replace_review_subjects(
        proposed,
        replacements=DocumentReplacementSet.create(
            tuple(
                item.with_status(reviewed_status)
                for item in proposed.document_replacements.assessments
            )
        ),
        constraints=TemporalConstraintSet.create(
            tuple(
                item.with_status(reviewed_status)
                for item in proposed.temporal_constraints.constraints
            )
        ),
    )
    assert {item.status for item in reviewed.document_replacements.assessments} == {reviewed_status}
    assert {item.status for item in reviewed.temporal_constraints.constraints} == {reviewed_status}

    store = _store(tmp_path / f"{write_kind}-{reviewed_status.value}.sqlite3")
    if write_kind == "compare-and-swap":
        seed = empty_aggregate()
        store.create(seed, operation_id="seed")
        expected_revision: int | None = 1
    else:
        seed = None
        expected_revision = None
    operation_id = f"forbidden-{write_kind}-{reviewed_status.value}"
    with pytest.raises(ChangeControlReviewTransitionError, match="authoritative decision"):
        store.compare_and_swap(
            reviewed,
            expected_revision=expected_revision,
            operation_id=operation_id,
        )
    snapshot = store.load("workspace")
    if seed is None:
        assert snapshot is None
        assert (
            store.conn.execute("SELECT count(*) FROM change_control_aggregates").fetchone()[0] == 0
        )
    else:
        assert snapshot is not None and snapshot.aggregate == seed and snapshot.revision == 1
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
        == 0
    )
    store.close()


def test_generic_create_accepts_proposed_review_subjects(tmp_path: Path) -> None:
    proposed = proposed_full_aggregate()
    store = _store(tmp_path / "proposed.sqlite3")
    committed = store.create(proposed, operation_id="create-proposed")
    assert committed.revision == 1
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.aggregate == proposed
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='create-proposed'"
        ).fetchone()[0]
        == 1
    )
    store.close()


def test_review_operation_ids_are_global_and_payload_bound(tmp_path: Path) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(store, aggregate, operation_id="request-global")
    with pytest.raises(ChangeControlIdempotencyError, match="authoritative review"):
        store.compare_and_swap(aggregate, expected_revision=1, operation_id="request-global")
    conflicting_request = HumanReviewRequestCommand(
        aggregate_id="workspace",
        expected_revision=1,
        expected_aggregate_sha256=aggregate_sha256(aggregate),
        subjects=_review_refs(aggregate),
        requester_id="different@example.com",
        rationale="Different human metadata cannot reuse the request operation.",
    )
    with pytest.raises(ChangeControlIdempotencyError, match="different inputs"):
        store.create_review_request(conflicting_request, operation_id="request-global")
    command = HumanReviewDecisionCommand(
        request_id=request.request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="Reject every subject for this payload-binding test.",
        items=tuple(
            _decision_item(item, ReviewDisposition.REJECTED) for item in request.request.subjects
        ),
    )
    store.decide_review(command, operation_id="decision-global")
    conflicting_items = list(command.items)
    conflicting_items[0] = ReviewDecisionItem(
        **{
            **conflicting_items[0].model_dump(),
            "disposition": ReviewDisposition.ACCEPTED,
        }
    )
    with pytest.raises(ChangeControlIdempotencyError, match="different human inputs"):
        store.decide_review(
            HumanReviewDecisionCommand(
                request_id=command.request_id,
                reviewer_id=command.reviewer_id,
                rationale=command.rationale,
                items=tuple(conflicting_items),
            ),
            operation_id="decision-global",
        )
    with pytest.raises(ChangeControlReviewAlreadyDecidedError):
        store.decide_review(command, operation_id="second-decision-operation")
    store.close()


def test_review_request_and_decision_survive_post_commit_lost_ack(tmp_path: Path) -> None:
    class LostAck(RuntimeError):
        pass

    class LostRequestStore(SqliteChangeControlStore):
        def _deliver_review_request(
            self, result: HumanReviewRequestReceipt
        ) -> HumanReviewRequestReceipt:
            raise LostAck(result.request.requested_at)

    class LostDecisionStore(SqliteChangeControlStore):
        def _deliver_review_decision(
            self, result: HumanReviewDecisionReceipt
        ) -> HumanReviewDecisionReceipt:
            raise LostAck(result.decision.decided_at)

    path = tmp_path / "state.sqlite3"
    aggregate = proposed_full_aggregate()
    owner = _store(path)
    owner.create(aggregate, operation_id="seed")
    snapshot = owner.load("workspace")
    assert snapshot is not None
    request_command = HumanReviewRequestCommand(
        aggregate_id="workspace",
        expected_revision=1,
        expected_aggregate_sha256=snapshot.aggregate_sha256,
        subjects=_review_refs(aggregate),
        requester_id="operator@example.com",
        rationale="Persist this request before acknowledging its creation.",
    )
    owner.close()

    lost_request = LostRequestStore(path)
    lost_request.init_schema()
    with pytest.raises(LostAck) as request_error:
        lost_request.create_review_request(request_command, operation_id="request-lost-ack")
    assert not lost_request.conn.in_transaction
    requested_at = str(request_error.value)
    lost_request.close()

    reopened = _store(path)
    request = reopened.create_review_request(request_command, operation_id="request-lost-ack")
    assert request.replayed and request.request.requested_at == requested_at
    decision_command = HumanReviewDecisionCommand(
        request_id=request.request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="Persist rejection before acknowledging the decision.",
        items=tuple(
            _decision_item(item, ReviewDisposition.REJECTED) for item in request.request.subjects
        ),
    )
    reopened.close()

    lost_decision = LostDecisionStore(path)
    lost_decision.init_schema()
    with pytest.raises(LostAck) as decision_error:
        lost_decision.decide_review(decision_command, operation_id="decision-lost-ack")
    assert not lost_decision.conn.in_transaction
    decided_at = str(decision_error.value)
    lost_decision.close()

    final = _store(path)
    replay = final.decide_review(decision_command, operation_id="decision-lost-ack")
    assert replay.replayed and replay.decision.decided_at == decided_at
    assert final.load("workspace").revision == 2  # type: ignore[union-attr]
    assert (
        final.conn.execute("SELECT count(*) FROM change_control_review_requests").fetchone()[0] == 1
    )
    assert (
        final.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 1
    )
    final.close()


def test_review_models_reject_noncanonical_actor_time_keys_and_order(tmp_path: Path) -> None:
    aggregate = proposed_full_aggregate()
    digest = aggregate_sha256(aggregate)
    refs = _review_refs(aggregate)
    with pytest.raises(ValidationError, match="actor ID"):
        HumanReviewRequestCommand(
            aggregate_id="workspace",
            expected_revision=1,
            expected_aggregate_sha256=digest,
            subjects=refs,
            requester_id="Ｒeviewer",
            rationale="A valid rationale.",
        )
    with pytest.raises(ValidationError, match="canonical whitespace"):
        HumanReviewRequestCommand(
            aggregate_id="workspace",
            expected_revision=1,
            expected_aggregate_sha256=digest,
            subjects=refs,
            requester_id="reviewer@example.com",
            rationale="Repeated  whitespace is not canonical.",
        )

    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request_receipt = _request_review(store, aggregate, operation_id="request-models")
    request = request_receipt.request
    with pytest.raises(ValidationError, match="timestamp"):
        request.__class__.model_validate(
            {**request.model_dump(), "requested_at": "2026-08-07T12:00:00"}
        )
    original_subject = request.subjects[0]
    assert isinstance(original_subject.subject, DocumentReplacementAssessment)
    changed_subject = DocumentReplacementAssessment.model_validate(
        {
            **original_subject.subject.model_dump(mode="json"),
            "rationale": "A different valid snapshot with the same logical subject key.",
        }
    )
    duplicate = ReviewSubjectSnapshot.create(
        original_subject.kind,
        changed_subject,
    )
    with pytest.raises(ValidationError, match="ordered and unique"):
        request.__class__.model_validate(
            {**request.model_dump(), "subjects": (request.subjects[0], duplicate)}
        )

    command = HumanReviewDecisionCommand(
        request_id=request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="Reject all proposed subjects in the model contract test.",
        items=tuple(_decision_item(item, ReviewDisposition.REJECTED) for item in request.subjects),
    )
    decision = store.decide_review(command, operation_id="decision-models").decision
    with pytest.raises(ValidationError, match="operation_id"):
        decision.__class__.model_validate({**decision.model_dump(), "operation_id": " unsafe"})
    with pytest.raises(ValidationError, match="timestamp"):
        decision.__class__.model_validate(
            {**decision.model_dump(), "decided_at": "2026-08-07T12:00:00+01:00"}
        )
    with pytest.raises(ValidationError, match="canonically ordered"):
        decision.__class__.model_validate(
            {**decision.model_dump(), "items": tuple(reversed(decision.items))}
        )
    store.close()


@pytest.mark.parametrize("target", ["base-aggregate", "subject-snapshot"])
def test_review_audit_json_rejects_type_coercible_noncanonical_values(
    tmp_path: Path, target: str
) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / f"{target}.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(store, aggregate, operation_id="request-typed-json")
    if target == "base-aggregate":
        row = store.conn.execute(
            "SELECT base_aggregate_json FROM change_control_review_requests WHERE request_id=?",
            (request.request.request_id,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["document_replacements"]["assessments"][0]["confidence"] = "0.96"
        with store.conn:
            store.conn.execute(
                "UPDATE change_control_review_requests SET base_aggregate_json=? "
                "WHERE request_id=?",
                (
                    canonical_json_bytes(payload).decode("utf-8"),
                    request.request.request_id,
                ),
            )
    else:
        row = store.conn.execute(
            "SELECT payload_json FROM change_control_review_request_subjects "
            "WHERE request_id=? AND subject_kind='document-replacement'",
            (request.request.request_id,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["confidence"] = "0.96"
        with store.conn:
            store.conn.execute(
                "UPDATE change_control_review_request_subjects SET payload_json=? "
                "WHERE request_id=? AND subject_kind='document-replacement'",
                (
                    canonical_json_bytes(payload).decode("utf-8"),
                    request.request.request_id,
                ),
            )
    with pytest.raises(ChangeControlCorruptionError):
        store.get_review_request(request.request.request_id)
    store.close()


def test_review_request_view_rejects_unrelated_or_mutated_decision_bindings(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "state.sqlite3")
    first_aggregate = proposed_full_aggregate()
    second_aggregate = ChangeControlAggregate.create(
        aggregate_id="evaluation.run-2",
        documents=first_aggregate.documents,
        claims=first_aggregate.claims,
        relation_graph=first_aggregate.relation_graph,
        dependencies=first_aggregate.dependencies,
        document_replacements=first_aggregate.document_replacements,
        temporal_constraints=first_aggregate.temporal_constraints,
    )
    store.create(first_aggregate, operation_id="seed-first")
    store.create(second_aggregate, operation_id="seed-second")
    first_request = _request_review(store, first_aggregate, operation_id="request-first")
    second_request = _request_review(store, second_aggregate, operation_id="request-second")

    def reject(request: HumanReviewRequestReceipt, operation_id: str) -> HumanReviewDecisionReceipt:
        return store.decide_review(
            HumanReviewDecisionCommand(
                request_id=request.request.request_id,
                reviewer_id="reviewer@example.com",
                rationale="Reject every subject for standalone view binding validation.",
                items=tuple(
                    _decision_item(item, ReviewDisposition.REJECTED)
                    for item in request.request.subjects
                ),
            ),
            operation_id=operation_id,
        )

    first_decision = reject(first_request, "decision-first").decision
    second_decision = reject(second_request, "decision-second").decision
    with pytest.raises(ValidationError, match="does not belong"):
        HumanReviewRequestView(
            request=first_request.request,
            lifecycle=ReviewLifecycle.DECIDED,
            decision=second_decision,
        )

    rebound_command = HumanReviewDecisionCommand(
        request_id=first_request.request.request_id,
        reviewer_id=second_decision.reviewer_id,
        rationale=second_decision.rationale,
        items=second_decision.items,
    )
    rebound = second_decision.model_copy(
        update={
            "request_id": first_request.request.request_id,
            "decision_payload_sha256": human_review_decision_payload_sha256(rebound_command),
        }
    )
    with pytest.raises(ValidationError, match="result does not match"):
        HumanReviewRequestView(
            request=first_request.request,
            lifecycle=ReviewLifecycle.DECIDED,
            decision=rebound,
        )
    wrong_revision = first_decision.model_copy(
        update={"decided_revision": first_request.request.base_revision + 2}
    )
    with pytest.raises(ValidationError, match="revision"):
        HumanReviewRequestView(
            request=first_request.request,
            lifecycle=ReviewLifecycle.DECIDED,
            decision=wrong_revision,
        )
    wrong_item = first_decision.items[0].model_copy(update={"original_subject_sha256": "0" * 64})
    wrong_command = HumanReviewDecisionCommand(
        request_id=first_decision.request_id,
        reviewer_id=first_decision.reviewer_id,
        rationale=first_decision.rationale,
        items=(wrong_item, *first_decision.items[1:]),
    )
    wrong_items = first_decision.model_copy(
        update={
            "items": wrong_command.items,
            "decision_payload_sha256": human_review_decision_payload_sha256(wrong_command),
        }
    )
    with pytest.raises(ValidationError, match="original subject SHA"):
        HumanReviewRequestView(
            request=first_request.request,
            lifecycle=ReviewLifecycle.DECIDED,
            decision=wrong_items,
        )
    store.close()


@pytest.mark.parametrize(
    "trigger_sql",
    [
        "CREATE TEMP TRIGGER fail_before_children BEFORE DELETE ON "
        "change_control_document_replacements BEGIN SELECT RAISE(ABORT, 'before children'); END",
        "CREATE TEMP TRIGGER fail_after_children AFTER INSERT ON "
        "change_control_temporal_constraints BEGIN SELECT RAISE(ABORT, 'after children'); END",
        "CREATE TEMP TRIGGER fail_before_decision BEFORE INSERT ON "
        "change_control_review_decisions BEGIN SELECT RAISE(ABORT, 'before decision'); END",
        "CREATE TEMP TRIGGER fail_after_decision AFTER INSERT ON "
        "change_control_review_decisions BEGIN SELECT RAISE(ABORT, 'after decision'); END",
    ],
)
def test_review_decision_failure_points_roll_back_everything(
    tmp_path: Path, trigger_sql: str
) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(store, aggregate, operation_id="request-rollback")
    command = HumanReviewDecisionCommand(
        request_id=request.request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="This decision is deliberately interrupted.",
        items=tuple(
            _decision_item(item, ReviewDisposition.REJECTED) for item in request.request.subjects
        ),
    )
    store.conn.execute(trigger_sql)
    with pytest.raises(sqlite3.IntegrityError):
        store.decide_review(command, operation_id="decision-rollback")
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.revision == 1
    assert snapshot.aggregate == aggregate
    assert store.get_review_request(request.request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='decision-rollback'"
        ).fetchone()[0]
        == 0
    )
    store.close()


def test_two_review_decisions_race_to_one_authoritative_revision(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    aggregate = proposed_full_aggregate()
    owner = _store(path)
    owner.create(aggregate, operation_id="seed")
    request = _request_review(owner, aggregate, operation_id="request-race")
    command = HumanReviewDecisionCommand(
        request_id=request.request.request_id,
        reviewer_id="reviewer@example.com",
        rationale="Only one concurrent decision may become authoritative.",
        items=tuple(
            _decision_item(item, ReviewDisposition.REJECTED) for item in request.request.subjects
        ),
    )
    owner.close()

    def decide(index: int) -> str:
        contender = _store(path)
        try:
            contender.decide_review(command, operation_id=f"decision-race-{index}")
            return "committed"
        except ChangeControlReviewAlreadyDecidedError:
            return "already-decided"
        finally:
            contender.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, (1, 2)))
    assert sorted(results) == ["already-decided", "committed"]
    final = _store(path)
    assert final.load("workspace").revision == 2  # type: ignore[union-attr]
    assert (
        final.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 1
    )
    final.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE change_control_review_request_subjects SET ordinal=ordinal+10",
        "UPDATE change_control_review_request_subjects SET payload_json='{}'",
        "UPDATE change_control_review_requests SET request_payload_sha256='" + "0" * 64 + "'",
        "UPDATE change_control_review_requests SET base_aggregate_json='{}'",
    ],
)
def test_review_request_corruption_fails_closed(tmp_path: Path, mutation: str) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    _request_review(store, aggregate, operation_id="request-corruption")
    with store.conn:
        store.conn.execute(mutation)
    with pytest.raises(ChangeControlCorruptionError):
        store.load("workspace")
    store.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE change_control_review_decision_items SET original_subject_sha256='"
        + "0" * 64
        + "'",
        "UPDATE change_control_review_decisions SET decision_payload_sha256='" + "0" * 64 + "'",
        "UPDATE change_control_review_decisions SET decided_aggregate_json='{}'",
        "UPDATE change_control_review_decision_items SET ordinal=4 WHERE ordinal=0",
    ],
)
def test_review_decision_corruption_fails_closed(tmp_path: Path, mutation: str) -> None:
    aggregate = proposed_full_aggregate()
    store = _store(tmp_path / "state.sqlite3")
    store.create(aggregate, operation_id="seed")
    request = _request_review(store, aggregate, operation_id="request-corruption")
    store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="Persist a decision before corrupting one immutable field.",
            items=tuple(
                _decision_item(item, ReviewDisposition.REJECTED)
                for item in request.request.subjects
            ),
        ),
        operation_id="decision-corruption",
    )
    with store.conn:
        store.conn.execute(mutation)
    with pytest.raises(ChangeControlCorruptionError):
        store.load("workspace")
    store.close()


def test_schema_v1_upgrades_to_v2_preserving_aggregate_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    shutil.copy(
        _DEFAULT_MIGRATIONS_DIR / "001_change_control_aggregate.sql",
        migrations / "001_change_control_aggregate.sql",
    )
    path = tmp_path / "upgrade.sqlite3"
    aggregate = proposed_full_aggregate()
    with monkeypatch.context() as old_schema:
        old_schema.setattr(store_module, "_SCHEMA_VERSION", 1)
        old_schema.setattr(SqliteChangeControlStore, "_validate_reviews", lambda self: None)
        original = SqliteChangeControlStore(path, migrations)
        original.init_schema()
        committed = original.create(aggregate, operation_id="v1-receipt")
        original.close()

    shutil.copy(
        _DEFAULT_MIGRATIONS_DIR / "002_authoritative_human_review.sql",
        migrations / "002_authoritative_human_review.sql",
    )
    upgraded = SqliteChangeControlStore(path, migrations)
    upgraded.init_schema()
    snapshot = upgraded.load("workspace")
    assert snapshot is not None and snapshot.aggregate == aggregate
    assert snapshot.revision == committed.revision == 1
    assert [
        int(row[0])
        for row in upgraded.conn.execute(
            "SELECT version FROM change_control_schema_migrations ORDER BY version"
        )
    ] == [1, 2]
    assert (
        upgraded.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='v1-receipt'"
        ).fetchone()[0]
        == 1
    )
    assert upgraded._read_meta()["schema_version"] == "2"  # type: ignore[index]
    upgraded.close()


def test_failed_v1_to_v2_upgrade_rolls_back_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    first_path = migrations / "001_change_control_aggregate.sql"
    second_path = migrations / "002_authoritative_human_review.sql"
    shutil.copy(_DEFAULT_MIGRATIONS_DIR / first_path.name, first_path)
    path = tmp_path / "upgrade-rollback.sqlite3"
    aggregate = proposed_full_aggregate()
    with monkeypatch.context() as old_schema:
        old_schema.setattr(store_module, "_SCHEMA_VERSION", 1)
        old_schema.setattr(SqliteChangeControlStore, "_validate_reviews", lambda self: None)
        original = SqliteChangeControlStore(path, migrations)
        original.init_schema()
        original.create(aggregate, operation_id="preserved-v1")
        original.close()

    migration_sql = (_DEFAULT_MIGRATIONS_DIR / "002_authoritative_human_review.sql").read_text(
        encoding="utf-8"
    )
    second_path.write_text(migration_sql + "\nNOT VALID SQL;\n", encoding="utf-8")
    broken = SqliteChangeControlStore(path, migrations)
    with pytest.raises(sqlite3.Error):
        broken.init_schema()
    assert broken._read_meta()["schema_version"] == "1"  # type: ignore[index]
    assert broken._user_tables() == store_module._V1_EXPECTED_TABLES
    broken.close()

    second_path.write_text(migration_sql, encoding="utf-8")
    recovered = SqliteChangeControlStore(path, migrations)
    recovered.init_schema()
    snapshot = recovered.load("workspace")
    assert snapshot is not None and snapshot.aggregate == aggregate
    assert [
        int(row[0])
        for row in recovered.conn.execute(
            "SELECT version FROM change_control_schema_migrations ORDER BY version"
        )
    ] == [1, 2]
    recovered.close()


def test_lost_ack_after_commit_replays_once_from_a_fresh_connection(tmp_path: Path) -> None:
    class LostAckError(RuntimeError):
        pass

    class LostAckStore(SqliteChangeControlStore):
        committed_result: ChangeControlCommit | None = None

        def _deliver_commit(self, result: ChangeControlCommit) -> ChangeControlCommit:
            self.committed_result = result
            raise LostAckError("commit acknowledgement was lost")

    path = tmp_path / "state.sqlite3"
    aggregate = proposed_full_aggregate()
    first = LostAckStore(path)
    first.init_schema()
    with pytest.raises(LostAckError, match="acknowledgement was lost"):
        first.create(aggregate, operation_id="lost-ack")
    assert not first.conn.in_transaction
    assert first.committed_result is not None
    original_committed_at = first.committed_result.committed_at
    first.close()

    reopened = _store(path)
    snapshot = reopened.load("workspace")
    assert snapshot is not None and snapshot.revision == 1
    assert snapshot.aggregate == aggregate
    replay = reopened.create(aggregate, operation_id="lost-ack")
    assert replay.replayed and replay.changed and replay.revision == 1
    assert replay.committed_at == original_committed_at
    assert (
        reopened.conn.execute("SELECT count(*) FROM change_control_aggregates").fetchone()[0] == 1
    )
    assert (
        reopened.conn.execute("SELECT count(*) FROM change_control_operations").fetchone()[0] == 1
    )
    reopened.close()


def test_same_operation_id_on_another_aggregate_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    store.create(empty_aggregate("workspace"), operation_id="globally-unique")
    with pytest.raises(ChangeControlIdempotencyError):
        store.create(empty_aggregate("evaluation.run-2"), operation_id="globally-unique")
    assert store.load("evaluation.run-2") is None
    store.close()


@pytest.mark.parametrize(
    "operation_id",
    ["", " leading", "-leading", "line\nbreak", "control\x1fchar", "unicode-☃"],
)
def test_operation_ids_are_ascii_safe_and_canonical(tmp_path: Path, operation_id: str) -> None:
    store = _store(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError, match="operation_id"):
        store.create(empty_aggregate(), operation_id=operation_id)
    assert store.load("workspace") is None
    store.close()


def test_two_connections_race_one_revision(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    owner = _store(path)
    owner.create(empty_aggregate(), operation_id="seed")
    owner.close()

    def write(index: int) -> str:
        store = _store(path)
        try:
            aggregate = proposed_full_aggregate(
                relation_rationale=f"Reviewed rationale variant {index}."
            )
            store.compare_and_swap(
                aggregate,
                expected_revision=1,
                operation_id=f"race-{index}",
            )
            return "committed"
        except ChangeControlConflictError:
            return "conflict"
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (1, 2)))
    assert sorted(results) == ["committed", "conflict"]
    inspector = _store(path)
    assert inspector.load("workspace").revision == 2  # type: ignore[union-attr]
    inspector.close()


def test_injected_failure_rolls_back_every_collection_and_receipt(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    before = empty_aggregate()
    store.create(before, operation_id="seed")
    store.conn.execute(
        "CREATE TEMP TRIGGER fail_dependencies BEFORE INSERT ON change_control_dependencies "
        "BEGIN SELECT RAISE(ABORT, 'injected dependency failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected dependency failure"):
        store.compare_and_swap(
            proposed_full_aggregate(), expected_revision=1, operation_id="failed"
        )
    snapshot = store.load("workspace")
    assert snapshot is not None and snapshot.revision == 1 and snapshot.aggregate == before
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='failed'"
        ).fetchone()[0]
        == 0
    )
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    store.close()


def test_held_write_lock_is_typed_busy_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    owner = _store(path)
    owner.create(empty_aggregate(), operation_id="seed")
    contender = SqliteChangeControlStore(path, timeout_seconds=0.01)
    contender.init_schema()
    owner.conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ChangeControlBusyError) as caught:
            contender.compare_and_swap(
                proposed_full_aggregate(), expected_revision=1, operation_id="blocked-write"
            )
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        assert not contender.conn.in_transaction
    finally:
        owner.conn.execute("ROLLBACK")
    assert contender.load("workspace").revision == 1  # type: ignore[union-attr]
    assert (
        contender.conn.execute(
            "SELECT count(*) FROM change_control_operations WHERE operation_id='blocked-write'"
        ).fetchone()[0]
        == 0
    )
    contender.close()
    owner.close()


def test_unidentified_database_and_tampered_ledger_are_refused(tmp_path: Path) -> None:
    unknown_path = tmp_path / "unknown.sqlite3"
    connection = sqlite3.connect(unknown_path)
    connection.execute("CREATE TABLE unrelated (id INTEGER)")
    connection.close()
    unknown = SqliteChangeControlStore(unknown_path)
    with pytest.raises(ChangeControlCorruptionError, match="unidentified"):
        unknown.init_schema()
    unknown.close()

    store = _store(tmp_path / "identified.sqlite3")
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_schema_migrations SET checksum_sha256=? WHERE version=1",
            ("0" * 64,),
        )
    with pytest.raises(ChangeControlCorruptionError, match="ledger"):
        store.load("workspace")
    store.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("committed_revision", 99),
        ("aggregate_sha256", "A" * 64),
        ("operation_id", " unsafe"),
        ("committed_at", "not-a-timestamp"),
        ("receipt_sha256", "0" * 64),
    ],
)
def test_tampered_receipt_fields_fail_closed(tmp_path: Path, column: str, value: object) -> None:
    store = _store(tmp_path / "state.sqlite3")
    store.create(empty_aggregate(), operation_id="receipt")
    with store.conn:
        store.conn.execute(
            f"UPDATE change_control_operations SET {column}=? WHERE operation_id='receipt'",
            (value,),
        )
    with pytest.raises(ChangeControlCorruptionError, match="receipt"):
        store.load("workspace")
    store.close()


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE INDEX unexpected_receipt_index ON change_control_operations(committed_at)",
        "ALTER TABLE change_control_operations ADD COLUMN drift TEXT",
        "ALTER TABLE change_control_operations RENAME COLUMN receipt_sha256 TO receipt_digest",
        "ALTER TABLE change_control_meta RENAME COLUMN value TO stored_value",
    ],
)
def test_live_schema_drift_is_typed_corruption(tmp_path: Path, ddl: str) -> None:
    store = _store(tmp_path / "state.sqlite3")
    store.create(empty_aggregate(), operation_id="seed")
    with store.conn:
        store.conn.execute(ddl)
    with pytest.raises(ChangeControlCorruptionError, match="schema"):
        store.load("workspace")
    store.close()


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (
            "UPDATE change_control_claim_revisions SET statement=? WHERE claim_revision_id="
            "(SELECT claim_revision_id FROM change_control_claim_revisions ORDER BY claim_revision_id LIMIT 1)",
            ("Customers may return an item within 31 days of delivery.",),
        ),
        (
            "UPDATE change_control_claim_evidence SET payload_json=? WHERE ordinal=0",
            ("{not-json",),
        ),
        (
            "UPDATE change_control_claim_evidence SET ordinal=2 WHERE ordinal=0",
            (),
        ),
        (
            "UPDATE change_control_dependency_span_evidence SET ordinal=2 WHERE ordinal=0",
            (),
        ),
        (
            "UPDATE change_control_aggregates SET aggregate_sha256=?",
            ("0" * 64,),
        ),
        (
            "DELETE FROM change_control_temporal_constraint_bases WHERE rowid IN "
            "(SELECT rowid FROM change_control_temporal_constraint_bases LIMIT 1)",
            (),
        ),
    ],
)
def test_corrupt_json_ordinals_bases_and_hash_fail_closed(
    tmp_path: Path, sql: str, params: tuple[object, ...]
) -> None:
    store = _store(tmp_path / "state.sqlite3")
    store.create(proposed_full_aggregate(), operation_id="seed")
    with store.conn:
        store.conn.execute(sql, params)
    with pytest.raises(ChangeControlCorruptionError):
        store.load("workspace")
    store.close()


def test_foreign_key_orphan_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = _store(path)
    store.create(proposed_full_aggregate(), operation_id="seed")
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("DELETE FROM change_control_aggregates")
    raw.commit()
    raw.close()
    store = SqliteChangeControlStore(path)
    with pytest.raises(ChangeControlCorruptionError, match="foreign keys"):
        store.load("workspace")
    store.close()


def test_dependency_span_ordinal_corruption_fails_even_when_foreign_keys_match(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    store = _store(path)
    store.create(proposed_full_aggregate(), operation_id="seed")
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("UPDATE change_control_dependency_spans SET ordinal=2")
    raw.execute("UPDATE change_control_dependency_span_evidence SET span_ordinal=2")
    raw.commit()
    raw.close()
    store = SqliteChangeControlStore(path)
    with pytest.raises(ChangeControlCorruptionError, match="ordered rows"):
        store.load("workspace")
    store.close()


def test_failed_schema_creation_is_atomic_and_retryable(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    shutil.copytree(_DEFAULT_MIGRATIONS_DIR, migrations)
    migration = migrations / "001_change_control_aggregate.sql"
    original = migration.read_text(encoding="utf-8")
    migration.write_text(original + "\nCREATE TABLE reached (id INTEGER);\nNOT SQL;\n")
    store = SqliteChangeControlStore(tmp_path / "atomic.sqlite3", migrations)
    with pytest.raises(sqlite3.Error):
        store.init_schema()
    assert store._user_tables() == set()
    migration.write_text(original, encoding="utf-8")
    store.init_schema()
    assert store.load("workspace") is None
    store.close()


def test_malformed_meta_version_is_typed_corruption(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_meta SET value='not-an-integer' WHERE key='schema_version'"
        )
    with pytest.raises(ChangeControlCorruptionError, match="schema version"):
        store.load("workspace")
    store.close()


def test_path_configuration_is_separate_from_rebuildable_index(tmp_path: Path) -> None:
    from mastervault.config import PathsCfg

    paths = PathsCfg(workspace=tmp_path)
    assert paths.change_control_db_path == tmp_path / "change_control" / "state.sqlite3"
    assert paths.change_control_db_path != paths.sqlite_path
