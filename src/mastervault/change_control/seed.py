"""Strict, evaluator-isolated materialization of a pre-change runtime vault."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control._repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    read_repository_file,
    verified_repository_root,
)
from mastervault.change_control.models import (
    ClaimSourceReference,
    DocumentAuthority,
    DocumentRole,
    DocumentSpanReference,
    DocumentVersionMetadata,
    VersionedClaimRevision,
)
from mastervault.core.paths import PathBoundaryError, resolve_within
from mastervault.models import NoteType, SourceNote, content_hash
from mastervault.vaultfs.frontmatter import parse_frontmatter, split_frontmatter
from mastervault.vaultfs.notes import extract_title

SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RAW_PREFIX = PurePosixPath("datasets/larkstead/raw")
_PROCESSED_PREFIX = PurePosixPath("datasets/larkstead/processed")
_RECEIPT_RELATIVE_PATH = PurePosixPath("change_control/seed-receipt.json")
_VERIFIED_MANIFEST_TOKEN = object()
_VERIFIED_CONTEXT_TOKEN = object()
_VERIFIED_CONTEXT_SECRET = os.urandom(32)
MAX_SEED_MANIFEST_BYTES = 64 * 1024
MAX_SEED_SOURCE_BYTES = 128 * 1024
MAX_SEED_PROCESSED_NOTE_BYTES = 256 * 1024

# These keys carry answers rather than runtime source metadata. The recursive
# check gives a direct boundary error before Pydantic's general extra-field
# failure, including when a key is hidden inside a nested mapping.
_FORBIDDEN_KEYS = {
    "affected",
    "classification",
    "dependencies",
    "edge_label",
    "expected_affected_document_ids",
    "expected_after",
    "expected_impacts",
    "expected_pair_classifications",
    "expected_patch",
    "expected_patches",
    "expected_review_decision",
    "patch",
    "patches",
    "rationale",
    "review_decision",
    "temporal_phases",
}
_FORBIDDEN_LABEL_VALUES = {
    "SUPERSEDES",
    "CONTRADICTS",
    "COEXISTS",
    "UNRELATED",
    "DEPENDS_ON",
    "approve",
    "edit",
    "reject",
}


class SeedBoundaryError(ValueError):
    """Runtime seed data crossed into evaluator truth or an unsafe path."""


class SeedIntegrityError(ValueError):
    """A committed source or processed note no longer matches the manifest."""


class SeedReuseError(ValueError):
    """An existing target is not the exact pristine seed this builder owns."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _repo_relative(value: str) -> str:
    raw = value.strip()
    candidate = PurePosixPath(raw.replace("\\", "/"))
    windows = PureWindowsPath(raw)
    if (
        not raw
        or "\x00" in raw
        or not candidate.parts
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or Path(raw).is_absolute()
        or Path(raw).drive
    ):
        raise ValueError(f"must be a safe repository-relative path, got {value!r}")
    return candidate.as_posix()


def _require_prefix(value: str, prefix: PurePosixPath) -> str:
    normalized = PurePosixPath(_repo_relative(value))
    if normalized == prefix or not normalized.is_relative_to(prefix):
        raise ValueError(f"path must name a file below {prefix.as_posix()}")
    if "golden" in {part.casefold() for part in normalized.parts}:
        raise SeedBoundaryError("runtime seed paths cannot enter an evaluator-gold directory")
    return normalized.as_posix()


