"""Static price table for pre-flight cost estimates.

Prices are USD per 1M tokens as (input, output) pairs. Model pricing drifts
over time — treat every number here as an estimate for budget pre-flight, not
billing reconciliation. A config-override layer (mastervault.toml `[prices]`)
is planned; until then this table is the single source.

Lookup uses longest-prefix matching so dated snapshots ("claude-haiku-4-5-20251001")
and versioned variants resolve to their family row.
"""

from __future__ import annotations

# (usd_per_1m_input, usd_per_1m_output) — snapshot 2026-07.
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4": (5.00, 25.00),
    # OpenAI chat
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o": (2.50, 10.00),
    # OpenAI embeddings (output side is zero — embeddings have no output tokens)
    "text-embedding-3-small": (0.02, 0.0),
}

# Fallback for unknown models: mid-tier chat rates. Deliberately conservative
# so budget pre-flight over-estimates rather than under-estimates.
DEFAULT_RATES: tuple[float, float] = (3.00, 15.00)


def rates_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD-per-1M rates via longest-prefix match."""
    best_key: str | None = None
    for key in PRICES:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return DEFAULT_RATES
    return PRICES[best_key]


def cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimated USD cost for a call with the given token counts."""
    rate_in, rate_out = rates_for(model)
    return tokens_in / 1_000_000 * rate_in + tokens_out / 1_000_000 * rate_out


def estimate_tokens(text: str) -> int:
    """Char/4 approximation — sufficient for budget pre-flight only."""
    return max(1, len(text)) // 4 + 1
