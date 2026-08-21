from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceIndexConflictError,
    LifecycleEvidenceIndexError,
    LifecycleEvidenceIndexV1,
    LifecycleEvidenceOwnerV1,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.models import canonical_json_bytes

RUN_ID = f"operatorrun:{'a' * 64}"
RECORDED_AT = "2026-08-20T12:00:00+00:00"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _index(*, digest: str = "b", recorded_at: str = RECORDED_AT) -> LifecycleEvidenceIndexV1:
    return LifecycleEvidenceIndexV1.create(
        run_id=RUN_ID,
        stage=LifecycleEvidenceStageV1.TEMPORAL,
        owners=(
            LifecycleEvidenceOwnerV1(
                owner_kind="temporal-analysis",
                owner_id=f"temporal-analysis:{digest * 64}",
                owner_sha256=digest * 64,
                relative_locator=f"temporal/evidence/analyses/{digest * 64}.json",
            ),
        ),
        recorded_at=recorded_at,
    )


def test_index_is_canonical_create_only_and_exact_retry_keeps_original_time(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    repository = FilesystemLifecycleEvidenceIndex(root)
    first = repository.persist(_index())
    retried = repository.persist(
        _index(recorded_at="2026-08-20T12:00:01+00:00")
    )

    assert retried == first
    assert retried.recorded_at == RECORDED_AT
    assert repository.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL) == first
    manifest = next((root / "application/lifecycle-index-v1").glob("*.json"))
    assert manifest.read_bytes() == canonical_json_bytes(first.model_dump(mode="json"))
    assert manifest.stat().st_mode & 0o077 == 0


def test_index_rejects_different_owner_for_same_run_stage(tmp_path: Path) -> None:
    repository = FilesystemLifecycleEvidenceIndex(_root(tmp_path))
    repository.persist(_index())

    with pytest.raises(LifecycleEvidenceIndexConflictError):
        repository.persist(_index(digest="c"))


def test_read_only_reopen_does_not_create_or_change_any_bytes_or_mtime(tmp_path: Path) -> None:
    root = _root(tmp_path)
    writer = FilesystemLifecycleEvidenceIndex(root)
    expected = writer.persist(_index())
    files_before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    directories_before = tuple(sorted(path.relative_to(root) for path in root.rglob("*") if path.is_dir()))

    reader = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)
    assert reader.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL) == expected

    files_after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    directories_after = tuple(sorted(path.relative_to(root) for path in root.rglob("*") if path.is_dir()))
    assert files_after == files_before
    assert directories_after == directories_before


def test_reopen_rejects_tampered_manifest(tmp_path: Path) -> None:
    root = _root(tmp_path)
    writer = FilesystemLifecycleEvidenceIndex(root)
    writer.persist(_index())
    manifest = next((root / "application/lifecycle-index-v1").glob("*.json"))
    payload = manifest.read_bytes()
    manifest.write_bytes(payload.replace(b"b" * 64, b"c" * 64, 1))

    reader = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)
    with pytest.raises(LifecycleEvidenceIndexError, match="corrupt"):
        reader.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL)


def test_models_reject_absolute_or_parent_locators() -> None:
    for locator in ("/private/source.json", "../source.json", "safe/../source.json"):
        with pytest.raises(ValueError):
            LifecycleEvidenceOwnerV1(
                owner_kind="incoming",
                owner_id=f"incomingreceipt:{'d' * 64}",
                owner_sha256="d" * 64,
                relative_locator=locator,
            )


def test_read_only_constructor_does_not_create_missing_index_tree(tmp_path: Path) -> None:
    root = _root(tmp_path)
    reader = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)

    with pytest.raises(LifecycleEvidenceIndexError, match="does not exist"):
        reader.reopen(RUN_ID, LifecycleEvidenceStageV1.BASELINE)
    assert tuple(root.iterdir()) == ()


def test_reopen_rejects_intermediate_symlink_and_inode_substitution(tmp_path: Path) -> None:
    root = _root(tmp_path)
    writer = FilesystemLifecycleEvidenceIndex(root)
    writer.persist(_index())
    reader = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)
    application = root / "application"
    index = application / "lifecycle-index-v1"
    saved = application / "saved-index"
    index.rename(saved)
    index.symlink_to(saved.name, target_is_directory=True)

    with pytest.raises(LifecycleEvidenceIndexError):
        reader.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL)

    index.unlink()
    index.mkdir(mode=0o700)
    os.chmod(index, 0o700)
    with pytest.raises(LifecycleEvidenceIndexError, match="inode was substituted"):
        reader.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL)


def test_shared_reader_never_observes_pending_partial_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    writer = FilesystemLifecycleEvidenceIndex(root)
    reader = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)
    original_write = os.write
    partial_written = threading.Event()
    allow_completion = threading.Event()
    intercepted = False

    def controlled_write(fd: int, payload: bytes) -> int:
        nonlocal intercepted
        if not intercepted and len(payload) > 32:
            intercepted = True
            count = original_write(fd, payload[: len(payload) // 2])
            partial_written.set()
            assert allow_completion.wait(timeout=5)
            return count
        return original_write(fd, payload)

    monkeypatch.setattr(os, "write", controlled_write)
    written: list[LifecycleEvidenceIndexV1] = []
    observed: list[LifecycleEvidenceIndexV1] = []
    writer_thread = threading.Thread(target=lambda: written.append(writer.persist(_index())))
    writer_thread.start()
    assert partial_written.wait(timeout=5)
    reader_thread = threading.Thread(
        target=lambda: observed.append(
            reader.reopen(RUN_ID, LifecycleEvidenceStageV1.TEMPORAL)
        )
    )
    reader_thread.start()
    allow_completion.set()
    writer_thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert written == observed == [_index()]
