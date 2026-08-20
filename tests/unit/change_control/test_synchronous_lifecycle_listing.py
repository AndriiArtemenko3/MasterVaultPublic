from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunPhase,
    OperatorRunRecord,
    OperatorRunView,
)
from mastervault.change_control.regression_suite import RegressionSuiteV1
from mastervault.change_control.review import ReviewDisposition
from mastervault.change_control.store import ChangeControlCorruptionError
from mastervault.change_control.synchronous_lifecycle_store_models import (
    RegressionSuiteAdmissionIntentV1,
    RegressionSuiteAdmissionRecordV1,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"


def _navigation_run(*, suffix: str) -> OperatorRunView:
    command = OperatorRunCommand.create(
        operation_id=f"cross-run-navigation:{suffix}",
        aggregate_id="cross-run-navigation",
        base_authority_id=f"mauthority:{suffix * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256=suffix * 64,
    )
    return OperatorRunView(
        record=OperatorRunRecord(
            command=command,
            created_at="2026-08-20T12:00:00+00:00",
        ),
        links=(),
    )


def _temporal_navigation_authority(
    *, suffix: str
) -> tuple[OperatorRunView, OperatorRunLinkCommand, Any, object]:
    run = _navigation_run(suffix=suffix)
    proposal_link = OperatorRunLinkCommand.create(
        operation_id=f"cross-run-navigation:{suffix}:proposal",
        run_id=run.record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_PROPOSAL,
        target_id=f"temporal-commit:{suffix * 64}",
        target_sha256=suffix * 64,
    )
    subject = object()
    proposal_aggregate = object()
    proposal = SimpleNamespace(
        binding=SimpleNamespace(proposed_aggregate_sha256=suffix * 64),
        proposed_aggregate=proposal_aggregate,
        review_subjects=(SimpleNamespace(kind="temporal-constraint"),),
    )
    commit = SimpleNamespace(
        proposal=proposal,
        operation_id=proposal_link.target_id,
        aggregate_id=run.record.command.aggregate_id,
        revision=3,
        aggregate_sha256=suffix * 64,
    )
    return (
        OperatorRunView(
            record=run.record,
            links=(
                OperatorRunLinkRecord(
                    command=proposal_link,
                    sequence=0,
                    recorded_at=run.record.created_at,
                ),
            ),
        ),
        proposal_link,
        commit,
        subject,
    )


def test_temporal_review_request_link_reopens_exact_run_proposal_and_rejects_foreign_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    run, proposal_link, commit, subject = _temporal_navigation_authority(suffix="a")
    request_id = f"reviewreq:{'b' * 64}"
    request_sha256 = "c" * 64
    request = SimpleNamespace(
        request_payload_sha256=request_sha256,
        aggregate_id=run.record.command.aggregate_id,
        base_revision=commit.revision,
        base_aggregate_sha256=commit.aggregate_sha256,
        base_aggregate=commit.proposal.proposed_aggregate,
        subjects=(subject,),
    )
    monkeypatch.setattr(store, "_read_review_request", lambda _request_id: request)
    monkeypatch.setattr(
        "mastervault.change_control.managed_store.subject_from_aggregate",
        lambda _aggregate, _ref: subject,
    )
    monkeypatch.setattr(
        "mastervault.change_control.managed_store.ReviewSubjectSnapshot.create",
        classmethod(lambda _cls, _kind, value: value),
    )
    calls: list[dict[str, str]] = []

    class Resolver:
        def resolve_temporal_proposal(self, **kwargs: str) -> object:
            calls.append(kwargs)
            return commit

    link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:a:request",
        run_id=run.record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
        target_id=request_id,
        target_sha256=request_sha256,
    )
    try:
        for _ in range(2):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                run,
                resolver=Resolver(),  # type: ignore[arg-type]
            )
        assert (
            calls
            == [
                {
                    "run_id": run.record.command.run_id,
                    "target_id": proposal_link.target_id,
                    "target_sha256": proposal_link.target_sha256,
                }
            ]
            * 2
        )

        _foreign_run, _foreign_link, foreign, _foreign_subject = _temporal_navigation_authority(
            suffix="d"
        )

        class ForeignResolver:
            def resolve_temporal_proposal(self, **_kwargs: str) -> object:
                return foreign

        with pytest.raises(ChangeControlCorruptionError, match="exact run link"):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                run,
                resolver=ForeignResolver(),  # type: ignore[arg-type]
            )
    finally:
        store.close()


