"""Restart-safe orchestration for one reviewed managed SQLite generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.generation_corpus import (
    verify_generation_base_inventory,
)
from mastervault.change_control.generation_resolution import (
    ManagedActivationServiceError,
    ResolvedManagedGenerationSource,
    derive_generation_projection,
    require_exact_generation_source,
    resolve_generation_notes,
)
from mastervault.change_control.generic_governing_source import (
    ResolvedGenericGenerationSourceV2,
)
from mastervault.change_control.managed_generation import (
    ManagedActivationCommand,
    ManagedGenerationActivationReceipt,
    ResolvedManagedGenerationProjection,
)
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    RepositoryVerifiedManagedGenerationEffects,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    GenerationZeroOriginBasis,
    GenericGoverningSourceAdoptionBindingV2,
    GoverningSourceAdoptionAuthority,
    ManagedGenerationManifestBindingV2,
    WorkspaceGenerationZeroOriginBasis,
)
from mastervault.change_control.managed_review_repository import (
    ResolvedReviewedGenerationSource,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    ManagedGenerationActivationState,
    ManagedReviewRepositoryResolver,
    ManagedRevisionStoreLifecycle,
    OperatorRunAuthorityResolver,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.regression_baseline import (
    GenerationZeroBaselineRepository,
    VerifiedGenerationZeroBaselineCapability,
)
from mastervault.change_control.store import ChangeControlIdempotencyError
from mastervault.change_control.synchronous_lifecycle_store_models import (
    GenerationZeroBaselineStoreRecordV1,
)
from mastervault.providers import EmbeddingProvider
from mastervault.sync.indexer import ExactVaultNoteInput


class ManagedGenerationSourceResolver(ManagedReviewRepositoryResolver, Protocol):
    """Repository capabilities needed by the generation effect service."""

    def resolve_reviewed_generation_source(
        self, binding: GoverningSourceAdoptionAuthority
    ) -> ResolvedReviewedGenerationSource | ResolvedGenericGenerationSourceV2: ...

    def protected_generation_roots(self) -> tuple[Path, ...]: ...


class ManagedActivationBackendUnsupportedError(ManagedActivationServiceError):
    """PR15 intentionally supports managed activation through SQLite only."""


class ManagedActivationOutcome(StrEnum):
    NO_OP = "no-op"
    ACTIVATED = "activated"


@dataclass(frozen=True)
class ManagedActivationServiceResult:
    outcome: ManagedActivationOutcome
    request_id: str
    operation_id: str
    projection: ResolvedManagedGenerationProjection | None
    command: ManagedActivationCommand | None
    receipt: ManagedGenerationActivationReceipt | None


FailureHook = Callable[[str], None]


def _notify(hook: FailureHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _resolve_generation_source(
    resolver: ManagedGenerationSourceResolver,
    binding: GoverningSourceAdoptionAuthority,
) -> ResolvedManagedGenerationSource:
    try:
        source = resolver.resolve_reviewed_generation_source(binding)
    except (TypeError, ValueError) as exc:
        raise ManagedActivationServiceError(
            "governing generation source cannot be freshly reopened"
        ) from exc
    return require_exact_generation_source(binding=binding, source=source)


def _require_baseline_command_authority(
    *,
    record: GenerationZeroBaselineStoreRecordV1,
    expected_authority: AuthorityRevisionBinding,
    embedding_model_version: str,
    embedding_dimensions: int,
) -> None:
    """Bind external baseline evidence to this exact generation-zero command."""

    baseline = record.baseline_receipt
    authority = baseline.authority
    generation = authority.query_generation
    origin = expected_authority.origin_basis
    if type(origin) is not WorkspaceGenerationZeroOriginBasis:
        raise ManagedActivationServiceError(
            "operator-run activation requires exact workspace generation-zero authority"
        )
    matches_origin = (
        authority.workspace_inventory_receipt_id == origin.inventory_receipt_id
        and authority.workspace_inventory_receipt_sha256 == origin.inventory_receipt_sha256
        and authority.legacy_readiness_receipt_id == origin.index_receipt_id
        and authority.legacy_readiness_receipt_sha256 == origin.index_receipt_sha256
    )
    if not (
        record.incoming_admission_receipt_id == authority.incoming_admission_receipt_id
        and record.incoming_admission_receipt_sha256 == authority.incoming_admission_receipt_sha256
        and authority.run_id == record.incoming_admission.intent.run_id
        and generation.backend == "sqlite"
        and generation.generation_id == expected_authority.active_generation.generation_id
        and generation.generation_number
        == expected_authority.active_generation.generation_number
        == 0
        and generation.active_generation_id == expected_authority.active_generation.generation_id
        and generation.active_authority_revision == expected_authority.authority_revision == 0
        and generation.manifest_sha256 == expected_authority.active_generation.manifest_sha256
        and generation.embedding_model == embedding_model_version
        and generation.embedding_dimensions == embedding_dimensions
        and generation.index_logical_fingerprint is not None
        and generation.index_file_sha256 is not None
        and generation.index_file_byte_count is not None
        and generation.storage_schema_version is not None
        and matches_origin
    ):
        raise ManagedActivationServiceError(
            "generation-zero baseline differs from exact activation authority and index identity"
        )


def _require_operator_generic_source(
    binding: GoverningSourceAdoptionAuthority, *, operator_run_present: bool = True
) -> None:
    if type(binding) is GenericGoverningSourceAdoptionBindingV2:
        if not operator_run_present:
            raise ManagedActivationServiceError(
                "generic-v2 activation requires its exact operator-run authority"
            )
        return
    if operator_run_present:
        raise ManagedActivationServiceError(
            "operator-run activation requires exact generic-v2 governing source authority"
        )


def _baseline_binding_operation_id(command: ManagedActivationCommand) -> str:
    digest = hashlib.sha256(command.activation_id.encode("utf-8")).hexdigest()
    return f"activation-baseline:{digest}"


def activate_reviewed_managed_generation(
    *,
    request_id: str,
    operation_id: str,
    store: SqliteManagedChangeControlStore,
    resolver: ManagedGenerationSourceResolver,
    generation_root: Path,
    embedder: EmbeddingProvider,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
    prechange_head: AggregateHeadBinding | None = None,
    authority_context: AuthorityVerificationContext | None = None,
    backend_kind: str = "sqlite",
    protected_paths: tuple[Path, ...] = (),
    workspace_base_notes: tuple[ExactVaultNoteInput, ...] | None = None,
    baseline_record: GenerationZeroBaselineStoreRecordV1 | None = None,
    baseline_capability: VerifiedGenerationZeroBaselineCapability | None = None,
    baseline_repository: GenerationZeroBaselineRepository | None = None,
    operator_run_resolver: OperatorRunAuthorityResolver | None = None,
    failure_hook: FailureHook | None = None,
) -> ManagedActivationServiceResult:
    """Publish, index, and activate one exact managed decision synchronously.

    SQLite authority, not the filesystem and not a workflow checkpoint, owns
    progress. Each invocation reopens immutable receipts and can therefore
    converge after process failure or a lost acknowledgement.
    """

    if backend_kind != "sqlite":
        raise ManagedActivationBackendUnsupportedError(
            "managed generation activation supports SQLite only in PR15"
        )
    coordinated = getattr(store, "securely_coordinated", False)
    if type(coordinated) is not bool or not coordinated:
        raise ManagedActivationServiceError(
            "managed generation activation requires one secure coordinated authority store"
        )
    if authority_context is not None:
        if verified_bootstrap is not None or prechange_head is not None:
            raise TypeError("authority_context cannot be mixed with legacy bootstrap arguments")
        context = authority_context
    else:
        if verified_bootstrap is None or prechange_head is None:
            raise TypeError(
                "either authority_context or the complete legacy bootstrap pair is required"
            )
        context = AuthorityVerificationContext.legacy(
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
    review = store.get_managed_review(
        request_id,
        resolver=resolver,
        authority_context=context,
    )
    decision = review.decision_record
    if review.lifecycle != ManagedRevisionStoreLifecycle.DECIDED or decision is None:
        raise ManagedActivationServiceError(
            "managed generation activation requires one authoritative decision"
        )
    manifest = decision.command.generation_manifest
    if not manifest.requires_activation:
        return ManagedActivationServiceResult(
            outcome=ManagedActivationOutcome.NO_OP,
            request_id=request_id,
            operation_id=operation_id,
            projection=None,
            command=None,
            receipt=None,
        )
    if not isinstance(manifest, ManagedGenerationManifestBindingV2):
        raise ManagedActivationServiceError(
            "PR15 activation requires the exact accepted v2 manifest"
        )
    expected_authority = decision.command.expected_authority
    if not (
        isinstance(
            expected_authority.origin_basis,
            (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
        )
        and expected_authority.authority_revision == 0
        and expected_authority.active_generation.generation_number == 0
    ):
        raise ManagedActivationServiceError(
            "PR15 activation supports exactly one managed successor from generation zero"
        )
    run_id = decision.command.request_record.command.bundle.run_binding.run_id
    try:
        operator_run = store.get_operator_run(run_id, resolver=operator_run_resolver)
    except (TypeError, ValueError) as exc:
        raise ManagedActivationServiceError(
            "operator-run authority cannot be freshly reopened before activation"
        ) from exc
    _require_operator_generic_source(
        manifest.governing_source_adoption,
        operator_run_present=operator_run is not None,
    )
    baseline_required = operator_run is not None
    if operator_run is not None:
        if not (
            type(baseline_record) is GenerationZeroBaselineStoreRecordV1
            and type(baseline_capability) is VerifiedGenerationZeroBaselineCapability
            and type(baseline_repository) is GenerationZeroBaselineRepository
            and baseline_repository.read_only
            and operator_run_resolver is not None
        ):
            raise ManagedActivationServiceError(
                "operator-run activation requires exact SQLite and read-only baseline authority"
            )
        if not (
            operator_run.record.command.run_id == run_id
            and operator_run.record.command.aggregate_id == expected_authority.aggregate_id
            and operator_run.record.command.base_authority_id == expected_authority.authority_id
            and operator_run.record.command.base_authority_revision
            == expected_authority.authority_revision
            and operator_run.record.command.base_active_pointer_sha256
            == expected_authority.active_pointer_sha256
        ):
            raise ManagedActivationServiceError(
                "operator run differs from the activation's exact prior authority"
            )
        try:
            reopened_capability = baseline_repository.reopen(run_id)
            reopened_receipt = baseline_repository.verify_capability(reopened_capability)
            supplied_receipt = baseline_repository.verify_capability(baseline_capability)
            sqlite_baseline = store.get_generation_zero_baseline(
                baseline_record.baseline_receipt.receipt_id
            )
        except (TypeError, ValueError) as exc:
            raise ManagedActivationServiceError(
                "generation-zero baseline cannot be freshly reopened before activation"
            ) from exc
        if not (
            reopened_capability == baseline_capability
            and reopened_receipt == supplied_receipt == baseline_record.baseline_receipt
            and sqlite_baseline == baseline_record
            and operator_run_resolver.resolve_generation_zero_baseline(baseline_record)
            == baseline_record.baseline_receipt
        ):
            raise ManagedActivationServiceError(
                "generation-zero baseline authorities do not reopen exactly"
            )
        _require_baseline_command_authority(
            record=baseline_record,
            expected_authority=expected_authority,
            embedding_model_version=embedder.model_version,
            embedding_dimensions=embedder.dimensions,
        )
    try:
        verified_base_notes = verify_generation_base_inventory(
            expected_authority=expected_authority,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
            base_notes=workspace_base_notes,
        )
    except ValueError as exc:
        raise ManagedActivationServiceError(str(exc)) from exc
    active_authority = store.get_active_generation(
        expected_authority.aggregate_id,
        authority_context=context,
    )
    owned_request_id = store.get_managed_activation_operation_request_id(operation_id)
    if owned_request_id is not None and owned_request_id != request_id:
        raise ChangeControlIdempotencyError(
            "managed activation operation_id was reused for different inputs"
        )
    prior_state: ManagedGenerationActivationState | None = None
    if active_authority != expected_authority and owned_request_id == request_id:
        prior_state = store.get_managed_generation_activation(
            operation_id,
            resolver=resolver,
            authority_context=context,
        )
    exact_completed_replay = bool(
        prior_state is not None
        and prior_state.activation_receipt is not None
        and prior_state.intent.command.request_id == request_id
        and prior_state.activation_receipt.prior_authority == expected_authority
        and prior_state.activation_receipt.activated_authority == active_authority
    )
    if active_authority != expected_authority and not exact_completed_replay:
        raise ManagedActivationServiceError(
            "PR15 activation base is no longer the exact generation-zero authority"
        )
    source = _resolve_generation_source(resolver, manifest.governing_source_adoption)
    projection = derive_generation_projection(decision=decision, source=source)

    # The repository constructor creates its root, so every backend, decision,
    # source-byte, and protected-path preflight above deliberately precedes it.
    forbidden = tuple(
        dict.fromkeys(
            (
                *resolver.protected_generation_roots(),
                store.db_path,
                *protected_paths,
            )
        )
    )
    repository = ManagedGenerationRepository(
        generation_root,
        forbidden_roots=forbidden,
    )
    command = ManagedActivationCommand.create(
        operation_id=operation_id,
        request_id=request_id,
        decision_id=decision.command.decision_id,
        decision_record_sha256=decision.record_sha256,
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.manifest_sha256,
        projection=projection,
        expected_authority=decision.command.expected_authority,
        generation_repository_id=repository.repository_id,
        embedding_provider=embedder.name,
        embedding_model_version=embedder.model_version,
        embedding_dimensions=embedder.dimensions,
    )
    baseline_guard: Callable[[], None] | None
    if baseline_required:
        assert operator_run is not None
        assert baseline_record is not None
        assert baseline_capability is not None
        assert baseline_repository is not None
        assert operator_run_resolver is not None

        def baseline_guard() -> None:
            reopened = baseline_repository.reopen(run_id)
            reopened_receipt = baseline_repository.verify_capability(reopened)
            supplied_receipt = baseline_repository.verify_capability(baseline_capability)
            resolver_receipt = operator_run_resolver.resolve_generation_zero_baseline(
                baseline_record
            )
            incoming = baseline_record.incoming_admission.intent
            reopened_incoming = operator_run_resolver.resolve_incoming_source(incoming)
            if not (
                reopened == baseline_capability
                and reopened_receipt
                == supplied_receipt
                == resolver_receipt
                == baseline_record.baseline_receipt
                and reopened_incoming.bundle_id == incoming.bundle_id
                and reopened_incoming.bundle_sha256 == incoming.bundle_sha256
                and reopened_incoming.admission_sha256 == incoming.admission_sha256
                and reopened_incoming.source_receipt_sha256 == incoming.source_receipt_sha256
                and reopened_incoming.projection_sha256 == incoming.projection_sha256
                and reopened_incoming.inference_sha256 == incoming.inference_sha256
            ):
                raise ManagedActivationServiceError(
                    "generation-zero baseline or operator source authority changed"
                )
            _require_baseline_command_authority(
                record=baseline_record,
                expected_authority=command.expected_authority,
                embedding_model_version=command.embedding_model_version,
                embedding_dimensions=command.embedding_dimensions,
            )
            fresh_source = _resolve_generation_source(resolver, manifest.governing_source_adoption)
            if derive_generation_projection(decision=decision, source=fresh_source) != (
                command.projection
            ):
                raise ManagedActivationServiceError(
                    "governing generation source changed from the activation command"
                )
            adoption = manifest.governing_source_adoption
            if type(adoption) is not GenericGoverningSourceAdoptionBindingV2 or not (
                incoming.bundle_id == adoption.incoming_bundle_id
                and incoming.bundle_sha256 == adoption.incoming_bundle_sha256
                and incoming.admission_sha256 == adoption.incoming_admission_sha256
                and incoming.source_receipt_sha256 == adoption.incoming_source_receipt_sha256
                and incoming.projection_sha256 == adoption.incoming_projection_sha256
                and incoming.inference_sha256 == adoption.incoming_inference_sha256
            ):
                raise ManagedActivationServiceError(
                    "generation-zero baseline incoming authority differs from governing source"
                )

        baseline_guard()
    else:
        baseline_guard = None
    store.claim_managed_activation(
        command,
        resolver=resolver,
        authority_context=context,
    )
    if baseline_required:
        assert baseline_record is not None
        store.bind_activation_to_generation_zero_baseline(
            operation_id=_baseline_binding_operation_id(command),
            activation_id=command.activation_id,
            run_id=run_id,
            baseline_receipt_id=baseline_record.baseline_receipt.receipt_id,
        )
    _notify(failure_hook, "intent-committed")

    state = store.get_managed_generation_activation(
        operation_id,
        resolver=resolver,
        authority_context=context,
    )
    assert state is not None
    for ordinal, publication in enumerate(manifest.publication_delta):
        if ordinal < len(state.publication_events):
            event = state.publication_events[ordinal]
            repository.open_publication(event)
        else:
            content = resolver.open_artifact(publication.staged_artifact)
            event = repository.publish(
                command=command,
                ordinal=ordinal,
                publication=publication,
                content=content,
                published_at=state.intent.created_at,
            )
            _notify(failure_hook, f"publication-file:{ordinal}")
            publication_capability = repository.verify_effects(
                command=command,
                publication_events=(event,),
                index_receipt=None,
                base_notes=verified_base_notes,
                verified_workspace_bootstrap=context.verified_workspace_bootstrap,
            )
            store.record_managed_publication(
                event,
                capability=publication_capability,
            )
            _notify(failure_hook, f"publication-receipt:{ordinal}")
        state = store.get_managed_generation_activation(
            operation_id,
            resolver=resolver,
            authority_context=context,
        )
        assert state is not None

    notes = resolve_generation_notes(
        source=source,
        projection=projection,
        state=state,
        repository=repository,
        base_notes=verified_base_notes or (),
    )
    if state.index_receipt is None:
        built = repository.build_index(
            command=command,
            notes=notes,
            embedder=embedder,
            ready_at=state.intent.created_at,
            base_notes=verified_base_notes,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
        )
        _notify(failure_hook, "index-file-ready")
        index_capability = repository.verify_effects(
            command=command,
            publication_events=state.publication_events,
            index_receipt=built.receipt,
            notes=notes,
            base_notes=verified_base_notes,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
        )
        store.record_managed_index_readiness(
            built.receipt,
            capability=index_capability,
        )
        _notify(failure_hook, "index-receipt-committed")
    else:
        repository.verify_index(
            receipt=state.index_receipt,
            command=command,
            notes=notes,
            base_notes=verified_base_notes,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
        )

    # Mutation guard: reopen the decision, reviewed snapshot, exact SourceNote
    # bytes, published bytes, and logical/physical index immediately before CAS.
    fresh_review = store.get_managed_review(
        request_id,
        resolver=resolver,
        authority_context=context,
    )
    if fresh_review.decision_record != decision:
        raise ManagedActivationServiceError(
            "managed decision changed between effect preparation and CAS"
        )
    fresh_source = _resolve_generation_source(resolver, manifest.governing_source_adoption)
    fresh_projection = derive_generation_projection(decision=decision, source=fresh_source)
    if fresh_projection != projection:
        raise ManagedActivationServiceError(
            "reviewed generation projection changed before authority CAS"
        )
    state = store.get_managed_generation_activation(
        operation_id,
        resolver=resolver,
        authority_context=context,
    )
    assert state is not None and state.index_receipt is not None
    try:
        fresh_base_notes = verify_generation_base_inventory(
            expected_authority=expected_authority,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
            base_notes=workspace_base_notes,
        )
    except ValueError as exc:
        raise ManagedActivationServiceError(str(exc)) from exc
    if fresh_base_notes != verified_base_notes:
        raise ManagedActivationServiceError("workspace base inventory changed before authority CAS")
    fresh_notes = resolve_generation_notes(
        source=fresh_source,
        projection=fresh_projection,
        state=state,
        repository=repository,
        base_notes=fresh_base_notes or (),
    )
    repository.verify_index(
        receipt=state.index_receipt,
        command=command,
        notes=fresh_notes,
        base_notes=fresh_base_notes,
        verified_workspace_bootstrap=context.verified_workspace_bootstrap,
    )
    effects_capability = repository.verify_effects(
        command=command,
        publication_events=state.publication_events,
        index_receipt=state.index_receipt,
        notes=fresh_notes,
        base_notes=fresh_base_notes,
        verified_workspace_bootstrap=context.verified_workspace_bootstrap,
    )
    _notify(failure_hook, "before-authority-cas")
    receipt = store.activate_managed_generation(
        command,
        capability=effects_capability,
        resolver=resolver,
        authority_context=context,
        baseline_guard=baseline_guard,
        failure_hook=failure_hook,
    )
    # Lost acknowledgement and post-commit workspace/repository drift must not
    # return a successful activation result from this bounded service call.
    RepositoryVerifiedManagedGenerationEffects.verify(
        effects_capability,
        command=command,
        publication_events=state.publication_events,
        index_receipt=state.index_receipt,
    )
    _notify(failure_hook, "authority-cas-committed")
    return ManagedActivationServiceResult(
        outcome=ManagedActivationOutcome.ACTIVATED,
        request_id=request_id,
        operation_id=operation_id,
        projection=projection,
        command=command,
        receipt=receipt,
    )


__all__ = [
    "ManagedActivationBackendUnsupportedError",
    "ManagedActivationOutcome",
    "ManagedActivationServiceError",
    "ManagedActivationServiceResult",
    "ManagedGenerationSourceResolver",
    "activate_reviewed_managed_generation",
]
