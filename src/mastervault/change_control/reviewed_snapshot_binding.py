"""Dependency-neutral serializable binding for reviewed temporal authority."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

import mastervault.change_control as change_control_types
from mastervault.change_control.dependency_analysis import SourceNoteInventory
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    TemporalDecisionPrerequisite,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes

_BINDING_ID_PATTERN = r"^reviewed-snapshot:[0-9a-f]{64}$"


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
    def _identity(self) -> Self:
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
        temporal_analysis: change_control_types.TemporalAnalysisEvidence,
        commit: change_control_types.TemporalProposalCommit,
        request: change_control_types.HumanReviewRequest,
        decision: change_control_types.HumanReviewDecision,
        prerequisite: TemporalDecisionPrerequisite,
        analysis_inventory: SourceNoteInventory,
        reviewed_inventory: SourceNoteInventory,
    ) -> Self:
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
            "temporal_decision_record_sha256": prerequisite.temporal_decision_record_sha256,
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


__all__ = ["ReviewedTemporalSnapshotBinding"]
