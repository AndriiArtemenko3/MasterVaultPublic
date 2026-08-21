from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
from test_application import OPERATION_ID, _workspace

import mastervault.change_control.application as application_module
import mastervault.change_control.application_generic_extraction as extraction_module
import mastervault.change_control.application_provider_bridge as bridge_module
import mastervault.change_control.application_start_lifecycle as start_module
import mastervault.change_control.regression_baseline as baseline_module
from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationIntegrityError,
)
from mastervault.change_control.application_extraction_calls import (
    ApplicationExtractionCallRepository,
)
from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.application_replay import (
    ChangeReplayBundleV1,
    ChangeReplayEvidenceIntegrityError,
    ChangeReplayStageEvidenceV1,
    ChangeReplayStageV1,
    ReplayArtifactRefV1,
    capture_completed_live_replay_bundle,
    parse_change_replay_bundle_v1,
)
from mastervault.change_control.application_runtime_identity import (
    application_configuration_sha256,
)
from mastervault.change_control.application_start_command import (
    ApplicationStartCommandRepository,
)
from mastervault.change_control.change_application_contracts import (
    ChangeExecutionModeV1,
    ChangeRunPhaseV1,
    StartChangeRequestV1,
    TemporalReviewChoiceV1,
    TemporalReviewDecisionDocumentV1,
    TemporalReviewDecisionItemV1,
)
from mastervault.change_control.generic_incoming import (
    GenericExtractionModeV2,
    admit_generic_incoming_markdown_v2,
    extraction_request_sha256_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.regression_baseline import (
    GenerationZeroBaselineRepository,
)
from mastervault.change_control.regression_suite import load_regression_suite
from mastervault.config import Settings
from mastervault.models import Domain
from mastervault.providers.embedding import MockEmbedding
from mastervault.providers.llm import LLMResult, MockLLM


class _DynamicLifecycleLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, task, prompt, **kwargs):
        del kwargs
        self.calls.append(task)
        if task == "generic_grounded_claim_extraction_v2":
            text = json.dumps(
                {
                    "claims": [
                        {
                            "quote": "Customers must present a receipt for every return.",
                            "confidence": "high",
                            "affects": ["returns"],
                        }
                    ]
                }
            )
            model = "mock-small"
        else:
            request = json.loads(prompt)
            shard = json.loads(request["input_shard_utf8"])
            if request["task"] == "classification":
                decisions = []
                for item in shard["pairs"]:
                    endpoints = item["endpoint_revisions"]
                    by_id = {entry["claim_revision_id"]: entry for entry in endpoints}
                    changed_id = item["candidate"]["changed_claim_revision_id"]
                    changed = by_id[changed_id]
                    other = next(entry for entry in endpoints if entry is not changed)
                    supersedes = (
                        changed["document"]["document_family"]
                        == other["document"]["document_family"]
                        and changed["declared_effective_from"] > other["declared_effective_from"]
                    )
                    decisions.append(
                        {
                            "pair_id": item["candidate"]["pair_id"],
                            "disposition": "SUPERSEDES" if supersedes else "UNRELATED",
                            "newer_revision_id": changed_id if supersedes else None,
                            "rationale": (
                                "The newer governing claim replaces its predecessor."
                                if supersedes
                                else "The claims govern distinct document families."
                            ),
                            "confidence": 0.99,
                        }
                    )
                text = json.dumps(
                    {
                        "schema_version": 1,
                        "task": "classification",
                        "decisions": decisions,
                    }
                )
            else:
                text = json.dumps(
                    {
                        "schema_version": 1,
                        "task": "dependency",
                        "decisions": [
                            {
                                "candidate_id": item["candidate_id"],
                                "disposition": "NOT_DEPENDENT",
                                "dependency_kind": None,
                                "selected_downstream_claim_revision_ids": [],
                                "spans": [],
                                "rationale": "No exact downstream dependency is present.",
                                "confidence": 0.99,
                            }
                            for item in shard["candidates"]
                        ],
                    }
                )
            model = "mock-medium"
        return LLMResult(
            text=text,
            parsed=None,
            request_id=f"test:{len(self.calls)}",
            model=model,
            usage_in=10,
            usage_out=10,
            cost_usd=0.0,
        )


