from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.change_control import store as store_module
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunRecord,
    decode_operator_run_cursor,
    encode_operator_run_cursor,
)
from mastervault.change_control.regression_suite import RegressionSuiteV1
from mastervault.change_control.store import _DEFAULT_MIGRATIONS_DIR, SqliteChangeControlStore
from mastervault.change_control.synchronous_lifecycle_store_models import (
    IncomingAdmissionIntentV1,
    RegressionSuiteAdmissionIntentV1,
)


def _suite() -> RegressionSuiteV1:
    return RegressionSuiteV1.model_validate(
        {
            "schema_version": 1,
            "suite_id": "change-smoke",
            "suite_version": 1,
            "cases": (
                {
                    "case_id": "ask-control",
                    "role": "control",
                    "kind": "ask",
                    "query": "What remains unchanged?",
                    "max_rounds": 2,
                    "budget_usd_micros": 1000,
                },
                {
                    "case_id": "search-target",
                    "role": "targeted",
                    "kind": "search",
                    "query": "What changed?",
                    "k": 5,
                    "record_types": ("claim",),
                    "rerank": False,
                },
            ),
        },
        strict=True,
    )


def test_migration_006_creates_exact_tables_and_retains_link_values(tmp_path: Path) -> None:
    store = SqliteChangeControlStore(tmp_path / "authority.sqlite3")
    try:
        store.init_schema()
        assert store._read_meta()["schema_version"] == "6"  # type: ignore[index]
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        sql = str(
            store.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='change_control_operator_run_links'"
            ).fetchone()[0]
        )
        for kind in OperatorRunLinkKind:
            assert f"'{kind.value}'" in sql
        expected = {
            "change_control_incoming_admission_intents",
            "change_control_incoming_admission_receipts",
            "change_control_regression_suite_admission_intents",
            "change_control_regression_suite_admission_receipts",
            "change_control_generation_zero_baseline_receipts",
            "change_control_generation_zero_baseline_cases",
            "change_control_activation_baseline_bindings",
        }
        assert expected <= store._user_tables()
    finally:
        store.close()


def test_migration_006_upgrades_v5_without_foreign_key_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_v5 = tmp_path / "migrations-v5"
    migrations_v5.mkdir()
    for source in sorted(_DEFAULT_MIGRATIONS_DIR.glob("00[1-5]_*.sql")):
        shutil.copy(source, migrations_v5 / source.name)
    path = tmp_path / "upgrade.sqlite3"
    monkeypatch.setattr(store_module, "_SCHEMA_VERSION", 5)
    old = SqliteChangeControlStore(path, migrations_v5)
    old.init_schema()
    created_at = "2026-08-20T12:00:00+00:00"
    old.conn.execute(
        "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
        ("migration-populated", "0" * 64, created_at),
    )
    run_command = OperatorRunCommand.create(
        operation_id="migration:operator-run",
        aggregate_id="migration-populated",
        base_authority_id=f"mauthority:{'1' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="2" * 64,
    )
    run_record = OperatorRunRecord(command=run_command, created_at=created_at)
    old.conn.execute(
        "INSERT INTO change_control_operator_runs VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (
            run_command.run_id,
            run_command.run_sha256,
            run_command.operation_id,
            run_command.aggregate_id,
            run_command.base_authority_id,
            run_command.base_authority_revision,
            run_command.base_active_pointer_sha256,
            canonical_json_bytes(run_record.model_dump(mode="json")).decode(),
            created_at,
        ),
    )
    old_kinds = tuple(
        kind
        for kind in OperatorRunLinkKind
        if kind
        not in {
            OperatorRunLinkKind.REGRESSION_SUITE,
            OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
        }
    )
    for sequence, kind in enumerate(old_kinds):
        command = OperatorRunLinkCommand.create(
            operation_id=f"migration:link:{kind.value}",
            run_id=run_command.run_id,
            kind=kind,
            target_id=f"target:{kind.value}",
            target_sha256=f"{sequence + 3:064x}",
        )
        record = OperatorRunLinkRecord(command=command, sequence=sequence, recorded_at=created_at)
        old.conn.execute(
            "INSERT INTO change_control_operator_run_links VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                command.run_id,
                sequence,
                command.link_id,
                command.link_sha256,
                command.operation_id,
                command.kind.value,
                command.target_id,
                command.target_sha256,
                canonical_json_bytes(record.model_dump(mode="json")).decode(),
                created_at,
            ),
        )
    old.conn.commit()
    before_links = old.conn.execute(
        "SELECT * FROM change_control_operator_run_links ORDER BY sequence"
    ).fetchall()
    before_values = tuple(tuple(row) for row in before_links)
    old.close()
    monkeypatch.setattr(store_module, "_SCHEMA_VERSION", 6)
    upgraded = SqliteChangeControlStore(path)
    try:
        upgraded.init_schema()
        assert upgraded._read_meta()["schema_version"] == "6"  # type: ignore[index]
        assert upgraded.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        after_links = upgraded.conn.execute(
            "SELECT * FROM change_control_operator_run_links ORDER BY sequence"
        ).fetchall()
        assert tuple(tuple(row) for row in after_links) == before_values
        assert tuple(str(row["link_kind"]) for row in after_links) == tuple(
            kind.value for kind in old_kinds
        )
        assert [
            int(row[0])
            for row in upgraded.conn.execute(
                "SELECT version FROM change_control_schema_migrations ORDER BY version"
            )
        ] == [1, 2, 3, 4, 5, 6]
    finally:
        upgraded.close()


