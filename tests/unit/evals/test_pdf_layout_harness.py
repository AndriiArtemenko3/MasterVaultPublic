from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.cli.app import app
from mastervault.core.errors import UnreadableDocument
from mastervault.document_intelligence.benchmark import BenchmarkSplit
from mastervault.document_intelligence.models import (
    DocumentBlockType,
    DocumentBlockV2,
    DocumentResourceLimits,
    DocumentSectionV2,
    DocumentTableV2,
    FurnitureKind,
    NormalizedBBox,
    PageDimensions,
    ParsedDocumentV2,
    ParsedPageV2,
    TableCellV2,
    TableRowV2,
)
from mastervault.evals.pdf_benchmark import (
    ClaimEvidenceTruth,
    RenditionGroundTruth,
    TruthBlock,
    TruthCell,
    TruthTable,
    TruthTargetType,
)
from mastervault.evals.pdf_layout_harness import (
    PdfLayoutEvalError,
    normalize_layout_text,
    run_pdf_layout_benchmark,
    score_rendition,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "datasets/larkstead/pdf/manifest.json"
GOLDEN_PATH = REPO_ROOT / "datasets/larkstead/golden/pdf_layout.json"
BOX = NormalizedBBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2)


def _sha(value: str) -> str:
    return hashlib.sha256(normalize_layout_text(value).encode()).hexdigest()


def _perfect_fixture() -> tuple[RenditionGroundTruth, ParsedDocumentV2]:
    blocks = [
        DocumentBlockV2(
            block_id="block-0001",
            block_type=DocumentBlockType.HEADING,
            page_number=1,
            reading_order=1,
            text="Returns policy",
            bbox=BOX,
            section_id="section-0001",
        ),
        DocumentBlockV2(
            block_id="block-0002",
            block_type=DocumentBlockType.PARAGRAPH,
            page_number=1,
            reading_order=2,
            text="Returns are accepted.",
            bbox=BOX,
            section_id="section-0001",
        ),
        DocumentBlockV2(
            block_id="block-0003",
            block_type=DocumentBlockType.TABLE,
            page_number=1,
            reading_order=3,
            text="Returns table",
            bbox=BOX,
            section_id="section-0001",
            table_id="table-0001",
        ),
        DocumentBlockV2(
            block_id="block-0004",
            block_type=DocumentBlockType.HEADER,
            page_number=1,
            reading_order=4,
            text="Confidential",
            bbox=BOX,
            section_id="section-0001",
            furniture=FurnitureKind.HEADER,
        ),
    ]
    cells = [
        TableCellV2(
            cell_id="cell-0001",
            row_id="row-0001",
            row_index=0,
            column_index=0,
            row_span=1,
            column_span=1,
            text="Rule",
            column_header=True,
            bbox=BOX,
        ),
        TableCellV2(
            cell_id="cell-0002",
            row_id="row-0001",
            row_index=0,
            column_index=1,
            row_span=1,
            column_span=1,
            text="Window",
            column_header=True,
            bbox=BOX,
        ),
        TableCellV2(
            cell_id="cell-0003",
            row_id="row-0002",
            row_index=1,
            column_index=0,
            row_span=1,
            column_span=1,
            text="Standard",
            bbox=BOX,
        ),
        TableCellV2(
            cell_id="cell-0004",
            row_id="row-0002",
            row_index=1,
            column_index=1,
            row_span=1,
            column_span=1,
            text="45 days",
            bbox=BOX,
        ),
    ]
    table = DocumentTableV2(
        table_id="table-0001",
        block_id="block-0003",
        page_number=1,
        bbox=BOX,
        num_rows=2,
        num_columns=2,
        rows=[
            TableRowV2(row_id="row-0001", row_index=0, cell_ids=["cell-0001", "cell-0002"]),
            TableRowV2(row_id="row-0002", row_index=1, cell_ids=["cell-0003", "cell-0004"]),
        ],
        cells=cells,
    )
    parsed = ParsedDocumentV2(
        asset_sha256="a" * 64,
        parser_version="test",
        parser_core_version="test",
        model_identity="test",
        pages=[
            ParsedPageV2(
                page_number=1,
                dimensions=PageDimensions(width_points=600.0, height_points=800.0),
                block_ids=[block.block_id for block in blocks],
            )
        ],
        sections=[
            DocumentSectionV2(
                section_id="section-0001",
                title="Returns policy",
                level=2,
                reading_order=1,
            )
        ],
        blocks=blocks,
        tables=[table],
    )
    truth_blocks = [
        TruthBlock(
            semantic_anchor="anchor.block.0000000000000001",
            block_type="heading",
            reading_order=1,
            page_numbers=[1],
            normalized_text_sha256=_sha("Returns policy"),
            heading_level=2,
        ),
        TruthBlock(
            semantic_anchor="anchor.block.0000000000000002",
            block_type="paragraph",
            reading_order=2,
            page_numbers=[1],
            normalized_text_sha256=_sha("Returns are accepted."),
        ),
        TruthBlock(
            semantic_anchor="anchor.block.0000000000000003",
            block_type="table",
            reading_order=3,
            page_numbers=[1],
            normalized_text_sha256=_sha("Returns table"),
        ),
    ]
    truth_cells = [
        TruthCell(
            semantic_anchor=f"anchor.cell.0000000000000004.r{row}.c{column}",
            row_index=row,
            column_index=column,
            text=text,
            column_header=row == 0,
        )
        for row, column, text in [
            (0, 0, "Rule"),
            (0, 1, "Window"),
            (1, 0, "Standard"),
            (1, 1, "45 days"),
        ]
    ]
    truth = RenditionGroundTruth(
        asset_id="fixture.single-column",
        variant_id="single-column",
        blocks=truth_blocks,
        tables=[
            TruthTable(
                semantic_anchor="anchor.table.0000000000000005",
                block_anchor="anchor.block.0000000000000003",
                reading_order=3,
                page_numbers=[1],
                num_rows=2,
                num_columns=2,
                cells=truth_cells,
            )
        ],
        claim_evidence=[
            ClaimEvidenceTruth(
                expected_claim_id="returns-window",
                target_type=TruthTargetType.CELL,
                semantic_anchor=truth_cells[-1].semantic_anchor,
                page_number=1,
                quote="45 days",
            )
        ],
        expected_furniture=["Confidential"],
    )
    return truth, parsed


