"""Pipeline wrapper: draft a wiki-body addition or a full new entry.

Thin dispatch shim around `contracts.wiki_draft.WikiDraftContract` — the
ingest pipeline calls `draft_extend` for an 'extends' relation (tier-2
edit-wiki-body) and `draft_new_entry` for a NEW concept that hit the run's
occurrence tally (tier-3 new-wiki-page).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mastervault.contracts.wiki_draft import WikiDraftContract
from mastervault.core.budget import BudgetLedger
from mastervault.providers.llm import LLMProvider


@dataclass
class DraftedWiki:
    ok: bool
    body_markdown: str = ""
    aliases: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    hard_fails: list[str] = field(default_factory=list)


def _dispatch(
    llm: LLMProvider,
    *,
    mode: str,
    concept_name: str,
    domain: str,
    evidence: str,
    ledger: BudgetLedger | None,
    emit: Callable[[str, dict], None] | None,
) -> DraftedWiki:
    contract = WikiDraftContract()
    result = contract.dispatch(
        llm,
        {"mode": mode, "concept_name": concept_name, "domain": domain, "evidence": evidence},
        {"mode": mode},
        ledger=ledger,
        emit=emit,
    )
    if not result.ok or result.parsed is None:
        return DraftedWiki(ok=False, cost_usd=result.cost_usd, hard_fails=result.hard_fails or ["no output"])
    return DraftedWiki(
        ok=True,
        body_markdown=result.parsed.body_markdown,
        aliases=list(result.parsed.aliases),
        cost_usd=result.cost_usd,
    )


def draft_extend(
    llm: LLMProvider,
    *,
    concept_name: str,
    domain: str,
    evidence: str,
    ledger: BudgetLedger | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> DraftedWiki:
    """One-paragraph addition to an existing wiki entry's body."""
    return _dispatch(
        llm,
        mode="extend",
        concept_name=concept_name,
        domain=domain,
        evidence=evidence,
        ledger=ledger,
        emit=emit,
    )


def draft_new_entry(
    llm: LLMProvider,
    *,
    concept_name: str,
    domain: str,
    evidence: str,
    ledger: BudgetLedger | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> DraftedWiki:
    """Full 5-section entry body for a brand-new concept."""
    return _dispatch(
        llm,
        mode="new",
        concept_name=concept_name,
        domain=domain,
        evidence=evidence,
        ledger=ledger,
        emit=emit,
    )
