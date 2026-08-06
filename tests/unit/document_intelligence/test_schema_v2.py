from __future__ import annotations

import copy
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

import mastervault.document_intelligence.docling_adapter as docling_adapter
import mastervault.document_intelligence.fetch_docling_artifacts as docling_fetcher
from mastervault.cli.app import app
from mastervault.contracts.page_grounded_claims import EvidenceCandidate
from mastervault.core.errors import EvidenceGroundingError, UnreadableDocument
from mastervault.document_intelligence import (
    DoclingParser,
    ParsedDocumentV2,
    PdfSource,
    StructuralEvidenceRef,
    load_parsed_document,
    parsed_document_bytes,
    render_document_markdown,
    resolve_evidence,
    store_parsed_document,
    validate_resolved_evidence,
)
from mastervault.document_intelligence.docling_adapter import (
    DOCLING_ARTIFACT_MANIFEST,
    DOCLING_COMPONENT_VERSIONS,
    DOCLING_DOCUMENT_TIMEOUT_SECONDS,
    DOCLING_EXPECTED_ARTIFACT_BYTES,
    DOCLING_HEADING_HIERARCHY_OPTIONS,
    DOCLING_MAX_PAGES,
    DOCLING_MAX_SOURCE_BYTES,
    DOCLING_MODEL_IDENTITY,
    DoclingDoctorReport,
    doctor_docling,
)
from mastervault.document_intelligence.docling_normalizer import normalize_docling_export


def _prov(page: int, left: float, top: float, right: float, bottom: float) -> list[dict]:
    return [
        {
            "page_no": page,
            "bbox": {
                "l": left,
                "t": top,
                "r": right,
                "b": bottom,
                "coord_origin": "BOTTOMLEFT",
            },
            "charspan": [0, 1],
        }
    ]


def _cell(row: int, column: int, text: str, **extra) -> dict:
    return {
        "bbox": {
            "l": 10 + column * 40,
            "t": 100 + row * 15,
            "r": 45 + column * 40,
            "b": 112 + row * 15,
            "coord_origin": "TOPLEFT",
        },
        "row_span": 1,
        "col_span": 1,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": column,
        "end_col_offset_idx": column + 1,
        "text": text,
        "column_header": row == 0,
        "row_header": False,
        **extra,
    }


def _vendor_export(*, second_table: bool = False) -> dict:
    tables = [
        {
            "label": "table",
            "prov": _prov(1, 10, 100, 90, 40),
            "data": {
                "num_rows": 2,
                "num_cols": 2,
                "table_cells": [
                    _cell(0, 0, "Region"),
                    _cell(0, 1, "Total"),
                    _cell(1, 0, "North"),
                    _cell(1, 1, "42"),
                ],
            },
        }
    ]
    if second_table:
        tables.append(
            {
                "label": "table",
                "prov": _prov(1, 10, 35, 90, 10),
                "data": {
                    "num_rows": 1,
                    "num_cols": 1,
                    "table_cells": [_cell(0, 0, "Elsewhere")],
                },
            }
        )
    return {
        "pages": {"1": {"size": {"width": 100.0, "height": 200.0}}},
        "texts": [
            {"label": "page_header", "text": "ACME REPORT", "prov": _prov(1, 5, 195, 45, 185)},
            {"label": "title", "text": "Quarterly report", "prov": _prov(1, 10, 180, 90, 165)},
            {"label": "section_header", "text": "Results", "prov": _prov(1, 10, 155, 90, 145)},
            {"label": "text", "text": "North reached 42 units.", "prov": _prov(1, 10, 140, 90, 125)},
        ],
        "tables": tables,
    }


def _document(*, second_table: bool = False) -> ParsedDocumentV2:
    return normalize_docling_export(
        _vendor_export(second_table=second_table),
        asset_sha256="a" * 64,
        parser_version="2.118.0",
        parser_core_version="2.91.0",
        model_identity="sha256:" + "b" * 64,
    )


