from __future__ import annotations

import threading

import pytest

import mastervault.change_control.recorded_inference as recorded_module
from mastervault.change_control.application_inference_assets import (
    ApplicationInferenceWireResponse,
    load_application_inference_assets_v1,
)
from mastervault.change_control.application_provider_bridge import (
    ApplicationProviderBridgeError,
    SettingsRecordedInferenceProvider,
)
from mastervault.change_control.application_provider_calls import (
    ApplicationProviderCallClaimV1,
    ApplicationProviderCallConflictError,
    ApplicationProviderCallError,
    ApplicationProviderCallIndeterminateError,
    ApplicationProviderCallJournal,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    InferenceCorrection,
    RecordedInferenceTask,
)
from mastervault.config import Settings
from mastervault.prompts.registry import load
from mastervault.providers.llm import MockLLM

RUN_ID = f"operatorrun:{'a' * 64}"
OWNER_ID = f"start-command:{'b' * 64}"


def _settings(tmp_path) -> Settings:
    return Settings(
        paths={"workspace": tmp_path / "workspace"},
        llm={
            "provider": "mock",
            "model_small": "mock-small",
            "model_medium": "mock-medium",
            "model_large": "mock-large",
        },
    )


def _contract(settings: Settings) -> ManagedInferenceContractBinding:
    assets = load_application_inference_assets_v1()
    return ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=assets.algorithm_manifest_sha256,
        contract_id=assets.contract_id,
        contract_version=assets.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=settings.llm.provider,
        model=settings.llm.model_medium,
        prompt_sha256=assets.prompt_sha256,
        response_schema_sha256=assets.response_schema_sha256,
    )


def _request_bytes(
    contract: ManagedInferenceContractBinding,
    *,
    shard: str = "first",
    correction: InferenceCorrection | None = None,
) -> bytes:
    assets = load_application_inference_assets_v1()
    input_bytes = canonical_json_bytes({"shard": shard})
    envelope, _artifacts = recorded_module._input_envelope(  # noqa: SLF001
        task=RecordedInferenceTask.CLASSIFICATION,
        contract=contract,
        workload_id="workload:test",
        workload_sha256="1" * 64,
        input_shard_id=f"shard:{shard}",
        input_shard_sha256=recorded_module._bytes_sha256(input_bytes),  # noqa: SLF001
        input_bytes=input_bytes,
        algorithm_manifest_bytes=assets.algorithm_manifest_bytes,
        prompt_bytes=assets.prompt_bytes,
        response_schema_bytes=assets.response_schema_bytes,
    )
    request = recorded_module._provider_request(  # noqa: SLF001
        ordinal=2 if correction is not None else 1,
        task=RecordedInferenceTask.CLASSIFICATION,
        envelope=envelope,
        prompt_bytes=assets.prompt_bytes,
        response_schema_bytes=assets.response_schema_bytes,
        input_bytes=input_bytes,
        correction=correction,
    )
    return request.canonical_bytes()


def test_synchronous_lifecycle_assets_are_registry_backed_and_pinned() -> None:
    first = load_application_inference_assets_v1()
    second = load_application_inference_assets_v1()
    prompt = load("synchronous_change_inference", 1)

    assert first == second
    assert prompt.output_model is ApplicationInferenceWireResponse
    assert first.registry_contract_id == "synchronous_change_inference"
    assert first.contract_id == "synchronous-change-inference"
    assert len(first.algorithm_manifest_sha256) == 64
    assert len(first.prompt_sha256) == 64
    assert len(first.response_schema_sha256) == 64
    assert b"Larkstead" not in first.algorithm_manifest_bytes + first.prompt_bytes


def test_recorded_provider_bridge_rejects_non_medium_contract_model(tmp_path) -> None:
    settings = Settings(
        paths={"workspace": tmp_path / "workspace"},
        llm={
            "provider": "mock",
            "model_small": "small-only",
            "model_medium": "medium-required",
            "model_large": "large-only",
        },
    )
    assets = load_application_inference_assets_v1()
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=assets.algorithm_manifest_sha256,
        contract_id=assets.contract_id,
        contract_version=assets.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider="mock",
        model="small-only",
        prompt_sha256=assets.prompt_sha256,
        response_schema_sha256=assets.response_schema_sha256,
    )
    with pytest.raises(ApplicationProviderBridgeError, match="medium model"):
        SettingsRecordedInferenceProvider(
            settings,
            contract,
            journal=ApplicationProviderCallJournal(tmp_path / "evidence"),
            owner_id=OWNER_ID,
            run_id=RUN_ID,
        )


