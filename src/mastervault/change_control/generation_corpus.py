"""Complete-corpus reconstruction for one immutable serving generation.

The managed temporal projection intentionally contains SourceNotes only.  A
workspace-origin generation must also carry forward every attested wiki,
decision, strategy, and unselected SourceNote from generation zero.  This
module joins those independently authoritative inputs without walking a root
or treating the selected managed subset as the serving corpus.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

from mastervault.change_control.managed_generation import (
    GenerationSourceNoteEntry,
    ManagedActivationCommand,
)
from mastervault.change_control.managed_review import (
    AuthorityRevisionBinding,
    GenerationZeroOriginBasis,
    WorkspaceGenerationZeroOriginBasis,
)
from mastervault.change_control.workspace_bootstrap import (
    VerifiedWorkspaceBootstrapCapability,
    WorkspaceNoteKind,
    WorkspaceVaultMember,
    verify_workspace_bootstrap_capability,
)
from mastervault.sync.indexer import (
    ExactVaultNoteInput,
    ExactWorkspaceFileInput,
    prepare_exact_vault_notes,
)


class GenerationCorpusIntegrityError(ValueError):
    """Generation inputs do not reproduce one complete authorized corpus."""


def _canonical_base_notes(
    base_notes: tuple[ExactVaultNoteInput, ...],
) -> tuple[ExactVaultNoteInput, ...]:
    """Snapshot caller-owned inputs into exact immutable value carriers once."""

    if type(base_notes) is not tuple:
        raise GenerationCorpusIntegrityError(
            "workspace generation-zero base inventory must be one exact tuple"
        )
    result: list[ExactVaultNoteInput] = []
    for item in base_notes:
        if type(item) is not ExactVaultNoteInput:
            raise GenerationCorpusIntegrityError(
                "workspace generation-zero base inventory contains a substituted note"
            )
        if (
            type(item.rel_path) is not str
            or type(item.content) is not bytes
            or not isinstance(item.workspace, Path)
            or type(item.supporting_files) is not tuple
        ):
            raise GenerationCorpusIntegrityError(
                "workspace generation-zero base inventory contains invalid value carriers"
            )
        support: list[ExactWorkspaceFileInput] = []
        for member in item.supporting_files:
            if (
                type(member) is not ExactWorkspaceFileInput
                or type(member.rel_path) is not str
                or type(member.content) is not bytes
            ):
                raise GenerationCorpusIntegrityError(
                    "workspace generation-zero support inventory was substituted"
                )
            support.append(
                ExactWorkspaceFileInput(
                    rel_path=member.rel_path,
                    content=bytes(member.content),
                )
            )
        result.append(
            ExactVaultNoteInput(
                rel_path=item.rel_path,
                content=bytes(item.content),
                workspace=Path(str(item.workspace)),
                supporting_files=tuple(support),
            )
        )
    return tuple(result)


def verify_generation_base_inventory(
    *,
    expected_authority: AuthorityRevisionBinding,
    verified_workspace_bootstrap: VerifiedWorkspaceBootstrapCapability | None,
    base_notes: tuple[ExactVaultNoteInput, ...] | None,
) -> tuple[ExactVaultNoteInput, ...] | None:
    """Bind optional base notes to the exact durable generation-zero origin.

    Seed-fixture authority has no generic all-note inventory and therefore
    rejects a supplied base.  Workspace authority requires the exact guarded
    inventory and compares its parsed note kinds, paths, byte counts, and
    hashes to the persisted bootstrap chain on every verification.
    """

    origin = expected_authority.origin_basis
    if isinstance(origin, GenerationZeroOriginBasis):
        if verified_workspace_bootstrap is not None or base_notes is not None:
            raise GenerationCorpusIntegrityError(
                "seed-origin generation cannot accept a workspace base inventory"
            )
        return None
    if not isinstance(origin, WorkspaceGenerationZeroOriginBasis):
        raise GenerationCorpusIntegrityError(
            "managed generation has an unsupported generation-zero origin"
        )
    if verified_workspace_bootstrap is None or base_notes is None:
        raise GenerationCorpusIntegrityError(
            "workspace-origin generation requires its exact guarded base inventory"
        )
    if type(verified_workspace_bootstrap) is not VerifiedWorkspaceBootstrapCapability:
        raise GenerationCorpusIntegrityError(
            "workspace generation-zero base requires the exact verified capability"
        )
    try:
        state = verify_workspace_bootstrap_capability(verified_workspace_bootstrap)
        inventory_receipt, index_receipt = state.require_complete()
        canonical_base_notes = _canonical_base_notes(base_notes)
        prepared = prepare_exact_vault_notes(canonical_base_notes)
    except (TypeError, ValueError) as exc:
        raise GenerationCorpusIntegrityError(
            "workspace generation-zero base inventory cannot be freshly verified"
        ) from exc
    if not (
        expected_authority.aggregate_id == state.intent.aggregate_id
        and expected_authority.authority_revision == 0
        and expected_authority.active_generation.generation_number == 0
        and origin.bootstrap_id == state.intent.bootstrap_id
        and origin.intent_sha256 == state.intent.intent_sha256
        and origin.inventory_receipt_id == inventory_receipt.receipt_id
        and origin.inventory_receipt_sha256 == inventory_receipt.receipt_sha256
        and origin.index_receipt_id == index_receipt.receipt_id
        and origin.index_receipt_sha256 == index_receipt.receipt_sha256
        and origin.prechange_head.aggregate_id == inventory_receipt.aggregate_id
        and origin.prechange_head.revision == inventory_receipt.aggregate_revision
        and origin.prechange_head.aggregate_sha256 == inventory_receipt.aggregate_sha256
        and origin.generation_zero_manifest_sha256
        == expected_authority.active_generation.manifest_sha256
    ):
        raise GenerationCorpusIntegrityError(
            "workspace base inventory differs from durable generation-zero authority"
        )
    actual_members = tuple(
        WorkspaceVaultMember(
            logical_path=note.rel_path,
            note_kind=WorkspaceNoteKind(projected.doc.doc_type),
            content_sha256=hashlib.sha256(note.content).hexdigest(),
            byte_count=len(note.content),
        )
        for note, projected in zip(canonical_base_notes, prepared, strict=True)
    )
    if actual_members != state.inventory.vault_members:
        raise GenerationCorpusIntegrityError(
            "supplied base notes do not exactly reproduce the persisted workspace inventory"
        )
    return canonical_base_notes


def complete_generation_index_notes(
    *,
    command: ManagedActivationCommand,
    managed_notes: tuple[object, ...],
    base_notes: tuple[ExactVaultNoteInput, ...] | None,
) -> tuple[ExactVaultNoteInput, ...]:
    """Overlay current managed SourceNotes onto an exact all-note base.

    Every path represented anywhere in the managed projection is removed from
    generation zero first.  Only the projection's current entry is then added,
    so a predecessor can never leak back into the active index.  PDF support
    bytes may be carried from the same base path only when the successor's
    exact frontmatter validates against those bytes.
    """

    workspace_origin = isinstance(
        command.expected_authority.origin_basis,
        WorkspaceGenerationZeroOriginBasis,
    )
    if workspace_origin != (base_notes is not None):
        raise GenerationCorpusIntegrityError(
            "generation base inventory does not match its authority origin"
        )
    base = () if base_notes is None else _canonical_base_notes(base_notes)
    base_by_path = {item.rel_path: item for item in base}
    if len(base_by_path) != len(base):
        raise GenerationCorpusIntegrityError("generation base paths are not unique")
    projection_paths = {item.logical_path for item in command.projection.entries}
    if type(managed_notes) is not tuple:
        raise GenerationCorpusIntegrityError(
            "generation managed notes must be one exact tuple"
        )
    try:
        typed_notes = tuple(
            (
                cast(Any, item).entry,
                bytes(cast(Any, item).content),
                Path(str(cast(Any, item).workspace)),
            )
            for item in managed_notes
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise GenerationCorpusIntegrityError(
            "generation contains a substituted managed note"
        ) from exc
    if any(
        not isinstance(entry, GenerationSourceNoteEntry)
        or type(content) is not bytes
        or not isinstance(workspace, Path)
        for entry, content, workspace in typed_notes
    ):
        raise GenerationCorpusIntegrityError(
            "generation contains an invalid managed note"
        )
    current = tuple(
        (entry, content, workspace)
        for entry, content, workspace in typed_notes
        if entry.included_in_serving_index
    )
    if tuple(entry.entry_id for entry, _content, _workspace in current) != (
        command.projection.serving_entry_ids
    ):
        raise GenerationCorpusIntegrityError(
            "current managed notes differ from the exact serving projection"
        )

    complete = [item for item in base if item.rel_path not in projection_paths]
    for entry, content, workspace in current:
        prior = base_by_path.get(entry.logical_path)
        supporting = () if prior is None else prior.supporting_files
        candidate = ExactVaultNoteInput(
            rel_path=entry.logical_path,
            content=content,
            workspace=(
                prior.workspace
                if prior is not None and supporting
                else workspace
            ),
            supporting_files=supporting,
        )
        try:
            # This rejects both a PDF-backed successor without authoritative
            # support bytes and stale carried support whose exact bindings no
            # longer match the successor frontmatter.
            prepare_exact_vault_notes((candidate,))
        except (TypeError, ValueError) as exc:
            raise GenerationCorpusIntegrityError(
                "managed SourceNote structural bindings cannot be carried into the complete index"
            ) from exc
        complete.append(candidate)

    result = tuple(sorted(complete, key=lambda item: item.rel_path))
    try:
        prepared = prepare_exact_vault_notes(result)
    except (TypeError, ValueError) as exc:
        raise GenerationCorpusIntegrityError(
            "complete generation corpus is invalid or contains conflicting identities"
        ) from exc
    expected_paths = (
        {item.rel_path for item in base} - projection_paths
    ) | {entry.logical_path for entry, _content, _workspace in current}
    if (
        {item.rel_path for item in result} != expected_paths
        or {item.doc.rel_path for item in prepared} != expected_paths
        or len(result) != len(expected_paths)
    ):
        raise GenerationCorpusIntegrityError(
            "complete generation corpus has missing or surplus documents"
        )
    return result


__all__ = [
    "GenerationCorpusIntegrityError",
    "complete_generation_index_notes",
    "verify_generation_base_inventory",
]
