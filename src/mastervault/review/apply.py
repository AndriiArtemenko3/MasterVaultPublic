"""Apply an approved review item to its target file.

Safety order, per item:

1. Re-read the target and compare content_hash(current) against the item's
   base_hash. Any drift -> the item is marked conflict and NOTHING is applied.
2. kind=replace applies one of three explicit payload modes:
   full_file (swap the body after frontmatter), replace_section (swap one
   '## X' block), append_section (append a new block to the body).
3. kind=diff applies a unified diff to the full file text; any hunk mismatch
   -> conflict.
4. On success: bump `updated:` surgically, write the target, synchronize the
   derived index, then archive the item. Handled reindex/archive failures
   restore the prior canonical content and index when the proposal is still
   live. A concurrent edit is preserved and surfaced as a visible conflict.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mastervault.core.errors import PatchError, UsageError
from mastervault.core.events import Clock
from mastervault.core.paths import PathBoundaryError, resolve_within
from mastervault.models import content_hash
from mastervault.review.queue import LoadedReview, ReviewQueue
from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    join_frontmatter,
    split_frontmatter,
    surgical_replace_field,
)

REPLACE_MODES = ("full_file", "append_section", "replace_section")

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _write_temp_file(fd: int, text: str) -> None:
    """Write and durably flush one already-created temporary file."""
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _read_live_no_follow(path: Path) -> tuple[str, os.stat_result]:
    """Read the final regular file with Path.read_text-compatible newlines."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise PathBoundaryError(f"target became a symlink before replacement: {path}") from exc
        raise
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(errno.EINVAL, "canonical target is not a regular file", path)
        try:
            with os.fdopen(fd, "r", encoding="utf-8", newline=None) as handle:
                fd = -1  # The text wrapper owns and closes the descriptor.
                text = handle.read()
        except UnicodeDecodeError as exc:
            raise OSError(errno.EILSEQ, "canonical target is not valid UTF-8", path) from exc
    finally:
        if fd >= 0:
            os.close(fd)

    # Bind the descriptor we read to the path that os.replace will target.
    # This catches a path swap during the read; another swap can still occur
    # after this check, as documented by _write_no_follow.
    live_path = path.lstat()
    if stat.S_ISLNK(live_path.st_mode):
        raise PathBoundaryError(f"target became a symlink before replacement: {path}")
    if not stat.S_ISREG(live_path.st_mode):
        raise OSError(errno.EINVAL, "canonical target is not a regular file", path)
    if (live_path.st_dev, live_path.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError(errno.ESTALE, "canonical target changed during verification", path)
    return text, opened


def _write_no_follow(path: Path, text: str, *, expected_hash: str) -> None:
    """Compare and atomically replace `path`, refusing an unsafe FINAL symlink.

    resolve_within() checked this path before the item was read, hashed and
    patched. This writer rechecks the final component, writes and fsyncs a
    uniquely named same-directory temporary file, then uses os.replace() only
    after the complete payload is durable in that file. A failed pre-replace
    write therefore cannot truncate the canonical source. The replacement
    retains the target's permission bits. Immediately before replacement it
    reopens the live destination without following the final symlink and
    verifies its content still matches ``expected_hash``.

    It does not make the write race-proof, and it is not claimed to:

    - a parent directory that passed resolution can still be swapped before
      the temporary-file creation or replacement, so that concurrent race is
      not defended against;
    - hard links are invisible to every symlink-aware check, including this one.

    Both require an attacker with concurrent write access to the vault
    directory, which is outside this tool's threat model -- a single-operator
    local CLI. See SECURITY.md for the enforced/not-enforced split.

    The temp-file fsync plus atomic replacement protects handled pre-replace
    failures, and the compare closes drift during temporary-file preparation.
    Generic POSIX paths do not provide a single content-CAS operation: a narrow
    race remains between the final descriptor/path comparison and os.replace()
    unless all writers cooperate on a lock or stronger platform primitives are
    used. This also does not claim whole-filesystem or process-crash durability;
    in particular, the containing directory is not fsynced here.
    """
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode):
        raise PathBoundaryError(f"target became a symlink before the write: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise OSError(errno.EINVAL, "canonical target is not a regular file", path)

    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        os.fchmod(fd, stat.S_IMODE(before.st_mode))
        _write_temp_file(fd, text)
        fd = -1  # _write_temp_file owns and closes it.

        live_text, _opened = _read_live_no_follow(path)
        live_hash = content_hash(live_text)
        if live_hash != expected_hash:
            raise OSError(
                errno.ESTALE,
                "canonical target drifted during atomic write preparation "
                f"(expected {expected_hash}, now {live_hash})",
                path,
            )
        os.replace(temp_path, path)
        temp_path = None
    except BaseException:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()
        raise


@dataclass(frozen=True)
class AppliedResult:
    target: Path
    archived_to: Path


@dataclass(frozen=True)
class ConflictResult:
    target: Path
    reason: str


ApplyResult = AppliedResult | ConflictResult


# ---------------------------------------------------------------------------
# Unified diff patcher (strict: any hunk mismatch raises PatchError)
# ---------------------------------------------------------------------------


def apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply a unified diff to `original`. Strict positional match, no fuzz."""
    orig_lines = original.split("\n")
    out: list[str] = []
    pos = 0
    old_remaining = 0
    new_remaining = 0

    def consume(expected: str, raw: str) -> None:
        nonlocal pos
        if pos >= len(orig_lines) or orig_lines[pos] != expected:
            got = orig_lines[pos] if pos < len(orig_lines) else "<EOF>"
            raise PatchError(
                f"hunk mismatch at line {pos + 1}: diff expected {expected!r}, file has {got!r}"
                f" (diff line {raw!r})"
            )
        pos += 1

    for raw in diff_text.split("\n"):
        m = _HUNK_RE.match(raw)
        if m:
            if old_remaining or new_remaining:
                raise PatchError(f"new hunk begins before the previous one is complete: {raw!r}")
            old_start = int(m.group("old_start"))
            old_remaining = int(m.group("old_count") or "1")
            new_remaining = int(m.group("new_count") or "1")
            # For a zero-length old range the hunk inserts AFTER line old_start.
            start = old_start if old_remaining == 0 else old_start - 1
            if start < pos or start > len(orig_lines):
                raise PatchError(f"hunk out of order or beyond EOF: {raw!r}")
            out.extend(orig_lines[pos:start])
            pos = start
            continue
        if old_remaining == 0 and new_remaining == 0:
            continue  # between hunks: ---/+++ headers, preamble, trailing blank
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        if raw.startswith("+"):
            out.append(raw[1:])
            new_remaining -= 1
        elif raw.startswith("-"):
            consume(raw[1:], raw)
            old_remaining -= 1
        elif raw.startswith(" ") or raw == "":
            # Some generators emit a bare empty line for empty context lines.
            expected = raw[1:] if raw.startswith(" ") else ""
            consume(expected, raw)
            out.append(expected)
            old_remaining -= 1
            new_remaining -= 1
        else:
            raise PatchError(f"unexpected line inside hunk: {raw!r}")
        if old_remaining < 0 or new_remaining < 0:
            raise PatchError("hunk contains more lines than its header declares")

    if old_remaining or new_remaining:
        raise PatchError("diff ended mid-hunk: fewer lines than the hunk header declares")

    out.extend(orig_lines[pos:])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Replace modes
# ---------------------------------------------------------------------------


def _section_block_re(section: str) -> re.Pattern[str]:
    # The section header line through (not including) the next '## ' or EOF.
    return re.compile(rf"^{re.escape(section)}[ \t]*\n.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE)


def _apply_replace(current: str, mode: str, section: str | None, proposal: str) -> str:
    yaml_str, body, had = split_frontmatter(current)
    proposal_block = proposal.strip("\n")

    if mode == "full_file":
        new_body = f"\n{proposal_block}\n" if proposal_block else ""
        return join_frontmatter(yaml_str, new_body) if had else f"{proposal_block}\n"

    if mode == "append_section":
        base = body.rstrip("\n")
        new_body = f"{base}\n\n{proposal_block}\n" if base else f"\n{proposal_block}\n"
        return join_frontmatter(yaml_str, new_body) if had else new_body.lstrip("\n")

    if mode == "replace_section":
        if not section:
            raise UsageError("replace_section payload needs a 'section' key (e.g. '## Notes')")
        pattern = _section_block_re(section)
        if pattern.search(body) is None:
            raise PatchError(f"section not found in target: {section!r}")
        # Function replacement: proposal text must never be re-interpreted as
        # regex escapes (\g, \1) by re.sub.
        new_body = pattern.sub(lambda _: f"{proposal_block}\n\n", body, count=1)
        # Collapse the padding when the section was the last block.
        new_body = new_body.rstrip("\n") + "\n"
        return join_frontmatter(yaml_str, new_body) if had else new_body

    raise UsageError(f"unknown replace mode {mode!r} (expected one of {REPLACE_MODES})")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(
    item_path: Path | str,
    vault_root: Path | str,
    reindex_hook: Callable[[Path], None] | None = None,
    *,
    queue: ReviewQueue | None = None,
    clock: Clock | None = None,
) -> ApplyResult:
    """Apply one approved review item. Drift or patch failure -> ConflictResult."""
    item_path = Path(item_path)
    vault_root = Path(vault_root)
    tick = clock or (lambda: datetime.now(UTC))
    if queue is None:
        # pending/ and archive/ are siblings under <workspace>/review/.
        queue = ReviewQueue(item_path.parent, item_path.parent.parent / "archive", clock=clock)

    try:
        loaded: LoadedReview = queue.load(item_path)
    except UsageError as exc:
        # Malformed or unsafe on disk (e.g. a planted `target:` the model
        # refuses). Nothing is applied and nothing is rewritten -- the file is
        # not trustworthy enough to stamp a status into.
        return ConflictResult(target=Path(vault_root), reason=str(exc))
    item = loaded.item

    # `target:` arrives from a queue file written by an LLM-driven producer, so
    # it is untrusted input: an absolute path or a `..` walk would otherwise
    # make this write anywhere the process can reach. Treated like any other
    # unapplyable item -- marked conflict, nothing written.
    try:
        target = resolve_within(vault_root, item.target)
    except PathBoundaryError as exc:
        reason = f"unsafe target path: {exc}"
        queue.mark_conflict(item_path, reason)
        return ConflictResult(target=Path(vault_root), reason=reason)

    if not target.is_file():
        reason = f"target file missing: {item.target}"
        queue.mark_conflict(item_path, reason)
        return ConflictResult(target=target, reason=reason)

    current = target.read_text(encoding="utf-8")
    if content_hash(current) != item.base_hash:
        reason = (
            f"base_hash drift: target changed since proposal "
            f"(expected {item.base_hash}, now {content_hash(current)})"
        )
        queue.mark_conflict(item_path, reason)
        return ConflictResult(target=target, reason=reason)

    try:
        if loaded.kind == "diff":
            new_text = apply_unified_diff(current, loaded.proposal)
        else:
            mode = item.payload.get("mode", "full_file")
            new_text = _apply_replace(current, mode, item.payload.get("section"), loaded.proposal)
    except PatchError as exc:
        queue.mark_conflict(item_path, str(exc))
        return ConflictResult(target=target, reason=str(exc))

    # Targets without frontmatter (or without `updated:`) apply as-is.
    with contextlib.suppress(KeyError, FrontmatterError):
        new_text = surgical_replace_field(
            new_text, "updated", f"updated: {tick().date().isoformat()}"
        )

    try:
        _write_no_follow(target, new_text, expected_hash=item.base_hash)
    except (PathBoundaryError, OSError) as exc:
        # The confinement check happened before the hash/patch work above; if
        # the target itself became a symlink in between, refuse rather than
        # follow it. Parent-component swaps are not covered -- see
        # _write_no_follow.
        reason = f"canonical write failed before apply: {type(exc).__name__}: {exc}"
        queue.mark_conflict(item_path, reason)
        return ConflictResult(target=target, reason=reason)

    applied_hash = content_hash(new_text)

    def rollback(reason: str) -> ConflictResult:
        recovery: list[str] = []
        restored = False
        live_text: str | None = None
        try:
            if target.is_file():
                live_text = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - preserve primary and recovery failures
            recovery.append(f"could not inspect live canonical file: {type(exc).__name__}: {exc}")

        if live_text is not None and content_hash(live_text) == applied_hash:
            try:
                _write_no_follow(target, current, expected_hash=applied_hash)
                restored = True
            except Exception as exc:  # noqa: BLE001 - preserve primary and recovery failures
                recovery.append(f"canonical rollback failed: {type(exc).__name__}: {exc}")
        else:
            recovery.append(
                "concurrent canonical change preserved; proposal rollback did not overwrite it"
            )

        # Re-run the synchronization hook against whichever canonical state is
        # now live.  This is best effort: its original failure remains the
        # primary conflict, but a second run can converge an edit made by the
        # hook itself before it raised.
        if reindex_hook is not None:
            try:
                reindex_hook(target)
            except Exception as exc:  # noqa: BLE001 - surfaced in the conflict record
                label = "index rollback" if restored else "live-content reindex"
                recovery.append(f"{label} failed: {type(exc).__name__}: {exc}")

        if restored:
            recovery.append("canonical content and derived index restored to the pre-apply state")
        elif live_text is None:
            recovery.append("inspect the canonical path before retrying")

        detail = reason
        if recovery:
            detail += "; " + "; ".join(recovery)
        if any("failed:" in part for part in recovery):
            detail += "; run `mvault sync --full` before retrying"
        if item_path.is_file():
            queue.mark_conflict(item_path, detail)
        return ConflictResult(target=target, reason=detail)

    if reindex_hook is not None:
        try:
            reindex_hook(target)
        except Exception as exc:  # noqa: BLE001 - hook is an application boundary
            return rollback(
                f"reindex failed after applying the proposal: {type(exc).__name__}: {exc}"
            )

        # The hook is allowed to touch the canonical file (and an external
        # editor may do so while it runs). Prove the exact proposal content is
        # still live immediately before resolution. This closes handled
        # in-process hook drift; it does not claim race-freedom against an
        # arbitrary writer after this comparison.
        try:
            live_after_reindex = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - surfaced through conflict recovery
            return rollback(
                "could not verify canonical content after reindex: "
                f"{type(exc).__name__}: {exc}"
            )
        if content_hash(live_after_reindex) != applied_hash:
            return rollback(
                "canonical target drifted while reindexing; refusing to archive or report success"
            )

    try:
        note = f"applied to {item.target} ({loaded.kind})"
        if reindex_hook is not None:
            note += "; derived index synchronized"
        archived_to = queue.archive(
            item_path,
            outcome="applied",
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - archive is the final atomicity boundary
        return rollback(
            f"review archive failed after reindex: {type(exc).__name__}: {exc}"
        )
    return AppliedResult(target=target, archived_to=archived_to)
