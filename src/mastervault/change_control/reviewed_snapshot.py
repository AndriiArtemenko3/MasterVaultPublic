"""Reviewed revision-4 SourceNote continuity authority.

This seam reopens the durable revision-2 temporal-analysis evidence and the
already-decided authoritative revision-3 review, then proves that the live
revision-4 aggregate preserves the exact document, claim, and SourceNote roots.
It does not open reviews, classify impact, or grant mutation authority.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any, Literal, SupportsIndex

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    TemporalDecisionPrerequisite,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    ChangeControlAggregate,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.recorded_inference import RecordedInferenceOutcome
from mastervault.change_control.review import (
    HumanReviewDecision,
    HumanReviewDecisionReceipt,
    HumanReviewRequest,
    HumanReviewRequestReceipt,
    HumanReviewRequestView,
    ReviewLifecycle,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
    RepositoryVerifiedSourceNoteInventoryCapability,
)
from mastervault.change_control.store import (
    ChangeControlCommit,
    ChangeControlSnapshot,
    SqliteChangeControlStore,
)
from mastervault.change_control.temporal_analysis import (
    TemporalAnalysisEvidence,
    verify_temporal_analysis_evidence,
)
from mastervault.change_control.temporal_proposal import (
    TemporalProposal,
    TemporalProposalCommit,
    temporal_prerequisite_from_decision,
)

_BINDING_ID_PATTERN = r"^reviewed-snapshot:[0-9a-f]{64}$"
_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)
_AUTHORITY_TOKEN = object()
_AUTHORITY_SECRET = os.urandom(32)


class ReviewedTemporalSnapshotAuthorityError(ValueError):
    """Durable evidence, review authority, or SourceNote continuity failed closed."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewedTemporalSnapshotBinding(_StrictFrozenModel):
    """Serializable audit binding for one exact revision-2 to revision-4 proof."""

    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=_BINDING_ID_PATTERN)
    binding_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_repository_id: str = Field(pattern=SHA256_PATTERN)
    temporal_analysis_manifest_id: str = Field(pattern=r"^temporal-analysis:[0-9a-f]{64}$")
    temporal_analysis_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_commit_operation_id: str
    temporal_proposal_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_head: AggregateHeadBinding
    committed_head: AggregateHeadBinding
    review_request_id: str = Field(pattern=r"^reviewreq:[0-9a-f]{64}$")
    review_request_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    review_decision_operation_id: str
    review_decision_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_head: AggregateHeadBinding
    analysis_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_inventory_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> ReviewedTemporalSnapshotBinding:
        digest = hashlib.sha256(canonical_json_bytes(self._payload())).hexdigest()
        if self.binding_sha256 != digest or self.binding_id != f"reviewed-snapshot:{digest}":
            raise ValueError("reviewed-snapshot binding ID/SHA differs from its exact content")
        if (
            len(
                {
                    self.analysis_head.aggregate_id,
                    self.committed_head.aggregate_id,
                    self.reviewed_head.aggregate_id,
                }
            )
            != 1
        ):
            raise ValueError("reviewed-snapshot heads do not bind one aggregate")
        if (
            self.analysis_head.revision != 2
            or self.committed_head.revision != 3
            or self.reviewed_head.revision != 4
        ):
            raise ValueError("reviewed-snapshot heads must be the exact revisions 2, 3, and 4")
        if self.temporal_analysis_manifest_id != (
            f"temporal-analysis:{self.temporal_analysis_manifest_sha256}"
        ):
            raise ValueError("reviewed-snapshot manifest ID does not match its exact SHA")
        if self.temporal_commit_operation_id != (
            f"temporal-commit:{self.temporal_analysis_manifest_sha256}"
        ):
            raise ValueError("reviewed-snapshot commit operation does not bind its manifest")
        return self

    @classmethod
    def create(
        cls,
        *,
        evidence_repository_id: str,
        temporal_analysis: TemporalAnalysisEvidence,
        commit: TemporalProposalCommit,
        request: HumanReviewRequest,
        decision: HumanReviewDecision,
        prerequisite: TemporalDecisionPrerequisite,
        analysis_inventory: SourceNoteInventory,
        reviewed_inventory: SourceNoteInventory,
    ) -> ReviewedTemporalSnapshotBinding:
        values: dict[str, Any] = {
            "schema_version": 1,
            "evidence_repository_id": evidence_repository_id,
            "temporal_analysis_manifest_id": temporal_analysis.manifest_id,
            "temporal_analysis_manifest_sha256": temporal_analysis.manifest_sha256,
            "temporal_commit_operation_id": commit.operation_id,
            "temporal_proposal_binding_sha256": commit.proposal.binding.binding_sha256,
            "analysis_head": temporal_analysis.analysis_head.model_dump(mode="json"),
            "committed_head": AggregateHeadBinding.create(
                aggregate_id=commit.aggregate_id,
                revision=commit.revision,
                aggregate_sha256=commit.aggregate_sha256,
            ).model_dump(mode="json"),
            "review_request_id": request.request_id,
            "review_request_payload_sha256": request.request_payload_sha256,
            "review_decision_operation_id": decision.operation_id,
            "review_decision_payload_sha256": decision.decision_payload_sha256,
            "temporal_decision_record_sha256": (prerequisite.temporal_decision_record_sha256),
            "reviewed_head": prerequisite.review_open_head.model_dump(mode="json"),
            "analysis_inventory_sha256": analysis_inventory.inventory_sha256,
            "reviewed_inventory_sha256": reviewed_inventory.inventory_sha256,
        }
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "binding_id": f"reviewed-snapshot:{digest}",
                    "binding_sha256": digest,
                    **values,
                }
            )
        )


