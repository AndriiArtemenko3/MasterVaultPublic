"""Create-only owner for one public synchronous start operation."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.change_application_contracts import ChangeExecutionModeV1
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.synchronous_lifecycle_store_models import (
    SynchronousRunLockAuthorityV1,
)
from mastervault.models import Domain

_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_MAX_COMMAND_BYTES = 64 * 1024
_RUN_LOCK_ROOT = "application/start-commands/run-locks"


class ApplicationStartCommandError(ValueError):
    """Start ownership is absent, conflicting, or corrupt."""


class ApplicationStartCommandConflictError(ApplicationStartCommandError):
    """An operation or run is already owned by different immutable inputs."""


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
        raise ValueError("claimed_at must be canonical UTC with second precision")
    return value


class ApplicationStartCommandV1(_StrictFrozenModel):
    """Path-free immutable pre-provider owner for one exact start request."""

    schema_version: Literal[1] = 1
    command_id: str = Field(pattern=r"^start-command:[0-9a-f]{64}$")
    command_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    run_id: str = Field(pattern=r"^operatorrun:[0-9a-f]{64}$")
    base_authority_id: str = Field(pattern=r"^mauthority:[0-9a-f]{64}$")
    base_authority_revision: Literal[0]
    base_active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    source_byte_count: int = Field(ge=1, le=64 * 1024)
    source_metadata_sha256: str = Field(pattern=SHA256_PATTERN)
    suite_id: str
    suite_version: int = Field(ge=1)
    suite_original_sha256: str = Field(pattern=SHA256_PATTERN)
    suite_original_byte_count: int = Field(ge=1, le=1024 * 1024)
    suite_canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    domain: Domain
    mode: ChangeExecutionModeV1
    replay_bundle_id: str | None = Field(default=None, pattern=r"^change-replay:[0-9a-f]{64}$")
    replay_bundle_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    claimed_at: str

    _claimed = field_validator("claimed_at")(_canonical_utc)

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        if _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("operation_id is not canonical")
        return value

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if (self.replay_bundle_id is None) != (self.replay_bundle_sha256 is None):
            raise ValueError("replay bundle ID and SHA must appear together")
        if self.mode == ChangeExecutionModeV1.REPLAY:
            if self.replay_bundle_id != f"change-replay:{self.replay_bundle_sha256}":
                raise ValueError("REPLAY start requires the exact replay bundle identity")
        elif self.replay_bundle_id is not None:
            raise ValueError("LIVE start cannot bind replay evidence")
        payload = self.model_dump(mode="json", exclude={"command_id", "command_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.command_sha256 != digest or self.command_id != f"start-command:{digest}":
            raise ValueError("start command identity differs from its exact canonical payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        payload = {"schema_version": 1, **values}
        payload = {
            key: value.value if hasattr(value, "value") else value for key, value in payload.items()
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "command_id": f"start-command:{digest}",
                    "command_sha256": digest,
                    **payload,
                }
            )
        )

    @property
    def semantic_key(self) -> tuple[Any, ...]:
        payload = self.model_dump(
            mode="json", exclude={"command_id", "command_sha256", "claimed_at"}
        )
        return tuple(sorted(payload.items()))


class ApplicationStartCommandRepository:
    """Descriptor-safe operation/run uniqueness index with crash repair."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        self._backend = FilesystemInferenceEvidenceRepository(
            root, create=create, read_only=read_only
        )
        self._read_only = read_only
        if create and not read_only:
            try:
                # Establish the shared repository lock before an optional reopen can
                # race another process's first claim.  This creates repository
                # coordination infrastructure only; command evidence remains
                # create-only under ``claim``.
                for attempt in range(3):
                    try:
                        with self._backend._exclusive_lock():  # noqa: SLF001
                            break
                    except InferenceEvidenceRepositoryError as exc:
                        if str(exc) != "cannot acquire evidence repository lock" or attempt == 2:
                            raise
            except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
                raise ApplicationStartCommandError(
                    "start command repository cannot be initialized"
                ) from exc

    @staticmethod
    def _name(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _operation_path(self, operation_id: str) -> str:
        return f"application/start-commands/by-operation/{self._name(operation_id)}.json"

    def _run_path(self, run_id: str) -> str:
        return f"application/start-commands/by-run/{self._name(run_id)}.json"

    def _run_lock_path(self, run_id: str) -> str:
        return f"{_RUN_LOCK_ROOT}/{self._name(run_id)}.lock"

    @staticmethod
    def _lock_signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)

    @staticmethod
    def _require_private_lock(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise ApplicationStartCommandError(
                "start run lock is not one private owned regular inode"
            )

    def _verify_visible_run_lock(
        self,
        relative: str,
        expected: tuple[int, int, int, int, int],
        *,
        message: str,
    ) -> None:
        """Freshly resolve and verify the root-visible lock inode."""

        parent = -1
        fd = -1
        try:
            parent, name = self._backend._open_parent(relative, create=False)  # noqa: SLF001
            names = os.listdir(parent)
            if name not in names:
                raise ApplicationStartCommandError(message)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(name, flags, dir_fd=parent)
            opened = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self._require_private_lock(opened)
            if self._lock_signature(opened) != expected or self._lock_signature(named) != expected:
                raise ApplicationStartCommandError(message)
        except ApplicationStartCommandError:
            raise
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            raise ApplicationStartCommandError(message) from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if parent >= 0:
                os.close(parent)

    def prepare_run_lock_authority(
        self, run_id: str, *, claimed_at: str
    ) -> SynchronousRunLockAuthorityV1:
        """Create/open one candidate inode before SQLite independently binds it."""

        if self._read_only:
            raise ApplicationStartCommandError("read-only start repository rejects run locking")
        if re.fullmatch(r"operatorrun:[0-9a-f]{64}", run_id) is None:
            raise ApplicationStartCommandError("start run ID is invalid")
        parent = -1
        fd = -1
        try:
            relative = self._run_lock_path(run_id)
            parent, name = self._backend._open_parent(relative, create=True)  # noqa: SLF001
            names = os.listdir(parent)
            if name not in names and any(item.casefold() == name.casefold() for item in names):
                raise ApplicationStartCommandError("start run lock path has incorrect case")
            flags = (
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            fd = os.open(name, flags, 0o600, dir_fd=parent)
            opened = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self._require_private_lock(opened)
            if self._lock_signature(opened) != self._lock_signature(named):
                raise ApplicationStartCommandError("start run lock was substituted")
            return SynchronousRunLockAuthorityV1.create(
                run_id=run_id,
                device=opened.st_dev,
                inode=opened.st_ino,
                owner_uid=opened.st_uid,
                file_mode=opened.st_mode,
                link_count=opened.st_nlink,
                claimed_at=claimed_at,
            )
        except ApplicationStartCommandError:
            raise
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            raise ApplicationStartCommandError(
                "start run lock authority cannot be prepared"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if parent >= 0:
                os.close(parent)

    @contextmanager
    def run_lifecycle_lock(
        self, run_id: str, authority: SynchronousRunLockAuthorityV1
    ) -> Iterator[None]:
        """Serialize one run's filesystem/provider saga across processes."""

        if self._read_only:
            raise ApplicationStartCommandError("read-only start repository rejects run locking")
        if re.fullmatch(r"operatorrun:[0-9a-f]{64}", run_id) is None:
            raise ApplicationStartCommandError("start run ID is invalid")
        if type(authority) is not SynchronousRunLockAuthorityV1 or not (
            authority.run_id == run_id
            and authority.relative_locator == self._run_lock_path(run_id)
        ):
            raise ApplicationStartCommandError(
                "start run lock requires its exact durable authority"
            )
        parent = -1
        fd = -1
        lock_acquired = False
        try:
            relative = self._run_lock_path(run_id)
            parent, name = self._backend._open_parent(relative, create=False)  # noqa: SLF001
            names = os.listdir(parent)
            if name not in names:
                if any(item.casefold() == name.casefold() for item in names):
                    raise ApplicationStartCommandError("start run lock path has incorrect case")
                raise ApplicationStartCommandError("start run lock authority is absent")
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(name, flags, dir_fd=parent)
            before = os.fstat(fd)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self._require_private_lock(before)
            expected = (
                authority.device,
                authority.inode,
                authority.file_mode,
                authority.owner_uid,
                authority.link_count,
            )
            if self._lock_signature(before) != expected or self._lock_signature(current) != expected:
                raise ApplicationStartCommandError("start run lock differs from durable authority")
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = os.fstat(fd)
            if self._lock_signature(locked) != expected:
                raise ApplicationStartCommandError("start run lock was substituted")
            self._verify_visible_run_lock(
                relative,
                expected,
                message="start run lock was substituted",
            )
            lock_acquired = True
            try:
                yield
            finally:
                try:
                    finished = os.fstat(fd)
                except OSError as exc:
                    raise ApplicationStartCommandError(
                        "start run lock was substituted during lifecycle execution"
                    ) from exc
                if self._lock_signature(finished) != expected:
                    raise ApplicationStartCommandError(
                        "start run lock was substituted during lifecycle execution"
                    )
                self._verify_visible_run_lock(
                    relative,
                    expected,
                    message="start run lock was substituted during lifecycle execution",
                )
        except ApplicationStartCommandError:
            raise
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if lock_acquired:
                raise
            raise ApplicationStartCommandError("start run lock cannot be acquired") from exc
        finally:
            if fd >= 0:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            if parent >= 0:
                os.close(parent)

    def _read(self, relative: str) -> ApplicationStartCommandV1 | None:
        payload = self._backend._read_optional(  # noqa: SLF001
            relative, limit=_MAX_COMMAND_BYTES, label="application start command"
        )
        if payload is None:
            return None
        try:
            value = ApplicationStartCommandV1.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise ApplicationStartCommandError("start command is invalid") from exc
        if canonical_json_bytes(value.model_dump(mode="json")) != payload:
            raise ApplicationStartCommandError("start command is not canonical")
        return value

    def reopen_operation(self, operation_id: str) -> ApplicationStartCommandV1:
        try:
            with self._backend._read_lock():  # noqa: SLF001
                value = self._read(self._operation_path(operation_id))
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStartCommandError):
                raise
            raise ApplicationStartCommandError("start command cannot be reopened") from exc
        if value is None or value.operation_id != operation_id:
            raise ApplicationStartCommandError("start operation does not exist")
        return value

    def reopen_operation_optional(self, operation_id: str) -> ApplicationStartCommandV1 | None:
        """Return an exact operation owner, or ``None`` only when it is absent."""

        try:
            with self._backend._read_lock():  # noqa: SLF001
                value = self._read(self._operation_path(operation_id))
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStartCommandError):
                raise
            raise ApplicationStartCommandError("start command cannot be reopened") from exc
        if value is not None and value.operation_id != operation_id:
            raise ApplicationStartCommandError("start operation key differs from its owner")
        return value

    def reopen_run(self, run_id: str) -> ApplicationStartCommandV1:
        try:
            with self._backend._read_lock():  # noqa: SLF001
                value = self._read(self._run_path(run_id))
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStartCommandError):
                raise
            raise ApplicationStartCommandError("start run owner cannot be reopened") from exc
        if value is None or value.run_id != run_id:
            raise ApplicationStartCommandError("start run owner does not exist")
        return value

    def claim(self, command: ApplicationStartCommandV1) -> ApplicationStartCommandV1:
        if type(command) is not ApplicationStartCommandV1:
            raise TypeError("start repository requires its exact command type")
        payload = canonical_json_bytes(command.model_dump(mode="json"))
        try:
            with self._backend._exclusive_lock():  # noqa: SLF001
                operation_path = self._operation_path(command.operation_id)
                run_path = self._run_path(command.run_id)
                operation_owner = self._read(operation_path)
                run_owner = self._read(run_path)
                for owner in (operation_owner, run_owner):
                    if owner is not None and owner.semantic_key != command.semantic_key:
                        raise ApplicationStartCommandConflictError(
                            "start operation or run is already bound to different immutable inputs"
                        )
                if operation_owner is None:
                    self._backend._create_only(  # noqa: SLF001
                        operation_path, payload, label="start operation owner"
                    )
                if run_owner is None:
                    self._backend._create_only(  # noqa: SLF001
                        run_path, payload, label="start run owner"
                    )
                reopened_operation = self._read(operation_path)
                reopened_run = self._read(run_path)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, ApplicationStartCommandError):
                raise
            raise ApplicationStartCommandError("start command claim failed") from exc
        if reopened_operation is None or reopened_run is None or reopened_operation != reopened_run:
            raise ApplicationStartCommandError("start command owner did not reopen exactly")
        return reopened_operation


__all__ = [
    "ApplicationStartCommandConflictError",
    "ApplicationStartCommandError",
    "ApplicationStartCommandRepository",
    "ApplicationStartCommandV1",
]