def test_temporal_review_decision_requires_exact_run_linked_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    run, _proposal_link, commit, subject = _temporal_navigation_authority(suffix="e")
    request_id = f"reviewreq:{'f' * 64}"
    request_sha256 = "1" * 64
    decision_sha256 = "2" * 64
    request_link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:e:request",
        run_id=run.record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
        target_id=request_id,
        target_sha256=request_sha256,
    )
    run = OperatorRunView(
        record=run.record,
        links=(
            *run.links,
            OperatorRunLinkRecord(
                command=request_link,
                sequence=1,
                recorded_at=run.record.created_at,
            ),
        ),
    )
    request = SimpleNamespace(
        request_payload_sha256=request_sha256,
        aggregate_id=run.record.command.aggregate_id,
        base_revision=commit.revision,
        base_aggregate_sha256=commit.aggregate_sha256,
        base_aggregate=commit.proposal.proposed_aggregate,
        subjects=(subject,),
    )
    decision = SimpleNamespace(
        request_id=request_id,
        decision_payload_sha256=decision_sha256,
    )
    monkeypatch.setattr(store, "_read_review_request", lambda _request_id: request)
    monkeypatch.setattr(store, "_read_review_decision", lambda _request_id: decision)
    monkeypatch.setattr(
        "mastervault.change_control.managed_store.subject_from_aggregate",
        lambda _aggregate, _ref: subject,
    )
    monkeypatch.setattr(
        "mastervault.change_control.managed_store.ReviewSubjectSnapshot.create",
        classmethod(lambda _cls, _kind, value: value),
    )

    class Resolver:
        def resolve_temporal_proposal(self, **_kwargs: str) -> object:
            return commit

    link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:e:decision",
        run_id=run.record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION,
        target_id=request_id,
        target_sha256=decision_sha256,
    )
    try:
        store._verify_operator_link_target(  # noqa: SLF001
            link,
            run,
            resolver=Resolver(),  # type: ignore[arg-type]
        )
        foreign_request_link = request_link.model_copy(
            update={"target_id": f"reviewreq:{'3' * 64}"}
        )
        foreign_run = OperatorRunView.model_construct(
            record=run.record,
            links=(
                run.links[0],
                OperatorRunLinkRecord.model_construct(
                    command=foreign_request_link,
                    sequence=1,
                    recorded_at=run.record.created_at,
                ),
            ),
        )
        with pytest.raises(ChangeControlCorruptionError, match="exact run request"):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                foreign_run,
                resolver=Resolver(),  # type: ignore[arg-type]
            )
    finally:
        store.close()


