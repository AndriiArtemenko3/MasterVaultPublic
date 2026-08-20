"""V2 provider contract for exact quotations from admitted Markdown."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mastervault.contracts.base import Contract
from mastervault.models import Confidence
from mastervault.providers.llm import Tier


class GenericGroundedClaimCandidateV2(BaseModel):
    """Provider suggestion only; local mechanics establish all authority."""

    model_config = ConfigDict(extra="forbid", strict=True)

    quote: str = Field(
        min_length=1,
        description="One exact, complete, atomic sentence copied verbatim from the input.",
    )
    confidence: Confidence
    affects: tuple[str, ...] = Field(default_factory=tuple, max_length=16)


class GenericGroundedClaimExtractionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claims: tuple[GenericGroundedClaimCandidateV2, ...] = Field(min_length=1, max_length=10)


class GenericGroundedClaimExtractionContractV2(Contract[GenericGroundedClaimExtractionV2]):
    """Shape gate only; :mod:`generic_incoming` performs exact grounding."""

    contract_id: ClassVar[str] = "generic_grounded_claim_extraction_v2"
    tier: ClassVar[Tier] = "small"

    def autofix(
        self, parsed: GenericGroundedClaimExtractionV2
    ) -> tuple[GenericGroundedClaimExtractionV2, list[str]]:
        return parsed, []

    def hard_fail_checks(
        self, parsed: GenericGroundedClaimExtractionV2, ctx: dict[str, Any]
    ) -> list[str]:
        del ctx
        count = len(parsed.claims)
        if not 1 <= count <= 10:
            return ["grounded extraction requires 1-10 claims"]
        return []


__all__ = [
    "GenericGroundedClaimCandidateV2",
    "GenericGroundedClaimExtractionContractV2",
    "GenericGroundedClaimExtractionV2",
]
