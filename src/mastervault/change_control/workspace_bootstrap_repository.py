"""Resolve one existing workspace into inert, exact bootstrap evidence.

This module owns filesystem resolution only.  It neither opens the legacy
index nor persists receipts, initializes authority, or mints a verification
capability.  The application layer may consume the returned immutable
snapshot and pass its exact index expectation to the independent index
attestation boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import TracebackType
from typing import Final, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from mastervault.change_control.claim_scopes import claim_scopes_v1
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    RelationGraph,
    TemporalConstraintSet,
    VersionedClaimRevision,
    canonical_json_bytes,
    normalize_logical_key,
)
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    verified_repository_root,
)
from mastervault.change_control.workspace_bootstrap import (
    MAX_WORKSPACE_INDEX_BYTES_V1,
    MAX_WORKSPACE_MANAGED_SOURCE_NOTES_V1,
    MAX_WORKSPACE_MEMBER_BYTES_V1,
    MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
    MAX_WORKSPACE_RAW_SOURCE_TOTAL_BYTES_V1,
    MAX_WORKSPACE_VAULT_DEPTH_V1,
    MAX_WORKSPACE_VAULT_DIRECTORIES_V1,
    MAX_WORKSPACE_VAULT_MEMBERS_V1,
    LegacyIndexExpectation,
    ManagedSourceNoteBootstrapMetadata,
    WorkspaceBootstrapInventory,
    WorkspaceNoteKind,
    WorkspaceVaultMember,
)
from mastervault.core.errors import DocumentIntegrityError
from mastervault.models import NoteType, SourceNote, content_hash
from mastervault.sync.indexer import (
    ExactVaultNoteInput,
    ExactWorkspaceFileInput,
    prepare_exact_vault_notes,
)
from mastervault.vaultfs.frontmatter import FrontmatterError, parse_frontmatter, split_frontmatter
from mastervault.vaultfs.notes import MODEL_BY_TYPE, extract_title

MAX_BOOTSTRAP_MANIFEST_BYTES_V1: Final = 1024 * 1024
MAX_BOOTSTRAP_YAML_NODES_V1: Final = 20_000
MAX_BOOTSTRAP_YAML_DEPTH_V1: Final = 32


class WorkspaceBootstrapRepositoryError(ValueError):
    """An existing workspace cannot be resolved into exact bootstrap evidence."""


class WorkspaceBootstrapManifestError(WorkspaceBootstrapRepositoryError):
    """The operator-supplied manifest is malformed or non-canonical."""


class WorkspaceBootstrapPlatformUnsupportedError(WorkspaceBootstrapRepositoryError):
    """The platform cannot supply the required no-follow filesystem guarantees."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkspaceManagedSourceManifestEntry(_StrictFrozenModel):
    """Explicit temporal authority for one selected canonical SourceNote."""

    logical_path: str
    source_note_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_note_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_MEMBER_BYTES_V1)
    source_root_id: str
    source_relative_path: str
    source_note_provenance: str = Field(min_length=1, max_length=4096)
    raw_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_source_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_RAW_SOURCE_BYTES_V1)
    document_id: str
    document_family: str
    version_label: str
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority

    @field_validator("declared_effective_from", "declared_effective_to", mode="before")
    @classmethod
    def _dates(cls, value: object) -> object:
        if value is None or isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise ValueError("effective dates must be canonical YYYY-MM-DD values")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("effective dates must be canonical YYYY-MM-DD values") from exc
        if parsed.isoformat() != value:
            raise ValueError("effective dates must be canonical YYYY-MM-DD values")
        return parsed

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return DocumentRole(value)
            except ValueError as exc:
                raise ValueError("role is not supported") from exc
        return value

    @field_validator("authority", mode="before")
    @classmethod
    def _authority(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return DocumentAuthority(value)
            except ValueError as exc:
                raise ValueError("authority is not supported") from exc
        return value

    @field_validator("source_root_id")
    @classmethod
    def _source_root_id(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value) is None:
            raise ValueError("source_root_id must be one normalized path-safe key")
        return value

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
    def _canonical(self) -> WorkspaceManagedSourceManifestEntry:
        try:
            canonical = canonical_repo_relative(self.logical_path)
        except (RepositoryFileBoundaryError, ValueError) as exc:
            raise ValueError("managed SourceNote path is not canonical") from exc
        if canonical != self.logical_path:
            raise ValueError("managed SourceNote path is not canonical")
        _canonical_source_relative(self.source_relative_path)
        for label, value in (
            ("document_id", self.document_id),
            ("document_family", self.document_family),
            ("version_label", self.version_label),
        ):
            if normalize_logical_key(value) != value:
                raise ValueError(f"{label} must already be normalized")
        if not self.logical_path.endswith(".md"):
            raise ValueError("managed SourceNote path must name Markdown")
        if self.declared_effective_to is not None and (
            self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("declared_effective_to must follow declared_effective_from")
        return self


class WorkspaceBootstrapManifest(_StrictFrozenModel):
    """Versioned, operator-authored selection for a generic workspace."""

    schema_version: Literal[1] = 1
    aggregate_id: str
    legacy_index_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    legacy_index_file_byte_count: int = Field(ge=1, le=MAX_WORKSPACE_INDEX_BYTES_V1)
    managed_source_notes: tuple[WorkspaceManagedSourceManifestEntry, ...] = Field(
        min_length=1, max_length=MAX_WORKSPACE_MANAGED_SOURCE_NOTES_V1
    )

    @field_validator("managed_source_notes", mode="before")
    @classmethod
    def _managed_source_note_sequence(cls, value: object) -> object:
        # YAML and JSON represent arrays as lists.  Convert only that container;
        # the nested strict models still reject coercive scalar inputs.
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _canonical(self) -> WorkspaceBootstrapManifest:
        if normalize_logical_key(self.aggregate_id) != self.aggregate_id:
            raise ValueError("aggregate_id must already be normalized")
        ordered = tuple(sorted(self.managed_source_notes, key=lambda item: item.logical_path))
        if self.managed_source_notes != ordered:
            raise ValueError("managed SourceNotes must use canonical path order")
        paths = tuple(item.logical_path for item in ordered)
        if len(set(paths)) != len(paths):
            raise ValueError("managed SourceNote paths must be unique")
        if len({_path_identity(value) for value in paths}) != len(paths):
            raise ValueError("managed SourceNote paths must be case/Unicode unambiguous")
        raw_paths = tuple((item.source_root_id, item.source_relative_path) for item in ordered)
        if len({(root_id, _path_identity(relative)) for root_id, relative in raw_paths}) != len(
            raw_paths
        ):
            raise ValueError("managed raw-source selections must be case/Unicode unambiguous")
        if sum(item.raw_source_byte_count for item in ordered) > (
            MAX_WORKSPACE_RAW_SOURCE_TOTAL_BYTES_V1
        ):
            raise ValueError("managed raw sources exceed the aggregate byte limit")
        logical_versions = tuple((item.document_family, item.version_label) for item in ordered)
        if len(set(logical_versions)) != len(logical_versions):
            raise ValueError("managed document family/version identities must be unique")
        return self


@dataclass(frozen=True)
class ResolvedManagedSourceNote:
    metadata: ManagedSourceNoteBootstrapMetadata
    note: SourceNote
    snapshot: CanonicalSourceNoteSnapshot
    raw_source_bytes: bytes


@dataclass(frozen=True)
class BootstrapSourceRoot:
    """Runtime-only operator binding for one manifest source-root identity."""

    root_id: str
    path: Path

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", self.root_id) is None:
            raise ValueError("bootstrap source-root id must be one normalized path-safe key")
        if self.root_id == "workspace":
            raise ValueError("workspace is a reserved bootstrap source-root id")
        if not isinstance(self.path, Path):
            raise TypeError("bootstrap source-root path must be a pathlib.Path")


@dataclass(frozen=True)
class ResolvedWorkspaceBootstrap:
    workspace_root: Path
    legacy_index_path: Path
    manifest: WorkspaceBootstrapManifest
    manifest_sha256: str
    inventory: WorkspaceBootstrapInventory
    aggregate: ChangeControlAggregate
    exact_vault_notes: tuple[ExactVaultNoteInput, ...]
    managed_source_notes: tuple[ResolvedManagedSourceNote, ...]
    source_roots: tuple[BootstrapSourceRoot, ...]


@dataclass
class _PinnedEvidenceFile:
    root_id: str
    relative_path: str
    file_fd: int
    signature: tuple[int, int, int, int, int, int]
    sha256: str
    byte_count: int
    limit: int
    source_path: bool = False

    def close(self) -> None:
        if self.file_fd >= 0:
            os.close(self.file_fd)
            self.file_fd = -1


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _overlap_paths(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _canonical_source_relative(value: str) -> str:
    """Validate an exact POSIX-relative source path while permitting spaces."""

    candidate = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or candidate.is_absolute()
        or not candidate.parts
        or windows.is_absolute()
        or bool(windows.drive)
        or candidate.as_posix() != value
        or "." in candidate.parts
        or ".." in candidate.parts
        or any(part.startswith(".") for part in candidate.parts)
        or any(unicodedata.category(character).startswith("C") for character in value)
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > 1024
    ):
        raise ValueError(
            "source_relative_path must be one NFC-normalized exact relative POSIX path"
        )
    _reject_evaluator_path(value, label="managed source-relative path")
    return value


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _stable_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _exact_child_name(parent_fd: int, name: str, *, label: str) -> None:
    try:
        matches = [
            entry
            for entry in os.listdir(parent_fd)
            if _path_identity(entry) == _path_identity(name)
        ]
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot inspect {label} directory") from exc
    if len(matches) != 1 or matches[0] != name:
        raise WorkspaceBootstrapRepositoryError(
            f"{label} path is missing or case/Unicode-ambiguous"
        )


def _open_directory_at(parent_fd: int, name: str, *, label: str) -> int:
    _exact_child_name(parent_fd, name, label=label)
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise WorkspaceBootstrapRepositoryError(f"{label} is not a directory")
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(child_fd)
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"cannot open {label} without following links"
        ) from exc
    if not stat.S_ISDIR(opened.st_mode) or _inode(opened) != _inode(before):
        os.close(child_fd)
        raise WorkspaceBootstrapRepositoryError(f"{label} changed while being opened")
    return child_fd


@dataclass
class _PinnedWorkspace:
    root: Path
    root_fd: int
    root_signature: tuple[int, int, int, int, int, int]

    def verify(self) -> None:
        try:
            opened = os.fstat(self.root_fd)
            current = self.root.lstat()
        except OSError as exc:
            raise WorkspaceBootstrapRepositoryError(
                "workspace root changed while bootstrap evidence was resolved"
            ) from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or _stable_signature(opened) != self.root_signature
            or _stable_signature(current) != self.root_signature
        ):
            raise WorkspaceBootstrapRepositoryError(
                "workspace root changed while bootstrap evidence was resolved"
            )

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1