def test_schema_v2_normalization_ids_json_and_markdown_are_deterministic(tmp_path) -> None:
    first = _document()
    second = _document()
    assert parsed_document_bytes(first) == parsed_document_bytes(second)
    assert [block.block_id for block in first.blocks] == [
        f"block-{index:04d}" for index in range(1, len(first.blocks) + 1)
    ]
    assert [cell.cell_id for cell in first.tables[0].cells] == [
        "cell-0001",
        "cell-0002",
        "cell-0003",
        "cell-0004",
    ]
    restored = ParsedDocumentV2.model_validate_json(parsed_document_bytes(first))
    assert restored == first
    reference = store_parsed_document(first, tmp_path)
    assert reference.document_schema_version == 2
    assert reference.normalization_profile == "mv-clean-digital-v1"
    assert reference.resource_limits == first.resource_limits
    assert load_parsed_document(reference, tmp_path) == first

    markdown = render_document_markdown(first)
    assert "ACME REPORT" not in markdown
    assert "| Column 1 | Column 2 |" in markdown
    assert "| North | 42 |" in markdown
    assert markdown == render_document_markdown(second)


def test_schema_v2_rejects_invalid_ids_hierarchy_coordinates_and_spans() -> None:
    payload = _document().model_dump()
    invalid_id = copy.deepcopy(payload)
    invalid_id["blocks"][0]["block_id"] = "#/texts/0"
    with pytest.raises(ValidationError):
        ParsedDocumentV2.model_validate(invalid_id)

    invalid_parent = copy.deepcopy(payload)
    invalid_parent["sections"][0]["parent_section_id"] = "section-0001"
    with pytest.raises(ValidationError, match="section parent"):
        ParsedDocumentV2.model_validate(invalid_parent)

    invalid_coordinate = copy.deepcopy(payload)
    invalid_coordinate["blocks"][0]["bbox"]["x0"] = 0.1234567
    with pytest.raises(ValidationError, match="quantized"):
        ParsedDocumentV2.model_validate(invalid_coordinate)

    invalid_span = copy.deepcopy(payload)
    invalid_span["tables"][0]["cells"][0]["column_span"] = 3
    with pytest.raises(ValidationError, match="outside the grid"):
        ParsedDocumentV2.model_validate(invalid_span)

    contradictory_row = copy.deepcopy(payload)
    cell = contradictory_row["tables"][0]["cells"][3]
    cell["row_id"] = "row-0001"
    contradictory_row["tables"][0]["rows"][0]["cell_ids"].append("cell-0004")
    contradictory_row["tables"][0]["rows"][1]["cell_ids"].remove("cell-0004")
    with pytest.raises(ValidationError, match="row_id and row_index"):
        ParsedDocumentV2.model_validate(contradictory_row)

    incomplete_grid = copy.deepcopy(payload)
    incomplete_grid["tables"][0]["cells"].pop()
    incomplete_grid["tables"][0]["rows"][1]["cell_ids"].pop()
    with pytest.raises(ValidationError, match="do not cover"):
        ParsedDocumentV2.model_validate(incomplete_grid)

    forged_section_owner = copy.deepcopy(payload)
    forged_section_owner["sections"][0]["title"] = "Different heading"
    with pytest.raises(ValidationError, match="matching heading block"):
        ParsedDocumentV2.model_validate(forged_section_owner)

    forged_block_owner = copy.deepcopy(payload)
    forged_block_owner["blocks"][3]["section_id"] = None
    with pytest.raises(ValidationError, match="active deepest section"):
        ParsedDocumentV2.model_validate(forged_block_owner)


def test_schema_v2_rejects_a_skipped_nearest_section_parent() -> None:
    export = _vendor_export()
    export["texts"].extend(
        [
            {
                "label": "section_header",
                "text": "Nested section",
                "level": 2,
                "prov": _prov(1, 10, 120, 90, 115),
            },
            {
                "label": "section_header",
                "text": "Deep section",
                "level": 3,
                "prov": _prov(1, 10, 110, 90, 105),
            },
        ]
    )
    payload = normalize_docling_export(
        export,
        asset_sha256="a" * 64,
        parser_version="2.118.0",
        parser_core_version="2.91.0",
        model_identity="sha256:" + "b" * 64,
    ).model_dump()
    assert payload["sections"][2]["parent_section_id"] == "section-0002"
    payload["sections"][2]["parent_section_id"] = "section-0001"
    with pytest.raises(ValidationError, match="nearest shallower"):
        ParsedDocumentV2.model_validate(payload)


