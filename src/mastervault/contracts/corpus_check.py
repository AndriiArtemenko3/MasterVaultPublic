"""Corpus check — adjudicates one (claim, candidate-wiki) pairing.

Used by the ingest pipeline when a claim's embedding lands in the
`band_candidate..band_exists` similarity band against an existing wiki entry:
close enough to be about the same concept, not close enough to auto-attach.
The verdict decides ingest routing (tier-2 cross-ref / tier-2 edit-wiki-body /
tier-3 open-contradiction).

`relation` is deliberately a plain string, not a closed pydantic enum: the
membership check is a mechanical hard-fail here so a malformed value retries
through the same guarded path as every other contract instead of failing at
LLM-parse time.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.providers.llm import Tier

VALID_RELATIONS: tuple[str, ...] = ("supports", "extends", "challenges")
MIN_RATIONALE_CHARS = 20


class CorpusCheckOut(BaseModel):
    relation: str = Field(
        description=(
            "How the claim relates to the existing wiki entry. One of: "
            "supports (restates/confirms it), extends (adds a new fact the "
            "entry doesn't cover), challenges (contradicts something the "
            "entry states)."
        ),
    )
    rationale: str = Field(
        description=(
            "One or two sentences quoting the load-bearing part of both the "
            "claim statement and the wiki text that justify the relation."
        ),
    )


class CorpusCheckContract(Contract[CorpusCheckOut]):
    contract_id: ClassVar[str] = "corpus_check"
    tier: ClassVar[Tier] = "small"

    def autofix(self, parsed: CorpusCheckOut) -> tuple[CorpusCheckOut, list[str]]:
        fixes: list[str] = []
        relation = parsed.relation.strip().lower()
        if relation != parsed.relation:
            fixes.append(f"normalized relation {parsed.relation!r} -> {relation!r}")
        rationale = " ".join(parsed.rationale.split())
        if rationale != parsed.rationale:
            fixes.append("collapsed whitespace in rationale")
        if not fixes:
            return parsed, []
        return CorpusCheckOut(relation=relation, rationale=rationale), fixes

    def hard_fail_checks(self, parsed: CorpusCheckOut, ctx: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if parsed.relation not in VALID_RELATIONS:
            errors.append(f"relation {parsed.relation!r} not in {VALID_RELATIONS}")
        if len(parsed.rationale.strip()) < MIN_RATIONALE_CHARS:
            errors.append(f"rationale shorter than {MIN_RATIONALE_CHARS} characters")
        return errors
