"""Pure bounded contracts for classifying document dependencies.

A graph-valid claim ``SUPERSEDES`` result identifies the newer changed claim
and the older policy claim whose dependants must be investigated.  Candidate
coverage does *not* come from claim-pair neighbours: a dependency may exist
only in canonical note body text and have no extracted claim.  Instead, a
repository resolver supplies an exhaustive, exact UTF-8 canonical-note
inventory for every document in the immutable aggregate snapshot.

This module performs no filesystem I/O, provider calls, persistence, impact
inference, or orchestration.  One complete document is one bounded input and
output shard.  Oversized documents and workloads over 64 candidates fail
closed; no prefix or chunk is silently selected.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationResultSet,
    GraphMaterializationStatus,
    validate_classification_results,
)
from mastervault.change_control.discovery import (
    MAX_SPAN_CANONICAL_BYTES_V1,
    MAX_SPANS_PER_DEPENDENCY_V1,
    RelationshipCandidateSet,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    DependencyAssessment,
    DependencyKind,
    DocumentSpanReference,
    DocumentVersionMetadata,
    PairDisposition,
    PersistedRelationType,
    TemporalResolution,
    TemporalResolutionContext,
    VersionedClaimRevision,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from mastervault.change_control.store import ChangeControlSnapshot


MAX_DEPENDENCY_CANDIDATES_V1 = 64
MAX_DEPENDENCY_RATIONALE_UTF8_BYTES_V1 = 4_000
MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1 = 256 * 1024
MAX_DEPENDENCY_DOCUMENT_SHARDS_V1 = 32
MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1 = 1024 * 1024

_CLAIM_REVISION_ID = r"^claimrev:[0-9a-f]{64}$"
_DOCUMENT_VERSION_ID = r"^docv:[0-9a-f]{64}$"
_PAIR_ID = r"^pair:[0-9a-f]{64}$"
_PAIR_CLASSIFICATION_ID = r"^pairclass:[0-9a-f]{64}$"
_NOTE_ID = r"^depsource:[0-9a-f]{64}$"
_CANDIDATE_ID = r"^depcand:[0-9a-f]{64}$"
_INPUT_SHARD_ID = r"^depin:[0-9a-f]{64}$"
_WORKLOAD_ID = r"^depwork:[0-9a-f]{64}$"
_CLASSIFICATION_ID = r"^depclass:[0-9a-f]{64}$"
_OUTPUT_SHARD_ID = r"^depout:[0-9a-f]{64}$"
_RESULT_ID = r"^depresult:[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DependencyAnalysisLimitError(RuntimeError):
    """A fixed limit was exceeded and no partial artifact was returned."""

    def __init__(self, *, category: str, limit: int, observed: int) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        super().__init__(f"dependency analysis limit exceeded: {category}={observed} > {limit}")


class DependencyCandidateExclusionReason(StrEnum):
    CHANGED_DOCUMENT = "changed-document"
    GOVERNING_UPSTREAM_DOCUMENT = "governing-upstream-document"


class DependencyDisposition(StrEnum):
    DEPENDS_ON = "DEPENDS_ON"
    NOT_DEPENDENT = "NOT_DEPENDENT"


_EXCLUSION_ORDER = {
    DependencyCandidateExclusionReason.CHANGED_DOCUMENT: 0,
    DependencyCandidateExclusionReason.GOVERNING_UPSTREAM_DOCUMENT: 1,
}


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_rationale(value: str) -> str:
    if not value or value != " ".join(value.split()):
        raise ValueError("dependency rationale must be canonical non-empty text")
    if len(value.encode("utf-8")) > MAX_DEPENDENCY_RATIONALE_UTF8_BYTES_V1:
        raise ValueError("dependency rationale exceeds the fixed v1 UTF-8 byte limit")
    return value


class CanonicalSourceNoteSnapshot(_StrictFrozenModel):
    """Complete exact UTF-8 SourceNote bytes for one aggregate document.

    ``body_start_char`` is supplied by the repository resolver that parsed the
    verified SourceNote.  Dependency evidence is required to start at or after
    this boundary, preventing frontmatter from masquerading as body evidence.
    """

    document: DocumentVersionMetadata
    source_note_path: str
    source_note_sha256: str = Field(pattern=SHA256_PATTERN)
    source_note_utf8: str
    source_note_utf8_bytes: int = Field(ge=0)
    body_start_char: int = Field(ge=0)
    snapshot_id: str = Field(pattern=_NOTE_ID)
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-source-note-snapshot.v1",
            "document": self.document.model_dump(mode="json"),
            "source_note_path": self.source_note_path,
            "source_note_sha256": self.source_note_sha256,
            "source_note_utf8": self.source_note_utf8,
            "source_note_utf8_bytes": self.source_note_utf8_bytes,
            "body_start_char": self.body_start_char,
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        note_bytes = self.source_note_utf8.encode("utf-8")
        if len(note_bytes) != self.source_note_utf8_bytes:
            raise ValueError("source-note UTF-8 byte count does not match exact text")
        if _bytes_sha256(note_bytes) != self.source_note_sha256:
            raise ValueError("source-note SHA does not match exact UTF-8 text")
        if self.body_start_char > len(self.source_note_utf8):
            raise ValueError("SourceNote body boundary lies beyond exact text")
        digest = _sha256(self._payload())
        if self.snapshot_sha256 != digest or self.snapshot_id != f"depsource:{digest}":
            raise ValueError("source-note snapshot ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        document: DocumentVersionMetadata,
        source_note_path: str,
        source_note_utf8: str,
        body_start_char: int,
    ) -> Self:
        note_bytes = source_note_utf8.encode("utf-8")
        note_sha = _bytes_sha256(note_bytes)
        data = {
            "namespace": "mastervault.dependency-source-note-snapshot.v1",
            "document": document.model_dump(mode="json"),
            "source_note_path": source_note_path,
            "source_note_sha256": note_sha,
            "source_note_utf8": source_note_utf8,
            "source_note_utf8_bytes": len(note_bytes),
            "body_start_char": body_start_char,
        }
        digest = _sha256(data)
        return cls(
            document=document,
            source_note_path=source_note_path,
            source_note_sha256=note_sha,
            source_note_utf8=source_note_utf8,
            source_note_utf8_bytes=len(note_bytes),
            body_start_char=body_start_char,
            snapshot_id=f"depsource:{digest}",
            snapshot_sha256=digest,
        )

    def validate_span(self, span: DocumentSpanReference) -> None:
        """Revalidate path, SHA, body boundary, offsets, and exact quote text."""

        if span.document_version_id != self.document.document_version_id:
            raise ValueError("dependency span names a different document")
        if (
            span.source_note_path != self.source_note_path
            or span.source_note_sha256 != self.source_note_sha256
        ):
            raise ValueError("dependency span binds a different SourceNote snapshot")
        if span.start_char < self.body_start_char:
            raise ValueError("dependency span must be SourceNote body evidence")
        if span.end_char > len(self.source_note_utf8):
            raise ValueError("dependency span ends beyond the exact SourceNote")
        if self.source_note_utf8[span.start_char : span.end_char] != span.quote:
            raise ValueError("dependency span quote does not equal the exact character slice")


class SourceNoteInventory(_StrictFrozenModel):
    """Serializable content envelope; authority requires a verified capability."""

    schema_version: Literal[1] = 1
    aggregate_id: str
    snapshot_revision: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    notes: tuple[CanonicalSourceNoteSnapshot, ...]
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.repository-resolved-source-note-inventory.v1",
            "schema_version": 1,
            "aggregate_id": self.aggregate_id,
            "snapshot_revision": self.snapshot_revision,
            "aggregate_sha256": self.aggregate_sha256,
            "notes": [item.model_dump(mode="json") for item in self.notes],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        ids = tuple(item.document.document_version_id for item in self.notes)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("source-note inventory must use unique canonical document order")
        if self.inventory_sha256 != _sha256(self._payload()):
            raise ValueError("source-note inventory SHA does not match its complete content")
        return self

    @classmethod
    def create(
        cls,
        *,
        snapshot: ChangeControlSnapshot,
        notes: tuple[CanonicalSourceNoteSnapshot, ...],
    ) -> Self:
        canonical = tuple(sorted(notes, key=lambda item: item.document.document_version_id))
        data = {
            "namespace": "mastervault.repository-resolved-source-note-inventory.v1",
            "schema_version": 1,
            "aggregate_id": snapshot.aggregate.aggregate_id,
            "snapshot_revision": snapshot.revision,
            "aggregate_sha256": snapshot.aggregate_sha256,
            "notes": [item.model_dump(mode="json") for item in canonical],
        }
        return cls(
            aggregate_id=snapshot.aggregate.aggregate_id,
            snapshot_revision=snapshot.revision,
            aggregate_sha256=snapshot.aggregate_sha256,
            notes=canonical,
            inventory_sha256=_sha256(data),
        )


class DependencySourceInventoryResolver(Protocol):
    """Outer service boundary; it may perform I/O before pure analysis begins."""

    def resolve_source_note_inventory(
        self, *, snapshot: ChangeControlSnapshot
    ) -> VerifiedSourceNoteInventoryCapability: ...


class VerifiedSourceNoteInventoryCapability(Protocol):
    """Sealed in-memory authority over already-resolved bytes and body bounds.

    ``verify`` must not perform filesystem or network I/O.  A future repository
    adapter creates this capability after reopening and validating source bytes.
    """

    def verify(self, *, snapshot: ChangeControlSnapshot) -> SourceNoteInventory: ...


class GoverningSupersessionRef(_StrictFrozenModel):
    pair_id: str = Field(pattern=_PAIR_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_id: str = Field(pattern=_PAIR_CLASSIFICATION_ID)
    classification_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_id: str = Field(pattern=r"^rel:[0-9a-f]{64}$")
    changed_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)
    upstream_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)


class SelectedNeighbourRef(_StrictFrozenModel):
    """Exact advisory pair result represented by a candidate document, if any."""

    pair_id: str = Field(pattern=_PAIR_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_id: str = Field(pattern=_PAIR_CLASSIFICATION_ID)
    classification_sha256: str = Field(pattern=SHA256_PATTERN)
    incumbent_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)


class DependencyCandidate(_StrictFrozenModel):
    """One old-claim/downstream-document question; status is unassessed."""

    schema_version: Literal[1] = 1
    status: Literal["unassessed"] = "unassessed"
    governing: GoverningSupersessionRef
    changed_claim_revision: VersionedClaimRevision
    upstream_claim_revision: VersionedClaimRevision
    downstream_document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    selected_neighbour_refs: tuple[SelectedNeighbourRef, ...]
    candidate_id: str = Field(pattern=_CANDIDATE_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-candidate.v1",
            "schema_version": 1,
            "status": self.status,
            "governing": self.governing.model_dump(mode="json"),
            "changed_claim_revision": self.changed_claim_revision.model_dump(mode="json"),
            "upstream_claim_revision": self.upstream_claim_revision.model_dump(mode="json"),
            "downstream_document_version_id": self.downstream_document_version_id,
            "selected_neighbour_refs": [
                item.model_dump(mode="json") for item in self.selected_neighbour_refs
            ],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.changed_claim_revision.claim_revision_id
            != self.governing.changed_claim_revision_id
            or self.upstream_claim_revision.claim_revision_id
            != self.governing.upstream_claim_revision_id
        ):
            raise ValueError("candidate governing revisions differ from supersession endpoints")
        neighbour_keys = tuple(
            (item.pair_id, item.incumbent_claim_revision_id)
            for item in self.selected_neighbour_refs
        )
        if neighbour_keys != tuple(sorted(set(neighbour_keys))):
            raise ValueError("selected-neighbour refs must be unique and canonical")
        digest = _sha256(self._payload())
        if self.candidate_sha256 != digest or self.candidate_id != f"depcand:{digest}":
            raise ValueError("dependency candidate ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        governing: GoverningSupersessionRef,
        changed_claim_revision: VersionedClaimRevision,
        upstream_claim_revision: VersionedClaimRevision,
        downstream_document_version_id: str,
        selected_neighbour_refs: tuple[SelectedNeighbourRef, ...],
    ) -> Self:
        refs = tuple(
            sorted(
                selected_neighbour_refs,
                key=lambda item: (item.pair_id, item.incumbent_claim_revision_id),
            )
        )
        payload = {
            "namespace": "mastervault.dependency-candidate.v1",
            "schema_version": 1,
            "status": "unassessed",
            "governing": governing.model_dump(mode="json"),
            "changed_claim_revision": changed_claim_revision.model_dump(mode="json"),
            "upstream_claim_revision": upstream_claim_revision.model_dump(mode="json"),
            "downstream_document_version_id": downstream_document_version_id,
            "selected_neighbour_refs": [item.model_dump(mode="json") for item in refs],
        }
        digest = _sha256(payload)
        return cls(
            governing=governing,
            changed_claim_revision=changed_claim_revision,
            upstream_claim_revision=upstream_claim_revision,
            downstream_document_version_id=downstream_document_version_id,
            selected_neighbour_refs=refs,
            candidate_id=f"depcand:{digest}",
            candidate_sha256=digest,
        )


class ExcludedDependencyDocument(_StrictFrozenModel):
    governing: GoverningSupersessionRef
    downstream_note: CanonicalSourceNoteSnapshot
    reasons: tuple[DependencyCandidateExclusionReason, ...] = Field(min_length=1)
    exclusion_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        expected = tuple(sorted(set(self.reasons), key=_EXCLUSION_ORDER.__getitem__))
        if self.reasons != expected:
            raise ValueError("dependency exclusion reasons must be canonical")
        payload = {
            "namespace": "mastervault.dependency-candidate-exclusion.v1",
            "governing": self.governing.model_dump(mode="json"),
            "downstream_note": self.downstream_note.model_dump(mode="json"),
            "reasons": [item.value for item in self.reasons],
        }
        if self.exclusion_sha256 != _sha256(payload):
            raise ValueError("dependency exclusion SHA does not match its content")
        return self


class DependencyInferenceShard(_StrictFrozenModel):
    """Exactly one complete downstream document, never a lossy chunk."""

    schema_version: Literal[1] = 1
    downstream_note: CanonicalSourceNoteSnapshot
    downstream_claim_revisions: tuple[VersionedClaimRevision, ...]
    temporal_resolution: TemporalResolution
    candidates: tuple[DependencyCandidate, ...] = Field(min_length=1, max_length=64)
    shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-inference-shard.v1",
            "schema_version": 1,
            "downstream_note": self.downstream_note.model_dump(mode="json"),
            "downstream_claim_revisions": [
                item.model_dump(mode="json") for item in self.downstream_claim_revisions
            ],
            "temporal_resolution": self.temporal_resolution.model_dump(mode="json"),
            "candidates": [item.model_dump(mode="json") for item in self.candidates],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        root_keys = tuple(
            (
                item.governing.changed_claim_revision_id,
                item.governing.upstream_claim_revision_id,
            )
            for item in self.candidates
        )
        if root_keys != tuple(sorted(set(root_keys))):
            raise ValueError("document shard candidates must use unique canonical root order")
        if any(
            item.downstream_document_version_id != self.downstream_note.document.document_version_id
            for item in self.candidates
        ):
            raise ValueError("document shard candidates must bind its exact downstream note")
        claim_ids = tuple(item.claim_revision_id for item in self.downstream_claim_revisions)
        if claim_ids != tuple(sorted(set(claim_ids))):
            raise ValueError("document shard claims must use unique canonical order")
        for claim in self.downstream_claim_revisions:
            if (
                claim.document != self.downstream_note.document
                or claim.source.source_note_path != self.downstream_note.source_note_path
                or claim.source.source_note_sha256 != self.downstream_note.source_note_sha256
            ):
                raise ValueError("document shard claims and exact note binding differ")
        if (
            self.temporal_resolution.target.target_id
            != self.downstream_note.document.document_version_id
        ):
            raise ValueError("document shard temporal resolution names another document")
        if not all(
            set(item.incumbent_claim_revision_id for item in candidate.selected_neighbour_refs)
            <= set(claim_ids)
            for candidate in self.candidates
        ):
            raise ValueError("selected neighbour is absent from document shard claims")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("complete dependency input document exceeds 256 KiB")
        digest = _sha256(payload)
        if self.shard_sha256 != digest or self.shard_id != f"depin:{digest}":
            raise ValueError("dependency input shard ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        downstream_note: CanonicalSourceNoteSnapshot,
        downstream_claim_revisions: tuple[VersionedClaimRevision, ...],
        temporal_resolution: TemporalResolution,
        candidates: tuple[DependencyCandidate, ...],
    ) -> Self:
        canonical = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.governing.changed_claim_revision_id,
                    item.governing.upstream_claim_revision_id,
                ),
            )
        )
        claims = tuple(sorted(downstream_claim_revisions, key=lambda item: item.claim_revision_id))
        payload = {
            "namespace": "mastervault.dependency-inference-shard.v1",
            "schema_version": 1,
            "downstream_note": downstream_note.model_dump(mode="json"),
            "downstream_claim_revisions": [item.model_dump(mode="json") for item in claims],
            "temporal_resolution": temporal_resolution.model_dump(mode="json"),
            "candidates": [item.model_dump(mode="json") for item in canonical],
        }
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1:
            raise DependencyAnalysisLimitError(
                category="complete-document-input-bytes",
                limit=MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1,
                observed=observed,
            )
        digest = _sha256(payload)
        return cls(
            downstream_note=downstream_note,
            downstream_claim_revisions=claims,
            temporal_resolution=temporal_resolution,
            candidates=canonical,
            shard_id=f"depin:{digest}",
            shard_sha256=digest,
        )


class DependencyCandidateRef(_StrictFrozenModel):
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    changed_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)
    upstream_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)
    candidate_id: str = Field(pattern=_CANDIDATE_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)


class DependencyExclusionRef(_StrictFrozenModel):
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    changed_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)
    upstream_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID)
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    reasons: tuple[DependencyCandidateExclusionReason, ...] = Field(min_length=1)
    exclusion_sha256: str = Field(pattern=SHA256_PATTERN)


class DependencyWorkloadIndex(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    aggregate_id: str
    snapshot_revision: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    source_candidate_set_sha256: str = Field(pattern=SHA256_PATTERN)
    source_classification_result_id: str = Field(pattern=r"^classresult:[0-9a-f]{64}$")
    source_classification_result_sha256: str = Field(pattern=SHA256_PATTERN)
    governing_supersessions: tuple[GoverningSupersessionRef, ...] = Field(min_length=1)
    candidate_refs: tuple[DependencyCandidateRef, ...]
    exclusion_refs: tuple[DependencyExclusionRef, ...]
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-workload-index.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"workload_id", "workload_sha256", "schema_version"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        governing_ids = tuple(
            (item.changed_claim_revision_id, item.upstream_claim_revision_id)
            for item in self.governing_supersessions
        )
        if governing_ids != tuple(sorted(set(governing_ids))):
            raise ValueError("governing supersessions must use unique canonical upstream order")
        candidate_keys = tuple(
            (
                item.document_version_id,
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            )
            for item in self.candidate_refs
        )
        excluded_keys = tuple(
            (
                item.document_version_id,
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            )
            for item in self.exclusion_refs
        )
        if candidate_keys != tuple(sorted(set(candidate_keys))):
            raise ValueError("candidate ledger must use unique canonical cross-product order")
        if excluded_keys != tuple(sorted(set(excluded_keys))):
            raise ValueError("exclusion ledger must use unique canonical cross-product order")
        if set(candidate_keys) & set(excluded_keys):
            raise ValueError("candidate and exclusion ledgers must be disjoint")
        payload = self._payload()
        if len(canonical_json_bytes(payload)) > MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1:
            raise ValueError("dependency workload index exceeds 256 KiB")
        digest = _sha256(payload)
        if self.workload_sha256 != digest or self.workload_id != f"depwork:{digest}":
            raise ValueError("dependency workload ID/SHA does not match its complete ledger")
        return self


class DependencyWorkload(_StrictFrozenModel):
    """In-memory envelope; the index and per-document shards are artifacts."""

    index: DependencyWorkloadIndex
    input_shards: tuple[DependencyInferenceShard, ...]
    exclusions: tuple[ExcludedDependencyDocument, ...]

    @property
    def candidates(self) -> tuple[DependencyCandidate, ...]:
        return tuple(candidate for item in self.input_shards for candidate in item.candidates)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.input_shards) > MAX_DEPENDENCY_DOCUMENT_SHARDS_V1:
            raise ValueError("dependency workload exceeds the input-artifact count limit")
        if sum(len(item.canonical_bytes()) for item in self.input_shards) > (
            MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1
        ):
            raise ValueError("dependency workload exceeds the aggregate input-byte limit")
        shard_docs = tuple(
            item.downstream_note.document.document_version_id for item in self.input_shards
        )
        if shard_docs != tuple(sorted(set(shard_docs))):
            raise ValueError("input shards must use one canonical shard per document")
        refs = tuple(
            sorted(
                (
                    DependencyCandidateRef(
                        document_version_id=shard.downstream_note.document.document_version_id,
                        changed_claim_revision_id=candidate.governing.changed_claim_revision_id,
                        upstream_claim_revision_id=candidate.governing.upstream_claim_revision_id,
                        candidate_id=candidate.candidate_id,
                        candidate_sha256=candidate.candidate_sha256,
                        input_shard_id=shard.shard_id,
                        input_shard_sha256=shard.shard_sha256,
                    )
                    for shard in self.input_shards
                    for candidate in shard.candidates
                ),
                key=lambda item: (
                    item.document_version_id,
                    item.changed_claim_revision_id,
                    item.upstream_claim_revision_id,
                ),
            )
        )
        if self.index.candidate_refs != refs:
            raise ValueError("workload index contains a substituted input shard")
        excluded = tuple(
            DependencyExclusionRef(
                document_version_id=item.downstream_note.document.document_version_id,
                changed_claim_revision_id=item.governing.changed_claim_revision_id,
                upstream_claim_revision_id=item.governing.upstream_claim_revision_id,
                snapshot_sha256=item.downstream_note.snapshot_sha256,
                reasons=item.reasons,
                exclusion_sha256=item.exclusion_sha256,
            )
            for item in self.exclusions
        )
        if self.index.exclusion_refs != excluded:
            raise ValueError("workload index contains a substituted exclusion")
        return self


def _governing_ref(
    classification: ClaimPairClassification,
) -> GoverningSupersessionRef | None:
    assessment = classification.relation_assessment
    if (
        classification.disposition != PairDisposition.SUPERSEDES
        or classification.materialization_status != GraphMaterializationStatus.GRAPH_VALID
        or assessment is None
        or assessment.relation_type != PersistedRelationType.SUPERSEDES
        or assessment.relation_id is None
        or assessment.endpoint_ids is None
    ):
        return None
    result = GoverningSupersessionRef(
        pair_id=classification.candidate.pair_id,
        candidate_sha256=classification.candidate_sha256,
        classification_id=classification.classification_id,
        classification_sha256=classification.classification_sha256,
        relation_id=assessment.relation_id,
        changed_claim_revision_id=assessment.endpoint_ids[0],
        upstream_claim_revision_id=assessment.endpoint_ids[1],
    )
    if result.changed_claim_revision_id != classification.candidate.changed_claim_revision_id:
        return None
    return result


def derive_governing_supersessions(
    classification_results: ClassificationResultSet,
) -> tuple[GoverningSupersessionRef, ...]:
    """Derive the complete canonical changed-to-older governing edge set."""

    results = ClassificationResultSet.model_validate(classification_results.model_dump(mode="json"))
    governing_refs = tuple(
        sorted(
            (
                governing
                for item in results.classifications
                for governing in (_governing_ref(item),)
                if governing is not None
            ),
            key=lambda item: (
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            ),
        )
    )
    governing_keys = tuple(
        (item.changed_claim_revision_id, item.upstream_claim_revision_id) for item in governing_refs
    )
    if governing_keys != tuple(sorted(set(governing_keys))):
        raise ValueError("classification result has duplicate governing supersessions")
    return governing_refs


def _validate_inventory(
    snapshot: ChangeControlSnapshot,
    capability: VerifiedSourceNoteInventoryCapability,
) -> SourceNoteInventory:
    inventory = SourceNoteInventory.model_validate(
        capability.verify(snapshot=snapshot).model_dump(mode="json")
    )
    if (
        inventory.aggregate_id != snapshot.aggregate.aggregate_id
        or inventory.snapshot_revision != snapshot.revision
        or inventory.aggregate_sha256 != snapshot.aggregate_sha256
    ):
        raise ValueError("repository inventory binds a stale or different snapshot")
    documents = {item.document_version_id: item for item in snapshot.aggregate.documents.documents}
    notes = {item.document.document_version_id: item for item in inventory.notes}
    if set(notes) != set(documents):
        raise ValueError("repository inventory must exactly cover every aggregate document")
    claims_by_document: dict[str, list[VersionedClaimRevision]] = {}
    for claim in snapshot.aggregate.claims.revisions:
        claims_by_document.setdefault(claim.document.document_version_id, []).append(claim)
    for document_id, document in documents.items():
        note = notes[document_id]
        if note.document != document:
            raise ValueError("inventory document differs from the authoritative aggregate")
        for claim in claims_by_document.get(document_id, []):
            if (
                claim.source.source_note_path != note.source_note_path
                or claim.source.source_note_sha256 != note.source_note_sha256
            ):
                raise ValueError("inventory note differs from authoritative claim source binding")
    return inventory


def generate_dependency_workload(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
) -> DependencyWorkload:
    """Cross every graph-valid older root with every resolved document."""

    results = validate_classification_results(
        snapshot, candidates=candidates, results=classification_results
    )
    revisions = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    governing_refs = derive_governing_supersessions(results)
    if not governing_refs:
        raise ValueError("classification result has no graph-valid changed-to-older supersession")
    governing_supersessions = governing_refs
    changed_document_ids = {
        revisions[item.changed_claim_revision_id].document.document_version_id
        for item in governing_supersessions
    }

    inventory = _validate_inventory(snapshot, inventory_capability)
    notes = {item.document.document_version_id: item for item in inventory.notes}
    claims_by_document: dict[str, list[VersionedClaimRevision]] = {}
    for claim in snapshot.aggregate.claims.revisions:
        claims_by_document.setdefault(claim.document.document_version_id, []).append(claim)
    neighbours_by_root_document: dict[tuple[str, str], list[SelectedNeighbourRef]] = {}
    for item in results.classifications:
        incumbent = revisions[item.candidate.incumbent_claim_revision_id]
        neighbours_by_root_document.setdefault(
            (
                item.candidate.changed_claim_revision_id,
                incumbent.document.document_version_id,
            ),
            [],
        ).append(
            SelectedNeighbourRef(
                pair_id=item.candidate.pair_id,
                candidate_sha256=item.candidate_sha256,
                classification_id=item.classification_id,
                classification_sha256=item.classification_sha256,
                incumbent_claim_revision_id=incumbent.claim_revision_id,
            )
        )

    candidate_pairs = tuple(
        (document_id, governing)
        for document_id in sorted(notes)
        for governing in governing_supersessions
        if document_id not in changed_document_ids
        and document_id
        != revisions[governing.upstream_claim_revision_id].document.document_version_id
    )
    candidate_count = len(candidate_pairs)
    if candidate_count > MAX_DEPENDENCY_CANDIDATES_V1:
        raise DependencyAnalysisLimitError(
            category="dependency-candidate-cross-product",
            limit=MAX_DEPENDENCY_CANDIDATES_V1,
            observed=candidate_count,
        )
    temporal = TemporalResolutionContext.from_aggregate(
        snapshot.aggregate, as_of=candidates.binding.as_of
    )
    shards: list[DependencyInferenceShard] = []
    exclusions: list[ExcludedDependencyDocument] = []
    for document_id in sorted(notes):
        note = notes[document_id]
        document_candidates: list[DependencyCandidate] = []
        for governing in governing_supersessions:
            changed = revisions[governing.changed_claim_revision_id]
            upstream = revisions[governing.upstream_claim_revision_id]
            reasons: list[DependencyCandidateExclusionReason] = []
            if document_id in changed_document_ids:
                reasons.append(DependencyCandidateExclusionReason.CHANGED_DOCUMENT)
            if document_id == upstream.document.document_version_id:
                reasons.append(DependencyCandidateExclusionReason.GOVERNING_UPSTREAM_DOCUMENT)
            if reasons:
                canonical_reasons = tuple(sorted(set(reasons), key=_EXCLUSION_ORDER.__getitem__))
                payload = {
                    "namespace": "mastervault.dependency-candidate-exclusion.v1",
                    "governing": governing.model_dump(mode="json"),
                    "downstream_note": note.model_dump(mode="json"),
                    "reasons": [item.value for item in canonical_reasons],
                }
                exclusions.append(
                    ExcludedDependencyDocument(
                        governing=governing,
                        downstream_note=note,
                        reasons=canonical_reasons,
                        exclusion_sha256=_sha256(payload),
                    )
                )
                continue
            document_candidates.append(
                DependencyCandidate.create(
                    governing=governing,
                    changed_claim_revision=changed,
                    upstream_claim_revision=upstream,
                    downstream_document_version_id=note.document.document_version_id,
                    selected_neighbour_refs=tuple(
                        neighbours_by_root_document.get(
                            (governing.changed_claim_revision_id, document_id), []
                        )
                    ),
                )
            )
        if document_candidates:
            shards.append(
                DependencyInferenceShard.create(
                    downstream_note=note,
                    downstream_claim_revisions=tuple(claims_by_document.get(document_id, [])),
                    temporal_resolution=temporal.resolve_document(note.document),
                    candidates=tuple(document_candidates),
                )
            )
    canonical_shards = tuple(
        sorted(
            shards,
            key=lambda item: item.downstream_note.document.document_version_id,
        )
    )
    if len(canonical_shards) > MAX_DEPENDENCY_DOCUMENT_SHARDS_V1:
        raise DependencyAnalysisLimitError(
            category="document-input-shards",
            limit=MAX_DEPENDENCY_DOCUMENT_SHARDS_V1,
            observed=len(canonical_shards),
        )
    total_input_bytes = sum(len(item.canonical_bytes()) for item in canonical_shards)
    if total_input_bytes > MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1:
        raise DependencyAnalysisLimitError(
            category="total-input-bytes",
            limit=MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1,
            observed=total_input_bytes,
        )
    canonical_exclusions = tuple(
        sorted(
            exclusions,
            key=lambda item: (
                item.downstream_note.document.document_version_id,
                item.governing.changed_claim_revision_id,
                item.governing.upstream_claim_revision_id,
            ),
        )
    )
    candidate_refs = tuple(
        sorted(
            (
                DependencyCandidateRef(
                    document_version_id=shard.downstream_note.document.document_version_id,
                    changed_claim_revision_id=candidate.governing.changed_claim_revision_id,
                    upstream_claim_revision_id=candidate.governing.upstream_claim_revision_id,
                    candidate_id=candidate.candidate_id,
                    candidate_sha256=candidate.candidate_sha256,
                    input_shard_id=shard.shard_id,
                    input_shard_sha256=shard.shard_sha256,
                )
                for shard in canonical_shards
                for candidate in shard.candidates
            ),
            key=lambda item: (
                item.document_version_id,
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            ),
        )
    )
    exclusion_refs = tuple(
        DependencyExclusionRef(
            document_version_id=item.downstream_note.document.document_version_id,
            changed_claim_revision_id=item.governing.changed_claim_revision_id,
            upstream_claim_revision_id=item.governing.upstream_claim_revision_id,
            snapshot_sha256=item.downstream_note.snapshot_sha256,
            reasons=item.reasons,
            exclusion_sha256=item.exclusion_sha256,
        )
        for item in canonical_exclusions
    )
    index_payload: dict[str, Any] = {
        "namespace": "mastervault.dependency-workload-index.v1",
        "schema_version": 1,
        "aggregate_id": snapshot.aggregate.aggregate_id,
        "snapshot_revision": snapshot.revision,
        "aggregate_sha256": snapshot.aggregate_sha256,
        "inventory_sha256": inventory.inventory_sha256,
        "source_candidate_set_sha256": candidates.result_sha256,
        "source_classification_result_id": results.result_set_id,
        "source_classification_result_sha256": results.result_sha256,
        "governing_supersessions": [
            item.model_dump(mode="json") for item in governing_supersessions
        ],
        "candidate_refs": [item.model_dump(mode="json") for item in candidate_refs],
        "exclusion_refs": [item.model_dump(mode="json") for item in exclusion_refs],
    }
    observed = len(canonical_json_bytes(index_payload))
    if observed > MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1:
        raise DependencyAnalysisLimitError(
            category="workload-index-bytes",
            limit=MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1,
            observed=observed,
        )
    digest = _sha256(index_payload)
    return DependencyWorkload(
        index=DependencyWorkloadIndex(
            aggregate_id=snapshot.aggregate.aggregate_id,
            snapshot_revision=snapshot.revision,
            aggregate_sha256=snapshot.aggregate_sha256,
            inventory_sha256=inventory.inventory_sha256,
            source_candidate_set_sha256=candidates.result_sha256,
            source_classification_result_id=results.result_set_id,
            source_classification_result_sha256=results.result_sha256,
            governing_supersessions=governing_supersessions,
            candidate_refs=candidate_refs,
            exclusion_refs=exclusion_refs,
            workload_id=f"depwork:{digest}",
            workload_sha256=digest,
        ),
        input_shards=canonical_shards,
        exclusions=canonical_exclusions,
    )


def validate_dependency_workload(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    workload: DependencyWorkload,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
) -> DependencyWorkload:
    validated = DependencyWorkload.model_validate(workload.model_dump(mode="json"))
    expected = generate_dependency_workload(
        snapshot,
        candidates=candidates,
        classification_results=classification_results,
        inventory_capability=inventory_capability,
    )
    if validated != expected:
        raise ValueError("dependency workload differs from its exhaustive derivation")
    return validated


class DependencyClassification(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=_CANDIDATE_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    disposition: DependencyDisposition
    dependency_kind: DependencyKind | None = None
    selected_downstream_claim_revision_ids: tuple[str, ...] = ()
    downstream_spans: tuple[DocumentSpanReference, ...] = ()
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    classification_id: str = Field(pattern=_CLASSIFICATION_ID)
    classification_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-classification.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "classification_id", "classification_sha256"},
            ),
        }

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_rationale(value)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        claim_ids = self.selected_downstream_claim_revision_ids
        if claim_ids != tuple(sorted(set(claim_ids))):
            raise ValueError("selected downstream claims must be unique and canonical")
        span_keys = tuple(
            canonical_json_bytes(item.model_dump(mode="json")) for item in self.downstream_spans
        )
        if len(span_keys) > MAX_SPANS_PER_DEPENDENCY_V1:
            raise ValueError("dependency classification exceeds the span-count limit")
        if any(len(item) > MAX_SPAN_CANONICAL_BYTES_V1 for item in span_keys):
            raise ValueError("dependency classification span exceeds the byte limit")
        if span_keys != tuple(sorted(set(span_keys))):
            raise ValueError("dependency spans must be unique and canonical")
        if self.disposition == DependencyDisposition.DEPENDS_ON:
            if self.dependency_kind is None or not self.downstream_spans:
                raise ValueError("DEPENDS_ON requires a dependency kind and exact evidence")
        elif self.dependency_kind is not None or claim_ids or self.downstream_spans:
            raise ValueError("NOT_DEPENDENT must not carry graph-edge fields")
        digest = _sha256(self._payload())
        if self.classification_sha256 != digest or self.classification_id != f"depclass:{digest}":
            raise ValueError("dependency classification ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        input_shard: DependencyInferenceShard,
        candidate: DependencyCandidate,
        disposition: DependencyDisposition,
        rationale: str,
        confidence: float,
        dependency_kind: DependencyKind | None = None,
        selected_downstream_claim_revision_ids: tuple[str, ...] = (),
        downstream_spans: tuple[DocumentSpanReference, ...] = (),
    ) -> Self:
        claim_ids = tuple(sorted(selected_downstream_claim_revision_ids))
        spans = tuple(
            sorted(
                downstream_spans,
                key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
            )
        )
        if len(spans) > MAX_SPANS_PER_DEPENDENCY_V1:
            raise DependencyAnalysisLimitError(
                category="spans-per-dependency",
                limit=MAX_SPANS_PER_DEPENDENCY_V1,
                observed=len(spans),
            )
        for span in spans:
            span_bytes = len(canonical_json_bytes(span.model_dump(mode="json")))
            if span_bytes > MAX_SPAN_CANONICAL_BYTES_V1:
                raise DependencyAnalysisLimitError(
                    category="span-canonical-bytes",
                    limit=MAX_SPAN_CANONICAL_BYTES_V1,
                    observed=span_bytes,
                )
        if candidate not in input_shard.candidates:
            raise ValueError("classification candidate is absent from the exact input shard")
        allowed = {item.claim_revision_id for item in input_shard.downstream_claim_revisions}
        if not set(claim_ids) <= allowed:
            raise ValueError("selected downstream claim is absent from the candidate")
        for span in spans:
            input_shard.downstream_note.validate_span(span)
        canonical_rationale = _canonical_rationale(rationale)
        payload = {
            "namespace": "mastervault.dependency-classification.v1",
            "schema_version": 1,
            "candidate_id": candidate.candidate_id,
            "candidate_sha256": candidate.candidate_sha256,
            "disposition": disposition.value,
            "dependency_kind": dependency_kind.value if dependency_kind else None,
            "selected_downstream_claim_revision_ids": list(claim_ids),
            "downstream_spans": [item.model_dump(mode="json") for item in spans],
            "rationale": canonical_rationale,
            "confidence": confidence,
        }
        digest = _sha256(payload)
        return cls(
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate.candidate_sha256,
            disposition=disposition,
            dependency_kind=dependency_kind,
            selected_downstream_claim_revision_ids=claim_ids,
            downstream_spans=spans,
            rationale=canonical_rationale,
            confidence=confidence,
            classification_id=f"depclass:{digest}",
            classification_sha256=digest,
        )


class DependencyOutputShard(_StrictFrozenModel):
    """All root results for one complete-document input shard."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    classifications: tuple[DependencyClassification, ...] = Field(min_length=1, max_length=64)
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-output-shard.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "output_shard_id", "output_shard_sha256"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        candidate_ids = tuple(item.candidate_id for item in self.classifications)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError("document output classifications must use canonical candidate order")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("complete dependency output document exceeds 256 KiB")
        digest = _sha256(payload)
        if self.output_shard_sha256 != digest or self.output_shard_id != f"depout:{digest}":
            raise ValueError("dependency output shard ID/SHA does not match its content")
        return self


