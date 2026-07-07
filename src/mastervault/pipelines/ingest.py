"""Ingest pipeline: raw files -> source notes -> indexed -> concept-routed.

Stages (see module docstrings in `mastervault.ingest` for the per-stage
contracts): PLAN (enumerate + dedupe + freeze) -> EXTRACT+WRITE per unit ->
INDEX (one `sync_vault` call) -> CONCEPT MATCH + CORPUS CHECK + ROUTE over
every completed unit's claims -> SUMMARY.

Budget-exhaustion special case: a unit skipped with `reason="budget"` is
NOT treated as permanently terminal by this pipeline's own resume logic (see
`_pending_units`), even though `UNIT_SKIPPED` is terminal for the generic
`core.events` machinery. That is the only way a "budget-exhaust exits 3,
--resume completes rest" contract is honorable without changing the shared
event vocabulary: the run stays fully resumable, it just needs a resume
(optionally with a larger `--budget`) to pick the skipped units back up.

The route phase (concept-match + corpus-check + linker + review enqueue) is
idempotent by construction — the linker refuses to double-link, and
`ReviewQueue.enqueue` dedupes by content — so it always re-runs in full over
every currently-completed unit rather than tracking its own resume state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mastervault.config import Settings
from mastervault.core.errors import EXIT_CODES, BudgetExceeded, ResumeConflict
from mastervault.core.events import Clock, Event, EventName, utc_now
from mastervault.core.runctx import RunContext
from mastervault.ingest.concept_match import MatchResult, match_claim
from mastervault.ingest.convert import discover_units, read_raw_text
from mastervault.ingest.corpus_check import adjudicate
from mastervault.ingest.extract import extract_claims, guess_source_type, summary_sentences
from mastervault.ingest.linker import insert_wikilink
from mastervault.ingest.validate import validate_source_note
from mastervault.ingest.wiki_draft import draft_extend, draft_new_entry
from mastervault.models import (
    ChangeType,
    Domain,
    NoteStatus,
    ReviewItem,
    SourceNote,
    content_hash,
)
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.providers.llm import LLMProvider
from mastervault.providers.prices import cost as price_cost
from mastervault.providers.prices import estimate_tokens
from mastervault.review.apply import apply as apply_review_item
from mastervault.review.queue import ReviewQueue
from mastervault.storage.base import DocumentRow, StorageBackend
from mastervault.sync import sync_vault
from mastervault.sync.indexer import wiki_definition_text
from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    join_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
    split_frontmatter,
)
from mastervault.vaultfs.notes import slugify

EXTRACT_MAX_TOKENS = 1024  # matches Contract.dispatch's default, for cost estimates


@dataclass
class IngestOutcome:
    exit_code: int
    run_id: str
    run_dir: Path
    summary: dict[str, Any]


def _title_from_stem(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip().title() or stem


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def _rel_unit_id(root: Path, f: Path) -> str:
    base = root if root.is_dir() else root.parent
    rel = f.resolve().relative_to(base.resolve())
    return slugify(str(rel.with_suffix("")).replace("/", "-").replace("\\", "-"))


def _existing_provenance_hashes(vault_dir: Path) -> set[str]:
    hashes: set[str] = set()
    if not vault_dir.is_dir():
        return hashes
    for p in vault_dir.rglob("*.md"):
        try:
            data, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError, UnicodeDecodeError):
            continue
        h = data.get("provenance_hash")
        if isinstance(h, str):
            hashes.add(h)
    return hashes


def _render_source_body(title: str, raw_text: str) -> str:
    summary = summary_sentences(raw_text, 2) or "(no summary available)"
    return f"# {title}\n\n## Summary\n\n{summary}\n\n## Content\n\n{raw_text.strip()}\n"


def _write_source_note(path: Path, model: SourceNote, body: str, provenance_hash: str) -> None:
    """Like `vaultfs.notes.write_note`, plus one extra dedupe-only frontmatter key."""
    data = model.model_dump(mode="json", exclude_none=True)
    data["provenance_hash"] = provenance_hash
    yaml_str = serialize_frontmatter(data)
    normalized_body = body.strip("\n")
    text = join_frontmatter(yaml_str, f"\n{normalized_body}\n" if normalized_body else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _estimate_plan_cost(units: list[dict[str, Any]], settings: Settings) -> float:
    from mastervault.providers.llm import resolve_model

    model = resolve_model(settings, "small")
    return sum(price_cost(model, u["est_tokens"], EXTRACT_MAX_TOKENS) for u in units)


def _pending_units(
    units: list[dict[str, Any]], completed: dict[str, Event]
) -> list[dict[str, Any]]:
    """Units still owed work: never-attempted, plus budget-skips (retryable)."""
    pending = []
    for u in units:
        ev = completed.get(u["unit_id"])
        if ev is None or ev.event == EventName.UNIT_SKIPPED and ev.payload.get("reason") == "budget":
            pending.append(u)
    return pending


def _emit_adapter(ctx: RunContext, stage: str, unit: str | None) -> Callable[[str, dict], None]:
    def _emit(event: str, payload: dict) -> None:
        ctx.emit(event, stage=stage, unit=unit, payload=payload)

    return _emit


def _next_item_id(ctx: RunContext, counter: list[int]) -> str:
    counter[0] += 1
    return f"{ctx.run_id}-rv-{counter[0]:04d}"


def _enqueue(
    review_queue: ReviewQueue,
    ctx: RunContext,
    counter: list[int],
    *,
    tick: Clock,
    unit_id: str,
    tier: int,
    target: str,
    change_type: ChangeType,
    pattern_key: str,
    importance: str,
    rationale: str,
    base_hash: str,
    proposal: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    auto_approve: bool,
    vault_dir: Path,
    announce: Callable[[str], None],
) -> None:
    item = ReviewItem(
        id=_next_item_id(ctx, counter),
        created=tick(),
        producer="ingest",
        run_id=ctx.run_id,
        tier=tier,
        target=target,
        change_type=change_type,
        pattern_key=pattern_key,
        importance=importance,
        rationale=rationale,
        base_hash=base_hash,
        payload=payload or {},
    )
    path = review_queue.enqueue(item, proposal, kind)  # type: ignore[arg-type]
    if path is None:
        ctx.emit(EventName.REVIEW_DEDUPED, stage="route", unit=unit_id, payload={"pattern_key": pattern_key})
        return
    ctx.emit(
        EventName.REVIEW_ENQUEUED,
        stage="route",
        unit=unit_id,
        payload={"id": item.id, "pattern_key": pattern_key, "tier": tier},
    )
    if auto_approve and tier == 2:
        result = apply_review_item(path, vault_root=vault_dir, queue=review_queue, clock=tick)
        announce(f"auto-approved {item.id} -> {target}")
        ctx.emit(EventName.AUTO_APPLIED, stage="route", unit=unit_id, payload={"id": item.id, "target": target})
        _ = result  # ConflictResult is logged via the queue's own conflict marker


def _wiki_doc(backend: StorageBackend, domain: str, slug: str) -> DocumentRow | None:
    rows = backend.get_documents([f"wiki:{domain}:{slug}"])
    return rows[0] if rows else None


def _route_claim(
    *,
    claim_id: str,
    statement: str,
    match: MatchResult,
    llm: LLMProvider,
    backend: StorageBackend,
    ctx: RunContext,
    review_queue: ReviewQueue,
    counter: list[int],
    tick: Clock,
    unit_id: str,
    source_rel_path: str,
    current_body: str,
    new_concept_tally: dict[str, list[tuple[str, str]]],
    affects_candidates: list[str],
    auto_approve: bool,
    vault_dir: Path,
    announce: Callable[[str], None],
    counters: dict[str, int],
) -> str:
    """Route one claim's concept match. Returns the (possibly wikilinked) body."""
    if match.kind == "exists" and match.matched_alias is not None:
        link = insert_wikilink(current_body, match.matched_alias, match.wiki_slug or "")
        if link.applied:
            counters["linked"] += 1
            ctx.emit(
                EventName.AUTO_APPLIED,
                stage="route",
                unit=unit_id,
                payload={
                    "target": source_rel_path,
                    "alias": match.matched_alias,
                    "slug": match.wiki_slug,
                    "kind": "wikilink",
                },
            )
            return link.body
        return current_body

    if match.kind == "exists":
        # Confident KNN match with no literal alias text to anchor a link on:
        # still worth a human-reviewable cross-ref rather than silently dropped.
        doc = _wiki_doc(backend, match.domain or "", match.wiki_slug or "")
        if doc is not None:
            _enqueue(
                review_queue, ctx, counter, tick=tick, unit_id=unit_id, tier=2,
                target=doc.rel_path, change_type=ChangeType.ADD_CROSS_REF,
                pattern_key=f"ingest-cross-ref::{unit_id}", importance="normal",
                rationale=f"Claim {claim_id!r} matches {match.wiki_slug!r} at similarity "
                f"{match.similarity:.2f} with no literal alias text to auto-link.",
                base_hash=doc.content_hash,
                proposal=f"*Also supported by [[{unit_id}]]: {statement}*",
                kind="replace", payload={"mode": "append_section"},
                auto_approve=auto_approve, vault_dir=vault_dir, announce=announce,
            )
            counters["tier2"] += 1
        return current_body

    if match.kind == "candidate":
        doc = _wiki_doc(backend, match.domain or "", match.wiki_slug or "")
        if doc is None:
            return current_body
        wiki_text = wiki_definition_text(doc.body)
        pair = adjudicate(
            llm,
            claim_statement=statement,
            wiki_slug=match.wiki_slug or "",
            wiki_text=wiki_text,
            ledger=ctx.ledger,
            emit=_emit_adapter(ctx, "corpus_check", unit_id),
        )
        if not pair.ok:
            return current_body
        if pair.relation == "supports":
            _enqueue(
                review_queue, ctx, counter, tick=tick, unit_id=unit_id, tier=2,
                target=doc.rel_path, change_type=ChangeType.ADD_CROSS_REF,
                pattern_key=f"ingest-cross-ref::{unit_id}", importance="normal",
                rationale=pair.rationale, base_hash=doc.content_hash,
                proposal=f"*Also supported by [[{unit_id}]]: {statement}*",
                kind="replace", payload={"mode": "append_section"},
                auto_approve=auto_approve, vault_dir=vault_dir, announce=announce,
            )
            counters["tier2"] += 1
        elif pair.relation == "extends":
            drafted = draft_extend(
                llm, concept_name=_title_from_slug(match.wiki_slug or ""),
                domain=match.domain or "", evidence=statement,
                ledger=ctx.ledger, emit=_emit_adapter(ctx, "wiki_draft", unit_id),
            )
            if drafted.ok:
                _enqueue(
                    review_queue, ctx, counter, tick=tick, unit_id=unit_id, tier=2,
                    target=doc.rel_path, change_type=ChangeType.EDIT_WIKI_BODY,
                    pattern_key=f"ingest-extend::{unit_id}", importance="normal",
                    rationale=pair.rationale, base_hash=doc.content_hash,
                    proposal=drafted.body_markdown, kind="replace",
                    payload={"mode": "append_section"},
                    auto_approve=auto_approve, vault_dir=vault_dir, announce=announce,
                )
                counters["tier2"] += 1
        elif pair.relation == "challenges":
            _enqueue(
                review_queue, ctx, counter, tick=tick, unit_id=unit_id, tier=3,
                target=doc.rel_path, change_type=ChangeType.ADD_OPEN_CONTRADICTION,
                pattern_key=f"ingest-contradiction::{unit_id}", importance="high",
                rationale=pair.rationale, base_hash=doc.content_hash,
                proposal=(
                    "## Open Contradictions\n\n"
                    f"- New source [[{unit_id}]] states: \"{statement}\" — {pair.rationale}"
                ),
                kind="replace", payload={"mode": "append_section"},
                auto_approve=False, vault_dir=vault_dir, announce=announce,
            )
            counters["tier3"] += 1
        return current_body

    # match.kind == "new"
    label = affects_candidates[0] if affects_candidates else slugify(statement, max_len=40)
    new_concept_tally.setdefault(label, []).append((claim_id, statement))
    return current_body


