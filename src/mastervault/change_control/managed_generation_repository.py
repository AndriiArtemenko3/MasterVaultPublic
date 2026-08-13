"""Create-only generation publication and isolated SQLite index repository."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, SupportsIndex

from mastervault.change_control.inference_repository import (
    MAX_PENDING_FILES_PER_DIRECTORY_V1,
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceConflictError,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.managed_generation import (
    INDEX_COUNT_KEYS_V1,
    MAX_INDEX_COUNTS_V1,
    MAX_INDEX_FILE_BYTES_V1,
    GenerationSourceNoteEntry,
    ManagedActivationCommand,
    ManagedIndexReadinessReceipt,
    ManagedPublicationEvent,
)
from mastervault.change_control.managed_review import (
    GenerationPublicationBinding,
    GenerationZeroOriginBasis,
    WorkspaceGenerationZeroOriginBasis,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    require_exact_repository_path,
    verified_repository_root,
)
from mastervault.providers import EmbeddingProvider
from mastervault.storage.base import SCHEMA_VERSION, StorageError
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import (
    ExactSourceNoteInput,
    prepare_exact_source_notes,
    sync_exact_source_notes,
)


class ManagedGenerationRepositoryError(RuntimeError):
    """Base failure at the dedicated generation repository boundary."""


class ManagedGenerationRepositoryConflictError(ManagedGenerationRepositoryError):
    """An immutable generation locator already contains different evidence."""


class ManagedGenerationIndexError(ManagedGenerationRepositoryError):
    """A generation index is incomplete, corrupt, or does not match authority."""


@dataclass(frozen=True)
class ResolvedGenerationSourceNote:
    entry: GenerationSourceNoteEntry
    content: bytes
    workspace: Path


@dataclass(frozen=True)
class BuiltManagedIndex:
    receipt: ManagedIndexReadinessReceipt
    index_path: Path


_EFFECT_CAPABILITY_TOKEN = object()
_EFFECT_CAPABILITY_SECRET = os.urandom(32)
_INDEX_COMPLETION_META_KEY = "managed_generation_completion_v1"
_MAX_INDEX_READY_RECEIPT_BYTES_V1 = 64 * 1024


def _inode_signature(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _stable_file_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


@dataclass
class _PinnedIndexFile:
    """One index inode and its exact repository parent held through a critical section."""

    parent_fd: int
    file_fd: int
    name: str
    relative: str
    path: Path
    sealed: bool

    def close(self) -> None:
        file_fd, parent_fd = self.file_fd, self.parent_fd
        self.file_fd = -1
        self.parent_fd = -1
        if file_fd >= 0:
            os.close(file_fd)
        if parent_fd >= 0:
            os.close(parent_fd)

    def duplicate(self) -> _PinnedIndexFile:
        if self.file_fd < 0 or self.parent_fd < 0:
            raise ManagedGenerationIndexError("managed index guard is already closed")
        return _PinnedIndexFile(
            parent_fd=os.dup(self.parent_fd),
            file_fd=os.dup(self.file_fd),
            name=self.name,
            relative=self.relative,
            path=self.path,
            sealed=self.sealed,
        )

    def verify_entry(self, *, allow_empty: bool) -> os.stat_result:
        if self.file_fd < 0 or self.parent_fd < 0:
            raise ManagedGenerationIndexError("managed index guard is already closed")
        try:
            opened = os.fstat(self.file_fd)
            current = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite index path changed while pinned"
            ) from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_uid != os.getuid()
            or current.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != (0o400 if self.sealed else 0o600)
            or stat.S_IMODE(current.st_mode) != (0o400 if self.sealed else 0o600)
            or _inode_signature(opened) != _inode_signature(current)
            or (not allow_empty and (opened.st_size <= 0 or current.st_size <= 0))
            or opened.st_size > MAX_INDEX_FILE_BYTES_V1
            or current.st_size > MAX_INDEX_FILE_BYTES_V1
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite index is not the exact private pinned inode"
            )
        return opened

    def exact_bytes(self, *, allow_empty: bool = False) -> bytes:
        before = self.verify_entry(allow_empty=allow_empty)
        try:
            content = bytearray()
            offset = 0
            while True:
                block = os.pread(self.file_fd, 1024 * 1024, offset)
                if not block:
                    break
                content.extend(block)
                offset += len(block)
                if len(content) > MAX_INDEX_FILE_BYTES_V1:
                    raise ManagedGenerationIndexError(
                        "managed SQLite index exceeds its fixed size limit"
                    )
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite index cannot be read through its pinned inode"
            ) from exc
        after = self.verify_entry(allow_empty=allow_empty)
        if (
            _stable_file_signature(before) != _stable_file_signature(after)
            or after.st_size != len(content)
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite index changed during pinned reading"
            )
        return bytes(content)

    def sha256(self) -> tuple[str, int]:
        before = self.verify_entry(allow_empty=False)
        digest = hashlib.sha256()
        offset = 0
        try:
            while True:
                block = os.pread(self.file_fd, 1024 * 1024, offset)
                if not block:
                    break
                digest.update(block)
                offset += len(block)
                if offset > MAX_INDEX_FILE_BYTES_V1:
                    raise ManagedGenerationIndexError(
                        "managed SQLite index exceeds its fixed size limit"
                    )
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite index cannot be hashed through its pinned inode"
            ) from exc
        after = self.verify_entry(allow_empty=False)
        if _stable_file_signature(before) != _stable_file_signature(after) or (
            after.st_size != offset
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite index changed during pinned hashing"
            )
        return digest.hexdigest(), offset

    def reset(self) -> None:
        before = self.verify_entry(allow_empty=True)
        try:
            os.fchmod(self.file_fd, 0o600)
            self.sealed = False
            os.ftruncate(self.file_fd, 0)
            os.lseek(self.file_fd, 0, os.SEEK_SET)
            os.fsync(self.file_fd)
            os.fsync(self.parent_fd)
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "unsealed managed SQLite index could not be reset"
            ) from exc
        after = self.verify_entry(allow_empty=True)
        if _inode_signature(before) != _inode_signature(after) or after.st_size != 0:
            raise ManagedGenerationIndexError(
                "unsealed managed SQLite index changed during exact reset"
            )

    def write_image(self, image: bytes) -> None:
        if not image or len(image) > MAX_INDEX_FILE_BYTES_V1:
            raise ManagedGenerationIndexError("managed SQLite image size is invalid")
        self.reset()
        try:
            view = memoryview(image)
            offset = 0
            while view:
                written = os.pwrite(self.file_fd, view, offset)
                if written <= 0:
                    raise OSError("zero-byte write while persisting managed SQLite image")
                view = view[written:]
                offset += written
            os.fsync(self.file_fd)
            os.fchmod(self.file_fd, 0o400)
            self.sealed = True
            os.fsync(self.file_fd)
            os.fsync(self.parent_fd)
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite image could not be written durably"
            ) from exc
        after = self.verify_entry(allow_empty=False)
        if after.st_size != len(image) or self.exact_bytes() != image:
            raise ManagedGenerationIndexError(
                "managed SQLite image differs after pinned publication"
            )


class _GuardedSqliteBackend(SqliteBackend):
    """Read-only SQLite backend that owns its inode and parent guards."""

    def __init__(self, *, alias: Path, guard: _PinnedIndexFile) -> None:
        self._managed_index_guard: _PinnedIndexFile | None = guard
        try:
            super().__init__(
                alias,
                read_only=True,
                _read_only_uri=alias.as_uri(),
            )
            self.db_path = guard.path
        except Exception:
            self._managed_index_guard = None
            guard.close()
            raise

    def close(self) -> None:
        guard = self._managed_index_guard
        self._managed_index_guard = None
        try:
            super().close()
        finally:
            if guard is not None:
                guard.close()


def _effect_capability_payload(
    *,
    repository_id: str,
    repository_root: str,
    command: ManagedActivationCommand,
    publication_events: tuple[ManagedPublicationEvent, ...],
    index_receipt: ManagedIndexReadinessReceipt | None,
) -> bytes:
    return canonical_json_bytes(
        {
            "namespace": "mastervault.managed-generation-effects-capability.v1",
            "repository_id": repository_id,
            "repository_root": repository_root,
            "activation_id": command.activation_id,
            "activation_sha256": command.activation_sha256,
            "publication_events": [
                {
                    "event_id": event.event_id,
                    "event_sha256": event.event_sha256,
                }
                for event in publication_events
            ],
            "index_receipt": (
                None
                if index_receipt is None
                else {
                    "receipt_id": index_receipt.receipt_id,
                    "receipt_sha256": index_receipt.receipt_sha256,
                }
            ),
        }
    )


@dataclass(frozen=True, eq=False)
class RepositoryVerifiedManagedGenerationEffects:
    """Process-local proof that exact generation effects were reopened.

    The SQLite authority store accepts effect evidence only alongside this
    repository-minted capability. Verification reopens the immutable files,
    so a self-consistent Pydantic receipt alone can never advance authority.
    """

    repository_id: str
    repository_root: str
    activation_id: str
    activation_sha256: str
    publication_event_ids: tuple[str, ...]
    publication_event_sha256s: tuple[str, ...]
    index_receipt_id: str | None
    index_receipt_sha256: str | None
    _repository: ManagedGenerationRepository
    _notes: tuple[ResolvedGenerationSourceNote, ...]
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if self._token is not _EFFECT_CAPABILITY_TOKEN:
            raise TypeError("managed generation effect capabilities are repository-created only")

    def __reduce__(self) -> Any:
        raise TypeError("managed generation effect capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("managed generation effect capabilities are process-local")

    def __getstate__(self) -> Any:
        raise TypeError("managed generation effect capabilities are process-local")

    def verify(
        self,
        *,
        command: ManagedActivationCommand,
        publication_events: tuple[ManagedPublicationEvent, ...],
        index_receipt: ManagedIndexReadinessReceipt | None,
    ) -> None:
        """Authenticate the capability and reopen every supplied effect."""

        if type(self) is not RepositoryVerifiedManagedGenerationEffects:
            raise ManagedGenerationRepositoryError(
                "managed generation effect capability type was substituted"
            )
        if self._token is not _EFFECT_CAPABILITY_TOKEN:
            raise ManagedGenerationRepositoryError(
                "managed generation effect capability was not repository-created"
            )
        if type(self._repository) is not ManagedGenerationRepository:
            raise ManagedGenerationRepositoryError(
                "managed generation effect repository was substituted"
            )
        payload = _effect_capability_payload(
            repository_id=self.repository_id,
            repository_root=self.repository_root,
            command=command,
            publication_events=publication_events,
            index_receipt=index_receipt,
        )
        expected_seal = hmac.new(
            _EFFECT_CAPABILITY_SECRET,
            payload,
            hashlib.sha256,
        ).hexdigest()
        expected_receipt_id = None if index_receipt is None else index_receipt.receipt_id
        expected_receipt_sha = None if index_receipt is None else index_receipt.receipt_sha256
        if (
            self.repository_id != self._repository.repository_id
            or self.repository_root != str(self._repository.root)
            or self.activation_id != command.activation_id
            or self.activation_sha256 != command.activation_sha256
            or command.generation_repository_id != self.repository_id
            or self.publication_event_ids != tuple(event.event_id for event in publication_events)
            or self.publication_event_sha256s
            != tuple(event.event_sha256 for event in publication_events)
            or self.index_receipt_id != expected_receipt_id
            or self.index_receipt_sha256 != expected_receipt_sha
            or not hmac.compare_digest(self._seal, expected_seal)
        ):
            raise ManagedGenerationRepositoryError(
                "managed generation effect capability differs from exact evidence"
            )
        for event in publication_events:
            if event.activation_id != command.activation_id:
                raise ManagedGenerationRepositoryError(
                    "managed publication capability crosses activation identities"
                )
            self._repository.open_publication(event)
        if index_receipt is not None:
            self._repository.verify_index(
                receipt=index_receipt,
                command=command,
                notes=self._notes,
            )


class ManagedGenerationRepository:
    """Dedicated POSIX repository for immutable generations and derived indexes."""

    def __init__(
        self,
        root: Path,
        *,
        forbidden_roots: tuple[Path, ...] = (),
        create: bool = True,
    ) -> None:
        requested = Path(root)
        try:
            forbidden = tuple(item.resolve(strict=True) for item in forbidden_roots)
            parent = requested.parent.resolve(strict=True)
            existing_candidate = (
                (parent / requested.name).resolve(strict=True)
                if os.path.lexists(parent / requested.name)
                else None
            )
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise ManagedGenerationRepositoryError(
                "cannot preflight the dedicated generation repository root"
            ) from exc
        candidate = parent / requested.name

        def inode_chain(path: Path) -> set[tuple[int, int]]:
            return {
                (info.st_dev, info.st_ino)
                for current in (path, *path.parents)
                for info in (current.stat(),)
            }

        candidate_ancestors = inode_chain(existing_candidate or parent)
        existing_candidate_identity = (
            None
            if existing_candidate is None
            else (
                existing_candidate.stat().st_dev,
                existing_candidate.stat().st_ino,
            )
        )
        overlaps_forbidden = any(
            candidate == item
            or candidate.is_relative_to(item)
            or item.is_relative_to(candidate)
            or (item.stat().st_dev, item.stat().st_ino) in candidate_ancestors
            or (
                existing_candidate_identity is not None
                and existing_candidate_identity in inode_chain(item)
            )
            for item in forbidden
        )
        if overlaps_forbidden:
            raise ManagedGenerationRepositoryError(
                "generation repository must be disjoint from every protected root"
            )
        parent_fd = -1
        root_fd = -1
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            parent_fd = os.open(parent, flags)
            names = os.listdir(parent_fd)
            created_root = False
            if requested.name not in names:
                aliases = [name for name in names if name.casefold() == requested.name.casefold()]
                if aliases:
                    raise RepositoryFileBoundaryError(
                        "generation repository root does not use exact case"
                    )
                if not create:
                    raise FileNotFoundError(requested)
                os.mkdir(requested.name, mode=0o700, dir_fd=parent_fd)
                created_root = True
            root_fd = os.open(requested.name, flags, dir_fd=parent_fd)
            root_info = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.getuid()
                or root_info.st_mode & 0o077
            ):
                raise RepositoryFileIntegrityError(
                    "generation repository root is not a private owned directory"
                )
            os.fsync(root_fd)
            if created_root:
                # Make the newly-created root entry durable, not merely its children.
                os.fsync(parent_fd)
            resolved = verified_repository_root(candidate)
            if _inode_signature(resolved.stat()) != _inode_signature(root_info):
                raise RepositoryFileIntegrityError(
                    "generation repository root changed during creation"
                )
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise ManagedGenerationRepositoryError(
                "cannot establish the dedicated generation repository root"
            ) from exc
        finally:
            if root_fd >= 0:
                os.close(root_fd)
            if parent_fd >= 0:
                os.close(parent_fd)
        if resolved != candidate:
            raise ManagedGenerationRepositoryError(
                "generation repository resolved to a substituted location"
            )
        info = resolved.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ManagedGenerationRepositoryError(
                "generation repository must be private and owned by the current user"
            )
        self._root = resolved
        self._signature = (info.st_dev, info.st_ino)
        self._backend = FilesystemInferenceEvidenceRepository(resolved)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def repository_id(self) -> str:
        return self._backend.repository_id

    def _verified_root(self) -> Path:
        try:
            resolved = verified_repository_root(self._root)
            info = resolved.stat()
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise ManagedGenerationRepositoryError(
                "generation repository root is unavailable"
            ) from exc
        if (
            resolved != self._root
            or (info.st_dev, info.st_ino) != self._signature
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ManagedGenerationRepositoryError("generation repository root was substituted")
        return resolved

    @staticmethod
    def publication_relative_path(
        *, generation_id: str, publication: GenerationPublicationBinding
    ) -> str:
        return canonical_repo_relative(
            f"generations/{generation_id}/canonical/{publication.destination.path}"
        )

    @staticmethod
    def index_relative_path(*, generation_id: str) -> str:
        return canonical_repo_relative(f"generations/{generation_id}/index/mastervault.sqlite3")

    @staticmethod
    def index_readiness_relative_path(*, generation_id: str) -> str:
        return canonical_repo_relative(f"generations/{generation_id}/index/READY.json")

    @staticmethod
    def _require_generation_zero_command(command: ManagedActivationCommand) -> None:
        expected = command.expected_authority
        if not (
            isinstance(
                expected.origin_basis,
                (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
            )
            and expected.authority_revision == 0
            and expected.active_generation.generation_number == 0
        ):
            raise ManagedGenerationRepositoryError(
                "PR15 effects support exactly one managed successor from generation zero"
            )

    def _read_index_readiness(
        self,
        *,
        generation_id: str,
    ) -> ManagedIndexReadinessReceipt | None:
        relative = self.index_readiness_relative_path(generation_id=generation_id)
        try:
            exact_data = self._read_optional_exact(
                relative,
                limit=_MAX_INDEX_READY_RECEIPT_BYTES_V1,
                label="managed index readiness receipt",
            )
        except (
            ManagedGenerationRepositoryError,
            InferenceEvidenceRepositoryError,
            RepositoryFileBoundaryError,
            RepositoryFileIntegrityError,
            OSError,
        ) as exc:
            raise ManagedGenerationIndexError(
                "managed index readiness receipt cannot be reopened"
            ) from exc
        if exact_data is None:
            return None
        try:
            receipt = ManagedIndexReadinessReceipt.model_validate_json(exact_data)
        except ValueError as exc:
            raise ManagedGenerationIndexError("managed index readiness receipt is invalid") from exc
        if canonical_json_bytes(receipt.model_dump(mode="json")) != exact_data:
            raise ManagedGenerationIndexError("managed index readiness receipt is not canonical")
        return receipt

    @staticmethod
    def _index_completion_marker(command: ManagedActivationCommand) -> dict[str, Any]:
        return {
            "namespace": "mastervault.managed-sqlite-completion.v1",
            "activation_id": command.activation_id,
            "generation_id": command.projection.generation_id,
            "manifest_sha256": command.manifest_sha256,
            "projection_id": command.projection.projection_id,
            "projection_sha256": command.projection.projection_sha256,
            "serving_content_fingerprint": (command.projection.serving_content_fingerprint),
            "embedding_model_version": command.embedding_model_version,
            "embedding_dimensions": command.embedding_dimensions,
            "storage_schema_version": SCHEMA_VERSION,
        }

    @classmethod
    def _completed_index_marker(
        cls,
        *,
        command: ManagedActivationCommand,
        logical_content_fingerprint: str,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        if len(logical_content_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in logical_content_fingerprint
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite logical content fingerprint is invalid"
            )
        if tuple(sorted(counts)) != INDEX_COUNT_KEYS_V1:
            raise ManagedGenerationIndexError("managed SQLite completion counts are incomplete")
        return {
            **cls._index_completion_marker(command),
            "logical_content_fingerprint": logical_content_fingerprint,
            "counts": [{"name": name, "count": counts[name]} for name in INDEX_COUNT_KEYS_V1],
        }

    @classmethod
    def _validate_completed_index_marker(
        cls,
        marker: dict[str, Any],
        *,
        command: ManagedActivationCommand,
    ) -> tuple[str, dict[str, int]]:
        base = cls._index_completion_marker(command)
        if {key: marker.get(key) for key in base} != base or set(marker) != {
            *base,
            "logical_content_fingerprint",
            "counts",
        }:
            raise ManagedGenerationIndexError(
                "managed SQLite index belongs to different activation inputs"
            )
        fingerprint = marker["logical_content_fingerprint"]
        count_rows = marker["counts"]
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
            or not isinstance(count_rows, list)
            or any(
                not isinstance(row, dict)
                or set(row) != {"name", "count"}
                or not isinstance(row["name"], str)
                or type(row["count"]) is not int
                or row["count"] < 0
                or row["count"] > MAX_INDEX_COUNTS_V1
                for row in count_rows
            )
        ):
            raise ManagedGenerationIndexError("managed SQLite completion marker is invalid")
        counts = {str(row["name"]): int(row["count"]) for row in count_rows}
        if (
            len(counts) != len(count_rows)
            or tuple(row["name"] for row in count_rows) != INDEX_COUNT_KEYS_V1
            or tuple(sorted(counts)) != INDEX_COUNT_KEYS_V1
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite completion marker counts are incomplete"
            )
        return fingerprint, counts

    @classmethod
    def _read_index_completion_marker(cls, backend: SqliteBackend) -> dict[str, Any] | None:
        has_meta = backend.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if has_meta is None:
            return None
        row = backend.conn.execute(
            "SELECT value FROM meta WHERE key=?",
            (_INDEX_COMPLETION_META_KEY,),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row[0]))
        except (TypeError, ValueError) as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite completion marker is malformed"
            ) from exc
        if not isinstance(value, dict):
            raise ManagedGenerationIndexError("managed SQLite completion marker must be one object")
        return value

    @classmethod
    def _write_index_completion_marker(
        cls,
        backend: SqliteBackend,
        *,
        marker: dict[str, Any],
    ) -> None:
        current = cls._read_index_completion_marker(backend)
        if current is not None and current != marker:
            raise ManagedGenerationIndexError(
                "managed SQLite completion marker belongs to different inputs"
            )
        if current is None:
            with backend.conn:
                backend.conn.execute(
                    "INSERT INTO meta(key,value) VALUES (?,?)",
                    (
                        _INDEX_COMPLETION_META_KEY,
                        json.dumps(marker, sort_keys=True, separators=(",", ":")),
                    ),
                )

    def _read_optional_exact(
        self,
        relative: str,
        *,
        limit: int,
        label: str,
    ) -> bytes | None:
        """Read one repository member through held no-follow parent and leaf descriptors."""

        relative = canonical_repo_relative(relative)
        self._verified_root()
        parent_fd = -1
        file_fd = -1
        try:
            parent_fd, name = self._backend._open_parent(relative, create=False)
        except FileNotFoundError:
            return None
        except (
            RepositoryFileBoundaryError,
            RepositoryFileIntegrityError,
        ):
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ManagedGenerationRepositoryError(f"cannot resolve {label}: {relative}") from exc
        try:
            names = os.listdir(parent_fd)
            if name not in names:
                aliases = [item for item in names if item.casefold() == name.casefold()]
                if aliases:
                    raise RepositoryFileBoundaryError(
                        f"{label} path does not use exact repository case: {relative}"
                    )
                return None
            file_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent_fd,
            )
            before = os.fstat(file_fd)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or before.st_nlink != 1
                or current.st_nlink != 1
                or before.st_uid != os.getuid()
                or current.st_uid != os.getuid()
                or before.st_mode & 0o077
                or current.st_mode & 0o077
                or _inode_signature(before) != _inode_signature(current)
                or before.st_size <= 0
                or before.st_size > limit
                or current.st_size != before.st_size
            ):
                raise ManagedGenerationRepositoryError(
                    f"{label} must be one exact private owned regular inode"
                )
            content = bytearray()
            offset = 0
            while True:
                block = os.pread(file_fd, min(1024 * 1024, limit + 1 - offset), offset)
                if not block:
                    break
                content.extend(block)
                offset += len(block)
                if offset > limit:
                    raise ManagedGenerationRepositoryError(
                        f"{label} exceeds its fixed size limit"
                    )
            after = os.fstat(file_fd)
            current_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                _stable_file_signature(before) != _stable_file_signature(after)
                or _stable_file_signature(after) != _stable_file_signature(current_after)
                or after.st_size != len(content)
            ):
                raise ManagedGenerationRepositoryError(f"{label} changed during exact reading")
            return bytes(content)
        except (
            ManagedGenerationRepositoryError,
            RepositoryFileBoundaryError,
            RepositoryFileIntegrityError,
        ):
            raise
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ManagedGenerationRepositoryError(f"cannot read {label}: {relative}") from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            if parent_fd >= 0:
                os.close(parent_fd)

    def _read_exact(self, relative: str, *, limit: int) -> bytes:
        content = self._read_optional_exact(
            relative,
            limit=limit,
            label="managed generation member",
        )
        if content is None:
            raise ManagedGenerationRepositoryError(
                f"managed generation member is unavailable: {relative}"
            )
        return content

    def publish(
        self,
        *,
        command: ManagedActivationCommand,
        ordinal: int,
        publication: GenerationPublicationBinding,
        content: bytes,
        published_at: str,
    ) -> ManagedPublicationEvent:
        self._require_generation_zero_command(command)
        if len(content) != publication.destination.expected_byte_count or (
            hashlib.sha256(content).hexdigest() != publication.destination.expected_sha256
        ):
            raise ManagedGenerationRepositoryConflictError(
                "publication bytes differ from the reviewed destination"
            )
        relative = self.publication_relative_path(
            generation_id=command.projection.generation_id,
            publication=publication,
        )
        try:
            self._verified_root()
            with self._backend._exclusive_lock():
                self._verified_root()
                self._backend._create_only(
                    relative,
                    content,
                    label="managed generation publication",
                )
        except InferenceEvidenceConflictError as exc:
            raise ManagedGenerationRepositoryConflictError(str(exc)) from exc
        persisted = self._read_exact(relative, limit=len(content))
        if persisted != content:
            raise ManagedGenerationRepositoryConflictError(
                "published generation bytes changed during exact reopening"
            )
        return ManagedPublicationEvent.create(
            activation_id=command.activation_id,
            ordinal=ordinal,
            publication=publication,
            repository_relative_path=relative,
            published_sha256=publication.destination.expected_sha256,
            published_byte_count=publication.destination.expected_byte_count,
            published_at=published_at,
        )

    def open_publication(self, event: ManagedPublicationEvent) -> bytes:
        content = self._read_exact(
            event.repository_relative_path,
            limit=event.published_byte_count,
        )
        if len(content) != event.published_byte_count or hashlib.sha256(content).hexdigest() != (
            event.published_sha256
        ):
            raise ManagedGenerationRepositoryConflictError(
                "published generation member differs from its immutable event"
            )
        return content

    def verify_effects(
        self,
        *,
        command: ManagedActivationCommand,
        publication_events: tuple[ManagedPublicationEvent, ...],
        index_receipt: ManagedIndexReadinessReceipt | None,
        notes: tuple[ResolvedGenerationSourceNote, ...] = (),
    ) -> RepositoryVerifiedManagedGenerationEffects:
        """Reopen exact effects and mint process-local store authority."""

        self._require_generation_zero_command(command)
        if command.generation_repository_id != self.repository_id:
            raise ManagedGenerationRepositoryError(
                "activation command belongs to another generation repository"
            )
        for event in publication_events:
            if event.activation_id != command.activation_id:
                raise ManagedGenerationRepositoryError(
                    "managed publication event belongs to another activation"
                )
            self.open_publication(event)
        if index_receipt is not None:
            self.verify_index(
                receipt=index_receipt,
                command=command,
                notes=notes,
            )
        payload = _effect_capability_payload(
            repository_id=self.repository_id,
            repository_root=str(self.root),
            command=command,
            publication_events=publication_events,
            index_receipt=index_receipt,
        )
        capability = RepositoryVerifiedManagedGenerationEffects(
            repository_id=self.repository_id,
            repository_root=str(self.root),
            activation_id=command.activation_id,
            activation_sha256=command.activation_sha256,
            publication_event_ids=tuple(event.event_id for event in publication_events),
            publication_event_sha256s=tuple(event.event_sha256 for event in publication_events),
            index_receipt_id=(None if index_receipt is None else index_receipt.receipt_id),
            index_receipt_sha256=(None if index_receipt is None else index_receipt.receipt_sha256),
            _repository=self,
            _notes=notes,
            _token=_EFFECT_CAPABILITY_TOKEN,
            _seal=hmac.new(
                _EFFECT_CAPABILITY_SECRET,
                payload,
                hashlib.sha256,
            ).hexdigest(),
        )
        capability.verify(
            command=command,
            publication_events=publication_events,
            index_receipt=index_receipt,
        )
        return capability

    def _ensure_index_file(self, relative: str) -> tuple[Path, bool]:
        relative = canonical_repo_relative(relative)
        self._verified_root()
        parent_fd, name = self._backend._open_parent(relative, create=True)
        created = False
        try:
            names = os.listdir(parent_fd)
            if name not in names:
                aliases = [item for item in names if item.casefold() == name.casefold()]
                if aliases:
                    raise ManagedGenerationRepositoryConflictError(
                        "managed index path has a case-colliding alias"
                    )
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent_fd)
                created = True
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ManagedGenerationRepositoryConflictError(
                    "managed index locator is not one private owned regular inode"
                )
        finally:
            os.close(parent_fd)
        try:
            path = require_exact_repository_path(
                repo_root=self._verified_root(),
                relative=relative,
                label="managed SQLite index",
            )
        except (
            RepositoryFileBoundaryError,
            RepositoryFileIntegrityError,
            OSError,
        ) as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite index cannot be resolved exactly"
            ) from exc
        return path, created

    def _open_pinned_index(
        self,
        relative: str,
        *,
        writable: bool,
    ) -> _PinnedIndexFile:
        """Resolve and hold one exact index through no-follow repository dirfds."""

        relative = canonical_repo_relative(relative)
        self._verified_root()
        parent_fd = -1
        file_fd = -1
        writable_fd = -1
        try:
            parent_fd, name = self._backend._open_parent(relative, create=False)
            names = os.listdir(parent_fd)
            if name not in names:
                aliases = [item for item in names if item.casefold() == name.casefold()]
                if aliases:
                    raise ManagedGenerationIndexError(
                        "managed index path has a case-colliding alias"
                    )
                raise FileNotFoundError(relative)
            read_flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            file_fd = os.open(name, read_flags, dir_fd=parent_fd)
            initial = os.fstat(file_fd)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(initial.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or initial.st_nlink != 1
                or current.st_nlink != 1
                or initial.st_uid != os.getuid()
                or current.st_uid != os.getuid()
                or initial.st_mode & 0o077
                or current.st_mode & 0o077
                or _inode_signature(initial) != _inode_signature(current)
            ):
                raise ManagedGenerationIndexError(
                    "managed index locator is not one private owned regular inode"
                )
            if writable:
                # A crash after image sealing but before READY may leave an unsealed
                # 0400 inode. Restore owner-write only after READY absence is known.
                os.fchmod(file_fd, 0o600)
                writable_fd = os.open(
                    name,
                    os.O_RDWR
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent_fd,
                )
                reopened = os.fstat(writable_fd)
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    _inode_signature(reopened) != _inode_signature(initial)
                    or _inode_signature(current) != _inode_signature(initial)
                ):
                    raise ManagedGenerationIndexError(
                        "managed index inode changed while enabling exact rebuild"
                    )
                os.close(file_fd)
                file_fd = writable_fd
                writable_fd = -1
            pinned = _PinnedIndexFile(
                parent_fd=parent_fd,
                file_fd=file_fd,
                name=name,
                relative=relative,
                path=self._root / relative,
                sealed=not writable,
            )
            parent_fd = -1
            file_fd = -1
            pinned.verify_entry(allow_empty=True)
            return pinned
        except ManagedGenerationRepositoryError:
            raise
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite index cannot be pinned exactly"
            ) from exc
        finally:
            if writable_fd >= 0:
                os.close(writable_fd)
            if file_fd >= 0:
                os.close(file_fd)
            if parent_fd >= 0:
                os.close(parent_fd)

    @staticmethod
    def _descriptor_alias(guard: _PinnedIndexFile) -> Path:
        expected = _inode_signature(guard.verify_entry(allow_empty=False))
        for candidate in (
            Path(f"/proc/self/fd/{guard.file_fd}"),
            Path(f"/dev/fd/{guard.file_fd}"),
        ):
            probe_fd = -1
            try:
                probe_fd = os.open(candidate, os.O_RDONLY)
                if _inode_signature(os.fstat(probe_fd)) == expected:
                    return candidate
            except OSError:
                continue
            finally:
                if probe_fd >= 0:
                    os.close(probe_fd)
        raise ManagedGenerationIndexError(
            "platform cannot bind SQLite to the exact managed index descriptor"
        )

    def _open_sqlite_backend(
        self,
        pinned: _PinnedIndexFile,
        *,
        transfer_guard: bool = False,
    ) -> SqliteBackend:
        """Open read-only SQLite on one exact descriptor and retain its guards."""

        owned = pinned if transfer_guard else pinned.duplicate()
        backend: _GuardedSqliteBackend | None = None
        try:
            alias = self._descriptor_alias(owned)
            backend = _GuardedSqliteBackend(alias=alias, guard=owned)
            database_rows = backend.conn.execute("PRAGMA database_list").fetchall()
            main_rows = [row for row in database_rows if str(row[1]) == "main"]
            if len(main_rows) != 1:
                raise ManagedGenerationIndexError(
                    "managed SQLite connection did not open one main database"
                )
            reported = Path(str(main_rows[0][2]))
            opened_signature = self._verify_reported_sqlite_locator(
                reported=reported,
                pinned=owned,
            )
            serialized = backend.conn.serialize(name="main")
            exact = owned.exact_bytes()
            if serialized != exact:
                raise ManagedGenerationIndexError(
                    "managed SQLite connection content differs from the pinned inode"
                )
            if _stable_file_signature(owned.verify_entry(allow_empty=False)) != opened_signature:
                raise ManagedGenerationIndexError(
                    "managed SQLite index changed after its reported locator was verified"
                )
            return backend
        except ManagedGenerationRepositoryError:
            if backend is not None:
                backend.close()
            else:
                owned.close()
            raise
        except (OSError, sqlite3.Error, StorageError, ValueError) as exc:
            if backend is not None:
                backend.close()
            else:
                owned.close()
            raise ManagedGenerationIndexError(
                "managed SQLite index could not be opened on its pinned inode"
            ) from exc

    @staticmethod
    def _verify_reported_sqlite_locator(
        *,
        reported: Path,
        pinned: _PinnedIndexFile,
    ) -> tuple[int, int, int, int, int, int, int]:
        """Bind SQLite's platform-normalized reported locator to the pinned inode."""

        if not reported.is_absolute():
            raise ManagedGenerationIndexError(
                "managed SQLite reported a non-absolute main database locator"
            )
        before = pinned.verify_entry(allow_empty=False)
        reported_fd = -1
        try:
            # Linux may normalize /proc/self/fd/N to /proc/<pid>/fd/N or to the
            # resolved canonical file path. Text is not authority: this
            # nonblocking read-only reopen is accepted only for the exact
            # already-pinned regular inode below.
            reported_fd = os.open(
                reported,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
            )
            reported_info = os.fstat(reported_fd)
        except OSError as exc:
            raise ManagedGenerationIndexError(
                "managed SQLite reported locator cannot be inspected safely"
            ) from exc
        finally:
            if reported_fd >= 0:
                os.close(reported_fd)
        after = pinned.verify_entry(allow_empty=False)
        if (
            not stat.S_ISREG(reported_info.st_mode)
            or _inode_signature(reported_info) != _inode_signature(before)
            or _stable_file_signature(before) != _stable_file_signature(after)
        ):
            raise ManagedGenerationIndexError(
                "managed SQLite reported locator is not the pinned index inode"
            )
        return _stable_file_signature(after)

    def _inspect_unsealed_index_marker(
        self,
        pinned: _PinnedIndexFile,
    ) -> dict[str, Any] | None:
        """Reopen a prior unsealed database before its derived bytes are rebuilt."""

        try:
            if pinned.verify_entry(allow_empty=True).st_size == 0:
                return None
            backend = self._open_sqlite_backend(pinned)
        except (ManagedGenerationIndexError, OSError, sqlite3.Error, StorageError):
            # READY absence is authoritative: partial/corrupt derived bytes are
            # incomplete work and may be discarded. A valid marker is still
            # returned below and must reproduce the exact command before reset.
            return None
        try:
            if [str(row[0]) for row in backend.conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise ManagedGenerationIndexError(
                    "unsealed managed SQLite index failed integrity_check"
                )
            return self._read_index_completion_marker(backend)
        except (ManagedGenerationIndexError, sqlite3.Error, StorageError, TypeError, ValueError):
            return None
        finally:
            backend.close()

    @staticmethod
    def _reset_unsealed_index_file(pinned: _PinnedIndexFile) -> None:
        """Truncate one verified unsealed derived index before exact reconstruction."""

        pinned.reset()

    @staticmethod
    def _reject_index_sidecars(pinned: _PinnedIndexFile) -> None:
        names = os.listdir(pinned.parent_fd)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = f"{pinned.name}{suffix}"
            if sidecar in names or any(name.casefold() == sidecar.casefold() for name in names):
                raise ManagedGenerationIndexError(
                    "managed SQLite index retained an unsafe sidecar"
                )

    def _cleanup_index_readiness_pending(self, *, generation_id: str) -> None:
        relative = self.index_readiness_relative_path(generation_id=generation_id)
        try:
            parent_fd, name = self._backend._open_parent(relative, create=True)
            try:
                self._reconcile_readiness_parent(parent_fd=parent_fd, ready_name=name)
            finally:
                os.close(parent_fd)
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise ManagedGenerationIndexError(
                "managed index readiness residue cannot be reconciled"
            ) from exc

    @staticmethod
    def _reconcile_readiness_parent(*, parent_fd: int, ready_name: str) -> None:
        """Remove only interrupted, not-yet-committed READY link pairs."""

        names = os.listdir(parent_fd)
        pending = sorted(
            item
            for item in names
            if item.startswith("pending-")
            and len(item) == len("pending-") + 32
            and all(character in "0123456789abcdef" for character in item[len("pending-") :])
        )
        if len(pending) > MAX_PENDING_FILES_PER_DIRECTORY_V1:
            raise ManagedGenerationIndexError(
                "managed readiness directory has excessive pending residue"
            )
        ready_info = (
            os.stat(ready_name, dir_fd=parent_fd, follow_symlinks=False)
            if ready_name in names
            else None
        )
        changed = False
        for pending_name in pending:
            pending_info = os.stat(
                pending_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(pending_info.st_mode)
                or pending_info.st_uid != os.getuid()
                or pending_info.st_mode & 0o077
                or pending_info.st_size > _MAX_INDEX_READY_RECEIPT_BYTES_V1
                or pending_info.st_nlink not in (1, 2)
            ):
                raise ManagedGenerationIndexError(
                    "managed readiness directory has unsafe pending residue"
                )
            if pending_info.st_nlink == 2:
                if ready_info is None or _inode_signature(ready_info) != _inode_signature(
                    pending_info
                ):
                    raise ManagedGenerationIndexError(
                        "pending readiness inode has an unknown hard-link target"
                    )
                os.unlink(ready_name, dir_fd=parent_fd)
                ready_info = None
                changed = True
            os.unlink(pending_name, dir_fd=parent_fd)
            changed = True
        if changed:
            os.fsync(parent_fd)
        if ready_info is not None and (
            not stat.S_ISREG(ready_info.st_mode)
            or ready_info.st_nlink != 1
            or ready_info.st_uid != os.getuid()
            or ready_info.st_mode & 0o077
            or ready_info.st_size > _MAX_INDEX_READY_RECEIPT_BYTES_V1
        ):
            raise ManagedGenerationIndexError(
                "managed index readiness receipt is not one private regular inode"
            )

    def _create_index_readiness(
        self,
        *,
        pinned: _PinnedIndexFile,
        relative: str,
        content: bytes,
    ) -> None:
        """Commit READY beside the still-pinned sealed index, create-only."""

        relative = canonical_repo_relative(relative)
        expected_parent, name = relative.rsplit("/", 1)
        index_parent, _index_name = pinned.relative.rsplit("/", 1)
        if expected_parent != index_parent:
            raise ManagedGenerationIndexError(
                "managed readiness receipt is not beside its pinned index"
            )
        self._reconcile_readiness_parent(
            parent_fd=pinned.parent_fd,
            ready_name=name,
        )
        names = os.listdir(pinned.parent_fd)
        if name in names:
            raise ManagedGenerationRepositoryConflictError(
                "managed index readiness receipt already exists"
            )
        if any(item.casefold() == name.casefold() for item in names):
            raise ManagedGenerationRepositoryConflictError(
                "managed index readiness path has a case-colliding alias"
            )
        temporary = f"pending-{secrets.token_hex(16)}"
        fd = -1
        completed = False
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=pinned.parent_fd,
            )
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("zero-byte write while persisting readiness")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            pinned.verify_entry(allow_empty=False)
            os.link(
                temporary,
                name,
                src_dir_fd=pinned.parent_fd,
                dst_dir_fd=pinned.parent_fd,
                follow_symlinks=False,
            )
            pinned.verify_entry(allow_empty=False)
            ready_info = os.stat(name, dir_fd=pinned.parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(ready_info.st_mode)
                or ready_info.st_nlink != 2
                or ready_info.st_uid != os.getuid()
                or ready_info.st_mode & 0o077
                or ready_info.st_size != len(content)
            ):
                raise ManagedGenerationIndexError(
                    "managed index readiness receipt changed during commit"
                )
            os.fsync(pinned.parent_fd)
            os.unlink(temporary, dir_fd=pinned.parent_fd)
            os.fsync(pinned.parent_fd)
            completed = True
            pinned.verify_entry(allow_empty=False)
            committed_info = os.stat(name, dir_fd=pinned.parent_fd, follow_symlinks=False)
            if committed_info.st_nlink != 1:
                raise ManagedGenerationIndexError(
                    "managed index readiness receipt did not commit create-only"
                )
        except ManagedGenerationRepositoryError:
            raise
        except (OSError, InferenceEvidenceRepositoryError) as exc:
            raise ManagedGenerationIndexError(
                "managed index readiness receipt could not be committed"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if not completed:
                with suppress(ManagedGenerationRepositoryError, OSError):
                    self._reconcile_readiness_parent(
                        parent_fd=pinned.parent_fd,
                        ready_name=name,
                    )

    @classmethod
    def _sqlite_schema_rows(cls, backend: SqliteBackend) -> list[list[Any]]:
        """Return the complete application-visible SQLite schema in canonical order."""

        return [
            [str(row[0]), str(row[1]), str(row[2]), None if row[3] is None else str(row[3])]
            for row in backend.conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
            )
        ]

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"sha256": hashlib.sha256(value).hexdigest(), "byte_count": len(value)}
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    @classmethod
    def _index_value(cls, table: str, index: int, value: Any) -> Any:
        if isinstance(value, bytes):
            return cls._json_value(value)
        json_columns = {
            ("documents", 5),
            ("structural_records", 13),
            ("structural_records", 19),
            ("structural_records", 20),
        }
        return cls._json_value(value) if (table, index) in json_columns else value

    @staticmethod
    def _expected_semantic_rows(
        prepared: list[Any],
        *,
        embedding_model_version: str,
    ) -> dict[str, list[list[Any]]]:
        documents = sorted((item.doc for item in prepared), key=lambda row: row.doc_id)
        claims = sorted(
            (row for item in prepared for row in item.claims),
            key=lambda row: row.claim_id,
        )
        chunks = sorted(
            (row for item in prepared for row in item.chunks),
            key=lambda row: row.chunk_id,
        )
        aliases = sorted(
            (
                (row.alias, row.wiki_slug, row.domain, item.doc.doc_id)
                for item in prepared
                for row in item.aliases
            ),
            key=lambda row: (row[0], row[1]),
        )
        structural = sorted(
            (row for item in prepared for row in item.structural),
            key=lambda row: row.record_id,
        )
        units = sorted(
            (unit for item in prepared for unit in item.units),
            key=lambda unit: unit.record_id,
        )
        return {
            "documents": [
                [
                    row.doc_id,
                    row.doc_type,
                    row.domain,
                    row.rel_path,
                    row.title,
                    row.frontmatter,
                    row.body,
                    row.content_hash,
                ]
                for row in documents
            ],
            "claims": [
                [
                    row.claim_id,
                    row.doc_id,
                    row.ordinal,
                    row.statement,
                    row.confidence,
                    row.content_hash,
                ]
                for row in claims
            ],
            "claim_affects": [
                [claim.claim_id, slug]
                for claim, slug in sorted(
                    ((claim, slug) for claim in claims for slug in dict.fromkeys(claim.affects)),
                    key=lambda item: (item[0].claim_id, item[1]),
                )
            ],
            "wiki_aliases": [list(row) for row in aliases],
            "chunks": [
                [row.chunk_id, row.doc_id, row.ordinal, row.text, row.content_hash]
                for row in chunks
            ],
            "embeddings": [
                [
                    unit.record_id,
                    unit.record_type,
                    unit.doc_id,
                    unit.domain,
                    unit.content_hash,
                    embedding_model_version,
                ]
                for unit in units
            ],
            "structural_records": [
                [
                    row.record_id,
                    row.doc_id,
                    row.ordinal,
                    row.record_kind,
                    row.text,
                    row.asset_sha256,
                    row.parsed_artifact_sha256,
                    row.parser,
                    row.parser_version,
                    row.parser_core_version,
                    row.parser_profile,
                    row.normalization_profile,
                    row.model_identity,
                    row.resource_limits,
                    row.page_number,
                    row.block_id,
                    row.section_id,
                    row.table_id,
                    row.row_id,
                    row.cell_ids,
                    [
                        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                        for item in row.evidence
                    ],
                ]
                for row in structural
            ],
        }

    @classmethod
    def _semantic_index_fingerprint(
        cls,
        backend: SqliteBackend,
        *,
        expected_rows: dict[str, list[list[Any]]],
        expected_paths: tuple[str, ...],
        expected_doc_ids: tuple[str, ...],
        expected_record_ids: tuple[str, ...],
        embedding_model_version: str,
        embedding_dimensions: int,
        expected_schema: list[list[Any]] | None = None,
        omit_completion_marker: bool = False,
    ) -> tuple[str, dict[str, int]]:
        conn = backend.conn
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if [str(row[0]) for row in integrity] != ["ok"]:
            raise ManagedGenerationIndexError("managed SQLite index failed integrity_check")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ManagedGenerationIndexError("managed SQLite index failed foreign_key_check")
        meta = {
            str(row[0]): cls._json_value(row[1])
            for row in conn.execute("SELECT key, value FROM meta ORDER BY key")
            if not (omit_completion_marker and str(row[0]) == _INDEX_COMPLETION_META_KEY)
        }
        if (
            meta.get("schema_version") != SCHEMA_VERSION
            or meta.get("embedding_model") != embedding_model_version
            or meta.get("dimensions") != embedding_dimensions
        ):
            raise ManagedGenerationIndexError("managed SQLite index metadata is mismatched")
        schema = cls._sqlite_schema_rows(backend)
        if expected_schema is not None and schema != expected_schema:
            raise ManagedGenerationIndexError(
                "managed SQLite schema changed during exact generation build"
            )
        row_specs = {
            "documents": (
                "SELECT doc_id,doc_type,domain,rel_path,title,frontmatter,body,content_hash "
                "FROM documents ORDER BY doc_id"
            ),
            "documents_fts": (
                "SELECT rowid,doc_id,title,body FROM documents_fts ORDER BY rowid"
            ),
            "claims": (
                "SELECT claim_id,doc_id,ordinal,statement,confidence,content_hash "
                "FROM claims ORDER BY claim_id"
            ),
            "claims_fts": (
                "SELECT rowid,claim_id,statement FROM claims_fts ORDER BY rowid"
            ),
            "claim_affects": "SELECT claim_id,wiki_slug FROM claim_affects ORDER BY claim_id,wiki_slug",
            "wiki_aliases": "SELECT alias,wiki_slug,domain,doc_id FROM wiki_aliases ORDER BY alias,wiki_slug",
            "chunks": "SELECT chunk_id,doc_id,ordinal,text,content_hash FROM chunks ORDER BY chunk_id",
            "embeddings": (
                "SELECT record_id,record_type,doc_id,domain,content_hash,model_version "
                "FROM embeddings ORDER BY record_id"
            ),
            "structural_records": (
                "SELECT record_id,doc_id,ordinal,record_kind,text,asset_sha256,"
                "parsed_artifact_sha256,parser,parser_version,parser_core_version,"
                "parser_profile,normalization_profile,model_identity,resource_limits,"
                "page_number,block_id,section_id,table_id,row_id,cell_ids,evidence "
                "FROM structural_records ORDER BY record_id"
            ),
            "structural_records_fts": (
                "SELECT rowid,record_id,text FROM structural_records_fts ORDER BY rowid"
            ),
            "vec_records": "SELECT record_id,embedding FROM vec_records ORDER BY record_id",
            "schema_migrations": (
                "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
            ),
        }
        rows: dict[str, list[list[Any]]] = {}
        for name, sql in row_specs.items():
            count = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            if count > MAX_INDEX_COUNTS_V1:
                raise ManagedGenerationIndexError(
                    f"managed index {name} count exceeds its fixed limit"
                )
            rows[name] = [
                [cls._index_value(name, index, value) for index, value in enumerate(tuple(row))]
                for row in conn.execute(sql).fetchall()
            ]
        for name, expected in expected_rows.items():
            if rows.get(name) != expected:
                raise ManagedGenerationIndexError(
                    f"managed index {name} rows differ from exact SourceNote projection"
                )
        documents = rows["documents"]
        if tuple(sorted(str(row[3]) for row in documents)) != expected_paths:
            raise ManagedGenerationIndexError(
                "managed index document paths differ from exact serving inventory"
            )
        if tuple(sorted(str(row[0]) for row in documents)) != expected_doc_ids:
            raise ManagedGenerationIndexError(
                "managed index document IDs differ from exact serving inventory"
            )
        embedding_ids = tuple(str(row[0]) for row in rows["embeddings"])
        vec_ids = tuple(str(row[0]) for row in rows["vec_records"])
        if embedding_ids != expected_record_ids or vec_ids != expected_record_ids:
            raise ManagedGenerationIndexError(
                "managed index embedding/vector coverage is incomplete or surplus"
            )
        fts_expectations = (
            (
                "claims_fts",
                sorted([[str(row[0]), str(row[3])] for row in rows["claims"]]),
                "claim",
            ),
            (
                "documents_fts",
                sorted(
                    [[str(row[0]), str(row[4]), str(row[6])] for row in rows["documents"]]
                ),
                "document",
            ),
            (
                "structural_records_fts",
                sorted(
                    [[str(row[0]), str(row[4])] for row in rows["structural_records"]]
                ),
                "structural",
            ),
        )
        for table, expected_payloads, label in fts_expectations:
            fts_rows = rows[table]
            rowids = [int(row[0]) for row in fts_rows]
            payloads = sorted([[str(value) for value in row[1:]] for row in fts_rows])
            if rowids != list(range(1, len(fts_rows) + 1)) or payloads != expected_payloads:
                raise ManagedGenerationIndexError(
                    f"managed {label} FTS rows are not exact"
                )
        counts = {name: len(value) for name, value in sorted(rows.items())}
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "namespace": "mastervault.managed-sqlite-logical-index.v1",
                    "meta": meta,
                    "schema": schema,
                    "rows": rows,
                }
            )
        ).hexdigest()
        return fingerprint, counts

    def build_index(
        self,
        *,
        command: ManagedActivationCommand,
        notes: tuple[ResolvedGenerationSourceNote, ...],
        embedder: EmbeddingProvider,
        ready_at: str,
    ) -> BuiltManagedIndex:
        self._require_generation_zero_command(command)
        if embedder.name != command.embedding_provider or (
            embedder.model_version != command.embedding_model_version
            or embedder.dimensions != command.embedding_dimensions
        ):
            raise ManagedGenerationIndexError(
                "embedding provider differs from exact activation command"
            )
        if tuple(item.entry for item in notes) != command.projection.entries:
            raise ManagedGenerationIndexError(
                "resolved SourceNotes do not exactly cover the generation projection"
            )
        for item in notes:
            if len(item.content) != item.entry.source_note_byte_count or (
                hashlib.sha256(item.content).hexdigest() != item.entry.source_note_sha256
            ):
                raise ManagedGenerationIndexError(
                    "resolved SourceNote bytes differ from generation projection"
                )
        serving_entries = tuple(item for item in notes if item.entry.included_in_serving_index)
        if not serving_entries:
            raise ManagedGenerationIndexError("managed generation has no CURRENT serving notes")
        if tuple(item.entry.entry_id for item in serving_entries) != (
            command.projection.serving_entry_ids
        ):
            raise ManagedGenerationIndexError(
                "resolved serving notes differ from the exact projection"
            )
        exact_notes = tuple(
            ExactSourceNoteInput(
                rel_path=item.entry.logical_path,
                content=item.content,
                workspace=item.workspace,
            )
            for item in serving_entries
        )
        prepared = prepare_exact_source_notes(exact_notes)
        expected_paths = tuple(sorted(item.entry.logical_path for item in serving_entries))
        expected_doc_ids = tuple(sorted(item.doc.doc_id for item in prepared))
        expected_record_ids = tuple(
            sorted(unit.record_id for item in prepared for unit in item.units)
        )
        expected_rows = self._expected_semantic_rows(
            prepared,
            embedding_model_version=embedder.model_version,
        )
        self._verified_root()
        with self._backend._exclusive_lock():
            self._verified_root()
            relative = self.index_relative_path(generation_id=command.projection.generation_id)
            self._cleanup_index_readiness_pending(
                generation_id=command.projection.generation_id
            )
            ready = self._read_index_readiness(generation_id=command.projection.generation_id)
            if ready is not None:
                if ready.ready_at != ready_at:
                    raise ManagedGenerationIndexError(
                        "managed index readiness timestamp differs on exact retry"
                    )
                path = self.verify_index(
                    receipt=ready,
                    command=command,
                    notes=notes,
                )
                return BuiltManagedIndex(receipt=ready, index_path=path)
            path, _created = self._ensure_index_file(relative)
            pinned = self._open_pinned_index(relative, writable=True)
            build_backend: SqliteBackend | None = None
            try:
                old_marker = self._inspect_unsealed_index_marker(pinned)
                if old_marker is not None:
                    self._validate_completed_index_marker(old_marker, command=command)
                self._reset_unsealed_index_file(pinned)
                # Build the complete database away from every filesystem path.
                # Only a verified serialized image is copied to the pinned inode.
                build_backend = SqliteBackend(":memory:")
                build_backend.init_schema(embedder.dimensions, embedder.model_version)
                expected_schema = self._sqlite_schema_rows(build_backend)
                report = sync_exact_source_notes(
                    exact_notes,
                    build_backend,
                    embedder,
                    force_embeddings=True,
                )
                if report.doc_ids != expected_doc_ids or report.record_ids != expected_record_ids:
                    raise ManagedGenerationIndexError(
                        "managed index sync report differs from exact prepared inventory"
                    )
                # Prove the complete row set before marking a finished build.
                # Only the create-only READY receipt makes that database immutable.
                logical_content_fingerprint, content_counts = self._semantic_index_fingerprint(
                    build_backend,
                    expected_rows=expected_rows,
                    expected_paths=expected_paths,
                    expected_doc_ids=expected_doc_ids,
                    expected_record_ids=expected_record_ids,
                    embedding_model_version=embedder.model_version,
                    embedding_dimensions=embedder.dimensions,
                    expected_schema=expected_schema,
                )
                marker = self._completed_index_marker(
                    command=command,
                    logical_content_fingerprint=logical_content_fingerprint,
                    counts=content_counts,
                )
                self._write_index_completion_marker(build_backend, marker=marker)
                logical_fingerprint, counts = self._semantic_index_fingerprint(
                    build_backend,
                    expected_rows=expected_rows,
                    expected_paths=expected_paths,
                    expected_doc_ids=expected_doc_ids,
                    expected_record_ids=expected_record_ids,
                    embedding_model_version=embedder.model_version,
                    embedding_dimensions=embedder.dimensions,
                    expected_schema=expected_schema,
                )
                if counts != content_counts:
                    raise ManagedGenerationIndexError(
                        "managed index counts changed while sealing readiness"
                    )
                image = build_backend.conn.serialize(name="main")
            except ManagedGenerationRepositoryError:
                raise
            except (sqlite3.Error, StorageError, TypeError, ValueError) as exc:
                raise ManagedGenerationIndexError(
                    "managed SQLite index could not be rebuilt exactly"
                ) from exc
            finally:
                if build_backend is not None:
                    build_backend.close()
            try:
                pinned.write_image(image)
                self._reject_index_sidecars(pinned)
                reopened_backend = self._open_sqlite_backend(pinned)
                try:
                    reopened_marker = self._read_index_completion_marker(reopened_backend)
                    if reopened_marker is None:
                        raise ManagedGenerationIndexError(
                            "sealed managed index lacks its completion marker"
                        )
                    reopened_content_fingerprint, reopened_content_counts = (
                        self._validate_completed_index_marker(
                            reopened_marker,
                            command=command,
                        )
                    )
                    if (
                        reopened_content_fingerprint != logical_content_fingerprint
                        or reopened_content_counts != content_counts
                    ):
                        raise ManagedGenerationIndexError(
                            "serialized managed index completion marker changed"
                        )
                    reopened_fingerprint, reopened_counts = (
                        self._semantic_index_fingerprint(
                            reopened_backend,
                            expected_rows=expected_rows,
                            expected_paths=expected_paths,
                            expected_doc_ids=expected_doc_ids,
                            expected_record_ids=expected_record_ids,
                            embedding_model_version=embedder.model_version,
                            embedding_dimensions=embedder.dimensions,
                            expected_schema=expected_schema,
                        )
                    )
                    if reopened_fingerprint != logical_fingerprint or reopened_counts != counts:
                        raise ManagedGenerationIndexError(
                            "serialized managed index differs from the verified build"
                        )
                finally:
                    reopened_backend.close()

                file_sha, file_size = pinned.sha256()
                receipt = ManagedIndexReadinessReceipt.create(
                    activation_id=command.activation_id,
                    generation_id=command.projection.generation_id,
                    manifest_sha256=command.projection.manifest_sha256,
                    projection_id=command.projection.projection_id,
                    projection_sha256=command.projection.projection_sha256,
                    serving_content_fingerprint=command.projection.serving_content_fingerprint,
                    index_relative_path=relative,
                    index_file_sha256=file_sha,
                    index_file_byte_count=file_size,
                    logical_index_fingerprint=logical_fingerprint,
                    storage_schema_version=SCHEMA_VERSION,
                    embedding_model_version=embedder.model_version,
                    embedding_dimensions=embedder.dimensions,
                    counts=tuple(sorted(counts.items())),
                    ready_at=ready_at,
                )
                receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json"))
                if len(receipt_bytes) > _MAX_INDEX_READY_RECEIPT_BYTES_V1:
                    raise ManagedGenerationIndexError(
                        "managed index readiness receipt exceeds its fixed limit"
                    )
                readiness_relative = self.index_readiness_relative_path(
                    generation_id=command.projection.generation_id
                )
                before_ready = pinned.sha256()
                if before_ready != (file_sha, file_size):
                    raise ManagedGenerationIndexError(
                        "managed index changed before readiness commit"
                    )
                self._create_index_readiness(
                    pinned=pinned,
                    relative=readiness_relative,
                    content=receipt_bytes,
                )
                if pinned.sha256() != before_ready:
                    raise ManagedGenerationIndexError(
                        "managed index changed during readiness commit"
                    )
                reopened = self._read_index_readiness(
                    generation_id=command.projection.generation_id
                )
                if reopened != receipt:
                    raise ManagedGenerationIndexError(
                        "managed index readiness receipt changed after commit"
                    )
            finally:
                pinned.close()
            self.verify_index(receipt=receipt, command=command, notes=notes)
        return BuiltManagedIndex(receipt=receipt, index_path=path)

    def verify_index(
        self,
        *,
        receipt: ManagedIndexReadinessReceipt,
        command: ManagedActivationCommand,
        notes: tuple[ResolvedGenerationSourceNote, ...],
    ) -> Path:
        self._require_generation_zero_command(command)
        if (
            receipt.activation_id != command.activation_id
            or receipt.generation_id != command.projection.generation_id
            or receipt.manifest_sha256 != command.projection.manifest_sha256
            or receipt.projection_id != command.projection.projection_id
            or receipt.projection_sha256 != command.projection.projection_sha256
            or receipt.serving_content_fingerprint != command.projection.serving_content_fingerprint
            or receipt.embedding_model_version != command.embedding_model_version
            or receipt.embedding_dimensions != command.embedding_dimensions
            or receipt.storage_schema_version != SCHEMA_VERSION
        ):
            raise ManagedGenerationIndexError("index receipt differs from activation projection")
        persisted_readiness = self._read_index_readiness(
            generation_id=command.projection.generation_id
        )
        if persisted_readiness != receipt:
            raise ManagedGenerationIndexError(
                "index receipt differs from create-only readiness authority"
            )
        if tuple(item.entry for item in notes) != command.projection.entries:
            raise ManagedGenerationIndexError(
                "resolved SourceNotes do not exactly cover the generation projection"
            )
        for item in notes:
            if len(item.content) != item.entry.source_note_byte_count or (
                hashlib.sha256(item.content).hexdigest() != item.entry.source_note_sha256
            ):
                raise ManagedGenerationIndexError(
                    "resolved SourceNote bytes differ from generation projection"
                )
        serving = tuple(item for item in notes if item.entry.included_in_serving_index)
        prepared = prepare_exact_source_notes(
            tuple(
                ExactSourceNoteInput(
                    rel_path=item.entry.logical_path,
                    content=item.content,
                    workspace=item.workspace,
                )
                for item in serving
            )
        )
        expected_rows = self._expected_semantic_rows(
            prepared,
            embedding_model_version=receipt.embedding_model_version,
        )
        pinned = self._open_pinned_index(receipt.index_relative_path, writable=False)
        backend: SqliteBackend | None = None
        try:
            file_sha, file_size = pinned.sha256()
            if (
                file_sha != receipt.index_file_sha256
                or file_size != receipt.index_file_byte_count
            ):
                raise ManagedGenerationIndexError("ready managed index file changed")
            backend = self._open_sqlite_backend(pinned)
            marker = self._read_index_completion_marker(backend)
            if marker is None:
                raise ManagedGenerationIndexError(
                    "ready managed index lacks its exact completion marker"
                )
            content_fingerprint, marker_counts = self._validate_completed_index_marker(
                marker,
                command=command,
            )
            reopened_content_fingerprint, reopened_content_counts = (
                self._semantic_index_fingerprint(
                    backend,
                    expected_rows=expected_rows,
                    expected_paths=tuple(sorted(item.entry.logical_path for item in serving)),
                    expected_doc_ids=tuple(sorted(item.doc.doc_id for item in prepared)),
                    expected_record_ids=tuple(
                        sorted(unit.record_id for item in prepared for unit in item.units)
                    ),
                    embedding_model_version=receipt.embedding_model_version,
                    embedding_dimensions=receipt.embedding_dimensions,
                    omit_completion_marker=True,
                )
            )
            if (
                reopened_content_fingerprint != content_fingerprint
                or reopened_content_counts != marker_counts
            ):
                raise ManagedGenerationIndexError(
                    "ready managed index differs from its completion marker"
                )
            fingerprint, counts = self._semantic_index_fingerprint(
                backend,
                expected_rows=expected_rows,
                expected_paths=tuple(sorted(item.entry.logical_path for item in serving)),
                expected_doc_ids=tuple(sorted(item.doc.doc_id for item in prepared)),
                expected_record_ids=tuple(
                    sorted(unit.record_id for item in prepared for unit in item.units)
                ),
                embedding_model_version=receipt.embedding_model_version,
                embedding_dimensions=receipt.embedding_dimensions,
            )
            final_sha, final_size = pinned.sha256()
        except ManagedGenerationRepositoryError:
            raise
        except (sqlite3.Error, StorageError, TypeError, ValueError) as exc:
            raise ManagedGenerationIndexError(
                "ready managed SQLite index cannot be reopened exactly"
            ) from exc
        finally:
            if backend is not None:
                backend.close()
            pinned.close()
        if fingerprint != receipt.logical_index_fingerprint or tuple(sorted(counts.items())) != (
            receipt.counts
        ):
            raise ManagedGenerationIndexError("ready managed index logical fingerprint changed")
        if final_sha != receipt.index_file_sha256 or final_size != receipt.index_file_byte_count:
            raise ManagedGenerationIndexError(
                "ready managed index file changed during semantic verification"
            )
        return self._root / receipt.index_relative_path

    def open_read_only_index(self, receipt: ManagedIndexReadinessReceipt) -> SqliteBackend:
        if self._read_index_readiness(generation_id=receipt.generation_id) != receipt:
            raise ManagedGenerationIndexError(
                "active index receipt differs from create-only readiness authority"
            )
        opened_pin = self._open_pinned_index(
            receipt.index_relative_path,
            writable=False,
        )
        pinned: _PinnedIndexFile | None = opened_pin
        backend: SqliteBackend | None = None
        try:
            file_sha, file_size = opened_pin.sha256()
            if (
                file_sha != receipt.index_file_sha256
                or file_size != receipt.index_file_byte_count
            ):
                raise ManagedGenerationIndexError("active managed index file changed")
            backend = self._open_sqlite_backend(opened_pin, transfer_guard=True)
            pinned = None
        except (ManagedGenerationRepositoryError, sqlite3.Error, StorageError, OSError) as exc:
            if backend is not None:
                backend.close()
            if pinned is not None:
                pinned.close()
            raise ManagedGenerationIndexError(
                "active managed index cannot be opened read-only"
            ) from exc
        try:
            query_only = int(backend.conn.execute("PRAGMA query_only").fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError) as exc:
            backend.close()
            raise ManagedGenerationIndexError(
                "active managed index query-only state cannot be verified"
            ) from exc
        if query_only != 1:
            backend.close()
            raise ManagedGenerationIndexError("active managed index is not query-only")
        return backend


__all__ = [
    "BuiltManagedIndex",
    "ManagedGenerationIndexError",
    "ManagedGenerationRepository",
    "ManagedGenerationRepositoryConflictError",
    "ManagedGenerationRepositoryError",
    "RepositoryVerifiedManagedGenerationEffects",
    "ResolvedGenerationSourceNote",
]