def _exact_snapshot(snapshot: ChangeControlSnapshot, *, revision: int) -> ChangeControlSnapshot:
    if type(snapshot) is not ChangeControlSnapshot:
        raise ReviewedTemporalSnapshotAuthorityError("aggregate snapshot type was substituted")
    if type(snapshot.aggregate) is not ChangeControlAggregate:
        raise ReviewedTemporalSnapshotAuthorityError("aggregate domain type was substituted")
    try:
        canonical_aggregate = ChangeControlAggregate.model_validate_json(
            canonical_json_bytes(snapshot.aggregate.model_dump(mode="json"))
        )
        digest = aggregate_sha256(canonical_aggregate)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReviewedTemporalSnapshotAuthorityError(
            "aggregate snapshot content is not canonical"
        ) from exc
    if (
        canonical_aggregate != snapshot.aggregate
        or snapshot.revision != revision
        or digest != snapshot.aggregate_sha256
    ):
        raise ReviewedTemporalSnapshotAuthorityError(
            f"aggregate snapshot is not an exact revision-{revision} head"
        )
    return snapshot


def _canonical_inventory(inventory: SourceNoteInventory) -> SourceNoteInventory:
    if type(inventory) is not SourceNoteInventory:
        raise ReviewedTemporalSnapshotAuthorityError("SourceNote inventory type was substituted")
    reopened = SourceNoteInventory.model_validate_json(
        canonical_json_bytes(inventory.model_dump(mode="json"))
    )
    if type(reopened) is not SourceNoteInventory or reopened != inventory:
        raise ReviewedTemporalSnapshotAuthorityError("SourceNote inventory is not canonical")
    if any(type(note) is not CanonicalSourceNoteSnapshot for note in reopened.notes):
        raise ReviewedTemporalSnapshotAuthorityError("SourceNote snapshot type was substituted")
    return reopened


