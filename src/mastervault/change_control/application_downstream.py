"""Stage-aware public review and guarded activation orchestration.

This module is deliberately additive: :mod:`application` delegates to these
functions lazily, which keeps the public facade free of lifecycle mechanics and
avoids an import cycle.  SQLite receipts remain authority; operator-run links
are appended only after the receipt they navigate to can be freshly reopened.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from mastervault.change_control.analysis_binding import (
    GenericAnalysisBootstrapBindingV2,
)
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationError,
    ChangeControlApplicationIntegrityError,
    ChangeControlApplicationReviewRequiredError,
    ChangeControlApplicationUsageError,
    raise_mapped_application_error,
)
from mastervault.change_control.application_inference_assets import (
    load_application_inference_assets_v1,
)
from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceIndexV1,
    LifecycleEvidenceOwnerV1,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.application_no_work import (
    NoWorkPlanningEvidenceRepository,
    NoWorkPlanningEvidenceV1,
)
from mastervault.change_control.application_provider_bridge import (
    SettingsRecordedInferenceProvider,
)
from mastervault.change_control.application_provider_calls import (
    ApplicationProviderCallJournal,
)
from mastervault.change_control.application_replay import (
    ApplicationReplayBundleRepository,
    ChangeReplayBundleV1,
    ChangeReplayStageV1,
)
from mastervault.change_control.application_runtime_identity import (
    application_configuration_sha256,
)
from mastervault.change_control.application_source_note_resolver import (
    GenericApplicationSourceNoteResolverLoader,
)
from mastervault.change_control.application_stage_evidence import (
    ApplicationStageEvidenceRepository,
    ImpactStageEvidenceV1,
    PlanningStageEvidenceV1,
)
from mastervault.change_control.application_start_command import (
    ApplicationStartCommandRepository,
)
from mastervault.change_control.change_application_contracts import (
    ActivateChangeRequestV1,
    AuthoritySummaryV1,
    ChangeActivationResultV1,
    ChangeReviewStageV1,
    ChangeReviewSubjectKindV1,
    ChangeRunOutcomeV1,
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
    ManagedAdoptionChoiceV1,
    ManagedReviewChoiceV1,
    ManagedReviewDecisionDocumentV1,
    ReviewDecisionDocumentV1,
    TemporalReviewChoiceV1,
    TemporalReviewDecisionDocumentV1,
)
from mastervault.change_control.generic_governing_source import (
    CompositeManagedReviewResolverV2,
    GenericGoverningSourceResolverV2,
    WorkspaceSourceNoteProjectionAuthority,
    derive_generic_governing_source_adoption_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
)
from mastervault.change_control.impact_analysis import build_impact_workload
from mastervault.change_control.impact_inference import (
    ImpactReplaySourceBinding,
    execute_impact_workload,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.legacy_index import (
    open_legacy_sqlite_index_attestation_guard,
)
from mastervault.change_control.managed_activation_service import (
    ManagedActivationOutcome,
    activate_reviewed_managed_generation,
)
from mastervault.change_control.managed_generation import ManagedGenerationActivationReceipt
from mastervault.change_control.managed_impact_evidence import (
    bind_recorded_impact_inference_run,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    InferenceExecutionMode,
    ManagedAdoptionChoice,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedInferenceContractBinding,
    ManagedRevisionDisposition,
    ManagedRevisionPlanningAdmissionAuthority,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_review_repository import (
    ApprovedManagedInferenceContractAuthority,
    ApprovedManagedRevisionPlanningAdmissionAuthority,
    RepositoryBackedManagedReviewResolver,
)
from mastervault.change_control.managed_review_service import (
    ManagedRevisionReviewSelection,
    decide_managed_revision_review,
    open_managed_revision_review,
)
from mastervault.change_control.managed_revision_admission import (
    bind_no_work_planning_admission,
    bind_recorded_revision_planning_run,
)
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
)
from mastervault.change_control.regression_baseline import GenerationZeroBaselineRepository
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectKind,
)
from mastervault.change_control.revision_planning_inference import (
    RevisionPlanningPredecessorSnapshot,
    RevisionPlanningReplaySourceBinding,
    execute_revision_planning,
)
from mastervault.change_control.store import ChangeControlBusyError, ChangeControlSnapshot
from mastervault.change_control.synchronous_lifecycle_store_models import (
    SynchronousApplicationOperationV1,
)
from mastervault.change_control.workspace_bootstrap import (
    create_workspace_bootstrap_evidence_verifier,
    verify_workspace_bootstrap_evidence,
)
from mastervault.change_control.workspace_bootstrap_repository import (
    open_workspace_bootstrap_evidence_guard,
)
from mastervault.config import Settings
from mastervault.providers import get_embedding_provider

FailureHook = Callable[[str], None]


class _Paths(Protocol):
    workspace: Path
    state_db: Path
    generation_root: Path
    vault: Path
    legacy_index: Path
    checkpoint_db: Path


class SimpleReviewReader:
    """Minimal adapter allowing packet validation after read locks are closed."""

    def __init__(self, packet: Any) -> None:
        self._packet = packet

    def get_change_review(self, run_id: str) -> Any:
        if self._packet.run_id != run_id:
            raise ChangeControlApplicationConflictError(
                "review packet belongs to another operator run"
            )
        return self._packet


class _PreparedNavigationResolver:
    """DB-free authority answers prepared before a coordinated writer opens."""

    def __init__(self) -> None:
        self.incoming: dict[str, Any] = {}
        self.temporal: dict[tuple[str, str, str], Any] = {}
        self.impact: dict[tuple[str, str, str], Any] = {}
        self.planning: dict[tuple[str, str, str], Any] = {}
        self.baselines: dict[str, Any] = {}
        self.mechanical_no_change: dict[tuple[str, str, str], Any] = {}

    def resolve_incoming_source(self, intent: Any) -> Any:
        value = self.incoming.get(intent.intent_id)
        if value is None:
            raise ChangeControlApplicationIntegrityError("prepared incoming authority is absent")
        return value

    def resolve_temporal_proposal(self, *, run_id: str, target_id: str, target_sha256: str) -> Any:
        value = self.temporal.get((run_id, target_id, target_sha256))
        if value is None:
            raise ChangeControlApplicationIntegrityError("prepared temporal authority is absent")
        return value

    def resolve_operator_impact_evidence(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> Any:
        value = self.impact.get((run_id, target_id, target_sha256))
        if value is None:
            raise ChangeControlApplicationIntegrityError("prepared impact authority is absent")
        return value

    def resolve_operator_revision_planning(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> Any:
        value = self.planning.get((run_id, target_id, target_sha256))
        if value is None:
            raise ChangeControlApplicationIntegrityError("prepared planning authority is absent")
        return value

    def resolve_generation_zero_baseline(self, record: Any) -> Any:
        receipt = record.baseline_receipt
        value = self.baselines.get(receipt.receipt_id)
        if value is None or value != receipt:
            raise ChangeControlApplicationIntegrityError("prepared baseline authority is absent")
        return value

    def resolve_operator_mechanical_no_change(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> Any:
        value = self.mechanical_no_change.get((run_id, target_id, target_sha256))
        if value is None:
            raise ChangeControlApplicationIntegrityError(
                "prepared mechanical no-change authority is absent"
            )
        return value


def _prepare_navigation(
    *,
    state_path: Path,
    run_id: str,
    resolver: Any,
) -> tuple[Any, _PreparedNavigationResolver]:
    """Reopen every linked owner before opening the later writer connection."""

    prepared = _PreparedNavigationResolver()
    store = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
    try:
        run = store.get_operator_run(run_id, resolver=resolver)
        if run is None:
            raise ChangeControlApplicationIntegrityError("operator run does not exist")
        for record in run.links:
            link = record.command
            key = (run_id, link.target_id, link.target_sha256)
            if link.kind == OperatorRunLinkKind.INCOMING_SOURCE:
                row = store.conn.execute(
                    "SELECT intent_id FROM change_control_incoming_admission_receipts "
                    "WHERE receipt_id=?",
                    (link.target_id,),
                ).fetchone()
                incoming = (
                    store.get_incoming_admission(str(row["intent_id"])) if row is not None else None
                )
                if incoming is None:
                    raise ChangeControlApplicationIntegrityError(
                        "incoming navigation record is absent"
                    )
                prepared.incoming[incoming.intent.intent_id] = resolver.resolve_incoming_source(
                    incoming.intent
                )
            elif link.kind == OperatorRunLinkKind.TEMPORAL_PROPOSAL:
                prepared.temporal[key] = resolver.resolve_temporal_proposal(
                    run_id=run_id,
                    target_id=link.target_id,
                    target_sha256=link.target_sha256,
                )
            elif link.kind == OperatorRunLinkKind.IMPACT_EVIDENCE:
                prepared.impact[key] = resolver.resolve_operator_impact_evidence(
                    run_id=run_id,
                    target_id=link.target_id,
                    target_sha256=link.target_sha256,
                )
            elif link.kind == OperatorRunLinkKind.REVISION_PLANNING:
                prepared.planning[key] = resolver.resolve_operator_revision_planning(
                    run_id=run_id,
                    target_id=link.target_id,
                    target_sha256=link.target_sha256,
                )
            elif link.kind == OperatorRunLinkKind.GENERATION_ZERO_BASELINE:
                baseline = store.get_generation_zero_baseline(link.target_id)
                if baseline is None:
                    raise ChangeControlApplicationIntegrityError(
                        "baseline navigation record is absent"
                    )
                prepared.baselines[link.target_id] = resolver.resolve_generation_zero_baseline(
                    baseline
                )
            elif link.kind == OperatorRunLinkKind.MECHANICAL_NO_CHANGE:
                prepared.mechanical_no_change[key] = resolver.resolve_operator_mechanical_no_change(
                    run_id=run_id,
                    target_id=link.target_id,
                    target_sha256=link.target_sha256,
                )
        return run, prepared
    finally:
        store.close()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _notify(hook: FailureHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _derived_operation_id(operation_id: str, stage: str) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.synchronous-downstream.v1",
                "parent_operation_id": operation_id,
                "stage": stage,
            }
        )
    ).hexdigest()
    return f"change-downstream:{stage}:{digest}"


def _link(
    *,
    store: SqliteManagedChangeControlStore,
    resolver: Any,
    run_id: str,
    parent_operation_id: str,
    kind: OperatorRunLinkKind,
    target_id: str,
    target_sha256: str,
) -> None:
    command = OperatorRunLinkCommand.create(
        operation_id=_derived_operation_id(parent_operation_id, f"link-{kind.value}"),
        run_id=run_id,
        kind=kind,
        target_id=target_id,
        target_sha256=target_sha256,
    )
    store.record_operator_run_link(command, resolver=resolver)


def _index_stage(
    *,
    evidence_root: Path,
    run_id: str,
    stage: LifecycleEvidenceStageV1,
    owner_kind: str,
    owner_id: str,
    owner_sha256: str,
    relative_locator: str,
) -> None:
    index = FilesystemLifecycleEvidenceIndex(evidence_root)
    index.persist(
        LifecycleEvidenceIndexV1.create(
            run_id=run_id,
            stage=stage,
            owners=(
                LifecycleEvidenceOwnerV1(
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    owner_sha256=owner_sha256,
                    relative_locator=relative_locator,
                ),
            ),
            recorded_at=_now(),
        )
    )


def _authority_summary(status: ChangeRunStatusV1) -> AuthoritySummaryV1:
    authority = status.current_authority
    if authority is None:
        raise ChangeControlApplicationIntegrityError(
            "activated run does not expose its exact current authority"
        )
    return authority


@contextmanager
def _write_runtime(settings: Settings) -> Iterator[tuple[Any, _Paths, Any, Any, Any]]:
    """Retain workspace/index guards without retaining a state-store read lock."""

    # Lazy import is intentional: application.py delegates to this module.
    from mastervault.change_control.application import (  # noqa: PLC0415
        ChangeControlApplication,
        _configured_source_roots,
        _manifest_path,
        _preflight_paths,
    )

    app = ChangeControlApplication(settings)
    app._preflight_backend()  # noqa: SLF001
    paths = cast(_Paths, _preflight_paths(settings))
    configured_manifest = settings.query_generation.bootstrap_manifest
    if configured_manifest is None:
        raise ChangeControlApplicationUsageError(
            "downstream lifecycle requires query_generation.bootstrap_manifest"
        )
    manifest_path = _manifest_path(paths, configured_manifest)  # type: ignore[arg-type]
    source_roots = _configured_source_roots(settings)
    locator = SqliteManagedChangeControlStore(paths.state_db, secure_open=True, read_only=True)
    try:
        rows = locator.conn.execute(
            "SELECT bootstrap_id FROM change_control_workspace_bootstrap_intents "
            "ORDER BY bootstrap_id"
        ).fetchall()
        if len(rows) != 1:
            raise ChangeControlApplicationIntegrityError(
                "downstream lifecycle requires one exact workspace bootstrap"
            )
        state = locator.get_workspace_bootstrap(str(rows[0]["bootstrap_id"]))
        if state is None:
            raise ChangeControlApplicationIntegrityError(
                "workspace bootstrap locator cannot be reopened"
            )
        _inventory_receipt, readiness = state.require_complete()
    finally:
        locator.close()

    workspace_guard = open_workspace_bootstrap_evidence_guard(
        workspace_root=paths.workspace,
        manifest_path=manifest_path,
        source_roots=source_roots,
        index_schema_version=readiness.index_schema_version,
        embedding_model=readiness.embedding_model,
        embedding_dimensions=readiness.embedding_dimensions,
    )
    index_guard = None
    try:
        resolved = workspace_guard.resolved
        expected = resolved.inventory.legacy_index
        index_guard = open_legacy_sqlite_index_attestation_guard(
            index_path=resolved.legacy_index_path,
            notes=resolved.exact_vault_notes,
            embedding_model_version=expected.embedding_model,
            embedding_dimensions=expected.embedding_dimensions,
            expected_index_file_sha256=expected.index_file_sha256,
            expected_index_file_byte_count=expected.index_file_byte_count,
        )
        authority = SqliteManagedChangeControlStore(
            paths.state_db, secure_open=True, read_only=True
        )
        try:
            state = authority.get_workspace_bootstrap_by_inventory_id(
                resolved.inventory.inventory_id
            )
            if state is None:
                raise ChangeControlApplicationIntegrityError(
                    "workspace inventory has no durable bootstrap owner"
                )
            inventory_receipt, _readiness = state.require_complete()
            commit = authority.get_operation_commit(inventory_receipt.aggregate_operation_id)
            if commit is None:
                raise ChangeControlApplicationIntegrityError(
                    "workspace aggregate receipt is absent"
                )
            capability = verify_workspace_bootstrap_evidence(
                state=state,
                resolved_inventory=resolved.inventory,
                resolved_aggregate=resolved.aggregate,
                persisted_snapshot=ChangeControlSnapshot(
                    aggregate=resolved.aggregate,
                    revision=commit.revision,
                    aggregate_sha256=commit.aggregate_sha256,
                ),
                legacy_attestation=index_guard.attestation,
                evidence_verifier=create_workspace_bootstrap_evidence_verifier(
                    workspace_guard, index_guard
                ),
            )
        finally:
            authority.close()
        loader = GenericApplicationSourceNoteResolverLoader(
            evidence_root=settings.paths.change_control_evidence_root,
            workspace_capability=capability,
            workspace_source_notes=tuple(item.snapshot for item in resolved.managed_source_notes),
        )
        from mastervault.change_control.application_authority_resolver import (  # noqa: PLC0415
            ApplicationOperatorRunAuthorityResolver,
        )

        resolver = ApplicationOperatorRunAuthorityResolver(
            evidence_root=settings.paths.change_control_evidence_root,
            state_path=paths.state_db,
            source_note_resolver=loader,
            configuration_sha256=application_configuration_sha256(settings),
        )
        context = AuthorityVerificationContext.workspace(capability)
        yield app, paths, resolver, (loader, context), resolved
        index_guard.verify()
        workspace_guard.verify()
    finally:
        if index_guard is not None:
            index_guard.close()
        workspace_guard.close()


def _require_exact_packet(reads: Any, document: ReviewDecisionDocumentV1) -> Any:
    packet = reads.get_change_review(document.run_id)
    if not (
        packet.stage == document.stage
        and packet.request_id == document.request_id
        and packet.request_sha256 == document.request_sha256
    ):
        raise ChangeControlApplicationConflictError(
            "review decision differs from the current exact review request"
        )
    expected = tuple(
        (item.subject_kind, item.subject_id, item.subject_sha256) for item in packet.subjects
    )
    supplied = tuple(
        (item.subject_kind, item.subject_id, item.subject_sha256) for item in document.decisions
    )
    if supplied != expected:
        raise ChangeControlApplicationConflictError(
            "review decision must bind every current subject in exact canonical order"
        )
    return packet


def _record_temporal(
    *,
    store: SqliteManagedChangeControlStore,
    resolver: Any,
    document: TemporalReviewDecisionDocumentV1,
) -> None:
    view = store.get_review_request(document.request_id)
    if view.request.request_payload_sha256 != document.request_sha256:
        raise ChangeControlApplicationConflictError(
            "temporal decision request SHA differs from SQLite authority"
        )
    command = _temporal_command(document)
    receipt = store.decide_review(
        command,
        operation_id=document.operation_id,
    )
    _link(
        store=store,
        resolver=resolver,
        run_id=document.run_id,
        parent_operation_id=document.operation_id,
        kind=OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION,
        target_id=receipt.decision.request_id,
        target_sha256=receipt.decision.decision_payload_sha256,
    )


def _temporal_command(
    document: TemporalReviewDecisionDocumentV1,
) -> HumanReviewDecisionCommand:
    items = tuple(
        ReviewDecisionItem(
            kind=(
                ReviewSubjectKind.DOCUMENT_REPLACEMENT
                if item.subject_kind == ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT
                else ReviewSubjectKind.TEMPORAL_CONSTRAINT
            ),
            subject_id=item.subject_id,
            original_subject_sha256=item.subject_sha256,
            disposition=(
                ReviewDisposition.ACCEPTED
                if item.choice == TemporalReviewChoiceV1.ACCEPT
                else ReviewDisposition.REJECTED
            ),
        )
        for item in document.decisions
    )
    return HumanReviewDecisionCommand(
        request_id=document.request_id,
        reviewer_id=document.reviewer_id,
        rationale=document.rationale,
        items=items,
    )


def _exact_completed_review_replay(
    *, settings: Settings, document: ReviewDecisionDocumentV1
) -> bool:
    """Recognize a later-stage retry without invoking providers or repairing links."""

    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path,
        secure_open=True,
        read_only=True,
    )
    try:
        if document.stage == ChangeReviewStageV1.TEMPORAL:
            view = store.get_review_request(document.request_id)
            temporal_decision = view.decision
            expected_command = _temporal_command(document)
            return bool(
                temporal_decision is not None
                and temporal_decision.operation_id == document.operation_id
                and temporal_decision.reviewer_id == expected_command.reviewer_id
                and temporal_decision.rationale == expected_command.rationale
                and temporal_decision.items == expected_command.items
            )
        record = store._read_request_record(document.request_id)  # noqa: SLF001
        managed_decision = store._read_decision_record(document.request_id)  # noqa: SLF001
        if (
            managed_decision is None
            or managed_decision.command.operation_id != document.operation_id
        ):
            return False
        expected_items = tuple(
            (
                item.subject_id,
                item.subject_sha256,
                {
                    ManagedReviewChoiceV1.APPROVE: ManagedRevisionDisposition.APPROVE,
                    ManagedReviewChoiceV1.REJECT: ManagedRevisionDisposition.REJECT,
                    ManagedReviewChoiceV1.CONFIRM_NO_CHANGE: (
                        ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                    ),
                }[item.choice],
            )
            for item in document.decisions
        )
        actual_items = tuple(
            (item.target_id, item.original_target_sha256, item.disposition)
            for item in managed_decision.command.items
        )
        return bool(
            record.record_sha256 == document.request_sha256
            and managed_decision.command.reviewer_id == document.reviewer_id
            and managed_decision.command.rationale == document.rationale
            and actual_items == expected_items
            and managed_decision.command.adoption_choice
            == (
                {
                    ManagedAdoptionChoiceV1.ADOPT: ManagedAdoptionChoice.ADOPT,
                    ManagedAdoptionChoiceV1.REJECT: ManagedAdoptionChoice.REJECT,
                }[document.adoption_choice]
                if document.adoption_choice is not None
                else None
            )
        )
    finally:
        store.close()


def _has_operator_link(*, settings: Settings, run_id: str, kind: OperatorRunLinkKind) -> bool:
    """Inspect navigation presence only; authority is reopened before any repair."""

    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path,
        secure_open=True,
        read_only=True,
    )
    try:
        row = store.conn.execute(
            "SELECT 1 FROM change_control_operator_run_links WHERE run_id=? AND link_kind=?",
            (run_id, kind.value),
        ).fetchone()
        return row is not None
    finally:
        store.close()


def _managed_resolver(
    *,
    settings: Settings,
    run_id: str,
    resolver: Any,
    loader: GenericApplicationSourceNoteResolverLoader,
    run_binding: ManagedRunBindingV2,
    resolved_workspace: Any,
) -> CompositeManagedReviewResolverV2:
    canonical_root = settings.query_generation.canonical_repository_root
    if canonical_root is None:
        raise ChangeControlApplicationUsageError(
            "downstream lifecycle requires query_generation.canonical_repository_root"
        )
    reviewed = resolver._reviewed_snapshot(run_id)  # noqa: SLF001
    source_resolver = loader(run_id)
    analysis_capability = source_resolver.verified_bootstrap
    evidence_root = settings.paths.change_control_evidence_root
    inference = FilesystemInferenceEvidenceRepository(evidence_root, create=False, read_only=True)
    generic_repository = FilesystemGenericIncomingRepositoryV2(
        evidence_root, create=False, read_only=True
    )
    binding = run_binding.analysis_set.analysis_bootstrap
    if type(binding) is not GenericAnalysisBootstrapBindingV2:
        raise ChangeControlApplicationIntegrityError(
            "managed operator run does not bind generic analysis authority"
        )
    generic_evidence = generic_repository.reopen(binding.incoming_bundle_id)
    generic = GenericGoverningSourceResolverV2(
        reviewed_snapshot=reviewed,
        analysis_capability=analysis_capability,
        repository=generic_repository,
        evidence_capability=generic_evidence,
    )
    assets_path = (
        f"inference/algorithms/{run_binding.inference_contract.algorithm_manifest_sha256}.json"
    )
    algorithm_payload = inference._read_optional(  # noqa: SLF001
        assets_path,
        limit=1024 * 1024,
        label="synchronous inference algorithm manifest",
    )
    if algorithm_payload is None:
        raise ChangeControlApplicationIntegrityError(
            "recorded inference algorithm manifest is absent"
        )
    algorithm_artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path=assets_path,
        sha256=hashlib.sha256(algorithm_payload).hexdigest(),
        byte_count=len(algorithm_payload),
    )
    if algorithm_artifact.sha256 != run_binding.inference_contract.algorithm_manifest_sha256:
        raise ChangeControlApplicationIntegrityError(
            "recorded inference algorithm differs from the run contract"
        )
    sealed = RepositoryBackedManagedReviewResolver(
        evidence_repository=inference,
        staging_repository=ManagedStagingRepository(evidence_root, create=False, read_only=True),
        canonical_root=canonical_root,
        approved_contracts=(
            ApprovedManagedInferenceContractAuthority(
                contract=run_binding.inference_contract,
                algorithm_manifest_bytes=algorithm_payload,
            ),
        ),
        approved_revision_admissions=(
            ApprovedManagedRevisionPlanningAdmissionAuthority(
                admission=run_binding.revision_planning_admission,
                reviewed_snapshot=reviewed,
            ),
        ),
    )
    workspace_authorities: list[WorkspaceSourceNoteProjectionAuthority] = []
    for item in resolved_workspace.managed_source_notes:
        raw_bytes = item.raw_source_bytes
        note_bytes = item.snapshot.source_note_utf8.encode("utf-8")
        workspace_authorities.append(
            WorkspaceSourceNoteProjectionAuthority(
                metadata=item.metadata,
                snapshot=item.snapshot,
                raw_artifact=ManagedArtifactRef.create(
                    kind=ManagedArtifactKind.RAW_SOURCE,
                    path=item.snapshot.document.source_path,
                    sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    byte_count=len(raw_bytes),
                ),
                raw_bytes=raw_bytes,
                note_artifact=ManagedArtifactRef.create(
                    kind=ManagedArtifactKind.SOURCE_NOTE,
                    path=item.snapshot.source_note_path,
                    sha256=hashlib.sha256(note_bytes).hexdigest(),
                    byte_count=len(note_bytes),
                ),
                note_bytes=note_bytes,
                projected_claims=tuple(
                    revision
                    for revision in resolved_workspace.aggregate.claims.revisions
                    if revision.document == item.snapshot.document
                ),
            )
        )
    result = CompositeManagedReviewResolverV2(
        sealed=sealed,
        generic=generic,
        workspace_projection_authorities=tuple(workspace_authorities),
    )
    if (
        result.resolve_revision_planning_admission(run_binding.revision_planning_admission)
        != run_binding.revision_planning_admission
        or result.resolve_governing_source_adoption(run_binding.governing_source_adoption)
        != run_binding.governing_source_adoption
    ):
        raise ChangeControlApplicationIntegrityError(
            "managed resolver did not reopen exact run authority"
        )
    return result


def _record_managed(
    *,
    settings: Settings,
    store: SqliteManagedChangeControlStore,
    navigation_resolver: Any,
    loader: GenericApplicationSourceNoteResolverLoader,
    context: AuthorityVerificationContext,
    document: ManagedReviewDecisionDocumentV1,
    managed_resolver: CompositeManagedReviewResolverV2 | None = None,
) -> None:
    record = store._read_request_record(document.request_id)  # noqa: SLF001
    if record.record_sha256 != document.request_sha256:
        raise ChangeControlApplicationConflictError(
            "managed decision request SHA differs from SQLite authority"
        )
    run_binding = record.command.bundle.run_binding
    if type(run_binding) is not ManagedRunBindingV2 or run_binding.run_id != document.run_id:
        raise ChangeControlApplicationIntegrityError(
            "managed request does not bind this exact generic operator run"
        )
    if managed_resolver is None:
        raise ChangeControlApplicationIntegrityError(
            "managed review resolver was not prepared before the writer opened"
        )
    mapping = {
        ManagedReviewChoiceV1.APPROVE: ManagedRevisionDisposition.APPROVE,
        ManagedReviewChoiceV1.REJECT: ManagedRevisionDisposition.REJECT,
        ManagedReviewChoiceV1.CONFIRM_NO_CHANGE: (ManagedRevisionDisposition.CONFIRM_NO_CHANGE),
    }
    decided = decide_managed_revision_review(
        store=store,
        request_id=document.request_id,
        selections=tuple(
            ManagedRevisionReviewSelection(
                target_id=item.subject_id,
                disposition=mapping[item.choice],
            )
            for item in document.decisions
        ),
        adoption_choice=(
            {
                ManagedAdoptionChoiceV1.ADOPT: ManagedAdoptionChoice.ADOPT,
                ManagedAdoptionChoiceV1.REJECT: ManagedAdoptionChoice.REJECT,
            }[document.adoption_choice]
            if document.adoption_choice is not None
            else None
        ),
        resolver=managed_resolver,
        prechange_head=run_binding.prechange_head,
        authority_context=context,
        operation_id=document.operation_id,
        reviewer_id=document.reviewer_id,
        rationale=document.rationale,
    )
    decision = decided.decision_record
    if decision is None:
        raise ChangeControlApplicationIntegrityError(
            "managed decision receipt did not reopen after commit"
        )
    _link(
        store=store,
        resolver=navigation_resolver,
        run_id=document.run_id,
        parent_operation_id=document.operation_id,
        kind=OperatorRunLinkKind.MANAGED_REVIEW_DECISION,
        target_id=decision.command.decision_id,
        target_sha256=decision.record_sha256,
    )


def _replay_sources(
    *,
    bundle: ChangeReplayBundleV1,
    stage: ChangeReplayStageV1,
    evidence: FilesystemInferenceEvidenceRepository,
) -> tuple[tuple[str, str, ManagedArtifactRef], ...]:
    """Reopen refs, then let task execution rebind them to locally derived inputs."""

    stage_evidence = next(item for item in bundle.stages if item.stage == stage)
    reopened = tuple(
        (
            ref,
            evidence.resolve_replay_evidence(receipt_artifact=ref.recorded_inference_receipt()),
        )
        for ref in stage_evidence.artifacts
    )
    bundle.require_exact_stage(
        stage,
        tuple(item.execution.input_envelope.input_shard_sha256 for _ref, item in reopened),
    )
    return tuple(
        sorted(
            (
                item.execution.input_envelope.input_shard_id,
                item.execution.input_envelope.input_shard_sha256,
                ref.recorded_inference_receipt(),
            )
            for ref, item in reopened
        )
    )


def _advance_accepted_temporal(
    *,
    settings: Settings,
    paths: _Paths,
    document: TemporalReviewDecisionDocumentV1,
    navigation_resolver: Any,
    loader: GenericApplicationSourceNoteResolverLoader,
    context: AuthorityVerificationContext,
    resolved_workspace: Any,
    failure_hook: FailureHook | None,
) -> None:
    """Run and durably admit downstream work after temporal acceptance."""

    if not any(item.choice == TemporalReviewChoiceV1.ACCEPT for item in document.decisions):
        return
    command = ApplicationStartCommandRepository(
        settings.paths.change_control_evidence_root,
        create=False,
        read_only=True,
    ).reopen_run(document.run_id)
    replay_bundle = (
        ApplicationReplayBundleRepository(
            settings.paths.change_control_evidence_root,
            create=False,
            read_only=True,
        ).reopen_by_run(document.run_id)
        if command.mode.value == "replay"
        else None
    )
    if replay_bundle is not None and not (
        replay_bundle.bundle_id == command.replay_bundle_id
        and replay_bundle.bundle_sha256 == command.replay_bundle_sha256
    ):
        raise ChangeControlApplicationIntegrityError(
            "reopened replay bundle differs from the exact start command"
        )
    if replay_bundle is not None:
        incoming_index = FilesystemLifecycleEvidenceIndex(
            settings.paths.change_control_evidence_root,
            create=False,
            read_only=True,
        ).reopen(document.run_id, LifecycleEvidenceStageV1.INCOMING)
        incoming_owners = tuple(
            owner for owner in incoming_index.owners if owner.owner_kind == "generic-bundle"
        )
        if len(incoming_owners) != 1 or not (
            incoming_owners[0].owner_id == replay_bundle.incoming_bundle_id
            and incoming_owners[0].owner_sha256 == replay_bundle.incoming_bundle_sha256
        ):
            raise ChangeControlApplicationIntegrityError(
                "replay bundle incoming authority differs from the current run"
            )
    reviewed = navigation_resolver._reviewed_snapshot(document.run_id)  # noqa: SLF001
    reviewed.verify()
    evidence_root = settings.paths.change_control_evidence_root
    evidence = FilesystemInferenceEvidenceRepository(evidence_root)
    staging = ManagedStagingRepository(evidence_root)
    assets = load_application_inference_assets_v1()
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=assets.algorithm_manifest_sha256,
        contract_id=assets.contract_id,
        contract_version=assets.contract_version,
        mode=(
            InferenceExecutionMode.REPLAY
            if replay_bundle is not None
            else InferenceExecutionMode.LIVE
        ),
        provider=settings.llm.provider,
        model=settings.llm.model_medium,
        prompt_sha256=assets.prompt_sha256,
        response_schema_sha256=assets.response_schema_sha256,
    )
    provider = (
        SettingsRecordedInferenceProvider(
            settings,
            contract,
            journal=ApplicationProviderCallJournal(evidence_root),
            owner_id=command.command_id,
            run_id=document.run_id,
        )
        if replay_bundle is None
        else None
    )
    impact_workload = build_impact_workload(reviewed)
    impact_replay = (
        tuple(
            ImpactReplaySourceBinding(
                input_shard_id=input_id,
                input_shard_sha256=input_sha,
                receipt_artifact=receipt,
            )
            for input_id, input_sha, receipt in _replay_sources(
                bundle=replay_bundle,
                stage=ChangeReplayStageV1.IMPACT,
                evidence=evidence,
            )
        )
        if replay_bundle is not None and impact_workload.input_shards
        else ()
    )
    if replay_bundle is not None and not impact_workload.input_shards:
        replay_bundle.require_exact_stage(ChangeReplayStageV1.IMPACT, ())
    impact_run = execute_impact_workload(
        reviewed,
        workload=impact_workload,
        contract=contract,
        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
        prompt_bytes=assets.prompt_bytes,
        response_schema_bytes=assets.response_schema_bytes,
        evidence_repository=evidence,
        provider=provider,
        replay_sources=impact_replay,
    )
    impact_binding = (
        bind_recorded_impact_inference_run(impact_run)
        if impact_run.results.workload.questions
        else None
    )
    if impact_binding is not None:
        impact_receipt = ApplicationStageEvidenceRepository(evidence_root).persist_impact(
            ImpactStageEvidenceV1.create(
                run_id=document.run_id,
                reviewed_snapshot_binding_id=reviewed.binding.binding_id,
                reviewed_snapshot_binding_sha256=reviewed.binding.binding_sha256,
                configuration_sha256=application_configuration_sha256(settings),
                results=impact_run.results,
                binding=impact_binding,
                recorded_at=_now(),
            )
        )
        _index_stage(
            evidence_root=evidence_root,
            run_id=document.run_id,
            stage=LifecycleEvidenceStageV1.IMPACT,
            owner_kind="impact-stage-evidence",
            owner_id=impact_receipt.evidence_id,
            owner_sha256=impact_receipt.evidence_sha256,
            relative_locator=ApplicationStageEvidenceRepository.relative_locator(
                document.run_id, "impact"
            ),
        )
        prepared = _PreparedNavigationResolver()
        prepared.impact[
            (
                document.run_id,
                impact_binding.evidence_binding_id,
                impact_binding.evidence_binding_sha256,
            )
        ] = impact_binding
        writer = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
        try:
            _link(
                store=writer,
                resolver=prepared,
                run_id=document.run_id,
                parent_operation_id=document.operation_id,
                kind=OperatorRunLinkKind.IMPACT_EVIDENCE,
                target_id=impact_binding.evidence_binding_id,
                target_sha256=impact_binding.evidence_binding_sha256,
            )
        finally:
            writer.close()
        _notify(failure_hook, "impact-linked")

    raw_by_document = {
        item.snapshot.document.document_version_id: item
        for item in resolved_workspace.managed_source_notes
    }
    eligible_documents = {
        item.document_version_id
        for item in impact_run.results.output_shards
        if item.document_disposition.value != "UNRESOLVED"
    }
    snapshots = tuple(
        RevisionPlanningPredecessorSnapshot(
            target_key=item.snapshot.document.document_id,
            raw_path=item.snapshot.document.source_path,
            raw_bytes=item.raw_source_bytes,
            source_note_path=item.snapshot.source_note_path,
            source_note_bytes=item.snapshot.source_note_utf8.encode("utf-8"),
        )
        for document_id, item in sorted(raw_by_document.items())
        if document_id in eligible_documents
    )
    planning_replay = (
        tuple(
            RevisionPlanningReplaySourceBinding(
                input_shard_id=input_id,
                input_shard_sha256=input_sha,
                receipt_artifact=receipt,
            )
            for input_id, input_sha, receipt in _replay_sources(
                bundle=replay_bundle,
                stage=ChangeReplayStageV1.PLANNING,
                evidence=evidence,
            )
        )
        if replay_bundle is not None
        else ()
    )
    planning_run = execute_revision_planning(
        run_id=document.run_id,
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=contract,
        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
        prompt_bytes=assets.prompt_bytes,
        response_schema_bytes=assets.response_schema_bytes,
        evidence_repository=evidence,
        staging_repository=staging,
        provider=provider if snapshots else None,
        replay_sources=planning_replay,
    )
    adoption_only_admission = None
    if not planning_run.subjects:
        governing = impact_run.results.workload.index.binding.governing_changes
        no_work = NoWorkPlanningEvidenceRepository(evidence_root).persist(
            NoWorkPlanningEvidenceV1.create(
                run_id=document.run_id,
                reviewed_snapshot_binding_id=reviewed.binding.binding_id,
                reviewed_snapshot_binding_sha256=reviewed.binding.binding_sha256,
                impact_evidence_binding_id=(
                    impact_binding.evidence_binding_id if impact_binding else None
                ),
                impact_evidence_binding_sha256=(
                    impact_binding.evidence_binding_sha256 if impact_binding else None
                ),
                configuration_sha256=application_configuration_sha256(settings),
                impact_input_shards=impact_run.results.workload.input_shards,
                impact_output_shards=impact_run.results.output_shards,
                workload=planning_run.workload,
                recorded_at=_now(),
            )
        )
        _index_stage(
            evidence_root=evidence_root,
            run_id=document.run_id,
            stage=LifecycleEvidenceStageV1.PLANNING,
            owner_kind="no-work-planning",
            owner_id=no_work.evidence_id,
            owner_sha256=no_work.evidence_sha256,
            relative_locator=NoWorkPlanningEvidenceRepository.relative_locator(
                document.run_id,
                no_work.workload.workload_id,
                no_work.workload.workload_sha256,
            ),
        )
        if governing:
            adoption_only_admission = bind_no_work_planning_admission(
                no_work,
                reviewed_snapshot=reviewed,
                evidence_repository=evidence,
            )
        planning_id = (
            adoption_only_admission.admission_id
            if adoption_only_admission is not None
            else no_work.workload.workload_id
        )
        planning_sha256 = (
            adoption_only_admission.admission_sha256
            if adoption_only_admission is not None
            else no_work.workload.workload_sha256
        )
        prepared = _PreparedNavigationResolver()
        prepared.planning[(document.run_id, planning_id, planning_sha256)] = (
            adoption_only_admission if adoption_only_admission is not None else no_work.workload
        )
        writer = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
        try:
            _link(
                store=writer,
                resolver=prepared,
                run_id=document.run_id,
                parent_operation_id=document.operation_id,
                kind=OperatorRunLinkKind.REVISION_PLANNING,
                target_id=planning_id,
                target_sha256=planning_sha256,
            )
        finally:
            writer.close()
        if adoption_only_admission is None:
            return

    admission: ManagedRevisionPlanningAdmissionAuthority
    if adoption_only_admission is not None:
        admission = adoption_only_admission
    else:
        admission = bind_recorded_revision_planning_run(
            planning_run,
            reviewed_snapshot=reviewed,
            evidence_repository=evidence,
            staging_repository=staging,
        )
        planning_receipt = ApplicationStageEvidenceRepository(evidence_root).persist_planning(
            PlanningStageEvidenceV1.create(
                run_id=document.run_id,
                temporal_analysis_manifest_id=reviewed.temporal_analysis.manifest_id,
                temporal_analysis_manifest_sha256=reviewed.temporal_analysis.manifest_sha256,
                temporal_request_id=document.request_id,
                binding=admission,
                recorded_at=_now(),
            )
        )
        _index_stage(
            evidence_root=evidence_root,
            run_id=document.run_id,
            stage=LifecycleEvidenceStageV1.PLANNING,
            owner_kind="planning-stage-evidence",
            owner_id=planning_receipt.evidence_id,
            owner_sha256=planning_receipt.evidence_sha256,
            relative_locator=ApplicationStageEvidenceRepository.relative_locator(
                document.run_id, "planning"
            ),
        )
        prepared = _PreparedNavigationResolver()
        prepared.planning[(document.run_id, admission.admission_id, admission.admission_sha256)] = (
            admission
        )
        writer = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
        try:
            _link(
                store=writer,
                resolver=prepared,
                run_id=document.run_id,
                parent_operation_id=document.operation_id,
                kind=OperatorRunLinkKind.REVISION_PLANNING,
                target_id=admission.admission_id,
                target_sha256=admission.admission_sha256,
            )
        finally:
            writer.close()
    source_resolver = loader(document.run_id)
    generic_repository = FilesystemGenericIncomingRepositoryV2(
        evidence_root, create=False, read_only=True
    )
    bootstrap = source_resolver.verified_bootstrap
    generic_evidence = generic_repository.reopen(bootstrap.binding.incoming_bundle_id)
    adoption = derive_generic_governing_source_adoption_v2(
        reviewed_snapshot=reviewed,
        analysis_capability=bootstrap,
        repository=generic_repository,
        evidence_capability=generic_evidence,
    )
    analysis_set = admission.analysis_set
    analysis_bootstrap = analysis_set.analysis_bootstrap
    prechange = AggregateHeadBinding.create(
        aggregate_id=analysis_bootstrap.aggregate_id,
        revision=analysis_bootstrap.prechange_revision,
        aggregate_sha256=analysis_bootstrap.prechange_aggregate_sha256,
    )
    run_binding = ManagedRunBindingV2.create(
        run_id=document.run_id,
        operation_id=reviewed.temporal_commit.operation_id,
        prechange_head=prechange,
        analysis_head=reviewed.binding.analysis_head,
        algorithm_manifest_sha256=assets.algorithm_manifest_sha256,
        inference_contract=contract,
        analysis_set=analysis_set,
        revision_planning_admission=admission,
        governing_source_adoption=adoption,
    )
    managed_resolver = _managed_resolver(
        settings=settings,
        run_id=document.run_id,
        resolver=navigation_resolver,
        loader=loader,
        run_binding=run_binding,
        resolved_workspace=resolved_workspace,
    )
    writer = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
    try:
        opened = open_managed_revision_review(
            store=writer,
            run_binding=run_binding,
            admitted_subjects=planning_run.subjects,
            reviewed_snapshot=reviewed,
            resolver=managed_resolver,
            prechange_head=prechange,
            authority_context=context,
            operation_id=_derived_operation_id(document.operation_id, "managed-request"),
            requester_id=document.reviewer_id,
            rationale="Review the exact recorded downstream revision planning bundle.",
        )
        _link(
            store=writer,
            resolver=None,
            run_id=document.run_id,
            parent_operation_id=document.operation_id,
            kind=OperatorRunLinkKind.MANAGED_REVIEW_REQUEST,
            target_id=opened.request_record.command.request_id,
            target_sha256=opened.request_record.record_sha256,
        )
    finally:
        writer.close()
    _notify(failure_hook, "managed-review-linked")


def record_change_review(
    *,
    settings: Settings,
    document: ReviewDecisionDocumentV1,
    failure_hook: FailureHook | None = None,
) -> ChangeRunStatusV1:
    """Record the current exact temporal or managed human decision."""

    try:
        from mastervault.change_control.application import (  # noqa: PLC0415
            ChangeControlApplication,
        )

        app = ChangeControlApplication(settings)
        try:
            packet = app.get_change_review(document.run_id)
        except ChangeControlApplicationUsageError:
            if not _exact_completed_review_replay(settings=settings, document=document):
                raise
            status = app.get_change_status(document.run_id)
            if document.stage == ChangeReviewStageV1.TEMPORAL and status.phase in {
                ChangeRunPhaseV1.REJECTED_NO_OP,
                ChangeRunPhaseV1.COMPLETED_NO_OP,
            }:
                return status
            if document.stage == ChangeReviewStageV1.MANAGED and _has_operator_link(
                settings=settings,
                run_id=document.run_id,
                kind=OperatorRunLinkKind.MANAGED_REVIEW_DECISION,
            ):
                return status
            # A receipt may own the exact decision while its later navigation or
            # downstream stage is still absent after a lost acknowledgement.  Replay
            # the idempotent write below and reconcile forward from durable evidence.
        else:
            try:
                _require_exact_packet(
                    SimpleReviewReader(packet),
                    document,
                )
            except ChangeControlApplicationConflictError:
                # Once a temporal decision has advanced to the managed gate, the
                # current packet is intentionally a different stage.  An exact retry
                # of the already-owned temporal decision is a zero-provider replay.
                if _exact_completed_review_replay(settings=settings, document=document):
                    return app.get_change_status(document.run_id)
                raise
        with _write_runtime(settings) as (
            _app,
            paths,
            resolver,
            runtime,
            resolved,
        ):
            loader, context = runtime
            prepared_managed: CompositeManagedReviewResolverV2 | None = None
            prepared_navigation: Any = resolver
            if document.stage == ChangeReviewStageV1.TEMPORAL:
                _run, prepared_navigation = _prepare_navigation(
                    state_path=paths.state_db,
                    run_id=document.run_id,
                    resolver=resolver,
                )
            if document.stage == ChangeReviewStageV1.MANAGED:
                reader = SqliteManagedChangeControlStore(
                    paths.state_db, secure_open=True, read_only=True
                )
                try:
                    record = reader._read_request_record(  # noqa: SLF001
                        document.request_id
                    )
                finally:
                    reader.close()
                binding = record.command.bundle.run_binding
                if type(binding) is not ManagedRunBindingV2:
                    raise ChangeControlApplicationIntegrityError(
                        "managed review does not bind an exact v2 run"
                    )
                prepared_managed = _managed_resolver(
                    settings=settings,
                    run_id=document.run_id,
                    resolver=resolver,
                    loader=loader,
                    run_binding=binding,
                    resolved_workspace=resolved,
                )
            store = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
            try:
                if document.stage == ChangeReviewStageV1.TEMPORAL:
                    _record_temporal(
                        store=store,
                        resolver=prepared_navigation,
                        document=document,
                    )
                    _notify(failure_hook, "temporal-decision-linked")
                else:
                    _record_managed(
                        settings=settings,
                        store=store,
                        navigation_resolver=resolver,
                        loader=loader,
                        context=context,
                        document=document,
                        managed_resolver=prepared_managed,
                    )
                    _notify(failure_hook, "managed-decision-linked")
            finally:
                store.close()
            if document.stage == ChangeReviewStageV1.TEMPORAL:
                _advance_accepted_temporal(
                    settings=settings,
                    paths=paths,
                    document=document,
                    navigation_resolver=resolver,
                    loader=loader,
                    context=context,
                    resolved_workspace=resolved,
                    failure_hook=failure_hook,
                )
        return app.get_change_status(document.run_id)
    except ChangeControlApplicationError:
        raise
    except ChangeControlBusyError as exc:
        raise ChangeControlApplicationConflictError(str(exc)) from exc
    except (TypeError, AssertionError):
        raise
    except Exception as exc:
        raise_mapped_application_error(exc)


def activate_change(
    *,
    settings: Settings,
    request: ActivateChangeRequestV1,
    failure_hook: FailureHook | None = None,
) -> ChangeActivationResultV1:
    """Activate one ready generic run with mandatory baseline authority."""

    try:
        from mastervault.change_control.application import (  # noqa: PLC0415
            ChangeControlApplication,
        )

        app = ChangeControlApplication(settings)
        status = app.get_change_status(request.run_id)
        if status.phase in {
            ChangeRunPhaseV1.REJECTED_NO_OP,
            ChangeRunPhaseV1.COMPLETED_NO_OP,
        }:
            baseline = status.baseline
            if baseline is None:
                raise ChangeControlApplicationIntegrityError(
                    "terminal no-op lacks its exact generation-zero baseline"
                )
            request_sha256 = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "request": request.model_dump(mode="json"),
                        "terminal_status": status.model_dump(mode="json"),
                    }
                )
            ).hexdigest()
            operation_store = SqliteManagedChangeControlStore(
                settings.paths.change_control_db_path, secure_open=True
            )
            try:
                operation_store.claim_application_operation(
                    SynchronousApplicationOperationV1.create(
                        operation_id=request.operation_id,
                        operation_kind="activate-no-op",
                        run_id=request.run_id,
                        request_sha256=request_sha256,
                        claimed_at=_now(),
                    )
                )
            finally:
                operation_store.close()
            return ChangeActivationResultV1(
                run_id=request.run_id,
                outcome=status.outcome,
                phase=status.phase,
                baseline_id=baseline.baseline_id,
                baseline_sha256=baseline.baseline_id.removeprefix("regbaseline:"),
            )
        if status.phase == ChangeRunPhaseV1.ACTIVATED:
            baseline = status.baseline
            activation = status.activation
            if baseline is None or activation is None:
                raise ChangeControlApplicationIntegrityError(
                    "activated replay lacks exact baseline or activation evidence"
                )
            reader = SqliteManagedChangeControlStore(
                settings.paths.change_control_db_path,
                secure_open=True,
                read_only=True,
            )
            try:
                row = reader.conn.execute(
                    "SELECT payload_json FROM change_control_generation_activation_receipts "
                    "WHERE receipt_id=? AND receipt_sha256=?",
                    (activation.receipt_id, activation.receipt_sha256),
                ).fetchone()
            finally:
                reader.close()
            receipt = (
                ManagedGenerationActivationReceipt.model_validate_json(
                    str(row["payload_json"]), strict=True
                )
                if row is not None
                else None
            )
            if receipt is None or receipt.operation_id != request.operation_id:
                raise ChangeControlApplicationConflictError(
                    "activated run belongs to another immutable activation operation"
                )
            verified = app.verify_change(request.run_id).status
            verified_baseline = verified.baseline
            verified_activation = verified.activation
            if (
                verified.phase != ChangeRunPhaseV1.ACTIVATED
                or verified_baseline is None
                or verified_activation is None
            ):
                raise ChangeControlApplicationIntegrityError(
                    "activated replay did not freshly verify its exact authority"
                )
            return ChangeActivationResultV1(
                run_id=request.run_id,
                outcome=verified.outcome,
                phase=verified.phase,
                baseline_id=verified_baseline.baseline_id,
                baseline_sha256=verified_baseline.baseline_id.removeprefix("regbaseline:"),
                activation_receipt_id=verified_activation.receipt_id,
                activation_receipt_sha256=verified_activation.receipt_sha256,
                authority=_authority_summary(verified),
            )
        if status.phase in {
            ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW,
            ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW,
        }:
            raise ChangeControlApplicationReviewRequiredError(
                "operator run requires a human review decision before activation"
            )
        if status.phase != ChangeRunPhaseV1.READY_TO_ACTIVATE:
            raise ChangeControlApplicationUsageError("operator run is not ready to activate")

        with _write_runtime(settings) as (
            _app,
            paths,
            navigation_resolver,
            runtime,
            resolved,
        ):
            loader, context = runtime

            run, prepared_navigation = _prepare_navigation(
                state_path=paths.state_db,
                run_id=request.run_id,
                resolver=navigation_resolver,
            )
            links = {item.command.kind: item.command for item in run.links}
            request_link = links.get(OperatorRunLinkKind.MANAGED_REVIEW_REQUEST)
            baseline_link = links.get(OperatorRunLinkKind.GENERATION_ZERO_BASELINE)
            if request_link is None or baseline_link is None:
                raise ChangeControlApplicationIntegrityError(
                    "activation prerequisites are incomplete"
                )
            reader = SqliteManagedChangeControlStore(
                paths.state_db, secure_open=True, read_only=True
            )
            try:
                request_record = reader._read_request_record(  # noqa: SLF001
                    request_link.target_id
                )
            finally:
                reader.close()
            run_binding = request_record.command.bundle.run_binding
            if type(run_binding) is not ManagedRunBindingV2:
                raise ChangeControlApplicationIntegrityError(
                    "activation request does not bind an exact v2 managed run"
                )
            managed_resolver = _managed_resolver(
                settings=settings,
                run_id=request.run_id,
                resolver=navigation_resolver,
                loader=loader,
                run_binding=run_binding,
                resolved_workspace=resolved,
            )

            store = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
            try:
                run = store.get_operator_run(request.run_id, resolver=prepared_navigation)
                if run is None:
                    raise ChangeControlApplicationIntegrityError("operator run disappeared")
                managed_view = store.get_managed_review(
                    request_link.target_id,
                    resolver=managed_resolver,
                    authority_context=context,
                )
                if managed_view.request_record != request_record:
                    raise ChangeControlApplicationIntegrityError(
                        "managed activation request changed after preflight"
                    )
                baseline_record = store.get_generation_zero_baseline(baseline_link.target_id)
                if baseline_record is None:
                    raise ChangeControlApplicationIntegrityError(
                        "generation-zero baseline SQLite record is absent"
                    )
                baseline_repository = GenerationZeroBaselineRepository(
                    settings.paths.change_control_evidence_root,
                    create=False,
                    read_only=True,
                )
                baseline_capability = baseline_repository.reopen(request.run_id)
                result = activate_reviewed_managed_generation(
                    request_id=request_link.target_id,
                    operation_id=request.operation_id,
                    store=store,
                    resolver=managed_resolver,
                    generation_root=paths.generation_root,
                    embedder=get_embedding_provider(settings),
                    authority_context=context,
                    protected_paths=(
                        paths.vault,
                        paths.legacy_index,
                        paths.checkpoint_db,
                    ),
                    baseline_record=baseline_record,
                    baseline_capability=baseline_capability,
                    baseline_repository=baseline_repository,
                    operator_run_resolver=prepared_navigation,
                    workspace_base_notes=resolved.exact_vault_notes,
                    failure_hook=failure_hook,
                )
                if result.outcome != ManagedActivationOutcome.ACTIVATED or result.receipt is None:
                    raise ChangeControlApplicationIntegrityError(
                        "ready run did not produce an activation receipt"
                    )
                _link(
                    store=store,
                    resolver=prepared_navigation,
                    run_id=request.run_id,
                    parent_operation_id=request.operation_id,
                    kind=OperatorRunLinkKind.ACTIVATION_OPERATION,
                    target_id=result.receipt.receipt_id,
                    target_sha256=result.receipt.receipt_sha256,
                )
                _notify(failure_hook, "activation-linked")
            finally:
                store.close()

        activated = app.verify_change(request.run_id).status
        baseline = activated.baseline
        activation = activated.activation
        if activated.phase != ChangeRunPhaseV1.ACTIVATED or baseline is None or activation is None:
            raise ChangeControlApplicationIntegrityError(
                "activation receipt did not produce exact activated status"
            )
        return ChangeActivationResultV1(
            run_id=request.run_id,
            outcome=ChangeRunOutcomeV1.ACTIVATED,
            phase=ChangeRunPhaseV1.ACTIVATED,
            baseline_id=baseline.baseline_id,
            baseline_sha256=baseline.baseline_id.removeprefix("regbaseline:"),
            activation_receipt_id=activation.receipt_id,
            activation_receipt_sha256=activation.receipt_sha256,
            authority=_authority_summary(activated),
        )
    except ChangeControlApplicationError:
        raise
    except ChangeControlBusyError as exc:
        raise ChangeControlApplicationConflictError(str(exc)) from exc
    except (TypeError, AssertionError):
        raise
    except Exception as exc:
        raise_mapped_application_error(exc)


__all__ = [
    "FailureHook",
    "activate_change",
    "record_change_review",
]
