"""Private locator index for the synchronous application lifecycle.

The index is deliberately non-authoritative.  It only maps a run and stage to
content identities and repository-relative locators.  Callers must reopen and
verify every owning artifact before accepting a resolved value.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    verified_repository_root,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - explicit POSIX gate
    fcntl = None  # type: ignore[assignment]

_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$")
_MAX_INDEX_BYTES = 1024 * 1024
_INDEX_ROOT = "application/lifecycle-index-v1"
_LOCK_NAME = "repository.lock"


class LifecycleEvidenceIndexError(ValueError):
    """Lifecycle locator evidence is unavailable, conflicting, or corrupt."""


class LifecycleEvidenceIndexConflictError(LifecycleEvidenceIndexError):
    """A run/stage locator is already bound to different immutable inputs."""


class LifecycleEvidenceIndexUnsupportedError(LifecycleEvidenceIndexError):
    """The host cannot provide the required filesystem guarantees."""


class LifecycleEvidenceStageV1(StrEnum):
    INCOMING = "incoming"
    TEMPORAL = "temporal"
    IMPACT = "impact"
    PLANNING = "planning"
    BASELINE = "baseline"
    CLASSIFICATION = "classification"
    DEPENDENCY = "dependency"
    ACTIVATION = "activation"


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


class LifecycleEvidenceOwnerV1(_StrictFrozenModel):
    """One path-free pointer to an independently authoritative object."""

    owner_kind: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    owner_id: str
    owner_sha256: str = Field(pattern=SHA256_PATTERN)
    relative_locator: str | None = None

    @field_validator("owner_id")
    @classmethod
    def _owner_id(cls, value: str) -> str:
        if _OWNER_ID_RE.fullmatch(value) is None:
            raise ValueError("owner_id is not a safe canonical identity")
        return value

    @field_validator("relative_locator")
    @classmethod
    def _locator(cls, value: str | None) -> str | None:
        return canonical_repo_relative(value) if value is not None else None


class LifecycleEvidenceIndexV1(_StrictFrozenModel):
    """Canonical manifest-last locator record for one run/stage boundary."""

    schema_version: Literal[1] = 1
    index_id: str = Field(pattern=r"^lifecycle-index:[0-9a-f]{64}$")
    index_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    stage: LifecycleEvidenceStageV1
    owners: tuple[LifecycleEvidenceOwnerV1, ...] = Field(min_length=1, max_length=64)
    recorded_at: str

    @field_validator("recorded_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @field_validator("owners")
    @classmethod
    def _owners(
        cls, value: tuple[LifecycleEvidenceOwnerV1, ...]
    ) -> tuple[LifecycleEvidenceOwnerV1, ...]:
        keys = tuple(
            (item.owner_kind, item.owner_id, item.owner_sha256, item.relative_locator or "")
            for item in value
        )
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("lifecycle owners must be unique and canonically ordered")
        return value

    @model_validator(mode="after")
    def _identity(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"index_id", "index_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.index_sha256 != digest or self.index_id != f"lifecycle-index:{digest}":
            raise ValueError("lifecycle index identity differs from its exact bytes")
        return self

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        stage: LifecycleEvidenceStageV1,
        owners: tuple[LifecycleEvidenceOwnerV1, ...],
        recorded_at: str,
    ) -> Self:
        ordered = tuple(
            sorted(
                owners,
                key=lambda item: (
                    item.owner_kind,
                    item.owner_id,
                    item.owner_sha256,
                    item.relative_locator or "",
                ),
            )
        )
        values: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "stage": stage.value,
            "owners": [item.model_dump(mode="json") for item in ordered],
            "recorded_at": _canonical_utc(recorded_at),
        }
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "index_id": f"lifecycle-index:{digest}",
                    "index_sha256": digest,
                    **values,
                }
            )
        )

    @property
    def semantic_key(self) -> tuple[str, LifecycleEvidenceStageV1, tuple[LifecycleEvidenceOwnerV1, ...]]:
        """Inputs whose exact replay retains the original timestamp and bytes."""

        return self.run_id, self.stage, self.owners


class FilesystemLifecycleEvidenceIndex:
    """Owner-private, create-only, descriptor-pinned lifecycle locator index."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        if type(create) is not bool or type(read_only) is not bool:
            raise TypeError("create and read_only must be exact booleans")
        required = (os.open, os.mkdir, os.stat, os.link, os.unlink)
        if (
            os.name != "posix"
            or fcntl is None
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or any(function not in os.supports_dir_fd for function in required)
        ):
            raise LifecycleEvidenceIndexUnsupportedError(
                "lifecycle evidence index requires POSIX flock and descriptor-relative no-follow IO"
            )
        requested = Path(root)
        if create and not read_only:
            requested.mkdir(mode=0o700, parents=False, exist_ok=True)
        try:
            resolved = verified_repository_root(requested)
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise LifecycleEvidenceIndexError("lifecycle evidence repository is unavailable") from exc
        info = resolved.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise LifecycleEvidenceIndexError("lifecycle evidence repository must be owner-only")
        self._root = resolved
        self._signature = (info.st_dev, info.st_ino)
        self._read_only = read_only
        self._tree_signatures: tuple[tuple[int, int], ...] | None = None
        if create and not read_only:
            self._ensure_tree()
            directory_fd = self._open_index_dir(create=False)
            try:
                with self._lock(directory_fd, exclusive=True):
                    pass
            finally:
                os.close(directory_fd)
        else:
            try:
                directory_fd = self._open_index_dir(create=False, establish=True)
            except LifecycleEvidenceIndexError:
                pass
            else:
                os.close(directory_fd)

    def _verify_root(self) -> None:
        resolved = verified_repository_root(self._root)
        info = resolved.stat()
        if (
            resolved != self._root
            or (info.st_dev, info.st_ino) != self._signature
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise LifecycleEvidenceIndexError("lifecycle evidence repository was substituted")

    @staticmethod
    def _verify_directory(info: os.stat_result, *, label: str) -> tuple[int, int]:
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise LifecycleEvidenceIndexError(f"{label} must be an owner-only directory")
        return info.st_dev, info.st_ino

    def _open_index_dir(self, *, create: bool, establish: bool = False) -> int:
        """Open every directory relative to its verified parent and retain the leaf FD."""

        self._verify_root()
        try:
            current = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise LifecycleEvidenceIndexError("lifecycle evidence root cannot be pinned") from exc
        observed: list[tuple[int, int]] = []
        try:
            for component in _INDEX_ROOT.split("/"):
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                except OSError as exc:
                    raise LifecycleEvidenceIndexError(
                        "lifecycle index directory is unavailable"
                    ) from exc
                os.close(current)
                current = child
                observed.append(
                    self._verify_directory(
                        os.fstat(current),
                        label=f"lifecycle index {component}",
                    )
                )
            signatures = tuple(observed)
            if establish or self._tree_signatures is None:
                self._tree_signatures = signatures
            elif signatures != self._tree_signatures:
                raise LifecycleEvidenceIndexError(
                    "lifecycle index directory inode was substituted"
                )
            result = current
            current = -1
            return result
        finally:
            if current >= 0:
                os.close(current)

    def _ensure_tree(self) -> None:
        directory_fd = self._open_index_dir(create=True, establish=True)
        os.close(directory_fd)

    @staticmethod
    def _file_name(run_id: str, stage: LifecycleEvidenceStageV1) -> str:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run_id is not exact")
        return f"{run_id.removeprefix('operatorrun:')}-{stage.value}.json"

    @contextmanager
    def _lock(self, directory_fd: int, *, exclusive: bool) -> Iterator[None]:
        flags = os.O_RDWR | os.O_NOFOLLOW if exclusive else os.O_RDONLY | os.O_NOFOLLOW
        if exclusive:
            if self._read_only:
                raise LifecycleEvidenceIndexError("read-only lifecycle index rejects mutation")
            flags |= os.O_CREAT
        try:
            fd = os.open(_LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise LifecycleEvidenceIndexError("lifecycle index lock is unavailable") from exc
        try:
            lock_info = os.fstat(fd)
            if (
                not stat.S_ISREG(lock_info.st_mode)
                or lock_info.st_uid != os.getuid()
                or stat.S_IMODE(lock_info.st_mode) & 0o077
                or lock_info.st_nlink != 1
            ):
                raise LifecycleEvidenceIndexError("lifecycle index lock was substituted")
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(
        self,
        directory_fd: int,
        run_id: str,
        stage: LifecycleEvidenceStageV1,
    ) -> LifecycleEvidenceIndexV1 | None:
        name = self._file_name(run_id, stage)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise LifecycleEvidenceIndexError("lifecycle index cannot be opened") from exc
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_nlink != 1
                or before.st_size > _MAX_INDEX_BYTES
            ):
                raise LifecycleEvidenceIndexError("lifecycle index file was substituted")
            chunks: list[bytes] = []
            remaining = _MAX_INDEX_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(fd)

            def signature(item: os.stat_result) -> tuple[int, ...]:
                return (
                    item.st_dev,
                    item.st_ino,
                    item.st_mode,
                    item.st_uid,
                    item.st_nlink,
                    item.st_size,
                    item.st_mtime_ns,
                    item.st_ctime_ns,
                )

            if (
                not payload
                or len(payload) > _MAX_INDEX_BYTES
                or len(payload) != after.st_size
                or signature(before) != signature(after)
            ):
                raise LifecycleEvidenceIndexError("lifecycle index changed during read")
        finally:
            os.close(fd)
        try:
            exact = LifecycleEvidenceIndexV1.model_validate_json(payload)
        except ValueError as exc:
            raise LifecycleEvidenceIndexError("lifecycle index is corrupt") from exc
        if canonical_json_bytes(exact.model_dump(mode="json")) != payload:
            raise LifecycleEvidenceIndexError("lifecycle index bytes are not canonical")
        if exact.run_id != run_id or exact.stage != stage:
            raise LifecycleEvidenceIndexError("lifecycle index key differs from its payload")
        return exact

    def reopen(self, run_id: str, stage: LifecycleEvidenceStageV1) -> LifecycleEvidenceIndexV1:
        """Read one exact index without creating directories, locks, or sidecars."""

        if self._tree_signatures is None:
            raise LifecycleEvidenceIndexError("lifecycle index does not exist")
        directory_fd = self._open_index_dir(create=False)
        try:
            with self._lock(directory_fd, exclusive=False):
                result = self._read(directory_fd, run_id, stage)
                if result is None:
                    raise LifecycleEvidenceIndexError("lifecycle index does not exist")
                return result
        finally:
            os.close(directory_fd)

    def reopen_optional(
        self, run_id: str, stage: LifecycleEvidenceStageV1
    ) -> LifecycleEvidenceIndexV1 | None:
        """Return one exact index, or ``None`` only when that stage is absent."""

        if self._tree_signatures is None:
            return None
        directory_fd = self._open_index_dir(create=False)
        try:
            with self._lock(directory_fd, exclusive=False):
                return self._read(directory_fd, run_id, stage)
        finally:
            os.close(directory_fd)

    def persist(self, value: LifecycleEvidenceIndexV1) -> LifecycleEvidenceIndexV1:
        """Create manifest last; exact semantic retry returns original bytes/time."""

        if type(value) is not LifecycleEvidenceIndexV1:
            raise TypeError("lifecycle index requires its exact frozen model")
        payload = canonical_json_bytes(value.model_dump(mode="json"))
        if len(payload) > _MAX_INDEX_BYTES:
            raise LifecycleEvidenceIndexError("lifecycle index exceeds its fixed byte limit")
        name = self._file_name(value.run_id, value.stage)
        directory_fd = self._open_index_dir(create=False)
        try:
            with self._lock(directory_fd, exclusive=True):
                existing = self._read(directory_fd, value.run_id, value.stage)
                if existing is not None:
                    if existing.semantic_key != value.semantic_key:
                        raise LifecycleEvidenceIndexConflictError(
                            "lifecycle run/stage is already bound to different owner evidence"
                        )
                    return existing
                pending = f"pending-{secrets.token_hex(16)}"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                try:
                    fd = os.open(pending, flags, 0o600, dir_fd=directory_fd)
                    try:
                        offset = 0
                        while offset < len(payload):
                            offset += os.write(fd, payload[offset:])
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.link(
                        pending,
                        name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(pending, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                except FileExistsError:
                    with suppress(FileNotFoundError):
                        os.unlink(pending, dir_fd=directory_fd)
                    existing = self._read(directory_fd, value.run_id, value.stage)
                    if existing is not None and existing.semantic_key == value.semantic_key:
                        return existing
                    raise LifecycleEvidenceIndexConflictError(
                        "lifecycle index appeared with different immutable inputs"
                    ) from None
                except BaseException:
                    with suppress(FileNotFoundError):
                        os.unlink(pending, dir_fd=directory_fd)
                    raise
                reopened = self._read(directory_fd, value.run_id, value.stage)
                if reopened != value:
                    raise LifecycleEvidenceIndexError("lifecycle index did not reopen exactly")
                return reopened
        finally:
            os.close(directory_fd)


__all__ = [
    "FilesystemLifecycleEvidenceIndex",
    "LifecycleEvidenceIndexConflictError",
    "LifecycleEvidenceIndexError",
    "LifecycleEvidenceIndexUnsupportedError",
    "LifecycleEvidenceIndexV1",
    "LifecycleEvidenceOwnerV1",
    "LifecycleEvidenceStageV1",
]
