from __future__ import annotations

import os
import pickle
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from mastervault.change_control._repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    read_regular_file,
    require_exact_repository_path,
)
from mastervault.change_control.bootstrap import (
    AnalysisBootstrapIntegrityError,
    AnalysisBootstrapResult,
    bootstrap_analysis_aggregate,
    verify_analysis_bootstrap_snapshot,
)
from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.seed import (
    SeedBoundaryError,
    load_verified_prechange_seed_manifest_from_repository,
)
from mastervault.change_control.source_note_inventory import (
    PRECHANGE_MANIFEST_RELATIVE_PATH,
    RepositorySourceNoteInventoryResolver,
    RepositoryVerifiedSourceNoteInventoryCapability,
    SourceNoteInventoryResolutionError,
    _require_exact_inventory_coverage,
)
from mastervault.change_control.store import ChangeControlSnapshot, SqliteChangeControlStore

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / PRECHANGE_MANIFEST_RELATIVE_PATH
INCOMING_RELATIVE = "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"
INCOMING_MANIFEST = REPO_ROOT / INCOMING_RELATIVE


@pytest.fixture(scope="module")
def bootstrap(tmp_path_factory: pytest.TempPathFactory) -> AnalysisBootstrapResult:
    database = tmp_path_factory.mktemp("source-note-bootstrap") / "state.sqlite3"
    store = SqliteChangeControlStore(database)
    store.init_schema()
    try:
        return bootstrap_analysis_aggregate(
            repo_root=REPO_ROOT,
            prechange_manifest_path=PRECHANGE_MANIFEST,
            incoming_manifest_path=INCOMING_MANIFEST,
            store=store,
            prechange_operation_id="source-note-test:prechange",
            analysis_operation_id="source-note-test:analysis",
        )
    finally:
        store.close()


def _resolver(
    root: Path, bootstrap: AnalysisBootstrapResult
) -> RepositorySourceNoteInventoryResolver:
    return RepositorySourceNoteInventoryResolver(
        repo_root=root,
        prechange_manifest_path=root / PRECHANGE_MANIFEST_RELATIVE_PATH,
        incoming_manifest_path=root / INCOMING_RELATIVE,
        verified_bootstrap=bootstrap.verification_capability,
    )


def _copy_runtime_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT / "datasets", root / "datasets")
    return root