def _draft_new_concepts(
    tally: dict[str, list[tuple[str, str]]],
    *,
    llm: LLMProvider,
    domain: Domain,
    vault_dir: Path,
    ctx: RunContext,
    review_queue: ReviewQueue,
    counter: list[int],
    tick: Clock,
    auto_approve: bool,
    announce: Callable[[str], None],
    counters: dict[str, int],
) -> None:
    for label, occurrences in tally.items():
        if len(occurrences) < 2:
            continue
        slug = slugify(label, max_len=50)
        concept_name = _title_from_slug(slug)
        evidence = "\n".join(f"- {stmt}" for _cid, stmt in occurrences)
        drafted = draft_new_entry(
            llm, concept_name=concept_name, domain=domain.value, evidence=evidence,
            ledger=ctx.ledger, emit=_emit_adapter(ctx, "wiki_draft", f"new::{slug}"),
        )
        if not drafted.ok:
            continue

        wiki_path = vault_dir / domain.value / "wiki" / f"{slug}.md"
        rel_path = wiki_path.relative_to(vault_dir).as_posix()
        today = tick().date()
        if wiki_path.is_file():
            continue  # a concept with this slug already exists — not actually new
        stub_data = {
            "domain": domain.value,
            "type": "wiki",
            "title": concept_name,
            "aliases": drafted.aliases,
            "tags": [],
            "status": NoteStatus.DRAFT.value,
            "created": today.isoformat(),
            "updated": today.isoformat(),
        }
        stub_text = join_frontmatter(serialize_frontmatter(stub_data), "\n(pending review)\n")
        wiki_path.parent.mkdir(parents=True, exist_ok=True)
        wiki_path.write_text(stub_text, encoding="utf-8")
        base_hash = content_hash(stub_text)

        _enqueue(
            review_queue, ctx, counter, tick=tick, unit_id=f"new::{slug}", tier=3,
            target=rel_path, change_type=ChangeType.NEW_WIKI_PAGE,
            pattern_key=f"ingest-new-wiki::{slug}", importance="normal",
            rationale=f"{len(occurrences)} claims this run bear on {concept_name!r} "
            "with no existing wiki concept close enough to attach to.",
            base_hash=base_hash, proposal=drafted.body_markdown, kind="replace",
            payload={"mode": "full_file"}, auto_approve=False,
            vault_dir=vault_dir, announce=announce,
        )
        counters["new_concepts"] += 1


