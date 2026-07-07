"""Contract: a versioned prompt + typed output + mechanical guards, dispatched once.

The dispatch pipeline for every contract is fixed:

    render prompt (+ auto-generated JSON-schema section)
      -> llm.complete(task=contract_id, response_model=OutModel, tier)
      -> autofix (mechanical, idempotent)
      -> hard-fail checks (mechanical, semantic-free)
      -> on failure: retry ONCE with the error list appended to the prompt
      -> on second failure: return the result with hard_fails set
         (the caller decides whether that means unit.failed)

The JSON-shape instruction section is never handwritten: `schema_section`
renders it from output_model.model_json_schema(), so the prompt file and the
pydantic model cannot drift apart. Budget pre-flight runs before each call
when a ledger is given; dispatch.* events flow through the optional `emit`
callable (signature: emit(event_name, payload)).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from mastervault.core.budget import BudgetLedger
from mastervault.prompts import registry
from mastervault.providers.llm import LLMProvider, Tier
from mastervault.providers.prices import DEFAULT_RATES, estimate_tokens

#: emit(event_name, payload) — callers adapt RunContext.emit with a fixed stage.
EmitFn = Callable[[str, dict[str, Any]], None]


def schema_section(output_model: type[BaseModel]) -> str:
    """The auto-generated JSON-shape section appended to every rendered prompt."""
    schema = json.dumps(output_model.model_json_schema(), indent=2, ensure_ascii=False)
    return (
        "\n\n## Output format\n\n"
        "Return structured data that validates against this JSON Schema exactly:\n\n"
        f"```json\n{schema}\n```\n"
    )


def _estimate_call_cost(prompt: str, max_tokens: int) -> float:
    """Conservative pre-flight estimate: prompt tokens in, max_tokens out, default rates."""
    rate_in, rate_out = DEFAULT_RATES
    return estimate_tokens(prompt) / 1_000_000 * rate_in + max_tokens / 1_000_000 * rate_out


def _retry_suffix(errors: list[str]) -> str:
    listing = "\n".join(f"- {e}" for e in errors)
    return (
        "\n\nYour previous response failed these checks:\n"
        f"{listing}\n"
        "Respond again and fix every listed problem while keeping everything that was correct."
    )


@dataclass
class DispatchResult[OutModelT: BaseModel]:
    parsed: OutModelT | None
    autofixes: list[str] = field(default_factory=list)
    hard_fails: list[str] = field(default_factory=list)
    attempts: int = 0
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.parsed is not None and not self.hard_fails


class Contract[OutModelT: BaseModel]:
    """Base class. Subclasses set contract_id/tier and override the two guards."""

    contract_id: ClassVar[str]
    tier: ClassVar[Tier] = "medium"

    # -- guards (override in subclasses) --------------------------------------

    def autofix(self, parsed: OutModelT) -> tuple[OutModelT, list[str]]:
        """Mechanical, idempotent cleanup. Returns (fixed, list of fix notes)."""
        return parsed, []

    def hard_fail_checks(self, parsed: OutModelT, ctx: dict[str, Any]) -> list[str]:
        """Mechanical validity checks. Non-empty return = the attempt failed."""
        return []

    # -- dispatch --------------------------------------------------------------

    def dispatch(
        self,
        llm: LLMProvider,
        variables: Mapping[str, Any],
        ctx: dict[str, Any] | None = None,
        *,
        ledger: BudgetLedger | None = None,
        emit: EmitFn | None = None,
        version: int | None = None,
        max_tokens: int = 4096,
    ) -> DispatchResult[OutModelT]:
        ctx = ctx or {}
        send: EmitFn = emit or (lambda event, payload: None)

        spec = registry.load(self.contract_id, version)
        prompt = spec.render(variables) + schema_section(spec.output_model)

        attempts = 0
        cost_usd = 0.0
        autofixes: list[str] = []

        def call(current_prompt: str) -> OutModelT | None:
            nonlocal attempts, cost_usd
            if ledger is not None:
                ledger.check(_estimate_call_cost(current_prompt, max_tokens))
            attempts += 1
            result = llm.complete(
                task=self.contract_id,
                prompt=current_prompt,
                response_model=spec.output_model,
                tier=self.tier,
                max_tokens=max_tokens,
            )
            cost_usd += result.cost_usd
            if ledger is not None:
                ledger.record(result.cost_usd, result.model, result.usage_in, result.usage_out)
            return result.parsed  # type: ignore[return-value]

        def check(parsed: OutModelT | None) -> tuple[OutModelT | None, list[str]]:
            if parsed is None:
                return None, ["model produced no schema-valid structured output"]
            parsed, fixes = self.autofix(parsed)
            if fixes:
                autofixes.extend(fixes)
                send("dispatch.autofixed", {"contract": self.contract_id, "fixes": fixes})
            return parsed, self.hard_fail_checks(parsed, ctx)

        send(
            "dispatch.started",
            {"contract": self.contract_id, "version": spec.version, "tier": self.tier},
        )

        parsed, fails = check(call(prompt))
        if fails:
            send("dispatch.hardfail", {"contract": self.contract_id, "attempt": 1, "errors": fails})
            send("dispatch.retried", {"contract": self.contract_id})
            parsed, fails = check(call(prompt + _retry_suffix(fails)))
            if fails:
                send(
                    "dispatch.hardfail",
                    {"contract": self.contract_id, "attempt": 2, "errors": fails},
                )

        result = DispatchResult(
            parsed=parsed,
            autofixes=autofixes,
            hard_fails=fails,
            attempts=attempts,
            cost_usd=cost_usd,
        )
        send(
            "dispatch.completed",
            {
                "contract": self.contract_id,
                "attempts": attempts,
                "ok": result.ok,
                "hard_fails": fails,
                "cost_usd": cost_usd,
            },
        )
        return result
