"""Shared fixtures for the review layer: queue dirs, item factory, vault notes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mastervault.models import ChangeType, ReviewItem, content_hash
from mastervault.review.queue import ReviewQueue

FIXED_NOW = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

WIKI_NOTE = """---
domain: operations
type: wiki
title: Refund Policy
tags: [policy]
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Refund Policy

## Summary

Refunds are issued within 14 days.

## Notes

Legacy note line.
"""


@pytest.fixture
def wiki_note_text() -> str:
    return WIKI_NOTE


@pytest.fixture
def fixed_clock():
    return lambda: FIXED_NOW


@pytest.fixture
def queue(tmp_path, fixed_clock) -> ReviewQueue:
    return ReviewQueue(
        tmp_path / "review" / "pending",
        tmp_path / "review" / "archive",
        clock=fixed_clock,
    )


@pytest.fixture
def vault_root(tmp_path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def wiki_target(vault_root) -> Path:
    path = vault_root / "wiki" / "refund-policy.md"
    path.parent.mkdir(parents=True)
    path.write_text(WIKI_NOTE, encoding="utf-8")
    return path


@pytest.fixture
def make_item():
    def _make(
        id: str = "rv-0001-edit-wiki",
        target: str = "wiki/refund-policy.md",
        base_hash: str = content_hash(WIKI_NOTE),
        pattern_key: str = "wiki-body-edit",
        tier: int = 2,
        change_type: ChangeType = ChangeType.EDIT_WIKI_BODY,
        producer: str = "lint",
        payload: dict | None = None,
        created: datetime = FIXED_NOW,
    ) -> ReviewItem:
        return ReviewItem(
            id=id,
            created=created,
            producer=producer,
            run_id="run-1",
            tier=tier,
            target=target,
            change_type=change_type,
            pattern_key=pattern_key,
            rationale="The summary is stale against newer sources.",
            base_hash=base_hash,
            payload=payload or {},
        )

    return _make