def test_perfect_schema_v2_fixture_scores_every_applicable_metric() -> None:
    truth, parsed = _perfect_fixture()
    counts, ambiguity, limitations = score_rendition(truth, parsed)

    assert limitations == []
    assert ambiguity["block_matches"] == 0
    assert ambiguity["cell_matches"] == 0
    assert ambiguity["evidence_ambiguous_obligations"] == 0
    assert ambiguity["evidence_candidates"] == 0
    assert ambiguity["evidence_eligible_candidate_count"] == 1
    assert ambiguity["furniture_matches"] == 0
    metrics = counts.to_metrics()
    assert metrics["furniture_body_leakage_rate"]["value"] == 0.0
    assert all(
        metric["value"] == 1.0
        for name, metric in metrics.items()
        if name not in {"evidence_ambiguity_rate", "furniture_body_leakage_rate"}
    )
    assert metrics["evidence_ambiguity_rate"]["value"] == 0.0


@pytest.mark.parametrize(
    ("mutation", "metric"),
    [
        ("wrong_page", "block_page_recall"),
        ("wrong_order", "reading_order_accuracy"),
        ("missing_heading", "heading_recall"),
        ("wrong_level", "heading_level_accuracy"),
        ("missing_cell", "cell_text_recall"),
        ("wrong_coordinate", "cell_position_accuracy"),
        ("wrong_header", "cell_header_accuracy"),
        ("wrong_header", "table_exact_recall"),
        ("false_furniture", "furniture_label_precision"),
        ("missed_furniture", "furniture_token_page_recall"),
        ("wrong_granularity", "evidence_granularity_recall"),
        ("wrong_evidence_page", "evidence_granularity_recall"),
        ("wrong_evidence_page", "evidence_exact_target_recall"),
        ("wrong_target", "evidence_exact_target_recall"),
        ("furniture_table_evidence", "evidence_quote_recall"),
    ],
)
def test_targeted_defects_degrade_intended_metric(mutation: str, metric: str) -> None:
    truth, parsed = _perfect_fixture()
    blocks = list(parsed.blocks)
    tables = list(parsed.tables)
    if mutation == "wrong_page":
        blocks[1] = blocks[1].model_copy(update={"page_number": 2})
    elif mutation == "wrong_order":
        blocks[0] = blocks[0].model_copy(update={"reading_order": 2})
        blocks[1] = blocks[1].model_copy(update={"reading_order": 1})
    elif mutation == "missing_heading":
        blocks[0] = blocks[0].model_copy(update={"block_type": DocumentBlockType.PARAGRAPH})
    elif mutation == "wrong_level":
        sections = [parsed.sections[0].model_copy(update={"level": 3})]
        parsed = parsed.model_copy(update={"sections": sections})
    elif mutation == "missing_cell":
        tables[0] = tables[0].model_copy(update={"cells": tables[0].cells[:-1]})
    elif mutation == "wrong_coordinate":
        cells = list(tables[0].cells)
        cells[-1] = cells[-1].model_copy(update={"column_index": 0})
        tables[0] = tables[0].model_copy(update={"cells": cells})
    elif mutation == "wrong_header":
        cells = list(tables[0].cells)
        cells[-1] = cells[-1].model_copy(update={"column_header": True})
        tables[0] = tables[0].model_copy(update={"cells": cells})
    elif mutation == "false_furniture":
        blocks[1] = blocks[1].model_copy(update={"furniture": FurnitureKind.FOOTER})
    elif mutation == "missed_furniture":
        blocks[-1] = blocks[-1].model_copy(update={"furniture": None})
    elif mutation == "wrong_granularity":
        tables[0] = tables[0].model_copy(update={"cells": tables[0].cells[:-1]})
        blocks[1] = blocks[1].model_copy(update={"text": "Returns are accepted within 45 days."})
    elif mutation == "wrong_evidence_page":
        tables[0] = tables[0].model_copy(update={"page_number": 2})
    elif mutation == "wrong_target":
        cells = list(tables[0].cells)
        cells[-1] = cells[-1].model_copy(update={"column_index": 2})
        tables[0] = tables[0].model_copy(update={"cells": cells})
    elif mutation == "furniture_table_evidence":
        blocks[2] = blocks[2].model_copy(update={"furniture": FurnitureKind.FOOTER})
    parsed = parsed.model_copy(update={"blocks": blocks, "tables": tables})

    counts, _, _ = score_rendition(truth, parsed)

    assert counts.to_metrics()[metric]["value"] != 1.0


