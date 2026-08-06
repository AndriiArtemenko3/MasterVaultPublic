"""review.apply: three replace modes, diff patching, drift + mismatch conflicts."""

from __future__ import annotations

import difflib
import errno
import importlib
import os
import stat
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
    path = queue.enqueue(make_item(payload={"mode": "full_file"}), "New body.", kind="replace")
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
    path = queue.enqueue(make_item(payload={"mode": "full_file"}), "New body.", kind="replace")
    seen = []
    result = apply(path, vault_root, reindex_hook=seen.append, queue=queue)
    assert isinstance(result, AppliedResult)
    assert seen == [wiki_target]


def test_apply_mid_temp_write_failure_preserves_canonical_and_leaks_no_temp(
    queue, make_item, vault_root, wiki_target, monkeypatch
):
    apply_module = importlib.import_module("mastervault.review.apply")
    original = wiki_target.read_bytes()
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}),
        "A replacement body long enough to be only partly written.",
        kind="replace",
    )

    def write_partial_then_fail(fd: int, text: str) -> None:
        payload = text.encode("utf-8")
        assert os.write(fd, payload[: max(1, len(payload) // 3)]) > 0
        raise OSError(errno.ENOSPC, "injected disk-full failure")

    monkeypatch.setattr(apply_module, "_write_temp_file", write_partial_then_fail)

    result = apply_module.apply(path, vault_root, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "canonical write failed before apply" in result.reason
    assert wiki_target.read_bytes() == original
    assert path.is_file()
    assert queue.load(path).item.status == ReviewStatus.CONFLICT
    assert list(queue.archive_dir.glob("*.md")) == []
    assert list(wiki_target.parent.glob(f".{wiki_target.name}.*.tmp")) == []


def test_apply_human_edit_during_temp_preparation_is_preserved(
    queue, make_item, vault_root, wiki_target, monkeypatch
):
    apply_module = importlib.import_module("mastervault.review.apply")
    human_bytes = wiki_target.read_bytes().replace(
        b"Legacy note line.", b"Human edit during temporary-file preparation."
    )
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}), "Replacement body.", kind="replace"
    )
    real_write_temp_file = apply_module._write_temp_file

    def write_then_edit_canonical(fd: int, text: str) -> None:
        real_write_temp_file(fd, text)
        wiki_target.write_bytes(human_bytes)

    monkeypatch.setattr(apply_module, "_write_temp_file", write_then_edit_canonical)

    result = apply_module.apply(path, vault_root, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "drifted during atomic write preparation" in result.reason
    assert wiki_target.read_bytes() == human_bytes
    assert path.is_file()
    assert queue.load(path).item.status == ReviewStatus.CONFLICT
    assert list(queue.archive_dir.glob("*.md")) == []
    assert list(wiki_target.parent.glob(f".{wiki_target.name}.*.tmp")) == []


def test_rollback_human_edit_during_temp_preparation_is_preserved(
    queue, make_item, vault_root, wiki_target, monkeypatch
):
    apply_module = importlib.import_module("mastervault.review.apply")
    human_bytes = wiki_target.read_bytes().replace(
        b"Legacy note line.", b"Human edit during rollback temporary-file preparation."
    )
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}), "Replacement body.", kind="replace"
    )
    real_write_temp_file = apply_module._write_temp_file
    write_count = 0

    def edit_during_rollback_temp_write(fd: int, text: str) -> None:
        nonlocal write_count
        write_count += 1
        real_write_temp_file(fd, text)
        if write_count == 2:
            wiki_target.write_bytes(human_bytes)

    hook_count = 0

    def fail_first_reindex(_target) -> None:
        nonlocal hook_count
        hook_count += 1
        if hook_count == 1:
            raise RuntimeError("force rollback")

    monkeypatch.setattr(apply_module, "_write_temp_file", edit_during_rollback_temp_write)

    result = apply_module.apply(path, vault_root, reindex_hook=fail_first_reindex, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "canonical rollback failed" in result.reason
    assert "drifted during atomic write preparation" in result.reason
    assert wiki_target.read_bytes() == human_bytes
    assert write_count == 2
    assert hook_count == 2
    assert path.is_file()
    assert queue.load(path).item.status == ReviewStatus.CONFLICT
    assert list(queue.archive_dir.glob("*.md")) == []
    assert list(wiki_target.parent.glob(f".{wiki_target.name}.*.tmp")) == []


def test_apply_atomic_replacement_preserves_target_permission_mode(
    queue, make_item, vault_root, wiki_target
):
    wiki_target.chmod(0o640)
    path = queue.enqueue(
        make_item(payload={"mode": "full_file"}), "Replacement body.", kind="replace"
    )

    result = apply(path, vault_root, queue=queue)

    assert isinstance(result, AppliedResult)
    assert stat.S_IMODE(wiki_target.stat().st_mode) == 0o640


def test_apply_unchanged_crlf_target_uses_universal_newline_hash(
    queue, make_item, vault_root, wiki_target
):
    original = wiki_target.read_text(encoding="utf-8")
    wiki_target.write_bytes(original.replace("\n", "\r\n").encode("utf-8"))
    path = queue.enqueue(
        make_item(base_hash=content_hash(original), payload={"mode": "full_file"}),
        "Replacement body from a reviewed CRLF base.",
        kind="replace",
    )

    result = apply(path, vault_root, queue=queue, clock=APPLY_CLOCK)

    assert isinstance(result, AppliedResult)
    final_text = wiki_target.read_text(encoding="utf-8")
    assert "Replacement body from a reviewed CRLF base." in final_text
    assert "updated: 2026-07-08" in final_text
    # The read/replace path follows Path.read_text/write_text semantics: input
    # newlines are universal, and output uses the platform text newline.
    assert wiki_target.read_bytes() == final_text.replace("\n", os.linesep).encode("utf-8")
    assert not path.exists()
    assert result.archived_to.is_file()
    assert list(wiki_target.parent.glob(f".{wiki_target.name}.*.tmp")) == []


def test_apply_reindex_failure_rolls_back_canonical_and_marks_conflict(
    queue, make_item, vault_root, wiki_target
):
    original = wiki_target.read_text()
    path = queue.enqueue(make_item(payload={"mode": "full_file"}), "New body.", kind="replace")
    calls = []

    def fail_then_recover(target):
        calls.append(target.read_text())
        if len(calls) == 1:
            raise RuntimeError("index unavailable")

    result = apply(
        path,
        vault_root,
        reindex_hook=fail_then_recover,
        queue=queue,
    )

    assert isinstance(result, ConflictResult)
    assert "reindex failed" in result.reason
    assert wiki_target.read_text() == original
    assert len(calls) == 2
    assert "New body." in calls[0]
    assert calls[1] == original
    assert queue.load(path).item.status == ReviewStatus.CONFLICT


def test_apply_reindex_failure_preserves_a_concurrent_canonical_edit(
    queue, make_item, vault_root, wiki_target
):
    path = queue.enqueue(make_item(payload={"mode": "full_file"}), "New body.", kind="replace")
    human_edit = wiki_target.read_text().replace("Legacy note line.", "Human edit during sync.")
    calls = 0

    def edit_then_fail(target):
        nonlocal calls
        calls += 1
        if calls == 1:
            target.write_text(human_edit, encoding="utf-8")
            raise RuntimeError("index unavailable after concurrent edit")
        assert target.read_text(encoding="utf-8") == human_edit

    result = apply(path, vault_root, reindex_hook=edit_then_fail, queue=queue)

    assert isinstance(result, ConflictResult)
    assert "concurrent canonical change preserved" in result.reason
    assert wiki_target.read_text(encoding="utf-8") == human_edit
    assert calls == 2
    assert queue.load(path).item.status == ReviewStatus.CONFLICT


def test_apply_archive_failure_rolls_back_canonical_index_and_marks_conflict(
    queue, make_item, vault_root, wiki_target
):
    original = wiki_target.read_text()
    path = queue.enqueue(make_item(payload={"mode": "full_file"}), "New body.", kind="replace")
    queue.archive_dir.mkdir(parents=True)
    (queue.archive_dir / path.name).write_text("existing archive", encoding="utf-8")
    indexed_states = []

    result = apply(
        path,
        vault_root,
        reindex_hook=lambda target: indexed_states.append(target.read_text()),
        queue=queue,
    )

    assert isinstance(result, ConflictResult)
    assert "archive failed" in result.reason
    assert wiki_target.read_text() == original
    assert len(indexed_states) == 2
    assert "New body." in indexed_states[0]
    assert indexed_states[1] == original
    assert queue.load(path).item.status == ReviewStatus.CONFLICT


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
