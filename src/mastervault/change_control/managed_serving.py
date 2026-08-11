"""Fail-closed opener for the exact active managed SQLite generation."""

from __future__ import annotations

from pathlib import Path

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.managed_activation_service import (
    ManagedActivationServiceError,
    ManagedGenerationSourceResolver,
    _derive_projection,
    _resolve_generation_notes,
)
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ManagedGenerationRepositoryError,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedGenerationManifestBindingV2,
)
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.storage.sqlite import SqliteBackend


class ManagedServingError(ManagedActivationServiceError):
    """Active authority cannot be served by one exact verified SQLite index."""


class ManagedServingGenerationZeroError(ManagedServingError):
    """Generation zero has no PR15 managed index readiness receipt."""


def open_active_managed_sqlite_index(
    *,
    aggregate_id: str,
    store: SqliteManagedChangeControlStore,
    resolver: ManagedGenerationSourceResolver,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability,
    prechange_head: AggregateHeadBinding,
    generation_root: Path,
    protected_paths: tuple[Path, ...] = (),
) -> SqliteBackend:
    """Open the exact active index read-only, failing closed on every mismatch."""

    state = store.get_active_managed_generation_state(
        aggregate_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )
    if state is None:
        raise ManagedServingGenerationZeroError(
            "active generation zero has no managed SQLite index"
        )
    command = state.intent.command
    receipt = state.activation_receipt
    index_receipt = state.index_receipt
    if (
        receipt is None
        or index_receipt is None
        or not (
            receipt.activation_id == command.activation_id
            and receipt.activated_authority.aggregate_id == aggregate_id
            and receipt.activated_authority.active_generation.generation_id
            == command.projection.generation_id
            and receipt.activated_authority.active_generation.manifest_sha256
            == command.manifest_sha256
            and receipt.index_receipt_id == index_receipt.receipt_id
            and receipt.index_receipt_sha256 == index_receipt.receipt_sha256
        )
    ):
        raise ManagedServingError(
            "active authority and immutable index readiness evidence do not match"
        )
    forbidden = tuple(
        dict.fromkeys(
            (
                *resolver.protected_generation_roots(),
                store.db_path,
                *protected_paths,
            )
        )
    )
    try:
        repository = ManagedGenerationRepository(
            generation_root,
            forbidden_roots=forbidden,
            create=False,
        )
    except ManagedGenerationRepositoryError as exc:
        raise ManagedServingError(
            "active managed generation repository cannot be verified"
        ) from exc
    if repository.repository_id != command.generation_repository_id:
        raise ManagedServingError("active command names another generation repository")
    decision = store.get_managed_review(
        command.request_id,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    ).decision_record
    if decision is None or decision.record_sha256 != command.decision_record_sha256:
        raise ManagedServingError("active managed decision cannot be reopened exactly")
    manifest = decision.command.generation_manifest
    if not isinstance(manifest, ManagedGenerationManifestBindingV2):
        raise ManagedServingError("active managed generation does not have a v2 manifest")
    source = resolver.resolve_reviewed_generation_source(manifest.governing_source_adoption)
    projection = _derive_projection(decision=decision, source=source)
    if projection != command.projection:
        raise ManagedServingError("active generation projection is no longer reproducible")
    try:
        for event in state.publication_events:
            repository.open_publication(event)
        notes = _resolve_generation_notes(
            source=source,
            projection=projection,
            state=state,
            repository=repository,
        )
        repository.verify_index(
            receipt=index_receipt,
            command=command,
            notes=notes,
        )
        backend = repository.open_read_only_index(index_receipt)
    except (ManagedActivationServiceError, ManagedGenerationRepositoryError) as exc:
        raise ManagedServingError("active managed SQLite index cannot be verified") from exc
    try:
        current = store.get_active_managed_generation_state(
            aggregate_id,
            resolver=resolver,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        if current != state:
            raise ManagedServingError("active authority changed during managed index opening")
        return backend
    except BaseException:
        backend.close()
        raise


__all__ = [
    "ManagedServingError",
    "ManagedServingGenerationZeroError",
    "open_active_managed_sqlite_index",
]