def test_duplicate_matches_are_deterministic_and_never_double_credit() -> None:
    truth, parsed = _perfect_fixture()
    duplicate = parsed.blocks[1].model_copy(update={"block_id": "block-0005", "reading_order": 5})
    duplicated = parsed.model_copy(update={"blocks": [*parsed.blocks, duplicate]})

    first = score_rendition(truth, duplicated)
    second = score_rendition(truth, duplicated)

    assert first[0].to_metrics() == second[0].to_metrics()
    assert first[1] == second[1]
    assert first[0].count("block_recall").numerator == len(truth.blocks) - 1
    assert first[1]["block_matches"] == 3


def test_duplicate_cell_text_does_not_inflate_coordinate_accuracy() -> None:
    truth, parsed = _perfect_fixture()
    truth_cells = list(truth.tables[0].cells)
    truth_cells[-1] = truth_cells[-1].model_copy(update={"text": "Standard"})
    truth_table = truth.tables[0].model_copy(update={"cells": truth_cells})
    truth = truth.model_copy(update={"tables": [truth_table]})
    predicted_cells = list(parsed.tables[0].cells)
    predicted_cells[-1] = predicted_cells[-1].model_copy(
        update={"text": "Standard", "column_index": 2}
    )
    predicted_table = parsed.tables[0].model_copy(update={"cells": predicted_cells})
    parsed = parsed.model_copy(update={"tables": [predicted_table]})

    counts, ambiguity, _ = score_rendition(truth, parsed)

    assert counts.count("cell_text_recall").numerator == 4
    assert counts.count("cell_text_recall").denominator == 4
    assert counts.count("cell_position_accuracy").numerator == 3
    assert ambiguity["cell_matches"] == 2


