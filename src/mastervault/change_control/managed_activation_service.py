"""Restart-safe orchestration for one reviewed managed SQLite generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.managed_generation import (
    GoverningSourceBinding,
    ManagedActivationCommand,
    ManagedGenerationActivationReceipt,
    PublishedSourceBinding,
    ResolvedManagedGenerationProjection,
    ReviewedSourceBinding,
    derive_managed_generation_projection,
)
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ResolvedGenerationSourceNote,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    GenerationZeroOriginBasis,
    ManagedGenerationManifestBindingV2,
    ManagedGoverningSourceAdoptionBinding,
    ManagedRevisionDecisionRecord,
)
from mastervault.change_control.managed_review_repository import (
    ResolvedReviewedGenerationSource,
)
from mastervault.change_control.managed_store import (
    ManagedGenerationActivationError,
    ManagedGenerationActivationState,
    ManagedReviewRepositoryResolver,
    ManagedRevisionStoreLifecycle,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.store import ChangeControlIdempotencyError
from mastervault.providers import EmbeddingProvider


class ManagedGenerationSourceResolver(ManagedReviewRepositoryResolver, Protocol):
    """Repository capabilities needed by the generation effect service."""

    def resolve_reviewed_generation_source(
        self, binding: ManagedGoverningSourceAdoptionBinding
    ) -> ResolvedReviewedGenerationSource: ...

    def protected_generation_roots(self) -> tuple[Path, ...]: ...


class ManagedActivationServiceError(ManagedGenerationActivationError):
    """A managed generation could not be safely reconciled."""


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


def _derive_projection(
    *,
    decision: ManagedRevisionDecisionRecord,
    source: ResolvedReviewedGenerationSource,
) -> ResolvedManagedGenerationProjection:
    return derive_managed_generation_projection(
        decision=decision,
        reviewed_inventory=source.inventory,
        temporal_constraints=source.snapshot.aggregate.validated_temporal_constraints(),
    )


def _resolve_generation_notes(
    *,
    source: ResolvedReviewedGenerationSource,
    projection: ResolvedManagedGenerationProjection,
    state: ManagedGenerationActivationState,
    repository: ManagedGenerationRepository,
) -> tuple[ResolvedGenerationSourceNote, ...]:
    inventory = {item.document.document_version_id: item for item in source.inventory.notes}
    events = {
        item.publication.destination.destination_id: item for item in state.publication_events
    }
    if len(events) != len(state.publication_events):
        raise ManagedActivationServiceError(
            "managed publication events contain duplicate destinations"
        )
    generation_workspace = repository.root / "generations" / projection.generation_id / "canonical"
    resolved: list[ResolvedGenerationSourceNote] = []
    for entry in projection.entries:
        binding = entry.source
        if isinstance(binding, PublishedSourceBinding):
            event = events.get(binding.destination_id)
            if event is None or not (
                event.publication.staged_artifact.artifact_id == binding.staged_artifact_id
                and event.publication.destination.path == binding.destination_path
            ):
                raise ManagedActivationServiceError(
                    "published generation SourceNote lacks its exact durable event"
                )
            content = repository.open_publication(event)
            workspace = generation_workspace
        elif isinstance(binding, (ReviewedSourceBinding, GoverningSourceBinding)):
            note = inventory.get(entry.document.document_version_id)
            if note is None or not (
                note.source_note_path == entry.logical_path
                and note.source_note_sha256 == entry.source_note_sha256
                and note.source_note_utf8_bytes == entry.source_note_byte_count
                and note.snapshot_id == binding.source_note_snapshot_id
                and note.snapshot_sha256 == binding.source_note_snapshot_sha256
            ):
                raise ManagedActivationServiceError(
                    "reviewed generation SourceNote differs from exact projection"
                )
            content = note.source_note_utf8.encode("utf-8")
            workspace = source.workspace_root
        else:  # pragma: no cover - exhaustive discriminated-union guard
            raise ManagedActivationServiceError(
                "generation SourceNote has an unsupported source binding"
            )
        if len(content) != entry.source_note_byte_count or (
            hashlib.sha256(content).hexdigest() != entry.source_note_sha256
        ):
            raise ManagedActivationServiceError(
                "resolved generation SourceNote bytes differ from projection"
            )
        resolved.append(
            ResolvedGenerationSourceNote(
                entry=entry,
                content=content,
                workspace=workspace,
            )
        )
    return tuple(resolved)


def activate_reviewed_managed_generation(
    *,
    request_id: str,
    operation_id: str,
    store: SqliteManagedChangeControlStore,
    resolver: ManagedGenerationSourceResolver,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability,
    prechange_head: AggregateHeadBinding,
    generation_root: Path,
    embedder: EmbeddingProvider,
    backend_kind: str = "sqlite",
    protected_paths: tuple[Path, ...] = (),
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
    review = store.get_managed_review(
        request_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
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
        isinstance(expected_authority.origin_basis, GenerationZeroOriginBasis)
        and expected_authority.authority_revision == 0
        and expected_authority.active_generation.generation_number == 0
    ):
        raise ManagedActivationServiceError(
            "PR15 activation supports exactly one managed successor from generation zero"
        )
    active_authority = store.get_active_generation(
        expected_authority.aggregate_id,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
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
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
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
    source = resolver.resolve_reviewed_generation_source(manifest.governing_source_adoption)
    projection = _derive_projection(decision=decision, source=source)

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
    store.claim_managed_activation(
        command,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )
    _notify(failure_hook, "intent-committed")

    state = store.get_managed_generation_activation(
        operation_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
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
            )
            store.record_managed_publication(
                event,
                capability=publication_capability,
            )
            _notify(failure_hook, f"publication-receipt:{ordinal}")
        state = store.get_managed_generation_activation(
            operation_id,
            resolver=resolver,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        assert state is not None

    notes = _resolve_generation_notes(
        source=source,
        projection=projection,
        state=state,
        repository=repository,
    )
    if state.index_receipt is None:
        built = repository.build_index(
            command=command,
            notes=notes,
            embedder=embedder,
            ready_at=state.intent.created_at,
        )
        _notify(failure_hook, "index-file-ready")
        index_capability = repository.verify_effects(
            command=command,
            publication_events=state.publication_events,
            index_receipt=built.receipt,
            notes=notes,
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
        )

    # Mutation guard: reopen the decision, reviewed snapshot, exact SourceNote
    # bytes, published bytes, and logical/physical index immediately before CAS.
    fresh_review = store.get_managed_review(
        request_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )
    if fresh_review.decision_record != decision:
        raise ManagedActivationServiceError(
            "managed decision changed between effect preparation and CAS"
        )
    fresh_source = resolver.resolve_reviewed_generation_source(manifest.governing_source_adoption)
    fresh_projection = _derive_projection(decision=decision, source=fresh_source)
    if fresh_projection != projection:
        raise ManagedActivationServiceError(
            "reviewed generation projection changed before authority CAS"
        )
    state = store.get_managed_generation_activation(
        operation_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )
    assert state is not None and state.index_receipt is not None
    fresh_notes = _resolve_generation_notes(
        source=fresh_source,
        projection=fresh_projection,
        state=state,
        repository=repository,
    )
    repository.verify_index(
        receipt=state.index_receipt,
        command=command,
        notes=fresh_notes,
    )
    effects_capability = repository.verify_effects(
        command=command,
        publication_events=state.publication_events,
        index_receipt=state.index_receipt,
        notes=fresh_notes,
    )
    _notify(failure_hook, "before-authority-cas")
    receipt = store.activate_managed_generation(
        command,
        capability=effects_capability,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
        failure_hook=failure_hook,
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
