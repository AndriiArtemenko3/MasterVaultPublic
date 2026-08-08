"""Small fail-closed helpers for repository-owned runtime files."""

from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_REPOSITORY_PATH_UTF8_BYTES = 1024


class RepositoryFileBoundaryError(ValueError):
    """A requested runtime path crossed a repository or evaluator boundary."""


class RepositoryFileIntegrityError(ValueError):
    """A repository-owned path or file changed while it was being verified."""


def canonical_repo_relative(value: str) -> str:
    """Return an already-canonical, evaluator-isolated POSIX path."""

    if len(value.encode("utf-8")) > MAX_REPOSITORY_PATH_UTF8_BYTES:
        raise RepositoryFileBoundaryError(
            f"runtime path exceeds {MAX_REPOSITORY_PATH_UTF8_BYTES} UTF-8 bytes"
        )
    candidate = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or not candidate.parts
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or Path(value).is_absolute()
        or bool(Path(value).drive)
        or candidate.as_posix() != value
    ):
        raise RepositoryFileBoundaryError(
            f"runtime path must be canonical repository-relative POSIX, got {value!r}"
        )
    if "golden" in {part.casefold() for part in candidate.parts}:
        raise RepositoryFileBoundaryError("runtime paths cannot enter evaluator gold")
    for part in candidate.parts:
        if part.startswith("."):
            raise RepositoryFileBoundaryError(
                "runtime paths cannot contain dot-prefixed components"
            )
        if any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in part
        ):
            raise RepositoryFileBoundaryError(
                "runtime path components cannot contain whitespace or control characters"
            )
    return value


def verified_repository_root(repo_root: Path) -> Path:
    """Resolve one non-symlink repository directory."""

    if repo_root.is_symlink():
        raise RepositoryFileBoundaryError("repository root cannot be a symlink")
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        raise RepositoryFileIntegrityError(f"repository root is unavailable: {repo_root}") from exc
    if "golden" in {part.casefold() for part in resolved.parts}:
        raise RepositoryFileBoundaryError(
            "repository root cannot resolve within evaluator golden data"
        )
    try:
        info = resolved.stat()
    except OSError as exc:
        raise RepositoryFileIntegrityError(f"repository root is unavailable: {repo_root}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RepositoryFileIntegrityError(f"repository root is not a directory: {resolved}")
    return resolved


def require_exact_repository_path(*, repo_root: Path, relative: str, label: str) -> Path:
    """Resolve a canonical path while rejecting case aliases and symlinks."""

    normalized = PurePosixPath(canonical_repo_relative(relative))
    current = repo_root
    for part in normalized.parts:
        try:
            names = {entry.name for entry in os.scandir(current)}
        except OSError as exc:
            raise RepositoryFileIntegrityError(
                f"cannot inspect {label} path component: {current}"
            ) from exc
        if part not in names:
            case_aliases = sorted(name for name in names if name.casefold() == part.casefold())
            if case_aliases:
                raise RepositoryFileBoundaryError(
                    f"{label} path does not use exact repository case: {relative}"
                )
            raise RepositoryFileIntegrityError(f"{label} is unavailable: {relative}")
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise RepositoryFileIntegrityError(
                f"{label} disappeared while checking its path: {current}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise RepositoryFileBoundaryError(f"{label} path contains a symlink: {current}")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise RepositoryFileIntegrityError(
            f"{label} disappeared before verification: {current}"
        ) from exc
    if resolved != current or not resolved.is_relative_to(repo_root):
        raise RepositoryFileBoundaryError(f"{label} does not resolve to its exact repository path")
    return resolved


def read_regular_file(path: Path, *, limit: int, label: str) -> bytes:
    """Read one stable regular file through a no-follow descriptor."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise RepositoryFileIntegrityError(f"{label} is unavailable: {path}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise RepositoryFileIntegrityError(f"{label} is not a regular file: {path}")
    if before.st_size > limit:
        raise RepositoryFileIntegrityError(f"{label} exceeds fixed {limit}-byte limit")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RepositoryFileIntegrityError(
            f"cannot open {label} without following links: {path}"
        ) from exc
    owned_fd = fd
    try:
        opened = os.fstat(owned_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise RepositoryFileIntegrityError(
                f"{label} changed to a non-regular file before reading: {path}"
            )
        with os.fdopen(owned_fd, "rb") as handle:
            owned_fd = -1
            data = handle.read(limit + 1)
            first_finished = os.fstat(handle.fileno())
            handle.seek(0)
            confirmed = handle.read(limit + 1)
            finished = os.fstat(handle.fileno())
    except RepositoryFileIntegrityError:
        raise
    except OSError as exc:
        raise RepositoryFileIntegrityError(f"cannot read {label}: {path}") from exc
    finally:
        if owned_fd >= 0:
            os.close(owned_fd)
    if len(data) > limit or finished.st_size > limit:
        raise RepositoryFileIntegrityError(f"{label} exceeds fixed {limit}-byte limit")
    try:
        after = path.lstat()
    except OSError as exc:
        raise RepositoryFileIntegrityError(f"{label} disappeared after its verified read") from exc

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
        raise RepositoryFileIntegrityError(f"{label} changed during its verified read")
    return data


def read_repository_file(
    *, repo_root: Path, relative: str, limit: int, label: str
) -> tuple[Path, bytes]:
    path = require_exact_repository_path(
        repo_root=repo_root,
        relative=relative,
        label=label,
    )
    return path, read_regular_file(path, limit=limit, label=label)
