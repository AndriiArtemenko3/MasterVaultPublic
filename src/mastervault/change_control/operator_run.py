"""Pure, non-authoritative navigation records for one operator run.

These records deliberately contain only identities and hashes.  They are an
index into authority owned elsewhere; reopening a run never makes any linked
payload authoritative and callers must verify every referenced receipt again.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import (
    SHA256_PATTERN,
    canonical_json_bytes,
    normalize_logical_key,
)

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_utc(value: str) -> str:
    from datetime import datetime

    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("timestamp must be canonical UTC with second precision")
    return value


def _operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("operation_id is not canonical")
    return value


class OperatorRunLinkKind(StrEnum):
    BOOTSTRAP_INTENT = "bootstrap-intent"
    WORKSPACE_INVENTORY = "workspace-inventory"
    LEGACY_INDEX_READINESS = "legacy-index-readiness"
    GENERATION_ZERO_AUTHORITY = "generation-zero-authority"
    INCOMING_SOURCE = "incoming-source"
    TEMPORAL_PROPOSAL = "temporal-proposal"
    TEMPORAL_REVIEW_REQUEST = "temporal-review-request"
    TEMPORAL_REVIEW_DECISION = "temporal-review-decision"
    IMPACT_EVIDENCE = "impact-evidence"
    REVISION_PLANNING = "revision-planning"
    MANAGED_REVIEW_REQUEST = "managed-review-request"
    MANAGED_REVIEW_DECISION = "managed-review-decision"
    ACTIVATION_OPERATION = "activation-operation"
    REGRESSION_SUITE = "regression-suite"
    GENERATION_ZERO_BASELINE = "generation-zero-baseline"
    REGRESSION = "regression"
    REPORT = "report"


class OperatorRunPhase(StrEnum):
    BOOTSTRAPPED = "bootstrapped"
    AWAITING_TEMPORAL_REVIEW = "awaiting-temporal-review"
    AWAITING_MANAGED_REVIEW = "awaiting-managed-review"
    READY_TO_ACTIVATE = "ready-to-activate"
    ACTIVATED = "activated"
    REJECTED_NO_OP = "rejected-no-op"
    COMPLETED_NO_OP = "completed-no-op"


class OperatorRunCommand(_StrictFrozenModel):
    """Deterministic command for a navigation run rooted at exact authority."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    run_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    aggregate_id: str
    base_authority_id: str = Field(pattern=r"^mauthority:[0-9a-f]{64}$")
    base_authority_revision: int = Field(ge=0)
    base_active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("operation_id")
    @classmethod
    def _canonical_operation(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("aggregate_id")
    @classmethod
    def _canonical_aggregate(cls, value: str) -> str:
        return normalize_logical_key(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_id", "run_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.run_sha256 != digest or self.run_id != f"operatorrun:{digest}":
            raise ValueError("operator run identity differs from its exact base authority")
        return self

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        aggregate_id: str,
        base_authority_id: str,
        base_authority_revision: int,
        base_active_pointer_sha256: str,
    ) -> Self:
        values = {
            "schema_version": 1,
            "operation_id": operation_id,
            "aggregate_id": aggregate_id,
            "base_authority_id": base_authority_id,
            "base_authority_revision": base_authority_revision,
            "base_active_pointer_sha256": base_active_pointer_sha256,
        }
        digest = _sha256(values)
        return cls.model_validate(
            {"run_id": f"operatorrun:{digest}", "run_sha256": digest, **values}
        )


class OperatorRunRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    command: OperatorRunCommand
    created_at: str

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, value: str) -> str:
        return _canonical_utc(value)


class OperatorRunLinkCommand(_StrictFrozenModel):
    """One typed pointer to separately authoritative evidence."""

    schema_version: Literal[1] = 1
    link_id: str = Field(pattern=r"^operatorlink:[0-9a-f]{64}$")
    link_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    kind: OperatorRunLinkKind
    target_id: str
    target_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("operation_id")
    @classmethod
    def _canonical_operation(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("target_id")
    @classmethod
    def _canonical_target(cls, value: str) -> str:
        if _TARGET_ID_RE.fullmatch(value) is None:
            raise ValueError("operator run target_id is not canonical")
        return value

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"link_id", "link_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.link_sha256 != digest or self.link_id != f"operatorlink:{digest}":
            raise ValueError("operator link identity differs from its exact target")
        return self

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        run_id: str,
        kind: OperatorRunLinkKind,
        target_id: str,
        target_sha256: str,
    ) -> Self:
        values = {
            "schema_version": 1,
            "operation_id": operation_id,
            "run_id": run_id,
            "kind": kind,
            "target_id": target_id,
            "target_sha256": target_sha256,
        }
        digest = _sha256(
            {
                key: value.value if isinstance(value, StrEnum) else value
                for key, value in values.items()
            }
        )
        return cls.model_validate(
            {"link_id": f"operatorlink:{digest}", "link_sha256": digest, **values}
        )


class OperatorRunLinkRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    command: OperatorRunLinkCommand
    sequence: int = Field(ge=0)
    recorded_at: str

    @field_validator("recorded_at")
    @classmethod
    def _recorded_at(cls, value: str) -> str:
        return _canonical_utc(value)


class OperatorRunView(_StrictFrozenModel):
    record: OperatorRunRecord
    links: tuple[OperatorRunLinkRecord, ...]

    @model_validator(mode="after")
    def _links(self) -> Self:
        if any(item.command.run_id != self.record.command.run_id for item in self.links):
            raise ValueError("operator links name another run")
        if tuple(item.sequence for item in self.links) != tuple(range(len(self.links))):
            raise ValueError("operator links must be contiguous from zero")
        kinds = tuple(item.command.kind for item in self.links)
        if len(set(kinds)) != len(kinds):
            raise ValueError("operator run link kinds must be unique")
        return self


class OperatorRunListItem(_StrictFrozenModel):
    run: OperatorRunView
    phase: OperatorRunPhase


class OperatorRunPage(_StrictFrozenModel):
    items: tuple[OperatorRunListItem, ...]
    next_cursor: str | None = None


def encode_operator_run_cursor(created_at: str, run_id: str) -> str:
    payload = canonical_json_bytes(
        {"schema_version": 1, "created_at": _canonical_utc(created_at), "run_id": run_id}
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_operator_run_cursor(value: str) -> tuple[str, str]:
    if not value or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("operator run cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        payload = base64.urlsafe_b64decode(value + padding)
        raw = json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("operator run cursor is invalid") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "created_at", "run_id"}:
        raise ValueError("operator run cursor has an invalid shape")
    if (
        raw["schema_version"] != 1
        or not isinstance(raw["created_at"], str)
        or not isinstance(raw["run_id"], str)
    ):
        raise ValueError("operator run cursor has invalid values")
    created_at = _canonical_utc(raw["created_at"])
    if re.fullmatch(r"operatorrun:[0-9a-f]{64}", raw["run_id"]) is None:
        raise ValueError("operator run cursor has an invalid run ID")
    if encode_operator_run_cursor(created_at, raw["run_id"]) != value:
        raise ValueError("operator run cursor is not canonical")
    return created_at, raw["run_id"]


__all__ = [
    "OperatorRunCommand",
    "OperatorRunListItem",
    "OperatorRunLinkCommand",
    "OperatorRunLinkKind",
    "OperatorRunLinkRecord",
    "OperatorRunPage",
    "OperatorRunPhase",
    "OperatorRunRecord",
    "OperatorRunView",
    "decode_operator_run_cursor",
    "encode_operator_run_cursor",
]