def test_incoming_navigation_link_rejects_another_runs_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    owning_run = _navigation_run(suffix="1")
    foreign_run = _navigation_run(suffix="2")
    receipt_id = f"incomingreceipt:{'3' * 64}"
    receipt_sha256 = "4" * 64
    foreign_record = SimpleNamespace(
        receipt_sha256=receipt_sha256,
        intent=SimpleNamespace(
            run_id=foreign_run.record.command.run_id,
            bundle_id=f"generic-bundle-v2:{'5' * 64}",
            bundle_sha256="5" * 64,
            admission_sha256="6" * 64,
            source_receipt_sha256="7" * 64,
            projection_sha256="8" * 64,
            inference_sha256="9" * 64,
        ),
    )
    monkeypatch.setattr(
        store,
        "_read_incoming_receipt_in_transaction",
        lambda _receipt_id: foreign_record,
    )
    resolver = SimpleNamespace(
        resolve_incoming_source=lambda _intent: SimpleNamespace(
            bundle_id=foreign_record.intent.bundle_id,
            bundle_sha256=foreign_record.intent.bundle_sha256,
            admission_sha256=foreign_record.intent.admission_sha256,
            source_receipt_sha256=foreign_record.intent.source_receipt_sha256,
            projection_sha256=foreign_record.intent.projection_sha256,
            inference_sha256=foreign_record.intent.inference_sha256,
        )
    )
    link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:incoming-link",
        run_id=owning_run.record.command.run_id,
        kind=OperatorRunLinkKind.INCOMING_SOURCE,
        target_id=receipt_id,
        target_sha256=receipt_sha256,
    )
    try:
        with pytest.raises(ChangeControlCorruptionError):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                owning_run,
                resolver=resolver,
            )
    finally:
        store.close()


def test_regression_suite_navigation_link_rejects_another_runs_receipt(
    tmp_path: Path,
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    owning_run = _navigation_run(suffix="a")
    foreign_run = _navigation_run(suffix="b")
    admitted_suite = RegressionSuiteV1.model_validate(
        {
            "schema_version": 1,
            "suite_id": "cross-run-suite",
            "suite_version": 1,
            "cases": (
                {
                    "case_id": "search-control",
                    "role": "targeted",
                    "kind": "search",
                    "query": "unchanged",
                    "k": 1,
                    "record_types": ("claim",),
                    "rerank": False,
                },
                {
                    "case_id": "search-unchanged",
                    "role": "control",
                    "kind": "search",
                    "query": "still unchanged",
                    "k": 1,
                    "record_types": ("claim",),
                    "rerank": False,
                },
            ),
        },
        strict=True,
    )
    suite = RegressionSuiteAdmissionIntentV1.create(
        operation_id="cross-run-navigation:suite-admission",
        run_id=foreign_run.record.command.run_id,
        suite_id="cross-run-suite",
        suite_version=1,
        original_sha256="c" * 64,
        original_byte_count=1,
        canonical_sha256=admitted_suite.canonical_sha256,
        suite=admitted_suite,
    )
    receipt = RegressionSuiteAdmissionRecordV1.create(
        intent=suite,
        admitted_at="2026-08-20T12:00:00+00:00",
    )
    with store.conn:
        store.conn.execute(
            "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
            ("cross-run-navigation", "d" * 64, receipt.admitted_at),
        )
        record = foreign_run.record
        command = record.command
        store.conn.execute(
            "INSERT INTO change_control_operator_runs VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                command.run_id,
                command.run_sha256,
                command.operation_id,
                command.aggregate_id,
                command.base_authority_id,
                command.base_authority_revision,
                command.base_active_pointer_sha256,
                canonical_json_bytes(record.model_dump(mode="json")).decode(),
                record.created_at,
            ),
        )
        store.conn.execute(
            "INSERT INTO change_control_regression_suite_admission_intents VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                suite.intent_id,
                suite.intent_sha256,
                suite.operation_id,
                suite.run_id,
                suite.suite_id,
                suite.suite_version,
                suite.original_sha256,
                suite.original_byte_count,
                suite.canonical_sha256,
                canonical_json_bytes(suite.model_dump(mode="json")).decode(),
            ),
        )
        store.conn.execute(
            "INSERT INTO change_control_regression_suite_admission_receipts VALUES "
            "(?, ?, ?, 1, ?, ?)",
            (
                suite.intent_id,
                receipt.receipt_id,
                receipt.receipt_sha256,
                canonical_json_bytes(receipt.model_dump(mode="json")).decode(),
                receipt.admitted_at,
            ),
        )
    link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:suite-link",
        run_id=owning_run.record.command.run_id,
        kind=OperatorRunLinkKind.REGRESSION_SUITE,
        target_id=receipt.receipt_id,
        target_sha256=receipt.receipt_sha256,
    )
    try:
        with pytest.raises(ChangeControlCorruptionError):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                owning_run,
                resolver=None,
            )
    finally:
        store.close()


