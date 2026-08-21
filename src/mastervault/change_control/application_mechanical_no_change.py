"""Immutable authority for a classification-proven mechanically empty change."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.analysis_binding import GenericAnalysisBootstrapBindingV2
from mastervault.change_control.classification import ClassificationResultSet
from mastervault.change_control.dependency_analysis import derive_governing_supersessions
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.managed_review import ManagedInferenceContractBinding
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.regression_baseline import GenerationZeroBaselineReceiptV1
from mastervault.change_control.synchronous_lifecycle_store_models import (
    IncomingAdmissionRecordV1,
    RegressionSuiteAdmissionRecordV1,
)

_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024


class MechanicalNoChangeEvidenceError(ValueError):
    """Mechanical no-change evidence is unavailable, conflicting, or corrupt."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("timestamp must be canonical UTC with second precision")
    return value


class MechanicalNoChangeEvidenceV1(_StrictFrozenModel):
    """Canonical receipt proving that complete classification found no change."""

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^mechanical-no-change:[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    base_authority_id: str = Field(pattern=r"^mauthority:[0-9a-f]{64}$")
    base_authority_revision: Literal[0] = 0
    base_active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    generic_analysis: GenericAnalysisBootstrapBindingV2
    incoming_admission: IncomingAdmissionRecordV1
    suite_admission: RegressionSuiteAdmissionRecordV1
    baseline_receipt: GenerationZeroBaselineReceiptV1
    classification_contract: ManagedInferenceContractBinding
    classification_batch_id: str = Field(pattern=r"^inference-batch:[0-9a-f]{64}$")
    classification_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_results: ClassificationResultSet
    reason: Literal["complete-classification-no-governing-supersession"]
    completed_at: str

    @field_validator("completed_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _identity_and_lineage(self) -> Self:
        analysis = self.generic_analysis
        incoming = self.incoming_admission
        suite = self.suite_admission
        baseline = self.baseline_receipt
        results = self.classification_results
        if self.classification_batch_id != f"inference-batch:{self.classification_batch_sha256}":
            raise ValueError("classification batch ID differs from its SHA")
        if derive_governing_supersessions(results):
            raise ValueError("mechanical no-change cannot contain a governing supersession")
        if not (
            results.workload.aggregate_id == analysis.aggregate_id
            and results.workload.snapshot_revision == analysis.analysis_revision
            and results.workload.aggregate_sha256 == analysis.analysis_aggregate_sha256
            and incoming.intent.run_id == self.run_id
            and incoming.intent.bundle_id == analysis.incoming_bundle_id
            and incoming.intent.bundle_sha256 == analysis.incoming_bundle_sha256
            and incoming.intent.admission_sha256 == analysis.incoming_admission_sha256
            and incoming.intent.source_receipt_sha256 == analysis.incoming_source_receipt_sha256
            and incoming.intent.projection_sha256 == analysis.incoming_projection_sha256
            and incoming.intent.inference_sha256 == analysis.incoming_inference_sha256
            and suite.intent.run_id == self.run_id
            and baseline.authority.run_id == self.run_id
            and baseline.authority.incoming_admission_receipt_id == incoming.receipt_id
            and baseline.authority.incoming_admission_receipt_sha256 == incoming.receipt_sha256
            and baseline.authority.workspace_inventory_receipt_id
            == analysis.workspace_inventory_receipt_id
            and baseline.authority.workspace_inventory_receipt_sha256
            == analysis.workspace_inventory_receipt_sha256
            and baseline.authority.legacy_readiness_receipt_id
            == analysis.workspace_readiness_receipt_id
            and baseline.authority.legacy_readiness_receipt_sha256
            == analysis.workspace_readiness_receipt_sha256
            and baseline.suite_id == suite.intent.suite_id
            and baseline.suite_version == suite.intent.suite_version
            and baseline.suite_original_sha256 == suite.intent.original_sha256
            and baseline.suite_original_byte_count == suite.intent.original_byte_count
            and baseline.suite_canonical_sha256 == suite.intent.canonical_sha256
        ):
            raise ValueError("mechanical no-change evidence has inconsistent authority lineage")
        payload = self.model_dump(mode="json", exclude={"evidence_id", "evidence_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.evidence_sha256 != digest or self.evidence_id != (f"mechanical-no-change:{digest}"):
            raise ValueError("mechanical no-change identity differs from its exact payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        payload = {"schema_version": 1, **values}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in payload.items()
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "evidence_id": f"mechanical-no-change:{digest}",
                    "evidence_sha256": digest,
                    **payload,
                }
            )
        )

    @property
    def semantic_key(self) -> tuple[Any, ...]:
        return tuple(
            self.model_dump(
                mode="json", exclude={"evidence_id", "evidence_sha256", "completed_at"}
            ).items()
        )


class MechanicalNoChangeEvidenceRepository:
    """Descriptor-safe, create-only owner for one terminal receipt per run."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        self._backend = FilesystemInferenceEvidenceRepository(
            root, create=create, read_only=read_only
        )

    @staticmethod
    def relative_locator(run_id: str) -> str:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("mechanical no-change run identity is invalid")
        run_name = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return f"application/mechanical-no-change/{run_name}/COMPLETE.json"

    def _read(self, run_id: str) -> MechanicalNoChangeEvidenceV1 | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            self.relative_locator(run_id),
            limit=_MAX_RECEIPT_BYTES,
            label="mechanical no-change receipt",
        )
        if payload is None:
            return None
        try:
            receipt = MechanicalNoChangeEvidenceV1.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt is invalid"
            ) from exc
        if canonical_json_bytes(receipt.model_dump(mode="json")) != payload:
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt is not exact canonical JSON"
            )
        return receipt

    def reopen_optional(self, run_id: str) -> MechanicalNoChangeEvidenceV1 | None:
        try:
            with self._backend._read_lock():  # noqa: SLF001
                receipt = self._read(run_id)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, MechanicalNoChangeEvidenceError):
                raise
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt cannot be reopened"
            ) from exc
        if receipt is not None and receipt.run_id != run_id:
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt belongs to another run"
            )
        return receipt

    def reopen(
        self, *, run_id: str, evidence_id: str, evidence_sha256: str
    ) -> MechanicalNoChangeEvidenceV1:
        receipt = self.reopen_optional(run_id)
        if receipt is None or not (
            receipt.evidence_id == evidence_id and receipt.evidence_sha256 == evidence_sha256
        ):
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt does not match its navigation link"
            )
        return receipt

    def persist(self, value: MechanicalNoChangeEvidenceV1) -> MechanicalNoChangeEvidenceV1:
        if type(value) is not MechanicalNoChangeEvidenceV1:
            raise TypeError("mechanical no-change repository requires its exact frozen receipt")
        payload = canonical_json_bytes(value.model_dump(mode="json"))
        if len(payload) > _MAX_RECEIPT_BYTES:
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt exceeds its fixed limit"
            )
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing = self._read(value.run_id)
                if existing is not None:
                    if existing.semantic_key != value.semantic_key:
                        raise MechanicalNoChangeEvidenceError(
                            "mechanical no-change run is bound to different immutable inputs"
                        )
                    return existing
                self._backend._create_only(  # noqa: SLF001
                    self.relative_locator(value.run_id),
                    payload,
                    label="mechanical no-change receipt",
                )
                reopened = self._read(value.run_id)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, MechanicalNoChangeEvidenceError):
                raise
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt persistence failed"
            ) from exc
        if reopened != value:
            raise MechanicalNoChangeEvidenceError(
                "mechanical no-change receipt did not reopen exactly"
            )
        return reopened


__all__ = [
    "MechanicalNoChangeEvidenceError",
    "MechanicalNoChangeEvidenceRepository",
    "MechanicalNoChangeEvidenceV1",
]
