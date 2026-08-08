from __future__ import annotations

import inspect
import os
import runpy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from mastervault.change_control import (
    CheckpointHealth,
    HumanReviewDecisionCommand,
    OrchestrationPhase,
    ReviewDisposition,
    ReviewLifecycle,
    SqliteChangeControlStore,
    TemporalReviewAuthorityError,
    TemporalReviewCheckpointError,
    TemporalReviewClosedError,
    TemporalReviewNotStartedError,
    TemporalReviewPathConflictError,
    TemporalReviewWorkflow,
)
from mastervault.change_control import workflow as workflow_module
from mastervault.config import PathsCfg

_STORE_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_store.py")))
_decision_item = _STORE_HELPERS["_decision_item"]
_request_review = _STORE_HELPERS["_request_review"]
_store = _STORE_HELPERS["_store"]
proposed_full_aggregate = _STORE_HELPERS["proposed_full_aggregate"]


@pytest.fixture
def authoritative_review(tmp_path: Path):
    state_path = tmp_path / "change_control" / "state.sqlite3"
    checkpoint_path = tmp_path / "change_control" / "checkpoints.sqlite3"
    aggregate = proposed_full_aggregate()
    store = _store(state_path)
    store.create(aggregate, operation_id="seed-workflow")
    receipt = _request_review(
        store,
        aggregate,
        operation_id="request-workflow",
    )
    store.close()
    return state_path, checkpoint_path, aggregate, receipt.request


def _reject_authoritatively(state_path: Path, request):
    store = SqliteChangeControlStore(state_path)
    receipt = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request_id,
            reviewer_id="reviewer@example.com",
            rationale="The exact proposed temporal changes are not approved.",
            items=tuple(
                _decision_item(subject, ReviewDisposition.REJECTED) for subject in request.subjects
            ),
        ),
        operation_id="decide-workflow",
    )
    store.close()
    return receipt


def _authority_fingerprint(path: Path) -> tuple[str, list[str]]:
    connection = sqlite3.connect(path)
    metadata = connection.execute(
        "SELECT value FROM change_control_meta WHERE key='schema_sha256'"
    ).fetchone()[0]
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    connection.close()
    return metadata, tables


def _rewrite_latest_checkpoint(
    checkpoint_path: Path,
    *,
    field: str | None = None,
    value: object = None,
    extra_field: str | None = None,
    checkpoint_type: str | None = None,
    blob: bytes | None = None,
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
    if blob is None:
        checkpoint = serializer.loads_typed((row[3], row[4]))
        if field is not None:
            checkpoint["channel_values"][field] = value
        if extra_field is not None:
            checkpoint["channel_values"][extra_field] = value
        encoded_type, encoded_blob = serializer.dumps_typed(checkpoint)
    else:
        encoded_type, encoded_blob = checkpoint_type or row[3], blob
    connection.execute(
        "UPDATE checkpoints SET type=?, checkpoint=? "
        "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
        (encoded_type, encoded_blob, row[0], row[1], row[2]),
    )
    connection.commit()
    connection.close()


def test_open_start_interrupt_and_repeated_start_are_idempotent(
    authoritative_review,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        assert workflow.status().phase == OrchestrationPhase.NOT_STARTED
        assert not checkpoint_path.exists()
        first = workflow.start()
        assert workflow._checkpoint_conn is not None  # noqa: SLF001
        checkpoint_count = workflow._checkpoint_conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM checkpoints"
        ).fetchone()[0]
        second = workflow.start()
        assert first.phase == second.phase == OrchestrationPhase.WAITING
        assert first.authoritative_lifecycle == ReviewLifecycle.OPEN
        assert (
            workflow._checkpoint_conn.execute(  # noqa: SLF001
                "SELECT count(*) FROM checkpoints"
            ).fetchone()[0]
            == checkpoint_count
        )


@pytest.mark.parametrize(
    ("failing_method", "failure_timing"),
    [
        ("put", "before"),
        ("put", "after"),
        ("put_writes", "before"),
        ("put_writes", "after"),
    ],
)
def test_initial_start_checkpoint_failures_converge_without_mutating_open_authority(
    authoritative_review,
    monkeypatch: pytest.MonkeyPatch,
    failing_method: str,
    failure_timing: str,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    authority_before = _authority_fingerprint(state_path)
    workflow = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    workflow._initialize_checkpoint()  # noqa: SLF001
    assert workflow._checkpointer is not None  # noqa: SLF001
    original = getattr(workflow._checkpointer, failing_method)  # noqa: SLF001
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            if failure_timing == "before":
                raise RuntimeError("injected initial checkpoint failure before write")
            original(*args, **kwargs)
            raise RuntimeError("injected initial checkpoint failure after write")
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow._checkpointer, failing_method, fail_once)  # noqa: SLF001
    with pytest.raises(TemporalReviewCheckpointError):
        workflow.start()
    workflow.close()
    assert _authority_fingerprint(state_path) == authority_before
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 0
    )
    store.close()

    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as reopened:
        status = reopened.status()
        if status.phase == OrchestrationPhase.NOT_STARTED:
            with pytest.raises(TemporalReviewNotStartedError):
                reopened.retry()
            status = reopened.start()
        elif status.phase == OrchestrationPhase.RECOVERY_REQUIRED:
            status = reopened.retry()
        assert status.phase == OrchestrationPhase.WAITING
        assert status.authoritative_lifecycle == ReviewLifecycle.OPEN
    assert _authority_fingerprint(state_path) == authority_before


