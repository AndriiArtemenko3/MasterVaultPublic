"""Resumable synchronous start saga through the temporal human-review gate."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mastervault.change_control.application_authority_resolver import (
    ApplicationOperatorRunAuthorityResolver,
)
from mastervault.change_control.application_downstream import _prepare_navigation
from mastervault.change_control.application_extraction_calls import (
    ApplicationExtractionCallRepository,
)
from mastervault.change_control.application_generic_extraction import (
    execute_live_generic_extraction,
    execute_replay_generic_extraction,
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
from mastervault.change_control.application_mechanical_no_change import (
    MechanicalNoChangeEvidenceRepository,
    MechanicalNoChangeEvidenceV1,
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
from mastervault.change_control.application_start_command import ApplicationStartCommandV1
from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationResultSet,
    GraphMaterializationStatus,
    PairDisposition,
    select_classification_workload,
)
from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    DependencyClassification,
    DependencyClassificationResultSet,
    derive_governing_supersessions,
    generate_dependency_workload,
)
from mastervault.change_control.discovery import generate_relationship_candidates
from mastervault.change_control.generic_analysis import (
    GenericSourceNoteInventoryResolverV2,
    start_generic_analysis_v2,
)
from mastervault.change_control.generic_incoming import (
    VerifiedGenericIncomingV2,
    extraction_request_sha256_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    ReopenedGenericEvidenceV2,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.managed_store import (
    OperatorRunAuthorityResolver,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import (
    TemporalState,
    canonical_json_bytes,
    resolve_document_temporality,
)
from mastervault.change_control.operator_run import (
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
)
from mastervault.change_control.query_generation import ResolvedQueryGeneration
from mastervault.change_control.recorded_inference import (
    RecordedInferenceOutcome,
    RecordedInferenceTask,
    run_classification_inference,
    run_dependency_inference,
)
from mastervault.change_control.regression_baseline import (
    GenerationZeroBaselineRepository,
    RegressionAuthorityBindingV1,
    execute_generation_zero_baseline,
    regression_replay_runtime_identity,
    regression_runtime_identity,
)
from mastervault.change_control.regression_suite import AdmittedRegressionSuiteV1
from mastervault.change_control.synchronous_lifecycle_store_models import (
    IncomingAdmissionIntentV1,
    RegressionSuiteAdmissionIntentV1,
)
from mastervault.change_control.temporal_analysis import build_temporal_analysis_evidence
from mastervault.change_control.temporal_commit import commit_temporal_proposal
from mastervault.change_control.temporal_proposal import (
    DocumentReplacementProposalCandidate,
    build_temporal_proposal,
    open_temporal_review,
)
from mastervault.change_control.workspace_bootstrap import (
    VerifiedWorkspaceBootstrapCapability,
    WorkspaceBootstrapState,
)
from mastervault.config import Settings
from mastervault.providers import get_embedding_provider, get_llm

FailureHook = Callable[[str], None]
ResolvedGenerationFactory = Callable[[], ResolvedQueryGeneration]


class ApplicationStartLifecycleError(ValueError):
    """Start orchestration cannot reproduce one exact lifecycle authority chain."""


@dataclass(frozen=True, slots=True)
class StartLifecycleTemporalReviewV1:
    request_id: str


@dataclass(frozen=True, slots=True)
class StartLifecycleCompletedNoOpV1:
    evidence_id: str
    evidence_sha256: str
    completed_at: str


type StartLifecycleResultV1 = StartLifecycleTemporalReviewV1 | StartLifecycleCompletedNoOpV1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _notify(hook: FailureHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _operation(parent: str, stage: str) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.synchronous-start-stage.v1",
                "parent_operation_id": parent,
                "stage": stage,
            }
        )
    ).hexdigest()
    return f"start-stage:{stage}:{digest}"


def _require_replay_incoming(
    replay_bundle: ChangeReplayBundleV1,
    incoming: ReopenedGenericEvidenceV2,
) -> None:
    if not (
        replay_bundle.incoming_bundle_id == incoming.bundle.bundle_id
        and replay_bundle.incoming_bundle_sha256 == incoming.bundle.bundle_sha256
    ):
        raise ApplicationStartLifecycleError(
            "replay bundle differs from the newly derived current incoming bundle"
        )


def _link(
    store: SqliteManagedChangeControlStore,
    *,
    parent_operation_id: str,
    run_id: str,
    kind: OperatorRunLinkKind,
    target_id: str,
    target_sha256: str,
    resolver: OperatorRunAuthorityResolver | None = None,
) -> None:
    store.record_operator_run_link(
        OperatorRunLinkCommand.create(
            operation_id=_operation(parent_operation_id, f"link-{kind.value}"),
            run_id=run_id,
            kind=kind,
            target_id=target_id,
            target_sha256=target_sha256,
        ),
        resolver=resolver,
    )


def _index(
    repository: FilesystemLifecycleEvidenceIndex,
    *,
    run_id: str,
    stage: LifecycleEvidenceStageV1,
    owners: tuple[LifecycleEvidenceOwnerV1, ...],
) -> None:
    repository.persist(
        LifecycleEvidenceIndexV1.create(
            run_id=run_id,
            stage=stage,
            owners=owners,
            recorded_at=_now(),
        )
    )


def _contract(settings: Settings, mode: InferenceExecutionMode) -> ManagedInferenceContractBinding:
    assets = load_application_inference_assets_v1()
    return ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=assets.algorithm_manifest_sha256,
        contract_id=assets.contract_id,
        contract_version=assets.contract_version,
        mode=mode,
        provider=settings.llm.provider,
        model=settings.llm.model_medium,
        prompt_sha256=assets.prompt_sha256,
        response_schema_sha256=assets.response_schema_sha256,
    )


def _operation_receipt_sha256(store: SqliteManagedChangeControlStore, operation_id: str) -> str:
    receipt_sha256 = store.get_operation_receipt_sha256(operation_id)
    if receipt_sha256 is None:
        raise ApplicationStartLifecycleError("temporal commit receipt disappeared")
    return receipt_sha256


def _reopen_stage_batch(
    *,
    lifecycle: FilesystemLifecycleEvidenceIndex,
    inference: FilesystemInferenceEvidenceRepository,
    run_id: str,
    stage: LifecycleEvidenceStageV1,
    owner_kind: str,
) -> (
    tuple[
        tuple[RecordedInferenceOutcome, ...],
        RepositoryVerifiedInferenceEvidenceBatch,
    ]
    | None
):
    index = lifecycle.reopen_optional(run_id, stage)
    if index is None:
        return None
    if len(index.owners) != 1:
        raise ApplicationStartLifecycleError("inference stage has ambiguous owner evidence")
    owner = index.owners[0]
    if not (
        owner.owner_kind == owner_kind
        and owner.owner_id == f"inference-batch:{owner.owner_sha256}"
        and owner.relative_locator == f"inference/evidence/batches/{owner.owner_sha256}.json"
    ):
        raise ApplicationStartLifecycleError("inference stage locator is not exact")
    return inference.resolve_verified_batch(
        batch_id=owner.owner_id,
        batch_sha256=owner.owner_sha256,
    )


def _validate_stage_outcomes(
    outcomes: tuple[RecordedInferenceOutcome, ...],
    *,
    contract: ManagedInferenceContractBinding,
    task: RecordedInferenceTask,
    workload_id: str,
    workload_sha256: str,
    shard_bindings: tuple[tuple[str, str], ...],
) -> None:
    observed = tuple(
        (
            item.execution.input_envelope.input_shard_id,
            item.execution.input_envelope.input_shard_sha256,
        )
        for item in outcomes
    )
    if observed != shard_bindings or any(
        item.execution.task is not task
        or item.execution.contract != contract
        or item.execution.input_envelope.workload_id != workload_id
        or item.execution.input_envelope.workload_sha256 != workload_sha256
        for item in outcomes
    ):
        raise ApplicationStartLifecycleError(
            f"persisted {task.value} batch differs from the exact current workload"
        )


def resume_completed_temporal_publication(
    *,
    settings: Settings,
    state_path: Path,
    evidence_root: Path,
    command: ApplicationStartCommandV1,
) -> str | None:
    """Repair SQLite navigation after a durable temporal commit, without providers."""

    lifecycle = FilesystemLifecycleEvidenceIndex(Path(evidence_root))
    temporal_index = lifecycle.reopen_optional(command.run_id, LifecycleEvidenceStageV1.TEMPORAL)
    if temporal_index is None:
        return None
    if len(temporal_index.owners) != 1:
        raise ApplicationStartLifecycleError(
            "temporal lifecycle stage has ambiguous owner evidence"
        )
    owner = temporal_index.owners[0]
    if not (
        owner.owner_kind == "temporal-analysis"
        and owner.owner_id == f"temporal-analysis:{owner.owner_sha256}"
        and owner.relative_locator == f"temporal/evidence/analyses/{owner.owner_sha256}.json"
    ):
        raise ApplicationStartLifecycleError("temporal lifecycle stage has a non-canonical owner")
    operation_id = f"temporal-commit:{owner.owner_sha256}"
    reader = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
    try:
        receipt_sha256 = reader.get_operation_receipt_sha256(operation_id)
    finally:
        reader.close()
    # Evidence is intentionally owned before CAS.  If CAS has not happened yet,
    # the ordinary start path can safely reproduce and commit it from revision 2.
    if receipt_sha256 is None:
        return None
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=Path(evidence_root),
        state_path=state_path,
        configuration_sha256=application_configuration_sha256(settings),
    )
    run, _prepared_existing = _prepare_navigation(
        state_path=state_path,
        run_id=command.run_id,
        resolver=resolver,
    )
    links = {item.command.kind: item.command for item in run.links}
    commit = resolver.resolve_temporal_proposal(
        run_id=command.run_id,
        target_id=operation_id,
        target_sha256=receipt_sha256,
    )
    proposal_link = links.get(OperatorRunLinkKind.TEMPORAL_PROPOSAL)
    if proposal_link is None:
        _, prepared = _prepare_navigation(
            state_path=state_path, run_id=command.run_id, resolver=resolver
        )
        prepared.temporal[(command.run_id, operation_id, receipt_sha256)] = commit
        writer = SqliteManagedChangeControlStore(state_path, secure_open=True)
        try:
            _link(
                writer,
                parent_operation_id=command.operation_id,
                run_id=command.run_id,
                kind=OperatorRunLinkKind.TEMPORAL_PROPOSAL,
                target_id=operation_id,
                target_sha256=receipt_sha256,
                resolver=prepared,
            )
        finally:
            writer.close()
    elif not (
        proposal_link.target_id == operation_id and proposal_link.target_sha256 == receipt_sha256
    ):
        raise ApplicationStartLifecycleError(
            "temporal proposal navigation differs from its durable owner"
        )
    writer = SqliteManagedChangeControlStore(state_path, secure_open=True)
    try:
        request = open_temporal_review(
            writer,
            commit,
            requester_id="mastervault.change-control.application",
            rationale="Review every exact temporal subject before managed change planning.",
            operation_id=_operation(command.operation_id, "temporal-review"),
        )
    finally:
        writer.close()
    _, prepared = _prepare_navigation(
        state_path=state_path, run_id=command.run_id, resolver=resolver
    )
    writer = SqliteManagedChangeControlStore(state_path, secure_open=True)
    try:
        _link(
            writer,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
            target_id=request.request.request_id,
            target_sha256=request.request.request_payload_sha256,
            resolver=prepared,
        )
        return request.request.request_id
    finally:
        writer.close()


def _resume_completed_mechanical_no_change(
    *,
    settings: Settings,
    state_path: Path,
    evidence_root: Path,
    command: ApplicationStartCommandV1,
    repository: MechanicalNoChangeEvidenceRepository,
) -> StartLifecycleCompletedNoOpV1 | None:
    """Repair a lost terminal link from the create-only receipt, without providers."""

    receipt = repository.reopen_optional(command.run_id)
    if receipt is None:
        return None
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=evidence_root,
        state_path=state_path,
        configuration_sha256=application_configuration_sha256(settings),
    )
    reader = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
    try:
        run = reader.get_operator_run(command.run_id, resolver=resolver)
    finally:
        reader.close()
    if run is None:
        raise ApplicationStartLifecycleError(
            "mechanical no-change receipt has no owning operator run"
        )
    links = {item.command.kind: item.command for item in run.links}
    link = links.get(OperatorRunLinkKind.MECHANICAL_NO_CHANGE)
    if link is None:
        _, prepared = _prepare_navigation(
            state_path=state_path,
            run_id=command.run_id,
            resolver=resolver,
        )
        key = (command.run_id, receipt.evidence_id, receipt.evidence_sha256)
        prepared.mechanical_no_change[key] = resolver.resolve_operator_mechanical_no_change(
            run_id=command.run_id,
            target_id=receipt.evidence_id,
            target_sha256=receipt.evidence_sha256,
        )
        writer = SqliteManagedChangeControlStore(state_path, secure_open=True)
        try:
            _link(
                writer,
                parent_operation_id=command.operation_id,
                run_id=command.run_id,
                kind=OperatorRunLinkKind.MECHANICAL_NO_CHANGE,
                target_id=receipt.evidence_id,
                target_sha256=receipt.evidence_sha256,
                resolver=prepared,
            )
        finally:
            writer.close()
    elif not (
        link.target_id == receipt.evidence_id and link.target_sha256 == receipt.evidence_sha256
    ):
        raise ApplicationStartLifecycleError(
            "mechanical no-change navigation differs from its durable owner"
        )
    reopened = resolver.resolve_operator_mechanical_no_change(
        run_id=command.run_id,
        target_id=receipt.evidence_id,
        target_sha256=receipt.evidence_sha256,
    )
    return StartLifecycleCompletedNoOpV1(
        evidence_id=reopened.evidence_id,
        evidence_sha256=reopened.evidence_sha256,
        completed_at=reopened.completed_at,
    )


def run_start_change_lifecycle(
    *,
    settings: Settings,
    state_path: Path,
    evidence_root: Path,
    command: ApplicationStartCommandV1,
    admission: VerifiedGenericIncomingV2,
    suite: AdmittedRegressionSuiteV1,
    workspace_state: WorkspaceBootstrapState,
    workspace_capability: VerifiedWorkspaceBootstrapCapability,
    workspace_source_notes: tuple[CanonicalSourceNoteSnapshot, ...],
    resolve_generation_zero: ResolvedGenerationFactory,
    replay_bundle: ChangeReplayBundleV1 | None,
    failure_hook: FailureHook | None = None,
) -> StartLifecycleResultV1:
    """Run or exactly resume through either terminal no-op or temporal review."""

    evidence_root = Path(evidence_root)
    lifecycle = FilesystemLifecycleEvidenceIndex(evidence_root)
    generic = FilesystemGenericIncomingRepositoryV2(evidence_root)
    inference = FilesystemInferenceEvidenceRepository(evidence_root)
    provider_journal = ApplicationProviderCallJournal(evidence_root)
    baseline_repository = GenerationZeroBaselineRepository(evidence_root)
    extraction_repository = ApplicationExtractionCallRepository(evidence_root)
    mechanical_no_change = MechanicalNoChangeEvidenceRepository(evidence_root)
    if command.mode.value == "replay":
        if replay_bundle is None:
            raise ApplicationStartLifecycleError("REPLAY start lacks its exact bundle")
        replay_bundle = ApplicationReplayBundleRepository(evidence_root).claim(
            run_id=command.run_id,
            start_command_id=command.command_id,
            bundle=replay_bundle,
            canonical_bytes=canonical_json_bytes(replay_bundle.model_dump(mode="json")),
        )
    mechanically_completed = _resume_completed_mechanical_no_change(
        settings=settings,
        state_path=state_path,
        evidence_root=evidence_root,
        command=command,
        repository=mechanical_no_change,
    )
    if mechanically_completed is not None:
        return mechanically_completed
    resumed = resume_completed_temporal_publication(
        settings=settings,
        state_path=state_path,
        evidence_root=evidence_root,
        command=command,
    )
    if resumed is not None:
        return StartLifecycleTemporalReviewV1(request_id=resumed)
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=evidence_root,
        state_path=state_path,
        configuration_sha256=application_configuration_sha256(settings),
    )
    store = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
    try:
        run = store.get_operator_run(command.run_id, resolver=resolver)
        if run is None or not (
            run.record.command.base_authority_id == command.base_authority_id
            and run.record.command.base_authority_revision == command.base_authority_revision
            and run.record.command.base_active_pointer_sha256 == command.base_active_pointer_sha256
            and run.record.command.aggregate_id == workspace_state.intent.aggregate_id
        ):
            raise ApplicationStartLifecycleError(
                "start command does not bind the exact existing bootstrap run"
            )
        store.close()
        if command.mode.value == "live":
            extraction = execute_live_generic_extraction(
                command=command,
                admission=admission,
                settings=settings,
                repository=extraction_repository,
                failure_hook=failure_hook,
            ).extraction
            generic_capability = generic.persist(admission, extraction)
        else:
            if replay_bundle is None:
                raise ApplicationStartLifecycleError("REPLAY start lacks its exact bundle")
            refs = replay_bundle.require_exact_stage(
                ChangeReplayStageV1.EXTRACTION,
                (extraction_request_sha256_v2(admission),),
            )
            extraction = execute_replay_generic_extraction(
                command=command,
                admission=admission,
                settings=settings,
                repository=ApplicationExtractionCallRepository(
                    evidence_root, create=False, read_only=True
                ),
                reference=refs[0],
            ).extraction
            generic_capability = generic.persist(admission, extraction)
        incoming = generic.resolve_verified_evidence(generic_capability)
        if incoming.raw_source != admission.source_snapshot:
            raise ApplicationStartLifecycleError(
                "incoming bundle differs from the currently admitted source"
            )
        if replay_bundle is not None:
            _require_replay_incoming(replay_bundle, incoming)
        _index(
            lifecycle,
            run_id=command.run_id,
            stage=LifecycleEvidenceStageV1.INCOMING,
            owners=(
                LifecycleEvidenceOwnerV1(
                    owner_kind="generic-bundle",
                    owner_id=incoming.bundle.bundle_id,
                    owner_sha256=incoming.bundle.bundle_sha256,
                    relative_locator=(
                        f"generic-incoming/v2/bundles/{incoming.bundle.bundle_sha256}.json"
                    ),
                ),
            ),
        )
        incoming_intent = IncomingAdmissionIntentV1.create(
            operation_id=_operation(command.operation_id, "incoming-admission"),
            run_id=command.run_id,
            bundle_id=incoming.bundle.bundle_id,
            bundle_sha256=incoming.bundle.bundle_sha256,
            admission_sha256=incoming.admission.admission_sha256,
            source_receipt_sha256=incoming.source.source_receipt_sha256,
            projection_sha256=incoming.projection.projection_sha256,
            inference_sha256=incoming.inference.inference_sha256,
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        incoming_record = store.record_incoming_admission(incoming_intent, resolver=resolver)
        suite_intent = RegressionSuiteAdmissionIntentV1.create(
            operation_id=_operation(command.operation_id, "suite-admission"),
            run_id=command.run_id,
            suite_id=suite.suite.suite_id,
            suite_version=suite.suite.suite_version,
            original_sha256=suite.original_sha256,
            original_byte_count=suite.original_byte_count,
            canonical_sha256=suite.canonical_sha256,
            suite=suite.suite,
        )
        suite_record = store.record_regression_suite_admission(suite_intent)
        store.close()
        _, prepared_navigation = _prepare_navigation(
            state_path=state_path,
            run_id=command.run_id,
            resolver=resolver,
        )
        prepared_navigation.incoming[incoming_intent.intent_id] = resolver.resolve_incoming_source(
            incoming_intent
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        _link(
            store,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.INCOMING_SOURCE,
            target_id=incoming_record.receipt_id,
            target_sha256=incoming_record.receipt_sha256,
            resolver=prepared_navigation,
        )
        _link(
            store,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.REGRESSION_SUITE,
            target_id=suite_record.receipt_id,
            target_sha256=suite_record.receipt_sha256,
        )
        store.close()

        inventory, readiness = workspace_state.require_complete()
        baseline_capability = baseline_repository.reopen_optional(command.run_id)
        if command.mode.value == "replay":
            if replay_bundle is None:
                raise ApplicationStartLifecycleError("REPLAY start lacks its exact bundle")
            baseline_stage = next(
                item for item in replay_bundle.stages if item.stage == ChangeReplayStageV1.BASELINE
            )
            source_reference = baseline_stage.artifacts[0]
            with resolve_generation_zero() as resolved:
                authority = RegressionAuthorityBindingV1(
                    run_id=command.run_id,
                    incoming_admission_receipt_id=incoming_record.receipt_id,
                    incoming_admission_receipt_sha256=incoming_record.receipt_sha256,
                    workspace_inventory_receipt_id=inventory.receipt_id,
                    workspace_inventory_receipt_sha256=inventory.receipt_sha256,
                    legacy_readiness_receipt_id=readiness.receipt_id,
                    legacy_readiness_receipt_sha256=readiness.receipt_sha256,
                    query_generation=resolved.metadata,
                )
                if command.configuration_sha256 != application_configuration_sha256(settings):
                    raise ApplicationStartLifecycleError(
                        "REPLAY start differs from the current configuration identity"
                    )
                source_runtime = baseline_repository.replay_runtime(source_reference)
                replayed = baseline_repository.prepare_replay(
                    source_reference=source_reference,
                    current_authority=authority,
                    current_suite=suite,
                    expected_runtime=regression_replay_runtime_identity(
                        settings,
                        source_runtime=source_runtime,
                        generation=resolved.metadata,
                    ),
                )
            if baseline_capability is None:
                baseline_capability = baseline_repository.publish(
                    replayed.prepared,
                    captured_at=replayed.captured_at,
                )
                _notify(failure_hook, "generation-zero-baseline-published")
            else:
                baseline_repository.require_receipt_matches(
                    baseline_repository.verify_capability(baseline_capability),
                    replayed.prepared,
                )
        else:
            with resolve_generation_zero() as resolved:
                authority = RegressionAuthorityBindingV1(
                    run_id=command.run_id,
                    incoming_admission_receipt_id=incoming_record.receipt_id,
                    incoming_admission_receipt_sha256=incoming_record.receipt_sha256,
                    workspace_inventory_receipt_id=inventory.receipt_id,
                    workspace_inventory_receipt_sha256=inventory.receipt_sha256,
                    legacy_readiness_receipt_id=readiness.receipt_id,
                    legacy_readiness_receipt_sha256=readiness.receipt_sha256,
                    query_generation=resolved.metadata,
                )
                embedder = get_embedding_provider(settings)
                llm = get_llm(settings)
                current_runtime = regression_runtime_identity(settings, embedder=embedder, llm=llm)
                if (
                    current_runtime.embedding_model != resolved.metadata.embedding_model
                    or current_runtime.embedding_dimensions
                    != resolved.metadata.embedding_dimensions
                ):
                    raise ApplicationStartLifecycleError(
                        "embedding runtime differs from generation-zero index"
                    )
                resolved.verify()
                if baseline_capability is None:
                    prepared = execute_generation_zero_baseline(
                        resolved=resolved,
                        authority=authority,
                        suite=suite,
                        settings=settings,
                        embedder=embedder,
                        llm=llm,
                        reranker=None,
                        repository=baseline_repository,
                        failure_hook=failure_hook,
                    )
                else:
                    baseline_repository.require_current_live_inputs(
                        baseline_repository.verify_capability(baseline_capability),
                        authority=authority,
                        suite=suite,
                        runtime=current_runtime,
                    )
                resolved.verify()
            if baseline_capability is None:
                baseline_capability = baseline_repository.publish(prepared, captured_at=_now())
                _notify(failure_hook, "generation-zero-baseline-published")
        baseline_receipt = baseline_repository.verify_capability(baseline_capability)
        run_name = hashlib.sha256(command.run_id.encode("utf-8")).hexdigest()
        _index(
            lifecycle,
            run_id=command.run_id,
            stage=LifecycleEvidenceStageV1.BASELINE,
            owners=(
                LifecycleEvidenceOwnerV1(
                    owner_kind="generation-zero-baseline",
                    owner_id=baseline_receipt.receipt_id,
                    owner_sha256=baseline_receipt.receipt_sha256,
                    relative_locator=(f"regression-baselines/runs/{run_name}/COMPLETE.json"),
                ),
            ),
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        store.record_generation_zero_baseline(
            operation_id=_operation(command.operation_id, "baseline"),
            incoming_admission_receipt_id=incoming_record.receipt_id,
            suite_admission_receipt_id=suite_record.receipt_id,
            baseline_receipt=baseline_receipt,
            resolver=resolver,
        )
        store.close()
        _, prepared_navigation = _prepare_navigation(
            state_path=state_path,
            run_id=command.run_id,
            resolver=resolver,
        )
        prepared_navigation.baselines[baseline_receipt.receipt_id] = baseline_receipt
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        _link(
            store,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
            target_id=baseline_receipt.receipt_id,
            target_sha256=baseline_receipt.receipt_sha256,
            resolver=prepared_navigation,
        )
        store.close()

        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        analysis = start_generic_analysis_v2(
            store=store,
            repository=generic,
            workspace_capability=workspace_capability,
            evidence_capability=generic_capability,
            workspace_source_notes=workspace_source_notes,
            analysis_operation_id=_operation(command.operation_id, "generic-analysis-seed"),
        )
        store.close()
        candidates = generate_relationship_candidates(
            analysis.snapshot,
            changed_claim_revision_ids=analysis.binding.changed_claim_revision_ids,
            as_of=analysis.binding.analysis_as_of,
        )
        classification_workload = select_classification_workload(
            analysis.snapshot, candidates=candidates
        )
        assets = load_application_inference_assets_v1()
        mode = (
            InferenceExecutionMode.LIVE
            if command.mode.value == "live"
            else InferenceExecutionMode.REPLAY
        )
        contract = _contract(settings, mode)
        reopened_classification = _reopen_stage_batch(
            lifecycle=lifecycle,
            inference=inference,
            run_id=command.run_id,
            stage=LifecycleEvidenceStageV1.CLASSIFICATION,
            owner_kind="classification-batch",
        )
        if reopened_classification is None:
            if mode is InferenceExecutionMode.LIVE:
                provider = SettingsRecordedInferenceProvider(
                    settings,
                    contract,
                    journal=provider_journal,
                    owner_id=command.command_id,
                    run_id=command.run_id,
                )
                classification_outcomes = tuple(
                    run_classification_inference(
                        contract=contract,
                        workload=classification_workload,
                        input_shard=shard,
                        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
                        prompt_bytes=assets.prompt_bytes,
                        response_schema_bytes=assets.response_schema_bytes,
                        provider=provider,
                    )
                    for shard in classification_workload.inference_shards
                )
            else:
                if replay_bundle is None:
                    raise ApplicationStartLifecycleError("REPLAY start lacks its exact bundle")
                refs = replay_bundle.require_exact_stage(
                    ChangeReplayStageV1.CLASSIFICATION,
                    tuple(item.shard_sha256 for item in classification_workload.inference_shards),
                )
                classification_outcomes = tuple(
                    run_classification_inference(
                        contract=contract,
                        workload=classification_workload,
                        input_shard=shard,
                        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
                        prompt_bytes=assets.prompt_bytes,
                        response_schema_bytes=assets.response_schema_bytes,
                        replay_resolver=inference,
                        replay_source_receipt_artifact=ref.recorded_inference_receipt(),
                    )
                    for shard, ref in zip(
                        classification_workload.inference_shards, refs, strict=True
                    )
                )
            classification_batch = inference.persist_batch(classification_outcomes)
            _index(
                lifecycle,
                run_id=command.run_id,
                stage=LifecycleEvidenceStageV1.CLASSIFICATION,
                owners=(
                    LifecycleEvidenceOwnerV1(
                        owner_kind="classification-batch",
                        owner_id=classification_batch.batch_id,
                        owner_sha256=classification_batch.batch_sha256,
                        relative_locator=(
                            f"inference/evidence/batches/{classification_batch.batch_sha256}.json"
                        ),
                    ),
                ),
            )
            _notify(failure_hook, "classification-batch-recorded")
        else:
            classification_outcomes, classification_batch = reopened_classification
        _validate_stage_outcomes(
            classification_outcomes,
            contract=contract,
            task=RecordedInferenceTask.CLASSIFICATION,
            workload_id=classification_workload.workload_id,
            workload_sha256=classification_workload.workload_sha256,
            shard_bindings=tuple(
                (item.shard_id, item.shard_sha256)
                for item in classification_workload.inference_shards
            ),
        )
        classifications: list[ClaimPairClassification] = []
        for outcome in classification_outcomes:
            if outcome.classification_output is None:
                raise ApplicationStartLifecycleError("classification batch lacks its typed output")
            classifications.extend(
                item.classification for item in outcome.classification_output.items
            )
        classification_results = ClassificationResultSet.create(
            workload=classification_workload, classifications=tuple(classifications)
        )
        incoming_document = next(
            item
            for item in analysis.snapshot.aggregate.documents.documents
            if item.document_version_id == analysis.binding.incoming_document_version_id
        )
        predecessors = tuple(
            item
            for item in analysis.snapshot.aggregate.documents.documents
            if item.document_version_id != incoming_document.document_version_id
            and item.document_family == incoming_document.document_family
            and resolve_document_temporality(
                item,
                analysis.snapshot.aggregate.validated_temporal_constraints(),
                as_of=analysis.binding.analysis_as_of,
            ).state
            == TemporalState.CURRENT
        )
        if len(predecessors) != 1:
            raise ApplicationStartLifecycleError(
                "incoming source requires one exact same-family predecessor"
            )
        if not derive_governing_supersessions(classification_results):
            receipt = mechanical_no_change.persist(
                MechanicalNoChangeEvidenceV1.create(
                    run_id=command.run_id,
                    base_authority_id=command.base_authority_id,
                    base_authority_revision=command.base_authority_revision,
                    base_active_pointer_sha256=command.base_active_pointer_sha256,
                    configuration_sha256=command.configuration_sha256,
                    generic_analysis=analysis.binding,
                    incoming_admission=incoming_record,
                    suite_admission=suite_record,
                    baseline_receipt=baseline_receipt,
                    classification_contract=contract,
                    classification_batch_id=classification_batch.batch_id,
                    classification_batch_sha256=classification_batch.batch_sha256,
                    classification_results=classification_results,
                    reason="complete-classification-no-governing-supersession",
                    completed_at=_now(),
                )
            )
            _notify(failure_hook, "mechanical-no-change-recorded")
            _, prepared_navigation = _prepare_navigation(
                state_path=state_path,
                run_id=command.run_id,
                resolver=resolver,
            )
            key = (command.run_id, receipt.evidence_id, receipt.evidence_sha256)
            prepared_navigation.mechanical_no_change[key] = (
                resolver.resolve_operator_mechanical_no_change(
                    run_id=command.run_id,
                    target_id=receipt.evidence_id,
                    target_sha256=receipt.evidence_sha256,
                )
            )
            store = SqliteManagedChangeControlStore(state_path, secure_open=True)
            try:
                _link(
                    store,
                    parent_operation_id=command.operation_id,
                    run_id=command.run_id,
                    kind=OperatorRunLinkKind.MECHANICAL_NO_CHANGE,
                    target_id=receipt.evidence_id,
                    target_sha256=receipt.evidence_sha256,
                    resolver=prepared_navigation,
                )
            finally:
                store.close()
            _notify(failure_hook, "mechanical-no-change-linked")
            return StartLifecycleCompletedNoOpV1(
                evidence_id=receipt.evidence_id,
                evidence_sha256=receipt.evidence_sha256,
                completed_at=receipt.completed_at,
            )
        dependency_workload = generate_dependency_workload(
            analysis.snapshot,
            candidates=candidates,
            classification_results=classification_results,
            inventory_capability=analysis.inventory_capability,
        )
        reopened_dependency = _reopen_stage_batch(
            lifecycle=lifecycle,
            inference=inference,
            run_id=command.run_id,
            stage=LifecycleEvidenceStageV1.DEPENDENCY,
            owner_kind="dependency-batch",
        )
        if reopened_dependency is None:
            if mode is InferenceExecutionMode.LIVE:
                provider = SettingsRecordedInferenceProvider(
                    settings,
                    contract,
                    journal=provider_journal,
                    owner_id=command.command_id,
                    run_id=command.run_id,
                )
                dependency_outcomes = tuple(
                    run_dependency_inference(
                        contract=contract,
                        workload=dependency_workload,
                        input_shard=shard,
                        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
                        prompt_bytes=assets.prompt_bytes,
                        response_schema_bytes=assets.response_schema_bytes,
                        provider=provider,
                    )
                    for shard in dependency_workload.input_shards
                )
            else:
                if replay_bundle is None:
                    raise ApplicationStartLifecycleError("REPLAY start lacks its exact bundle")
                refs = replay_bundle.require_exact_stage(
                    ChangeReplayStageV1.DEPENDENCY,
                    tuple(item.shard_sha256 for item in dependency_workload.input_shards),
                )
                dependency_outcomes = tuple(
                    run_dependency_inference(
                        contract=contract,
                        workload=dependency_workload,
                        input_shard=shard,
                        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
                        prompt_bytes=assets.prompt_bytes,
                        response_schema_bytes=assets.response_schema_bytes,
                        replay_resolver=inference,
                        replay_source_receipt_artifact=ref.recorded_inference_receipt(),
                    )
                    for shard, ref in zip(dependency_workload.input_shards, refs, strict=True)
                )
            dependency_batch = inference.persist_batch(dependency_outcomes)
            _index(
                lifecycle,
                run_id=command.run_id,
                stage=LifecycleEvidenceStageV1.DEPENDENCY,
                owners=(
                    LifecycleEvidenceOwnerV1(
                        owner_kind="dependency-batch",
                        owner_id=dependency_batch.batch_id,
                        owner_sha256=dependency_batch.batch_sha256,
                        relative_locator=(
                            f"inference/evidence/batches/{dependency_batch.batch_sha256}.json"
                        ),
                    ),
                ),
            )
            _notify(failure_hook, "dependency-batch-recorded")
        else:
            dependency_outcomes, dependency_batch = reopened_dependency
        _validate_stage_outcomes(
            dependency_outcomes,
            contract=contract,
            task=RecordedInferenceTask.DEPENDENCY,
            workload_id=dependency_workload.index.workload_id,
            workload_sha256=dependency_workload.index.workload_sha256,
            shard_bindings=tuple(
                (item.shard_id, item.shard_sha256) for item in dependency_workload.input_shards
            ),
        )
        dependency_classifications: list[DependencyClassification] = []
        for outcome in dependency_outcomes:
            if outcome.dependency_output is None:
                raise ApplicationStartLifecycleError("dependency batch lacks its typed output")
            dependency_classifications.extend(outcome.dependency_output.classifications)
        dependency_results = DependencyClassificationResultSet.create(
            workload=dependency_workload,
            classifications=tuple(dependency_classifications),
        )
        supporting = tuple(
            item
            for item in classification_results.classifications
            if item.disposition == PairDisposition.SUPERSEDES
            and item.materialization_status == GraphMaterializationStatus.GRAPH_VALID
            and item.relation_assessment is not None
            and item.relation_assessment.endpoint_ids is not None
            and item.relation_assessment.pair.revision(
                item.relation_assessment.endpoint_ids[0]
            ).document
            == incoming_document
            and item.relation_assessment.pair.revision(
                item.relation_assessment.endpoint_ids[1]
            ).document
            == predecessors[0]
        )
        if not supporting:
            raise ApplicationStartLifecycleError(
                "incoming replacement lacks exact predecessor-bound SUPERSEDES support"
            )
        support_rationale = " ".join(
            f"{item.classification_id}: {item.rationale}" for item in supporting
        )
        if len(support_rationale) > 4000:
            raise ApplicationStartLifecycleError(
                "replacement support rationale exceeds the bounded proposal contract"
            )
        replacement = DocumentReplacementProposalCandidate.create(
            newer_document=incoming_document,
            older_document=predecessors[0],
            supporting_classifications=supporting,
            rationale=support_rationale,
            confidence=min(item.confidence for item in supporting),
        )
        proposal = build_temporal_proposal(
            verified_bootstrap=analysis.verification_capability,
            snapshot=analysis.snapshot,
            candidates=candidates,
            classification_results=classification_results,
            classification_outcomes=classification_outcomes,
            classification_evidence_batch_id=classification_batch.batch_id,
            classification_evidence_batch_sha256=classification_batch.batch_sha256,
            inventory_capability=analysis.inventory_capability,
            dependency_workload=dependency_workload,
            dependency_results=dependency_results,
            dependency_outcomes=dependency_outcomes,
            dependency_evidence_batch_id=dependency_batch.batch_id,
            dependency_evidence_batch_sha256=dependency_batch.batch_sha256,
            replacement_candidate=replacement,
        )
        temporal_evidence = build_temporal_analysis_evidence(
            verified_bootstrap=analysis.verification_capability,
            snapshot=analysis.snapshot,
            candidates=candidates,
            classification_results=classification_results,
            inventory_capability=analysis.inventory_capability,
            dependency_workload=dependency_workload,
            dependency_results=dependency_results,
            replacement_candidate=replacement,
            proposal=proposal,
        )
        manifest_bytes = temporal_evidence.canonical_bytes()
        manifest_path = inference.persist_temporal_analysis_manifest(
            manifest_id=temporal_evidence.manifest_id,
            manifest_sha256=temporal_evidence.manifest_sha256,
            content=manifest_bytes,
        )
        if not (
            manifest_path == f"temporal/evidence/analyses/{temporal_evidence.manifest_sha256}.json"
            and inference.resolve_temporal_analysis_manifest(
                manifest_id=temporal_evidence.manifest_id,
                manifest_sha256=temporal_evidence.manifest_sha256,
            )
            == manifest_bytes
        ):
            raise ApplicationStartLifecycleError(
                "temporal analysis manifest did not reopen exactly before commit"
            )
        _index(
            lifecycle,
            run_id=command.run_id,
            stage=LifecycleEvidenceStageV1.TEMPORAL,
            owners=(
                LifecycleEvidenceOwnerV1(
                    owner_kind="temporal-analysis",
                    owner_id=temporal_evidence.manifest_id,
                    owner_sha256=temporal_evidence.manifest_sha256,
                    relative_locator=manifest_path,
                ),
            ),
        )
        _notify(failure_hook, "temporal-evidence-recorded")
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        commit = commit_temporal_proposal(
            store,
            proposal,
            temporal_analysis=temporal_evidence,
            evidence_repository=inference,
            classification_batch=classification_batch,
            dependency_batch=dependency_batch,
            source_note_resolver=GenericSourceNoteInventoryResolverV2(
                verified_bootstrap=analysis.verification_capability,
                workspace_source_notes=workspace_source_notes,
            ),
        )
        store.close()
        _notify(failure_hook, "temporal-commit-recorded")
        resolver = ApplicationOperatorRunAuthorityResolver(
            evidence_root=evidence_root,
            state_path=state_path,
            configuration_sha256=application_configuration_sha256(settings),
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
        receipt_sha256 = _operation_receipt_sha256(store, commit.operation_id)
        store.close()
        _, prepared_navigation = _prepare_navigation(
            state_path=state_path,
            run_id=command.run_id,
            resolver=resolver,
        )
        prepared_navigation.temporal[(command.run_id, commit.operation_id, receipt_sha256)] = (
            resolver.resolve_temporal_proposal(
                run_id=command.run_id,
                target_id=commit.operation_id,
                target_sha256=receipt_sha256,
            )
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        _link(
            store,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.TEMPORAL_PROPOSAL,
            target_id=commit.operation_id,
            target_sha256=receipt_sha256,
            resolver=prepared_navigation,
        )
        store.close()
        _notify(failure_hook, "temporal-proposal-linked")
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        request = open_temporal_review(
            store,
            commit,
            requester_id="mastervault.change-control.application",
            rationale="Review every exact temporal subject before managed change planning.",
            operation_id=_operation(command.operation_id, "temporal-review"),
        )
        store.close()
        _notify(failure_hook, "temporal-review-recorded")
        _, prepared_navigation = _prepare_navigation(
            state_path=state_path,
            run_id=command.run_id,
            resolver=resolver,
        )
        store = SqliteManagedChangeControlStore(state_path, secure_open=True)
        _link(
            store,
            parent_operation_id=command.operation_id,
            run_id=command.run_id,
            kind=OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
            target_id=request.request.request_id,
            target_sha256=request.request.request_payload_sha256,
            resolver=prepared_navigation,
        )
        store.close()
        _notify(failure_hook, "temporal-review-linked")
        return StartLifecycleTemporalReviewV1(request_id=request.request.request_id)
    finally:
        store.close()


__all__ = [
    "ApplicationStartLifecycleError",
    "StartLifecycleCompletedNoOpV1",
    "StartLifecycleResultV1",
    "StartLifecycleTemporalReviewV1",
    "resume_completed_temporal_publication",
    "run_start_change_lifecycle",
]