def test_row_span_keeps_empty_covered_row_and_renders_explicit_grid() -> None:
    export = _vendor_export()
    export["tables"][0]["data"] = {
        "num_rows": 2,
        "num_cols": 1,
        "table_cells": [_cell(0, 0, "Spans both rows", row_span=2)],
    }
    document = normalize_docling_export(
        export,
        asset_sha256="a" * 64,
        parser_version="2.118.0",
        parser_core_version="2.91.0",
        model_identity="sha256:" + "b" * 64,
    )

    assert [row.row_id for row in document.tables[0].rows] == ["row-0001", "row-0002"]
    assert document.tables[0].rows[1].cell_ids == []
    assert document.tables[0].cells[0].column_header is True
    assert document.tables[0].cells[0].row_span == 2
    markdown = render_document_markdown(document)
    assert "```table-grid table-0001" in markdown
    assert "rowspan=2 colspan=1" in markdown


def test_cell_evidence_derives_location_and_rejects_every_forged_case() -> None:
    document = _document()
    refs = resolve_evidence(
        document,
        [EvidenceCandidate(cell_id="cell-0004", quote="42")],
    )
    assert refs == [
        StructuralEvidenceRef(
            target_type="cell",
            asset_sha256=document.asset_sha256,
            page_number=1,
            block_id="block-0005",
            table_id="table-0001",
            row_id="row-0002",
            cell_id="cell-0004",
            row_index=1,
            column_index=1,
            bbox=document.tables[0].cells[3].bbox,
            quote="42",
            start_char=0,
            end_char=2,
        )
    ]
    validate_resolved_evidence(document, refs)

    with pytest.raises(EvidenceGroundingError, match="unknown cell"):
        resolve_evidence(document, [EvidenceCandidate(cell_id="cell-9999", quote="42")])
    with pytest.raises(EvidenceGroundingError, match="duplicate"):
        resolve_evidence(
            document,
            [
                EvidenceCandidate(cell_id="cell-0004", quote="42"),
                EvidenceCandidate(cell_id="cell-0004", quote="42"),
            ],
        )
    mixed = _document(second_table=True)
    with pytest.raises(EvidenceGroundingError, match="must not mix tables"):
        resolve_evidence(
            mixed,
            [
                EvidenceCandidate(cell_id="cell-0004", quote="42"),
                EvidenceCandidate(cell_id="cell-0005", quote="Elsewhere"),
            ],
        )
    forged = refs[0].model_copy(update={"column_index": 0})
    with pytest.raises(EvidenceGroundingError, match="forged"):
        validate_resolved_evidence(document, [forged])

    with pytest.raises(EvidenceGroundingError, match="duplicate persisted"):
        validate_resolved_evidence(document, [refs[0], refs[0]])

    distinct_block_refs = resolve_evidence(
        document,
        [
            EvidenceCandidate(block_id="block-0004", quote="North reached"),
            EvidenceCandidate(block_id="block-0004", quote="42 units."),
        ],
    )
    validate_resolved_evidence(document, distinct_block_refs)


def test_optional_adapter_import_and_missing_artifacts_never_download(tmp_path) -> None:
    # Core import remains vendor-free even though the adapter symbol is public.
    assert "docling.document_converter" not in sys.modules


def test_docling_profile_freezes_hierarchy_timeout_and_conversion_ceilings() -> None:
    document = _document()
    assert document.parser_profile == "clean-digital-layout-table-v2"
    assert document.resource_limits.model_dump() == {
        "timeout_seconds": 120.0,
        "max_source_bytes": 52_428_800,
        "max_pages": 200,
    }
    assert DOCLING_DOCUMENT_TIMEOUT_SECONDS == 120.0
    assert DOCLING_MAX_SOURCE_BYTES == 52_428_800
    assert DOCLING_MAX_PAGES == 200
    assert DOCLING_HEADING_HIERARCHY_OPTIONS == {
        "enabled": True,
        "use_bookmarks": True,
        "use_numbering": False,
        "use_style": True,
        "max_level": 6,
        "bookmark_match_threshold": 0.8,
    }

    forged = document.model_dump()
    forged["resource_limits"]["max_pages"] = 201
    with pytest.raises(ValidationError, match="resource limits must match"):
        ParsedDocumentV2.model_validate(forged)

    class RecordingConverter:
        kwargs: dict | None = None

        def convert(self, stream, **kwargs):
            self.kwargs = kwargs
            return stream

    converter = RecordingConverter()
    sentinel = object()
    assert docling_adapter._convert_with_limits(converter, sentinel) is sentinel
    assert converter.kwargs == {
        "raises_on_error": True,
        "max_num_pages": 200,
        "max_file_size": 52_428_800,
    }


