"""Grounded synthesis — writes the ask pipeline's answer from retrieved evidence.

The prompt carries the question, MMR-selected claim cards, and the judge's
`missing_aspects` from the final round. `hard_fail_checks` enforces the one
thing the judge's gaps can't be silently dropped: every missing_aspects entry
must reappear in `gaps`, so the rendered answer never claims completeness the
judge didn't grant it.

Citation validity (do the `[<claim-id>]` tokens in `answer_markdown` resolve
against the evidence pool) is NOT checked here — that gate needs the live
evidence pool, which is pipeline state, not contract ctx, so `pipelines.ask`
applies it mechanically after dispatch.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.models import Confidence
from mastervault.providers.llm import Tier


class GroundedAnswerOut(BaseModel):
    answer_markdown: str = Field(
        description=(
            "The answer, in markdown, citing evidence inline as [<claim-id>] "
            "immediately after the sentence it supports."
        ),
    )
    confidence: Confidence = Field(
        description="Overall confidence the evidence supports this answer."
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="What the evidence doesn't cover; must include every missing aspect.",
    )


class GroundedSynthesisContract(Contract[GroundedAnswerOut]):
    contract_id: ClassVar[str] = "grounded_synthesis"
    tier: ClassVar[Tier] = "large"

    def autofix(self, parsed: GroundedAnswerOut) -> tuple[GroundedAnswerOut, list[str]]:
        text = parsed.answer_markdown.strip()
        if text == parsed.answer_markdown:
            return parsed, []
        return (
            GroundedAnswerOut(
                answer_markdown=text, confidence=parsed.confidence, gaps=list(parsed.gaps)
            ),
            ["stripped whitespace from answer_markdown"],
        )

    def hard_fail_checks(self, parsed: GroundedAnswerOut, ctx: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not parsed.answer_markdown:
            errors.append("empty answer_markdown")
        missing_aspects = ctx.get("missing_aspects") or []
        gaps_lower = {g.strip().lower() for g in parsed.gaps}
        for aspect in missing_aspects:
            if aspect.strip().lower() not in gaps_lower:
                errors.append(f"missing_aspect {aspect!r} from the judge is not reflected in gaps")
        return errors
