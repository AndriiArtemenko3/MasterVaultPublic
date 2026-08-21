"""Durable at-most-once journal for recorded LIVE provider requests.

The journal deliberately has three observable begin states.  Only the caller
that atomically creates a fresh claim may contact the provider.  A completed
claim returns its immutable result, while an existing claim without a durable
completion is indeterminate and must never be called again automatically.
The journal cannot recover the provider-accepted/result-not-yet-persisted crash
window without a provider idempotency key; it bounds that window by retaining
the incomplete claim and failing every retry closed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    InferenceProviderRequest,
    ProviderCallResult,
)

_ROOT = "application/provider-calls-v1"
# Provider text is bounded to 256 KiB before persistence, but canonical JSON
# escaping can expand arbitrary UTF-8/control bytes substantially.
_MAX_JOURNAL_BYTES = 2 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_RUN_ID = re.compile(r"^operatorrun:[0-9a-f]{64}$")


class ApplicationProviderCallError(ValueError):
    """Provider-call journal evidence is absent, conflicting, or corrupt."""


class ApplicationProviderCallConflictError(ApplicationProviderCallError):
    """One deterministic journal key is bound to different immutable inputs."""


class ApplicationProviderCallIndeterminateError(ApplicationProviderCallError):
    """A prior caller owns the claim but no durable provider result exists."""


class ApplicationProviderCallState(StrEnum):
    FRESH = "fresh"
    COMPLETED = "completed"
    INDETERMINATE = "indeterminate"


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


def _identity(model: BaseModel, *excluded: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes(model.model_dump(mode="json", exclude=set(excluded)))
    ).hexdigest()


class ApplicationProviderCallClaimV1(_StrictFrozenModel):
    """Exact pre-call owner of one canonical recorded-inference request."""

    schema_version: Literal[1] = 1
    claim_id: str = Field(pattern=r"^provider-call-claim:[0-9a-f]{64}$")
    claim_sha256: str = Field(pattern=SHA256_PATTERN)
    owner_id: str = Field(pattern=_SAFE_ID.pattern)
    run_id: str = Field(pattern=_RUN_ID.pattern)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    request_id: str = Field(pattern=r"^inference-provider-request:[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    request_byte_count: int = Field(ge=1, le=3 * 1024 * 1024)
    claimed_at: str

    _claimed_at = field_validator("claimed_at")(_canonical_utc)

    @model_validator(mode="after")
    def _exact(self) -> Self:
        digest = _identity(self, "claim_id", "claim_sha256")
        if self.claim_sha256 != digest or self.claim_id != f"provider-call-claim:{digest}":
            raise ValueError("provider-call claim identity differs from its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        owner_id: str,
        run_id: str,
        provider: str,
        model: str,
        request: InferenceProviderRequest,
        request_bytes: bytes,
        claimed_at: str,
    ) -> Self:
        if request.canonical_bytes() != request_bytes:
            raise ApplicationProviderCallConflictError(
                "provider-call request bytes are not the exact canonical request"
            )
        provisional = cls.model_construct(
            schema_version=1,
            claim_id=f"provider-call-claim:{'0' * 64}",
            claim_sha256="0" * 64,
            owner_id=owner_id,
            run_id=run_id,
            provider=provider,
            model=model,
            request_id=request.request_id,
            request_sha256=request.request_sha256,
            request_byte_count=len(request_bytes),
            claimed_at=claimed_at,
        )
        digest = _identity(provisional, "claim_id", "claim_sha256")
        return cls.model_validate(
            provisional.model_copy(
                update={
                    "claim_id": f"provider-call-claim:{digest}",
                    "claim_sha256": digest,
                }
            ).model_dump(mode="json")
        )

    @property
    def semantic_key(self) -> tuple[object, ...]:
        values = self.model_dump(
            mode="json", exclude={"claim_id", "claim_sha256", "claimed_at"}
        )
        return tuple(sorted(values.items()))


class ApplicationProviderCallCompletionV1(_StrictFrozenModel):
    """Durable exact result for one previously claimed provider request."""

    schema_version: Literal[1] = 1
    completion_id: str = Field(pattern=r"^provider-call-result:[0-9a-f]{64}$")
    completion_sha256: str = Field(pattern=SHA256_PATTERN)
    claim_id: str = Field(pattern=r"^provider-call-claim:[0-9a-f]{64}$")
    claim_sha256: str = Field(pattern=SHA256_PATTERN)
    owner_id: str = Field(pattern=_SAFE_ID.pattern)
    run_id: str = Field(pattern=_RUN_ID.pattern)
    request_id: str = Field(pattern=r"^inference-provider-request:[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    result: ProviderCallResult
    completed_at: str

    _completed_at = field_validator("completed_at")(_canonical_utc)

    @model_validator(mode="after")
    def _exact(self) -> Self:
        digest = _identity(self, "completion_id", "completion_sha256")
        if (
            self.completion_sha256 != digest
            or self.completion_id != f"provider-call-result:{digest}"
        ):
            raise ValueError("provider-call result identity differs from its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        claim: ApplicationProviderCallClaimV1,
        result: ProviderCallResult,
        completed_at: str,
    ) -> Self:
        if result.provider != claim.provider or result.model != claim.model:
            raise ApplicationProviderCallConflictError(
                "provider-call result differs from its exact claim contract"
            )
        provisional = cls.model_construct(
            schema_version=1,
            completion_id=f"provider-call-result:{'0' * 64}",
            completion_sha256="0" * 64,
            claim_id=claim.claim_id,
            claim_sha256=claim.claim_sha256,
            owner_id=claim.owner_id,
            run_id=claim.run_id,
            request_id=claim.request_id,
            request_sha256=claim.request_sha256,
            result=result,
            completed_at=completed_at,
        )
        digest = _identity(provisional, "completion_id", "completion_sha256")
        return cls.model_validate_json(
            canonical_json_bytes(
                provisional.model_copy(
                    update={
                        "completion_id": f"provider-call-result:{digest}",
                        "completion_sha256": digest,
                    }
                ).model_dump(mode="json")
            ),
            strict=True,
        )


@dataclass(frozen=True, slots=True)
class ApplicationProviderCallLease:
    state: ApplicationProviderCallState
    claim: ApplicationProviderCallClaimV1
    result: ProviderCallResult | None


class ApplicationProviderCallJournal:
    """Descriptor-safe create-only journal for exact provider requests."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        try:
            self._backend = FilesystemInferenceEvidenceRepository(
                Path(root), create=create, read_only=read_only
            )
        except InferenceEvidenceRepositoryError as exc:
            raise ApplicationProviderCallError(
                "provider-call journal cannot be established"
            ) from exc

    @staticmethod
    def _key(owner_id: str, run_id: str, request_sha256: str) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "owner_id": owner_id,
                    "run_id": run_id,
                    "request_sha256": request_sha256,
                }
            )
        ).hexdigest()

    @classmethod
    def _claim_path(cls, owner_id: str, run_id: str, request_sha256: str) -> str:
        return f"{_ROOT}/claims/{cls._key(owner_id, run_id, request_sha256)}.json"

    @classmethod
    def _pointer_path(cls, owner_id: str, run_id: str, request_sha256: str) -> str:
        return f"{_ROOT}/completed/{cls._key(owner_id, run_id, request_sha256)}.sha256"

    @staticmethod
    def _result_path(completion_sha256: str) -> str:
        return f"{_ROOT}/results/{completion_sha256}.json"

    def _read_model(
        self, path: str, model: type[_StrictFrozenModel]
    ) -> _StrictFrozenModel | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            path, limit=_MAX_JOURNAL_BYTES, label="provider-call journal evidence"
        )
        if payload is None:
            return None
        try:
            value = model.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise ApplicationProviderCallError("provider-call journal evidence is invalid") from exc
        if canonical_json_bytes(value.model_dump(mode="json")) != payload:
            raise ApplicationProviderCallError(
                "provider-call journal evidence is not canonical"
            )
        return value

    def _read_claim(
        self, *, owner_id: str, run_id: str, request_sha256: str
    ) -> ApplicationProviderCallClaimV1 | None:
        value = self._read_model(
            self._claim_path(owner_id, run_id, request_sha256),
            ApplicationProviderCallClaimV1,
        )
        if value is not None and not isinstance(value, ApplicationProviderCallClaimV1):
            raise AssertionError("provider-call claim parser returned the wrong exact model")
        return value

    def _read_completion(
        self, *, owner_id: str, run_id: str, request_sha256: str
    ) -> ApplicationProviderCallCompletionV1 | None:
        pointer = self._backend._read_optional(  # noqa: SLF001
            self._pointer_path(owner_id, run_id, request_sha256),
            limit=64,
            label="provider-call completion pointer",
        )
        if pointer is None:
            return None
        try:
            digest = pointer.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ApplicationProviderCallError(
                "provider-call completion pointer is invalid"
            ) from exc
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ApplicationProviderCallError("provider-call completion pointer is invalid")
        value = self._read_model(
            self._result_path(digest), ApplicationProviderCallCompletionV1
        )
        if not isinstance(value, ApplicationProviderCallCompletionV1) or (
            value.completion_sha256 != digest
            or value.owner_id != owner_id
            or value.run_id != run_id
            or value.request_sha256 != request_sha256
        ):
            raise ApplicationProviderCallError(
                "provider-call completion differs from its exact pointer"
            )
        return value

    def begin(
        self, claim: ApplicationProviderCallClaimV1
    ) -> ApplicationProviderCallLease:
        """Atomically return FRESH, COMPLETED, or INDETERMINATE for one request."""

        payload = canonical_json_bytes(claim.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing = self._read_claim(
                    owner_id=claim.owner_id,
                    run_id=claim.run_id,
                    request_sha256=claim.request_sha256,
                )
                if existing is not None and existing.semantic_key != claim.semantic_key:
                    raise ApplicationProviderCallConflictError(
                        "provider-call request key is bound to different immutable inputs"
                    )
                if existing is None:
                    self._backend._create_only(  # noqa: SLF001
                        self._claim_path(
                            claim.owner_id, claim.run_id, claim.request_sha256
                        ),
                        payload,
                        label="provider-call request claim",
                    )
                    return ApplicationProviderCallLease(
                        state=ApplicationProviderCallState.FRESH,
                        claim=claim,
                        result=None,
                    )
                completion = self._read_completion(
                    owner_id=claim.owner_id,
                    run_id=claim.run_id,
                    request_sha256=claim.request_sha256,
                )
                if completion is None:
                    return ApplicationProviderCallLease(
                        state=ApplicationProviderCallState.INDETERMINATE,
                        claim=existing,
                        result=None,
                    )
                if completion.claim_id != existing.claim_id or (
                    completion.claim_sha256 != existing.claim_sha256
                ):
                    raise ApplicationProviderCallError(
                        "provider-call completion belongs to another exact claim"
                    )
                return ApplicationProviderCallLease(
                    state=ApplicationProviderCallState.COMPLETED,
                    claim=existing,
                    result=completion.result,
                )
        except ApplicationProviderCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationProviderCallError("provider-call request claim failed") from exc

    def complete(
        self,
        *,
        claim: ApplicationProviderCallClaimV1,
        result: ProviderCallResult,
        completed_at: str,
    ) -> ProviderCallResult:
        """Persist the exact result and then its request-key completion pointer."""

        completion = ApplicationProviderCallCompletionV1.create(
            claim=claim, result=result, completed_at=completed_at
        )
        payload = canonical_json_bytes(completion.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing_claim = self._read_claim(
                    owner_id=claim.owner_id,
                    run_id=claim.run_id,
                    request_sha256=claim.request_sha256,
                )
                if existing_claim != claim:
                    raise ApplicationProviderCallConflictError(
                        "provider-call completion does not match its exact durable claim"
                    )
                existing = self._read_completion(
                    owner_id=claim.owner_id,
                    run_id=claim.run_id,
                    request_sha256=claim.request_sha256,
                )
                if existing is not None:
                    if existing != completion:
                        raise ApplicationProviderCallConflictError(
                            "provider-call claim already has a different durable result"
                        )
                    return existing.result
                self._backend._create_only(  # noqa: SLF001
                    self._result_path(completion.completion_sha256),
                    payload,
                    label="provider-call durable result",
                )
                self._backend._create_only(  # noqa: SLF001
                    self._pointer_path(
                        claim.owner_id, claim.run_id, claim.request_sha256
                    ),
                    completion.completion_sha256.encode("ascii"),
                    label="provider-call completion pointer",
                )
                reopened = self._read_completion(
                    owner_id=claim.owner_id,
                    run_id=claim.run_id,
                    request_sha256=claim.request_sha256,
                )
                if reopened != completion:
                    raise ApplicationProviderCallError(
                        "provider-call durable result did not reopen exactly"
                    )
                return reopened.result
        except ApplicationProviderCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationProviderCallError("provider-call completion failed") from exc


__all__ = [
    "ApplicationProviderCallClaimV1",
    "ApplicationProviderCallConflictError",
    "ApplicationProviderCallError",
    "ApplicationProviderCallIndeterminateError",
    "ApplicationProviderCallJournal",
    "ApplicationProviderCallLease",
    "ApplicationProviderCallState",
    "ApplicationProviderCallCompletionV1",
]