def test_docling_source_snapshot_hash_and_size_fail_before_vendor_import(monkeypatch) -> None:
    parser = object.__new__(DoclingParser)
    monkeypatch.setattr(docling_adapter, "DOCLING_MAX_SOURCE_BYTES", 4)
    too_large = b"12345"
    with pytest.raises(UnreadableDocument, match="at most 4 bytes"):
        parser.parse(
            PdfSource(
                path=Path("large.pdf"),
                data=too_large,
                asset_sha256=hashlib.sha256(too_large).hexdigest(),
            )
        )
    with pytest.raises(UnreadableDocument, match="snapshot hash changed"):
        parser.parse(PdfSource(path=Path("changed.pdf"), data=b"pdf", asset_sha256="a" * 64))


def test_docling_manifest_pins_full_revisions_sizes_and_identity() -> None:
    assert DOCLING_EXPECTED_ARTIFACT_BYTES == 317_123_044
    assert DOCLING_MODEL_IDENTITY.startswith("sha256:")
    repositories = DOCLING_ARTIFACT_MANIFEST["repositories"]
    assert {repository["repository"] for repository in repositories} == {
        "docling-project/docling-layout-heron",
        "docling-project/docling-models",
    }
    assert all(len(repository["revision"]) == 40 for repository in repositories)
    assert all(repository["revision"] != "main" for repository in repositories)
    assert sum(
        file["size_bytes"]
        for repository in repositories
        for file in repository["files"]
    ) == DOCLING_EXPECTED_ARTIFACT_BYTES


def test_docling_artifact_verifier_rejects_root_and_file_symlinks(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        docling_adapter,
        "_installed_versions",
        lambda: dict(DOCLING_COMPONENT_VERSIONS),
    )
    real_root = tmp_path / "real"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)
    report = doctor_docling(root_link)
    assert report.ok is False
    assert "must not be a symlink" in report.message

    payload = b"verified"
    target = tmp_path / "target.bin"
    target.write_bytes(payload)
    artifact = real_root / "repo/model.bin"
    artifact.parent.mkdir()
    artifact.symlink_to(target)
    monkeypatch.setattr(
        docling_adapter,
        "DOCLING_ARTIFACT_FILES",
        (
            {
                "relative_path": "repo/model.bin",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            },
        ),
    )
    with pytest.raises(ValueError, match="must not be a symlink"):
        docling_adapter._artifact_report(real_root)


def test_docling_artifact_verifier_rejects_non_regular_file(tmp_path, monkeypatch) -> None:
    root = tmp_path / "artifacts"
    artifact = root / "repo/model.bin"
    artifact.parent.mkdir(parents=True)
    os.mkfifo(artifact)
    monkeypatch.setattr(
        docling_adapter,
        "DOCLING_ARTIFACT_FILES",
        (
            {
                "relative_path": "repo/model.bin",
                "sha256": "0" * 64,
                "size_bytes": 1,
            },
        ),
    )
    with pytest.raises(ValueError, match="regular file"):
        docling_adapter._artifact_report(root)


def test_docling_parser_revalidates_artifacts_after_doctor(tmp_path, monkeypatch) -> None:
    parser = object.__new__(DoclingParser)
    parser.artifacts_path = tmp_path
    parser.model_identity = DOCLING_MODEL_IDENTITY

    def _changed(_path):
        raise ValueError("hash mismatch after initialization")

    monkeypatch.setattr(docling_adapter, "_artifact_report", _changed)
    with pytest.raises(UnreadableDocument, match="changed after verification"):
        parser._assert_artifact_identity()