def test_duplicate_eligible_evidence_candidate_is_not_uniquely_addressable() -> None:
    truth, parsed = _perfect_fixture()
    duplicate = (
        parsed.tables[0].cells[-1].model_copy(update={"cell_id": "cell-0005", "column_index": 2})
    )
    table = parsed.tables[0].model_copy(update={"cells": [*parsed.tables[0].cells, duplicate]})
    parsed = parsed.model_copy(update={"tables": [table]})

    counts, ambiguity, _ = score_rendition(truth, parsed)

    assert counts.count("evidence_exact_target_recall").value == 1.0
    assert counts.count("evidence_unique_exact_target_recall").value == 0.0
    assert counts.count("evidence_eligible_candidates_per_case").numerator == 2
    assert counts.count("evidence_ambiguity_rate").value == 1.0
    assert ambiguity["evidence_ambiguous_obligations"] == 1


def test_manifest_page_count_drives_page_and_furniture_denominators() -> None:
    truth, parsed = _perfect_fixture()

    counts, _, _ = score_rendition(truth, parsed, expected_page_count=2)

    assert counts.count("page_count_exact").value == 0.0
    furniture = counts.count("furniture_token_page_recall")
    assert (furniture.numerator, furniture.denominator) == (1, 2)


def test_default_development_and_explicit_held_out_account_for_all_assets() -> None:
    development = run_pdf_layout_benchmark(repo_root=REPO_ROOT)
    held_out = run_pdf_layout_benchmark(
        repo_root=REPO_ROOT,
        split=BenchmarkSplit.HELD_OUT,
        allow_held_out=True,
    )

    assert len(development.renditions) == len(held_out.renditions) == 12
    assert len({item.asset_id for item in (*development.renditions, *held_out.renditions)}) == 24
    assert {item.family for item in development.renditions}.isdisjoint(
        {item.family for item in held_out.renditions}
    )
    assert all(item.status == "success" for item in (*development.renditions, *held_out.renditions))
    assert all(item.limitations for item in development.renditions)
    stable = development.stable_dict()
    assert stable["report_schema_version"] == 1
    assert stable["eval_name"] == "pdf-layout-benchmark"
    assert stable["benchmark_counts"] == {"families": 3, "renditions": 12}
    assert stable["manifest_spec_identity"]["sha256"]
    assert stable["manifest_generator_identity"]["sha256"]
    assert stable["parser_capabilities"]["native_page_text"] is True
    assert stable["parser_capabilities"]["tables"] is False
    assert "furniture_label_f1" not in stable["aggregates"]["parser"]["pypdf"]["metrics"]
    assert (
        development.stable_json_bytes()
        == run_pdf_layout_benchmark(repo_root=REPO_ROOT).stable_json_bytes()
    )


def test_held_out_requires_explicit_opt_in() -> None:
    with pytest.raises(PdfLayoutEvalError, match="explicit"):
        run_pdf_layout_benchmark(repo_root=REPO_ROOT, split=BenchmarkSplit.HELD_OUT)


def test_parse_failures_account_for_every_rendition_with_stable_codes() -> None:
    class FailingParser:
        name = "pypdf"
        parser_version = "test"
        profile = "page-text-v1"

        def parse(self, _source):
            raise RuntimeError("machine-local detail")

    report = run_pdf_layout_benchmark(
        repo_root=REPO_ROOT,
        parser_factory=lambda _name, _artifacts: FailingParser(),
    )
    aggregate = report.stable_dict()["aggregates"]["parser"]["pypdf"]

    assert len(report.renditions) == 12
    assert all(result.status == "failure" for result in report.renditions)
    assert aggregate["failure_codes"] == {"parse.runtimeerror": 12}
    assert aggregate["metrics"]["parse_success"] == {
        "numerator": 0,
        "denominator": 12,
        "value": 0.0,
    }
    for unobservable in (
        "evidence_eligible_candidates_per_case",
        "evidence_ambiguity_rate",
        "furniture_body_leakage_rate",
    ):
        assert aggregate["metrics"][unobservable] == {
            "numerator": 0,
            "denominator": 0,
            "value": None,
        }
    assert aggregate["metrics"]["furniture_token_page_recall"] == {
        "numerator": 0,
        "denominator": 32,
        "value": 0.0,
    }
    assert all("failure" not in result.stable_dict() for result in report.renditions)
    assert all(
        result["failure"] == "RuntimeError: machine-local detail"
        for result in report.to_dict()["renditions"]
    )


