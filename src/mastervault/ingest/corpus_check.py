"""Pipeline wrapper: adjudicate one (claim, candidate-wiki) pairing.

Thin dispatch shim around `contracts.corpus_check.CorpusCheckContract` — the
ingest pipeline calls `adjudicate` once per candidate pairing produced by
`ingest.concept_match` and routes on the returned `relation`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mastervault.contracts.corpus_check import CorpusCheckContract
from mastervault.core.budget import BudgetLedger
from mastervault.prompts.untrusted import fence
from mastervault.providers.llm import LLMProvider


@dataclass
class AdjudicatedPair:
    relation: str | None  # None when the contract hard-failed even after retry
    rationale: str
    cost_usd: float
    hard_fails: list[str]

    @property
    def ok(self) -> bool:
        return self.relation is not None


def adjudicate(
    llm: LLMProvider,
    *,
    claim_statement: str,
    wiki_slug: str,
    wiki_text: str,
    ledger: BudgetLedger | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> AdjudicatedPair:
    contract = CorpusCheckContract()
    result = contract.dispatch(
        llm,
        {
            "claim_statement": fence(claim_statement, "CLAIM"),
            "wiki_slug": wiki_slug,
            "wiki_text": fence(wiki_text, "WIKI ENTRY"),
        },
        ledger=ledger,
        emit=emit,
    )
    if not result.ok or result.parsed is None:
        return AdjudicatedPair(
            relation=None,
            rationale="",
            cost_usd=result.cost_usd,
            hard_fails=result.hard_fails or ["no output"],
        )
    return AdjudicatedPair(
        relation=result.parsed.relation,
        rationale=result.parsed.rationale,
        cost_usd=result.cost_usd,
        hard_fails=[],
    )