def test_immutable_fetcher_uses_only_manifest_commits_and_allowlisted_files(
    tmp_path, monkeypatch
) -> None:
    calls: list[dict] = []
    output_root = tmp_path / "docling-artifacts"
    expected_files = {
        f"{repository['destination']}/{source_file['source_path']}"
        for repository in DOCLING_ARTIFACT_MANIFEST["repositories"]
        for source_file in repository["files"]
    }

    def _snapshot_download(**kwargs):
        calls.append(kwargs)
        repository = next(
            repository
            for repository in DOCLING_ARTIFACT_MANIFEST["repositories"]
            if repository["repository"] == kwargs["repo_id"]
        )
        local_dir = Path(kwargs["local_dir"])
        for source_file in repository["files"]:
            artifact = local_dir / source_file["source_path"]
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"downloaded")
        metadata = local_dir / ".cache/huggingface/download"
        metadata.mkdir(parents=True)
        (metadata / "metadata.json").write_text("{}", encoding="utf-8")
        return str(kwargs["local_dir"])

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)

    def _verify_complete_tree(root):
        assert expected_files <= {
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        }
        return DOCLING_MODEL_IDENTITY, DOCLING_EXPECTED_ARTIFACT_BYTES

    monkeypatch.setattr(
        docling_fetcher,
        "_artifact_report",
        _verify_complete_tree,
    )
    assert docling_fetcher.fetch_artifacts(output_root) == (
        DOCLING_MODEL_IDENTITY,
        DOCLING_EXPECTED_ARTIFACT_BYTES,
    )
    assert output_root.is_dir()
    assert {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*")
        if path.is_file()
    } == expected_files
    assert list(tmp_path.glob(".docling-artifacts.fetch-*")) == []
    assert len(calls) == 2
    assert [
        (
            call["repo_id"],
            call["revision"],
            call["allow_patterns"],
            call["force_download"],
        )
        for call in calls
    ] == [
        (
            "docling-project/docling-layout-heron",
            "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
            ["model.safetensors", "config.json", "preprocessor_config.json"],
            False,
        ),
        (
            "docling-project/docling-models",
            "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
            [
                "model_artifacts/tableformer/fast/tableformer_fast.safetensors",
                "model_artifacts/tableformer/fast/tm_config.json",
            ],
            False,
        ),
    ]
    assert all(".docling-artifacts.fetch-" in str(call["local_dir"]) for call in calls)
    report = doctor_docling(tmp_path / "missing")
    assert report.ok is False
    assert "pdf-layout" in report.message or "not a directory" in report.message
    with pytest.raises(UnreadableDocument, match="pdf-layout|not a directory"):
        DoclingParser(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    assert "docling.document_converter" not in sys.modules


@pytest.mark.parametrize("root_kind", ["symlink", "file", "fifo", "empty", "nonempty"])
def test_fetcher_rejects_every_existing_output_root_without_downloading(
    tmp_path, monkeypatch, root_kind
) -> None:
    output_root = tmp_path / "docling-artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    if root_kind == "symlink":
        output_root.symlink_to(outside, target_is_directory=True)
    elif root_kind == "file":
        output_root.write_text("occupied", encoding="utf-8")
    elif root_kind == "fifo":
        os.mkfifo(output_root)
    else:
        output_root.mkdir()
        if root_kind == "nonempty":
            (output_root / "occupied").write_text("occupied", encoding="utf-8")

    calls: list[dict] = []

    def _snapshot_download(**kwargs):
        calls.append(kwargs)
        (Path(kwargs["local_dir"]) / "outside-marker").write_text(
            "unsafe", encoding="utf-8"
        )

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    with pytest.raises(ValueError, match="must be absent"):
        docling_fetcher.fetch_artifacts(output_root)

    assert calls == []
    assert not (outside / "outside-marker").exists()


def test_fetcher_rejects_preexisting_destination_symlink_before_downloading(
    tmp_path, monkeypatch
) -> None:
    output_root = tmp_path / "docling-artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    output_root.mkdir()
    destination = DOCLING_ARTIFACT_MANIFEST["repositories"][0]["destination"]
    (output_root / destination).symlink_to(outside, target_is_directory=True)
    calls: list[dict] = []

    def _snapshot_download(**kwargs):
        calls.append(kwargs)
        (Path(kwargs["local_dir"]) / "outside-marker").write_text(
            "unsafe", encoding="utf-8"
        )

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    with pytest.raises(ValueError, match="must be absent"):
        docling_fetcher.fetch_artifacts(output_root)

    assert calls == []
    assert not (outside / "outside-marker").exists()


@pytest.mark.parametrize(
    "unsafe_destination",
    ["../outside", "/absolute", ".", "safe/../outside", "safe/nested", "safe\\outside"],
)
def test_fetcher_rejects_unsafe_manifest_destinations_before_downloading(
    tmp_path, monkeypatch, unsafe_destination
) -> None:
    forged = copy.deepcopy(DOCLING_ARTIFACT_MANIFEST)
    forged["repositories"][0]["destination"] = unsafe_destination
    monkeypatch.setattr(docling_fetcher, "DOCLING_ARTIFACT_MANIFEST", forged)
    calls: list[dict] = []
    outside_marker = tmp_path / "outside-marker"

    def _snapshot_download(**kwargs):
        calls.append(kwargs)
        outside_marker.write_text("unsafe", encoding="utf-8")

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    output_root = tmp_path / "docling-artifacts"
    with pytest.raises(RuntimeError, match="destination.*unsafe"):
        docling_fetcher.fetch_artifacts(output_root)

    assert calls == []
    assert not outside_marker.exists()
    assert not output_root.exists()


def test_fetcher_rejects_symlinked_output_parent_without_downloading(
    tmp_path, monkeypatch
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    calls: list[dict] = []
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download", lambda **kwargs: calls.append(kwargs)
    )

    with pytest.raises(ValueError, match="output parent must not be a symlink"):
        docling_fetcher.fetch_artifacts(linked_parent / "docling-artifacts")

    assert calls == []
    assert not (real_parent / "docling-artifacts").exists()


@pytest.mark.parametrize("failure", ["hash mismatch", "size mismatch"])
def test_fetcher_identity_failure_cleans_staging_and_publishes_nothing(
    tmp_path, monkeypatch, failure
) -> None:
    output_root = tmp_path / "docling-artifacts"

    def _snapshot_download(**kwargs):
        repository = next(
            repository
            for repository in DOCLING_ARTIFACT_MANIFEST["repositories"]
            if repository["repository"] == kwargs["repo_id"]
        )
        for source_file in repository["files"]:
            artifact = Path(kwargs["local_dir"]) / source_file["source_path"]
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"corrupt")

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    def _reject_artifacts(_root):
        raise ValueError(failure)

    monkeypatch.setattr(docling_fetcher, "_artifact_report", _reject_artifacts)

    with pytest.raises(ValueError, match=failure):
        docling_fetcher.fetch_artifacts(output_root)

    assert not output_root.exists()
    assert list(tmp_path.glob(".docling-artifacts.fetch-*")) == []