def _prove_source_note_continuity(
    *,
    analysis_snapshot: ChangeControlSnapshot,
    reviewed_snapshot: ChangeControlSnapshot,
    analysis_inventory: SourceNoteInventory,
    persisted_analysis_inventory: SourceNoteInventory,
) -> SourceNoteInventory:
    """Mechanically bind unchanged revision-2 source roots to exact revision 4."""

    analysis_snapshot = _exact_snapshot(analysis_snapshot, revision=2)
    reviewed_snapshot = _exact_snapshot(reviewed_snapshot, revision=4)
    analysis_inventory = _canonical_inventory(analysis_inventory)
    persisted_analysis_inventory = _canonical_inventory(persisted_analysis_inventory)
    if analysis_inventory != persisted_analysis_inventory:
        raise ReviewedTemporalSnapshotAuthorityError(
            "fresh repository SourceNote inventory differs from persisted temporal evidence"
        )
    if (
        analysis_snapshot.aggregate.aggregate_id != reviewed_snapshot.aggregate.aggregate_id
        or analysis_inventory.aggregate_id != analysis_snapshot.aggregate.aggregate_id
        or analysis_inventory.snapshot_revision != 2
        or analysis_inventory.aggregate_sha256 != analysis_snapshot.aggregate_sha256
    ):
        raise ReviewedTemporalSnapshotAuthorityError(
            "revision-2 SourceNote inventory binds a different analysis head"
        )
    if analysis_snapshot.aggregate.documents != reviewed_snapshot.aggregate.documents:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed revision changed the authenticated document registry"
        )
    if analysis_snapshot.aggregate.claims != reviewed_snapshot.aggregate.claims:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed revision changed the authenticated claim registry"
        )

    notes_by_document = {
        note.document.document_version_id: note for note in analysis_inventory.notes
    }
    documents_by_id = {
        document.document_version_id: document
        for document in reviewed_snapshot.aggregate.documents.documents
    }
    if len(notes_by_document) != len(analysis_inventory.notes) or set(notes_by_document) != set(
        documents_by_id
    ):
        raise ReviewedTemporalSnapshotAuthorityError(
            "SourceNote inventory does not exactly cover reviewed documents"
        )
    for document_version_id, inventory_note in notes_by_document.items():
        canonical_note = CanonicalSourceNoteSnapshot.model_validate_json(
            canonical_json_bytes(inventory_note.model_dump(mode="json"))
        )
        if (
            canonical_note != inventory_note
            or canonical_note.document != documents_by_id[document_version_id]
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed document metadata or exact SourceNote content changed"
            )
        encoded = inventory_note.source_note_utf8.encode("utf-8")
        if (
            len(encoded) != inventory_note.source_note_utf8_bytes
            or hashlib.sha256(encoded).hexdigest() != inventory_note.source_note_sha256
            or not 0 <= inventory_note.body_start_char <= len(inventory_note.source_note_utf8)
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "SourceNote bytes, SHA, or body boundary failed continuity"
            )

    claim_keys: list[tuple[str, str]] = []
    for claim in reviewed_snapshot.aggregate.claims.revisions:
        note = notes_by_document.get(claim.document.document_version_id)
        if (
            note is None
            or claim.document != note.document
            or claim.source.source_note_path != note.source_note_path
            or claim.source.source_note_sha256 != note.source_note_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed claim no longer binds its exact SourceNote path and SHA"
            )
        claim_keys.append((claim.document.document_version_id, claim.source.source_claim_id))
    if len(claim_keys) != len(set(claim_keys)):
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed source-claim coverage contains duplicate identities"
        )

    reviewed_inventory = SourceNoteInventory.create(
        snapshot=reviewed_snapshot,
        notes=analysis_inventory.notes,
    )
    if reviewed_inventory.notes != analysis_inventory.notes:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed SourceNote inventory changed exact note content"
        )
    return _canonical_inventory(reviewed_inventory)


def _capability_payload(
    *,
    binding: ReviewedTemporalSnapshotBinding,
    analysis_snapshot: ChangeControlSnapshot,
    reviewed_snapshot: ChangeControlSnapshot,
    analysis_inventory: SourceNoteInventory,
    reviewed_inventory: SourceNoteInventory,
) -> bytes:
    return canonical_json_bytes(
        {
            "namespace": "mastervault.reviewed-source-note-capability.v1",
            "binding": binding.model_dump(mode="json"),
            "analysis_snapshot": {
                "aggregate": analysis_snapshot.aggregate.model_dump(mode="json"),
                "revision": analysis_snapshot.revision,
                "aggregate_sha256": analysis_snapshot.aggregate_sha256,
            },
            "reviewed_snapshot": {
                "aggregate": reviewed_snapshot.aggregate.model_dump(mode="json"),
                "revision": reviewed_snapshot.revision,
                "aggregate_sha256": reviewed_snapshot.aggregate_sha256,
            },
            "analysis_inventory": analysis_inventory.model_dump(mode="json"),
            "reviewed_inventory": reviewed_inventory.model_dump(mode="json"),
        }
    )