def test_completed_provider_result_retry_is_call_free(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    journal = ApplicationProviderCallJournal(tmp_path / "evidence")
    llm = MockLLM(settings)
    llm.push("classification", '{"ok":true}')
    provider = SettingsRecordedInferenceProvider(
        settings,
        contract,
        journal=journal,
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        llm=llm,
    )
    request = _request_bytes(contract)

    first = provider.complete(request=request)
    second = SettingsRecordedInferenceProvider(
        settings,
        contract,
        journal=ApplicationProviderCallJournal(tmp_path / "evidence", create=False),
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        llm=llm,
    ).complete(request=request)

    assert second == first
    assert len(llm.calls) == 1


def test_incomplete_provider_claim_retry_is_call_free_and_indeterminate(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    request_bytes = _request_bytes(contract)
    exact = recorded_module.InferenceProviderRequest.model_validate_json(
        request_bytes, strict=True
    )
    journal = ApplicationProviderCallJournal(tmp_path / "evidence")
    journal.begin(
        ApplicationProviderCallClaimV1.create(
            owner_id=OWNER_ID,
            run_id=RUN_ID,
            provider=contract.provider,
            model=contract.model,
            request=exact,
            request_bytes=request_bytes,
            claimed_at="2026-08-20T12:00:00+00:00",
        )
    )
    llm = MockLLM(settings)

    with pytest.raises(ApplicationProviderCallIndeterminateError, match="unsafe"):
        SettingsRecordedInferenceProvider(
            settings,
            contract,
            journal=journal,
            owner_id=OWNER_ID,
            run_id=RUN_ID,
            llm=llm,
        ).complete(request=request_bytes)
    assert llm.calls == []


def test_provider_journal_rejects_contract_substitution_for_same_request(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    request_bytes = _request_bytes(contract)
    exact = recorded_module.InferenceProviderRequest.model_validate_json(
        request_bytes, strict=True
    )
    journal = ApplicationProviderCallJournal(tmp_path / "evidence")
    original = ApplicationProviderCallClaimV1.create(
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        provider=contract.provider,
        model=contract.model,
        request=exact,
        request_bytes=request_bytes,
        claimed_at="2026-08-20T12:00:00+00:00",
    )
    journal.begin(original)
    substituted = ApplicationProviderCallClaimV1.create(
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        provider=contract.provider,
        model="substituted-medium",
        request=exact,
        request_bytes=request_bytes,
        claimed_at="2026-08-20T12:00:01+00:00",
    )

    with pytest.raises(ApplicationProviderCallConflictError, match="different immutable"):
        journal.begin(substituted)


def test_tampered_completed_provider_result_fails_closed_without_call(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    root = tmp_path / "evidence"
    llm = MockLLM(settings)
    llm.push("classification", '{"ok":true}')
    request = _request_bytes(contract)
    SettingsRecordedInferenceProvider(
        settings,
        contract,
        journal=ApplicationProviderCallJournal(root),
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        llm=llm,
    ).complete(request=request)
    result_paths = tuple(
        (root / "application" / "provider-calls-v1" / "results").glob("*.json")
    )
    assert len(result_paths) == 1
    result_paths[0].write_bytes(b"{}")

    with pytest.raises(ApplicationProviderCallError, match="invalid"):
        SettingsRecordedInferenceProvider(
            settings,
            contract,
            journal=ApplicationProviderCallJournal(root, create=False),
            owner_id=OWNER_ID,
            run_id=RUN_ID,
            llm=llm,
        ).complete(request=request)
    assert len(llm.calls) == 1


def test_concurrent_same_provider_request_makes_exactly_one_call(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    request = _request_bytes(contract)
    llm = MockLLM(settings)
    llm.push("classification", '{"ok":true}')
    initialized = ApplicationProviderCallJournal(tmp_path / "evidence")
    with initialized._backend._exclusive_lock():  # noqa: SLF001
        pass
    barrier = threading.Barrier(2)
    results: list[object] = []

    def invoke() -> None:
        provider = SettingsRecordedInferenceProvider(
            settings,
            contract,
            journal=ApplicationProviderCallJournal(tmp_path / "evidence"),
            owner_id=OWNER_ID,
            run_id=RUN_ID,
            llm=llm,
        )
        barrier.wait()
        try:
            results.append(provider.complete(request=request))
        except ApplicationProviderCallIndeterminateError as exc:
            results.append(exc)

    threads = (threading.Thread(target=invoke), threading.Thread(target=invoke))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert len(llm.calls) == 1


def test_prefix_resume_and_correction_requests_are_individually_owned(tmp_path) -> None:
    settings = _settings(tmp_path)
    contract = _contract(settings)
    journal = ApplicationProviderCallJournal(tmp_path / "evidence")
    llm = MockLLM(settings)
    llm.push("classification", '{"first":true}')
    llm.push("classification", '{"second":true}')
    llm.push("classification", '{"corrected":true}')
    first = _request_bytes(contract, shard="first")
    second = _request_bytes(contract, shard="second")
    correction = _request_bytes(
        contract,
        shard="first",
        correction=InferenceCorrection(
            previous_raw_output_utf8='{"first":true}',
            validation_error="exact local validation failure",
        ),
    )

    provider = SettingsRecordedInferenceProvider(
        settings,
        contract,
        journal=journal,
        owner_id=OWNER_ID,
        run_id=RUN_ID,
        llm=llm,
    )
    first_result = provider.complete(request=first)
    assert provider.complete(request=first) == first_result
    provider.complete(request=second)
    provider.complete(request=correction)

    assert len(llm.calls) == 3
