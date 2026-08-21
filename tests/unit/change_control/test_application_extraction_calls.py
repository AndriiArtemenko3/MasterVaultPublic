from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mastervault.change_control.application_extraction_calls import (
    ApplicationExtractionCallConflictError,
    ApplicationExtractionCallIndeterminateError,
    ApplicationExtractionCallRepository,
    GenericExtractionCallRequestV1,
    GenericExtractionCallResultV1,
    GenericExtractionUsageV1,
)
from mastervault.change_control.application_generic_extraction import (
    EXTRACTION_RESULT_RECORDED,
    execute_live_generic_extraction,
    execute_replay_generic_extraction,
)
from mastervault.change_control.application_start_command import ApplicationStartCommandV1
from mastervault.change_control.change_application_contracts import ChangeExecutionModeV1
from mastervault.change_control.generic_incoming import (
    admit_generic_incoming_markdown_v2,
    extraction_request_sha256_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.config import Settings
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2
from mastervault.models import Domain
from mastervault.providers.llm import LLMResult, MockLLM

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC).isoformat(timespec="seconds")


def _admission(tmp_path: Path, sentence: str = "Returns require a receipt."):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    source = tmp_path / "returns-policy-v2.md"
    source.write_text(
        "---\nmastervault_change:\n"
        "  schema_version: 1\n"
        "  event_id: returns-event-v2\n"
        "  document_id: returns-policy-v2\n"
        "  document_family: returns-policy\n"
        "  version_label: v2\n"
        "  title: Returns Policy\n"
        "  domain: customer-support\n"
        "  source_type: policy\n"
        "  declared_effective_from: 2026-08-20\n"
        "  role: policy\n"
        "  authority: primary\n"
        "  operator_intent: Adopt the governing policy.\n"
        f"---\n{sentence}\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    return admit_generic_incoming_markdown_v2(source, active_workspace=workspace)


def _command(
    admission,
    *,
    operation_id: str = "start:extraction",
    mode: ChangeExecutionModeV1 = ChangeExecutionModeV1.LIVE,
):
    return ApplicationStartCommandV1.create(
        operation_id=operation_id,
        run_id=f"operatorrun:{'1' * 64}",
        base_authority_id=f"mauthority:{'2' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="3" * 64,
        source_sha256=admission.source_sha256,
        source_byte_count=admission.source_byte_count,
        source_metadata_sha256="5" * 64,
        suite_id="operator-suite",
        suite_version=1,
        suite_original_sha256="6" * 64,
        suite_original_byte_count=456,
        suite_canonical_sha256="7" * 64,
        domain=Domain.CUSTOMER_SUPPORT,
        mode=mode,
        replay_bundle_id=(f"change-replay:{'b' * 64}" if mode is ChangeExecutionModeV1.REPLAY else None),
        replay_bundle_sha256=("b" * 64 if mode is ChangeExecutionModeV1.REPLAY else None),
        configuration_sha256="8" * 64,
        claimed_at=_NOW,
    )


def _request(command, admission, *, model: str = "mock-small"):
    return GenericExtractionCallRequestV1.create(
        command=command,
        extraction_request_sha256=extraction_request_sha256_v2(admission),
        provider="mock",
        model=model,
        prompt_sha256="9" * 64,
        response_schema_sha256="a" * 64,
        claimed_at=_NOW,
    )


def _result(request, admission):
    provider = GenericGroundedClaimExtractionV2.model_validate_json(
        json.dumps(
            {
                "claims": [
                    {
                        "quote": "Returns require a receipt.",
                        "confidence": "high",
                        "affects": ["refund-policy"],
                    }
                ]
            }
        )
    )
    grounded = ground_generic_extraction_v2(admission, provider)
    return GenericExtractionCallResultV1.create(
        request=request,
        provider_request_id="provider-request-real-123",
        usage=GenericExtractionUsageV1(
            input_tokens=17,
            output_tokens=8,
            cached_input_tokens=3,
            cost_usd_micros=41,
            latency_ms=7,
        ),
        grounded_extraction=grounded,
        completed_at=_NOW,
    )


def test_claim_precedes_result_and_lost_ack_retry_reopens_exact_result(tmp_path: Path) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    request = _request(command, admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")

    claimed, existing = repository.claim(request)
    assert claimed == request
    assert existing is None
    recorded = repository.complete_live(request, _result(request, admission))

    # Simulate a fresh process after persistence but before acknowledgement.
    reopened_repository = ApplicationExtractionCallRepository(
        tmp_path / "evidence", create=False
    )
    retried_claim, retried_result = reopened_repository.claim(request)
    assert retried_claim == claimed
    assert retried_result == recorded
    assert retried_result.provider_request_id == "provider-request-real-123"
    assert retried_result.usage.cost_usd_micros == 41
    assert retried_result.completed_at == _NOW


def test_same_request_key_with_different_provider_input_conflicts(tmp_path: Path) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    repository.claim(_request(command, admission))

    with pytest.raises(ApplicationExtractionCallConflictError, match="different immutable"):
        repository.claim(_request(command, admission, model="other-small"))


def test_incomplete_extraction_claim_retry_is_explicitly_indeterminate(
    tmp_path: Path,
) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    request = _request(command, admission)
    repository.claim(request)

    with pytest.raises(ApplicationExtractionCallIndeterminateError, match="unsafe"):
        repository.claim(request)


def test_concurrent_same_extraction_request_makes_exactly_one_call(
    tmp_path: Path,
) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    root = tmp_path / "evidence"
    # Establish the shared repository lock before the concurrent provider phase.
    ApplicationExtractionCallRepository(root)
    settings = Settings(
        paths={"workspace": tmp_path / "configured-workspace"},
        llm={
            "provider": "mock",
            "model_small": "mock-small",
            "model_medium": "mock-medium",
            "model_large": "mock-large",
        },
    )
    entered = threading.Event()
    release = threading.Event()

    class BlockingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, task: str, prompt: str, **kwargs):
            del prompt, kwargs
            assert task == "generic_grounded_claim_extraction_v2"
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return LLMResult(
                text=json.dumps(
                    {
                        "claims": [
                            {
                                "quote": "Returns require a receipt.",
                                "confidence": "high",
                                "affects": ["refund-policy"],
                            }
                        ]
                    }
                ),
                parsed=None,
                request_id="provider-request-concurrent",
                model="mock-small",
                usage_in=17,
                usage_out=8,
                cost_usd=0.0,
            )

    llm = BlockingLLM()
    completed: list[object] = []
    failed: list[BaseException] = []

    def first() -> None:
        try:
            completed.append(
                execute_live_generic_extraction(
                    command=command,
                    admission=admission,
                    settings=settings,
                    repository=ApplicationExtractionCallRepository(root),
                    llm=llm,
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            failed.append(exc)

    worker = threading.Thread(target=first)
    worker.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(ApplicationExtractionCallIndeterminateError, match="unsafe"):
            execute_live_generic_extraction(
                command=command,
                admission=admission,
                settings=settings,
                repository=ApplicationExtractionCallRepository(root),
                llm=llm,
            )
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert failed == []
    assert len(completed) == 1
    assert llm.calls == 1


def test_replay_reopens_named_live_authority_and_regrounds_current_source(
    tmp_path: Path,
) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    request = _request(command, admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    repository.claim(request)
    live = repository.complete_live(request, _result(request, admission))

    replay = ApplicationExtractionCallRepository(
        tmp_path / "evidence", create=False, read_only=True
    ).reopen_replay(
        live.replay_ref,
        command=command,
        admission=admission,
        expected_request=request,
    )
    assert replay.mode.value == "replay"
    assert replay.claims == live.grounded_extraction.claims
    assert replay.provider_contract == live.provider_contract


def test_tampered_result_fails_closed_on_exact_retry(tmp_path: Path) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    request = _request(command, admission)
    root = tmp_path / "evidence"
    repository = ApplicationExtractionCallRepository(root)
    repository.claim(request)
    result = repository.complete_live(request, _result(request, admission))
    result_path = root / result.replay_ref.relative_locator
    payload = result.model_dump(mode="json")
    payload["provider_request_id"] = "substituted"
    result_path.chmod(0o600)
    result_path.write_bytes(canonical_json_bytes(payload))
    result_path.chmod(0o600)

    with pytest.raises(ValueError, match="invalid"):
        repository.claim(request)


def test_live_executor_claims_before_call_and_lost_ack_retry_makes_zero_calls(
    tmp_path: Path,
) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    settings = Settings(
        paths={"workspace": tmp_path / "configured-workspace"},
        llm={
            "provider": "mock",
            "model_small": "mock-small",
            "model_medium": "mock-medium",
            "model_large": "mock-large",
        },
    )
    llm = MockLLM(settings)
    llm.push(
        "generic_grounded_claim_extraction_v2",
        json.dumps(
            {
                "claims": [
                    {
                        "quote": "Returns require a receipt.",
                        "confidence": "high",
                        "affects": ["refund-policy"],
                    },
                ]
            }
        ),
    )

    def lose_ack(stage: str) -> None:
        if stage == EXTRACTION_RESULT_RECORDED:
            raise RuntimeError("lost acknowledgement")

    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        execute_live_generic_extraction(
            command=command,
            admission=admission,
            settings=settings,
            repository=repository,
            llm=llm,
            failure_hook=lose_ack,
        )
    assert len(llm.calls) == 1

    retried = execute_live_generic_extraction(
        command=command,
        admission=admission,
        settings=settings,
        repository=repository,
        llm=llm,
    )
    assert len(llm.calls) == 1
    assert retried.recorded_live.provider_request_id.startswith("mock:")
    assert retried.extraction == retried.recorded_live.grounded_extraction
    assert retried.extraction.mode.value == "live"


def test_replay_executor_is_offline_and_binds_prior_live_authority(tmp_path: Path) -> None:
    admission = _admission(tmp_path)
    live_command = _command(admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    settings = Settings(
        paths={"workspace": tmp_path / "configured-workspace"},
        llm={
            "provider": "mock",
            "model_small": "mock-small",
            "model_medium": "mock-medium",
            "model_large": "mock-large",
        },
    )
    llm = MockLLM(settings)
    llm.push(
        "generic_grounded_claim_extraction_v2",
        json.dumps(
            {
                "claims": [
                    {
                        "quote": "Returns require a receipt.",
                        "confidence": "high",
                        "affects": ["refund-policy"],
                    }
                ]
            }
        ),
    )
    live = execute_live_generic_extraction(
        command=live_command,
        admission=admission,
        settings=settings,
        repository=repository,
        llm=llm,
    ).recorded_live
    replay_command = _command(
        admission,
        operation_id="start:replay",
        mode=ChangeExecutionModeV1.REPLAY,
    )

    replay = execute_replay_generic_extraction(
        command=replay_command,
        admission=admission,
        settings=settings,
        repository=ApplicationExtractionCallRepository(
            tmp_path / "evidence", create=False, read_only=True
        ),
        reference=live.replay_ref,
    )
    assert replay.extraction.mode.value == "replay"
    assert replay.recorded_live == live


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other-provider"),
        ("model", "other-model"),
        ("prompt_sha256", "c" * 64),
        ("response_schema_sha256", "d" * 64),
    ],
)
def test_replay_rejects_each_current_extraction_contract_mismatch(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    admission = _admission(tmp_path)
    command = _command(admission)
    request = _request(command, admission)
    repository = ApplicationExtractionCallRepository(tmp_path / "evidence")
    repository.claim(request)
    live = repository.complete_live(request, _result(request, admission))

    with pytest.raises(ApplicationExtractionCallConflictError, match="LIVE extraction"):
        ApplicationExtractionCallRepository(
            tmp_path / "evidence", create=False, read_only=True
        ).reopen_replay(
            live.replay_ref,
            command=command,
            admission=admission,
            expected_request=request.model_copy(update={field: value}),
        )