def _source(path: Path) -> Path:
    source = path / "returns-policy-v2.md"
    source.write_text(
        "---\nmastervault_change:\n"
        "  schema_version: 1\n"
        "  event_id: returns-event-v2\n"
        "  document_id: returns-policy-v2\n"
        "  document_family: returns-policy\n"
        "  version_label: v2\n"
        "  title: Returns Policy V2\n"
        "  domain: customer-support\n"
        "  source_type: policy\n"
        "  declared_effective_from: 2026-08-20\n"
        "  role: policy\n"
        "  authority: primary\n"
        "  operator_intent: Adopt the successor policy.\n"
        "---\nCustomers must present a receipt for every return.\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    return source


def _suite(path: Path) -> Path:
    suite = path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_id": "returns-regression",
                "suite_version": 1,
                "cases": [
                    {
                        "case_id": "control-search",
                        "role": "control",
                        "kind": "search",
                        "query": "unrelated account topic",
                        "k": 1,
                        "record_types": ["claim"],
                        "rerank": False,
                    },
                    {
                        "case_id": "target-search",
                        "role": "targeted",
                        "kind": "search",
                        "query": "returns receipt",
                        "domain": "customer-support",
                        "k": 2,
                        "record_types": ["claim"],
                        "rerank": False,
                    },
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    suite.chmod(0o600)
    return suite


def test_live_start_reaches_temporal_review_and_exact_retry_makes_zero_llm_calls(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, OPERATION_ID)
    llm = _DynamicLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    request = StartChangeRequestV1(
        operation_id="start:generic-live",
        source=_source(tmp_path),
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=_suite(tmp_path),
        mode=ChangeExecutionModeV1.LIVE,
    )

    first = application.start_change(request)
    command_repository = ApplicationStartCommandRepository(
        settings.paths.change_control_evidence_root, create=False, read_only=True
    )
    original_command = command_repository.reopen_operation(request.operation_id)
    call_count = len(llm.calls)
    second = application.start_change(request)
    retried_command = command_repository.reopen_operation(request.operation_id)
    assert first == second
    assert retried_command == original_command
    assert retried_command.claimed_at == original_command.claimed_at
    assert first.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW
    assert len(llm.calls) == call_count

    source = request.source
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "Customers must present a receipt for every return.",
            "Customers must present the original receipt for every return.",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ChangeControlApplicationConflictError, match="different immutable"):
        application.start_change(request)
    assert len(llm.calls) == call_count


@pytest.mark.parametrize(
    "lost_boundary",
    (
        "application-operation-claimed",
        "generation-zero-baseline-published",
        "classification-batch-recorded",
        "dependency-batch-recorded",
        "temporal-commit-recorded",
        "temporal-evidence-recorded",
        "temporal-proposal-linked",
        "temporal-review-recorded",
        "temporal-review-linked",
    ),
)
def test_durable_lost_ack_retries_never_duplicate_provider_calls(
    tmp_path: Path, monkeypatch, lost_boundary: str
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, OPERATION_ID)
    llm = _DynamicLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    request = StartChangeRequestV1(
        operation_id="start:temporal-lost-ack",
        source=_source(tmp_path),
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=_suite(tmp_path),
        mode=ChangeExecutionModeV1.LIVE,
    )

    def lose_ack(boundary: str) -> None:
        if boundary == lost_boundary:
            raise RuntimeError("lost durable acknowledgement")

    with pytest.raises(RuntimeError, match="lost durable acknowledgement"):
        application.start_change(request, failure_hook=lose_ack)
    before = Counter(llm.calls)

    recovered = application.start_change(request)
    assert recovered.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW
    after = Counter(llm.calls)
    assert after["generic_grounded_claim_extraction_v2"] == 1
    assert after["classification"] == 1
    assert after["dependency"] == 0
    assert all(after[task] == count for task, count in before.items())


def test_completed_live_baseline_rejects_current_prompt_drift_without_calls_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, OPERATION_ID)
    llm = _DynamicLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    request = StartChangeRequestV1(
        operation_id="start:completed-baseline-prompt-drift",
        source=_source(tmp_path),
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=_suite(tmp_path),
        mode=ChangeExecutionModeV1.LIVE,
    )

    def lose_baseline_ack(boundary: str) -> None:
        if boundary == "generation-zero-baseline-published":
            raise RuntimeError("lost baseline acknowledgement")

    with pytest.raises(RuntimeError, match="lost baseline acknowledgement"):
        application.start_change(request, failure_hook=lose_baseline_ack)
    provider_call_count = len(llm.calls)
    evidence_root = settings.paths.change_control_evidence_root
    before = {
        path.relative_to(evidence_root).as_posix(): path.read_bytes()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }
    prompt_hashes, schema_hashes = baseline_module._prompt_identities()  # noqa: SLF001
    changed_prompts = dict(prompt_hashes)
    changed_prompts["grounded_synthesis.v1"] = "f" * 64
    monkeypatch.setattr(
        baseline_module,
        "_prompt_identities",
        lambda: (changed_prompts, schema_hashes),
    )
    retry_embedding = MockEmbedding()
    embedding_calls: list[tuple[str, ...]] = []
    original_embed = retry_embedding.embed

    def counted_embed(texts):
        embedding_calls.append(tuple(texts))
        return original_embed(texts)

    monkeypatch.setattr(retry_embedding, "embed", counted_embed)
    monkeypatch.setattr(start_module, "get_embedding_provider", lambda _settings: retry_embedding)
    monkeypatch.setattr(start_module, "get_llm", lambda _settings: MockLLM())

    with pytest.raises(ChangeControlApplicationIntegrityError):
        application.start_change(request)

    after = {
        path.relative_to(evidence_root).as_posix(): path.read_bytes()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }
    assert len(llm.calls) == provider_call_count
    assert embedding_calls == []
    assert after == before


