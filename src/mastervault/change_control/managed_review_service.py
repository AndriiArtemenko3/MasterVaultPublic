"""Authoritative PR-A service boundary for opening and deciding managed review.

This facade derives authority-bearing commands from exact admitted evidence and
store-reopened review targets.  It never accepts a caller-supplied request
record, never publishes a revision, and never advances the active generation.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.generic_governing_source import CompositeManagedReviewResolverV2
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedAdoptionChoice,
    ManagedBundleOutcome,
    ManagedNoWorkPlanningAdmissionBinding,
    ManagedReviewBaseBinding,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDecisionReceipt,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewOutcome,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewRequestReceipt,
    ManagedRevisionReviewTarget,
    ManagedRunBindingV2,
    NoChangeImpactCard,
    normalize_actor_id,
    normalize_review_rationale,
)
from mastervault.change_control.managed_review_repository import (
    RepositoryBackedManagedReviewResolver,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    ManagedReviewStaleError,
    ManagedRevisionEditDeferredError,
    ManagedRevisionReviewStoreView,
    ManagedRevisionStoreLifecycle,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.store import (
    ChangeControlIdempotencyError,
    ChangeControlReviewAlreadyDecidedError,
)

_TARGET_ID = r"^mtarget:[0-9a-f]{64}$"


class ManagedReviewServiceError(RuntimeError):
    """The managed-review facade could not prove its authoritative result."""


class ManagedReviewSelectionError(ManagedReviewServiceError):
    """A human selection set is incomplete, duplicated, foreign, or invalid."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ManagedRevisionReviewSelection(_StrictFrozenModel):
    """One reviewer choice; all other decision fields remain service-derived."""

    target_id: Annotated[str, Field(pattern=_TARGET_ID)]
    disposition: ManagedRevisionDisposition


def _exact_run(run_binding: ManagedRunBindingV2) -> ManagedRunBindingV2:
    if type(run_binding) is not ManagedRunBindingV2:
        raise TypeError("managed review opening requires an exact v2 run binding")
    exact = ManagedRunBindingV2.model_validate_json(
        canonical_json_bytes(run_binding.model_dump(mode="json"))
    )
    if exact != run_binding:
        raise ValueError("managed review run binding is not canonical")
    return exact


def _exact_subjects(
    subjects: tuple[ManagedRevisionPlan | NoChangeImpactCard, ...],
    *,
    allow_empty: bool = False,
) -> tuple[ManagedRevisionPlan | NoChangeImpactCard, ...]:
    if type(subjects) is not tuple or (not subjects and not allow_empty):
        raise ValueError("managed review requires the complete non-empty admitted subject set")
    exact: list[ManagedRevisionPlan | NoChangeImpactCard] = []
    for subject in subjects:
        if type(subject) is ManagedRevisionPlan:
            reopened: ManagedRevisionPlan | NoChangeImpactCard = (
                ManagedRevisionPlan.model_validate_json(
                    canonical_json_bytes(subject.model_dump(mode="json"))
                )
            )
        elif type(subject) is NoChangeImpactCard:
            reopened = NoChangeImpactCard.model_validate_json(
                canonical_json_bytes(subject.model_dump(mode="json"))
            )
        else:
            raise TypeError("managed review subject type was substituted")
        if reopened != subject:
            raise ValueError("managed review subject is not canonical")
        exact.append(reopened)
    ordered = tuple(sorted(exact, key=lambda item: item.target_key))
    if len({item.target_key for item in ordered}) != len(ordered):
        raise ValueError("managed review subjects must be target-unique")
    return ordered


def _require_production_resolver(
    resolver: RepositoryBackedManagedReviewResolver | CompositeManagedReviewResolverV2,
) -> RepositoryBackedManagedReviewResolver | CompositeManagedReviewResolverV2:
    if type(resolver) not in {
        RepositoryBackedManagedReviewResolver,
        CompositeManagedReviewResolverV2,
    }:
        raise TypeError("managed review service requires the production repository resolver")
    return resolver


def _store_authority_context(
    *,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None,
    prechange_head: AggregateHeadBinding,
    authority_context: AuthorityVerificationContext | None,
) -> AuthorityVerificationContext:
    if authority_context is not None:
        if type(authority_context) is not AuthorityVerificationContext:
            raise TypeError("managed review authority context type was substituted")
        if verified_bootstrap is not None:
            raise TypeError("workspace authority context cannot mix with legacy bootstrap")
        return authority_context
    if type(verified_bootstrap) is not VerifiedAnalysisBootstrapCapability:
        raise TypeError("legacy managed review requires exact sealed bootstrap authority")
    return AuthorityVerificationContext.legacy(
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )


def _delivery_matches_request(
    receipt: ManagedRevisionReviewRequestReceipt,
    view: ManagedRevisionReviewStoreView,
) -> bool:
    return receipt == ManagedRevisionReviewRequestReceipt.create(
        view.request_record, replayed=receipt.replayed
    )


def _delivery_matches_decision(
    receipt: ManagedRevisionDecisionReceipt,
    view: ManagedRevisionReviewStoreView,
) -> bool:
    return view.decision_record is not None and receipt == ManagedRevisionDecisionReceipt.create(
        view.decision_record, replayed=receipt.replayed
    )


def _read_review(
    *,
    store: SqliteManagedChangeControlStore,
    request_id: str,
    resolver: RepositoryBackedManagedReviewResolver | CompositeManagedReviewResolverV2,
    authority_context: AuthorityVerificationContext,
) -> ManagedRevisionReviewStoreView:
    return store.get_managed_review(
        request_id,
        resolver=resolver,
        authority_context=authority_context,
    )


def open_managed_revision_review(
    *,
    store: SqliteManagedChangeControlStore,
    run_binding: ManagedRunBindingV2,
    admitted_subjects: tuple[ManagedRevisionPlan | NoChangeImpactCard, ...],
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    resolver: RepositoryBackedManagedReviewResolver | CompositeManagedReviewResolverV2,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
    prechange_head: AggregateHeadBinding,
    authority_context: AuthorityVerificationContext | None = None,
    operation_id: str,
    requester_id: str,
    rationale: str,
) -> ManagedRevisionReviewStoreView:
    """Open exactly one V2-admitted review and verify the authoritative reread.

    Mechanical ``NO_WORK`` may use an empty subject set only through an exact
    no-work admission.  Every other empty subject set fails before store access.
    """

    exact_run = _exact_run(run_binding)
    subjects = _exact_subjects(
        admitted_subjects,
        allow_empty=isinstance(
            exact_run.revision_planning_admission,
            ManagedNoWorkPlanningAdmissionBinding,
        ),
    )
    production_resolver = _require_production_resolver(resolver)
    if type(reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
        raise TypeError("managed review requires exact reviewed temporal authority")
    reviewed = reviewed_snapshot.verify()
    exact_prechange = AggregateHeadBinding.model_validate_json(
        canonical_json_bytes(prechange_head.model_dump(mode="json"))
    )
    store_authority = _store_authority_context(
        verified_bootstrap=verified_bootstrap,
        prechange_head=exact_prechange,
        authority_context=authority_context,
    )
    requester_id = normalize_actor_id(requester_id)
    rationale = normalize_review_rationale(rationale)

    admission_subjects = tuple(
        (
            item.target_key,
            item.document_version_id,
            item.subject_id,
            item.subject_sha256,
        )
        for item in exact_run.revision_planning_admission.targets
    )
    supplied_subjects = tuple(
        (
            item.target_key,
            item.predecessor.document_version_id,
            item.plan_id if isinstance(item, ManagedRevisionPlan) else item.card_id,
            item.plan_sha256 if isinstance(item, ManagedRevisionPlan) else item.card_sha256,
        )
        for item in subjects
    )
    if supplied_subjects != admission_subjects:
        raise ValueError("managed review subjects differ from the exact admitted subject set")
    aggregate_ids = {
        exact_run.prechange_head.aggregate_id,
        exact_run.analysis_head.aggregate_id,
        reviewed.binding.analysis_head.aggregate_id,
        reviewed.binding.reviewed_head.aggregate_id,
        exact_prechange.aggregate_id,
    }
    if (
        exact_run.prechange_head != exact_prechange
        or reviewed.binding.evidence_repository_id
        != exact_run.revision_planning_admission.repository_id
        or exact_run.governing_source_adoption.evidence_repository_id
        != reviewed.binding.evidence_repository_id
        or exact_run.governing_source_adoption.reviewed_snapshot_binding_id
        != reviewed.binding.binding_id
        or exact_run.governing_source_adoption.reviewed_snapshot_binding_sha256
        != reviewed.binding.binding_sha256
        or exact_run.governing_source_adoption.reviewed_inventory_sha256
        != reviewed.binding.reviewed_inventory_sha256
        or exact_run.governing_source_adoption.reviewed_head != reviewed.binding.reviewed_head
        or exact_run.governing_source_adoption.temporal_decision_record_sha256
        != reviewed.binding.temporal_decision_record_sha256
        or exact_run.revision_planning_admission.reviewed_snapshot_binding_id
        != reviewed.binding.binding_id
        or exact_run.revision_planning_admission.reviewed_snapshot_binding_sha256
        != reviewed.binding.binding_sha256
        or exact_run.revision_planning_admission.temporal_decision_record_sha256
        != reviewed.binding.temporal_decision_record_sha256
        or len(aggregate_ids) != 1
        or reviewed.binding.analysis_head != exact_run.analysis_head
        or reviewed.temporal_prerequisite.review_open_head != reviewed.binding.reviewed_head
    ):
        raise ValueError("reviewed temporal authority differs from the admitted run lineage")

    existing_record = store.find_managed_review_request_by_operation_id(
        operation_id,
        resolver=production_resolver,
        authority_context=store_authority,
    )
    opening_authority = (
        existing_record.committed_authority
        if existing_record is not None
        else store.get_active_generation(
            exact_run.prechange_head.aggregate_id,
            authority_context=store_authority,
        )
    )
    review_base = ManagedReviewBaseBinding.create(
        review_open_head=reviewed.binding.reviewed_head,
        authority=opening_authority,
    )
    targets = tuple(ManagedRevisionReviewTarget.create(item) for item in subjects)
    bundle = ManagedRevisionReviewBundle.create(
        run_binding=exact_run,
        review_base=review_base,
        temporal_prerequisite=reviewed.temporal_prerequisite,
        targets=targets,
    )
    command = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id=operation_id,
        requester_id=requester_id,
        rationale=rationale,
    )
    if existing_record is not None and existing_record.command != command:
        raise ChangeControlIdempotencyError(
            "managed request operation_id was reused for different immutable inputs"
        )

    delivery: ManagedRevisionReviewRequestReceipt | None = None
    write_error: Exception | None = None
    try:
        delivery = store.create_managed_review_request(
            command,
            resolver=production_resolver,
            authority_context=store_authority,
        )
    except Exception as exc:  # lost acknowledgement is reconciled from SQLite below
        write_error = exc
    try:
        committed = store.find_managed_review_request_by_operation_id(
            operation_id,
            resolver=production_resolver,
            authority_context=store_authority,
        )
    except Exception as read_error:
        if write_error is not None:
            raise write_error from read_error
        raise
    if committed is None or committed.command != command:
        if write_error is not None:
            raise write_error
        raise ManagedReviewServiceError(
            "authoritative request operation differs from the exact derived opening"
        )
    view = _read_review(
        store=store,
        request_id=committed.command.request_id,
        resolver=production_resolver,
        authority_context=store_authority,
    )
    if view.request_record != committed:
        raise ManagedReviewServiceError(
            "authoritative request view differs from its immutable operation evidence"
        )
    if delivery is not None and not _delivery_matches_request(delivery, view):
        raise ManagedReviewServiceError(
            "managed request delivery receipt differs from the authoritative record"
        )
    return view


