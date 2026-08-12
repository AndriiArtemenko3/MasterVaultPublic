"""Focused tests for generic, filesystem-only workspace bootstrap resolution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from mastervault.change_control.legacy_index import LegacyIndexAttestation
from mastervault.change_control.models import aggregate_sha256
from mastervault.change_control.workspace_bootstrap import (
    LegacyIndexReadinessReceipt,
    WorkspaceBootstrapIntent,
    WorkspaceBootstrapState,
    WorkspaceInventoryReceipt,
    _mint_verified_workspace_bootstrap_evidence_verifier,
    verify_workspace_bootstrap_evidence,
)
from mastervault.change_control.workspace_bootstrap_repository import (
    BootstrapSourceRoot,
    WorkspaceBootstrapManifestError,
    WorkspaceBootstrapPlatformUnsupportedError,
    WorkspaceBootstrapRepositoryError,
    resolve_workspace_bootstrap,
)
from mastervault.contracts.page_grounded_claims import EvidenceCandidate
from mastervault.document_intelligence import (
    load_pdf_source,
    parse_pdf,
    resolve_evidence,
    store_parsed_document,
    store_source_asset,
)
from mastervault.models import Claim, SourceNote, content_hash
from mastervault.vaultfs.notes import write_note

MINI_VAULT = Path(__file__).parents[2] / "fixtures" / "mini_vault"
MANAGED = "customer-support/sources/policy-returns-and-refunds.md"
RAW = "raw/policy-returns-and-refunds.md"
INDEX_BYTES = b"declared legacy sqlite bytes"
PDF_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
PDF_QUOTE = "Customers may return any item within 45 days of the delivery date."
MODEL = "mock-hashing-trick-v1"
DIMENSIONS = 8
SCHEMA = 3


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    workspace = tmp_path / "workspace"
    shutil.copytree(MINI_VAULT, workspace / "vault")
    note_path = workspace / "vault" / MANAGED
    raw = b"Exact governing returns source.\n"
    (workspace / RAW).parent.mkdir(parents=True)
    (workspace / RAW).write_bytes(raw)
    note = note_path.read_text(encoding="utf-8")
    provenance = f"provenance: {RAW}\nprovenance_hash: {content_hash(raw.decode('utf-8'))}\n"
    note = note.replace("key_claims:\n", f"{provenance}key_claims:\n", 1)
    note_path.write_text(note, encoding="utf-8")
    return workspace, note.encode("utf-8"), raw


def _manifest(note: bytes, raw: bytes) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "aggregate_id": "workspace-bootstrap-test",
        "legacy_index_file_sha256": _sha(INDEX_BYTES),
        "legacy_index_file_byte_count": len(INDEX_BYTES),
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


def _write_manifest(
    workspace: Path,
    payload: dict[str, Any],
    *,
    suffix: str,
) -> Path:
    path = workspace / f"bootstrap{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _resolve(
    workspace: Path,
    manifest: Path,
    *,
    source_roots: tuple[BootstrapSourceRoot, ...] = (),
):
    return resolve_workspace_bootstrap(
        workspace_root=workspace,
        manifest_path=manifest,
        source_roots=source_roots,
        index_schema_version=SCHEMA,
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
    )


def _externalized_workspace(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, bytes, dict[str, Any]]:
    workspace, _old_note, raw = _workspace(tmp_path)
    external_root = tmp_path / "operator imports"
    external_root.mkdir()
    external_file = external_root / "Returns Policy Source.md"
    external_file.write_bytes(raw)
    (workspace / RAW).unlink()

    note_path = workspace / "vault" / MANAGED
    note_text = note_path.read_text(encoding="utf-8").replace(
        f"provenance: {RAW}\n",
        f"provenance: {external_file}\n",
        1,
    )
    note_path.write_text(note_text, encoding="utf-8")
    note = note_path.read_bytes()
    payload = _manifest(note, raw)
    entry = payload["managed_source_notes"][0]
    entry["source_root_id"] = "operator-imports"
    entry["source_relative_path"] = external_file.name
    entry["source_note_provenance"] = str(external_file)
    return workspace, external_root, external_file, note_path, raw, payload


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_resolves_yaml_and_json_into_the_same_exact_workspace(tmp_path: Path, suffix: str) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=suffix)

    result = _resolve(workspace, manifest)

    expected_paths = tuple(
        path.relative_to(workspace / "vault").as_posix()
        for path in sorted((workspace / "vault").rglob("*.md"))
    )
    assert result.workspace_root == workspace
    assert result.legacy_index_path == workspace / "index.db"
    assert tuple(item.rel_path for item in result.exact_vault_notes) == expected_paths
    assert len(result.inventory.vault_members) == len(expected_paths)
    assert len(result.inventory.managed_source_notes) == 1
    assert len(result.aggregate.documents.documents) == 1
    assert len(result.aggregate.claims.revisions) == 3
    assert aggregate_sha256(result.aggregate)
    assert result.aggregate.relation_graph.assessments == ()
    assert result.aggregate.dependencies.assessments == ()
    assert result.aggregate.document_replacements.assessments == ()
    assert result.aggregate.temporal_constraints.constraints == ()
    assert result.inventory.manifest_sha256 == _sha(manifest.read_bytes())
    assert result.inventory.legacy_index.index_file_sha256 == _sha(INDEX_BYTES)


def test_explicit_external_source_root_preserves_opaque_provenance(
    tmp_path: Path,
) -> None:
    workspace, external_root, external_file, _note_path, raw, payload = (
        _externalized_workspace(tmp_path)
    )
    manifest = _write_manifest(workspace, payload, suffix=".yaml")
    source_root = BootstrapSourceRoot(root_id="operator-imports", path=external_root)

    result = _resolve(workspace, manifest, source_roots=(source_root,))

    assert result.source_roots == (source_root,)
    assert len(result.managed_source_notes) == 1
    managed = result.managed_source_notes[0]
    assert managed.raw_source_bytes == raw
    assert managed.metadata.source_root_id == "operator-imports"
    assert managed.metadata.source_relative_path == external_file.name
    assert managed.metadata.source_note_provenance == str(external_file)
    assert managed.note.provenance == str(external_file)
    assert managed.metadata.raw_source_path.startswith(
        "bootstrap-sources/operator-imports/"
    )
    assert str(external_root) not in managed.metadata.raw_source_path
    assert managed.metadata.document.source_path == managed.metadata.raw_source_path
    assert managed.metadata.document.source_sha256 == _sha(raw)


def test_external_source_root_ids_must_match_the_manifest_exactly(tmp_path: Path) -> None:
    workspace, external_root, _external_file, _note_path, _raw, payload = (
        _externalized_workspace(tmp_path)
    )
    manifest = _write_manifest(workspace, payload, suffix=".json")

    with pytest.raises(WorkspaceBootstrapManifestError, match="exactly equal"):
        _resolve(workspace, manifest)
    with pytest.raises(WorkspaceBootstrapManifestError, match="exactly equal"):
        _resolve(
            workspace,
            manifest,
            source_roots=(BootstrapSourceRoot(root_id="wrong-root", path=external_root),),
        )


def test_external_source_root_rejects_group_writable_directory(tmp_path: Path) -> None:
    workspace, external_root, _external_file, _note_path, _raw, payload = (
        _externalized_workspace(tmp_path)
    )
    manifest = _write_manifest(workspace, payload, suffix=".yaml")
    external_root.chmod(0o770)

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="owner-controlled"):
        _resolve(
            workspace,
            manifest,
            source_roots=(
                BootstrapSourceRoot(root_id="operator-imports", path=external_root),
            ),
        )


def test_external_source_roots_must_be_pairwise_disjoint(tmp_path: Path) -> None:
    workspace, external_root, external_file, note_path, raw, payload = (
        _externalized_workspace(tmp_path)
    )
    nested_root = external_root / "nested"
    nested_root.mkdir()
    nested_file = nested_root / "second.md"
    nested_file.write_bytes(raw)
    duplicate = dict(payload["managed_source_notes"][0])
    duplicate.update(
        {
            "logical_path": "customer-support/sources/second-source.md",
            "source_root_id": "nested-imports",
            "source_relative_path": nested_file.name,
            "source_note_provenance": str(nested_file),
            "document_id": "second-source",
            "document_family": "second-source",
        }
    )
    second_note = note_path.read_text(encoding="utf-8").replace(
        str(external_file),
        str(nested_file),
    )
    second_path = workspace / "vault" / duplicate["logical_path"]
    second_path.write_text(second_note, encoding="utf-8")
    second_bytes = second_path.read_bytes()
    duplicate["source_note_sha256"] = _sha(second_bytes)
    duplicate["source_note_byte_count"] = len(second_bytes)
    payload["managed_source_notes"].append(duplicate)
    payload["managed_source_notes"].sort(key=lambda item: item["logical_path"])
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    with pytest.raises(WorkspaceBootstrapManifestError, match="pairwise disjoint"):
        _resolve(
            workspace,
            manifest,
            source_roots=(
                BootstrapSourceRoot(root_id="operator-imports", path=external_root),
                BootstrapSourceRoot(root_id="nested-imports", path=nested_root),
            ),
        )


@pytest.mark.parametrize("mutation", ["symlink-root", "symlink-file", "hardlink", "writable"])
def test_external_source_evidence_rejects_unsafe_aliases_and_modes(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace, external_root, external_file, _note_path, _raw, payload = (
        _externalized_workspace(tmp_path)
    )
    manifest = _write_manifest(workspace, payload, suffix=".yaml")
    runtime_root = external_root
    if mutation == "symlink-root":
        runtime_root = tmp_path / "linked imports"
        runtime_root.symlink_to(external_root, target_is_directory=True)
    elif mutation == "symlink-file":
        target = tmp_path / "source target.md"
        external_file.replace(target)
        external_file.symlink_to(target)
    elif mutation == "hardlink":
        os.link(external_file, tmp_path / "external source alias.md")
    else:
        external_file.chmod(0o664)

    with pytest.raises(
        WorkspaceBootstrapRepositoryError,
        match="non-symlink|owner-controlled|not a regular file",
    ):
        _resolve(
            workspace,
            manifest,
            source_roots=(
                BootstrapSourceRoot(root_id="operator-imports", path=runtime_root),
            ),
        )


def test_external_source_cannot_overlap_protected_workspace_evidence(tmp_path: Path) -> None:
    workspace, _external_root, _external_file, _note_path, raw, payload = (
        _externalized_workspace(tmp_path)
    )
    workspace_source = workspace / "imports" / "source.md"
    workspace_source.parent.mkdir()
    workspace_source.write_bytes(raw)
    entry = payload["managed_source_notes"][0]
    entry["source_relative_path"] = "imports/source.md"
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    with pytest.raises(WorkspaceBootstrapManifestError, match="protected workspace"):
        _resolve(
            workspace,
            manifest,
            source_roots=(
                BootstrapSourceRoot(root_id="operator-imports", path=workspace),
            ),
        )


def test_managed_selection_is_a_true_subset_of_the_complete_vault(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")

    result = _resolve(workspace, manifest)

    source_members = {
        item.logical_path
        for item in result.inventory.vault_members
        if item.note_kind.value == "source"
    }
    assert {item.logical_path for item in result.inventory.managed_source_notes} == {MANAGED}
    assert len(source_members) > 1


@pytest.mark.parametrize(
    "contents",
    [
        "schema_version: 1\nschema_version: 1\n",
        "schema_version: &version 1\naggregate_id: *version\n",
    ],
)
def test_ambiguous_yaml_manifest_fails_closed(tmp_path: Path, contents: str) -> None:
    workspace, _note, _raw = _workspace(tmp_path)
    manifest = workspace / "bootstrap.yaml"
    manifest.write_text(contents, encoding="utf-8")

    with pytest.raises(WorkspaceBootstrapManifestError, match="duplicate|anchors"):
        _resolve(workspace, manifest)


def test_json_dates_are_strict_but_supported(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    payload["managed_source_notes"][0]["declared_effective_from"] = "2026-3-1"
    manifest = _write_manifest(workspace, payload, suffix=".json")

    with pytest.raises(WorkspaceBootstrapManifestError, match="contract is invalid"):
        _resolve(workspace, manifest)


@pytest.mark.parametrize(
    "contents, message",
    [
        ('{"schema_version":1,"schema_version":1}', "duplicate JSON key"),
        ('{"schema_version":1,"aggregate_id":NaN}', "non-finite JSON value"),
    ],
)
def test_ambiguous_or_non_finite_json_fails_closed(
    tmp_path: Path, contents: str, message: str
) -> None:
    workspace, _note, _raw = _workspace(tmp_path)
    manifest = workspace / "bootstrap.json"
    manifest.write_text(contents, encoding="utf-8")

    with pytest.raises(WorkspaceBootstrapManifestError, match=message):
        _resolve(workspace, manifest)


def test_manifest_suffix_and_symlink_are_typed_manifest_errors(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    unsupported = _write_manifest(workspace, payload, suffix=".toml")
    with pytest.raises(WorkspaceBootstrapManifestError, match="YAML or JSON"):
        _resolve(workspace, unsupported)

    target = _write_manifest(workspace, payload, suffix=".yaml")
    linked = workspace / "linked.yaml"
    linked.symlink_to(target)
    with pytest.raises(WorkspaceBootstrapManifestError, match="symlink"):
        _resolve(workspace, linked)


@pytest.mark.parametrize("conflict_field", ["logical_path", "source_relative_path"])
def test_manifest_paths_are_case_and_unicode_unambiguous(
    tmp_path: Path, conflict_field: str
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    duplicate = dict(payload["managed_source_notes"][0])
    duplicate["logical_path"] = "customer-support/sources/other.md"
    duplicate["source_relative_path"] = "raw/other.md"
    duplicate["source_note_provenance"] = "raw/other.md"
    duplicate["document_id"] = "other"
    duplicate["document_family"] = "other"
    if conflict_field == "logical_path":
        duplicate["logical_path"] = MANAGED.replace("customer-support", "CUSTOMER-SUPPORT")
    else:
        duplicate["source_relative_path"] = RAW.upper()
    payload["managed_source_notes"].append(duplicate)
    payload["managed_source_notes"].sort(key=lambda item: item["logical_path"])
    manifest = _write_manifest(workspace, payload, suffix=".json")

    with pytest.raises(WorkspaceBootstrapManifestError, match="case/Unicode"):
        _resolve(workspace, manifest)


@pytest.mark.parametrize("alias_kind", ["filename", "parent"])
def test_filesystem_paths_reject_nfc_nfd_sibling_aliases(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    workspace, _note, raw = _workspace(tmp_path)
    if alias_kind == "filename":
        raw_rel = "raw/polícy.md"
        alias_rel = unicodedata.normalize("NFD", raw_rel)
    else:
        raw_rel = "ráw/policy.md"
        alias_rel = unicodedata.normalize("NFD", raw_rel)
    assert raw_rel != alias_rel
    old_raw = workspace / RAW
    selected = workspace / raw_rel
    selected.parent.mkdir(parents=True, exist_ok=True)
    old_raw.rename(selected)
    note_path = workspace / "vault" / MANAGED
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(RAW, raw_rel),
        encoding="utf-8",
    )
    note = note_path.read_bytes()
    payload = _manifest(note, raw)
    payload["managed_source_notes"][0]["source_relative_path"] = raw_rel
    payload["managed_source_notes"][0]["source_note_provenance"] = raw_rel
    manifest = _write_manifest(workspace, payload, suffix=".yaml")
    alias = workspace / alias_rel
    try:
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias.write_bytes(b"Unicode alias outside the selected exact bytes")
    except FileExistsError:
        pytest.skip("filesystem normalizes NFC/NFD sibling names")
    if alias.samefile(selected):
        pytest.skip("filesystem normalizes NFC/NFD sibling names")

    with pytest.raises(
        WorkspaceBootstrapRepositoryError,
        match="case/Unicode-ambiguous",
    ):
        _resolve(workspace, manifest)


@pytest.mark.parametrize("component", ["evals", "golden"])
def test_manifest_rejects_evaluator_raw_paths(tmp_path: Path, component: str) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    payload["managed_source_notes"][0]["source_relative_path"] = f"{component}/source.md"
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    with pytest.raises(WorkspaceBootstrapManifestError, match="canonical|evaluator"):
        _resolve(workspace, manifest)


def test_manifest_rejects_dot_source_relative_path(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    payload["managed_source_notes"][0]["source_relative_path"] = "."
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    with pytest.raises(WorkspaceBootstrapManifestError, match="relative POSIX path|contract"):
        _resolve(workspace, manifest)


def test_complete_vault_rejects_non_markdown_and_symlink_members(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    extra = workspace / "vault" / "unexpected.txt"
    extra.write_text("not indexable", encoding="utf-8")

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="closed indexable inventory"):
        _resolve(workspace, manifest)

    extra.unlink()
    victim = workspace / "outside.md"
    victim.write_text("outside", encoding="utf-8")
    (workspace / "vault" / "linked.md").symlink_to(victim)
    with pytest.raises(WorkspaceBootstrapRepositoryError, match="regular"):
        _resolve(workspace, manifest)


@pytest.mark.parametrize("target", ["manifest", "vault", "raw"])
def test_workspace_evidence_rejects_external_hardlink_aliases(
    tmp_path: Path,
    target: str,
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    selected = {
        "manifest": manifest,
        "vault": workspace / "vault" / MANAGED,
        "raw": workspace / RAW,
    }[target]
    os.link(selected, tmp_path / f"external-{target}")

    with pytest.raises(
        WorkspaceBootstrapRepositoryError,
        match="private owner-controlled|symlink or special",
    ):
        _resolve(workspace, manifest)


@pytest.mark.parametrize("target", ["manifest", "vault", "raw"])
def test_workspace_evidence_rejects_group_or_other_writable_files(
    tmp_path: Path,
    target: str,
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    selected = {
        "manifest": manifest,
        "vault": workspace / "vault" / MANAGED,
        "raw": workspace / RAW,
    }[target]
    selected.chmod(0o664)

    with pytest.raises(
        WorkspaceBootstrapRepositoryError,
        match="private owner-controlled",
    ):
        _resolve(workspace, manifest)


def test_workspace_root_rejects_group_writable_directory(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    workspace.chmod(0o770)

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="owner-controlled"):
        _resolve(workspace, manifest)


def test_supporting_file_attachment_classifies_quoted_source_frontmatter(
    tmp_path: Path,
) -> None:
    workspace, _old_note, _old_raw = _workspace(tmp_path)
    document = parse_pdf(PDF_FIXTURE)
    asset = store_source_asset(load_pdf_source(PDF_FIXTURE), workspace)
    parsed = store_parsed_document(document, workspace)
    note = SourceNote(
        domain="customer-support",
        title="Quoted source type",
        created=date(2026, 8, 12),
        updated=date(2026, 8, 12),
        source_type="policy",
        provenance=asset.stored_path,
        key_claims=[
            Claim(
                id="quoted-source-type-01",
                statement=PDF_QUOTE,
                confidence="high",
                evidence=resolve_evidence(
                    document,
                    [
                        EvidenceCandidate(
                            block_id="page-0001-block-0001",
                            quote=PDF_QUOTE,
                        )
                    ],
                ),
            )
        ],
        source_asset=asset,
        parsed_document=parsed,
    )
    note_path = workspace / "vault" / MANAGED
    write_note(note_path, note, "# Quoted source type")
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(
            "type: source",
            'type: "source"',
            1,
        ),
        encoding="utf-8",
    )
    note_bytes = note_path.read_bytes()
    asset_bytes = (workspace / asset.stored_path).read_bytes()
    payload = _manifest(note_bytes, asset_bytes)
    payload["managed_source_notes"][0]["source_relative_path"] = asset.stored_path
    payload["managed_source_notes"][0]["source_note_provenance"] = asset.stored_path
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    result = _resolve(workspace, manifest)

    exact = next(item for item in result.exact_vault_notes if item.rel_path == MANAGED)
    assert tuple(item.rel_path for item in exact.supporting_files) == (
        asset.stored_path,
        parsed.artifact_path,
    )


def test_non_source_body_text_cannot_spoof_frontmatter_classification(
    tmp_path: Path,
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    wiki = workspace / "vault" / "customer-support/wiki/refund-window.md"
    wiki.write_text(
        f"{wiki.read_text(encoding='utf-8')}\nLiteral example: type: source\n",
        encoding="utf-8",
    )

    result = _resolve(workspace, manifest)

    assert any(
        item.logical_path == "customer-support/wiki/refund-window.md"
        for item in result.inventory.vault_members
    )


@pytest.mark.parametrize("component", ["evals", "golden"])
def test_complete_vault_rejects_evaluator_components(tmp_path: Path, component: str) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    evaluator = workspace / "vault" / component
    evaluator.mkdir()
    (evaluator / "note.md").write_text("not runtime knowledge", encoding="utf-8")

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="evaluator"):
        _resolve(workspace, manifest)


def test_complete_vault_rejects_excessive_empty_directory_depth(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    nested = workspace / "vault"
    for ordinal in range(34):
        nested /= f"depth-{ordinal:02d}"
        nested.mkdir()

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="directory depth"):
        _resolve(workspace, manifest)


def test_raw_source_hash_count_provenance_and_legacy_hash_are_exact(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    payload = _manifest(note, raw)
    payload["managed_source_notes"][0]["raw_source_sha256"] = "0" * 64
    manifest = _write_manifest(workspace, payload, suffix=".yaml")

    with pytest.raises(WorkspaceBootstrapRepositoryError, match="raw-source bytes differ"):
        _resolve(workspace, manifest)

    payload = _manifest(note, raw)
    path = workspace / "vault" / MANAGED
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"provenance_hash: {content_hash(raw.decode('utf-8'))}",
            "provenance_hash: ffffffffffffffff",
        ),
        encoding="utf-8",
    )
    changed = path.read_bytes()
    payload["managed_source_notes"][0]["source_note_sha256"] = _sha(changed)
    payload["managed_source_notes"][0]["source_note_byte_count"] = len(changed)
    manifest = _write_manifest(workspace, payload, suffix=".yaml")
    with pytest.raises(WorkspaceBootstrapRepositoryError, match="provenance hash differs"):
        _resolve(workspace, manifest)


def test_platform_without_required_no_follow_contract_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    monkeypatch.delattr("os.O_NOFOLLOW")

    with pytest.raises(WorkspaceBootstrapPlatformUnsupportedError, match="platform"):
        _resolve(workspace, manifest)


@dataclass(frozen=True)
class _PersistedSnapshot:
    aggregate: Any
    revision: int
    aggregate_sha256: str


class _TestEvidenceGuard:
    def __init__(self) -> None:
        self.fresh = True
        self.checks = 0

    def verify(self) -> None:
        self.checks += 1
        if not self.fresh:
            raise WorkspaceBootstrapRepositoryError("simulated current evidence drift")


def _complete_evidence(result: Any) -> tuple[Any, _PersistedSnapshot, LegacyIndexAttestation]:
    intent = WorkspaceBootstrapIntent.create(
        operation_id="bootstrap:workspace",
        aggregate_id=result.aggregate.aggregate_id,
        inventory=result.inventory,
    )
    digest = aggregate_sha256(result.aggregate)
    inventory_receipt = WorkspaceInventoryReceipt.create(
        operation_id="bootstrap:inventory",
        bootstrap_id=intent.bootstrap_id,
        aggregate_operation_id="bootstrap:aggregate",
        aggregate_id=result.aggregate.aggregate_id,
        aggregate_revision=1,
        aggregate_sha256=digest,
        inventory_id=result.inventory.inventory_id,
        inventory_sha256=result.inventory.inventory_sha256,
        recorded_at="2026-08-10T00:00:00+00:00",
    )
    logical_fingerprint = "a" * 64
    readiness = LegacyIndexReadinessReceipt.create(
        operation_id="bootstrap:index",
        bootstrap_id=intent.bootstrap_id,
        inventory_receipt_id=inventory_receipt.receipt_id,
        inventory_receipt_sha256=inventory_receipt.receipt_sha256,
        index_logical_fingerprint=logical_fingerprint,
        index_file_sha256=result.inventory.legacy_index.index_file_sha256,
        index_file_byte_count=result.inventory.legacy_index.index_file_byte_count,
        index_schema_version=result.inventory.legacy_index.index_schema_version,
        embedding_model=result.inventory.legacy_index.embedding_model,
        embedding_dimensions=result.inventory.legacy_index.embedding_dimensions,
        ready_at="2026-08-10T00:00:01+00:00",
    )
    state = WorkspaceBootstrapState(
        intent=intent,
        inventory=result.inventory,
        inventory_receipt=inventory_receipt,
        index_readiness_receipt=readiness,
    )
    snapshot = _PersistedSnapshot(
        aggregate=result.aggregate,
        revision=1,
        aggregate_sha256=digest,
    )
    attestation = LegacyIndexAttestation(
        index_file_sha256=result.inventory.legacy_index.index_file_sha256,
        index_file_byte_count=result.inventory.legacy_index.index_file_byte_count,
        projection_fingerprint="b" * 64,
        logical_index_fingerprint=logical_fingerprint,
        storage_schema_version=result.inventory.legacy_index.index_schema_version,
        embedding_model_version=result.inventory.legacy_index.embedding_model,
        embedding_dimensions=result.inventory.legacy_index.embedding_dimensions,
        counts=(("documents", len(result.inventory.vault_members)),),
    )
    return state, snapshot, attestation


def test_public_verifier_mints_only_from_fresh_complete_exact_evidence(
    tmp_path: Path,
) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".json")
    result = _resolve(workspace, manifest)
    state, snapshot, attestation = _complete_evidence(result)
    guard = _TestEvidenceGuard()
    evidence_verifier = _mint_verified_workspace_bootstrap_evidence_verifier(
        guard,
        resolved_inventory=result.inventory,
        resolved_aggregate=result.aggregate,
        legacy_attestation=attestation,
    )

    capability = verify_workspace_bootstrap_evidence(
        state=state,
        resolved_inventory=result.inventory,
        resolved_aggregate=result.aggregate,
        persisted_snapshot=snapshot,
        legacy_attestation=attestation,
        evidence_verifier=evidence_verifier,
    )

    assert capability.verify() == state
    assert guard.checks >= 4
    guard.fresh = False
    with pytest.raises(ValueError, match="freshly verify"):
        capability.verify()
    fresh_evidence_verifier = _mint_verified_workspace_bootstrap_evidence_verifier(
        _TestEvidenceGuard(),
        resolved_inventory=result.inventory,
        resolved_aggregate=result.aggregate,
        legacy_attestation=replace(attestation, index_file_sha256="0" * 64),
    )
    with pytest.raises(ValueError, match="legacy index attestation"):
        verify_workspace_bootstrap_evidence(
            state=state,
            resolved_inventory=result.inventory,
            resolved_aggregate=result.aggregate,
            persisted_snapshot=snapshot,
            legacy_attestation=replace(attestation, index_file_sha256="0" * 64),
            evidence_verifier=fresh_evidence_verifier,
        )


def test_public_verifier_rejects_incomplete_persisted_state(tmp_path: Path) -> None:
    workspace, note, raw = _workspace(tmp_path)
    manifest = _write_manifest(workspace, _manifest(note, raw), suffix=".yaml")
    result = _resolve(workspace, manifest)
    complete, snapshot, attestation = _complete_evidence(result)
    incomplete = WorkspaceBootstrapState(
        intent=complete.intent,
        inventory=complete.inventory,
    )
    evidence_verifier = _mint_verified_workspace_bootstrap_evidence_verifier(
        _TestEvidenceGuard(),
        resolved_inventory=result.inventory,
        resolved_aggregate=result.aggregate,
        legacy_attestation=attestation,
    )

    with pytest.raises(ValueError, match="evidence is invalid"):
        verify_workspace_bootstrap_evidence(
            state=incomplete,
            resolved_inventory=result.inventory,
            resolved_aggregate=result.aggregate,
            persisted_snapshot=snapshot,
            legacy_attestation=attestation,
            evidence_verifier=evidence_verifier,
        )
