"""Explicit, transactional acquisition for the optional Docling artifacts."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mastervault.document_intelligence.docling_adapter import (
    DOCLING_ARTIFACT_MANIFEST,
    _artifact_report,
)

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


@dataclass(frozen=True)
class _RepositoryPlan:
    repository: str
    revision: str
    destination: str
    allow_patterns: tuple[str, ...]
    relative_files: tuple[str, ...]


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected: {path} ({exc})") from exc


def _assert_real_directory(path: Path, *, label: str) -> None:
    metadata = _lstat(path, label=label)
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a directory: {path}")


def _assert_directory_chain(path: Path, *, label: str) -> None:
    """Reject symlinks and non-directories in an existing absolute path."""
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {path}")
    current = Path(path.anchor)
    _assert_real_directory(current, label=label)
    for part in path.parts[1:]:
        current /= part
        _assert_real_directory(current, label=label)


def _assert_absent(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"output path cannot be inspected: {path} ({exc})") from exc
    raise ValueError(f"output path must be absent: {path}")


def _output_root(value: Path | str) -> tuple[Path, Path]:
    requested = Path(os.path.abspath(Path(value).expanduser()))
    if requested.name in {"", ".", ".."}:
        raise ValueError(f"output path must name a new directory: {requested}")
    _assert_absent(requested)
    parent = requested.parent
    _assert_directory_chain(parent, label="output parent")
    return requested, parent


def _safe_parts(value: Any, *, label: str, directory_name: bool) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"packaged Docling artifact manifest {label} is invalid")
    if "\\" in value:
        raise RuntimeError(f"packaged Docling artifact manifest {label} is unsafe")
    path = PurePosixPath(value)
    parts = tuple(value.split("/"))
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(_SAFE_NAME.fullmatch(part) is None for part in parts)
        or (directory_name and len(parts) != 1)
    ):
        raise RuntimeError(f"packaged Docling artifact manifest {label} is unsafe")
    return parts


def _repository_plans() -> tuple[_RepositoryPlan, ...]:
    repositories = DOCLING_ARTIFACT_MANIFEST.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise RuntimeError("packaged Docling artifact manifest repositories are invalid")

    plans: list[_RepositoryPlan] = []
    destinations: set[str] = set()
    relative_files: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, dict):
            raise RuntimeError("packaged Docling artifact manifest repository is invalid")
        repository_id = repository.get("repository")
        revision = repository.get("revision")
        destination = repository.get("destination")
        source_files = repository.get("files")
        if (
            not isinstance(repository_id, str)
            or len(_safe_parts(repository_id, label="repository", directory_name=False)) != 2
            or not isinstance(revision, str)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
            or not isinstance(source_files, list)
            or not source_files
        ):
            raise RuntimeError("packaged Docling artifact manifest repository is invalid")

        destination_parts = _safe_parts(
            destination, label="destination", directory_name=True
        )
        safe_destination = destination_parts[0]
        if safe_destination in destinations:
            raise RuntimeError("packaged Docling artifact manifest destination is duplicated")
        destinations.add(safe_destination)

        allow_patterns: list[str] = []
        repository_files: list[str] = []
        for source_file in source_files:
            if not isinstance(source_file, dict):
                raise RuntimeError("packaged Docling artifact manifest file is invalid")
            source_path = source_file.get("source_path")
            source_parts = _safe_parts(
                source_path, label="source path", directory_name=False
            )
            safe_source = PurePosixPath(*source_parts).as_posix()
            relative = PurePosixPath(safe_destination, *source_parts).as_posix()
            if relative in relative_files:
                raise RuntimeError("packaged Docling artifact manifest path is duplicated")
            relative_files.add(relative)
            allow_patterns.append(safe_source)
            repository_files.append(relative)

        plans.append(
            _RepositoryPlan(
                repository=repository_id,
                revision=revision,
                destination=safe_destination,
                allow_patterns=tuple(allow_patterns),
                relative_files=tuple(repository_files),
            )
        )
    return tuple(plans)


def _make_private_directory(path: Path) -> None:
    os.mkdir(path, mode=0o700)
    _assert_real_directory(path, label="private acquisition directory")


def _copy_regular_file(source: Path, destination: Path) -> None:
    source_metadata = _lstat(source, label="downloaded artifact")
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode):
        raise ValueError(f"downloaded artifact must be a regular file: {source}")

    read_flags = os.O_RDONLY
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        read_flags |= os.O_NOFOLLOW
        write_flags |= os.O_NOFOLLOW
    source_fd = os.open(source, read_flags)
    try:
        opened_metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or (opened_metadata.st_dev, opened_metadata.st_ino)
            != (source_metadata.st_dev, source_metadata.st_ino)
        ):
            raise ValueError(f"downloaded artifact changed while opening: {source}")
        destination_fd = os.open(destination, write_flags, 0o600)
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view) :]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        final_metadata = os.fstat(source_fd)
        if (
            final_metadata.st_size != opened_metadata.st_size
            or final_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            raise ValueError(f"downloaded artifact changed while copying: {source}")
    finally:
        os.close(source_fd)


def _build_verified_publication(
    download_root: Path, publish_root: Path, plans: tuple[_RepositoryPlan, ...]
) -> tuple[str, int]:
    _artifact_report(download_root)
    _make_private_directory(publish_root)
    for plan in plans:
        for relative in plan.relative_files:
            parts = PurePosixPath(relative).parts
            current = publish_root
            for part in parts[:-1]:
                current /= part
                try:
                    _make_private_directory(current)
                except FileExistsError:
                    _assert_real_directory(current, label="publication path component")
            _copy_regular_file(download_root.joinpath(*parts), publish_root.joinpath(*parts))
    return _artifact_report(publish_root)


def fetch_artifacts(output_dir: Path | str, *, force: bool = False) -> tuple[str, int]:
    """Fetch and verify the immutable contract, then atomically publish it."""
    root, parent = _output_root(output_dir)
    plans = _repository_plans()

    # Import only after all caller-controlled and manifest paths are known safe.
    from huggingface_hub import snapshot_download

    workspace = Path(tempfile.mkdtemp(prefix=f".{root.name}.fetch-", dir=parent))
    try:
        _assert_real_directory(workspace, label="private acquisition directory")
        download_root = workspace / "downloads"
        publish_root = workspace / "publish"
        _make_private_directory(download_root)
        for plan in plans:
            _assert_absent(root)
            _assert_directory_chain(parent, label="output parent")
            _assert_real_directory(workspace, label="private acquisition directory")
            _assert_real_directory(download_root, label="download staging directory")
            destination = download_root / plan.destination
            _make_private_directory(destination)
            _assert_real_directory(destination, label="download destination")
            snapshot_download(
                repo_id=plan.repository,
                revision=plan.revision,
                local_dir=destination,
                allow_patterns=list(plan.allow_patterns),
                force_download=force,
            )
            _assert_real_directory(destination, label="download destination")

        report = _build_verified_publication(download_root, publish_root, plans)
        shutil.rmtree(download_root)
        if set(workspace.iterdir()) != {publish_root}:
            raise ValueError("private acquisition directory contains unexpected entries")

        # This immediate no-follow revalidation makes the final rename the only
        # publish operation. A hostile same-user process racing this tiny window
        # remains outside the supported trusted-local operator model.
        _assert_absent(root)
        _assert_directory_chain(parent, label="output parent")
        _assert_real_directory(workspace, label="private acquisition directory")
        _assert_real_directory(publish_root, label="verified publication directory")
        os.rename(publish_root, root)
        os.rmdir(workspace)
        return report
    finally:
        try:
            workspace_metadata = os.lstat(workspace)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISDIR(workspace_metadata.st_mode) and not stat.S_ISLNK(
                workspace_metadata.st_mode
            ):
                shutil.rmtree(workspace)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the exact Docling layout/TableFormer files certified by MasterVault. "
            "This is the only network-enabled artifact step; runtime parsing remains offline."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help=(
            "absent destination directory under an existing real parent; a verified tree "
            "is published atomically"
        ),
    )
    parser.add_argument("--force", action="store_true", help="redownload immutable files")
    args = parser.parse_args()
    try:
        identity, artifact_bytes = fetch_artifacts(args.output_dir, force=args.force)
    # Keep the explicit acquisition command actionable when the optional
    # downloader is absent or the Hub rejects a pinned snapshot. Interrupts
    # still propagate because they do not derive from Exception.
    except Exception as exc:
        parser.exit(1, f"Docling artifact acquisition failed: {exc}\n")
    print(f"verified model identity: {identity}")
    print(f"verified runtime artifact bytes: {artifact_bytes}")


if __name__ == "__main__":
    main()