class DependencyOutputShardRef(_StrictFrozenModel):
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_canonical_bytes: int = Field(
        gt=0, le=MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1
    )


class DependencyResultIndex(_StrictFrozenModel):
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shards: tuple[DependencyOutputShardRef, ...]
    result_id: str = Field(pattern=_RESULT_ID)
    result_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.dependency-result-index.v1",
            "schema_version": 1,
            "workload_id": self.workload_id,
            "workload_sha256": self.workload_sha256,
            "output_shards": [item.model_dump(mode="json") for item in self.output_shards],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        ids = tuple(item.input_shard_id for item in self.output_shards)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("dependency result refs must use unique canonical input order")
        payload = self._payload()
        if len(canonical_json_bytes(payload)) > MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1:
            raise ValueError("dependency result index exceeds 256 KiB")
        digest = _sha256(payload)
        if self.result_sha256 != digest or self.result_id != f"depresult:{digest}":
            raise ValueError("dependency result index ID/SHA does not match its content")
        return self


class DependencyClassificationResultSet(_StrictFrozenModel):
    result_index: DependencyResultIndex
    output_shards: tuple[DependencyOutputShard, ...]

    @property
    def classifications(self) -> tuple[DependencyClassification, ...]:
        return tuple(result for item in self.output_shards for result in item.classifications)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if any(
            item.workload_id != self.result_index.workload_id
            or item.workload_sha256 != self.result_index.workload_sha256
            for item in self.output_shards
        ):
            raise ValueError("dependency output shard binds a different result workload")
        if tuple(item.input_shard_id for item in self.output_shards) != tuple(
            sorted(set(item.input_shard_id for item in self.output_shards))
        ):
            raise ValueError("dependency output shards must use unique canonical input order")
        refs = tuple(
            DependencyOutputShardRef(
                input_shard_id=item.input_shard_id,
                input_shard_sha256=item.input_shard_sha256,
                output_shard_id=item.output_shard_id,
                output_shard_sha256=item.output_shard_sha256,
                output_shard_canonical_bytes=len(item.canonical_bytes()),
            )
            for item in self.output_shards
        )
        if self.result_index.output_shards != refs:
            raise ValueError("dependency result index contains a substituted shard")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: DependencyWorkload,
        classifications: tuple[DependencyClassification, ...],
    ) -> Self:
        by_candidate = {item.candidate_id: item for item in classifications}
        if len(by_candidate) != len(classifications) or set(by_candidate) != {
            candidate.candidate_id
            for item in workload.input_shards
            for candidate in item.candidates
        }:
            raise ValueError("dependency results must classify every candidate exactly once")
        outputs: list[DependencyOutputShard] = []
        for input_shard in workload.input_shards:
            shard_classifications = tuple(
                sorted(
                    (by_candidate[candidate.candidate_id] for candidate in input_shard.candidates),
                    key=lambda item: item.candidate_id,
                )
            )
            expected_candidates = {item.candidate_id: item for item in input_shard.candidates}
            if any(
                item.candidate_sha256 != expected_candidates[item.candidate_id].candidate_sha256
                for item in shard_classifications
            ):
                raise ValueError("dependency result binds a substituted candidate")
            payload = {
                "namespace": "mastervault.dependency-output-shard.v1",
                "schema_version": 1,
                "workload_id": workload.index.workload_id,
                "workload_sha256": workload.index.workload_sha256,
                "input_shard_id": input_shard.shard_id,
                "input_shard_sha256": input_shard.shard_sha256,
                "classifications": [item.model_dump(mode="json") for item in shard_classifications],
            }
            observed = len(canonical_json_bytes(payload))
            if observed > MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1:
                raise DependencyAnalysisLimitError(
                    category="complete-document-output-bytes",
                    limit=MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1,
                    observed=observed,
                )
            digest = _sha256(payload)
            outputs.append(
                DependencyOutputShard(
                    workload_id=workload.index.workload_id,
                    workload_sha256=workload.index.workload_sha256,
                    input_shard_id=input_shard.shard_id,
                    input_shard_sha256=input_shard.shard_sha256,
                    classifications=shard_classifications,
                    output_shard_id=f"depout:{digest}",
                    output_shard_sha256=digest,
                )
            )
        canonical = tuple(sorted(outputs, key=lambda item: item.input_shard_id))
        refs = tuple(
            DependencyOutputShardRef(
                input_shard_id=item.input_shard_id,
                input_shard_sha256=item.input_shard_sha256,
                output_shard_id=item.output_shard_id,
                output_shard_sha256=item.output_shard_sha256,
                output_shard_canonical_bytes=len(item.canonical_bytes()),
            )
            for item in canonical
        )
        payload = {
            "namespace": "mastervault.dependency-result-index.v1",
            "schema_version": 1,
            "workload_id": workload.index.workload_id,
            "workload_sha256": workload.index.workload_sha256,
            "output_shards": [item.model_dump(mode="json") for item in refs],
        }
        digest = _sha256(payload)
        return cls(
            result_index=DependencyResultIndex(
                workload_id=workload.index.workload_id,
                workload_sha256=workload.index.workload_sha256,
                output_shards=refs,
                result_id=f"depresult:{digest}",
                result_sha256=digest,
            ),
            output_shards=canonical,
        )