@dataclass(frozen=True, eq=False)
class RepositoryVerifiedReviewedSourceNoteInventoryCapability:
    """Process-local authority over exact SourceNote continuity at revision 4."""

    _binding: ReviewedTemporalSnapshotBinding
    _analysis_snapshot: ChangeControlSnapshot
    _reviewed_snapshot: ChangeControlSnapshot
    _analysis_inventory: SourceNoteInventory
    _reviewed_inventory: SourceNoteInventory
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if type(self) is not RepositoryVerifiedReviewedSourceNoteInventoryCapability:
            raise TypeError("reviewed SourceNote capability type cannot be substituted")
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("reviewed SourceNote capabilities are service-created only")

    @property
    def binding(self) -> ReviewedTemporalSnapshotBinding:
        return self._binding

    def __reduce__(self) -> Any:
        raise TypeError("reviewed SourceNote capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("reviewed SourceNote capabilities are process-local")

    def __getstate__(self) -> Any:
        raise TypeError("reviewed SourceNote capabilities are process-local")

    def verify(self, *, snapshot: ChangeControlSnapshot) -> SourceNoteInventory:
        """Return the rev4 inventory only for the exact sealed reviewed head."""

        if type(self) is not RepositoryVerifiedReviewedSourceNoteInventoryCapability:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed SourceNote capability type was substituted"
            )
        if self._token is not _CAPABILITY_TOKEN:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed SourceNote capability was not service-created"
            )
        supplied = _exact_snapshot(snapshot, revision=4)
        try:
            binding = ReviewedTemporalSnapshotBinding.model_validate_json(
                canonical_json_bytes(self._binding.model_dump(mode="json"))
            )
            analysis_inventory = _canonical_inventory(self._analysis_inventory)
            reviewed_inventory = _canonical_inventory(self._reviewed_inventory)
            analysis_snapshot = _exact_snapshot(self._analysis_snapshot, revision=2)
            reviewed_snapshot = _exact_snapshot(self._reviewed_snapshot, revision=4)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed SourceNote capability content was altered"
            ) from exc
        if (
            binding != self._binding
            or supplied != reviewed_snapshot
            or reviewed_inventory
            != _prove_source_note_continuity(
                analysis_snapshot=analysis_snapshot,
                reviewed_snapshot=reviewed_snapshot,
                analysis_inventory=analysis_inventory,
                persisted_analysis_inventory=analysis_inventory,
            )
            or binding.analysis_inventory_sha256 != analysis_inventory.inventory_sha256
            or binding.reviewed_inventory_sha256 != reviewed_inventory.inventory_sha256
            or binding.analysis_head.revision != analysis_snapshot.revision
            or binding.analysis_head.aggregate_sha256 != analysis_snapshot.aggregate_sha256
            or binding.reviewed_head.revision != reviewed_snapshot.revision
            or binding.reviewed_head.aggregate_sha256 != reviewed_snapshot.aggregate_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed SourceNote capability does not bind the supplied exact snapshot"
            )
        expected_seal = hmac.new(
            _CAPABILITY_SECRET,
            _capability_payload(
                binding=binding,
                analysis_snapshot=analysis_snapshot,
                reviewed_snapshot=reviewed_snapshot,
                analysis_inventory=analysis_inventory,
                reviewed_inventory=reviewed_inventory,
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self._seal, expected_seal):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed SourceNote capability seal was altered"
            )
        return reviewed_inventory


def _authority_payload(
    *,
    snapshot: ChangeControlSnapshot,
    temporal_analysis: TemporalAnalysisEvidence,
    temporal_commit: TemporalProposalCommit,
    review_request: HumanReviewRequest,
    review_decision: HumanReviewDecision,
    classification_outcomes: tuple[RecordedInferenceOutcome, ...],
    dependency_outcomes: tuple[RecordedInferenceOutcome, ...],
    temporal_prerequisite: TemporalDecisionPrerequisite,
    binding: ReviewedTemporalSnapshotBinding,
) -> bytes:
    return canonical_json_bytes(
        {
            "namespace": "mastervault.reviewed-temporal-snapshot-authority.v1",
            "snapshot": {
                "aggregate": snapshot.aggregate.model_dump(mode="json"),
                "revision": snapshot.revision,
                "aggregate_sha256": snapshot.aggregate_sha256,
            },
            "temporal_analysis": temporal_analysis.model_dump(mode="json"),
            "temporal_commit": temporal_commit.model_dump(mode="json"),
            "review_request": review_request.model_dump(mode="json"),
            "review_decision": review_decision.model_dump(mode="json"),
            "classification_outcomes": [
                item.model_dump(mode="json") for item in classification_outcomes
            ],
            "dependency_outcomes": [item.model_dump(mode="json") for item in dependency_outcomes],
            "temporal_prerequisite": temporal_prerequisite.model_dump(mode="json"),
            "binding": binding.model_dump(mode="json"),
        }
    )


