"""Strict contracts for generic workspace bootstrap evidence.

The manifest is deliberately explicit.  It never infers temporal authority
from v0.2 frontmatter: every managed SourceNote names its complete
``DocumentVersionMetadata`` and exact raw-source bytes.  The complete vault and
legacy-index expectations are separate so non-SourceNote knowledge remains
bound without being misrepresented as a managed temporal document.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, Self, SupportsIndex

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import (
    SHA256_PATTERN,
    ChangeControlAggregate,
    DocumentVersionMetadata,
    aggregate_sha256,
    canonical_json_bytes,
    normalize_logical_key,
)

if TYPE_CHECKING:
    from mastervault.change_control.legacy_index import (
        LegacyIndexAttestation,
        LegacyIndexAttestationGuard,
    )
    from mastervault.change_control.workspace_bootstrap_repository import (
        WorkspaceBootstrapEvidenceGuard,
    )

MAX_WORKSPACE_VAULT_MEMBERS_V1: Final = 4096
MAX_WORKSPACE_VAULT_DIRECTORIES_V1: Final = 4096
MAX_WORKSPACE_VAULT_DEPTH_V1: Final = 32
MAX_WORKSPACE_MANAGED_SOURCE_NOTES_V1: Final = 64
MAX_WORKSPACE_PATH_BYTES_V1: Final = 1024
MAX_WORKSPACE_MEMBER_BYTES_V1: Final = 16 * 1024 * 1024
MAX_WORKSPACE_RAW_SOURCE_BYTES_V1: Final = 64 * 1024 * 1024
MAX_WORKSPACE_RAW_SOURCE_TOTAL_BYTES_V1: Final = 256 * 1024 * 1024
MAX_WORKSPACE_INVENTORY_PAYLOAD_BYTES_V1: Final = 16 * 1024 * 1024
MAX_WORKSPACE_INDEX_BYTES_V1: Final = 2 * 1024 * 1024 * 1024
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_CAPABILITY_TOKEN = object()
_EVIDENCE_VERIFIER_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkspaceBootstrapAggregateSnapshot(Protocol):
    """Minimal persisted aggregate head consumed by evidence verification."""

    @property
    def aggregate(self) -> ChangeControlAggregate: ...

    @property
    def revision(self) -> int: ...

    @property
    def aggregate_sha256(self) -> str: ...


class _WorkspaceBootstrapEvidenceGuard(Protocol):
    """Live owner of all filesystem and legacy-index evidence descriptors."""

    def verify(self) -> None: ...


def _sha256(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, tuple | list):
        value = [_json_ready(item) for item in value]
    elif isinstance(value, dict):
        value = {str(key): _json_ready(item) for key, item in value.items()}
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _canonical_path(value: str, *, label: str) -> str:
    candidate = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or any(part.startswith(".") for part in candidate.parts)
        or candidate.as_posix() != value
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES_V1
    ):
        raise ValueError(f"{label} must be one NFC-normalized canonical relative POSIX path")
    return value


def _operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("operation_id is not canonical")
    return value


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


class WorkspaceNoteKind(StrEnum):
    SOURCE = "source"
    WIKI = "wiki"
    DECISION = "decision"
    STRATEGY = "strategy"


class WorkspaceVaultMember(_StrictFrozenModel):
    logical_path: str
    note_kind: WorkspaceNoteKind
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(ge=1, le=MAX_WORKSPACE_MEMBER_BYTES_V1)

    @field_validator("logical_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _canonical_path(value, label="vault member path")


class ManagedSourceNoteBootstrapMetadata(_StrictFrozenModel):
    logical_path: str
    source_note_sha256: str = Field(pattern=SHA256_PATTERN)
    source_note_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_MEMBER_BYTES_V1)
    source_root_id: str
    source_relative_path: str
    source_note_provenance: str = Field(min_length=1, max_length=4096)
    raw_source_path: str
    raw_source_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_source_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_RAW_SOURCE_BYTES_V1)
    document: DocumentVersionMetadata

    @field_validator("logical_path")
    @classmethod
    def _note_path(cls, value: str) -> str:
        return _canonical_path(value, label="managed SourceNote path")

    @field_validator("raw_source_path")
    @classmethod
    def _raw_path(cls, value: str) -> str:
        return _canonical_path(value, label="managed raw-source path")

    @field_validator("source_root_id")
    @classmethod
    def _root_id(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value) is None:
            raise ValueError("source_root_id must be one normalized path-safe key")
        return value

    @field_validator("source_relative_path")
    @classmethod
    def _source_relative_path(cls, value: str) -> str:
        return _canonical_path(value, label="managed source-relative path")

    @field_validator("source_note_provenance")
    @classmethod
    def _source_note_provenance(cls, value: str) -> str:
        if (
            value != value.strip()
            or len(value.encode("utf-8")) > 4096
            or any(unicodedata.category(character).startswith("C") for character in value)
        ):
            raise ValueError("source_note_provenance must be bounded exact visible text")
        return value

    @model_validator(mode="after")
    def _raw_authority(self) -> Self:
        if (
            self.document.source_path != self.raw_source_path
            or self.document.source_sha256 != self.raw_source_sha256
        ):
            raise ValueError("managed temporal metadata must explicitly bind the exact raw source")
        return self


class LegacyIndexExpectation(_StrictFrozenModel):
    """Bounded configuration expected of the existing legacy SQLite index.

    Exact document and record rows are derived by the verifier from the complete
    vault inventory.  They are deliberately not duplicated in this manifest.
    """

    schema_version: Literal[1] = 1
    index_file_sha256: str = Field(pattern=SHA256_PATTERN)
    index_file_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_INDEX_BYTES_V1)
    index_schema_version: int = Field(ge=1)
    embedding_model: str = Field(min_length=1, max_length=512)
    embedding_dimensions: int = Field(ge=1, le=1_000_000)

    @field_validator("embedding_model")
    @classmethod
    def _model(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("embedding_model is not normalized")
        return value


class WorkspaceBootstrapInventory(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    inventory_id: str = Field(pattern=r"^workspaceinventory:[0-9a-f]{64}$")
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_schema_version: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    vault_members: tuple[WorkspaceVaultMember, ...] = Field(
        min_length=1, max_length=MAX_WORKSPACE_VAULT_MEMBERS_V1
    )
    managed_source_notes: tuple[ManagedSourceNoteBootstrapMetadata, ...] = Field(
        min_length=1, max_length=MAX_WORKSPACE_MANAGED_SOURCE_NOTES_V1
    )
    legacy_index: LegacyIndexExpectation

    @field_validator("vault_members")
    @classmethod
    def _members(cls, values: tuple[WorkspaceVaultMember, ...]) -> tuple[WorkspaceVaultMember, ...]:
        ordered = tuple(sorted(values, key=lambda item: item.logical_path))
        if values != ordered or len({item.logical_path for item in values}) != len(values):
            raise ValueError("vault members must use unique canonical path order")
        return values

    @field_validator("managed_source_notes")
    @classmethod
    def _managed(
        cls, values: tuple[ManagedSourceNoteBootstrapMetadata, ...]
    ) -> tuple[ManagedSourceNoteBootstrapMetadata, ...]:
        ordered = tuple(sorted(values, key=lambda item: item.logical_path))
        if values != ordered or len({item.logical_path for item in values}) != len(values):
            raise ValueError("managed SourceNotes must use unique canonical path order")
        if len({item.document.document_version_id for item in values}) != len(values):
            raise ValueError("managed document-version identities must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"inventory_id", "inventory_sha256"})

    @model_validator(mode="after")
    def _identity_and_coverage(self) -> Self:
        payload = self._payload()
        if len(canonical_json_bytes(payload)) > MAX_WORKSPACE_INVENTORY_PAYLOAD_BYTES_V1:
            raise ValueError("workspace inventory canonical payload exceeds its bounded limit")
        members = {item.logical_path: item for item in self.vault_members}
        source_paths = {
            item.logical_path
            for item in self.vault_members
            if item.note_kind == WorkspaceNoteKind.SOURCE
        }
        managed_paths = {item.logical_path for item in self.managed_source_notes}
        if not managed_paths.issubset(source_paths):
            raise ValueError("managed SourceNotes must be an explicit vault SourceNote subset")
        for item in self.managed_source_notes:
            member = members[item.logical_path]
            if (
                member.content_sha256 != item.source_note_sha256
                or member.byte_count != item.source_note_byte_count
            ):
                raise ValueError("managed SourceNote metadata differs from vault inventory")
        if sum(item.raw_source_byte_count for item in self.managed_source_notes) > (
            MAX_WORKSPACE_RAW_SOURCE_TOTAL_BYTES_V1
        ):
            raise ValueError("managed raw-source inventory exceeds its aggregate byte limit")
        digest = _sha256(payload)
        if self.inventory_sha256 != digest or self.inventory_id != f"workspaceinventory:{digest}":
            raise ValueError("workspace inventory identity differs from exact content")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        digest = _sha256(values)
        return cls.model_validate(
            {
                "inventory_id": f"workspaceinventory:{digest}",
                "inventory_sha256": digest,
                **values,
            }
        )


class WorkspaceBootstrapIntent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    intent_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    aggregate_id: str
    inventory_id: str = Field(pattern=r"^workspaceinventory:[0-9a-f]{64}$")
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate(cls, value: str) -> str:
        return normalize_logical_key(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"bootstrap_id", "intent_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.intent_sha256 != digest or self.bootstrap_id != f"workspacebootstrap:{digest}":
            raise ValueError("workspace bootstrap intent identity differs from exact inputs")
        return self

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        aggregate_id: str,
        inventory: WorkspaceBootstrapInventory,
    ) -> Self:
        values = {
            "schema_version": 1,
            "operation_id": operation_id,
            "aggregate_id": aggregate_id,
            "inventory_id": inventory.inventory_id,
            "inventory_sha256": inventory.inventory_sha256,
        }
        digest = _sha256(values)
        return cls.model_validate(
            {"bootstrap_id": f"workspacebootstrap:{digest}", "intent_sha256": digest, **values}
        )


class WorkspaceInventoryReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^workspaceinventoryreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    aggregate_operation_id: str
    aggregate_id: str
    aggregate_revision: int = Field(ge=1)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_id: str = Field(pattern=r"^workspaceinventory:[0-9a-f]{64}$")
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    recorded_at: str

    @field_validator("operation_id", "aggregate_operation_id")
    @classmethod
    def _operations(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate(cls, value: str) -> str:
        return normalize_logical_key(value)

    @field_validator("recorded_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if (
            self.receipt_sha256 != digest
            or self.receipt_id != f"workspaceinventoryreceipt:{digest}"
        ):
            raise ValueError("workspace inventory receipt identity differs from exact evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        digest = _sha256(values)
        return cls.model_validate(
            {
                "receipt_id": f"workspaceinventoryreceipt:{digest}",
                "receipt_sha256": digest,
                **values,
            }
        )


class LegacyIndexReadinessReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^legacyindexreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    bootstrap_id: str = Field(pattern=r"^workspacebootstrap:[0-9a-f]{64}$")
    inventory_receipt_id: str = Field(pattern=r"^workspaceinventoryreceipt:[0-9a-f]{64}$")
    inventory_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    index_logical_fingerprint: str = Field(pattern=SHA256_PATTERN)
    index_file_sha256: str = Field(pattern=SHA256_PATTERN)
    index_file_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_INDEX_BYTES_V1)
    index_schema_version: int = Field(ge=1)
    embedding_model: str = Field(min_length=1, max_length=512)
    embedding_dimensions: int = Field(ge=1, le=1_000_000)
    ready_at: str

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("embedding_model")
    @classmethod
    def _model(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("embedding_model is not normalized")
        return value

    @field_validator("ready_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.receipt_sha256 != digest or self.receipt_id != f"legacyindexreceipt:{digest}":
            raise ValueError("legacy index receipt identity differs from exact evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        digest = _sha256(values)
        return cls.model_validate(
            {
                "receipt_id": f"legacyindexreceipt:{digest}",
                "receipt_sha256": digest,
                **values,
            }
        )


class WorkspaceBootstrapState(_StrictFrozenModel):
    intent: WorkspaceBootstrapIntent
    inventory: WorkspaceBootstrapInventory
    inventory_receipt: WorkspaceInventoryReceipt | None = None
    index_readiness_receipt: LegacyIndexReadinessReceipt | None = None

    @model_validator(mode="after")
    def _chain(self) -> Self:
        inventory = self.inventory_receipt
        readiness = self.index_readiness_receipt
        if not (
            self.inventory.inventory_id == self.intent.inventory_id
            and self.inventory.inventory_sha256 == self.intent.inventory_sha256
        ):
            raise ValueError("workspace inventory differs from exact bootstrap intent")
        if readiness is not None and inventory is None:
            raise ValueError("legacy index readiness requires an inventory receipt")
        if inventory is not None and not (
            inventory.bootstrap_id == self.intent.bootstrap_id
            and inventory.aggregate_id == self.intent.aggregate_id
            and inventory.aggregate_revision == 1
            and inventory.inventory_id == self.intent.inventory_id
            and inventory.inventory_sha256 == self.intent.inventory_sha256
        ):
            raise ValueError("inventory receipt differs from exact bootstrap intent")
        expectation = self.inventory.legacy_index
        if (
            readiness is not None
            and inventory is not None
            and not (
                readiness.bootstrap_id == self.intent.bootstrap_id
                and readiness.inventory_receipt_id == inventory.receipt_id
                and readiness.inventory_receipt_sha256 == inventory.receipt_sha256
                and readiness.index_file_sha256 == expectation.index_file_sha256
                and readiness.index_file_byte_count == expectation.index_file_byte_count
                and readiness.index_schema_version == expectation.index_schema_version
                and readiness.embedding_model == expectation.embedding_model
                and readiness.embedding_dimensions == expectation.embedding_dimensions
            )
        ):
            raise ValueError("legacy index readiness differs from exact bootstrap expectation")
        return self

    def require_complete(self) -> tuple[WorkspaceInventoryReceipt, LegacyIndexReadinessReceipt]:
        if self.inventory_receipt is None or self.index_readiness_receipt is None:
            raise ValueError("workspace bootstrap evidence is incomplete")
        return self.inventory_receipt, self.index_readiness_receipt


def _capability_seal(state: WorkspaceBootstrapState) -> str:
    return hmac.new(
        _CAPABILITY_SECRET,
        canonical_json_bytes(
            {
                "namespace": "mastervault.verified-workspace-bootstrap-capability.v1",
                "state": state.model_dump(mode="json"),
            }
        ),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True, eq=False)
class VerifiedWorkspaceBootstrapEvidenceVerifier:
    """Process-local handle retaining the live composite evidence guard."""

    _evidence_guard: _WorkspaceBootstrapEvidenceGuard
    _resolved_inventory: WorkspaceBootstrapInventory
    _resolved_aggregate: ChangeControlAggregate
    _legacy_attestation: LegacyIndexAttestation
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _EVIDENCE_VERIFIER_TOKEN:
            raise TypeError("workspace bootstrap evidence verifiers are guard-created only")
        if not callable(getattr(self._evidence_guard, "verify", None)):
            raise TypeError("workspace bootstrap evidence verifier requires a live guard")

    def __reduce__(self) -> Any:
        raise TypeError("workspace bootstrap evidence verifiers are non-serializable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("workspace bootstrap evidence verifiers are non-serializable")

    def verify(self) -> None:
        if (
            type(self) is not VerifiedWorkspaceBootstrapEvidenceVerifier
            or self._token is not _EVIDENCE_VERIFIER_TOKEN
        ):
            raise TypeError("workspace bootstrap evidence verifier is invalid")
        self._evidence_guard.verify()


def create_workspace_bootstrap_evidence_verifier(
    workspace_guard: WorkspaceBootstrapEvidenceGuard,
    legacy_index_guard: LegacyIndexAttestationGuard,
) -> VerifiedWorkspaceBootstrapEvidenceVerifier:
    """Create process-local proof retaining a complete live evidence guard.

    This public trust handoff accepts only the concrete guard owners returned
    by the hardened workspace and legacy-index openers. Arbitrary duck-typed
    callbacks cannot mint authority proof.
    """

    from mastervault.change_control.legacy_index import (
        LegacyIndexAttestationGuard,
        expected_legacy_index_projection_fingerprint,
    )
    from mastervault.change_control.workspace_bootstrap_repository import (
        WorkspaceBootstrapEvidenceGuard,
    )

    if type(workspace_guard) is not WorkspaceBootstrapEvidenceGuard or (
        type(legacy_index_guard) is not LegacyIndexAttestationGuard
    ):
        raise TypeError(
            "workspace bootstrap evidence requires exact workspace and legacy-index guards"
        )
    if legacy_index_guard.index_path != workspace_guard.resolved.legacy_index_path:
        raise ValueError("workspace and legacy-index guards do not bind the same index")
    attestation = legacy_index_guard.attestation
    expected_projection = expected_legacy_index_projection_fingerprint(
        notes=workspace_guard.resolved.exact_vault_notes,
        embedding_model_version=attestation.embedding_model_version,
        embedding_dimensions=attestation.embedding_dimensions,
    )
    if attestation.projection_fingerprint != expected_projection:
        raise ValueError(
            "legacy-index guard does not bind the workspace guard's exact projection"
        )

    @dataclass(frozen=True)
    class _CompleteEvidenceGuard:
        workspace: WorkspaceBootstrapEvidenceGuard
        legacy_index: LegacyIndexAttestationGuard

        def verify(self) -> None:
            self.workspace.verify()
            self.legacy_index.verify()

    return _mint_verified_workspace_bootstrap_evidence_verifier(
        _CompleteEvidenceGuard(workspace_guard, legacy_index_guard),
        resolved_inventory=workspace_guard.resolved.inventory,
        resolved_aggregate=workspace_guard.resolved.aggregate,
        legacy_attestation=attestation,
    )


def _mint_verified_workspace_bootstrap_evidence_verifier(
    evidence_guard: _WorkspaceBootstrapEvidenceGuard,
    *,
    resolved_inventory: WorkspaceBootstrapInventory,
    resolved_aggregate: ChangeControlAggregate,
    legacy_attestation: LegacyIndexAttestation,
) -> VerifiedWorkspaceBootstrapEvidenceVerifier:
    """Internal constructor retained for focused trust-boundary tests."""

    verifier = VerifiedWorkspaceBootstrapEvidenceVerifier(
        _evidence_guard=evidence_guard,
        _resolved_inventory=resolved_inventory,
        _resolved_aggregate=resolved_aggregate,
        _legacy_attestation=legacy_attestation,
        _token=_EVIDENCE_VERIFIER_TOKEN,
    )
    verifier.verify()
    return verifier


@dataclass(frozen=True, eq=False)
class VerifiedWorkspaceBootstrapCapability:
    """Live process-local proof over freshly guarded repository/index evidence."""

    state: WorkspaceBootstrapState
    _token: object
    _seal: str
    _evidence_verifier: VerifiedWorkspaceBootstrapEvidenceVerifier

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("verified workspace bootstrap capabilities are service-created only")
        if (
            type(self._evidence_verifier) is not VerifiedWorkspaceBootstrapEvidenceVerifier
            or self._evidence_verifier._token is not _EVIDENCE_VERIFIER_TOKEN
        ):
            raise TypeError("verified workspace bootstrap capability requires a live verifier")

    def __reduce__(self) -> Any:
        raise TypeError("verified workspace bootstrap capabilities are non-serializable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("verified workspace bootstrap capabilities are non-serializable")

    def verify(self) -> WorkspaceBootstrapState:
        try:
            self._evidence_verifier.verify()
        except Exception as exc:
            raise ValueError(
                "workspace bootstrap capability cannot freshly verify its evidence"
            ) from exc
        try:
            exact = WorkspaceBootstrapState.model_validate_json(
                canonical_json_bytes(self.state.model_dump(mode="json"))
            )
            exact.require_complete()
        except (TypeError, ValueError) as exc:
            raise ValueError("workspace bootstrap capability state is invalid") from exc
        if exact != self.state or not hmac.compare_digest(self._seal, _capability_seal(exact)):
            raise ValueError("workspace bootstrap capability seal is invalid")
        try:
            self._evidence_verifier.verify()
        except Exception as exc:
            raise ValueError(
                "workspace bootstrap capability evidence changed during verification"
            ) from exc
        return exact


def _mint_verified_workspace_bootstrap_capability(
    state: WorkspaceBootstrapState,
    *,
    evidence_verifier: VerifiedWorkspaceBootstrapEvidenceVerifier,
) -> VerifiedWorkspaceBootstrapCapability:
    """Mint after a service has reopened every exact repository/index byte.

    Kept private deliberately: persistence may consume the capability but must
    never mint it from self-hashed database rows.
    """

    exact = WorkspaceBootstrapState.model_validate_json(
        canonical_json_bytes(state.model_dump(mode="json"))
    )
    exact.require_complete()
    evidence_verifier.verify()
    return VerifiedWorkspaceBootstrapCapability(
        state=exact,
        _token=_CAPABILITY_TOKEN,
        _seal=_capability_seal(exact),
        _evidence_verifier=evidence_verifier,
    )


def verify_workspace_bootstrap_evidence(
    *,
    state: WorkspaceBootstrapState,
    resolved_inventory: WorkspaceBootstrapInventory,
    resolved_aggregate: ChangeControlAggregate,
    persisted_snapshot: WorkspaceBootstrapAggregateSnapshot,
    legacy_attestation: LegacyIndexAttestation,
    evidence_verifier: VerifiedWorkspaceBootstrapEvidenceVerifier,
) -> VerifiedWorkspaceBootstrapCapability:
    """Verify freshly reopened workspace, aggregate, and index evidence.

    The application must call the filesystem resolver and legacy-index
    attestor immediately before this boundary.  This function compares those
    fresh values with the complete persisted state and mints the otherwise
    private process-local capability only after every exact identity agrees.
    """

    try:
        evidence_verifier.verify()
        exact_state = WorkspaceBootstrapState.model_validate_json(
            canonical_json_bytes(state.model_dump(mode="json"))
        )
        inventory_receipt, readiness = exact_state.require_complete()
        exact_inventory = WorkspaceBootstrapInventory.model_validate_json(
            canonical_json_bytes(resolved_inventory.model_dump(mode="json"))
        )
        exact_resolved_aggregate = ChangeControlAggregate.model_validate_json(
            canonical_json_bytes(resolved_aggregate.model_dump(mode="json"))
        )
        exact_persisted_aggregate = ChangeControlAggregate.model_validate_json(
            canonical_json_bytes(persisted_snapshot.aggregate.model_dump(mode="json"))
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("workspace bootstrap evidence is invalid") from exc
    if not (
        resolved_inventory == evidence_verifier._resolved_inventory
        and resolved_aggregate == evidence_verifier._resolved_aggregate
        and legacy_attestation == evidence_verifier._legacy_attestation
    ):
        raise ValueError("workspace bootstrap evidence differs from its live guard owners")
    if exact_state != state or exact_inventory != resolved_inventory:
        raise ValueError("workspace bootstrap evidence changed during verification")
    if exact_inventory != exact_state.inventory:
        raise ValueError("fresh workspace inventory differs from persisted bootstrap evidence")
    if not (
        exact_resolved_aggregate == resolved_aggregate == exact_persisted_aggregate
        and exact_persisted_aggregate == persisted_snapshot.aggregate
        and exact_resolved_aggregate.aggregate_id
        == exact_state.intent.aggregate_id
        == inventory_receipt.aggregate_id
        and persisted_snapshot.revision == inventory_receipt.aggregate_revision == 1
        and aggregate_sha256(exact_resolved_aggregate)
        == persisted_snapshot.aggregate_sha256
        == inventory_receipt.aggregate_sha256
    ):
        raise ValueError("fresh aggregate head differs from persisted bootstrap evidence")
    expectation = exact_inventory.legacy_index
    if not (
        legacy_attestation.index_file_sha256
        == readiness.index_file_sha256
        == expectation.index_file_sha256
        and legacy_attestation.index_file_byte_count
        == readiness.index_file_byte_count
        == expectation.index_file_byte_count
        and legacy_attestation.logical_index_fingerprint == readiness.index_logical_fingerprint
        and legacy_attestation.storage_schema_version
        == readiness.index_schema_version
        == expectation.index_schema_version
        and legacy_attestation.embedding_model_version
        == readiness.embedding_model
        == expectation.embedding_model
        and legacy_attestation.embedding_dimensions
        == readiness.embedding_dimensions
        == expectation.embedding_dimensions
    ):
        raise ValueError("fresh legacy index attestation differs from bootstrap readiness")
    counts = dict(legacy_attestation.counts)
    if counts.get("documents") != len(exact_inventory.vault_members):
        raise ValueError("fresh legacy index document count differs from workspace inventory")
    capability = _mint_verified_workspace_bootstrap_capability(
        exact_state,
        evidence_verifier=evidence_verifier,
    )
    capability.verify()
    return capability


__all__ = [
    "LegacyIndexExpectation",
    "LegacyIndexReadinessReceipt",
    "ManagedSourceNoteBootstrapMetadata",
    "VerifiedWorkspaceBootstrapCapability",
    "WorkspaceBootstrapIntent",
    "WorkspaceBootstrapInventory",
    "WorkspaceBootstrapAggregateSnapshot",
    "WorkspaceBootstrapState",
    "WorkspaceInventoryReceipt",
    "WorkspaceNoteKind",
    "WorkspaceVaultMember",
    "create_workspace_bootstrap_evidence_verifier",
    "verify_workspace_bootstrap_evidence",
]