def test_suite_admission_intent_embeds_and_binds_canonical_suite() -> None:
    suite = _suite()
    intent = RegressionSuiteAdmissionIntentV1.create(
        operation_id="suite:admit",
        run_id=f"operatorrun:{'1' * 64}",
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        original_sha256="2" * 64,
        original_byte_count=123,
        canonical_sha256=suite.canonical_sha256,
        suite=suite,
    )
    assert intent.suite == suite
    payload = intent.model_dump(mode="json")
    payload["canonical_sha256"] = "3" * 64
    with pytest.raises(ValidationError):
        RegressionSuiteAdmissionIntentV1.model_validate(payload, strict=True)


def test_suite_logical_version_cannot_fork_to_different_content(tmp_path: Path) -> None:
    store = SqliteChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    created_at = "2026-08-20T12:00:00+00:00"
    for index in range(2):
        aggregate_id = f"suite-conflict-{index}"
        store.conn.execute(
            "INSERT INTO change_control_aggregates VALUES (?, 1, 1, ?, ?)",
            (aggregate_id, f"{index + 1:064x}", created_at),
        )
        command = OperatorRunCommand.create(
            operation_id=f"suite-conflict:run:{index}",
            aggregate_id=aggregate_id,
            base_authority_id=f"mauthority:{index + 3:064x}",
            base_authority_revision=0,
            base_active_pointer_sha256=f"{index + 5:064x}",
        )
        record = OperatorRunRecord(command=command, created_at=created_at)
        store.conn.execute(
            "INSERT INTO change_control_operator_runs VALUES (?, ?, ?, ?, ?, 0, ?, 1, ?, ?)",
            (
                command.run_id,
                command.run_sha256,
                command.operation_id,
                aggregate_id,
                command.base_authority_id,
                command.base_active_pointer_sha256,
                canonical_json_bytes(record.model_dump(mode="json")).decode(),
                created_at,
            ),
        )
        if index == 0:
            first_run_id = command.run_id
        else:
            second_run_id = command.run_id
    values = (
        "suiteintent:" + "a" * 64,
        "a" * 64,
        "suite-conflict:first",
        first_run_id,
        "logical-suite",
        7,
        "b" * 64,
        10,
        "c" * 64,
        1,
        "{}",
    )
    store.conn.execute(
        "INSERT INTO change_control_regression_suite_admission_intents VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO change_control_regression_suite_admission_intents VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "suiteintent:" + "d" * 64,
                "d" * 64,
                "suite-conflict:second",
                second_run_id,
                "logical-suite",
                7,
                "e" * 64,
                11,
                "f" * 64,
                1,
                "{}",
            ),
        )
    store.close()


def test_operator_cursor_is_canonical_and_exact() -> None:
    created_at = "2026-08-20T12:00:00+00:00"
    run_id = f"operatorrun:{'a' * 64}"
    cursor = encode_operator_run_cursor(created_at, run_id)
    assert decode_operator_run_cursor(cursor) == (created_at, run_id)
    with pytest.raises(ValueError):
        decode_operator_run_cursor(cursor + "=")


def test_incoming_write_cannot_admit_self_consistent_absent_repository_evidence(
    tmp_path: Path,
) -> None:
    store = SqliteManagedChangeControlStore(tmp_path / "authority.sqlite3")
    store.init_schema()
    intent = IncomingAdmissionIntentV1.create(
        operation_id="incoming:fabricated",
        run_id=f"operatorrun:{'1' * 64}",
        bundle_id=f"generic-bundle-v2:{'2' * 64}",
        bundle_sha256="2" * 64,
        admission_sha256="3" * 64,
        source_receipt_sha256="4" * 64,
        projection_sha256="5" * 64,
        inference_sha256="6" * 64,
    )

    class AbsentRepository:
        def resolve_incoming_source(self, value: IncomingAdmissionIntentV1) -> object:
            assert value == intent
            raise FileNotFoundError("generic bundle is absent")

    try:
        with pytest.raises(FileNotFoundError, match="absent"):
            store.record_incoming_admission(
                intent,
                resolver=AbsentRepository(),  # type: ignore[arg-type]
            )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM change_control_incoming_admission_intents"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()