@dataclass(frozen=True, eq=False)
class ReviewedTemporalSnapshotAuthority:
    """Complete process-local authority chain for one exact reviewed head."""

    snapshot: ChangeControlSnapshot
    temporal_analysis: TemporalAnalysisEvidence
    temporal_commit: TemporalProposalCommit
    review_request: HumanReviewRequest
    review_decision: HumanReviewDecision
    classification_outcomes: tuple[RecordedInferenceOutcome, ...]
    dependency_outcomes: tuple[RecordedInferenceOutcome, ...]
    temporal_prerequisite: TemporalDecisionPrerequisite
    binding: ReviewedTemporalSnapshotBinding
    source_note_capability: RepositoryVerifiedReviewedSourceNoteInventoryCapability
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if type(self) is not ReviewedTemporalSnapshotAuthority:
            raise TypeError("reviewed temporal authority type cannot be substituted")
        if self._token is not _AUTHORITY_TOKEN:
            raise TypeError("reviewed temporal authorities are service-created only")
        self.verify()

    def verify(self) -> ReviewedTemporalSnapshotAuthority:
        """Revalidate the exact sealed lineage for a future trusted consumer."""

        if type(self) is not ReviewedTemporalSnapshotAuthority:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority type was substituted"
            )
        if self._token is not _AUTHORITY_TOKEN:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority was not service-created"
            )
        if (
            type(self.snapshot) is not ChangeControlSnapshot
            or type(self.temporal_analysis) is not TemporalAnalysisEvidence
            or type(self.temporal_commit) is not TemporalProposalCommit
            or type(self.review_request) is not HumanReviewRequest
            or type(self.review_decision) is not HumanReviewDecision
            or type(self.temporal_prerequisite) is not TemporalDecisionPrerequisite
            or type(self.binding) is not ReviewedTemporalSnapshotBinding
            or type(self.source_note_capability)
            is not RepositoryVerifiedReviewedSourceNoteInventoryCapability
            or type(self.classification_outcomes) is not tuple
            or type(self.dependency_outcomes) is not tuple
            or any(
                type(item) is not RecordedInferenceOutcome for item in self.classification_outcomes
            )
            or any(type(item) is not RecordedInferenceOutcome for item in self.dependency_outcomes)
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority contains a substituted domain type"
            )
        try:
            snapshot = _exact_snapshot(self.snapshot, revision=4)
            temporal_analysis = TemporalAnalysisEvidence.from_canonical_bytes(
                self.temporal_analysis.canonical_bytes()
            )
            temporal_commit = TemporalProposalCommit.model_validate_json(
                canonical_json_bytes(self.temporal_commit.model_dump(mode="json"))
            )
            review_request = HumanReviewRequest.model_validate_json(
                canonical_json_bytes(self.review_request.model_dump(mode="json"))
            )
            review_decision = HumanReviewDecision.model_validate_json(
                canonical_json_bytes(self.review_decision.model_dump(mode="json"))
            )
            classification_outcomes = tuple(
                RecordedInferenceOutcome.model_validate_json(
                    canonical_json_bytes(item.model_dump(mode="json"))
                )
                for item in self.classification_outcomes
            )
            dependency_outcomes = tuple(
                RecordedInferenceOutcome.model_validate_json(
                    canonical_json_bytes(item.model_dump(mode="json"))
                )
                for item in self.dependency_outcomes
            )
            prerequisite = TemporalDecisionPrerequisite.model_validate_json(
                canonical_json_bytes(self.temporal_prerequisite.model_dump(mode="json"))
            )
            binding = ReviewedTemporalSnapshotBinding.model_validate_json(
                canonical_json_bytes(self.binding.model_dump(mode="json"))
            )
            inventory = self.source_note_capability.verify(snapshot=snapshot)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority lineage was altered"
            ) from exc
        if (
            temporal_analysis != self.temporal_analysis
            or temporal_commit != self.temporal_commit
            or review_request != self.review_request
            or review_decision != self.review_decision
            or classification_outcomes != self.classification_outcomes
            or dependency_outcomes != self.dependency_outcomes
            or prerequisite != self.temporal_prerequisite
            or binding != self.binding
            or temporal_analysis.proposal != temporal_commit.proposal
            or temporal_analysis.manifest_id != binding.temporal_analysis_manifest_id
            or temporal_analysis.manifest_sha256 != binding.temporal_analysis_manifest_sha256
            or temporal_commit.evidence_repository_id != binding.evidence_repository_id
            or review_request.request_id != review_decision.request_id
            or review_request.request_id != binding.review_request_id
            or review_decision.decided_aggregate != snapshot.aggregate
            or review_decision.decided_aggregate_sha256 != snapshot.aggregate_sha256
            or prerequisite.review_open_head != binding.reviewed_head
            or self.source_note_capability.binding != binding
            or inventory.inventory_sha256 != binding.reviewed_inventory_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority fields do not form one exact chain"
            )
        expected_seal = hmac.new(
            _AUTHORITY_SECRET,
            _authority_payload(
                snapshot=snapshot,
                temporal_analysis=temporal_analysis,
                temporal_commit=temporal_commit,
                review_request=review_request,
                review_decision=review_decision,
                classification_outcomes=classification_outcomes,
                dependency_outcomes=dependency_outcomes,
                temporal_prerequisite=prerequisite,
                binding=binding,
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self._seal, expected_seal):
            raise ReviewedTemporalSnapshotAuthorityError(
                "reviewed temporal authority seal was altered"
            )
        return self

    def __reduce__(self) -> Any:
        raise TypeError("reviewed temporal authorities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("reviewed temporal authorities are process-local")

    def __getstate__(self) -> Any:
        raise TypeError("reviewed temporal authorities are process-local")


def _reopen_verified_batch(
    *,
    repository: FilesystemInferenceEvidenceRepository,
    batch_id: str,
    batch_sha256: str,
    label: str,
) -> tuple[RecordedInferenceOutcome, ...]:
    outcomes, capability = repository.resolve_verified_batch(
        batch_id=batch_id,
        batch_sha256=batch_sha256,
    )
    if type(outcomes) is not tuple or any(
        type(outcome) is not RecordedInferenceOutcome for outcome in outcomes
    ):
        raise ReviewedTemporalSnapshotAuthorityError(f"{label} outcomes were substituted")
    if type(capability) is not RepositoryVerifiedInferenceEvidenceBatch:
        raise ReviewedTemporalSnapshotAuthorityError(f"{label} capability was substituted")
    if capability.batch_id != batch_id or capability.batch_sha256 != batch_sha256:
        raise ReviewedTemporalSnapshotAuthorityError(
            f"{label} capability differs from the temporal-analysis reference"
        )
    verified = capability.verify(repository=repository, outcomes=outcomes)
    if type(verified) is not tuple or verified != outcomes:
        raise ReviewedTemporalSnapshotAuthorityError(f"{label} verification was substituted")
    return verified


def resolve_reviewed_temporal_snapshot(
    store: SqliteChangeControlStore,
    *,
    temporal_analysis_manifest_id: str,
    temporal_analysis_manifest_sha256: str,
    temporal_request_id: str,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    source_note_resolver: RepositorySourceNoteInventoryResolver,
) -> ReviewedTemporalSnapshotAuthority:
    """Mint rev4 SourceNote authority from exact durable evidence and a decided review."""

    if type(store) is not SqliteChangeControlStore:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed snapshot authority requires the exact SQLite store"
        )
    if type(evidence_repository) is not FilesystemInferenceEvidenceRepository:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed snapshot authority requires the exact filesystem evidence repository"
        )
    if type(source_note_resolver) is not RepositorySourceNoteInventoryResolver:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed snapshot authority requires the exact repository SourceNote resolver"
        )
    if (
        type(temporal_analysis_manifest_id) is not str
        or type(temporal_analysis_manifest_sha256) is not str
        or type(temporal_request_id) is not str
    ):
        raise ReviewedTemporalSnapshotAuthorityError(
            "temporal manifest and request locator types must be exact strings"
        )
    if (
        len(temporal_analysis_manifest_sha256) != 64
        or any(
            character not in "0123456789abcdef" for character in temporal_analysis_manifest_sha256
        )
        or temporal_analysis_manifest_id != f"temporal-analysis:{temporal_analysis_manifest_sha256}"
    ):
        raise ReviewedTemporalSnapshotAuthorityError(
            "temporal analysis manifest ID/SHA locator is not exact"
        )

    try:
        manifest_bytes = evidence_repository.resolve_temporal_analysis_manifest(
            manifest_id=temporal_analysis_manifest_id,
            manifest_sha256=temporal_analysis_manifest_sha256,
        )
        temporal_analysis = TemporalAnalysisEvidence.from_canonical_bytes(manifest_bytes)
        if type(temporal_analysis) is not TemporalAnalysisEvidence:
            raise ReviewedTemporalSnapshotAuthorityError(
                "temporal-analysis evidence type was substituted"
            )
        if (
            temporal_analysis.manifest_id != temporal_analysis_manifest_id
            or temporal_analysis.manifest_sha256 != temporal_analysis_manifest_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "durable temporal analysis differs from the requested exact manifest"
            )

        analysis_snapshot = _exact_snapshot(
            ChangeControlSnapshot(
                aggregate=temporal_analysis.analysis_aggregate,
                revision=temporal_analysis.analysis_head.revision,
                aggregate_sha256=temporal_analysis.analysis_head.aggregate_sha256,
            ),
            revision=2,
        )
        inventory_capability = source_note_resolver.resolve_source_note_inventory(
            snapshot=analysis_snapshot
        )
        if type(inventory_capability) is not RepositoryVerifiedSourceNoteInventoryCapability:
            raise ReviewedTemporalSnapshotAuthorityError(
                "revision-2 SourceNote capability was substituted"
            )
        analysis_inventory = _canonical_inventory(
            inventory_capability.verify(snapshot=analysis_snapshot)
        )

        classification_outcomes = _reopen_verified_batch(
            repository=evidence_repository,
            batch_id=temporal_analysis.classification_evidence_batch_id,
            batch_sha256=temporal_analysis.classification_evidence_batch_sha256,
            label="classification evidence batch",
        )
        dependency_outcomes = _reopen_verified_batch(
            repository=evidence_repository,
            batch_id=temporal_analysis.dependency_evidence_batch_id,
            batch_sha256=temporal_analysis.dependency_evidence_batch_sha256,
            label="dependency evidence batch",
        )
        reproduced = verify_temporal_analysis_evidence(
            temporal_analysis,
            verified_bootstrap=source_note_resolver.verified_bootstrap,
            inventory_capability=inventory_capability,
            classification_outcomes=classification_outcomes,
            dependency_outcomes=dependency_outcomes,
        )
        if type(reproduced) is not TemporalProposal or reproduced != temporal_analysis.proposal:
            raise ReviewedTemporalSnapshotAuthorityError(
                "durable evidence does not reproduce the exact temporal proposal"
            )

        preliminary_head = store.load(reproduced.proposed_aggregate.aggregate_id)
        if preliminary_head is None:
            raise ReviewedTemporalSnapshotAuthorityError("reviewed aggregate head is absent")
        preliminary_head = _exact_snapshot(preliminary_head, revision=4)

        operation_id = f"temporal-commit:{temporal_analysis.manifest_sha256}"
        commit_receipt = store.compare_and_swap(
            reproduced.proposed_aggregate,
            expected_revision=2,
            operation_id=operation_id,
        )
        if (
            type(commit_receipt) is not ChangeControlCommit
            or not commit_receipt.replayed
            or not commit_receipt.changed
            or commit_receipt.aggregate_id != reproduced.proposed_aggregate.aggregate_id
            or commit_receipt.revision != 3
            or commit_receipt.aggregate_sha256 != reproduced.binding.proposed_aggregate_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "SQLite does not contain the exact temporal proposal commit receipt"
            )
        exact_commit = TemporalProposalCommit(
            proposal=reproduced,
            operation_id=operation_id,
            temporal_analysis_manifest_id=temporal_analysis.manifest_id,
            temporal_analysis_manifest_sha256=temporal_analysis.manifest_sha256,
            temporal_analysis_manifest_path=(
                f"temporal/evidence/analyses/{temporal_analysis.manifest_sha256}.json"
            ),
            evidence_repository_id=evidence_repository.repository_id,
            aggregate_id=commit_receipt.aggregate_id,
            revision=3,
            aggregate_sha256=commit_receipt.aggregate_sha256,
            changed=True,
            committed_at=commit_receipt.committed_at,
            replayed=True,
        )

        view = store.get_review_request(temporal_request_id)
        if (
            type(view) is not HumanReviewRequestView
            or type(view.request) is not HumanReviewRequest
            or view.lifecycle != ReviewLifecycle.DECIDED
            or type(view.decision) is not HumanReviewDecision
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "temporal request is not one exact immutable decided review"
            )
        decision = view.decision
        assert decision is not None
        request_receipt = HumanReviewRequestReceipt(
            request=view.request,
            lifecycle=view.lifecycle,
            replayed=False,
        )
        decision_receipt = HumanReviewDecisionReceipt(
            decision=decision,
            aggregate_revision=decision.decided_revision,
            aggregate_sha256=decision.decided_aggregate_sha256,
            replayed=False,
        )
        prerequisite = temporal_prerequisite_from_decision(
            commit=exact_commit,
            request=request_receipt,
            decision=decision_receipt,
        )
        if type(prerequisite) is not TemporalDecisionPrerequisite:
            raise ReviewedTemporalSnapshotAuthorityError(
                "temporal decision prerequisite type was substituted"
            )

        reviewed_snapshot = store.load(exact_commit.aggregate_id)
        if reviewed_snapshot is None:
            raise ReviewedTemporalSnapshotAuthorityError("reviewed aggregate head is absent")
        reviewed_snapshot = _exact_snapshot(reviewed_snapshot, revision=4)
        if (
            reviewed_snapshot.aggregate != decision.decided_aggregate
            or reviewed_snapshot.aggregate_sha256 != decision.decided_aggregate_sha256
            or prerequisite.review_open_head.aggregate_id != exact_commit.aggregate_id
            or prerequisite.review_open_head.revision != reviewed_snapshot.revision
            or prerequisite.review_open_head.aggregate_sha256 != reviewed_snapshot.aggregate_sha256
        ):
            raise ReviewedTemporalSnapshotAuthorityError(
                "live head differs from the exact authoritative temporal decision"
            )

        if reviewed_snapshot != preliminary_head:
            raise ReviewedTemporalSnapshotAuthorityError(
                "authoritative aggregate head changed while reopening commit authority"
            )

        reviewed_inventory = _prove_source_note_continuity(
            analysis_snapshot=analysis_snapshot,
            reviewed_snapshot=reviewed_snapshot,
            analysis_inventory=analysis_inventory,
            persisted_analysis_inventory=temporal_analysis.source_note_inventory,
        )
        binding = ReviewedTemporalSnapshotBinding.create(
            evidence_repository_id=evidence_repository.repository_id,
            temporal_analysis=temporal_analysis,
            commit=exact_commit,
            request=view.request,
            decision=decision,
            prerequisite=prerequisite,
            analysis_inventory=analysis_inventory,
            reviewed_inventory=reviewed_inventory,
        )
        seal = hmac.new(
            _CAPABILITY_SECRET,
            _capability_payload(
                binding=binding,
                analysis_snapshot=analysis_snapshot,
                reviewed_snapshot=reviewed_snapshot,
                analysis_inventory=analysis_inventory,
                reviewed_inventory=reviewed_inventory,
            ),
            hashlib.sha256,
        ).hexdigest()
        capability = RepositoryVerifiedReviewedSourceNoteInventoryCapability(
            _binding=binding,
            _analysis_snapshot=analysis_snapshot,
            _reviewed_snapshot=reviewed_snapshot,
            _analysis_inventory=analysis_inventory,
            _reviewed_inventory=reviewed_inventory,
            _token=_CAPABILITY_TOKEN,
            _seal=seal,
        )
        if capability.verify(snapshot=reviewed_snapshot) != reviewed_inventory:
            raise ReviewedTemporalSnapshotAuthorityError(
                "new reviewed SourceNote capability failed self-verification"
            )

        final_head = store.load(exact_commit.aggregate_id)
        if type(final_head) is not ChangeControlSnapshot or final_head != reviewed_snapshot:
            raise ReviewedTemporalSnapshotAuthorityError(
                "authoritative aggregate head changed during reviewed-snapshot resolution"
            )
        authority_seal = hmac.new(
            _AUTHORITY_SECRET,
            _authority_payload(
                snapshot=reviewed_snapshot,
                temporal_analysis=temporal_analysis,
                temporal_commit=exact_commit,
                review_request=view.request,
                review_decision=decision,
                classification_outcomes=classification_outcomes,
                dependency_outcomes=dependency_outcomes,
                temporal_prerequisite=prerequisite,
                binding=binding,
            ),
            hashlib.sha256,
        ).hexdigest()
        return ReviewedTemporalSnapshotAuthority(
            snapshot=reviewed_snapshot,
            temporal_analysis=temporal_analysis,
            temporal_commit=exact_commit,
            review_request=view.request,
            review_decision=decision,
            classification_outcomes=classification_outcomes,
            dependency_outcomes=dependency_outcomes,
            temporal_prerequisite=prerequisite,
            binding=binding,
            source_note_capability=capability,
            _token=_AUTHORITY_TOKEN,
            _seal=authority_seal,
        )
    except ReviewedTemporalSnapshotAuthorityError:
        raise
    except Exception as exc:
        raise ReviewedTemporalSnapshotAuthorityError(
            "reviewed temporal snapshot authority failed closed"
        ) from exc


__all__ = [
    "RepositoryVerifiedReviewedSourceNoteInventoryCapability",
    "ReviewedTemporalSnapshotAuthority",
    "ReviewedTemporalSnapshotAuthorityError",
    "ReviewedTemporalSnapshotBinding",
    "resolve_reviewed_temporal_snapshot",
]
