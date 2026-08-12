"""Descriptor-pinned opening tests for the application authority database."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import mastervault.change_control.store as store_module
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.store import (
    ChangeControlBusyError,
    ChangeControlCorruptionError,
    ChangeControlStoreError,
)


def _initialize(path: Path) -> None:
    store = SqliteManagedChangeControlStore(path, secure_open=True)
    try:
        store.init_schema()
    finally:
        store.close()


def _stable_stat(path: Path) -> tuple[int, int, int, int, int, int, int, int]:
    info = path.stat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def test_secure_writable_open_creates_and_reopens_one_private_inode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace" / "change_control" / "state.sqlite3"
    (tmp_path / "workspace").mkdir(mode=0o700)

    _initialize(path)

    assert path.is_file()
    assert path.stat().st_nlink == 1
    assert path.stat().st_uid == os.getuid()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    reopened = SqliteManagedChangeControlStore(path, secure_open=True)
    try:
        reopened.init_schema()
        assert int(reopened.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        assert str(reopened.conn.execute("PRAGMA journal_mode").fetchone()[0]) == "delete"
    finally:
        reopened.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_secure_store_requires_exact_private_modes_in_both_modes(
    tmp_path: Path,
    read_only: bool,
) -> None:
    path = tmp_path / "change_control" / "state.sqlite3"
    _initialize(path)

    store = SqliteManagedChangeControlStore(
        path,
        secure_open=True,
        read_only=read_only,
    )
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    finally:
        store.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_secure_store_rejects_persistent_wal_without_converting_it(
    tmp_path: Path,
    read_only: bool,
) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    connection = sqlite3.connect(path)
    try:
        assert str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]) == "wal"
    finally:
        connection.close()
    assert path.read_bytes()[18:20] == b"\x02\x02"
    before = path.read_bytes()

    with pytest.raises(ChangeControlCorruptionError, match="rollback-journal"):
        SqliteManagedChangeControlStore(
            path,
            secure_open=True,
            read_only=read_only,
        )

    assert path.read_bytes() == before
    assert path.read_bytes()[18:20] == b"\x02\x02"


def test_secure_schema_initialization_converges_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "workspace" / "change_control" / "state.sqlite3"
    (tmp_path / "workspace").mkdir(mode=0o700)

    script = (
        "from pathlib import Path; "
        "from mastervault.change_control.managed_store import "
        "SqliteManagedChangeControlStore; "
        "store = SqliteManagedChangeControlStore(Path(__import__('sys').argv[1]), "
        "secure_open=True); store.init_schema(); store.close()"
    )
    processes = tuple(
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", script, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    )
    results = tuple(process.communicate(timeout=30) for process in processes)
    assert [
        (process.returncode, stdout, stderr)
        for process, (stdout, stderr) in zip(
            processes,
            results,
            strict=True,
        )
    ] == [(0, "", ""), (0, "", "")]

    reopened = SqliteManagedChangeControlStore(path, secure_open=True, read_only=True)
    try:
        assert reopened.get_operator_run(f"operatorrun:{'0' * 64}") is None
    finally:
        reopened.close()


def test_schema_busy_restores_foreign_keys_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "change_control" / "state.sqlite3"
    legacy_migrations = tmp_path / "migrations-v4"
    legacy_migrations.mkdir(mode=0o700)
    for source in sorted(store_module._DEFAULT_MIGRATIONS_DIR.glob("00[1-4]_*.sql")):
        shutil.copyfile(source, legacy_migrations / source.name)

    with monkeypatch.context() as context:
        context.setattr(store_module, "_SCHEMA_VERSION", 4)
        legacy = SqliteManagedChangeControlStore(
            path,
            migrations_dir=legacy_migrations,
            secure_open=True,
        )
        try:
            legacy.init_schema()
        finally:
            legacy.close()

    contender = SqliteManagedChangeControlStore(
        path,
        timeout_seconds=0.01,
        secure_open=True,
    )
    owner = sqlite3.connect(path)
    owner.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ChangeControlBusyError):
            contender.init_schema()
        assert not contender.conn.in_transaction
        assert int(contender.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    finally:
        owner.execute("ROLLBACK")
        owner.close()

    try:
        contender.init_schema()
        assert int(contender.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    finally:
        contender.close()


def test_secure_writer_rechecks_pinned_header_immediately_after_begin(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    store = SqliteManagedChangeControlStore(path, secure_open=True)
    changed = False

    def replace_header_mode(statement: str) -> None:
        nonlocal changed
        if statement != "BEGIN IMMEDIATE" or changed:
            return
        changed = True
        file_fd = os.open(path, os.O_WRONLY)
        try:
            assert os.pwrite(file_fd, b"\x02\x02", 18) == 2
            os.fsync(file_fd)
        finally:
            os.close(file_fd)

    store.conn.set_trace_callback(replace_header_mode)
    with pytest.raises(ChangeControlCorruptionError, match="rollback-journal"):
        store.init_schema()
    assert changed
    assert not store.conn.in_transaction

    store.conn.set_trace_callback(None)
    file_fd = os.open(path, os.O_WRONLY)
    try:
        assert os.pwrite(file_fd, b"\x01\x01", 18) == 2
        os.fsync(file_fd)
    finally:
        os.close(file_fd)
    try:
        store.init_schema()
    finally:
        store.close()


def test_secure_read_only_open_is_query_only_and_byte_preserving(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    before_bytes = path.read_bytes()
    before_stat = _stable_stat(path)
    before_names = tuple(sorted(item.name for item in tmp_path.iterdir()))

    store = SqliteManagedChangeControlStore(
        path,
        secure_open=True,
        read_only=True,
    )
    try:
        assert int(store.conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert store.get_operator_run(f"operatorrun:{'0' * 64}") is None
        with pytest.raises(ChangeControlStoreError, match="cannot initialize or migrate"):
            store.init_schema()
        with pytest.raises(sqlite3.OperationalError):
            store.conn.execute("CREATE TABLE forbidden(value TEXT)")
    finally:
        store.close()

    assert path.read_bytes() == before_bytes
    assert _stable_stat(path) == before_stat
    assert tuple(sorted(item.name for item in tmp_path.iterdir())) == before_names


def test_secure_read_only_open_never_creates_missing_paths(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "change_control" / "state.sqlite3"

    with pytest.raises(ChangeControlCorruptionError, match="directory does not exist"):
        SqliteManagedChangeControlStore(
            path,
            secure_open=True,
            read_only=True,
        )

    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "writable"])
def test_secure_open_rejects_unsafe_state_file(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    target = tmp_path / "target.sqlite3"
    _initialize(target)
    path = tmp_path / "state.sqlite3"
    if unsafe_kind == "symlink":
        path.symlink_to(target.name)
    elif unsafe_kind == "hardlink":
        os.link(target, path)
    else:
        target.rename(path)
        path.chmod(0o666)

    with pytest.raises(ChangeControlCorruptionError):
        SqliteManagedChangeControlStore(path, secure_open=True)


def test_secure_writable_open_detects_substitution_before_sqlite_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    original_connect = store_module.sqlite3.connect
    replaced = False

    def substitute_then_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        nonlocal replaced
        if not replaced and args and str(args[0]) == str(path):
            replaced = True
            pinned = tmp_path / "pinned.sqlite3"
            path.rename(pinned)
            shutil.copyfile(pinned, path)
            path.chmod(0o600)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", substitute_then_connect)

    with pytest.raises(ChangeControlCorruptionError, match="pinned inode"):
        SqliteManagedChangeControlStore(path, secure_open=True)


def test_secure_read_only_store_fails_closed_if_path_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    store = SqliteManagedChangeControlStore(
        path,
        secure_open=True,
        read_only=True,
    )
    pinned = tmp_path / "pinned.sqlite3"
    path.rename(pinned)
    shutil.copyfile(pinned, path)
    path.chmod(0o600)

    with pytest.raises(ChangeControlCorruptionError, match="pinned inode"):
        store.get_operator_run(f"operatorrun:{'0' * 64}")
    with pytest.raises(ChangeControlCorruptionError, match="pinned inode"):
        store.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_secure_store_fails_closed_if_parent_path_is_replaced(
    tmp_path: Path,
    read_only: bool,
) -> None:
    authority_dir = tmp_path / "change_control"
    authority_dir.mkdir(mode=0o700)
    path = authority_dir / "state.sqlite3"
    _initialize(path)
    store = SqliteManagedChangeControlStore(
        path,
        secure_open=True,
        read_only=read_only,
    )

    pinned_dir = tmp_path / "pinned-change-control"
    authority_dir.rename(pinned_dir)
    authority_dir.mkdir(mode=0o700)
    shutil.copyfile(pinned_dir / path.name, authority_dir / path.name)
    (authority_dir / path.name).chmod(0o600)

    with pytest.raises(ChangeControlCorruptionError, match="parent path was substituted"):
        store.get_operator_run(f"operatorrun:{'0' * 64}")
    with pytest.raises(ChangeControlCorruptionError, match="parent path was substituted"):
        store.close()


def test_secure_read_only_store_rejects_transaction_sidecars(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _initialize(path)
    sidecar = path.with_name(f"{path.name}-journal")
    sidecar.write_bytes(b"not authoritative")

    with pytest.raises(ChangeControlCorruptionError, match="transaction sidecars"):
        SqliteManagedChangeControlStore(
            path,
            secure_open=True,
            read_only=True,
        )
