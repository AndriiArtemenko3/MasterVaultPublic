"""Price table: prefix matching, default fallback, cost math."""

from __future__ import annotations

import pytest

from mastervault.providers.prices import DEFAULT_RATES, PRICES, cost, rates_for


def test_exact_match() -> None:
    assert rates_for("claude-sonnet-5") == PRICES["claude-sonnet-5"]


def test_prefix_match_dated_snapshot() -> None:
    # The config default model_small is a dated snapshot of haiku 4.5.
    assert rates_for("claude-haiku-4-5-20251001") == PRICES["claude-haiku-4-5"]


def test_prefix_match_opus_family() -> None:
    assert rates_for("claude-opus-4-8") == PRICES["claude-opus-4"]
    assert rates_for("claude-opus-4-6") == PRICES["claude-opus-4"]


def test_longest_prefix_wins() -> None:
    # "gpt-4o-mini-2024-07-18" starts with both "gpt-4o" and "gpt-4o-mini";
    # the longer key must win.
    assert rates_for("gpt-4o-mini-2024-07-18") == PRICES["gpt-4o-mini"]
    assert rates_for("gpt-4o-2024-08-06") == PRICES["gpt-4o"]


def test_unknown_model_uses_default_rates() -> None:
    assert rates_for("some-future-model") == DEFAULT_RATES


def test_cost_math() -> None:
    rate_in, rate_out = PRICES["claude-haiku-4-5"]
    assert cost("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(rate_in + rate_out)
    assert cost("claude-haiku-4-5", 0, 0) == 0.0


def test_embedding_model_has_zero_output_rate() -> None:
    rate_in, rate_out = rates_for("text-embedding-3-small")
    assert rate_in > 0
    assert rate_out == 0.0
    assert cost("text-embedding-3-small", 0, 1_000_000) == 0.0