def test_replay_start_reopens_live_receipts_with_zero_provider_calls(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)
    live_bootstrap = application.bootstrap(manifest, OPERATION_ID)
    state_snapshot = settings.paths.change_control_db_path.read_bytes()
    llm = _DynamicLifecycleLLM()
    embedding = MockEmbedding()
    embedding_calls: list[tuple[str, ...]] = []
    original_embed = embedding.embed

    def counted_embed(texts):
        embedding_calls.append(tuple(texts))
        return original_embed(texts)

    monkeypatch.setattr(embedding, "embed", counted_embed)
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(start_module, "get_llm", lambda _settings: MockLLM())
    monkeypatch.setattr(start_module, "get_embedding_provider", lambda _settings: embedding)
    source = _source(tmp_path)
    suite = _suite(tmp_path)
    live_request = StartChangeRequestV1(
        operation_id="start:replay-source-live",
        requested_run_id=live_bootstrap.operator_run.record.command.run_id,
        source=source,
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=suite,
        mode=ChangeExecutionModeV1.LIVE,
    )
    application.start_change(live_request)
    call_count = len(llm.calls)
    embedding_call_count = len(embedding_calls)

    evidence_root = settings.paths.change_control_evidence_root
    command = ApplicationStartCommandRepository(
        evidence_root, create=False, read_only=True
    ).reopen_operation(live_request.operation_id)
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    extraction = ApplicationExtractionCallRepository(
        evidence_root, create=False, read_only=True
    ).reopen_completed(
        start_command_id=command.command_id,
        extraction_request_sha256=extraction_request_sha256_v2(admission),
    )
    lifecycle = FilesystemLifecycleEvidenceIndex(evidence_root, create=False, read_only=True)
    inference = FilesystemInferenceEvidenceRepository(evidence_root, create=False, read_only=True)
    baseline_repository = GenerationZeroBaselineRepository(evidence_root)
    live_baseline = baseline_repository.open(command.run_id)
    source_run_id = f"operatorrun:{'e' * 64}"
    copied_source = baseline_repository.prepare_replay(
        source_reference=live_baseline.replay_ref,
        current_authority=live_baseline.authority.model_copy(
            update={
                "run_id": source_run_id,
                "incoming_admission_receipt_id": "incomingreceipt:replay-source-copy",
                "incoming_admission_receipt_sha256": "e" * 64,
            }
        ),
        current_suite=load_regression_suite(suite),
        expected_runtime=live_baseline.runtime,
    )
    replay_source_baseline = baseline_repository.publish(
        copied_source.prepared, captured_at=copied_source.captured_at
    ).receipt
    replayed_extraction = ground_generic_extraction_v2(
        admission,
        extraction.provider_contract,
        mode=GenericExtractionModeV2.REPLAY,
        replay_of=extraction.grounded_extraction,
    )
    generic_repository = FilesystemGenericIncomingRepositoryV2(evidence_root)
    replayed_incoming = generic_repository.resolve_verified_evidence(
        generic_repository.persist(admission, replayed_extraction)
    )

    def recorded_refs(stage: LifecycleEvidenceStageV1) -> tuple[ReplayArtifactRefV1, ...]:
        owner = lifecycle.reopen(command.run_id, stage).owners[0]
        outcomes = inference.resolve_batch(batch_id=owner.owner_id, batch_sha256=owner.owner_sha256)
        return tuple(
            ReplayArtifactRefV1(
                artifact_kind="recorded-inference",
                artifact_id=item.execution.receipt_artifact.artifact_id,
                artifact_sha256=item.execution.receipt_artifact.sha256,
                artifact_byte_count=item.execution.receipt_artifact.byte_count,
                relative_locator=item.execution.receipt_artifact.path,
                request_sha256=item.execution.input_envelope.input_shard_sha256,
            )
            for item in outcomes
        )

    replay_bundle = ChangeReplayBundleV1.create(
        run_id=live_bootstrap.operator_run.record.command.run_id,
        incoming_bundle_id=replayed_incoming.bundle.bundle_id,
        incoming_bundle_sha256=replayed_incoming.bundle.bundle_sha256,
        configuration_sha256=application_configuration_sha256(settings),
        stages=(
            ChangeReplayStageEvidenceV1(
                stage=ChangeReplayStageV1.BASELINE,
                artifacts=(replay_source_baseline.replay_ref,),
            ),
            ChangeReplayStageEvidenceV1(
                stage=ChangeReplayStageV1.EXTRACTION,
                artifacts=(extraction.replay_ref,),
            ),
            ChangeReplayStageEvidenceV1(
                stage=ChangeReplayStageV1.CLASSIFICATION,
                artifacts=recorded_refs(LifecycleEvidenceStageV1.CLASSIFICATION),
            ),
            ChangeReplayStageEvidenceV1(
                stage=ChangeReplayStageV1.DEPENDENCY,
                artifacts=recorded_refs(LifecycleEvidenceStageV1.DEPENDENCY),
            ),
            ChangeReplayStageEvidenceV1(stage=ChangeReplayStageV1.IMPACT, artifacts=()),
            ChangeReplayStageEvidenceV1(stage=ChangeReplayStageV1.PLANNING, artifacts=()),
        ),
    )
    mismatched_incoming = ChangeReplayBundleV1.create(
        run_id=replay_bundle.run_id,
        incoming_bundle_id=f"generic-bundle-v2:{'f' * 64}",
        incoming_bundle_sha256="f" * 64,
        configuration_sha256=replay_bundle.configuration_sha256,
        stages=replay_bundle.stages,
    )
    with pytest.raises(ValueError, match="newly derived current incoming"):
        start_module._require_replay_incoming(  # noqa: SLF001
            mismatched_incoming, replayed_incoming
        )
    replay_path = tmp_path / "replay.json"
    replay_path.write_bytes(canonical_json_bytes(replay_bundle.model_dump(mode="json")))
    settings.paths.change_control_db_path.write_bytes(state_snapshot)
    settings.paths.change_control_db_path.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{settings.paths.change_control_db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    shutil.rmtree(evidence_root / "application" / "start-commands")
    current_baseline_root = (
        evidence_root
        / "regression-baselines"
        / "runs"
        / hashlib.sha256(command.run_id.encode("utf-8")).hexdigest()
    )
    shutil.rmtree(current_baseline_root)
    lifecycle_root = evidence_root / "application" / "lifecycle-index-v1"
    for index_path in lifecycle_root.glob("*.json"):
        index_path.unlink()

    def forbidden_provider(_settings: Settings) -> object:
        raise AssertionError("strict replay must not construct a provider")

    monkeypatch.setattr(application_module, "get_embedding_provider", forbidden_provider)
    monkeypatch.setattr(start_module, "get_embedding_provider", forbidden_provider)
    monkeypatch.setattr(start_module, "get_llm", forbidden_provider)

    replay_status = application.start_change(
        StartChangeRequestV1(
            operation_id="start:offline-replay",
            requested_run_id=live_bootstrap.operator_run.record.command.run_id,
            source=source,
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=suite,
            mode=ChangeExecutionModeV1.REPLAY,
            replay_bundle=replay_path,
        )
    )
    assert replay_status.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW
    assert len(llm.calls) == call_count
    assert len(embedding_calls) == embedding_call_count
    replayed_baseline = baseline_repository.open(command.run_id)
    assert replayed_baseline.captured_at == replay_source_baseline.captured_at
    assert replayed_baseline.replay_source is not None
    assert replayed_baseline.replay_source.receipt_id == replay_source_baseline.receipt_id


def test_capture_completed_live_bundle_reopens_all_six_stages_without_calls(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, OPERATION_ID)
    llm = _DynamicLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    source = _source(tmp_path)
    started = application.start_change(
        StartChangeRequestV1(
            operation_id="start:capture-live-replay-source",
            source=source,
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=_suite(tmp_path),
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    temporal = application.get_change_review(started.run_id)
    application.record_change_review(
        TemporalReviewDecisionDocumentV1.create(
            run_id=temporal.run_id,
            request_id=temporal.request_id,
            request_sha256=temporal.request_sha256,
            operation_id="start:capture-live-temporal-accept",
            reviewer_id="reviewer.capture-test",
            rationale="Accept every exact temporal subject for replay capture.",
            decisions=tuple(
                TemporalReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=item.subject_kind,
                    choice=TemporalReviewChoiceV1.ACCEPT,
                )
                for item in temporal.subjects
            ),
        )
    )

    evidence_root = settings.paths.change_control_evidence_root
    command = ApplicationStartCommandRepository(
        evidence_root, create=False, read_only=True
    ).reopen_run(started.run_id)
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    recorded = ApplicationExtractionCallRepository(
        evidence_root, create=False, read_only=True
    ).reopen_completed(
        start_command_id=command.command_id,
        extraction_request_sha256=extraction_request_sha256_v2(admission),
    )
    replayed_extraction = ground_generic_extraction_v2(
        admission,
        recorded.provider_contract,
        mode=GenericExtractionModeV2.REPLAY,
        replay_of=recorded.grounded_extraction,
    )
    generic_repository = FilesystemGenericIncomingRepositoryV2(evidence_root)
    current_incoming = generic_repository.resolve_verified_evidence(
        generic_repository.persist(admission, replayed_extraction)
    )
    call_count = len(llm.calls)
    current_run_id = f"operatorrun:{'f' * 64}"

    captured = capture_completed_live_replay_bundle(
        evidence_root=evidence_root,
        source_run_id=started.run_id,
        current_run_id=current_run_id,
        current_incoming_bundle_id=current_incoming.bundle.bundle_id,
        current_incoming_bundle_sha256=current_incoming.bundle.bundle_sha256,
        configuration_sha256=application_configuration_sha256(settings),
    )

    assert len(llm.calls) == call_count
    assert parse_change_replay_bundle_v1(captured.canonical_bytes) == captured.bundle
    assert captured.bundle.run_id == current_run_id
    assert tuple(item.stage for item in captured.bundle.stages) == tuple(
        sorted(ChangeReplayStageV1, key=lambda item: item.value)
    )
    assert captured.bundle.require_exact_stage(ChangeReplayStageV1.IMPACT, ()) == ()
    assert captured.bundle.require_exact_stage(ChangeReplayStageV1.PLANNING, ()) == ()
    with pytest.raises(ChangeReplayEvidenceIntegrityError, match="configuration-bound"):
        capture_completed_live_replay_bundle(
            evidence_root=evidence_root,
            source_run_id=started.run_id,
            current_run_id=current_run_id,
            current_incoming_bundle_id=current_incoming.bundle.bundle_id,
            current_incoming_bundle_sha256=current_incoming.bundle.bundle_sha256,
            configuration_sha256="0" * 64,
        )
    assert len(llm.calls) == call_count
