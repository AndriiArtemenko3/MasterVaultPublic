"""Approved review changes keep Markdown and the derived index in lockstep."""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from mastervault.cli.app import app
from mastervault.config import load_settings
from mastervault.models import ChangeType, ReviewItem, content_hash
from mastervault.providers import get_embedding_provider
from mastervault.review.apply import ConflictResult, apply
from mastervault.review.queue import ReviewQueue
from mastervault.review.reindex import sync_review_target
from mastervault.storage import get_backend

OLD_PHRASE = "cobalt lantern warranty lasts eleven months"
NEW_PHRASE = "saffron lantern warranty lasts twenty seven months"
HUMAN_PHRASE = "violet lantern warranty lasts thirty one months"


def _note(phrase: str) -> str:
    return f"""---
domain: operations
type: source
title: Lantern Warranty Memo
tags: [policy]
status: processed
created: 2026-01-01
updated: 2026-01-01
source_type: policy
key_claims:
- id: lantern-warranty-01
  statement: {phrase}.
  confidence: high
  affects: []
---

# Lantern Warranty Memo

{phrase}.
"""


def test_cli_approve_replaces_the_content_served_by_search(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_RERANKER__BACKEND", "mock")
    # Exercise the shipped/default relative-path mode: resolve_within returns
    # an absolute target while Settings intentionally retains this relative
    # workspace value.
    monkeypatch.setenv("MV_PATHS__WORKSPACE", "workspace")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    target = workspace / "vault" / "operations" / "sources" / "lantern-warranty.md"
    target.parent.mkdir(parents=True)
    old_note = _note(OLD_PHRASE)
    new_note = _note(NEW_PHRASE)
    target.write_text(old_note, encoding="utf-8")

    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["sync"]).exit_code == 0

    diff = "\n".join(
        difflib.unified_diff(
            old_note.split("\n"),
            new_note.split("\n"),
            "a/lantern-warranty.md",
            "b/lantern-warranty.md",
            lineterm="",
        )
    )
    settings = load_settings()
    queue = ReviewQueue.from_settings(settings)
    item = ReviewItem(
        id="rv-lantern-warranty",
        created=datetime(2026, 8, 5, tzinfo=UTC),
        producer="integration-test",
        run_id="run-review-index-sync",
        tier=2,
        target="operations/sources/lantern-warranty.md",
        change_type=ChangeType.EDIT_WIKI_BODY,
        pattern_key="lantern-warranty-update",
        rationale="Replace a deliberately unique indexed phrase.",
        base_hash=content_hash(old_note),
    )
    queued = queue.enqueue(item, diff, "diff")
    assert queued is not None

    approved = runner.invoke(app, ["review", "approve", item.id])
    assert approved.exit_code == 0, approved.output

    backend = get_backend(load_settings())
    try:
        assert backend.lexical_claims(OLD_PHRASE, 5) == []
        assert backend.lexical_docs(OLD_PHRASE, 5) == []
        assert backend.lexical_claims(NEW_PHRASE, 5) == ["lantern-warranty-01"]
        assert backend.lexical_docs(NEW_PHRASE, 5) == [
            "source:operations/sources/lantern-warranty.md"
        ]
        stored = backend.get_claims(["lantern-warranty-01"])[0]
        assert NEW_PHRASE in stored.statement
        assert OLD_PHRASE not in stored.statement
    finally:
        backend.close()

    searched = runner.invoke(app, ["search", NEW_PHRASE, "--json"])
    assert searched.exit_code == 0, searched.output
    payload = json.loads(searched.output)
    assert any(hit["record_id"] == "claim:lantern-warranty-01" for hit in payload["hits"])
    assert OLD_PHRASE not in target.read_text(encoding="utf-8")


