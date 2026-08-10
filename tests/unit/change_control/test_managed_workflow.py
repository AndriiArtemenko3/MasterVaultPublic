"""LangGraph wait/reconciliation tests for authoritative managed review."""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import replace
from itertools import combinations
from pathlib import Path

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from managed_v2_test_support import (
    RealManagedV2Scenario,
    build_real_managed_v2_scenario,
    clone_real_managed_v2_scenario,
)

from mastervault.change_control.managed_review import (
    AuthorityRevisionBinding,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
)
from mastervault.change_control.managed_review_service import (
    ManagedRevisionReviewSelection,
    decide_managed_revision_review,
    open_managed_revision_review,
)
from mastervault.change_control.managed_store import ManagedRevisionStoreLifecycle
from mastervault.change_control.managed_workflow import (
    ManagedReviewCheckpointHealth,
    ManagedReviewOrchestrationPhase,
    ManagedReviewWorkflow,
    ManagedReviewWorkflowAuthorityError,
    ManagedReviewWorkflowCheckpointError,
    ManagedReviewWorkflowNotStartedError,
    ManagedReviewWorkflowPathConflictError,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ComparableClaimPair,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
)


@pytest.fixture(scope="module")
def managed_v2_seed(tmp_path_factory: pytest.TempPathFactory) -> RealManagedV2Scenario:
    scenario = build_real_managed_v2_scenario(
        tmp_path_factory.mktemp("managed-workflow-seed")
    )
    scenario.store.close()
    return scenario


@pytest.fixture
def managed_v2_scenario(
    managed_v2_seed: RealManagedV2Scenario, tmp_path: Path
) -> RealManagedV2Scenario:
    scenario = clone_real_managed_v2_scenario(managed_v2_seed, tmp_path / "authority")
    yield scenario
    scenario.store.close()


def _open(scenario: RealManagedV2Scenario):
    return open_managed_revision_review(
        store=scenario.store,
        run_binding=scenario.run_binding,
        admitted_subjects=scenario.subjects,
        reviewed_snapshot=scenario.reviewed_snapshot,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
        operation_id="managed-workflow:open",
        requester_id="operator@example.test",
        rationale="Open the exact admitted revision bundle for managed workflow review.",
    )


def _selections(opened) -> tuple[ManagedRevisionReviewSelection, ...]:
    return tuple(
        ManagedRevisionReviewSelection(
            target_id=target.target_id,
            disposition=(
                ManagedRevisionDisposition.APPROVE
                if isinstance(target.subject, ManagedRevisionPlan)
                else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
            ),
        )
        for target in opened.request_record.command.bundle.targets
    )


def _workflow(
    scenario: RealManagedV2Scenario, checkpoint_path: Path, request_id: str
) -> ManagedReviewWorkflow:
    return ManagedReviewWorkflow(
        request_id,
        authority_path=scenario.authority_path,
        checkpoint_path=checkpoint_path,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
    )


