"""ClaimExtractionContract: dispatch flow, autofix guards, hard-fail retry, budget gate."""

from __future__ import annotations

import pytest

from mastervault.contracts.claims import (
    ClaimCandidate,
    ClaimExtractionContract,
    ClaimExtractionOut,
)
from mastervault.core.budget import BudgetLedger
from mastervault.core.errors import BudgetExceeded
from mastervault.providers.llm import MockLLM

VARIABLES = {
    "title": "Refund FAQ",
    "source_type": "faq",
    "domain": "operations",
    "body": "Refunds are issued within 30 days of purchase for annual plans.",
}


def good_output() -> ClaimExtractionOut:
    return ClaimExtractionOut(
        claims=[
            ClaimCandidate(
                statement="Refunds are issued within 30 days of purchase.",
                confidence="high",
                affects_candidates=["refund-policy"],
            )
        ]
    )


@pytest.fixture
def contract():
    return ClaimExtractionContract()


@pytest.fixture
def llm():
    return MockLLM()


# -- dispatch ------------------------------------------------------------------


def test_dispatch_happy_path(contract, llm):
    llm.push("claim_extraction", good_output())
    result = contract.dispatch(llm, VARIABLES, {"max_claims": 10})
    assert result.ok
    assert result.attempts == 1
    assert result.hard_fails == []
    assert result.parsed.claims[0].statement == "Refunds are issued within 30 days of purchase."


def test_dispatch_prompt_carries_variables_and_schema_section(contract, llm):
    llm.push("claim_extraction", good_output())
    contract.dispatch(llm, VARIABLES)
    task, prompt = llm.calls[0]
    assert task == "claim_extraction"
    assert "Refund FAQ" in prompt
    assert VARIABLES["body"] in prompt
    # The JSON-shape section is auto-appended from the output model's schema.
    assert "## Output format" in prompt
    assert '"claims"' in prompt


def test_dispatch_hardfail_retries_once_then_succeeds(contract, llm):
    llm.push("claim_extraction", ClaimExtractionOut(claims=[]))  # bad: zero claims
    llm.push("claim_extraction", good_output())  # good on retry
    events: list[tuple[str, dict]] = []

    result = contract.dispatch(llm, VARIABLES, emit=lambda e, p: events.append((e, p)))

    assert result.ok
    assert result.attempts == 2
    names = [e for e, _ in events]
    assert names == [
        "dispatch.started",
        "dispatch.hardfail",
        "dispatch.retried",
        "dispatch.completed",
    ]
    # The retry prompt carries the error list appended to the original prompt.
    retry_prompt = llm.calls[1][1]
    assert "zero claims extracted" in retry_prompt
    assert retry_prompt.startswith(llm.calls[0][1])


def test_dispatch_double_hardfail_surfaces_in_result(contract, llm):
    llm.push("claim_extraction", ClaimExtractionOut(claims=[]))
    llm.push("claim_extraction", ClaimExtractionOut(claims=[]))
    events: list[str] = []

    result = contract.dispatch(llm, VARIABLES, emit=lambda e, p: events.append(e))

    assert not result.ok
    assert result.attempts == 2
    assert result.hard_fails == ["zero claims extracted"]
    assert events.count("dispatch.hardfail") == 2
    assert events.count("dispatch.retried") == 1


def test_dispatch_budget_gate_blocks_before_any_call(contract, llm):
    ledger = BudgetLedger(cap_usd=0.0)
    with pytest.raises(BudgetExceeded):
        contract.dispatch(llm, VARIABLES, ledger=ledger)
    assert llm.calls == []  # pre-flight check fired before the LLM was touched


def test_dispatch_records_usage_to_ledger(contract, llm):
    llm.push("claim_extraction", good_output())
    ledger = BudgetLedger(cap_usd=10.0)
    contract.dispatch(llm, VARIABLES, ledger=ledger)
    snap = ledger.snapshot()
    assert snap["calls"] == 1
    assert snap["by_model"]["mock-small"]["tokens_in"] > 0


# -- autofix ---------------------------------------------------------------------


def messy_output() -> ClaimExtractionOut:
    return ClaimExtractionOut(
        claims=[
            ClaimCandidate(
                statement="  Refunds   take\t30 days. ",
                confidence="high",
                affects_candidates=["Refund Policy", "refund-policy"],
            ),
            ClaimCandidate(
                statement="Refunds take 30 days.",  # duplicate after normalization
                confidence="medium",
            ),
            ClaimCandidate(
                statement="Support answers within one business day.",
                confidence="low",
                affects_candidates=["Support SLA!!"],
            ),
        ]
    )


def test_autofix_normalizes_dedupes_and_kebab_cases(contract):
    fixed, fixes = contract.autofix(messy_output())
    assert [c.statement for c in fixed.claims] == [
        "Refunds take 30 days.",
        "Support answers within one business day.",
    ]
    assert fixed.claims[0].affects_candidates == ["refund-policy"]
    assert fixed.claims[1].affects_candidates == ["support-sla"]
    assert fixes  # every transform reported


def test_autofix_is_idempotent(contract):
    once, _ = contract.autofix(messy_output())
    twice, second_fixes = contract.autofix(once)
    assert twice == once
    assert second_fixes == []


# -- hard-fail checks ---------------------------------------------------------------


def test_hard_fail_zero_claims(contract):
    assert contract.hard_fail_checks(ClaimExtractionOut(claims=[]), {}) == [
        "zero claims extracted"
    ]


def test_hard_fail_max_claims_from_ctx(contract):
    out = ClaimExtractionOut(
        claims=[
            ClaimCandidate(statement=f"Fact number {i} is stated here.", confidence="high")
            for i in range(3)
        ]
    )
    errors = contract.hard_fail_checks(out, {"max_claims": 2})
    assert errors == ["3 claims exceeds max_claims=2"]
    assert contract.hard_fail_checks(out, {"max_claims": 3}) == []


def test_hard_fail_statement_too_short(contract):
    out = ClaimExtractionOut(
        claims=[ClaimCandidate(statement="Tiny.", confidence="low")]
    )
    errors = contract.hard_fail_checks(out, {})
    assert errors == ["claim 1: statement shorter than 8 characters"]


def test_hard_fail_statement_too_long(contract):
    out = ClaimExtractionOut(
        claims=[ClaimCandidate(statement="word " * 41, confidence="low")]
    )
    errors = contract.hard_fail_checks(out, {})
    assert errors == ["claim 1: statement longer than 40 words"]