def validate_dependency_results(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    workload: DependencyWorkload,
    results: DependencyClassificationResultSet,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
) -> DependencyClassificationResultSet:
    authoritative = validate_dependency_workload(
        snapshot,
        candidates=candidates,
        classification_results=classification_results,
        workload=workload,
        inventory_capability=inventory_capability,
    )
    validated = DependencyClassificationResultSet.model_validate(results.model_dump(mode="json"))
    if (
        validated.result_index.workload_id != authoritative.index.workload_id
        or validated.result_index.workload_sha256 != authoritative.index.workload_sha256
    ):
        raise ValueError("dependency results bind a different workload")
    input_by_id = {item.shard_id: item for item in authoritative.input_shards}
    if tuple(item.input_shard_id for item in validated.output_shards) != tuple(sorted(input_by_id)):
        raise ValueError("dependency result coverage differs from complete candidate inventory")
    for output in validated.output_shards:
        if (
            output.workload_id != authoritative.index.workload_id
            or output.workload_sha256 != authoritative.index.workload_sha256
        ):
            raise ValueError("dependency output shard binds a foreign authoritative workload")
        source = input_by_id[output.input_shard_id]
        if output.input_shard_sha256 != source.shard_sha256:
            raise ValueError("dependency output substitutes its input shard")
        source_candidates = {item.candidate_id: item for item in source.candidates}
        if tuple(item.candidate_id for item in output.classifications) != tuple(
            sorted(source_candidates)
        ):
            raise ValueError("dependency output does not exactly cover its document roots")
        for result in output.classifications:
            candidate = source_candidates[result.candidate_id]
            if result.candidate_sha256 != candidate.candidate_sha256:
                raise ValueError("dependency output substitutes its candidate")
            allowed = {item.claim_revision_id for item in source.downstream_claim_revisions}
            if not set(result.selected_downstream_claim_revision_ids) <= allowed:
                raise ValueError("dependency output names an unknown downstream claim")
            for span in result.downstream_spans:
                source.downstream_note.validate_span(span)
    return validated


