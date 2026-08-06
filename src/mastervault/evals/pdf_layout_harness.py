"""Deterministic, evaluator-only scoring for the frozen PDF layout benchmark.

Gold data is loaded and used only in this module.  Runtime parsers receive one
``PdfSource`` and cannot observe labels, semantic anchors, or expected quotes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mastervault.document_intelligence.benchmark import (
    BenchmarkSplit,
    PdfBenchmarkManifest,
    PdfRenditionManifest,
    SemanticDocumentManifest,
    load_pdf_benchmark_manifest,
)
from mastervault.document_intelligence.models import (
    DOCUMENT_SCHEMA_VERSION,
    LATEST_DOCUMENT_SCHEMA_VERSION,
    NormalizationIdentity,
    ParsedDocument,
    ParsedDocumentAny,
    ParsedDocumentV2,
)
from mastervault.document_intelligence.parser import (
    DocumentParser,
    PdfSource,
    load_pdf_source,
    make_document_parser,
)
from mastervault.evals.pdf_benchmark import (
    PdfLayoutGoldenSet,
    RenditionGroundTruth,
    load_pdf_layout_golden,
)

MetricValue = dict[str, int | float | None]
ParserFactory = Callable[[str, Path | str | None], DocumentParser]


class PdfLayoutEvalError(ValueError):
    """The benchmark identity or requested evaluation is invalid."""


def normalize_layout_text(value: str) -> str:
    """Apply the benchmark's frozen Unicode/whitespace identity profile."""
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