def test_resume_has_no_payload_and_open_authority_reinterrupts(authoritative_review) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    assert list(inspect.signature(TemporalReviewWorkflow.resume).parameters) == ["self"]
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
        resumed = workflow.resume()
        assert resumed.phase == OrchestrationPhase.WAITING
        assert resumed.authoritative_lifecycle == ReviewLifecycle.OPEN
        with pytest.raises(TypeError):
            workflow.resume(True)  # type: ignore[call-arg]


def test_external_decision_is_pending_then_reconciled_exactly(authoritative_review) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    receipt = _reject_authoritatively(state_path, request)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        pending = workflow.status()
        assert pending.phase == OrchestrationPhase.RECONCILIATION_PENDING
        assert pending.decision == receipt.decision
        complete = workflow.resume()
        assert complete.phase == OrchestrationPhase.COMPLETE
        assert complete.decision == receipt.decision


@pytest.mark.parametrize(
    ("failing_method", "failure_timing"),
    [
        ("put", "before"),
        ("put", "after"),
        ("put_writes", "before"),
        ("put_writes", "after"),
    ],
)
def test_checkpoint_response_failure_cannot_duplicate_authoritative_decision(
    authoritative_review,
    monkeypatch: pytest.MonkeyPatch,
    failing_method: str,
    failure_timing: str,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    receipt = _reject_authoritatively(state_path, request)
    store = SqliteChangeControlStore(state_path)
    before = store.load(request.aggregate_id)
    assert before is not None
    store.close()
    authority_fingerprint = _authority_fingerprint(state_path)

    workflow = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    assert workflow.status().phase == OrchestrationPhase.RECONCILIATION_PENDING
    assert workflow._checkpointer is not None  # noqa: SLF001
    original = getattr(workflow._checkpointer, failing_method)  # noqa: SLF001
    failed = False

    def fail_after_write(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            if failure_timing == "before":
                raise RuntimeError("injected checkpoint failure before write")
            original(*args, **kwargs)
            raise RuntimeError("injected lost checkpoint acknowledgement")
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow._checkpointer, failing_method, fail_after_write)  # noqa: SLF001
    with pytest.raises(TemporalReviewCheckpointError):
        workflow.resume()
    workflow.close()

    monkeypatch.setattr(
        SqliteChangeControlStore,
        "decide_review",
        lambda *args, **kwargs: pytest.fail("workflow must never decide review"),
    )
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as reopened:
        final = reopened.retry()
        assert final.phase == OrchestrationPhase.COMPLETE
        assert final.decision == receipt.decision
    store = SqliteChangeControlStore(state_path)
    after = store.load(request.aggregate_id)
    decision_count = store.conn.execute(
        "SELECT count(*) FROM change_control_review_decisions WHERE request_id=?",
        (request.request_id,),
    ).fetchone()[0]
    store.close()
    assert after == before
    assert decision_count == 1
    assert _authority_fingerprint(state_path) == authority_fingerprint


def test_stale_authority_ends_without_synthesizing_a_decision(authoritative_review) -> None:
    state_path, checkpoint_path, aggregate, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    store = SqliteChangeControlStore(state_path)
    store.compare_and_swap(
        proposed_full_aggregate(relation_rationale="A separately committed revision."),
        expected_revision=1,
        operation_id="make-review-stale",
    )
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.STALE
    store.close()
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        status = workflow.resume()
        assert status.phase == OrchestrationPhase.COMPLETE
        assert status.authoritative_lifecycle == ReviewLifecycle.STALE
        assert status.decision is None
    assert aggregate.document_replacements.assessments[0].status.value == "proposed"


def test_missing_busy_and_corrupt_authority_fail_without_creating_lifecycle(
    tmp_path: Path, authoritative_review
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    missing = tmp_path / "missing" / "state.sqlite3"
    with (
        TemporalReviewWorkflow(
            request,
            authority_path=missing,
            checkpoint_path=tmp_path / "missing" / "checkpoints.sqlite3",
        ) as workflow,
        pytest.raises(TemporalReviewAuthorityError),
    ):
        workflow.status()
    assert not missing.exists()

    locker = sqlite3.connect(state_path)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=checkpoint_path,
            timeout_seconds=0,
        ) as workflow:
            with pytest.raises(TemporalReviewAuthorityError) as busy:
                workflow.status()
            assert busy.value.retryable
    finally:
        locker.rollback()
        locker.close()

    corrupt = tmp_path / "corrupt" / "state.sqlite3"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"not sqlite")
    with (
        TemporalReviewWorkflow(
            request,
            authority_path=corrupt,
            checkpoint_path=tmp_path / "corrupt" / "checkpoints.sqlite3",
        ) as workflow,
        pytest.raises(TemporalReviewAuthorityError),
    ):
        workflow.start()


