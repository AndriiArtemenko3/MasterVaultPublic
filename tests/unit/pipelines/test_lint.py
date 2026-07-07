"""run_lint: contradiction double-confirmation, --mechanical-only never
touches the LLM, and the broken-affects difflib suggestion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mastervault.contracts.contradiction import ContradictionVerdictOut
from mastervault.models import ChangeType, ReviewItem, ReviewStatus, content_hash
from mastervault.pipelines.lint import run_lint

WIKI_NOTE = """---
domain: operations
type: wiki
title: Refund Policy
aliases:
  - refund policy
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Refund Policy

## Definition

Refunds are issued according to the standard policy window.
"""


def _source_note(claim_id: str, statement: str, affects: str) -> str:
    return (
        "---\n"
        "domain: operations\n"
        "type: source\n"
        f"title: {claim_id}\n"
        "source_type: ticket\n"
        "tags: []\n"
        "status: processed\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "key_claims:\n"
        f"  - id: {claim_id}\n"
        f'    statement: "{statement}"\n'
        "    confidence: high\n"
        f"    affects: [{affects}]\n"
        "---\n\n"
        f"# {claim_id}\n\n{statement}\n"
    )


def _seed_contradiction_fixture(vault_dir: Path) -> None:
    (vault_dir / "operations" / "wiki").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "sources").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "wiki" / "refund-policy.md").write_text(WIKI_NOTE, encoding="utf-8")
    (vault_dir / "operations" / "sources" / "ticket-1.md").write_text(
        _source_note("ticket-1-01", "Refunds take exactly 10 business days to process.", "refund-policy"),
        encoding="utf-8",
    )
    (vault_dir / "operations" / "sources" / "ticket-2.md").write_text(
        _source_note("ticket-2-01", "Refunds take exactly 30 business days to process.", "refund-policy"),
        encoding="utf-8",
    )


def test_contradicts_then_contradicts_queues_exactly_one(settings, llm, review_queue):
    _seed_contradiction_fixture(settings.paths.vault_dir)
    llm.push("contradiction_judge", ContradictionVerdictOut(verdict="contradicts", rationale="10 days vs 30 days cannot both be the refund window."))
    llm.push("contradiction_judge", ContradictionVerdictOut(verdict="contradicts", rationale="30 days vs 10 days cannot both be the refund window."))

    outcome = run_lint(settings, llm, review_queue)

    assert outcome.summary["semantic"]["contradictions_queued"] == 1
    pending = review_queue.list_items(status=ReviewStatus.PENDING)
    assert len(pending) == 1
    _, item = pending[0]
    assert item.tier == 2
    assert item.pattern_key == "lint-contradiction::refund-policy"
    assert item.target == "operations/wiki/refund-policy.md"


def test_contradicts_then_compatible_queues_nothing(settings, llm, review_queue):
    _seed_contradiction_fixture(settings.paths.vault_dir)
    llm.push("contradiction_judge", ContradictionVerdictOut(verdict="contradicts", rationale="10 days vs 30 days looks contradictory at first."))
    llm.push("contradiction_judge", ContradictionVerdictOut(verdict="compatible", rationale="Different order types can have different windows."))

    outcome = run_lint(settings, llm, review_queue)

    assert outcome.summary["semantic"]["contradictions_queued"] == 0
    assert review_queue.list_items(status=ReviewStatus.PENDING) == []


def test_mechanical_only_never_calls_the_llm(settings, llm, review_queue):
    _seed_contradiction_fixture(settings.paths.vault_dir)
    # Deliberately push nothing: mechanical-only must never dispatch at all.

    outcome = run_lint(settings, llm, review_queue, mechanical_only=True)

    assert outcome.summary["semantic"]["skipped"] is True
    assert outcome.summary["semantic"]["pairs_examined"] == 0
    assert llm.calls == []


def test_broken_affects_gets_a_closest_match_suggestion(settings, llm, review_queue):
    vault_dir = settings.paths.vault_dir
    (vault_dir / "operations" / "wiki").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "sources").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "wiki" / "refund-policy.md").write_text(WIKI_NOTE, encoding="utf-8")
    (vault_dir / "operations" / "sources" / "ticket-3.md").write_text(
        _source_note("ticket-3-01", "Refunds are capped at $500 per order.", "refund-polcy"),
        encoding="utf-8",
    )

    outcome = run_lint(settings, llm, review_queue, mechanical_only=True)

    assert outcome.summary["mechanical"]["broken_affects"] == 1
    assert any("refund-polcy" in a and "refund-policy" in a for a in outcome.summary["top_actions"])


def test_review_conflict_marking(settings, llm, review_queue):
    """A pending review item whose target has drifted is marked conflict during lint."""
    vault_dir = settings.paths.vault_dir
    (vault_dir / "operations" / "wiki").mkdir(parents=True, exist_ok=True)
    target_path = vault_dir / "operations" / "wiki" / "refund-policy.md"
    target_path.write_text(WIKI_NOTE, encoding="utf-8")

    stale_item = ReviewItem(
        id="stale-rv-0001",
        created=datetime(2026, 1, 1, tzinfo=UTC),
        producer="lint",
        run_id="prior-run",
        tier=2,
        target="operations/wiki/refund-policy.md",
        change_type=ChangeType.EDIT_WIKI_BODY,
        pattern_key="prior-pattern",
        rationale="stale proposal",
        base_hash=content_hash(WIKI_NOTE),
    )
    review_queue.enqueue(stale_item, "some stale proposal text", "replace")

    # Now drift the target so the stale item's base_hash no longer matches.
    target_path.write_text(WIKI_NOTE + "\nExtra drift line.\n", encoding="utf-8")

    outcome = run_lint(settings, llm, review_queue, mechanical_only=True)

    assert outcome.summary["mechanical"]["review_conflicts_marked"] == 1
    pending = review_queue.list_items(status=ReviewStatus.CONFLICT)
    assert len(pending) == 1
