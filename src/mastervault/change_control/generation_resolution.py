"""Shared reconstruction of one reviewed managed generation.

Activation and serving both need to reproduce the exact generation projection
and resolve every SourceNote byte snapshot behind it.  Keeping that logic here
prevents the read path from depending on private helpers owned by the effect
orchestrator.
"""

from __future__ import annotations

import hashlib

from mastervault.change_control.generic_governing_source import (
    ResolvedGenericGenerationSourceV2,
)
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
from mastervault.change_control.managed_review import (
    GenericGoverningSourceAdoptionBindingV2,
    GoverningSourceAdoptionAuthority,
    ManagedGoverningSourceAdoptionBinding,
    ManagedRevisionDecisionRecord,
)
from mastervault.change_control.managed_review_repository import (
    ResolvedReviewedGenerationSource,
)
from mastervault.change_control.managed_store import (
    ManagedGenerationActivationError,
    ManagedGenerationActivationState,
)
from mastervault.sync.indexer import ExactVaultNoteInput


class ManagedActivationServiceError(ManagedGenerationActivationError):
    """A managed generation could not be safely reconstructed or reconciled."""


ResolvedManagedGenerationSource = (
    ResolvedReviewedGenerationSource | ResolvedGenericGenerationSourceV2
)


def require_exact_generation_source(
    *,
    binding: GoverningSourceAdoptionAuthority,
    source: ResolvedManagedGenerationSource,
) -> ResolvedManagedGenerationSource:
    """Fail closed unless a resolver returned the exact source kind and adoption."""

    if type(binding) is ManagedGoverningSourceAdoptionBinding:
        if type(source) is ResolvedReviewedGenerationSource and source.adoption == binding:
            return source
    elif type(binding) is GenericGoverningSourceAdoptionBindingV2:
        if type(source) is ResolvedGenericGenerationSourceV2 and source.adoption == binding:
            return source
    else:  # pragma: no cover - defensive against a widened authority union
        raise ManagedActivationServiceError(
            "generation source uses an unsupported governing-source authority"
        )
    raise ManagedActivationServiceError(
        "generation resolver returned the wrong exact governing-source authority"
    )


def derive_generation_projection(
    *,
    decision: ManagedRevisionDecisionRecord,
    source: ResolvedManagedGenerationSource,
) -> ResolvedManagedGenerationProjection:
    """Reproduce the exact managed projection from reviewed source authority."""

    return derive_managed_generation_projection(
        decision=decision,
        reviewed_inventory=source.inventory,
        temporal_constraints=source.snapshot.aggregate.validated_temporal_constraints(),
    )


def resolve_generation_notes(
    *,
    source: ResolvedManagedGenerationSource,
    projection: ResolvedManagedGenerationProjection,
    state: ManagedGenerationActivationState,
    repository: ManagedGenerationRepository,
    base_notes: tuple[ExactVaultNoteInput, ...] = (),
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
    base_by_path: dict[str, ExactVaultNoteInput] = {}
    for base_note in base_notes:
        if base_note.rel_path in base_by_path:
            raise ManagedActivationServiceError(
                "generation base notes contain a duplicate logical path"
            )
        base_by_path[base_note.rel_path] = base_note
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
            matched_base_note = base_by_path.get(entry.logical_path)
            if matched_base_note is None:
                workspace = source.workspace_root
            elif not (
                matched_base_note.content == content
                and len(matched_base_note.content) == entry.source_note_byte_count
                and hashlib.sha256(matched_base_note.content).hexdigest()
                == entry.source_note_sha256
            ):
                raise ManagedActivationServiceError(
                    "generation base-note workspace mapping differs from exact SourceNote bytes"
                )
            else:
                workspace = matched_base_note.workspace
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
    "ResolvedManagedGenerationSource",
    "derive_generation_projection",
    "require_exact_generation_source",
    "resolve_generation_notes",
]
