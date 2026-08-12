"""Verified, evaluator-isolated boundary for one SL2 incoming document.

The manifest is a runtime ingestion receipt.  It identifies one raw source,
one canonical SourceNote, and exact raw evidence for every processed claim.
Only a sealed snapshot capability can expose the resulting temporal objects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import unicodedata
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from mastervault.change_control.analysis_binding import (
    ALIGNMENT_ATTESTATION_ID as ALIGNMENT_ATTESTATION_ID,
)
from mastervault.change_control.analysis_binding import (
    ALIGNMENT_POLICY_VERSION as ALIGNMENT_POLICY_VERSION,
)
from mastervault.change_control.analysis_binding import (
    MAX_INCOMING_CLAIMS,
    PINNED_ALIGNMENT_ATTESTATION_SHA256,
)
from mastervault.change_control.claim_scopes import (
    CLAIM_SCOPE_POLICY_VERSION,
    claim_scopes_v1,
)
from mastervault.change_control.models import (
    ClaimRevisionRegistry,
    ClaimSourceReference,
    DocumentAuthority,
    DocumentRole,
    DocumentVersionMetadata,
    VersionedClaimRevision,
    canonical_json_bytes,
    normalize_semantic_text,
)
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    require_exact_repository_path,
)
from mastervault.models import CLAIM_ID_RE, Claim, NoteType, SourceNote, SourceType, content_hash
from mastervault.vaultfs.frontmatter import parse_frontmatter, split_frontmatter
from mastervault.vaultfs.notes import extract_title

SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
LOGICAL_KEY_PATTERN: Final = r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"
EVENT_ID: Final = "sl2-returns-policy-v2"
DOCUMENT_ID: Final = "sl2-policy-returns-v2"
SOURCE_RELATIVE_PATH: Final = (
    "datasets/larkstead/raw/customer-support/policy/sl2-policy-returns-v2.md"
)
PROCESSED_RELATIVE_PATH: Final = (
    "datasets/larkstead/processed/customer-support/sources/policy-sl2-policy-returns-v2.md"
)
MANIFEST_RELATIVE_PATH: Final = "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"
ALIGNMENT_ATTESTATION_RELATIVE_PATH: Final = (
    "datasets/larkstead/change_control/attestations/sl2_returns_v2_alignment_v1.yaml"
)
EVENT_NAMESPACE: Final = "mastervault.incoming-event.v1"

MAX_MANIFEST_BYTES: Final = 16 * 1024
MAX_ALIGNMENT_ATTESTATION_BYTES: Final = 4 * 1024
MAX_SOURCE_BYTES: Final = 64 * 1024
MAX_PROCESSED_NOTE_BYTES: Final = 128 * 1024
MAX_AFFECTS_PER_CLAIM: Final = 16
MAX_AFFECT_SLUG_BYTES: Final = 128
MAX_SCAN_DEPTH: Final = 32
MAX_SCAN_NODES: Final = 1024
MAX_RAW_EVIDENCE_BYTES: Final = 512

_RAW_PREFIX = PurePosixPath("datasets/larkstead/raw")
_PROCESSED_PREFIX = PurePosixPath("datasets/larkstead/processed")
_VERIFIED_TOKEN = object()
_SEAL_SECRET = os.urandom(32)

_FORBIDDEN_KEYS = {
    "affected",
    "affected_document_ids",
    "classification",
    "dependencies",
    "edge_label",
    "expected_after",
    "expected_impacts",
    "expected_pair_classifications",
    "expected_patch",
    "expected_patches",
    "expected_review_decision",
    "grounding_document_id",
    "grounding_quote",
    "impact",
    "impacts",
    "patch",
    "patches",
    "rationale",
    "review_decision",
    "temporal_phases",
}
_FORBIDDEN_LABELS = {
    "approve",
    "coexists",
    "contradicts",
    "depends-on",
    "depends_on",
    "edit",
    "reject",
    "supersedes",
    "unrelated",
}
_ROLE_SOURCE_TYPES: Final = {
    DocumentRole.POLICY: {SourceType.POLICY},
    DocumentRole.MEMO: {SourceType.MEMO},
    DocumentRole.FAQ: {SourceType.FAQ},
    DocumentRole.SOP: {SourceType.SOP},
    DocumentRole.PROCESS: {SourceType.PROCESS},
    DocumentRole.PROPOSAL: {SourceType.PROPOSAL},
    DocumentRole.OTHER: {SourceType.OTHER},
}
_ANSWER_LABELS: Final = _FORBIDDEN_LABELS - {"supersedes"}


class IncomingBoundaryError(ValueError):
    """Runtime input crossed a path, shape, resource, or evaluator boundary."""


class IncomingIntegrityError(ValueError):
    """A manifest-bound file, evidence span, or capability failed verification."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _normalized_key(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold().replace("-", "_")


def _scan_answer_shaped_text(value: str, *, path: str) -> None:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = " ".join(re.findall(r"[a-z0-9]+", normalized))
    for forbidden in (*sorted(_FORBIDDEN_KEYS), *sorted(_ANSWER_LABELS)):
        needle = " ".join(re.findall(r"[a-z0-9]+", forbidden.casefold()))
        if re.search(rf"(?:^| ){re.escape(needle)}(?: |$)", words):
            raise IncomingBoundaryError(
                f"runtime structure contains evaluator-shaped text at {path}"
            )