def test_all_failure_docling_report_retains_configured_model_identity() -> None:
    class FailingDoclingParser:
        name = "docling"
        parser_version = "test-docling"
        parser_core_version = "test-core"
        profile = "clean-digital-layout-table-v2"
        resource_limits = DocumentResourceLimits()

        def __init__(self, model_identity: str):
            self.model_identity = model_identity

        def parse(self, _source):
            raise RuntimeError("parse failed")

    def run(model_identity: str):
        return run_pdf_layout_benchmark(
            repo_root=REPO_ROOT,
            parser_name="docling",
            docling_artifacts_path="unused-offline-fixture",
            parser_factory=lambda _name, _artifacts: FailingDoclingParser(model_identity),
        )

    first = run("sha256:first")
    second = run("sha256:second")
    identity = first.renditions[0].parser_identity

    assert identity["component_version"] == "test-core"
    assert identity["model_identity"] == "sha256:first"
    assert identity["normalization_identity"]["profile"] == "mv-clean-digital-v2"
    assert identity["resource_limits"] == DocumentResourceLimits().model_dump(mode="json")
    assert first.stable_json_bytes() != second.stable_json_bytes()


def test_evaluator_defects_abort_instead_of_becoming_parser_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mastervault.evals import pdf_layout_harness

    def fail_score(*_args, **_kwargs):
        raise RuntimeError("scorer defect")

    monkeypatch.setattr(pdf_layout_harness, "score_rendition", fail_score)

    with pytest.raises(RuntimeError, match="scorer defect"):
        run_pdf_layout_benchmark(repo_root=REPO_ROOT)


def test_pdf_identity_failure_happens_before_parser_construction(tmp_path: Path) -> None:
    manifest_bytes = MANIFEST_PATH.read_bytes()
    (tmp_path / "datasets/larkstead/pdf").mkdir(parents=True)
    (tmp_path / "datasets/larkstead/golden").mkdir(parents=True)
    (tmp_path / "datasets/larkstead/pdf/manifest.json").write_bytes(manifest_bytes)
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    (tmp_path / "datasets/larkstead/golden/pdf_layout.json").write_text(json.dumps(golden))
    manifest = json.loads(manifest_bytes)
    for document in manifest["semantic_documents"]:
        if document["split"] != "development":
            continue
        for rendition in document["renditions"]:
            target = tmp_path / rendition["pdf_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / rendition["pdf_path"], target)
    first_path = tmp_path / manifest["semantic_documents"][0]["renditions"][0]["pdf_path"]
    first_path.write_bytes(first_path.read_bytes() + b"tamper")
    called = False

    def factory(_name: str, _artifacts: Path | str | None):
        nonlocal called
        called = True
        raise AssertionError("must not construct parser")

    with pytest.raises(PdfLayoutEvalError, match="byte identity mismatch"):
        run_pdf_layout_benchmark(repo_root=tmp_path, parser_factory=factory)
    assert called is False


def test_manifest_gold_identity_mismatch_happens_before_parser_construction(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(MANIFEST_PATH.read_bytes() + b"\n")
    called = False

    def factory(_name: str, _artifacts: Path | str | None):
        nonlocal called
        called = True
        raise AssertionError("must not construct parser")

    with pytest.raises(PdfLayoutEvalError, match="exact runtime manifest"):
        run_pdf_layout_benchmark(
            repo_root=REPO_ROOT,
            manifest_path=manifest_path,
            golden_path=GOLDEN_PATH,
            parser_factory=factory,
        )
    assert called is False


def test_cli_defaults_to_development_and_guards_held_out() -> None:
    runner = CliRunner()
    default = runner.invoke(app, ["pdf-eval", "--json"])
    guarded = runner.invoke(app, ["pdf-eval", "--split", "held-out"])

    assert default.exit_code == 0
    assert json.loads(default.stdout)["split"] == "development"
    assert "latency" not in json.loads(default.stdout)
    assert guarded.exit_code == 1
    assert "explicit" in guarded.stderr


def test_cli_formats_parser_setup_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mastervault.cli import evals as cli_evals

    def fail_run(**_kwargs):
        raise UnreadableDocument("verified artifacts are unavailable")

    monkeypatch.setattr(cli_evals, "run_pdf_layout_benchmark", fail_run)
    result = CliRunner().invoke(app, ["pdf-eval"])

    assert result.exit_code == 1
    assert result.stderr == "error: verified artifacts are unavailable\n"
    assert "Traceback" not in result.stdout