class PrechangeSeedDocument(_StrictFrozenModel):
    """Manifest binding for one source-declared pre-change document version."""

    document_id: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
    document_family: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
    version_label: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
    source_path: str
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    processed_path: str
    processed_sha256: str = Field(pattern=SHA256_PATTERN)
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority

    @field_validator("source_path")
    @classmethod
    def _source_path(cls, value: str) -> str:
        return _require_prefix(value, _RAW_PREFIX)

    @field_validator("processed_path")
    @classmethod
    def _processed_path(cls, value: str) -> str:
        return _require_prefix(value, _PROCESSED_PREFIX)

    @model_validator(mode="after")
    def _date_range(self) -> Self:
        if (
            self.declared_effective_to is not None
            and self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("declared_effective_to must follow declared_effective_from")
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


class PrechangeSeedManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    scenario_id: Literal["sl2-returns-prechange"]
    storyline: Literal["SL2"]
    as_of: date
    documents: tuple[PrechangeSeedDocument, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _closed_runtime_inventory(self) -> Self:
        ids = [document.document_id for document in self.documents]
        identities = [
            (document.document_family, document.version_label) for document in self.documents
        ]
        paths = [document.source_path for document in self.documents]
        processed = [document.processed_path for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("seed document IDs must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("seed document family/version identities must be unique")
        if len(paths) != len(set(paths)) or len(processed) != len(set(processed)):
            raise ValueError("seed source and processed paths must be unique")
        if any(document.declared_effective_from > self.as_of for document in self.documents):
            raise ValueError("a pre-change seed cannot contain a not-yet-effective document")
        return self


@dataclass(frozen=True)
class SeedMaterializationReport:
    scenario_id: str
    target: Path
    document_count: int
    manifest_sha256: str
    reused: bool


@dataclass(frozen=True)
class VerifiedPrechangeSeedManifest:
    """One evaluator-isolated manifest parsed from one exact byte snapshot."""

    manifest_path: Path
    manifest: PrechangeSeedManifest
    snapshot: bytes
    manifest_sha256: str
    _verification_token: object = dataclass_field(repr=False, compare=False)
    _verification_seal: str = dataclass_field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verification_token is not _VERIFIED_MANIFEST_TOKEN:
            raise TypeError("verified manifests must be created by the manifest loader")


@dataclass(frozen=True)
class VerifiedDocumentContext:
    """One verified, single-snapshot Markdown source binding.

    Callers obtain this through :func:`verify_seed_document_context`; resolver
    APIs do not accept free-form document metadata or note paths. A later PDF
    boundary can produce an analogous context after validating its immutable
    asset, parsed-document lineage, and evidence regions.
    """

    manifest_context: VerifiedPrechangeSeedManifest
    seed_document: PrechangeSeedDocument
    document: DocumentVersionMetadata
    repo_root: Path
    source_path: Path
    source_bytes: bytes
    source_note_path: str
    source_note_disk_path: Path
    source_note_bytes: bytes
    source_note_sha256: str
    source_note: SourceNote
    note_text: str
    body: str
    body_start_char: int
    _verification_token: object = dataclass_field(repr=False, compare=False)
    _verification_seal: str = dataclass_field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verification_token is not _VERIFIED_CONTEXT_TOKEN:
            raise TypeError("verified document contexts must be created by the verifier")


def _scan_forbidden(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_KEYS or key_text.startswith("expected_"):
                raise SeedBoundaryError(
                    f"runtime seed contains evaluator field at {path}.{key_text}"
                )
            _scan_forbidden(nested, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_forbidden(nested, f"{path}[{index}]")
    elif isinstance(value, str) and value in _FORBIDDEN_LABEL_VALUES:
        raise SeedBoundaryError(f"runtime seed contains evaluator label at {path}")


def _parse_manifest_snapshot(snapshot: bytes) -> PrechangeSeedManifest:
    try:
        raw = yaml.safe_load(snapshot.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise SeedBoundaryError(f"runtime seed manifest is not valid UTF-8 YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SeedBoundaryError("runtime seed manifest must be a YAML mapping")
    _scan_forbidden(raw)
    return PrechangeSeedManifest.model_validate(raw)


def _manifest_seal(
    *,
    manifest_path: Path,
    manifest: PrechangeSeedManifest,
    manifest_sha256: str,
) -> str:
    payload = {
        "manifest_path": manifest_path.as_posix(),
        "manifest": manifest.model_dump(mode="json"),
        "manifest_sha256": manifest_sha256,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(_VERIFIED_CONTEXT_SECRET, encoded, hashlib.sha256).hexdigest()


def load_verified_prechange_seed_manifest(path: Path) -> VerifiedPrechangeSeedManifest:
    """Capture, parse, and seal one exact runtime-manifest snapshot."""
    resolved = path.resolve(strict=True)
    if "golden" in {part.casefold() for part in resolved.parts}:
        raise SeedBoundaryError("runtime seed loader cannot read from an evaluator-gold directory")
    snapshot = resolved.read_bytes()
    manifest = _parse_manifest_snapshot(snapshot)
    manifest_sha256 = hashlib.sha256(snapshot).hexdigest()
    return VerifiedPrechangeSeedManifest(
        manifest_path=resolved,
        manifest=manifest,
        snapshot=snapshot,
        manifest_sha256=manifest_sha256,
        _verification_token=_VERIFIED_MANIFEST_TOKEN,
        _verification_seal=_manifest_seal(
            manifest_path=resolved,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
        ),
    )


def load_verified_prechange_seed_manifest_from_repository(
    *, repo_root: Path, manifest_path: Path
) -> VerifiedPrechangeSeedManifest:
    """Capture one exact-case, non-symlink repository manifest snapshot."""

    try:
        resolved_root = verified_repository_root(repo_root)
        absolute = Path(os.path.abspath(manifest_path))
        relative = absolute.relative_to(resolved_root).as_posix()
        resolved, snapshot = read_repository_file(
            repo_root=resolved_root,
            relative=relative,
            limit=MAX_SEED_MANIFEST_BYTES,
            label="pre-change seed manifest",
        )
    except RepositoryFileBoundaryError as exc:
        raise SeedBoundaryError(str(exc)) from exc
    except (RepositoryFileIntegrityError, ValueError) as exc:
        raise SeedIntegrityError(str(exc)) from exc
    if resolved.suffix not in {".yaml", ".yml"}:
        raise SeedBoundaryError("pre-change seed manifest must be YAML")
    snapshot_sha256 = hashlib.sha256(snapshot).hexdigest()
    manifest = _parse_manifest_snapshot(snapshot)
    return VerifiedPrechangeSeedManifest(
        manifest_path=resolved,
        manifest=manifest,
        snapshot=snapshot,
        manifest_sha256=snapshot_sha256,
        _verification_token=_VERIFIED_MANIFEST_TOKEN,
        _verification_seal=_manifest_seal(
            manifest_path=resolved,
            manifest=manifest,
            manifest_sha256=snapshot_sha256,
        ),
    )


def load_prechange_seed_manifest(path: Path) -> PrechangeSeedManifest:
    """Load runtime metadata while refusing evaluator directories and answers."""
    return load_verified_prechange_seed_manifest(path).manifest


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_verified_manifest(context: VerifiedPrechangeSeedManifest) -> None:
    if context._verification_token is not _VERIFIED_MANIFEST_TOKEN:
        raise SeedIntegrityError("manifest context was not created by the verified loader")
    if hashlib.sha256(context.snapshot).hexdigest() != context.manifest_sha256:
        raise SeedIntegrityError("verified manifest byte snapshot was altered")
    reparsed = _parse_manifest_snapshot(context.snapshot)
    if reparsed != context.manifest:
        raise SeedIntegrityError("verified manifest parsed snapshot was altered")
    expected_seal = _manifest_seal(
        manifest_path=context.manifest_path,
        manifest=context.manifest,
        manifest_sha256=context.manifest_sha256,
    )
    if not hmac.compare_digest(context._verification_seal, expected_seal):
        raise SeedIntegrityError("verified manifest snapshot binding was altered")


def _context_seal(
    manifest_context: VerifiedPrechangeSeedManifest,
    seed_document: PrechangeSeedDocument,
    repo_root: Path,
) -> str:
    payload = {
        "manifest_sha256": manifest_context.manifest_sha256,
        "manifest_path": manifest_context.manifest_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "seed_document": seed_document.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(_VERIFIED_CONTEXT_SECRET, encoded, hashlib.sha256).hexdigest()


def _read_verified(repo_root: Path, relative: str, expected_sha256: str) -> tuple[Path, bytes]:
    lexical = PurePosixPath(_repo_relative(relative))
    if lexical.is_relative_to(_RAW_PREFIX):
        allowed_prefix = _RAW_PREFIX
    elif lexical.is_relative_to(_PROCESSED_PREFIX):
        allowed_prefix = _PROCESSED_PREFIX
    else:
        raise SeedIntegrityError(f"seed source is outside the runtime roots: {relative}")
    limit = (
        MAX_SEED_SOURCE_BYTES if allowed_prefix == _RAW_PREFIX else MAX_SEED_PROCESSED_NOTE_BYTES
    )
    try:
        path, data = read_repository_file(
            repo_root=repo_root,
            relative=relative,
            limit=limit,
            label="seed source" if allowed_prefix == _RAW_PREFIX else "seed canonical note",
        )
    except RepositoryFileBoundaryError as exc:
        raise SeedBoundaryError(
            f"runtime seed path escapes its declared source root: {relative}"
        ) from exc
    except RepositoryFileIntegrityError as exc:
        if "unavailable" in str(exc) or "disappeared" in str(exc):
            raise SeedIntegrityError(
                f"seed source is missing or not a regular file: {relative}"
            ) from exc
        raise SeedIntegrityError(str(exc)) from exc
    actual = _sha256_bytes(data)
    if actual != expected_sha256:
        raise SeedIntegrityError(
            f"seed source hash drift for {relative}: expected {expected_sha256}, got {actual}"
        )
    return path, data


def _vault_relative(processed_path: str) -> PurePosixPath:
    path = PurePosixPath(processed_path)
    return path.relative_to(_PROCESSED_PREFIX)


def _source_note_from_snapshot(path: Path, snapshot: bytes) -> tuple[SourceNote, str, str, int]:
    """Parse exactly the bytes whose full SHA was verified by the caller."""
    try:
        text = snapshot.decode("utf-8")
        data, body = parse_frontmatter(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SeedIntegrityError(f"cannot parse canonical seed note {path}: {exc}") from exc
    if data.get("type") != NoteType.SOURCE.value:
        raise SeedIntegrityError(f"seed note is not a source note: {path}")
    if not data.get("title"):
        data = {**data, "title": extract_title(body, path.stem)}
    try:
        note = SourceNote.model_validate(data)
    except ValueError as exc:
        raise SeedIntegrityError(f"cannot validate canonical seed note {path}: {exc}") from exc
    _yaml_text, split_body, had_frontmatter = split_frontmatter(text)
    if not had_frontmatter or split_body != body:
        raise SeedIntegrityError(f"cannot resolve canonical note body boundary: {path}")
    body_start_char = len(text) - len(body)
    return note, text, body, body_start_char


def _require_unique_source_claim_ids(note: SourceNote, path: str | Path) -> None:
    claim_ids = [claim.id for claim in note.key_claims]
    duplicate_ids = sorted(claim_id for claim_id, count in Counter(claim_ids).items() if count > 1)
    if duplicate_ids:
        raise SeedIntegrityError(
            f"canonical seed note has duplicate claim IDs at {path}: {duplicate_ids}"
        )


def verify_seed_document_context(
    *,
    repo_root: Path,
    manifest_context: VerifiedPrechangeSeedManifest,
    document_id: str,
) -> VerifiedDocumentContext:
    """Bind a manifest entry to exact raw and canonical-note byte snapshots."""
    _require_verified_manifest(manifest_context)
    matching_documents = tuple(
        document
        for document in manifest_context.manifest.documents
        if document.document_id == document_id
    )
    if len(matching_documents) != 1:
        raise SeedIntegrityError(
            f"document_id must resolve exactly once in verified manifest: {document_id}"
        )
    seed_document = matching_documents[0]
    try:
        resolved_root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise SeedIntegrityError(f"repository root is unavailable: {repo_root}") from exc
    if not resolved_root.is_dir():
        raise SeedIntegrityError(f"repository root is not a directory: {resolved_root}")

    source_path, source_bytes = _read_verified(
        resolved_root, seed_document.source_path, seed_document.source_sha256
    )
    note_disk_path, note_bytes = _read_verified(
        resolved_root, seed_document.processed_path, seed_document.processed_sha256
    )
    note, note_text, body, body_start_char = _source_note_from_snapshot(note_disk_path, note_bytes)
    _require_unique_source_claim_ids(note, seed_document.processed_path)
    if note.provenance != seed_document.source_path:
        raise SeedIntegrityError(
            f"seed note provenance does not name its exact source: {seed_document.processed_path}"
        )
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SeedIntegrityError(
            f"legacy Markdown seed source is not UTF-8: {seed_document.source_path}"
        ) from exc
    if note.provenance_hash != content_hash(source_text):
        raise SeedIntegrityError(
            f"seed note provenance hash does not match source: {seed_document.processed_path}"
        )

    source_note_path = _vault_relative(seed_document.processed_path).as_posix()
    document = seed_document.document_version()
    if document.source_path != note.provenance:
        raise SeedIntegrityError("document metadata and canonical note bind different raw sources")
    return VerifiedDocumentContext(
        manifest_context=manifest_context,
        seed_document=seed_document,
        document=document,
        repo_root=resolved_root,
        source_path=source_path,
        source_bytes=source_bytes,
        source_note_path=source_note_path,
        source_note_disk_path=note_disk_path,
        source_note_bytes=note_bytes,
        source_note_sha256=_sha256_bytes(note_bytes),
        source_note=note,
        note_text=note_text,
        body=body,
        body_start_char=body_start_char,
        _verification_token=_VERIFIED_CONTEXT_TOKEN,
        _verification_seal=_context_seal(
            manifest_context,
            seed_document,
            resolved_root,
        ),
    )


def _require_verified_context(context: VerifiedDocumentContext) -> None:
    """Recheck the in-memory capability without rereading mutable files."""
    if context._verification_token is not _VERIFIED_CONTEXT_TOKEN:
        raise SeedIntegrityError("document context was not created by the verifier")
    _require_verified_manifest(context.manifest_context)
    matching_documents = tuple(
        document
        for document in context.manifest_context.manifest.documents
        if document.document_id == context.seed_document.document_id
    )
    if len(matching_documents) != 1 or matching_documents[0] != context.seed_document:
        raise SeedIntegrityError("verified context is not an exact manifest member")
    expected_seal = _context_seal(
        context.manifest_context,
        context.seed_document,
        context.repo_root,
    )
    if not hmac.compare_digest(context._verification_seal, expected_seal):
        raise SeedIntegrityError("verified context manifest-entry binding was altered")
    if context.document != context.seed_document.document_version():
        raise SeedIntegrityError("verified context document binding was altered")
    if _sha256_bytes(context.source_bytes) != context.seed_document.source_sha256:
        raise SeedIntegrityError("verified context raw-source snapshot was altered")
    if _sha256_bytes(context.source_note_bytes) != context.seed_document.processed_sha256:
        raise SeedIntegrityError("verified context canonical-note snapshot was altered")
    if context.source_note_sha256 != context.seed_document.processed_sha256:
        raise SeedIntegrityError("verified context canonical-note SHA binding was altered")
    if context.source_note_path != _vault_relative(context.seed_document.processed_path).as_posix():
        raise SeedIntegrityError("verified context canonical-note path was altered")
    try:
        expected_source_path = resolve_within(context.repo_root, context.seed_document.source_path)
        expected_note_path = resolve_within(context.repo_root, context.seed_document.processed_path)
    except PathBoundaryError as exc:
        raise SeedIntegrityError("verified context path binding was altered") from exc
    if (
        context.source_path != expected_source_path
        or context.source_note_disk_path != expected_note_path
    ):
        raise SeedIntegrityError("verified context filesystem binding was altered")
    note, text, body, body_start_char = _source_note_from_snapshot(
        context.source_note_disk_path,
        context.source_note_bytes,
    )
    _require_unique_source_claim_ids(note, context.source_note_path)
    if (
        note != context.source_note
        or text != context.note_text
        or body != context.body
        or body_start_char != context.body_start_char
    ):
        raise SeedIntegrityError("verified context parsed-note snapshot was altered")
    if note.provenance != context.seed_document.source_path:
        raise SeedIntegrityError("verified context provenance binding was altered")
    try:
        source_text = context.source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SeedIntegrityError("verified context raw-source snapshot is not UTF-8") from exc
    if note.provenance_hash != content_hash(source_text):
        raise SeedIntegrityError("verified context legacy provenance hash was altered")


def _receipt_bytes(
    manifest: PrechangeSeedManifest,
    manifest_sha256: str,
    copies: dict[PurePosixPath, bytes],
) -> bytes:
    document_versions = {
        document.document_id: document.document_version().document_version_id
        for document in manifest.documents
    }
    files = {
        relative.as_posix(): {"sha256": _sha256_bytes(data), "bytes": len(data)}
        for relative, data in sorted(copies.items(), key=lambda item: item[0].as_posix())
    }
    payload = {
        "schema_version": 1,
        "scenario_id": manifest.scenario_id,
        "as_of": manifest.as_of.isoformat(),
        "manifest_sha256": manifest_sha256,
        "document_versions": document_versions,
        "files": files,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _expected_files(
    repo_root: Path,
    manifest_context: VerifiedPrechangeSeedManifest,
) -> dict[PurePosixPath, bytes]:
    _require_verified_manifest(manifest_context)
    copies: dict[PurePosixPath, bytes] = {}
    for seed_document in manifest_context.manifest.documents:
        context = verify_seed_document_context(
            repo_root=repo_root,
            manifest_context=manifest_context,
            document_id=seed_document.document_id,
        )
        copies[PurePosixPath("vault") / PurePosixPath(context.source_note_path)] = (
            context.source_note_bytes
        )
        copies[PurePosixPath("source_snapshot") / PurePosixPath(seed_document.source_path)] = (
            context.source_bytes
        )

    copies[_RECEIPT_RELATIVE_PATH] = _receipt_bytes(
        manifest_context.manifest,
        manifest_context.manifest_sha256,
        copies,
    )
    return copies


def _verify_pristine_reuse(target: Path, expected: dict[PurePosixPath, bytes]) -> None:
    if target.is_symlink() or not target.is_dir():
        raise SeedReuseError(f"seed target exists but is not a real directory: {target}")
    actual_entries = {path.relative_to(target).as_posix() for path in target.rglob("*")}
    expected_files = {path.as_posix() for path in expected}
    expected_directories: set[str] = set()
    for relative in expected:
        parent = relative.parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if actual_entries != expected_files | expected_directories:
        raise SeedReuseError("existing seed target has missing, extra, or replaced files")
    for directory_relative in expected_directories:
        path = target / directory_relative
        if path.is_symlink() or not path.is_dir():
            raise SeedReuseError(f"existing seed directory was replaced: {directory_relative}")
    for relative, expected_bytes in expected.items():
        path = target / relative
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected_bytes:
            raise SeedReuseError(f"existing seed file drifted: {relative.as_posix()}")


def _publish_files(staging: Path, files: dict[PurePosixPath, bytes]) -> None:
    for relative, data in sorted(files.items(), key=lambda item: item[0].as_posix()):
        destination = staging.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(data)


def _is_at_or_below(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)


def _has_samefile_ancestor(candidate: Path, root: Path) -> bool:
    """Use filesystem identity so case aliases cannot evade containment."""
    current = candidate
    while True:
        try:
            if (current.exists() or current.is_symlink()) and os.path.samefile(current, root):
                return True
        except OSError as exc:
            raise SeedBoundaryError(
                f"cannot verify seed-target filesystem identity: {current}"
            ) from exc
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _resolve_external_target(repo_root: Path, target: Path) -> Path:
    """Resolve a target and reject lexical or symlink routes into the repository."""
    lexical_target = Path(os.path.abspath(target))
    if _is_at_or_below(lexical_target, repo_root):
        raise SeedBoundaryError("seed target must be outside the repository tree")
    if target.name in {"", ".", ".."}:
        raise SeedBoundaryError(f"seed target must name a child directory: {target}")
    try:
        target_parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise SeedBoundaryError(
            f"seed target must have an existing directory parent: {target}"
        ) from exc
    if not target_parent.is_dir():
        raise SeedBoundaryError(f"seed target parent is not a directory: {target_parent}")
    candidate = target_parent / target.name
    resolved_candidate = candidate.resolve(strict=False)
    if (
        _is_at_or_below(target_parent, repo_root)
        or _is_at_or_below(resolved_candidate, repo_root)
        or _has_samefile_ancestor(candidate, repo_root)
        or _has_samefile_ancestor(resolved_candidate, repo_root)
    ):
        raise SeedBoundaryError("seed target must be outside the repository tree")
    return candidate


def _unlink_owned_lock(lock_path: Path, owned_stat: os.stat_result) -> None:
    """Remove only the same regular-file inode opened by this materializer."""
    try:
        current = os.lstat(lock_path)
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(current.st_mode)
        and current.st_dev == owned_stat.st_dev
        and current.st_ino == owned_stat.st_ino
    ):
        lock_path.unlink()


def materialize_prechange_seed(
    *,
    repo_root: Path,
    manifest_path: Path,
    target: Path,
) -> SeedMaterializationReport:
    """Publish or verify an exact disposable pre-change workspace.

    Publication uses one directory rename and an exclusive lock among
    cooperating materializers. It does not claim exclusion against unrelated
    processes that ignore or replace that lock.
    """
    try:
        repo_root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise SeedIntegrityError(f"repository root is unavailable: {repo_root}") from exc
    if not repo_root.is_dir():
        raise SeedIntegrityError(f"repository root is not a directory: {repo_root}")
    target = _resolve_external_target(repo_root, target)

    manifest_context = load_verified_prechange_seed_manifest(manifest_path)
    manifest = manifest_context.manifest
    expected = _expected_files(repo_root, manifest_context)
    if target.exists() or target.is_symlink():
        _verify_pristine_reuse(target, expected)
        return SeedMaterializationReport(
            scenario_id=manifest.scenario_id,
            target=target,
            document_count=len(manifest.documents),
            manifest_sha256=manifest_context.manifest_sha256,
            reused=True,
        )

    target_parent = target.parent
    lock_path = target_parent / f".{target.name}.materialize.lock"
    lock_fd: int | None = None
    owned_lock_stat: os.stat_result | None = None
    staging: Path | None = None
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owned_lock_stat = os.fstat(lock_fd)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target_parent))
        _publish_files(staging, expected)
        if target.exists() or target.is_symlink():
            raise SeedReuseError(f"seed target appeared during materialization: {target}")
        staging.rename(target)
        staging = None
    except FileExistsError as exc:
        raise SeedReuseError(
            f"another materializer owns the seed target lock: {lock_path}"
        ) from exc
    finally:
        try:
            if staging is not None:
                shutil.rmtree(staging)
        finally:
            if lock_fd is not None:
                try:
                    if owned_lock_stat is not None:
                        _unlink_owned_lock(lock_path, owned_lock_stat)
                finally:
                    os.close(lock_fd)

    return SeedMaterializationReport(
        scenario_id=manifest.scenario_id,
        target=target,
        document_count=len(manifest.documents),
        manifest_sha256=manifest_context.manifest_sha256,
        reused=False,
    )


def resolve_claim_revision(
    *,
    context: VerifiedDocumentContext,
    source_claim_id: str,
    declared_effective_from: date,
    declared_effective_to: date | None,
    scopes: tuple[str, ...],
) -> VersionedClaimRevision:
    """Resolve one claim only from its verified manifest-bound byte snapshot."""
    _require_verified_context(context)
    matches = tuple(
        candidate for candidate in context.source_note.key_claims if candidate.id == source_claim_id
    )
    if len(matches) != 1:
        raise SeedIntegrityError(
            f"claim ID must resolve exactly once in {context.source_note_path}: {source_claim_id}"
        )
    claim = matches[0]
    reference = ClaimSourceReference(
        source_note_path=context.source_note_path,
        source_note_sha256=context.source_note_sha256,
        source_claim_id=claim.id,
        evidence=tuple(claim.evidence),
    )
    return VersionedClaimRevision.create(
        document=context.document,
        source=reference,
        statement=claim.statement,
        declared_effective_from=declared_effective_from,
        declared_effective_to=declared_effective_to,
        scopes=scopes,
    )


def resolve_document_span(
    *,
    context: VerifiedDocumentContext,
    quote: str,
    start_char: int,
) -> DocumentSpanReference:
    """Resolve an exact body-only span from the same verified note snapshot."""
    _require_verified_context(context)
    end_char = start_char + len(quote)
    if (
        start_char < context.body_start_char
        or end_char > len(context.note_text)
        or context.note_text[start_char:end_char] != quote
    ):
        raise SeedIntegrityError(
            "document span quote must match the canonical note body at file-relative offsets"
        )
    return DocumentSpanReference(
        document_version_id=context.document.document_version_id,
        source_note_path=context.source_note_path,
        source_note_sha256=context.source_note_sha256,
        quote=quote,
        start_char=start_char,
        end_char=end_char,
    )
