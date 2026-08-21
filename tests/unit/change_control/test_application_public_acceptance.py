from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_application_downstream import (
    BOOTSTRAP_OPERATION,
    _AdoptionOnlyLifecycleLLM,
    _incoming_source,
    _operator_workspace,
    _regression_suite,
)

import mastervault.change_control.application_generic_extraction as extraction_module
import mastervault.change_control.application_provider_bridge as bridge_module
from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_errors import ChangeControlApplicationIntegrityError
from mastervault.change_control.application_mechanical_no_change import (
    MechanicalNoChangeEvidenceError,
    MechanicalNoChangeEvidenceRepository,
)
from mastervault.change_control.change_application_contracts import (
    ChangeExecutionModeV1,
    ChangeRunPhaseV1,
    StartChangeRequestV1,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.regression_baseline import GenerationZeroBaselineRepository
from mastervault.config import Settings
from mastervault.models import Domain
from mastervault.providers.llm import LLMResult


class _CoexistingNoWorkLifecycleLLM(_AdoptionOnlyLifecycleLLM):
    """Return complete classification output with no governing supersession."""

    def __init__(self) -> None:
        super().__init__(downstream_dependency=False)

    def complete(self, task: str, prompt: str, **kwargs: Any) -> LLMResult:
        result = super().complete(task, prompt, **kwargs)
        if task != "classification":
            return result
        payload = json.loads(result.text)
        for decision in payload["decisions"]:
            decision["disposition"] = "COEXISTS"
            decision["newer_revision_id"] = None
            decision["rationale"] = "The incoming source coexists without governing replacement."
        return LLMResult(
            text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            parsed=None,
            request_id=result.request_id,
            model=result.model,
            usage_in=result.usage_in,
            usage_out=result.usage_out,
            cost_usd=result.cost_usd,
        )


def _settings(workspace: Path, manifest: Path) -> Settings:
    return Settings.model_validate(
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


def _snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_facade_mechanical_no_op_is_terminal_exactly_replayable_and_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _operator_workspace(tmp_path)
    settings = _settings(workspace, manifest)
    application = ChangeControlApplication(settings)
    bootstrap = application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    llm = _CoexistingNoWorkLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    request = StartChangeRequestV1(
        operation_id="public-acceptance:mechanical-no-op",
        source=_incoming_source(tmp_path),
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=_regression_suite(tmp_path),
        mode=ChangeExecutionModeV1.LIVE,
    )
    legacy_before = _snapshot(workspace / "vault")
    index_before = (
        (workspace / "index.db").read_bytes(),
        (workspace / "index.db").stat().st_mtime_ns,
    )
    before_connection = sqlite3.connect(settings.paths.change_control_db_path)
    try:
        pointer_before = before_connection.execute(
            "SELECT authority_revision, active_generation_number, active_pointer_sha256 "
            "FROM change_control_active_generation"
        ).fetchone()
    finally:
        before_connection.close()

    lost_ack = RuntimeError("lost mechanical receipt acknowledgement")

    def lose_receipt_ack(boundary: str) -> None:
        if boundary == "mechanical-no-change-recorded":
            raise lost_ack

    with pytest.raises(RuntimeError) as captured:
        application.start_change(request, failure_hook=lose_receipt_ack)
    assert captured.value is lost_ack
    lost_ack_repository = MechanicalNoChangeEvidenceRepository(
        settings.paths.change_control_evidence_root,
        create=False,
        read_only=True,
    )
    lost_ack_receipt = lost_ack_repository.reopen_optional(
        bootstrap.operator_run.record.command.run_id
    )
    assert lost_ack_receipt is not None
    lost_ack_connection = sqlite3.connect(settings.paths.change_control_db_path)
    try:
        assert (
            lost_ack_connection.execute(
                "SELECT count(*) FROM change_control_operator_run_links "
                "WHERE run_id=? AND link_kind='mechanical-no-change'",
                (bootstrap.operator_run.record.command.run_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        lost_ack_connection.close()
    provider_calls = len(llm.calls)
    completed = application.start_change(request)
    assert len(llm.calls) == provider_calls
    replay = application.start_change(request)

    assert completed == replay
    assert completed.phase == ChangeRunPhaseV1.COMPLETED_NO_OP
    assert completed.run_id == bootstrap.operator_run.record.command.run_id
    assert completed.baseline is not None
    assert completed.baseline.case_count == 2
    assert completed.completeness.baseline_complete is True
    assert len(llm.calls) == provider_calls
    assert application.get_change_status(completed.run_id) == completed
    assert application.verify_change(completed.run_id).status == completed
    baseline = GenerationZeroBaselineRepository(settings.paths.change_control_evidence_root).open(
        completed.run_id
    )
    assert len(baseline.artifacts) == 2
    assert baseline.replay_ref.relative_locator.endswith("/COMPLETE.json")
    assert baseline.captured_at == completed.baseline.captured_at
    assert _snapshot(workspace / "vault") == legacy_before
    assert (
        (workspace / "index.db").read_bytes(),
        (workspace / "index.db").stat().st_mtime_ns,
    ) == index_before
    assert not settings.paths.change_control_generation_root.exists()

    connection = sqlite3.connect(settings.paths.change_control_db_path)
    try:
        assert (
            connection.execute(
                "SELECT count(*) FROM change_control_generation_zero_baseline_receipts "
                "WHERE run_id=?",
                (completed.run_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM change_control_generation_zero_baseline_cases "
                "WHERE receipt_id=?",
                (baseline.receipt_id,),
            ).fetchone()[0]
            == 2
        )
        for table in (
            "change_control_review_requests",
            "change_control_review_decisions",
            "change_control_managed_review_request_records",
            "change_control_managed_review_decisions",
            "change_control_managed_activation_intents",
            "change_control_index_generation_receipts",
            "change_control_generation_activation_receipts",
            "change_control_activation_baseline_bindings",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        pointer_after = connection.execute(
            "SELECT authority_revision, active_generation_number, active_pointer_sha256 "
            "FROM change_control_active_generation"
        ).fetchone()
        assert pointer_after == pointer_before
        assert pointer_after[:2] == (0, 0)
        assert (
            connection.execute(
                "SELECT count(*) FROM change_control_operator_run_links "
                "WHERE run_id=? AND link_kind IN "
                "('temporal-review-request','temporal-review-decision',"
                "'managed-review-request','managed-review-decision','activation-operation')",
                (completed.run_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()

    repository = MechanicalNoChangeEvidenceRepository(
        settings.paths.change_control_evidence_root,
        create=False,
        read_only=True,
    )
    receipt = repository.reopen(
        run_id=completed.run_id,
        evidence_id=next(
            item.command.target_id
            for item in application.get_status(completed.run_id).links
            if item.command.kind.value == "mechanical-no-change"
        ),
        evidence_sha256=next(
            item.command.target_sha256
            for item in application.get_status(completed.run_id).links
            if item.command.kind.value == "mechanical-no-change"
        ),
    )
    assert receipt == lost_ack_receipt
    assert receipt.completed_at == lost_ack_receipt.completed_at
    receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json"))
    assert str(tmp_path).encode() not in receipt_bytes
    with pytest.raises(MechanicalNoChangeEvidenceError):
        repository.reopen(
            run_id=f"operatorrun:{'f' * 64}",
            evidence_id=receipt.evidence_id,
            evidence_sha256=receipt.evidence_sha256,
        )
    with pytest.raises(MechanicalNoChangeEvidenceError):
        repository.reopen(
            run_id=completed.run_id,
            evidence_id=receipt.evidence_id,
            evidence_sha256="f" * 64,
        )

    receipt_path = settings.paths.change_control_evidence_root / repository.relative_locator(
        completed.run_id
    )
    receipt_path.write_bytes(receipt_bytes[:-1])
    with pytest.raises(ChangeControlApplicationIntegrityError) as status_failure:
        application.get_change_status(completed.run_id)
    assert str(status_failure.value) == "change-control evidence could not be verified"
    assert str(tmp_path) not in str(status_failure.value)
    tamper_provider_calls = len(llm.calls)
    with pytest.raises(ChangeControlApplicationIntegrityError) as retry_failure:
        application.start_change(request)
    assert str(retry_failure.value) == "change-control evidence could not be verified"
    assert str(tmp_path) not in str(retry_failure.value)
    assert len(llm.calls) == tamper_provider_calls
