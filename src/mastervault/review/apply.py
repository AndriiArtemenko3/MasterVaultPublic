"""Apply an approved review item to its target file.

Safety order, per item:

1. Re-read the target and compare content_hash(current) against the item's
   base_hash. Any drift -> the item is marked conflict and NOTHING is applied.
2. kind=replace applies one of three explicit payload modes:
   full_file (swap the body after frontmatter), replace_section (swap one
   '## X' block), append_section (append a new block to the body).
3. kind=diff applies a unified diff to the full file text; any hunk mismatch
   -> conflict.
4. On success: bump the target's `updated:` frontmatter surgically, archive
   the item with outcome=applied, and call reindex_hook(target_path).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mastervault.core.errors import PatchError, UsageError
from mastervault.core.events import Clock
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
    return re.compile(
        rf"^{re.escape(section)}[ \t]*\n.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE
    )


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

    loaded: LoadedReview = queue.load(item_path)
    item = loaded.item
    target = vault_root / item.target

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

    target.write_text(new_text, encoding="utf-8")
    archived_to = queue.archive(
        item_path,
        outcome="applied",
        note=f"applied to {item.target} ({loaded.kind})",
    )
    if reindex_hook is not None:
        reindex_hook(target)
    return AppliedResult(target=target, archived_to=archived_to)