def _exact_selections(
    selections: tuple[ManagedRevisionReviewSelection, ...],
    *,
    allow_empty: bool = False,
) -> tuple[ManagedRevisionReviewSelection, ...]:
    if type(selections) is not tuple or (not selections and not allow_empty):
        raise ManagedReviewSelectionError("managed decision requires a non-empty selection set")
    exact = tuple(
        ManagedRevisionReviewSelection.model_validate_json(
            canonical_json_bytes(item.model_dump(mode="json"))
        )
        if type(item) is ManagedRevisionReviewSelection
        else (_raise_selection_type())
        for item in selections
    )
    if any(item.disposition == ManagedRevisionDisposition.EDIT for item in exact):
        raise ManagedRevisionEditDeferredError(
            "managed review EDIT is deferred until a separately admitted edited plan exists"
        )
    if len({item.target_id for item in exact}) != len(exact):
        raise ManagedReviewSelectionError("managed decision selections contain duplicate targets")
    return tuple(sorted(exact, key=lambda item: item.target_id))


def _raise_selection_type() -> ManagedRevisionReviewSelection:
    raise TypeError("managed review selection type was substituted")


def decide_managed_revision_review(
    *,
    store: SqliteManagedChangeControlStore,
    request_id: str,
    selections: tuple[ManagedRevisionReviewSelection, ...],
    adoption_choice: ManagedAdoptionChoice | None = None,
    resolver: RepositoryBackedManagedReviewResolver | CompositeManagedReviewResolverV2,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
    prechange_head: AggregateHeadBinding,
    authority_context: AuthorityVerificationContext | None = None,
    operation_id: str,
    reviewer_id: str,
    rationale: str,
) -> ManagedRevisionReviewStoreView:
    """Decide from SQLite-owned targets; caller input contains only target choices."""

    production_resolver = _require_production_resolver(resolver)
    exact_prechange = AggregateHeadBinding.model_validate_json(
        canonical_json_bytes(prechange_head.model_dump(mode="json"))
    )
    store_authority = _store_authority_context(
        verified_bootstrap=verified_bootstrap,
        prechange_head=exact_prechange,
        authority_context=authority_context,
    )
    reviewer_id = normalize_actor_id(reviewer_id)
    rationale = normalize_review_rationale(rationale)

    before = _read_review(
        store=store,
        request_id=request_id,
        resolver=production_resolver,
        authority_context=store_authority,
    )
    if before.lifecycle == ManagedRevisionStoreLifecycle.STALE:
        raise ManagedReviewStaleError("stale managed review cannot accept a new decision")
    targets = {item.target_id: item for item in before.request_record.command.bundle.targets}
    adoption_only = not targets and isinstance(
        getattr(
            before.request_record.command.bundle.run_binding,
            "revision_planning_admission",
            None,
        ),
        ManagedNoWorkPlanningAdmissionBinding,
    )
    exact_selections = _exact_selections(selections, allow_empty=adoption_only)
    if adoption_only != (adoption_choice is not None):
        raise ManagedReviewSelectionError(
            "adoption choice is required only for an exact zero-target review"
        )
    if tuple(item.target_id for item in exact_selections) != tuple(sorted(targets)):
        raise ManagedReviewSelectionError(
            "managed decision requires exactly one selection for every stored target"
        )

    outcomes: list[ManagedRevisionReviewOutcome] = []
    for selection in exact_selections:
        target = targets[selection.target_id]
        allowed = (
            {
                ManagedRevisionDisposition.APPROVE,
                ManagedRevisionDisposition.REJECT,
            }
            if isinstance(target.subject, ManagedRevisionPlan)
            else {
                ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
                ManagedRevisionDisposition.REJECT,
            }
        )
        if selection.disposition not in allowed:
            raise ManagedReviewSelectionError(
                "managed review selection is invalid for the stored target kind"
            )
        outcomes.append(
            ManagedRevisionReviewOutcome(
                target_id=target.target_id,
                original_target_sha256=target.target_sha256,
                disposition=selection.disposition,
            )
        )
    bundle_outcome = (
        ManagedBundleOutcome.ACCEPTED
        if adoption_choice == ManagedAdoptionChoice.ADOPT
        else ManagedBundleOutcome.REJECTED
        if adoption_choice == ManagedAdoptionChoice.REJECT
        or all(item.disposition == ManagedRevisionDisposition.REJECT for item in outcomes)
        else ManagedBundleOutcome.ACCEPTED
    )
    command = ManagedRevisionDecisionCommand.create(
        operation_id=operation_id,
        request_record=before.request_record,
        bundle_outcome=bundle_outcome,
        adoption_choice=adoption_choice,
        reviewer_id=reviewer_id,
        rationale=rationale,
        items=tuple(outcomes),
    )
    if before.lifecycle == ManagedRevisionStoreLifecycle.DECIDED and (
        before.decision_record is None or before.decision_record.command != command
    ):
        raise ChangeControlReviewAlreadyDecidedError(
            "managed review already has a different immutable decision"
        )

    delivery: ManagedRevisionDecisionReceipt | None = None
    write_error: Exception | None = None
    try:
        delivery = store.decide_managed_review(
            command,
            resolver=production_resolver,
            authority_context=store_authority,
        )
    except Exception as exc:  # reconcile a commit whose acknowledgement was lost
        write_error = exc
    try:
        after = _read_review(
            store=store,
            request_id=request_id,
            resolver=production_resolver,
            authority_context=store_authority,
        )
    except Exception as read_error:
        if write_error is not None:
            raise write_error from read_error
        raise
    if (
        after.lifecycle != ManagedRevisionStoreLifecycle.DECIDED
        or after.decision_record is None
        or after.decision_record.command != command
    ):
        if write_error is not None:
            raise write_error
        raise ManagedReviewServiceError(
            "authoritative decision reread differs from the exact derived outcome"
        )
    if delivery is not None and not _delivery_matches_decision(delivery, after):
        raise ManagedReviewServiceError(
            "managed decision delivery receipt differs from the authoritative record"
        )
    return after


__all__ = [
    "ManagedReviewSelectionError",
    "ManagedReviewServiceError",
    "ManagedRevisionReviewSelection",
    "decide_managed_revision_review",
    "open_managed_revision_review",
]
