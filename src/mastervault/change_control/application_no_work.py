"""Independent owner receipt for a mechanically empty planning result."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.impact_analysis import ImpactInferenceShard
from mastervault.change_control.impact_results import ImpactOutputShard
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningEligibilityStatus,
    RevisionPlanningWorkload,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.revision_planning_inference import (
    derive_revision_planning_eligibility_from_impact_evidence,
)

_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024


class NoWorkPlanningEvidenceError(ValueError):
    """NO_WORK evidence could not be persisted, reopened, or reproduced."""


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


class NoWorkPlanningEvidenceV1(_StrictFrozenModel):
    """Canonical reproducible receipt that owns a NO_WORK planning link."""

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^no-work-planning:[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    reviewed_snapshot_binding_id: str = Field(pattern=r"^reviewed-snapshot:[0-9a-f]{64}$")
    reviewed_snapshot_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    impact_evidence_binding_id: str | None = Field(
        default=None, pattern=r"^mimpactevidence:[0-9a-f]{64}$"
    )
    impact_evidence_binding_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    impact_input_shards: tuple[ImpactInferenceShard, ...] = Field(max_length=4096)
    impact_output_shards: tuple[ImpactOutputShard, ...] = Field(max_length=4096)
    workload: RevisionPlanningWorkload
    recorded_at: str

    @field_validator("recorded_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _identity_and_reproduction(self) -> Self:
        eligibility = self.workload.eligibility
        if (self.impact_evidence_binding_id is None) != (
            self.impact_evidence_binding_sha256 is None
        ):
            raise ValueError("NO_WORK impact binding ID and SHA must appear together")
        if self.impact_evidence_binding_id is None and (
            self.impact_input_shards or self.impact_output_shards
        ):
            raise ValueError("unrecorded empty impact authority cannot carry inference shards")
        if eligibility.status != RevisionPlanningEligibilityStatus.NO_WORK:
            raise ValueError("NO_WORK receipt cannot carry eligible planning work")
        reproduced = derive_revision_planning_eligibility_from_impact_evidence(
            workload_id=eligibility.workload_id,
            workload_sha256=eligibility.workload_sha256,
            result_id=eligibility.result_id,
            result_sha256=eligibility.result_sha256,
            input_shards=self.impact_input_shards,
            output_shards=self.impact_output_shards,
        )
        expected = RevisionPlanningWorkload.create(eligibility=reproduced, input_shards=())
        if expected != self.workload:
            raise ValueError("NO_WORK workload does not reproduce from exact impact evidence")
        payload = self.model_dump(mode="json", exclude={"evidence_id", "evidence_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.evidence_sha256 != digest or self.evidence_id != f"no-work-planning:{digest}":
            raise ValueError("NO_WORK evidence identity differs from its exact payload")
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
                    "evidence_id": f"no-work-planning:{digest}",
                    "evidence_sha256": digest,
                    **payload,
                }
            )
        )

    @property
    def semantic_key(self) -> tuple[Any, ...]:
        return (
            self.run_id,
            self.reviewed_snapshot_binding_id,
            self.reviewed_snapshot_binding_sha256,
            self.impact_evidence_binding_id,
            self.impact_evidence_binding_sha256,
            self.configuration_sha256,
            self.impact_input_shards,
            self.impact_output_shards,
            self.workload,
        )


class NoWorkPlanningEvidenceRepository:
    """Descriptor-safe create-only owner of NO_WORK receipts."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        self._backend = FilesystemInferenceEvidenceRepository(
            root, create=create, read_only=read_only
        )

    @staticmethod
    def relative_locator(run_id: str, workload_id: str, workload_sha256: str) -> str:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("NO_WORK run identity is invalid")
        if workload_id != f"revisionwork:{workload_sha256}" or re.fullmatch(
            SHA256_PATTERN, workload_sha256
        ) is None:
            raise ValueError("NO_WORK workload identity is invalid")
        run_name = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return f"application/no-work-planning/{run_name}/{workload_sha256}.json"

    def _read(
        self, run_id: str, workload_id: str, workload_sha256: str
    ) -> NoWorkPlanningEvidenceV1 | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            self.relative_locator(run_id, workload_id, workload_sha256),
            limit=_MAX_RECEIPT_BYTES,
            label="NO_WORK planning receipt",
        )
        if payload is None:
            return None
        try:
            receipt = NoWorkPlanningEvidenceV1.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt is invalid") from exc
        if canonical_json_bytes(receipt.model_dump(mode="json")) != payload:
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt is not exact canonical JSON")
        return receipt

    def reopen(
        self, run_id: str, workload_id: str, workload_sha256: str
    ) -> NoWorkPlanningEvidenceV1:
        try:
            with self._backend._read_lock():  # noqa: SLF001
                result = self._read(run_id, workload_id, workload_sha256)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, NoWorkPlanningEvidenceError):
                raise
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt cannot be reopened") from exc
        if result is None or not (
            result.run_id == run_id
            and result.workload.workload_id == workload_id
            and result.workload.workload_sha256 == workload_sha256
        ):
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt does not exist")
        return result

    def persist(self, value: NoWorkPlanningEvidenceV1) -> NoWorkPlanningEvidenceV1:
        if type(value) is not NoWorkPlanningEvidenceV1:
            raise TypeError("NO_WORK repository requires its exact frozen receipt")
        payload = canonical_json_bytes(value.model_dump(mode="json"))
        if len(payload) > _MAX_RECEIPT_BYTES:
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt exceeds its fixed limit")
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing = self._read(
                    value.run_id,
                    value.workload.workload_id,
                    value.workload.workload_sha256,
                )
                if existing is not None:
                    if existing.semantic_key != value.semantic_key:
                        raise NoWorkPlanningEvidenceError(
                            "NO_WORK run is already bound to different immutable inputs"
                        )
                    return existing
                self._backend._create_only(  # noqa: SLF001
                    self.relative_locator(
                        value.run_id,
                        value.workload.workload_id,
                        value.workload.workload_sha256,
                    ),
                    payload,
                    label="NO_WORK planning receipt",
                )
                reopened = self._read(
                    value.run_id,
                    value.workload.workload_id,
                    value.workload.workload_sha256,
                )
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, NoWorkPlanningEvidenceError):
                raise
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt persistence failed") from exc
        if reopened != value:
            raise NoWorkPlanningEvidenceError("NO_WORK planning receipt did not reopen exactly")
        return reopened


__all__ = [
    "NoWorkPlanningEvidenceError",
    "NoWorkPlanningEvidenceRepository",
    "NoWorkPlanningEvidenceV1",
]
