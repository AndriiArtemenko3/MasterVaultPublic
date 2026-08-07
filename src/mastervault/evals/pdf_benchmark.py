"""Evaluator-only gold contracts for PDF layout and temporal change impact."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from mastervault.document_intelligence.benchmark import (
    ID_PATTERN,
    REQUIRED_VARIANTS,
    DocumentAuthority,
    DocumentRole,
    StrictBenchmarkModel,
    repo_relative_path,
)

ANCHOR_PATTERN = r"^anchor\.(?:block|table|cell)\.[a-f0-9]{16}(?:\.r\d+\.c\d+)?$"
REQUIRED_PAIR_CLASSIFICATIONS = {"SUPERSEDES", "CONTRADICTS", "COEXISTS", "UNRELATED"}


class TruthTargetType(StrEnum):
    BLOCK = "block"
    CELL = "cell"


class PairClassification(StrEnum):
    SUPERSEDES = "SUPERSEDES"
    CONTRADICTS = "CONTRADICTS"
    COEXISTS = "COEXISTS"
    UNRELATED = "UNRELATED"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ExpectedClaim(StrictBenchmarkModel):
    expected_claim_id: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class TruthBlock(StrictBenchmarkModel):
    semantic_anchor: str = Field(pattern=ANCHOR_PATTERN)
    block_type: Literal["title", "heading", "paragraph", "list-item", "table"]
    reading_order: int = Field(ge=1)
    page_numbers: list[int] = Field(min_length=1)
    normalized_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    heading_level: int | None = Field(default=None, ge=1, le=6)

    @field_validator("page_numbers")
    @classmethod
    def _ordered_pages(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values) or values != sorted(set(values)):
            raise ValueError("page_numbers must be sorted, unique, and one-based")
        return values

    @model_validator(mode="after")
    def _heading_shape(self) -> TruthBlock:
        if (self.block_type == "heading") != (self.heading_level is not None):
            raise ValueError("only headings must carry heading_level")
        return self


class TruthCell(StrictBenchmarkModel):
    semantic_anchor: str = Field(pattern=ANCHOR_PATTERN)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    text: str
    column_header: bool = False


class TruthTable(StrictBenchmarkModel):
    semantic_anchor: str = Field(pattern=ANCHOR_PATTERN)
    block_anchor: str = Field(pattern=ANCHOR_PATTERN)
    reading_order: int = Field(ge=1)
    page_numbers: list[int] = Field(min_length=1)
    num_rows: int = Field(ge=1)
    num_columns: int = Field(ge=1)
    cells: list[TruthCell] = Field(min_length=1)

    @model_validator(mode="after")
    def _grid(self) -> TruthTable:
        coordinates = [(cell.row_index, cell.column_index) for cell in self.cells]
        expected = [
            (row, column) for row in range(self.num_rows) for column in range(self.num_columns)
        ]
        if coordinates != expected:
            raise ValueError("truth cells must cover the rectangular grid in row order")
        return self


class ClaimEvidenceTruth(StrictBenchmarkModel):
    expected_claim_id: str = Field(pattern=ID_PATTERN)
    target_type: TruthTargetType
    semantic_anchor: str = Field(pattern=ANCHOR_PATTERN)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)


class RenditionGroundTruth(StrictBenchmarkModel):
    asset_id: str = Field(pattern=ID_PATTERN)
    variant_id: str = Field(pattern=ID_PATTERN)
    blocks: list[TruthBlock] = Field(min_length=1)
    tables: list[TruthTable] = Field(default_factory=list)
    claim_evidence: list[ClaimEvidenceTruth] = Field(min_length=1)
    expected_furniture: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> RenditionGroundTruth:
        anchors = [block.semantic_anchor for block in self.blocks]
        if len(anchors) != len(set(anchors)):
            raise ValueError("truth block semantic anchors must be unique")
        if [block.reading_order for block in self.blocks] != list(range(1, len(self.blocks) + 1)):
            raise ValueError("truth block reading_order must be contiguous")
        table_anchors = [table.semantic_anchor for table in self.tables]
        cell_anchors = [cell.semantic_anchor for table in self.tables for cell in table.cells]
        if len(table_anchors) != len(set(table_anchors)) or len(cell_anchors) != len(
            set(cell_anchors)
        ):
            raise ValueError("table and cell semantic anchors must be unique")
        table_blocks = {table.block_anchor for table in self.tables}
        declared = {block.semantic_anchor for block in self.blocks if block.block_type == "table"}
        if table_blocks != declared:
            raise ValueError("every table block must have exactly one truth table")
        target_pages = {block.semantic_anchor: block.page_numbers for block in self.blocks}
        for table in self.tables:
            target_pages.update({cell.semantic_anchor: table.page_numbers for cell in table.cells})
        for evidence in self.claim_evidence:
            if evidence.semantic_anchor not in target_pages:
                raise ValueError("claim evidence targets an unknown semantic anchor")
            if evidence.page_number not in target_pages[evidence.semantic_anchor]:
                raise ValueError("claim evidence page does not match its semantic anchor")
        return self


class LayoutDocumentTruth(StrictBenchmarkModel):
    semantic_document_id: str = Field(pattern=ID_PATTERN)
    expected_claims: list[ExpectedClaim] = Field(min_length=1)
    renditions: list[RenditionGroundTruth] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _coverage(self) -> LayoutDocumentTruth:
        if {rendition.variant_id for rendition in self.renditions} != REQUIRED_VARIANTS:
            raise ValueError("layout gold must cover every variant")
        claim_ids = {claim.expected_claim_id for claim in self.expected_claims}
        for rendition in self.renditions:
            if {evidence.expected_claim_id for evidence in rendition.claim_evidence} != claim_ids:
                raise ValueError("each rendition must ground every expected claim")
        return self


class PdfLayoutGoldenSet(StrictBenchmarkModel):
    schema_version: Literal[1] = 1
    dataset_id: Literal["larkstead-pdf-layout-benchmark"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    documents: list[LayoutDocumentTruth] = Field(min_length=6, max_length=6)


class ChangeDocument(StrictBenchmarkModel):
    document_id: str = Field(pattern=ID_PATTERN)
    source_path: str
    version_id: str = Field(pattern=ID_PATTERN)
    effective_from: date
    effective_to: date | None = None
    document_role: DocumentRole
    authority: DocumentAuthority

    @field_validator("source_path")
    @classmethod
    def _source_path(cls, value: str) -> str:
        return repo_relative_path(value)

    @model_validator(mode="after")
    def _date_range(self) -> ChangeDocument:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self


class TemporalPhase(StrictBenchmarkModel):
    phase_id: str = Field(pattern=ID_PATTERN)
    governing_document_id: str = Field(pattern=ID_PATTERN)
    announced_on: date
    effective_from: date
    effective_to: date | None = None
    return_window_days: Literal[30, 45]
    status: Literal["superseded", "temporary", "permanent"]

    @model_validator(mode="after")
    def _date_range(self) -> TemporalPhase:
        if self.announced_on > self.effective_from:
            raise ValueError("a phase cannot be announced after it becomes effective")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self


class ExpectedDependency(StrictBenchmarkModel):
    upstream_document_id: str = Field(pattern=ID_PATTERN)
    downstream_document_id: str = Field(pattern=ID_PATTERN)
    edge_label: Literal["DEPENDS_ON"] = "DEPENDS_ON"
    dependency_type: Literal["quotes", "implements", "summarizes", "historical-reference"]
    evidence_quote: str = Field(min_length=1)


class ExpectedPairClassification(StrictBenchmarkModel):
    pair_id: str = Field(pattern=ID_PATTERN)
    source_document_id: str = Field(pattern=ID_PATTERN)
    target_document_id: str = Field(pattern=ID_PATTERN)
    pair_scope: Literal["claim"] = "claim"
    classification: PairClassification
    edge_label: Literal["SUPERSEDES", "CONTRADICTS"] | None
    source_quote: str = Field(min_length=1)
    target_quote: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _edge_semantics(self) -> ExpectedPairClassification:
        expected_edge = (
            self.classification.value
            if self.classification
            in {PairClassification.SUPERSEDES, PairClassification.CONTRADICTS}
            else None
        )
        if self.edge_label != expected_edge:
            raise ValueError(
                "SUPERSEDES/CONTRADICTS persist matching edges; "
                "COEXISTS/UNRELATED are no-edge pair dispositions"
            )
        return self


class ExpectedPatch(StrictBenchmarkModel):
    patch_id: str = Field(pattern=ID_PATTERN)
    before: str = Field(min_length=1)
    proposed_after: str = Field(min_length=1)
    expected_after: str = Field(min_length=1)
    grounding_document_id: str = Field(pattern=ID_PATTERN)
    grounding_quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _actual_change(self) -> ExpectedPatch:
        if self.before in {self.proposed_after, self.expected_after}:
            raise ValueError("patch replacements must differ from original text")
        return self


class ExpectedImpact(StrictBenchmarkModel):
    target_document_id: str = Field(pattern=ID_PATTERN)
    affected: bool
    reason: str = Field(min_length=1)
    expected_review_decision: ReviewDecision
    patches: list[ExpectedPatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _patch_shape(self) -> ExpectedImpact:
        if self.affected != bool(self.patches):
            raise ValueError("affected documents require patches; unaffected documents forbid them")
        if not self.affected and self.expected_review_decision != ReviewDecision.REJECT:
            raise ValueError("an unaffected proposal must be rejected")
        if self.expected_review_decision == ReviewDecision.APPROVE and any(
            patch.proposed_after != patch.expected_after for patch in self.patches
        ):
            raise ValueError("approved patches must apply exactly as proposed")
        if self.expected_review_decision == ReviewDecision.EDIT and not any(
            patch.proposed_after != patch.expected_after for patch in self.patches
        ):
            raise ValueError("edited impact must alter at least one proposal")
        return self


class ChangeImpactGoldenSet(StrictBenchmarkModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=ID_PATTERN)
    storyline: Literal["SL2"]
    title: str = Field(min_length=1)
    trigger_document_id: str = Field(pattern=ID_PATTERN)
    documents: list[ChangeDocument] = Field(min_length=2)
    temporal_phases: list[TemporalPhase] = Field(min_length=3)
    dependencies: list[ExpectedDependency] = Field(default_factory=list)
    expected_pair_classifications: list[ExpectedPairClassification] = Field(min_length=4)
    expected_affected_document_ids: list[str]
    expected_impacts: list[ExpectedImpact] = Field(min_length=1)

    @model_validator(mode="after")
    def _closed_world(self) -> ChangeImpactGoldenSet:
        document_ids = [document.document_id for document in self.documents]
        known = set(document_ids)
        if len(document_ids) != len(known) or self.trigger_document_id not in known:
            raise ValueError("event documents must be unique and include the trigger")
        if any(phase.governing_document_id not in known for phase in self.temporal_phases):
            raise ValueError("temporal phase references an unknown document")
        documents = {document.document_id: document for document in self.documents}
        phases = {phase.phase_id: phase for phase in self.temporal_phases}
        original = phases.get("original-policy-window")
        exception = phases.get("holiday-exception")
        permanent = phases.get("permanent-policy-v2")
        version_one = documents.get("sl2-policy-returns-v1")
        if (
            self.trigger_document_id != "sl2-policy-returns-v2"
            or version_one is None
            or version_one.effective_from != date(2024, 1, 15)
            or version_one.effective_to != date(2026, 1, 11)
            or original is None
            or original.governing_document_id != "sl2-policy-returns-v1"
            or original.announced_on != date(2024, 1, 15)
            or original.effective_from != date(2024, 1, 15)
            or original.effective_to != date(2025, 11, 2)
            or original.return_window_days != 30
            or original.status != "superseded"
            or exception is None
            or exception.governing_document_id != "sl2-memo-holiday-exception"
            or permanent is None
            or exception.announced_on != date(2025, 10, 27)
            or exception.effective_from != date(2025, 11, 3)
            or exception.effective_to != date(2026, 1, 11)
            or exception.return_window_days != 45
            or exception.status != "temporary"
            or permanent.governing_document_id != "sl2-policy-returns-v2"
            or permanent.effective_from != date(2026, 1, 12)
            or permanent.return_window_days != 45
            or permanent.status != "permanent"
        ):
            raise ValueError("SL2 must preserve v1, temporary exception, then permanent v2")
        if any(
            {dependency.upstream_document_id, dependency.downstream_document_id} - known
            for dependency in self.dependencies
        ):
            raise ValueError("dependency references an unknown document")
        classifications = {pair.classification.value for pair in self.expected_pair_classifications}
        if classifications != REQUIRED_PAIR_CLASSIFICATIONS:
            raise ValueError("seed event must cover all four pair classifications")
        if any(
            {pair.source_document_id, pair.target_document_id} - known
            for pair in self.expected_pair_classifications
        ):
            raise ValueError("pair classification references an unknown document")
        impact_ids = [impact.target_document_id for impact in self.expected_impacts]
        if len(impact_ids) != len(set(impact_ids)) or set(impact_ids) - known:
            raise ValueError("impact targets must be unique known documents")
        affected = sorted(
            impact.target_document_id for impact in self.expected_impacts if impact.affected
        )
        if sorted(self.expected_affected_document_ids) != affected:
            raise ValueError("expected affected IDs must match impact rows")
        if {impact.expected_review_decision for impact in self.expected_impacts} != {
            ReviewDecision.APPROVE,
            ReviewDecision.EDIT,
            ReviewDecision.REJECT,
        }:
            raise ValueError("seed event must exercise approve, edit, and reject")
        return self


def load_pdf_layout_golden(path: Path) -> PdfLayoutGoldenSet:
    return PdfLayoutGoldenSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_change_impact_golden(path: Path) -> ChangeImpactGoldenSet:
    return ChangeImpactGoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def canonical_golden_json_bytes(golden: PdfLayoutGoldenSet) -> bytes:
    payload = golden.model_dump(mode="json", exclude_none=False)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
