"""Pure, closed-world workload contracts for reviewed actual-impact analysis.

The public builder accepts only a process-local
``ReviewedTemporalSnapshotAuthority``.  It derives governing changes from the
exact claim-level temporal subjects accepted by that authority's human review,
then crosses every governing change with every revision-4 document.  Every
pair is retained either as an inference question or as a typed exclusion.

Revision-4 attention is regenerated as bounded context only.  A current
document remains eligible when it is unreached, contains no extracted claims,
or is reached solely through historical-reference dependencies.  This module
performs no provider, filesystem, store, review, staging, managed-publication,
evaluation, or orchestration work and defines no impact result dispositions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

import mastervault.change_control as change_control_types
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.discovery import (
    AttentionPath,
    DocumentAttentionRanking,
    RelationshipCandidateSet,
    generate_relationship_candidates,
    rank_document_attention,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    DependencyAssessment,
    DocumentVersionMetadata,
    PersistedRelationType,
    RelationAssessment,
    TemporalConstraint,
    TemporalConstraintStatus,
    TemporalResolution,
    TemporalResolutionContext,
    TemporalState,
    TemporalTargetKind,
    VersionedClaimRevision,
    canonical_json_bytes,
)
from mastervault.change_control.review import (
    ReviewDisposition,
    ReviewSubjectKind,
)
from mastervault.change_control.reviewed_snapshot_binding import ReviewedTemporalSnapshotBinding

MAX_IMPACT_DOCUMENT_SHARDS_V1 = 16
MAX_IMPACT_QUESTIONS_V1 = 64
MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_IMPACT_TOTAL_INPUT_BYTES_V1 = 1024 * 1024
MAX_IMPACT_INDEX_CANONICAL_BYTES_V1 = 256 * 1024

_DOCUMENT_VERSION_ID = r"^docv:[0-9a-f]{64}$"
_GOVERNING_ID = r"^impactroot:[0-9a-f]{64}$"
_QUESTION_ID = r"^impactq:[0-9a-f]{64}$"
_EXCLUSION_ID = r"^impactx:[0-9a-f]{64}$"
_SHARD_ID = r"^impactin:[0-9a-f]{64}$"
_BINDING_ID = r"^impactbinding:[0-9a-f]{64}$"
_WORKLOAD_ID = r"^impactwork:[0-9a-f]{64}$"
_PLACEHOLDER_SHA256 = "0" * 64
_PLACEHOLDER_QUESTION_ID = f"impactq:{_PLACEHOLDER_SHA256}"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ImpactAnalysisLimitError(RuntimeError):
    """A fixed workload limit was exceeded and no partial artifact exists."""

    def __init__(self, *, category: str, limit: int, observed: int) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        super().__init__(f"impact analysis limit exceeded: {category}={observed} > {limit}")


class ImpactExclusionReason(StrEnum):
    CHANGED_DOCUMENT = "changed-document"
    GOVERNING_UPSTREAM_DOCUMENT = "governing-upstream-document"
    FUTURE = "future"
    HISTORICAL = "historical"
    EXPIRED = "expired"
    UNRESOLVED = "unresolved"


class ImpactAttentionStatus(StrEnum):
    RANKED = "ranked"
    DISCOVERY_EXCLUDED = "discovery-excluded"
    UNREACHED = "unreached"


def _root_attention_status(paths: tuple[AttentionPath, ...]) -> ImpactAttentionStatus:
    if not paths:
        return ImpactAttentionStatus.UNREACHED
    if any(path.eligible_for_attention for path in paths):
        return ImpactAttentionStatus.RANKED
    return ImpactAttentionStatus.DISCOVERY_EXCLUDED


_EXCLUSION_ORDER = {
    ImpactExclusionReason.CHANGED_DOCUMENT: 0,
    ImpactExclusionReason.GOVERNING_UPSTREAM_DOCUMENT: 1,
    ImpactExclusionReason.FUTURE: 2,
    ImpactExclusionReason.HISTORICAL: 3,
    ImpactExclusionReason.EXPIRED: 4,
    ImpactExclusionReason.UNRESOLVED: 5,
}


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class AcceptedGoverningChange(_StrictFrozenModel):
    """One exact reviewed changed-to-older claim supersession."""

    schema_version: Literal[1] = 1
    constraint: TemporalConstraint
    original_subject_sha256: str = Field(pattern=SHA256_PATTERN)
    review_disposition: ReviewDisposition
    relation: RelationAssessment
    changed_claim_revision: VersionedClaimRevision
    upstream_claim_revision: VersionedClaimRevision
    changed_temporal_resolution: TemporalResolution
    upstream_temporal_resolution: TemporalResolution
    governing_change_id: str = Field(pattern=_GOVERNING_ID)
    governing_change_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.governing-impact-change.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "governing_change_id", "governing_change_sha256"},
            ),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.review_disposition not in {
            ReviewDisposition.ACCEPTED,
            ReviewDisposition.EDITED,
        }:
            raise ValueError("governing impact changes require an accepted or edited review item")
        if (
            self.constraint.status != TemporalConstraintStatus.ACCEPTED
            or self.constraint.target.kind != TemporalTargetKind.CLAIM_REVISION
            or self.constraint.target.target_id != self.upstream_claim_revision.claim_revision_id
        ):
            raise ValueError("governing impact constraint must accept the exact older claim")
        if (
            self.relation.relation_type != PersistedRelationType.SUPERSEDES
            or self.relation.relation_id is None
            or self.relation.endpoint_ids
            != (
                self.changed_claim_revision.claim_revision_id,
                self.upstream_claim_revision.claim_revision_id,
            )
            or self.constraint.basis_relation_ids != (self.relation.relation_id,)
        ):
            raise ValueError("governing impact change requires one exact SUPERSEDES basis")
        if (
            self.changed_temporal_resolution.target.target_id
            != self.changed_claim_revision.claim_revision_id
            or self.changed_temporal_resolution.state != TemporalState.CURRENT
            or self.upstream_temporal_resolution.target.target_id
            != self.upstream_claim_revision.claim_revision_id
            or self.upstream_temporal_resolution.state != TemporalState.HISTORICAL
            or self.changed_temporal_resolution.as_of != self.upstream_temporal_resolution.as_of
        ):
            raise ValueError("governing impact claims must resolve current-to-historical")
        digest = _sha256(self._payload())
        if (
            self.governing_change_sha256 != digest
            or self.governing_change_id != f"impactroot:{digest}"
        ):
            raise ValueError("governing impact change ID/SHA differs from its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        constraint: TemporalConstraint,
        original_subject_sha256: str,
        review_disposition: ReviewDisposition,
        relation: RelationAssessment,
        changed_claim_revision: VersionedClaimRevision,
        upstream_claim_revision: VersionedClaimRevision,
        changed_temporal_resolution: TemporalResolution,
        upstream_temporal_resolution: TemporalResolution,
    ) -> Self:
        values: dict[str, Any] = {
            "constraint": constraint.model_dump(mode="json"),
            "original_subject_sha256": original_subject_sha256,
            "review_disposition": review_disposition.value,
            "relation": relation.model_dump(mode="json"),
            "changed_claim_revision": changed_claim_revision.model_dump(mode="json"),
            "upstream_claim_revision": upstream_claim_revision.model_dump(mode="json"),
            "changed_temporal_resolution": changed_temporal_resolution.model_dump(mode="json"),
            "upstream_temporal_resolution": upstream_temporal_resolution.model_dump(mode="json"),
        }
        payload = {
            "namespace": "mastervault.governing-impact-change.v1",
            "schema_version": 1,
            **values,
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **values,
                    "governing_change_id": f"impactroot:{digest}",
                    "governing_change_sha256": digest,
                }
            )
        )


def _path_is_root_relevant(path: AttentionPath, governing: AcceptedGoverningChange) -> bool:
    if path.changed_claim_revision_id != governing.changed_claim_revision.claim_revision_id:
        return False
    relation_ids = {item.relation_id for item in path.relation_steps}
    first_upstream = path.dependency_steps[0].upstream_claim_revision_id
    return bool(
        governing.relation.relation_id in relation_ids
        or first_upstream
        in {
            governing.changed_claim_revision.claim_revision_id,
            governing.upstream_claim_revision.claim_revision_id,
        }
    )


def _impact_question_values(
    *,
    governing_change: AcceptedGoverningChange,
    target_document: DocumentVersionMetadata,
    target_temporal_resolution: TemporalResolution,
    attention_status: ImpactAttentionStatus,
    attention_paths: tuple[AttentionPath, ...],
    existing_dependencies: tuple[DependencyAssessment, ...],
) -> dict[str, Any]:
    return {
        "governing_change": governing_change.model_dump(mode="json"),
        "target_document": target_document.model_dump(mode="json"),
        "target_temporal_resolution": target_temporal_resolution.model_dump(mode="json"),
        "attention_status": attention_status.value,
        "attention_paths": [item.model_dump(mode="json") for item in attention_paths],
        "existing_dependencies": [item.model_dump(mode="json") for item in existing_dependencies],
    }


class ImpactQuestion(_StrictFrozenModel):
    """One current target document assessed against one reviewed governing root."""

    schema_version: Literal[1] = 1
    governing_change: AcceptedGoverningChange
    target_document: DocumentVersionMetadata
    target_temporal_resolution: TemporalResolution
    attention_status: ImpactAttentionStatus
    attention_paths: tuple[AttentionPath, ...]
    existing_dependencies: tuple[DependencyAssessment, ...]
    question_id: str = Field(pattern=_QUESTION_ID)
    question_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.impact-question.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "question_id", "question_sha256"},
            ),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.target_temporal_resolution.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or self.target_temporal_resolution.target.target_id
            != self.target_document.document_version_id
            or self.target_temporal_resolution.state != TemporalState.CURRENT
        ):
            raise ValueError("impact questions require the exact current target resolution")
        path_ids = tuple(item.path_id for item in self.attention_paths)
        if path_ids != tuple(sorted(set(path_ids))):
            raise ValueError("impact attention paths must be unique and canonical")
        if any(
            item.target_document_version_id != self.target_document.document_version_id
            or not _path_is_root_relevant(item, self.governing_change)
            for item in self.attention_paths
        ):
            raise ValueError("impact attention paths must be exact target/root context")
        if self.attention_status != _root_attention_status(self.attention_paths):
            raise ValueError("impact attention status differs from its root-specific paths")
        dependency_ids = tuple(item.dependency_id for item in self.existing_dependencies)
        if dependency_ids != tuple(sorted(set(dependency_ids))):
            raise ValueError("impact dependencies must be unique and canonical")
        root_claim_ids = {
            self.governing_change.changed_claim_revision.claim_revision_id,
            self.governing_change.upstream_claim_revision.claim_revision_id,
        }
        if any(
            item.downstream != self.target_document
            or item.upstream.claim_revision_id not in root_claim_ids
            for item in self.existing_dependencies
        ):
            raise ValueError("impact dependencies must bind the exact target and governing claims")
        digest = _sha256(self._payload())
        if self.question_sha256 != digest or self.question_id != f"impactq:{digest}":
            raise ValueError("impact question ID/SHA differs from its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        governing_change: AcceptedGoverningChange,
        target_document: DocumentVersionMetadata,
        target_temporal_resolution: TemporalResolution,
        attention_status: ImpactAttentionStatus,
        attention_paths: tuple[AttentionPath, ...],
        existing_dependencies: tuple[DependencyAssessment, ...],
    ) -> Self:
        paths = tuple(sorted(attention_paths, key=lambda item: item.path_id))
        dependencies = tuple(sorted(existing_dependencies, key=lambda item: item.dependency_id))
        values = _impact_question_values(
            governing_change=governing_change,
            target_document=target_document,
            target_temporal_resolution=target_temporal_resolution,
            attention_status=attention_status,
            attention_paths=paths,
            existing_dependencies=dependencies,
        )
        payload = {"namespace": "mastervault.impact-question.v1", "schema_version": 1, **values}
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **values,
                    "question_id": f"impactq:{digest}",
                    "question_sha256": digest,
                }
            )
        )


@dataclass(frozen=True)
class _ImpactQuestionDraft:
    """Canonical pre-identity question values used for exact budget projection."""

    governing_change: AcceptedGoverningChange
    target_document: DocumentVersionMetadata
    target_temporal_resolution: TemporalResolution
    attention_status: ImpactAttentionStatus
    attention_paths: tuple[AttentionPath, ...]
    existing_dependencies: tuple[DependencyAssessment, ...]

    @classmethod
    def create(
        cls,
        *,
        governing_change: AcceptedGoverningChange,
        target_document: DocumentVersionMetadata,
        target_temporal_resolution: TemporalResolution,
        attention_status: ImpactAttentionStatus,
        attention_paths: tuple[AttentionPath, ...],
        existing_dependencies: tuple[DependencyAssessment, ...],
    ) -> _ImpactQuestionDraft:
        return cls(
            governing_change=governing_change,
            target_document=target_document,
            target_temporal_resolution=target_temporal_resolution,
            attention_status=attention_status,
            attention_paths=tuple(sorted(attention_paths, key=lambda item: item.path_id)),
            existing_dependencies=tuple(
                sorted(existing_dependencies, key=lambda item: item.dependency_id)
            ),
        )

    def values(self) -> dict[str, Any]:
        return _impact_question_values(
            governing_change=self.governing_change,
            target_document=self.target_document,
            target_temporal_resolution=self.target_temporal_resolution,
            attention_status=self.attention_status,
            attention_paths=self.attention_paths,
            existing_dependencies=self.existing_dependencies,
        )

    def projected_model_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            **self.values(),
            "question_id": _PLACEHOLDER_QUESTION_ID,
            "question_sha256": _PLACEHOLDER_SHA256,
        }

    def mint(self) -> ImpactQuestion:
        return ImpactQuestion.create(
            governing_change=self.governing_change,
            target_document=self.target_document,
            target_temporal_resolution=self.target_temporal_resolution,
            attention_status=self.attention_status,
            attention_paths=self.attention_paths,
            existing_dependencies=self.existing_dependencies,
        )


class ExcludedImpactQuestion(_StrictFrozenModel):
    """One typed, pair-specific exclusion from the complete document cross-product."""

    schema_version: Literal[1] = 1
    governing_change: AcceptedGoverningChange
    target_document: DocumentVersionMetadata
    target_temporal_resolution: TemporalResolution
    reasons: tuple[ImpactExclusionReason, ...] = Field(min_length=1)
    exclusion_id: str = Field(pattern=_EXCLUSION_ID)
    exclusion_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.impact-question-exclusion.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "exclusion_id", "exclusion_sha256"},
            ),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        expected_reasons = tuple(sorted(set(self.reasons), key=_EXCLUSION_ORDER.__getitem__))
        if self.reasons != expected_reasons:
            raise ValueError("impact exclusion reasons must be unique and canonical")
        if (
            self.target_temporal_resolution.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or self.target_temporal_resolution.target.target_id
            != self.target_document.document_version_id
        ):
            raise ValueError("impact exclusion resolution names a different document")
        expected_temporal = (
            None
            if self.target_temporal_resolution.state == TemporalState.CURRENT
            else ImpactExclusionReason(self.target_temporal_resolution.state.value)
        )
        temporal_reasons = {
            ImpactExclusionReason.FUTURE,
            ImpactExclusionReason.HISTORICAL,
            ImpactExclusionReason.EXPIRED,
            ImpactExclusionReason.UNRESOLVED,
        }
        present_temporal = set(self.reasons) & temporal_reasons
        if present_temporal != ({expected_temporal} if expected_temporal else set()):
            raise ValueError("impact temporal exclusion reason differs from exact resolution")
        upstream_reason = (
            self.target_document.document_version_id
            == self.governing_change.upstream_claim_revision.document.document_version_id
        )
        if (ImpactExclusionReason.GOVERNING_UPSTREAM_DOCUMENT in self.reasons) != upstream_reason:
            raise ValueError("governing-document exclusion must be pair-specific")
        digest = _sha256(self._payload())
        if self.exclusion_sha256 != digest or self.exclusion_id != f"impactx:{digest}":
            raise ValueError("impact exclusion ID/SHA differs from its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        governing_change: AcceptedGoverningChange,
        target_document: DocumentVersionMetadata,
        target_temporal_resolution: TemporalResolution,
        reasons: tuple[ImpactExclusionReason, ...],
    ) -> Self:
        canonical_reasons = tuple(sorted(set(reasons), key=_EXCLUSION_ORDER.__getitem__))
        values: dict[str, Any] = {
            "governing_change": governing_change.model_dump(mode="json"),
            "target_document": target_document.model_dump(mode="json"),
            "target_temporal_resolution": target_temporal_resolution.model_dump(mode="json"),
            "reasons": [item.value for item in canonical_reasons],
        }
        payload = {
            "namespace": "mastervault.impact-question-exclusion.v1",
            "schema_version": 1,
            **values,
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **values,
                    "exclusion_id": f"impactx:{digest}",
                    "exclusion_sha256": digest,
                }
            )
        )


def _impact_shard_payload(
    *,
    target_note: CanonicalSourceNoteSnapshot,
    target_claim_revisions: tuple[VersionedClaimRevision, ...],
    target_temporal_resolution: TemporalResolution,
    question_json: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.impact-input-shard.v1",
        "schema_version": 1,
        "target_note": target_note.model_dump(mode="json"),
        "target_claim_revisions": [item.model_dump(mode="json") for item in target_claim_revisions],
        "target_temporal_resolution": target_temporal_resolution.model_dump(mode="json"),
        "questions": list(question_json),
    }


class ImpactInferenceShard(_StrictFrozenModel):
    """One complete, exact SourceNote and all selected questions for its document."""

    schema_version: Literal[1] = 1
    target_note: CanonicalSourceNoteSnapshot
    target_claim_revisions: tuple[VersionedClaimRevision, ...]
    target_temporal_resolution: TemporalResolution
    questions: tuple[ImpactQuestion, ...] = Field(min_length=1, max_length=MAX_IMPACT_QUESTIONS_V1)
    shard_id: str = Field(pattern=_SHARD_ID)
    shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return _impact_shard_payload(
            target_note=self.target_note,
            target_claim_revisions=self.target_claim_revisions,
            target_temporal_resolution=self.target_temporal_resolution,
            question_json=tuple(item.model_dump(mode="json") for item in self.questions),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        document = self.target_note.document
        if (
            self.target_temporal_resolution.target.target_id != document.document_version_id
            or self.target_temporal_resolution.state != TemporalState.CURRENT
        ):
            raise ValueError("impact shard requires its exact current document resolution")
        claim_ids = tuple(item.claim_revision_id for item in self.target_claim_revisions)
        if claim_ids != tuple(sorted(set(claim_ids))):
            raise ValueError("impact shard claims must be unique and canonical")
        for claim in self.target_claim_revisions:
            if (
                claim.document != document
                or claim.source.source_note_path != self.target_note.source_note_path
                or claim.source.source_note_sha256 != self.target_note.source_note_sha256
            ):
                raise ValueError("impact shard claim differs from the exact SourceNote binding")
        governing_ids = tuple(item.governing_change.governing_change_id for item in self.questions)
        if governing_ids != tuple(sorted(set(governing_ids))):
            raise ValueError("impact shard questions must use unique canonical governing order")
        if any(
            item.target_document != document
            or item.target_temporal_resolution != self.target_temporal_resolution
            for item in self.questions
        ):
            raise ValueError("impact shard questions bind a different target document")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("complete impact input document exceeds 256 KiB")
        digest = _sha256(payload)
        if self.shard_sha256 != digest or self.shard_id != f"impactin:{digest}":
            raise ValueError("impact input shard ID/SHA differs from its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        target_note: CanonicalSourceNoteSnapshot,
        target_claim_revisions: tuple[VersionedClaimRevision, ...],
        target_temporal_resolution: TemporalResolution,
        questions: tuple[ImpactQuestion, ...],
    ) -> Self:
        claims = tuple(sorted(target_claim_revisions, key=lambda item: item.claim_revision_id))
        canonical_questions = tuple(
            sorted(questions, key=lambda item: item.governing_change.governing_change_id)
        )
        payload = _impact_shard_payload(
            target_note=target_note,
            target_claim_revisions=claims,
            target_temporal_resolution=target_temporal_resolution,
            question_json=tuple(item.model_dump(mode="json") for item in canonical_questions),
        )
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1:
            raise ImpactAnalysisLimitError(
                category="complete-document-input-bytes",
                limit=MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1,
                observed=observed,
            )
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **{
                        key: value
                        for key, value in payload.items()
                        if key not in {"namespace", "schema_version"}
                    },
                    "shard_id": f"impactin:{digest}",
                    "shard_sha256": digest,
                }
            )
        )


class ImpactQuestionRef(_StrictFrozenModel):
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    governing_change_id: str = Field(pattern=_GOVERNING_ID)
    question_id: str = Field(pattern=_QUESTION_ID)
    question_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str = Field(pattern=_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)


class ImpactExclusionRef(_StrictFrozenModel):
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    governing_change_id: str = Field(pattern=_GOVERNING_ID)
    exclusion_id: str = Field(pattern=_EXCLUSION_ID)
    exclusion_sha256: str = Field(pattern=SHA256_PATTERN)
    reasons: tuple[ImpactExclusionReason, ...] = Field(min_length=1)


class ImpactWorkloadBinding(_StrictFrozenModel):
    """Exact serializable identity inputs; authority still requires regeneration."""

    schema_version: Literal[1] = 1
    algorithm_version: Literal["reviewed-impact-workload-v1"] = "reviewed-impact-workload-v1"
    reviewed_snapshot_binding: ReviewedTemporalSnapshotBinding
    temporal_analysis_manifest_id: str = Field(pattern=r"^temporal-analysis:[0-9a-f]{64}$")
    temporal_analysis_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    review_request_id: str = Field(pattern=r"^reviewreq:[0-9a-f]{64}$")
    review_decision_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    aggregate_id: str
    snapshot_revision: Literal[4] = 4
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_as_of: date
    source_note_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    changed_claim_revision_ids: tuple[str, ...]
    governing_changes: tuple[AcceptedGoverningChange, ...]
    document_versions: tuple[DocumentVersionMetadata, ...]
    relationship_candidate_result_sha256: str = Field(pattern=SHA256_PATTERN)
    attention_result_sha256: str = Field(pattern=SHA256_PATTERN)
    mechanically_relevant_claim_revision_ids: tuple[str, ...]
    binding_id: str = Field(pattern=_BINDING_ID)
    binding_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.reviewed-impact-workload-binding.v1",
            **self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"}),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        reviewed_head = self.reviewed_snapshot_binding.reviewed_head
        if (
            reviewed_head.aggregate_id != self.aggregate_id
            or reviewed_head.revision != self.snapshot_revision
            or reviewed_head.aggregate_sha256 != self.aggregate_sha256
        ):
            raise ValueError("impact binding differs from the reviewed revision-4 head")
        if (
            self.temporal_analysis_manifest_id
            != self.reviewed_snapshot_binding.temporal_analysis_manifest_id
            or self.temporal_analysis_manifest_sha256
            != self.reviewed_snapshot_binding.temporal_analysis_manifest_sha256
            or self.review_request_id != self.reviewed_snapshot_binding.review_request_id
            or self.review_decision_payload_sha256
            != self.reviewed_snapshot_binding.review_decision_payload_sha256
        ):
            raise ValueError("impact binding differs from exact manifest or review authority")
        for values, label in (
            (self.changed_claim_revision_ids, "changed claims"),
            (self.mechanically_relevant_claim_revision_ids, "mechanically relevant claims"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"impact binding {label} must be unique and canonical")
        governing_ids = tuple(item.governing_change_id for item in self.governing_changes)
        if governing_ids != tuple(sorted(set(governing_ids))):
            raise ValueError("impact governing roots must be unique and canonical")
        document_ids = tuple(item.document_version_id for item in self.document_versions)
        if document_ids != tuple(sorted(set(document_ids))):
            raise ValueError("impact document universe must be unique and canonical")
        if not set(self.changed_claim_revision_ids) <= set(
            self.mechanically_relevant_claim_revision_ids
        ):
            raise ValueError("changed claims must remain mechanically relevant")
        digest = _sha256(self._payload())
        if self.binding_sha256 != digest or self.binding_id != f"impactbinding:{digest}":
            raise ValueError("impact workload binding ID/SHA differs from its exact content")
        return self


class ImpactWorkloadIndex(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    binding: ImpactWorkloadBinding
    question_refs: tuple[ImpactQuestionRef, ...]
    exclusion_refs: tuple[ImpactExclusionRef, ...]
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.reviewed-impact-workload-index.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "workload_id", "workload_sha256"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        selected_keys = tuple(
            (item.governing_change_id, item.document_version_id) for item in self.question_refs
        )
        excluded_keys = tuple(
            (item.governing_change_id, item.document_version_id) for item in self.exclusion_refs
        )
        if selected_keys != tuple(sorted(set(selected_keys))):
            raise ValueError("impact question ledger must be unique and canonical")
        if excluded_keys != tuple(sorted(set(excluded_keys))):
            raise ValueError("impact exclusion ledger must be unique and canonical")
        if set(selected_keys) & set(excluded_keys):
            raise ValueError("impact question and exclusion ledgers must be disjoint")
        expected_keys = {
            (governing.governing_change_id, document.document_version_id)
            for governing in self.binding.governing_changes
            for document in self.binding.document_versions
        }
        if set(selected_keys) | set(excluded_keys) != expected_keys:
            raise ValueError("impact ledger does not cover the complete governing/document product")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_INDEX_CANONICAL_BYTES_V1:
            raise ValueError("impact workload index exceeds 256 KiB")
        digest = _sha256(payload)
        if self.workload_sha256 != digest or self.workload_id != f"impactwork:{digest}":
            raise ValueError("impact workload ID/SHA differs from its complete ledger")
        return self


class ImpactWorkload(_StrictFrozenModel):
    """In-memory envelope over the index, complete-document shards, and exclusions."""

    index: ImpactWorkloadIndex
    input_shards: tuple[ImpactInferenceShard, ...]
    exclusions: tuple[ExcludedImpactQuestion, ...]

    @property
    def questions(self) -> tuple[ImpactQuestion, ...]:
        return tuple(question for shard in self.input_shards for question in shard.questions)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.input_shards) > MAX_IMPACT_DOCUMENT_SHARDS_V1:
            raise ValueError("impact workload exceeds the document-shard limit")
        if len(self.questions) > MAX_IMPACT_QUESTIONS_V1:
            raise ValueError("impact workload exceeds the selected-question limit")
        total_bytes = sum(len(item.canonical_bytes()) for item in self.input_shards)
        if total_bytes > MAX_IMPACT_TOTAL_INPUT_BYTES_V1:
            raise ValueError("impact workload exceeds the aggregate input-byte limit")
        shard_documents = tuple(
            item.target_note.document.document_version_id for item in self.input_shards
        )
        if shard_documents != tuple(sorted(set(shard_documents))):
            raise ValueError("impact input shards must use one canonical shard per document")
        logical_ids = tuple(item.target_note.document.document_id for item in self.input_shards)
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("eligible current targets must have unique logical document IDs")
        expected_question_refs = tuple(
            sorted(
                (
                    ImpactQuestionRef(
                        document_version_id=shard.target_note.document.document_version_id,
                        governing_change_id=question.governing_change.governing_change_id,
                        question_id=question.question_id,
                        question_sha256=question.question_sha256,
                        input_shard_id=shard.shard_id,
                        input_shard_sha256=shard.shard_sha256,
                    )
                    for shard in self.input_shards
                    for question in shard.questions
                ),
                key=lambda item: (item.governing_change_id, item.document_version_id),
            )
        )
        if self.index.question_refs != expected_question_refs:
            raise ValueError("impact index contains a substituted input shard or question")
        expected_exclusion_refs = tuple(
            ImpactExclusionRef(
                document_version_id=item.target_document.document_version_id,
                governing_change_id=item.governing_change.governing_change_id,
                exclusion_id=item.exclusion_id,
                exclusion_sha256=item.exclusion_sha256,
                reasons=item.reasons,
            )
            for item in self.exclusions
        )
        if self.index.exclusion_refs != expected_exclusion_refs:
            raise ValueError("impact index contains a substituted exclusion")
        return self


def _verify_authority(
    authority: change_control_types.ReviewedTemporalSnapshotAuthority,
) -> change_control_types.ReviewedTemporalSnapshotAuthority:
    from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority

    if type(authority) is not ReviewedTemporalSnapshotAuthority:
        raise TypeError("impact workload requires the exact reviewed temporal authority")
    return authority.verify()


def _derive_governing_changes(
    authority: change_control_types.ReviewedTemporalSnapshotAuthority,
    *,
    temporal_context: TemporalResolutionContext,
) -> tuple[AcceptedGoverningChange, ...]:
    aggregate = authority.snapshot.aggregate
    changed_ids = set(
        authority.temporal_analysis.proposal.binding.analysis_bootstrap.changed_claim_revision_ids
    )
    decisions = {(item.kind, item.subject_id): item for item in authority.review_decision.items}
    constraints = {item.constraint_id: item for item in aggregate.temporal_constraints.constraints}
    relations = {
        item.relation_id: item
        for item in aggregate.relation_graph.assessments
        if item.relation_id is not None
    }
    claims = {item.claim_revision_id: item for item in aggregate.claims.revisions}
    roots: list[AcceptedGoverningChange] = []
    for subject in authority.review_request.subjects:
        if subject.kind != ReviewSubjectKind.TEMPORAL_CONSTRAINT:
            continue
        original = subject.subject
        if type(original) is not TemporalConstraint:
            raise ValueError("claim temporal review subject has a substituted payload type")
        if original.target.kind != TemporalTargetKind.CLAIM_REVISION:
            continue
        decision = decisions[(subject.kind, subject.subject_id)]
        if decision.disposition == ReviewDisposition.REJECTED:
            continue
        current = constraints.get(original.constraint_id)
        if current is None or current.status != TemporalConstraintStatus.ACCEPTED:
            raise ValueError("review-accepted claim constraint is not accepted in revision 4")
        if len(current.basis_relation_ids) != 1:
            raise ValueError("impact governing constraints require exactly one relation basis")
        relation = relations.get(current.basis_relation_ids[0])
        if (
            relation is None
            or relation.relation_type != PersistedRelationType.SUPERSEDES
            or relation.endpoint_ids is None
        ):
            raise ValueError("impact governing constraint basis is not an exact SUPERSEDES edge")
        changed_id, upstream_id = relation.endpoint_ids
        if changed_id not in changed_ids or upstream_id != current.target.target_id:
            raise ValueError("accepted governing relation is not changed-to-constraint-target")
        changed = claims[changed_id]
        upstream = claims[upstream_id]
        roots.append(
            AcceptedGoverningChange.create(
                constraint=current,
                original_subject_sha256=subject.subject_sha256,
                review_disposition=decision.disposition,
                relation=relation,
                changed_claim_revision=changed,
                upstream_claim_revision=upstream,
                changed_temporal_resolution=temporal_context.resolve_claim(changed),
                upstream_temporal_resolution=temporal_context.resolve_claim(upstream),
            )
        )
    canonical = tuple(sorted(roots, key=lambda item: item.governing_change_id))
    if len({item.governing_change_id for item in canonical}) != len(canonical):
        raise ValueError("review produced duplicate governing impact roots")
    return canonical


def _attention_paths_by_document(
    ranking: DocumentAttentionRanking,
) -> dict[str, tuple[AttentionPath, ...]]:
    context: dict[str, tuple[AttentionPath, ...]] = {}
    for candidate in ranking.attention_candidates:
        context[candidate.document_version_id] = candidate.paths
    for target in ranking.excluded_targets:
        if target.document_version_id in context:
            raise ValueError("attention ranking repeats a target across selected and excluded sets")
        context[target.document_version_id] = target.paths
    return context


def _mechanically_relevant_claim_ids(
    *,
    changed_ids: tuple[str, ...],
    governing: tuple[AcceptedGoverningChange, ...],
    candidates: RelationshipCandidateSet,
    attention: DocumentAttentionRanking,
    shards: tuple[ImpactInferenceShard, ...],
) -> tuple[str, ...]:
    relevant = set(changed_ids)
    for root in governing:
        relevant.add(root.changed_claim_revision.claim_revision_id)
        relevant.add(root.upstream_claim_revision.claim_revision_id)
    for candidate in candidates.candidates:
        relevant.update(candidate.claim_revision_ids)
    for target in attention.attention_candidates:
        for path in target.paths:
            relevant.add(path.changed_claim_revision_id)
            relevant.add(path.anchor_claim_revision_id)
            for relation_step in path.relation_steps:
                relevant.update(relation_step.canonical_endpoint_ids)
            for dependency_step in path.dependency_steps:
                relevant.add(dependency_step.upstream_claim_revision_id)
                relevant.update(dependency_step.exposed_downstream_claim_revision_ids)
    for excluded_target in attention.excluded_targets:
        for path in excluded_target.paths:
            relevant.add(path.changed_claim_revision_id)
            relevant.add(path.anchor_claim_revision_id)
            for excluded_relation_step in path.relation_steps:
                relevant.update(excluded_relation_step.canonical_endpoint_ids)
            for excluded_dependency_step in path.dependency_steps:
                relevant.add(excluded_dependency_step.upstream_claim_revision_id)
                relevant.update(excluded_dependency_step.exposed_downstream_claim_revision_ids)
    for shard in shards:
        relevant.update(item.claim_revision_id for item in shard.target_claim_revisions)
        for question in shard.questions:
            for dependency in question.existing_dependencies:
                relevant.add(dependency.upstream.claim_revision_id)
                relevant.update(
                    item.claim_revision_id for item in dependency.downstream_claim_revisions
                )
    return tuple(sorted(relevant))


def _preflight_impact_inference(
    *,
    drafts_by_document: dict[str, list[_ImpactQuestionDraft]],
    notes: dict[str, CanonicalSourceNoteSnapshot],
    claims_by_document: dict[str, list[VersionedClaimRevision]],
) -> dict[str, int]:
    """Enforce every inference-input budget before minting question/shard IDs."""

    selected_pair_count = sum(len(items) for items in drafts_by_document.values())
    if selected_pair_count > MAX_IMPACT_QUESTIONS_V1:
        raise ImpactAnalysisLimitError(
            category="selected-questions",
            limit=MAX_IMPACT_QUESTIONS_V1,
            observed=selected_pair_count,
        )
    selected_document_ids = tuple(sorted(drafts_by_document))
    if len(selected_document_ids) > MAX_IMPACT_DOCUMENT_SHARDS_V1:
        raise ImpactAnalysisLimitError(
            category="document-input-shards",
            limit=MAX_IMPACT_DOCUMENT_SHARDS_V1,
            observed=len(selected_document_ids),
        )
    logical_document_ids = tuple(
        drafts_by_document[document_id][0].target_document.document_id
        for document_id in selected_document_ids
    )
    if len(logical_document_ids) != len(set(logical_document_ids)):
        raise ValueError("eligible current targets contain duplicate logical document IDs")

    projected_bytes: dict[str, int] = {}
    for document_id in selected_document_ids:
        drafts = tuple(
            sorted(
                drafts_by_document[document_id],
                key=lambda item: item.governing_change.governing_change_id,
            )
        )
        claims = tuple(
            sorted(
                claims_by_document.get(document_id, ()),
                key=lambda item: item.claim_revision_id,
            )
        )
        payload = _impact_shard_payload(
            target_note=notes[document_id],
            target_claim_revisions=claims,
            target_temporal_resolution=drafts[0].target_temporal_resolution,
            question_json=tuple(item.projected_model_json() for item in drafts),
        )
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1:
            raise ImpactAnalysisLimitError(
                category="complete-document-input-bytes",
                limit=MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1,
                observed=observed,
            )
        projected_bytes[document_id] = observed
    total_bytes = sum(projected_bytes.values())
    if total_bytes > MAX_IMPACT_TOTAL_INPUT_BYTES_V1:
        raise ImpactAnalysisLimitError(
            category="total-input-bytes",
            limit=MAX_IMPACT_TOTAL_INPUT_BYTES_V1,
            observed=total_bytes,
        )
    return projected_bytes


def build_impact_workload(
    authority: change_control_types.ReviewedTemporalSnapshotAuthority,
) -> ImpactWorkload:
    """Build the exact closed-world workload from one sealed reviewed authority."""

    exact = _verify_authority(authority)
    snapshot = exact.snapshot
    aggregate = snapshot.aggregate
    bootstrap = exact.temporal_analysis.proposal.binding.analysis_bootstrap
    as_of = bootstrap.analysis_as_of
    changed_ids = bootstrap.changed_claim_revision_ids
    temporal_context = TemporalResolutionContext.from_aggregate(aggregate, as_of=as_of)
    governing = _derive_governing_changes(exact, temporal_context=temporal_context)
    inventory = exact.source_note_capability.verify(snapshot=snapshot)
    notes = {item.document.document_version_id: item for item in inventory.notes}
    documents = tuple(
        sorted(aggregate.documents.documents, key=lambda item: item.document_version_id)
    )
    if set(notes) != {item.document_version_id for item in documents}:
        raise ValueError(
            "reviewed SourceNote inventory does not cover the complete document universe"
        )

    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=changed_ids,
        as_of=as_of,
    )
    attention = rank_document_attention(snapshot, candidates=candidates)
    attention_by_document = _attention_paths_by_document(attention)
    claims_by_document: dict[str, list[VersionedClaimRevision]] = {}
    for claim in aggregate.claims.revisions:
        claims_by_document.setdefault(claim.document.document_version_id, []).append(claim)
    changed_document_ids = {
        aggregate.claims.get(claim_id).document.document_version_id for claim_id in changed_ids
    }

    drafts_by_document: dict[str, list[_ImpactQuestionDraft]] = {}
    exclusions: list[ExcludedImpactQuestion] = []
    for root in governing:
        for document in documents:
            resolution = temporal_context.resolve_document(document)
            reasons: list[ImpactExclusionReason] = []
            if document.document_version_id in changed_document_ids:
                reasons.append(ImpactExclusionReason.CHANGED_DOCUMENT)
            if (
                document.document_version_id
                == root.upstream_claim_revision.document.document_version_id
            ):
                reasons.append(ImpactExclusionReason.GOVERNING_UPSTREAM_DOCUMENT)
            if resolution.state != TemporalState.CURRENT:
                reasons.append(ImpactExclusionReason(resolution.state.value))
            if reasons:
                exclusions.append(
                    ExcludedImpactQuestion.create(
                        governing_change=root,
                        target_document=document,
                        target_temporal_resolution=resolution,
                        reasons=tuple(reasons),
                    )
                )
                continue

            paths = attention_by_document.get(document.document_version_id, ())
            relevant_paths = tuple(path for path in paths if _path_is_root_relevant(path, root))
            status = _root_attention_status(relevant_paths)
            governing_claim_ids = {
                root.changed_claim_revision.claim_revision_id,
                root.upstream_claim_revision.claim_revision_id,
            }
            dependencies = tuple(
                item
                for item in aggregate.dependencies.assessments
                if item.downstream == document
                and item.upstream.claim_revision_id in governing_claim_ids
            )
            drafts_by_document.setdefault(document.document_version_id, []).append(
                _ImpactQuestionDraft.create(
                    governing_change=root,
                    target_document=document,
                    target_temporal_resolution=resolution,
                    attention_status=status,
                    attention_paths=relevant_paths,
                    existing_dependencies=dependencies,
                )
            )

    projected_shard_bytes = _preflight_impact_inference(
        drafts_by_document=drafts_by_document,
        notes=notes,
        claims_by_document=claims_by_document,
    )

    shards = tuple(
        ImpactInferenceShard.create(
            target_note=notes[document_id],
            target_claim_revisions=tuple(claims_by_document.get(document_id, ())),
            target_temporal_resolution=drafts_by_document[document_id][
                0
            ].target_temporal_resolution,
            questions=tuple(
                draft.mint()
                for draft in sorted(
                    drafts_by_document[document_id],
                    key=lambda item: item.governing_change.governing_change_id,
                )
            ),
        )
        for document_id in sorted(drafts_by_document)
    )
    if any(
        len(shard.canonical_bytes())
        != projected_shard_bytes[shard.target_note.document.document_version_id]
        for shard in shards
    ):
        raise RuntimeError("impact shard identity projection changed during minting")
    total_input_bytes = sum(len(item.canonical_bytes()) for item in shards)
    if total_input_bytes > MAX_IMPACT_TOTAL_INPUT_BYTES_V1:
        raise ImpactAnalysisLimitError(
            category="total-input-bytes",
            limit=MAX_IMPACT_TOTAL_INPUT_BYTES_V1,
            observed=total_input_bytes,
        )
    canonical_exclusions = tuple(
        sorted(
            exclusions,
            key=lambda item: (
                item.governing_change.governing_change_id,
                item.target_document.document_version_id,
            ),
        )
    )
    binding_relevant_claim_ids = _mechanically_relevant_claim_ids(
        changed_ids=changed_ids,
        governing=governing,
        candidates=candidates,
        attention=attention,
        shards=shards,
    )
    binding_values: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "reviewed-impact-workload-v1",
        "reviewed_snapshot_binding": exact.binding.model_dump(mode="json"),
        "temporal_analysis_manifest_id": exact.binding.temporal_analysis_manifest_id,
        "temporal_analysis_manifest_sha256": (exact.binding.temporal_analysis_manifest_sha256),
        "review_request_id": exact.binding.review_request_id,
        "review_decision_payload_sha256": (exact.binding.review_decision_payload_sha256),
        "aggregate_id": aggregate.aggregate_id,
        "snapshot_revision": snapshot.revision,
        "aggregate_sha256": snapshot.aggregate_sha256,
        "analysis_as_of": as_of.isoformat(),
        "source_note_inventory_sha256": inventory.inventory_sha256,
        "changed_claim_revision_ids": list(changed_ids),
        "governing_changes": [item.model_dump(mode="json") for item in governing],
        "document_versions": [item.model_dump(mode="json") for item in documents],
        "relationship_candidate_result_sha256": candidates.result_sha256,
        "attention_result_sha256": attention.result_sha256,
        "mechanically_relevant_claim_revision_ids": list(binding_relevant_claim_ids),
    }
    binding_payload = {
        "namespace": "mastervault.reviewed-impact-workload-binding.v1",
        **binding_values,
    }
    binding_digest = _sha256(binding_payload)
    binding = ImpactWorkloadBinding.model_validate_json(
        canonical_json_bytes(
            {
                **binding_values,
                "binding_id": f"impactbinding:{binding_digest}",
                "binding_sha256": binding_digest,
            }
        )
    )
    question_refs = tuple(
        sorted(
            (
                ImpactQuestionRef(
                    document_version_id=shard.target_note.document.document_version_id,
                    governing_change_id=question.governing_change.governing_change_id,
                    question_id=question.question_id,
                    question_sha256=question.question_sha256,
                    input_shard_id=shard.shard_id,
                    input_shard_sha256=shard.shard_sha256,
                )
                for shard in shards
                for question in shard.questions
            ),
            key=lambda item: (item.governing_change_id, item.document_version_id),
        )
    )
    exclusion_refs = tuple(
        ImpactExclusionRef(
            document_version_id=item.target_document.document_version_id,
            governing_change_id=item.governing_change.governing_change_id,
            exclusion_id=item.exclusion_id,
            exclusion_sha256=item.exclusion_sha256,
            reasons=item.reasons,
        )
        for item in canonical_exclusions
    )
    index_values: dict[str, Any] = {
        "binding": binding.model_dump(mode="json"),
        "question_refs": [item.model_dump(mode="json") for item in question_refs],
        "exclusion_refs": [item.model_dump(mode="json") for item in exclusion_refs],
    }
    index_payload = {
        "namespace": "mastervault.reviewed-impact-workload-index.v1",
        "schema_version": 1,
        **index_values,
    }
    index_bytes = canonical_json_bytes(index_payload)
    if len(index_bytes) > MAX_IMPACT_INDEX_CANONICAL_BYTES_V1:
        raise ImpactAnalysisLimitError(
            category="workload-index-bytes",
            limit=MAX_IMPACT_INDEX_CANONICAL_BYTES_V1,
            observed=len(index_bytes),
        )
    index_digest = _sha256(index_payload)
    return ImpactWorkload(
        index=ImpactWorkloadIndex.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **index_values,
                    "workload_id": f"impactwork:{index_digest}",
                    "workload_sha256": index_digest,
                }
            )
        ),
        input_shards=shards,
        exclusions=canonical_exclusions,
    )


def validate_impact_workload(
    authority: change_control_types.ReviewedTemporalSnapshotAuthority,
    workload: ImpactWorkload,
) -> ImpactWorkload:
    """Reopen and exactly regenerate a workload from its sealed authority."""

    _verify_authority(authority)
    validated = ImpactWorkload.model_validate_json(
        canonical_json_bytes(workload.model_dump(mode="json"))
    )
    expected = build_impact_workload(authority)
    if validated != expected:
        raise ValueError("impact workload differs from its complete authoritative derivation")
    return validated


__all__ = [
    "MAX_IMPACT_DOCUMENT_SHARDS_V1",
    "MAX_IMPACT_INDEX_CANONICAL_BYTES_V1",
    "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_IMPACT_QUESTIONS_V1",
    "MAX_IMPACT_TOTAL_INPUT_BYTES_V1",
    "ExcludedImpactQuestion",
    "AcceptedGoverningChange",
    "ImpactAnalysisLimitError",
    "ImpactAttentionStatus",
    "ImpactExclusionReason",
    "ImpactExclusionRef",
    "ImpactInferenceShard",
    "ImpactQuestion",
    "ImpactQuestionRef",
    "ImpactWorkload",
    "ImpactWorkloadBinding",
    "ImpactWorkloadIndex",
    "build_impact_workload",
    "validate_impact_workload",
]
