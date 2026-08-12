"""Shared reconstruction of one reviewed managed generation.

Activation and serving both need to reproduce the exact generation projection
and resolve every SourceNote byte snapshot behind it.  Keeping that logic here
prevents the read path from depending on private helpers owned by the effect
orchestrator.
"""

from __future__ import annotations

import hashlib

from mastervault.change_control.managed_generation import (
    GoverningSourceBinding,
    PublishedSourceBinding,
    ResolvedManagedGenerationProjection,
    ReviewedSourceBinding,
    derive_managed_generation_projection,
)
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ResolvedGenerationSourceNote,
)
from mastervault.change_control.managed_review import ManagedRevisionDecisionRecord
from mastervault.change_control.managed_review_repository import (
    ResolvedReviewedGenerationSource,
)
from mastervault.change_control.managed_store import (
    ManagedGenerationActivationError,
    ManagedGenerationActivationState,
)


class ManagedActivationServiceError(ManagedGenerationActivationError):
    """A managed generation could not be safely reconstructed or reconciled."""


def derive_generation_projection(
    *,
    decision: ManagedRevisionDecisionRecord,
    source: ResolvedReviewedGenerationSource,
) -> ResolvedManagedGenerationProjection:
    """Reproduce the exact managed projection from reviewed source authority."""

    return derive_managed_generation_projection(
        decision=decision,
        reviewed_inventory=source.inventory,
        temporal_constraints=source.snapshot.aggregate.validated_temporal_constraints(),
    )


def resolve_generation_notes(
    *,
    source: ResolvedReviewedGenerationSource,
    projection: ResolvedManagedGenerationProjection,
    state: ManagedGenerationActivationState,
    repository: ManagedGenerationRepository,
) -> tuple[ResolvedGenerationSourceNote, ...]:
    """Resolve and revalidate every exact SourceNote in a managed projection."""

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


__all__ = [
    "derive_generation_projection",
    "resolve_generation_notes",
]