@pytest.mark.parametrize(
    ("mutation", "value"),
    [("approved", True), ("request_id", "reviewreq:" + "0" * 64), ("workflow_schema_version", 999)],
)
def test_forged_or_incompatible_checkpoint_state_fails_closed(
    authoritative_review,
    mutation: str,
    value: object,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    if mutation == "approved":
        _rewrite_latest_checkpoint(checkpoint_path, extra_field=mutation, value=value)
    else:
        _rewrite_latest_checkpoint(checkpoint_path, field=mutation, value=value)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        status = workflow.status()
        assert status.checkpoint_health == CheckpointHealth.CORRUPT
        assert status.phase == OrchestrationPhase.RECOVERY_REQUIRED
        assert status.authoritative_lifecycle == ReviewLifecycle.OPEN
        with pytest.raises(TemporalReviewCheckpointError):
            workflow.resume()


@pytest.mark.parametrize(
    "corruption",
    ["pickle", "malformed-msgpack", "unknown-extension", "malformed-metadata"],
)
def test_disallowed_checkpoint_encoding_is_not_deleted(
    authoritative_review, corruption: str
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    if corruption == "pickle":
        _rewrite_latest_checkpoint(
            checkpoint_path,
            checkpoint_type="pickle",
            blob=b"forbidden payload",
        )
    elif corruption == "malformed-msgpack":
        _rewrite_latest_checkpoint(
            checkpoint_path,
            checkpoint_type="msgpack",
            blob=b"\xc1",
        )
    elif corruption == "unknown-extension":
        _rewrite_latest_checkpoint(
            checkpoint_path,
            checkpoint_type="msgpack",
            # msgpack ext8: 11-byte payload, unknown extension code 99.
            blob=b"\xc7\x0b\x63unsupported",
        )
    else:
        connection = sqlite3.connect(checkpoint_path)
        connection.execute(
            "UPDATE checkpoints SET metadata='{' WHERE checkpoint_id="
            "(SELECT checkpoint_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1)"
        )
        connection.commit()
        connection.close()
    before = checkpoint_path.read_bytes()
    authority_before = _authority_fingerprint(state_path)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        status = workflow.status()
        assert status.checkpoint_health == CheckpointHealth.CORRUPT
        assert status.authoritative_lifecycle == ReviewLifecycle.OPEN
    assert checkpoint_path.read_bytes() == before
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    store.close()
    assert _authority_fingerprint(state_path) == authority_before


def test_authority_and_checkpoint_paths_must_be_physically_distinct(
    authoritative_review, tmp_path: Path
) -> None:
    state_path, _, _, request = authoritative_review
    with pytest.raises(TemporalReviewPathConflictError):
        TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=state_path,
        )
    alias = tmp_path / "authority-alias.sqlite3"
    alias.symlink_to(state_path)
    with pytest.raises(TemporalReviewPathConflictError):
        TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=alias,
        )


