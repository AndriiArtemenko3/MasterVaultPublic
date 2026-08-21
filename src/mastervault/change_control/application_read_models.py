"""Strict read-only projections for the public synchronous lifecycle facade.

Navigation links are treated only as locators.  Every entry point opens a
query-only store and asks it to reopen each linked owner before constructing a
path-free public DTO.  Incomplete prefixes are represented only by the
``bootstrapped`` phase; holes, surplus links, cross-run evidence, and later
stage evidence without its prerequisites fail closed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from mastervault.change_control.application_authority_resolver import (
    ApplicationOperatorRunAuthorityResolver,
    ApplicationSourceNoteResolver,
    ApplicationSourceNoteResolverLoader,
)
from mastervault.change_control.change_application_contracts import (
    AuthoritySummaryV1,
    ChangeActivationEvidenceSummaryV1,
    ChangeEvidenceCompletenessV1,
    ChangeReviewCitationV1,
    ChangeReviewEvidenceSummaryV1,
    ChangeReviewPacketV1,
    ChangeReviewStageV1,
    ChangeReviewSubjectKindV1,
    ChangeReviewSubjectV1,
    ChangeRunNextActionV1,
    ChangeRunOutcomeV1,
    ChangeRunPageV1,
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
    ChangeRunSummaryV1,
    ChangeVerificationResultV1,
    GenerationZeroBaselineSummaryV1,
    IncomingEvidenceSummaryV1,
    RegressionSuiteEvidenceSummaryV1,
)
from mastervault.change_control.managed_generation import ManagedGenerationActivationReceipt
from mastervault.change_control.managed_review import (
    AuthorityRevisionBinding,
    ManagedNoWorkPlanningAdmissionBinding,
    ManagedRevisionDecisionRecord,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.models import (
    DocumentReplacementAssessment,
    TemporalConstraint,
    canonical_json_bytes,
)
from mastervault.change_control.operator_run import (
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunPhase,
    OperatorRunView,
)
from mastervault.change_control.review import ReviewDisposition


class ApplicationReadModelError(ValueError):
    """Authoritative lifecycle evidence cannot produce an exact public view."""


class ApplicationRunNotFoundError(ApplicationReadModelError):
    """A syntactically valid caller-supplied run ID has no durable owner."""


class ApplicationReviewUnavailableError(ApplicationReadModelError):
    """A valid lifecycle run is not currently awaiting a human review gate."""


type ActiveAuthorityLoader = Callable[[str], AuthorityRevisionBinding]
type ActivationEvidenceVerifier = Callable[[OperatorRunView, ChangeRunStatusV1], None]


_PHASES = {phase.value: phase for phase in ChangeRunPhaseV1}
_BASE_KINDS = {
    OperatorRunLinkKind.BOOTSTRAP_INTENT,
    OperatorRunLinkKind.WORKSPACE_INVENTORY,
    OperatorRunLinkKind.LEGACY_INDEX_READINESS,
    OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
}
_ADMISSION_KINDS = {
    OperatorRunLinkKind.INCOMING_SOURCE,
    OperatorRunLinkKind.REGRESSION_SUITE,
    OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
}
_TEMPORAL_OPEN_KINDS = {
    OperatorRunLinkKind.TEMPORAL_PROPOSAL,
    OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
}
_TEMPORAL_DECIDED_KINDS = _TEMPORAL_OPEN_KINDS | {OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION}
_MANAGED_WORK_KINDS = {
    OperatorRunLinkKind.IMPACT_EVIDENCE,
    OperatorRunLinkKind.REVISION_PLANNING,
}
_MANAGED_OPEN_KINDS = _MANAGED_WORK_KINDS | {OperatorRunLinkKind.MANAGED_REVIEW_REQUEST}
_MANAGED_DECIDED_KINDS = _MANAGED_OPEN_KINDS | {OperatorRunLinkKind.MANAGED_REVIEW_DECISION}


def _authority(value: AuthorityRevisionBinding, *, active: bool) -> AuthoritySummaryV1:
    generation = value.active_generation
    return AuthoritySummaryV1(
        authority_id=value.authority_id,
        revision=value.authority_revision,
        generation_id=generation.generation_id,
        generation_number=generation.generation_number,
        manifest_sha256=generation.manifest_sha256,
        active_pointer_sha256=value.active_pointer_sha256,
        is_active=active,
    )


def _phase(value: OperatorRunPhase) -> ChangeRunPhaseV1:
    return _PHASES[value.value]


def _outcome(value: ChangeRunPhaseV1) -> ChangeRunOutcomeV1:
    return {
        ChangeRunPhaseV1.ACTIVATED: ChangeRunOutcomeV1.ACTIVATED,
        ChangeRunPhaseV1.REJECTED_NO_OP: ChangeRunOutcomeV1.REJECTED_NO_OP,
        ChangeRunPhaseV1.COMPLETED_NO_OP: ChangeRunOutcomeV1.COMPLETED_NO_OP,
    }.get(value, ChangeRunOutcomeV1.IN_PROGRESS)


def _next_action(value: ChangeRunPhaseV1) -> ChangeRunNextActionV1:
    return {
        ChangeRunPhaseV1.BOOTSTRAPPED: ChangeRunNextActionV1.RESUME,
        ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW: (ChangeRunNextActionV1.SUBMIT_TEMPORAL_REVIEW),
        ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW: ChangeRunNextActionV1.SUBMIT_MANAGED_REVIEW,
        ChangeRunPhaseV1.READY_TO_ACTIVATE: ChangeRunNextActionV1.ACTIVATE,
        ChangeRunPhaseV1.ACTIVATED: ChangeRunNextActionV1.NONE,
        ChangeRunPhaseV1.REJECTED_NO_OP: ChangeRunNextActionV1.NONE,
        ChangeRunPhaseV1.COMPLETED_NO_OP: ChangeRunNextActionV1.NONE,
    }[value]


class ApplicationReadModels:
    """Read-only, restart-safe lifecycle projections for ``ChangeControlApplication``."""

    def __init__(
        self,
        state_path: Path,
        evidence_root: Path,
        *,
        source_note_resolver: (
            ApplicationSourceNoteResolver | ApplicationSourceNoteResolverLoader | None
        ) = None,
        configuration_sha256: str | None = None,
        active_authority_loader: ActiveAuthorityLoader | None = None,
        activation_evidence_verifier: ActivationEvidenceVerifier | None = None,
    ) -> None:
        self._state_path = Path(state_path)
        self._resolver = ApplicationOperatorRunAuthorityResolver(
            evidence_root=Path(evidence_root),
            state_path=self._state_path,
            source_note_resolver=source_note_resolver,
            configuration_sha256=configuration_sha256,
        )
        self._active_authority_loader = active_authority_loader
        self._activation_evidence_verifier = activation_evidence_verifier

    def _store(self) -> SqliteManagedChangeControlStore:
        return SqliteManagedChangeControlStore(self._state_path, secure_open=True, read_only=True)

    def _load_current_authority(self, aggregate_id: str) -> AuthorityRevisionBinding:
        if self._active_authority_loader is not None:
            authority = self._active_authority_loader(aggregate_id)
            if authority.aggregate_id != aggregate_id:
                raise ApplicationReadModelError("active authority belongs to another aggregate")
            return authority
        store = self._store()
        try:
            row = store.conn.execute(
                "SELECT authority_json FROM change_control_active_generation WHERE aggregate_id=?",
                (aggregate_id,),
            ).fetchone()
        finally:
            store.close()
        if row is None:
            raise ApplicationReadModelError("active authority does not exist")
        try:
            authority = AuthorityRevisionBinding.model_validate_json(
                str(row["authority_json"]), strict=True
            )
        except ValueError as exc:
            raise ApplicationReadModelError("active authority is not canonical") from exc
        if authority.aggregate_id != aggregate_id:
            raise ApplicationReadModelError("active authority belongs to another aggregate")
        return authority

    def _base_authority(
        self, run: OperatorRunView, current: AuthorityRevisionBinding
    ) -> AuthorityRevisionBinding:
        command = run.record.command
        if current.authority_id == command.base_authority_id:
            candidate = current
        else:
            store = self._store()
            try:
                rows = store.conn.execute(
                    "SELECT payload_json FROM change_control_generation_activation_receipts "
                    "ORDER BY activated_at,receipt_id"
                ).fetchall()
            finally:
                store.close()
            matches: list[AuthorityRevisionBinding] = []
            from mastervault.change_control.managed_generation import (
                ManagedGenerationActivationReceipt,
            )

            for row in rows:
                try:
                    receipt = ManagedGenerationActivationReceipt.model_validate_json(
                        str(row["payload_json"]), strict=True
                    )
                except ValueError as exc:
                    raise ApplicationReadModelError(
                        "historical activation receipt is not canonical"
                    ) from exc
                if receipt.prior_authority.authority_id == command.base_authority_id:
                    matches.append(receipt.prior_authority)
            if len(matches) != 1:
                raise ApplicationReadModelError(
                    "historical base authority has no unique activation origin"
                )
            candidate = matches[0]
        if not (
            candidate.authority_id == command.base_authority_id
            and candidate.authority_revision == command.base_authority_revision == 0
            and candidate.active_pointer_sha256 == command.base_active_pointer_sha256
            and candidate.active_generation.generation_number == 0
        ):
            raise ApplicationReadModelError("run base authority differs from generation zero")
        return candidate

    def _activation_summary(
        self,
        *,
        run: OperatorRunView,
        link: OperatorRunLinkCommand,
        base: AuthorityRevisionBinding,
        current: AuthorityRevisionBinding,
    ) -> ChangeActivationEvidenceSummaryV1:
        store = self._store()
        try:
            row = store.conn.execute(
                "SELECT payload_json FROM change_control_generation_activation_receipts "
                "WHERE receipt_id=? AND receipt_sha256=?",
                (link.target_id, link.target_sha256),
            ).fetchone()
            if row is None:
                raise ApplicationReadModelError("activation receipt cannot be reopened")
            try:
                receipt = ManagedGenerationActivationReceipt.model_validate_json(
                    str(row["payload_json"]), strict=True
                )
            except ValueError as exc:
                raise ApplicationReadModelError("activation evidence is not canonical") from exc
            decision_rows = store.conn.execute(
                "SELECT payload_json FROM change_control_managed_review_decisions "
                "WHERE record_sha256=?",
                (receipt.decision_record_sha256,),
            ).fetchall()
        finally:
            store.close()
        if len(decision_rows) != 1:
            raise ApplicationReadModelError(
                "activation receipt cannot reopen its unique managed decision"
            )
        try:
            decision = ManagedRevisionDecisionRecord.model_validate_json(
                str(decision_rows[0]["payload_json"]), strict=True
            )
        except ValueError as exc:
            raise ApplicationReadModelError("activation evidence is not canonical") from exc
        run_binding = decision.command.bundle.run_binding
        if not (
            canonical_json_bytes(receipt.model_dump(mode="json")).decode()
            == str(row["payload_json"])
            and canonical_json_bytes(decision.model_dump(mode="json")).decode()
            == str(decision_rows[0]["payload_json"])
            and receipt.receipt_id == link.target_id
            and receipt.receipt_sha256 == link.target_sha256
            and receipt.prior_authority == base
            and receipt.activated_authority == current
            and decision.record_sha256 == receipt.decision_record_sha256
            and type(run_binding) is ManagedRunBindingV2
            and run_binding.run_id == run.record.command.run_id
        ):
            raise ApplicationReadModelError(
                "activation evidence differs from its run, prior authority, or active successor"
            )
        return ChangeActivationEvidenceSummaryV1(
            receipt_id=receipt.receipt_id,
            receipt_sha256=receipt.receipt_sha256,
            generation_id=receipt.activated_authority.active_generation.generation_id,
        )

    @staticmethod
    def _links(run: OperatorRunView) -> dict[OperatorRunLinkKind, OperatorRunLinkCommand]:
        return {link.command.kind: link.command for link in run.links}

    @staticmethod
    def _require_shape(run: OperatorRunView, phase: ChangeRunPhaseV1) -> None:
        kinds = {link.command.kind for link in run.links}
        if not kinds >= _BASE_KINDS:
            raise ApplicationReadModelError("operator run lacks its exact bootstrap authority")
        allowed = set(_BASE_KINDS)
        if phase == ChangeRunPhaseV1.BOOTSTRAPPED:
            allowed |= _ADMISSION_KINDS | {OperatorRunLinkKind.TEMPORAL_PROPOSAL}
        else:
            allowed |= _ADMISSION_KINDS
        if phase in {
            ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW,
            ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW,
            ChangeRunPhaseV1.READY_TO_ACTIVATE,
            ChangeRunPhaseV1.ACTIVATED,
            ChangeRunPhaseV1.REJECTED_NO_OP,
            ChangeRunPhaseV1.COMPLETED_NO_OP,
        }:
            allowed |= _TEMPORAL_OPEN_KINDS
        if phase in {
            ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW,
            ChangeRunPhaseV1.READY_TO_ACTIVATE,
            ChangeRunPhaseV1.ACTIVATED,
            ChangeRunPhaseV1.REJECTED_NO_OP,
            ChangeRunPhaseV1.COMPLETED_NO_OP,
        }:
            allowed |= _TEMPORAL_DECIDED_KINDS
        if phase in {
            ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW,
            ChangeRunPhaseV1.READY_TO_ACTIVATE,
            ChangeRunPhaseV1.ACTIVATED,
            ChangeRunPhaseV1.REJECTED_NO_OP,
        }:
            allowed |= _MANAGED_OPEN_KINDS
        if phase == ChangeRunPhaseV1.COMPLETED_NO_OP:
            allowed |= _MANAGED_WORK_KINDS | {OperatorRunLinkKind.MECHANICAL_NO_CHANGE}
        if phase in {
            ChangeRunPhaseV1.READY_TO_ACTIVATE,
            ChangeRunPhaseV1.ACTIVATED,
            ChangeRunPhaseV1.REJECTED_NO_OP,
        }:
            allowed |= _MANAGED_DECIDED_KINDS
        if phase == ChangeRunPhaseV1.ACTIVATED:
            allowed.add(OperatorRunLinkKind.ACTIVATION_OPERATION)
        if not kinds <= allowed:
            raise ApplicationReadModelError("operator run contains surplus lifecycle links")
        if phase != ChangeRunPhaseV1.BOOTSTRAPPED and not kinds >= _ADMISSION_KINDS:
            raise ApplicationReadModelError("operator run has partial admission evidence")
        if OperatorRunLinkKind.MECHANICAL_NO_CHANGE in kinds and kinds != (
            _BASE_KINDS | _ADMISSION_KINDS | {OperatorRunLinkKind.MECHANICAL_NO_CHANGE}
        ):
            raise ApplicationReadModelError(
                "mechanical no-change run contains surplus lifecycle links"
            )
        ordered_prefixes = (
            OperatorRunLinkKind.INCOMING_SOURCE,
            OperatorRunLinkKind.REGRESSION_SUITE,
            OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
            OperatorRunLinkKind.TEMPORAL_PROPOSAL,
        )
        seen_gap = False
        for kind in ordered_prefixes:
            if kind not in kinds:
                seen_gap = True
            elif seen_gap:
                raise ApplicationReadModelError("operator run contains a lifecycle evidence gap")

    def _read_run(self, run_id: str) -> tuple[OperatorRunView, ChangeRunPhaseV1]:
        store = self._store()
        try:
            run = store.get_operator_run(run_id, resolver=self._resolver)
            if run is None:
                raise ApplicationRunNotFoundError("operator run does not exist")
            derived = store._derive_operator_run_phase(run, resolver=self._resolver)  # noqa: SLF001
        finally:
            store.close()
        phase = _phase(derived)
        kinds = {link.command.kind for link in run.links}
        if (
            phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW
            and OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST not in kinds
        ):
            phase = ChangeRunPhaseV1.BOOTSTRAPPED
        if (
            phase == ChangeRunPhaseV1.ACTIVATED
            and OperatorRunLinkKind.ACTIVATION_OPERATION not in kinds
        ):
            # The authority CAS can commit before its owned receipt/navigation link.
            # Keep the public run resumable until exact activation evidence is linked.
            phase = ChangeRunPhaseV1.READY_TO_ACTIVATE
        self._require_shape(run, phase)
        return run, phase

    def _temporal(
        self, run: OperatorRunView
    ) -> tuple[ChangeReviewEvidenceSummaryV1 | None, object | None]:
        links = self._links(run)
        link = links.get(OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST)
        if link is None:
            return None, None
        store = self._store()
        try:
            view = store.get_review_request(link.target_id)
        finally:
            store.close()
        if view.request.request_payload_sha256 != link.target_sha256:
            raise ApplicationReadModelError("temporal request differs from its navigation link")
        decision = view.decision
        if decision is not None and any(
            item.disposition == ReviewDisposition.EDITED for item in decision.items
        ):
            raise ApplicationReadModelError("public lifecycle does not support temporal EDIT")
        summary = ChangeReviewEvidenceSummaryV1(
            stage=ChangeReviewStageV1.TEMPORAL,
            request_id=view.request.request_id,
            request_sha256=view.request.request_payload_sha256,
            subject_count=len(view.request.subjects),
            decision_id=(view.request.request_id if decision is not None else None),
            decision_sha256=(decision.decision_payload_sha256 if decision is not None else None),
        )
        return summary, view

    def _managed(
        self, run: OperatorRunView
    ) -> tuple[ChangeReviewEvidenceSummaryV1 | None, object | None]:
        links = self._links(run)
        link = links.get(OperatorRunLinkKind.MANAGED_REVIEW_REQUEST)
        if link is None:
            return None, None
        store = self._store()
        try:
            record = store._read_request_record(link.target_id)  # noqa: SLF001
            decision_row = store.conn.execute(
                "SELECT 1 FROM change_control_managed_review_decisions WHERE request_id=?",
                (link.target_id,),
            ).fetchone()
            decision = store._read_decision_record(link.target_id) if decision_row else None  # noqa: SLF001
        finally:
            store.close()
        if record.record_sha256 != link.target_sha256:
            raise ApplicationReadModelError("managed request differs from its navigation link")
        if decision is not None and any(
            item.disposition == ManagedRevisionDisposition.EDIT for item in decision.command.items
        ):
            raise ApplicationReadModelError("public lifecycle does not support managed EDIT")
        summary = ChangeReviewEvidenceSummaryV1(
            stage=ChangeReviewStageV1.MANAGED,
            request_id=record.command.request_id,
            request_sha256=record.record_sha256,
            subject_count=len(record.command.bundle.targets),
            decision_id=(decision.command.decision_id if decision is not None else None),
            decision_sha256=(decision.record_sha256 if decision is not None else None),
        )
        return summary, record

    def get_change_status(self, run_id: str) -> ChangeRunStatusV1:
        run, phase = self._read_run(run_id)
        links = self._links(run)
        store = self._store()
        try:
            incoming: IncomingEvidenceSummaryV1 | None = None
            suite: RegressionSuiteEvidenceSummaryV1 | None = None
            baseline: GenerationZeroBaselineSummaryV1 | None = None
            incoming_link = links.get(OperatorRunLinkKind.INCOMING_SOURCE)
            if incoming_link is not None:
                row = store.conn.execute(
                    "SELECT intent_id FROM change_control_incoming_admission_receipts WHERE receipt_id=?",
                    (incoming_link.target_id,),
                ).fetchone()
                record = store.get_incoming_admission(str(row["intent_id"])) if row else None
                if record is None:
                    raise ApplicationReadModelError("incoming receipt cannot be reopened")
                bundle = self._resolver.resolve_incoming_source(record.intent)
                generic_repository = self._resolver._generic  # noqa: SLF001
                reopened = generic_repository.resolve_verified_evidence(
                    generic_repository.reopen(record.intent.bundle_id)
                )
                incoming = IncomingEvidenceSummaryV1(
                    receipt_id=record.receipt_id,
                    receipt_sha256=record.receipt_sha256,
                    bundle_id=bundle.bundle_id,
                    bundle_sha256=bundle.bundle_sha256,
                    admission_sha256=bundle.admission_sha256,
                    source_receipt_sha256=bundle.source_receipt_sha256,
                    projection_sha256=bundle.projection_sha256,
                    inference_sha256=bundle.inference_sha256,
                    source_byte_count=reopened.admission.source_byte_count,
                )
            suite_link = links.get(OperatorRunLinkKind.REGRESSION_SUITE)
            if suite_link is not None:
                row = store.conn.execute(
                    "SELECT intent_id FROM change_control_regression_suite_admission_receipts WHERE receipt_id=?",
                    (suite_link.target_id,),
                ).fetchone()
                suite_record = (
                    store.get_regression_suite_admission(str(row["intent_id"])) if row else None
                )
                if suite_record is None:
                    raise ApplicationReadModelError("suite receipt cannot be reopened")
                intent = suite_record.intent
                suite = RegressionSuiteEvidenceSummaryV1(
                    receipt_id=suite_record.receipt_id,
                    receipt_sha256=suite_record.receipt_sha256,
                    suite_id=intent.suite_id,
                    suite_version=intent.suite_version,
                    original_sha256=intent.original_sha256,
                    original_byte_count=intent.original_byte_count,
                    canonical_sha256=intent.canonical_sha256,
                    case_count=len(intent.suite.cases),
                )
            baseline_link = links.get(OperatorRunLinkKind.GENERATION_ZERO_BASELINE)
            if baseline_link is not None:
                baseline_record = store.get_generation_zero_baseline(baseline_link.target_id)
                if baseline_record is None:
                    raise ApplicationReadModelError("baseline receipt cannot be reopened")
                receipt = self._resolver.resolve_generation_zero_baseline(baseline_record)
                baseline = GenerationZeroBaselineSummaryV1(
                    baseline_id=receipt.baseline_id,
                    receipt_id=receipt.receipt_id,
                    receipt_sha256=receipt.receipt_sha256,
                    case_count=len(receipt.artifacts),
                    captured_at=receipt.captured_at,
                )
        finally:
            store.close()
        temporal, _ = self._temporal(run)
        managed, _ = self._managed(run)
        current = self._load_current_authority(run.record.command.aggregate_id)
        base = self._base_authority(run, current)
        activation = None
        activation_link = links.get(OperatorRunLinkKind.ACTIVATION_OPERATION)
        if activation_link is not None:
            activation = self._activation_summary(
                run=run,
                link=activation_link,
                base=base,
                current=current,
            )
        return ChangeRunStatusV1(
            run_id=run.record.command.run_id,
            phase=phase,
            outcome=_outcome(phase),
            next_action=_next_action(phase),
            created_at=run.record.created_at,
            base_authority=_authority(base, active=base.authority_id == current.authority_id),
            current_authority=_authority(current, active=True),
            incoming=incoming,
            suite=suite,
            baseline=baseline,
            temporal_review=temporal,
            managed_review=managed,
            activation=activation,
            completeness=ChangeEvidenceCompletenessV1(
                incoming_complete=incoming is not None,
                suite_complete=suite is not None,
                baseline_complete=baseline is not None,
                temporal_review_complete=temporal is not None and temporal.decision_id is not None,
                managed_review_complete=managed is not None and managed.decision_id is not None,
                activation_complete=activation is not None,
                regression_case_count=suite.case_count if suite else 0,
                temporal_subject_count=temporal.subject_count if temporal else 0,
                managed_subject_count=managed.subject_count if managed else 0,
            ),
        )

    def get_operator_run(self, run_id: str) -> OperatorRunView:
        """Reopen compatibility navigation through the same exact authority resolver."""

        run, _phase_value = self._read_run(run_id)
        return run

    def list_changes(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        phase: ChangeRunPhaseV1 | None = None,
    ) -> ChangeRunPageV1:
        store = self._store()
        try:
            page = store.list_operator_runs(
                limit=limit,
                cursor=cursor,
                phase=OperatorRunPhase(phase.value) if phase is not None else None,
                resolver=self._resolver,
            )
        finally:
            store.close()
        items = []
        for item in page.items:
            status = self.get_change_status(item.run.record.command.run_id)
            if status.phase.value != item.phase.value:
                raise ApplicationReadModelError("listed phase differs from exact status")
            items.append(
                ChangeRunSummaryV1(
                    run_id=status.run_id,
                    created_at=status.created_at,
                    phase=status.phase,
                    outcome=status.outcome,
                    next_action=status.next_action,
                    base_authority=status.base_authority,
                    current_authority=status.current_authority,
                )
            )
        return ChangeRunPageV1(items=tuple(items), next_cursor=page.next_cursor)

    def get_change_review(self, run_id: str) -> ChangeReviewPacketV1:
        run, phase = self._read_run(run_id)
        adoption_only = False
        governing_source_adoption_id: str | None = None
        governing_source_adoption_sha256: str | None = None
        if phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW:
            summary, view = self._temporal(run)
            if summary is None or view is None:
                raise ApplicationReadModelError("temporal review request is incomplete")
            subjects = []
            for snapshot in view.request.subjects:  # type: ignore[attr-defined]
                subject = snapshot.subject
                if isinstance(subject, DocumentReplacementAssessment):
                    statement = (
                        f"{subject.newer_document.document_version_id} supersedes "
                        f"{subject.older_document.document_version_id}"
                    )
                    kind = ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT
                elif isinstance(subject, TemporalConstraint):
                    statement = (
                        f"{subject.target.target_id} is valid until "
                        f"{subject.inferred_valid_to_exclusive.isoformat()}"
                    )
                    kind = ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT
                else:
                    raise ApplicationReadModelError("temporal request has an unknown subject")
                subjects.append(
                    ChangeReviewSubjectV1(
                        subject_id=snapshot.subject_id,
                        subject_sha256=snapshot.subject_sha256,
                        subject_kind=kind,
                        statement=statement,
                        rationale=subject.rationale,
                    )
                )
            stage = ChangeReviewStageV1.TEMPORAL
        elif phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW:
            summary, record = self._managed(run)
            if summary is None or record is None:
                raise ApplicationReadModelError("managed review request is incomplete")
            subjects = []
            for target in record.command.bundle.targets:  # type: ignore[attr-defined]
                subject = target.subject
                artifacts = {
                    artifact.artifact_id: artifact
                    for artifact in subject.inference_receipt.input_artifacts
                }
                citations = (
                    tuple(citation for hunk in subject.hunks for citation in hunk.citations)
                    if isinstance(subject, ManagedRevisionPlan)
                    else subject.citations
                )
                public_by_key: dict[tuple[str, int, int, str], ChangeReviewCitationV1] = {}
                for citation in citations:
                    artifact = artifacts.get(citation.artifact_id)
                    if artifact is None or artifact.sha256 != citation.artifact_sha256:
                        raise ApplicationReadModelError(
                            "managed citation does not bind an exact inference input"
                        )
                    artifact_bytes = self._resolver.open_managed_artifact(
                        run_id=run.record.command.run_id,
                        artifact=artifact,
                    )
                    if not (
                        len(artifact_bytes) == artifact.byte_count
                        and hashlib.sha256(artifact_bytes).hexdigest() == artifact.sha256
                        and citation.end_byte <= len(artifact_bytes)
                    ):
                        raise ApplicationReadModelError(
                            "managed citation artifact cannot be reopened exactly"
                        )
                    quote_bytes = citation.quote.encode("utf-8")
                    if artifact_bytes[citation.start_byte : citation.end_byte] != quote_bytes:
                        raise ApplicationReadModelError(
                            "managed citation quote differs from reopened artifact bytes"
                        )
                    projected = ChangeReviewCitationV1(
                        locator=artifact.path,
                        sha256=citation.artifact_sha256,
                        start_byte=citation.start_byte,
                        end_byte=citation.end_byte,
                        quote=citation.quote,
                    )
                    key = (
                        projected.locator,
                        projected.start_byte,
                        projected.end_byte,
                        projected.sha256,
                    )
                    existing = public_by_key.setdefault(key, projected)
                    if existing != projected:
                        raise ApplicationReadModelError(
                            "managed citations conflict at one exact artifact span"
                        )
                public_citations = tuple(public_by_key[key] for key in sorted(public_by_key))
                kind = (
                    ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN
                    if isinstance(subject, ManagedRevisionPlan)
                    else ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD
                )
                subjects.append(
                    ChangeReviewSubjectV1(
                        subject_id=target.target_id,
                        subject_sha256=target.target_sha256,
                        subject_kind=kind,
                        target_key=target.target_key,
                        document_version_id=target.predecessor.document_version_id,
                        statement=(
                            f"Revise {target.target_key}"
                            if isinstance(subject, ManagedRevisionPlan)
                            else f"No change required for {target.target_key}"
                        ),
                        rationale=subject.rationale,
                        citations=public_citations,
                    )
                )
            run_binding = record.command.bundle.run_binding  # type: ignore[attr-defined]
            adoption_only = bool(
                not record.command.bundle.targets  # type: ignore[attr-defined]
                and isinstance(run_binding, ManagedRunBindingV2)
                and isinstance(
                    run_binding.revision_planning_admission,
                    ManagedNoWorkPlanningAdmissionBinding,
                )
            )
            if adoption_only:
                governing_source_adoption_id = run_binding.governing_source_adoption.adoption_id
                governing_source_adoption_sha256 = (
                    run_binding.governing_source_adoption.adoption_sha256
                )
            stage = ChangeReviewStageV1.MANAGED
        else:
            raise ApplicationReviewUnavailableError("operator run is not awaiting human review")
        return ChangeReviewPacketV1(
            run_id=run.record.command.run_id,
            stage=stage,
            request_id=summary.request_id,
            request_sha256=summary.request_sha256,
            subjects=tuple(
                sorted(subjects, key=lambda item: (item.subject_kind.value, item.subject_id))
            ),
            adoption_only=adoption_only,
            governing_source_adoption_id=governing_source_adoption_id,
            governing_source_adoption_sha256=governing_source_adoption_sha256,
        )

    def verify_change(self, run_id: str) -> ChangeVerificationResultV1:
        run, _phase_value = self._read_run(run_id)
        status = self.get_change_status(run_id)
        if status.phase == ChangeRunPhaseV1.ACTIVATED:
            if self._activation_evidence_verifier is None:
                raise ApplicationReadModelError(
                    "activated verification requires a serving/index authority verifier"
                )
            self._activation_evidence_verifier(run, status)
        status_sha256 = hashlib.sha256(
            canonical_json_bytes(status.model_dump(mode="json"))
        ).hexdigest()
        return ChangeVerificationResultV1(
            run_id=status.run_id,
            phase=status.phase,
            outcome=status.outcome,
            status_sha256=status_sha256,
            status=status,
        )


__all__ = [
    "ActiveAuthorityLoader",
    "ActivationEvidenceVerifier",
    "ApplicationReadModelError",
    "ApplicationReviewUnavailableError",
    "ApplicationRunNotFoundError",
    "ApplicationReadModels",
]
