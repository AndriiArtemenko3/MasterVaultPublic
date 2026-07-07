"""review.apply: three replace modes, diff patching, drift + mismatch conflicts."""

from __future__ import annotations

import difflib
from datetime import UTC, datetime

import pytest

from mastervault.core.errors import PatchError
from mastervault.models import ReviewStatus, content_hash
from mastervault.review.apply import (
    AppliedResult,
    ConflictResult,
    apply,
    apply_unified_diff,
)


def APPLY_CLOCK() -> datetime:
    return datetime(2026, 7, 8, 9, 0, tzinfo=UTC)


def make_diff(old: str, new: str) -> str:
    return "\n".join(
        difflib.unified_diff(old.split("\n"), new.split("\n"), "a/x.md", "b/x.md", lineterm="")
    )


# -- replace modes -------------------------------------------------------------


def test_apply_full_file(queue, make_item, vault_root, wiki_target):
    item = make_item(payload={"mode": "full_file"})
    proposal = "# Refund Policy\n\nEntirely new body."
    path = queue.enqueue(item, proposal, kind="replace")

    result = apply(path, vault_root, queue=queue, clock=APPLY_CLOCK)

    assert isinstance(result, AppliedResult)
    text = wiki_target.read_text()
    assert "Entirely new body." in text
    assert "Legacy note line." not in text
    assert text.startswith("---\ndomain: operations")  # frontmatter preserved
    assert "updated: 2026-07-08" in text  # bumped
    assert not path.exists()
    assert result.archived_to.parent == queue.archive_dir
    assert queue.load(result.archived_to).item.status == ReviewStatus.APPLIED


def test_apply_replace_section(queue, make_item, vault_root, wiki_target):
    item = make_item(payload={"mode": "replace_section", "section": "## Summary"})
    proposal = "## Summary\n\nRefunds are issued within 30 days."
    path = queue.enqueue(item, proposal, kind="replace")

    result = apply(path, vault_root, queue=queue, clock=APPLY_CLOCK)

    assert isinstance(result, AppliedResult)
    text = wiki_target.read_text()
    assert "within 30 days." in text
    assert "within 14 days." not in text
    assert "Legacy note line." in text  # other sections untouched
    assert "updated: 2026-07-08" in text


def test_apply_replace_section_missing_section_conflicts(queue, make_item, vault_root, wiki_target):
    original = wiki_target.read_text()
    item = make_item(payload={"mode": "replace_section", "section": "## Nonexistent"})
    path = queue.enqueue(item, "## Nonexistent\n\nX", kind="replace")

    result = apply(path, vault_root, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "section not found" in result.reason
    assert wiki_target.read_text() == original  # untouched


def test_apply_append_section(queue, make_item, vault_root, wiki_target):
    item = make_item(payload={"mode": "append_section"})
    proposal = "## History\n\nPolicy loosened in 2026-Q2."
    path = queue.enqueue(item, proposal, kind="replace")

    result = apply(path, vault_root, queue=queue, clock=APPLY_CLOCK)

    assert isinstance(result, AppliedResult)
    text = wiki_target.read_text()
    assert text.rstrip().endswith("Policy loosened in 2026-Q2.")
    assert "Legacy note line." in text
    assert "updated: 2026-07-08" in text


# -- diff kind -------------------------------------------------------------------


def test_apply_diff_happy_path(queue, make_item, vault_root, wiki_target):
    original = wiki_target.read_text()
    new_note = original.replace("within 14 days", "within 45 days")
    item = make_item(payload={})
    path = queue.enqueue(item, make_diff(original, new_note), kind="diff")

    result = apply(path, vault_root, queue=queue, clock=APPLY_CLOCK)

    assert isinstance(result, AppliedResult)
    text = wiki_target.read_text()
    assert "within 45 days" in text
    assert "updated: 2026-07-08" in text


def test_apply_diff_hunk_mismatch_conflicts(queue, make_item, vault_root, wiki_target):
    original = wiki_target.read_text()
    # Diff generated against a different base than what is on disk.
    other_base = original.replace("Legacy note line.", "A line that never existed.")
    bad_diff = make_diff(other_base, other_base.replace("never existed.", "changed."))
    # base_hash matches the CURRENT file, so drift detection passes; the
    # conflict must come from the hunk mismatch itself.
    item = make_item(base_hash=content_hash(original))
    path = queue.enqueue(item, bad_diff, kind="diff")

    result = apply(path, vault_root, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "mismatch" in result.reason
    assert wiki_target.read_text() == original
    assert queue.load(path).item.status == ReviewStatus.CONFLICT


# -- drift + missing target ---------------------------------------------------------


def test_apply_base_hash_drift_conflicts(queue, make_item, vault_root, wiki_target):
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}), "New body.", kind="replace"
    )
    wiki_target.write_text(wiki_target.read_text() + "\nEdited by a human meanwhile.\n")

    result = apply(path, vault_root, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "drift" in result.reason
    assert "Edited by a human meanwhile." in wiki_target.read_text()  # never applied
    assert path.exists()
    assert queue.load(path).item.status == ReviewStatus.CONFLICT


def test_apply_missing_target_conflicts(queue, make_item, vault_root):
    path = queue.enqueue(
        make_item(target="wiki/gone.md", payload={"mode": "full_file"}), "X", kind="replace"
    )
    result = apply(path, vault_root, queue=queue)
    assert isinstance(result, ConflictResult)
    assert "missing" in result.reason


def test_apply_calls_reindex_hook(queue, make_item, vault_root, wiki_target):
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}), "New body.", kind="replace"
    )
    seen = []
    result = apply(path, vault_root, reindex_hook=seen.append, queue=queue)
    assert isinstance(result, AppliedResult)
    assert seen == [wiki_target]


# -- unified diff patcher unit tests -------------------------------------------------


def test_apply_unified_diff_round_trip():
    old = "alpha\nbravo\ncharlie\ndelta\n"
    new = "alpha\nbravo new\ncharlie\ndelta\nextra\n"
    assert apply_unified_diff(old, make_diff(old, new)) == new


def test_apply_unified_diff_multiple_hunks():
    old = "\n".join(f"line-{i}" for i in range(40)) + "\n"
    new = old.replace("line-3", "line-3-x").replace("line-30", "line-30-x")
    assert apply_unified_diff(old, make_diff(old, new)) == new


def test_apply_unified_diff_rejects_context_mismatch():
    old = "alpha\nbravo\ncharlie\n"
    diff = make_diff(old, old.replace("bravo", "bravo!"))
    with pytest.raises(PatchError, match="mismatch"):
        apply_unified_diff("alpha\nBRAVO\ncharlie\n", diff)


def test_apply_unified_diff_rejects_truncated_hunk():
    with pytest.raises(PatchError, match="mid-hunk"):
        apply_unified_diff("a\nb\n", "@@ -1,2 +1,2 @@\n a")
