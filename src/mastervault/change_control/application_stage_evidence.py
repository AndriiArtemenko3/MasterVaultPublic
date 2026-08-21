"""Independent owner receipts for impact and revision-planning navigation."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.impact_results import ImpactResultSet
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.managed_review import ManagedImpactAnalysisEvidenceBinding
from mastervault.change_control.managed_revision_admission import (
    ManagedRevisionPlanningAdmissionBinding,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes

_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024


class ApplicationStageEvidenceError(ValueError):
    """Application stage evidence is absent, conflicting, or corrupt."""


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


class ImpactStageEvidenceV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^impact-stage:[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    reviewed_snapshot_binding_id: str = Field(pattern=r"^reviewed-snapshot:[0-9a-f]{64}$")
    reviewed_snapshot_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    results: ImpactResultSet
    binding: ManagedImpactAnalysisEvidenceBinding
    recorded_at: str

    _time = field_validator("recorded_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        result = self.results.result_index
        if not (
            self.results.workload.index.workload_id == self.binding.workload_id
            and self.results.workload.index.workload_sha256 == self.binding.workload_sha256
            and result.result_id == self.binding.result_id
            and result.result_sha256 == self.binding.result_sha256
        ):
            raise ValueError("impact stage binding differs from its exact result set")
        payload = self.model_dump(mode="json", exclude={"evidence_id", "evidence_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.evidence_sha256 != digest or self.evidence_id != f"impact-stage:{digest}":
            raise ValueError("impact stage evidence ID/SHA differs from exact payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        return _create(cls, "impact-stage", values)


class PlanningStageEvidenceV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^planning-stage:[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    temporal_analysis_manifest_id: str = Field(pattern=r"^temporal-analysis:[0-9a-f]{64}$")
    temporal_analysis_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_request_id: str = Field(pattern=r"^reviewreq:[0-9a-f]{64}$")
    binding: ManagedRevisionPlanningAdmissionBinding
    recorded_at: str

    _time = field_validator("recorded_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if not (
            self.temporal_analysis_manifest_id
            == f"temporal-analysis:{self.temporal_analysis_manifest_sha256}"
            and self.binding.run_id == self.run_id
            and self.binding.temporal_decision_record_sha256
        ):
            raise ValueError("planning stage evidence differs from exact upstream identity")
        payload = self.model_dump(mode="json", exclude={"evidence_id", "evidence_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.evidence_sha256 != digest or self.evidence_id != f"planning-stage:{digest}":
            raise ValueError("planning stage evidence ID/SHA differs from exact payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        return _create(cls, "planning-stage", values)


def _create[ModelT: BaseModel](model: type[ModelT], prefix: str, values: dict[str, Any]) -> ModelT:
    payload = {"schema_version": 1, **values}
    payload = {
        key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        for key, value in payload.items()
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return model.model_validate_json(
        canonical_json_bytes(
            {
                "evidence_id": f"{prefix}:{digest}",
                "evidence_sha256": digest,
                **payload,
            }
        )
    )


class ApplicationStageEvidenceRepository:
    """Descriptor-safe create-only receipt repository with exact retry."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        self._backend = FilesystemInferenceEvidenceRepository(
            root, create=create, read_only=read_only
        )

    @staticmethod
    def relative_locator(run_id: str, stage: Literal["impact", "planning"]) -> str:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("stage evidence run_id is invalid")
        name = hashlib.sha256(run_id.encode()).hexdigest()
        return f"application/stage-evidence/{name}/{stage}.json"

    def _read[ModelT: BaseModel](
        self,
        run_id: str,
        stage: Literal["impact", "planning"],
        model: type[ModelT],
    ) -> ModelT | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            self.relative_locator(run_id, stage),
            limit=_MAX_RECEIPT_BYTES,
            label=f"application {stage} evidence",
        )
        if payload is None:
            return None
        try:
            receipt = model.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise ApplicationStageEvidenceError(f"application {stage} evidence is invalid") from exc
        if canonical_json_bytes(receipt.model_dump(mode="json")) != payload:
            raise ApplicationStageEvidenceError(
                f"application {stage} evidence is not canonical"
            )
        return receipt

    def reopen_impact(self, run_id: str) -> ImpactStageEvidenceV1:
        return self._reopen(run_id, "impact", ImpactStageEvidenceV1)

    def reopen_planning(self, run_id: str) -> PlanningStageEvidenceV1:
        return self._reopen(run_id, "planning", PlanningStageEvidenceV1)

    def _reopen[ModelT: BaseModel](
        self,
        run_id: str,
        stage: Literal["impact", "planning"],
        model: type[ModelT],
    ) -> ModelT:
        try:
            with self._backend._read_lock():  # noqa: SLF001
                receipt = self._read(run_id, stage, model)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStageEvidenceError):
                raise
            raise ApplicationStageEvidenceError(
                f"application {stage} evidence cannot be reopened"
            ) from exc
        if receipt is None:
            raise ApplicationStageEvidenceError(f"application {stage} evidence does not exist")
        return receipt

    def persist_impact(self, value: ImpactStageEvidenceV1) -> ImpactStageEvidenceV1:
        return self._persist(value.run_id, "impact", value, ImpactStageEvidenceV1)

    def persist_planning(self, value: PlanningStageEvidenceV1) -> PlanningStageEvidenceV1:
        return self._persist(value.run_id, "planning", value, PlanningStageEvidenceV1)

    def _persist[ModelT: BaseModel](
        self,
        run_id: str,
        stage: Literal["impact", "planning"],
        value: ModelT,
        model: type[ModelT],
    ) -> ModelT:
        if type(value) is not model:
            raise TypeError(f"application {stage} repository requires its exact receipt type")
        payload = canonical_json_bytes(value.model_dump(mode="json"))
        if len(payload) > _MAX_RECEIPT_BYTES:
            raise ApplicationStageEvidenceError(f"application {stage} evidence exceeds its limit")
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing = self._read(run_id, stage, model)
                if existing is not None:
                    semantic_exclusions = {
                        "evidence_id",
                        "evidence_sha256",
                        "recorded_at",
                    }
                    previous = existing.model_dump(
                        mode="json", exclude=semantic_exclusions
                    )
                    requested = value.model_dump(
                        mode="json", exclude=semantic_exclusions
                    )
                    if previous != requested:
                        raise ApplicationStageEvidenceError(
                            f"application {stage} evidence conflicts with exact retry"
                        )
                    return existing
                self._backend._create_only(  # noqa: SLF001
                    self.relative_locator(run_id, stage),
                    payload,
                    label=f"application {stage} evidence",
                )
                reopened = self._read(run_id, stage, model)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStageEvidenceError):
                raise
            raise ApplicationStageEvidenceError(
                f"application {stage} evidence persistence failed"
            ) from exc
        if reopened != value:
            raise ApplicationStageEvidenceError(
                f"application {stage} evidence did not reopen exactly"
            )
        return reopened


__all__ = [
    "ApplicationStageEvidenceError",
    "ApplicationStageEvidenceRepository",
    "ImpactStageEvidenceV1",
    "PlanningStageEvidenceV1",
]