def _pin_workspace(workspace_root: Path) -> _PinnedWorkspace:
    if (
        not workspace_root.is_absolute()
        or "." in workspace_root.parts
        or ".." in workspace_root.parts
    ):
        raise WorkspaceBootstrapRepositoryError("workspace root must be absolute and canonical")
    current_fd = -1
    try:
        current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in workspace_root.parts[1:]:
            next_fd = _open_directory_at(
                current_fd,
                component,
                label="workspace root",
            )
            os.close(current_fd)
            current_fd = next_fd
        opened = os.fstat(current_fd)
        current = workspace_root.lstat()
        if not stat.S_ISDIR(opened.st_mode) or _stable_signature(opened) != _stable_signature(
            current
        ):
            raise WorkspaceBootstrapRepositoryError("workspace root changed while being pinned")
        pinned = _PinnedWorkspace(
            root=workspace_root,
            root_fd=current_fd,
            root_signature=_stable_signature(opened),
        )
        current_fd = -1
        return pinned
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(
            "workspace root cannot be pinned without following links"
        ) from exc
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _verified_source_roots(
    *,
    workspace: Path,
    manifest: WorkspaceBootstrapManifest,
    source_roots: tuple[BootstrapSourceRoot, ...],
) -> tuple[BootstrapSourceRoot, ...]:
    required = {
        item.source_root_id
        for item in manifest.managed_source_notes
        if item.source_root_id != "workspace"
    }
    supplied_ids = tuple(item.root_id for item in source_roots)
    if len(set(supplied_ids)) != len(supplied_ids) or set(supplied_ids) != required:
        raise WorkspaceBootstrapManifestError(
            "runtime source-root ids must exactly equal the manifest's external ids"
        )
    verified: list[BootstrapSourceRoot] = []
    for item in sorted(source_roots, key=lambda value: value.root_id):
        if not item.path.is_absolute() or "." in item.path.parts or ".." in item.path.parts:
            raise WorkspaceBootstrapManifestError(
                f"source root {item.root_id!r} must be a canonical absolute path"
            )
        try:
            lexical = Path(os.path.abspath(os.fspath(item.path)))
            resolved = item.path.resolve(strict=True)
            info = item.path.lstat()
        except (OSError, ValueError) as exc:
            raise WorkspaceBootstrapRepositoryError(
                f"source root {item.root_id!r} is unavailable"
            ) from exc
        if lexical != item.path or resolved != item.path or item.path == Path("/"):
            raise WorkspaceBootstrapRepositoryError(
                f"source root {item.root_id!r} must be exact, non-symlink, and bounded"
            )
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or bool(info.st_mode & 0o022)
        ):
            raise WorkspaceBootstrapRepositoryError(
                f"source root {item.root_id!r} is not an owner-controlled directory"
            )
        if {part.casefold() for part in item.path.parts} & {"evals", "golden"}:
            raise WorkspaceBootstrapManifestError(
                f"source root {item.root_id!r} cannot be evaluator data"
            )
        if _overlap_paths(item.path, workspace):
            raise WorkspaceBootstrapManifestError(
                f"source root {item.root_id!r} overlaps the protected workspace"
            )
        if any(_overlap_paths(item.path, prior.path) for prior in verified):
            raise WorkspaceBootstrapManifestError(
                "external source roots must be pairwise disjoint"
            )
        verified.append(item)
    return tuple(verified)


