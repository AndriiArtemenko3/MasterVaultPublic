"""Authoritative repository adapter for dependency SourceNote inventories.

All filesystem access is confined to resolver construction.  The returned
capability is a process-local, sealed snapshot whose ``verify`` method performs
only deterministic in-memory integrity checks.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, SupportsIndex

from mastervault.change_control.bootstrap import (
    ANALYSIS_AGGREGATE_ID,
    AnalysisBootstrapBinding,
    AnalysisBootstrapIntegrityError,
    VerifiedAnalysisBootstrapCapability,
    incoming_claim_evidence_sha256,
    verify_analysis_bootstrap_snapshot,
)
from mastervault.change_control.claim_scopes import claim_scopes_v1
from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.incoming import (
    MANIFEST_RELATIVE_PATH,
    VerifiedIncomingEvent,
    load_verified_incoming_event,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    DependencyRegistry,
    DocumentReplacementSet,
    DocumentVersionRegistry,
    RelationGraph,
    TemporalConstraintSet,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    verified_repository_root,
)
from mastervault.change_control.seed import (
    VerifiedDocumentContext,
    load_verified_prechange_seed_manifest_from_repository,
    resolve_claim_revision,
    verify_seed_document_context,
)
from mastervault.change_control.store import ChangeControlSnapshot
from mastervault.vaultfs.frontmatter import split_frontmatter

PRECHANGE_MANIFEST_RELATIVE_PATH: Final = "datasets/larkstead/change_control/sl2_prechange.yaml"
_PROCESSED_PREFIX: Final = PurePosixPath("datasets/larkstead/processed")
_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)


class SourceNoteInventoryResolutionError(ValueError):
    """Repository roots do not exactly reproduce the authenticated snapshot."""


def _empty_aggregate(
    *,
    documents: tuple[Any, ...],
    claims: tuple[VersionedClaimRevision, ...],
) -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id=ANALYSIS_AGGREGATE_ID,
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )


def _prechange_projection(
    *, repo_root: Path, manifest_path: Path, expected_manifest_sha256: str
) -> tuple[tuple[VerifiedDocumentContext, ...], ChangeControlAggregate, str, Any]:
    manifest = load_verified_prechange_seed_manifest_from_repository(
        repo_root=repo_root,
        manifest_path=manifest_path,
    )
    if manifest.manifest_sha256 != expected_manifest_sha256:
        raise SourceNoteInventoryResolutionError(
            "pre-change manifest does not match the authenticated bootstrap binding"
        )
    contexts: list[VerifiedDocumentContext] = []
    claims: list[VersionedClaimRevision] = []
    for item in sorted(manifest.manifest.documents, key=lambda value: value.document_id):
        context = verify_seed_document_context(
            repo_root=repo_root,
            manifest_context=manifest,
            document_id=item.document_id,
        )
        contexts.append(context)
        for claim in sorted(context.source_note.key_claims, key=lambda value: value.id):
            claims.append(
                resolve_claim_revision(
                    context=context,
                    source_claim_id=claim.id,
                    declared_effective_from=item.declared_effective_from,
                    declared_effective_to=item.declared_effective_to,
                    scopes=claim_scopes_v1(
                        document_family=item.document_family,
                        affects=tuple(claim.affects),
                    ),
                )
            )
    aggregate = _empty_aggregate(
        documents=tuple(context.document for context in contexts),
        claims=tuple(claims),
    )
    return tuple(contexts), aggregate, manifest.manifest_sha256, manifest.manifest


def _incoming_note_snapshot(event: VerifiedIncomingEvent) -> CanonicalSourceNoteSnapshot:
    processed = event.processed_snapshot
    try:
        text = processed.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceNoteInventoryResolutionError(
            "incoming canonical SourceNote is not UTF-8"
        ) from exc
    _yaml_text, body, had_frontmatter = split_frontmatter(text)
    if not had_frontmatter:
        raise SourceNoteInventoryResolutionError(
            "incoming canonical SourceNote has no resolvable body boundary"
        )
    body_start_char = len(text) - len(body)
    document = event.document
    processed_path = PurePosixPath(event.manifest.document.processed_path)
    try:
        note_path = processed_path.relative_to(_PROCESSED_PREFIX).as_posix()
    except ValueError as exc:
        raise SourceNoteInventoryResolutionError(
            "incoming canonical SourceNote is outside the processed vault root"
        ) from exc
    return CanonicalSourceNoteSnapshot.create(
        document=document,
        source_note_path=note_path,
        source_note_utf8=text,
        body_start_char=body_start_char,
    )


def _prechange_note_snapshot(context: VerifiedDocumentContext) -> CanonicalSourceNoteSnapshot:
    return CanonicalSourceNoteSnapshot.create(
        document=context.document,
        source_note_path=context.source_note_path,
        source_note_utf8=context.note_text,
        body_start_char=context.body_start_char,
    )


def _require_manifest_bindings(
    *,
    binding: AnalysisBootstrapBinding,
    prechange_manifest_sha256: str,
    prechange_manifest: Any,
    incoming: VerifiedIncomingEvent,
) -> None:
    incoming_manifest = incoming.manifest
    incoming_document = incoming.document
    if (
        prechange_manifest_sha256 != binding.seed_manifest_sha256
        or prechange_manifest.scenario_id != binding.seed_scenario_id
        or prechange_manifest.as_of != binding.seed_as_of
        or incoming.manifest_sha256 != binding.incoming_manifest_sha256
        or incoming_manifest.event_id != binding.incoming_event_id
        or incoming.event_identity != binding.incoming_event_identity
        or incoming_document.document_id != binding.incoming_document_id
        or incoming_document.document_version_id != binding.incoming_document_version_id
        or incoming_manifest.arrived_on != binding.analysis_as_of
        or incoming_claim_evidence_sha256(incoming) != binding.incoming_claim_evidence_sha256
        or tuple(sorted(item.claim_revision_id for item in incoming.claim_revisions))
        != binding.changed_claim_revision_ids
    ):
        raise SourceNoteInventoryResolutionError(
            "repository manifests do not match the authenticated bootstrap binding"
        )


def _require_exact_inventory_coverage(
    *,
    snapshot: ChangeControlSnapshot,
    notes: tuple[CanonicalSourceNoteSnapshot, ...],
    source_claim_ids: dict[str, tuple[str, ...]],
) -> None:
    aggregate_documents = {
        item.document_version_id: item for item in snapshot.aggregate.documents.documents
    }
    note_documents = {item.document.document_version_id: item for item in notes}
    if set(note_documents) != set(aggregate_documents) or len(note_documents) != len(notes):
        raise SourceNoteInventoryResolutionError(
            "SourceNote inventory does not exactly cover aggregate documents"
        )
    for document_version_id, inventory_note in note_documents.items():
        if inventory_note.document != aggregate_documents[document_version_id]:
            raise SourceNoteInventoryResolutionError(
                "SourceNote document metadata differs from the aggregate binding"
            )

    aggregate_claim_keys: list[tuple[str, str]] = []
    for claim in snapshot.aggregate.claims.revisions:
        claim_note = note_documents.get(claim.document.document_version_id)
        if claim_note is None or claim.document != claim_note.document:
            raise SourceNoteInventoryResolutionError(
                "aggregate claim names a missing or mismatched SourceNote document"
            )
        if (
            claim.source.source_note_path != claim_note.source_note_path
            or claim.source.source_note_sha256 != claim_note.source_note_sha256
        ):
            raise SourceNoteInventoryResolutionError(
                "aggregate claim SourceNote path/SHA binding is not exact"
            )
        aggregate_claim_keys.append(
            (claim.document.document_version_id, claim.source.source_claim_id)
        )

    note_claim_keys = [
        (document_version_id, source_claim_id)
        for document_version_id, claim_ids in source_claim_ids.items()
        for source_claim_id in claim_ids
    ]
    if (
        len(aggregate_claim_keys) != len(set(aggregate_claim_keys))
        or len(note_claim_keys) != len(set(note_claim_keys))
        or set(aggregate_claim_keys) != set(note_claim_keys)
    ):
        raise SourceNoteInventoryResolutionError(
            "aggregate claims do not exactly cover SourceNote source_claim_id bindings"
        )


def _capability_payload(
    *, inventory: SourceNoteInventory, binding: AnalysisBootstrapBinding
) -> bytes:
    return canonical_json_bytes(
        {
            "namespace": "mastervault.repository-source-note-inventory-capability.v1",
            "analysis_bootstrap": binding.model_dump(mode="json"),
            "inventory": inventory.model_dump(mode="json"),
        }
    )


@dataclass(frozen=True, eq=False)
class RepositoryVerifiedSourceNoteInventoryCapability:
    """Non-serializable in-memory authority over exact SourceNote bytes."""

    _inventory: SourceNoteInventory
    _verified_bootstrap: VerifiedAnalysisBootstrapCapability
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("repository SourceNote capabilities are service-created only")

    def __reduce__(self) -> Any:
        raise TypeError("repository SourceNote capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("repository SourceNote capabilities are process-local")

    def __getstate__(self) -> Any:
        raise TypeError("repository SourceNote capabilities are process-local")

    def verify(self, *, snapshot: ChangeControlSnapshot) -> SourceNoteInventory:
        """Revalidate captured content and authority without filesystem I/O."""

        if self._token is not _CAPABILITY_TOKEN:
            raise SourceNoteInventoryResolutionError(
                "SourceNote capability was not created by the repository resolver"
            )
        try:
            binding = verify_analysis_bootstrap_snapshot(self._verified_bootstrap, snapshot)
            inventory = SourceNoteInventory.model_validate(
                self._inventory.model_dump(mode="python")
            )
        except (AnalysisBootstrapIntegrityError, AttributeError, TypeError, ValueError) as exc:
            raise SourceNoteInventoryResolutionError(
                "SourceNote capability or authenticated snapshot was altered"
            ) from exc
        if inventory != self._inventory:
            raise SourceNoteInventoryResolutionError("captured SourceNote inventory was altered")
        expected = hmac.new(
            _CAPABILITY_SECRET,
            _capability_payload(inventory=inventory, binding=binding),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self._seal, expected):
            raise SourceNoteInventoryResolutionError("SourceNote capability seal was altered")
        if (
            inventory.aggregate_id != snapshot.aggregate.aggregate_id
            or inventory.snapshot_revision != snapshot.revision
            or inventory.aggregate_sha256 != snapshot.aggregate_sha256
        ):
            raise SourceNoteInventoryResolutionError(
                "SourceNote inventory does not bind the supplied aggregate snapshot"
            )
        return inventory


@dataclass(frozen=True)
class RepositorySourceNoteInventoryResolver:
    """Reload repository roots and mint one sealed dependency inventory."""

    repo_root: Path
    prechange_manifest_path: Path
    incoming_manifest_path: Path
    verified_bootstrap: VerifiedAnalysisBootstrapCapability

    def resolve_source_note_inventory(
        self, *, snapshot: ChangeControlSnapshot
    ) -> RepositoryVerifiedSourceNoteInventoryCapability:
        try:
            binding = verify_analysis_bootstrap_snapshot(self.verified_bootstrap, snapshot)
            repo_root = verified_repository_root(self.repo_root)
        except (
            AnalysisBootstrapIntegrityError,
            RepositoryFileBoundaryError,
            RepositoryFileIntegrityError,
        ) as exc:
            raise SourceNoteInventoryResolutionError(
                "cannot authenticate repository inventory authority"
            ) from exc

        expected_prechange = repo_root.joinpath(
            *PurePosixPath(PRECHANGE_MANIFEST_RELATIVE_PATH).parts
        )
        expected_incoming = repo_root.joinpath(*PurePosixPath(MANIFEST_RELATIVE_PATH).parts)
        if (
            Path(os.path.abspath(self.prechange_manifest_path)) != expected_prechange
            or Path(os.path.abspath(self.incoming_manifest_path)) != expected_incoming
        ):
            raise SourceNoteInventoryResolutionError(
                "resolver manifests must name the exact allowlisted SL2 runtime paths"
            )

        try:
            contexts, prechange, manifest_sha256, manifest = _prechange_projection(
                repo_root=repo_root,
                manifest_path=expected_prechange,
                expected_manifest_sha256=binding.seed_manifest_sha256,
            )
            incoming = load_verified_incoming_event(
                repo_root=repo_root,
                manifest_path=expected_incoming,
            )
        except (TypeError, ValueError, OSError) as exc:
            raise SourceNoteInventoryResolutionError(
                "repository SourceNote roots failed verification"
            ) from exc

        _require_manifest_bindings(
            binding=binding,
            prechange_manifest_sha256=manifest_sha256,
            prechange_manifest=manifest,
            incoming=incoming,
        )
        if aggregate_sha256(prechange) != binding.prechange_aggregate_sha256:
            raise SourceNoteInventoryResolutionError(
                "reloaded pre-change roots do not reproduce the bootstrap aggregate"
            )
        analysis = _empty_aggregate(
            documents=(*prechange.documents.documents, incoming.document),
            claims=(*prechange.claims.revisions, *incoming.claim_revisions),
        )
        if (
            analysis != snapshot.aggregate
            or aggregate_sha256(analysis) != snapshot.aggregate_sha256
        ):
            raise SourceNoteInventoryResolutionError(
                "reloaded repository roots do not reproduce the exact analysis snapshot"
            )

        incoming_note = _incoming_note_snapshot(incoming)
        notes = (*tuple(_prechange_note_snapshot(item) for item in contexts), incoming_note)
        source_claim_ids = {
            context.document.document_version_id: tuple(
                sorted(claim.id for claim in context.source_note.key_claims)
            )
            for context in contexts
        }
        source_claim_ids[incoming.document.document_version_id] = tuple(
            sorted(claim.id for claim in incoming.source_note.key_claims)
        )
        _require_exact_inventory_coverage(
            snapshot=snapshot,
            notes=notes,
            source_claim_ids=source_claim_ids,
        )
        inventory = SourceNoteInventory.create(snapshot=snapshot, notes=notes)
        seal = hmac.new(
            _CAPABILITY_SECRET,
            _capability_payload(inventory=inventory, binding=binding),
            hashlib.sha256,
        ).hexdigest()
        capability = RepositoryVerifiedSourceNoteInventoryCapability(
            _inventory=inventory,
            _verified_bootstrap=self.verified_bootstrap,
            _token=_CAPABILITY_TOKEN,
            _seal=seal,
        )
        capability.verify(snapshot=snapshot)
        return capability


__all__ = [
    "PRECHANGE_MANIFEST_RELATIVE_PATH",
    "RepositorySourceNoteInventoryResolver",
    "RepositoryVerifiedSourceNoteInventoryCapability",
    "SourceNoteInventoryResolutionError",
]