def test_in_memory_checkpoint_target_is_rejected_as_non_durable(
    authoritative_review,
) -> None:
    state_path, _, _, request = authoritative_review
    authority_before = _authority_fingerprint(state_path)
    for _ in range(2):
        with TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=":memory:",
        ) as workflow:
            assert workflow.status().phase == OrchestrationPhase.NOT_STARTED
            with pytest.raises(TemporalReviewPathConflictError, match="file-backed"):
                workflow.start()
            assert workflow._checkpoint_conn is None  # noqa: SLF001
    assert _authority_fingerprint(state_path) == authority_before


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_interrupted_checkpoint_initialization_closes_connection_and_propagates(
    authoritative_review,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_type: type[BaseException],
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    workflow = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    opened: list[sqlite3.Connection] = []

    def interrupt_after_open() -> None:
        assert workflow._checkpoint_conn is not None  # noqa: SLF001
        opened.append(workflow._checkpoint_conn)  # noqa: SLF001
        raise interrupt_type()

    monkeypatch.setattr(workflow, "_validate_open_checkpoint_connection", interrupt_after_open)
    with pytest.raises(interrupt_type):
        workflow.start()
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert workflow._checkpoint_conn is None  # noqa: SLF001
    assert workflow._checkpointer is None  # noqa: SLF001
    assert workflow._graph is None  # noqa: SLF001
    workflow.close()


def test_checkpoint_path_swap_during_open_cannot_write_authority_schema(
    authoritative_review, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    authority_before = _authority_fingerprint(state_path)
    real_connect = sqlite3.connect
    swapped = False

    def swap_before_checkpoint_open(database, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(database) == str(checkpoint_path):
            swapped = True
            checkpoint_path.symlink_to(state_path)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(workflow_module.sqlite3, "connect", swap_before_checkpoint_open)
    with (
        TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=checkpoint_path,
        ) as workflow,
        pytest.raises(TemporalReviewPathConflictError),
    ):
        workflow.start()
    assert swapped
    assert _authority_fingerprint(state_path) == authority_before
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert not {
        "checkpoints",
        "writes",
    }.intersection(_authority_fingerprint(state_path)[1])
    store.close()


def test_checkpoint_filesystem_open_failures_are_typed(
    authoritative_review, tmp_path: Path
) -> None:
    state_path, _, _, request = authoritative_review
    blocking_parent = tmp_path / "blocking-parent"
    blocking_parent.write_text("not a directory", encoding="utf-8")
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=blocking_parent / "checkpoints.sqlite3",
    ) as workflow:
        with pytest.raises(TemporalReviewCheckpointError) as blocked:
            workflow.start()
        assert not blocked.value.retryable

    directory_database = tmp_path / "directory.sqlite3"
    directory_database.mkdir()
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=directory_database,
    ) as workflow:
        with pytest.raises(TemporalReviewCheckpointError) as directory:
            workflow.start()
        assert not directory.value.retryable


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_checkpoint_permission_failure_is_typed_when_enforced(
    authoritative_review, tmp_path: Path
) -> None:
    state_path, _, _, request = authoritative_review
    protected_parent = tmp_path / "protected"
    protected_parent.mkdir()
    protected_parent.chmod(0)
    try:
        if os.access(protected_parent, os.W_OK):
            pytest.skip("current user bypasses directory permission bits")
        with TemporalReviewWorkflow(
            request,
            authority_path=state_path,
            checkpoint_path=protected_parent / "checkpoints.sqlite3",
        ) as workflow:
            with pytest.raises(TemporalReviewCheckpointError) as denied:
                workflow.start()
            assert not denied.value.retryable
    finally:
        protected_parent.chmod(0o700)


