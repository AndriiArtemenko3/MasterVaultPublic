"""Sufficiency judge — decides whether one round of retrieved evidence answers
the ask pipeline's question, or whether another round of search is needed.

The judge proposes; the ask pipeline's mechanical guards (max-rounds cap,
novelty floor, followup-dedup) decide. This contract only renders a verdict
shape the pipeline can trust: `followup_queries` capped at 3 by autofix, and
an insufficient verdict always carries at least one `missing_aspects` entry
(unfixable — that's a judgement call, not a shape defect).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.providers.llm import Tier

MAX_FOLLOWUP_QUERIES = 3


class SufficiencyVerdictOut(BaseModel):
    sufficient: bool = Field(
        description="True when the evidence so far fully answers the question."
    )
    missing_aspects: list[str] = Field(
        default_factory=list,
        description="What the evidence doesn't cover yet. Required when sufficient=false.",
    )
    followup_queries: list[str] = Field(
        default_factory=list,
        description="Up to 3 concrete search queries that would close the gaps.",
    )
    rationale: str = Field(description="One or two sentences explaining the verdict.")


class SufficiencyJudgeContract(Contract[SufficiencyVerdictOut]):
    contract_id: ClassVar[str] = "sufficiency_judge"
    tier: ClassVar[Tier] = "small"

    def autofix(
        self, parsed: SufficiencyVerdictOut
    ) -> tuple[SufficiencyVerdictOut, list[str]]:
        fixes: list[str] = []
        queries: list[str] = []
        seen: set[str] = set()
        for q in parsed.followup_queries:
            normalized = " ".join(q.split())
            key = normalized.lower()
            if not normalized:
                fixes.append("dropped empty followup query")
                continue
            if key in seen:
                fixes.append(f"dropped duplicate followup query: {normalized!r}")
                continue
            seen.add(key)
            queries.append(normalized)
        if len(queries) > MAX_FOLLOWUP_QUERIES:
            fixes.append(
                f"truncated followup_queries from {len(queries)} to {MAX_FOLLOWUP_QUERIES}"
            )
            queries = queries[:MAX_FOLLOWUP_QUERIES]
        if queries == list(parsed.followup_queries) and not fixes:
            return parsed, []
        return (
            SufficiencyVerdictOut(
                sufficient=parsed.sufficient,
                missing_aspects=list(parsed.missing_aspects),
                followup_queries=queries,
                rationale=parsed.rationale,
            ),
            fixes,
        )

    def hard_fail_checks(
        self, parsed: SufficiencyVerdictOut, ctx: dict[str, Any]
    ) -> list[str]:
        errors: list[str] = []
        if not parsed.sufficient and not parsed.missing_aspects:
            errors.append("insufficient verdict needs at least one missing_aspects entry")
        if not parsed.rationale.strip():
            errors.append("empty rationale")
        return errors
