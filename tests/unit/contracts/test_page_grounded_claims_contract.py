from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.contracts.page_grounded_claims import (
    EvidenceCandidate,
    PageGroundedClaimCandidate,
    PageGroundedClaimExtractionContract,
    PageGroundedClaimExtractionOut,
)
from mastervault.document_intelligence import parse_pdf
from mastervault.providers.llm import MockLLM

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
QUOTE = "Customers may return any item within 45 days of the delivery date."


def _output(block_id: str = "page-0001-block-0001", quote: str = QUOTE):
    return PageGroundedClaimExtractionOut(
        claims=[
            PageGroundedClaimCandidate(
                statement="Customers may return items within 45 days of delivery.",
                confidence="high",
                affects_candidates=["refund-policy"],
                evidence=[EvidenceCandidate(block_id=block_id, quote=quote)],
            )
        ]
    )


def _dispatch(llm: MockLLM):
    document = parse_pdf(FIXTURE)
    return PageGroundedClaimExtractionContract().dispatch(
        llm,
        {
            "title": "Returns and refunds",
            "source_type": "policy",
            "domain": "customer-support",
            "document_blocks": document.prompt_text(),
        },
        {"max_claims": 10, "document": document},
    )


def test_valid_grounded_output_passes() -> None:
    llm = MockLLM()
    llm.push("page_grounded_claim_extraction", _output())
    result = _dispatch(llm)
    assert result.ok
    assert result.attempts == 1


def test_forged_quote_retries_once_then_fails() -> None:
    llm = MockLLM()
    llm.push("page_grounded_claim_extraction", _output(quote="invented evidence"))
    llm.push("page_grounded_claim_extraction", _output(quote="still invented"))
    result = _dispatch(llm)
    assert not result.ok
    assert result.attempts == 2
    assert any("not present" in error for error in result.hard_fails)


def test_retry_can_recover_with_real_evidence() -> None:
    llm = MockLLM()
    llm.push("page_grounded_claim_extraction", _output(block_id="page-9999-block-0001"))
    llm.push("page_grounded_claim_extraction", _output())
    result = _dispatch(llm)
    assert result.ok
    assert result.attempts == 2


@pytest.mark.parametrize("field", ["block_id", "quote"])
def test_evidence_candidate_rejects_whitespace_only_values(field: str) -> None:
    values = {"block_id": "page-0001-block-0001", "quote": QUOTE, field: "   "}
    with pytest.raises(ValidationError, match="non-whitespace"):
        EvidenceCandidate(**values)
