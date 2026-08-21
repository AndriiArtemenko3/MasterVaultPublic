from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import os
import signal
from collections import Counter
from pathlib import Path
from typing import Any, cast

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
from mastervault.change_control import application_downstream as downstream
from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_start_command import (
    ApplicationStartCommandError,
    ApplicationStartCommandRepository,
)
from mastervault.change_control.change_application_contracts import (
    ActivateChangeRequestV1,
    ChangeExecutionModeV1,
    ChangeRunPhaseV1,
    ManagedAdoptionChoiceV1,
    ManagedReviewDecisionDocumentV1,
    StartChangeRequestV1,
    TemporalReviewChoiceV1,
    TemporalReviewDecisionDocumentV1,
    TemporalReviewDecisionItemV1,
)
from mastervault.change_control.inference_repository import InferenceEvidenceRepositoryError
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.operator_run import OperatorRunCommand, OperatorRunRecord
from mastervault.config import Settings
from mastervault.models import Domain

_PROCESS_TIMEOUT_SECONDS = 30.0


def _cause_chain(exc: BaseException) -> tuple[str, ...]:
    observed: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        observed.append(f"{type(current).__name__}: {current}")
        current = current.__cause__
    return tuple(observed)


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


class _ReceiptLifecycleLLM:
    """Real deterministic lifecycle double with a process-shared call receipt."""

    def __init__(self, receipt_path: Path) -> None:
        self._delegate = _AdoptionOnlyLifecycleLLM(downstream_dependency=False)
        self._receipt_path = receipt_path

    def complete(self, task: str, prompt: str, **kwargs: Any) -> Any:
        request_sha256 = hashlib.sha256(
            json.dumps(
                {"kwargs": kwargs, "prompt": prompt, "task": task},
                default=str,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with self._receipt_path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.write(
                    json.dumps(
                        {"pid": os.getpid(), "request_sha256": request_sha256, "task": task},
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return self._delegate.complete(task, prompt, **kwargs)


def _start_process(
    workspace: str,
    manifest: str,
    operation_id: str,
    source: str,
    suite: str,
    receipt_path: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    settings = _settings(Path(workspace), Path(manifest))
    provider: Any = _ReceiptLifecycleLLM(Path(receipt_path))
    cast(Any, extraction_module).get_llm = lambda _settings: provider
    cast(Any, bridge_module).get_llm = lambda _settings: provider
    ready.put(True)
    release.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    try:
        result = ChangeControlApplication(settings).start_change(
            StartChangeRequestV1(
                operation_id=operation_id,
                source=Path(source),
                domain=Domain.CUSTOMER_SUPPORT,
                regression_suite=Path(suite),
                mode=ChangeExecutionModeV1.LIVE,
            )
        )
    except BaseException as exc:  # stable process-boundary result only
        results.put(
            {
                "kind": "error",
                "type": type(exc).__name__,
                "message": str(exc),
                "causes": _cause_chain(exc),
            }
        )
    else:
        results.put({"kind": "ok", "payload": result.model_dump(mode="json")})


def _activate_process(
    workspace: str,
    manifest: str,
    run_id: str,
    operation_id: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    ready.put(True)
    release.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    try:
        result = downstream.activate_change(
            settings=_settings(Path(workspace), Path(manifest)),
            request=ActivateChangeRequestV1(run_id=run_id, operation_id=operation_id),
        )
    except BaseException as exc:  # stable process-boundary result only
        results.put(
            {
                "kind": "error",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )
    else:
        results.put({"kind": "ok", "payload": result.model_dump(mode="json")})


def _status_process(
    workspace: str,
    manifest: str,
    run_id: str,
    results: Any,
) -> None:
    try:
        application = ChangeControlApplication(_settings(Path(workspace), Path(manifest)))
        status = application.get_change_status(run_id)
        verification = application.verify_change(run_id)
    except BaseException as exc:
        results.put(
            {
                "kind": "error",
                "type": type(exc).__name__,
                "message": str(exc),
                "cause": repr(exc.__cause__),
            }
        )
    else:
        results.put(
            {
                "kind": "ok",
                "phase": status.phase.value,
                "run_id": status.run_id,
                "verified_phase": verification.status.phase.value,
            }
        )


def _fresh_start_repository_process(
    root: str,
    operation_id: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    ready.put(True)
    release.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    try:
        value = ApplicationStartCommandRepository(Path(root)).reopen_operation_optional(
            operation_id
        )
    except BaseException as exc:
        results.put(
            {
                "kind": "error",
                "type": type(exc).__name__,
                "message": str(exc),
                "cause": repr(exc.__cause__),
            }
        )
    else:
        results.put({"kind": "ok", "value": value})


def _hold_start_run_lock_process(
    root: str,
    run_id: str,
    entered: Any,
) -> None:
    repository = ApplicationStartCommandRepository(Path(root))
    authority = repository.prepare_run_lock_authority(
        run_id, claimed_at="2026-08-20T12:00:00+00:00"
    )
    with repository.run_lifecycle_lock(run_id, authority):
        entered.put(run_id)
        while True:
            signal.pause()


def _bound_run_lock_process(
    root: str,
    state_path: str,
    run_id: str,
    label: str,
    entered: Any,
    release: Any,
    results: Any,
) -> None:
    did_enter = False
    try:
        store = SqliteManagedChangeControlStore(
            Path(state_path), secure_open=True, read_only=True
        )
        try:
            authority = store.get_run_lock_authority(run_id)
        finally:
            store.close()
        if authority is None:
            raise AssertionError("bound run-lock authority is absent")
        repository = ApplicationStartCommandRepository(Path(root))
        with repository.run_lifecycle_lock(run_id, authority):
            did_enter = True
            entered.put(label)
            release.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    except BaseException as exc:
        results.put(
            {
                "entered": did_enter,
                "kind": "error",
                "message": str(exc),
                "type": type(exc).__name__,
            }
        )
    else:
        results.put({"entered": did_enter, "kind": "ok"})


def _insert_run_lock_test_operator(
    store: SqliteManagedChangeControlStore,
    *,
    operation_id: str,
) -> str:
    created_at = "2026-08-20T12:00:00+00:00"
    aggregate_id = f"run-lock-{hashlib.sha256(operation_id.encode()).hexdigest()[:16]}"
    store.conn.execute(
        "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
        (aggregate_id, hashlib.sha256(aggregate_id.encode()).hexdigest(), created_at),
    )
    command = OperatorRunCommand.create(
        operation_id=operation_id,
        aggregate_id=aggregate_id,
        base_authority_id=f"mauthority:{hashlib.sha256(operation_id.encode()).hexdigest()}",
        base_authority_revision=0,
        base_active_pointer_sha256=hashlib.sha256(
            f"{operation_id}:pointer".encode()
        ).hexdigest(),
    )
    record = OperatorRunRecord(command=command, created_at=created_at)
    store.conn.execute(
        "INSERT INTO change_control_operator_runs VALUES (?, ?, ?, ?, ?, 0, ?, 1, ?, ?)",
        (
            command.run_id,
            command.run_sha256,
            command.operation_id,
            command.aggregate_id,
            command.base_authority_id,
            command.base_active_pointer_sha256,
            canonical_json_bytes(record.model_dump(mode="json")).decode(),
            created_at,
        ),
    )
    store.conn.commit()
    return command.run_id


def _race(
    context: Any, target: Any, arguments: tuple[tuple[Any, ...], ...]
) -> list[dict[str, Any]]:
    ready = context.Queue()
    release = context.Event()
    results = context.Queue()
    processes = tuple(
        context.Process(target=target, args=(*args, ready, release, results)) for args in arguments
    )
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=_PROCESS_TIMEOUT_SECONDS) is True
        release.set()
        observed = [results.get(timeout=_PROCESS_TIMEOUT_SECONDS) for _ in processes]
        for process in processes:
            process.join(timeout=_PROCESS_TIMEOUT_SECONDS)
            assert process.exitcode == 0
        return observed
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
        for queue in (ready, results):
            queue.close()
            queue.join_thread()


def _fresh_status(context: Any, settings: Settings, manifest: Path, run_id: str) -> dict[str, Any]:
    results = context.Queue()
    process = context.Process(
        target=_status_process,
        args=(str(settings.paths.workspace), str(manifest), run_id, results),
    )
    try:
        process.start()
        observed = results.get(timeout=_PROCESS_TIMEOUT_SECONDS)
        process.join(timeout=_PROCESS_TIMEOUT_SECONDS)
        assert process.exitcode == 0
        return cast(dict[str, Any], observed)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        results.close()
        results.join_thread()


def _assert_clean_storage(settings: Settings) -> None:
    state_path = settings.paths.change_control_db_path
    store = SqliteManagedChangeControlStore(state_path, secure_open=True, read_only=True)
    try:
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_workspace_inventory_receipts"
            ).fetchone()[0]
            == 1
        )
    finally:
        store.close()
    for database in (state_path, settings.paths.sqlite_path):
        for suffix in ("-wal", "-shm", "-journal"):
            assert not Path(f"{database}{suffix}").exists()
    for lock_path in settings.paths.change_control_evidence_root.rglob("*.lock"):
        with lock_path.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_fresh_start_repository_process_race_establishes_lock_without_evidence(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "evidence"
    observed = _race(
        context,
        _fresh_start_repository_process,
        ((str(root), "process-acceptance:fresh"),) * 2,
    )
    assert observed == [
        {"kind": "ok", "value": None},
        {"kind": "ok", "value": None},
    ]
    assert not (root / "application" / "start-commands").exists()
    lock_path = root / "inference" / "evidence" / "repository.lock"
    assert lock_path.is_file()
    with lock_path.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_read_only_start_repository_never_creates_missing_root_or_lock(tmp_path: Path) -> None:
    root = tmp_path / "missing-evidence"
    with pytest.raises(InferenceEvidenceRepositoryError):
        ApplicationStartCommandRepository(root, create=False, read_only=True)
    assert not root.exists()

    root.mkdir(mode=0o700)
    repository = ApplicationStartCommandRepository(root, create=False, read_only=True)
    with pytest.raises(ApplicationStartCommandError, match="cannot be reopened"):
        repository.reopen_operation_optional("process-acceptance:read-only")
    with (
        pytest.raises(ApplicationStartCommandError, match="rejects run locking"),
        repository.run_lifecycle_lock(f"operatorrun:{'0' * 64}", cast(Any, None)),
    ):
        pass
    assert tuple(root.iterdir()) == ()


def test_run_lifecycle_locks_are_per_run_process_death_safe_and_inode_strict(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "evidence"
    entered = context.Queue()
    run_ids = (f"operatorrun:{'1' * 64}", f"operatorrun:{'2' * 64}")
    processes = tuple(
        context.Process(
            target=_hold_start_run_lock_process,
            args=(str(root), run_id, entered),
        )
        for run_id in run_ids
    )
    try:
        for process in processes:
            process.start()
        assert {entered.get(timeout=10.0), entered.get(timeout=10.0)} == set(run_ids)
        processes[0].terminate()
        processes[0].join(timeout=5.0)
        assert processes[0].exitcode is not None
        repository = ApplicationStartCommandRepository(root)
        authority = repository.prepare_run_lock_authority(
            run_ids[0], claimed_at="2026-08-20T12:00:00+00:00"
        )
        with repository.run_lifecycle_lock(run_ids[0], authority):
            pass
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
        entered.close()
        entered.join_thread()

    lock_root = root / "application" / "start-commands" / "run-locks"
    for index, unsafe_kind in enumerate(("symlink", "hardlink", "mode"), start=3):
        run_id = f"operatorrun:{str(index) * 64}"
        lock_path = lock_root / f"{hashlib.sha256(run_id.encode()).hexdigest()}.lock"
        target = tmp_path / f"unsafe-{index}"
        target.write_bytes(b"")
        target.chmod(0o600)
        if unsafe_kind == "symlink":
            lock_path.symlink_to(target)
        elif unsafe_kind == "hardlink":
            os.link(target, lock_path)
        else:
            lock_path.write_bytes(b"")
            lock_path.chmod(0o644)
        with pytest.raises(ApplicationStartCommandError):
            repository.prepare_run_lock_authority(
                run_id, claimed_at="2026-08-20T12:00:00+00:00"
            )


def test_sqlite_bound_run_lock_rejects_visible_inode_substitution(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "evidence"
    state_path = tmp_path / "authority.sqlite3"
    store = SqliteManagedChangeControlStore(state_path, secure_open=True)
    store.init_schema()
    run_id = _insert_run_lock_test_operator(store, operation_id="process-lock:bound")
    repository = ApplicationStartCommandRepository(root)
    candidate = repository.prepare_run_lock_authority(
        run_id, claimed_at="2026-08-20T12:00:00+00:00"
    )
    authority = store.claim_run_lock_authority(candidate)
    store.close()

    entered = context.Queue()
    first_release = context.Event()
    second_release = context.Event()
    second_release.set()
    results = context.Queue()
    first = context.Process(
        target=_bound_run_lock_process,
        args=(
            str(root),
            str(state_path),
            run_id,
            "first",
            entered,
            first_release,
            results,
        ),
    )
    second = context.Process(
        target=_bound_run_lock_process,
        args=(
            str(root),
            str(state_path),
            run_id,
            "second",
            entered,
            second_release,
            results,
        ),
    )
    try:
        first.start()
        assert entered.get(timeout=_PROCESS_TIMEOUT_SECONDS) == "first"
        lock_path = root / authority.relative_locator
        displaced = lock_path.with_suffix(".displaced")
        lock_path.rename(displaced)
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        assert displaced.stat().st_ino == authority.inode
        assert lock_path.stat().st_ino != authority.inode

        second.start()
        second_result = results.get(timeout=_PROCESS_TIMEOUT_SECONDS)
        assert second_result["kind"] == "error"
        assert second_result["entered"] is False
        assert "durable authority" in second_result["message"]

        first_release.set()
        first_result = results.get(timeout=_PROCESS_TIMEOUT_SECONDS)
        assert first_result["kind"] == "error"
        assert first_result["entered"] is True
        assert "substituted during lifecycle" in first_result["message"]
    finally:
        first_release.set()
        second_release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(timeout=5.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5.0)
        entered.close()
        entered.join_thread()
        results.close()
        results.join_thread()


def test_run_lifecycle_lock_normalizes_visible_inode_deletion_at_exit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    run_id = f"operatorrun:{'9' * 64}"
    repository = ApplicationStartCommandRepository(root)
    authority = repository.prepare_run_lock_authority(
        run_id, claimed_at="2026-08-20T12:00:00+00:00"
    )
    lock_path = root / authority.relative_locator

    with (
        pytest.raises(
            ApplicationStartCommandError,
            match="substituted during lifecycle execution",
        ),
        repository.run_lifecycle_lock(run_id, authority),
    ):
        lock_path.unlink()


def test_run_lifecycle_lock_rejects_visible_parent_directory_substitution_at_exit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    run_id = f"operatorrun:{'8' * 64}"
    repository = ApplicationStartCommandRepository(root)
    authority = repository.prepare_run_lock_authority(
        run_id, claimed_at="2026-08-20T12:00:00+00:00"
    )
    lock_path = root / authority.relative_locator
    lock_parent = lock_path.parent
    displaced = lock_parent.with_name("run-locks.displaced")

    with (
        pytest.raises(
            ApplicationStartCommandError,
            match="substituted during lifecycle execution",
        ),
        repository.run_lifecycle_lock(run_id, authority),
    ):
        lock_parent.rename(displaced)
        lock_parent.mkdir(mode=0o700)

    assert not lock_path.exists()
    assert (displaced / lock_path.name).stat().st_ino == authority.inode


def test_spawned_start_and_activation_lifecycle_is_single_owner_and_replayable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = multiprocessing.get_context("spawn")

    workspace, manifest = _operator_workspace(tmp_path / "exact", include_support_guide=False)
    settings = _settings(workspace, manifest)
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    source = _incoming_source(tmp_path / "exact")
    suite = _regression_suite(tmp_path / "exact")
    provider_receipts = tmp_path / "exact" / "provider-receipts.jsonl"
    operation_id = "process-acceptance:exact-start"

    exact = _race(
        context,
        _start_process,
        tuple(
            (
                str(workspace),
                str(manifest),
                operation_id,
                str(source),
                str(suite),
                str(provider_receipts),
            )
            for _ in range(2)
        ),
    )
    successful = [item for item in exact if item["kind"] == "ok"]
    errors = [item for item in exact if item["kind"] == "error"]
    assert successful
    assert errors == []
    assert len({json.dumps(item["payload"], sort_keys=True) for item in successful}) == 1
    start_payload = successful[0]["payload"]
    run_id = str(start_payload["run_id"])
    assert start_payload["phase"] == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW.value
    monkeypatch.setattr(
        extraction_module,
        "get_llm",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("exact process retry must not construct a provider")
        ),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_llm",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("exact process retry must not construct a provider")
        ),
    )
    assert (
        application.start_change(
            StartChangeRequestV1(
                operation_id=operation_id,
                source=source,
                domain=Domain.CUSTOMER_SUPPORT,
                regression_suite=suite,
                mode=ChangeExecutionModeV1.LIVE,
            )
        ).model_dump(mode="json")
        == start_payload
    )
    call_receipts = [
        json.loads(line) for line in provider_receipts.read_text(encoding="utf-8").splitlines()
    ]
    request_counts = Counter(item["request_sha256"] for item in call_receipts)
    assert request_counts and max(request_counts.values()) == 1

    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_operator_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM synchronous_application_operations "
                "WHERE operation_id=? AND run_id=?",
                (operation_id, run_id),
            ).fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_operator_run_links "
                "WHERE run_id=? AND link_kind='temporal-review-request'",
                (run_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_generation_zero_baseline_receipts "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        store.close()
    assert _fresh_status(context, settings, manifest, run_id) == {
        "kind": "ok",
        "phase": ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW.value,
        "run_id": run_id,
        "verified_phase": ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW.value,
    }

    conflict_workspace, conflict_manifest = _operator_workspace(
        tmp_path / "conflict", include_support_guide=False
    )
    conflict_settings = _settings(conflict_workspace, conflict_manifest)
    ChangeControlApplication(conflict_settings).bootstrap(conflict_manifest, BOOTSTRAP_OPERATION)
    first_source = _incoming_source(tmp_path / "conflict")
    second_source = tmp_path / "conflict" / "alternate" / "returns-policy-v2.md"
    second_source.parent.mkdir()
    second_source.write_text(
        first_source.read_text(encoding="utf-8").replace(
            "Adopt the successor policy.", "Adopt this successor policy."
        ),
        encoding="utf-8",
    )
    second_source.chmod(0o600)
    conflict_suite = _regression_suite(tmp_path / "conflict")
    conflict_results = _race(
        context,
        _start_process,
        (
            (
                str(conflict_workspace),
                str(conflict_manifest),
                "process-acceptance:conflicting-start",
                str(first_source),
                str(conflict_suite),
                str(tmp_path / "conflict" / "provider-receipts.jsonl"),
            ),
            (
                str(conflict_workspace),
                str(conflict_manifest),
                "process-acceptance:conflicting-start",
                str(second_source),
                str(conflict_suite),
                str(tmp_path / "conflict" / "provider-receipts.jsonl"),
            ),
        ),
    )
    assert Counter(item["kind"] for item in conflict_results) == {
        "ok": 1,
        "error": 1,
    }, json.dumps(conflict_results, indent=2, sort_keys=True)
    conflict_error = next(item for item in conflict_results if item["kind"] == "error")
    assert conflict_error["type"] == "ChangeControlApplicationConflictError", conflict_results
    conflict_store = SqliteManagedChangeControlStore(
        conflict_settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        assert (
            conflict_store.conn.execute(
                "SELECT count(*) FROM synchronous_application_operations "
                "WHERE operation_id='process-acceptance:conflicting-start'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conflict_store.close()

    provider = _AdoptionOnlyLifecycleLLM(downstream_dependency=False)
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: provider)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: provider)
    temporal = application.get_change_review(run_id)
    awaiting = downstream.record_change_review(
        settings=settings,
        document=TemporalReviewDecisionDocumentV1.create(
            run_id=temporal.run_id,
            request_id=temporal.request_id,
            request_sha256=temporal.request_sha256,
            operation_id="process-acceptance:temporal-decision",
            reviewer_id="reviewer.process-acceptance",
            rationale="Accept the exact generic governing-source subjects.",
            decisions=tuple(
                TemporalReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=TemporalReviewChoiceV1.ACCEPT,
                )
                for item in temporal.subjects
            ),
        ),
    )
    assert awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    managed = application.get_change_review(run_id)
    assert managed.adoption_only is True
    ready = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=managed.run_id,
            request_id=managed.request_id,
            request_sha256=managed.request_sha256,
            operation_id="process-acceptance:managed-decision",
            reviewer_id="reviewer.process-acceptance",
            rationale="Adopt the exact governing source with no downstream revisions.",
            decisions=(),
            adoption_choice=ManagedAdoptionChoiceV1.ADOPT,
        ),
    )
    assert ready.phase == ChangeRunPhaseV1.READY_TO_ACTIVATE

    activation_operation = "process-acceptance:activate"
    activations = _race(
        context,
        _activate_process,
        tuple(
            (
                str(workspace),
                str(manifest),
                run_id,
                activation_operation,
            )
            for _ in range(2)
        ),
    )
    assert all(item["kind"] == "ok" for item in activations)
    assert activations[0]["payload"] == activations[1]["payload"]
    assert activations[0]["payload"]["phase"] == ChangeRunPhaseV1.ACTIVATED.value
    activation_receipt_id = activations[0]["payload"]["activation_receipt_id"]

    final_store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        counts = {
            table: final_store.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "change_control_managed_activation_intents",
                "change_control_revision_publication_events",
                "change_control_index_generation_receipts",
                "change_control_generation_activation_receipts",
                "change_control_active_generation",
            )
        }
        assert counts == {
            "change_control_managed_activation_intents": 1,
            "change_control_revision_publication_events": 0,
            "change_control_index_generation_receipts": 1,
            "change_control_generation_activation_receipts": 1,
            "change_control_active_generation": 1,
        }
        assert (
            final_store.conn.execute(
                "SELECT count(*) FROM change_control_operator_run_links "
                "WHERE run_id=? AND link_kind='activation-operation' AND target_id=?",
                (run_id, activation_receipt_id),
            ).fetchone()[0]
            == 1
        )
    finally:
        final_store.close()
    assert _fresh_status(context, settings, manifest, run_id) == {
        "kind": "ok",
        "phase": ChangeRunPhaseV1.ACTIVATED.value,
        "run_id": run_id,
        "verified_phase": ChangeRunPhaseV1.ACTIVATED.value,
    }
    _assert_clean_storage(settings)
    _assert_clean_storage(conflict_settings)
