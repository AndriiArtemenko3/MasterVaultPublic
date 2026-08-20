"""Focused protected-path preflight tests for managed generation effects."""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ManagedGenerationRepositoryError,
)


def test_absent_protected_leaf_is_retained_without_creation(tmp_path: Path) -> None:
    protected_parent = tmp_path / "private-state"
    protected_parent.mkdir()
    absent_checkpoint = protected_parent / "checkpoint.sqlite3"
    generation_root = tmp_path / "generation-effects"

    repository = ManagedGenerationRepository(
        generation_root,
        forbidden_roots=(absent_checkpoint,),
    )

    assert repository.root == generation_root.resolve(strict=True)
    assert not absent_checkpoint.exists()


@pytest.mark.parametrize("relationship", ("equal", "ancestor", "descendant"))
def test_absent_protected_leaf_overlap_is_rejected(tmp_path: Path, relationship: str) -> None:
    protected_parent = tmp_path / "private-state"
    protected_parent.mkdir()
    absent_checkpoint = protected_parent / "checkpoint.sqlite3"
    if relationship == "equal":
        generation_root = absent_checkpoint
    elif relationship == "ancestor":
        generation_root = protected_parent
    else:
        generation_root = absent_checkpoint / "generation-effects"

    with pytest.raises(ManagedGenerationRepositoryError):
        ManagedGenerationRepository(
            generation_root,
            forbidden_roots=(absent_checkpoint,),
        )

    assert not absent_checkpoint.exists()


def test_absent_protected_leaf_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-private-state"
    real_parent.mkdir()
    parent_alias = tmp_path / "private-state"
    parent_alias.symlink_to(real_parent, target_is_directory=True)
    absent_checkpoint = parent_alias / "checkpoint.sqlite3"
    generation_root = tmp_path / "generation-effects"

    with pytest.raises(ManagedGenerationRepositoryError, match="preflight"):
        ManagedGenerationRepository(
            generation_root,
            forbidden_roots=(absent_checkpoint,),
        )

    assert not generation_root.exists()
    assert not (real_parent / "checkpoint.sqlite3").exists()


def test_existing_protected_path_keeps_strict_resolution(tmp_path: Path) -> None:
    protected = tmp_path / "checkpoint.sqlite3"
    protected.write_bytes(b"existing protected authority")
    generation_root = tmp_path / "generation-effects"

    repository = ManagedGenerationRepository(
        generation_root,
        forbidden_roots=(protected,),
    )

    assert repository.root == generation_root.resolve(strict=True)
    assert protected.read_bytes() == b"existing protected authority"
