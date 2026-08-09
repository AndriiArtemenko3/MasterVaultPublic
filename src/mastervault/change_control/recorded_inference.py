"""I/O-agnostic, receipt-grade execution for bounded change-control inference."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationInferenceShard,
    ClassificationOutputShard,
    ClassificationWorkload,
)
from mastervault.change_control.dependency_analysis import (
    DependencyClassification,
    DependencyDisposition,
    DependencyInferenceShard,
    DependencyOutputShard,
    DependencyWorkload,
)
from mastervault.change_control.impact_analysis import (
    ImpactInferenceShard,
    ImpactWorkload,
)
from mastervault.change_control.impact_results import (
    MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1,
    MAX_IMPACT_QUESTIONS_V1,
    ImpactDecision,
    ImpactDisposition,
    ImpactOutputShard,
)
from mastervault.change_control.managed_review import (
    ContentAddressedInferenceReceipt,
    InferenceExecutionMode,
    InferenceUsage,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    DependencyKind,
    DocumentSpanReference,
    PairDisposition,
    canonical_json_bytes,
)

MAX_PROVIDER_OUTPUT_BYTES_V1 = 256 * 1024
MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1 = 3 * 1024 * 1024
MAX_ATTEMPTS_V1 = 2
MAX_VALIDATION_ERROR_BYTES_V1 = 2_000
MAX_OUTCOME_ARTIFACTS_V1 = 8
MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1 = MAX_OUTCOME_ARTIFACTS_V1 * 256 * 1024
# JSON may expand each content byte to a six-byte ``\uXXXX`` escape.  One MiB
# above that 12 MiB worst case bounds the eight fixed reference wrappers.
MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1 = 13 * 1024 * 1024

_EXECUTION_ID = r"^inference-exec:[0-9a-f]{64}$"
_ATTEMPT_ID = r"^inference-attempt:[0-9a-f]{64}$"
_ENVELOPE_ID = r"^inference-input:[0-9a-f]{64}$"
_PROVIDER_REQUEST_ID = r"^inference-provider-request:[0-9a-f]{64}$"
_OPERATION_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RecordedInferenceTask(StrEnum):
    CLASSIFICATION = "classification"
    DEPENDENCY = "dependency"
    IMPACT = "impact"


class ProviderCallResult(_StrictFrozenModel):
    """Truthful evidence returned by exactly one provider call."""

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    provider_request_id: str = Field(pattern=_OPERATION_ID)
    raw_output_utf8: str
    usage: InferenceUsage

    @model_validator(mode="after")
    def _live_evidence(self) -> Self:
        observed = len(self.raw_output_utf8.encode("utf-8"))
        if observed == 0 or observed > MAX_PROVIDER_OUTPUT_BYTES_V1:
            raise ValueError("provider output must be non-empty and at most 256 KiB")
        if self.usage.latency_ms == 0 or self.usage.input_tokens + self.usage.output_tokens == 0:
            raise ValueError("provider call requires non-zero token and latency evidence")
        return self


class RecordedInferenceProvider(Protocol):
    def complete(
        self,
        *,
        request: bytes,
    ) -> ProviderCallResult: ...


class InferenceCorrection(_StrictFrozenModel):
    """Exact prior semantic failure supplied to the one permitted correction call."""

    previous_raw_output_utf8: str
    validation_error: str = Field(min_length=1)

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if len(self.previous_raw_output_utf8.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES_V1:
            raise ValueError("correction raw output exceeds the provider-output limit")
        if len(self.validation_error.encode("utf-8")) > MAX_VALIDATION_ERROR_BYTES_V1:
            raise ValueError("correction validation error exceeds the fixed byte limit")
        return self


class InferenceProviderRequest(_StrictFrozenModel):
    """Canonical call bytes containing the semantic input, not merely artifact locators."""

    schema_version: Literal[1] = 1
    ordinal: int = Field(ge=1, le=MAX_ATTEMPTS_V1)
    task: RecordedInferenceTask
    input_envelope: InferenceInputEnvelope
    prompt_utf8: str
    response_schema_utf8: str
    input_shard_utf8: str
    correction: InferenceCorrection | None = None
    request_id: str = Field(pattern=_PROVIDER_REQUEST_ID)
    request_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"request_id", "request_sha256"})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.task != self.input_envelope.task:
            raise ValueError("provider request task differs from its input envelope")
        if self.ordinal == 1 and self.correction is not None:
            raise ValueError("first provider request cannot contain correction evidence")
        if self.ordinal == 2 and self.correction is None:
            raise ValueError("second provider request requires exact correction evidence")
        exact = (
            (self.prompt_utf8, self.input_envelope.prompt_sha256),
            (self.response_schema_utf8, self.input_envelope.response_schema_sha256),
            (self.input_shard_utf8, self.input_envelope.input_shard_sha256),
        )
        if any(_bytes_sha256(value.encode("utf-8")) != digest for value, digest in exact):
            raise ValueError("provider request semantic bytes differ from the input envelope")
        digest = _sha256(self._payload())
        if (
            self.request_sha256 != digest
            or self.request_id != f"inference-provider-request:{digest}"
        ):
            raise ValueError("provider request ID/SHA does not match its exact content")
        return self


class ClassificationWireDecision(_StrictFrozenModel):
    pair_id: str = Field(pattern=r"^pair:[0-9a-f]{64}$")
    disposition: PairDisposition
    newer_revision_id: str | None = Field(default=None, pattern=r"^claimrev:[0-9a-f]{64}$")
    rationale: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0.0, le=1.0)


class ClassificationWireResponse(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task: Literal[RecordedInferenceTask.CLASSIFICATION] = RecordedInferenceTask.CLASSIFICATION
    decisions: tuple[ClassificationWireDecision, ...] = Field(min_length=1, max_length=256)


class DependencySpanWireDecision(_StrictFrozenModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("dependency evidence end must follow its start")
        return self


class DependencyWireDecision(_StrictFrozenModel):
    candidate_id: str = Field(pattern=r"^depcand:[0-9a-f]{64}$")
    disposition: DependencyDisposition
    dependency_kind: DependencyKind | None = None
    selected_downstream_claim_revision_ids: tuple[str, ...] = ()
    spans: tuple[DependencySpanWireDecision, ...] = ()
    rationale: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0.0, le=1.0)


class DependencyWireResponse(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task: Literal[RecordedInferenceTask.DEPENDENCY] = RecordedInferenceTask.DEPENDENCY
    decisions: tuple[DependencyWireDecision, ...] = Field(min_length=1, max_length=64)


class ImpactSpanWireDecision(_StrictFrozenModel):
    """Provider-selected character offsets; provenance is derived locally."""

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("impact evidence end must follow its start")
        return self


class ImpactWireDecision(_StrictFrozenModel):
    """The complete semantic wire vocabulary for one actual-impact question."""

    question_id: str = Field(pattern=r"^impactq:[0-9a-f]{64}$")
    disposition: ImpactDisposition
    spans: tuple[ImpactSpanWireDecision, ...] = Field(
        default=(),
        max_length=MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1,
    )
    attention_path_context_ids: tuple[str, ...] = ()
    dependency_context_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=4_000)


class ImpactWireResponse(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task: Literal[RecordedInferenceTask.IMPACT] = RecordedInferenceTask.IMPACT
    decisions: tuple[ImpactWireDecision, ...] = Field(
        min_length=1,
        max_length=MAX_IMPACT_QUESTIONS_V1,
    )


class InferenceArtifactPayload(_StrictFrozenModel):
    artifact: ManagedArtifactRef
    content_utf8: str

    @model_validator(mode="after")
    def _exact(self) -> Self:
        content = self.content_utf8.encode("utf-8")
        if len(content) != self.artifact.byte_count:
            raise ValueError("artifact payload byte count differs from its reference")
        if hashlib.sha256(content).hexdigest() != self.artifact.sha256:
            raise ValueError("artifact payload SHA differs from its reference")
        return self


class InferenceInputEnvelope(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task: RecordedInferenceTask
    algorithm_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    contract_id: str
    contract_version: int = Field(ge=1)
    provider: str
    model: str
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    workload_id: str
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    input_artifacts: tuple[ManagedArtifactRef, ...] = Field(min_length=4, max_length=4)
    envelope_id: str = Field(pattern=_ENVELOPE_ID)
    envelope_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"envelope_id", "envelope_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.input_artifacts != tuple(
            sorted(self.input_artifacts, key=lambda item: item.artifact_id)
        ):
            raise ValueError("input artifacts must use canonical artifact-ID order")
        if len({item.artifact_id for item in self.input_artifacts}) != 4:
            raise ValueError("input artifacts must be unique")
        expected = {
            f"inference/algorithms/{self.algorithm_manifest_sha256}.json": (
                self.algorithm_manifest_sha256
            ),
            f"inference/prompts/{self.prompt_sha256}.txt": self.prompt_sha256,
            f"inference/schemas/{self.response_schema_sha256}.json": (self.response_schema_sha256),
            f"inference/inputs/{self.input_shard_sha256}.json": self.input_shard_sha256,
        }
        observed = {item.path: item.sha256 for item in self.input_artifacts}
        if observed != expected or any(
            item.kind != ManagedArtifactKind.INFERENCE_INPUT for item in self.input_artifacts
        ):
            raise ValueError("input artifacts do not bind the four exact contract/input locators")
        digest = _sha256(self._payload())
        if self.envelope_sha256 != digest or self.envelope_id != f"inference-input:{digest}":
            raise ValueError("input envelope ID/SHA does not match its exact content")
        return self


class InferenceAttemptEvidence(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    ordinal: int = Field(ge=1, le=MAX_ATTEMPTS_V1)
    provider: str
    model: str
    provider_request_id: str = Field(pattern=_OPERATION_ID)
    usage: InferenceUsage
    raw_output_artifact: ManagedArtifactRef
    accepted: bool
    validation_error: str | None = None
    validated_output_artifact: ManagedArtifactRef | None = None
    attempt_id: str = Field(pattern=_ATTEMPT_ID)
    attempt_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"attempt_id", "attempt_sha256"})

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_inference_output_locator(self.raw_output_artifact, root="raw")
        if self.accepted:
            if self.validation_error is not None or self.validated_output_artifact is None:
                raise ValueError("accepted attempt requires only a validated output artifact")
        elif self.validation_error is None or self.validated_output_artifact is not None:
            raise ValueError("rejected attempt requires only a validation error")
        if self.validated_output_artifact is not None:
            _require_inference_output_locator(self.validated_output_artifact, root="outputs")
        if self.validation_error is not None and len(self.validation_error.encode("utf-8")) > (
            MAX_VALIDATION_ERROR_BYTES_V1
        ):
            raise ValueError("attempt validation error exceeds the fixed byte limit")
        digest = _sha256(self._payload())
        if self.attempt_sha256 != digest or self.attempt_id != f"inference-attempt:{digest}":
            raise ValueError("attempt evidence ID/SHA does not match its content")
        return self


class RecordedInferenceExecution(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task: RecordedInferenceTask
    contract: ManagedInferenceContractBinding
    input_envelope: InferenceInputEnvelope
    attempts: tuple[InferenceAttemptEvidence, ...] = Field(max_length=MAX_ATTEMPTS_V1)
    raw_output_artifact: ManagedArtifactRef
    validated_output_artifact: ManagedArtifactRef
    receipt: ContentAddressedInferenceReceipt
    receipt_artifact: ManagedArtifactRef
    replay_source_execution_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    execution_id: str = Field(pattern=_EXECUTION_ID)
    execution_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"execution_id", "execution_sha256"})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        self.contract.require_receipt(self.receipt)
        envelope_contract = (
            self.input_envelope.algorithm_manifest_sha256,
            self.input_envelope.contract_id,
            self.input_envelope.contract_version,
            self.input_envelope.provider,
            self.input_envelope.model,
            self.input_envelope.prompt_sha256,
            self.input_envelope.response_schema_sha256,
        )
        exact_contract = (
            self.contract.algorithm_manifest_sha256,
            self.contract.contract_id,
            self.contract.contract_version,
            self.contract.provider,
            self.contract.model,
            self.contract.prompt_sha256,
            self.contract.response_schema_sha256,
        )
        if envelope_contract != exact_contract:
            raise ValueError("execution input envelope differs from its exact contract")
        if self.input_envelope.task != self.task:
            raise ValueError("execution task differs from its input envelope")
        _require_inference_output_locator(self.raw_output_artifact, root="raw")
        _require_inference_output_locator(self.validated_output_artifact, root="outputs")
        if self.receipt.input_envelope_sha256 != self.input_envelope.envelope_sha256:
            raise ValueError("receipt binds a different input envelope")
        if self.receipt.input_artifacts != self.input_envelope.input_artifacts:
            raise ValueError("receipt binds different input artifacts")
        if (
            self.receipt.raw_output_sha256 != self.raw_output_artifact.sha256
            or self.receipt.validated_output_sha256 != self.validated_output_artifact.sha256
        ):
            raise ValueError("receipt output hashes differ from execution artifacts")
        receipt_bytes = canonical_json_bytes(self.receipt.model_dump(mode="json"))
        if (
            self.receipt_artifact.kind != ManagedArtifactKind.INFERENCE_RECEIPT
            or self.receipt_artifact.path
            != f"receipts/inference/{self.receipt_artifact.sha256}.json"
            or self.receipt_artifact.sha256 != hashlib.sha256(receipt_bytes).hexdigest()
            or self.receipt_artifact.byte_count != len(receipt_bytes)
        ):
            raise ValueError("receipt artifact does not bind exact receipt bytes and locator")
        if self.contract.mode == InferenceExecutionMode.LIVE:
            if not self.attempts or self.replay_source_execution_sha256 is not None:
                raise ValueError("live execution requires attempts and no replay source")
            if tuple(item.ordinal for item in self.attempts) != tuple(
                range(1, len(self.attempts) + 1)
            ):
                raise ValueError("live attempt ordinals must be contiguous")
            if any(item.accepted for item in self.attempts[:-1]) or not self.attempts[-1].accepted:
                raise ValueError("only the final live attempt may be accepted")
            request_ids = tuple(item.provider_request_id for item in self.attempts)
            if len(request_ids) != len(set(request_ids)):
                raise ValueError("live attempts require distinct provider request IDs")
            final = self.attempts[-1]
            if (
                final.provider_request_id != self.receipt.provider_request_id
                or final.usage != self.receipt.usage
                or final.raw_output_artifact != self.raw_output_artifact
                or final.validated_output_artifact != self.validated_output_artifact
            ):
                raise ValueError("live receipt does not equal final successful call evidence")
        elif self.attempts or self.replay_source_execution_sha256 is None:
            raise ValueError("replay execution requires no provider attempts and one live source")
        digest = _sha256(self._payload())
        if self.execution_sha256 != digest or self.execution_id != f"inference-exec:{digest}":
            raise ValueError("execution ID/SHA does not match its exact content")
        return self


class RecordedInferenceOutcome(_StrictFrozenModel):
    execution: RecordedInferenceExecution
    classification_output: ClassificationOutputShard | None = None
    dependency_output: DependencyOutputShard | None = None
    impact_output: ImpactOutputShard | None = None
    artifacts: tuple[InferenceArtifactPayload, ...] = Field(
        min_length=7,
        max_length=MAX_OUTCOME_ARTIFACTS_V1,
    )

    @model_serializer(mode="wrap")
    def _preserve_v1_two_task_bytes(self, handler: Any) -> Any:
        """Omit the additive field when absent so old committed bytes stay exact."""

        data = handler(self)
        if self.impact_output is None:
            data.pop("impact_output", None)
        return data

    @field_validator("artifacts", mode="before")
    @classmethod
    def _preflight_artifact_bounds(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            if not 7 <= len(value) <= MAX_OUTCOME_ARTIFACTS_V1:
                raise ValueError("outcome must contain seven or eight exact v1 artifacts")
            content_bytes = 0
            for item in value:
                content = (
                    item.content_utf8
                    if isinstance(item, InferenceArtifactPayload)
                    else item.get("content_utf8")
                    if isinstance(item, dict)
                    else None
                )
                if isinstance(content, str):
                    content_bytes += len(content.encode("utf-8"))
                    if content_bytes > MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1:
                        raise ValueError("outcome artifact content exceeds the 2 MiB v1 limit")
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _complete(self) -> Self:
        output = _task_output(
            task=self.execution.task,
            classification_output=self.classification_output,
            dependency_output=self.dependency_output,
            impact_output=self.impact_output,
        )
        envelope = self.execution.input_envelope
        if (
            envelope.workload_id != output.workload_id
            or envelope.workload_sha256 != output.workload_sha256
            or envelope.input_shard_id != output.input_shard_id
            or envelope.input_shard_sha256 != output.input_shard_sha256
        ):
            raise ValueError("inference envelope differs from its exact typed output binding")
        refs = tuple(item.artifact for item in self.artifacts)
        if refs != tuple(sorted(refs, key=lambda item: item.artifact_id)):
            raise ValueError("artifact payloads must use canonical artifact-ID order")
        by_id = {item.artifact.artifact_id: item for item in self.artifacts}
        if len(by_id) != len(self.artifacts):
            raise ValueError("artifact payloads must be unique")
        required = {
            self.execution.raw_output_artifact.artifact_id,
            self.execution.validated_output_artifact.artifact_id,
            self.execution.receipt_artifact.artifact_id,
            *(item.artifact_id for item in self.execution.input_envelope.input_artifacts),
            *(item.raw_output_artifact.artifact_id for item in self.execution.attempts),
        }
        replay_source = self.execution.receipt.replay_source_receipt_artifact
        if replay_source is not None:
            required.add(replay_source.artifact_id)
        if set(by_id) != required:
            raise ValueError("outcome artifacts must be exactly the required inference evidence")
        artifact_payload = [item.model_dump(mode="json") for item in self.artifacts]
        if len(canonical_json_bytes(artifact_payload)) > (MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1):
            raise ValueError("outcome artifact envelope exceeds the 13 MiB canonical limit")
        output_bytes = output.canonical_bytes()
        payload = by_id[self.execution.validated_output_artifact.artifact_id]
        if payload.content_utf8.encode("utf-8") != output_bytes:
            raise ValueError("validated output bytes differ from the typed output shard")
        return self


class ReplayEvidenceResolver(Protocol):
    def resolve_replay_evidence(
        self,
        *,
        receipt_artifact: ManagedArtifactRef,
    ) -> RecordedInferenceOutcome: ...


class InferenceExecutionFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        attempts: tuple[InferenceAttemptEvidence, ...],
        artifacts: tuple[InferenceArtifactPayload, ...],
    ) -> None:
        self.attempts = attempts
        self.artifacts = artifacts
        super().__init__(message)


def _task_output(
    *,
    task: RecordedInferenceTask,
    classification_output: ClassificationOutputShard | None,
    dependency_output: DependencyOutputShard | None,
    impact_output: ImpactOutputShard | None,
) -> ClassificationOutputShard | DependencyOutputShard | ImpactOutputShard:
    outputs = {
        RecordedInferenceTask.CLASSIFICATION: classification_output,
        RecordedInferenceTask.DEPENDENCY: dependency_output,
        RecordedInferenceTask.IMPACT: impact_output,
    }
    selected = outputs[task]
    if selected is None or sum(item is not None for item in outputs.values()) != 1:
        raise ValueError(f"{task.value} execution requires only its exact typed output")
    return selected


def _outcome_output_fields(
    *,
    task: RecordedInferenceTask,
    output: ClassificationOutputShard | DependencyOutputShard | ImpactOutputShard,
) -> dict[str, Any]:
    expected_type = {
        RecordedInferenceTask.CLASSIFICATION: ClassificationOutputShard,
        RecordedInferenceTask.DEPENDENCY: DependencyOutputShard,
        RecordedInferenceTask.IMPACT: ImpactOutputShard,
    }[task]
    if type(output) is not expected_type:
        raise TypeError("recorded inference task produced the wrong typed output shard")
    return {
        "classification_output": (
            output if task == RecordedInferenceTask.CLASSIFICATION else None
        ),
        "dependency_output": output if task == RecordedInferenceTask.DEPENDENCY else None,
        "impact_output": output if task == RecordedInferenceTask.IMPACT else None,
    }


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_inference_output_locator(
    artifact: ManagedArtifactRef,
    *,
    root: Literal["raw", "outputs"],
) -> None:
    if (
        artifact.kind != ManagedArtifactKind.INFERENCE_OUTPUT
        or artifact.path != f"inference/{root}/{artifact.sha256}.json"
    ):
        raise ValueError(f"inference {root} artifact requires its exact content locator")


def _artifact(
    *, kind: ManagedArtifactKind, root: str, suffix: str, content: bytes
) -> ManagedArtifactRef:
    digest = _bytes_sha256(content)
    return ManagedArtifactRef.create(
        kind=kind,
        path=f"inference/{root}/{digest}.{suffix}",
        sha256=digest,
        byte_count=len(content),
    )


def _receipt_artifact(receipt: ContentAddressedInferenceReceipt) -> ManagedArtifactRef:
    content = canonical_json_bytes(receipt.model_dump(mode="json"))
    digest = _bytes_sha256(content)
    return ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_RECEIPT,
        path=f"receipts/inference/{digest}.json",
        sha256=digest,
        byte_count=len(content),
    )


def _bounded_error(error: Exception) -> str:
    value = " ".join(f"{type(error).__name__}: {error}".split())
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_VALIDATION_ERROR_BYTES_V1:
        return value
    return encoded[:MAX_VALIDATION_ERROR_BYTES_V1].decode("utf-8", errors="ignore")


def _provider_request(
    *,
    ordinal: int,
    task: RecordedInferenceTask,
    envelope: InferenceInputEnvelope,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    input_bytes: bytes,
    correction: InferenceCorrection | None,
) -> InferenceProviderRequest:
    values = {
        "schema_version": 1,
        "ordinal": ordinal,
        "task": task.value,
        "input_envelope": envelope.model_dump(mode="json"),
        "prompt_utf8": prompt_bytes.decode("utf-8"),
        "response_schema_utf8": response_schema_bytes.decode("utf-8"),
        "input_shard_utf8": input_bytes.decode("utf-8"),
        "correction": correction.model_dump(mode="json") if correction is not None else None,
    }
    digest = _sha256(values)
    return InferenceProviderRequest(
        ordinal=ordinal,
        task=task,
        input_envelope=envelope,
        prompt_utf8=prompt_bytes.decode("utf-8"),
        response_schema_utf8=response_schema_bytes.decode("utf-8"),
        input_shard_utf8=input_bytes.decode("utf-8"),
        correction=correction,
        request_id=f"inference-provider-request:{digest}",
        request_sha256=digest,
    )


def _bounded_provider_request_bytes(request: InferenceProviderRequest) -> bytes:
    content = request.canonical_bytes()
    if len(content) > MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1:
        raise ValueError("provider request exceeds the 3 MiB canonical v1 limit")
    return content


def _input_envelope(
    *,
    task: RecordedInferenceTask,
    contract: ManagedInferenceContractBinding,
    workload_id: str,
    workload_sha256: str,
    input_shard_id: str,
    input_shard_sha256: str,
    input_bytes: bytes,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
) -> tuple[InferenceInputEnvelope, tuple[InferenceArtifactPayload, ...]]:
    exact = (
        ("algorithms", "json", algorithm_manifest_bytes, contract.algorithm_manifest_sha256),
        ("prompts", "txt", prompt_bytes, contract.prompt_sha256),
        ("schemas", "json", response_schema_bytes, contract.response_schema_sha256),
    )
    payloads: list[InferenceArtifactPayload] = []
    for root, suffix, content, expected_sha in exact:
        if _bytes_sha256(content) != expected_sha:
            raise ValueError(f"{root} bytes differ from the exact inference contract")
        ref = _artifact(
            kind=ManagedArtifactKind.INFERENCE_INPUT,
            root=root,
            suffix=suffix,
            content=content,
        )
        payloads.append(
            InferenceArtifactPayload(artifact=ref, content_utf8=content.decode("utf-8"))
        )
    input_ref = _artifact(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        root="inputs",
        suffix="json",
        content=input_bytes,
    )
    if input_ref.sha256 != input_shard_sha256:
        raise ValueError("input shard bytes differ from its exact content hash")
    payloads.append(
        InferenceArtifactPayload(artifact=input_ref, content_utf8=input_bytes.decode("utf-8"))
    )
    refs = tuple(sorted((item.artifact for item in payloads), key=lambda item: item.artifact_id))
    values = {
        "schema_version": 1,
        "task": task.value,
        "algorithm_manifest_sha256": contract.algorithm_manifest_sha256,
        "contract_id": contract.contract_id,
        "contract_version": contract.contract_version,
        "provider": contract.provider,
        "model": contract.model,
        "prompt_sha256": contract.prompt_sha256,
        "response_schema_sha256": contract.response_schema_sha256,
        "workload_id": workload_id,
        "workload_sha256": workload_sha256,
        "input_shard_id": input_shard_id,
        "input_shard_sha256": input_shard_sha256,
        "input_artifacts": [item.model_dump(mode="json") for item in refs],
    }
    digest = _sha256(values)
    envelope = InferenceInputEnvelope(
        task=task,
        algorithm_manifest_sha256=contract.algorithm_manifest_sha256,
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        provider=contract.provider,
        model=contract.model,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        workload_id=workload_id,
        workload_sha256=workload_sha256,
        input_shard_id=input_shard_id,
        input_shard_sha256=input_shard_sha256,
        input_artifacts=refs,
        envelope_id=f"inference-input:{digest}",
        envelope_sha256=digest,
    )
    return envelope, tuple(sorted(payloads, key=lambda item: item.artifact.artifact_id))


def _classification_output(
    *,
    workload: ClassificationWorkload,
    shard: ClassificationInferenceShard,
    raw: str,
) -> ClassificationOutputShard:
    if not any(item == shard for item in workload.inference_shards):
        raise ValueError("classification input shard is not an exact workload member")
    response = ClassificationWireResponse.model_validate_json(raw)
    by_pair = {item.pair_id: item for item in response.decisions}
    expected = {item.candidate.pair_id: item for item in shard.pairs}
    if len(by_pair) != len(response.decisions) or set(by_pair) != set(expected):
        raise ValueError("classification response must cover every input pair exactly once")
    classifications: list[ClaimPairClassification] = []
    for pair_id in sorted(expected):
        decision = by_pair[pair_id]
        inference_pair = expected[pair_id]
        if decision.disposition == PairDisposition.SUPERSEDES:
            newer = decision.newer_revision_id
            if newer is None:
                raise ValueError("SUPERSEDES requires a newer endpoint")
            endpoints = {item.claim_revision_id: item for item in inference_pair.endpoint_revisions}
            if newer not in endpoints:
                raise ValueError("SUPERSEDES newer endpoint is absent from the input pair")
            older = next(item for key, item in endpoints.items() if key != newer)
            if endpoints[newer].declared_effective_from <= older.declared_effective_from:
                raise ValueError("SUPERSEDES newer endpoint must be temporally newer")
        classifications.append(
            ClaimPairClassification.create(
                candidate=inference_pair.candidate,
                endpoint_revisions=inference_pair.endpoint_revisions,
                disposition=decision.disposition,
                rationale=decision.rationale,
                confidence=decision.confidence,
                newer_revision_id=decision.newer_revision_id,
            )
        )
    return ClassificationOutputShard.create(
        workload=workload,
        input_shard=shard,
        classifications=tuple(classifications),
    )


def _dependency_output(
    *,
    workload: DependencyWorkload,
    shard: DependencyInferenceShard,
    raw: str,
) -> DependencyOutputShard:
    if not any(item == shard for item in workload.input_shards):
        raise ValueError("dependency input shard is not an exact workload member")
    response = DependencyWireResponse.model_validate_json(raw)
    by_id = {item.candidate_id: item for item in response.decisions}
    expected = {item.candidate_id: item for item in shard.candidates}
    if len(by_id) != len(response.decisions) or set(by_id) != set(expected):
        raise ValueError("dependency response must cover every input candidate exactly once")
    classifications: list[DependencyClassification] = []
    note = shard.downstream_note
    for candidate_id in sorted(expected):
        decision = by_id[candidate_id]
        spans = tuple(
            DocumentSpanReference(
                document_version_id=note.document.document_version_id,
                source_note_path=note.source_note_path,
                source_note_sha256=note.source_note_sha256,
                quote=note.source_note_utf8[item.start_char : item.end_char],
                start_char=item.start_char,
                end_char=item.end_char,
            )
            for item in decision.spans
        )
        classifications.append(
            DependencyClassification.create(
                input_shard=shard,
                candidate=expected[candidate_id],
                disposition=decision.disposition,
                rationale=decision.rationale,
                confidence=decision.confidence,
                dependency_kind=decision.dependency_kind,
                selected_downstream_claim_revision_ids=(
                    decision.selected_downstream_claim_revision_ids
                ),
                downstream_spans=spans,
            )
        )
    return _dependency_output_shard(
        workload=workload,
        shard=shard,
        classifications=tuple(classifications),
    )


def _impact_output(
    *,
    workload: ImpactWorkload,
    shard: ImpactInferenceShard,
    raw: str,
) -> ImpactOutputShard:
    if not any(item == shard for item in workload.input_shards):
        raise ValueError("impact input shard is not an exact workload member")
    response = ImpactWireResponse.model_validate_json(raw)
    by_question = {item.question_id: item for item in response.decisions}
    expected = {item.question_id: item for item in shard.questions}
    if len(by_question) != len(response.decisions) or set(by_question) != set(expected):
        raise ValueError("impact response must cover every input question exactly once")
    note = shard.target_note
    decisions: list[ImpactDecision] = []
    for question_id in sorted(expected):
        decision = by_question[question_id]
        spans = tuple(
            DocumentSpanReference(
                document_version_id=note.document.document_version_id,
                source_note_path=note.source_note_path,
                source_note_sha256=note.source_note_sha256,
                quote=note.source_note_utf8[item.start_char : item.end_char],
                start_char=item.start_char,
                end_char=item.end_char,
            )
            for item in decision.spans
        )
        decisions.append(
            ImpactDecision.create(
                input_shard=shard,
                question=expected[question_id],
                disposition=decision.disposition,
                evidence_spans=spans,
                attention_path_context_ids=decision.attention_path_context_ids,
                dependency_context_ids=decision.dependency_context_ids,
                rationale=decision.rationale,
            )
        )
    return ImpactOutputShard.create(
        workload=workload,
        input_shard=shard,
        decisions=tuple(decisions),
    )


def _dependency_output_shard(
    *,
    workload: DependencyWorkload,
    shard: DependencyInferenceShard,
    classifications: tuple[DependencyClassification, ...],
) -> DependencyOutputShard:
    exact_shard = next(
        (
            item
            for item in workload.input_shards
            if item.shard_id == shard.shard_id and item.shard_sha256 == shard.shard_sha256
        ),
        None,
    )
    if exact_shard != shard:
        raise ValueError("dependency output shard input is not an exact workload member")
    expected = {item.candidate_id: item for item in shard.candidates}
    canonical = tuple(sorted(classifications, key=lambda item: item.candidate_id))
    candidate_ids = tuple(item.candidate_id for item in canonical)
    if candidate_ids != tuple(sorted(expected)):
        raise ValueError("dependency output must classify every shard candidate exactly once")
    for classification in canonical:
        candidate = expected[classification.candidate_id]
        if classification.candidate_sha256 != candidate.candidate_sha256:
            raise ValueError("dependency output candidate SHA differs from the exact input")
        reconstructed = DependencyClassification.create(
            input_shard=shard,
            candidate=candidate,
            disposition=classification.disposition,
            rationale=classification.rationale,
            confidence=classification.confidence,
            dependency_kind=classification.dependency_kind,
            selected_downstream_claim_revision_ids=(
                classification.selected_downstream_claim_revision_ids
            ),
            downstream_spans=classification.downstream_spans,
        )
        if reconstructed != classification:
            raise ValueError("dependency output differs from exact candidate/shard reconstruction")
    payload = {
        "namespace": "mastervault.dependency-output-shard.v1",
        "schema_version": 1,
        "workload_id": workload.index.workload_id,
        "workload_sha256": workload.index.workload_sha256,
        "input_shard_id": shard.shard_id,
        "input_shard_sha256": shard.shard_sha256,
        "classifications": [item.model_dump(mode="json") for item in canonical],
    }
    digest = _sha256(payload)
    return DependencyOutputShard(
        workload_id=workload.index.workload_id,
        workload_sha256=workload.index.workload_sha256,
        input_shard_id=shard.shard_id,
        input_shard_sha256=shard.shard_sha256,
        classifications=canonical,
        output_shard_id=f"depout:{digest}",
        output_shard_sha256=digest,
    )


def _canonical_payload(value: str, *, namespace: str) -> dict[str, Any]:
    decoded: Any = json.loads(value)
    if not isinstance(decoded, dict) or decoded.get("namespace") != namespace:
        raise ValueError("replay output has the wrong canonical namespace")
    if canonical_json_bytes(decoded) != value.encode("utf-8"):
        raise ValueError("replay output bytes are not exact canonical JSON")
    return decoded


def _rehydrate_classification_output(
    value: str,
    *,
    workload: ClassificationWorkload,
    shard: ClassificationInferenceShard,
) -> ClassificationOutputShard:
    payload = _canonical_payload(
        value,
        namespace="mastervault.classification-output-shard.v1",
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("classification replay payload requires an item list")
    classifications: list[ClaimPairClassification] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("classification"), dict):
            raise ValueError("classification replay item has the wrong shape")
        classifications.append(
            ClaimPairClassification.model_validate_json(
                canonical_json_bytes(item["classification"])
            )
        )
    output = ClassificationOutputShard.create(
        workload=workload,
        input_shard=shard,
        classifications=tuple(classifications),
    )
    if output.canonical_bytes() != value.encode("utf-8"):
        raise ValueError("classification replay payload differs from reconstructed output")
    return output


def _rehydrate_dependency_output(
    value: str,
    *,
    workload: DependencyWorkload,
    shard: DependencyInferenceShard,
) -> DependencyOutputShard:
    payload = _canonical_payload(
        value,
        namespace="mastervault.dependency-output-shard.v1",
    )
    raw_classifications = payload.get("classifications")
    if not isinstance(raw_classifications, list):
        raise ValueError("dependency replay payload requires a classification list")
    candidates = {item.candidate_id: item for item in shard.candidates}
    classifications: list[DependencyClassification] = []
    for raw_classification in raw_classifications:
        parsed = DependencyClassification.model_validate_json(
            canonical_json_bytes(raw_classification)
        )
        candidate = candidates.get(parsed.candidate_id)
        if candidate is None:
            raise ValueError("dependency replay classification is absent from the input shard")
        reconstructed = DependencyClassification.create(
            input_shard=shard,
            candidate=candidate,
            disposition=parsed.disposition,
            rationale=parsed.rationale,
            confidence=parsed.confidence,
            dependency_kind=parsed.dependency_kind,
            selected_downstream_claim_revision_ids=(parsed.selected_downstream_claim_revision_ids),
            downstream_spans=parsed.downstream_spans,
        )
        if reconstructed != parsed:
            raise ValueError("dependency replay classification differs from exact reconstruction")
        classifications.append(reconstructed)
    output = _dependency_output_shard(
        workload=workload,
        shard=shard,
        classifications=tuple(classifications),
    )
    if output.canonical_bytes() != value.encode("utf-8"):
        raise ValueError("dependency replay payload differs from reconstructed output")
    return output


def _rehydrate_impact_output(
    value: str,
    *,
    workload: ImpactWorkload,
    shard: ImpactInferenceShard,
) -> ImpactOutputShard:
    payload = _canonical_payload(
        value,
        namespace="mastervault.actual-impact-output-shard.v1",
    )
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("impact replay payload requires a decision list")
    questions = {item.question_id: item for item in shard.questions}
    decisions: list[ImpactDecision] = []
    for raw_decision in raw_decisions:
        parsed = ImpactDecision.model_validate_json(canonical_json_bytes(raw_decision))
        question = questions.get(parsed.question_id)
        if question is None:
            raise ValueError("impact replay decision is absent from the input shard")
        reconstructed = ImpactDecision.create(
            input_shard=shard,
            question=question,
            disposition=parsed.disposition,
            evidence_spans=parsed.evidence_spans,
            attention_path_context_ids=parsed.attention_path_context_ids,
            dependency_context_ids=parsed.dependency_context_ids,
            rationale=parsed.rationale,
        )
        if reconstructed != parsed:
            raise ValueError("impact replay decision differs from exact local reconstruction")
        decisions.append(reconstructed)
    output = ImpactOutputShard.create(
        workload=workload,
        input_shard=shard,
        decisions=tuple(decisions),
    )
    if output.canonical_bytes() != value.encode("utf-8"):
        raise ValueError("impact replay payload differs from reconstructed output")
    return output


def _attempt(
    *,
    ordinal: int,
    call: ProviderCallResult,
    raw_artifact: ManagedArtifactRef,
    output_artifact: ManagedArtifactRef | None,
    error: str | None,
) -> InferenceAttemptEvidence:
    values = {
        "schema_version": 1,
        "ordinal": ordinal,
        "provider": call.provider,
        "model": call.model,
        "provider_request_id": call.provider_request_id,
        "usage": call.usage.model_dump(mode="json"),
        "raw_output_artifact": raw_artifact.model_dump(mode="json"),
        "accepted": output_artifact is not None,
        "validation_error": error,
        "validated_output_artifact": (
            output_artifact.model_dump(mode="json") if output_artifact else None
        ),
    }
    digest = _sha256(values)
    return InferenceAttemptEvidence(
        ordinal=ordinal,
        provider=call.provider,
        model=call.model,
        provider_request_id=call.provider_request_id,
        usage=call.usage,
        raw_output_artifact=raw_artifact,
        accepted=output_artifact is not None,
        validation_error=error,
        validated_output_artifact=output_artifact,
        attempt_id=f"inference-attempt:{digest}",
        attempt_sha256=digest,
    )


def _execute_live(
    *,
    task: RecordedInferenceTask,
    contract: ManagedInferenceContractBinding,
    envelope: InferenceInputEnvelope,
    base_artifacts: tuple[InferenceArtifactPayload, ...],
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    input_bytes: bytes,
    provider: RecordedInferenceProvider,
    build_output: Any,
) -> RecordedInferenceOutcome:
    attempts: list[InferenceAttemptEvidence] = []
    artifacts = list(base_artifacts)
    correction: InferenceCorrection | None = None
    output: ClassificationOutputShard | DependencyOutputShard | ImpactOutputShard | None = None
    final_call: ProviderCallResult | None = None
    for ordinal in range(1, MAX_ATTEMPTS_V1 + 1):
        request = _provider_request(
            ordinal=ordinal,
            task=task,
            envelope=envelope,
            prompt_bytes=prompt_bytes,
            response_schema_bytes=response_schema_bytes,
            input_bytes=input_bytes,
            correction=correction,
        )
        try:
            request_bytes = _bounded_provider_request_bytes(request)
        except ValueError as caught:
            raise InferenceExecutionFailed(
                str(caught),
                attempts=tuple(attempts),
                artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact.artifact_id)),
            ) from caught
        call = ProviderCallResult.model_validate_json(
            canonical_json_bytes(provider.complete(request=request_bytes).model_dump(mode="json"))
        )
        raw = call.raw_output_utf8.encode("utf-8")
        raw_ref = _artifact(
            kind=ManagedArtifactKind.INFERENCE_OUTPUT,
            root="raw",
            suffix="json",
            content=raw,
        )
        artifacts.append(
            InferenceArtifactPayload(artifact=raw_ref, content_utf8=call.raw_output_utf8)
        )
        error: str | None = None
        output_ref: ManagedArtifactRef | None = None
        evidence_error: str | None = None
        if call.provider != contract.provider or call.model != contract.model:
            evidence_error = "provider/model evidence differs from the exact contract"
        elif any(item.provider_request_id == call.provider_request_id for item in attempts):
            evidence_error = "provider request ID was reused across LIVE attempts"
        if evidence_error is not None:
            attempts.append(
                _attempt(
                    ordinal=ordinal,
                    call=call,
                    raw_artifact=raw_ref,
                    output_artifact=None,
                    error=evidence_error,
                )
            )
            raise InferenceExecutionFailed(
                "provider evidence failed the exact LIVE contract",
                attempts=tuple(attempts),
                artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact.artifact_id)),
            )
        try:
            output = build_output(call.raw_output_utf8)
            output_bytes = output.canonical_bytes()
            output_ref = _artifact(
                kind=ManagedArtifactKind.INFERENCE_OUTPUT,
                root="outputs",
                suffix="json",
                content=output_bytes,
            )
            artifacts.append(
                InferenceArtifactPayload(
                    artifact=output_ref,
                    content_utf8=output_bytes.decode("utf-8"),
                )
            )
        except (ValueError, TypeError) as caught:
            error = _bounded_error(caught)
        attempts.append(
            _attempt(
                ordinal=ordinal,
                call=call,
                raw_artifact=raw_ref,
                output_artifact=output_ref,
                error=error,
            )
        )
        if output_ref is not None:
            final_call = call
            break
        assert error is not None
        correction = InferenceCorrection(
            previous_raw_output_utf8=call.raw_output_utf8,
            validation_error=error,
        )
    if final_call is None or output is None or attempts[-1].validated_output_artifact is None:
        raise InferenceExecutionFailed(
            "provider failed both bounded inference attempts",
            attempts=tuple(attempts),
            artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact.artifact_id)),
        )
    final = attempts[-1]
    final_output_artifact = final.validated_output_artifact
    assert final_output_artifact is not None
    receipt = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=final_call.provider_request_id,
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=envelope.input_artifacts,
        input_envelope_sha256=envelope.envelope_sha256,
        raw_output_sha256=final.raw_output_artifact.sha256,
        validated_output_sha256=final_output_artifact.sha256,
        usage=final_call.usage,
    )
    receipt_ref = _receipt_artifact(receipt)
    receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json"))
    artifacts.append(
        InferenceArtifactPayload(
            artifact=receipt_ref,
            content_utf8=receipt_bytes.decode("utf-8"),
        )
    )
    values = {
        "schema_version": 1,
        "task": task.value,
        "contract": contract.model_dump(mode="json"),
        "input_envelope": envelope.model_dump(mode="json"),
        "attempts": [item.model_dump(mode="json") for item in attempts],
        "raw_output_artifact": final.raw_output_artifact.model_dump(mode="json"),
        "validated_output_artifact": final_output_artifact.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "receipt_artifact": receipt_ref.model_dump(mode="json"),
        "replay_source_execution_sha256": None,
    }
    digest = _sha256(values)
    execution = RecordedInferenceExecution(
        task=task,
        contract=contract,
        input_envelope=envelope,
        attempts=tuple(attempts),
        raw_output_artifact=final.raw_output_artifact,
        validated_output_artifact=final_output_artifact,
        receipt=receipt,
        receipt_artifact=receipt_ref,
        replay_source_execution_sha256=None,
        execution_id=f"inference-exec:{digest}",
        execution_sha256=digest,
    )
    output_fields = _outcome_output_fields(task=task, output=output)
    return RecordedInferenceOutcome(
        execution=execution,
        artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact.artifact_id)),
        **output_fields,
    )


def _execute_replay(
    *,
    task: RecordedInferenceTask,
    contract: ManagedInferenceContractBinding,
    envelope: InferenceInputEnvelope,
    base_artifacts: tuple[InferenceArtifactPayload, ...],
    resolver: ReplayEvidenceResolver,
    source_receipt_artifact: ManagedArtifactRef,
    validate_output: Any,
) -> RecordedInferenceOutcome:
    source = RecordedInferenceOutcome.model_validate_json(
        canonical_json_bytes(
            resolver.resolve_replay_evidence(receipt_artifact=source_receipt_artifact).model_dump(
                mode="json"
            )
        )
    )
    prior = source.execution
    if prior.contract.mode != InferenceExecutionMode.LIVE or prior.receipt.mode != (
        InferenceExecutionMode.LIVE
    ):
        raise ValueError("replay source must be one prior LIVE execution")
    if prior.receipt_artifact != source_receipt_artifact:
        raise ValueError("resolved replay evidence differs from requested receipt artifact")
    comparable_contract = (
        "algorithm_manifest_sha256",
        "contract_id",
        "contract_version",
        "provider",
        "model",
        "prompt_sha256",
        "response_schema_sha256",
    )
    if any(
        getattr(prior.contract, item) != getattr(contract, item) for item in comparable_contract
    ):
        raise ValueError("replay contract differs from prior LIVE execution")
    if prior.task != task or prior.input_envelope != envelope:
        raise ValueError("replay task/input differs from prior LIVE execution")
    source_payloads = {item.artifact.artifact_id: item for item in source.artifacts}
    required_source_refs = (
        prior.raw_output_artifact,
        prior.validated_output_artifact,
        prior.receipt_artifact,
    )
    if not all(item.artifact_id in source_payloads for item in required_source_refs):
        raise ValueError("resolved LIVE evidence omits required artifact bytes")
    raw_payload = source_payloads[prior.raw_output_artifact.artifact_id]
    output_payload = source_payloads[prior.validated_output_artifact.artifact_id]
    source_receipt_payload = source_payloads[prior.receipt_artifact.artifact_id]
    output = validate_output(output_payload.content_utf8)
    if output.canonical_bytes() != output_payload.content_utf8.encode("utf-8"):
        raise ValueError("replay validated bytes do not exactly rehydrate current output contract")
    output_ref = _artifact(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        root="outputs",
        suffix="json",
        content=output.canonical_bytes(),
    )
    raw_ref = _artifact(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        root="raw",
        suffix="json",
        content=raw_payload.content_utf8.encode("utf-8"),
    )
    if output_ref != prior.validated_output_artifact or raw_ref != prior.raw_output_artifact:
        raise ValueError("replay bytes differ from prior LIVE artifacts")
    replay_receipt = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=source_receipt_artifact.sha256,
        replay_source_receipt_artifact=source_receipt_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=envelope.input_artifacts,
        input_envelope_sha256=envelope.envelope_sha256,
        raw_output_sha256=raw_ref.sha256,
        validated_output_sha256=output_ref.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    replay_receipt.verify_replay_source(prior.receipt)
    receipt_ref = _receipt_artifact(replay_receipt)
    receipt_bytes = canonical_json_bytes(replay_receipt.model_dump(mode="json"))
    artifacts = [*base_artifacts, raw_payload, output_payload, source_receipt_payload]
    artifacts.append(
        InferenceArtifactPayload(
            artifact=receipt_ref,
            content_utf8=receipt_bytes.decode("utf-8"),
        )
    )
    values: dict[str, Any] = {
        "schema_version": 1,
        "task": task.value,
        "contract": contract.model_dump(mode="json"),
        "input_envelope": envelope.model_dump(mode="json"),
        "attempts": [],
        "raw_output_artifact": raw_ref.model_dump(mode="json"),
        "validated_output_artifact": output_ref.model_dump(mode="json"),
        "receipt": replay_receipt.model_dump(mode="json"),
        "receipt_artifact": receipt_ref.model_dump(mode="json"),
        "replay_source_execution_sha256": prior.execution_sha256,
    }
    digest = _sha256(values)
    execution = RecordedInferenceExecution(
        task=task,
        contract=contract,
        input_envelope=envelope,
        attempts=(),
        raw_output_artifact=raw_ref,
        validated_output_artifact=output_ref,
        receipt=replay_receipt,
        receipt_artifact=receipt_ref,
        replay_source_execution_sha256=prior.execution_sha256,
        execution_id=f"inference-exec:{digest}",
        execution_sha256=digest,
    )
    output_fields = _outcome_output_fields(task=task, output=output)
    return RecordedInferenceOutcome(
        execution=execution,
        artifacts=tuple(
            sorted(
                {item.artifact.artifact_id: item for item in artifacts}.values(),
                key=lambda item: item.artifact.artifact_id,
            )
        ),
        **output_fields,
    )


def run_classification_inference(
    *,
    contract: ManagedInferenceContractBinding,
    workload: ClassificationWorkload,
    input_shard: ClassificationInferenceShard,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    provider: RecordedInferenceProvider | None = None,
    replay_resolver: ReplayEvidenceResolver | None = None,
    replay_source_receipt_artifact: ManagedArtifactRef | None = None,
) -> RecordedInferenceOutcome:
    if not any(item == input_shard for item in workload.inference_shards):
        raise ValueError("classification input shard is not an exact workload member")
    input_bytes = canonical_json_bytes(input_shard._payload())
    envelope, artifacts = _input_envelope(
        task=RecordedInferenceTask.CLASSIFICATION,
        contract=contract,
        workload_id=workload.workload_id,
        workload_sha256=workload.workload_sha256,
        input_shard_id=input_shard.shard_id,
        input_shard_sha256=input_shard.shard_sha256,
        input_bytes=input_bytes,
        algorithm_manifest_bytes=algorithm_manifest_bytes,
        prompt_bytes=prompt_bytes,
        response_schema_bytes=response_schema_bytes,
    )

    def build(raw: str) -> ClassificationOutputShard:
        return _classification_output(workload=workload, shard=input_shard, raw=raw)

    if contract.mode == InferenceExecutionMode.LIVE:
        if (
            provider is None
            or replay_resolver is not None
            or replay_source_receipt_artifact is not None
        ):
            raise ValueError("LIVE execution requires only one provider")
        return _execute_live(
            task=RecordedInferenceTask.CLASSIFICATION,
            contract=contract,
            envelope=envelope,
            base_artifacts=artifacts,
            prompt_bytes=prompt_bytes,
            response_schema_bytes=response_schema_bytes,
            input_bytes=input_bytes,
            provider=provider,
            build_output=build,
        )
    if provider is not None or replay_resolver is None or replay_source_receipt_artifact is None:
        raise ValueError("REPLAY execution requires only resolver and exact source receipt")
    return _execute_replay(
        task=RecordedInferenceTask.CLASSIFICATION,
        contract=contract,
        envelope=envelope,
        base_artifacts=artifacts,
        resolver=replay_resolver,
        source_receipt_artifact=replay_source_receipt_artifact,
        validate_output=lambda value: _rehydrate_classification_output(
            value,
            workload=workload,
            shard=input_shard,
        ),
    )


def run_dependency_inference(
    *,
    contract: ManagedInferenceContractBinding,
    workload: DependencyWorkload,
    input_shard: DependencyInferenceShard,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    provider: RecordedInferenceProvider | None = None,
    replay_resolver: ReplayEvidenceResolver | None = None,
    replay_source_receipt_artifact: ManagedArtifactRef | None = None,
) -> RecordedInferenceOutcome:
    if not any(item == input_shard for item in workload.input_shards):
        raise ValueError("dependency input shard is not an exact workload member")
    input_bytes = input_shard.canonical_bytes()
    envelope, artifacts = _input_envelope(
        task=RecordedInferenceTask.DEPENDENCY,
        contract=contract,
        workload_id=workload.index.workload_id,
        workload_sha256=workload.index.workload_sha256,
        input_shard_id=input_shard.shard_id,
        input_shard_sha256=input_shard.shard_sha256,
        input_bytes=input_bytes,
        algorithm_manifest_bytes=algorithm_manifest_bytes,
        prompt_bytes=prompt_bytes,
        response_schema_bytes=response_schema_bytes,
    )

    def build(raw: str) -> DependencyOutputShard:
        return _dependency_output(workload=workload, shard=input_shard, raw=raw)

    if contract.mode == InferenceExecutionMode.LIVE:
        if (
            provider is None
            or replay_resolver is not None
            or replay_source_receipt_artifact is not None
        ):
            raise ValueError("LIVE execution requires only one provider")
        return _execute_live(
            task=RecordedInferenceTask.DEPENDENCY,
            contract=contract,
            envelope=envelope,
            base_artifacts=artifacts,
            prompt_bytes=prompt_bytes,
            response_schema_bytes=response_schema_bytes,
            input_bytes=input_bytes,
            provider=provider,
            build_output=build,
        )
    if provider is not None or replay_resolver is None or replay_source_receipt_artifact is None:
        raise ValueError("REPLAY execution requires only resolver and exact source receipt")
    return _execute_replay(
        task=RecordedInferenceTask.DEPENDENCY,
        contract=contract,
        envelope=envelope,
        base_artifacts=artifacts,
        resolver=replay_resolver,
        source_receipt_artifact=replay_source_receipt_artifact,
        validate_output=lambda value: _rehydrate_dependency_output(
            value,
            workload=workload,
            shard=input_shard,
        ),
    )


def run_impact_inference(
    *,
    contract: ManagedInferenceContractBinding,
    workload: ImpactWorkload,
    input_shard: ImpactInferenceShard,
    algorithm_manifest_bytes: bytes,
    prompt_bytes: bytes,
    response_schema_bytes: bytes,
    provider: RecordedInferenceProvider | None = None,
    replay_resolver: ReplayEvidenceResolver | None = None,
    replay_source_receipt_artifact: ManagedArtifactRef | None = None,
) -> RecordedInferenceOutcome:
    if not any(item == input_shard for item in workload.input_shards):
        raise ValueError("impact input shard is not an exact workload member")
    input_bytes = input_shard.canonical_bytes()
    envelope, artifacts = _input_envelope(
        task=RecordedInferenceTask.IMPACT,
        contract=contract,
        workload_id=workload.index.workload_id,
        workload_sha256=workload.index.workload_sha256,
        input_shard_id=input_shard.shard_id,
        input_shard_sha256=input_shard.shard_sha256,
        input_bytes=input_bytes,
        algorithm_manifest_bytes=algorithm_manifest_bytes,
        prompt_bytes=prompt_bytes,
        response_schema_bytes=response_schema_bytes,
    )

    def build(raw: str) -> ImpactOutputShard:
        return _impact_output(workload=workload, shard=input_shard, raw=raw)

    if contract.mode == InferenceExecutionMode.LIVE:
        if (
            provider is None
            or replay_resolver is not None
            or replay_source_receipt_artifact is not None
        ):
            raise ValueError("LIVE execution requires only one provider")
        return _execute_live(
            task=RecordedInferenceTask.IMPACT,
            contract=contract,
            envelope=envelope,
            base_artifacts=artifacts,
            prompt_bytes=prompt_bytes,
            response_schema_bytes=response_schema_bytes,
            input_bytes=input_bytes,
            provider=provider,
            build_output=build,
        )
    if provider is not None or replay_resolver is None or replay_source_receipt_artifact is None:
        raise ValueError("REPLAY execution requires only resolver and exact source receipt")
    return _execute_replay(
        task=RecordedInferenceTask.IMPACT,
        contract=contract,
        envelope=envelope,
        base_artifacts=artifacts,
        resolver=replay_resolver,
        source_receipt_artifact=replay_source_receipt_artifact,
        validate_output=lambda value: _rehydrate_impact_output(
            value,
            workload=workload,
            shard=input_shard,
        ),
    )


__all__ = [
    "ClassificationWireDecision",
    "ClassificationWireResponse",
    "DependencySpanWireDecision",
    "DependencyWireDecision",
    "DependencyWireResponse",
    "ImpactSpanWireDecision",
    "ImpactWireDecision",
    "ImpactWireResponse",
    "InferenceArtifactPayload",
    "InferenceAttemptEvidence",
    "InferenceCorrection",
    "InferenceExecutionFailed",
    "InferenceInputEnvelope",
    "InferenceProviderRequest",
    "MAX_OUTCOME_ARTIFACTS_V1",
    "MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1",
    "MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1",
    "MAX_PROVIDER_OUTPUT_BYTES_V1",
    "MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1",
    "ProviderCallResult",
    "RecordedInferenceExecution",
    "RecordedInferenceOutcome",
    "RecordedInferenceProvider",
    "RecordedInferenceTask",
    "ReplayEvidenceResolver",
    "run_classification_inference",
    "run_dependency_inference",
    "run_impact_inference",
]
