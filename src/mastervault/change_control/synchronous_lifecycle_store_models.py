"""Content-bound commands and immutable SQLite records for the synchronous lifecycle."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.regression_baseline import GenerationZeroBaselineReceiptV1
from mastervault.change_control.regression_suite import RegressionSuiteV1

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


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


def _operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("operation_id is not canonical")
    return value


class IncomingAdmissionIntentV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    intent_id: str = Field(pattern=r"^incomingintent:[0-9a-f]{64}$")
    intent_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    bundle_id: str = Field(pattern=r"^generic-bundle-v2:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    admission_sha256: str = Field(pattern=SHA256_PATTERN)
    source_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_sha256: str = Field(pattern=SHA256_PATTERN)

    _operation = field_validator("operation_id")(_operation_id)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.bundle_id != f"generic-bundle-v2:{self.bundle_sha256}":
            raise ValueError("incoming admission bundle ID differs from its SHA")
        payload = self.model_dump(mode="json", exclude={"intent_id", "intent_sha256"})
        digest = _sha256(payload)
        if self.intent_sha256 != digest or self.intent_id != f"incomingintent:{digest}":
            raise ValueError("incoming admission intent differs from exact bundle authority")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        values = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(values)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"intent_id": f"incomingintent:{digest}", "intent_sha256": digest, **values}
            )
        )


class IncomingAdmissionRecordV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^incomingreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    intent: IncomingAdmissionIntentV1
    admitted_at: str

    _timestamp = field_validator("admitted_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})
        digest = _sha256(payload)
        if self.receipt_sha256 != digest or self.receipt_id != f"incomingreceipt:{digest}":
            raise ValueError("incoming admission receipt differs from exact intent")
        return self

    @classmethod
    def create(cls, *, intent: IncomingAdmissionIntentV1, admitted_at: str) -> Self:
        payload = {
            "schema_version": 1,
            "intent": intent.model_dump(mode="json"),
            "admitted_at": _canonical_utc(admitted_at),
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"receipt_id": f"incomingreceipt:{digest}", "receipt_sha256": digest, **payload}
            )
        )


class RegressionSuiteAdmissionIntentV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    intent_id: str = Field(pattern=r"^suiteintent:[0-9a-f]{64}$")
    intent_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    suite_id: str
    suite_version: int = Field(ge=1)
    original_sha256: str = Field(pattern=SHA256_PATTERN)
    original_byte_count: int = Field(ge=1, le=1024 * 1024)
    canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    suite: RegressionSuiteV1

    _operation = field_validator("operation_id")(_operation_id)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if (
            self.suite_id != self.suite.suite_id
            or self.suite_version != self.suite.suite_version
            or self.canonical_sha256 != self.suite.canonical_sha256
            or len(self.suite.canonical_bytes) > 1024 * 1024
        ):
            raise ValueError("regression-suite searchable identity differs from canonical suite")
        payload = self.model_dump(mode="json", exclude={"intent_id", "intent_sha256"})
        digest = _sha256(payload)
        if self.intent_sha256 != digest or self.intent_id != f"suiteintent:{digest}":
            raise ValueError("regression-suite intent differs from exact admitted suite")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        values = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(values)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"intent_id": f"suiteintent:{digest}", "intent_sha256": digest, **values}
            )
        )


class RegressionSuiteAdmissionRecordV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^suitereceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    intent: RegressionSuiteAdmissionIntentV1
    admitted_at: str

    _timestamp = field_validator("admitted_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})
        digest = _sha256(payload)
        if self.receipt_sha256 != digest or self.receipt_id != f"suitereceipt:{digest}":
            raise ValueError("regression-suite receipt differs from exact intent")
        return self

    @classmethod
    def create(cls, *, intent: RegressionSuiteAdmissionIntentV1, admitted_at: str) -> Self:
        payload = {
            "schema_version": 1,
            "intent": intent.model_dump(mode="json"),
            "admitted_at": _canonical_utc(admitted_at),
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"receipt_id": f"suitereceipt:{digest}", "receipt_sha256": digest, **payload}
            )
        )


class GenerationZeroBaselineStoreRecordV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    record_id: str = Field(pattern=r"^baselinestore:[0-9a-f]{64}$")
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    incoming_admission_receipt_id: str = Field(pattern=r"^incomingreceipt:[0-9a-f]{64}$")
    incoming_admission_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    suite_admission_receipt_id: str = Field(pattern=r"^suitereceipt:[0-9a-f]{64}$")
    suite_admission_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    incoming_admission: IncomingAdmissionRecordV1
    suite_admission: RegressionSuiteAdmissionRecordV1
    baseline_receipt: GenerationZeroBaselineReceiptV1
    recorded_at: str

    _operation = field_validator("operation_id")(_operation_id)
    _timestamp = field_validator("recorded_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        authority = self.baseline_receipt.authority
        if not (
            self.incoming_admission.receipt_id == self.incoming_admission_receipt_id
            and self.incoming_admission.receipt_sha256
            == self.incoming_admission_receipt_sha256
            == authority.incoming_admission_receipt_sha256
            and authority.incoming_admission_receipt_id == self.incoming_admission_receipt_id
            and self.incoming_admission.intent.run_id == authority.run_id
            and self.suite_admission.receipt_id == self.suite_admission_receipt_id
            and self.suite_admission.receipt_sha256 == self.suite_admission_receipt_sha256
            and self.suite_admission.intent.run_id == authority.run_id
            and self.suite_admission.intent.suite_id == self.baseline_receipt.suite_id
            and self.suite_admission.intent.suite_version == self.baseline_receipt.suite_version
            and self.suite_admission.intent.original_sha256
            == self.baseline_receipt.suite_original_sha256
            and self.suite_admission.intent.canonical_sha256
            == self.baseline_receipt.suite_canonical_sha256
        ):
            raise ValueError("baseline store record differs from admission receipt authority")
        payload = self.model_dump(mode="json", exclude={"record_id", "record_sha256"})
        digest = _sha256(payload)
        if self.record_sha256 != digest or self.record_id != f"baselinestore:{digest}":
            raise ValueError("baseline store record differs from exact receipt bindings")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        if "recorded_at" in values:
            values["recorded_at"] = _canonical_utc(values["recorded_at"])
        values = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(values)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"record_id": f"baselinestore:{digest}", "record_sha256": digest, **values}
            )
        )


class ActivationBaselineBindingV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=r"^activationbaseline:[0-9a-f]{64}$")
    binding_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    activation_id: str = Field(pattern=r"^mactivation:[0-9a-f]{64}$")
    activation_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    baseline_receipt_id: str = Field(pattern=r"^regreceipt:[0-9a-f]{64}$")
    baseline_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    bound_at: str

    _operation = field_validator("operation_id")(_operation_id)
    _timestamp = field_validator("bound_at")(_canonical_utc)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"})
        digest = _sha256(payload)
        if self.binding_sha256 != digest or self.binding_id != f"activationbaseline:{digest}":
            raise ValueError("activation-baseline binding differs from exact authority")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        if "bound_at" in values:
            values["bound_at"] = _canonical_utc(values["bound_at"])
        digest = _sha256(values)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"binding_id": f"activationbaseline:{digest}", "binding_sha256": digest, **values}
            )
        )


__all__ = [
    "ActivationBaselineBindingV1",
    "GenerationZeroBaselineStoreRecordV1",
    "IncomingAdmissionIntentV1",
    "IncomingAdmissionRecordV1",
    "RegressionSuiteAdmissionIntentV1",
    "RegressionSuiteAdmissionRecordV1",
]