def test_post_reindex_hook_drift_preserves_human_edit_and_converges_index(
    tmp_path, monkeypatch
):
    """A hook that indexes then edits cannot resolve the proposal as applied."""
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_RERANKER__BACKEND", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", "workspace")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    target = workspace / "vault" / "operations" / "sources" / "lantern-warranty.md"
    target.parent.mkdir(parents=True)
    old_note = _note(OLD_PHRASE)
    proposal_note = _note(NEW_PHRASE)
    human_note = _note(HUMAN_PHRASE)
    target.write_text(old_note, encoding="utf-8")

    settings = load_settings()
    queue = ReviewQueue.from_settings(settings)
    diff = "\n".join(
        difflib.unified_diff(
            old_note.split("\n"),
            proposal_note.split("\n"),
            "a/lantern-warranty.md",
            "b/lantern-warranty.md",
            lineterm="",
        )
    )
    item = ReviewItem(
        id="rv-hook-drift",
        created=datetime(2026, 8, 5, tzinfo=UTC),
        producer="integration-test",
        run_id="run-hook-drift",
        tier=2,
        target="operations/sources/lantern-warranty.md",
        change_type=ChangeType.EDIT_WIKI_BODY,
        pattern_key="hook-drift",
        rationale="Exercise a canonical edit after the real index sync.",
        base_hash=content_hash(old_note),
    )
    queued = queue.enqueue(item, diff, "diff")
    assert queued is not None

    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    backend.init_schema(embedder.dimensions, embedder.model_version)
    calls = 0

    def sync_then_edit(path):
        nonlocal calls
        sync_review_target(
            path,
            vault_root=settings.paths.vault_dir,
            backend=backend,
            embedder=embedder,
        )
        calls += 1
        if calls == 1:
            path.write_text(human_note, encoding="utf-8")

    try:
        result = apply(
            queued,
            settings.paths.vault_dir,
            sync_then_edit,
            queue=queue,
        )
        assert isinstance(result, ConflictResult)
        assert "drifted while reindexing" in result.reason
        assert calls == 2
        assert target.read_text(encoding="utf-8") == human_note
        assert queued.is_file()
        assert queue.load(queued).item.status.value == "conflict"
        assert list(settings.paths.review_archive.glob("*.md")) == []
        assert backend.lexical_claims(HUMAN_PHRASE, 5) == ["lantern-warranty-01"]
        assert backend.lexical_claims(NEW_PHRASE, 5) == []
        assert backend.lexical_claims(OLD_PHRASE, 5) == []
    finally:
        backend.close()


@pytest.mark.parametrize(
    ("relative_target", "expected_detail"),
    [
        ("operations/sources/lantern-warranty.txt", "unsupported extension"),
        (".private/lantern-warranty.md", "hidden path"),
    ],
)
def test_cli_approve_does_not_archive_targets_ignored_by_the_indexer(
    tmp_path, monkeypatch, relative_target, expected_detail
):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_RERANKER__BACKEND", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", "workspace")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    target = workspace / "vault" / relative_target
    target.parent.mkdir(parents=True)
    old_note = _note(OLD_PHRASE)
    target.write_text(old_note, encoding="utf-8")

    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    settings = load_settings()
    queue = ReviewQueue.from_settings(settings)
    item = ReviewItem(
        id="rv-ignored-target",
        created=datetime(2026, 8, 5, tzinfo=UTC),
        producer="integration-test",
        run_id="run-review-index-proof",
        tier=2,
        target=relative_target,
        change_type=ChangeType.EDIT_WIKI_BODY,
        pattern_key="ignored-target",
        rationale="An ignored target must never be reported as indexed.",
        base_hash=content_hash(old_note),
        payload={"mode": "full_file"},
    )
    queued = queue.enqueue(item, "# Replacement\n\nThis must roll back.", "replace")
    assert queued is not None

    approved = runner.invoke(app, ["review", "approve", item.id])
    assert approved.exit_code != 0
    assert expected_detail in approved.output
    assert queued.is_file()
    assert queue.load(queued).item.status.value == "conflict"
    assert target.read_text(encoding="utf-8") == old_note
    assert list(settings.paths.review_archive.glob("*.md")) == []