def test_real_sl2_resolver_returns_exact_eight_note_inventory(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    capability = _resolver(REPO_ROOT, bootstrap).resolve_source_note_inventory(
        snapshot=bootstrap.snapshot
    )
    inventory = capability.verify(snapshot=bootstrap.snapshot)

    assert isinstance(capability, RepositoryVerifiedSourceNoteInventoryCapability)
    assert len(inventory.notes) == 8
    assert {item.document.document_version_id for item in inventory.notes} == {
        item.document_version_id for item in bootstrap.snapshot.aggregate.documents.documents
    }
    showroom = next(
        item
        for item in inventory.notes
        if item.document.document_id == "process-showroom-demo-unit-rotation"
    )
    body = showroom.source_note_utf8[showroom.body_start_char :]
    assert "Open-box sales carry the standard 30-day refund window" in body
    assert not any(
        claim.document.document_version_id == showroom.document.document_version_id
        and "Open-box sales carry" in claim.statement
        for claim in bootstrap.snapshot.aggregate.claims.revisions
    )


def test_bootstrap_capability_seal_and_exact_snapshot_are_required(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    assert (
        verify_analysis_bootstrap_snapshot(
            bootstrap.verification_capability,
            bootstrap.snapshot,
        )
        == bootstrap.binding
    )
    stale = replace(bootstrap.snapshot, revision=3)
    with pytest.raises(AnalysisBootstrapIntegrityError, match="does not match"):
        verify_analysis_bootstrap_snapshot(bootstrap.verification_capability, stale)

    original_seal = bootstrap.verification_capability._seal
    object.__setattr__(bootstrap.verification_capability, "_seal", "0" * 64)
    try:
        with pytest.raises(AnalysisBootstrapIntegrityError, match="seal"):
            verify_analysis_bootstrap_snapshot(
                bootstrap.verification_capability,
                bootstrap.snapshot,
            )
    finally:
        object.__setattr__(bootstrap.verification_capability, "_seal", original_seal)


def test_capability_is_nonserializable_and_detects_tampering(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    capability = _resolver(REPO_ROOT, bootstrap).resolve_source_note_inventory(
        snapshot=bootstrap.snapshot
    )
    with pytest.raises(TypeError, match="process-local"):
        pickle.dumps(capability)
    original_seal = capability._seal
    object.__setattr__(capability, "_seal", "0" * 64)
    try:
        with pytest.raises(SourceNoteInventoryResolutionError, match="seal"):
            capability.verify(snapshot=bootstrap.snapshot)
    finally:
        object.__setattr__(capability, "_seal", original_seal)

    original_inventory = capability._inventory
    object.__setattr__(
        capability,
        "_inventory",
        SourceNoteInventory.create(
            snapshot=bootstrap.snapshot,
            notes=original_inventory.notes[:-1],
        ),
    )
    try:
        with pytest.raises(SourceNoteInventoryResolutionError, match="seal"):
            capability.verify(snapshot=bootstrap.snapshot)
    finally:
        object.__setattr__(capability, "_inventory", original_inventory)


def test_verify_is_io_free_but_new_resolution_reopens_repository(
    tmp_path: Path,
    bootstrap: AnalysisBootstrapResult,
) -> None:
    root = _copy_runtime_repository(tmp_path)
    capability = _resolver(root, bootstrap).resolve_source_note_inventory(
        snapshot=bootstrap.snapshot
    )
    inventory = capability.verify(snapshot=bootstrap.snapshot)
    showroom = next(
        item
        for item in inventory.notes
        if item.document.document_id == "process-showroom-demo-unit-rotation"
    )
    (root / "datasets/larkstead/processed" / showroom.source_note_path).unlink()

    assert capability.verify(snapshot=bootstrap.snapshot) == inventory
    with pytest.raises(SourceNoteInventoryResolutionError, match="failed verification"):
        _resolver(root, bootstrap).resolve_source_note_inventory(snapshot=bootstrap.snapshot)


def test_resolver_rejects_changed_hash_and_symlink(
    tmp_path: Path,
    bootstrap: AnalysisBootstrapResult,
) -> None:
    changed_root = _copy_runtime_repository(tmp_path / "changed")
    target = (
        changed_root
        / "datasets/larkstead/processed/customer-support/sources/faq-sl2-faq-returns.md"
    )
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SourceNoteInventoryResolutionError, match="failed verification"):
        _resolver(changed_root, bootstrap).resolve_source_note_inventory(
            snapshot=bootstrap.snapshot
        )

    symlink_root = _copy_runtime_repository(tmp_path / "symlink")
    target = (
        symlink_root
        / "datasets/larkstead/processed/customer-support/sources/faq-sl2-faq-returns.md"
    )
    replacement = target.with_name("faq-copy.md")
    target.rename(replacement)
    try:
        target.symlink_to(replacement.name)
    except OSError:
        pytest.skip("platform does not permit symlink creation")
    with pytest.raises(SourceNoteInventoryResolutionError, match="failed verification"):
        _resolver(symlink_root, bootstrap).resolve_source_note_inventory(
            snapshot=bootstrap.snapshot
        )


def test_coverage_rejects_missing_extra_note_and_claim_bindings(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    inventory = (
        _resolver(REPO_ROOT, bootstrap)
        .resolve_source_note_inventory(snapshot=bootstrap.snapshot)
        .verify(snapshot=bootstrap.snapshot)
    )
    claims = {
        note.document.document_version_id: tuple(
            sorted(
                claim.source.source_claim_id
                for claim in bootstrap.snapshot.aggregate.claims.revisions
                if claim.document.document_version_id == note.document.document_version_id
            )
        )
        for note in inventory.notes
    }
    with pytest.raises(SourceNoteInventoryResolutionError, match="documents"):
        _require_exact_inventory_coverage(
            snapshot=bootstrap.snapshot,
            notes=inventory.notes[:-1],
            source_claim_ids=claims,
        )
    with pytest.raises(SourceNoteInventoryResolutionError, match="documents"):
        _require_exact_inventory_coverage(
            snapshot=bootstrap.snapshot,
            notes=(*inventory.notes, inventory.notes[0]),
            source_claim_ids=claims,
        )

    first = inventory.notes[0]
    mismatched = CanonicalSourceNoteSnapshot.create(
        document=first.document,
        source_note_path=f"mismatch/{first.source_note_path}",
        source_note_utf8=first.source_note_utf8,
        body_start_char=first.body_start_char,
    )
    with pytest.raises(SourceNoteInventoryResolutionError, match="path/SHA"):
        _require_exact_inventory_coverage(
            snapshot=bootstrap.snapshot,
            notes=(mismatched, *inventory.notes[1:]),
            source_claim_ids=claims,
        )

    missing_claim = dict(claims)
    missing_claim[first.document.document_version_id] = claims[first.document.document_version_id][
        1:
    ]
    with pytest.raises(SourceNoteInventoryResolutionError, match="source_claim_id"):
        _require_exact_inventory_coverage(
            snapshot=bootstrap.snapshot,
            notes=inventory.notes,
            source_claim_ids=missing_claim,
        )


@pytest.mark.parametrize(
    "path",
    (
        "datasets/.hidden/file.md",
        "datasets/white space/file.md",
        "datasets/control\u200b/file.md",
        "datasets/golden/file.md",
        "datasets/../file.md",
    ),
)
def test_repository_path_grammar_rejects_unsafe_components(path: str) -> None:
    with pytest.raises(RepositoryFileBoundaryError):
        canonical_repo_relative(path)


def test_repository_resolution_rejects_case_alias(tmp_path: Path) -> None:
    (tmp_path / "datasets").mkdir()
    with pytest.raises(RepositoryFileBoundaryError, match="exact repository case"):
        require_exact_repository_path(
            repo_root=tmp_path,
            relative="Datasets",
            label="case alias",
        )


def test_public_seed_loader_rejects_golden_repository_before_file_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "golden" / "repository"
    root.mkdir(parents=True)
    missing_manifest = root / PRECHANGE_MANIFEST_RELATIVE_PATH
    with pytest.raises(SeedBoundaryError, match="golden"):
        load_verified_prechange_seed_manifest_from_repository(
            repo_root=root,
            manifest_path=missing_manifest,
        )


def test_resolver_rejects_golden_repository_before_file_read(
    tmp_path: Path,
    bootstrap: AnalysisBootstrapResult,
) -> None:
    root = tmp_path / "golden" / "repository"
    root.mkdir(parents=True)
    with pytest.raises(SourceNoteInventoryResolutionError, match="authenticate") as captured:
        _resolver(root, bootstrap).resolve_source_note_inventory(snapshot=bootstrap.snapshot)
    assert captured.value.__cause__ is not None
    assert "golden" in str(captured.value.__cause__)


def test_nonblocking_descriptor_check_rejects_fifo_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fifo = tmp_path / "runtime.fifo"
    regular = tmp_path / "regular.md"
    regular.write_bytes(b"safe")
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pytest.skip("platform does not permit FIFO creation")

    real_lstat = Path.lstat
    regular_info = regular.lstat()

    def stale_regular_lstat(path: Path):
        if path == fifo:
            return regular_info
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", stale_regular_lstat)
    with pytest.raises(RepositoryFileIntegrityError, match="non-regular"):
        read_regular_file(fifo, limit=1024, label="FIFO replacement")


def test_counterfeit_capability_constructor_is_rejected(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    inventory = (
        _resolver(REPO_ROOT, bootstrap)
        .resolve_source_note_inventory(snapshot=bootstrap.snapshot)
        .verify(snapshot=bootstrap.snapshot)
    )
    with pytest.raises(TypeError, match="service-created"):
        RepositoryVerifiedSourceNoteInventoryCapability(
            _inventory=inventory,
            _verified_bootstrap=bootstrap.verification_capability,
            _token=object(),
            _seal="0" * 64,
        )


def test_wrong_snapshot_is_rejected_by_capability(
    bootstrap: AnalysisBootstrapResult,
) -> None:
    capability = _resolver(REPO_ROOT, bootstrap).resolve_source_note_inventory(
        snapshot=bootstrap.snapshot
    )
    wrong = ChangeControlSnapshot(
        aggregate=bootstrap.snapshot.aggregate,
        revision=bootstrap.snapshot.revision + 1,
        aggregate_sha256=bootstrap.snapshot.aggregate_sha256,
    )
    with pytest.raises(SourceNoteInventoryResolutionError, match="altered"):
        capability.verify(snapshot=wrong)
