"""Focused integration tests for the stable change-control application façade."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

import mastervault.change_control as change_control_package
import mastervault.change_control.application as application_module
from mastervault.change_control.application import (
    AGGREGATE_CREATED,
    AUTHORITY_HANDOFF_STARTED,
    BOOTSTRAP_INTENT_CLAIMED,
    GENERATION_ZERO_INITIALIZED,
    LEGACY_INDEX_READINESS_RECORDED,
    OPERATOR_RUN_CREATED,
    SCHEMA_INITIALIZED,
    WORKSPACE_INVENTORY_RECORDED,
    BootstrapSourceRoot,
    ChangeControlApplication,
)
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationIntegrityError,
    ChangeControlApplicationUnsupportedOperationError,
    ChangeControlApplicationUsageError,
)
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.operator_run import OperatorRunLinkKind
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
)
from mastervault.config import Settings
from mastervault.models import content_hash
from mastervault.providers import MockEmbedding
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import sync_vault

MINI_VAULT = Path(__file__).parents[2] / "fixtures" / "mini_vault"
MANAGED = "customer-support/sources/policy-returns-and-refunds.md"
RAW = "raw/policy-returns-and-refunds.md"
OPERATION_ID = "workspace-bootstrap:test-v1"
LINK_STAGES = (
    "operator-link-bootstrap-intent-recorded",
    "operator-link-workspace-inventory-recorded",
    "operator-link-legacy-index-readiness-recorded",
    "operator-link-generation-zero-authority-recorded",
)
ALL_DURABLE_STAGES = (
    SCHEMA_INITIALIZED,
    BOOTSTRAP_INTENT_CLAIMED,
    AGGREGATE_CREATED,
    WORKSPACE_INVENTORY_RECORDED,
    LEGACY_INDEX_READINESS_RECORDED,
    AUTHORITY_HANDOFF_STARTED,
    GENERATION_ZERO_INITIALIZED,
    OPERATOR_RUN_CREATED,
    *LINK_STAGES,
)
REPOSITORY_ROOT = Path(__file__).parents[3]
PROCESS_CRASH_EXIT = 73
PROCESS_WORKER = textwrap.dedent(
    r"""
    import json
    import os
    import sys
    import time
    from pathlib import Path

    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.config import Settings

    workspace = Path(sys.argv[1])
    manifest = Path(sys.argv[2])
    operation_id = sys.argv[3]
    target = sys.argv[4]
    marker = Path(sys.argv[5]) if sys.argv[5] else None
    if marker is not None:
        deadline = time.monotonic() + 10.0
        while not marker.exists():
            if time.monotonic() >= deadline:
                raise SystemExit(74)
            time.sleep(0.01)

    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
        }
    )

    def failure_hook(stage: str) -> None:
        if target and stage == target:
            os._exit(73)

    result = ChangeControlApplication(settings).bootstrap(
        manifest,
        operation_id,
        failure_hook=failure_hook,
    )
    inventory, readiness = result.bootstrap_state.require_complete()
    payload = {
        "bootstrap_id": result.bootstrap_state.intent.bootstrap_id,
        "intent_sha256": result.bootstrap_state.intent.intent_sha256,
        "inventory_receipt": [inventory.receipt_id, inventory.receipt_sha256],
        "index_receipt": [readiness.receipt_id, readiness.receipt_sha256],
        "authority": [result.authority.authority_id, result.authority.active_pointer_sha256],
        "run": [
            result.operator_run.record.command.run_id,
            result.operator_run.record.command.run_sha256,
        ],
        "links": [
            [
                item.command.link_id,
                item.command.link_sha256,
                item.command.kind.value,
                item.command.target_id,
                item.command.target_sha256,
            ]
            for item in result.operator_run.links
        ],
    }
    print(json.dumps(payload, sort_keys=True), flush=True)
    """
)


def test_change_control_package_exports_the_stable_application_facade() -> None:
    assert change_control_package.BootstrapSourceRoot is BootstrapSourceRoot
    assert change_control_package.ChangeControlApplication is ChangeControlApplication
    assert "BootstrapSourceRoot" in change_control_package.__all__
    assert "ChangeControlApplication" in change_control_package.__all__


def test_failed_query_construction_closes_every_resource_and_retains_failure() -> None:
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def close(self) -> None:
            closed.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} close failed")

    original = ChangeControlApplicationIntegrityError("construction failed")
    with pytest.raises(ChangeControlApplicationIntegrityError) as captured:
        application_module._close_failed_query_resources(  # noqa: SLF001
            original,
            Resource("backend", fail=True),
            Resource("store"),
            None,
            Resource("workspace-guard"),
        )

    assert captured.value is original
    assert closed == ["backend", "store", "workspace-guard"]


def test_query_generation_selector_is_exact_and_versioned() -> None:
    assert QueryGenerationSelectionV1.parse("auto") == QueryGenerationSelectionV1(
        selector=QueryGenerationSelector.AUTO
    )
    exact = f"mgeneration:{'a' * 64}"
    parsed = QueryGenerationSelectionV1.parse(exact)
    assert parsed.selector == QueryGenerationSelector.GENERATION_ID
    assert parsed.generation_id == exact
    for invalid in ("", " auto", "generation-id", "mgeneration:" + "A" * 64):
        with pytest.raises(ValueError):
            QueryGenerationSelectionV1.parse(invalid)


def test_query_generation_resolution_rejects_constructed_or_substituted_selection(
    tmp_path: Path,
) -> None:
    workspace, _manifest = _workspace(tmp_path)
    application = ChangeControlApplication(_settings(workspace))
    constructed = QueryGenerationSelectionV1.model_construct(
        selector=QueryGenerationSelector.GENERATION_ID,
        generation_id=None,
    )

    with pytest.raises(ChangeControlApplicationUsageError):
        application.resolve_query_generation(constructed)

    class SubstitutedSelection(QueryGenerationSelectionV1):
        pass

    with pytest.raises(ChangeControlApplicationUsageError, match="substituted"):
        application.resolve_query_generation(
            SubstitutedSelection(selector=QueryGenerationSelector.AUTO)
        )


def test_unmanaged_query_resolution_preserves_v02_and_rejects_explicit_selection(
    tmp_path: Path,
) -> None:
    workspace, _manifest = _workspace(tmp_path)
    application = ChangeControlApplication(_settings(workspace))

    with application.resolve_query_generation() as resolved:
        assert resolved.metadata.generation_kind == QueryGenerationKind.UNMANAGED
        assert resolved.metadata.backend == "sqlite"
        assert resolved.metadata.generation_id is None
        assert resolved.evidence_workspaces == {}
        assert int(resolved.backend.conn.execute("PRAGMA query_only").fetchone()[0]) == 0

    assert not (workspace / "change_control").exists()
    with pytest.raises(ChangeControlApplicationUsageError, match="initialized authority"):
        application.resolve_query_generation("active")
    assert not (workspace / "change_control").exists()


def test_unmanaged_postgres_auto_delegates_to_existing_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _manifest = _workspace(tmp_path)
    settings = _settings(workspace, backend="postgres")

    class ExistingPostgresBackend:
        name = "postgres"

        def close(self) -> None:
            pass

    backend = ExistingPostgresBackend()
    monkeypatch.setattr(application_module, "get_backend", lambda _settings: backend)
    monkeypatch.setattr(
        ChangeControlApplication,
        "_preflight_backend",
        lambda _application: pytest.fail("unmanaged auto must not run managed preflight"),
    )

    with ChangeControlApplication(settings).resolve_query_generation() as resolved:
        assert resolved.backend is backend
        assert resolved.metadata.backend == "postgres"
        assert resolved.metadata.generation_kind == QueryGenerationKind.UNMANAGED
    assert not (workspace / "change_control").exists()


def test_workspace_generation_zero_resolution_is_query_only_and_byte_preserving(
    tmp_path: Path,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    bootstrap_settings = _settings(workspace)
    bootstrap = ChangeControlApplication(bootstrap_settings).bootstrap(manifest, OPERATION_ID)
    state_db = workspace / "change_control" / "state.sqlite3"
    before = {
        path: (path.stat().st_ino, path.stat().st_size, path.read_bytes())
        for path in (state_db, workspace / "index.db", manifest)
    }
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )

    with ChangeControlApplication(settings).resolve_query_generation("auto") as resolved:
        inventory, readiness = bootstrap.bootstrap_state.require_complete()
        assert resolved.metadata.generation_kind == QueryGenerationKind.GENERATION_ZERO
        assert resolved.metadata.generation_id == bootstrap.authority.active_generation.generation_id
        assert resolved.metadata.active_generation_id == resolved.metadata.generation_id
        assert resolved.metadata.active_authority_revision == 0
        assert resolved.metadata.index_file_sha256 == readiness.index_file_sha256
        assert resolved.metadata.index_logical_fingerprint == (
            readiness.index_logical_fingerprint
        )
        assert inventory.aggregate_revision == 1
        assert int(resolved.backend.conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        assert resolved.backend.get_documents([]) == []
        with pytest.raises(sqlite3.OperationalError):
            resolved.backend.conn.execute("DELETE FROM documents")

    generation_id = bootstrap.authority.active_generation.generation_id
    for selector in ("legacy", "active", generation_id):
        with ChangeControlApplication(settings).resolve_query_generation(selector) as resolved:
            assert resolved.metadata.generation_id == generation_id
            assert resolved.metadata.is_active
            assert resolved.metadata.selection == QueryGenerationSelectionV1.parse(selector)

    with pytest.raises(ChangeControlApplicationUsageError, match="not available"):
        ChangeControlApplication(settings).resolve_query_generation(
            "mgeneration:" + "f" * 64
        )

    assert {
        path: (path.stat().st_ino, path.stat().st_size, path.read_bytes())
        for path in before
    } == before


def test_managed_query_backend_is_rejected_before_provider_or_repository_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    state = workspace / "change_control" / "state.sqlite3"
    state.parent.mkdir()
    state.write_bytes(b"not-opened")
    settings = Settings.model_validate(
        {
            "storage": {"backend": "postgres"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )

    def forbidden_provider(_settings: Settings) -> MockEmbedding:
        pytest.fail("managed PostgreSQL rejection must precede provider resolution")

    monkeypatch.setattr(application_module, "get_embedding_provider", forbidden_provider)
    with pytest.raises(ChangeControlApplicationUnsupportedOperationError):
        ChangeControlApplication(settings).resolve_query_generation()
    assert state.read_bytes() == b"not-opened"


def test_configured_managed_query_never_falls_back_when_state_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )

    def forbidden_backend(_settings: Settings) -> SqliteBackend:
        pytest.fail("configured managed resolution must not open an unmanaged backend")

    monkeypatch.setattr(application_module, "get_backend", forbidden_backend)
    with pytest.raises(ChangeControlApplicationIntegrityError, match="does not exist"):
        ChangeControlApplication(settings).resolve_query_generation()
    assert not (workspace / "change_control").exists()


def test_generation_zero_tamper_and_authority_race_fail_before_result_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)

    resolved = application.resolve_query_generation()
    resolved.__enter__()
    index_path = workspace / "index.db"
    index_path.write_bytes(index_path.read_bytes() + b"tampered")
    with pytest.raises(ChangeControlApplicationIntegrityError):
        resolved.close()

    # Use a fresh exact workspace to prove a post-resolution authority change
    # is checked again before any caller can release output.
    workspace2, manifest2 = _workspace(tmp_path / "race")
    ChangeControlApplication(_settings(workspace2)).bootstrap(manifest2, OPERATION_ID)
    settings2 = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace2},
            "query_generation": {"bootstrap_manifest": manifest2},
        }
    )
    original = SqliteManagedChangeControlStore.get_active_generation
    reads = 0

    def race_after_open(store: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        active = original(store, *args, **kwargs)
        return active if reads < 3 else None

    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_generation",
        race_after_open,
    )
    with (
        pytest.raises(ChangeControlApplicationConflictError, match="changed"),
        ChangeControlApplication(settings2).resolve_query_generation(),
    ):
        pass
    assert reads == 3


def test_query_close_reopens_a_fresh_read_only_authority_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    observed_stores: list[SqliteManagedChangeControlStore] = []
    original = SqliteManagedChangeControlStore.get_active_generation

    def observe(
        store: SqliteManagedChangeControlStore,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        observed_stores.append(store)
        return original(store, *args, **kwargs)

    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_generation",
        observe,
    )
    with ChangeControlApplication(settings).resolve_query_generation():
        pass

    assert len(observed_stores) == 3
    assert observed_stores[0] is not observed_stores[1]
    assert observed_stores[1] is not observed_stores[2]
    assert observed_stores[0] is not observed_stores[2]
    assert all(store._read_only for store in observed_stores)  # noqa: SLF001


def test_query_close_maps_original_authority_guard_drift_to_public_integrity(
    tmp_path: Path,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    resolved = ChangeControlApplication(settings).resolve_query_generation()
    resolved.__enter__()

    os.utime(workspace / "change_control" / "state.sqlite3")

    with pytest.raises(ChangeControlApplicationIntegrityError):
        resolved.close()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _settings(workspace: Path, *, backend: str = "sqlite") -> Settings:
    return Settings.model_validate(
        {
            "storage": {"backend": backend},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
        }
    )


def _write_manifest(
    workspace: Path,
    *,
    note: bytes,
    raw: bytes,
    index: bytes,
    aggregate_id: str = "workspace-bootstrap-test",
) -> Path:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "aggregate_id": aggregate_id,
        "legacy_index_file_sha256": _sha(index),
        "legacy_index_file_byte_count": len(index),
        "managed_source_notes": [
            {
                "logical_path": MANAGED,
                "source_note_sha256": _sha(note),
                "source_note_byte_count": len(note),
                "source_root_id": "workspace",
                "source_relative_path": RAW,
                "source_note_provenance": RAW,
                "raw_source_sha256": _sha(raw),
                "raw_source_byte_count": len(raw),
                "document_id": "returns-policy",
                "document_family": "returns-policy",
                "version_label": "v1",
                "declared_effective_from": "2026-03-01",
                "declared_effective_to": None,
                "role": "policy",
                "authority": "primary",
            }
        ],
    }
    path = workspace / "bootstrap.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    shutil.copytree(MINI_VAULT, workspace / "vault")
    raw = b"Exact governing returns source.\n"
    raw_path = workspace / RAW
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(raw)

    note_path = workspace / "vault" / MANAGED
    note_text = note_path.read_text(encoding="utf-8")
    provenance = f"provenance: {RAW}\nprovenance_hash: {content_hash(raw.decode('utf-8'))}\n"
    note_text = note_text.replace("key_claims:\n", f"{provenance}key_claims:\n", 1)
    note_path.write_text(note_text, encoding="utf-8")

    embedder = MockEmbedding()
    index_path = workspace / "index.db"
    backend = SqliteBackend(index_path)
    try:
        backend.init_schema(embedder.dimensions, embedder.model_version)
        report = sync_vault(workspace / "vault", backend, embedder, full=True)
        assert not report.skipped
    finally:
        backend.close()
    manifest = _write_manifest(
        workspace,
        note=note_path.read_bytes(),
        raw=raw,
        index=index_path.read_bytes(),
    )
    return workspace, manifest


def _externalize_source_root(
    workspace: Path,
    manifest: Path,
    tmp_path: Path,
) -> tuple[BootstrapSourceRoot, Path]:
    source_root = tmp_path / "operator-inputs"
    source_root.mkdir()
    external_source = source_root / "returns-source.md"
    internal_source = workspace / RAW
    internal_source.replace(external_source)
    note_path = workspace / "vault" / MANAGED
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(RAW, str(external_source)),
        encoding="utf-8",
    )
    embedder = MockEmbedding()
    backend = SqliteBackend(workspace / "index.db")
    try:
        report = sync_vault(workspace / "vault", backend, embedder, full=True)
        assert not report.skipped
    finally:
        backend.close()
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    entry = payload["managed_source_notes"][0]
    note_bytes = note_path.read_bytes()
    entry["source_note_sha256"] = _sha(note_bytes)
    entry["source_note_byte_count"] = len(note_bytes)
    entry["source_root_id"] = "operator-inputs"
    entry["source_relative_path"] = external_source.name
    entry["source_note_provenance"] = str(external_source)
    index_bytes = (workspace / "index.db").read_bytes()
    payload["legacy_index_file_sha256"] = _sha(index_bytes)
    payload["legacy_index_file_byte_count"] = len(index_bytes)
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return BootstrapSourceRoot(root_id="operator-inputs", path=source_root), external_source


def _immutable_snapshot(workspace: Path) -> dict[str, tuple[int, bytes]]:
    paths = sorted((workspace / "vault").rglob("*.md")) + [workspace / "index.db"]
    return {
        path.relative_to(workspace).as_posix(): (path.stat().st_ino, path.read_bytes())
        for path in paths
    }


def _complete_evidence_snapshot(workspace: Path, manifest: Path) -> dict[str, tuple[int, bytes]]:
    paths = sorted((workspace / "vault").rglob("*.md")) + [
        workspace / RAW,
        manifest,
        workspace / "index.db",
    ]
    return {
        path.relative_to(workspace).as_posix(): (path.stat().st_ino, path.read_bytes())
        for path in paths
    }


def _process_command(
    workspace: Path,
    manifest: Path,
    *,
    target: str = "",
    marker: Path | None = None,
) -> list[str]:
    return [
        sys.executable,
        "-c",
        PROCESS_WORKER,
        str(workspace),
        str(manifest),
        OPERATION_ID,
        target,
        str(marker) if marker is not None else "",
    ]


def _assert_exact_database_cardinality(workspace: Path) -> None:
    connection = sqlite3.connect(workspace / "change_control" / "state.sqlite3")
    try:
        expected = {
            "change_control_workspace_bootstrap_intents": 1,
            "change_control_workspace_inventories": 1,
            "change_control_workspace_inventory_receipts": 1,
            "change_control_legacy_index_readiness_receipts": 1,
            "change_control_active_generation": 1,
            "change_control_operator_runs": 1,
            "change_control_operator_run_links": 4,
        }
        assert {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in expected
        } == expected
    finally:
        connection.close()


def _assert_exact_navigation(result: Any) -> None:
    state = result.bootstrap_state
    inventory, readiness = state.require_complete()
    expected = (
        (
            OperatorRunLinkKind.BOOTSTRAP_INTENT,
            state.intent.bootstrap_id,
            state.intent.intent_sha256,
        ),
        (
            OperatorRunLinkKind.WORKSPACE_INVENTORY,
            inventory.receipt_id,
            inventory.receipt_sha256,
        ),
        (
            OperatorRunLinkKind.LEGACY_INDEX_READINESS,
            readiness.receipt_id,
            readiness.receipt_sha256,
        ),
        (
            OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
            result.authority.authority_id,
            result.authority.active_pointer_sha256,
        ),
    )
    assert (
        tuple(
            (
                item.command.kind,
                item.command.target_id,
                item.command.target_sha256,
            )
            for item in result.operator_run.links
        )
        == expected
    )


def test_every_durable_failpoint_replays_with_stable_identity_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = _settings(workspace)
    immutable_before = _immutable_snapshot(workspace)

    for target in ALL_DURABLE_STAGES:
        reached: list[str] = []

        def fail_once(
            stage: str,
            *,
            _reached: list[str] = reached,
            _target: str = target,
        ) -> None:
            _reached.append(stage)
            if stage == _target:
                raise RuntimeError(f"simulated crash after {_target}")

        with pytest.raises(ChangeControlApplicationIntegrityError) as captured:
            ChangeControlApplication(settings).bootstrap(
                manifest,
                OPERATION_ID,
                failure_hook=fail_once,
            )
        assert isinstance(captured.value.__cause__, RuntimeError)
        assert reached == list(ALL_DURABLE_STAGES[: ALL_DURABLE_STAGES.index(target) + 1])

    first = ChangeControlApplication(settings).bootstrap(manifest, OPERATION_ID)
    second = ChangeControlApplication(settings).bootstrap(manifest, OPERATION_ID)

    assert second == first
    assert first.bootstrap_state.inventory_receipt is not None
    assert first.bootstrap_state.index_readiness_receipt is not None
    assert first.authority.authority_revision == 0
    assert first.authority.active_generation.generation_number == 0
    _assert_exact_navigation(first)

    def forbidden_schema_initialization(_store: Any) -> None:
        pytest.fail("status lookup must never initialize or migrate the authority store")

    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "init_schema",
        forbidden_schema_initialization,
    )
    assert (
        ChangeControlApplication(settings).get_status(first.operator_run.record.command.run_id)
        == first.operator_run
    )
    assert _immutable_snapshot(workspace) == immutable_before


def test_fresh_process_crash_after_every_durable_stage_converges_exactly(
    tmp_path: Path,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    immutable_before = _complete_evidence_snapshot(workspace, manifest)

    for target in ALL_DURABLE_STAGES:
        crashed = subprocess.run(
            _process_command(workspace, manifest, target=target),
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert crashed.returncode == PROCESS_CRASH_EXIT, (
            target,
            crashed.stdout,
            crashed.stderr,
        )

    replays = [
        subprocess.run(
            _process_command(workspace, manifest),
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for _ in range(2)
    ]
    assert [item.returncode for item in replays] == [0, 0]
    payloads = [json.loads(item.stdout) for item in replays]
    assert payloads[0] == payloads[1]
    assert len(payloads[0]["links"]) == 4
    _assert_exact_database_cardinality(workspace)
    assert _complete_evidence_snapshot(workspace, manifest) == immutable_before


def test_two_fresh_processes_converge_to_one_authority(tmp_path: Path) -> None:
    workspace, manifest = _workspace(tmp_path)
    immutable_before = _complete_evidence_snapshot(workspace, manifest)
    marker = tmp_path / "start-two-processes"
    processes = [
        subprocess.Popen(
            _process_command(workspace, manifest, marker=marker),
            cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    time.sleep(0.05)
    marker.touch()
    completed = [process.communicate(timeout=60) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], completed
    payloads = [json.loads(stdout) for stdout, _stderr in completed]
    assert payloads[0] == payloads[1]
    _assert_exact_database_cardinality(workspace)
    assert _complete_evidence_snapshot(workspace, manifest) == immutable_before


def test_same_operation_with_different_manifest_is_a_conflict(tmp_path: Path) -> None:
    workspace, manifest = _workspace(tmp_path)
    application = ChangeControlApplication(_settings(workspace))
    first = application.bootstrap(manifest, OPERATION_ID)
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["aggregate_id"] = "different-workspace-aggregate"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ChangeControlApplicationConflictError):
        application.bootstrap(manifest, OPERATION_ID)

    assert application.get_status(first.operator_run.record.command.run_id) == (first.operator_run)


def test_same_inventory_with_different_operation_is_a_conflict(tmp_path: Path) -> None:
    workspace, manifest = _workspace(tmp_path)
    application = ChangeControlApplication(_settings(workspace))
    first = application.bootstrap(manifest, OPERATION_ID)

    with pytest.raises(ChangeControlApplicationConflictError, match="another operation_id"):
        application.bootstrap(manifest, f"{OPERATION_ID}:other")

    assert application.get_status(first.operator_run.record.command.run_id) == (first.operator_run)


def test_concurrent_exact_bootstraps_converge_to_one_authority(tmp_path: Path) -> None:
    workspace, manifest = _workspace(tmp_path)
    settings = _settings(workspace)

    def execute(_ordinal: int) -> Any:
        return ChangeControlApplication(settings).bootstrap(manifest, OPERATION_ID)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(execute, range(2)))

    assert results[0] == results[1]
    _assert_exact_navigation(results[0])
    connection = sqlite3.connect(workspace / "change_control" / "state.sqlite3")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM change_control_active_generation"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM change_control_operator_runs"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM change_control_operator_run_links"
        ).fetchone() == (4,)
    finally:
        connection.close()


def test_bootstrap_capability_fails_after_composite_evidence_guard_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    captured: list[Any] = []
    verifier = application_module.verify_workspace_bootstrap_evidence

    def capture_capability(**kwargs: Any) -> Any:
        capability = verifier(**kwargs)
        captured.append(capability)
        return capability

    monkeypatch.setattr(
        application_module,
        "verify_workspace_bootstrap_evidence",
        capture_capability,
    )
    ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)

    assert len(captured) == 1
    with pytest.raises(ValueError, match="cannot freshly verify"):
        captured[0].verify()


def test_bootstrap_verifier_rejects_index_guard_for_a_different_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    factory = application_module.create_workspace_bootstrap_evidence_verifier

    def corrupt_projection(workspace_guard: Any, index_guard: Any) -> Any:
        index_guard.attestation = replace(
            index_guard.attestation,
            projection_fingerprint="0" * 64,
        )
        return factory(workspace_guard, index_guard)

    monkeypatch.setattr(
        application_module,
        "create_workspace_bootstrap_evidence_verifier",
        corrupt_projection,
    )

    with pytest.raises(ChangeControlApplicationIntegrityError, match="exact projection"):
        ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)


@pytest.mark.parametrize(
    ("stage", "drift"),
    [
        (BOOTSTRAP_INTENT_CLAIMED, "source"),
        (AGGREGATE_CREATED, "manifest"),
        (WORKSPACE_INVENTORY_RECORDED, "index"),
        (AUTHORITY_HANDOFF_STARTED, "source"),
        (AUTHORITY_HANDOFF_STARTED, "manifest"),
        (AUTHORITY_HANDOFF_STARTED, "index"),
    ],
)
def test_evidence_drift_after_early_receipts_fails_before_authority(
    tmp_path: Path,
    stage: str,
    drift: str,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    changed = False

    def mutate(target: str) -> None:
        nonlocal changed
        if target != stage or changed:
            return
        changed = True
        if drift == "source":
            source = workspace / "vault" / MANAGED
            source.write_bytes(source.read_bytes() + b"\n")
        elif drift == "manifest":
            manifest.write_bytes(manifest.read_bytes() + b"\n")
        else:
            index = workspace / "index.db"
            index.write_bytes(index.read_bytes() + b"tampered")

    with pytest.raises(ChangeControlApplicationIntegrityError):
        ChangeControlApplication(_settings(workspace)).bootstrap(
            manifest,
            OPERATION_ID,
            failure_hook=mutate,
        )

    connection = sqlite3.connect(workspace / "change_control" / "state.sqlite3")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM change_control_active_generation"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_external_source_inode_substitution_during_handoff_fails_before_authority(
    tmp_path: Path,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    source_root, external_source = _externalize_source_root(workspace, manifest, tmp_path)
    changed = False

    def substitute(target: str) -> None:
        nonlocal changed
        if target != AUTHORITY_HANDOFF_STARTED or changed:
            return
        changed = True
        replacement = source_root.path / "replacement.md"
        replacement.write_bytes(external_source.read_bytes())
        replacement.replace(external_source)

    with pytest.raises(ChangeControlApplicationIntegrityError):
        ChangeControlApplication(_settings(workspace)).bootstrap(
            manifest,
            OPERATION_ID,
            source_roots=(source_root,),
            failure_hook=substitute,
        )

    connection = sqlite3.connect(workspace / "change_control" / "state.sqlite3")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM change_control_active_generation"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_backend_and_attestation_rejections_have_no_managed_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    immutable_before = _immutable_snapshot(workspace)
    change_control = workspace / "change_control"

    with pytest.raises(ChangeControlApplicationUnsupportedOperationError):
        ChangeControlApplication(_settings(workspace, backend="postgres")).bootstrap(
            manifest,
            OPERATION_ID,
        )
    assert not change_control.exists()

    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(ChangeControlApplicationUnsupportedOperationError):
        ChangeControlApplication(_settings(workspace, backend="auto")).bootstrap(
            manifest,
            OPERATION_ID,
        )
    assert not change_control.exists()
    monkeypatch.delenv("DATABASE_URL")

    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["legacy_index_file_sha256"] = "0" * 64
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ChangeControlApplicationIntegrityError):
        ChangeControlApplication(_settings(workspace)).bootstrap(manifest, OPERATION_ID)
    assert not change_control.exists()
    assert _immutable_snapshot(workspace) == immutable_before


def test_malformed_manifest_is_usage_and_status_never_creates_or_migrates(
    tmp_path: Path,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    manifest.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    application = ChangeControlApplication(_settings(workspace))

    with pytest.raises(ChangeControlApplicationUsageError):
        application.bootstrap(manifest, OPERATION_ID)
    assert not (workspace / "change_control").exists()

    valid_run = f"operatorrun:{'0' * 64}"
    with pytest.raises(ChangeControlApplicationIntegrityError):
        application.get_status(valid_run)
    assert not (workspace / "change_control").exists()

    with pytest.raises(ChangeControlApplicationUsageError):
        application.get_status("not-a-run")
    assert not (workspace / "change_control").exists()


@pytest.mark.parametrize(
    "table",
    [
        "change_control_workspace_bootstrap_intents",
        "change_control_workspace_inventory_receipts",
        "change_control_legacy_index_readiness_receipts",
        "change_control_active_generation",
    ],
)
def test_status_fails_closed_when_a_navigation_target_disappears(
    tmp_path: Path,
    table: str,
) -> None:
    workspace, manifest = _workspace(tmp_path)
    application = ChangeControlApplication(_settings(workspace))
    result = application.bootstrap(manifest, OPERATION_ID)
    database = workspace / "change_control" / "state.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ChangeControlApplicationIntegrityError):
        application.get_status(result.operator_run.record.command.run_id)
