"""BudgetLedger: thread-safe running spend against a hard USD cap.

`check(next_estimate)` is the pre-flight gate — call it BEFORE an LLM call
with the estimated cost of that call; it raises BudgetExceeded when the
estimate would push spend past the cap. `record` books the actual cost after
the call. `snapshot()` is the cost.json payload for the run dir.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from mastervault.core.errors import BudgetExceeded


@dataclass(frozen=True)
class LedgerEntry:
    cost_usd: float
    model: str
    tokens_in: int
    tokens_out: int


class BudgetLedger:
    def __init__(self, cap_usd: float) -> None:
        if cap_usd < 0:
            raise ValueError(f"cap_usd must be >= 0, got {cap_usd}")
        self.cap_usd = cap_usd
        self._entries: list[LedgerEntry] = []
        self._spent = 0.0
        self._lock = threading.Lock()

    def record(self, cost_usd: float, model: str, tokens_in: int, tokens_out: int) -> None:
        """Book the actual cost of one completed call."""
        with self._lock:
            self._entries.append(LedgerEntry(cost_usd, model, tokens_in, tokens_out))
            self._spent += cost_usd

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def remaining(self) -> float:
        with self._lock:
            return self.cap_usd - self._spent

    def check(self, next_estimate: float) -> None:
        """Raise BudgetExceeded when spent + next_estimate would pass the cap."""
        with self._lock:
            projected = self._spent + next_estimate
            if projected > self.cap_usd:
                raise BudgetExceeded(
                    f"budget cap ${self.cap_usd:.4f} would be exceeded: "
                    f"spent ${self._spent:.4f} + estimated ${next_estimate:.4f} "
                    f"= ${projected:.4f}"
                )

    def snapshot(self) -> dict[str, Any]:
        """Serializable state for cost.json."""
        with self._lock:
            by_model: dict[str, dict[str, float | int]] = {}
            for e in self._entries:
                agg = by_model.setdefault(
                    e.model, {"calls": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
                )
                agg["calls"] += 1
                agg["cost_usd"] += e.cost_usd
                agg["tokens_in"] += e.tokens_in
                agg["tokens_out"] += e.tokens_out
            return {
                "cap_usd": self.cap_usd,
                "spent_usd": self._spent,
                "remaining_usd": self.cap_usd - self._spent,
                "calls": len(self._entries),
                "by_model": by_model,
            }
