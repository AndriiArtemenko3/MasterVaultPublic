"""ReviewQueue: enqueue/dedupe, pattern-key guard, round-trip, archive, listing."""

from __future__ import annotations

import pytest

from mastervault.core.errors import UsageError
from mastervault.models import ReviewItem, ReviewStatus
from mastervault.vaultfs.frontmatter import parse_frontmatter

PROPOSAL = "## Summary\n\nRefunds are issued within 30 days."


def test_enqueue_writes_item_file(queue, make_item):
    path = queue.enqueue(make_item(), PROPOSAL, kind="replace")
    assert path is not None
    assert path.name == "rv-0001-edit-wiki.md"
    text = path.read_text()
    assert "## Rationale" in text
    assert "## Proposal" in text
    assert "````replace" in text
    assert "## Resolution" in text

    data, _ = parse_frontmatter(text)
    item = ReviewItem.model_validate(data)
    assert item.status == ReviewStatus.PENDING
    assert item.payload["dedupe_key"]


def test_load_round_trips_proposal_exactly(queue, make_item):
    # A proposal containing its own triple-backtick fence must survive round-trip.
    tricky = "## Example\n\n```python\nprint('hi')\n```\n\nDone."
    path = queue.enqueue(make_item(), tricky, kind="replace")
    loaded = queue.load(path)
    assert loaded.proposal == tricky
    assert loaded.kind == "replace"
    assert loaded.rationale == "The summary is stale against newer sources."
    assert loaded.resolution == ""


def test_enqueue_dedupes_identical_pending_proposal(queue, make_item):
    first = queue.enqueue(make_item(id="rv-0001-a"), PROPOSAL, kind="replace")
    second = queue.enqueue(make_item(id="rv-0002-b"), PROPOSAL, kind="replace")
    assert first is not None
    assert second is None
    assert len(queue.list_items()) == 1


def test_enqueue_different_proposal_is_not_deduped(queue, make_item):
    queue.enqueue(make_item(id="rv-0001-a"), PROPOSAL, kind="replace")
    other = queue.enqueue(make_item(id="rv-0002-b"), PROPOSAL + " Extended.", kind="replace")
    assert other is not None
    assert len(queue.list_items()) == 2


def test_archived_item_does_not_block_reenqueue(queue, make_item):
    path = queue.enqueue(make_item(id="rv-0001-a"), PROPOSAL, kind="replace")
    queue.archive(path, outcome="rejected", note="not yet")
    again = queue.enqueue(make_item(id="rv-0002-b"), PROPOSAL, kind="replace")
    assert again is not None


def test_enqueue_empty_pattern_key_raises(queue, make_item):
    # pydantic already rejects empty pattern_key at construction; model_construct
    # simulates a buggy producer that bypassed validation.
    item = make_item().model_copy()
    item = ReviewItem.model_construct(**{**item.model_dump(), "pattern_key": ""})
    with pytest.raises(UsageError, match="pattern_key"):
        queue.enqueue(item, PROPOSAL, kind="replace")


def test_enqueue_bad_kind_raises(queue, make_item):
    with pytest.raises(UsageError, match="kind"):
        queue.enqueue(make_item(), PROPOSAL, kind="patch")  # type: ignore[arg-type]


def test_archive_moves_and_stamps_resolution(queue, make_item, fixed_clock):
    path = queue.enqueue(make_item(), PROPOSAL, kind="replace")
    dest = queue.archive(path, outcome="rejected", note="duplicate of rv-0000")

    assert not path.exists()
    assert dest.parent == queue.archive_dir
    loaded = queue.load(dest)
    assert loaded.item.status == ReviewStatus.REJECTED
    assert loaded.item.outcome == "rejected"
    assert loaded.item.resolved is not None
    assert loaded.resolution == "duplicate of rv-0000"
    assert loaded.proposal == PROPOSAL  # payload preserved through resolution


def test_archive_unknown_outcome_raises(queue, make_item):
    path = queue.enqueue(make_item(), PROPOSAL, kind="replace")
    with pytest.raises(UsageError, match="outcome"):
        queue.archive(path, outcome="maybe-later")
    with pytest.raises(UsageError, match="resolved"):
        queue.archive(path, outcome="pending")


def test_list_items_filters_by_status_and_pattern(queue, make_item):
    queue.enqueue(make_item(id="rv-1", pattern_key="alias-add"), "p1", kind="replace")
    queue.enqueue(make_item(id="rv-2", pattern_key="alias-add"), "p2", kind="replace")
    queue.enqueue(make_item(id="rv-3", pattern_key="body-edit"), "p3", kind="replace")

    assert len(queue.list_items()) == 3
    assert len(queue.list_items(pattern="alias-add")) == 2
    assert len(queue.list_items(status=ReviewStatus.PENDING)) == 3
    assert queue.list_items(status=ReviewStatus.CONFLICT) == []


def test_list_items_empty_dir(queue):
    assert queue.list_items() == []


def test_mark_conflict_flips_status_in_place(queue, make_item):
    path = queue.enqueue(make_item(), PROPOSAL, kind="replace")
    queue.mark_conflict(path, "target drifted")
    loaded = queue.load(path)
    assert loaded.item.status == ReviewStatus.CONFLICT
    assert loaded.resolution == "target drifted"
    assert path.exists()  # conflict items stay pending-side for a human
