"""Truthful adapter from the configured LLM to recorded-inference evidence."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from mastervault.change_control.application_provider_calls import (
    ApplicationProviderCallClaimV1,
    ApplicationProviderCallIndeterminateError,
    ApplicationProviderCallJournal,
    ApplicationProviderCallState,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.recorded_inference import (
    InferenceProviderRequest,
    InferenceUsage,
    ProviderCallResult,
)
from mastervault.config import Settings
from mastervault.providers import LLMProvider, get_llm


class ApplicationProviderBridgeError(ValueError):
    """Configured provider identity cannot satisfy the immutable run contract."""


class SettingsRecordedInferenceProvider:
    """Execute canonical recorded requests through exactly one configured LLM call."""

    def __init__(
        self,
        settings: Settings,
        contract: ManagedInferenceContractBinding,
        *,
        journal: ApplicationProviderCallJournal,
        owner_id: str,
        run_id: str,
        llm: LLMProvider | None = None,
    ) -> None:
        provider = settings.llm.provider
        if (
            contract.mode is not InferenceExecutionMode.LIVE
            or provider != contract.provider
            or contract.model != settings.llm.model_medium
        ):
            raise ApplicationProviderBridgeError(
                "configured LIVE provider/medium model does not match the recorded contract"
            )
        self._provider = provider
        self._model = contract.model
        self._journal = journal
        self._owner_id = owner_id
        self._run_id = run_id
        self._llm = llm if llm is not None else get_llm(settings)

    def complete(self, *, request: bytes) -> ProviderCallResult:
        exact = InferenceProviderRequest.model_validate_json(request, strict=True)
        if exact.canonical_bytes() != request:
            raise ApplicationProviderBridgeError("recorded provider request is not canonical")
        claim = ApplicationProviderCallClaimV1.create(
            owner_id=self._owner_id,
            run_id=self._run_id,
            provider=self._provider,
            model=self._model,
            request=exact,
            request_bytes=request,
            claimed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        lease = self._journal.begin(claim)
        if lease.state is ApplicationProviderCallState.COMPLETED:
            if lease.result is None:
                raise ApplicationProviderBridgeError(
                    "completed provider-call claim has no durable result"
                )
            return lease.result
        if lease.state is ApplicationProviderCallState.INDETERMINATE:
            raise ApplicationProviderCallIndeterminateError(
                "provider-call claim exists without a durable result; automatic retry is unsafe"
            )
        if lease.state is not ApplicationProviderCallState.FRESH or lease.result is not None:
            raise ApplicationProviderBridgeError("provider-call journal returned an invalid lease")
        started = time.perf_counter_ns()
        result = self._llm.complete(
            exact.task.value,
            request.decode("utf-8"),
            response_model=None,
            tier="medium",
        )
        latency_ms = max(1, (time.perf_counter_ns() - started + 999_999) // 1_000_000)
        if result.parsed is not None or result.model != self._model:
            raise ApplicationProviderBridgeError(
                "LLM result differs from the exact raw-output contract"
            )
        if not result.request_id:
            raise ApplicationProviderBridgeError("LLM result omits its provider request ID")
        recorded = ProviderCallResult(
            provider=self._provider,
            model=self._model,
            provider_request_id=result.request_id,
            raw_output_utf8=result.text,
            usage=InferenceUsage(
                input_tokens=result.usage_in,
                output_tokens=result.usage_out,
                cached_input_tokens=0,
                cost_usd_micros=max(0, round(result.cost_usd * 1_000_000)),
                latency_ms=latency_ms,
            ),
        )
        return self._journal.complete(
            claim=lease.claim,
            result=recorded,
            completed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


__all__ = ["ApplicationProviderBridgeError", "SettingsRecordedInferenceProvider"]