def run_ingest(
    path: Path | str,
    domain: Domain,
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    *,
    budget_usd: float | None = None,
    dry_run: bool = False,
    resume_run_id: str | None = None,
    auto_approve: bool = False,
    fail_fast: bool = False,
    review_queue: ReviewQueue | None = None,
    clock: Clock | None = None,
    announce: Callable[[str], None] | None = None,
) -> IngestOutcome:
    path = Path(path)
    tick = clock or utc_now
    announce = announce or (lambda _msg: None)
    vault_dir = Path(settings.paths.vault_dir)
    runs_dir = Path(settings.paths.runs_dir)
    review_queue = review_queue or ReviewQueue.from_settings(settings)
    cap = budget_usd if budget_usd is not None else settings.budgets.ingest

    try:
        if resume_run_id is not None:
            ctx = RunContext.resume(runs_dir / resume_run_id, clock=clock, cap_usd=cap)
        else:
            ctx = RunContext.create(runs_dir, "ingest", clock=clock, cap_usd=cap)
    except ResumeConflict as exc:
        return IngestOutcome(
            exit_code=EXIT_CODES["resume-conflict"],
            run_id=resume_run_id or "",
            run_dir=runs_dir / (resume_run_id or ""),
            summary={"error": str(exc)},
        )

    if resume_run_id is None:
        files = discover_units(path)
        existing_hashes = _existing_provenance_hashes(vault_dir)
        units: list[dict[str, Any]] = []
        for f in files:
            raw = read_raw_text(f)
            sha = content_hash(raw)
            if sha in existing_hashes:
                continue
            units.append(
                {
                    "unit_id": _rel_unit_id(path, f),
                    "src_path": str(f),
                    "sha": sha,
                    "est_tokens": estimate_tokens(raw),
                }
            )
        plan = {
            "args": {
                "path": str(path),
                "domain": domain.value,
                "budget": cap,
                "auto_approve": auto_approve,
                "fail_fast": fail_fast,
            },
            "units": units,
        }
        ctx.freeze_plan(plan)
    else:
        plan = ctx.plan or {}
        domain = Domain(plan.get("args", {}).get("domain", domain.value))
        auto_approve = plan.get("args", {}).get("auto_approve", auto_approve)
        fail_fast = plan.get("args", {}).get("fail_fast", fail_fast)

    units: list[dict[str, Any]] = plan.get("units", [])

    if dry_run:
        summary = {
            "run_id": ctx.run_id,
            "dry_run": True,
            "units_planned": len(units),
            "estimated_cost_usd": round(_estimate_plan_cost(units, settings), 4),
            "budget_cap_usd": cap,
        }
        ctx.write_summary(summary)
        return IngestOutcome(EXIT_CODES["ok"], ctx.run_id, ctx.run_dir, summary)

    pending = _pending_units(units, ctx.completed_units)

    # Resume integrity: a pending unit's source must not have changed since the
    # plan was frozen (a completed/failed unit is never re-verified — it is
    # genuinely done and its source may have moved on since).
    if resume_run_id is not None:
        for u in pending:
            src = Path(u["src_path"])
            if not src.is_file():
                reason = f"unit {u['unit_id']}: source file missing: {src}"
                ctx.write_summary({"error": reason})
                return IngestOutcome(EXIT_CODES["resume-conflict"], ctx.run_id, ctx.run_dir, {"error": reason})
            current_sha = content_hash(read_raw_text(src))
            if current_sha != u["sha"]:
                reason = (
                    f"unit {u['unit_id']}: source file changed since the plan was frozen "
                    f"(expected sha {u['sha']}, now {current_sha})"
                )
                ctx.write_summary({"error": reason})
                return IngestOutcome(EXIT_CODES["resume-conflict"], ctx.run_id, ctx.run_dir, {"error": reason})

    max_claims = settings.ingestion.max_claims_per_doc
    any_hard_fail = False
    budget_exhausted = False
    # ctx.completed_units only reflects prior invocations (populated at resume
    # time from disk) — it never live-updates from this invocation's own
    # emit() calls, so newly-completed units are tracked locally here.
    newly_completed: dict[str, str] = {}

    for idx, u in enumerate(pending):
        unit_id = u["unit_id"]
        src = Path(u["src_path"])
        try:
            raw_text = read_raw_text(src)
        except OSError as exc:
            ctx.emit(EventName.UNIT_FAILED, stage="extract", unit=unit_id, payload={"reason": str(exc)})
            any_hard_fail = True
            if fail_fast:
                break
            continue

        source_type = guess_source_type(src.name, raw_text)
        title = _title_from_stem(src.stem)

        try:
            extraction = extract_claims(
                llm, unit_slug=unit_id, title=title, source_type=source_type,
                domain=domain.value, body=raw_text, max_claims=max_claims,
                ledger=ctx.ledger, emit=_emit_adapter(ctx, "extract", unit_id),
            )
        except BudgetExceeded:
            for rest in pending[idx:]:
                ctx.emit(EventName.UNIT_SKIPPED, stage="extract", unit=rest["unit_id"], payload={"reason": "budget"})
            ctx.emit(EventName.BUDGET_EXHAUSTED, stage="extract", payload=ctx.ledger.snapshot())
            budget_exhausted = True
            break

        if not extraction.ok:
            ctx.emit(EventName.UNIT_FAILED, stage="extract", unit=unit_id, payload={"reasons": extraction.hard_fails})
            any_hard_fail = True
            if fail_fast:
                break
            continue

        note_path = vault_dir / domain.value / "sources" / f"{unit_id}.md"
        model = SourceNote(
            domain=domain,
            title=title,
            tags=[source_type.value],
            status=NoteStatus.PROCESSED,
            created=tick().date(),
            updated=tick().date(),
            source_type=source_type,
            key_claims=extraction.claims,
            provenance=str(src),
        )
        _write_source_note(note_path, model, _render_source_body(title, raw_text), provenance_hash=u["sha"])

        report = validate_source_note(note_path, fix=True, settings=settings)
        if report.status == "hard_fail":
            ctx.emit(EventName.UNIT_FAILED, stage="validate", unit=unit_id, payload={"reasons": report.hard_fails})
            any_hard_fail = True
            if fail_fast:
                break
            continue

        note_rel = note_path.relative_to(vault_dir).as_posix()
        ctx.emit(
            EventName.UNIT_COMPLETED, stage="write", unit=unit_id,
            payload={"note": note_rel, "n_claims": len(extraction.claims)},
        )
        newly_completed[unit_id] = note_rel

    # -- INDEX: one sync call, regardless of how the loop above ended ----------
    sync_report = sync_vault(vault_dir, backend, embedder)
    ctx.emit(
        EventName.STAGE_COMPLETED, stage="index",
        payload={"docs_upserted": sync_report.docs_upserted, "records_embedded": sync_report.records_embedded},
    )

    # -- CONCEPT MATCH + CORPUS CHECK + ROUTE, over every completed unit --------
    counters = {"linked": 0, "tier2": 0, "tier3": 0, "new_concepts": 0, "claims": 0}
    new_concept_tally: dict[str, list[tuple[str, str]]] = {}
    review_counter = [0]

    written: dict[str, str] = {
        unit_id: ev.payload.get("note")
        for unit_id, ev in ctx.completed_units.items()
        if ev.event == EventName.UNIT_COMPLETED and ev.payload.get("note")
    }
    written.update(newly_completed)

    for unit_id, note_rel in written.items():
        note_path = vault_dir / note_rel
        if not note_path.is_file():
            continue
        try:
            loaded_text = note_path.read_text(encoding="utf-8")
            data, _ = parse_frontmatter(loaded_text)
        except (OSError, FrontmatterError):
            continue
        raw_claims = data.get("key_claims") or []
        source_rel_path = note_path.relative_to(vault_dir).as_posix()
        yaml_str, current_body, _had = split_frontmatter(loaded_text)

        route_budget_dead = False
        for claim in raw_claims:
            if route_budget_dead:
                break
            counters["claims"] += 1
            statement = claim.get("statement", "")
            affects = list(claim.get("affects") or [])
            try:
                match = match_claim(statement, affects, backend, embedder, settings.ingestion)
                current_body = _route_claim(
                    claim_id=claim.get("id", ""), statement=statement, match=match, llm=llm,
                    backend=backend, ctx=ctx, review_queue=review_queue, counter=review_counter,
                    tick=tick, unit_id=unit_id, source_rel_path=source_rel_path,
                    current_body=current_body, new_concept_tally=new_concept_tally,
                    affects_candidates=affects, auto_approve=auto_approve, vault_dir=vault_dir,
                    announce=announce, counters=counters,
                )
            except BudgetExceeded:
                budget_exhausted = True
                route_budget_dead = True

        note_path.write_text(join_frontmatter(yaml_str, current_body), encoding="utf-8")

    if not budget_exhausted:
        try:
            _draft_new_concepts(
                new_concept_tally, llm=llm, domain=domain, vault_dir=vault_dir, ctx=ctx,
                review_queue=review_queue, counter=review_counter, tick=tick,
                auto_approve=auto_approve, announce=announce, counters=counters,
            )
        except BudgetExceeded:
            budget_exhausted = True

    exit_code = (
        EXIT_CODES["budget-exhausted"] if budget_exhausted
        else EXIT_CODES["completed-with-failures"] if any_hard_fail
        else EXIT_CODES["ok"]
    )
    summary = {
        "run_id": ctx.run_id,
        "units_total": len(units),
        "units_completed": len(written),
        "claims_routed": counters["claims"],
        "wikilinks_inserted": counters["linked"],
        "tier2_enqueued": counters["tier2"],
        "tier3_enqueued": counters["new_concepts"] + counters["tier3"],
        "new_concepts_drafted": counters["new_concepts"],
        "docs_upserted": sync_report.docs_upserted,
        "records_embedded": sync_report.records_embedded,
        "cost_usd": round(ctx.ledger.spent, 6),
        "budget_cap_usd": cap,
        "exit_code": exit_code,
    }
    ctx.write_summary(summary)
    ctx.emit(EventName.RUN_COMPLETED, stage="summary", payload=summary)
    return IngestOutcome(exit_code, ctx.run_id, ctx.run_dir, summary)
