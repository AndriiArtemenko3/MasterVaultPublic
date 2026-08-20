from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mastervault.change_control.bootstrap import bootstrap_analysis_aggregate
from mastervault.change_control.managed_review import AggregateHeadBinding
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningEligibility,
    RevisionPlanningEligibilityStatus,
    RevisionPlanningWorkload,
)
from mastervault.change_control.managed_store import (
    ManagedReviewAuthorityError,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunPhase,
    OperatorRunRecord,
    OperatorRunView,
)
from mastervault.change_control.review import ReviewDisposition
from mastervault.change_control.store import ChangeControlCorruptionError

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"


def test_list_operator_runs_is_deterministic_keyset_and_strictly_read_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authority.sqlite3"
    store = SqliteManagedChangeControlStore(path, secure_open=True)
    store.init_schema()
    bootstrap = bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id="listing:prechange",
        analysis_operation_id="listing:analysis",
    )
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=AggregateHeadBinding.create(
            aggregate_id=bootstrap.binding.aggregate_id,
            revision=bootstrap.binding.prechange_revision,
            aggregate_sha256=bootstrap.binding.prechange_aggregate_sha256,
        ),
    )
    runs = tuple(
        store.create_operator_run(
            OperatorRunCommand.create(
                operation_id=f"listing:run:{index}",
                aggregate_id=authority.aggregate_id,
                base_authority_id=authority.authority_id,
                base_authority_revision=authority.authority_revision,
                base_active_pointer_sha256=authority.active_pointer_sha256,
            )
        )
        for index in range(3)
    )
    store.close()

    before = path.read_bytes()
    before_stat = path.stat()
    reader = SqliteManagedChangeControlStore(path, secure_open=True, read_only=True)
    statements: list[str] = []
    reader.conn.set_trace_callback(statements.append)
    try:
        first = reader.list_operator_runs(limit=2, phase=OperatorRunPhase.BOOTSTRAPPED)
        assert len(first.items) == 2 and first.next_cursor is not None
        second = reader.list_operator_runs(limit=2, cursor=first.next_cursor)
        assert len(second.items) == 1 and second.next_cursor is None
        observed = tuple(item.run.record.command.run_id for item in (*first.items, *second.items))
        expected = tuple(
            item.record.command.run_id
            for item in sorted(
                runs,
                key=lambda item: (item.record.created_at, item.record.command.run_id),
                reverse=True,
            )
        )
        assert observed == expected
        assert all(item.phase == OperatorRunPhase.BOOTSTRAPPED for item in first.items)
    finally:
        reader.conn.set_trace_callback(None)
        reader.close()
    after_stat = path.stat()
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(before).digest()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )
    assert not any(
        statement.lstrip()
        .upper()
        .startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "PRAGMA WAL"))
        for statement in statements
    )
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()


def test_phase_derives_completed_no_op_only_from_reopened_no_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    eligibility = RevisionPlanningEligibility(
        status=RevisionPlanningEligibilityStatus.NO_WORK,
        workload_id=f"impactwork:{'1' * 64}",
        workload_sha256="1" * 64,
        result_id=f"impactresult:{'2' * 64}",
        result_sha256="2" * 64,
        targets=(),
    )
    workload = RevisionPlanningWorkload.create(eligibility=eligibility, input_shards=())
    # Use a real immutable record while keeping this test independent of bootstrap fixtures.
    command = OperatorRunCommand.create(
        operation_id="phase:no-work:run",
        aggregate_id="phase-no-work",
        base_authority_id=f"mauthority:{'3' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="4" * 64,
    )
    record = OperatorRunRecord(command=command, created_at="2026-08-20T12:00:00+00:00")
    temporal_link = OperatorRunLinkCommand.create(
        operation_id="phase:no-work:temporal",
        run_id=command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION,
        target_id=f"review:{'5' * 64}",
        target_sha256="6" * 64,
    )
    planning_link = OperatorRunLinkCommand.create(
        operation_id="phase:no-work:planning",
        run_id=command.run_id,
        kind=OperatorRunLinkKind.REVISION_PLANNING,
        target_id=workload.workload_id,
        target_sha256=workload.workload_sha256,
    )
    run = OperatorRunView(
        record=record,
        links=(
            OperatorRunLinkRecord(command=temporal_link, sequence=0, recorded_at=record.created_at),
            OperatorRunLinkRecord(command=planning_link, sequence=1, recorded_at=record.created_at),
        ),
    )
    monkeypatch.setattr(store, "_verify_operator_link_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        store,
        "_read_review_decision",
        lambda _request_id: SimpleNamespace(
            decision_payload_sha256=temporal_link.target_sha256,
            items=(SimpleNamespace(disposition=ReviewDisposition.ACCEPTED),),
        ),
    )

    class Resolver:
        def resolve_operator_revision_planning(self, **kwargs: object) -> RevisionPlanningWorkload:
            assert kwargs == {
                "run_id": command.run_id,
                "target_id": workload.workload_id,
                "target_sha256": workload.workload_sha256,
            }
            return workload

    try:
        assert (
            store._derive_operator_run_phase(  # noqa: SLF001
                run,
                resolver=Resolver(),  # type: ignore[arg-type]
            )
            == OperatorRunPhase.COMPLETED_NO_OP
        )
    finally:
        store.close()


