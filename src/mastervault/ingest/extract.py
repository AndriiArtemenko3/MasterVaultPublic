"""Claim extraction + note-shape helpers for the ingest pipeline.

`extract_claims` wraps `ClaimExtractionContract` for one raw-text unit: it
assigns canonical `<unit-slug>-NN` ids up front (the same numbering
`ingest.validate.validate_source_note` would assign on its own pass, so the
post-write validation gate reports zero id autofixes on the happy path) and
keeps the raw `ClaimCandidate` list around so `ingest.concept_match` can read
`affects_candidates` without re-deriving them from the canonical `Claim`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mastervault.contracts.claims import ClaimCandidate, ClaimExtractionContract
from mastervault.core.budget import BudgetLedger
from mastervault.models import Claim, SourceType
from mastervault.prompts.untrusted import fence
from mastervault.providers.llm import LLMProvider

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Ordered most-specific-first: the first substring match in filename+lede wins.
_KEYWORD_SOURCE_TYPES: tuple[tuple[str, SourceType], ...] = (
    ("postmortem", SourceType.POSTMORTEM),
    ("case-study", SourceType.CASE_STUDY),
    ("case_study", SourceType.CASE_STUDY),
    ("competitor", SourceType.COMPETITOR_NOTE),
    ("integration", SourceType.INTEGRATION_GUIDE),
    ("checklist", SourceType.CHECKLIST),
    ("transcript", SourceType.CALL_TRANSCRIPT),
    ("call", SourceType.CALL_TRANSCRIPT),
    ("email", SourceType.EMAIL_THREAD),
    ("chat", SourceType.CHAT_LOG),
    ("faq", SourceType.FAQ),
    ("policy", SourceType.POLICY),
    ("sop", SourceType.SOP),
    ("bug", SourceType.BUG_REPORT),
    ("ticket", SourceType.TICKET),
    ("process", SourceType.PROCESS),
    ("log", SourceType.LOG),
    ("contract", SourceType.CONTRACT),
    ("invoice", SourceType.INVOICE),
    ("memo", SourceType.MEMO),
    ("csat", SourceType.CSAT),
    ("lead", SourceType.LEAD_NOTE),
    ("proposal", SourceType.PROPOSAL),
    ("project", SourceType.PROJECT),
)


def guess_source_type(filename: str, text: str) -> SourceType:
    """Cheap keyword heuristic over the filename + the text's opening; 'other' fallback."""
    haystack = f"{filename}\n{text[:500]}".lower()
    for keyword, source_type in _KEYWORD_SOURCE_TYPES:
        if keyword in haystack:
            return source_type
    return SourceType.OTHER


def summary_sentences(text: str, n: int = 2) -> str:
    """First `n` sentences of `text`, whitespace-collapsed."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(collapsed)
    return " ".join(parts[:n]).strip()


@dataclass
class ExtractResult:
    ok: bool
    claims: list[Claim] = field(default_factory=list)
    candidates: list[ClaimCandidate] = field(default_factory=list)
    cost_usd: float = 0.0
    hard_fails: list[str] = field(default_factory=list)


def extract_claims(
    llm: LLMProvider,
    *,
    unit_slug: str,
    title: str,
    source_type: SourceType,
    domain: str,
    body: str,
    max_claims: int,
    ledger: BudgetLedger | None = None,
    emit=None,
) -> ExtractResult:
    """Dispatch claim extraction for one unit and assign canonical claim ids."""
    contract = ClaimExtractionContract()
    result = contract.dispatch(
        llm,
        {
            "title": title,
            "source_type": source_type.value,
            "domain": domain,
            "body": fence(body, "DOCUMENT"),
        },
        {"max_claims": max_claims},
        ledger=ledger,
        emit=emit,
    )
    if not result.ok or result.parsed is None:
        return ExtractResult(
            ok=False, cost_usd=result.cost_usd, hard_fails=result.hard_fails or ["no output"]
        )

    claims = [
        Claim(
            id=f"{unit_slug}-{i:02d}",
            statement=candidate.statement,
            confidence=candidate.confidence,
            affects=list(candidate.affects_candidates),
        )
        for i, candidate in enumerate(result.parsed.claims, start=1)
    ]
    return ExtractResult(
        ok=True, claims=claims, candidates=list(result.parsed.claims), cost_usd=result.cost_usd
    )