def materialize_dependencies(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    workload: DependencyWorkload,
    results: DependencyClassificationResultSet,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
) -> tuple[DependencyAssessment, ...]:
    """Materialize exact positive edges; historical-reference remains an edge."""

    validated = validate_dependency_results(
        snapshot,
        candidates=candidates,
        classification_results=classification_results,
        workload=workload,
        results=results,
        inventory_capability=inventory_capability,
    )
    candidates_by_id = {item.candidate_id: item for item in workload.candidates}
    shard_by_candidate = {
        candidate.candidate_id: shard
        for shard in workload.input_shards
        for candidate in shard.candidates
    }
    revisions = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    materialized: list[DependencyAssessment] = []
    for result in validated.classifications:
        if result.disposition == DependencyDisposition.NOT_DEPENDENT:
            continue
        candidate = candidates_by_id[result.candidate_id]
        shard = shard_by_candidate[result.candidate_id]
        downstream = {item.claim_revision_id: item for item in shard.downstream_claim_revisions}
        assert result.dependency_kind is not None
        materialized.append(
            DependencyAssessment.create(
                downstream=shard.downstream_note.document,
                upstream=revisions[candidate.governing.upstream_claim_revision_id],
                dependency_kind=result.dependency_kind,
                downstream_spans=result.downstream_spans,
                downstream_claim_revisions=tuple(
                    downstream[item] for item in result.selected_downstream_claim_revision_ids
                ),
                rationale=result.rationale,
                confidence=result.confidence,
            )
        )
    return tuple(sorted(materialized, key=lambda item: item.dependency_id))


__all__ = [
    "MAX_DEPENDENCY_CANDIDATES_V1",
    "MAX_DEPENDENCY_DOCUMENT_SHARDS_V1",
    "MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_RATIONALE_UTF8_BYTES_V1",
    "MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1",
    "CanonicalSourceNoteSnapshot",
    "DependencyAnalysisLimitError",
    "DependencyCandidate",
    "DependencyCandidateExclusionReason",
    "DependencyClassification",
    "DependencyClassificationResultSet",
    "DependencyDisposition",
    "DependencyExclusionRef",
    "DependencyInferenceShard",
    "DependencyOutputShard",
    "DependencyOutputShardRef",
    "DependencyResultIndex",
    "DependencySourceInventoryResolver",
    "DependencyWorkload",
    "DependencyWorkloadIndex",
    "ExcludedDependencyDocument",
    "GoverningSupersessionRef",
    "SourceNoteInventory",
    "SelectedNeighbourRef",
    "VerifiedSourceNoteInventoryCapability",
    "derive_governing_supersessions",
    "generate_dependency_workload",
    "materialize_dependencies",
    "validate_dependency_results",
    "validate_dependency_workload",
]