def _preflight_yaml_text(value: str, *, label: str) -> None:
    """Reject ambiguous YAML syntax before ordinary construction collapses it."""
    try:
        tokens = tuple(yaml.scan(value))
    except (yaml.YAMLError, RecursionError) as exc:
        raise IncomingBoundaryError(f"{label} is not safe YAML: {exc}") from exc
    if any(isinstance(token, (AnchorToken, AliasToken)) for token in tokens):
        raise IncomingBoundaryError(f"{label} cannot contain YAML anchors or aliases")
    try:
        root = yaml.compose(value, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise IncomingBoundaryError(f"{label} is not safe YAML: {exc}") from exc

    nodes = 0

    def visit(node: Node, path: str, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_SCAN_NODES:
            raise IncomingBoundaryError(
                f"runtime structure exceeds fixed {MAX_SCAN_NODES}-node scan limit"
            )
        if depth > MAX_SCAN_DEPTH:
            raise IncomingBoundaryError(
                f"runtime structure exceeds fixed {MAX_SCAN_DEPTH}-level nesting limit"
            )
        if isinstance(node, MappingNode):
            seen: set[tuple[str, str]] = set()
            for key, nested in node.value:
                if not isinstance(key, ScalarNode):
                    raise IncomingBoundaryError(f"{label} contains a non-scalar key at {path}")
                identity = (key.tag, key.value)
                if identity in seen:
                    raise IncomingBoundaryError(
                        f"{label} contains a duplicate YAML key at {path}.{key.value}"
                    )
                seen.add(identity)
                visit(nested, f"{path}.{key.value}", depth + 1)
        elif isinstance(node, SequenceNode):
            for index, nested in enumerate(node.value):
                visit(nested, f"{path}[{index}]", depth + 1)

    if root is not None:
        visit(root, "$", 0)


def _scan_forbidden(value: Any, path: str = "$") -> None:
    """Reject answer-shaped data, cycles, and adversarially deep/wide YAML."""
    active: set[int] = set()
    nodes = 0

    def visit(item: Any, item_path: str, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_SCAN_NODES:
            raise IncomingBoundaryError(
                f"runtime structure exceeds fixed {MAX_SCAN_NODES}-node scan limit"
            )
        if depth > MAX_SCAN_DEPTH:
            raise IncomingBoundaryError(
                f"runtime structure exceeds fixed {MAX_SCAN_DEPTH}-level nesting limit"
            )

        if isinstance(item, dict):
            identity = id(item)
            if identity in active:
                raise IncomingBoundaryError(
                    f"runtime structure contains a cyclic alias at {item_path}"
                )
            active.add(identity)
            try:
                for key, nested in item.items():
                    normalized = _normalized_key(key)
                    if normalized.startswith("expected_") or normalized in _FORBIDDEN_KEYS:
                        raise IncomingBoundaryError(
                            f"runtime structure contains evaluator field at {item_path}.{key}"
                        )
                    visit(nested, f"{item_path}.{key}", depth + 1)
            finally:
                active.remove(identity)
            return

        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in active:
                raise IncomingBoundaryError(
                    f"runtime structure contains a cyclic alias at {item_path}"
                )
            active.add(identity)
            try:
                for index, nested in enumerate(item):
                    visit(nested, f"{item_path}[{index}]", depth + 1)
            finally:
                active.remove(identity)
            return

        if isinstance(item, str):
            normalized = unicodedata.normalize("NFKC", item).strip().casefold()
            if normalized in _FORBIDDEN_LABELS:
                raise IncomingBoundaryError(
                    f"runtime structure contains evaluator label at {item_path}"
                )
            _scan_answer_shaped_text(item, path=item_path)

    try:
        visit(value, path, 0)
    except RecursionError as exc:
        raise IncomingBoundaryError("runtime structure exceeded safe recursive scan depth") from exc


def _safe_repo_relative(value: str) -> str:
    raw = value
    candidate = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        not raw
        or raw != raw.strip()
        or "\\" in raw
        or "\x00" in raw
        or not candidate.parts
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or Path(raw).is_absolute()
        or bool(Path(raw).drive)
    ):
        raise ValueError(f"must be a safe repository-relative path, got {value!r}")
    if any(part.startswith(".") for part in candidate.parts):
        raise IncomingBoundaryError("runtime paths cannot contain hidden components")
    if "golden" in {part.casefold() for part in candidate.parts}:
        raise IncomingBoundaryError("runtime paths cannot enter evaluator gold")
    normalized = candidate.as_posix()
    if normalized != raw:
        raise ValueError("runtime paths must already use canonical relative POSIX form")
    return normalized


def _require_below(value: str, prefix: PurePosixPath, label: str) -> str:
    normalized = PurePosixPath(_safe_repo_relative(value))
    if normalized == prefix or not normalized.is_relative_to(prefix) or normalized.suffix != ".md":
        raise ValueError(f"{label} must name a Markdown file below {prefix.as_posix()}")
    return normalized.as_posix()


class RawEvidenceSpan(_StrictFrozenModel):
    quote: str = Field(min_length=1, max_length=MAX_RAW_EVIDENCE_BYTES)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)

    @model_validator(mode="after")
    def _exact_lengths(self) -> Self:
        encoded = self.quote.encode("utf-8")
        if len(encoded) > MAX_RAW_EVIDENCE_BYTES:
            raise ValueError(
                f"raw evidence exceeds fixed {MAX_RAW_EVIDENCE_BYTES}-byte atomic-span limit"
            )
        if self.quote != self.quote.strip():
            raise ValueError("raw evidence cannot contain surrounding whitespace")
        if "\n\n" in self.quote or re.search(r"(?m)^\s*#", self.quote):
            raise ValueError("raw evidence must be one atomic sentence, not a section")
        if self.quote[-1] not in ".!?" or len(re.findall(r"[.!?]", self.quote)) != 1:
            raise ValueError("raw evidence must contain exactly one complete sentence")
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("raw evidence character offsets must exactly span quote")
        if self.end_byte - self.start_byte != len(self.quote.encode("utf-8")):
            raise ValueError("raw evidence byte offsets must exactly span UTF-8 quote")
        return self


class IncomingClaimBinding(_StrictFrozenModel):
    source_claim_id: str = Field(pattern=CLAIM_ID_RE.pattern)
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence: tuple[RawEvidenceSpan, ...] = Field(min_length=1, max_length=1)


class IncomingDocumentBinding(_StrictFrozenModel):
    document_id: str = Field(pattern=LOGICAL_KEY_PATTERN)
    document_family: str = Field(pattern=LOGICAL_KEY_PATTERN)
    version_label: str = Field(pattern=LOGICAL_KEY_PATTERN)
    source_path: str
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    processed_path: str
    processed_sha256: str = Field(pattern=SHA256_PATTERN)
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority
    claim_bindings: tuple[IncomingClaimBinding, ...] = Field(
        min_length=1, max_length=MAX_INCOMING_CLAIMS
    )

    @field_validator("source_path")
    @classmethod
    def _source_path(cls, value: str) -> str:
        return _require_below(value, _RAW_PREFIX, "incoming source_path")

    @field_validator("processed_path")
    @classmethod
    def _processed_path(cls, value: str) -> str:
        return _require_below(value, _PROCESSED_PREFIX, "incoming processed_path")

    @model_validator(mode="after")
    def _coherent_binding(self) -> Self:
        if (
            self.declared_effective_to is not None
            and self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("declared_effective_to must follow declared_effective_from")
        ids = [binding.source_claim_id for binding in self.claim_bindings]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("incoming claim bindings must use unique canonical claim-ID order")
        spans = [
            (
                binding.evidence[0].start_char,
                binding.evidence[0].end_char,
                binding.evidence[0].start_byte,
                binding.evidence[0].end_byte,
            )
            for binding in self.claim_bindings
        ]
        if len(spans) != len(set(spans)):
            raise ValueError("each incoming claim must bind one unique atomic raw span")
        source = PurePosixPath(self.source_path)
        processed = PurePosixPath(self.processed_path)
        if source.stem != self.document_id or not processed.stem.endswith(self.document_id):
            raise ValueError("incoming document identity must match its source and note filenames")
        return self

    def document_version(self) -> DocumentVersionMetadata:
        return DocumentVersionMetadata.create(
            document_id=self.document_id,
            document_family=self.document_family,
            version_label=self.version_label,
            source_path=self.source_path,
            source_sha256=self.source_sha256,
            declared_effective_from=self.declared_effective_from,
            declared_effective_to=self.declared_effective_to,
            role=self.role,
            authority=self.authority,
        )


class IncomingEventManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: Literal["sl2-returns-policy-v2"]
    storyline: Literal["SL2"]
    arrived_on: date
    document: IncomingDocumentBinding

    @model_validator(mode="after")
    def _single_logical_event(self) -> Self:
        if self.arrived_on != self.document.declared_effective_from:
            raise ValueError("arrival and declared effective dates must match")
        if self.event_id == self.document.document_id:
            raise ValueError("incoming event and document identities must remain distinct")
        return self


class IncomingAlignmentAttestation(BaseModel):
    """Repository-reviewed alignment authority for this one fixed SL2 fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    attestation_id: Literal["sl2-returns-v2-alignment-v1"]
    alignment_policy_version: Literal["fixture-reviewed-extractive-alignment-v1"]
    claim_scope_policy_version: Literal["claim-scopes-v1"]
    payload_sha256: str = Field(pattern=SHA256_PATTERN)


class GroundedIncomingClaim(_StrictFrozenModel):
    """Classifier input preserving the exact extractive statement and raw span."""

    revision: VersionedClaimRevision
    processed_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    extractive_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_evidence: tuple[RawEvidenceSpan, ...] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _extractive_binding(self) -> Self:
        statement = normalize_semantic_text(self.raw_evidence[0].quote)
        if statement != self.revision.statement:
            raise ValueError("grounded revision must be the canonical exact raw quote")
        if self.extractive_statement_sha256 != _sha256(statement.encode("utf-8")):
            raise ValueError("extractive statement SHA does not bind the grounded revision")
        return self


@dataclass(frozen=True)
class VerifiedIncomingEvent:
    """Sealed snapshot capability; every public projection revalidates it."""

    _repo_root: Path = dataclass_field(repr=False)
    _manifest_path: Path = dataclass_field(repr=False)
    _manifest_snapshot: bytes = dataclass_field(repr=False)
    _manifest: IncomingEventManifest = dataclass_field(repr=False)
    _manifest_sha256: str = dataclass_field(repr=False)
    _attestation_path: Path = dataclass_field(repr=False)
    _attestation_snapshot: bytes = dataclass_field(repr=False)
    _attestation: IncomingAlignmentAttestation = dataclass_field(repr=False)
    _attestation_sha256: str = dataclass_field(repr=False)
    _source_path: Path = dataclass_field(repr=False)
    _source_snapshot: bytes = dataclass_field(repr=False)
    _processed_path: Path = dataclass_field(repr=False)
    _processed_snapshot: bytes = dataclass_field(repr=False)
    _source_note: SourceNote = dataclass_field(repr=False)
    _document: DocumentVersionMetadata = dataclass_field(repr=False)
    _grounded_claims: tuple[GroundedIncomingClaim, ...] = dataclass_field(repr=False)
    _event_identity: str = dataclass_field(repr=False)
    _verification_token: object = dataclass_field(repr=False, compare=False)
    _verification_seal: str = dataclass_field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verification_token is not _VERIFIED_TOKEN:
            raise TypeError("verified incoming events must be created by the loader")

    @property
    def manifest(self) -> IncomingEventManifest:
        _require_verified(self)
        return self._manifest

    @property
    def manifest_sha256(self) -> str:
        _require_verified(self)
        return self._manifest_sha256

    @property
    def alignment_attestation_id(self) -> str:
        _require_verified(self)
        return self._attestation.attestation_id

    @property
    def alignment_attestation_sha256(self) -> str:
        _require_verified(self)
        return self._attestation_sha256

    @property
    def alignment_policy_version(self) -> str:
        _require_verified(self)
        return self._attestation.alignment_policy_version

    @property
    def alignment_payload_sha256(self) -> str:
        _require_verified(self)
        return self._attestation.payload_sha256

    @property
    def claim_scope_policy_version(self) -> str:
        _require_verified(self)
        return self._attestation.claim_scope_policy_version

    @property
    def event_identity(self) -> str:
        _require_verified(self)
        return self._event_identity

    @property
    def document(self) -> DocumentVersionMetadata:
        _require_verified(self)
        return self._document

    @property
    def grounded_claims(self) -> tuple[GroundedIncomingClaim, ...]:
        """Evidence-bearing classifier/bootstrap interface for this event."""
        _require_verified(self)
        return self._grounded_claims

    @property
    def aggregate_claim_roots(self) -> tuple[VersionedClaimRevision, ...]:
        """Evidence-free aggregate identity roots derived from grounded claims."""
        _require_verified(self)
        return tuple(item.revision for item in self._grounded_claims)

    @property
    def claim_revisions(self) -> tuple[VersionedClaimRevision, ...]:
        """Compatibility alias for aggregate identity roots; use grounded_claims for analysis."""
        return self.aggregate_claim_roots

    @property
    def source_snapshot(self) -> bytes:
        _require_verified(self)
        return self._source_snapshot

    @property
    def processed_snapshot(self) -> bytes:
        _require_verified(self)
        return self._processed_snapshot

    @property
    def source_note(self) -> SourceNote:
        """Return the canonical parsed SourceNote from the sealed byte snapshot."""

        _require_verified(self)
        return self._source_note


def _load_yaml_mapping(snapshot: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IncomingBoundaryError(f"{label} is not safe UTF-8 YAML: {exc}") from exc
    _preflight_yaml_text(text, label=label)
    try:
        raw = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise IncomingBoundaryError(f"{label} is not safe UTF-8 YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise IncomingBoundaryError(f"{label} must be a YAML mapping")
    _scan_forbidden(raw)
    return raw


def _parse_manifest(snapshot: bytes) -> IncomingEventManifest:
    raw = _load_yaml_mapping(snapshot, label="incoming runtime manifest")
    try:
        return IncomingEventManifest.model_validate(raw)
    except ValidationError as exc:
        raise IncomingBoundaryError(f"incoming runtime manifest is invalid: {exc}") from exc


def _parse_alignment_attestation(snapshot: bytes) -> IncomingAlignmentAttestation:
    raw = _load_yaml_mapping(snapshot, label="incoming alignment attestation")
    try:
        attestation = IncomingAlignmentAttestation.model_validate(raw)
    except ValidationError as exc:
        raise IncomingBoundaryError(f"incoming alignment attestation is invalid: {exc}") from exc
    actual_sha256 = _sha256(snapshot)
    if actual_sha256 != PINNED_ALIGNMENT_ATTESTATION_SHA256:
        raise IncomingIntegrityError(
            "incoming alignment attestation file SHA-256 is not the code-pinned fixture"
        )
    return attestation


def _ensure_no_symlink_components(root: Path, target: Path, *, label: str) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise IncomingBoundaryError(f"{label} is outside the repository") from exc
    current = root
    paths = (
        root,
        *(
            root / PurePosixPath(*relative.parts[:index])
            for index in range(1, len(relative.parts) + 1)
        ),
    )
    for current in paths:
        try:
            info = current.lstat()
        except OSError as exc:
            raise IncomingIntegrityError(
                f"{label} disappeared while checking its path: {current}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise IncomingBoundaryError(f"{label} path contains a symlink: {current}")


def _require_exact_case_path(repo_root: Path, relative: str, *, label: str) -> Path:
    try:
        return require_exact_repository_path(
            repo_root=repo_root,
            relative=relative,
            label=label,
        )
    except RepositoryFileBoundaryError as exc:
        raise IncomingBoundaryError(str(exc)) from exc
    except RepositoryFileIntegrityError as exc:
        raise IncomingIntegrityError(
            f"{label} disappeared before verification: {relative}"
        ) from exc


def _read_regular(path: Path, *, limit: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise IncomingIntegrityError(f"{label} is unavailable: {path}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise IncomingIntegrityError(f"{label} is not a regular file: {path}")
    if before.st_size > limit:
        raise IncomingIntegrityError(f"{label} exceeds fixed {limit}-byte limit")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise IncomingIntegrityError(
            f"cannot open {label} without following links: {path}"
        ) from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            data = handle.read(limit + 1)
            first_finished = os.fstat(handle.fileno())
            handle.seek(0)
            confirmed = handle.read(limit + 1)
            finished = os.fstat(handle.fileno())
    except OSError as exc:
        raise IncomingIntegrityError(f"cannot read {label}: {path}") from exc
    if len(data) > limit or finished.st_size > limit:
        raise IncomingIntegrityError(f"{label} exceeds fixed {limit}-byte limit")
    try:
        after = path.lstat()
    except OSError as exc:
        raise IncomingIntegrityError(f"{label} disappeared after its verified read") from exc

    def signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    expected = signature(before)
    if (
        data != confirmed
        or not stat.S_ISREG(finished.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or signature(opened) != expected
        or signature(first_finished) != expected
        or signature(finished) != expected
        or signature(after) != expected
        or finished.st_size != len(data)
        or after.st_size != len(data)
    ):
        raise IncomingIntegrityError(f"{label} changed during its verified read")
    return data


def _resolve_runtime_file(
    *, repo_root: Path, relative: str, prefix: PurePosixPath, limit: int, label: str
) -> tuple[Path, bytes]:
    normalized = PurePosixPath(_require_below(relative, prefix, label))
    path = _require_exact_case_path(repo_root, normalized.as_posix(), label=label)
    _ensure_no_symlink_components(repo_root, path, label=label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise IncomingIntegrityError(f"{label} disappeared before verification: {path}") from exc
    if resolved != path or not resolved.is_relative_to(repo_root):
        raise IncomingBoundaryError(f"{label} does not resolve to its declared repository file")
    if "golden" in {part.casefold() for part in resolved.parts}:
        raise IncomingBoundaryError(f"{label} cannot resolve into evaluator gold")
    return resolved, _read_regular(resolved, limit=limit, label=label)


def _read_allowlisted_attestation(repo_root: Path) -> tuple[Path, bytes]:
    expected = _require_exact_case_path(
        repo_root,
        ALIGNMENT_ATTESTATION_RELATIVE_PATH,
        label="incoming alignment attestation",
    )
    _ensure_no_symlink_components(repo_root, expected, label="incoming alignment attestation")
    try:
        resolved = expected.resolve(strict=True)
    except OSError as exc:
        raise IncomingIntegrityError(
            "incoming alignment attestation disappeared before verification"
        ) from exc
    if resolved != expected or not resolved.is_relative_to(repo_root):
        raise IncomingBoundaryError(
            "incoming alignment attestation does not resolve to its exact allowlisted path"
        )
    if "golden" in {part.casefold() for part in resolved.parts}:
        raise IncomingBoundaryError("incoming alignment attestation cannot enter evaluator gold")
    return resolved, _read_regular(
        resolved,
        limit=MAX_ALIGNMENT_ATTESTATION_BYTES,
        label="incoming alignment attestation",
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _without_one_terminal_lf(value: str) -> str:
    """Canonical source comparison permits presence/absence of one terminal LF only."""
    return value[:-1] if value.endswith("\n") else value


def _parse_source_note(
    *,
    processed_path: Path,
    processed_snapshot: bytes,
    source_snapshot: bytes,
    document: IncomingDocumentBinding,
) -> SourceNote:
    try:
        source_text = source_snapshot.decode("utf-8")
        note_text = processed_snapshot.decode("utf-8")
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise IncomingIntegrityError(
            f"incoming source/note is not safe UTF-8 Markdown: {exc}"
        ) from exc
    yaml_text, _split_body, had_frontmatter = split_frontmatter(note_text)
    if not had_frontmatter:
        raise IncomingIntegrityError("incoming canonical SourceNote has no frontmatter")
    _preflight_yaml_text(yaml_text, label="incoming canonical SourceNote frontmatter")
    try:
        data, body = parse_frontmatter(note_text)
    except (ValueError, RecursionError) as exc:
        raise IncomingIntegrityError(
            f"incoming source/note is not safe UTF-8 Markdown: {exc}"
        ) from exc
    _scan_forbidden(data, path="$.processed_note")
    if data.get("type") != NoteType.SOURCE.value:
        raise IncomingIntegrityError("incoming canonical note is not a SourceNote")
    unknown_note_fields = sorted(set(data) - set(SourceNote.model_fields))
    if unknown_note_fields:
        raise IncomingIntegrityError(
            f"incoming canonical SourceNote has unknown fields: {unknown_note_fields}"
        )
    raw_claims = data.get("key_claims", [])
    if isinstance(raw_claims, list):
        for index, raw_claim in enumerate(raw_claims):
            if not isinstance(raw_claim, dict):
                continue
            unknown_claim_fields = sorted(set(raw_claim) - set(Claim.model_fields))
            if unknown_claim_fields:
                raise IncomingIntegrityError(
                    "incoming canonical SourceNote claim has unknown fields at "
                    f"index {index}: {unknown_claim_fields}"
                )
    if not data.get("title"):
        data = {**data, "title": extract_title(body, processed_path.stem)}
    try:
        note = SourceNote.model_validate(data)
    except ValidationError as exc:
        raise IncomingIntegrityError(f"incoming canonical SourceNote is invalid: {exc}") from exc
    if note.source_asset is not None or note.parsed_document is not None:
        raise IncomingIntegrityError(
            "incoming Markdown boundary does not accept PDF artefact fields"
        )
    if any(claim.evidence for claim in note.key_claims):
        raise IncomingIntegrityError("incoming Markdown claims must use manifest raw evidence only")
    for claim in note.key_claims:
        canonical_statement = normalize_semantic_text(claim.statement)
        if claim.statement != canonical_statement:
            raise IncomingIntegrityError(
                f"incoming processed claim statement is not canonical: {claim.id}"
            )
    if note.provenance != document.source_path:
        raise IncomingIntegrityError("incoming canonical note provenance does not name raw source")
    if note.provenance_hash != content_hash(source_text):
        raise IncomingIntegrityError(
            "incoming canonical note provenance hash does not match raw source"
        )

    marker = "\n## Content\n\n"
    before_content, found, rendered_content = body.partition(marker)
    if not found or _without_one_terminal_lf(rendered_content) != _without_one_terminal_lf(
        source_text
    ):
        raise IncomingIntegrityError(
            "incoming canonical note Content differs beyond one optional terminal LF"
        )
    _scan_answer_shaped_text(before_content, path="$.processed_note.body_preamble")

    claims = note.key_claims
    if not claims:
        raise IncomingIntegrityError("incoming canonical note must contain claims")
    if len(claims) > MAX_INCOMING_CLAIMS:
        raise IncomingIntegrityError(
            f"incoming canonical note exceeds fixed {MAX_INCOMING_CLAIMS}-claim limit"
        )
    id_counts = Counter(claim.id for claim in claims)
    duplicate_ids = sorted(claim_id for claim_id, count in id_counts.items() if count > 1)
    if duplicate_ids:
        raise IncomingIntegrityError(
            f"incoming canonical note has duplicate claim IDs: {duplicate_ids}"
        )
    statements = Counter(" ".join(claim.statement.split()).casefold() for claim in claims)
    if any(count > 1 for count in statements.values()):
        raise IncomingIntegrityError("incoming canonical note has duplicate claim statements")
    expected_prefix = f"{processed_path.stem}-"
    if any(not claim.id.startswith(expected_prefix) for claim in claims):
        raise IncomingIntegrityError("incoming claim IDs do not bind the canonical note identity")

    source_parts = PurePosixPath(document.source_path).relative_to(_RAW_PREFIX).parts
    processed_parts = PurePosixPath(document.processed_path).relative_to(_PROCESSED_PREFIX).parts
    if (
        len(source_parts) < 3
        or len(processed_parts) < 3
        or processed_parts[1] != "sources"
        or source_parts[0] != note.domain.value
        or processed_parts[0] != note.domain.value
        or source_parts[-2] != note.source_type.value
    ):
        raise IncomingIntegrityError("incoming note domain/type does not match declared paths")
    if note.source_type not in _ROLE_SOURCE_TYPES[document.role]:
        raise IncomingIntegrityError("incoming note source_type does not match document role")
    return SourceNote.model_validate(note.model_dump(mode="python"))


def _verify_raw_evidence(span: RawEvidenceSpan, source_snapshot: bytes) -> None:
    try:
        source_text = source_snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IncomingIntegrityError("incoming raw source is not UTF-8") from exc
    if (
        span.end_char > len(source_text)
        or source_text[span.start_char : span.end_char] != span.quote
        or span.end_byte > len(source_snapshot)
        or source_snapshot[span.start_byte : span.end_byte] != span.quote.encode("utf-8")
    ):
        raise IncomingIntegrityError("incoming claim raw evidence quote/span does not match source")
    if len(source_text[: span.start_char].encode("utf-8")) != span.start_byte:
        raise IncomingIntegrityError("incoming claim raw character and byte offsets disagree")
    prefix = source_text[: span.start_char]
    trimmed_prefix = prefix.rstrip()
    if prefix and not (prefix.endswith("\n\n") or (trimmed_prefix and trimmed_prefix[-1] in ".!?")):
        raise IncomingIntegrityError("incoming raw evidence does not start at a sentence boundary")
    suffix = source_text[span.end_char :]
    if suffix and not suffix[0].isspace():
        raise IncomingIntegrityError("incoming raw evidence does not end at a sentence boundary")


@dataclass(frozen=True)
class _DerivedClaimAlignment:
    source_claim_id: str
    processed_statement_sha256: str
    raw_evidence: RawEvidenceSpan
    extractive_statement: str
    extractive_statement_sha256: str
    affects: tuple[str, ...]
    scopes: tuple[str, ...]


def _derive_alignment_payload(
    *,
    attestation: IncomingAlignmentAttestation,
    manifest: IncomingEventManifest,
    manifest_sha256: str,
    note: SourceNote,
    source_snapshot: bytes,
    processed_snapshot: bytes,
) -> tuple[dict[str, Any], tuple[_DerivedClaimAlignment, ...]]:
    """Derive the fixed-fixture payload from verified inputs without trusting a copy."""

    claims_by_id = {claim.id: claim for claim in note.key_claims}
    bindings_by_id = {
        binding.source_claim_id: binding for binding in manifest.document.claim_bindings
    }
    if set(claims_by_id) != set(bindings_by_id):
        missing = sorted(set(claims_by_id) - set(bindings_by_id))
        extra = sorted(set(bindings_by_id) - set(claims_by_id))
        raise IncomingIntegrityError(
            f"incoming claim bindings do not exactly cover SourceNote claims; missing={missing}, extra={extra}"
        )

    derived: list[_DerivedClaimAlignment] = []
    for claim_id in sorted(claims_by_id):
        claim = claims_by_id[claim_id]
        binding = bindings_by_id[claim_id]
        processed_statement_sha256 = _sha256(claim.statement.encode("utf-8"))
        if processed_statement_sha256 != binding.statement_sha256:
            raise IncomingIntegrityError(
                f"incoming processed claim statement drifted from receipt: {claim_id}"
            )
        raw_evidence = binding.evidence[0]
        _verify_raw_evidence(raw_evidence, source_snapshot)
        extractive_statement = normalize_semantic_text(raw_evidence.quote)
        extractive_statement_sha256 = _sha256(extractive_statement.encode("utf-8"))
        affects = tuple(claim.affects)
        if len(affects) > MAX_AFFECTS_PER_CLAIM:
            raise IncomingIntegrityError(
                f"incoming claim exceeds fixed {MAX_AFFECTS_PER_CLAIM}-affects limit: {claim_id}"
            )
        if affects != tuple(sorted(set(affects))):
            raise IncomingIntegrityError(
                f"incoming claim affects must be canonical, ordered, and unique: {claim_id}"
            )
        if any(len(value.encode("utf-8")) > MAX_AFFECT_SLUG_BYTES for value in affects):
            raise IncomingIntegrityError(
                f"incoming claim affects exceeds fixed {MAX_AFFECT_SLUG_BYTES}-byte slug limit: {claim_id}"
            )
        try:
            scopes = claim_scopes_v1(
                document_family=manifest.document.document_family,
                affects=affects,
            )
        except (TypeError, ValueError) as exc:
            raise IncomingIntegrityError(
                f"incoming claim affects cannot produce canonical scopes: {claim_id}"
            ) from exc
        derived.append(
            _DerivedClaimAlignment(
                source_claim_id=claim_id,
                processed_statement_sha256=processed_statement_sha256,
                raw_evidence=raw_evidence,
                extractive_statement=extractive_statement,
                extractive_statement_sha256=extractive_statement_sha256,
                affects=affects,
                scopes=scopes,
            )
        )

    document = manifest.document
    payload = {
        "schema_version": 1,
        "attestation_id": attestation.attestation_id,
        "alignment_policy_version": attestation.alignment_policy_version,
        "claim_scope_policy_version": CLAIM_SCOPE_POLICY_VERSION,
        "manifest_sha256": manifest_sha256,
        "event": {
            "event_id": manifest.event_id,
            "storyline": manifest.storyline,
            "arrived_on": manifest.arrived_on.isoformat(),
        },
        "document": {
            "document_id": document.document_id,
            "document_family": document.document_family,
            "version_label": document.version_label,
            "source_path": document.source_path,
            "source_sha256": _sha256(source_snapshot),
            "processed_path": document.processed_path,
            "processed_sha256": _sha256(processed_snapshot),
            "declared_effective_from": document.declared_effective_from.isoformat(),
            "declared_effective_to": (
                document.declared_effective_to.isoformat()
                if document.declared_effective_to is not None
                else None
            ),
            "role": document.role.value,
            "authority": document.authority.value,
        },
        "claims": [
            {
                "source_claim_id": item.source_claim_id,
                "processed_statement_sha256": item.processed_statement_sha256,
                "raw_evidence": item.raw_evidence.model_dump(mode="json"),
                "extractive_statement_sha256": item.extractive_statement_sha256,
                "affects": list(item.affects),
                "derived_scopes": list(item.scopes),
            }
            for item in derived
        ],
    }
    return payload, tuple(derived)


def _verify_alignment_attestation(
    *,
    attestation: IncomingAlignmentAttestation,
    manifest: IncomingEventManifest,
    manifest_sha256: str,
    note: SourceNote,
    source_snapshot: bytes,
    processed_snapshot: bytes,
) -> tuple[_DerivedClaimAlignment, ...]:
    payload, derived = _derive_alignment_payload(
        attestation=attestation,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        note=note,
        source_snapshot=source_snapshot,
        processed_snapshot=processed_snapshot,
    )
    actual_payload_sha256 = _sha256(canonical_json_bytes(payload))
    if actual_payload_sha256 != attestation.payload_sha256:
        raise IncomingIntegrityError(
            "incoming manifest/raw/SourceNote alignment does not match the reviewed attestation"
        )
    return derived


def _grounded_claims(
    *, manifest: IncomingEventManifest, derived: tuple[_DerivedClaimAlignment, ...]
) -> tuple[GroundedIncomingClaim, ...]:
    """Construct public grounded claims only after alignment attestation verification."""

    document = manifest.document.document_version()
    source_note_path = (
        PurePosixPath(manifest.document.processed_path).relative_to(_PROCESSED_PREFIX).as_posix()
    )
    grounded = []
    for item in derived:
        revision = VersionedClaimRevision.create(
            document=document,
            source=ClaimSourceReference(
                source_note_path=source_note_path,
                source_note_sha256=manifest.document.processed_sha256,
                source_claim_id=item.source_claim_id,
                evidence=(),
            ),
            statement=item.extractive_statement,
            declared_effective_from=manifest.document.declared_effective_from,
            declared_effective_to=manifest.document.declared_effective_to,
            scopes=item.scopes,
        )
        grounded.append(
            GroundedIncomingClaim(
                revision=revision,
                processed_statement_sha256=item.processed_statement_sha256,
                extractive_statement_sha256=item.extractive_statement_sha256,
                raw_evidence=(item.raw_evidence,),
            )
        )
    registry = ClaimRevisionRegistry.create(tuple(item.revision for item in grounded))
    by_revision = {item.revision.claim_revision_id: item for item in grounded}
    return tuple(by_revision[item.claim_revision_id] for item in registry.revisions)


def _event_identity(
    *,
    manifest: IncomingEventManifest,
    manifest_sha256: str,
    attestation: IncomingAlignmentAttestation,
    attestation_sha256: str,
    document: DocumentVersionMetadata,
    grounded_claims: tuple[GroundedIncomingClaim, ...],
) -> str:
    payload = {
        "namespace": EVENT_NAMESPACE,
        "event_id": manifest.event_id,
        "manifest_sha256": manifest_sha256,
        "alignment_attestation": {
            "attestation_id": attestation.attestation_id,
            "attestation_file_sha256": attestation_sha256,
            "alignment_policy_version": attestation.alignment_policy_version,
            "claim_scope_policy_version": attestation.claim_scope_policy_version,
            "payload_sha256": attestation.payload_sha256,
        },
        "document_version_id": document.document_version_id,
        "source_sha256": manifest.document.source_sha256,
        "processed_sha256": manifest.document.processed_sha256,
        "grounded_claims": [
            {
                "claim_revision_id": item.revision.claim_revision_id,
                "extractive_statement": item.revision.statement,
                "extractive_statement_sha256": item.extractive_statement_sha256,
                "processed_statement_sha256": item.processed_statement_sha256,
                "scopes": list(item.revision.scopes),
                "raw_evidence": [span.model_dump(mode="json") for span in item.raw_evidence],
            }
            for item in grounded_claims
        ],
    }
    return f"incoming:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def _seal_payload(context: VerifiedIncomingEvent) -> bytes:
    payload = {
        "repo_root": context._repo_root.as_posix(),
        "manifest_path": context._manifest_path.as_posix(),
        "manifest_sha256": context._manifest_sha256,
        "manifest": context._manifest.model_dump(mode="json"),
        "attestation_path": context._attestation_path.as_posix(),
        "attestation_file_sha256": context._attestation_sha256,
        "attestation": context._attestation.model_dump(mode="json"),
        "source_path": context._source_path.as_posix(),
        "source_sha256": _sha256(context._source_snapshot),
        "processed_path": context._processed_path.as_posix(),
        "processed_sha256": _sha256(context._processed_snapshot),
        "source_note": context._source_note.model_dump(mode="json"),
        "document": context._document.model_dump(mode="json"),
        "grounded_claims": [item.model_dump(mode="json") for item in context._grounded_claims],
        "event_identity": context._event_identity,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _seal(context: VerifiedIncomingEvent) -> str:
    return hmac.new(_SEAL_SECRET, _seal_payload(context), hashlib.sha256).hexdigest()


def _require_verified(context: VerifiedIncomingEvent) -> None:
    if context._verification_token is not _VERIFIED_TOKEN:
        raise IncomingIntegrityError("incoming capability was not created by the verified loader")
    if _sha256(context._manifest_snapshot) != context._manifest_sha256:
        raise IncomingIntegrityError("incoming manifest snapshot was altered in memory")
    manifest = _parse_manifest(context._manifest_snapshot)
    if manifest != context._manifest:
        raise IncomingIntegrityError("incoming parsed manifest was altered in memory")
    if _sha256(context._attestation_snapshot) != context._attestation_sha256:
        raise IncomingIntegrityError(
            "incoming alignment attestation snapshot was altered in memory"
        )
    attestation = _parse_alignment_attestation(context._attestation_snapshot)
    if attestation != context._attestation:
        raise IncomingIntegrityError("incoming parsed alignment attestation was altered in memory")
    expected_attestation_path = context._repo_root.joinpath(
        *PurePosixPath(ALIGNMENT_ATTESTATION_RELATIVE_PATH).parts
    )
    if context._attestation_path != expected_attestation_path:
        raise IncomingIntegrityError("incoming alignment attestation path binding was altered")
    if _sha256(context._source_snapshot) != manifest.document.source_sha256:
        raise IncomingIntegrityError("incoming raw-source snapshot was altered in memory")
    if _sha256(context._processed_snapshot) != manifest.document.processed_sha256:
        raise IncomingIntegrityError("incoming canonical-note snapshot was altered in memory")
    note = _parse_source_note(
        processed_path=context._processed_path,
        processed_snapshot=context._processed_snapshot,
        source_snapshot=context._source_snapshot,
        document=manifest.document,
    )
    derived = _verify_alignment_attestation(
        attestation=attestation,
        manifest=manifest,
        note=note,
        manifest_sha256=context._manifest_sha256,
        source_snapshot=context._source_snapshot,
        processed_snapshot=context._processed_snapshot,
    )
    grounded = _grounded_claims(
        manifest=manifest,
        derived=derived,
    )
    document = manifest.document.document_version()
    identity = _event_identity(
        manifest=manifest,
        manifest_sha256=context._manifest_sha256,
        attestation=attestation,
        attestation_sha256=context._attestation_sha256,
        document=document,
        grounded_claims=grounded,
    )
    if (
        note != context._source_note
        or document != context._document
        or grounded != context._grounded_claims
        or identity != context._event_identity
    ):
        raise IncomingIntegrityError("incoming capability projections were altered in memory")
    if not hmac.compare_digest(context._verification_seal, _seal(context)):
        raise IncomingIntegrityError("incoming capability seal was altered")


def load_verified_incoming_event(*, repo_root: Path, manifest_path: Path) -> VerifiedIncomingEvent:
    """Capture and verify the one-document SL2 incoming-event receipt."""
    if repo_root.is_symlink():
        raise IncomingBoundaryError("incoming repository root cannot be a symlink")
    try:
        resolved_root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise IncomingIntegrityError(f"repository root is unavailable: {repo_root}") from exc
    if not resolved_root.is_dir():
        raise IncomingIntegrityError(f"repository root is not a directory: {resolved_root}")

    if "golden" in {part.casefold() for part in manifest_path.parts}:
        raise IncomingBoundaryError("incoming loader cannot read an evaluator-gold manifest")
    expected_manifest = _require_exact_case_path(
        resolved_root,
        MANIFEST_RELATIVE_PATH,
        label="incoming manifest",
    )
    provided_manifest = Path(os.path.abspath(manifest_path))
    if provided_manifest != expected_manifest:
        raise IncomingBoundaryError(
            f"incoming manifest must be the allowlisted {MANIFEST_RELATIVE_PATH}"
        )
    _ensure_no_symlink_components(resolved_root, expected_manifest, label="incoming manifest")
    manifest_snapshot = _read_regular(
        expected_manifest, limit=MAX_MANIFEST_BYTES, label="incoming manifest"
    )
    manifest = _parse_manifest(manifest_snapshot)
    manifest_sha256 = _sha256(manifest_snapshot)

    attestation_path, attestation_snapshot = _read_allowlisted_attestation(resolved_root)
    attestation = _parse_alignment_attestation(attestation_snapshot)
    attestation_sha256 = _sha256(attestation_snapshot)

    source_path, source_snapshot = _resolve_runtime_file(
        repo_root=resolved_root,
        relative=manifest.document.source_path,
        prefix=_RAW_PREFIX,
        limit=MAX_SOURCE_BYTES,
        label="incoming raw source",
    )
    processed_path, processed_snapshot = _resolve_runtime_file(
        repo_root=resolved_root,
        relative=manifest.document.processed_path,
        prefix=_PROCESSED_PREFIX,
        limit=MAX_PROCESSED_NOTE_BYTES,
        label="incoming canonical note",
    )
    if _sha256(source_snapshot) != manifest.document.source_sha256:
        raise IncomingIntegrityError("incoming raw source SHA-256 does not match manifest")
    if _sha256(processed_snapshot) != manifest.document.processed_sha256:
        raise IncomingIntegrityError("incoming canonical note SHA-256 does not match manifest")

    note = _parse_source_note(
        processed_path=processed_path,
        processed_snapshot=processed_snapshot,
        source_snapshot=source_snapshot,
        document=manifest.document,
    )
    document = manifest.document.document_version()
    derived = _verify_alignment_attestation(
        attestation=attestation,
        manifest=manifest,
        note=note,
        manifest_sha256=manifest_sha256,
        source_snapshot=source_snapshot,
        processed_snapshot=processed_snapshot,
    )
    grounded = _grounded_claims(
        manifest=manifest,
        derived=derived,
    )
    identity = _event_identity(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        attestation=attestation,
        attestation_sha256=attestation_sha256,
        document=document,
        grounded_claims=grounded,
    )
    context = VerifiedIncomingEvent(
        _repo_root=resolved_root,
        _manifest_path=expected_manifest,
        _manifest_snapshot=manifest_snapshot,
        _manifest=manifest,
        _manifest_sha256=manifest_sha256,
        _attestation_path=attestation_path,
        _attestation_snapshot=attestation_snapshot,
        _attestation=attestation,
        _attestation_sha256=attestation_sha256,
        _source_path=source_path,
        _source_snapshot=source_snapshot,
        _processed_path=processed_path,
        _processed_snapshot=processed_snapshot,
        _source_note=note,
        _document=document,
        _grounded_claims=grounded,
        _event_identity=identity,
        _verification_token=_VERIFIED_TOKEN,
        _verification_seal="",
    )
    object.__setattr__(context, "_verification_seal", _seal(context))
    _require_verified(context)
    return context
