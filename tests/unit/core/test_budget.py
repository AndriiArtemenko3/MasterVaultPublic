"""BudgetLedger: record/spent/remaining, check gate, snapshot shape."""

from __future__ import annotations

import pytest

from mastervault.core.budget import BudgetLedger
from mastervault.core.errors import BudgetExceeded


def test_record_accumulates_spent_and_remaining():
    ledger = BudgetLedger(cap_usd=1.00)
    ledger.record(0.25, "claude-haiku-4-5", tokens_in=1000, tokens_out=200)
    ledger.record(0.10, "claude-sonnet-5", tokens_in=500, tokens_out=100)
    assert ledger.spent == pytest.approx(0.35)
    assert ledger.remaining == pytest.approx(0.65)


def test_check_passes_under_cap():
    ledger = BudgetLedger(cap_usd=1.00)
    ledger.record(0.50, "m", 1, 1)
    ledger.check(0.40)  # 0.90 projected: fine


def test_check_allows_landing_exactly_on_cap():
    ledger = BudgetLedger(cap_usd=1.00)
    ledger.record(0.60, "m", 1, 1)
    ledger.check(0.40)  # projected == cap: allowed; only strict overshoot raises


def test_check_raises_when_estimate_would_exceed():
    ledger = BudgetLedger(cap_usd=1.00)
    ledger.record(0.90, "m", 1, 1)
    with pytest.raises(BudgetExceeded, match=r"\$1\.0000"):
        ledger.check(0.20)


def test_zero_cap_blocks_any_positive_estimate():
    ledger = BudgetLedger(cap_usd=0.0)
    with pytest.raises(BudgetExceeded):
        ledger.check(0.0001)


def test_negative_cap_rejected():
    with pytest.raises(ValueError, match="cap_usd"):
        BudgetLedger(cap_usd=-1.0)


def test_snapshot_aggregates_by_model():
    ledger = BudgetLedger(cap_usd=2.00)
    ledger.record(0.10, "claude-haiku-4-5", tokens_in=100, tokens_out=20)
    ledger.record(0.30, "claude-haiku-4-5", tokens_in=300, tokens_out=60)
    ledger.record(0.50, "claude-sonnet-5", tokens_in=200, tokens_out=40)

    snap = ledger.snapshot()
    assert snap["cap_usd"] == 2.00
    assert snap["spent_usd"] == pytest.approx(0.90)
    assert snap["remaining_usd"] == pytest.approx(1.10)
    assert snap["calls"] == 3
    haiku = snap["by_model"]["claude-haiku-4-5"]
    assert haiku == {
        "calls": 2,
        "cost_usd": pytest.approx(0.40),
        "tokens_in": 400,
        "tokens_out": 80,
    }