def test_checkpoint_disappearing_between_is_file_and_resolve_is_retryable(
    authoritative_review, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    reopened = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    real_resolve = Path.resolve
    deleted = False

    def delete_before_resolve(path: Path, strict: bool = False):
        nonlocal deleted
        if path == checkpoint_path and strict and not deleted:
            deleted = True
            checkpoint_path.unlink()
            raise FileNotFoundError(checkpoint_path)
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", delete_before_resolve)
    try:
        with pytest.raises(TemporalReviewCheckpointError) as raced:
            reopened.status()
        assert raced.value.retryable
    finally:
        reopened.close()
    assert deleted
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    store.close()


def test_checkpoint_tables_are_separate_and_authority_schema_is_unchanged(
    authoritative_review,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    before = _authority_fingerprint(state_path)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        workflow.start()
    assert _authority_fingerprint(state_path) == before
    checkpoint = sqlite3.connect(checkpoint_path)
    checkpoint_tables = {
        row[0]
        for row in checkpoint.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    checkpoint.close()
    assert checkpoint_tables == {"checkpoints", "writes"}
    assert not any(table in {"checkpoints", "writes"} for table in before[1])


def test_concurrent_same_service_resumes_are_serialized_and_never_mutate_authority(
    authoritative_review,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    workflow = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    workflow.start()
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: workflow.resume(), range(8)))
    workflow.close()
    assert all(item.phase == OrchestrationPhase.WAITING for item in statuses)
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 0
    )
    store.close()
    decided = _reject_authoritatively(state_path, request)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as reconciler:
        assert reconciler.status().phase == OrchestrationPhase.RECONCILIATION_PENDING
        complete = reconciler.resume()
        assert complete.phase == OrchestrationPhase.COMPLETE
        assert complete.decision == decided.decision
    store = SqliteChangeControlStore(state_path)
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_review_decisions WHERE request_id=?",
            (request.request_id,),
        ).fetchone()[0]
        == 1
    )
    store.close()


def test_two_independent_services_converge_without_authority_mutation(
    authoritative_review,
) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    first = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
        timeout_seconds=0.1,
    )
    second = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
        timeout_seconds=0.1,
    )
    first.start()

    def resume(service: TemporalReviewWorkflow):
        try:
            return service.resume()
        except TemporalReviewCheckpointError as exc:
            assert exc.retryable
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, (first, second)))
    first.close()
    second.close()
    assert all(
        isinstance(item, TemporalReviewCheckpointError) or item.phase == OrchestrationPhase.WAITING
        for item in results
    )
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as reopened:
        assert reopened.status().phase == OrchestrationPhase.WAITING
    store = SqliteChangeControlStore(state_path)
    assert store.get_review_request(request.request_id).lifecycle == ReviewLifecycle.OPEN
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_review_decisions").fetchone()[0]
        == 0
    )
    store.close()
    decided = _reject_authoritatively(state_path, request)
    with TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as reconciler:
        assert reconciler.status().phase == OrchestrationPhase.RECONCILIATION_PENDING
        complete = reconciler.resume()
        assert complete.phase == OrchestrationPhase.COMPLETE
        assert complete.decision == decided.decision
    store = SqliteChangeControlStore(state_path)
    assert (
        store.conn.execute(
            "SELECT count(*) FROM change_control_review_decisions WHERE request_id=?",
            (request.request_id,),
        ).fetchone()[0]
        == 1
    )
    store.close()


def test_configured_checkpoint_path_is_exact_distinct_sibling(tmp_path: Path) -> None:
    paths = PathsCfg(workspace=tmp_path / "workspace")
    assert paths.change_control_db_path == (
        tmp_path / "workspace" / "change_control" / "state.sqlite3"
    )
    assert paths.change_control_checkpoint_path == (
        tmp_path / "workspace" / "change_control" / "checkpoints.sqlite3"
    )
    assert paths.change_control_checkpoint_path != paths.change_control_db_path


def test_owned_checkpoint_connection_closes(authoritative_review) -> None:
    state_path, checkpoint_path, _, request = authoritative_review
    workflow = TemporalReviewWorkflow(
        request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    )
    workflow.start()
    workflow.close()
    workflow.close()
    with pytest.raises(TemporalReviewClosedError):
        workflow.status()
    with pytest.raises(sqlite3.ProgrammingError):
        workflow._checkpoint_conn.execute("SELECT 1")  # noqa: SLF001
