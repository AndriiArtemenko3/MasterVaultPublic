"""Generic V2 admission and exact-grounding boundary for incoming Markdown.

This module deliberately does not extend or parameterize the sealed SL2 v1 loader.
Provider output remains an untrusted suggestion until every quote is resolved against
the process-local admitted byte snapshot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, SupportsIndex

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from mastervault.change_control.models import (
    DocumentAuthority,
    DocumentRole,
    canonical_json_bytes,
)
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2
from mastervault.models import Confidence, Domain, SourceNote, SourceType, content_hash
from mastervault.prompts.untrusted import fence

MAX_GENERIC_SOURCE_BYTES_V2: Final = 64 * 1024
MAX_GENERIC_EVIDENCE_BYTES_V2: Final = 512
MAX_GENERIC_CLAIMS_V2: Final = 10
MAX_AFFECTS_V2: Final = 16
_ID_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_AFFECT_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)
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
_FORBIDDEN_VALUE_PHRASES = {
    "evaluator answer",
    "expected after",
    "expected impacts",
    "expected pair classifications",
    "expected patch",
    "expected patches",
    "expected review decision",
    "golden answer",
    "grounding document id",
    "grounding quote",
}
_SECRET_ASSIGNMENT = re.compile(r"(?i)(?:api[_ -]?key|bearer|password|secret|token)\s*[:=]\s*\S+")
_ABSOLUTE_PATH_FRAGMENT = re.compile(r"(?:^|\s)(?:/[^\s]+|[A-Za-z]:[\\/][^\s]+)")
_ROLE_SOURCE_TYPES: Final = {
    DocumentRole.POLICY: {SourceType.POLICY},
    DocumentRole.MEMO: {SourceType.MEMO},
    DocumentRole.FAQ: {SourceType.FAQ},
    DocumentRole.SOP: {SourceType.SOP},
    DocumentRole.PROCESS: {SourceType.PROCESS},
    DocumentRole.PROPOSAL: {SourceType.PROPOSAL},
    DocumentRole.OTHER: {SourceType.OTHER},
}


class GenericIncomingBoundaryError(ValueError):
    """Untrusted input violates the generic admission boundary."""


class GenericIncomingIntegrityError(ValueError):
    """An admitted path, byte snapshot, quote, or binding changed or disagreed."""


class GenericExtractionModeV2(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenericChangeMetadataV2(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: str
    document_id: str
    document_family: str
    version_label: str
    title: str = Field(min_length=1, max_length=512)
    domain: Domain
    source_type: SourceType
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority
    operator_intent: str = Field(min_length=1, max_length=2048)

    @field_validator("event_id", "document_id", "document_family", "version_label")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        if _ID_PATTERN.fullmatch(value) is None:
            raise ValueError("must be a canonical lowercase dot/kebab identifier")
        return value

    @field_validator("title", "operator_intent")
    @classmethod
    def _normalized_text(cls, value: str) -> str:
        if value != unicodedata.normalize("NFKC", value) or value != " ".join(value.split()):
            raise ValueError("must be NFKC text with canonical whitespace")
        _reject_answer_leakage(value, label="change metadata")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> GenericChangeMetadataV2:
        if self.event_id == self.document_id:
            raise ValueError("event_id and document_id must remain distinct")
        if self.declared_effective_to is not None and (
            self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("declared_effective_to must follow declared_effective_from")
        if self.source_type not in _ROLE_SOURCE_TYPES[self.role]:
            raise ValueError("source_type does not match role")
        return self


class GenericEvidenceSpanV2(_StrictFrozenModel):
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)

    @model_validator(mode="after")
    def _exact(self) -> GenericEvidenceSpanV2:
        encoded = self.quote.encode("utf-8")
        if len(encoded) > MAX_GENERIC_EVIDENCE_BYTES_V2:
            raise ValueError("evidence exceeds the 512-byte limit")
        if self.quote != self.quote.strip() or "\n" in self.quote:
            raise ValueError("evidence must be one atomic sentence")
        if self.quote[-1] not in ".!?" or len(re.findall(r"[.!?]", self.quote)) != 1:
            raise ValueError("evidence must contain exactly one complete sentence")
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("character span does not exactly bind quote")
        if self.end_byte - self.start_byte != len(encoded):
            raise ValueError("UTF-8 byte span does not exactly bind quote")
        return self


class GenericGroundedClaimV2(_StrictFrozenModel):
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*-[0-9]{2}$")
    statement: str
    confidence: Confidence
    affects: tuple[str, ...] = Field(max_length=MAX_AFFECTS_V2)
    evidence: GenericEvidenceSpanV2

    @model_validator(mode="after")
    def _exact(self) -> GenericGroundedClaimV2:
        if self.statement != self.evidence.quote:
            raise ValueError("grounded statement must equal the exact quotation")
        if tuple(sorted(set(self.affects))) != self.affects:
            raise ValueError("affects must be unique canonical order")
        if any(_AFFECT_PATTERN.fullmatch(value) is None for value in self.affects):
            raise ValueError("affects must be bare lowercase kebab-case slugs")
        return self


class GenericGroundedExtractionV2(_StrictFrozenModel):
    schema_version: Literal[2] = 2
    mode: GenericExtractionModeV2
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_contract: GenericGroundedClaimExtractionV2
    claims: tuple[GenericGroundedClaimV2, ...] = Field(
        min_length=1, max_length=MAX_GENERIC_CLAIMS_V2
    )


@dataclass(frozen=True, eq=False)
class VerifiedGenericIncomingV2:
    """Process-local proof over one exact external source inode and byte snapshot."""

    metadata: GenericChangeMetadataV2
    source_sha256: str
    source_byte_count: int
    source_name: str
    _source_path: Path = field(repr=False)
    _source_snapshot: bytes = field(repr=False)
    _source_signature: tuple[int, int, int, int, int, int, int, int] = field(repr=False)
    _token: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("generic incoming capabilities are loader-created only")

    @property
    def source_snapshot(self) -> bytes:
        _verify_capability(self)
        return self._source_snapshot

    @property
    def source_text(self) -> str:
        return self.source_snapshot.decode("utf-8")

    def verify_current_path(self) -> None:
        _verify_capability(self)
        try:
            current = self._source_path.lstat()
        except OSError as exc:
            raise GenericIncomingIntegrityError("admitted source path is unavailable") from exc
        if _stat_signature(current) != self._source_signature:
            raise GenericIncomingIntegrityError("admitted source path was substituted or changed")

    def __reduce__(self) -> Any:
        raise TypeError("generic incoming capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("generic incoming capabilities are process-local")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
        info.st_uid,
    )


def _reject_answer_leakage(value: str, *, label: str) -> None:
    words = " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value).casefold()))
    for needle in sorted(_FORBIDDEN_VALUE_PHRASES):
        if re.search(rf"(?:^| ){re.escape(needle)}(?: |$)", words):
            raise GenericIncomingBoundaryError(f"{label} contains forbidden answer-shaped text")
    if _SECRET_ASSIGNMENT.search(value) or "-----BEGIN PRIVATE KEY-----" in value:
        raise GenericIncomingBoundaryError(f"{label} contains secret-shaped material")
    if _ABSOLUTE_PATH_FRAGMENT.search(value):
        raise GenericIncomingBoundaryError(f"{label} contains an absolute path")


def _preflight_yaml(value: str) -> None:
    try:
        tokens = tuple(yaml.scan(value))
        root = yaml.compose(value, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise GenericIncomingBoundaryError("frontmatter is not safe YAML") from exc
    if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in tokens):
        raise GenericIncomingBoundaryError("frontmatter cannot contain aliases, anchors, or tags")
    nodes = 0

    def visit(node: Node, path: str, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 256 or depth > 8:
            raise GenericIncomingBoundaryError("frontmatter exceeds safe structural limits")
        if isinstance(node, MappingNode):
            seen: set[str] = set()
            for key, child in node.value:
                if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                    raise GenericIncomingBoundaryError("frontmatter keys must be plain strings")
                if key.value in seen:
                    raise GenericIncomingBoundaryError(f"duplicate YAML key at {path}.{key.value}")
                seen.add(key.value)
                visit(child, f"{path}.{key.value}", depth + 1)
        elif isinstance(node, SequenceNode):
            for index, child in enumerate(node.value):
                visit(child, f"{path}[{index}]", depth + 1)

    if root is not None:
        visit(root, "$", 0)


def _split_strict_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise GenericIncomingBoundaryError("incoming Markdown requires YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise GenericIncomingBoundaryError("incoming Markdown has unterminated frontmatter")
    yaml_text, body = text[4:end], text[end + 5 :]
    if not body.strip():
        raise GenericIncomingBoundaryError("incoming Markdown body cannot be empty")
    return yaml_text, body


def _parse_metadata(snapshot: bytes) -> GenericChangeMetadataV2:
    try:
        text = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenericIncomingBoundaryError("incoming Markdown must be UTF-8") from exc
    yaml_text, body = _split_strict_frontmatter(text)
    _preflight_yaml(yaml_text)
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise GenericIncomingBoundaryError("frontmatter is not safe YAML") from exc
    if not isinstance(raw, dict) or set(raw) != {"mastervault_change"}:
        raise GenericIncomingBoundaryError(
            "frontmatter requires exactly one top-level mastervault_change mapping"
        )
    change = raw["mastervault_change"]
    if not isinstance(change, dict):
        raise GenericIncomingBoundaryError("mastervault_change must be a mapping")
    for key, value in change.items():
        normalized = str(key).casefold().replace("-", "_")
        if normalized.startswith("expected_") or normalized in _FORBIDDEN_KEYS:
            raise GenericIncomingBoundaryError("frontmatter contains forbidden answer authority")
        if isinstance(value, str) and key not in {"authority", "source_type", "role"}:
            _reject_answer_leakage(value, label=str(key))
    try:
        json_ready = {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in change.items()
        }
        return GenericChangeMetadataV2.model_validate_json(canonical_json_bytes(json_ready))
    except ValidationError as exc:
        raise GenericIncomingBoundaryError(f"mastervault_change is invalid: {exc}") from exc


def _read_external_regular(
    path: Path,
) -> tuple[Path, bytes, tuple[int, int, int, int, int, int, int, int]]:
    if path.is_symlink():
        raise GenericIncomingBoundaryError("incoming source cannot be a symlink")
    try:
        requested = path.absolute()
        if any(part in {"", ".", ".."} for part in requested.parts[1:]):
            raise GenericIncomingBoundaryError("incoming source path is not canonical")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent_fd = os.open(requested.anchor, directory_flags)
        component_signatures: list[tuple[Path, tuple[int, int, int, int, int, int, int, int]]] = []
        current_path = Path(requested.anchor)
        try:
            for part in requested.parts[1:-1]:
                names = os.listdir(parent_fd)
                if part not in names:
                    if any(name.casefold() == part.casefold() for name in names):
                        raise GenericIncomingBoundaryError(
                            "incoming source path does not use exact filesystem case"
                        )
                    raise GenericIncomingIntegrityError(
                        "incoming source path component is unavailable"
                    )
                child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
                named_component = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                os.close(parent_fd)
                parent_fd = child_fd
                current_path /= part
                component = os.fstat(parent_fd)
                if not stat.S_ISDIR(component.st_mode) or (
                    component.st_dev,
                    component.st_ino,
                ) != (
                    named_component.st_dev,
                    named_component.st_ino,
                ):
                    raise GenericIncomingBoundaryError(
                        "incoming source path contains a non-directory or substituted component"
                    )
                component_signatures.append((current_path, _stat_signature(component)))
            names = os.listdir(parent_fd)
            if requested.name not in names:
                if any(name.casefold() == requested.name.casefold() for name in names):
                    raise GenericIncomingBoundaryError(
                        "incoming source filename does not use exact filesystem case"
                    )
                raise GenericIncomingIntegrityError("incoming source is unavailable")
            before = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        except Exception:
            os.close(parent_fd)
            raise
    except OSError as exc:
        raise GenericIncomingIntegrityError("incoming source is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.getuid()
        or before.st_mode & 0o022
    ):
        os.close(parent_fd)
        raise GenericIncomingBoundaryError(
            "incoming source must be one owner-controlled, non-writable-linked regular file"
        )
    if before.st_size > MAX_GENERIC_SOURCE_BYTES_V2:
        os.close(parent_fd)
        raise GenericIncomingBoundaryError("incoming source exceeds fixed 65536-byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(requested.name, flags, dir_fd=parent_fd)
    except OSError as exc:
        os.close(parent_fd)
        raise GenericIncomingIntegrityError("cannot descriptor-open incoming source") from exc
    try:
        try:
            opened = os.fstat(fd)
            data = os.pread(fd, MAX_GENERIC_SOURCE_BYTES_V2 + 1, 0)
            confirmed = os.pread(fd, MAX_GENERIC_SOURCE_BYTES_V2 + 1, 0)
            finished = os.fstat(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        os.close(parent_fd)
        raise GenericIncomingIntegrityError("cannot read incoming source") from exc
    try:
        after = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        for component_path, component_signature in component_signatures:
            if _stat_signature(component_path.lstat()) != component_signature:
                raise GenericIncomingIntegrityError(
                    "incoming source parent directory was substituted during read"
                )
    except OSError as exc:
        raise GenericIncomingIntegrityError("incoming source disappeared after read") from exc
    finally:
        os.close(parent_fd)
    expected = _stat_signature(before)
    if (
        data != confirmed
        or len(data) > MAX_GENERIC_SOURCE_BYTES_V2
        or len(data) != before.st_size
        or any(_stat_signature(item) != expected for item in (opened, finished, after))
    ):
        raise GenericIncomingIntegrityError("incoming source changed during verified read")
    return requested, data, expected


def _capability_seal(
    metadata: GenericChangeMetadataV2, digest: str, signature: tuple[int, ...]
) -> str:
    payload = canonical_json_bytes(
        {
            "metadata": metadata.model_dump(mode="json"),
            "source_sha256": digest,
            "signature": signature,
        }
    )
    return hmac.new(_CAPABILITY_SECRET, payload, hashlib.sha256).hexdigest()


def _verify_capability(admission: VerifiedGenericIncomingV2) -> None:
    expected = _capability_seal(
        admission.metadata, admission.source_sha256, admission._source_signature
    )
    if not hmac.compare_digest(admission._seal, expected):
        raise GenericIncomingIntegrityError("generic incoming capability authentication failed")
    if _sha256(admission._source_snapshot) != admission.source_sha256:
        raise GenericIncomingIntegrityError("admitted source bytes no longer match capability")


def admit_generic_incoming_markdown_v2(
    source_path: Path, *, active_workspace: Path
) -> VerifiedGenericIncomingV2:
    """Admit one stable external Markdown file without invoking any provider or effect."""

    path, snapshot, signature = _read_external_regular(Path(source_path))
    try:
        workspace = Path(active_workspace).resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise GenericIncomingIntegrityError("source or active workspace disappeared") from exc
    if resolved_path.is_relative_to(workspace):
        raise GenericIncomingBoundaryError("incoming source must be outside the active workspace")
    if path.suffix != ".md":
        raise GenericIncomingBoundaryError("incoming source filename must end in .md")
    metadata = _parse_metadata(snapshot)
    if path.stem != metadata.document_id:
        raise GenericIncomingBoundaryError("incoming filename must exactly match document_id")
    digest = _sha256(snapshot)
    return VerifiedGenericIncomingV2(
        metadata=metadata,
        source_sha256=digest,
        source_byte_count=len(snapshot),
        source_name=path.name,
        _source_path=path,
        _source_snapshot=snapshot,
        _source_signature=signature,
        _token=_CAPABILITY_TOKEN,
        _seal=_capability_seal(metadata, digest, signature),
    )


def extraction_request_sha256_v2(admission: VerifiedGenericIncomingV2) -> str:
    _verify_capability(admission)
    return _sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.generic-grounded-extraction-request.v2",
                "source_sha256": admission.source_sha256,
                "document_id": admission.metadata.document_id,
                "contract_id": "generic_grounded_claim_extraction_v2",
            }
        )
    )


def generic_extraction_prompt_variables_v2(
    admission: VerifiedGenericIncomingV2,
) -> dict[str, str]:
    """Return only the admitted document in an injection-resistant data fence."""

    _verify_capability(admission)
    return {"document": fence(admission.source_text, "GENERIC INCOMING MARKDOWN")}


def _resolve_quote(text: str, body: str, quote: str, *, body_start: int) -> GenericEvidenceSpanV2:
    starts = [body_start + match.start() for match in re.finditer(re.escape(quote), body)]
    if len(starts) != 1:
        raise GenericIncomingIntegrityError("quotation must resolve exactly once in admitted bytes")
    if len(quote) < 8:
        raise GenericIncomingIntegrityError("quotation is shorter than the SourceNote claim limit")
    start = starts[0]
    end = start + len(quote)
    prefix = text[:start]
    suffix = text[end:]
    trimmed = prefix.rstrip()
    if (
        start != body_start
        and prefix
        and not (prefix.endswith("\n\n") or (trimmed and trimmed[-1] in ".!?"))
    ):
        raise GenericIncomingIntegrityError("quotation does not start at a sentence boundary")
    if suffix and not suffix[0].isspace():
        raise GenericIncomingIntegrityError("quotation does not end at a sentence boundary")
    return GenericEvidenceSpanV2(
        quote=quote,
        start_char=start,
        end_char=end,
        start_byte=len(text[:start].encode("utf-8")),
        end_byte=len(text[:end].encode("utf-8")),
    )


def ground_generic_extraction_v2(
    admission: VerifiedGenericIncomingV2,
    provider_result: GenericGroundedClaimExtractionV2 | dict[str, Any] | bytes,
    *,
    mode: GenericExtractionModeV2 = GenericExtractionModeV2.LIVE,
    replay_of: GenericGroundedExtractionV2 | None = None,
) -> GenericGroundedExtractionV2:
    """Resolve an exact LIVE/REPLAY provider representation against admitted bytes."""

    _verify_capability(admission)
    admission.verify_current_path()
    if isinstance(provider_result, bytes):
        try:
            parsed_json = json.loads(provider_result.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenericIncomingBoundaryError("provider representation is not UTF-8 JSON") from exc
    elif isinstance(provider_result, BaseModel):
        parsed_json = provider_result.model_dump(mode="json")
    else:
        parsed_json = provider_result
    try:
        proposed = GenericGroundedClaimExtractionV2.model_validate_json(
            canonical_json_bytes(parsed_json)
        )
    except ValidationError as exc:
        raise GenericIncomingBoundaryError(f"provider extraction is invalid: {exc}") from exc
    provider_digest = _sha256(canonical_json_bytes(proposed.model_dump(mode="json")))
    if mode is GenericExtractionModeV2.REPLAY:
        if replay_of is None or replay_of.mode is not GenericExtractionModeV2.LIVE:
            raise GenericIncomingIntegrityError("REPLAY requires one exact prior LIVE extraction")
        if (
            replay_of.source_sha256 != admission.source_sha256
            or replay_of.request_sha256 != extraction_request_sha256_v2(admission)
            or replay_of.provider_result_sha256 != provider_digest
        ):
            raise GenericIncomingIntegrityError(
                "REPLAY differs from its exact content-bound LIVE input"
            )
    elif replay_of is not None:
        raise GenericIncomingBoundaryError("LIVE extraction cannot specify replay authority")
    source_text = admission.source_text
    _yaml_text, body = _split_strict_frontmatter(source_text)
    body_start = len(source_text) - len(body)
    claims: list[tuple[GenericEvidenceSpanV2, Confidence, tuple[str, ...]]] = []
    for candidate in proposed.claims:
        span = _resolve_quote(source_text, body, candidate.quote, body_start=body_start)
        affects = tuple(candidate.affects)
        if tuple(sorted(set(affects))) != affects or any(
            _AFFECT_PATTERN.fullmatch(value) is None for value in affects
        ):
            raise GenericIncomingBoundaryError("affects must be unique canonical kebab-case order")
        claims.append((span, candidate.confidence, affects))
    claims.sort(key=lambda item: (item[0].start_byte, item[0].end_byte, item[0].quote))
    spans = [(item[0].start_byte, item[0].end_byte) for item in claims]
    if len(spans) != len(set(spans)):
        raise GenericIncomingIntegrityError("each claim requires a distinct source quotation")
    claim_prefix = admission.metadata.document_id.replace(".", "-")
    grounded = tuple(
        GenericGroundedClaimV2(
            claim_id=f"{claim_prefix}-{index:02d}",
            statement=span.quote,
            confidence=confidence,
            affects=affects,
            evidence=span,
        )
        for index, (span, confidence, affects) in enumerate(claims, start=1)
    )
    return GenericGroundedExtractionV2(
        mode=mode,
        source_sha256=admission.source_sha256,
        request_sha256=extraction_request_sha256_v2(admission),
        provider_result_sha256=provider_digest,
        provider_contract=proposed,
        claims=grounded,
    )


def render_verified_generic_source_note_projection_v2(
    *,
    metadata: GenericChangeMetadataV2,
    source_sha256: str,
    source_snapshot: bytes,
    claims: tuple[GenericGroundedClaimV2, ...],
) -> bytes:
    """Purely reproduce the canonical SourceNote from already-verified evidence."""

    if hashlib.sha256(source_snapshot).hexdigest() != source_sha256:
        raise GenericIncomingIntegrityError("raw source bytes differ from their verified SHA")
    try:
        source_text = source_snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenericIncomingIntegrityError("verified raw source is not UTF-8") from exc
    if not claims:
        raise GenericIncomingIntegrityError("generic SourceNote requires grounded claims")
    verify_generic_grounded_claim_projection_v2(
        metadata=metadata,
        source_snapshot=source_snapshot,
        claims=claims,
    )
    frontmatter = {
        "domain": metadata.domain.value,
        "type": "source",
        "title": metadata.title,
        "tags": [],
        "status": "processed",
        "created": metadata.declared_effective_from.isoformat(),
        "updated": metadata.declared_effective_from.isoformat(),
        "source_type": metadata.source_type.value,
        "key_claims": [
            {
                "id": claim.claim_id,
                "statement": claim.statement,
                "confidence": claim.confidence.value,
                "affects": list(claim.affects),
                "evidence": [],
            }
            for claim in claims
        ],
        "provenance": f"generic-incoming/v2/sources/{source_sha256}.md",
        "provenance_hash": content_hash(source_text),
    }
    try:
        SourceNote.model_validate(frontmatter)
    except ValidationError as exc:
        raise GenericIncomingIntegrityError(
            "canonical generic SourceNote projection is invalid"
        ) from exc
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
    _yaml_text, body = _split_strict_frontmatter(source_text)
    if not body.endswith("\n"):
        body += "\n"
    return f"---\n{yaml_text}\n---\n\n## Content\n\n{body}".encode()


def verify_generic_grounded_claim_projection_v2(
    *,
    metadata: GenericChangeMetadataV2,
    source_snapshot: bytes,
    claims: tuple[GenericGroundedClaimV2, ...],
) -> None:
    """Revalidate exact character/UTF-8 spans and deterministic grounded identities."""

    try:
        source_text = source_snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenericIncomingIntegrityError("verified raw source is not UTF-8") from exc
    _yaml_text, body = _split_strict_frontmatter(source_text)
    body_start = len(source_text) - len(body)
    if not claims or len(claims) > MAX_GENERIC_CLAIMS_V2:
        raise GenericIncomingIntegrityError("grounded claim projection has invalid coverage")
    ordered = tuple(
        sorted(
            claims,
            key=lambda item: (
                item.evidence.start_byte,
                item.evidence.end_byte,
                item.evidence.quote,
            ),
        )
    )
    if claims != ordered or len(
        {(item.evidence.start_byte, item.evidence.end_byte) for item in claims}
    ) != len(claims):
        raise GenericIncomingIntegrityError(
            "grounded claims must preserve unique canonical source order"
        )
    prefix = metadata.document_id.replace(".", "-")
    for index, claim in enumerate(claims, start=1):
        span = claim.evidence
        if claim.claim_id != f"{prefix}-{index:02d}":
            raise GenericIncomingIntegrityError(
                "grounded claim ID differs from deterministic source order"
            )
        if span.start_char < body_start or span.end_char > len(source_text):
            raise GenericIncomingIntegrityError("grounded claim lies outside the raw body")
        if source_text[span.start_char : span.end_char] != span.quote:
            raise GenericIncomingIntegrityError(
                "grounded claim quote differs from exact character span"
            )
        encoded_prefix = source_text[: span.start_char].encode("utf-8")
        encoded_span = source_text[: span.end_char].encode("utf-8")
        if len(encoded_prefix) != span.start_byte or len(encoded_span) != span.end_byte:
            raise GenericIncomingIntegrityError(
                "grounded claim UTF-8 offsets differ from exact raw bytes"
            )
        expected = _resolve_quote(source_text, body, span.quote, body_start=body_start)
        if expected != span:
            raise GenericIncomingIntegrityError(
                "grounded claim span differs from unique sentence-bound evidence"
            )


def render_generic_source_note_v2(
    admission: VerifiedGenericIncomingV2, extraction: GenericGroundedExtractionV2
) -> bytes:
    """Render deterministic canonical Markdown bound to the admitted raw bytes."""

    _verify_capability(admission)
    if extraction.source_sha256 != admission.source_sha256:
        raise GenericIncomingIntegrityError("extraction is bound to different admitted bytes")
    return render_verified_generic_source_note_projection_v2(
        metadata=admission.metadata,
        source_sha256=admission.source_sha256,
        source_snapshot=admission.source_snapshot,
        claims=extraction.claims,
    )


__all__ = [
    "GenericChangeMetadataV2",
    "GenericEvidenceSpanV2",
    "GenericExtractionModeV2",
    "GenericGroundedClaimV2",
    "GenericGroundedExtractionV2",
    "GenericIncomingBoundaryError",
    "GenericIncomingIntegrityError",
    "VerifiedGenericIncomingV2",
    "admit_generic_incoming_markdown_v2",
    "extraction_request_sha256_v2",
    "generic_extraction_prompt_variables_v2",
    "ground_generic_extraction_v2",
    "render_generic_source_note_v2",
    "render_verified_generic_source_note_projection_v2",
    "verify_generic_grounded_claim_projection_v2",
]
