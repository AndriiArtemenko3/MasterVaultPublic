"""Dependency-neutral identity contract for the sealed analysis bootstrap."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.claim_scopes import CLAIM_SCOPE_POLICY_VERSION
from mastervault.change_control.models import (
    DocumentAuthority,
    DocumentRole,
    canonical_json_bytes,
    normalize_logical_key,
)
from mastervault.models import Domain, SourceType

ANALYSIS_AGGREGATE_ID: Final = "larkstead.sl2-returns"
ALIGNMENT_ATTESTATION_ID: Final = "sl2-returns-v2-alignment-v1"
PINNED_ALIGNMENT_ATTESTATION_SHA256: Final = (
    "0de6f81a8f21285c1be020b09a322ea3f2c1ebd62d554fcdec8752dc70adc359"
)
ALIGNMENT_POLICY_VERSION: Final = "fixture-reviewed-extractive-alignment-v1"
MAX_INCOMING_CLAIMS: Final = 10

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CONTENT_ID_PATTERN = r"^[a-z]+:[0-9a-f]{64}$"
_BINDING_ID_PATTERN = r"^analysis-bootstrap:[0-9a-f]{64}$"
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class AnalysisBootstrapError(RuntimeError):
    """Base failure for the deterministic analysis bootstrap."""


class AnalysisBootstrapIntegrityError(AnalysisBootstrapError):
    """Verified inputs or a persisted bootstrap snapshot are not exact."""


class AnalysisBootstrapBinding(BaseModel):
    """Pure content identity for the exact revision-1 to revision-2 bootstrap.

    Repository-backed construction belongs to
    ``create_verified_analysis_bootstrap_binding`` in ``bootstrap``. Compare
    independent instances by their validated ``binding_id`` and
    ``binding_sha256`` rather than by Python subclass identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=_BINDING_ID_PATTERN)
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    scope_policy_version: Literal["claim-scopes-v1"] = CLAIM_SCOPE_POLICY_VERSION
    aggregate_id: Literal["larkstead.sl2-returns"] = ANALYSIS_AGGREGATE_ID
    seed_scenario_id: str
    seed_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    seed_as_of: date
    prechange_revision: Literal[1] = 1
    prechange_aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_event_id: str
    incoming_event_identity: str = Field(pattern=_CONTENT_ID_PATTERN)
    incoming_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    alignment_attestation_id: Literal["sl2-returns-v2-alignment-v1"] = ALIGNMENT_ATTESTATION_ID
    alignment_attestation_sha256: Literal[
        "0de6f81a8f21285c1be020b09a322ea3f2c1ebd62d554fcdec8752dc70adc359"
    ] = PINNED_ALIGNMENT_ATTESTATION_SHA256
    alignment_policy_version: Literal["fixture-reviewed-extractive-alignment-v1"] = (
        ALIGNMENT_POLICY_VERSION
    )
    alignment_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_claim_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_document_id: str
    incoming_document_version_id: str = Field(pattern=_CONTENT_ID_PATTERN)
    analysis_revision: Literal[2] = 2
    analysis_aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_as_of: date
    changed_claim_revision_ids: tuple[str, ...] = Field(
        min_length=MAX_INCOMING_CLAIMS,
        max_length=MAX_INCOMING_CLAIMS,
    )
    prechange_operation_id: str
    analysis_operation_id: str
    canonical_input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("seed_scenario_id", "incoming_event_id", "incoming_document_id")
    @classmethod
    def _logical_keys(cls, value: str) -> str:
        normalized = normalize_logical_key(value)
        if value != normalized:
            raise ValueError(f"bootstrap logical key must already be normalized as {normalized!r}")
        return value

    @field_validator("prechange_operation_id", "analysis_operation_id")
    @classmethod
    def _operation_ids(cls, value: str) -> str:
        if _OPERATION_ID_RE.fullmatch(value) is None:
            raise ValueError("bootstrap operation ID uses an unsafe or unsupported shape")
        return value

    @field_validator("changed_claim_revision_ids")
    @classmethod
    def _changed_claim_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("changed claim revision IDs must be canonically ordered and unique")
        if any(re.fullmatch(r"claimrev:[0-9a-f]{64}", value) is None for value in values):
            raise ValueError("changed claim revision ID has the wrong content-ID shape")
        return values

    def _canonical_input_payload(self) -> dict[str, Any]:
        return {
            "aggregate_id": self.aggregate_id,
            "analysis_as_of": self.analysis_as_of.isoformat(),
            "analysis_operation_id": self.analysis_operation_id,
            "alignment_attestation_id": self.alignment_attestation_id,
            "alignment_attestation_sha256": self.alignment_attestation_sha256,
            "alignment_payload_sha256": self.alignment_payload_sha256,
            "alignment_policy_version": self.alignment_policy_version,
            "incoming_document_id": self.incoming_document_id,
            "incoming_document_version_id": self.incoming_document_version_id,
            "incoming_claim_evidence_sha256": self.incoming_claim_evidence_sha256,
            "incoming_event_id": self.incoming_event_id,
            "incoming_event_identity": self.incoming_event_identity,
            "incoming_manifest_sha256": self.incoming_manifest_sha256,
            "prechange_operation_id": self.prechange_operation_id,
            "schema_version": self.schema_version,
            "scope_policy_version": self.scope_policy_version,
            "seed_as_of": self.seed_as_of.isoformat(),
            "seed_manifest_sha256": self.seed_manifest_sha256,
            "seed_scenario_id": self.seed_scenario_id,
        }

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.analysis_as_of <= self.seed_as_of:
            raise ValueError("analysis as-of date must follow the pre-change seed date")
        if self.prechange_operation_id == self.analysis_operation_id:
            raise ValueError("bootstrap phases require distinct operation IDs")
        if self.canonical_input_sha256 != _sha256(self._canonical_input_payload()):
            raise ValueError("bootstrap canonical input SHA does not match its sealed inputs")
        digest = _sha256(self._identity_payload())
        if self.binding_sha256 != digest or self.binding_id != f"analysis-bootstrap:{digest}":
            raise ValueError("analysis bootstrap binding ID/SHA does not match its content")
        return self