def _text_sha(value: str) -> str:
    return hashlib.sha256(normalize_layout_text(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Count:
    numerator: int = 0
    denominator: int = 0

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    def to_dict(self) -> MetricValue:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


@dataclass
class Counts:
    values: dict[str, list[int]] = field(default_factory=dict)

    def add(self, name: str, numerator: int, denominator: int) -> None:
        current = self.values.setdefault(name, [0, 0])
        current[0] += numerator
        current[1] += denominator

    def merge(self, other: Counts) -> None:
        for name, (numerator, denominator) in other.values.items():
            self.add(name, numerator, denominator)

    def count(self, name: str) -> Count:
        numerator, denominator = self.values.get(name, [0, 0])
        return Count(numerator, denominator)

    def to_metrics(self) -> dict[str, MetricValue]:
        result = {name: self.count(name).to_dict() for name in sorted(self.values)}
        for prefix, precision_name, recall_name in (
            ("block", "block_precision", "block_recall"),
            ("heading", "heading_precision", "heading_recall"),
            ("table_detection", "table_detection_precision", "table_detection_recall"),
            ("cell_text", "cell_text_precision", "cell_text_recall"),
        ):
            result[f"{prefix}_f1"] = _f1(self.count(precision_name), self.count(recall_name))
        return result


def _f1(precision: Count, recall: Count) -> MetricValue:
    numerator = 2 * precision.numerator
    denominator = precision.denominator + recall.denominator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


@dataclass(frozen=True)
class PredictedBlock:
    identity: str
    text: str
    text_sha256: str
    block_type: str
    page_number: int
    reading_order: int
    heading_level: int | None
    furniture: str | None
    table_id: str | None


@dataclass(frozen=True)
class PredictedCell:
    identity: str
    table_id: str
    text: str
    row_index: int
    column_index: int
    column_header: bool
    page_number: int
    reading_order: int


@dataclass(frozen=True)
class PredictedTable:
    identity: str
    block_identity: str
    page_number: int
    reading_order: int
    num_rows: int
    num_columns: int
    cells: tuple[PredictedCell, ...]


@dataclass(frozen=True)
class Prediction:
    blocks: tuple[PredictedBlock, ...]
    tables: tuple[PredictedTable, ...]


@dataclass
class RenditionResult:
    parser: str
    split: str
    family: str
    semantic_document_id: str
    asset_id: str
    variant_id: str
    status: Literal["success", "failure"]
    metrics: dict[str, MetricValue]
    ambiguity: dict[str, int]
    limitations: list[str]
    parser_identity: dict[str, Any]
    failure_code: str | None = None
    failure: str | None = None
    latency_seconds: float = 0.0
    _counts: Counts = field(default_factory=Counts, repr=False)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "ambiguity": dict(sorted(self.ambiguity.items())),
            "asset_id": self.asset_id,
            "failure_code": self.failure_code,
            "family": self.family,
            "limitations": sorted(self.limitations),
            "metrics": self.metrics,
            "parser": self.parser,
            "parser_identity": self.parser_identity,
            "semantic_document_id": self.semantic_document_id,
            "split": self.split,
            "status": self.status,
            "variant_id": self.variant_id,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.stable_dict()
        payload["failure"] = self.failure
        payload["latency_seconds"] = self.latency_seconds
        return payload


@dataclass(frozen=True)
class PdfLayoutReport:
    report_schema_version: int
    eval_name: str
    dataset_id: str
    manifest_sha256: str
    golden_sha256: str
    golden_manifest_sha256: str
    manifest_spec_identity: dict[str, str]
    manifest_generator_identity: dict[str, Any]
    parser_capabilities: dict[str, bool]
    parser_resource_limits: dict[str, Any] | None
    parser: str
    split: str
    renditions: tuple[RenditionResult, ...]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "aggregates": _aggregates(self.renditions),
            "benchmark_counts": {
                "families": len({result.family for result in self.renditions}),
                "renditions": len(self.renditions),
            },
            "dataset_id": self.dataset_id,
            "eval_name": self.eval_name,
            "golden_manifest_sha256": self.golden_manifest_sha256,
            "golden_sha256": self.golden_sha256,
            "manifest_sha256": self.manifest_sha256,
            "manifest_generator_identity": self.manifest_generator_identity,
            "manifest_spec_identity": self.manifest_spec_identity,
            "parser": self.parser,
            "parser_capabilities": self.parser_capabilities,
            "parser_resource_limits": self.parser_resource_limits,
            "report_schema_version": self.report_schema_version,
            "renditions": [result.stable_dict() for result in self.renditions],
            "split": self.split,
            "selected_assets": [
                {"asset_id": result.asset_id, "pdf_sha256": result.parser_identity["asset_sha256"]}
                for result in self.renditions
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.stable_dict()
        payload["renditions"] = [result.to_dict() for result in self.renditions]
        payload["latency"] = {
            "renditions_seconds": {
                result.asset_id: result.latency_seconds for result in self.renditions
            },
            "total_seconds": sum(result.latency_seconds for result in self.renditions),
        }
        return payload

    def stable_json_bytes(self) -> bytes:
        return (
            json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()


def _prediction(document: ParsedDocumentAny) -> Prediction:
    if isinstance(document, ParsedDocument):
        blocks = tuple(
            PredictedBlock(
                identity=block.block_id,
                text=block.text,
                text_sha256=_text_sha(block.text),
                block_type=block.block_type.value,
                page_number=block.page_number,
                reading_order=sum(len(item.blocks) for item in document.pages[:page_index])
                + block.reading_order,
                heading_level=None,
                furniture=None,
                table_id=None,
            )
            for page_index, page in enumerate(document.pages)
            for block in page.blocks
        )
        return Prediction(blocks=blocks, tables=())

    section_levels = {section.section_id: section.level for section in document.sections}
    blocks = tuple(
        PredictedBlock(
            identity=block.block_id,
            text=block.text,
            text_sha256=_text_sha(block.text),
            block_type=block.block_type.value.replace("_", "-"),
            page_number=block.page_number,
            reading_order=block.reading_order,
            heading_level=(
                section_levels.get(block.section_id) if block.section_id is not None else None
            ),
            furniture=block.furniture.value if block.furniture is not None else None,
            table_id=block.table_id,
        )
        for block in document.blocks
    )
    block_orders = {block.identity: block.reading_order for block in blocks}
    tables = tuple(
        PredictedTable(
            identity=table.table_id,
            block_identity=table.block_id,
            page_number=table.page_number,
            reading_order=block_orders[table.block_id],
            num_rows=table.num_rows,
            num_columns=table.num_columns,
            cells=tuple(
                PredictedCell(
                    identity=cell.cell_id,
                    table_id=table.table_id,
                    text=cell.text,
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    column_header=cell.column_header,
                    page_number=table.page_number,
                    reading_order=block_orders[table.block_id],
                )
                for cell in table.cells
            ),
        )
        for table in document.tables
    )
    return Prediction(blocks=blocks, tables=tables)


def _one_to_one(
    truth_items: Iterable[Any],
    predicted_items: Iterable[Any],
    *,
    truth_key: Callable[[Any], Any],
    predicted_key: Callable[[Any], Any],
    truth_sort: Callable[[Any], Any],
    predicted_sort: Callable[[Any], Any],
) -> tuple[dict[str, Any], int]:
    truths: dict[Any, list[Any]] = defaultdict(list)
    predictions: dict[Any, list[Any]] = defaultdict(list)
    for item in truth_items:
        truths[truth_key(item)].append(item)
    for item in predicted_items:
        predictions[predicted_key(item)].append(item)
    matches: dict[str, Any] = {}
    ambiguous = 0
    for key in sorted(set(truths) & set(predictions), key=repr):
        truth_group = sorted(truths[key], key=truth_sort)
        prediction_group = sorted(predictions[key], key=predicted_sort)
        if len(truth_group) != 1 or len(prediction_group) != 1:
            ambiguous += len(truth_group) + len(prediction_group)
            continue
        matches[truth_group[0].semantic_anchor] = prediction_group[0]
    return matches, ambiguous


def score_rendition(
    truth: RenditionGroundTruth,
    document: ParsedDocumentAny,
    *,
    expected_page_count: int | None = None,
) -> tuple[Counts, dict[str, int], list[str]]:
    """Score one validated parser output without mutating either input."""
    prediction = _prediction(document)
    counts = Counts()
    ambiguity = {
        "block_matches": 0,
        "cell_matches": 0,
        "evidence_ambiguous_obligations": 0,
        "evidence_candidates": 0,
        "evidence_eligible_candidate_count": 0,
    }
    limitations: list[str] = []

    truth_page_count = max(page for block in truth.blocks for page in block.page_numbers)
    if expected_page_count is not None:
        if expected_page_count < truth_page_count:
            raise PdfLayoutEvalError(
                "expected page count cannot be smaller than a gold evidence page"
            )
        truth_page_count = expected_page_count
    counts.add("page_count_exact", int(len(document.pages) == truth_page_count), 1)

    semantic_blocks = [block for block in prediction.blocks if block.furniture is None]
    semantic_block_ids = {block.identity for block in semantic_blocks}
    block_matches, ambiguity["block_matches"] = _one_to_one(
        truth.blocks,
        semantic_blocks,
        truth_key=lambda item: item.normalized_text_sha256,
        predicted_key=lambda item: item.text_sha256,
        truth_sort=lambda item: (item.reading_order, item.semantic_anchor),
        predicted_sort=lambda item: (item.reading_order, item.identity),
    )
    counts.add("block_precision", len(block_matches), len(semantic_blocks))
    counts.add("block_recall", len(block_matches), len(truth.blocks))
    correct_types = sum(
        predicted.block_type
        == next(block.block_type for block in truth.blocks if block.semantic_anchor == anchor)
        for anchor, predicted in block_matches.items()
    )
    counts.add("block_type_accuracy", correct_types, len(block_matches))
    counts.add("block_type_recall", correct_types, len(truth.blocks))
    correct_pages = sum(
        pred.page_number
        in next(block.page_numbers for block in truth.blocks if block.semantic_anchor == anchor)
        for anchor, pred in block_matches.items()
    )
    counts.add(
        "block_page_accuracy",
        correct_pages,
        len(block_matches),
    )
    counts.add("block_page_recall", correct_pages, len(truth.blocks))

    truth_headings = [block for block in truth.blocks if block.block_type == "heading"]
    predicted_headings = [block for block in semantic_blocks if block.block_type == "heading"]
    truth_heading_anchors = {block.semantic_anchor for block in truth_headings}
    matched_headings = {
        anchor: predicted
        for anchor, predicted in block_matches.items()
        if anchor in truth_heading_anchors and predicted.block_type == "heading"
    }
    counts.add("heading_precision", len(matched_headings), len(predicted_headings))
    counts.add("heading_recall", len(matched_headings), len(truth_headings))
    counts.add(
        "heading_level_accuracy",
        sum(
            predicted.heading_level
            == next(
                block.heading_level for block in truth_headings if block.semantic_anchor == anchor
            )
            for anchor, predicted in matched_headings.items()
        ),
        len(matched_headings),
    )
    correct_heading_levels = counts.count("heading_level_accuracy").numerator
    counts.add("heading_level_recall", correct_heading_levels, len(truth_headings))

    ordered_truth_matches = [
        (truth_block, block_matches[truth_block.semantic_anchor])
        for truth_block in truth.blocks
        if truth_block.semantic_anchor in block_matches
    ]
    correct_pairs = 0
    for left_index, (left_truth, left_pred) in enumerate(ordered_truth_matches):
        for right_truth, right_pred in ordered_truth_matches[left_index + 1 :]:
            correct_pairs += int(
                (left_truth.reading_order < right_truth.reading_order)
                == (left_pred.reading_order < right_pred.reading_order)
            )
    matched_pairs = len(ordered_truth_matches) * (len(ordered_truth_matches) - 1) // 2
    truth_pairs = len(truth.blocks) * (len(truth.blocks) - 1) // 2
    counts.add("reading_order_accuracy", correct_pairs, matched_pairs)
    counts.add("reading_order_pair_coverage", matched_pairs, truth_pairs)
    counts.add("reading_order_pair_recall", correct_pairs, truth_pairs)

    table_by_block = {table.block_identity: table for table in prediction.tables}
    truth_tables_by_anchor = {table.semantic_anchor: table for table in truth.tables}
    table_matches = {
        table.semantic_anchor: table_by_block[block_matches[table.block_anchor].identity]
        for table in truth.tables
        if table.block_anchor in block_matches
        and block_matches[table.block_anchor].identity in table_by_block
    }
    counts.add("table_detection_precision", len(table_matches), len(prediction.tables))
    counts.add("table_detection_recall", len(table_matches), len(truth.tables))
    table_page_correct = sum(
        predicted.page_number in truth_tables_by_anchor[anchor].page_numbers
        for anchor, predicted in table_matches.items()
    )
    counts.add("table_page_recall", table_page_correct, len(truth.tables))
    grid_correct = sum(
        (predicted.num_rows, predicted.num_columns)
        == (truth_tables_by_anchor[anchor].num_rows, truth_tables_by_anchor[anchor].num_columns)
        for anchor, predicted in table_matches.items()
    )
    counts.add("table_grid_shape_recall", grid_correct, len(truth.tables))
    cell_matches: dict[str, PredictedCell] = {}
    text_match_count = 0
    exact_table_count = 0
    for table_anchor, predicted_table in table_matches.items():
        truth_table = truth_tables_by_anchor[table_anchor]
        matches, matched_texts, ambiguous = _cell_matches(truth_table.cells, predicted_table.cells)
        ambiguity["cell_matches"] += ambiguous
        text_match_count += matched_texts
        cell_matches.update(matches)
        exact_table_count += int(
            predicted_table.page_number in truth_table.page_numbers
            and (predicted_table.num_rows, predicted_table.num_columns)
            == (truth_table.num_rows, truth_table.num_columns)
            and len(predicted_table.cells) == len(truth_table.cells)
            and len(matches) == len(truth_table.cells)
            and all(
                predicted.column_header
                == next(
                    cell.column_header
                    for cell in truth_table.cells
                    if cell.semantic_anchor == anchor
                )
                for anchor, predicted in matches.items()
            )
        )
    truth_cells = [cell for table in truth.tables for cell in table.cells]
    predicted_cells = [cell for table in prediction.tables for cell in table.cells]
    truth_cells_by_anchor = {cell.semantic_anchor: cell for cell in truth_cells}
    counts.add("cell_text_precision", text_match_count, len(predicted_cells))
    counts.add("cell_text_recall", text_match_count, len(truth_cells))
    coordinate_correct = len(cell_matches)
    counts.add("cell_position_accuracy", coordinate_correct, text_match_count)
    header_correct = sum(
        pred.column_header == truth_cells_by_anchor[anchor].column_header
        for anchor, pred in cell_matches.items()
    )
    counts.add(
        "cell_header_accuracy",
        header_correct,
        coordinate_correct,
    )
    counts.add(
        "cell_exact_recall",
        sum(
            (pred.row_index, pred.column_index)
            == (
                truth_cells_by_anchor[anchor].row_index,
                truth_cells_by_anchor[anchor].column_index,
            )
            and pred.column_header == truth_cells_by_anchor[anchor].column_header
            for anchor, pred in cell_matches.items()
        ),
        len(truth_cells),
    )
    counts.add("table_exact_recall", exact_table_count, len(truth.tables))

    predicted_to_truth_block = {
        predicted.identity: anchor for anchor, predicted in block_matches.items()
    }
    predicted_to_truth_cell = {
        predicted.identity: anchor for anchor, predicted in cell_matches.items()
    }
    evidence_recovered = evidence_page = evidence_granularity = 0
    evidence_exact = evidence_unique_exact = 0
    eligible_candidate_count = ambiguous_evidence = 0
    for expected in truth.claim_evidence:
        quote = normalize_layout_text(expected.quote)
        candidates: list[tuple[int, int, str, int, str, str | None]] = []
        for block in semantic_blocks:
            if quote in normalize_layout_text(block.text):
                candidates.append(
                    (
                        block.reading_order,
                        1,
                        block.identity,
                        block.page_number,
                        "block",
                        predicted_to_truth_block.get(block.identity),
                    )
                )
        for table in prediction.tables:
            if table.block_identity not in semantic_block_ids:
                continue
            for cell in table.cells:
                if quote in normalize_layout_text(cell.text):
                    candidates.append(
                        (
                            cell.reading_order,
                            0,
                            cell.identity,
                            cell.page_number,
                            "cell",
                            predicted_to_truth_cell.get(cell.identity),
                        )
                    )
        if len(candidates) > 1:
            ambiguity["evidence_candidates"] += 1
        if not candidates:
            continue
        evidence_recovered += 1
        evidence_page += int(any(item[3] == expected.page_number for item in candidates))
        eligible = [
            item
            for item in candidates
            if item[3] == expected.page_number and item[4] == expected.target_type.value
        ]
        eligible_candidate_count += len(eligible)
        if len(eligible) > 1:
            ambiguous_evidence += 1
            ambiguity["evidence_ambiguous_obligations"] += 1
        evidence_granularity += int(bool(eligible))
        exact_candidates = [item for item in eligible if item[5] == expected.semantic_anchor]
        evidence_exact += int(bool(exact_candidates))
        evidence_unique_exact += int(len(eligible) == 1 and len(exact_candidates) == 1)
    counts.add("evidence_quote_recall", evidence_recovered, len(truth.claim_evidence))
    counts.add("evidence_page_recall", evidence_page, len(truth.claim_evidence))
    counts.add("evidence_granularity_recall", evidence_granularity, len(truth.claim_evidence))
    counts.add("evidence_exact_target_recall", evidence_exact, len(truth.claim_evidence))
    counts.add(
        "evidence_unique_exact_target_recall",
        evidence_unique_exact,
        len(truth.claim_evidence),
    )
    counts.add(
        "evidence_eligible_candidates_per_case",
        eligible_candidate_count,
        len(truth.claim_evidence),
    )
    counts.add(
        "evidence_ambiguity_rate",
        ambiguous_evidence,
        len(truth.claim_evidence),
    )
    ambiguity["evidence_eligible_candidate_count"] = eligible_candidate_count

    expected_furniture = [
        (normalize_layout_text(value), page)
        for value in truth.expected_furniture
        for page in range(1, truth_page_count + 1)
    ]
    predicted_furniture = [
        (normalize_layout_text(block.text), block.page_number)
        for block in prediction.blocks
        if block.furniture is not None
    ]
    obligation_hits = [
        sum(
            page == predicted_page and token in predicted_text
            for predicted_text, predicted_page in predicted_furniture
        )
        for token, page in expected_furniture
    ]
    correct_labels = sum(
        any(page == expected_page and token in text for token, expected_page in expected_furniture)
        for text, page in predicted_furniture
    )
    ambiguity["furniture_matches"] = sum(hits > 1 for hits in obligation_hits)
    counts.add("furniture_label_precision", correct_labels, len(predicted_furniture))
    counts.add(
        "furniture_token_page_recall",
        sum(hits > 0 for hits in obligation_hits),
        len(expected_furniture),
    )
    semantic_by_page: dict[int, str] = defaultdict(str)
    for block in semantic_blocks:
        semantic_by_page[block.page_number] += " " + normalize_layout_text(block.text)
    leakage = sum(token in semantic_by_page[page] for token, page in expected_furniture)
    counts.add(
        "furniture_body_leakage_rate",
        leakage,
        len(expected_furniture),
    )

    counts.add("parse_success", 1, 1)
    if isinstance(document, ParsedDocument):
        limitations.append(
            "pypdf emits one page_text block per page and no headings, tables, cells, or furniture labels"
        )
    return counts, ambiguity, limitations


def _cell_matches(
    truth: Iterable[Any], predicted: Iterable[PredictedCell]
) -> tuple[dict[str, PredictedCell], int, int]:
    truth = tuple(truth)
    predicted = tuple(predicted)
    truth_groups: dict[str, list[Any]] = defaultdict(list)
    prediction_groups: dict[str, list[PredictedCell]] = defaultdict(list)
    for item in truth:
        truth_groups[normalize_layout_text(item.text)].append(item)
    for item in predicted:
        prediction_groups[normalize_layout_text(item.text)].append(item)
    text_matches = sum(
        min(len(truth_groups[key]), len(prediction_groups[key]))
        for key in truth_groups.keys() & prediction_groups.keys()
    )
    matches: dict[str, PredictedCell] = {}
    ambiguity = 0
    for key in sorted(truth_groups.keys() & prediction_groups.keys()):
        truth_group = sorted(
            truth_groups[key],
            key=lambda item: (item.row_index, item.column_index, item.semantic_anchor),
        )
        prediction_group = sorted(
            prediction_groups[key],
            key=lambda item: (item.row_index, item.column_index, item.identity),
        )
        if len(truth_group) > 1 or len(prediction_group) > 1:
            ambiguity += min(len(truth_group), len(prediction_group))
    prediction_by_coordinate = {(item.row_index, item.column_index): item for item in predicted}
    for truth_group in truth_groups.values():
        for truth_item in truth_group:
            prediction_item = prediction_by_coordinate.get(
                (truth_item.row_index, truth_item.column_index)
            )
            if prediction_item is not None and normalize_layout_text(
                prediction_item.text
            ) == normalize_layout_text(truth_item.text):
                matches[truth_item.semantic_anchor] = prediction_item
    return matches, text_matches, ambiguity


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser_identity(document: ParsedDocumentAny) -> dict[str, Any]:
    if isinstance(document, ParsedDocumentV2):
        return {
            "asset_sha256": document.asset_sha256,
            "component_version": document.parser_core_version,
            "model_identity": document.model_identity,
            "normalization_identity": document.normalization.model_dump(mode="json"),
            "parser_profile": document.parser_profile,
            "parser_version": document.parser_version,
            "resource_limits": document.resource_limits.model_dump(mode="json"),
            "schema_version": document.schema_version,
        }
    return {
        "asset_sha256": document.asset_sha256,
        "component_version": None,
        "model_identity": None,
        "normalization_identity": document.parser_profile,
        "parser_profile": document.parser_profile,
        "parser_version": document.parser_version,
        "resource_limits": None,
        "schema_version": document.schema_version,
    }


def _configured_parser_identity(parser: DocumentParser, asset_sha256: str) -> dict[str, Any]:
    """Return identities known before parsing, including on an all-failure run."""
    parser_name = getattr(parser, "name", None)
    resource_limits = getattr(parser, "resource_limits", None)
    if resource_limits is not None:
        resource_limits = resource_limits.model_dump(mode="json")
    if parser_name == "docling":
        normalization_identity: Any = NormalizationIdentity().model_dump(mode="json")
        schema_version: int | None = LATEST_DOCUMENT_SCHEMA_VERSION
    elif parser_name == "pypdf":
        normalization_identity = getattr(parser, "profile", None)
        schema_version = DOCUMENT_SCHEMA_VERSION
    else:
        normalization_identity = None
        schema_version = None
    return {
        "asset_sha256": asset_sha256,
        "component_version": getattr(parser, "parser_core_version", None),
        "model_identity": getattr(parser, "model_identity", None),
        "normalization_identity": normalization_identity,
        "parser_profile": getattr(parser, "profile", None),
        "parser_version": getattr(parser, "parser_version", None),
        "resource_limits": resource_limits,
        "schema_version": schema_version,
    }


def _failure_counts(
    truth: RenditionGroundTruth, *, expected_page_count: int | None = None
) -> Counts:
    counts = Counts()
    truth_blocks = len(truth.blocks)
    truth_headings = sum(block.block_type == "heading" for block in truth.blocks)
    truth_pairs = truth_blocks * (truth_blocks - 1) // 2
    truth_tables = len(truth.tables)
    truth_cells = sum(len(table.cells) for table in truth.tables)
    truth_evidence = len(truth.claim_evidence)
    truth_page_count = expected_page_count or max(
        page for block in truth.blocks for page in block.page_numbers
    )
    furniture_obligations = len(truth.expected_furniture) * truth_page_count
    for name, denominator in (
        ("block_precision", 0),
        ("block_recall", truth_blocks),
        ("block_type_accuracy", 0),
        ("block_type_recall", truth_blocks),
        ("block_page_accuracy", 0),
        ("block_page_recall", truth_blocks),
        ("page_count_exact", 1),
        ("heading_precision", 0),
        ("heading_recall", truth_headings),
        ("heading_level_accuracy", 0),
        ("heading_level_recall", truth_headings),
        ("reading_order_accuracy", 0),
        ("reading_order_pair_coverage", truth_pairs),
        ("reading_order_pair_recall", truth_pairs),
        ("table_detection_precision", 0),
        ("table_detection_recall", truth_tables),
        ("table_page_recall", truth_tables),
        ("table_grid_shape_recall", truth_tables),
        ("table_exact_recall", truth_tables),
        ("cell_text_precision", 0),
        ("cell_text_recall", truth_cells),
        ("cell_position_accuracy", 0),
        ("cell_header_accuracy", 0),
        ("cell_exact_recall", truth_cells),
        ("evidence_quote_recall", truth_evidence),
        ("evidence_page_recall", truth_evidence),
        ("evidence_granularity_recall", truth_evidence),
        ("evidence_exact_target_recall", truth_evidence),
        ("evidence_unique_exact_target_recall", truth_evidence),
        # Candidate density and ambiguity are unobservable when parsing fails;
        # full denominators would make failures look artificially clean.
        ("evidence_eligible_candidates_per_case", 0),
        ("evidence_ambiguity_rate", 0),
        ("furniture_label_precision", 0),
        ("furniture_token_page_recall", furniture_obligations),
        # Leakage is likewise unobservable without parser output.
        ("furniture_body_leakage_rate", 0),
        ("parse_success", 1),
    ):
        counts.add(name, 0, denominator)
    return counts


def _validate_identity(
    manifest_path: Path,
    manifest: PdfBenchmarkManifest,
    golden: PdfLayoutGoldenSet,
) -> dict[str, RenditionGroundTruth]:
    manifest_sha = _sha256(manifest_path)
    if golden.dataset_id != manifest.dataset_id or golden.manifest_sha256 != manifest_sha:
        raise PdfLayoutEvalError("layout gold does not identify the exact runtime manifest")
    manifest_assets = {
        (document.semantic_document_id, rendition.asset_id, rendition.variant_id)
        for document in manifest.semantic_documents
        for rendition in document.renditions
    }
    truth_assets = {
        (document.semantic_document_id, rendition.asset_id, rendition.variant_id)
        for document in golden.documents
        for rendition in document.renditions
    }
    if manifest_assets != truth_assets:
        raise PdfLayoutEvalError("runtime manifest and layout gold asset inventories differ")
    return {
        rendition.asset_id: rendition
        for document in golden.documents
        for rendition in document.renditions
    }


def _selected(
    manifest: PdfBenchmarkManifest, split: BenchmarkSplit
) -> list[tuple[SemanticDocumentManifest, PdfRenditionManifest]]:
    return [
        (document, rendition)
        for document in manifest.semantic_documents
        if document.split == split
        for rendition in document.renditions
    ]


def _default_parser_factory(name: str, artifacts: Path | str | None) -> DocumentParser:
    return make_document_parser(name, docling_artifacts_path=artifacts)


def run_pdf_layout_benchmark(
    *,
    repo_root: Path,
    parser_name: Literal["pypdf", "docling"] = "pypdf",
    split: BenchmarkSplit = BenchmarkSplit.DEVELOPMENT,
    allow_held_out: bool = False,
    manifest_path: Path | None = None,
    golden_path: Path | None = None,
    docling_artifacts_path: Path | str | None = None,
    parser_factory: ParserFactory = _default_parser_factory,
    clock: Callable[[], float] = time.perf_counter,
) -> PdfLayoutReport:
    """Preflight the exact selection, parse every asset once, and score it."""
    if split == BenchmarkSplit.HELD_OUT and not allow_held_out:
        raise PdfLayoutEvalError("held-out evaluation requires explicit allow_held_out=True")
    if parser_name == "docling" and docling_artifacts_path is None:
        raise PdfLayoutEvalError("Docling evaluation requires an explicit verified artifacts path")
    manifest_path = manifest_path or repo_root / "datasets/larkstead/pdf/manifest.json"
    golden_path = golden_path or repo_root / "datasets/larkstead/golden/pdf_layout.json"
    manifest = load_pdf_benchmark_manifest(manifest_path)
    golden = load_pdf_layout_golden(golden_path)
    truth_by_asset = _validate_identity(manifest_path, manifest, golden)
    selected = _selected(manifest, split)

    # Complete byte preflight happens before parser construction or any parse call.
    sources: dict[str, PdfSource] = {}
    for _, rendition in selected:
        truth = truth_by_asset[rendition.asset_id]
        highest_gold_page = max(page for block in truth.blocks for page in block.page_numbers)
        if highest_gold_page > rendition.page_count:
            raise PdfLayoutEvalError(
                f"layout gold exceeds manifest page count for {rendition.asset_id}"
            )
        source = load_pdf_source(repo_root / rendition.pdf_path)
        if len(source.data) != rendition.pdf_bytes or source.asset_sha256 != rendition.pdf_sha256:
            raise PdfLayoutEvalError(f"PDF byte identity mismatch for {rendition.asset_id}")
        sources[rendition.asset_id] = source

    parser = parser_factory(parser_name, docling_artifacts_path)
    results: list[RenditionResult] = []
    for semantic_document, rendition in selected:
        started = clock()
        try:
            parsed = parser.parse(sources[rendition.asset_id])
            if parsed.asset_sha256 != rendition.pdf_sha256:
                raise ValueError("parser output asset_sha256 differs from the verified source")
        except Exception as exc:
            counts = _failure_counts(
                truth_by_asset[rendition.asset_id],
                expected_page_count=rendition.page_count,
            )
            result = RenditionResult(
                parser=getattr(parser, "name", parser_name),
                split=split.value,
                family=semantic_document.document_family_id,
                semantic_document_id=semantic_document.semantic_document_id,
                asset_id=rendition.asset_id,
                variant_id=rendition.variant_id,
                status="failure",
                metrics=counts.to_metrics(),
                ambiguity={},
                limitations=[],
                parser_identity=_configured_parser_identity(parser, rendition.pdf_sha256),
                failure_code=f"parse.{type(exc).__name__.lower()}",
                failure=f"{type(exc).__name__}: {exc}",
                _counts=counts,
            )
        else:
            # Scorer exceptions are evaluator defects, not parser failures. Let
            # them abort the run instead of laundering them into benchmark data.
            counts, ambiguity, limitations = score_rendition(
                truth_by_asset[rendition.asset_id],
                parsed,
                expected_page_count=rendition.page_count,
            )
            result = RenditionResult(
                parser=parser.name,
                split=split.value,
                family=semantic_document.document_family_id,
                semantic_document_id=semantic_document.semantic_document_id,
                asset_id=rendition.asset_id,
                variant_id=rendition.variant_id,
                status="success",
                metrics=counts.to_metrics(),
                ambiguity=ambiguity,
                limitations=limitations,
                parser_identity=_parser_identity(parsed),
                _counts=counts,
            )
        result.latency_seconds = max(0.0, clock() - started)
        results.append(result)

    if len(results) != len(selected) or len({result.asset_id for result in results}) != len(
        selected
    ):
        raise AssertionError("each selected rendition must have exactly one result")
    successful_identity = next(
        (result.parser_identity for result in results if result.status == "success"), None
    )
    configured_resource_limits = getattr(parser, "resource_limits", None)
    parser_capabilities = {
        "block_structure": parser_name == "docling",
        "cell_structure": parser_name == "docling",
        "furniture_labels": parser_name == "docling",
        "heading_levels": parser_name == "docling",
        "native_page_text": True,
        "tables": parser_name == "docling",
    }
    return PdfLayoutReport(
        report_schema_version=1,
        eval_name="pdf-layout-benchmark",
        dataset_id=manifest.dataset_id,
        manifest_sha256=_sha256(manifest_path),
        golden_sha256=_sha256(golden_path),
        golden_manifest_sha256=golden.manifest_sha256,
        manifest_spec_identity={"path": manifest.spec_path, "sha256": manifest.spec_sha256},
        manifest_generator_identity=manifest.generator.model_dump(mode="json"),
        parser_capabilities=parser_capabilities,
        parser_resource_limits=(
            configured_resource_limits.model_dump(mode="json")
            if configured_resource_limits is not None
            else (
                successful_identity.get("resource_limits")
                if successful_identity is not None
                else None
            )
        ),
        parser=parser.name,
        split=split.value,
        renditions=tuple(results),
    )


def _aggregates(results: Iterable[RenditionResult]) -> dict[str, Any]:
    result_list = list(results)
    dimensions: dict[str, Callable[[RenditionResult], str]] = {
        "parser": lambda item: item.parser,
        "split": lambda item: item.split,
        "family": lambda item: item.family,
        "variant": lambda item: item.variant_id,
    }
    aggregates: dict[str, Any] = {}
    for dimension, key_fn in dimensions.items():
        groups: dict[str, list[RenditionResult]] = defaultdict(list)
        for result in result_list:
            groups[key_fn(result)].append(result)
        aggregates[dimension] = {
            key: _aggregate_group(group) for key, group in sorted(groups.items())
        }
    return aggregates


def _aggregate_group(results: list[RenditionResult]) -> dict[str, Any]:
    counts = Counts()
    ambiguity: dict[str, int] = defaultdict(int)
    failure_codes: dict[str, int] = defaultdict(int)
    for result in results:
        counts.merge(result._counts)
        for key, value in result.ambiguity.items():
            ambiguity[key] += value
        if result.failure_code is not None:
            failure_codes[result.failure_code] += 1
    return {
        "ambiguity": dict(sorted(ambiguity.items())),
        "failure_codes": dict(sorted(failure_codes.items())),
        "failures": sum(result.status == "failure" for result in results),
        "metrics": counts.to_metrics(),
        "renditions": len(results),
        "successes": sum(result.status == "success" for result in results),
    }
