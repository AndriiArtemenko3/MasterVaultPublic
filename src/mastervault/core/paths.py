"""One way to turn an untrusted relative path into a real path inside a root.

Several places take a path out of data the operator did not type: a review
item's `target:` (written by an LLM-driven producer), a note path replayed from
a run's event log. `Path(root) / untrusted` is unsafe for all of them --
`Path("/vault") / "/etc/passwd"` is `/etc/passwd`, and `..` walks straight out.

`resolve_within` is the only sanctioned join for those paths. It refuses
absolute inputs and `..` segments up front, then resolves symlinks on the
result and re-checks containment, so neither a crafted string nor a symlink
planted inside the root can land a write outside it.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from mastervault.core.errors import MasterVaultError


class PathBoundaryError(MasterVaultError):
    """A path derived from untrusted data pointed outside its allowed root."""


def _real(path: Path) -> Path:
    """Fully symlink-resolved path that works for paths that do not exist yet."""
    return Path(os.path.realpath(path))


def resolve_within(root: Path | str, relative: str | Path) -> Path:
    """Resolve `relative` under `root`, or raise PathBoundaryError.

    Rejected: absolute paths, drive/UNC-anchored paths, any `..` segment, and
    embedded NUL bytes. After joining, both sides are symlink-resolved and
    containment is re-checked, which is what stops a symlink inside `root`
    from redirecting a write outside it.

    The returned path is the resolved one, so callers write through the same
    path that was checked rather than re-joining an unchecked string.
    """
    raw = str(relative)
    if not raw or not raw.strip():
        raise PathBoundaryError("empty path")
    if "\x00" in raw:
        raise PathBoundaryError(f"path contains a NUL byte: {raw!r}")

    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or Path(raw).is_absolute() or Path(raw).drive:
        raise PathBoundaryError(f"path must be relative to the workspace, got {raw!r}")
    if any(part == ".." for part in candidate.parts):
        raise PathBoundaryError(f"path must not contain '..' segments, got {raw!r}")

    root_real = _real(Path(root))
    target_real = _real(Path(root) / candidate)
    if target_real != root_real and not target_real.is_relative_to(root_real):
        # Only reachable via a symlink, since '..' and absolutes are gone.
        raise PathBoundaryError(
            f"path escapes the workspace through a symlink: {raw!r} -> {target_real}"
        )
    return target_real


def is_within(root: Path | str, candidate: Path | str) -> bool:
    """True when `candidate` resolves inside `root`. For assertions and tests."""
    root_real = _real(Path(root))
    target_real = _real(Path(candidate))
    return target_real == root_real or target_real.is_relative_to(root_real)