def _authority_source_path(entry: WorkspaceManagedSourceManifestEntry) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.workspace-bootstrap-source.v1",
                "source_root_id": entry.source_root_id,
                "source_relative_path": entry.source_relative_path,
                "source_note_provenance": entry.source_note_provenance,
            }
        )
    ).hexdigest()
    return f"bootstrap-sources/{entry.source_root_id}/{digest}"


def _read_file_at(
    parent_fd: int,
    name: str,
    *,
    limit: int,
    label: str,
) -> bytes:
    _exact_child_name(parent_fd, name, label=label)
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkspaceBootstrapRepositoryError(f"{label} is not a regular file")
    if before.st_uid != os.getuid() or before.st_nlink != 1 or bool(before.st_mode & 0o022):
        raise WorkspaceBootstrapRepositoryError(
            f"{label} is not one private owner-controlled regular file"
        )
    if before.st_size > limit:
        raise WorkspaceBootstrapRepositoryError(f"{label} exceeds fixed {limit}-byte limit")
    fd = -1
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or bool(opened.st_mode & 0o022)
            or _stable_signature(opened) != _stable_signature(before)
        ):
            raise WorkspaceBootstrapRepositoryError(f"{label} changed while being opened")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(limit + 1)
            first_finished = os.fstat(handle.fileno())
            handle.seek(0)
            confirmed = handle.read(limit + 1)
            finished = os.fstat(handle.fileno())
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot read {label} exactly") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    signature = _stable_signature(before)
    if (
        len(data) > limit
        or data != confirmed
        or len(data) != finished.st_size
        or signature != _stable_signature(first_finished)
        or signature != _stable_signature(finished)
        or signature != _stable_signature(after)
    ):
        raise WorkspaceBootstrapRepositoryError(f"{label} changed during its verified read")
    return data


def _read_pinned_file(
    pinned: _PinnedWorkspace,
    relative: str,
    *,
    limit: int,
    label: str,
    source_path: bool = False,
) -> bytes:
    canonical = (
        _canonical_source_relative(relative) if source_path else canonical_repo_relative(relative)
    )
    _reject_evaluator_path(canonical, label=label)
    parts = Path(canonical).parts
    fds: list[int] = []
    current_fd = os.dup(pinned.root_fd)
    fds.append(current_fd)
    edges: list[tuple[int, str, int]] = []
    try:
        for component in parts[:-1]:
            child_fd = _open_directory_at(current_fd, component, label=label)
            edges.append((current_fd, component, child_fd))
            fds.append(child_fd)
            current_fd = child_fd
        data = _read_file_at(
            current_fd,
            parts[-1],
            limit=limit,
            label=label,
        )
        for parent_fd, component, child_fd in reversed(edges):
            current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if _inode(current) != _inode(os.fstat(child_fd)):
                raise WorkspaceBootstrapRepositoryError(
                    f"{label} directory changed during its verified read"
                )
        pinned.verify()
        return data
    except WorkspaceBootstrapRepositoryError:
        raise
    except (RepositoryFileBoundaryError, ValueError) as exc:
        raise WorkspaceBootstrapManifestError(f"{label} path is not canonical") from exc
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot inspect {label} exactly") from exc
    finally:
        for descriptor in reversed(fds):
            os.close(descriptor)


def _read_workspace_file(
    pinned: _PinnedWorkspace,
    relative: str,
    *,
    limit: int,
    label: str,
) -> bytes:
    return _read_pinned_file(
        pinned,
        relative,
        limit=limit,
        label=label,
    )


def _open_workspace_regular_file(
    pinned: _PinnedWorkspace,
    relative: str,
    *,
    label: str,
) -> int:
    """Open one exact current workspace file while retaining only its file FD."""

    try:
        canonical = canonical_repo_relative(relative)
    except (RepositoryFileBoundaryError, ValueError) as exc:
        raise WorkspaceBootstrapRepositoryError(f"{label} path is not canonical") from exc
    parts = Path(canonical).parts
    current_fd = os.dup(pinned.root_fd)
    try:
        for component in parts[:-1]:
            child_fd = _open_directory_at(current_fd, component, label=label)
            os.close(current_fd)
            current_fd = child_fd
        _exact_child_name(current_fd, parts[-1], label=label)
        before = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=current_fd,
        )
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or bool(opened.st_mode & 0o022)
            or _stable_signature(opened) != _stable_signature(before)
        ):
            os.close(file_fd)
            raise WorkspaceBootstrapRepositoryError(f"{label} changed while being pinned")
        return file_fd
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot pin {label} exactly") from exc
    finally:
        os.close(current_fd)


def _open_pinned_source_file(
    pinned: _PinnedWorkspace,
    relative: str,
    *,
    label: str,
) -> int:
    """Open an exact source-root file while permitting visible path spaces."""

    canonical = _canonical_source_relative(relative)
    parts = PurePosixPath(canonical).parts
    current_fd = os.dup(pinned.root_fd)
    try:
        for component in parts[:-1]:
            child_fd = _open_directory_at(current_fd, component, label=label)
            os.close(current_fd)
            current_fd = child_fd
        _exact_child_name(current_fd, parts[-1], label=label)
        before = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=current_fd,
        )
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or bool(opened.st_mode & 0o022)
            or _stable_signature(opened) != _stable_signature(before)
        ):
            os.close(file_fd)
            raise WorkspaceBootstrapRepositoryError(f"{label} changed while being pinned")
        return file_fd
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot pin {label} exactly") from exc
    finally:
        os.close(current_fd)


