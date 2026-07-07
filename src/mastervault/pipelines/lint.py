"""Lint pipeline: mechanical vault-health checks, plus an optional semantic
contradiction-detection pass.

Everything here reads the vault tree directly (`vaultfs.walker` +
`vaultfs.notes`) rather than the storage index — a lint pass should be
trustworthy even against a stale or never-synced index, and the mechanical
checks (frontmatter validity, broken `affects`, duplicate claim ids, orphan
wiki entries, drifted review items) don't need retrieval at all.

The semantic pass never queues a contradiction off one verdict: 'contradicts'
is confirmed by a SECOND dispatch with the two statements in swapped order
(guards against a directional bias in the judge), and only a pair that
contradicts both ways reaches the review queue. A pair examined in any past
run is remembered in `<runs_dir>/lint-seen.json` so re-running lint doesn't
keep re-paying to re-judge the same pair.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mastervault.config import Settings
from mastervault.contracts.contradiction import ContradictionJudgeContract, ContradictionVerdictOut
from mastervault.core.budget import BudgetLedger
from mastervault.core.errors import BudgetExceeded
from mastervault.core.events import Clock, EventName, utc_now
from mastervault.core.runctx import RunContext
from mastervault.models import (
    ChangeType,
    NoteType,
    ReviewItem,
    ReviewStatus,
    SourceNote,
    content_hash,
)
from mastervault.providers.llm import LLMProvider
from mastervault.review.queue import ReviewQueue
from mastervault.vaultfs.frontmatter import FrontmatterError
from mastervault.vaultfs.notes import read_note
from mastervault.vaultfs.walker import SkippedFile, walk_vault

MAX_PAIRS_PER_SLUG = 5
LINT_SEEN_FILENAME = "lint-seen.json"


@dataclass(frozen=True)
class _ClaimRef:
    claim_id: str
    statement: str
    affects: list[str]
    source_rel_path: str
    domain: str


@dataclass(frozen=True)
class _WikiRef:
    slug: str
    domain: str
    rel_path: str


@dataclass
class LintOutcome:
    exit_code: int
    run_id: str
    run_dir: Path
    summary: dict[str, Any]


def _scan_vault(vault_dir: Path) -> tuple[list[_ClaimRef], dict[str, _WikiRef], list[SkippedFile]]:
    walk = walk_vault(vault_dir)
    claims: list[_ClaimRef] = []
    wikis: dict[str, _WikiRef] = {}
    for note in walk.notes:
        if note.note_type is NoteType.WIKI:
            slug = Path(note.rel_path).stem
            wikis[slug] = _WikiRef(slug=slug, domain=note.domain.value, rel_path=note.rel_path)
        elif note.note_type is NoteType.SOURCE:
            try:
                loaded = read_note(note.abs_path)
            except (FrontmatterError, ValueError):
                continue
            model = loaded.model
            if isinstance(model, SourceNote):
                claims.extend(
                    _ClaimRef(
                        claim_id=c.id, statement=c.statement, affects=list(c.affects),
                        source_rel_path=note.rel_path, domain=note.domain.value,
                    )
                    for c in model.key_claims
                )
    return claims, wikis, walk.skipped


def _broken_affects(claims: list[_ClaimRef], wikis: dict[str, _WikiRef]) -> list[dict[str, Any]]:
    wiki_slugs = list(wikis)
    out: list[dict[str, Any]] = []
    for c in claims:
        for slug in c.affects:
            if slug in wikis:
                continue
            suggestion = difflib.get_close_matches(slug, wiki_slugs, n=1)
            out.append(
                {
                    "claim_id": c.claim_id,
                    "slug": slug,
                    "source": c.source_rel_path,
                    "suggestion": suggestion[0] if suggestion else None,
                }
            )
    return out


def _duplicate_claim_ids(claims: list[_ClaimRef]) -> dict[str, list[str]]:
    positions: dict[str, list[str]] = {}
    for c in claims:
        positions.setdefault(c.claim_id, []).append(c.source_rel_path)
    return {cid: paths for cid, paths in positions.items() if len(paths) > 1}


def _orphan_wikis(claims: list[_ClaimRef], wikis: dict[str, _WikiRef]) -> list[str]:
    inbound = {slug for c in claims for slug in c.affects}
    return sorted(slug for slug in wikis if slug not in inbound)


def _mark_drifted_review_items(review_queue: ReviewQueue, vault_dir: Path, tick: Clock) -> int:
    marked = 0
    for path, item in review_queue.list_items(status=ReviewStatus.PENDING):
        target = vault_dir / item.target
        if not target.is_file():
            continue
        current = content_hash(target.read_text(encoding="utf-8"))
        if current != item.base_hash:
            review_queue.mark_conflict(
                path, f"target changed since proposal (lint scan {tick().isoformat()})"
            )
            marked += 1
    return marked


def _load_seen(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _candidate_pairs(
    claims: list[_ClaimRef], seen: set[str]
) -> list[tuple[_ClaimRef, _ClaimRef, str, str]]:
    """(claim_a, claim_b, shared_slug, pair_id) tuples, capped per slug, seen-deduped."""
    by_slug: dict[str, list[_ClaimRef]] = {}
    for c in claims:
        for slug in c.affects:
            by_slug.setdefault(slug, []).append(c)

    out: list[tuple[_ClaimRef, _ClaimRef, str, str]] = []
    seen_this_run: set[str] = set()
    for slug in sorted(by_slug):
        group = sorted(by_slug[slug], key=lambda c: c.claim_id)
        taken = 0
        for i in range(len(group)):
            if taken >= MAX_PAIRS_PER_SLUG:
                break
            for j in range(i + 1, len(group)):
                if taken >= MAX_PAIRS_PER_SLUG:
                    break
                a, b = group[i], group[j]
                if a.claim_id == b.claim_id:
                    continue
                pair_id = "::".join(sorted((a.claim_id, b.claim_id))) + f"@{slug}"
                if pair_id in seen or pair_id in seen_this_run:
                    continue
                seen_this_run.add(pair_id)
                taken += 1
                out.append((a, b, slug, pair_id))
    return out


def _confirm_contradiction(
    llm: LLMProvider,
    ledger: BudgetLedger,
    emit: Callable[[str, dict], None],
    a: _ClaimRef,
    b: _ClaimRef,
) -> ContradictionVerdictOut | None:
    """Double-confirm: a second dispatch with statements swapped. Disagreement -> unclear."""
    contract = ContradictionJudgeContract()
    first = contract.dispatch(
        llm, {"statement_a": a.statement, "statement_b": b.statement}, ledger=ledger, emit=emit
    )
    if not first.ok or first.parsed is None:
        return None
    if first.parsed.verdict != "contradicts":
        return first.parsed

    second = contract.dispatch(
        llm, {"statement_a": b.statement, "statement_b": a.statement}, ledger=ledger, emit=emit
    )
    if not second.ok or second.parsed is None or second.parsed.verdict != "contradicts":
        return ContradictionVerdictOut(
            verdict="unclear", rationale=f"disagreement on order-swap: {first.parsed.rationale}"
        )
    return first.parsed


def run_lint(
    settings: Settings,
    llm: LLMProvider,
    review_queue: ReviewQueue | None = None,
    *,
    mechanical_only: bool = False,
    budget_usd: float | None = None,
    no_queue: bool = False,
    clock: Clock | None = None,
) -> LintOutcome:
    tick = clock or utc_now
    vault_dir = Path(settings.paths.vault_dir)
    runs_dir = Path(settings.paths.runs_dir)
    review_queue = review_queue or ReviewQueue.from_settings(settings)
    cap = budget_usd if budget_usd is not None else settings.budgets.lint

    ctx = RunContext.create(runs_dir, "lint", clock=clock, cap_usd=cap)
    ctx.freeze_plan({"mechanical_only": mechanical_only, "no_queue": no_queue, "budget": cap})

    claims, wikis, skipped = _scan_vault(vault_dir)

    broken_affects = _broken_affects(claims, wikis)
    duplicate_ids = _duplicate_claim_ids(claims)
    orphan_wikis = _orphan_wikis(claims, wikis)
    conflicts_marked = 0 if no_queue else _mark_drifted_review_items(review_queue, vault_dir, tick)

    ctx.emit(
        EventName.STAGE_COMPLETED, stage="mechanical",
        payload={
            "frontmatter_skipped": len(skipped),
            "broken_affects": len(broken_affects),
            "duplicate_claim_ids": len(duplicate_ids),
            "orphan_wikis": len(orphan_wikis),
            "review_conflicts_marked": conflicts_marked,
        },
    )

    pairs_examined = 0
    contradictions_queued = 0
    budget_exhausted = False
    seen_path = runs_dir / LINT_SEEN_FILENAME
    seen_pairs = _load_seen(seen_path)

    if not mechanical_only:
        candidates = _candidate_pairs(claims, seen_pairs)

        def emit(event: str, payload: dict) -> None:
            ctx.emit(event, stage="contradiction", payload=payload)

        for a, b, slug, pair_id in candidates:
            seen_pairs.add(pair_id)
            pairs_examined += 1
            try:
                verdict = _confirm_contradiction(llm, ctx.ledger, emit, a, b)
            except BudgetExceeded:
                ctx.emit(EventName.BUDGET_EXHAUSTED, stage="contradiction", payload=ctx.ledger.snapshot())
                budget_exhausted = True
                break

            if verdict is None or verdict.verdict != "contradicts" or no_queue:
                continue
            wiki_ref = wikis.get(slug)
            if wiki_ref is None:
                continue
            wiki_path = vault_dir / wiki_ref.rel_path
            if not wiki_path.is_file():
                continue
            base_hash = content_hash(wiki_path.read_text(encoding="utf-8"))
            a_slug, b_slug = Path(a.source_rel_path).stem, Path(b.source_rel_path).stem
            item = ReviewItem(
                id=f"{ctx.run_id}-rv-{pairs_examined:04d}",
                created=tick(),
                producer="lint",
                run_id=ctx.run_id,
                tier=2,
                target=wiki_ref.rel_path,
                change_type=ChangeType.ADD_OPEN_CONTRADICTION,
                pattern_key=f"lint-contradiction::{slug}",
                importance="high",
                rationale=verdict.rationale,
                base_hash=base_hash,
                payload={"mode": "append_section"},
            )
            proposal = (
                "## Open Contradictions\n\n"
                f"- [[{a_slug}]] states \"{a.statement}\" while [[{b_slug}]] states "
                f"\"{b.statement}\" — {verdict.rationale}"
            )
            path = review_queue.enqueue(item, proposal, "replace")
            if path is None:
                ctx.emit(EventName.REVIEW_DEDUPED, stage="contradiction", payload={"pattern_key": item.pattern_key})
            else:
                contradictions_queued += 1
                ctx.emit(
                    EventName.REVIEW_ENQUEUED, stage="contradiction",
                    payload={"id": item.id, "pattern_key": item.pattern_key, "tier": 2},
                )

        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps(sorted(seen_pairs), indent=2), encoding="utf-8")

    top_actions: list[str] = []
    for ba in broken_affects[:5]:
        hint = f" (did you mean {ba['suggestion']!r}?)" if ba["suggestion"] else ""
        top_actions.append(f"broken affect {ba['slug']!r} in {ba['source']}{hint}")
    for slug in orphan_wikis[:5]:
        top_actions.append(f"orphan wiki concept: {slug} (no inbound claims)")
    if contradictions_queued:
        top_actions.append(f"{contradictions_queued} confirmed contradiction(s) queued for review")

    exit_code = 1 if (broken_affects or duplicate_ids) else 0
    summary = {
        "run_id": ctx.run_id,
        "mechanical": {
            "frontmatter_skipped": len(skipped),
            "broken_affects": len(broken_affects),
            "duplicate_claim_ids": len(duplicate_ids),
            "orphan_wikis": len(orphan_wikis),
            "review_conflicts_marked": conflicts_marked,
        },
        "semantic": {
            "skipped": mechanical_only,
            "pairs_examined": pairs_examined,
            "contradictions_queued": contradictions_queued,
            "budget_exhausted": budget_exhausted,
        },
        "top_actions": top_actions,
        "next": "mvault review list",
        "cost_usd": round(ctx.ledger.spent, 6),
        "exit_code": exit_code,
    }
    ctx.write_summary(summary)
    ctx.emit(EventName.RUN_COMPLETED, stage="summary", payload=summary)
    return LintOutcome(exit_code, ctx.run_id, ctx.run_dir, summary)
