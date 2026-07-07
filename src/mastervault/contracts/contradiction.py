"""Contradiction judge — compares two claim statements sharing an `affects` slug.

The lint pipeline never queues a contradiction off a single verdict: a
'contradicts' result is confirmed by a SECOND dispatch with the statements in
swapped order, and only a double-confirmed pair reaches the review queue (see
`pipelines.lint`). This contract only renders one verdict; the double-confirm
policy is pipeline-level, not contract-level.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.providers.llm import Tier

VALID_VERDICTS: tuple[str, ...] = ("contradicts", "compatible", "unclear")
MIN_RATIONALE_CHARS = 20


class ContradictionVerdictOut(BaseModel):
    verdict: str = Field(
        description="One of: contradicts | compatible | unclear.",
    )
    rationale: str = Field(
        description="One or two sentences quoting the load-bearing part of both statements.",
    )


class ContradictionJudgeContract(Contract[ContradictionVerdictOut]):
    contract_id: ClassVar[str] = "contradiction_judge"
    tier: ClassVar[Tier] = "small"

    def autofix(
        self, parsed: ContradictionVerdictOut
    ) -> tuple[ContradictionVerdictOut, list[str]]:
        fixes: list[str] = []
        verdict = parsed.verdict.strip().lower()
        if verdict != parsed.verdict:
            fixes.append(f"normalized verdict {parsed.verdict!r} -> {verdict!r}")
        rationale = " ".join(parsed.rationale.split())
        if rationale != parsed.rationale:
            fixes.append("collapsed whitespace in rationale")
        if not fixes:
            return parsed, []
        return ContradictionVerdictOut(verdict=verdict, rationale=rationale), fixes

    def hard_fail_checks(
        self, parsed: ContradictionVerdictOut, ctx: dict[str, Any]
    ) -> list[str]:
        errors: list[str] = []
        if parsed.verdict not in VALID_VERDICTS:
            errors.append(f"verdict {parsed.verdict!r} not in {VALID_VERDICTS}")
        if len(parsed.rationale.strip()) < MIN_RATIONALE_CHARS:
            errors.append(f"rationale shorter than {MIN_RATIONALE_CHARS} characters")
        return errors
