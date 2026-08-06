"""Claim extraction contract for addressable PDF page blocks."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mastervault.contracts.base import Contract
from mastervault.contracts.claims import MAX_STATEMENT_WORDS, MIN_STATEMENT_CHARS
from mastervault.document_intelligence.grounding import evidence_errors
from mastervault.document_intelligence.models import ParsedDocument
from mastervault.models import Confidence
from mastervault.providers.llm import Tier

_KEBAB_RE = re.compile(r"[^a-z0-9]+")


class EvidenceCandidate(BaseModel):
    """Model-proposed quote; page and offsets are deliberately not model fields."""

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(
        min_length=1,
        description="Exact BLOCK identifier containing the evidence.",
    )
    quote: str = Field(
        min_length=1,
        description="Short verbatim quote copied from that block.",
    )

    @field_validator("block_id", "quote")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain a non-whitespace character")
        return value


class PageGroundedClaimCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(
        description=(
            "One atomic factual claim from the document, present tense, 25 words or fewer, "
            "with concrete numbers/dates/names kept verbatim."
        )
    )
    confidence: Confidence
    affects_candidates: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCandidate] = Field(
        default_factory=list,
        description="One or more BLOCK identifiers with verbatim supporting quotes.",
    )


class PageGroundedClaimExtractionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[PageGroundedClaimCandidate]


def _kebab(text: str) -> str:
    return _KEBAB_RE.sub("-", text.lower()).strip("-")


class PageGroundedClaimExtractionContract(Contract[PageGroundedClaimExtractionOut]):
    contract_id: ClassVar[str] = "page_grounded_claim_extraction"
    tier: ClassVar[Tier] = "small"

    def autofix(
        self, parsed: PageGroundedClaimExtractionOut
    ) -> tuple[PageGroundedClaimExtractionOut, list[str]]:
        fixes: list[str] = []
        seen_statements: set[str] = set()
        claims: list[PageGroundedClaimCandidate] = []
        for claim in parsed.claims:
            statement = " ".join(claim.statement.split())
            if statement != claim.statement:
                fixes.append(f"normalized whitespace in statement: {statement[:60]!r}")
            if statement in seen_statements:
                fixes.append(f"dropped duplicate statement: {statement[:60]!r}")
                continue
            seen_statements.add(statement)

            affects: list[str] = []
            for raw in claim.affects_candidates:
                slug = _kebab(raw)
                if slug != raw:
                    fixes.append(f"kebab-cased affects candidate {raw!r} -> {slug!r}")
                if slug and slug not in affects:
                    affects.append(slug)

            evidence: list[EvidenceCandidate] = []
            for item in claim.evidence:
                block_id = item.block_id.strip()
                quote = item.quote.strip()
                if block_id != item.block_id or quote != item.quote:
                    fixes.append(f"trimmed evidence for block {block_id!r}")
                evidence.append(EvidenceCandidate(block_id=block_id, quote=quote))
            claims.append(
                PageGroundedClaimCandidate(
                    statement=statement,
                    confidence=claim.confidence,
                    affects_candidates=affects,
                    evidence=evidence,
                )
            )
        return PageGroundedClaimExtractionOut(claims=claims), fixes

    def hard_fail_checks(
        self, parsed: PageGroundedClaimExtractionOut, ctx: dict[str, Any]
    ) -> list[str]:
        errors: list[str] = []
        if not parsed.claims:
            errors.append("zero claims extracted")
        max_claims = ctx.get("max_claims")
        if max_claims is not None and len(parsed.claims) > max_claims:
            errors.append(f"{len(parsed.claims)} claims exceeds max_claims={max_claims}")
        document = ctx.get("document")
        if not isinstance(document, ParsedDocument):
            errors.append("grounding context is missing the parsed document")
            return errors
        for idx, claim in enumerate(parsed.claims, start=1):
            if len(claim.statement) < MIN_STATEMENT_CHARS:
                errors.append(
                    f"claim {idx}: statement shorter than {MIN_STATEMENT_CHARS} characters"
                )
            if len(claim.statement.split()) > MAX_STATEMENT_WORDS:
                errors.append(f"claim {idx}: statement longer than {MAX_STATEMENT_WORDS} words")
            errors.extend(
                f"claim {idx}: {error}" for error in evidence_errors(document, claim.evidence)
            )
        return errors
