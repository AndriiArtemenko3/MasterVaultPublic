"""Pure, content-addressed contracts for managed revision review.

The contracts deliberately perform no filesystem or database I/O.  A producer
may assert that byte and projection checks passed, but the later authoritative
store MUST reopen every referenced artifact, repeat those checks, and compare
the resulting attestations before publishing a generation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from mastervault.change_control.analysis_binding import AnalysisBootstrapBinding
from mastervault.change_control.models import (
    CONTENT_ID_PATTERNS,
    SHA256_PATTERN,
    DocumentVersionMetadata,
    VersionedClaimRevision,
    canonical_json_bytes,
    normalize_logical_key,
)
from mastervault.change_control.review import normalize_actor_id, normalize_review_rationale
from mastervault.change_control.workspace_bootstrap import (
    LegacyIndexReadinessReceipt,
    WorkspaceBootstrapIntent,
    WorkspaceInventoryReceipt,
)

MAX_MANAGED_TARGETS_V1 = 16
MAX_MANAGED_REVISION_PLANS_V1 = 8
MAX_MANAGED_HUNKS_PER_PLAN_V1 = 16
MAX_MANAGED_HUNKS_PER_BUNDLE_V1 = 64
MAX_MANAGED_CITATIONS_PER_HUNK_V1 = 16
MAX_MANAGED_INFERENCE_INPUTS_V1 = 32
MAX_MANAGED_INFERENCE_INPUT_BYTES_V1 = 1024 * 1024
MAX_MANAGED_ARTIFACT_BYTES_V1 = 256 * 1024
MAX_MANAGED_PROJECTION_CLAIMS_V1 = 256
MAX_MANAGED_RECONCILIATION_ENTRIES_V1 = 512
MAX_MANAGED_CHANGED_CLAIMS_V1 = 16
MAX_MANAGED_GLOBAL_RELEVANT_CLAIMS_V1 = 256
MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1 = 1024 * 1024
MAX_MANAGED_DECISION_CANONICAL_BYTES_V1 = 1024 * 1024
MAX_ATTESTED_TEXT_BYTES_V1 = 64 * 1024
MAX_MANAGED_CITATION_QUOTE_BYTES_V1 = 8 * 1024
MAX_MANAGED_PATH_BYTES_V1 = 1024
MAX_MANAGED_LOGICAL_KEY_BYTES_V1 = 512
MAX_MANAGED_CLAIM_TEXT_BYTES_V1 = 64 * 1024
MAX_MANAGED_SCOPES_PER_CLAIM_V1 = 16
MAX_MANAGED_EVIDENCE_REFS_PER_CLAIM_V1 = 16
MAX_MANAGED_EVIDENCE_QUOTE_BYTES_V1 = 8 * 1024
MAX_MANAGED_IMPACT_OUTPUT_REFS_V1 = 16

_ID_PATTERNS = {
    name: re.compile(rf"^{name}:[0-9a-f]{{64}}$")
    for name in (
        "martifact",
        "mdestination",
        "minference",
        "mcontract",
        "mcitation",
        "mhunk",
        "mpatch",
        "mprojection",
        "mclaims",
        "manalysis",
        "mimpactevidence",
        "mrevisionadmission",
        "mgoverningsource",
        "mtargetanalysis",
        "mhead",
        "mgeneration",
        "mzeromanifest",
        "mauthority",
        "mauthorityplan",
        "mrun",
        "mreviewbase",
        "mplan",
        "mproposal",
        "mnochange",
        "mtarget",
        "mbundle",
        "mrequest",
        "mrequestrecord",
        "mrequestreceipt",
        "mdecision",
        "mdecisionrecord",
        "mgenerationmanifest",
        "mreceipt",
        "mview",
    )
}
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_INCOMING_ID_RE = re.compile(r"^incoming:[0-9a-f]{64}$")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _content_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{_sha256(payload)}"


def _exact_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(SHA256_PATTERN, value) is None:
        raise ValueError(f"{label} must be exact lowercase 64-hex SHA-256")
    return value


def _exact_artifact_byte_count(value: Any, *, label: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_MANAGED_ARTIFACT_BYTES_V1:
        raise ValueError(
            f"{label} must be a non-bool integer between 1 and MAX_MANAGED_ARTIFACT_BYTES_V1"
        )
    return value


def _exact_operation_id(value: str, *, label: str = "operation_id") -> str:
    if not isinstance(value, str) or _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} uses an unsafe or unsupported shape")
    return value


def _exact_logical_key(value: str, *, label: str) -> str:
    if len(value.encode("utf-8")) > MAX_MANAGED_LOGICAL_KEY_BYTES_V1:
        raise ValueError(f"{label} exceeds MAX_MANAGED_LOGICAL_KEY_BYTES_V1 UTF-8 byte limit")
    normalized = normalize_logical_key(value)
    if value != normalized:
        raise ValueError(f"{label} must already be normalized")
    return value


def _safe_path(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_MANAGED_PATH_BYTES_V1:
        raise ValueError("path exceeds MAX_MANAGED_PATH_BYTES_V1 UTF-8 byte limit")
    if "\\" in value or any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("paths must not contain whitespace, controls, or backslashes")
    candidate = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or not candidate.parts
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or any(part.startswith(".") for part in candidate.parts)
        or any(part.casefold() == "golden" for part in candidate.parts)
    ):
        raise ValueError(f"must be a safe exact relative path, got {value!r}")
    if candidate.as_posix() != value:
        raise ValueError("paths must already use exact POSIX canonical form")
    return value


def _is_managed_review_staging_path(value: str) -> bool:
    return PurePosixPath(value).parts[:2] == ("staging", "managed-review")


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


def _bounded_exact_text(value: str, *, label: str, maximum: int = 4000) -> str:
    if value != " ".join(value.split()) or not value or len(value) > maximum:
        raise ValueError(f"{label} must be canonical non-empty text of at most {maximum} chars")
    return value


def _preflight_collection(
    values: tuple[Any, ...],
    *,
    label: str,
    maximum: int,
    minimum: int = 0,
    unique_key: Any | None = None,
) -> None:
    """Reject authority-significant collection excess/duplicates before sorting or hashing."""

    count = len(values)
    if count < minimum or count > maximum:
        raise ValueError(f"{label} count must be between {minimum} and {maximum}")
    if unique_key is not None:
        keys = tuple(unique_key(item) for item in values)
        if len(set(keys)) != count:
            raise ValueError(f"{label} must be unique before canonical ordering")


def _preflight_claim_texts(claims: tuple[VersionedClaimRevision, ...], *, label: str) -> None:
    for claim in claims:
        if len(claim.statement.encode("utf-8")) > MAX_MANAGED_CLAIM_TEXT_BYTES_V1:
            raise ValueError(
                f"{label} claim text exceeds MAX_MANAGED_CLAIM_TEXT_BYTES_V1 UTF-8 byte limit"
            )
        if len(claim.scopes) > MAX_MANAGED_SCOPES_PER_CLAIM_V1:
            raise ValueError(f"{label} claim exceeds managed scope count limit")
        for scope in claim.scopes:
            _exact_logical_key(scope, label=f"{label} claim scope")
        _safe_path(claim.document.source_path)
        _safe_path(claim.source.source_note_path)
        if len(claim.source.source_claim_id.encode("utf-8")) > MAX_MANAGED_LOGICAL_KEY_BYTES_V1:
            raise ValueError(f"{label} source claim ID exceeds managed logical-key byte limit")
        if len(claim.source.evidence) > MAX_MANAGED_EVIDENCE_REFS_PER_CLAIM_V1:
            raise ValueError(f"{label} claim exceeds managed evidence-reference count limit")
        for evidence in claim.source.evidence:
            if len(evidence.quote.encode("utf-8")) > MAX_MANAGED_EVIDENCE_QUOTE_BYTES_V1:
                raise ValueError(f"{label} evidence quote exceeds managed UTF-8 byte limit")


def _preflight_raw_claim_payloads(value: Any, *, label: str) -> None:
    if not isinstance(value, list | tuple):
        return
    if len(value) > MAX_MANAGED_PROJECTION_CLAIMS_V1:
        raise ValueError(f"{label} exceeds managed projection claim count limit")
    for claim in value:
        if isinstance(claim, VersionedClaimRevision):
            _preflight_claim_texts((claim,), label=label)
            continue
        if not isinstance(claim, dict):
            continue
        statement = claim.get("statement")
        if isinstance(statement, str) and len(statement.encode("utf-8")) > (
            MAX_MANAGED_CLAIM_TEXT_BYTES_V1
        ):
            raise ValueError(f"{label} claim text exceeds managed UTF-8 byte limit")
        scopes = claim.get("scopes")
        if isinstance(scopes, list | tuple):
            if len(scopes) > MAX_MANAGED_SCOPES_PER_CLAIM_V1:
                raise ValueError(f"{label} claim exceeds managed scope count limit")
            for scope in scopes:
                if isinstance(scope, str):
                    _exact_logical_key(scope, label=f"{label} claim scope")
        document = claim.get("document")
        if isinstance(document, dict):
            source_path = document.get("source_path")
            if isinstance(source_path, str):
                _safe_path(source_path)
            for key in ("document_id", "document_family", "version_label"):
                item = document.get(key)
                if isinstance(item, str) and len(item.encode("utf-8")) > (
                    MAX_MANAGED_LOGICAL_KEY_BYTES_V1
                ):
                    raise ValueError(f"{label} document key exceeds managed byte limit")
        source = claim.get("source")
        if isinstance(source, dict):
            note_path = source.get("source_note_path")
            if isinstance(note_path, str):
                _safe_path(note_path)
            source_claim_id = source.get("source_claim_id")
            if isinstance(source_claim_id, str) and len(source_claim_id.encode("utf-8")) > (
                MAX_MANAGED_LOGICAL_KEY_BYTES_V1
            ):
                raise ValueError(f"{label} source claim ID exceeds managed byte limit")
            evidence = source.get("evidence")
            if isinstance(evidence, list | tuple):
                if len(evidence) > MAX_MANAGED_EVIDENCE_REFS_PER_CLAIM_V1:
                    raise ValueError(f"{label} claim exceeds managed evidence count limit")
                for reference in evidence:
                    if isinstance(reference, dict):
                        quote = reference.get("quote")
                        if isinstance(quote, str) and len(quote.encode("utf-8")) > (
                            MAX_MANAGED_EVIDENCE_QUOTE_BYTES_V1
                        ):
                            raise ValueError(f"{label} evidence quote exceeds managed byte limit")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_canonical_json[ModelT: BaseModel](model: type[ModelT], payload: Any) -> ModelT:
    """Validate a persisted/canonical boundary without Python-side coercion."""

    def jsonable(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple | list):
            return [jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: jsonable(item) for key, item in value.items()}
        return value

    return model.model_validate_json(canonical_json_bytes(jsonable(payload)))


class ManagedArtifactKind(StrEnum):
    RAW_SOURCE = "managed-raw-source"
    SOURCE_NOTE = "versioned-source-note"
    INFERENCE_INPUT = "inference-input"
    INFERENCE_OUTPUT = "inference-output"
    INFERENCE_RECEIPT = "inference-receipt"


class PublicationKind(StrEnum):
    RAW_SOURCE = "raw-source"
    SOURCE_NOTE = "source-note"


class InferenceExecutionMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class AttestationStatus(StrEnum):
    PASSED_REQUIRES_STORE_REVALIDATION = "passed-requires-store-revalidation"


class ManagedArtifactRef(_StrictFrozenModel):
    """An immutable locator/hash receipt; this model does not read the bytes."""

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    kind: ManagedArtifactKind
    path: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(ge=1, le=MAX_MANAGED_ARTIFACT_BYTES_V1)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _safe_path(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"artifact_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.artifact_id != _content_id("martifact", self._payload()):
            raise ValueError("artifact ID does not match its exact locator/hash receipt")
        return self

    @classmethod
    def create(cls, *, kind: ManagedArtifactKind, path: str, sha256: str, byte_count: int) -> Self:
        sha256 = _exact_sha256(sha256, label="artifact sha256")
        byte_count = _exact_artifact_byte_count(byte_count, label="artifact byte_count")
        values = {
            "schema_version": 1,
            "kind": kind.value,
            "path": _safe_path(path),
            "sha256": sha256,
            "byte_count": byte_count,
        }
        return _validate_canonical_json(
            cls, {"artifact_id": _content_id("martifact", values), **values}
        )


class PublicationDestination(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    destination_id: str = Field(pattern=_ID_PATTERNS["mdestination"].pattern)
    target_key: str
    kind: PublicationKind
    path: str
    expected_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_byte_count: int = Field(ge=1, le=MAX_MANAGED_ARTIFACT_BYTES_V1)

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_logical_key(value, label="target_key")

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _safe_path(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"destination_id"})

    @model_validator(mode="after")
    def _identity_and_path(self) -> Self:
        root = "managed_sources" if self.kind == PublicationKind.RAW_SOURCE else "vault"
        expected = f"{root}/{self.target_key}/{self.target_key}-{self.expected_sha256}.md"
        if self.path != expected:
            raise ValueError("publication destination must bind target and full SHA in exact path")
        if self.destination_id != _content_id("mdestination", self._payload()):
            raise ValueError("publication destination ID does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        target_key: str,
        kind: PublicationKind,
        expected_sha256: str,
        expected_byte_count: int,
    ) -> Self:
        expected_sha256 = _exact_sha256(expected_sha256, label="publication expected_sha256")
        expected_byte_count = _exact_artifact_byte_count(
            expected_byte_count, label="publication expected_byte_count"
        )
        target = _exact_logical_key(target_key, label="target_key")
        root = "managed_sources" if kind == PublicationKind.RAW_SOURCE else "vault"
        path = f"{root}/{target}/{target}-{expected_sha256}.md"
        values = {
            "schema_version": 1,
            "target_key": target,
            "kind": kind.value,
            "path": path,
            "expected_sha256": expected_sha256,
            "expected_byte_count": expected_byte_count,
        }
        return _validate_canonical_json(
            cls, {"destination_id": _content_id("mdestination", values), **values}
        )


def _artifact_locator_binding(artifact: ManagedArtifactRef) -> tuple[ManagedArtifactKind, str, int]:
    return artifact.kind, artifact.sha256, artifact.byte_count


def _publication_locator_binding(
    destination: PublicationDestination,
) -> tuple[ManagedArtifactKind, str, int]:
    kind = (
        ManagedArtifactKind.RAW_SOURCE
        if destination.kind == PublicationKind.RAW_SOURCE
        else ManagedArtifactKind.SOURCE_NOTE
    )
    return kind, destination.expected_sha256, destination.expected_byte_count


class InferenceUsage(_StrictFrozenModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)
    latency_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached tokens cannot exceed input tokens")
        return self


class ContentAddressedInferenceReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=_ID_PATTERNS["minference"].pattern)
    contract_id: str
    contract_version: int = Field(ge=1)
    mode: InferenceExecutionMode
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    provider_request_id: str | None = Field(default=None, pattern=_OPERATION_ID_RE.pattern)
    replay_source_receipt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    replay_source_receipt_artifact: ManagedArtifactRef | None = None
    authoritative_replay_source_resolution_required: bool
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    input_artifacts: tuple[ManagedArtifactRef, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_INFERENCE_INPUTS_V1
    )
    input_envelope_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_input_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_output_sha256: str = Field(pattern=SHA256_PATTERN)
    validated_output_sha256: str = Field(pattern=SHA256_PATTERN)
    usage: InferenceUsage

    @field_validator("contract_id")
    @classmethod
    def _contract(cls, value: str) -> str:
        return _exact_logical_key(value, label="contract_id")

    @field_validator("provider", "model")
    @classmethod
    def _text(cls, value: str, info: Any) -> str:
        return _bounded_exact_text(value, label=info.field_name, maximum=256)

    @field_validator("input_artifacts")
    @classmethod
    def _inputs(cls, values: tuple[ManagedArtifactRef, ...]) -> tuple[ManagedArtifactRef, ...]:
        if values != tuple(sorted(values, key=lambda item: item.artifact_id)):
            raise ValueError("inference inputs must be canonically ordered")
        if len({item.artifact_id for item in values}) != len(values):
            raise ValueError("inference inputs must be unique")
        if len({item.path for item in values}) != len(values):
            raise ValueError("inference input artifact paths must be unique")
        if any(
            item.kind
            in {ManagedArtifactKind.INFERENCE_OUTPUT, ManagedArtifactKind.INFERENCE_RECEIPT}
            for item in values
        ):
            raise ValueError("inference output/receipt cannot be an inference input")
        if sum(item.byte_count for item in values) > MAX_MANAGED_INFERENCE_INPUT_BYTES_V1:
            raise ValueError("inference inputs exceed aggregate declared-byte limit")
        return values

    def _input_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "prompt_sha256": self.prompt_sha256,
            "response_schema_sha256": self.response_schema_sha256,
            "input_artifacts": [item.model_dump(mode="json") for item in self.input_artifacts],
            "input_envelope_sha256": self.input_envelope_sha256,
        }

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id"})

    def artifact_ref(self) -> ManagedArtifactRef:
        """Return the exact durable receipt locator implied by canonical bytes."""

        content = canonical_json_bytes(self.model_dump(mode="json"))
        digest = hashlib.sha256(content).hexdigest()
        return ManagedArtifactRef.create(
            kind=ManagedArtifactKind.INFERENCE_RECEIPT,
            path=f"receipts/inference/{digest}.json",
            sha256=digest,
            byte_count=len(content),
        )

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.canonical_input_sha256 != _sha256(self._input_payload()):
            raise ValueError("canonical inference input SHA does not bind exact inputs")
        if self.mode == InferenceExecutionMode.LIVE:
            if (
                self.provider_request_id is None
                or self.replay_source_receipt_sha256 is not None
                or self.replay_source_receipt_artifact is not None
                or self.authoritative_replay_source_resolution_required
            ):
                raise ValueError("live inference requires only provider request evidence")
            if (
                self.usage.latency_ms == 0
                or self.usage.input_tokens + self.usage.output_tokens == 0
            ):
                raise ValueError("live inference requires non-zero token and latency evidence")
        elif (
            self.provider_request_id is not None
            or self.replay_source_receipt_sha256 is None
            or self.replay_source_receipt_artifact is None
            or not self.authoritative_replay_source_resolution_required
            or self.replay_source_receipt_artifact.kind != ManagedArtifactKind.INFERENCE_RECEIPT
            or self.replay_source_receipt_artifact.sha256 != self.replay_source_receipt_sha256
        ):
            raise ValueError(
                "replay inference requires an exact resolvable replay receipt artifact"
            )
        if self.replay_source_receipt_artifact is not None and (
            self.replay_source_receipt_artifact.path
            != f"receipts/inference/{self.replay_source_receipt_artifact.sha256}.json"
            or _is_managed_review_staging_path(self.replay_source_receipt_artifact.path)
        ):
            raise ValueError(
                "replay receipt must use its exact non-staging content-addressed locator"
            )
        if self.replay_source_receipt_artifact is not None and any(
            item.path == self.replay_source_receipt_artifact.path for item in self.input_artifacts
        ):
            raise ValueError("replay receipt locator cannot collide with an inference input")
        if self.receipt_id != _content_id("minference", self._payload()):
            raise ValueError("inference receipt ID does not match its exact content")
        return self

    def verify_replay_source(self, source_receipt: ContentAddressedInferenceReceipt) -> None:
        """Resolve one replay reference against an independently stored prior LIVE receipt."""

        if self.mode != InferenceExecutionMode.REPLAY:
            raise ValueError("replay-source verification requires a replay receipt")
        if source_receipt.mode != InferenceExecutionMode.LIVE:
            raise ValueError(
                "replay source must be a prior LIVE receipt; replay chains are invalid"
            )
        artifact = self.replay_source_receipt_artifact
        if artifact is None:
            raise ValueError("replay receipt is missing its source artifact reference")
        source_bytes = canonical_json_bytes(source_receipt.model_dump(mode="json"))
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        if (
            artifact.kind != ManagedArtifactKind.INFERENCE_RECEIPT
            or artifact.sha256 != source_sha
            or artifact.byte_count != len(source_bytes)
            or artifact.path != f"receipts/inference/{source_sha}.json"
            or self.replay_source_receipt_sha256 != source_sha
        ):
            raise ValueError("replay source artifact does not bind exact LIVE receipt bytes")
        comparable = (
            "contract_id",
            "contract_version",
            "provider",
            "model",
            "prompt_sha256",
            "response_schema_sha256",
            "input_artifacts",
            "input_envelope_sha256",
            "canonical_input_sha256",
            "raw_output_sha256",
            "validated_output_sha256",
        )
        if any(getattr(self, field) != getattr(source_receipt, field) for field in comparable):
            raise ValueError("replay receipt does not exactly match resolved LIVE inference")

    @classmethod
    def create(
        cls,
        *,
        contract_id: str,
        contract_version: int,
        mode: InferenceExecutionMode,
        provider: str,
        model: str,
        provider_request_id: str | None,
        replay_source_receipt_sha256: str | None,
        replay_source_receipt_artifact: ManagedArtifactRef | None,
        prompt_sha256: str,
        response_schema_sha256: str,
        input_artifacts: tuple[ManagedArtifactRef, ...],
        input_envelope_sha256: str,
        raw_output_sha256: str,
        validated_output_sha256: str,
        usage: InferenceUsage,
    ) -> Self:
        _preflight_collection(
            input_artifacts,
            label="inference inputs",
            minimum=1,
            maximum=MAX_MANAGED_INFERENCE_INPUTS_V1,
            unique_key=lambda item: item.artifact_id,
        )
        if len({item.path for item in input_artifacts}) != len(input_artifacts):
            raise ValueError("inference input artifact paths must be unique")
        if sum(item.byte_count for item in input_artifacts) > MAX_MANAGED_INFERENCE_INPUT_BYTES_V1:
            raise ValueError("inference inputs exceed aggregate declared-byte limit")
        ordered = tuple(sorted(input_artifacts, key=lambda item: item.artifact_id))
        input_payload = {
            "schema_version": 1,
            "contract_id": _exact_logical_key(contract_id, label="contract_id"),
            "contract_version": contract_version,
            "prompt_sha256": prompt_sha256,
            "response_schema_sha256": response_schema_sha256,
            "input_artifacts": [item.model_dump(mode="json") for item in ordered],
            "input_envelope_sha256": input_envelope_sha256,
        }
        values = {
            **input_payload,
            "mode": mode.value,
            "provider": _bounded_exact_text(provider, label="provider", maximum=128),
            "model": _bounded_exact_text(model, label="model", maximum=256),
            "provider_request_id": provider_request_id,
            "replay_source_receipt_sha256": replay_source_receipt_sha256,
            "replay_source_receipt_artifact": (
                replay_source_receipt_artifact.model_dump(mode="json")
                if replay_source_receipt_artifact is not None
                else None
            ),
            "authoritative_replay_source_resolution_required": (
                mode == InferenceExecutionMode.REPLAY
            ),
            "canonical_input_sha256": _sha256(input_payload),
            "raw_output_sha256": raw_output_sha256,
            "validated_output_sha256": validated_output_sha256,
            "usage": usage.model_dump(mode="json"),
        }
        return _validate_canonical_json(
            cls, {"receipt_id": _content_id("minference", values), **values}
        )


class ManagedInferenceContractBinding(_StrictFrozenModel):
    """Run-level exact inference contract; algorithm bytes are resolved by the later store."""

    schema_version: Literal[1] = 1
    contract_binding_id: str = Field(pattern=_ID_PATTERNS["mcontract"].pattern)
    algorithm_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    contract_id: str
    contract_version: int = Field(ge=1)
    mode: InferenceExecutionMode
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("contract_id")
    @classmethod
    def _contract(cls, value: str) -> str:
        return _exact_logical_key(value, label="contract_id")

    @field_validator("provider", "model")
    @classmethod
    def _text(cls, value: str, info: Any) -> str:
        return _bounded_exact_text(value, label=info.field_name, maximum=256)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"contract_binding_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.contract_binding_id != _content_id("mcontract", self._payload()):
            raise ValueError("managed inference contract ID does not bind exact run contract")
        return self

    def require_receipt(self, receipt: ContentAddressedInferenceReceipt) -> None:
        expected = (
            self.contract_id,
            self.contract_version,
            self.mode,
            self.provider,
            self.model,
            self.prompt_sha256,
            self.response_schema_sha256,
        )
        actual = (
            receipt.contract_id,
            receipt.contract_version,
            receipt.mode,
            receipt.provider,
            receipt.model,
            receipt.prompt_sha256,
            receipt.response_schema_sha256,
        )
        if actual != expected:
            raise ValueError("inference receipt does not match exact run-level contract")

    @classmethod
    def create(
        cls,
        *,
        algorithm_manifest_sha256: str,
        contract_id: str,
        contract_version: int,
        mode: InferenceExecutionMode,
        provider: str,
        model: str,
        prompt_sha256: str,
        response_schema_sha256: str,
    ) -> Self:
        values = {
            "schema_version": 1,
            "algorithm_manifest_sha256": algorithm_manifest_sha256,
            "contract_id": _exact_logical_key(contract_id, label="contract_id"),
            "contract_version": contract_version,
            "mode": mode.value,
            "provider": _bounded_exact_text(provider, label="provider", maximum=128),
            "model": _bounded_exact_text(model, label="model", maximum=256),
            "prompt_sha256": prompt_sha256,
            "response_schema_sha256": response_schema_sha256,
        }
        return _validate_canonical_json(
            cls,
            {"contract_binding_id": _content_id("mcontract", values), **values},
        )


class GroundedArtifactCitation(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    citation_id: str = Field(pattern=_ID_PATTERNS["mcitation"].pattern)
    artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=8192)
    quote_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"citation_id"})

    @field_validator("quote")
    @classmethod
    def _quote_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_MANAGED_CITATION_QUOTE_BYTES_V1:
            raise ValueError("quote exceeds MAX_MANAGED_CITATION_QUOTE_BYTES_V1 UTF-8 byte limit")
        return value

    @model_validator(mode="after")
    def _identity(self) -> Self:
        quote_bytes = self.quote.encode("utf-8")
        if self.end_byte - self.start_byte != len(quote_bytes):
            raise ValueError("citation byte offsets must exactly span quote bytes")
        if self.quote_sha256 != hashlib.sha256(quote_bytes).hexdigest():
            raise ValueError("citation quote SHA must derive from bounded quote text")
        if self.citation_id != _content_id("mcitation", self._payload()):
            raise ValueError("citation ID does not match its exact content")
        return self

    @classmethod
    def create(cls, *, artifact: ManagedArtifactRef, start_byte: int, quote: str) -> Self:
        quote_bytes = quote.encode("utf-8")
        if len(quote_bytes) > MAX_MANAGED_CITATION_QUOTE_BYTES_V1:
            raise ValueError("quote exceeds MAX_MANAGED_CITATION_QUOTE_BYTES_V1 UTF-8 byte limit")
        values = {
            "schema_version": 1,
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.sha256,
            "start_byte": start_byte,
            "end_byte": start_byte + len(quote_bytes),
            "quote": quote,
            "quote_sha256": hashlib.sha256(quote_bytes).hexdigest(),
        }
        return _validate_canonical_json(
            cls, {"citation_id": _content_id("mcitation", values), **values}
        )


class ManagedSemanticHunk(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    hunk_id: str = Field(pattern=_ID_PATTERNS["mhunk"].pattern)
    semantic_key: str
    base_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    result_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    before_text: str = Field(max_length=MAX_ATTESTED_TEXT_BYTES_V1)
    before_sha256: str = Field(pattern=SHA256_PATTERN)
    replacement_text: str = Field(max_length=MAX_ATTESTED_TEXT_BYTES_V1)
    replacement_sha256: str = Field(pattern=SHA256_PATTERN)
    citations: tuple[GroundedArtifactCitation, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_CITATIONS_PER_HUNK_V1
    )

    @field_validator("semantic_key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _exact_logical_key(value, label="semantic_key")

    @field_validator("before_text", "replacement_text")
    @classmethod
    def _attested_text_bytes(cls, value: str, info: Any) -> str:
        if len(value.encode("utf-8")) > MAX_ATTESTED_TEXT_BYTES_V1:
            raise ValueError(
                f"{info.field_name} exceeds MAX_ATTESTED_TEXT_BYTES_V1 UTF-8 byte limit"
            )
        return value

    @field_validator("citations")
    @classmethod
    def _citations(
        cls, values: tuple[GroundedArtifactCitation, ...]
    ) -> tuple[GroundedArtifactCitation, ...]:
        if values != tuple(sorted(values, key=lambda item: item.citation_id)):
            raise ValueError("hunk citations must be canonically ordered")
        if len({item.citation_id for item in values}) != len(values):
            raise ValueError("hunk citations must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"hunk_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        before = self.before_text.encode("utf-8")
        replacement = self.replacement_text.encode("utf-8")
        if self.end_byte - self.start_byte != len(before):
            raise ValueError("hunk offsets must exactly span its before text")
        if self.before_sha256 != hashlib.sha256(before).hexdigest():
            raise ValueError("before SHA must derive from bounded before text")
        if self.replacement_sha256 != hashlib.sha256(replacement).hexdigest():
            raise ValueError("replacement SHA must derive from bounded replacement text")
        if self.base_artifact_id == self.result_artifact_id:
            raise ValueError("a hunk must bind distinct base and result artifacts")
        if self.hunk_id != _content_id("mhunk", self._payload()):
            raise ValueError("hunk ID does not match its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        semantic_key: str,
        base_artifact: ManagedArtifactRef,
        result_artifact: ManagedArtifactRef,
        start_byte: int,
        before_text: str,
        replacement_text: str,
        citations: tuple[GroundedArtifactCitation, ...],
    ) -> Self:
        _preflight_collection(
            citations,
            label="hunk citations",
            minimum=1,
            maximum=MAX_MANAGED_CITATIONS_PER_HUNK_V1,
            unique_key=lambda item: item.citation_id,
        )
        before = before_text.encode("utf-8")
        replacement = replacement_text.encode("utf-8")
        if len(before) > MAX_ATTESTED_TEXT_BYTES_V1:
            raise ValueError("before_text exceeds MAX_ATTESTED_TEXT_BYTES_V1 UTF-8 byte limit")
        if len(replacement) > MAX_ATTESTED_TEXT_BYTES_V1:
            raise ValueError("replacement_text exceeds MAX_ATTESTED_TEXT_BYTES_V1 UTF-8 byte limit")
        ordered = tuple(sorted(citations, key=lambda item: item.citation_id))
        values = {
            "schema_version": 1,
            "semantic_key": _exact_logical_key(semantic_key, label="semantic_key"),
            "base_artifact_id": base_artifact.artifact_id,
            "result_artifact_id": result_artifact.artifact_id,
            "start_byte": start_byte,
            "end_byte": start_byte + len(before),
            "before_text": before_text,
            "before_sha256": hashlib.sha256(before).hexdigest(),
            "replacement_text": replacement_text,
            "replacement_sha256": hashlib.sha256(replacement).hexdigest(),
            "citations": [item.model_dump(mode="json") for item in ordered],
        }
        return _validate_canonical_json(cls, {"hunk_id": _content_id("mhunk", values), **values})


class PatchReconstructionAttestation(_StrictFrozenModel):
    """Producer attestation; the authoritative store must reproduce it from bytes."""

    schema_version: Literal[1] = 1
    attestation_id: str = Field(pattern=_ID_PATTERNS["mpatch"].pattern)
    status: Literal[AttestationStatus.PASSED_REQUIRES_STORE_REVALIDATION]
    store_revalidation_required: Literal[True] = True
    base_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    base_sha256: str = Field(pattern=SHA256_PATTERN)
    base_byte_count: int = Field(ge=1, le=MAX_MANAGED_ARTIFACT_BYTES_V1)
    result_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    result_byte_count: int = Field(ge=1, le=MAX_MANAGED_ARTIFACT_BYTES_V1)
    ordered_hunk_ids: tuple[str, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_HUNKS_PER_PLAN_V1
    )
    ordered_citation_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_MANAGED_HUNKS_PER_PLAN_V1 * MAX_MANAGED_CITATIONS_PER_HUNK_V1,
    )
    complete_diff_hunk_ids: tuple[str, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_HUNKS_PER_PLAN_V1
    )
    complete_diff_sha256: str = Field(pattern=SHA256_PATTERN)
    hunk_program_sha256: str = Field(pattern=SHA256_PATTERN)
    reconstructed_result_sha256: str = Field(pattern=SHA256_PATTERN)
    uncovered_diff_byte_count: Literal[0] = 0

    @field_validator("ordered_hunk_ids", "complete_diff_hunk_ids")
    @classmethod
    def _hunk_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("attested hunk IDs must be unique")
        if any(_ID_PATTERNS["mhunk"].fullmatch(value) is None for value in values):
            raise ValueError("attested hunk ID has the wrong shape")
        return values

    @field_validator("ordered_citation_ids")
    @classmethod
    def _citation_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_ID_PATTERNS["mcitation"].fullmatch(value) is None for value in values):
            raise ValueError("attested citation ID has the wrong shape")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"attestation_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.complete_diff_hunk_ids != self.ordered_hunk_ids:
            raise ValueError("complete raw diff must be covered by exactly the ordered hunks")
        if self.reconstructed_result_sha256 != self.result_sha256:
            raise ValueError("reconstructed result SHA must equal the staged result SHA")
        expected_program = _sha256(
            {
                "base_sha256": self.base_sha256,
                "hunk_ids": self.ordered_hunk_ids,
                "result_sha256": self.result_sha256,
            }
        )
        if self.hunk_program_sha256 != expected_program:
            raise ValueError("hunk program SHA does not bind base, result, and complete hunks")
        if self.attestation_id != _content_id("mpatch", self._payload()):
            raise ValueError("patch attestation ID does not match its exact content")
        return self

    @classmethod
    def create_from_verifier_output(
        cls,
        *,
        base_artifact: ManagedArtifactRef,
        result_artifact: ManagedArtifactRef,
        hunks: tuple[ManagedSemanticHunk, ...],
        complete_diff_sha256: str,
    ) -> Self:
        _preflight_collection(
            hunks,
            label="attested hunks",
            minimum=1,
            maximum=MAX_MANAGED_HUNKS_PER_PLAN_V1,
            unique_key=lambda item: item.hunk_id,
        )
        hunk_ids = tuple(item.hunk_id for item in hunks)
        citation_ids = tuple(citation.citation_id for hunk in hunks for citation in hunk.citations)
        values = {
            "schema_version": 1,
            "status": AttestationStatus.PASSED_REQUIRES_STORE_REVALIDATION.value,
            "store_revalidation_required": True,
            "base_artifact_id": base_artifact.artifact_id,
            "base_sha256": base_artifact.sha256,
            "base_byte_count": base_artifact.byte_count,
            "result_artifact_id": result_artifact.artifact_id,
            "result_sha256": result_artifact.sha256,
            "result_byte_count": result_artifact.byte_count,
            "ordered_hunk_ids": hunk_ids,
            "ordered_citation_ids": citation_ids,
            "complete_diff_hunk_ids": hunk_ids,
            "complete_diff_sha256": complete_diff_sha256,
            "hunk_program_sha256": _sha256(
                {
                    "base_sha256": base_artifact.sha256,
                    "hunk_ids": hunk_ids,
                    "result_sha256": result_artifact.sha256,
                }
            ),
            "reconstructed_result_sha256": result_artifact.sha256,
            "uncovered_diff_byte_count": 0,
        }
        return _validate_canonical_json(
            cls, {"attestation_id": _content_id("mpatch", values), **values}
        )


class SourceNoteProjectionBinding(_StrictFrozenModel):
    """Exact projected claim set asserted by a parser/validator invocation."""

    schema_version: Literal[1] = 1
    projection_id: str = Field(pattern=_ID_PATTERNS["mprojection"].pattern)
    status: Literal[AttestationStatus.PASSED_REQUIRES_STORE_REVALIDATION]
    store_revalidation_required: Literal[True] = True
    validator_version: str
    source_note_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    validator_result_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_artifact: ManagedArtifactRef
    note_artifact: ManagedArtifactRef
    canonical_raw_path: str
    canonical_note_path: str
    projected_claims: tuple[VersionedClaimRevision, ...] = Field(
        max_length=MAX_MANAGED_PROJECTION_CLAIMS_V1
    )

    @model_validator(mode="before")
    @classmethod
    def _raw_nested_limits(cls, value: Any) -> Any:
        if isinstance(value, dict):
            _preflight_raw_claim_payloads(value.get("projected_claims"), label="projected")
            projected_claims = value.get("projected_claims")
            if isinstance(projected_claims, list):
                value = {
                    **value,
                    "projected_claims": tuple(
                        VersionedClaimRevision.model_validate_json(canonical_json_bytes(item))
                        if isinstance(item, dict)
                        else item
                        for item in projected_claims
                    ),
                }
        return value

    @field_validator("validator_version")
    @classmethod
    def _version(cls, value: str) -> str:
        return _exact_logical_key(value, label="validator_version")

    @field_validator("canonical_raw_path", "canonical_note_path")
    @classmethod
    def _paths(cls, value: str) -> str:
        return _safe_path(value)

    @field_validator("projected_claims")
    @classmethod
    def _claims(
        cls, values: tuple[VersionedClaimRevision, ...]
    ) -> tuple[VersionedClaimRevision, ...]:
        _preflight_claim_texts(values, label="projected")
        if values != tuple(sorted(values, key=lambda item: item.claim_revision_id)):
            raise ValueError("projected claims must be canonically ordered")
        if len({item.claim_revision_id for item in values}) != len(values):
            raise ValueError("projected claim revisions must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"projection_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.raw_artifact.kind != ManagedArtifactKind.RAW_SOURCE:
            raise ValueError("projection raw artifact has the wrong kind")
        if self.note_artifact.kind != ManagedArtifactKind.SOURCE_NOTE:
            raise ValueError("projection note artifact has the wrong kind")
        if PurePosixPath(self.canonical_raw_path).suffix != ".md":
            raise ValueError("managed raw projection path must be Markdown in this slice")
        if PurePosixPath(self.canonical_note_path).suffix != ".md":
            raise ValueError("SourceNote projection path must be Markdown")
        for claim in self.projected_claims:
            if claim.document.source_path != self.canonical_raw_path:
                raise ValueError("projected claim document does not bind canonical raw path")
            if claim.document.source_sha256 != self.raw_artifact.sha256:
                raise ValueError("projected claim document does not bind raw bytes")
            if claim.source.source_note_path != self.canonical_note_path:
                raise ValueError("projected claim does not bind canonical note path")
            if claim.source.source_note_sha256 != self.note_artifact.sha256:
                raise ValueError("projected claim does not bind exact note bytes")
        if self.projection_id != _content_id("mprojection", self._payload()):
            raise ValueError("projection ID does not match exact artifacts and claim set")
        return self

    @classmethod
    def create_from_validator_output(
        cls,
        *,
        raw_artifact: ManagedArtifactRef,
        note_artifact: ManagedArtifactRef,
        canonical_raw_path: str,
        canonical_note_path: str,
        validator_version: str,
        source_note_schema_sha256: str,
        validator_result_sha256: str,
        projected_claims: tuple[VersionedClaimRevision, ...],
    ) -> Self:
        _preflight_collection(
            projected_claims,
            label="projected claims",
            maximum=MAX_MANAGED_PROJECTION_CLAIMS_V1,
            unique_key=lambda item: item.claim_revision_id,
        )
        _preflight_claim_texts(projected_claims, label="projected")
        ordered = tuple(sorted(projected_claims, key=lambda item: item.claim_revision_id))
        values = {
            "schema_version": 1,
            "status": AttestationStatus.PASSED_REQUIRES_STORE_REVALIDATION.value,
            "store_revalidation_required": True,
            "validator_version": _exact_logical_key(validator_version, label="validator_version"),
            "source_note_schema_sha256": source_note_schema_sha256,
            "validator_result_sha256": validator_result_sha256,
            "raw_artifact": raw_artifact.model_dump(mode="json"),
            "note_artifact": note_artifact.model_dump(mode="json"),
            "canonical_raw_path": _safe_path(canonical_raw_path),
            "canonical_note_path": _safe_path(canonical_note_path),
            "projected_claims": [item.model_dump(mode="json") for item in ordered],
        }
        return _validate_canonical_json(
            cls, {"projection_id": _content_id("mprojection", values), **values}
        )


class ClaimReconciliationAction(StrEnum):
    CARRIED_FORWARD = "carried-forward"
    REWORDED = "reworded"
    ADDED = "added"
    RETIRED = "retired"


class ClaimReconciliationEntry(_StrictFrozenModel):
    action: ClaimReconciliationAction
    predecessor: VersionedClaimRevision | None = None
    successor: VersionedClaimRevision | None = None

    @model_validator(mode="after")
    def _semantics(self) -> Self:
        if self.action == ClaimReconciliationAction.ADDED:
            if self.predecessor is not None or self.successor is None:
                raise ValueError("added claim requires only a successor")
        elif self.action == ClaimReconciliationAction.RETIRED:
            if self.predecessor is None or self.successor is not None:
                raise ValueError("retired claim requires only a predecessor")
        elif self.predecessor is None or self.successor is None:
            raise ValueError("carried/reworded claim requires predecessor and successor")
        else:
            if self.predecessor.claim_identity_id == self.successor.claim_identity_id:
                raise ValueError("create-only successor claims require new claim identities")
            same_semantics = (
                self.predecessor.statement == self.successor.statement
                and self.predecessor.scopes == self.successor.scopes
            )
            if self.action == ClaimReconciliationAction.CARRIED_FORWARD and not same_semantics:
                raise ValueError("carried-forward claim must preserve statement and scopes")
            if self.action == ClaimReconciliationAction.REWORDED and same_semantics:
                raise ValueError("reworded claim must change statement or scopes")
        return self


class ClaimReconciliationBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    reconciliation_id: str = Field(pattern=_ID_PATTERNS["mclaims"].pattern)
    predecessor_projection_id: str = Field(pattern=_ID_PATTERNS["mprojection"].pattern)
    successor_projection_id: str = Field(pattern=_ID_PATTERNS["mprojection"].pattern)
    predecessor_revisions: tuple[VersionedClaimRevision, ...] = Field(
        max_length=MAX_MANAGED_PROJECTION_CLAIMS_V1
    )
    successor_revisions: tuple[VersionedClaimRevision, ...] = Field(
        max_length=MAX_MANAGED_PROJECTION_CLAIMS_V1
    )
    entries: tuple[ClaimReconciliationEntry, ...] = Field(
        max_length=MAX_MANAGED_RECONCILIATION_ENTRIES_V1
    )

    @model_validator(mode="before")
    @classmethod
    def _raw_nested_limits(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        _preflight_raw_claim_payloads(
            value.get("predecessor_revisions"), label="predecessor reconciliation"
        )
        _preflight_raw_claim_payloads(
            value.get("successor_revisions"), label="successor reconciliation"
        )
        entries = value.get("entries")
        if isinstance(entries, list | tuple):
            if len(entries) > MAX_MANAGED_RECONCILIATION_ENTRIES_V1:
                raise ValueError("claim reconciliation entries exceed managed count limit")
            for entry in entries:
                if isinstance(entry, dict):
                    _preflight_raw_claim_payloads(
                        [entry.get("predecessor")], label="reconciliation predecessor"
                    )
                    _preflight_raw_claim_payloads(
                        [entry.get("successor")], label="reconciliation successor"
                    )
        rewritten = dict(value)
        for field_name in ("predecessor_revisions", "successor_revisions"):
            raw_revisions = rewritten.get(field_name)
            if isinstance(raw_revisions, list):
                rewritten[field_name] = tuple(
                    VersionedClaimRevision.model_validate_json(canonical_json_bytes(item))
                    if isinstance(item, dict)
                    else item
                    for item in raw_revisions
                )
        if isinstance(entries, list):
            rewritten["entries"] = tuple(
                ClaimReconciliationEntry.model_validate_json(canonical_json_bytes(item))
                if isinstance(item, dict)
                else item
                for item in entries
            )
        return rewritten

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"reconciliation_id"})

    @field_validator("entries")
    @classmethod
    def _canonical_entries(
        cls, values: tuple[ClaimReconciliationEntry, ...]
    ) -> tuple[ClaimReconciliationEntry, ...]:
        ordered = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.predecessor.claim_revision_id if item.predecessor else "",
                    item.successor.claim_revision_id if item.successor else "",
                    item.action.value,
                ),
            )
        )
        if values != ordered:
            raise ValueError("claim reconciliation entries must be canonically ordered")
        return values

    @model_validator(mode="after")
    def _coverage_and_identity(self) -> Self:
        predecessor_ids = tuple(item.claim_revision_id for item in self.predecessor_revisions)
        successor_ids = tuple(item.claim_revision_id for item in self.successor_revisions)
        if predecessor_ids != tuple(sorted(set(predecessor_ids))):
            raise ValueError("predecessor revisions must be ordered and unique")
        if successor_ids != tuple(sorted(set(successor_ids))):
            raise ValueError("successor revisions must be ordered and unique")
        if (predecessor_ids or successor_ids) and not self.entries:
            raise ValueError("claim reconciliation coverage must be nonempty when claims exist")
        entry_predecessors = tuple(
            sorted(
                item.predecessor.claim_revision_id
                for item in self.entries
                if item.predecessor is not None
            )
        )
        entry_successors = tuple(
            sorted(
                item.successor.claim_revision_id
                for item in self.entries
                if item.successor is not None
            )
        )
        if entry_predecessors != predecessor_ids or entry_successors != successor_ids:
            raise ValueError("reconciliation entries must exactly cover both projection claim sets")
        if len(set(entry_predecessors)) != len(entry_predecessors) or len(
            set(entry_successors)
        ) != len(entry_successors):
            raise ValueError("each projected claim must be reconciled exactly once")
        if self.reconciliation_id != _content_id("mclaims", self._payload()):
            raise ValueError("claim reconciliation ID does not match exact typed sets")
        return self

    @classmethod
    def create(
        cls,
        *,
        predecessor_projection: SourceNoteProjectionBinding,
        successor_projection: SourceNoteProjectionBinding,
        entries: tuple[ClaimReconciliationEntry, ...],
    ) -> Self:
        _preflight_collection(
            predecessor_projection.projected_claims,
            label="predecessor projection claims",
            maximum=MAX_MANAGED_PROJECTION_CLAIMS_V1,
            unique_key=lambda item: item.claim_revision_id,
        )
        _preflight_collection(
            successor_projection.projected_claims,
            label="successor projection claims",
            maximum=MAX_MANAGED_PROJECTION_CLAIMS_V1,
            unique_key=lambda item: item.claim_revision_id,
        )
        _preflight_collection(
            entries,
            label="claim reconciliation entries",
            maximum=MAX_MANAGED_RECONCILIATION_ENTRIES_V1,
        )
        _preflight_claim_texts(
            predecessor_projection.projected_claims, label="predecessor projection"
        )
        _preflight_claim_texts(successor_projection.projected_claims, label="successor projection")
        ordered = tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.predecessor.claim_revision_id if item.predecessor else "",
                    item.successor.claim_revision_id if item.successor else "",
                    item.action.value,
                ),
            )
        )
        values = {
            "schema_version": 1,
            "predecessor_projection_id": predecessor_projection.projection_id,
            "successor_projection_id": successor_projection.projection_id,
            "predecessor_revisions": [
                item.model_dump(mode="json") for item in predecessor_projection.projected_claims
            ],
            "successor_revisions": [
                item.model_dump(mode="json") for item in successor_projection.projected_claims
            ],
            "entries": [item.model_dump(mode="json") for item in ordered],
        }
        return _validate_canonical_json(
            cls, {"reconciliation_id": _content_id("mclaims", values), **values}
        )


class AggregateHeadBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    head_id: str = Field(pattern=_ID_PATTERNS["mhead"].pattern)
    aggregate_id: str
    revision: int = Field(ge=0)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate(cls, value: str) -> str:
        return _exact_logical_key(value, label="aggregate_id")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"head_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.head_id != _content_id("mhead", self._payload()):
            raise ValueError("aggregate head ID does not match its exact revision/hash")
        return self

    @classmethod
    def create(cls, *, aggregate_id: str, revision: int, aggregate_sha256: str) -> Self:
        values = {
            "schema_version": 1,
            "aggregate_id": _exact_logical_key(aggregate_id, label="aggregate_id"),
            "revision": revision,
            "aggregate_sha256": aggregate_sha256,
        }
        return _validate_canonical_json(cls, {"head_id": _content_id("mhead", values), **values})


class ContentAddressedGenerationBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    generation_id: str = Field(pattern=_ID_PATTERNS["mgeneration"].pattern)
    generation_number: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"generation_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.generation_id != _content_id("mgeneration", self._payload()):
            raise ValueError("generation ID does not match number and manifest")
        return self

    @classmethod
    def create(cls, *, generation_number: int, manifest_sha256: str) -> Self:
        values = {
            "schema_version": 1,
            "generation_number": generation_number,
            "manifest_sha256": manifest_sha256,
        }
        return _validate_canonical_json(
            cls, {"generation_id": _content_id("mgeneration", values), **values}
        )


class GenerationZeroManifestBinding(_StrictFrozenModel):
    """Deterministic base manifest over the verified pre-change aggregate root."""

    schema_version: Literal[1] = 1
    manifest_id: str = Field(pattern=_ID_PATTERNS["mzeromanifest"].pattern)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    encoding: Literal["verified-prechange-aggregate-v1"] = "verified-prechange-aggregate-v1"
    seed_scenario_id: str
    seed_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    prechange_head: AggregateHeadBinding

    @field_validator("seed_scenario_id")
    @classmethod
    def _scenario(cls, value: str) -> str:
        return _exact_logical_key(value, label="seed_scenario_id")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id", "manifest_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.manifest_sha256 != digest or self.manifest_id != f"mzeromanifest:{digest}":
            raise ValueError("generation-zero manifest ID/SHA does not bind exact base roots")
        return self

    @classmethod
    def create(
        cls,
        *,
        analysis_bootstrap: AnalysisBootstrapBinding,
        prechange_head: AggregateHeadBinding,
    ) -> Self:
        if (
            prechange_head.aggregate_id != analysis_bootstrap.aggregate_id
            or prechange_head.revision != analysis_bootstrap.prechange_revision
            or prechange_head.aggregate_sha256 != analysis_bootstrap.prechange_aggregate_sha256
        ):
            raise ValueError("generation-zero manifest requires the exact bootstrap prechange head")
        values = {
            "schema_version": 1,
            "encoding": "verified-prechange-aggregate-v1",
            "seed_scenario_id": analysis_bootstrap.seed_scenario_id,
            "seed_manifest_sha256": analysis_bootstrap.seed_manifest_sha256,
            "prechange_head": prechange_head.model_dump(mode="json"),
        }
        digest = _sha256(values)
        return _validate_canonical_json(
            cls,
            {"manifest_id": f"mzeromanifest:{digest}", "manifest_sha256": digest, **values},
        )


class GenerationZeroOriginBasis(_StrictFrozenModel):
    origin_kind: Literal["verified-seed-bootstrap"] = "verified-seed-bootstrap"
    authoritative_repository_resolution_required: Literal[True] = True
    analysis_bootstrap: AnalysisBootstrapBinding
    analysis_bootstrap_binding_id: str = Field(pattern=r"^analysis-bootstrap:[0-9a-f]{64}$")
    analysis_bootstrap_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    seed_scenario_id: str
    seed_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    prechange_head: AggregateHeadBinding
    generation_zero_manifest: GenerationZeroManifestBinding
    generation_zero_manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("seed_scenario_id")
    @classmethod
    def _scenario(cls, value: str) -> str:
        return _exact_logical_key(value, label="seed_scenario_id")

    @model_validator(mode="after")
    def _bootstrap(self) -> Self:
        bootstrap = self.analysis_bootstrap
        if (
            self.analysis_bootstrap_binding_id != bootstrap.binding_id
            or self.analysis_bootstrap_binding_sha256 != bootstrap.binding_sha256
            or self.seed_scenario_id != bootstrap.seed_scenario_id
            or self.seed_manifest_sha256 != bootstrap.seed_manifest_sha256
            or self.prechange_head.aggregate_id != bootstrap.aggregate_id
            or self.prechange_head.revision != bootstrap.prechange_revision
            or self.prechange_head.aggregate_sha256 != bootstrap.prechange_aggregate_sha256
            or self.generation_zero_manifest.seed_scenario_id != bootstrap.seed_scenario_id
            or self.generation_zero_manifest.seed_manifest_sha256 != bootstrap.seed_manifest_sha256
            or self.generation_zero_manifest.prechange_head != self.prechange_head
            or self.generation_zero_manifest_sha256 != self.generation_zero_manifest.manifest_sha256
        ):
            raise ValueError("generation-zero origin must bind exact bootstrap and seed roots")
        return self


class WorkspaceGenerationZeroManifestBinding(_StrictFrozenModel):
    """Deterministic base manifest over a verified complete workspace."""

    schema_version: Literal[1] = 1
    manifest_id: str = Field(pattern=_ID_PATTERNS["mzeromanifest"].pattern)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    encoding: Literal["verified-workspace-bootstrap-v1"] = "verified-workspace-bootstrap-v1"
    bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    intent_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_receipt_id: str = Field(
        pattern=r"^workspaceinventoryreceipt:[0-9a-f]{64}$"
    )
    inventory_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    index_receipt_id: str = Field(pattern=r"^legacyindexreceipt:[0-9a-f]{64}$")
    index_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    prechange_head: AggregateHeadBinding

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id", "manifest_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.manifest_sha256 != digest or self.manifest_id != f"mzeromanifest:{digest}":
            raise ValueError("workspace generation-zero manifest differs from exact evidence")
        return self

    @classmethod
    def create(
        cls,
        *,
        intent: WorkspaceBootstrapIntent,
        inventory_receipt: WorkspaceInventoryReceipt,
        index_receipt: LegacyIndexReadinessReceipt,
        prechange_head: AggregateHeadBinding,
    ) -> Self:
        if not (
            inventory_receipt.bootstrap_id == intent.bootstrap_id
            and inventory_receipt.aggregate_id == intent.aggregate_id == prechange_head.aggregate_id
            and inventory_receipt.aggregate_revision == prechange_head.revision
            and inventory_receipt.aggregate_sha256 == prechange_head.aggregate_sha256
            and inventory_receipt.inventory_id == intent.inventory_id
            and inventory_receipt.inventory_sha256 == intent.inventory_sha256
            and index_receipt.bootstrap_id == intent.bootstrap_id
            and index_receipt.inventory_receipt_id == inventory_receipt.receipt_id
            and index_receipt.inventory_receipt_sha256 == inventory_receipt.receipt_sha256
        ):
            raise ValueError("workspace generation zero requires one exact bootstrap chain")
        values = {
            "schema_version": 1,
            "encoding": "verified-workspace-bootstrap-v1",
            "bootstrap_id": intent.bootstrap_id,
            "intent_sha256": intent.intent_sha256,
            "inventory_receipt_id": inventory_receipt.receipt_id,
            "inventory_receipt_sha256": inventory_receipt.receipt_sha256,
            "index_receipt_id": index_receipt.receipt_id,
            "index_receipt_sha256": index_receipt.receipt_sha256,
            "prechange_head": prechange_head.model_dump(mode="json"),
        }
        digest = _sha256(values)
        return _validate_canonical_json(
            cls,
            {"manifest_id": f"mzeromanifest:{digest}", "manifest_sha256": digest, **values},
        )


class WorkspaceGenerationZeroOriginBasis(_StrictFrozenModel):
    origin_kind: Literal["verified-workspace-bootstrap"] = "verified-workspace-bootstrap"
    authoritative_repository_resolution_required: Literal[True] = True
    bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    intent_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_receipt_id: str = Field(
        pattern=r"^workspaceinventoryreceipt:[0-9a-f]{64}$"
    )
    inventory_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    index_receipt_id: str = Field(pattern=r"^legacyindexreceipt:[0-9a-f]{64}$")
    index_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    prechange_head: AggregateHeadBinding
    generation_zero_manifest: WorkspaceGenerationZeroManifestBinding
    generation_zero_manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _bootstrap(self) -> Self:
        manifest = self.generation_zero_manifest
        if not (
            manifest.bootstrap_id == self.bootstrap_id
            and manifest.intent_sha256 == self.intent_sha256
            and manifest.inventory_receipt_id == self.inventory_receipt_id
            and manifest.inventory_receipt_sha256 == self.inventory_receipt_sha256
            and manifest.index_receipt_id == self.index_receipt_id
            and manifest.index_receipt_sha256 == self.index_receipt_sha256
            and manifest.prechange_head == self.prechange_head
            and self.generation_zero_manifest_sha256 == manifest.manifest_sha256
        ):
            raise ValueError("workspace generation-zero origin differs from exact receipts")
        return self


class ManagedDecisionOriginBasis(_StrictFrozenModel):
    origin_kind: Literal["managed-decision"] = "managed-decision"
    authoritative_record_resolution_required: Literal[True] = True
    request_record_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_id: str = Field(pattern=_ID_PATTERNS["mdecision"].pattern)
    decision_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    activation_plan_id: str = Field(pattern=_ID_PATTERNS["mauthorityplan"].pattern)
    expected_authority_id: str = Field(pattern=_ID_PATTERNS["mauthority"].pattern)
    expected_authority_revision: int = Field(ge=0)
    expected_active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    prior_generation: ContentAddressedGenerationBinding


class AuthorityRevisionBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    authority_id: str = Field(pattern=_ID_PATTERNS["mauthority"].pattern)
    aggregate_id: str
    authority_revision: int = Field(ge=0)
    active_generation: ContentAddressedGenerationBinding
    active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    origin_basis: (
        GenerationZeroOriginBasis
        | WorkspaceGenerationZeroOriginBasis
        | ManagedDecisionOriginBasis
    ) = Field(
        discriminator="origin_kind"
    )

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate(cls, value: str) -> str:
        return _exact_logical_key(value, label="aggregate_id")

    def _pointer_payload(self) -> dict[str, Any]:
        return {
            "aggregate_id": self.aggregate_id,
            "authority_revision": self.authority_revision,
            "active_generation": self.active_generation.model_dump(mode="json"),
            "origin_basis": self.origin_basis.model_dump(mode="json"),
            "schema_version": self.schema_version,
        }

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if isinstance(
            self.origin_basis,
            (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
        ):
            zero_origin = self.origin_basis
            if self.authority_revision != 0:
                raise ValueError("generation-zero authority revision must be zero")
            if self.active_generation.generation_number != 0 or (
                self.active_generation.manifest_sha256
                != zero_origin.generation_zero_manifest_sha256
            ):
                raise ValueError("verified seed origin is valid only for exact generation zero")
        else:
            decision_origin = self.origin_basis
            if (
                self.active_generation.generation_number
                != decision_origin.prior_generation.generation_number + 1
                or self.authority_revision != decision_origin.expected_authority_revision + 1
            ):
                raise ValueError(
                    "managed decision origin must advance generation and revision once"
                )
        if self.active_pointer_sha256 != _sha256(self._pointer_payload()):
            raise ValueError("active pointer SHA does not bind CAS authority state")
        payload = {**self._pointer_payload(), "active_pointer_sha256": self.active_pointer_sha256}
        if self.authority_id != _content_id("mauthority", payload):
            raise ValueError("authority ID does not match active generation and revision")
        return self

    @classmethod
    def _create(
        cls,
        *,
        aggregate_id: str,
        authority_revision: int,
        active_generation: ContentAddressedGenerationBinding,
        origin_basis: (
            GenerationZeroOriginBasis
            | WorkspaceGenerationZeroOriginBasis
            | ManagedDecisionOriginBasis
        ),
    ) -> Self:
        pointer = {
            "aggregate_id": _exact_logical_key(aggregate_id, label="aggregate_id"),
            "authority_revision": authority_revision,
            "active_generation": active_generation.model_dump(mode="json"),
            "origin_basis": origin_basis.model_dump(mode="json"),
            "schema_version": 1,
        }
        pointer_sha = _sha256(pointer)
        payload = {**pointer, "active_pointer_sha256": pointer_sha}
        return _validate_canonical_json(
            cls,
            {
                "authority_id": _content_id("mauthority", payload),
                "active_pointer_sha256": pointer_sha,
                **pointer,
            },
        )

    @classmethod
    def create_generation_zero(
        cls,
        *,
        analysis_bootstrap: AnalysisBootstrapBinding,
        prechange_head: AggregateHeadBinding,
    ) -> Self:
        if (
            prechange_head.aggregate_id != analysis_bootstrap.aggregate_id
            or prechange_head.revision != analysis_bootstrap.prechange_revision
            or prechange_head.aggregate_sha256 != analysis_bootstrap.prechange_aggregate_sha256
        ):
            raise ValueError("generation-zero authority must bind exact bootstrap prechange head")
        base_manifest = GenerationZeroManifestBinding.create(
            analysis_bootstrap=analysis_bootstrap,
            prechange_head=prechange_head,
        )
        generation = ContentAddressedGenerationBinding.create(
            generation_number=0,
            manifest_sha256=base_manifest.manifest_sha256,
        )
        origin = GenerationZeroOriginBasis(
            authoritative_repository_resolution_required=True,
            analysis_bootstrap=analysis_bootstrap,
            analysis_bootstrap_binding_id=analysis_bootstrap.binding_id,
            analysis_bootstrap_binding_sha256=analysis_bootstrap.binding_sha256,
            seed_scenario_id=analysis_bootstrap.seed_scenario_id,
            seed_manifest_sha256=analysis_bootstrap.seed_manifest_sha256,
            prechange_head=prechange_head,
            generation_zero_manifest=base_manifest,
            generation_zero_manifest_sha256=base_manifest.manifest_sha256,
        )
        return cls._create(
            aggregate_id=analysis_bootstrap.aggregate_id,
            authority_revision=0,
            active_generation=generation,
            origin_basis=origin,
        )

    def verify_generation_zero_origin(
        self,
        *,
        analysis_bootstrap: AnalysisBootstrapBinding,
        prechange_head: AggregateHeadBinding,
    ) -> None:
        """Rederive and compare a structural generation-zero pointer."""

        if not isinstance(self.origin_basis, GenerationZeroOriginBasis):
            raise ValueError("generation-zero verification requires a verified-seed origin")
        expected = self.create_generation_zero(
            analysis_bootstrap=analysis_bootstrap,
            prechange_head=prechange_head,
        )
        if self != expected:
            raise ValueError(
                "generation-zero authority does not resolve to exact verified bootstrap roots"
            )

    @classmethod
    def create_workspace_generation_zero(
        cls,
        *,
        intent: WorkspaceBootstrapIntent,
        inventory_receipt: WorkspaceInventoryReceipt,
        index_receipt: LegacyIndexReadinessReceipt,
    ) -> Self:
        prechange_head = AggregateHeadBinding.create(
            aggregate_id=inventory_receipt.aggregate_id,
            revision=inventory_receipt.aggregate_revision,
            aggregate_sha256=inventory_receipt.aggregate_sha256,
        )
        manifest = WorkspaceGenerationZeroManifestBinding.create(
            intent=intent,
            inventory_receipt=inventory_receipt,
            index_receipt=index_receipt,
            prechange_head=prechange_head,
        )
        generation = ContentAddressedGenerationBinding.create(
            generation_number=0,
            manifest_sha256=manifest.manifest_sha256,
        )
        origin = WorkspaceGenerationZeroOriginBasis(
            authoritative_repository_resolution_required=True,
            bootstrap_id=intent.bootstrap_id,
            intent_sha256=intent.intent_sha256,
            inventory_receipt_id=inventory_receipt.receipt_id,
            inventory_receipt_sha256=inventory_receipt.receipt_sha256,
            index_receipt_id=index_receipt.receipt_id,
            index_receipt_sha256=index_receipt.receipt_sha256,
            prechange_head=prechange_head,
            generation_zero_manifest=manifest,
            generation_zero_manifest_sha256=manifest.manifest_sha256,
        )
        return cls._create(
            aggregate_id=intent.aggregate_id,
            authority_revision=0,
            active_generation=generation,
            origin_basis=origin,
        )

    def verify_workspace_generation_zero_origin(
        self,
        *,
        intent: WorkspaceBootstrapIntent,
        inventory_receipt: WorkspaceInventoryReceipt,
        index_receipt: LegacyIndexReadinessReceipt,
    ) -> None:
        if not isinstance(self.origin_basis, WorkspaceGenerationZeroOriginBasis):
            raise ValueError("workspace verification requires a verified-workspace origin")
        expected = self.create_workspace_generation_zero(
            intent=intent,
            inventory_receipt=inventory_receipt,
            index_receipt=index_receipt,
        )
        if self != expected:
            raise ValueError(
                "workspace generation-zero authority does not resolve to exact receipts"
            )

    @classmethod
    def create_managed_successor(
        cls,
        *,
        expected_authority: AuthorityRevisionBinding,
        decision_record: ManagedRevisionDecisionRecord,
    ) -> Self:
        origin, plan = cls._managed_origin_from_evidence(
            expected_authority=expected_authority,
            decision_record=decision_record,
        )
        successor = cls._create(
            aggregate_id=expected_authority.aggregate_id,
            authority_revision=plan.authorized_authority_revision,
            active_generation=plan.authorized_generation,
            origin_basis=origin,
        )
        successor.verify_managed_successor_origin(
            expected_authority=expected_authority,
            decision_record=decision_record,
        )
        return successor

    @classmethod
    def _managed_origin_from_evidence(
        cls,
        *,
        expected_authority: AuthorityRevisionBinding,
        decision_record: ManagedRevisionDecisionRecord,
    ) -> tuple[ManagedDecisionOriginBasis, PlannedAuthorityActivation]:
        command = decision_record.command
        plan = command.activation_plan
        if plan is None or not command.generation_manifest.requires_activation:
            raise ValueError("managed authority successor requires an activating decision")
        if command.expected_authority != expected_authority or (
            plan.expected_authority_id != expected_authority.authority_id
            or plan.expected_authority_revision != expected_authority.authority_revision
            or plan.expected_active_pointer_sha256 != expected_authority.active_pointer_sha256
            or plan.authorized_authority_revision != expected_authority.authority_revision + 1
            or plan.authorized_generation != command.generation_manifest.authorized_generation
            or plan.authorized_generation.generation_number
            != expected_authority.active_generation.generation_number + 1
        ):
            raise ValueError("decision record does not authorize the exact expected CAS successor")
        origin = ManagedDecisionOriginBasis(
            authoritative_record_resolution_required=True,
            request_record_sha256=command.request_record.record_sha256,
            decision_id=command.decision_id,
            decision_payload_sha256=command.decision_payload_sha256,
            decision_record_sha256=decision_record.record_sha256,
            activation_plan_id=plan.activation_plan_id,
            expected_authority_id=expected_authority.authority_id,
            expected_authority_revision=expected_authority.authority_revision,
            expected_active_pointer_sha256=expected_authority.active_pointer_sha256,
            prior_generation=expected_authority.active_generation,
        )
        return origin, plan

    def verify_managed_successor_origin(
        self,
        *,
        expected_authority: AuthorityRevisionBinding,
        decision_record: ManagedRevisionDecisionRecord,
    ) -> None:
        """Resolve managed-origin references against authoritative typed evidence."""

        if not isinstance(self.origin_basis, ManagedDecisionOriginBasis):
            raise ValueError("managed successor verification requires a managed-decision origin")
        expected_origin, plan = self._managed_origin_from_evidence(
            expected_authority=expected_authority,
            decision_record=decision_record,
        )
        expected = self._create(
            aggregate_id=expected_authority.aggregate_id,
            authority_revision=plan.authorized_authority_revision,
            active_generation=plan.authorized_generation,
            origin_basis=expected_origin,
        )
        if self != expected:
            raise ValueError(
                "managed authority origin does not resolve to exact prior authority and decision"
            )


class ManagedImpactBatchMemberBinding(_StrictFrozenModel):
    """One exact member of the committed Stage B inference batch."""

    execution_id: str = Field(pattern=r"^inference-exec:[0-9a-f]{64}$")
    receipt_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)


class ManagedImpactOutputRefBinding(_StrictFrozenModel):
    """Dependency-neutral copy of one complete Step 10b output-shard identity."""

    document_version_id: str = Field(pattern=r"^docv:[0-9a-f]{64}$")
    input_shard_id: str = Field(pattern=r"^impactin:[0-9a-f]{64}$")
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str = Field(pattern=r"^impactout:[0-9a-f]{64}$")
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_count: int = Field(gt=0, le=64)
    document_disposition: Literal["AFFECTED", "NO_CHANGE_REQUIRED", "UNRESOLVED"]

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.input_shard_id != f"impactin:{self.input_shard_sha256}":
            raise ValueError("managed impact input shard ID differs from its SHA")
        if self.output_shard_id != f"impactout:{self.output_shard_sha256}":
            raise ValueError("managed impact output shard ID differs from its SHA")
        return self


class ManagedImpactAnalysisEvidenceBinding(_StrictFrozenModel):
    """Durable, non-empty Stage B authority required by new managed review runs."""

    schema_version: Literal[1] = 1
    evidence_binding_id: str = Field(pattern=_ID_PATTERNS["mimpactevidence"].pattern)
    evidence_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    repository_id: str = Field(pattern=SHA256_PATTERN)
    batch_id: str = Field(pattern=r"^inference-batch:[0-9a-f]{64}$")
    batch_sha256: str = Field(pattern=SHA256_PATTERN)
    batch_members: tuple[ManagedImpactBatchMemberBinding, ...] = Field(
        min_length=1,
        max_length=MAX_MANAGED_IMPACT_OUTPUT_REFS_V1,
    )
    workload_id: str = Field(pattern=r"^impactwork:[0-9a-f]{64}$")
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    result_id: str = Field(pattern=r"^impactresult:[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shards: tuple[ManagedImpactOutputRefBinding, ...] = Field(
        min_length=1,
        max_length=MAX_MANAGED_IMPACT_OUTPUT_REFS_V1,
    )

    @field_validator("batch_members")
    @classmethod
    def _members(
        cls, values: tuple[ManagedImpactBatchMemberBinding, ...]
    ) -> tuple[ManagedImpactBatchMemberBinding, ...]:
        identities = tuple(
            (item.execution_id, item.receipt_artifact_id, item.outcome_sha256) for item in values
        )
        if identities != tuple(sorted(identities)):
            raise ValueError("managed impact batch members must be canonically ordered")
        if len({item.execution_id for item in values}) != len(values):
            raise ValueError("managed impact batch execution IDs must be unique")
        if len({item.receipt_artifact_id for item in values}) != len(values):
            raise ValueError("managed impact batch receipt IDs must be unique")
        if len({item.outcome_sha256 for item in values}) != len(values):
            raise ValueError("managed impact batch outcome SHAs must be unique")
        return values

    @field_validator("output_shards")
    @classmethod
    def _outputs(
        cls, values: tuple[ManagedImpactOutputRefBinding, ...]
    ) -> tuple[ManagedImpactOutputRefBinding, ...]:
        keys = tuple((item.document_version_id, item.input_shard_id) for item in values)
        if keys != tuple(sorted(keys)):
            raise ValueError("managed impact output refs must be canonically ordered")
        if len({item.document_version_id for item in values}) != len(values):
            raise ValueError("managed impact output refs require unique document versions")
        if len({item.input_shard_id for item in values}) != len(values):
            raise ValueError("managed impact output refs require unique input shards")
        if len({item.output_shard_id for item in values}) != len(values):
            raise ValueError("managed impact output refs require unique output shards")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", exclude={"evidence_binding_id", "evidence_binding_sha256"}
        )

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.batch_id != f"inference-batch:{self.batch_sha256}":
            raise ValueError("managed impact batch ID differs from its SHA")
        if self.workload_id != f"impactwork:{self.workload_sha256}":
            raise ValueError("managed impact workload ID differs from its SHA")
        if self.result_id != f"impactresult:{self.result_sha256}":
            raise ValueError("managed impact result ID differs from its SHA")
        if len(self.batch_members) != len(self.output_shards):
            raise ValueError("managed impact batch and output-shard counts must match")
        digest = _sha256(self._payload())
        if (
            self.evidence_binding_sha256 != digest
            or self.evidence_binding_id != f"mimpactevidence:{digest}"
        ):
            raise ValueError("managed impact evidence identity differs from exact authority")
        return self

    @classmethod
    def create(
        cls,
        *,
        repository_id: str,
        batch_id: str,
        batch_sha256: str,
        batch_members: tuple[ManagedImpactBatchMemberBinding, ...],
        workload_id: str,
        workload_sha256: str,
        result_id: str,
        result_sha256: str,
        output_shards: tuple[ManagedImpactOutputRefBinding, ...],
    ) -> Self:
        _preflight_collection(
            batch_members,
            label="managed impact batch members",
            minimum=1,
            maximum=MAX_MANAGED_IMPACT_OUTPUT_REFS_V1,
            unique_key=lambda item: item.execution_id,
        )
        _preflight_collection(
            output_shards,
            label="managed impact output refs",
            minimum=1,
            maximum=MAX_MANAGED_IMPACT_OUTPUT_REFS_V1,
            unique_key=lambda item: item.document_version_id,
        )
        members = tuple(
            sorted(
                batch_members,
                key=lambda item: (
                    item.execution_id,
                    item.receipt_artifact_id,
                    item.outcome_sha256,
                ),
            )
        )
        outputs = tuple(
            sorted(output_shards, key=lambda item: (item.document_version_id, item.input_shard_id))
        )
        values = {
            "schema_version": 1,
            "repository_id": repository_id,
            "batch_id": batch_id,
            "batch_sha256": batch_sha256,
            "batch_members": [item.model_dump(mode="json") for item in members],
            "workload_id": workload_id,
            "workload_sha256": workload_sha256,
            "result_id": result_id,
            "result_sha256": result_sha256,
            "output_shards": [item.model_dump(mode="json") for item in outputs],
        }
        digest = _sha256(values)
        return _validate_canonical_json(
            cls,
            {
                "evidence_binding_id": f"mimpactevidence:{digest}",
                "evidence_binding_sha256": digest,
                **values,
            },
        )


class ManagedAnalysisSetBinding(_StrictFrozenModel):
    schema_version: Literal[1, 2] = 1
    analysis_set_id: str = Field(pattern=_ID_PATTERNS["manalysis"].pattern)
    analysis_set_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_bootstrap: AnalysisBootstrapBinding
    incoming_logical_event_id: str
    incoming_event_identity: str = Field(pattern=_INCOMING_ID_RE.pattern)
    incoming_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    incoming_claim_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_bootstrap_binding_id: str = Field(pattern=r"^analysis-bootstrap:[0-9a-f]{64}$")
    analysis_bootstrap_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_result_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_result_sha256: str = Field(pattern=SHA256_PATTERN)
    attention_result_sha256: str = Field(pattern=SHA256_PATTERN)
    impact_result_sha256: str = Field(pattern=SHA256_PATTERN)
    impact_evidence: ManagedImpactAnalysisEvidenceBinding | None = None
    changed_claim_revision_ids: tuple[str, ...] = Field(max_length=MAX_MANAGED_CHANGED_CLAIMS_V1)
    global_relevant_claim_revision_ids: tuple[str, ...] = Field(
        max_length=MAX_MANAGED_GLOBAL_RELEVANT_CLAIMS_V1
    )

    @field_validator("changed_claim_revision_ids", "global_relevant_claim_revision_ids")
    @classmethod
    def _claim_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("analysis claim IDs must be ordered and unique")
        if any(re.fullmatch(CONTENT_ID_PATTERNS["claimrev"], item) is None for item in values):
            raise ValueError("analysis claim revision ID has the wrong shape")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"analysis_set_id", "analysis_set_sha256"})

    @model_serializer(mode="wrap")
    def _serialize_without_legacy_null(self, handler: Any) -> dict[str, Any]:
        values = cast(dict[str, Any], handler(self))
        if self.impact_evidence is None:
            values.pop("impact_evidence", None)
        return values

    @model_validator(mode="after")
    def _identity(self) -> Self:
        bootstrap = self.analysis_bootstrap
        if self.schema_version == 1:
            if self.impact_evidence is not None:
                raise ValueError("managed analysis v1 cannot carry impact evidence")
        elif self.impact_evidence is None:
            raise ValueError("managed analysis v2 requires durable impact evidence")
        elif self.impact_result_sha256 != self.impact_evidence.result_sha256:
            raise ValueError("managed analysis result SHA differs from durable impact evidence")
        if (
            self.incoming_logical_event_id != bootstrap.incoming_event_id
            or self.incoming_event_identity != bootstrap.incoming_event_identity
            or self.incoming_manifest_sha256 != bootstrap.incoming_manifest_sha256
            or self.incoming_claim_evidence_sha256 != bootstrap.incoming_claim_evidence_sha256
            or self.analysis_bootstrap_binding_id != bootstrap.binding_id
            or self.analysis_bootstrap_binding_sha256 != bootstrap.binding_sha256
            or self.changed_claim_revision_ids != bootstrap.changed_claim_revision_ids
        ):
            raise ValueError("analysis set must derive exact inputs from AnalysisBootstrapBinding")
        if not set(self.changed_claim_revision_ids).issubset(
            self.global_relevant_claim_revision_ids
        ):
            raise ValueError("changed claims must be globally relevant")
        digest = _sha256(self._payload())
        if self.analysis_set_sha256 != digest or self.analysis_set_id != f"manalysis:{digest}":
            raise ValueError("analysis set ID/SHA does not bind exact algorithm results")
        return self

    @classmethod
    def create(
        cls,
        *,
        analysis_bootstrap: AnalysisBootstrapBinding,
        candidate_result_sha256: str,
        classification_result_sha256: str,
        attention_result_sha256: str,
        impact_result_sha256: str,
        global_relevant_claim_revision_ids: tuple[str, ...],
    ) -> Self:
        _preflight_collection(
            analysis_bootstrap.changed_claim_revision_ids,
            label="changed claim revision IDs",
            maximum=MAX_MANAGED_CHANGED_CLAIMS_V1,
            unique_key=lambda item: item,
        )
        _preflight_collection(
            global_relevant_claim_revision_ids,
            label="globally relevant claim revision IDs",
            maximum=MAX_MANAGED_GLOBAL_RELEVANT_CLAIMS_V1,
            unique_key=lambda item: item,
        )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "analysis_bootstrap": analysis_bootstrap.model_dump(mode="json"),
            "incoming_logical_event_id": analysis_bootstrap.incoming_event_id,
            "incoming_event_identity": analysis_bootstrap.incoming_event_identity,
            "incoming_manifest_sha256": analysis_bootstrap.incoming_manifest_sha256,
            "incoming_claim_evidence_sha256": (analysis_bootstrap.incoming_claim_evidence_sha256),
            "analysis_bootstrap_binding_id": analysis_bootstrap.binding_id,
            "analysis_bootstrap_binding_sha256": analysis_bootstrap.binding_sha256,
            "candidate_result_sha256": candidate_result_sha256,
            "classification_result_sha256": classification_result_sha256,
            "attention_result_sha256": attention_result_sha256,
            "impact_result_sha256": impact_result_sha256,
            "changed_claim_revision_ids": analysis_bootstrap.changed_claim_revision_ids,
            "global_relevant_claim_revision_ids": global_relevant_claim_revision_ids,
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls,
            {
                "analysis_set_id": f"manalysis:{digest}",
                "analysis_set_sha256": digest,
                **payload,
                "analysis_bootstrap": analysis_bootstrap,
            },
        )

    @classmethod
    def create_with_impact_evidence(
        cls,
        *,
        analysis_bootstrap: AnalysisBootstrapBinding,
        candidate_result_sha256: str,
        classification_result_sha256: str,
        attention_result_sha256: str,
        impact_evidence: ManagedImpactAnalysisEvidenceBinding,
        global_relevant_claim_revision_ids: tuple[str, ...],
    ) -> Self:
        _preflight_collection(
            analysis_bootstrap.changed_claim_revision_ids,
            label="changed claim revision IDs",
            maximum=MAX_MANAGED_CHANGED_CLAIMS_V1,
            unique_key=lambda item: item,
        )
        _preflight_collection(
            global_relevant_claim_revision_ids,
            label="globally relevant claim revision IDs",
            maximum=MAX_MANAGED_GLOBAL_RELEVANT_CLAIMS_V1,
            unique_key=lambda item: item,
        )
        payload: dict[str, Any] = {
            "schema_version": 2,
            "analysis_bootstrap": analysis_bootstrap.model_dump(mode="json"),
            "incoming_logical_event_id": analysis_bootstrap.incoming_event_id,
            "incoming_event_identity": analysis_bootstrap.incoming_event_identity,
            "incoming_manifest_sha256": analysis_bootstrap.incoming_manifest_sha256,
            "incoming_claim_evidence_sha256": analysis_bootstrap.incoming_claim_evidence_sha256,
            "analysis_bootstrap_binding_id": analysis_bootstrap.binding_id,
            "analysis_bootstrap_binding_sha256": analysis_bootstrap.binding_sha256,
            "candidate_result_sha256": candidate_result_sha256,
            "classification_result_sha256": classification_result_sha256,
            "attention_result_sha256": attention_result_sha256,
            "impact_result_sha256": impact_evidence.result_sha256,
            "impact_evidence": impact_evidence.model_dump(mode="json"),
            "changed_claim_revision_ids": analysis_bootstrap.changed_claim_revision_ids,
            "global_relevant_claim_revision_ids": global_relevant_claim_revision_ids,
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls,
            {
                "analysis_set_id": f"manalysis:{digest}",
                "analysis_set_sha256": digest,
                **payload,
                "analysis_bootstrap": analysis_bootstrap,
                "impact_evidence": impact_evidence,
            },
        )


class TargetAnalysisBinding(_StrictFrozenModel):
    schema_version: Literal[1, 2] = 1
    target_analysis_id: str = Field(pattern=_ID_PATTERNS["mtargetanalysis"].pattern)
    target_key: str
    analysis_set_id: str = Field(pattern=_ID_PATTERNS["manalysis"].pattern)
    analysis_set_sha256: str = Field(pattern=SHA256_PATTERN)
    impact_result_sha256: str = Field(pattern=SHA256_PATTERN)
    target_result_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_input: ManagedArtifactRef
    input_envelope_sha256: str = Field(pattern=SHA256_PATTERN)
    staged_input_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_logical_key(value, label="target_key")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"target_analysis_id"})

    @model_serializer(mode="wrap")
    def _preserve_v1_bytes(self, handler: Any) -> dict[str, Any]:
        values = cast(dict[str, Any], handler(self))
        if self.schema_version == 1:
            values.pop("staged_input_sha256", None)
        return values

    def _envelope_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"target_analysis_id", "input_envelope_sha256", "inference_input"},
        )

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.inference_input.kind != ManagedArtifactKind.INFERENCE_INPUT:
            raise ValueError("target analysis must bind an inference-input artifact")
        if self.schema_version == 1:
            if self.staged_input_sha256 is not None:
                raise ValueError("target analysis v1 cannot carry staged input identity")
            envelope_bytes = canonical_json_bytes(self._envelope_payload())
            envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
            if self.input_envelope_sha256 != envelope_sha or (
                self.inference_input.sha256 != envelope_sha
                or self.inference_input.byte_count != len(envelope_bytes)
            ):
                raise ValueError("target analysis input envelope SHA does not bind provenance")
        elif (
            self.staged_input_sha256 != self.inference_input.sha256
            or "/analysis-input-" not in self.inference_input.path
            or not self.inference_input.path.endswith(
                f"analysis-input-{self.staged_input_sha256}.json"
            )
        ):
            raise ValueError("target analysis v2 does not bind its exact staged input")
        if self.target_analysis_id != _content_id("mtargetanalysis", self._payload()):
            raise ValueError("target analysis ID does not bind exact provenance")
        return self

    @classmethod
    def create(
        cls,
        *,
        target_key: str,
        analysis_set: ManagedAnalysisSetBinding,
        target_result_sha256: str,
        inference_input: ManagedArtifactRef,
    ) -> Self:
        envelope = {
            "schema_version": 1,
            "target_key": _exact_logical_key(target_key, label="target_key"),
            "analysis_set_id": analysis_set.analysis_set_id,
            "analysis_set_sha256": analysis_set.analysis_set_sha256,
            "impact_result_sha256": analysis_set.impact_result_sha256,
            "target_result_sha256": target_result_sha256,
        }
        envelope_bytes = canonical_json_bytes(envelope)
        envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
        if inference_input.sha256 != envelope_sha or (
            inference_input.byte_count != len(envelope_bytes)
        ):
            raise ValueError("inference input artifact must equal canonical target envelope bytes")
        values = {
            **envelope,
            "inference_input": inference_input.model_dump(mode="json"),
            "input_envelope_sha256": envelope_sha,
        }
        return _validate_canonical_json(
            cls, {"target_analysis_id": _content_id("mtargetanalysis", values), **values}
        )

    @classmethod
    def create_recorded(
        cls,
        *,
        target_key: str,
        analysis_set: ManagedAnalysisSetBinding,
        target_result_sha256: str,
        inference_input: ManagedArtifactRef,
        input_envelope_sha256: str,
    ) -> Self:
        values = {
            "schema_version": 2,
            "target_key": _exact_logical_key(target_key, label="target_key"),
            "analysis_set_id": analysis_set.analysis_set_id,
            "analysis_set_sha256": analysis_set.analysis_set_sha256,
            "impact_result_sha256": analysis_set.impact_result_sha256,
            "target_result_sha256": target_result_sha256,
            "inference_input": inference_input.model_dump(mode="json"),
            "input_envelope_sha256": _exact_sha256(
                input_envelope_sha256, label="input_envelope_sha256"
            ),
            "staged_input_sha256": inference_input.sha256,
        }
        return _validate_canonical_json(
            cls,
            {"target_analysis_id": _content_id("mtargetanalysis", values), **values},
        )


class ManagedRevisionPlanningBatchMemberBinding(_StrictFrozenModel):
    """One exact member of a committed revision-planning inference batch."""

    execution_id: str = Field(pattern=r"^inference-exec:[0-9a-f]{64}$")
    receipt_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)


class ManagedRevisionPlanningTargetBinding(_StrictFrozenModel):
    """Durable join from one planning input/output to its review subject."""

    target_key: str
    document_version_id: str = Field(pattern=CONTENT_ID_PATTERNS["docv"])
    input_shard_id: str = Field(pattern=r"^revisionin:[0-9a-f]{64}$")
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str = Field(pattern=r"^revisionout:[0-9a-f]{64}$")
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_id: str = Field(pattern=r"^inference-exec:[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_id: str = Field(pattern=_ID_PATTERNS["minference"].pattern)
    receipt_artifact_id: str = Field(pattern=_ID_PATTERNS["martifact"].pattern)
    subject_kind: Literal["managed-revision-plan", "no-change-impact-card"]
    subject_id: str
    subject_sha256: str = Field(pattern=SHA256_PATTERN)
    staged_artifacts: tuple[ManagedArtifactRef, ...] = Field(min_length=2, max_length=8)

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_logical_key(value, label="target_key")

    @field_validator("staged_artifacts")
    @classmethod
    def _staged(
        cls, values: tuple[ManagedArtifactRef, ...]
    ) -> tuple[ManagedArtifactRef, ...]:
        if values != tuple(sorted(values, key=lambda item: item.artifact_id)):
            raise ValueError("revision admission staged artifacts must be canonical")
        if len({item.artifact_id for item in values}) != len(values) or len(
            {item.path for item in values}
        ) != len(values):
            raise ValueError("revision admission staged artifacts must be unique")
        return values

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if self.input_shard_id != f"revisionin:{self.input_shard_sha256}":
            raise ValueError("revision admission input ID differs from its SHA")
        if self.output_shard_id != f"revisionout:{self.output_shard_sha256}":
            raise ValueError("revision admission output ID differs from its SHA")
        expected_subject = (
            "mplan" if self.subject_kind == "managed-revision-plan" else "mnochange"
        )
        if self.subject_id != f"{expected_subject}:{self.subject_sha256}":
            raise ValueError("revision admission subject ID differs from its kind/SHA")
        prefix = ("staging", "managed-review")
        if any(PurePosixPath(item.path).parts[:2] != prefix for item in self.staged_artifacts):
            raise ValueError("revision admission artifacts must use managed-review staging")
        by_sha = {item.sha256: item for item in self.staged_artifacts}
        if self.input_shard_sha256 not in by_sha or (
            by_sha[self.input_shard_sha256].kind != ManagedArtifactKind.INFERENCE_INPUT
        ):
            raise ValueError("revision admission omits its staged exact planning input")
        if self.output_shard_sha256 not in by_sha or (
            by_sha[self.output_shard_sha256].kind != ManagedArtifactKind.INFERENCE_OUTPUT
        ):
            raise ValueError("revision admission omits its staged exact planning output")
        return self


class ManagedRevisionPlanningAdmissionBinding(_StrictFrozenModel):
    """Restart-safe authority joining PR13 batch, staging, and review subjects."""

    schema_version: Literal[1] = 1
    admission_id: str = Field(pattern=_ID_PATTERNS["mrevisionadmission"].pattern)
    admission_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str
    repository_id: str = Field(pattern=SHA256_PATTERN)
    workload_id: str = Field(pattern=r"^revisionwork:[0-9a-f]{64}$")
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_set: ManagedAnalysisSetBinding
    analysis_set_id: str = Field(pattern=_ID_PATTERNS["manalysis"].pattern)
    analysis_set_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_snapshot_binding_id: str = Field(pattern=r"^reviewed-snapshot:[0-9a-f]{64}$")
    reviewed_snapshot_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    contract_binding_id: str = Field(pattern=_ID_PATTERNS["mcontract"].pattern)
    batch_id: str = Field(pattern=r"^inference-batch:[0-9a-f]{64}$")
    batch_sha256: str = Field(pattern=SHA256_PATTERN)
    batch_members: tuple[ManagedRevisionPlanningBatchMemberBinding, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_TARGETS_V1
    )
    staging_manifest_id: str = Field(pattern=r"^managed-staging:[0-9a-f]{64}$")
    staging_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    staging_manifest_path: str
    staging_completion_id: str = Field(
        pattern=r"^managed-staging-completion:[0-9a-f]{64}$"
    )
    staging_completion_sha256: str = Field(pattern=SHA256_PATTERN)
    staging_completion_path: str
    targets: tuple[ManagedRevisionPlanningTargetBinding, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_TARGETS_V1
    )

    @field_validator("run_id")
    @classmethod
    def _run(cls, value: str) -> str:
        return _exact_logical_key(value, label="run_id")

    @field_validator("staging_manifest_path", "staging_completion_path")
    @classmethod
    def _paths(cls, value: str) -> str:
        return _safe_path(value)

    @field_validator("batch_members")
    @classmethod
    def _members(
        cls, values: tuple[ManagedRevisionPlanningBatchMemberBinding, ...]
    ) -> tuple[ManagedRevisionPlanningBatchMemberBinding, ...]:
        identities = tuple(
            (item.execution_id, item.receipt_artifact_id, item.outcome_sha256)
            for item in values
        )
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(identities):
            raise ValueError("revision admission batch members must be unique and canonical")
        if len({item.execution_id for item in values}) != len(values) or len(
            {item.receipt_artifact_id for item in values}
        ) != len(values):
            raise ValueError("revision admission batch member identities must be unique")
        return values

    @field_validator("targets")
    @classmethod
    def _targets(
        cls, values: tuple[ManagedRevisionPlanningTargetBinding, ...]
    ) -> tuple[ManagedRevisionPlanningTargetBinding, ...]:
        keys = tuple((item.target_key, item.input_shard_id) for item in values)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("revision admission targets must be unique and canonical")
        for attribute in (
            "document_version_id",
            "input_shard_id",
            "output_shard_id",
            "execution_id",
            "receipt_id",
            "receipt_artifact_id",
            "subject_id",
        ):
            if len({getattr(item, attribute) for item in values}) != len(values):
                raise ValueError(f"revision admission target {attribute} values must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"admission_id", "admission_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.workload_id != f"revisionwork:{self.workload_sha256}":
            raise ValueError("revision admission workload ID differs from its SHA")
        if (
            self.analysis_set_id != f"manalysis:{self.analysis_set_sha256}"
            or self.analysis_set_id != self.analysis_set.analysis_set_id
            or self.analysis_set_sha256 != self.analysis_set.analysis_set_sha256
        ):
            raise ValueError("revision admission analysis-set ID differs from its SHA")
        if self.reviewed_snapshot_binding_id != (
            f"reviewed-snapshot:{self.reviewed_snapshot_binding_sha256}"
        ):
            raise ValueError("revision admission reviewed-snapshot ID differs from its SHA")
        if self.batch_id != f"inference-batch:{self.batch_sha256}":
            raise ValueError("revision admission batch ID differs from its SHA")
        if self.staging_manifest_id != f"managed-staging:{self.staging_manifest_sha256}":
            raise ValueError("revision admission staging manifest ID differs from its SHA")
        expected_manifest = (
            f"staging/managed-review/{self.run_id}/manifests/"
            f"{self.staging_manifest_sha256}.json"
        )
        expected_completion = f"staging/managed-review/{self.run_id}/COMPLETE.json"
        if (
            self.staging_manifest_path != expected_manifest
            or self.staging_completion_path != expected_completion
        ):
            raise ValueError("revision admission staging locators differ from its exact run")
        completion_payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "repository_id": self.repository_id,
            "manifest_id": self.staging_manifest_id,
            "manifest_sha256": self.staging_manifest_sha256,
            "manifest_path": self.staging_manifest_path,
            "completion_path": self.staging_completion_path,
        }
        completion_sha = _sha256(completion_payload)
        if (
            self.staging_completion_sha256 != completion_sha
            or self.staging_completion_id
            != f"managed-staging-completion:{completion_sha}"
        ):
            raise ValueError("revision admission staging completion is not self-consistent")
        expected_members = tuple(
            sorted(
                (
                    item.execution_id,
                    item.receipt_artifact_id,
                    item.outcome_sha256,
                )
                for item in self.targets
            )
        )
        actual_members = tuple(
            (item.execution_id, item.receipt_artifact_id, item.outcome_sha256)
            for item in self.batch_members
        )
        if actual_members != expected_members:
            raise ValueError("revision admission targets do not exactly cover its batch")
        staged = [artifact for target in self.targets for artifact in target.staged_artifacts]
        if len({item.artifact_id for item in staged}) != len(staged) or len(
            {item.path for item in staged}
        ) != len(staged):
            raise ValueError("revision admission staged members collide across targets")
        prefix = f"staging/managed-review/{self.run_id}/"
        if any(not item.path.startswith(prefix) for item in staged):
            raise ValueError("revision admission staged member escapes its exact run")
        digest = _sha256(self._payload())
        if (
            self.admission_sha256 != digest
            or self.admission_id != f"mrevisionadmission:{digest}"
        ):
            raise ValueError("revision admission ID/SHA differs from exact durable evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        raw_members = kwargs.pop("batch_members")
        raw_targets = kwargs.pop("targets")
        _preflight_collection(
            raw_members,
            label="revision admission batch members",
            minimum=1,
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item.execution_id,
        )
        _preflight_collection(
            raw_targets,
            label="revision admission targets",
            minimum=1,
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item.target_key,
        )
        members = tuple(
            sorted(
                raw_members,
                key=lambda item: (
                    item.execution_id,
                    item.receipt_artifact_id,
                    item.outcome_sha256,
                ),
            )
        )
        targets = tuple(sorted(raw_targets, key=lambda item: (item.target_key, item.input_shard_id)))
        values = {
            "schema_version": 1,
            **kwargs,
            "batch_members": members,
            "targets": targets,
        }
        payload = {
            key: (
                [item.model_dump(mode="json") for item in value]
                if key in {"batch_members", "targets"}
                else value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else value
            )
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls,
            {
                "admission_id": f"mrevisionadmission:{digest}",
                "admission_sha256": digest,
                **values,
            },
        )


class ManagedRunBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_binding_id: str = Field(pattern=_ID_PATTERNS["mrun"].pattern)
    run_id: str
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    prechange_head: AggregateHeadBinding
    analysis_head: AggregateHeadBinding
    algorithm_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_contract: ManagedInferenceContractBinding
    analysis_set: ManagedAnalysisSetBinding

    @field_validator("run_id")
    @classmethod
    def _run(cls, value: str) -> str:
        return _exact_logical_key(value, label="run_id")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_binding_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        bootstrap = self.analysis_set.analysis_bootstrap
        if (
            self.prechange_head.aggregate_id != bootstrap.aggregate_id
            or self.prechange_head.revision != bootstrap.prechange_revision
            or self.prechange_head.aggregate_sha256 != bootstrap.prechange_aggregate_sha256
            or self.analysis_head.aggregate_id != bootstrap.aggregate_id
            or self.analysis_head.revision != bootstrap.analysis_revision
            or self.analysis_head.aggregate_sha256 != bootstrap.analysis_aggregate_sha256
        ):
            raise ValueError("run heads must exactly match the revision-1/2 bootstrap binding")
        if self.inference_contract.algorithm_manifest_sha256 != self.algorithm_manifest_sha256:
            raise ValueError("run inference contract must bind the exact algorithm manifest")
        if self.run_binding_id != _content_id("mrun", self._payload()):
            raise ValueError("run binding ID does not match exact heads and inputs")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["run_id"] = _exact_logical_key(kwargs["run_id"], label="run_id")
        kwargs["operation_id"] = _exact_operation_id(kwargs["operation_id"])
        kwargs["algorithm_manifest_sha256"] = _exact_sha256(
            kwargs["algorithm_manifest_sha256"], label="algorithm_manifest_sha256"
        )
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        return _validate_canonical_json(
            cls, {"run_binding_id": _content_id("mrun", payload), **values}
        )


class ManagedGoverningSourceAdoptionBinding(_StrictFrozenModel):
    """Exact, read-only adoption of the reviewed incoming governing source.

    This is deliberately a repository locator and lineage proof, not a
    publication instruction.  The raw source and SourceNote remain at their
    original immutable manifest paths; later activation may include those exact
    bytes without copying or rerendering them.
    """

    schema_version: Literal[1] = 1
    adoption_id: str = Field(pattern=_ID_PATTERNS["mgoverningsource"].pattern)
    adoption_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_repository_id: str = Field(pattern=SHA256_PATTERN)
    source_repository_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_bootstrap_binding_id: str = Field(pattern=r"^analysis-bootstrap:[0-9a-f]{64}$")
    analysis_bootstrap_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    incoming_logical_event_id: str
    incoming_event_identity: str = Field(pattern=_INCOMING_ID_RE.pattern)
    incoming_manifest_path: str
    incoming_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    incoming_manifest_byte_count: int = Field(ge=1, le=MAX_MANAGED_ARTIFACT_BYTES_V1)
    alignment_attestation_id: str
    alignment_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    alignment_policy_version: str
    alignment_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    incoming_claim_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    document: DocumentVersionMetadata
    raw_artifact: ManagedArtifactRef
    source_note_artifact: ManagedArtifactRef
    source_note_logical_path: str
    source_note_snapshot_id: str = Field(pattern=r"^depsource:[0-9a-f]{64}$")
    source_note_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_snapshot_binding_id: str = Field(pattern=r"^reviewed-snapshot:[0-9a-f]{64}$")
    reviewed_snapshot_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_head: AggregateHeadBinding
    authoritative_repository_resolution_required: Literal[True] = True

    @field_validator(
        "incoming_manifest_path", "source_note_logical_path", "alignment_attestation_id"
    )
    @classmethod
    def _paths_and_identity(cls, value: str, info: Any) -> str:
        if info.field_name == "alignment_attestation_id":
            return _exact_logical_key(value, label=info.field_name)
        return _safe_path(value)

    @field_validator("incoming_logical_event_id")
    @classmethod
    def _event_id(cls, value: str) -> str:
        return _exact_logical_key(value, label="incoming_logical_event_id")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"adoption_id", "adoption_sha256"})

    @model_validator(mode="after")
    def _identity_and_artifacts(self) -> Self:
        if (
            self.raw_artifact.kind != ManagedArtifactKind.RAW_SOURCE
            or self.raw_artifact.path != self.document.source_path
            or self.raw_artifact.sha256 != self.document.source_sha256
        ):
            raise ValueError("governing adoption raw artifact differs from document metadata")
        if self.source_note_artifact.kind != ManagedArtifactKind.SOURCE_NOTE:
            raise ValueError("governing adoption SourceNote artifact has the wrong kind")
        expected_note_path = f"datasets/larkstead/processed/{self.source_note_logical_path}"
        if self.source_note_artifact.path != expected_note_path:
            raise ValueError("governing adoption SourceNote locator differs from logical note path")
        if self.source_note_snapshot_id != f"depsource:{self.source_note_snapshot_sha256}":
            raise ValueError("governing adoption SourceNote snapshot ID differs from its SHA")
        if self.reviewed_snapshot_binding_id != (
            f"reviewed-snapshot:{self.reviewed_snapshot_binding_sha256}"
        ):
            raise ValueError("governing adoption reviewed-snapshot ID differs from its SHA")
        expected_source_repository_binding_sha256 = _sha256(
            {
                "namespace": "mastervault.governing-source-repository.v1",
                "incoming_manifest_path": self.incoming_manifest_path,
                "reviewed_snapshot_binding_id": self.reviewed_snapshot_binding_id,
                "reviewed_inventory_sha256": self.reviewed_inventory_sha256,
                "raw_path": self.raw_artifact.path,
                "source_note_path": self.source_note_artifact.path,
            }
        )
        if (
            self.source_repository_binding_sha256
            != expected_source_repository_binding_sha256
        ):
            raise ValueError("governing adoption source-repository binding is not reproducible")
        digest = _sha256(self._payload())
        if self.adoption_sha256 != digest or self.adoption_id != f"mgoverningsource:{digest}":
            raise ValueError("governing source adoption ID/SHA differs from exact authority")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        expected_source_repository_binding_sha256 = _sha256(
            {
                "namespace": "mastervault.governing-source-repository.v1",
                "incoming_manifest_path": kwargs["incoming_manifest_path"],
                "reviewed_snapshot_binding_id": kwargs["reviewed_snapshot_binding_id"],
                "reviewed_inventory_sha256": kwargs["reviewed_inventory_sha256"],
                "raw_path": kwargs["raw_artifact"].path,
                "source_note_path": kwargs["source_note_artifact"].path,
            }
        )
        supplied_source_repository_binding_sha256 = kwargs.pop(
            "source_repository_binding_sha256", None
        )
        if (
            supplied_source_repository_binding_sha256 is not None
            and supplied_source_repository_binding_sha256
            != expected_source_repository_binding_sha256
        ):
            raise ValueError("supplied source-repository binding is not reproducible")
        kwargs["source_repository_binding_sha256"] = (
            expected_source_repository_binding_sha256
        )
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls,
            {
                "adoption_id": f"mgoverningsource:{digest}",
                "adoption_sha256": digest,
                **values,
            },
        )


class ManagedRunBindingV2(_StrictFrozenModel):
    """Run authority that durably admits one complete recorded planning run."""

    schema_version: Literal[2] = 2
    run_binding_id: str = Field(pattern=_ID_PATTERNS["mrun"].pattern)
    run_id: str
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    prechange_head: AggregateHeadBinding
    analysis_head: AggregateHeadBinding
    algorithm_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_contract: ManagedInferenceContractBinding
    analysis_set: ManagedAnalysisSetBinding
    revision_planning_admission: ManagedRevisionPlanningAdmissionBinding
    governing_source_adoption: ManagedGoverningSourceAdoptionBinding

    @field_validator("run_id")
    @classmethod
    def _run(cls, value: str) -> str:
        return _exact_logical_key(value, label="run_id")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_binding_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        bootstrap = self.analysis_set.analysis_bootstrap
        admission = self.revision_planning_admission
        adoption = self.governing_source_adoption
        impact_evidence = self.analysis_set.impact_evidence
        if (
            self.prechange_head.aggregate_id != bootstrap.aggregate_id
            or self.prechange_head.revision != bootstrap.prechange_revision
            or self.prechange_head.aggregate_sha256 != bootstrap.prechange_aggregate_sha256
            or self.analysis_head.aggregate_id != bootstrap.aggregate_id
            or self.analysis_head.revision != bootstrap.analysis_revision
            or self.analysis_head.aggregate_sha256 != bootstrap.analysis_aggregate_sha256
        ):
            raise ValueError("v2 run heads must exactly match revision-1/2 bootstrap binding")
        if self.inference_contract.algorithm_manifest_sha256 != self.algorithm_manifest_sha256:
            raise ValueError("v2 run inference contract must bind the exact algorithm manifest")
        if (
            admission.run_id != self.run_id
            or admission.analysis_set_id != self.analysis_set.analysis_set_id
            or admission.analysis_set_sha256 != self.analysis_set.analysis_set_sha256
            or admission.contract_binding_id != self.inference_contract.contract_binding_id
            or admission.reviewed_snapshot_binding_id
            != adoption.reviewed_snapshot_binding_id
            or admission.reviewed_snapshot_binding_sha256
            != adoption.reviewed_snapshot_binding_sha256
            or admission.temporal_decision_record_sha256
            != adoption.temporal_decision_record_sha256
        ):
            raise ValueError("v2 run binding differs from its durable planning admission")
        if (
            impact_evidence is None
            or adoption.evidence_repository_id != admission.repository_id
            or adoption.evidence_repository_id != impact_evidence.repository_id
            or adoption.analysis_bootstrap_binding_id != bootstrap.binding_id
            or adoption.analysis_bootstrap_binding_sha256 != bootstrap.binding_sha256
            or adoption.incoming_logical_event_id != bootstrap.incoming_event_id
            or adoption.incoming_event_identity != bootstrap.incoming_event_identity
            or adoption.incoming_manifest_sha256 != bootstrap.incoming_manifest_sha256
            or adoption.alignment_attestation_id != bootstrap.alignment_attestation_id
            or adoption.alignment_attestation_sha256 != bootstrap.alignment_attestation_sha256
            or adoption.alignment_policy_version != bootstrap.alignment_policy_version
            or adoption.alignment_payload_sha256 != bootstrap.alignment_payload_sha256
            or adoption.incoming_claim_evidence_sha256
            != bootstrap.incoming_claim_evidence_sha256
            or adoption.document.document_version_id != bootstrap.incoming_document_version_id
            or adoption.document.document_id != bootstrap.incoming_document_id
        ):
            raise ValueError("v2 run governing-source adoption differs from reviewed inputs")
        if self.run_binding_id != _content_id("mrun", self._payload()):
            raise ValueError("v2 run binding ID does not match exact heads and admission")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["run_id"] = _exact_logical_key(kwargs["run_id"], label="run_id")
        kwargs["operation_id"] = _exact_operation_id(kwargs["operation_id"])
        kwargs["algorithm_manifest_sha256"] = _exact_sha256(
            kwargs["algorithm_manifest_sha256"], label="algorithm_manifest_sha256"
        )
        values = {"schema_version": 2, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        return _validate_canonical_json(
            cls, {"run_binding_id": _content_id("mrun", payload), **values}
        )


ManagedRun = Annotated[
    ManagedRunBinding | ManagedRunBindingV2,
    Field(discriminator="schema_version"),
]


class ManagedReviewBaseBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    review_base_id: str = Field(pattern=_ID_PATTERNS["mreviewbase"].pattern)
    review_open_head: AggregateHeadBinding
    authority: AuthorityRevisionBinding

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"review_base_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.review_open_head.aggregate_id != self.authority.aggregate_id:
            raise ValueError("review-open head and authority must bind one aggregate")
        if self.review_base_id != _content_id("mreviewbase", self._payload()):
            raise ValueError("review base ID does not match head and active generation")
        return self

    @classmethod
    def create(
        cls, *, review_open_head: AggregateHeadBinding, authority: AuthorityRevisionBinding
    ) -> Self:
        values = {
            "schema_version": 1,
            "review_open_head": review_open_head.model_dump(mode="json"),
            "authority": authority.model_dump(mode="json"),
        }
        return _validate_canonical_json(
            cls, {"review_base_id": _content_id("mreviewbase", values), **values}
        )


class TemporalDecisionPrerequisite(_StrictFrozenModel):
    review_open_head: AggregateHeadBinding
    temporal_decision_record_sha256: str = Field(pattern=SHA256_PATTERN)


def managed_successor_version_label(
    *, predecessor: DocumentVersionMetadata, proposed_raw_sha256: str
) -> str:
    """Semantic version identity excludes staging/note/inference metadata."""

    digest = _sha256(
        {
            "namespace": "mastervault.managed-successor-version.v1",
            "predecessor_document_version_id": predecessor.document_version_id,
            "proposed_raw_sha256": proposed_raw_sha256,
        }
    )
    return f"managed-{digest}"


def derive_managed_successor(
    *,
    predecessor: DocumentVersionMetadata,
    target_key: str,
    proposed_raw: ManagedArtifactRef,
    raw_destination: PublicationDestination,
    effective_from: date,
    effective_to: date | None = None,
) -> DocumentVersionMetadata:
    target = _exact_logical_key(target_key, label="target_key")
    if target != predecessor.document_id:
        raise ValueError("target_key must equal predecessor logical document ID")
    if (
        raw_destination.target_key != target
        or raw_destination.kind != PublicationKind.RAW_SOURCE
        or raw_destination.expected_sha256 != proposed_raw.sha256
        or raw_destination.expected_byte_count != proposed_raw.byte_count
    ):
        raise ValueError("raw destination must bind exact proposed raw bytes")
    return DocumentVersionMetadata.create(
        document_id=target,
        document_family=predecessor.document_family,
        version_label=managed_successor_version_label(
            predecessor=predecessor,
            proposed_raw_sha256=proposed_raw.sha256,
        ),
        source_path=raw_destination.path,
        source_sha256=proposed_raw.sha256,
        declared_effective_from=effective_from,
        declared_effective_to=effective_to,
        role=predecessor.role,
        authority=predecessor.authority,
    )


class ManagedRevisionPlan(_StrictFrozenModel):
    kind: Literal["proposed-revision"] = "proposed-revision"
    schema_version: Literal[1] = 1
    plan_id: str = Field(pattern=_ID_PATTERNS["mplan"].pattern)
    plan_sha256: str = Field(pattern=SHA256_PATTERN)
    proposal_id: str = Field(pattern=_ID_PATTERNS["mproposal"].pattern)
    proposal_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str
    target_key: str
    predecessor: DocumentVersionMetadata
    predecessor_raw: ManagedArtifactRef
    predecessor_note: ManagedArtifactRef
    successor: DocumentVersionMetadata
    proposed_raw: ManagedArtifactRef
    proposed_note: ManagedArtifactRef
    raw_destination: PublicationDestination
    note_destination: PublicationDestination
    analysis: TargetAnalysisBinding
    inference_receipt: ContentAddressedInferenceReceipt
    validated_output: ManagedArtifactRef
    predecessor_projection: SourceNoteProjectionBinding
    successor_projection: SourceNoteProjectionBinding
    patch_attestation: PatchReconstructionAttestation
    claim_reconciliation: ClaimReconciliationBinding
    rationale: str = Field(min_length=1, max_length=4000)
    hunks: tuple[ManagedSemanticHunk, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_HUNKS_PER_PLAN_V1
    )

    @field_validator("run_id", "target_key")
    @classmethod
    def _keys(cls, value: str, info: Any) -> str:
        return _exact_logical_key(value, label=info.field_name)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("hunks")
    @classmethod
    def _hunks(cls, values: tuple[ManagedSemanticHunk, ...]) -> tuple[ManagedSemanticHunk, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.start_byte, item.hunk_id)))
        if values != ordered or len({item.hunk_id for item in values}) != len(values):
            raise ValueError("managed hunks must be ordered and unique")
        previous_end = 0
        for hunk in values:
            if hunk.start_byte < previous_end:
                raise ValueError("managed hunks must not overlap")
            previous_end = hunk.end_byte
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"plan_id", "plan_sha256"})

    def _proposal_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "plan_id",
                "plan_sha256",
                "proposal_id",
                "proposal_sha256",
                "inference_receipt",
                "validated_output",
            },
        )

    @classmethod
    def proposal_output_bytes(cls, **kwargs: Any) -> bytes:
        _exact_logical_key(kwargs["run_id"], label="run_id")
        _exact_logical_key(kwargs["target_key"], label="target_key")
        normalize_review_rationale(kwargs["rationale"])
        hunks = kwargs.get("hunks", ())
        _preflight_collection(
            hunks,
            label="managed hunks",
            minimum=1,
            maximum=MAX_MANAGED_HUNKS_PER_PLAN_V1,
            unique_key=lambda item: item.hunk_id,
        )
        payload = {
            "kind": "proposed-revision",
            "schema_version": 1,
            **{
                key: value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [item.model_dump(mode="json") for item in value]
                if key == "hunks"
                else value
                for key, value in kwargs.items()
                if key not in {"inference_receipt", "validated_output"}
            },
        }
        return canonical_json_bytes(payload)

    @model_validator(mode="after")
    def _bindings_and_identity(self) -> Self:
        if self.target_key != self.predecessor.document_id:
            raise ValueError("target_key must equal predecessor logical document ID")
        if self.analysis.target_key != self.target_key:
            raise ValueError("plan analysis must bind its exact target")
        if self.predecessor_raw.kind != ManagedArtifactKind.RAW_SOURCE or (
            self.predecessor_note.kind != ManagedArtifactKind.SOURCE_NOTE
        ):
            raise ValueError("predecessor artifacts have wrong kinds")
        if any(
            _is_managed_review_staging_path(artifact.path)
            for artifact in (self.predecessor_raw, self.predecessor_note)
        ):
            raise ValueError("predecessor artifacts cannot resolve from managed-review staging")
        if self.proposed_raw.kind != ManagedArtifactKind.RAW_SOURCE or (
            self.proposed_note.kind != ManagedArtifactKind.SOURCE_NOTE
        ):
            raise ValueError("proposed artifacts have wrong kinds")
        staging_prefix = ("staging", "managed-review", self.run_id, self.target_key)
        for artifact in (self.proposed_raw, self.proposed_note, self.validated_output):
            path = PurePosixPath(artifact.path)
            if path.parts[:4] != staging_prefix:
                raise ValueError("PR-A artifacts must use exact run/target-scoped staging root")
            if path.parts[0] in {"managed_sources", "vault"} or "index" in path.parts:
                raise ValueError("staging cannot enter publication or index roots")
        expected_staged_paths = {
            self.proposed_raw.path: (
                f"staging/managed-review/{self.run_id}/{self.target_key}/raw-"
                f"{self.proposed_raw.sha256}.md"
            ),
            self.proposed_note.path: (
                f"staging/managed-review/{self.run_id}/{self.target_key}/note-"
                f"{self.proposed_note.sha256}.md"
            ),
            self.validated_output.path: (
                f"staging/managed-review/{self.run_id}/{self.target_key}/validated-output-"
                f"{self.validated_output.sha256}.json"
            ),
        }
        if any(actual != expected for actual, expected in expected_staged_paths.items()):
            raise ValueError("staged artifact filenames must be exactly content-addressed by SHA")
        if self.validated_output.kind != ManagedArtifactKind.INFERENCE_OUTPUT or (
            self.validated_output.sha256 != self.inference_receipt.validated_output_sha256
        ):
            raise ValueError("validated output artifact must bind exact inference receipt output")
        if self.inference_receipt.replay_source_receipt_sha256 == self.validated_output.sha256:
            raise ValueError("replay receipt cannot be the current validated output")
        if self.predecessor.source_path != self.predecessor_raw.path or (
            self.predecessor.source_sha256 != self.predecessor_raw.sha256
        ):
            raise ValueError("predecessor metadata does not bind exact raw artifact")
        for artifact, destination, kind in (
            (self.proposed_raw, self.raw_destination, PublicationKind.RAW_SOURCE),
            (self.proposed_note, self.note_destination, PublicationKind.SOURCE_NOTE),
        ):
            if (
                destination.target_key != self.target_key
                or destination.kind != kind
                or destination.expected_sha256 != artifact.sha256
                or destination.expected_byte_count != artifact.byte_count
            ):
                raise ValueError("publication destination must equal exact staged SHA/bytes")
        expected_successor = derive_managed_successor(
            predecessor=self.predecessor,
            target_key=self.target_key,
            proposed_raw=self.proposed_raw,
            raw_destination=self.raw_destination,
            effective_from=self.successor.declared_effective_from,
            effective_to=self.successor.declared_effective_to,
        )
        if self.successor != expected_successor:
            raise ValueError("successor identity must derive only from predecessor and raw bytes")
        inputs_by_id = {item.artifact_id: item for item in self.inference_receipt.input_artifacts}
        if any(
            _is_managed_review_staging_path(artifact.path)
            and artifact != self.analysis.inference_input
            for artifact in self.inference_receipt.input_artifacts
        ):
            raise ValueError(
                "receipt staging input must be the subject's exact analysis input artifact"
            )
        if (
            inputs_by_id.get(self.analysis.inference_input.artifact_id)
            != self.analysis.inference_input
            or self.inference_receipt.input_envelope_sha256 != self.analysis.input_envelope_sha256
            or self.analysis.inference_input.path
            != (
                f"staging/managed-review/{self.run_id}/{self.target_key}/analysis-input-"
                f"{self.analysis.inference_input.sha256}.json"
            )
        ):
            raise ValueError("plan inference receipt does not bind exact target input envelope")
        for hunk in self.hunks:
            if hunk.base_artifact_id != self.predecessor_raw.artifact_id or (
                hunk.result_artifact_id != self.proposed_raw.artifact_id
            ):
                raise ValueError("every hunk must bind exact predecessor/result raw artifacts")
            if hunk.end_byte > self.predecessor_raw.byte_count:
                raise ValueError("hunk cannot extend beyond predecessor raw bytes")
            for citation in hunk.citations:
                cited_artifact = inputs_by_id.get(citation.artifact_id)
                if (
                    cited_artifact is None
                    or citation.artifact_sha256 != cited_artifact.sha256
                    or citation.end_byte > cited_artifact.byte_count
                ):
                    raise ValueError("hunk citation must bind an inference input artifact")
        if (
            self.patch_attestation.base_artifact_id != self.predecessor_raw.artifact_id
            or self.patch_attestation.base_sha256 != self.predecessor_raw.sha256
            or self.patch_attestation.base_byte_count != self.predecessor_raw.byte_count
            or self.patch_attestation.result_artifact_id != self.proposed_raw.artifact_id
            or self.patch_attestation.result_sha256 != self.proposed_raw.sha256
            or self.patch_attestation.result_byte_count != self.proposed_raw.byte_count
            or self.patch_attestation.ordered_hunk_ids != tuple(item.hunk_id for item in self.hunks)
            or self.patch_attestation.ordered_citation_ids
            != tuple(citation.citation_id for hunk in self.hunks for citation in hunk.citations)
        ):
            raise ValueError("patch attestation must cover the complete exact hunk program")
        if (
            self.predecessor_projection.raw_artifact != self.predecessor_raw
            or self.predecessor_projection.note_artifact != self.predecessor_note
            or self.predecessor_projection.canonical_raw_path != self.predecessor_raw.path
            or self.predecessor_projection.canonical_note_path != self.predecessor_note.path
        ):
            raise ValueError("predecessor projection does not bind exact predecessor artifacts")
        if (
            self.successor_projection.raw_artifact != self.proposed_raw
            or self.successor_projection.note_artifact != self.proposed_note
            or self.successor_projection.canonical_raw_path != self.raw_destination.path
            or self.successor_projection.canonical_note_path != self.note_destination.path
        ):
            raise ValueError("successor projection does not bind staged bytes/publication paths")
        if any(
            claim.document != self.predecessor
            for claim in self.predecessor_projection.projected_claims
        ):
            raise ValueError("predecessor projection embeds another document version")
        if any(
            claim.document != self.successor for claim in self.successor_projection.projected_claims
        ):
            raise ValueError("successor projection embeds another document version")
        if (
            self.claim_reconciliation.predecessor_projection_id
            != self.predecessor_projection.projection_id
            or self.claim_reconciliation.successor_projection_id
            != self.successor_projection.projection_id
            or self.claim_reconciliation.predecessor_revisions
            != self.predecessor_projection.projected_claims
            or self.claim_reconciliation.successor_revisions
            != self.successor_projection.projected_claims
        ):
            raise ValueError("claim reconciliation must bind both exact projection claim sets")
        proposal_bytes = canonical_json_bytes(self._proposal_payload())
        proposal_digest = hashlib.sha256(proposal_bytes).hexdigest()
        if (
            self.validated_output.sha256 != proposal_digest
            or self.validated_output.byte_count != len(proposal_bytes)
        ):
            raise ValueError("validated output bytes must equal canonical proposal envelope")
        if self.proposal_sha256 != proposal_digest or (
            self.proposal_id != f"mproposal:{proposal_digest}"
        ):
            raise ValueError("proposal identity does not bind validated output and exact revision")
        digest = _sha256(self._payload())
        if self.plan_sha256 != digest or self.plan_id != f"mplan:{digest}":
            raise ValueError("managed plan ID/SHA does not match its exact content")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["run_id"] = _exact_logical_key(kwargs["run_id"], label="run_id")
        kwargs["target_key"] = _exact_logical_key(kwargs["target_key"], label="target_key")
        kwargs["rationale"] = normalize_review_rationale(kwargs["rationale"])
        hunks = kwargs.get("hunks", ())
        _preflight_collection(
            hunks,
            label="managed hunks",
            minimum=1,
            maximum=MAX_MANAGED_HUNKS_PER_PLAN_V1,
            unique_key=lambda item: item.hunk_id,
        )
        proposal_payload = {
            "kind": "proposed-revision",
            "schema_version": 1,
            **{
                key: value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [item.model_dump(mode="json") for item in value]
                if key == "hunks"
                else value
                for key, value in kwargs.items()
            },
        }
        proposal_digest = hashlib.sha256(cls.proposal_output_bytes(**kwargs)).hexdigest()
        payload = {
            **proposal_payload,
            "proposal_id": f"mproposal:{proposal_digest}",
            "proposal_sha256": proposal_digest,
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls, {"plan_id": f"mplan:{digest}", "plan_sha256": digest, **payload}
        )


class NoChangeImpactCard(_StrictFrozenModel):
    kind: Literal["no-change"] = "no-change"
    schema_version: Literal[1] = 1
    card_id: str = Field(pattern=_ID_PATTERNS["mnochange"].pattern)
    card_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str
    target_key: str
    predecessor: DocumentVersionMetadata
    predecessor_raw: ManagedArtifactRef
    predecessor_note: ManagedArtifactRef
    predecessor_projection: SourceNoteProjectionBinding
    analysis: TargetAnalysisBinding
    inference_receipt: ContentAddressedInferenceReceipt
    validated_output: ManagedArtifactRef
    rationale: str = Field(min_length=1, max_length=4000)
    citations: tuple[GroundedArtifactCitation, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_CITATIONS_PER_HUNK_V1
    )

    @field_validator("run_id", "target_key")
    @classmethod
    def _keys(cls, value: str, info: Any) -> str:
        return _exact_logical_key(value, label=info.field_name)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("citations")
    @classmethod
    def _citations(
        cls, values: tuple[GroundedArtifactCitation, ...]
    ) -> tuple[GroundedArtifactCitation, ...]:
        if values != tuple(sorted(values, key=lambda item: item.citation_id)):
            raise ValueError("no-change citations must be ordered")
        if len({item.citation_id for item in values}) != len(values):
            raise ValueError("no-change citations must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"card_id", "card_sha256"})

    def _output_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"card_id", "card_sha256", "inference_receipt", "validated_output"},
        )

    @classmethod
    def proposal_output_bytes(cls, **kwargs: Any) -> bytes:
        _exact_logical_key(kwargs["run_id"], label="run_id")
        _exact_logical_key(kwargs["target_key"], label="target_key")
        normalize_review_rationale(kwargs["rationale"])
        citations = kwargs.get("citations", ())
        _preflight_collection(
            citations,
            label="no-change citations",
            minimum=1,
            maximum=MAX_MANAGED_CITATIONS_PER_HUNK_V1,
            unique_key=lambda item: item.citation_id,
        )
        payload = {
            "kind": "no-change",
            "schema_version": 1,
            **{
                key: value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [item.model_dump(mode="json") for item in value]
                if key == "citations"
                else value
                for key, value in kwargs.items()
                if key not in {"inference_receipt", "validated_output"}
            },
        }
        return canonical_json_bytes(payload)

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if self.target_key != self.predecessor.document_id or (
            self.analysis.target_key != self.target_key
        ):
            raise ValueError("no-change card must bind predecessor logical target")
        if self.predecessor.source_path != self.predecessor_raw.path or (
            self.predecessor.source_sha256 != self.predecessor_raw.sha256
        ):
            raise ValueError("no-change predecessor raw receipt does not bind metadata")
        if any(
            _is_managed_review_staging_path(artifact.path)
            for artifact in (self.predecessor_raw, self.predecessor_note)
        ):
            raise ValueError("predecessor artifacts cannot resolve from managed-review staging")
        if (
            self.predecessor_projection.raw_artifact != self.predecessor_raw
            or self.predecessor_projection.note_artifact != self.predecessor_note
            or self.predecessor_projection.canonical_raw_path != self.predecessor_raw.path
            or self.predecessor_projection.canonical_note_path != self.predecessor_note.path
        ):
            raise ValueError("no-change card must bind exact predecessor projection paths")
        if any(
            claim.document != self.predecessor
            for claim in self.predecessor_projection.projected_claims
        ):
            raise ValueError("no-change projection embeds another predecessor document")
        inputs = {item.artifact_id: item for item in self.inference_receipt.input_artifacts}
        if any(
            _is_managed_review_staging_path(artifact.path)
            and artifact != self.analysis.inference_input
            for artifact in self.inference_receipt.input_artifacts
        ):
            raise ValueError(
                "receipt staging input must be the subject's exact analysis input artifact"
            )
        invalid_citation = any(
            (artifact := inputs.get(citation.artifact_id)) is None
            or citation.artifact_sha256 != artifact.sha256
            or citation.end_byte > artifact.byte_count
            for citation in self.citations
        )
        if (
            inputs.get(self.analysis.inference_input.artifact_id) != self.analysis.inference_input
            or self.inference_receipt.input_envelope_sha256 != self.analysis.input_envelope_sha256
            or self.analysis.inference_input.path
            != (
                f"staging/managed-review/{self.run_id}/{self.target_key}/analysis-input-"
                f"{self.analysis.inference_input.sha256}.json"
            )
            or invalid_citation
        ):
            raise ValueError("no-change analysis/citations must bind exact input envelope")
        output_bytes = canonical_json_bytes(self._output_payload())
        output_sha = hashlib.sha256(output_bytes).hexdigest()
        expected_output_path = (
            f"staging/managed-review/{self.run_id}/{self.target_key}/validated-output-"
            f"{output_sha}.json"
        )
        if (
            self.validated_output.kind != ManagedArtifactKind.INFERENCE_OUTPUT
            or self.validated_output.sha256 != output_sha
            or self.validated_output.byte_count != len(output_bytes)
            or self.validated_output.path != expected_output_path
            or self.inference_receipt.validated_output_sha256 != output_sha
        ):
            raise ValueError("no-change validated output must equal canonical output envelope")
        if self.inference_receipt.replay_source_receipt_sha256 == self.validated_output.sha256:
            raise ValueError("replay receipt cannot be the current validated output")
        digest = _sha256(self._payload())
        if self.card_sha256 != digest or self.card_id != f"mnochange:{digest}":
            raise ValueError("no-change card ID/SHA does not match exact content")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["run_id"] = _exact_logical_key(kwargs["run_id"], label="run_id")
        kwargs["target_key"] = _exact_logical_key(kwargs["target_key"], label="target_key")
        kwargs["rationale"] = normalize_review_rationale(kwargs["rationale"])
        citations = kwargs.get("citations", ())
        _preflight_collection(
            citations,
            label="no-change citations",
            minimum=1,
            maximum=MAX_MANAGED_CITATIONS_PER_HUNK_V1,
            unique_key=lambda item: item.citation_id,
        )
        payload = {
            "kind": "no-change",
            "schema_version": 1,
            **{
                key: value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [item.model_dump(mode="json") for item in value]
                if key == "citations"
                else value
                for key, value in kwargs.items()
            },
        }
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls, {"card_id": f"mnochange:{digest}", "card_sha256": digest, **payload}
        )


class ManagedRevisionReviewTarget(_StrictFrozenModel):
    target_id: str = Field(pattern=_ID_PATTERNS["mtarget"].pattern)
    target_sha256: str = Field(pattern=SHA256_PATTERN)
    subject: ManagedRevisionPlan | NoChangeImpactCard

    @property
    def target_key(self) -> str:
        return self.subject.target_key

    @property
    def predecessor(self) -> DocumentVersionMetadata:
        return self.subject.predecessor

    @property
    def predecessor_paths(self) -> tuple[str, str]:
        return (self.subject.predecessor_raw.path, self.subject.predecessor_note.path)

    def _payload(self) -> dict[str, Any]:
        return {"subject": self.subject.model_dump(mode="json")}

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.target_sha256 != digest or self.target_id != f"mtarget:{digest}":
            raise ValueError("review target ID/SHA does not match exact subject")
        return self

    @classmethod
    def create(cls, subject: ManagedRevisionPlan | NoChangeImpactCard) -> Self:
        payload = {"subject": subject.model_dump(mode="json")}
        digest = _sha256(payload)
        return cls(target_id=f"mtarget:{digest}", target_sha256=digest, subject=subject)


class ManagedRevisionReviewBundle(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    bundle_id: str = Field(pattern=_ID_PATTERNS["mbundle"].pattern)
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    run_binding: ManagedRun
    review_base: ManagedReviewBaseBinding
    temporal_prerequisite: TemporalDecisionPrerequisite
    targets: tuple[ManagedRevisionReviewTarget, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_TARGETS_V1
    )

    @field_validator("targets")
    @classmethod
    def _targets(
        cls, values: tuple[ManagedRevisionReviewTarget, ...]
    ) -> tuple[ManagedRevisionReviewTarget, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.target_key, item.target_id)))
        if values != ordered or len({item.target_id for item in values}) != len(values):
            raise ValueError("review targets must be ordered and unique")
        if len({item.target_key for item in values}) != len(values):
            raise ValueError("review target keys must be unique")
        if len({item.predecessor.document_version_id for item in values}) != len(values):
            raise ValueError("predecessor document versions must be unique")
        if sum(isinstance(item.subject, ManagedRevisionPlan) for item in values) > (
            MAX_MANAGED_REVISION_PLANS_V1
        ):
            raise ValueError("bundle exceeds managed revision plan limit")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"bundle_id", "bundle_sha256"})

    def require_authoritative_impact_evidence(self) -> ManagedImpactAnalysisEvidenceBinding:
        """Reject legacy analysis before any new managed-review authority is accepted."""

        evidence = self.run_binding.analysis_set.impact_evidence
        if self.run_binding.analysis_set.schema_version != 2 or evidence is None:
            raise ValueError("new managed review requires v2 durable impact evidence")
        return evidence

    @model_validator(mode="after")
    def _bindings_collision_size(self) -> Self:
        if self.review_base.review_open_head != self.temporal_prerequisite.review_open_head:
            raise ValueError("only review-open head may equal temporal prerequisite")
        if (
            self.run_binding.analysis_head.aggregate_id
            != self.review_base.review_open_head.aggregate_id
        ):
            raise ValueError("run and review-open heads must belong to one aggregate")
        if self.review_base.review_open_head.revision <= self.run_binding.analysis_head.revision:
            raise ValueError("review-open head must follow the incoming analysis head")
        analysis = self.run_binding.analysis_set
        inference_contract = self.run_binding.inference_contract
        if isinstance(self.run_binding, ManagedRunBindingV2):
            adoption = self.run_binding.governing_source_adoption
            admission = self.run_binding.revision_planning_admission
            if (
                adoption.reviewed_head != self.review_base.review_open_head
                or adoption.reviewed_head != self.temporal_prerequisite.review_open_head
                or adoption.temporal_decision_record_sha256
                != self.temporal_prerequisite.temporal_decision_record_sha256
                or admission.reviewed_snapshot_binding_id
                != adoption.reviewed_snapshot_binding_id
                or admission.reviewed_snapshot_binding_sha256
                != adoption.reviewed_snapshot_binding_sha256
                or admission.temporal_decision_record_sha256
                != self.temporal_prerequisite.temporal_decision_record_sha256
            ):
                raise ValueError(
                    "admission and governing source must bind the exact reviewed temporal head"
                )
            admitted = self.run_binding.revision_planning_admission.targets
            target_subjects = tuple(
                sorted(
                    (
                        target.target_key,
                        target.predecessor.document_version_id,
                        (
                            target.subject.plan_id
                            if isinstance(target.subject, ManagedRevisionPlan)
                            else target.subject.card_id
                        ),
                        (
                            target.subject.plan_sha256
                            if isinstance(target.subject, ManagedRevisionPlan)
                            else target.subject.card_sha256
                        ),
                        target.subject.inference_receipt.receipt_id,
                        target.subject.inference_receipt.artifact_ref().artifact_id,
                        target.subject.validated_output.sha256,
                    )
                    for target in self.targets
                )
            )
            admitted_subjects = tuple(
                (
                    item.target_key,
                    item.document_version_id,
                    item.subject_id,
                    item.subject_sha256,
                    item.receipt_id,
                    item.receipt_artifact_id,
                    item.output_shard_sha256,
                )
                for item in admitted
            )
            if target_subjects != admitted_subjects:
                raise ValueError("managed review targets differ from v2 admitted planning subjects")
        impact_evidence = analysis.impact_evidence
        if impact_evidence is not None:
            output_by_document = {
                item.document_version_id: item for item in impact_evidence.output_shards
            }
            target_documents = tuple(
                sorted(item.predecessor.document_version_id for item in self.targets)
            )
            if target_documents != tuple(sorted(output_by_document)):
                raise ValueError(
                    "managed review targets must exactly cover durable impact output documents"
                )
            for target in self.targets:
                subject = target.subject
                output = output_by_document[target.predecessor.document_version_id]
                if subject.analysis.target_result_sha256 != output.output_shard_sha256:
                    raise ValueError(
                        "managed target analysis must bind the exact impact output shard SHA"
                    )
                if output.document_disposition == "UNRESOLVED":
                    raise ValueError("unresolved impact output cannot enter managed review")
                if output.document_disposition == "AFFECTED" and not isinstance(
                    subject, ManagedRevisionPlan
                ):
                    raise ValueError("affected impact output requires a managed revision plan")
                if output.document_disposition == "NO_CHANGE_REQUIRED" and not isinstance(
                    subject, NoChangeImpactCard
                ):
                    raise ValueError("no-change impact output requires an explicit no-change card")
        predecessor_paths: list[str] = []
        staging_paths: list[str] = []
        destination_paths: list[str] = []
        locator_bindings: dict[str, tuple[ManagedArtifactKind, str, int]] = {}
        total_hunks = 0
        for target in self.targets:
            subject = target.subject
            if subject.run_id != self.run_binding.run_id:
                raise ValueError("every subject must bind the exact managed run")
            if (
                subject.analysis.analysis_set_id != analysis.analysis_set_id
                or subject.analysis.analysis_set_sha256 != analysis.analysis_set_sha256
                or subject.analysis.impact_result_sha256 != analysis.impact_result_sha256
            ):
                raise ValueError("every subject must bind the run's exact analysis set")
            inference_contract.require_receipt(subject.inference_receipt)
            predecessor_paths.extend(target.predecessor_paths)
            staging_paths.extend(
                (subject.analysis.inference_input.path, subject.validated_output.path)
            )
            artifacts = [
                subject.predecessor_raw,
                subject.predecessor_note,
                subject.analysis.inference_input,
                subject.validated_output,
                *subject.inference_receipt.input_artifacts,
            ]
            if subject.inference_receipt.replay_source_receipt_artifact is not None:
                artifacts.append(subject.inference_receipt.replay_source_receipt_artifact)
            if isinstance(subject, ManagedRevisionPlan):
                staging_paths.extend((subject.proposed_raw.path, subject.proposed_note.path))
                artifacts.extend((subject.proposed_raw, subject.proposed_note))
                destination_paths.extend(
                    (subject.raw_destination.path, subject.note_destination.path)
                )
                total_hunks += len(subject.hunks)
            for artifact in artifacts:
                binding = _artifact_locator_binding(artifact)
                existing = locator_bindings.setdefault(artifact.path, binding)
                if existing != binding:
                    raise ValueError(
                        "artifact locator path cannot bind conflicting kind, SHA, or bytes"
                    )
            if isinstance(subject, ManagedRevisionPlan):
                for destination in (subject.raw_destination, subject.note_destination):
                    binding = _publication_locator_binding(destination)
                    existing = locator_bindings.setdefault(destination.path, binding)
                    if existing != binding:
                        raise ValueError(
                            "publication destination cannot conflict with an artifact locator"
                        )
        if len(set(staging_paths)) != len(staging_paths):
            raise ValueError("staged artifact paths must be globally unique")
        if set(staging_paths) & set(predecessor_paths):
            raise ValueError("predecessor and managed-review staging paths must be disjoint")
        if len(set(destination_paths)) != len(destination_paths):
            raise ValueError("publication destinations must be globally unique")
        if set(destination_paths) & set(predecessor_paths):
            raise ValueError("publication destinations cannot collide with any predecessor path")
        if total_hunks > MAX_MANAGED_HUNKS_PER_BUNDLE_V1:
            raise ValueError("bundle exceeds total semantic hunk limit")
        digest = _sha256(self._payload())
        if self.bundle_sha256 != digest or self.bundle_id != f"mbundle:{digest}":
            raise ValueError("bundle ID/SHA does not match exact content")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > (
            MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1
        ):
            raise ValueError("bundle exceeds canonical byte limit")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        raw_targets = kwargs.pop("targets")
        _preflight_collection(
            raw_targets,
            label="review targets",
            minimum=1,
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item.target_id,
        )
        if len({item.target_key for item in raw_targets}) != len(raw_targets):
            raise ValueError("review target keys must be unique before canonical ordering")
        plans = tuple(
            item.subject for item in raw_targets if isinstance(item.subject, ManagedRevisionPlan)
        )
        if len(plans) > MAX_MANAGED_REVISION_PLANS_V1:
            raise ValueError("bundle exceeds managed revision plan limit")
        if sum(len(plan.hunks) for plan in plans) > MAX_MANAGED_HUNKS_PER_BUNDLE_V1:
            raise ValueError("bundle exceeds total semantic hunk limit")
        targets = tuple(sorted(raw_targets, key=lambda item: (item.target_key, item.target_id)))
        values = {"schema_version": 1, **kwargs, "targets": targets}
        payload = {
            key: value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else [item.model_dump(mode="json") for item in value]
            if key == "targets"
            else value
            for key, value in values.items()
        }
        size_probe = {
            "bundle_id": "mbundle:" + "0" * 64,
            "bundle_sha256": "0" * 64,
            **payload,
        }
        if len(canonical_json_bytes(size_probe)) > MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1:
            raise ValueError("bundle exceeds canonical byte limit before identity hashing")
        digest = _sha256(payload)
        return _validate_canonical_json(
            cls, {"bundle_id": f"mbundle:{digest}", "bundle_sha256": digest, **values}
        )


def _managed_request_id(bundle: ManagedRevisionReviewBundle) -> str:
    return _content_id(
        "mrequest",
        {
            "bundle_id": bundle.bundle_id,
            "review_base_id": bundle.review_base.review_base_id,
            "authority_id": bundle.review_base.authority.authority_id,
            "schema_version": 1,
        },
    )


class ManagedRevisionReviewRequestCommand(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=_ID_PATTERNS["mrequest"].pattern)
    request_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle: ManagedRevisionReviewBundle
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    requester_id: str
    rationale: str = Field(min_length=1, max_length=4000)

    @field_validator("requester_id")
    @classmethod
    def _requester(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    def _intent(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"bundle", "request_payload_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.request_id != _managed_request_id(self.bundle):
            raise ValueError("request ID does not bind immutable bundle/base authority")
        if self.request_payload_sha256 != _sha256(self._intent()):
            raise ValueError("request payload SHA does not bind opening intent")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["operation_id"] = _exact_operation_id(kwargs["operation_id"])
        kwargs["requester_id"] = normalize_actor_id(kwargs["requester_id"])
        kwargs["rationale"] = normalize_review_rationale(kwargs["rationale"])
        bundle = kwargs["bundle"]
        request_id = _managed_request_id(bundle)
        values = {
            "schema_version": 1,
            "request_id": request_id,
            **kwargs,
        }
        intent = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
            if key != "bundle"
        }
        return _validate_canonical_json(cls, {"request_payload_sha256": _sha256(intent), **values})


class ManagedRevisionReviewRequestRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    record_id: str = Field(pattern=_ID_PATTERNS["mrequestrecord"].pattern)
    command: ManagedRevisionReviewRequestCommand
    requested_at: str
    committed_authority: AuthorityRevisionBinding
    record_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("requested_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_id", "record_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.committed_authority != self.command.bundle.review_base.authority:
            raise ValueError("request record must bind committed review-open authority")
        digest = _sha256(self._payload())
        if self.record_sha256 != digest or self.record_id != f"mrequestrecord:{digest}":
            raise ValueError("request record ID/SHA does not match committed evidence")
        return self

    @classmethod
    def create(
        cls,
        command: ManagedRevisionReviewRequestCommand,
        *,
        requested_at: str,
        committed_authority: AuthorityRevisionBinding,
    ) -> Self:
        values = {
            "schema_version": 1,
            "command": command.model_dump(mode="json"),
            "requested_at": _canonical_utc(requested_at),
            "committed_authority": committed_authority.model_dump(mode="json"),
        }
        digest = _sha256(values)
        return _validate_canonical_json(
            cls,
            {
                "record_id": f"mrequestrecord:{digest}",
                "record_sha256": digest,
                **values,
                "command": command,
                "committed_authority": committed_authority,
            },
        )


class ManagedRevisionReviewRequestReceipt(_StrictFrozenModel):
    """Store-delivery fact; replay does not alter immutable request intent."""

    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=_ID_PATTERNS["mrequestreceipt"].pattern)
    request_record_sha256: str = Field(pattern=SHA256_PATTERN)
    request_id: str = Field(pattern=_ID_PATTERNS["mrequest"].pattern)
    replayed: bool
    request_committed: Literal[True] = True

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.receipt_id != _content_id("mrequestreceipt", self._payload()):
            raise ValueError("request receipt ID does not bind exact delivery fact")
        return self

    @classmethod
    def create(cls, record: ManagedRevisionReviewRequestRecord, *, replayed: bool) -> Self:
        values = {
            "schema_version": 1,
            "request_record_sha256": record.record_sha256,
            "request_id": record.command.request_id,
            "replayed": replayed,
            "request_committed": True,
        }
        return _validate_canonical_json(
            cls, {"receipt_id": _content_id("mrequestreceipt", values), **values}
        )


class GenerationPublicationBinding(_StrictFrozenModel):
    target_key: str
    staged_artifact: ManagedArtifactRef
    destination: PublicationDestination

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_logical_key(value, label="target_key")

    @model_validator(mode="after")
    def _binding(self) -> Self:
        expected_artifact_kind = (
            ManagedArtifactKind.RAW_SOURCE
            if self.destination.kind == PublicationKind.RAW_SOURCE
            else ManagedArtifactKind.SOURCE_NOTE
        )
        if (
            self.destination.target_key != self.target_key
            or self.staged_artifact.kind != expected_artifact_kind
            or self.destination.expected_sha256 != self.staged_artifact.sha256
            or self.destination.expected_byte_count != self.staged_artifact.byte_count
        ):
            raise ValueError("generation publication must bind exact staged SHA/bytes")
        return self


class ManagedGenerationManifestBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    manifest_id: str = Field(pattern=_ID_PATTERNS["mgenerationmanifest"].pattern)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    request_id: str = Field(pattern=_ID_PATTERNS["mrequest"].pattern)
    bundle_id: str = Field(pattern=_ID_PATTERNS["mbundle"].pattern)
    prior_generation_id: str = Field(pattern=_ID_PATTERNS["mgeneration"].pattern)
    prior_generation_number: int = Field(ge=0)
    prior_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_number: int = Field(ge=0)
    requires_activation: bool
    manifest_encoding: Literal["content-addressed-overlay-v1"] = "content-addressed-overlay-v1"
    preserve_unmentioned_prior_entries: Literal[True] = True
    retained_review_target_keys: tuple[str, ...] = Field(max_length=MAX_MANAGED_TARGETS_V1)
    publication_delta: tuple[GenerationPublicationBinding, ...] = Field(
        max_length=2 * MAX_MANAGED_REVISION_PLANS_V1
    )
    authorized_generation: ContentAddressedGenerationBinding

    @field_validator("retained_review_target_keys")
    @classmethod
    def _retained(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("retained review target keys must be ordered and unique")
        return tuple(_exact_logical_key(value, label="target_key") for value in values)

    @field_validator("publication_delta")
    @classmethod
    def _publications(
        cls, values: tuple[GenerationPublicationBinding, ...]
    ) -> tuple[GenerationPublicationBinding, ...]:
        ordered = tuple(sorted(values, key=lambda item: item.destination.path))
        if values != ordered or len({item.destination.path for item in values}) != len(values):
            raise ValueError("generation publications must be ordered and destination-unique")
        if len({(item.target_key, item.destination.kind) for item in values}) != len(values):
            raise ValueError("generation publications must be unique by target and kind")
        return values

    def _manifest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "encoding": self.manifest_encoding,
            "base_manifest_sha256": self.prior_manifest_sha256,
            "preserve_unmentioned_prior_entries": self.preserve_unmentioned_prior_entries,
            "target_overrides": [item.model_dump(mode="json") for item in self.publication_delta],
        }

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id", "authorized_generation"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        exact_prior = ContentAddressedGenerationBinding.create(
            generation_number=self.prior_generation_number,
            manifest_sha256=self.prior_manifest_sha256,
        )
        if self.prior_generation_id != exact_prior.generation_id:
            raise ValueError("generation manifest does not bind exact typed prior generation")
        replacement_targets = {item.target_key for item in self.publication_delta}
        if replacement_targets & set(self.retained_review_target_keys):
            raise ValueError("a target cannot be both retained and replaced")
        expected_manifest_sha = (
            _sha256(self._manifest_payload())
            if self.publication_delta
            else self.prior_manifest_sha256
        )
        if self.manifest_sha256 != expected_manifest_sha:
            raise ValueError(
                "complete generation manifest SHA does not bind prior content and overrides"
            )
        if self.manifest_id != _content_id("mgenerationmanifest", self._identity_payload()):
            raise ValueError("generation manifest ID does not bind complete manifest evidence")
        if (
            self.authorized_generation.manifest_sha256 != self.manifest_sha256
            or self.authorized_generation.generation_number != self.generation_number
        ):
            raise ValueError("typed resulting generation must bind exact manifest SHA")
        if self.requires_activation != bool(self.publication_delta):
            raise ValueError("activation is required exactly when publication overrides exist")
        if self.requires_activation:
            if self.generation_number != self.prior_generation_number + 1:
                raise ValueError("activating generation must be exactly the next generation")
        elif (
            self.generation_number != self.prior_generation_number
            or self.authorized_generation != exact_prior
        ):
            raise ValueError("no-op manifest must retain the exact prior generation")
        return self

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        bundle_id: str,
        prior_generation: ContentAddressedGenerationBinding,
        publications: tuple[GenerationPublicationBinding, ...],
        retained_review_target_keys: tuple[str, ...],
    ) -> Self:
        _preflight_collection(
            publications,
            label="generation publications",
            maximum=2 * MAX_MANAGED_REVISION_PLANS_V1,
            unique_key=lambda item: item.destination.path,
        )
        if len({(item.target_key, item.destination.kind) for item in publications}) != len(
            publications
        ):
            raise ValueError("generation publications must be unique by target and kind")
        _preflight_collection(
            retained_review_target_keys,
            label="retained review target keys",
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item,
        )
        ordered = tuple(sorted(publications, key=lambda item: item.destination.path))
        retained = tuple(sorted(retained_review_target_keys))
        requires_activation = bool(ordered)
        generation_number = (
            prior_generation.generation_number + 1
            if requires_activation
            else prior_generation.generation_number
        )
        manifest_payload = {
            "schema_version": 1,
            "encoding": "content-addressed-overlay-v1",
            "base_manifest_sha256": prior_generation.manifest_sha256,
            "preserve_unmentioned_prior_entries": True,
            "target_overrides": [item.model_dump(mode="json") for item in ordered],
        }
        manifest_sha = _sha256(manifest_payload) if ordered else prior_generation.manifest_sha256
        values = {
            "schema_version": 1,
            "request_id": request_id,
            "bundle_id": bundle_id,
            "prior_generation_id": prior_generation.generation_id,
            "prior_generation_number": prior_generation.generation_number,
            "prior_manifest_sha256": prior_generation.manifest_sha256,
            "generation_number": generation_number,
            "requires_activation": requires_activation,
            "manifest_encoding": "content-addressed-overlay-v1",
            "preserve_unmentioned_prior_entries": True,
            "retained_review_target_keys": retained,
            "publication_delta": [item.model_dump(mode="json") for item in ordered],
            "manifest_sha256": manifest_sha,
        }
        manifest_id = _content_id("mgenerationmanifest", values)
        return _validate_canonical_json(
            cls,
            {
                "manifest_id": manifest_id,
                "authorized_generation": (
                    ContentAddressedGenerationBinding.create(
                        generation_number=generation_number,
                        manifest_sha256=manifest_sha,
                    )
                    if requires_activation
                    else prior_generation
                ),
                **values,
            },
        )


class ManagedGenerationManifestBindingV2(_StrictFrozenModel):
    """Overlay-v2: one adopted governing source plus optional downstream revisions."""

    schema_version: Literal[2] = 2
    manifest_id: str = Field(pattern=_ID_PATTERNS["mgenerationmanifest"].pattern)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    request_id: str = Field(pattern=_ID_PATTERNS["mrequest"].pattern)
    bundle_id: str = Field(pattern=_ID_PATTERNS["mbundle"].pattern)
    prior_generation_id: str = Field(pattern=_ID_PATTERNS["mgeneration"].pattern)
    prior_generation_number: int = Field(ge=0)
    prior_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_number: int = Field(ge=1)
    requires_activation: Literal[True] = True
    manifest_encoding: Literal["content-addressed-overlay-v2"] = "content-addressed-overlay-v2"
    preserve_unmentioned_prior_entries: Literal[True] = True
    retained_review_target_keys: tuple[str, ...] = Field(max_length=MAX_MANAGED_TARGETS_V1)
    governing_source_adoption: ManagedGoverningSourceAdoptionBinding
    publication_delta: tuple[GenerationPublicationBinding, ...] = Field(
        max_length=2 * MAX_MANAGED_REVISION_PLANS_V1
    )
    authorized_generation: ContentAddressedGenerationBinding

    @field_validator("retained_review_target_keys")
    @classmethod
    def _retained(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("retained review target keys must be ordered and unique")
        return tuple(_exact_logical_key(value, label="target_key") for value in values)

    @field_validator("publication_delta")
    @classmethod
    def _publications(
        cls, values: tuple[GenerationPublicationBinding, ...]
    ) -> tuple[GenerationPublicationBinding, ...]:
        ordered = tuple(sorted(values, key=lambda item: item.destination.path))
        if values != ordered or len({item.destination.path for item in values}) != len(values):
            raise ValueError("generation publications must be ordered and destination-unique")
        if len({(item.target_key, item.destination.kind) for item in values}) != len(values):
            raise ValueError("generation publications must be unique by target and kind")
        return values

    def _manifest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "encoding": self.manifest_encoding,
            "base_manifest_sha256": self.prior_manifest_sha256,
            "preserve_unmentioned_prior_entries": self.preserve_unmentioned_prior_entries,
            "governing_source_adoption": self.governing_source_adoption.model_dump(mode="json"),
            "target_overrides": [item.model_dump(mode="json") for item in self.publication_delta],
        }

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id", "authorized_generation"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        exact_prior = ContentAddressedGenerationBinding.create(
            generation_number=self.prior_generation_number,
            manifest_sha256=self.prior_manifest_sha256,
        )
        if self.prior_generation_id != exact_prior.generation_id:
            raise ValueError("generation manifest does not bind exact typed prior generation")
        if self.generation_number != self.prior_generation_number + 1:
            raise ValueError("overlay-v2 must authorize exactly the next generation")
        replacement_targets = {item.target_key for item in self.publication_delta}
        if replacement_targets & set(self.retained_review_target_keys):
            raise ValueError("a target cannot be both retained and replaced")
        expected_sha = _sha256(self._manifest_payload())
        if self.manifest_sha256 != expected_sha:
            raise ValueError("overlay-v2 manifest SHA does not bind adoption and overrides")
        if self.manifest_id != _content_id("mgenerationmanifest", self._identity_payload()):
            raise ValueError("overlay-v2 manifest ID does not bind complete manifest evidence")
        if (
            self.authorized_generation.manifest_sha256 != self.manifest_sha256
            or self.authorized_generation.generation_number != self.generation_number
        ):
            raise ValueError("typed resulting generation must bind exact overlay-v2 manifest")
        return self

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        bundle_id: str,
        prior_generation: ContentAddressedGenerationBinding,
        governing_source_adoption: ManagedGoverningSourceAdoptionBinding,
        publications: tuple[GenerationPublicationBinding, ...],
        retained_review_target_keys: tuple[str, ...],
    ) -> Self:
        _preflight_collection(
            publications,
            label="generation publications",
            maximum=2 * MAX_MANAGED_REVISION_PLANS_V1,
            unique_key=lambda item: item.destination.path,
        )
        if len({(item.target_key, item.destination.kind) for item in publications}) != len(
            publications
        ):
            raise ValueError("generation publications must be unique by target and kind")
        _preflight_collection(
            retained_review_target_keys,
            label="retained review target keys",
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item,
        )
        ordered = tuple(sorted(publications, key=lambda item: item.destination.path))
        retained = tuple(sorted(retained_review_target_keys))
        generation_number = prior_generation.generation_number + 1
        manifest_payload = {
            "schema_version": 2,
            "encoding": "content-addressed-overlay-v2",
            "base_manifest_sha256": prior_generation.manifest_sha256,
            "preserve_unmentioned_prior_entries": True,
            "governing_source_adoption": governing_source_adoption.model_dump(mode="json"),
            "target_overrides": [item.model_dump(mode="json") for item in ordered],
        }
        manifest_sha = _sha256(manifest_payload)
        values = {
            "schema_version": 2,
            "request_id": request_id,
            "bundle_id": bundle_id,
            "prior_generation_id": prior_generation.generation_id,
            "prior_generation_number": prior_generation.generation_number,
            "prior_manifest_sha256": prior_generation.manifest_sha256,
            "generation_number": generation_number,
            "requires_activation": True,
            "manifest_encoding": "content-addressed-overlay-v2",
            "preserve_unmentioned_prior_entries": True,
            "retained_review_target_keys": retained,
            "governing_source_adoption": governing_source_adoption.model_dump(mode="json"),
            "publication_delta": [item.model_dump(mode="json") for item in ordered],
            "manifest_sha256": manifest_sha,
        }
        manifest_id = _content_id("mgenerationmanifest", values)
        return _validate_canonical_json(
            cls,
            {
                "manifest_id": manifest_id,
                "authorized_generation": ContentAddressedGenerationBinding.create(
                    generation_number=generation_number,
                    manifest_sha256=manifest_sha,
                ),
                **values,
                "governing_source_adoption": governing_source_adoption,
            },
        )


ManagedGenerationManifest = Annotated[
    ManagedGenerationManifestBinding | ManagedGenerationManifestBindingV2,
    Field(discriminator="schema_version"),
]


class PlannedAuthorityActivation(_StrictFrozenModel):
    """PR-A authorization only; PR B must execute and receipt this CAS."""

    schema_version: Literal[1] = 1
    activation_plan_id: str = Field(pattern=_ID_PATTERNS["mauthorityplan"].pattern)
    status: Literal["authorized-inactive-until-pr-b"] = "authorized-inactive-until-pr-b"
    expected_authority_id: str = Field(pattern=_ID_PATTERNS["mauthority"].pattern)
    expected_authority_revision: int = Field(ge=0)
    expected_active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    authorized_authority_revision: int = Field(ge=1)
    authorized_generation: ContentAddressedGenerationBinding

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"activation_plan_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.authorized_authority_revision != self.expected_authority_revision + 1:
            raise ValueError("authorized authority revision must be the next CAS revision")
        if self.activation_plan_id != _content_id("mauthorityplan", self._payload()):
            raise ValueError("activation plan ID does not bind exact inactive CAS successor")
        return self

    @classmethod
    def create(
        cls,
        *,
        expected_authority: AuthorityRevisionBinding,
        authorized_generation: ContentAddressedGenerationBinding,
    ) -> Self:
        values = {
            "schema_version": 1,
            "status": "authorized-inactive-until-pr-b",
            "expected_authority_id": expected_authority.authority_id,
            "expected_authority_revision": expected_authority.authority_revision,
            "expected_active_pointer_sha256": expected_authority.active_pointer_sha256,
            "authorized_authority_revision": expected_authority.authority_revision + 1,
            "authorized_generation": authorized_generation.model_dump(mode="json"),
        }
        return _validate_canonical_json(
            cls, {"activation_plan_id": _content_id("mauthorityplan", values), **values}
        )


class ManagedRevisionDisposition(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    CONFIRM_NO_CHANGE = "confirm-no-change"


class ManagedBundleOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ManagedRevisionReviewOutcome(_StrictFrozenModel):
    target_id: str = Field(pattern=_ID_PATTERNS["mtarget"].pattern)
    original_target_sha256: str = Field(pattern=SHA256_PATTERN)
    disposition: ManagedRevisionDisposition
    edited_plan: ManagedRevisionPlan | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.disposition == ManagedRevisionDisposition.EDIT) != (self.edited_plan is not None):
            raise ValueError("only an edit outcome carries a final edited plan")
        return self


class ManagedRevisionDecisionCommand(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    decision_id: str = Field(pattern=_ID_PATTERNS["mdecision"].pattern)
    decision_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    request_record: ManagedRevisionReviewRequestRecord
    bundle_outcome: ManagedBundleOutcome
    reviewer_id: str
    rationale: str = Field(min_length=1, max_length=4000)
    items: tuple[ManagedRevisionReviewOutcome, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_TARGETS_V1
    )
    generation_manifest: ManagedGenerationManifest
    expected_authority: AuthorityRevisionBinding
    activation_plan: PlannedAuthorityActivation | None = None

    @property
    def bundle(self) -> ManagedRevisionReviewBundle:
        return self.request_record.command.bundle

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("items")
    @classmethod
    def _items(
        cls, values: tuple[ManagedRevisionReviewOutcome, ...]
    ) -> tuple[ManagedRevisionReviewOutcome, ...]:
        if values != tuple(sorted(values, key=lambda item: item.target_id)):
            raise ValueError("decision items must be ordered")
        if len({item.target_id for item in values}) != len(values):
            raise ValueError("decision items must be target-unique")
        return values

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", exclude={"decision_id", "decision_payload_sha256", "operation_id"}
        )

    def _operation_payload(self) -> dict[str, Any]:
        return {**self._identity_payload(), "operation_id": self.operation_id}

    def _final_plans(self) -> tuple[ManagedRevisionPlan, ...]:
        targets = {item.target_id: item for item in self.bundle.targets}
        plans: list[ManagedRevisionPlan] = []
        for outcome in self.items:
            target = targets[outcome.target_id]
            if isinstance(target.subject, ManagedRevisionPlan):
                if outcome.disposition == ManagedRevisionDisposition.APPROVE:
                    plans.append(target.subject)
                elif outcome.disposition == ManagedRevisionDisposition.EDIT:
                    assert outcome.edited_plan is not None
                    plans.append(outcome.edited_plan)
        return tuple(plans)

    @model_validator(mode="after")
    def _atomic_binding_and_identity(self) -> Self:
        bundle = self.bundle
        targets = {item.target_id: item for item in bundle.targets}
        if tuple(item.target_id for item in self.items) != tuple(sorted(targets)):
            raise ValueError("decision requires exactly one outcome for every target")
        if self.expected_authority != bundle.review_base.authority or (
            self.request_record.committed_authority != self.expected_authority
        ):
            raise ValueError("decision CAS must bind committed review-open authority")
        final_plans: list[ManagedRevisionPlan] = []
        for item in self.items:
            target = targets[item.target_id]
            if item.original_target_sha256 != target.target_sha256:
                raise ValueError("decision item does not bind original target SHA")
            if isinstance(target.subject, NoChangeImpactCard):
                if item.disposition not in {
                    ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
                    ManagedRevisionDisposition.REJECT,
                }:
                    raise ValueError("no-change card supports confirm-no-change or reject only")
                continue
            original = target.subject
            if item.disposition == ManagedRevisionDisposition.CONFIRM_NO_CHANGE:
                raise ValueError("revision plan cannot be confirmed as no-change")
            if item.disposition == ManagedRevisionDisposition.EDIT:
                assert item.edited_plan is not None
                edited = item.edited_plan
                if edited.plan_sha256 == original.plan_sha256:
                    raise ValueError("edited plan must differ from original")
                if (
                    edited.run_id != original.run_id
                    or edited.target_key != original.target_key
                    or edited.predecessor != original.predecessor
                    or edited.predecessor_raw != original.predecessor_raw
                    or edited.predecessor_note != original.predecessor_note
                    or edited.predecessor_projection != original.predecessor_projection
                    or edited.analysis != original.analysis
                    or edited.successor.declared_effective_from
                    != original.successor.declared_effective_from
                    or edited.successor.declared_effective_to
                    != original.successor.declared_effective_to
                ):
                    raise ValueError(
                        "edited plan must preserve run, analysis, predecessor, and interval"
                    )
                final_plans.append(edited)
            elif item.disposition == ManagedRevisionDisposition.APPROVE:
                final_plans.append(original)
        if self.bundle_outcome == ManagedBundleOutcome.REJECTED:
            if any(item.disposition != ManagedRevisionDisposition.REJECT for item in self.items):
                raise ValueError("rejected bundle requires every target rejected")
            if final_plans:
                raise ValueError("rejected bundle cannot publish plans")
        elif not final_plans and not any(
            item.disposition == ManagedRevisionDisposition.CONFIRM_NO_CHANGE for item in self.items
        ):
            raise ValueError("accepted bundle requires an approved/edit/no-change confirmation")
        if (
            len(final_plans) > MAX_MANAGED_REVISION_PLANS_V1
            or sum(len(item.hunks) for item in final_plans) > MAX_MANAGED_HUNKS_PER_BUNDLE_V1
        ):
            raise ValueError("final decision exceeds plan or hunk work limits")
        inference_contract = bundle.run_binding.inference_contract
        for plan in final_plans:
            inference_contract.require_receipt(plan.inference_receipt)
        predecessor_paths = {path for target in bundle.targets for path in target.predecessor_paths}
        destinations = [
            destination.path
            for plan in final_plans
            for destination in (plan.raw_destination, plan.note_destination)
        ]
        if len(set(destinations)) != len(destinations) or set(destinations) & predecessor_paths:
            raise ValueError("final publication destinations collide globally or with predecessor")
        artifacts_by_path: dict[str, tuple[ManagedArtifactKind, str, int]] = {}
        all_original_subjects = tuple(target.subject for target in bundle.targets)
        for subject in (*all_original_subjects, *final_plans):
            artifacts = [
                subject.predecessor_raw,
                subject.predecessor_note,
                subject.analysis.inference_input,
                subject.validated_output,
                *subject.inference_receipt.input_artifacts,
            ]
            if subject.inference_receipt.replay_source_receipt_artifact is not None:
                artifacts.append(subject.inference_receipt.replay_source_receipt_artifact)
            if isinstance(subject, ManagedRevisionPlan):
                artifacts.extend((subject.proposed_raw, subject.proposed_note))
            for artifact in artifacts:
                binding = _artifact_locator_binding(artifact)
                existing = artifacts_by_path.setdefault(artifact.path, binding)
                if existing != binding:
                    raise ValueError(
                        "original/final artifact locator path cannot bind conflicting kind, SHA, or bytes"
                    )
            if isinstance(subject, ManagedRevisionPlan):
                for destination in (subject.raw_destination, subject.note_destination):
                    binding = _publication_locator_binding(destination)
                    existing = artifacts_by_path.setdefault(destination.path, binding)
                    if existing != binding:
                        raise ValueError(
                            "original/final publication destination cannot conflict with an artifact locator"
                        )
        expected_publications = tuple(
            sorted(
                (
                    GenerationPublicationBinding(
                        target_key=plan.target_key,
                        staged_artifact=artifact,
                        destination=destination,
                    )
                    for plan in final_plans
                    for artifact, destination in (
                        (plan.proposed_raw, plan.raw_destination),
                        (plan.proposed_note, plan.note_destination),
                    )
                ),
                key=lambda item: item.destination.path,
            )
        )
        replaced_target_keys = {plan.target_key for plan in final_plans}
        expected_retained_keys = tuple(
            sorted(
                target.target_key
                for target in bundle.targets
                if target.target_key not in replaced_target_keys
            )
        )
        is_v2_run = type(bundle.run_binding) is ManagedRunBindingV2
        if self.bundle_outcome == ManagedBundleOutcome.REJECTED:
            if type(self.generation_manifest) is not ManagedGenerationManifestBinding:
                raise ValueError("a rejected bundle must retain the exact overlay-v1 no-op")
        elif is_v2_run:
            v2_run = cast(ManagedRunBindingV2, bundle.run_binding)
            if type(self.generation_manifest) is not ManagedGenerationManifestBindingV2 or (
                self.generation_manifest.governing_source_adoption
                != v2_run.governing_source_adoption
            ):
                raise ValueError("an accepted v2 run must adopt its exact governing source")
        elif type(self.generation_manifest) is not ManagedGenerationManifestBinding:
            raise ValueError("a legacy run must preserve the exact overlay-v1 manifest")
        if self.generation_manifest.publication_delta != expected_publications or (
            self.generation_manifest.retained_review_target_keys != expected_retained_keys
            or self.generation_manifest.prior_manifest_sha256
            != self.expected_authority.active_generation.manifest_sha256
            or self.generation_manifest.request_id != self.request_record.command.request_id
            or self.generation_manifest.bundle_id != bundle.bundle_id
            or self.generation_manifest.prior_generation_id
            != self.expected_authority.active_generation.generation_id
            or self.generation_manifest.prior_generation_number
            != self.expected_authority.active_generation.generation_number
        ):
            raise ValueError("authoritative decision must bind exact resulting generation manifest")
        if self.generation_manifest.requires_activation:
            if self.activation_plan is None or (
                self.activation_plan.expected_authority_id != self.expected_authority.authority_id
                or self.activation_plan.expected_authority_revision
                != self.expected_authority.authority_revision
                or self.activation_plan.expected_active_pointer_sha256
                != self.expected_authority.active_pointer_sha256
                or self.activation_plan.authorized_generation
                != self.generation_manifest.authorized_generation
            ):
                raise ValueError("activation plan must bind the exact inactive CAS successor")
        elif self.activation_plan is not None:
            raise ValueError("a no-op decision cannot carry an authority activation plan")
        decision_digest = _sha256(self._identity_payload())
        if self.decision_id != f"mdecision:{decision_digest}":
            raise ValueError("decision ID does not match exact authoritative outcome")
        if self.decision_payload_sha256 != _sha256(self._operation_payload()):
            raise ValueError("decision payload SHA does not bind operation identity")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > (
            MAX_MANAGED_DECISION_CANONICAL_BYTES_V1
        ):
            raise ValueError("decision exceeds canonical byte limit")
        return self

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        request_record: ManagedRevisionReviewRequestRecord,
        bundle_outcome: ManagedBundleOutcome,
        reviewer_id: str,
        rationale: str,
        items: tuple[ManagedRevisionReviewOutcome, ...],
    ) -> Self:
        operation_id = _exact_operation_id(operation_id)
        reviewer_id = normalize_actor_id(reviewer_id)
        rationale = normalize_review_rationale(rationale)
        _preflight_collection(
            items,
            label="decision items",
            minimum=1,
            maximum=MAX_MANAGED_TARGETS_V1,
            unique_key=lambda item: item.target_id,
        )
        ordered = tuple(sorted(items, key=lambda item: item.target_id))
        bundle = request_record.command.bundle
        targets = {item.target_id: item for item in bundle.targets}
        final_plans: list[ManagedRevisionPlan] = []
        for item in ordered:
            target = targets.get(item.target_id)
            if target is None or not isinstance(target.subject, ManagedRevisionPlan):
                continue
            if item.disposition == ManagedRevisionDisposition.APPROVE:
                final_plans.append(target.subject)
            elif item.disposition == ManagedRevisionDisposition.EDIT and item.edited_plan:
                final_plans.append(item.edited_plan)
        for plan in final_plans:
            bundle.run_binding.inference_contract.require_receipt(plan.inference_receipt)
        if len(final_plans) > MAX_MANAGED_REVISION_PLANS_V1:
            raise ValueError("final decision exceeds managed revision plan limit")
        if sum(len(plan.hunks) for plan in final_plans) > MAX_MANAGED_HUNKS_PER_BUNDLE_V1:
            raise ValueError("final decision exceeds aggregate semantic hunk limit")
        publication_previews = tuple(
            {
                "target_key": plan.target_key,
                "staged_artifact": artifact.model_dump(mode="json"),
                "destination": destination.model_dump(mode="json"),
            }
            for plan in final_plans
            for artifact, destination in (
                (plan.proposed_raw, plan.raw_destination),
                (plan.proposed_note, plan.note_destination),
            )
        )
        prior_generation = bundle.review_base.authority.active_generation
        retained_review_target_keys = tuple(
            sorted(
                target.target_key
                for target in bundle.targets
                if target.target_key not in {plan.target_key for plan in final_plans}
            )
        )
        accepted_v2 = (
            type(bundle.run_binding) is ManagedRunBindingV2
            and bundle_outcome == ManagedBundleOutcome.ACCEPTED
        )
        requires_activation = bool(publication_previews) or accepted_v2
        generation_number = (
            prior_generation.generation_number + 1
            if requires_activation
            else prior_generation.generation_number
        )
        preview_generation = (
            {
                "schema_version": 1,
                "generation_id": "mgeneration:" + "0" * 64,
                "generation_number": generation_number,
                "manifest_sha256": "0" * 64,
            }
            if requires_activation
            else prior_generation.model_dump(mode="json")
        )
        generation_manifest_preview: dict[str, Any] = {
            "schema_version": 2 if accepted_v2 else 1,
            "manifest_id": "mgenerationmanifest:" + "0" * 64,
            "request_id": request_record.command.request_id,
            "bundle_id": bundle.bundle_id,
            "manifest_sha256": (
                "0" * 64 if requires_activation else prior_generation.manifest_sha256
            ),
            "prior_generation_id": prior_generation.generation_id,
            "prior_generation_number": prior_generation.generation_number,
            "prior_manifest_sha256": prior_generation.manifest_sha256,
            "generation_number": generation_number,
            "requires_activation": requires_activation,
            "manifest_encoding": (
                "content-addressed-overlay-v2"
                if accepted_v2
                else "content-addressed-overlay-v1"
            ),
            "preserve_unmentioned_prior_entries": True,
            "retained_review_target_keys": list(retained_review_target_keys),
            "publication_delta": list(publication_previews),
            "authorized_generation": preview_generation,
        }
        if accepted_v2:
            assert isinstance(bundle.run_binding, ManagedRunBindingV2)
            generation_manifest_preview["governing_source_adoption"] = (
                bundle.run_binding.governing_source_adoption.model_dump(mode="json")
            )
        activation_plan_preview = (
            {
                "schema_version": 1,
                "activation_plan_id": "mauthorityplan:" + "0" * 64,
                "status": "authorized-inactive-until-pr-b",
                "expected_authority_id": bundle.review_base.authority.authority_id,
                "expected_authority_revision": bundle.review_base.authority.authority_revision,
                "expected_active_pointer_sha256": (
                    bundle.review_base.authority.active_pointer_sha256
                ),
                "authorized_authority_revision": (
                    bundle.review_base.authority.authority_revision + 1
                ),
                "authorized_generation": preview_generation,
            }
            if requires_activation
            else None
        )
        identity_preview = {
            "schema_version": 1,
            "request_record": request_record.model_dump(mode="json"),
            "bundle_outcome": bundle_outcome.value,
            "reviewer_id": reviewer_id,
            "rationale": rationale,
            "items": [item.model_dump(mode="json") for item in ordered],
            "generation_manifest": generation_manifest_preview,
            "expected_authority": bundle.review_base.authority.model_dump(mode="json"),
            "activation_plan": activation_plan_preview,
        }
        size_probe = {
            "decision_id": "mdecision:" + "0" * 64,
            "decision_payload_sha256": "0" * 64,
            "operation_id": operation_id,
            **identity_preview,
        }
        if len(canonical_json_bytes(size_probe)) > MAX_MANAGED_DECISION_CANONICAL_BYTES_V1:
            raise ValueError("decision exceeds canonical byte limit before identity hashing")
        publications = tuple(
            GenerationPublicationBinding(
                target_key=plan.target_key,
                staged_artifact=artifact,
                destination=destination,
            )
            for plan in final_plans
            for artifact, destination in (
                (plan.proposed_raw, plan.raw_destination),
                (plan.proposed_note, plan.note_destination),
            )
        )
        generation_manifest: ManagedGenerationManifestBinding | ManagedGenerationManifestBindingV2
        if accepted_v2:
            assert isinstance(bundle.run_binding, ManagedRunBindingV2)
            generation_manifest = ManagedGenerationManifestBindingV2.create(
                request_id=request_record.command.request_id,
                bundle_id=bundle.bundle_id,
                prior_generation=prior_generation,
                governing_source_adoption=bundle.run_binding.governing_source_adoption,
                publications=publications,
                retained_review_target_keys=retained_review_target_keys,
            )
        else:
            generation_manifest = ManagedGenerationManifestBinding.create(
                request_id=request_record.command.request_id,
                bundle_id=bundle.bundle_id,
                prior_generation=prior_generation,
                publications=publications,
                retained_review_target_keys=retained_review_target_keys,
            )
        activation_plan = (
            PlannedAuthorityActivation.create(
                expected_authority=bundle.review_base.authority,
                authorized_generation=generation_manifest.authorized_generation,
            )
            if generation_manifest.requires_activation
            else None
        )
        values = {
            "schema_version": 1,
            "operation_id": operation_id,
            "request_record": request_record,
            "bundle_outcome": bundle_outcome,
            "reviewer_id": reviewer_id,
            "rationale": rationale,
            "items": ordered,
            "generation_manifest": generation_manifest,
            "expected_authority": bundle.review_base.authority,
            "activation_plan": activation_plan,
        }
        identity = {
            "schema_version": 1,
            "request_record": request_record.model_dump(mode="json"),
            "bundle_outcome": bundle_outcome.value,
            "reviewer_id": reviewer_id,
            "rationale": rationale,
            "items": [item.model_dump(mode="json") for item in ordered],
            "generation_manifest": generation_manifest.model_dump(mode="json"),
            "expected_authority": bundle.review_base.authority.model_dump(mode="json"),
            "activation_plan": (
                activation_plan.model_dump(mode="json") if activation_plan is not None else None
            ),
        }
        decision_id = _content_id("mdecision", identity)
        operation_payload = {**identity, "operation_id": operation_id}
        return _validate_canonical_json(
            cls,
            {
                "decision_id": decision_id,
                "decision_payload_sha256": _sha256(operation_payload),
                **values,
            },
        )


class ManagedRevisionDecisionRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    record_id: str = Field(pattern=_ID_PATTERNS["mdecisionrecord"].pattern)
    command: ManagedRevisionDecisionCommand
    decided_at: str
    record_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_id", "record_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if datetime.fromisoformat(self.decided_at) < datetime.fromisoformat(
            self.command.request_record.requested_at
        ):
            raise ValueError("decision chronology cannot precede the review request")
        digest = _sha256(self._payload())
        if self.record_sha256 != digest or self.record_id != f"mdecisionrecord:{digest}":
            raise ValueError("decision record ID/SHA does not bind authoritative decision")
        return self

    @classmethod
    def create(cls, command: ManagedRevisionDecisionCommand, *, decided_at: str) -> Self:
        values = {
            "schema_version": 1,
            "command": command.model_dump(mode="json"),
            "decided_at": _canonical_utc(decided_at),
        }
        digest = _sha256(values)
        return _validate_canonical_json(
            cls,
            {
                "record_id": f"mdecisionrecord:{digest}",
                "record_sha256": digest,
                **values,
                "command": command,
            },
        )


class ManagedRevisionDecisionReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=_ID_PATTERNS["mreceipt"].pattern)
    request_record_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_id: str = Field(pattern=_ID_PATTERNS["mdecision"].pattern)
    authorized_generation: ContentAddressedGenerationBinding
    authorized_authority_revision: int | None = Field(default=None, ge=1)
    activation_required: bool
    replayed: bool
    decision_committed: Literal[True] = True
    generation_activated: Literal[False] = False

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.activation_required != (self.authorized_authority_revision is not None):
            raise ValueError("receipt activation flag and authority revision must agree")
        if self.receipt_id != _content_id("mreceipt", self._payload()):
            raise ValueError("decision receipt ID does not match committed lifecycle evidence")
        return self

    @classmethod
    def create(cls, record: ManagedRevisionDecisionRecord, *, replayed: bool) -> Self:
        command = record.command
        values = {
            "schema_version": 1,
            "request_record_sha256": command.request_record.record_sha256,
            "decision_record_sha256": record.record_sha256,
            "decision_id": command.decision_id,
            "authorized_generation": command.generation_manifest.authorized_generation.model_dump(
                mode="json"
            ),
            "authorized_authority_revision": (
                command.activation_plan.authorized_authority_revision
                if command.activation_plan is not None
                else None
            ),
            "activation_required": command.generation_manifest.requires_activation,
            "replayed": replayed,
            "decision_committed": True,
            "generation_activated": False,
        }
        return _validate_canonical_json(
            cls, {"receipt_id": _content_id("mreceipt", values), **values}
        )


class ManagedReviewLifecycleStatus(StrEnum):
    OPEN = "open"
    DECIDED = "decided"


class ManagedRevisionReviewView(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    view_id: str = Field(pattern=_ID_PATTERNS["mview"].pattern)
    request_record: ManagedRevisionReviewRequestRecord
    request_receipt: ManagedRevisionReviewRequestReceipt
    status: ManagedReviewLifecycleStatus
    decision_record: ManagedRevisionDecisionRecord | None = None
    receipt: ManagedRevisionDecisionReceipt | None = None

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"view_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if (
            self.request_receipt.request_record_sha256 != self.request_record.record_sha256
            or self.request_receipt.request_id != self.request_record.command.request_id
        ):
            raise ValueError("review view request receipt must bind exact request record")
        if (self.decision_record is None) != (self.receipt is None):
            raise ValueError("review view requires decision and receipt together")
        decided = self.decision_record is not None and self.receipt is not None
        if (self.status == ManagedReviewLifecycleStatus.DECIDED) != decided:
            raise ValueError("review view status must match decision/receipt presence")
        if self.decision_record is not None:
            if self.decision_record.command.request_record != self.request_record:
                raise ValueError("review view decision must bind exact request record")
            assert self.receipt is not None
            expected_receipt = ManagedRevisionDecisionReceipt.create(
                self.decision_record, replayed=self.receipt.replayed
            )
            if self.receipt != expected_receipt:
                raise ValueError(
                    "review view receipt must equal the exact decision command receipt"
                )
            if datetime.fromisoformat(self.decision_record.decided_at) < datetime.fromisoformat(
                self.request_record.requested_at
            ):
                raise ValueError("review view decision chronology cannot precede request")
        if self.view_id != _content_id("mview", self._payload()):
            raise ValueError("review view ID does not match lifecycle content")
        return self

    @classmethod
    def create(
        cls,
        *,
        request_record: ManagedRevisionReviewRequestRecord,
        request_receipt: ManagedRevisionReviewRequestReceipt,
        decision_record: ManagedRevisionDecisionRecord | None = None,
        receipt: ManagedRevisionDecisionReceipt | None = None,
    ) -> Self:
        status = (
            ManagedReviewLifecycleStatus.DECIDED
            if decision_record is not None and receipt is not None
            else ManagedReviewLifecycleStatus.OPEN
        )
        values = {
            "schema_version": 1,
            "request_record": request_record.model_dump(mode="json"),
            "request_receipt": request_receipt.model_dump(mode="json"),
            "status": status.value,
            "decision_record": (
                decision_record.model_dump(mode="json") if decision_record is not None else None
            ),
            "receipt": receipt.model_dump(mode="json") if receipt is not None else None,
        }
        return _validate_canonical_json(
            cls,
            {
                "view_id": _content_id("mview", values),
                **values,
                "request_record": request_record,
                "request_receipt": request_receipt,
                "decision_record": decision_record,
                "receipt": receipt,
            },
        )


__all__ = [
    "MAX_ATTESTED_TEXT_BYTES_V1",
    "MAX_MANAGED_ARTIFACT_BYTES_V1",
    "MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1",
    "MAX_MANAGED_CHANGED_CLAIMS_V1",
    "MAX_MANAGED_CITATION_QUOTE_BYTES_V1",
    "MAX_MANAGED_CITATIONS_PER_HUNK_V1",
    "MAX_MANAGED_CLAIM_TEXT_BYTES_V1",
    "MAX_MANAGED_DECISION_CANONICAL_BYTES_V1",
    "MAX_MANAGED_GLOBAL_RELEVANT_CLAIMS_V1",
    "MAX_MANAGED_HUNKS_PER_BUNDLE_V1",
    "MAX_MANAGED_HUNKS_PER_PLAN_V1",
    "MAX_MANAGED_INFERENCE_INPUTS_V1",
    "MAX_MANAGED_INFERENCE_INPUT_BYTES_V1",
    "MAX_MANAGED_IMPACT_OUTPUT_REFS_V1",
    "MAX_MANAGED_LOGICAL_KEY_BYTES_V1",
    "MAX_MANAGED_PATH_BYTES_V1",
    "MAX_MANAGED_PROJECTION_CLAIMS_V1",
    "MAX_MANAGED_RECONCILIATION_ENTRIES_V1",
    "MAX_MANAGED_REVISION_PLANS_V1",
    "MAX_MANAGED_SCOPES_PER_CLAIM_V1",
    "MAX_MANAGED_EVIDENCE_REFS_PER_CLAIM_V1",
    "MAX_MANAGED_EVIDENCE_QUOTE_BYTES_V1",
    "MAX_MANAGED_TARGETS_V1",
    "AggregateHeadBinding",
    "AttestationStatus",
    "AuthorityRevisionBinding",
    "ClaimReconciliationAction",
    "ClaimReconciliationBinding",
    "ClaimReconciliationEntry",
    "ContentAddressedGenerationBinding",
    "ContentAddressedInferenceReceipt",
    "GenerationPublicationBinding",
    "GenerationZeroManifestBinding",
    "GenerationZeroOriginBasis",
    "GroundedArtifactCitation",
    "InferenceExecutionMode",
    "InferenceUsage",
    "ManagedAnalysisSetBinding",
    "ManagedArtifactKind",
    "ManagedArtifactRef",
    "ManagedBundleOutcome",
    "ManagedGenerationManifest",
    "ManagedGenerationManifestBinding",
    "ManagedGenerationManifestBindingV2",
    "ManagedGoverningSourceAdoptionBinding",
    "ManagedImpactAnalysisEvidenceBinding",
    "ManagedImpactBatchMemberBinding",
    "ManagedImpactOutputRefBinding",
    "ManagedInferenceContractBinding",
    "ManagedDecisionOriginBasis",
    "WorkspaceGenerationZeroManifestBinding",
    "WorkspaceGenerationZeroOriginBasis",
    "ManagedReviewBaseBinding",
    "ManagedReviewLifecycleStatus",
    "ManagedRevisionDecisionCommand",
    "ManagedRevisionDecisionReceipt",
    "ManagedRevisionDecisionRecord",
    "ManagedRevisionDisposition",
    "ManagedRevisionPlan",
    "ManagedRevisionPlanningAdmissionBinding",
    "ManagedRevisionPlanningBatchMemberBinding",
    "ManagedRevisionPlanningTargetBinding",
    "ManagedRevisionReviewBundle",
    "ManagedRevisionReviewOutcome",
    "ManagedRevisionReviewRequestCommand",
    "ManagedRevisionReviewRequestRecord",
    "ManagedRevisionReviewRequestReceipt",
    "ManagedRevisionReviewTarget",
    "ManagedRevisionReviewView",
    "ManagedRunBinding",
    "ManagedRunBindingV2",
    "ManagedRun",
    "ManagedSemanticHunk",
    "NoChangeImpactCard",
    "PatchReconstructionAttestation",
    "PlannedAuthorityActivation",
    "PublicationDestination",
    "PublicationKind",
    "SourceNoteProjectionBinding",
    "TargetAnalysisBinding",
    "TemporalDecisionPrerequisite",
    "derive_managed_successor",
    "managed_successor_version_label",
]