def _hash_pinned_file(file_fd: int, *, limit: int, label: str) -> tuple[str, int]:
    try:
        before = os.fstat(file_fd)
        digest = hashlib.sha256()
        offset = 0
        while True:
            block = os.pread(file_fd, 1024 * 1024, offset)
            if not block:
                break
            digest.update(block)
            offset += len(block)
            if offset > limit:
                raise WorkspaceBootstrapRepositoryError(f"{label} exceeds its fixed limit")
        after = os.fstat(file_fd)
    except WorkspaceBootstrapRepositoryError:
        raise
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError(f"cannot hash {label} exactly") from exc
    if _stable_signature(before) != _stable_signature(after) or after.st_size != offset:
        raise WorkspaceBootstrapRepositoryError(f"{label} changed while being guarded")
    return digest.hexdigest(), offset


def _reject_evaluator_path(value: str, *, label: str) -> None:
    parts = {part.casefold() for part in Path(value).parts}
    if parts & {"evals", "golden"}:
        raise WorkspaceBootstrapManifestError(f"{label} cannot enter evaluator data")


def _preflight_yaml(text: str) -> None:
    try:
        tokens = tuple(yaml.scan(text))
    except (yaml.YAMLError, RecursionError) as exc:
        raise WorkspaceBootstrapManifestError("bootstrap manifest is not safe YAML") from exc
    if any(isinstance(token, (AliasToken, AnchorToken)) for token in tokens):
        raise WorkspaceBootstrapManifestError(
            "bootstrap manifest cannot contain YAML anchors or aliases"
        )
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise WorkspaceBootstrapManifestError("bootstrap manifest is not safe YAML") from exc

    nodes = 0

    def visit(node: Node, *, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_BOOTSTRAP_YAML_NODES_V1:
            raise WorkspaceBootstrapManifestError("bootstrap manifest is too large")
        if depth > MAX_BOOTSTRAP_YAML_DEPTH_V1:
            raise WorkspaceBootstrapManifestError("bootstrap manifest is too deeply nested")
        if isinstance(node, MappingNode):
            seen: set[tuple[str, str]] = set()
            for key, value in node.value:
                if not isinstance(key, ScalarNode):
                    raise WorkspaceBootstrapManifestError(
                        "bootstrap manifest contains a non-scalar key"
                    )
                identity = (key.tag, key.value)
                if identity in seen:
                    raise WorkspaceBootstrapManifestError(
                        "bootstrap manifest contains a duplicate YAML key"
                    )
                seen.add(identity)
                visit(value, depth=depth + 1)
        elif isinstance(node, SequenceNode):
            for value in node.value:
                visit(value, depth=depth + 1)

    if root is not None:
        visit(root, depth=0)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceBootstrapManifestError(
                "bootstrap manifest contains a duplicate JSON key"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise WorkspaceBootstrapManifestError(
        f"bootstrap manifest contains a non-finite JSON value: {value}"
    )


def _preflight_loaded_json(value: object) -> None:
    nodes = 0

    def visit(item: object, *, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_BOOTSTRAP_YAML_NODES_V1:
            raise WorkspaceBootstrapManifestError("bootstrap manifest is too large")
        if depth > MAX_BOOTSTRAP_YAML_DEPTH_V1:
            raise WorkspaceBootstrapManifestError("bootstrap manifest is too deeply nested")
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested, depth=depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth=depth + 1)

    visit(value, depth=0)


def _load_manifest(snapshot: bytes, *, suffix: str) -> WorkspaceBootstrapManifest:
    try:
        text = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceBootstrapManifestError("bootstrap manifest is not UTF-8") from exc
    try:
        if suffix == ".json":
            raw = json.loads(
                text,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
            _preflight_loaded_json(raw)
        else:
            _preflight_yaml(text)
            raw = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise WorkspaceBootstrapManifestError("bootstrap manifest is invalid") from exc
    if not isinstance(raw, dict):
        raise WorkspaceBootstrapManifestError("bootstrap manifest must be a mapping")
    try:
        return WorkspaceBootstrapManifest.model_validate(raw)
    except ValidationError as exc:
        raise WorkspaceBootstrapManifestError(
            f"bootstrap manifest contract is invalid: {exc}"
        ) from exc


def _verified_workspace_root(workspace_root: Path) -> Path:
    try:
        root = verified_repository_root(workspace_root)
    except (RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
        raise WorkspaceBootstrapRepositoryError(str(exc)) from exc
    if any(part.casefold() in {"golden", "evals"} for part in root.parts):
        raise WorkspaceBootstrapPlatformUnsupportedError(
            "workspace root cannot resolve within evaluator data"
        )
    try:
        info = root.lstat()
    except OSError as exc:
        raise WorkspaceBootstrapRepositoryError("workspace root is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or bool(info.st_mode & 0o022)
    ):
        raise WorkspaceBootstrapRepositoryError(
            "workspace root is not an owner-controlled directory"
        )
    return root


def _require_posix_no_follow_contract() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise WorkspaceBootstrapPlatformUnsupportedError(
            "platform cannot resolve bootstrap evidence without following links"
        )
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise WorkspaceBootstrapPlatformUnsupportedError(
            "platform lacks descriptor-relative bootstrap inspection"
        )
    if os.stat not in os.supports_follow_symlinks:
        raise WorkspaceBootstrapPlatformUnsupportedError(
            "platform cannot inspect bootstrap paths without following links"
        )


def _manifest_snapshot(
    *,
    pinned: _PinnedWorkspace,
    manifest_path: Path,
) -> tuple[bytes, str]:
    if manifest_path.is_absolute():
        try:
            relative_path = manifest_path.relative_to(pinned.root)
        except ValueError as exc:
            raise WorkspaceBootstrapManifestError(
                "bootstrap manifest must be inside the exact workspace root"
            ) from exc
    else:
        relative_path = manifest_path
    try:
        relative = canonical_repo_relative(relative_path.as_posix())
        _reject_evaluator_path(relative, label="bootstrap manifest")
    except (RepositoryFileBoundaryError, ValueError) as exc:
        raise WorkspaceBootstrapManifestError("bootstrap manifest path is not canonical") from exc
    suffix = Path(relative).suffix.casefold()
    if suffix not in {".yaml", ".yml", ".json"}:
        raise WorkspaceBootstrapManifestError("bootstrap manifest must be versioned YAML or JSON")
    try:
        snapshot = _read_workspace_file(
            pinned,
            relative,
            limit=MAX_BOOTSTRAP_MANIFEST_BYTES_V1,
            label="workspace bootstrap manifest",
        )
    except WorkspaceBootstrapManifestError as exc:
        if "is not a regular file" in str(exc):
            raise WorkspaceBootstrapManifestError(
                "bootstrap manifest cannot be a symlink or special file"
            ) from exc
        raise
    except WorkspaceBootstrapRepositoryError as exc:
        if "is not a regular file" in str(exc):
            raise WorkspaceBootstrapManifestError(
                "bootstrap manifest cannot be a symlink or special file"
            ) from exc
        raise
    except RepositoryFileBoundaryError as exc:
        raise WorkspaceBootstrapManifestError(str(exc)) from exc
    except (RepositoryFileIntegrityError, OSError) as exc:
        raise WorkspaceBootstrapRepositoryError(str(exc)) from exc
    return snapshot, suffix


def _walk_exact_vault(*, pinned: _PinnedWorkspace) -> tuple[ExactVaultNoteInput, ...]:
    candidates: list[ExactVaultNoteInput] = []
    directory_count = 1
    vault_fd = _open_directory_at(pinned.root_fd, "vault", label="workspace vault root")

    def walk(directory_fd: int, prefix: tuple[str, ...]) -> None:
        nonlocal directory_count
        if len(prefix) > MAX_WORKSPACE_VAULT_DEPTH_V1:
            raise WorkspaceBootstrapRepositoryError(
                "workspace vault exceeds the bounded directory depth"
            )
        try:
            before = os.fstat(directory_fd)
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise WorkspaceBootstrapRepositoryError("cannot enumerate the exact vault") from exc
        if len({_path_identity(name) for name in names}) != len(names):
            raise WorkspaceBootstrapRepositoryError(
                "vault directory entries must be case/Unicode unambiguous"
            )
        for name in names:
            if name.startswith("."):
                raise WorkspaceBootstrapRepositoryError(
                    "vault contains a hidden entry that cannot be silently skipped"
                )
            relative = Path(*prefix, name).as_posix()
            if {part.casefold() for part in Path(relative).parts} & {"evals", "golden"}:
                raise WorkspaceBootstrapRepositoryError(
                    f"vault inventory cannot enter evaluator data: {relative}"
                )
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise WorkspaceBootstrapRepositoryError(
                    f"vault inventory member disappeared: {relative}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                directory_count += 1
                if directory_count > MAX_WORKSPACE_VAULT_DIRECTORIES_V1:
                    raise WorkspaceBootstrapRepositoryError(
                        "workspace vault exceeds the bounded directory count"
                    )
                child_fd = _open_directory_at(
                    directory_fd,
                    name,
                    label=f"vault directory {relative}",
                )
                try:
                    walk(child_fd, (*prefix, name))
                    current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if _inode(current) != _inode(os.fstat(child_fd)):
                        raise WorkspaceBootstrapRepositoryError(
                            f"vault directory changed during enumeration: {relative}"
                        )
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceBootstrapRepositoryError(
                    f"vault contains a non-regular file: {relative}"
                )
            if not name.endswith(".md"):
                raise WorkspaceBootstrapRepositoryError(
                    f"vault contains a file outside the closed indexable inventory: {relative}"
                )
            try:
                canonical_repo_relative(relative)
            except RepositoryFileBoundaryError as exc:
                raise WorkspaceBootstrapRepositoryError(
                    f"vault inventory path is not canonical: {relative}"
                ) from exc
            content = _read_file_at(
                directory_fd,
                name,
                limit=MAX_WORKSPACE_MEMBER_BYTES_V1,
                label=f"vault note {relative}",
            )
            candidates.append(
                ExactVaultNoteInput(
                    rel_path=relative,
                    content=content,
                    workspace=pinned.root,
                )
            )
            if len(candidates) > MAX_WORKSPACE_VAULT_MEMBERS_V1:
                raise WorkspaceBootstrapRepositoryError(
                    "workspace vault exceeds the bounded inventory member limit"
                )
        try:
            after_names = sorted(os.listdir(directory_fd))
            after = os.fstat(directory_fd)
        except OSError as exc:
            raise WorkspaceBootstrapRepositoryError(
                "cannot revalidate exact vault closure"
            ) from exc
        if names != after_names or _stable_signature(before) != _stable_signature(after):
            raise WorkspaceBootstrapRepositoryError(
                "vault directory changed during exact inventory enumeration"
            )

    try:
        walk(vault_fd, ())
        current_vault = os.stat("vault", dir_fd=pinned.root_fd, follow_symlinks=False)
        if _inode(current_vault) != _inode(os.fstat(vault_fd)):
            raise WorkspaceBootstrapRepositoryError(
                "workspace vault root changed during exact inventory enumeration"
            )
        pinned.verify()
    finally:
        os.close(vault_fd)
    if not candidates:
        raise WorkspaceBootstrapRepositoryError("workspace vault contains no indexable notes")
    candidates.sort(key=lambda item: item.rel_path)
    paths = tuple(item.rel_path for item in candidates)
    if len({_path_identity(value) for value in paths}) != len(paths):
        raise WorkspaceBootstrapRepositoryError("vault paths must be unique and case-unambiguous")
    return tuple(candidates)


def _kind(note_type: str) -> WorkspaceNoteKind:
    try:
        return WorkspaceNoteKind(note_type)
    except ValueError as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"unsupported exact vault note type: {note_type}"
        ) from exc


def _parse_source_note(item: ExactVaultNoteInput) -> tuple[SourceNote, int]:
    try:
        text = item.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"managed SourceNote is not UTF-8: {item.rel_path}"
        ) from exc
    yaml_text, body, had_frontmatter = split_frontmatter(text)
    if not had_frontmatter:
        raise WorkspaceBootstrapRepositoryError(
            f"managed SourceNote has no frontmatter: {item.rel_path}"
        )
    try:
        _preflight_yaml(yaml_text)
    except WorkspaceBootstrapManifestError as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"managed SourceNote frontmatter is ambiguous: {item.rel_path}"
        ) from exc
    try:
        data, parsed_body = parse_frontmatter(text)
        if parsed_body != body:
            raise WorkspaceBootstrapRepositoryError(
                f"managed SourceNote body boundary is invalid: {item.rel_path}"
            )
        raw_type = data.get("type")
        if raw_type != NoteType.SOURCE.value:
            raise WorkspaceBootstrapRepositoryError(
                f"managed path is not a SourceNote: {item.rel_path}"
            )
        if not data.get("title"):
            data = {**data, "title": extract_title(body, Path(item.rel_path).stem)}
        note = MODEL_BY_TYPE[NoteType.SOURCE].model_validate(data)
    except (FrontmatterError, ValidationError, ValueError) as exc:
        if isinstance(exc, WorkspaceBootstrapRepositoryError):
            raise
        raise WorkspaceBootstrapRepositoryError(
            f"managed SourceNote is invalid: {item.rel_path}"
        ) from exc
    assert isinstance(note, SourceNote)
    return note, len(text) - len(body)


def _declares_source_note(item: ExactVaultNoteInput) -> bool:
    """Classify from parsed frontmatter, never from arbitrary body bytes."""

    try:
        text = item.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"exact vault note is not UTF-8: {item.rel_path}"
        ) from exc
    yaml_text, body, had_frontmatter = split_frontmatter(text)
    if not had_frontmatter:
        raise WorkspaceBootstrapRepositoryError(
            f"exact vault note has no frontmatter: {item.rel_path}"
        )
    try:
        _preflight_yaml(yaml_text)
        data, parsed_body = parse_frontmatter(text)
    except (FrontmatterError, WorkspaceBootstrapManifestError, ValueError) as exc:
        raise WorkspaceBootstrapRepositoryError(
            f"exact vault note frontmatter is invalid: {item.rel_path}"
        ) from exc
    if parsed_body != body:
        raise WorkspaceBootstrapRepositoryError(
            f"exact vault note body boundary is invalid: {item.rel_path}"
        )
    return data.get("type") == NoteType.SOURCE.value


def _attach_exact_supporting_files(
    *,
    pinned: _PinnedWorkspace,
    notes: tuple[ExactVaultNoteInput, ...],
) -> tuple[ExactVaultNoteInput, ...]:
    attached: list[ExactVaultNoteInput] = []
    for item in notes:
        note, _body_start = _parse_source_note(item) if _declares_source_note(item) else (None, 0)
        if note is None or note.source_asset is None or note.parsed_document is None:
            attached.append(item)
            continue
        support: list[ExactWorkspaceFileInput] = []
        for relative, limit, label in (
            (
                note.source_asset.stored_path,
                MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
                f"PDF source asset for {item.rel_path}",
            ),
            (
                note.parsed_document.artifact_path,
                MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
                f"parsed PDF artifact for {item.rel_path}",
            ),
        ):
            support.append(
                ExactWorkspaceFileInput(
                    rel_path=relative,
                    content=_read_workspace_file(
                        pinned,
                        relative,
                        limit=limit,
                        label=label,
                    ),
                )
            )
        attached.append(
            ExactVaultNoteInput(
                rel_path=item.rel_path,
                content=item.content,
                workspace=item.workspace,
                supporting_files=tuple(support),
            )
        )
    pinned.verify()
    return tuple(attached)


def resolve_workspace_bootstrap(
    *,
    workspace_root: Path,
    manifest_path: Path,
    source_roots: tuple[BootstrapSourceRoot, ...] = (),
    index_schema_version: int,
    embedding_model: str,
    embedding_dimensions: int,
) -> ResolvedWorkspaceBootstrap:
    """Resolve exact workspace bytes into inert bootstrap and index expectations."""

    _require_posix_no_follow_contract()
    workspace = _verified_workspace_root(Path(workspace_root))
    pinned = _pin_workspace(workspace)
    try:
        manifest_bytes, suffix = _manifest_snapshot(
            pinned=pinned,
            manifest_path=Path(manifest_path),
        )
        manifest = _load_manifest(manifest_bytes, suffix=suffix)
        verified_roots = _verified_source_roots(
            workspace=workspace,
            manifest=manifest,
            source_roots=source_roots,
        )
        exact_notes = _walk_exact_vault(pinned=pinned)
        exact_notes = _attach_exact_supporting_files(pinned=pinned, notes=exact_notes)
        try:
            prepared = prepare_exact_vault_notes(exact_notes)
        except (
            DocumentIntegrityError,
            FrontmatterError,
            ValidationError,
            ValueError,
            OSError,
        ) as exc:
            raise WorkspaceBootstrapRepositoryError(
                "complete vault inventory contains an invalid or unprojectable note"
            ) from exc
    finally:
        pinned.close()
    if len(prepared) != len(exact_notes):
        raise WorkspaceBootstrapRepositoryError(
            "exact vault projection skipped an inventory member"
        )

    by_path = {item.rel_path: item for item in exact_notes}
    prepared_by_path = {item.doc.rel_path: item for item in prepared}
    vault_members = tuple(
        WorkspaceVaultMember(
            logical_path=item.rel_path,
            note_kind=_kind(prepared_by_path[item.rel_path].doc.doc_type),
            content_sha256=_sha256(item.content),
            byte_count=len(item.content),
        )
        for item in exact_notes
    )
    legacy_index = LegacyIndexExpectation(
        index_file_sha256=manifest.legacy_index_file_sha256,
        index_file_byte_count=manifest.legacy_index_file_byte_count,
        index_schema_version=index_schema_version,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )

    documents: list[DocumentVersionMetadata] = []
    claims: list[VersionedClaimRevision] = []
    managed_metadata: list[ManagedSourceNoteBootstrapMetadata] = []
    resolved_managed: list[ResolvedManagedSourceNote] = []
    source_root_paths = {
        "workspace": workspace,
        **{item.root_id: item.path for item in verified_roots},
    }
    protected = (
        workspace / "vault",
        workspace / "index.db",
        workspace / "change_control",
        Path(manifest_path),
    )
    selected_inodes: set[tuple[int, int]] = set()
    for entry in manifest.managed_source_notes:
        _reject_evaluator_path(entry.logical_path, label="managed SourceNote path")
        _reject_evaluator_path(
            entry.source_relative_path,
            label="managed source-relative path",
        )
        exact = by_path.get(entry.logical_path)
        if exact is None:
            raise WorkspaceBootstrapRepositoryError(
                f"managed SourceNote is absent from the complete vault: {entry.logical_path}"
            )
        if _sha256(exact.content) != entry.source_note_sha256 or (
            len(exact.content) != entry.source_note_byte_count
        ):
            raise WorkspaceBootstrapRepositoryError(
                f"managed SourceNote bytes differ from the manifest: {entry.logical_path}"
            )
        note, body_start = _parse_source_note(exact)
        if note.provenance is None or note.provenance != entry.source_note_provenance:
            raise WorkspaceBootstrapRepositoryError(
                f"managed SourceNote provenance differs from the manifest: {entry.logical_path}"
            )
        root_path = source_root_paths[entry.source_root_id]
        selected = root_path.joinpath(*PurePosixPath(entry.source_relative_path).parts)
        if any(_overlap_paths(selected, boundary) for boundary in protected):
            raise WorkspaceBootstrapManifestError(
                "managed raw source overlaps protected workspace evidence"
            )
        pinned = _pin_workspace(root_path)
        try:
            raw_bytes = _read_pinned_file(
                pinned,
                entry.source_relative_path,
                limit=MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
                label=f"managed raw source {entry.source_relative_path}",
                source_path=True,
            )
            raw_fd = _open_pinned_source_file(
                pinned,
                entry.source_relative_path,
                label=f"managed raw source {entry.source_relative_path}",
            )
            try:
                inode = _inode(os.fstat(raw_fd))
            finally:
                os.close(raw_fd)
            if inode in selected_inodes:
                raise WorkspaceBootstrapRepositoryError(
                    "managed raw sources alias one filesystem object"
                )
            selected_inodes.add(inode)
        finally:
            pinned.close()
        if _sha256(raw_bytes) != entry.raw_source_sha256 or (
            len(raw_bytes) != entry.raw_source_byte_count
        ):
            raise WorkspaceBootstrapRepositoryError(
                f"managed raw-source bytes differ from the manifest: {entry.source_relative_path}"
            )
        if note.source_asset is None:
            try:
                raw_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceBootstrapRepositoryError(
                    f"managed text raw source is not UTF-8: {entry.source_relative_path}"
                ) from exc
            if note.provenance_hash is None or note.provenance_hash != content_hash(raw_text):
                raise WorkspaceBootstrapRepositoryError(
                    f"managed text provenance hash differs from exact raw source: "
                    f"{entry.source_relative_path}"
                )
        elif not (
            entry.source_root_id == "workspace"
            and note.source_asset.stored_path == entry.source_relative_path
            and note.source_asset.asset_sha256 == entry.raw_source_sha256
            and note.source_asset.size_bytes == entry.raw_source_byte_count
        ):
            raise WorkspaceBootstrapRepositoryError(
                f"managed PDF asset binding differs from exact raw source: "
                f"{entry.source_relative_path}"
            )
        authority_source_path = _authority_source_path(entry)
        document = DocumentVersionMetadata.create(
            document_id=entry.document_id,
            document_family=entry.document_family,
            version_label=entry.version_label,
            source_path=authority_source_path,
            source_sha256=entry.raw_source_sha256,
            declared_effective_from=entry.declared_effective_from,
            declared_effective_to=entry.declared_effective_to,
            role=entry.role,
            authority=entry.authority,
        )
        metadata = ManagedSourceNoteBootstrapMetadata(
            logical_path=entry.logical_path,
            source_note_sha256=entry.source_note_sha256,
            source_note_byte_count=entry.source_note_byte_count,
            source_root_id=entry.source_root_id,
            source_relative_path=entry.source_relative_path,
            source_note_provenance=entry.source_note_provenance,
            raw_source_path=authority_source_path,
            raw_source_sha256=entry.raw_source_sha256,
            raw_source_byte_count=entry.raw_source_byte_count,
            document=document,
        )
        snapshot = CanonicalSourceNoteSnapshot.create(
            document=document,
            source_note_path=entry.logical_path,
            source_note_utf8=exact.content.decode("utf-8"),
            body_start_char=body_start,
        )
        documents.append(document)
        managed_metadata.append(metadata)
        resolved_managed.append(
            ResolvedManagedSourceNote(
                metadata=metadata,
                note=note,
                snapshot=snapshot,
                raw_source_bytes=raw_bytes,
            )
        )
        claim_ids = tuple(claim.id for claim in note.key_claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise WorkspaceBootstrapRepositoryError(
                f"managed SourceNote contains duplicate claim IDs: {entry.logical_path}"
            )
        for claim in sorted(note.key_claims, key=lambda value: value.id):
            claims.append(
                VersionedClaimRevision.create(
                    document=document,
                    source=ClaimSourceReference(
                        source_note_path=entry.logical_path,
                        source_note_sha256=entry.source_note_sha256,
                        source_claim_id=claim.id,
                        evidence=tuple(claim.evidence),
                    ),
                    statement=claim.statement,
                    declared_effective_from=entry.declared_effective_from,
                    declared_effective_to=entry.declared_effective_to,
                    scopes=claim_scopes_v1(
                        document_family=entry.document_family,
                        affects=tuple(claim.affects),
                    ),
                )
            )

    aggregate = ChangeControlAggregate.create(
        aggregate_id=manifest.aggregate_id,
        documents=DocumentVersionRegistry.create(tuple(documents)),
        claims=ClaimRevisionRegistry.create(tuple(claims)),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    inventory = WorkspaceBootstrapInventory.create(
        manifest_schema_version=manifest.schema_version,
        manifest_sha256=_sha256(manifest_bytes),
        vault_members=vault_members,
        managed_source_notes=tuple(managed_metadata),
        legacy_index=legacy_index,
    )
    return ResolvedWorkspaceBootstrap(
        workspace_root=workspace,
        legacy_index_path=workspace / "index.db",
        manifest=manifest,
        manifest_sha256=_sha256(manifest_bytes),
        inventory=inventory,
        aggregate=aggregate,
        exact_vault_notes=exact_notes,
        managed_source_notes=tuple(resolved_managed),
        source_roots=verified_roots,
    )


@dataclass
class WorkspaceBootstrapEvidenceGuard:
    """Live no-follow guard spanning workspace verification and authority commit."""

    resolved: ResolvedWorkspaceBootstrap
    _manifest_path: Path
    _index_schema_version: int
    _embedding_model: str
    _embedding_dimensions: int
    _workspace: _PinnedWorkspace
    _source_roots: tuple[tuple[str, _PinnedWorkspace], ...]
    _files: tuple[_PinnedEvidenceFile, ...]

    def __enter__(self) -> Self:
        self.verify()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    def _verify_pins(self) -> None:
        self._workspace.verify()
        roots = {"workspace": self._workspace, **dict(self._source_roots)}
        for source_root in roots.values():
            source_root.verify()
        for item in self._files:
            root = roots[item.root_id]
            try:
                opened = os.fstat(item.file_fd)
            except OSError as exc:
                raise WorkspaceBootstrapRepositoryError(
                    f"workspace evidence guard is unavailable: {item.relative_path}"
                ) from exc
            if _stable_signature(opened) != item.signature:
                raise WorkspaceBootstrapRepositoryError(
                    f"workspace evidence changed while guarded: {item.relative_path}"
                )
            digest, byte_count = _hash_pinned_file(
                item.file_fd,
                limit=item.limit,
                label=f"guarded workspace evidence {item.relative_path}",
            )
            if (digest, byte_count) != (item.sha256, item.byte_count):
                raise WorkspaceBootstrapRepositoryError(
                    f"workspace evidence bytes changed while guarded: {item.relative_path}"
                )
            opener = _open_pinned_source_file if item.source_path else _open_workspace_regular_file
            current_fd = opener(
                root,
                item.relative_path,
                label=f"guarded bootstrap evidence {item.relative_path}",
            )
            try:
                current = os.fstat(current_fd)
            finally:
                os.close(current_fd)
            if _stable_signature(current) != item.signature:
                raise WorkspaceBootstrapRepositoryError(
                    f"workspace evidence path was substituted: {item.relative_path}"
                )
        self._workspace.verify()
        for source_root in roots.values():
            source_root.verify()

    def verify(self) -> None:
        """Reopen the closed inventory and prove every retained inode still matches."""

        self._verify_pins()
        fresh = resolve_workspace_bootstrap(
            workspace_root=self.resolved.workspace_root,
            manifest_path=self._manifest_path,
            source_roots=self.resolved.source_roots,
            index_schema_version=self._index_schema_version,
            embedding_model=self._embedding_model,
            embedding_dimensions=self._embedding_dimensions,
        )
        if fresh != self.resolved:
            raise WorkspaceBootstrapRepositoryError(
                "workspace bootstrap evidence drifted during its authority handoff"
            )
        self._verify_pins()

    def close(self) -> None:
        for item in self._files:
            item.close()
        for _root_id, source_root in self._source_roots:
            source_root.close()
        self._workspace.close()


def open_workspace_bootstrap_evidence_guard(
    *,
    workspace_root: Path,
    manifest_path: Path,
    source_roots: tuple[BootstrapSourceRoot, ...] = (),
    index_schema_version: int,
    embedding_model: str,
    embedding_dimensions: int,
) -> WorkspaceBootstrapEvidenceGuard:
    """Resolve exact evidence and retain all source descriptors through handoff."""

    resolved = resolve_workspace_bootstrap(
        workspace_root=workspace_root,
        manifest_path=manifest_path,
        source_roots=source_roots,
        index_schema_version=index_schema_version,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )
    try:
        manifest_relative = manifest_path.relative_to(resolved.workspace_root).as_posix()
    except ValueError as exc:
        raise WorkspaceBootstrapRepositoryError(
            "bootstrap manifest is outside the guarded workspace"
        ) from exc

    expected: dict[tuple[str, str], tuple[str, int, int, bool]] = {}

    def add(
        root_id: str,
        relative: str,
        content: bytes,
        limit: int,
        *,
        source_path: bool = False,
    ) -> None:
        identity = (_sha256(content), len(content), limit, source_path)
        key = (root_id, relative)
        existing = expected.get(key)
        if existing is not None and existing[:2] != identity[:2]:
            raise WorkspaceBootstrapRepositoryError(
                f"workspace evidence path has conflicting exact bytes: {relative}"
            )
        if existing is None or limit > existing[2]:
            expected[key] = identity

    expected[("workspace", manifest_relative)] = (
        resolved.manifest_sha256,
        -1,
        MAX_BOOTSTRAP_MANIFEST_BYTES_V1,
        False,
    )
    for note in resolved.exact_vault_notes:
        add(
            "workspace",
            f"vault/{note.rel_path}",
            note.content,
            MAX_WORKSPACE_MEMBER_BYTES_V1,
        )
        for supporting in note.supporting_files:
            add(
                "workspace",
                supporting.rel_path,
                supporting.content,
                MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
            )
    for managed in resolved.managed_source_notes:
        add(
            managed.metadata.source_root_id,
            managed.metadata.source_relative_path,
            managed.raw_source_bytes,
            MAX_WORKSPACE_RAW_SOURCE_BYTES_V1,
            source_path=True,
        )

    workspace = _pin_workspace(resolved.workspace_root)
    pinned_source_roots: list[tuple[str, _PinnedWorkspace]] = []
    files: list[_PinnedEvidenceFile] = []
    try:
        for source_root in resolved.source_roots:
            pinned_source_roots.append((source_root.root_id, _pin_workspace(source_root.path)))
        roots = {"workspace": workspace, **dict(pinned_source_roots)}
        for (root_id, relative), (
            expected_sha,
            expected_count,
            limit,
            source_path,
        ) in sorted(expected.items()):
            opener = _open_pinned_source_file if source_path else _open_workspace_regular_file
            file_fd = opener(
                roots[root_id],
                relative,
                label=f"bootstrap evidence {relative}",
            )
            try:
                signature = _stable_signature(os.fstat(file_fd))
                actual_sha, actual_count = _hash_pinned_file(
                    file_fd,
                    limit=limit,
                    label=f"workspace evidence {relative}",
                )
                if actual_sha != expected_sha or (
                    expected_count >= 0 and actual_count != expected_count
                ):
                    raise WorkspaceBootstrapRepositoryError(
                        f"workspace evidence differs while being guarded: {relative}"
                    )
                files.append(
                    _PinnedEvidenceFile(
                        root_id=root_id,
                        relative_path=relative,
                        file_fd=file_fd,
                        signature=signature,
                        sha256=actual_sha,
                        byte_count=actual_count,
                        limit=limit,
                        source_path=source_path,
                    )
                )
                file_fd = -1
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        guard = WorkspaceBootstrapEvidenceGuard(
            resolved=resolved,
            _manifest_path=manifest_path,
            _index_schema_version=index_schema_version,
            _embedding_model=embedding_model,
            _embedding_dimensions=embedding_dimensions,
            _workspace=workspace,
            _source_roots=tuple(pinned_source_roots),
            _files=tuple(files),
        )
        guard.verify()
        workspace = _PinnedWorkspace(Path(), -1, (0, 0, 0, 0, 0, 0))
        pinned_source_roots = []
        files = []
        return guard
    finally:
        for item in files:
            item.close()
        for _root_id, pinned_root in pinned_source_roots:
            pinned_root.close()
        workspace.close()


__all__ = [
    "ResolvedManagedSourceNote",
    "ResolvedWorkspaceBootstrap",
    "BootstrapSourceRoot",
    "WorkspaceBootstrapEvidenceGuard",
    "WorkspaceBootstrapManifest",
    "WorkspaceBootstrapManifestError",
    "WorkspaceBootstrapPlatformUnsupportedError",
    "WorkspaceBootstrapRepositoryError",
    "WorkspaceManagedSourceManifestEntry",
    "open_workspace_bootstrap_evidence_guard",
    "resolve_workspace_bootstrap",
]