class GenericAnalysisBootstrapBindingV2(BaseModel):
    """Path-free authority binding for a generic workspace revision-1 to revision-2 CAS."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    binding_kind: Literal["generic-analysis-bootstrap-v2"] = "generic-analysis-bootstrap-v2"
    binding_id: str = Field(pattern=r"^generic-analysis-bootstrap-v2:[0-9a-f]{64}$")
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    scope_policy_version: Literal["claim-scopes-v1"] = CLAIM_SCOPE_POLICY_VERSION
    aggregate_id: str
    workspace_bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    workspace_intent_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_inventory_id: str = Field(pattern=r"^workspaceinventory:[0-9a-f]{64}$")
    workspace_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_inventory_receipt_id: str = Field(pattern=r"^workspaceinventoryreceipt:[0-9a-f]{64}$")
    workspace_inventory_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_readiness_receipt_id: str = Field(pattern=r"^legacyindexreceipt:[0-9a-f]{64}$")
    workspace_readiness_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    prechange_revision: Literal[1] = 1
    prechange_aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_event_id: str
    incoming_event_identity: str = Field(pattern=_CONTENT_ID_PATTERN)
    incoming_bundle_id: str = Field(pattern=r"^generic-bundle-v2:[0-9a-f]{64}$")
    incoming_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_source_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_inference_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_claim_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_document_id: str
    incoming_document_version_id: str = Field(pattern=_CONTENT_ID_PATTERN)
    incoming_document_family: str
    incoming_version_label: str
    incoming_title_sha256: str = Field(pattern=_SHA256_PATTERN)
    incoming_operator_intent_sha256: str = Field(pattern=_SHA256_PATTERN)
    domain: Domain
    source_type: SourceType
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority
    analysis_as_of: date
    analysis_revision: Literal[2] = 2
    analysis_aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    changed_claim_revision_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    analysis_operation_seed_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_operation_id: str
    canonical_input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "aggregate_id",
        "incoming_event_id",
        "incoming_document_id",
        "incoming_document_family",
        "incoming_version_label",
    )
    @classmethod
    def _generic_logical_keys(cls, value: str) -> str:
        normalized = normalize_logical_key(value)
        if value != normalized:
            raise ValueError(f"generic bootstrap key must already be normalized as {normalized!r}")
        return value

    @field_validator("analysis_operation_id")
    @classmethod
    def _generic_operation_id(cls, value: str) -> str:
        if _OPERATION_ID_RE.fullmatch(value) is None:
            raise ValueError("generic bootstrap operation ID uses an unsafe shape")
        return value

    @field_validator("changed_claim_revision_ids")
    @classmethod
    def _generic_changed_claims(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))) or any(
            re.fullmatch(r"claimrev:[0-9a-f]{64}", value) is None for value in values
        ):
            raise ValueError("generic changed claims must be canonical unique claim revisions")
        return values

    def _canonical_input_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "analysis_aggregate_sha256",
                "analysis_revision",
                "binding_id",
                "binding_sha256",
                "canonical_input_sha256",
                "changed_claim_revision_ids",
                "prechange_revision",
            },
        )

    def _operation_identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.generic-analysis-operation.v2",
            "analysis_operation_seed_sha256": self.analysis_operation_seed_sha256,
            "aggregate_id": self.aggregate_id,
            "workspace_bootstrap_id": self.workspace_bootstrap_id,
            "workspace_intent_sha256": self.workspace_intent_sha256,
            "workspace_inventory_id": self.workspace_inventory_id,
            "workspace_inventory_sha256": self.workspace_inventory_sha256,
            "workspace_inventory_receipt_id": self.workspace_inventory_receipt_id,
            "workspace_inventory_receipt_sha256": self.workspace_inventory_receipt_sha256,
            "workspace_readiness_receipt_id": self.workspace_readiness_receipt_id,
            "workspace_readiness_receipt_sha256": self.workspace_readiness_receipt_sha256,
            "prechange_aggregate_sha256": self.prechange_aggregate_sha256,
            "incoming_bundle_id": self.incoming_bundle_id,
            "incoming_bundle_sha256": self.incoming_bundle_sha256,
            "incoming_admission_sha256": self.incoming_admission_sha256,
            "incoming_metadata_sha256": self.incoming_metadata_sha256,
            "incoming_source_receipt_sha256": self.incoming_source_receipt_sha256,
            "incoming_projection_sha256": self.incoming_projection_sha256,
            "incoming_inference_sha256": self.incoming_inference_sha256,
            "incoming_claim_evidence_sha256": self.incoming_claim_evidence_sha256,
        }

    @model_validator(mode="after")
    def _generic_identity(self) -> Self:
        if self.declared_effective_to is not None and (
            self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("generic effective interval is not increasing")
        if self.analysis_as_of != self.declared_effective_from:
            raise ValueError("generic analysis as-of must equal declared effective-from")
        if self.incoming_bundle_id != f"generic-bundle-v2:{self.incoming_bundle_sha256}":
            raise ValueError("generic bundle ID differs from its SHA")
        expected_operation_id = f"generic-analysis-v2:{_sha256(self._operation_identity_payload())}"
        if self.analysis_operation_id != expected_operation_id:
            raise ValueError("generic analysis operation ID differs from exact input identity")
        if self.canonical_input_sha256 != _sha256(self._canonical_input_payload()):
            raise ValueError("generic bootstrap canonical input SHA differs from exact inputs")
        digest = _sha256(self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"}))
        if (
            self.binding_sha256 != digest
            or self.binding_id != f"generic-analysis-bootstrap-v2:{digest}"
        ):
            raise ValueError("generic bootstrap binding identity differs from exact content")
        return self


AnalysisBootstrapAuthority = Annotated[
    AnalysisBootstrapBinding | GenericAnalysisBootstrapBindingV2,
    Field(discriminator="schema_version"),
]


__all__ = [
    "ANALYSIS_AGGREGATE_ID",
    "AnalysisBootstrapBinding",
    "AnalysisBootstrapError",
    "AnalysisBootstrapIntegrityError",
    "AnalysisBootstrapAuthority",
    "GenericAnalysisBootstrapBindingV2",
]
