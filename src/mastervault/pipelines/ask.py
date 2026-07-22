"""Ask pipeline: agentic multi-round retrieval, judged, grounded, cited.

Round 0 is the raw question; the sufficiency judge may propose up to 3
follow-up queries per round, subject to three MECHANICAL guards the judge
never controls:

- a hard cap at `settings.ask.max_rounds` (the judge is told how many rounds
  remain so it can plan, but the pipeline enforces the stop);
- a novelty floor — a round that adds zero new record_ids to the evidence
  pool forces a sufficient-with-gaps stop, since another round of the same
  ground can't help;
- a followup-dedup pass that drops any proposed query that differs from an
  already-tried query only by stopword shuffle; if every followup is
  dropped, the pipeline stops as sufficient.

A judge hard-fail (bad shape after retry) is treated the same as a
sufficient verdict: the pipeline stops and answers with whatever evidence it
has rather than looping forever on a broken judge.

The final answer is either a grounded LLM synthesis (citation-gated: any
`[<record-id>]` token not in the evidence pool is stripped with a warning,
and zero surviving citations forces the fallback below) or a deterministic
EXTRACTIVE answer built straight from the top-5 MMR-selected evidence cards.
Extractive is also where a `MockLLM` with nothing pushed for
"grounded_synthesis" naturally ends up: dispatch returns no parsed output,
which is exactly the same hard-fail path a broken real provider takes — no
special-casing needed to keep the mock path honest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mastervault.config import Settings
from mastervault.contracts.judge import SufficiencyJudgeContract
from mastervault.contracts.synthesis import GroundedSynthesisContract
from mastervault.core.errors import BudgetExceeded
from mastervault.core.events import Clock, EventName
from mastervault.core.runctx import RunContext
from mastervault.models import Confidence, Hit
from mastervault.prompts.untrusted import fence, neutralise
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.providers.llm import LLMProvider, StructuredOutputError
from mastervault.retrieval.channels import vector_channel
from mastervault.retrieval.mmr import mmr_select_texts
from mastervault.retrieval.search import hybrid_search
from mastervault.storage.base import StorageBackend

_TOKEN_RE = re.compile(r"\w+")
_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "to", "in", "on", "and", "or", "is",
        "are", "what", "how", "does", "do", "did", "with", "about", "at",
        "by", "as", "that", "this", "it", "be", "was", "were",
    }
)

NEAREST_WIKI_K = 5
EVIDENCE_CARDS_N = 15
EXTRACTIVE_CARDS_N = 5


@dataclass
class AskOutcome:
    exit_code: int
    run_id: str
    run_dir: Path
    answer_markdown: str
    confidence: str | None
    gaps: list[str]
    sources: list[dict[str, str]]
    trace: str
    extractive: bool
    zero_evidence: bool
    rounds: int
    cost_usd: float
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    nearest_wiki_titles: list[str] = field(default_factory=list)


def _significant_tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS)


def _dedupe_followups(followups: list[str], prior_queries: list[str]) -> list[str]:
    """Drop a followup that is only a stopword-shuffle of an already-tried query."""
    prior_signatures = {_significant_tokens(q) for q in prior_queries}
    out: list[str] = []
    seen: set[frozenset[str]] = set()
    for q in followups:
        sig = _significant_tokens(q)
        if sig in prior_signatures or sig in seen:
            continue
        seen.add(sig)
        out.append(q)
    return out


def _evidence_cards(hits: Iterable[Hit]) -> str:
    """Render evidence for a prompt, fenced as untrusted corpus text.

    Card text is document content, so it is delimited rather than dropped into
    the instructions. The record id is ours and stays outside the payload; the
    text is neutralised so a document cannot close the fence and keep writing.
    """
    lines = [f"[{h.record_id}] {neutralise(' '.join(h.text.split()))}" for h in hits]
    body = "\n".join(lines) if lines else "(no evidence gathered yet)"
    return fence(body, "EVIDENCE")


def _mmr_pick(question: str, hits: list[Hit], n: int, lambda_: float) -> list[Hit]:
    if not hits:
        return []
    tokens = _TOKEN_RE.findall(question.lower())
    candidates = [(h.record_id, h.text) for h in hits]
    picks = mmr_select_texts(tokens, candidates, lambda_=lambda_, n=n)
    by_id = {h.record_id: h for h in hits}
    return [by_id[rid] for rid, _score in picks if rid in by_id]


#: Fixed-point bound for the citation sweeps below.
_CITATION_SCAN_PASSES = 8
_ANY_BRACKET_RE = re.compile(r"[\[\]]")


def _apply_citation_gate(text: str, evidence_ids: set[str]) -> tuple[str, list[str], list[str]]:
    """Strip any `[<id>]` token not in the evidence pool.

    Returns (clean, valid_ids, warnings). `valid_ids` is ordered by first
    appearance in the answer and de-duplicated -- deliberately a list, not a
    set: it becomes the rendered `sources` list, and set iteration order varies
    with PYTHONHASHSEED, which would make the citation order of an otherwise
    identical answer differ between processes.
    """
    warnings: list[str] = []
    valid_ids: list[str] = []
    seen: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        if token in evidence_ids:
            if token not in seen:
                seen.add(token)
                valid_ids.append(token)
            return m.group(0)
        warnings.append(f"stripped citation not in evidence pool: [{token}]")
        return ""

    # Same fixed-point sweep as _strip_forged_citations: one pass would let
    # "[cl[claim:real]aim:forged]" collapse into an unvalidated "[claim:forged]".
    cleaned = text
    for _ in range(_CITATION_SCAN_PASSES):
        swept = _CITATION_RE.sub(_sub, cleaned)
        if swept == cleaned:
            return cleaned, valid_ids, warnings
        cleaned = swept
    warnings.append("removed all bracket tokens: citation text kept reassembling under stripping")
    return _ANY_BRACKET_RE.sub("", cleaned), valid_ids, warnings


#: Every record-id namespace the index issues. Claim/chunk/wiki records come
#: from the embedding layer; `sync.indexer` additionally mints document-level
#: ids as "<note-type>:<rel-path>", and `retrieval.search` surfaces those
#: verbatim as a hit's record_id -- so source/decision/strategy/wiki are all
#: citation-shaped too. Anything outside this set is ordinary prose ("[sic]").
_RECORD_NAMESPACES = ("claim", "chunk", "wiki", "source", "decision", "strategy")
_FORGED_CITATION_RE = re.compile(
    r"\[((?:" + "|".join(_RECORD_NAMESPACES) + r"):[^\[\]]+)\]"
)


def _strip_forged_citations(text: str, evidence_ids: set[str]) -> tuple[str, list[str]]:
    """Remove record-shaped citations a document quoted at itself.

    The synthesis path is citation-gated, but the extractive path quotes
    document text verbatim -- so a document containing "[claim:made-up-01]"
    would have that token rendered into the answer as if it were a citation the
    pipeline issued. Anything citation-shaped that is not in the evidence pool
    is stripped here, so both answer paths obey the same rule: every citation
    in an answer names a record that was really retrieved.
    """
    warnings: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        if token in evidence_ids:
            return m.group(0)
        warnings.append(f"stripped forged citation embedded in document text: [{token}]")
        return ""

    # Re-scan until stable. A single pass is not enough: re.sub never re-reads
    # its own output, so "[cl[claim:real]aim:forged]" strips the inner token and
    # leaves the residue "[claim:forged]" -- a fake citation assembled out of the
    # deletion. Iterating to a fixed point closes that.
    cleaned = text
    for _ in range(_CITATION_SCAN_PASSES):
        swept = _FORGED_CITATION_RE.sub(_sub, cleaned)
        if swept == cleaned:
            return cleaned, warnings
        cleaned = swept
    # Pathological input that keeps reassembling: drop every bracket rather than
    # emit something citation-shaped that was never checked.
    warnings.append("removed all bracket tokens: citation text kept reassembling under stripping")
    return _ANY_BRACKET_RE.sub("", cleaned), warnings


def _extractive_answer(hits: list[Hit], evidence_ids: set[str]) -> tuple[str, list[str]]:
    lines = ["Extractive answer (no generative synthesis):", ""]
    warnings: list[str] = []
    for h in hits:
        cleaned, stripped = _strip_forged_citations(" ".join(h.text.split()), evidence_ids)
        warnings.extend(stripped)
        lines.append(f"- {cleaned.strip()} [{h.record_id}]")
    return "\n".join(lines), warnings


def _render_sources(hits: list[Hit]) -> list[dict[str, str]]:
    return [
        {"record_id": h.record_id, "rel_path": h.rel_path or h.doc_id}
        for h in hits
    ]


def _nearest_wiki_titles(
    backend: StorageBackend, embedder: EmbeddingProvider, question: str, k: int = NEAREST_WIKI_K
) -> list[str]:
    ids = [i for i in vector_channel(question, backend, embedder, k * 4) if i.startswith("wiki:")][:k]
    if not ids:
        return []
    return [doc.title for doc in backend.get_documents(ids)]


def run_ask(
    question: str,
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    *,
    domain: str | None = None,
    max_rounds: int | None = None,
    budget_usd: float | None = None,
    clock: Clock | None = None,
) -> AskOutcome:
    runs_dir = Path(settings.paths.runs_dir)
    cap = budget_usd if budget_usd is not None else settings.ask.budget_usd
    max_r = max_rounds if max_rounds is not None else settings.ask.max_rounds

    ctx = RunContext.create(runs_dir, "ask", clock=clock, cap_usd=cap)
    ctx.freeze_plan({"question": question, "domain": domain, "max_rounds": max_r, "budget": cap})

    def emit(event: str, payload: dict) -> None:
        ctx.emit(event, stage="ask", payload=payload)

    # -- zero-evidence short-circuit -------------------------------------------
    round0 = hybrid_search(question, settings, backend, embedder, domain=domain)
    if not round0.hits and round0.wiki_card is None:
        nearest = _nearest_wiki_titles(backend, embedder, question)
        message = "corpus has no grounding for this question"
        summary = {"run_id": ctx.run_id, "zero_evidence": True, "nearest_wiki_titles": nearest}
        ctx.write_summary(summary)
        return AskOutcome(
            exit_code=0, run_id=ctx.run_id, run_dir=ctx.run_dir, answer_markdown=message,
            confidence=None, gaps=[], sources=[], trace="0 rounds, 0 evidence items",
            extractive=True, zero_evidence=True, rounds=0, cost_usd=0.0,
            nearest_wiki_titles=nearest,
        )

    evidence_by_id: dict[str, Hit] = {}
    prior_queries: list[str] = [question]
    queries_this_round: list[str] = [question]
    missing_aspects: list[str] = []
    rounds_run = 0

    for round_idx in range(1, max_r + 1):
        rounds_run = round_idx
        new_ids = 0
        round_snapshot: dict[str, list[str]] = {}
        for q in queries_this_round:
            result = round0 if (round_idx == 1 and q == question) else hybrid_search(
                q, settings, backend, embedder, domain=domain
            )
            round_snapshot[q] = [h.record_id for h in result.hits]
            for h in result.hits:
                if h.record_id not in evidence_by_id:
                    evidence_by_id[h.record_id] = h
                    new_ids += 1
        (ctx.artifacts_dir / f"round-{round_idx}.json").write_text(
            json.dumps(round_snapshot, indent=2), encoding="utf-8"
        )

        remaining = max_r - round_idx
        evidence_summary = _evidence_cards(evidence_by_id.values())
        try:
            judge_result = SufficiencyJudgeContract().dispatch(
                llm,
                {"question": question, "evidence_summary": evidence_summary, "rounds_remaining": remaining},
                ledger=ctx.ledger,
                emit=emit,
            )
        except BudgetExceeded:
            emit("budget.exhausted", ctx.ledger.snapshot())
            break

        if not judge_result.ok or judge_result.parsed is None:
            # Judge hard-fail: treat as sufficient rather than loop forever.
            break

        verdict = judge_result.parsed
        missing_aspects = list(verdict.missing_aspects)

        if new_ids == 0 and round_idx > 1:
            # Novelty floor: another round over the same ground can't help.
            if not missing_aspects:
                missing_aspects = ["no new evidence found in this round"]
            break

        if verdict.sufficient:
            break

        followups = _dedupe_followups(verdict.followup_queries, prior_queries)
        if not followups:
            break

        prior_queries.extend(followups)
        queries_this_round = followups

    all_hits = list(evidence_by_id.values())
    warnings: list[str] = []
    extractive = False
    confidence: str | None = None
    gaps = list(missing_aspects)

    # Synthesis is always attempted; the mock-provider default is reached
    # naturally (not special-cased): an empty MockLLM push-registry for
    # "grounded_synthesis" yields parsed=None -> hard-fail -> extractive,
    # exactly like a real provider producing no usable structured output.
    top15 = _mmr_pick(question, all_hits, EVIDENCE_CARDS_N, settings.retrieval.mmr_lambda)
    missing_text = "\n".join(f"- {m}" for m in missing_aspects) or "(none)"
    try:
        synth_result = GroundedSynthesisContract().dispatch(
            llm,
            {"question": question, "evidence_cards": _evidence_cards(top15), "missing_aspects": missing_text},
            {"missing_aspects": missing_aspects},
            ledger=ctx.ledger,
            emit=emit,
        )
    except (BudgetExceeded, StructuredOutputError):
        synth_result = None

    # Every way synthesis can fail to produce a usable grounded answer -- it
    # raised, it came back not-ok, it parsed to nothing, or the citation gate
    # stripped every id -- funnels into the same extractive fallback.
    parsed = synth_result.parsed if synth_result is not None and synth_result.ok else None
    if parsed is None:
        extractive = True
    else:
        cleaned, valid_ids, strip_warnings = _apply_citation_gate(
            parsed.answer_markdown, set(evidence_by_id)
        )
        warnings.extend(strip_warnings)
        if not valid_ids:
            extractive = True
        else:
            answer_markdown = cleaned
            confidence = parsed.confidence.value
            gaps = list(parsed.gaps)
            cited_hits = [evidence_by_id[i] for i in valid_ids if i in evidence_by_id]

    if extractive:
        top5 = _mmr_pick(question, all_hits, EXTRACTIVE_CARDS_N, settings.retrieval.mmr_lambda)
        answer_markdown, forged_warnings = _extractive_answer(top5, set(evidence_by_id))
        warnings.extend(forged_warnings)
        confidence = Confidence.LOW.value if top5 else None
        cited_hits = top5

    trace = f"{rounds_run} round(s), {len(all_hits)} evidence item(s), ${ctx.ledger.spent:.4f} spent"
    summary = {
        "run_id": ctx.run_id,
        "zero_evidence": False,
        "rounds": rounds_run,
        "evidence_count": len(all_hits),
        "extractive": extractive,
        "cost_usd": round(ctx.ledger.spent, 6),
        "gaps": gaps,
    }
    ctx.write_summary(summary)
    ctx.emit(EventName.RUN_COMPLETED, stage="ask", payload=summary)

    return AskOutcome(
        exit_code=0,
        run_id=ctx.run_id,
        run_dir=ctx.run_dir,
        answer_markdown=answer_markdown,
        confidence=confidence,
        gaps=gaps,
        sources=_render_sources(cited_hits),
        trace=trace,
        extractive=extractive,
        zero_evidence=False,
        rounds=rounds_run,
        cost_usd=round(ctx.ledger.spent, 6),
        warnings=warnings,
        evidence=_render_sources(all_hits),
    )