def _rewrite_latest_checkpoint(
    checkpoint_path: Path, *, field: str, value: object
) -> None:
    connection = sqlite3.connect(checkpoint_path)
    row = connection.execute(
        "SELECT thread_id, checkpoint_ns, checkpoint_id, type, checkpoint "
        "FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    checkpoint = serializer.loads_typed((row[3], row[4]))
    checkpoint["channel_values"][field] = value
    encoded_type, encoded_blob = serializer.dumps_typed(checkpoint)
    connection.execute(
        "UPDATE checkpoints SET type=?, checkpoint=? "
        "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
        (encoded_type, encoded_blob, row[0], row[1], row[2]),
    )
    connection.commit()
    connection.close()


def _table_inventory(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def _make_review_stale(
    scenario: RealManagedV2Scenario,
    *,
    operation_id: str = "managed-workflow:post-open-analysis",
) -> None:
    snapshot = scenario.store.load(scenario.prechange_head.aggregate_id)
    assert snapshot is not None
    existing_pairs = {item.pair.pair_id for item in snapshot.aggregate.relation_graph.assessments}
    pair = next(
        candidate
        for left, right in combinations(snapshot.aggregate.claims.revisions, 2)
        if (candidate := ComparableClaimPair.create(left, right)).pair_id not in existing_pairs
    )
    additional = RelationAssessment.create(
        pair=pair,
        disposition=(
            PairDisposition.COEXISTS if pair.shared_scopes else PairDisposition.UNRELATED
        ),
        rationale="A later independent analysis records an unrelated coexisting claim pair.",
        confidence=0.75,
    )
    aggregate = snapshot.aggregate
    replacement = ChangeControlAggregate.create(
        aggregate_id=aggregate.aggregate_id,
        documents=aggregate.documents,
        claims=aggregate.claims,
        relation_graph=RelationGraph.create(
            (*aggregate.relation_graph.assessments, additional)
        ),
        dependencies=aggregate.dependencies,
        document_replacements=aggregate.document_replacements,
        temporal_constraints=aggregate.temporal_constraints,
    )
    scenario.store.compare_and_swap(
        replacement,
        expected_revision=snapshot.revision,
        operation_id=operation_id,
    )


def test_open_wait_external_decision_and_fixed_wake_reconcile_after_restart(
    managed_v2_scenario: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _open(managed_v2_scenario)
    checkpoint_path = tmp_path / "workflow" / "managed.sqlite3"
    authority_tables = _table_inventory(managed_v2_scenario.authority_path)
    assert list(inspect.signature(ManagedReviewWorkflow.resume).parameters) == ["self"]

    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        assert workflow.status().phase == ManagedReviewOrchestrationPhase.NOT_STARTED
        assert not checkpoint_path.exists()
        started = workflow.start()
        assert started.phase == ManagedReviewOrchestrationPhase.WAITING
        assert started.authoritative_lifecycle == ManagedRevisionStoreLifecycle.OPEN
        assert _table_inventory(checkpoint_path) == {"checkpoints", "writes"}
        assert _table_inventory(managed_v2_scenario.authority_path) == authority_tables
        assert workflow.resume().phase == ManagedReviewOrchestrationPhase.WAITING
        with pytest.raises(TypeError):
            workflow.resume(True)  # type: ignore[call-arg]

    decided = decide_managed_revision_review(
        store=managed_v2_scenario.store,
        request_id=opened.request_record.command.request_id,
        selections=_selections(opened),
        resolver=managed_v2_scenario.resolver,
        verified_bootstrap=managed_v2_scenario.verified_bootstrap,
        prechange_head=managed_v2_scenario.prechange_head,
        operation_id="managed-workflow:decision",
        reviewer_id="reviewer@example.test",
        rationale="Approve the grounded plan and confirm the exact no-change result.",
    )
    assert decided.decision_record is not None
    assert managed_v2_scenario.store.conn.execute(
        "SELECT count(*) FROM change_control_managed_review_decisions"
    ).fetchone()[0] == 1

    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        pending = workflow.status()
        assert pending.phase == ManagedReviewOrchestrationPhase.RECONCILIATION_PENDING
        assert pending.decision_record == decided.decision_record
        complete = workflow.resume()
        assert complete.phase == ManagedReviewOrchestrationPhase.COMPLETE
        assert complete.authoritative_lifecycle == ManagedRevisionStoreLifecycle.DECIDED
        assert complete.decision_record == decided.decision_record
        assert managed_v2_scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0] == 1

    _make_review_stale(
        managed_v2_scenario,
        operation_id="managed-workflow:post-decision-analysis",
    )
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        assert workflow.status().phase == ManagedReviewOrchestrationPhase.COMPLETE
        current = managed_v2_scenario.store.get_managed_review(
            opened.request_record.command.request_id,
            resolver=managed_v2_scenario.resolver,
            verified_bootstrap=managed_v2_scenario.verified_bootstrap,
            prechange_head=managed_v2_scenario.prechange_head,
        )
        assert current.decision_record is not None
        successor = AuthorityRevisionBinding.create_managed_successor(
            expected_authority=current.current_authority,
            decision_record=current.decision_record,
        )
        advanced = replace(current, current_authority=successor)
        monkeypatch.setattr(workflow, "_read_authority", lambda: advanced)
        status = workflow.status()
        assert status.phase == ManagedReviewOrchestrationPhase.COMPLETE
        assert status.decision_record == decided.decision_record


def test_stale_authority_is_terminal_after_fixed_wake(
    managed_v2_scenario: RealManagedV2Scenario, tmp_path: Path
) -> None:
    opened = _open(managed_v2_scenario)
    checkpoint_path = tmp_path / "workflow" / "managed.sqlite3"
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        assert workflow.start().phase == ManagedReviewOrchestrationPhase.WAITING

    _make_review_stale(managed_v2_scenario)
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        pending = workflow.status()
        assert pending.phase == ManagedReviewOrchestrationPhase.RECONCILIATION_PENDING
        assert pending.authoritative_lifecycle == ManagedRevisionStoreLifecycle.STALE
        complete = workflow.resume()
        assert complete.phase == ManagedReviewOrchestrationPhase.COMPLETE
        assert complete.authoritative_lifecycle == ManagedRevisionStoreLifecycle.STALE
        assert complete.decision_record is None

    _make_review_stale(
        managed_v2_scenario,
        operation_id="managed-workflow:later-stale-analysis",
    )
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        status = workflow.status()
        assert status.phase == ManagedReviewOrchestrationPhase.COMPLETE
        assert status.authoritative_lifecycle == ManagedRevisionStoreLifecycle.STALE
        assert status.decision_record is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_record_sha256", "0" * 64),
        ("revision_admission_id", "madmission:" + "0" * 64),
        ("bundle_sha256", "0" * 64),
        ("authority_decision_payload_sha256", "0" * 64),
    ),
)
def test_checkpoint_identity_tamper_fails_closed_but_sqlite_can_rebuild_wait(
    managed_v2_scenario: RealManagedV2Scenario,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    opened = _open(managed_v2_scenario)
    checkpoint_path = tmp_path / "workflow" / "managed.sqlite3"
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        workflow.start()
    _rewrite_latest_checkpoint(
        checkpoint_path,
        field=field,
        value=value,
    )

    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        status = workflow.status()
        assert status.checkpoint_health == ManagedReviewCheckpointHealth.CORRUPT
        assert status.phase == ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED
        assert status.authoritative_lifecycle == ManagedRevisionStoreLifecycle.OPEN
        with pytest.raises(ManagedReviewWorkflowCheckpointError):
            workflow.resume()

    checkpoint_path.unlink()
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        assert workflow.status().phase == ManagedReviewOrchestrationPhase.NOT_STARTED
        assert workflow.start().phase == ManagedReviewOrchestrationPhase.WAITING


def test_missing_request_creates_no_checkpoint_and_retry_cannot_start(
    managed_v2_scenario: RealManagedV2Scenario, tmp_path: Path
) -> None:
    checkpoint_path = tmp_path / "workflow" / "managed.sqlite3"
    request_id = "mrequest:" + "0" * 64
    with _workflow(managed_v2_scenario, checkpoint_path, request_id) as workflow:
        with pytest.raises(ManagedReviewWorkflowAuthorityError):
            workflow.status()
        assert not checkpoint_path.exists()
        with pytest.raises(ManagedReviewWorkflowAuthorityError):
            workflow.start()
        assert not checkpoint_path.exists()

    opened = _open(managed_v2_scenario)
    with _workflow(
        managed_v2_scenario,
        checkpoint_path,
        opened.request_record.command.request_id,
    ) as workflow:
        with pytest.raises(ManagedReviewWorkflowNotStartedError):
            workflow.retry()
        assert not checkpoint_path.exists()


def test_authority_and_checkpoint_paths_must_be_physically_distinct(
    managed_v2_scenario: RealManagedV2Scenario, tmp_path: Path
) -> None:
    opened = _open(managed_v2_scenario)
    request_id = opened.request_record.command.request_id
    with pytest.raises(ManagedReviewWorkflowPathConflictError):
        _workflow(managed_v2_scenario, managed_v2_scenario.authority_path, request_id)

    alias = tmp_path / "authority-alias.sqlite3"
    alias.symlink_to(managed_v2_scenario.authority_path)
    with pytest.raises(ManagedReviewWorkflowPathConflictError):
        _workflow(managed_v2_scenario, alias, request_id)