def test_fetcher_does_not_replace_output_created_during_acquisition(
    tmp_path, monkeypatch
) -> None:
    output_root = tmp_path / "docling-artifacts"
    calls = 0

    def _snapshot_download(**_kwargs):
        nonlocal calls
        calls += 1
        output_root.mkdir()
        (output_root / "owner-marker").write_text("preserve", encoding="utf-8")

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    with pytest.raises(ValueError, match="must be absent"):
        docling_fetcher.fetch_artifacts(output_root)

    assert calls == 1
    assert (output_root / "owner-marker").read_text(encoding="utf-8") == "preserve"
    assert list(tmp_path.glob(".docling-artifacts.fetch-*")) == []


def test_docling_workflow_prepares_cache_parent_before_cold_fetch(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load(
        (repo_root / ".github/workflows/docling-contract.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["offline-contract"]["steps"]
    step_positions = {
        step.get("name"): index for index, step in enumerate(steps) if "name" in step
    }
    restore = step_positions["Restore certified layout and TableFormer artifacts"]
    prepare = step_positions["Prepare real artifact cache parent"]
    fetch = step_positions["Fetch immutable models on an empty cache"]
    assert restore < prepare < fetch
    assert steps[fetch]["if"] == "steps.model-cache.outputs.cache-hit != 'true'"

    bootstrap = steps[prepare]
    assert bootstrap["env"]["MODEL_CACHE_HIT"] == (
        "${{ steps.model-cache.outputs.cache-hit }}"
    )
    environment = {
        **os.environ,
        "GITHUB_WORKSPACE": str(tmp_path),
        "MV_DOCUMENT__DOCLING_ARTIFACTS_PATH": str(
            tmp_path / ".ci-cache/docling-artifacts"
        ),
        "MODEL_CACHE_HIT": "false",
    }
    for _attempt in range(2):
        subprocess.run(
            ["bash", "-eu", "-o", "pipefail", "-c", bootstrap["run"]],
            check=True,
            cwd=tmp_path,
            env=environment,
        )
        cache_parent = tmp_path / ".ci-cache"
        assert cache_parent.is_dir()
        assert not cache_parent.is_symlink()
        assert stat.S_IMODE(os.lstat(cache_parent).st_mode) == 0o700
        assert not (cache_parent / "docling-artifacts").exists()

    artifact_root = tmp_path / ".ci-cache/docling-artifacts"
    artifact_root.mkdir()
    environment["MODEL_CACHE_HIT"] = "true"
    subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", bootstrap["run"]],
        check=True,
        cwd=tmp_path,
        env=environment,
    )
    assert artifact_root.is_dir()

    unsafe_workspace = tmp_path / "unsafe-workspace"
    unsafe_workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (unsafe_workspace / ".ci-cache").symlink_to(outside, target_is_directory=True)
    unsafe_environment = {
        **environment,
        "GITHUB_WORKSPACE": str(unsafe_workspace),
        "MV_DOCUMENT__DOCLING_ARTIFACTS_PATH": str(
            unsafe_workspace / ".ci-cache/docling-artifacts"
        ),
        "MODEL_CACHE_HIT": "false",
    }
    rejected = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", bootstrap["run"]],
        check=False,
        cwd=unsafe_workspace,
        env=unsafe_environment,
    )
    assert rejected.returncode != 0
    assert list(outside.iterdir()) == []


def test_docling_doctor_checks_path_and_artifacts_after_components_match(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        docling_adapter,
        "_installed_versions",
        lambda: dict(DOCLING_COMPONENT_VERSIONS),
    )
    unconfigured = doctor_docling(None)
    assert unconfigured.ok is False
    assert "No Docling artifacts path" in unconfigured.message

    missing = doctor_docling(tmp_path / "missing")
    assert missing.ok is False
    assert "not a directory" in missing.message

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    incomplete = doctor_docling(artifacts)
    assert incomplete.ok is False
    assert "missing docling-project--docling-layout-heron/model.safetensors" in (
        incomplete.message
    )


def test_document_doctor_cli_is_read_only_and_actionable(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    pypdf = runner.invoke(app, ["document", "doctor", "--parser", "pypdf"])
    assert pypdf.exit_code == 0
    assert "ok: pypdf" in pypdf.stdout

    invalid = runner.invoke(app, ["document", "doctor", "--parser", "other"])
    assert invalid.exit_code == 2
    assert "must be one of pypdf, docling" in invalid.output

    monkeypatch.setenv("MV_DOCUMENT__DOCLING_ARTIFACTS_PATH", str(tmp_path / "missing"))
    missing = runner.invoke(app, ["document", "doctor", "--parser", "docling"])
    assert missing.exit_code == 1
    assert "pdf-layout" in missing.output or "not a directory" in missing.output
    assert "Traceback" not in missing.output
    assert not (tmp_path / "missing").exists()

    monkeypatch.setattr(
        "mastervault.cli.document.doctor_docling",
        lambda _path: DoclingDoctorReport(
            ok=True,
            message="ready",
            component_versions={"docling-slim": "2.118.0"},
            model_identity="sha256:" + "a" * 64,
            artifact_bytes=317123044,
        ),
    )
    ready = runner.invoke(app, ["document", "doctor", "--parser", "docling"])
    assert ready.exit_code == 0
    assert "components: docling-slim=2.118.0" in ready.stdout
    assert "verified runtime artifact bytes: 317123044" in ready.stdout
