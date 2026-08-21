"""Durable claim-before-call authority for generic incoming extraction.

The repository deliberately separates the immutable request claim from the
sanitized result.  A process that loses the acknowledgement after result
publication can therefore reopen the result without calling the provider, while
an incomplete claim is explicitly indeterminate and can never authorize an
automatic provider retry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.application_replay import ReplayArtifactRefV1
from mastervault.change_control.application_start_command import ApplicationStartCommandV1
from mastervault.change_control.generic_incoming import (
    GenericExtractionModeV2,
    GenericGroundedExtractionV2,
    VerifiedGenericIncomingV2,
    extraction_request_sha256_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2

_ROOT = "application/extraction-calls"
_MAX_RECEIPT_BYTES = 512 * 1024


class ApplicationExtractionCallError(ValueError):
    """An extraction-call claim/result is absent, conflicting, or corrupt."""


class ApplicationExtractionCallConflictError(ApplicationExtractionCallError):
    """A request key is already owned by different immutable inputs."""


class ApplicationExtractionCallIndeterminateError(ApplicationExtractionCallError):
    """A prior claimant has no durable result, so retrying the call is unsafe."""


class ApplicationExtractionCallState(StrEnum):
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


class GenericExtractionCallRequestV1(_StrictFrozenModel):
    """Exact pre-provider claim keyed by start command and local request SHA."""

    schema_version: Literal[1] = 1
    call_id: str = Field(pattern=r"^generic-extraction-call:[0-9a-f]{64}$")
    call_sha256: str = Field(pattern=SHA256_PATTERN)
    start_command_id: str = Field(pattern=r"^start-command:[0-9a-f]{64}$")
    start_command_sha256: str = Field(pattern=SHA256_PATTERN)
    extraction_request_sha256: str = Field(pattern=SHA256_PATTERN)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    claimed_at: str

    _claimed = field_validator("claimed_at")(_canonical_utc)

    @model_validator(mode="after")
    def _exact(self) -> Self:
        digest = _identity(self, "call_id", "call_sha256")
        if self.call_sha256 != digest or self.call_id != f"generic-extraction-call:{digest}":
            raise ValueError("extraction call identity differs from its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        command: ApplicationStartCommandV1,
        extraction_request_sha256: str,
        provider: str,
        model: str,
        prompt_sha256: str,
        response_schema_sha256: str,
        claimed_at: str,
    ) -> Self:
        provisional = cls.model_construct(
            schema_version=1,
            call_id=f"generic-extraction-call:{'0' * 64}",
            call_sha256="0" * 64,
            start_command_id=command.command_id,
            start_command_sha256=command.command_sha256,
            extraction_request_sha256=extraction_request_sha256,
            source_sha256=command.source_sha256,
            provider=provider,
            model=model,
            prompt_sha256=prompt_sha256,
            response_schema_sha256=response_schema_sha256,
            claimed_at=claimed_at,
        )
        digest = _identity(provisional, "call_id", "call_sha256")
        return cls.model_validate(
            provisional.model_copy(
                update={"call_id": f"generic-extraction-call:{digest}", "call_sha256": digest}
            ).model_dump(mode="json")
        )

    @property
    def retry_key(self) -> tuple[str, str]:
        return self.start_command_id, self.extraction_request_sha256

    @property
    def semantic_key(self) -> tuple[object, ...]:
        payload = self.model_dump(
            mode="json", exclude={"call_id", "call_sha256", "claimed_at"}
        )
        return tuple(sorted(payload.items()))


class GenericExtractionUsageV1(_StrictFrozenModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cost_usd_micros: int = Field(ge=0)
    latency_ms: int = Field(ge=1)


class GenericExtractionCallResultV1(_StrictFrozenModel):
    """Canonical sanitized LIVE authority; raw SDK/provider envelopes are excluded."""

    schema_version: Literal[1] = 1
    result_id: str = Field(pattern=r"^generic-extraction:[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    call_id: str = Field(pattern=r"^generic-extraction-call:[0-9a-f]{64}$")
    call_sha256: str = Field(pattern=SHA256_PATTERN)
    extraction_request_sha256: str = Field(pattern=SHA256_PATTERN)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    usage: GenericExtractionUsageV1
    provider_result_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_contract: GenericGroundedClaimExtractionV2
    grounded_extraction: GenericGroundedExtractionV2
    completed_at: str

    _completed = field_validator("completed_at")(_canonical_utc)

    @model_validator(mode="after")
    def _exact(self) -> Self:
        provider_bytes = canonical_json_bytes(self.provider_contract.model_dump(mode="json"))
        if hashlib.sha256(provider_bytes).hexdigest() != self.provider_result_sha256:
            raise ValueError("provider-result SHA differs from sanitized contract")
        grounded = self.grounded_extraction
        if (
            grounded.mode is not GenericExtractionModeV2.LIVE
            or grounded.request_sha256 != self.extraction_request_sha256
            or grounded.source_sha256 != self.source_sha256
            or grounded.provider_result_sha256 != self.provider_result_sha256
            or grounded.provider_contract != self.provider_contract
        ):
            raise ValueError("grounded extraction differs from recorded LIVE authority")
        digest = _identity(self, "result_id", "result_sha256")
        if self.result_sha256 != digest or self.result_id != f"generic-extraction:{digest}":
            raise ValueError("extraction result identity differs from its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: GenericExtractionCallRequestV1,
        provider_request_id: str,
        usage: GenericExtractionUsageV1,
        grounded_extraction: GenericGroundedExtractionV2,
        completed_at: str,
    ) -> Self:
        provisional = cls.model_construct(
            schema_version=1,
            result_id=f"generic-extraction:{'0' * 64}",
            result_sha256="0" * 64,
            call_id=request.call_id,
            call_sha256=request.call_sha256,
            extraction_request_sha256=request.extraction_request_sha256,
            source_sha256=request.source_sha256,
            provider=request.provider,
            model=request.model,
            prompt_sha256=request.prompt_sha256,
            response_schema_sha256=request.response_schema_sha256,
            provider_request_id=provider_request_id,
            usage=usage,
            provider_result_sha256=grounded_extraction.provider_result_sha256,
            provider_contract=grounded_extraction.provider_contract,
            grounded_extraction=grounded_extraction,
            completed_at=completed_at,
        )
        digest = _identity(provisional, "result_id", "result_sha256")
        return cls.model_validate_json(
            canonical_json_bytes(
                provisional.model_copy(
                    update={
                        "result_id": f"generic-extraction:{digest}",
                        "result_sha256": digest,
                    }
                ).model_dump(mode="json")
            ),
            strict=True,
        )

    @property
    def replay_ref(self) -> ReplayArtifactRefV1:
        content = canonical_json_bytes(self.model_dump(mode="json"))
        return ReplayArtifactRefV1(
            artifact_kind="generic-extraction",
            artifact_id=self.result_id,
            artifact_sha256=self.result_sha256,
            artifact_byte_count=len(content),
            relative_locator=f"{_ROOT}/results/{self.result_sha256}.json",
            request_sha256=self.extraction_request_sha256,
        )


@dataclass(frozen=True, slots=True)
class ApplicationExtractionCallLease:
    state: ApplicationExtractionCallState
    claim: GenericExtractionCallRequestV1
    result: GenericExtractionCallResultV1 | None


class ApplicationExtractionCallRepository:
    """Descriptor-safe create-only extraction-call request/result repository."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        try:
            self._backend = FilesystemInferenceEvidenceRepository(
                Path(root), create=create, read_only=read_only
            )
        except InferenceEvidenceRepositoryError as exc:
            raise ApplicationExtractionCallError(
                "cannot establish extraction-call repository"
            ) from exc

    @staticmethod
    def _key(command_id: str, request_sha256: str) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {"start_command_id": command_id, "extraction_request_sha256": request_sha256}
            )
        ).hexdigest()

    @classmethod
    def _claim_path(cls, command_id: str, request_sha256: str) -> str:
        return f"{_ROOT}/claims/{cls._key(command_id, request_sha256)}.json"

    @classmethod
    def _completion_path(cls, command_id: str, request_sha256: str) -> str:
        return f"{_ROOT}/completed/{cls._key(command_id, request_sha256)}.json"

    @staticmethod
    def _result_path(result_sha256: str) -> str:
        return f"{_ROOT}/results/{result_sha256}.json"

    def _read_model(self, path: str, model: type[_StrictFrozenModel]) -> _StrictFrozenModel | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            path, limit=_MAX_RECEIPT_BYTES, label="generic extraction call"
        )
        if payload is None:
            return None
        try:
            parsed = model.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise ApplicationExtractionCallError("extraction-call evidence is invalid") from exc
        if canonical_json_bytes(parsed.model_dump(mode="json")) != payload:
            raise ApplicationExtractionCallError("extraction-call evidence is not canonical")
        return parsed

    def _read_claim(
        self, command_id: str, request_sha256: str
    ) -> GenericExtractionCallRequestV1 | None:
        value = self._read_model(self._claim_path(command_id, request_sha256), GenericExtractionCallRequestV1)
        assert value is None or isinstance(value, GenericExtractionCallRequestV1)
        return value

    def _read_result_for(
        self, command_id: str, request_sha256: str
    ) -> GenericExtractionCallResultV1 | None:
        pointer = self._backend._read_optional(  # noqa: SLF001
            self._completion_path(command_id, request_sha256),
            limit=64,
            label="generic extraction completion",
        )
        if pointer is None:
            return None
        try:
            digest = pointer.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ApplicationExtractionCallError("extraction completion is invalid") from exc
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ApplicationExtractionCallError("extraction completion is invalid")
        value = self._read_model(self._result_path(digest), GenericExtractionCallResultV1)
        if not isinstance(value, GenericExtractionCallResultV1) or value.result_sha256 != digest:
            raise ApplicationExtractionCallError("extraction result is missing or mismatched")
        if (value.call_id, value.extraction_request_sha256) != (command_id, request_sha256):
            # command_id here is the start command, so compare through its claim below.
            claim = self._read_claim(command_id, request_sha256)
            if claim is None or value.call_id != claim.call_id:
                raise ApplicationExtractionCallError("extraction result belongs to another claim")
        return value

    def begin(
        self, request: GenericExtractionCallRequestV1
    ) -> ApplicationExtractionCallLease:
        """Atomically classify a request as fresh, completed, or indeterminate."""

        payload = canonical_json_bytes(request.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                existing = self._read_claim(
                    request.start_command_id, request.extraction_request_sha256
                )
                if existing is not None and existing.semantic_key != request.semantic_key:
                    raise ApplicationExtractionCallConflictError(
                        "extraction request key is already bound to different immutable inputs"
                    )
                if existing is None:
                    self._backend._create_only(  # noqa: SLF001
                        self._claim_path(
                            request.start_command_id, request.extraction_request_sha256
                        ),
                        payload,
                        label="generic extraction request claim",
                    )
                    return ApplicationExtractionCallLease(
                        state=ApplicationExtractionCallState.FRESH,
                        claim=request,
                        result=None,
                    )
                result = self._read_result_for(
                    request.start_command_id, request.extraction_request_sha256
                )
                return ApplicationExtractionCallLease(
                    state=(
                        ApplicationExtractionCallState.COMPLETED
                        if result is not None
                        else ApplicationExtractionCallState.INDETERMINATE
                    ),
                    claim=existing,
                    result=result,
                )
        except ApplicationExtractionCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationExtractionCallError("cannot claim extraction request") from exc

    def claim(
        self, request: GenericExtractionCallRequestV1
    ) -> tuple[GenericExtractionCallRequestV1, GenericExtractionCallResultV1 | None]:
        """Compatibility wrapper that fails closed for an incomplete prior claim."""

        lease = self.begin(request)
        if lease.state is ApplicationExtractionCallState.INDETERMINATE:
            raise ApplicationExtractionCallIndeterminateError(
                "extraction claim exists without a durable result; automatic retry is unsafe"
            )
        return lease.claim, lease.result

    def complete_live(
        self,
        request: GenericExtractionCallRequestV1,
        result: GenericExtractionCallResultV1,
    ) -> GenericExtractionCallResultV1:
        """Publish the sanitized result then its request-key completion pointer."""

        if (
            result.call_id != request.call_id
            or result.call_sha256 != request.call_sha256
            or result.extraction_request_sha256 != request.extraction_request_sha256
            or result.source_sha256 != request.source_sha256
            or result.provider != request.provider
            or result.model != request.model
            or result.prompt_sha256 != request.prompt_sha256
            or result.response_schema_sha256 != request.response_schema_sha256
        ):
            raise ApplicationExtractionCallConflictError(
                "extraction result differs from its exact request claim"
            )
        result_payload = canonical_json_bytes(result.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                claimed = self._read_claim(
                    request.start_command_id, request.extraction_request_sha256
                )
                if claimed != request:
                    raise ApplicationExtractionCallConflictError(
                        "extraction request was not claimed exactly before completion"
                    )
                existing = self._read_result_for(
                    request.start_command_id, request.extraction_request_sha256
                )
                if existing is not None:
                    if existing != result:
                        raise ApplicationExtractionCallConflictError(
                            "extraction request already has a different result"
                        )
                    return existing
                self._backend._create_only(  # noqa: SLF001
                    self._result_path(result.result_sha256),
                    result_payload,
                    label="generic extraction result",
                )
                self._backend._create_only(  # noqa: SLF001
                    self._completion_path(
                        request.start_command_id, request.extraction_request_sha256
                    ),
                    result.result_sha256.encode("ascii"),
                    label="generic extraction completion",
                )
                reopened = self._read_result_for(
                    request.start_command_id, request.extraction_request_sha256
                )
                if reopened != result:
                    raise ApplicationExtractionCallError(
                        "completed extraction result did not reopen exactly"
                    )
                return reopened
        except ApplicationExtractionCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationExtractionCallError("cannot complete extraction request") from exc

    def reopen_replay(
        self,
        reference: ReplayArtifactRefV1,
        *,
        command: ApplicationStartCommandV1,
        admission: VerifiedGenericIncomingV2,
        expected_request: GenericExtractionCallRequestV1,
    ) -> GenericGroundedExtractionV2:
        """Reopen exact prior LIVE authority and ground it against current source."""

        request_sha = extraction_request_sha256_v2(admission)
        if (
            expected_request.start_command_id != command.command_id
            or expected_request.start_command_sha256 != command.command_sha256
            or expected_request.source_sha256 != admission.source_sha256
            or expected_request.extraction_request_sha256 != request_sha
            or reference.artifact_kind != "generic-extraction"
            or reference.request_sha256 != request_sha
            or reference.relative_locator
            != f"{_ROOT}/results/{reference.artifact_sha256}.json"
        ):
            raise ApplicationExtractionCallConflictError(
                "replay reference differs from the current extraction request"
            )
        try:
            with self._backend._read_lock():  # noqa: SLF001
                result = self._read_model(
                    reference.relative_locator, GenericExtractionCallResultV1
                )
        except ApplicationExtractionCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationExtractionCallError("cannot reopen replay extraction") from exc
        if not isinstance(result, GenericExtractionCallResultV1) or (
            result.result_id != reference.artifact_id
            or result.result_sha256 != reference.artifact_sha256
            or len(canonical_json_bytes(result.model_dump(mode="json")))
            != reference.artifact_byte_count
            or result.source_sha256 != admission.source_sha256
            or result.extraction_request_sha256 != request_sha
            or result.provider != expected_request.provider
            or result.model != expected_request.model
            or result.prompt_sha256 != expected_request.prompt_sha256
            or result.response_schema_sha256 != expected_request.response_schema_sha256
        ):
            raise ApplicationExtractionCallConflictError(
                "replay reference does not name exact LIVE extraction authority"
            )
        # The referenced LIVE authority may originate from another start command;
        # the strict replay bundle and the current command bind reuse explicitly.
        if command.source_sha256 != admission.source_sha256:
            raise ApplicationExtractionCallConflictError(
                "replay command differs from the currently admitted source"
            )
        return ground_generic_extraction_v2(
            admission,
            result.provider_contract,
            mode=GenericExtractionModeV2.REPLAY,
            replay_of=result.grounded_extraction,
        )

    def reopen_result_reference(
        self, reference: ReplayArtifactRefV1
    ) -> GenericExtractionCallResultV1:
        """Reopen one exact immutable result reference without deriving authority."""

        if (
            reference.artifact_kind != "generic-extraction"
            or reference.relative_locator
            != f"{_ROOT}/results/{reference.artifact_sha256}.json"
        ):
            raise ApplicationExtractionCallConflictError(
                "generic extraction result reference is not canonical"
            )
        try:
            with self._backend._read_lock():  # noqa: SLF001
                value = self._read_model(
                    reference.relative_locator, GenericExtractionCallResultV1
                )
        except ApplicationExtractionCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationExtractionCallError(
                "cannot reopen extraction result reference"
            ) from exc
        if not isinstance(value, GenericExtractionCallResultV1) or (
            value.result_id != reference.artifact_id
            or value.result_sha256 != reference.artifact_sha256
            or len(canonical_json_bytes(value.model_dump(mode="json")))
            != reference.artifact_byte_count
            or value.extraction_request_sha256 != reference.request_sha256
        ):
            raise ApplicationExtractionCallConflictError(
                "generic extraction result reference differs from authority"
            )
        return value

    def reopen_completed(
        self, *, start_command_id: str, extraction_request_sha256: str
    ) -> GenericExtractionCallResultV1:
        """Reopen the completed exact result owned by one request key."""

        try:
            with self._backend._read_lock():  # noqa: SLF001
                value = self._read_result_for(
                    start_command_id, extraction_request_sha256
                )
        except ApplicationExtractionCallError:
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ApplicationExtractionCallError(
                "cannot reopen completed extraction result"
            ) from exc
        if value is None:
            raise ApplicationExtractionCallError("extraction result is not complete")
        return value


__all__ = [
    "ApplicationExtractionCallConflictError",
    "ApplicationExtractionCallError",
    "ApplicationExtractionCallIndeterminateError",
    "ApplicationExtractionCallLease",
    "ApplicationExtractionCallRepository",
    "ApplicationExtractionCallState",
    "GenericExtractionCallRequestV1",
    "GenericExtractionCallResultV1",
    "GenericExtractionUsageV1",
]
