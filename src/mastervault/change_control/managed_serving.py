"""Fail-closed opener for the exact active managed SQLite generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.generation_corpus import (
    complete_generation_index_notes,
    verify_generation_base_inventory,
)
from mastervault.change_control.generation_resolution import (
    derive_generation_projection,
    require_exact_generation_source,
    resolve_generation_notes,
)
from mastervault.change_control.managed_activation_service import (
    ManagedActivationServiceError,
    ManagedGenerationSourceResolver,
)
from mastervault.change_control.managed_generation import ManagedIndexReadinessReceipt
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ManagedGenerationRepositoryError,
    ResolvedGenerationSourceNote,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ManagedGenerationManifestBindingV2,
    ManagedRevisionDecisionRecord,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    ManagedGenerationActivationState,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.workspace_bootstrap import (
    VerifiedWorkspaceBootstrapCapability,
)
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import ExactVaultNoteInput


class ManagedServingError(ManagedActivationServiceError):
    """Active authority cannot be served by one exact verified SQLite index."""


class ManagedServingConflictError(ManagedServingError):
    """Active authority changed across one bounded generation resolution."""


class ManagedServingGenerationZeroError(ManagedServingError):
    """Generation zero has no PR15 managed index readiness receipt."""


@dataclass(frozen=True)
class ManagedServingResolution:
    """One verified, current managed generation opened for read-only serving."""

    backend: SqliteBackend
    authority: AuthorityRevisionBinding
    activation_state: ManagedGenerationActivationState
    index_receipt: ManagedIndexReadinessReceipt
    resolved_notes: tuple[ResolvedGenerationSourceNote, ...]
    index_notes: tuple[ExactVaultNoteInput, ...]
    workspace_base_notes: tuple[ExactVaultNoteInput, ...] | None
    verified_workspace_bootstrap: VerifiedWorkspaceBootstrapCapability | None
    generation_root: Path
    protected_paths: tuple[Path, ...]
    repository: ManagedGenerationRepository
    resolver: ManagedGenerationSourceResolver
    decision: ManagedRevisionDecisionRecord

    def verify(self) -> None:
        """Revalidate immutable repository and index evidence before rendering."""

        command = self.activation_state.intent.command
        manifest = self.decision.command.generation_manifest
        if not isinstance(manifest, ManagedGenerationManifestBindingV2):
            raise ManagedServingError("active managed generation does not have a v2 manifest")
        try:
            if (
                self.repository.root != self.generation_root
                or self.repository.repository_id != command.generation_repository_id
                or not self.repository.read_only
            ):
                raise ManagedServingError("active command names another generation repository")
            self.repository.verify_open_read_only_index(
                backend=self.backend,
                receipt=self.index_receipt,
            )
            source = require_exact_generation_source(
                binding=manifest.governing_source_adoption,
                source=self.resolver.resolve_reviewed_generation_source(
                    manifest.governing_source_adoption
                ),
            )
            projection = derive_generation_projection(
                decision=self.decision,
                source=source,
            )
            if projection != command.projection:
                raise ManagedServingError("active generation projection is no longer reproducible")
            for event in self.activation_state.publication_events:
                self.repository.open_publication(event)
            notes = resolve_generation_notes(
                source=source,
                projection=projection,
                state=self.activation_state,
                repository=self.repository,
                base_notes=self.workspace_base_notes or (),
            )
            if notes != self.resolved_notes:
                raise ManagedServingError("active generation SourceNotes changed while serving")
            base_notes = verify_generation_base_inventory(
                expected_authority=command.expected_authority,
                verified_workspace_bootstrap=self.verified_workspace_bootstrap,
                base_notes=self.workspace_base_notes,
            )
            index_notes = complete_generation_index_notes(
                command=command,
                managed_notes=notes,
                base_notes=base_notes,
            )
            if base_notes != self.workspace_base_notes or index_notes != self.index_notes:
                raise ManagedServingError("active generation complete corpus changed while serving")
            self.repository.verify_index(
                receipt=self.index_receipt,
                command=command,
                notes=notes,
                base_notes=base_notes,
                verified_workspace_bootstrap=self.verified_workspace_bootstrap,
            )
        except ManagedServingError:
            raise
        except (
            ManagedActivationServiceError,
            ManagedGenerationRepositoryError,
            TypeError,
            ValueError,
        ) as exc:
            raise ManagedServingError("active managed generation changed while serving") from exc

    def close(self) -> None:
        """Verify once more, then close the read-only backend and its guards."""

        self.backend.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_active_managed_sqlite_generation(
    *,
    aggregate_id: str,
    store: SqliteManagedChangeControlStore,
    resolver: ManagedGenerationSourceResolver,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
    prechange_head: AggregateHeadBinding | None = None,
    authority_context: AuthorityVerificationContext | None = None,
    generation_root: Path,
    protected_paths: tuple[Path, ...] = (),
    workspace_base_notes: tuple[ExactVaultNoteInput, ...] | None = None,
) -> ManagedServingResolution:
    """Resolve the exact active generation and open its index read-only."""

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

    state = store.get_active_managed_generation_state(
        aggregate_id,
        resolver=resolver,
        authority_context=context,
    )
    if state is None:
        raise ManagedServingGenerationZeroError(
            "active generation zero has no managed SQLite index"
        )
    command = state.intent.command
    try:
        verified_base_notes = verify_generation_base_inventory(
            expected_authority=command.expected_authority,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
            base_notes=workspace_base_notes,
        )
    except ValueError as exc:
        raise ManagedServingError(str(exc)) from exc
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
            read_only=True,
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
        authority_context=context,
    ).decision_record
    if decision is None or decision.record_sha256 != command.decision_record_sha256:
        raise ManagedServingError("active managed decision cannot be reopened exactly")
    manifest = decision.command.generation_manifest
    if not isinstance(manifest, ManagedGenerationManifestBindingV2):
        raise ManagedServingError("active managed generation does not have a v2 manifest")
    source = require_exact_generation_source(
        binding=manifest.governing_source_adoption,
        source=resolver.resolve_reviewed_generation_source(manifest.governing_source_adoption),
    )
    projection = derive_generation_projection(decision=decision, source=source)
    if projection != command.projection:
        raise ManagedServingError("active generation projection is no longer reproducible")
    try:
        for event in state.publication_events:
            repository.open_publication(event)
        notes = resolve_generation_notes(
            source=source,
            projection=projection,
            state=state,
            repository=repository,
            base_notes=verified_base_notes or (),
        )
        index_notes = complete_generation_index_notes(
            command=command,
            managed_notes=notes,
            base_notes=verified_base_notes,
        )
        repository.verify_index(
            receipt=index_receipt,
            command=command,
            notes=notes,
            base_notes=verified_base_notes,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
        )
        backend = repository.open_read_only_index(index_receipt)
    except (
        ManagedActivationServiceError,
        ManagedGenerationRepositoryError,
        TypeError,
        ValueError,
    ) as exc:
        raise ManagedServingError("active managed SQLite index cannot be verified") from exc
    try:
        current = store.get_active_managed_generation_state(
            aggregate_id,
            resolver=resolver,
            authority_context=context,
        )
        if current is None or current != state:
            raise ManagedServingConflictError(
                "active authority changed during managed index opening"
            )
        current_receipt = current.activation_receipt
        current_index_receipt = current.index_receipt
        if current_receipt is None or current_index_receipt is None:
            raise ManagedServingError(
                "active authority and immutable index readiness evidence do not match"
            )
        resolution = ManagedServingResolution(
            backend=backend,
            authority=current_receipt.activated_authority,
            activation_state=current,
            index_receipt=current_index_receipt,
            resolved_notes=notes,
            index_notes=index_notes,
            workspace_base_notes=verified_base_notes,
            verified_workspace_bootstrap=context.verified_workspace_bootstrap,
            generation_root=repository.root,
            protected_paths=forbidden,
            repository=repository,
            resolver=resolver,
            decision=decision,
        )
        repository.bind_open_read_only_index_close_verifier(
            backend=backend,
            verifier=resolution.verify,
        )
        return resolution
    except BaseException:
        backend.close()
        raise


def open_active_managed_sqlite_index(
    *,
    aggregate_id: str,
    store: SqliteManagedChangeControlStore,
    resolver: ManagedGenerationSourceResolver,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
    prechange_head: AggregateHeadBinding | None = None,
    authority_context: AuthorityVerificationContext | None = None,
    generation_root: Path,
    protected_paths: tuple[Path, ...] = (),
    workspace_base_notes: tuple[ExactVaultNoteInput, ...] | None = None,
) -> SqliteBackend:
    """Open only the exact active index, retaining the legacy backend-only API."""

    return open_active_managed_sqlite_generation(
        aggregate_id=aggregate_id,
        store=store,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
        authority_context=authority_context,
        generation_root=generation_root,
        protected_paths=protected_paths,
        workspace_base_notes=workspace_base_notes,
    ).backend


__all__ = [
    "ManagedServingResolution",
    "ManagedServingConflictError",
    "ManagedServingError",
    "ManagedServingGenerationZeroError",
    "open_active_managed_sqlite_generation",
    "open_active_managed_sqlite_index",
]
