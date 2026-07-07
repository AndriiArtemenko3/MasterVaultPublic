"""Claim extraction — the reference contract.

Output shape mirrors the vault's claims layer (models.Claim) minus the id,
which the ingest pipeline assigns after wiki adjudication. Field descriptions
matter: they feed the auto-generated JSON-schema section of the prompt.

Groundedness of a statement is NOT checkable mechanically, so it is
deliberately absent from hard_fail_checks — that judgement belongs to a judge
contract, not to this layer.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.models import Confidence
from mastervault.providers.llm import Tier

_KEBAB_RE = re.compile(r"[^a-z0-9]+")

MAX_STATEMENT_WORDS = 40
MIN_STATEMENT_CHARS = 8


class ClaimCandidate(BaseModel):
    statement: str = Field(
        description=(
            "One atomic factual claim from the document, present tense, 25 words or fewer, "
            "with concrete numbers/dates/names kept verbatim."
        ),
    )
    confidence: Confidence = Field(
        description=(
            "How directly the document supports the statement: high = stated outright, "
            "medium = strongly implied, low = weakly implied or hedged."
        ),
    )
    affects_candidates: list[str] = Field(
        default_factory=list,
        description=(
            "0-3 short kebab-case concept names this claim bears on "
            "(e.g. 'refund-policy'). Empty when no clear concept applies."
        ),
    )


class ClaimExtractionOut(BaseModel):
    claims: list[ClaimCandidate] = Field(
        description="Every atomic claim the document asserts, one fact per entry.",
    )


def _kebab(text: str) -> str:
    return _KEBAB_RE.sub("-", text.lower()).strip("-")


class ClaimExtractionContract(Contract[ClaimExtractionOut]):
    contract_id: ClassVar[str] = "claim_extraction"
    tier: ClassVar[Tier] = "small"

    def autofix(self, parsed: ClaimExtractionOut) -> tuple[ClaimExtractionOut, list[str]]:
        """Whitespace normalize, dedupe identical statements, kebab-case candidates.

        Idempotent: every transform is a fixpoint, so a second pass changes
        nothing and reports zero fixes.
        """
        fixes: list[str] = []
        seen: set[str] = set()
        out: list[ClaimCandidate] = []
        for claim in parsed.claims:
            statement = " ".join(claim.statement.split())
            if statement != claim.statement:
                fixes.append(f"normalized whitespace in statement: {statement[:60]!r}")

            candidates: list[str] = []
            for raw in claim.affects_candidates:
                slug = _kebab(raw)
                if slug != raw:
                    fixes.append(f"kebab-cased affects candidate {raw!r} -> {slug!r}")
                if slug and slug not in candidates:
                    candidates.append(slug)
                elif not slug:
                    fixes.append(f"dropped empty affects candidate {raw!r}")

            if statement in seen:
                fixes.append(f"dropped duplicate statement: {statement[:60]!r}")
                continue
            seen.add(statement)
            out.append(
                ClaimCandidate(
                    statement=statement,
                    confidence=claim.confidence,
                    affects_candidates=candidates,
                )
            )
        return ClaimExtractionOut(claims=out), fixes

    def hard_fail_checks(self, parsed: ClaimExtractionOut, ctx: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        n = len(parsed.claims)
        if n == 0:
            errors.append("zero claims extracted")
        max_claims = ctx.get("max_claims")
        if max_claims is not None and n > max_claims:
            errors.append(f"{n} claims exceeds max_claims={max_claims}")
        for i, claim in enumerate(parsed.claims, start=1):
            if len(claim.statement) < MIN_STATEMENT_CHARS:
                errors.append(
                    f"claim {i}: statement shorter than {MIN_STATEMENT_CHARS} characters"
                )
            if len(claim.statement.split()) > MAX_STATEMENT_WORDS:
                errors.append(f"claim {i}: statement longer than {MAX_STATEMENT_WORDS} words")
        return errors