def test_activated_phase_requires_exact_baseline_binding(tmp_path: Path) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    try:
        with pytest.raises(ChangeControlCorruptionError, match="exact baseline binding"):
            store._require_operator_activation_baseline(  # noqa: SLF001
                run_id=f"operatorrun:{'7' * 64}",
                activation_id=f"mactivation:{'8' * 64}",
            )
    finally:
        store.close()


def test_post_activation_state_refuses_generation_zero_baseline_backfill(
    tmp_path: Path,
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    bootstrap = bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id="backfill:prechange",
        analysis_operation_id="backfill:analysis",
    )
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=AggregateHeadBinding.create(
            aggregate_id=bootstrap.binding.aggregate_id,
            revision=bootstrap.binding.prechange_revision,
            aggregate_sha256=bootstrap.binding.prechange_aggregate_sha256,
        ),
    )
    run = store.create_operator_run(
        OperatorRunCommand.create(
            operation_id="backfill:run",
            aggregate_id=authority.aggregate_id,
            base_authority_id=authority.authority_id,
            base_authority_revision=0,
            base_active_pointer_sha256=authority.active_pointer_sha256,
        )
    )
    active = store.conn.execute(
        "SELECT active_generation_id,active_manifest_sha256 "
        "FROM change_control_active_generation WHERE aggregate_id=?",
        (authority.aggregate_id,),
    ).fetchone()
    assert active is not None
    generation = SimpleNamespace(
        generation_id=str(active["active_generation_id"]),
        active_generation_id=str(active["active_generation_id"]),
        manifest_sha256=str(active["active_manifest_sha256"]),
    )
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_active_generation SET origin_kind='managed-decision',"
            "authority_revision=1,active_generation_number=1 WHERE aggregate_id=?",
            (authority.aggregate_id,),
        )
    try:
        with pytest.raises(ManagedReviewAuthorityError, match="active generation zero"):
            store._require_baseline_active_generation_zero(  # noqa: SLF001
                run_id=run.record.command.run_id,
                generation=generation,  # type: ignore[arg-type]
            )
    finally:
        store.close()


def test_activation_baseline_cross_run_cannot_poison_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    activation_id = f"mactivation:{'8' * 64}"
    request_id = f"mrequest:{'9' * 64}"
    request = SimpleNamespace(
        command=SimpleNamespace(
            request_id=request_id,
            bundle=SimpleNamespace(run_binding=SimpleNamespace(run_id="managed-run-a")),
        )
    )
    decision = SimpleNamespace(
        record_sha256="a" * 64,
        command=SimpleNamespace(
            request_record=request,
            decision_id=f"mdecision:{'b' * 64}",
            generation_manifest=SimpleNamespace(
                manifest_id=f"mgenerationmanifest:{'c' * 64}", manifest_sha256="c" * 64
            ),
        ),
    )
    activation = SimpleNamespace(
        command=SimpleNamespace(
            request_id=request_id,
            decision_id=decision.command.decision_id,
            decision_record_sha256=decision.record_sha256,
            manifest_id=decision.command.generation_manifest.manifest_id,
            manifest_sha256=decision.command.generation_manifest.manifest_sha256,
        )
    )
    monkeypatch.setattr(store, "_read_activation_intent", lambda _value: activation)
    monkeypatch.setattr(store, "_read_request_record", lambda _value: request)
    monkeypatch.setattr(store, "_read_decision_record", lambda _value: decision)
    try:
        with pytest.raises(ManagedReviewAuthorityError, match="different managed-run"):
            store.bind_activation_to_generation_zero_baseline(
                operation_id="cross-run:bind",
                activation_id=activation_id,
                run_id="managed-run-b",
                baseline_receipt_id=f"regreceipt:{'d' * 64}",
            )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_activation_baseline_bindings"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()
