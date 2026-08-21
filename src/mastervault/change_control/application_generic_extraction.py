"""Application service for claimed LIVE and strictly offline generic extraction."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mastervault.change_control.application_extraction_calls import (
    ApplicationExtractionCallIndeterminateError,
    ApplicationExtractionCallRepository,
    ApplicationExtractionCallState,
    GenericExtractionCallRequestV1,
    GenericExtractionCallResultV1,
    GenericExtractionUsageV1,
)
from mastervault.change_control.application_replay import ReplayArtifactRefV1
from mastervault.change_control.application_start_command import ApplicationStartCommandV1
from mastervault.change_control.generic_incoming import (
    GenericExtractionModeV2,
    GenericGroundedExtractionV2,
    VerifiedGenericIncomingV2,
    extraction_request_sha256_v2,
    generic_extraction_prompt_variables_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.config import Settings
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2
from mastervault.prompts.registry import load
from mastervault.providers import LLMProvider, get_llm
from mastervault.providers.llm import resolve_model

FailureHook = Callable[[str], None]
EXTRACTION_CALL_CLAIMED = "generic-extraction-call-claimed"
EXTRACTION_RESULT_RECORDED = "generic-extraction-result-recorded"


class ApplicationGenericExtractionError(ValueError):
    """The configured provider or its result violates the extraction contract."""


@dataclass(frozen=True)
class ApplicationGenericExtractionResult:
    extraction: GenericGroundedExtractionV2
    recorded_live: GenericExtractionCallResultV1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _notify(hook: FailureHook | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _request(
    *,
    command: ApplicationStartCommandV1,
    admission: VerifiedGenericIncomingV2,
    settings: Settings,
    claimed_at: str,
) -> tuple[GenericExtractionCallRequestV1, str]:
    spec = load("generic_grounded_claim_extraction_v2", version=2)
    if spec.output_model is not GenericGroundedClaimExtractionV2 or spec.tier != "small":
        raise ApplicationGenericExtractionError("generic extraction prompt identity is invalid")
    rendered = spec.render(generic_extraction_prompt_variables_v2(admission))
    prompt = rendered.encode("utf-8")
    schema = canonical_json_bytes(GenericGroundedClaimExtractionV2.model_json_schema())
    return (
        GenericExtractionCallRequestV1.create(
            command=command,
            extraction_request_sha256=extraction_request_sha256_v2(admission),
            provider=settings.llm.provider,
            model=resolve_model(settings, "small"),
            prompt_sha256=hashlib.sha256(prompt).hexdigest(),
            response_schema_sha256=hashlib.sha256(schema).hexdigest(),
            claimed_at=claimed_at,
        ),
        rendered,
    )


def execute_live_generic_extraction(
    *,
    command: ApplicationStartCommandV1,
    admission: VerifiedGenericIncomingV2,
    settings: Settings,
    repository: ApplicationExtractionCallRepository,
    llm: LLMProvider | None = None,
    failure_hook: FailureHook | None = None,
) -> ApplicationGenericExtractionResult:
    """Claim before the provider and reopen a completed exact retry with zero calls."""

    if command.mode.value != "live":
        raise ApplicationGenericExtractionError("LIVE extraction requires a LIVE start command")
    if command.source_sha256 != admission.source_sha256:
        raise ApplicationGenericExtractionError("start command differs from admitted source")
    request, prompt = _request(
        command=command,
        admission=admission,
        settings=settings,
        claimed_at=_now(),
    )
    lease = repository.begin(request)
    _notify(failure_hook, EXTRACTION_CALL_CLAIMED)
    if lease.state is ApplicationExtractionCallState.INDETERMINATE:
        raise ApplicationExtractionCallIndeterminateError(
            "extraction claim exists without a durable result; automatic retry is unsafe"
        )
    if lease.state is ApplicationExtractionCallState.COMPLETED:
        existing = lease.result
        if existing is None:
            raise ApplicationGenericExtractionError(
                "completed extraction claim has no durable result"
            )
        # Re-ground even exact retries so the current external source descriptor
        # remains part of the authority check.  This is local and call-free.
        try:
            replayed = ground_generic_extraction_v2(
                admission,
                existing.provider_contract,
                mode=GenericExtractionModeV2.REPLAY,
                replay_of=existing.grounded_extraction,
            )
        except (TypeError, ValueError) as exc:
            raise ApplicationGenericExtractionError(
                "recorded extraction failed current-source verification"
            ) from exc
        if replayed.claims != existing.grounded_extraction.claims:
            raise ApplicationGenericExtractionError(
                "current-source verification differs from recorded LIVE extraction"
            )
        return ApplicationGenericExtractionResult(
            extraction=existing.grounded_extraction,
            recorded_live=existing,
        )
    if lease.state is not ApplicationExtractionCallState.FRESH or lease.result is not None:
        raise ApplicationGenericExtractionError("extraction repository returned an invalid lease")
    claimed = lease.claim

    provider = llm if llm is not None else get_llm(settings)
    started = time.perf_counter_ns()
    response = provider.complete(
        "generic_grounded_claim_extraction_v2",
        prompt,
        response_model=None,
        tier="small",
    )
    latency_ms = max(1, (time.perf_counter_ns() - started + 999_999) // 1_000_000)
    if (
        response.parsed is not None
        or response.model != claimed.model
        or not response.request_id
    ):
        raise ApplicationGenericExtractionError(
            "provider result differs from the exact structured extraction contract"
        )
    try:
        extraction = ground_generic_extraction_v2(admission, response.text.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ApplicationGenericExtractionError(
            "provider result failed the structured extraction contract"
        ) from exc
    result = GenericExtractionCallResultV1.create(
        request=claimed,
        provider_request_id=response.request_id,
        usage=GenericExtractionUsageV1(
            input_tokens=response.usage_in,
            output_tokens=response.usage_out,
            cached_input_tokens=0,
            cost_usd_micros=max(0, round(response.cost_usd * 1_000_000)),
            latency_ms=latency_ms,
        ),
        grounded_extraction=extraction,
        completed_at=_now(),
    )
    recorded = repository.complete_live(claimed, result)
    _notify(failure_hook, EXTRACTION_RESULT_RECORDED)
    return ApplicationGenericExtractionResult(extraction=extraction, recorded_live=recorded)


def execute_replay_generic_extraction(
    *,
    command: ApplicationStartCommandV1,
    admission: VerifiedGenericIncomingV2,
    settings: Settings,
    repository: ApplicationExtractionCallRepository,
    reference: ReplayArtifactRefV1,
) -> ApplicationGenericExtractionResult:
    """Offline REPLAY through the exact repository authority named by the bundle."""

    if command.mode.value != "replay":
        raise ApplicationGenericExtractionError("REPLAY extraction requires a REPLAY command")
    expected_request, _prompt = _request(
        command=command,
        admission=admission,
        settings=settings,
        claimed_at=_now(),
    )
    extraction = repository.reopen_replay(
        reference,
        command=command,
        admission=admission,
        expected_request=expected_request,
    )
    recorded = repository.reopen_result_reference(reference)
    return ApplicationGenericExtractionResult(extraction=extraction, recorded_live=recorded)


__all__ = [
    "ApplicationGenericExtractionError",
    "ApplicationGenericExtractionResult",
    "EXTRACTION_CALL_CLAIMED",
    "EXTRACTION_RESULT_RECORDED",
    "execute_live_generic_extraction",
    "execute_replay_generic_extraction",
]