def test_baseline_navigation_link_rejects_another_runs_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    owning_run = _navigation_run(suffix="e")
    foreign_run = _navigation_run(suffix="f")
    receipt_id = f"regreceipt:{'1' * 64}"
    receipt_sha256 = "2" * 64
    receipt = SimpleNamespace(receipt_id=receipt_id, receipt_sha256=receipt_sha256)
    foreign_record = SimpleNamespace(
        baseline_receipt=SimpleNamespace(
            receipt_id=receipt_id,
            receipt_sha256=receipt_sha256,
            authority=SimpleNamespace(run_id=foreign_run.record.command.run_id),
        )
    )
    monkeypatch.setattr(
        store,
        "_read_baseline_in_transaction",
        lambda _receipt_id: foreign_record,
    )
    resolver = SimpleNamespace(resolve_generation_zero_baseline=lambda _record: receipt)
    link = OperatorRunLinkCommand.create(
        operation_id="cross-run-navigation:baseline-link",
        run_id=owning_run.record.command.run_id,
        kind=OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
        target_id=receipt_id,
        target_sha256=receipt_sha256,
    )
    try:
        with pytest.raises(ChangeControlCorruptionError):
            store._verify_operator_link_target(  # noqa: SLF001
                link,
                owning_run,
                resolver=resolver,
            )
    finally:
        store.close()


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
    proposal = OperatorRunLinkCommand.create(
        operation_id="listing:proposal-only-lost-ack",
        run_id=runs[0].record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_PROPOSAL,
        target_id=f"temporal-proposal:{'e' * 64}",
        target_sha256="e" * 64,
    )
    proposal_record = OperatorRunLinkRecord(
        command=proposal,
        sequence=0,
        recorded_at=runs[0].record.created_at,
    )
    with store.conn:
        store.conn.execute(
            "INSERT INTO change_control_operator_run_links VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                proposal.run_id,
                proposal_record.sequence,
                proposal.link_id,
                proposal.link_sha256,
                proposal.operation_id,
                proposal.kind.value,
                proposal.target_id,
                proposal.target_sha256,
                canonical_json_bytes(proposal_record.model_dump(mode="json")).decode(),
                proposal_record.recorded_at,
            ),
        )
    store.close()

    before = path.read_bytes()
    before_stat = path.stat()
    reader = SqliteManagedChangeControlStore(path, secure_open=True, read_only=True)
    # The phase/listing assertion is independent of repository proposal reopening.
    # Navigation-authority tests above cover exact cross-owner verification.
    reader._verify_operator_link_target = lambda *args, **kwargs: None  # type: ignore[method-assign]  # noqa: SLF001,E501
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
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(parent_fd, fcntl.LOCK_UN)
    finally:
        os.close(parent_fd)


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


def test_proposal_only_lost_ack_remains_bootstrapped_for_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    run = _navigation_run(suffix="7")
    proposal = OperatorRunLinkCommand.create(
        operation_id="phase:proposal-only",
        run_id=run.record.command.run_id,
        kind=OperatorRunLinkKind.TEMPORAL_PROPOSAL,
        target_id=f"temporal-proposal:{'8' * 64}",
        target_sha256="8" * 64,
    )
    proposal_only = OperatorRunView(
        record=run.record,
        links=(
            OperatorRunLinkRecord(
                command=proposal,
                sequence=0,
                recorded_at=run.record.created_at,
            ),
        ),
    )
    monkeypatch.setattr(store, "_verify_operator_link_target", lambda *args, **kwargs: None)
    try:
        assert (
            store._derive_operator_run_phase(  # noqa: SLF001
                proposal_only,
                resolver=None,
            )
            == OperatorRunPhase.BOOTSTRAPPED
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
