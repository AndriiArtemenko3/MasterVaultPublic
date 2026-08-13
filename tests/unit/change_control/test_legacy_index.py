"""Focused tests for exact, read-only legacy SQLite index attestation."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from mastervault.change_control.legacy_index import (
    LegacyIndexAttestation,
    LegacyIndexIntegrityError,
    LegacyIndexPlatformUnsupportedError,
    attest_legacy_sqlite_index,
    open_legacy_sqlite_index_attestation_guard,
)
from mastervault.providers import MockEmbedding
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync import ExactVaultNoteInput
from mastervault.sync.indexer import sync_vault

DIMENSIONS = 8
MODEL = "mock-hashing-trick-v1"
MINI_VAULT = Path(__file__).parents[2] / "fixtures" / "mini_vault"


def _notes(vault: Path = MINI_VAULT) -> tuple[ExactVaultNoteInput, ...]:
    return tuple(
        ExactVaultNoteInput(
            rel_path=path.relative_to(vault).as_posix(),
            content=path.read_bytes(),
            workspace=vault.parent,
        )
        for path in sorted(vault.rglob("*.md"))
    )


def _build_index(tmp_path: Path) -> Path:
    path = tmp_path / "legacy-index.sqlite3"
    backend = SqliteBackend(path)
    try:
        backend.init_schema(DIMENSIONS, MODEL)
        sync_vault(MINI_VAULT, backend, MockEmbedding(DIMENSIONS), full=True)
    finally:
        backend.close()
    return path


def _attest(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
) -> LegacyIndexAttestation:
    content = path.read_bytes()
    return attest_legacy_sqlite_index(
        index_path=path,
        notes=_notes(),
        embedding_model_version=MODEL,
        embedding_dimensions=DIMENSIONS,
        expected_index_file_sha256=(
            hashlib.sha256(content).hexdigest()
            if expected_sha256 is None
            else expected_sha256
        ),
        expected_index_file_byte_count=(
            len(content) if expected_byte_count is None else expected_byte_count
        ),
    )


def test_ordinary_sqlite_index_attests_twice_without_mutation(tmp_path: Path) -> None:
    path = _build_index(tmp_path)
    path.chmod(0o644)
    before = (
        path.stat().st_ino,
        path.stat().st_mode,
        path.read_bytes(),
        tuple(sorted(item.name for item in path.parent.iterdir())),
    )

    first = _attest(path)
    second = _attest(path)

    assert first == second
    assert first.storage_schema_version == 3
    assert first.embedding_model_version == MODEL
    assert first.embedding_dimensions == DIMENSIONS
    assert first.index_file_sha256
    assert first.logical_index_fingerprint
    assert dict(first.counts)["documents"] == len(_notes())
    assert (
        path.stat().st_ino,
        path.stat().st_mode,
        path.read_bytes(),
        tuple(sorted(item.name for item in path.parent.iterdir())),
    ) == before


def test_logical_fingerprint_excludes_only_volatile_timestamps(tmp_path: Path) -> None:
    path = _build_index(tmp_path)
    before = _attest(path)
    backend = SqliteBackend(path)
    try:
        with backend.conn:
            backend.conn.execute("UPDATE documents SET indexed_at = '2099-01-01T00:00:00+00:00'")
            backend.conn.execute("UPDATE embeddings SET updated_at = '2099-01-01T00:00:00+00:00'")
            backend.conn.execute(
                "UPDATE schema_migrations SET applied_at = '2099-01-01T00:00:00+00:00'"
            )
    finally:
        backend.close()

    after = _attest(path)

    assert after.index_file_sha256 != before.index_file_sha256
    assert after.logical_index_fingerprint == before.logical_index_fingerprint
    assert after.projection_fingerprint == before.projection_fingerprint


def test_model_or_dimensions_mismatch_fails_with_stable_error(tmp_path: Path) -> None:
    path = _build_index(tmp_path)

    with pytest.raises(LegacyIndexIntegrityError, match="metadata is not exact"):
        attest_legacy_sqlite_index(
            index_path=path,
            notes=_notes(),
            embedding_model_version="other-model",
            embedding_dimensions=DIMENSIONS,
            expected_index_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_index_file_byte_count=path.stat().st_size,
        )
    with pytest.raises(LegacyIndexIntegrityError, match="metadata is not exact"):
        attest_legacy_sqlite_index(
            index_path=path,
            notes=_notes(),
            embedding_model_version=MODEL,
            embedding_dimensions=DIMENSIONS + 1,
            expected_index_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_index_file_byte_count=path.stat().st_size,
        )


def test_declared_exact_file_identity_must_match_pinned_index(tmp_path: Path) -> None:
    path = _build_index(tmp_path)

    with pytest.raises(LegacyIndexIntegrityError, match="declared exact file identity"):
        _attest(path, expected_sha256="0" * 64)
    with pytest.raises(LegacyIndexIntegrityError, match="declared exact file identity"):
        _attest(path, expected_byte_count=path.stat().st_size + 1)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE documents SET title = title || ' stale' WHERE rowid = 1", "documents"),
        ("DELETE FROM claims_fts WHERE rowid = 1", "claims_fts"),
        (
            "DELETE FROM vec_records WHERE record_id = "
            "(SELECT record_id FROM vec_records LIMIT 1)",
            "coverage",
        ),
    ],
)
def test_semantic_fts_and_vector_tampering_fail_closed(
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    path = _build_index(tmp_path)
    backend = SqliteBackend(path)
    try:
        with backend.conn:
            backend.conn.execute(statement)
    finally:
        backend.close()

    with pytest.raises(LegacyIndexIntegrityError, match=message):
        _attest(path)


def test_migration_ledger_or_extra_schema_fails_closed(tmp_path: Path) -> None:
    path = _build_index(tmp_path)
    backend = SqliteBackend(path)
    try:
        with backend.conn:
            backend.conn.execute(
                "UPDATE schema_migrations SET checksum_sha256 = ? WHERE version = 1",
                ("0" * 64,),
            )
    finally:
        backend.close()

    with pytest.raises(LegacyIndexIntegrityError, match="migration ledger differs"):
        _attest(path)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_transaction_sidecars_fail_closed(tmp_path: Path, suffix: str) -> None:
    path = _build_index(tmp_path)
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_bytes(b"residue")

    with pytest.raises(LegacyIndexIntegrityError, match="sidecars"):
        _attest(path)


def test_symlink_and_hardlink_fail_closed(tmp_path: Path) -> None:
    path = _build_index(tmp_path)
    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(path.name)

    with pytest.raises(LegacyIndexIntegrityError):
        _attest(symlink)

    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(path, hardlink)
    with pytest.raises(LegacyIndexIntegrityError, match="private regular pinned inode"):
        _attest(path)


def test_group_writable_index_fails_but_group_readable_index_is_valid(tmp_path: Path) -> None:
    path = _build_index(tmp_path)
    path.chmod(0o644)
    _attest(path)

    path.chmod(0o664)
    with pytest.raises(LegacyIndexIntegrityError, match="private regular pinned inode"):
        _attest(path)


def test_live_guard_rejects_lexical_parent_substitution(tmp_path: Path) -> None:
    parent = tmp_path / "workspace"
    parent.mkdir()
    path = _build_index(parent)
    content = path.read_bytes()
    guard = open_legacy_sqlite_index_attestation_guard(
        index_path=path,
        notes=_notes(),
        embedding_model_version=MODEL,
        embedding_dimensions=DIMENSIONS,
        expected_index_file_sha256=hashlib.sha256(content).hexdigest(),
        expected_index_file_byte_count=len(content),
    )

    pinned_parent = tmp_path / "pinned-workspace"
    parent.rename(pinned_parent)
    parent.mkdir()
    shutil.copyfile(pinned_parent / path.name, parent / path.name)
    (parent / path.name).chmod(0o600)

    with pytest.raises(LegacyIndexIntegrityError, match="parent path was substituted"):
        guard.verify()
    guard.close()


def test_invalid_exact_inventory_is_wrapped_in_stable_error(tmp_path: Path) -> None:
    path = _build_index(tmp_path)

    with pytest.raises(LegacyIndexIntegrityError, match="attestation failed"):
        attest_legacy_sqlite_index(
            index_path=path,
            notes=(),
            embedding_model_version=MODEL,
            embedding_dimensions=DIMENSIONS,
            expected_index_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_index_file_byte_count=path.stat().st_size,
        )


def test_platform_without_descriptor_contract_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _build_index(tmp_path)
    monkeypatch.setattr(os, "supports_dir_fd", set())

    with pytest.raises(
        LegacyIndexPlatformUnsupportedError,
        match="descriptor-relative",
    ):
        _attest(path)


def test_module_has_no_private_sibling_imports() -> None:
    module_path = (
        Path(__file__).parents[3]
        / "src"
        / "mastervault"
        / "change_control"
        / "legacy_index.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert all(not alias.name.startswith("_") for alias in node.names)
