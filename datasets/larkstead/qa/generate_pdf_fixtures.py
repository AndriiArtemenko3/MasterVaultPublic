#!/usr/bin/env python3
"""Generate the bounded deterministic Larkstead clean-digital PDF benchmark.

The Markdown corpus remains the semantic source of truth. This script renders
six existing semantic families in four layout variants, records source/render
provenance separately from evaluator-only gold, and supports a non-mutating CI
check mode.

    uv run --extra dev python datasets/larkstead/qa/generate_pdf_fixtures.py --check
    uv run --extra dev python datasets/larkstead/qa/generate_pdf_fixtures.py --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from reportlab import Version as REPORTLAB_VERSION
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from mastervault.document_intelligence.benchmark import (
    GeneratorIdentity,
    LayoutProfileSpec,
    PdfBenchmarkManifest,
    PdfRenditionManifest,
    SemanticDocumentManifest,
    SemanticDocumentSpec,
    load_pdf_benchmark_spec,
)
from mastervault.evals.pdf_benchmark import (
    ChangeImpactGoldenSet,
    ClaimEvidenceTruth,
    ExpectedClaim,
    LayoutDocumentTruth,
    PdfLayoutGoldenSet,
    RenditionGroundTruth,
    TruthBlock,
    TruthCell,
    TruthTable,
    TruthTargetType,
    canonical_golden_json_bytes,
    load_change_impact_golden,
)

SCRIPT_DIR = Path(__file__).resolve().parent
LARKSTEAD_DIR = SCRIPT_DIR.parent
REPO_ROOT = LARKSTEAD_DIR.parents[1]
SOURCE_PATH = LARKSTEAD_DIR / "raw/customer-support/policy/sl2-policy-returns-v2.md"
OUTPUT_DIR = LARKSTEAD_DIR / "pdf"
GOLDEN_DIR = LARKSTEAD_DIR / "golden"
PDF_NAME = "sl2-policy-returns-v2-clean-digital.pdf"
MANIFEST_NAME = "manifest.json"
SPEC_PATH = OUTPUT_DIR / "benchmark.yaml"
LAYOUT_GOLDEN_PATH = GOLDEN_DIR / "pdf_layout.json"
CHANGE_GOLDEN_PATH = GOLDEN_DIR / "change_impact.yaml"

SOURCE_REPO_PATH = "datasets/larkstead/raw/customer-support/policy/sl2-policy-returns-v2.md"
PDF_REPO_PATH = f"datasets/larkstead/pdf/{PDF_NAME}"
GENERATOR_REPO_PATH = "datasets/larkstead/qa/generate_pdf_fixtures.py"
SPEC_REPO_PATH = "datasets/larkstead/pdf/benchmark.yaml"
LEGACY_PDF_SHA256 = "d12dc2de2b5a9fff9bba869c80cec305e5fc3744a1559302c3bbadf147e4332e"

EXPECTED_METADATA_KEYS = (
    "Doc",
    "Effective",
    "Supersedes",
    "Owner",
    "Approved by",
    "Applies to",
)
EXPECTED_SECTIONS = (
    "1. Return window",
    "2. Condition and restocking",
    "3. Defective items",
    "4. Starting a return",
    "5. Refund timing",
    "Change note",
)

NAVY = colors.HexColor("#17324D")
TEAL = colors.HexColor("#1D6B65")
PALE_TEAL = colors.HexColor("#E8F3F1")
PALE_BLUE = colors.HexColor("#EDF3F8")
INK = colors.HexColor("#24313D")
MUTED = colors.HexColor("#617181")
RULE = colors.HexColor("#CBD5DE")
WHITE = colors.white


@dataclass(frozen=True)
class Section:
    heading: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class PolicySource:
    title: str
    metadata: tuple[tuple[str, str], ...]
    sections: tuple[Section, ...]

    def metadata_value(self, key: str) -> str:
        return dict(self.metadata)[key]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collapse(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def parse_policy_source(text: str) -> PolicySource:
    """Parse the controlled Markdown policy into renderable semantic blocks."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Policy: "):
        raise ValueError("policy source must start with 'Policy: '")

    title = lines[0].removeprefix("Policy: ").strip()
    metadata: list[tuple[str, str]] = []
    sections: list[Section] = []
    current_heading: str | None = None
    current_paragraph: list[str] = []
    current_paragraphs: list[str] = []

    def finish_paragraph() -> None:
        nonlocal current_paragraph
        paragraph = _collapse(current_paragraph)
        if paragraph:
            current_paragraphs.append(paragraph)
        current_paragraph = []

    def finish_section() -> None:
        nonlocal current_paragraphs
        finish_paragraph()
        if current_heading is not None:
            sections.append(Section(current_heading, tuple(current_paragraphs)))
        current_paragraphs = []

    for line in lines[1:]:
        if line.startswith("## "):
            finish_section()
            current_heading = line.removeprefix("## ").strip()
            continue
        if current_heading is None:
            if not line.strip():
                continue
            key, separator, value = line.partition(":")
            if not separator:
                raise ValueError(f"invalid policy metadata line: {line!r}")
            metadata.append((key.strip(), value.strip()))
            continue
        if line.strip():
            current_paragraph.append(line)
        else:
            finish_paragraph()
    finish_section()

    metadata_keys = tuple(key for key, _value in metadata)
    section_headings = tuple(section.heading for section in sections)
    if metadata_keys != EXPECTED_METADATA_KEYS:
        raise ValueError(
            f"unexpected policy metadata: expected {EXPECTED_METADATA_KEYS}, got {metadata_keys}"
        )
    if section_headings != EXPECTED_SECTIONS:
        raise ValueError(
            f"unexpected policy sections: expected {EXPECTED_SECTIONS}, got {section_headings}"
        )
    if any(not section.paragraphs for section in sections):
        raise ValueError("every policy section must contain at least one paragraph")

    return PolicySource(title=title, metadata=tuple(metadata), sections=tuple(sections))


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PolicyTitle",
            parent=sample["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=4 * mm,
        ),
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=sample["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=TEAL,
            alignment=TA_CENTER,
            spaceAfter=2 * mm,
        ),
        "page_title": ParagraphStyle(
            "PageTitle",
            parent=sample["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=NAVY,
            spaceAfter=5 * mm,
        ),
        "section": ParagraphStyle(
            "SectionHeading",
            parent=sample["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=TEAL,
            spaceBefore=3 * mm,
            spaceAfter=1.5 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=INK,
            spaceAfter=2.5 * mm,
        ),
        "change": ParagraphStyle(
            "ChangeBody",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13.5,
            textColor=INK,
            leftIndent=4 * mm,
            rightIndent=4 * mm,
            spaceBefore=2 * mm,
            spaceAfter=2 * mm,
        ),
        "meta_label": ParagraphStyle(
            "MetaLabel",
            parent=sample["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.7,
            leading=10,
            textColor=MUTED,
        ),
        "meta_value": ParagraphStyle(
            "MetaValue",
            parent=sample["Normal"],
            fontName="Helvetica",
            fontSize=8.4,
            leading=10.5,
            textColor=INK,
        ),
    }


def _page_chrome(canvas: Any, document: BaseDocTemplate) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 18 * mm, width, 18 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawString(18 * mm, height - 11.2 * mm, "LARKSTEAD GOODS CO.")
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(width - 18 * mm, height - 11.2 * mm, "CONTROLLED POLICY")

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.2)
    canvas.drawString(18 * mm, 9.5 * mm, "Internal operating policy | Effective 2026-01-12")
    canvas.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {document.page} of 2")
    canvas.restoreState()


def _metadata_table(source: PolicySource, styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            Paragraph(escape(key.upper()), styles["meta_label"]),
            Paragraph(escape(value), styles["meta_value"]),
        ]
        for key, value in source.metadata
    ]
    table = Table(rows, colWidths=(34 * mm, 120 * mm), hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.8 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8 * mm),
            ]
        )
    )
    return table


def _section_flowables(
    section: Section, styles: dict[str, ParagraphStyle]
) -> list[KeepTogether | Table]:
    heading = Paragraph(escape(section.heading), styles["section"])
    body = [Paragraph(escape(paragraph), styles["body"]) for paragraph in section.paragraphs]
    if section.heading != "Change note":
        return [KeepTogether([heading, *body])]

    callout_body = [
        Paragraph(escape(section.heading), styles["section"]),
        *[Paragraph(escape(paragraph), styles["change"]) for paragraph in section.paragraphs],
    ]
    callout = Table([[callout_body]], colWidths=(154 * mm,))
    callout.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
                ("BOX", (0, 0), (-1, -1), 0.8, TEAL),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
            ]
        )
    )
    return [callout]


def render_pdf(source: PolicySource, output_path: Path) -> None:
    """Render a byte-deterministic two-page clean-digital policy PDF."""
    rl_config.invariant = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = A4
    frame = Frame(
        22 * mm,
        19 * mm,
        width - 44 * mm,
        height - 43 * mm,
        id="policy-body",
        leftPadding=0,
        rightPadding=0,
        topPadding=3 * mm,
        bottomPadding=0,
    )
    template = PageTemplate(id="policy", frames=[frame], onPage=_page_chrome)
    document = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        pageTemplates=[template],
        title=f"Larkstead Goods Co. - {source.title}",
        author="Larkstead Goods Co.",
        subject="Synthetic SL2 returns-policy development fixture",
        creator="MasterVault deterministic Larkstead PDF generator",
        showBoundary=0,
    )
    styles = _styles()
    sections = {section.heading: section for section in source.sections}

    story: list[Any] = [
        Spacer(1, 3 * mm),
        Paragraph("CUSTOMER SUPPORT POLICY | VERSION 2", styles["eyebrow"]),
        Paragraph(escape(source.title), styles["title"]),
        _metadata_table(source, styles),
        Spacer(1, 4 * mm),
    ]
    for heading in EXPECTED_SECTIONS[:2]:
        story.extend(_section_flowables(sections[heading], styles))

    story.extend(
        [
            PageBreak(),
            Paragraph("Operational terms", styles["page_title"]),
        ]
    )
    for heading in EXPECTED_SECTIONS[2:]:
        story.extend(_section_flowables(sections[heading], styles))

    document.build(story)


PLAIN_HEADINGS = {
    "Access",
    "Change control",
    "Contributing factors",
    "Failure modes",
    "Field mapping",
    "Follow-ups",
    "Impact",
    "Reconciliation report",
    "Root cause",
    "Rules",
    "Schedule",
    "Stages",
    "Summary",
    "Timeline",
    "Verification",
    "What went poorly",
    "What went well",
}

EXPECTED_CLAIMS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "sl2-policy-returns-v2": (
        (
            "returns-window-45-days",
            "Customers may return any item within 45 days of delivery.",
            "Customers may return any item within 45 days of the delivery date.",
        ),
        (
            "returns-b2b-restocking-waiver",
            "The 10% restocking fee is waived on B2B orders of 10 or more units.",
            "The restocking fee is waived on B2B orders of 10 or more units",
        ),
    ),
    "integration-guide-shopstack-ledgerly-daily-export": (
        (
            "export-nightly-schedule",
            "The Shopstack journal export runs nightly at 02:15 Pacific.",
            "Nightly export at 02:15 Pacific.",
        ),
        (
            "export-restocking-account",
            "Withheld restocking fees map to Ledgerly account 4110.",
            "refund.restocking_fee_withheld",
        ),
    ),
    "sl5-invoice-ppf-2026-003": (
        (
            "invoice-dimensional-adjustment",
            "The invoice includes a dimensional-weight adjustment.",
            "dimensional weight adjustment",
        ),
        (
            "invoice-dispute-repeat",
            "The adjustment repeats the calculation disputed on the preceding invoice.",
            "repeats the same disputed calculation as INV-PPF-2026-002",
        ),
    ),
    "receiving-log-ostrava-lot-2026-15": (
        (
            "receiving-heron-clamps",
            "The shipment includes Heron replacement desk-clamp kits.",
            "heron desk-clamp kit (replacement)",
        ),
        (
            "receiving-total-units",
            "The packing list totals 545 units across 11.5 cartons.",
            "545 units across 11.5 cartons",
        ),
    ),
    "proposal-bluebird-9seat-2026-01": (
        (
            "bluebird-rowan-chair",
            "The proposal includes Rowan task chairs in fog gray.",
            "Rowan task chair, fog gray",
        ),
        (
            "bluebird-return-window",
            "The proposal applies the 45-day return window.",
            "Returns per the 45-day window",
        ),
    ),
    "vireo-v12-flicker-postmortem": (
        (
            "vireo-rollback-restored",
            "The staged rollback restored 592 of 618 exposed units.",
            "592 of 618 units restored by the staged rollback",
        ),
        (
            "vireo-help-update-open",
            "Updating the public Vireo help document remained not started.",
            "update the public Vireo help doc for the warranty scope",
        ),
    ),
}


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


def _anchor(kind: str, semantic_document_id: str, semantic_key: str) -> str:
    digest = _sha256(
        f"{semantic_document_id}\x00{kind}\x00{_normalized_text(semantic_key)}".encode()
    )[:16]
    return f"anchor.{kind}.{digest}"


@dataclass(frozen=True)
class SemanticCell:
    semantic_anchor: str
    row_index: int
    column_index: int
    text: str
    column_header: bool


@dataclass(frozen=True)
class SemanticTable:
    semantic_anchor: str
    rows: tuple[tuple[str, ...], ...]
    cells: tuple[SemanticCell, ...]


BlockType = Literal["title", "heading", "paragraph", "list-item", "table"]


@dataclass(frozen=True)
class SemanticBlock:
    semantic_anchor: str
    block_type: BlockType
    text: str
    reading_order: int
    heading_level: int | None = None
    table: SemanticTable | None = None


@dataclass(frozen=True)
class SemanticSource:
    semantic_document_id: str
    title: str
    blocks: tuple[SemanticBlock, ...]


def semantic_projection_sha256(source: SemanticSource) -> str:
    """Hash the normalized parsed meaning independently of Markdown/PDF bytes."""
    projection = {
        "schema_version": 1,
        "semantic_document_id": source.semantic_document_id,
        "title": _normalized_text(source.title),
        "blocks": [
            {
                "semantic_anchor": block.semantic_anchor,
                "block_type": block.block_type,
                "normalized_text": _normalized_text(block.text),
                "reading_order": block.reading_order,
                "heading_level": block.heading_level,
                "table": (
                    {
                        "semantic_anchor": block.table.semantic_anchor,
                        "rows": [
                            [_normalized_text(cell) for cell in row] for row in block.table.rows
                        ],
                        "cells": [
                            {
                                "semantic_anchor": cell.semantic_anchor,
                                "row_index": cell.row_index,
                                "column_index": cell.column_index,
                                "normalized_text": _normalized_text(cell.text),
                                "column_header": cell.column_header,
                            }
                            for cell in block.table.cells
                        ],
                    }
                    if block.table is not None
                    else None
                ),
            }
            for block in source.blocks
        ],
    }
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(canonical)


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _is_plain_heading(line: str) -> bool:
    return line.strip() in PLAIN_HEADINGS or line.strip().startswith("Example run (")


def _table_rows(lines: list[str]) -> tuple[tuple[str, ...], ...]:
    parsed = [tuple(cell.strip() for cell in line.strip().strip("|").split("|")) for line in lines]
    if len(parsed) >= 2 and _is_table_separator(lines[1]):
        parsed.pop(1)
    if not parsed or not parsed[0]:
        raise ValueError("source table must contain a header row")
    width = len(parsed[0])
    if any(len(row) != width for row in parsed):
        raise ValueError("source table must be rectangular")
    return tuple(parsed)


def parse_semantic_source(spec: SemanticDocumentSpec, text: str) -> SemanticSource:
    """Parse the corpus's controlled Markdown subset into semantic anchors."""
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise ValueError(f"empty semantic source: {spec.source_path}")
    title_line = lines[0].strip()
    title_prefix, separator, title_value = title_line.partition(":")
    title = title_value.strip() if separator and title_prefix else title_line
    raw_blocks: list[tuple[BlockType, str, int | None, tuple[tuple[str, ...], ...] | None]] = [
        ("title", title, None, None)
    ]

    index = 1
    metadata: list[tuple[str, str]] = []
    while index < len(lines) and lines[index].strip():
        key, separator, value = lines[index].partition(":")
        if not separator:
            break
        metadata.append((key.strip(), value.strip()))
        index += 1
    if metadata:
        metadata_rows = (("Field", "Value"), *tuple(metadata))
        raw_blocks.append(
            (
                "table",
                " | ".join(" | ".join(row) for row in metadata_rows),
                None,
                metadata_rows,
            )
        )

    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        line = lines[index].strip()
        if line.startswith("## "):
            raw_blocks.append(("heading", line.removeprefix("## ").strip(), 2, None))
            index += 1
            continue
        if _is_plain_heading(line):
            raw_blocks.append(("heading", line, 1, None))
            index += 1
            continue
        if line.startswith("|"):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            table_rows = _table_rows(table_lines)
            raw_blocks.append(
                (
                    "table",
                    " | ".join(" | ".join(row) for row in table_rows),
                    None,
                    table_rows,
                )
            )
            continue
        if re.match(r"^(?:[-*]|\d+\.)\s+", line):
            raw_blocks.append(("list-item", line, None, None))
            index += 1
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index].strip()
            if (
                candidate.startswith("## ")
                or candidate.startswith("|")
                or re.match(r"^(?:[-*]|\d+\.)\s+", candidate)
                or _is_plain_heading(candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        raw_blocks.append(("paragraph", " ".join(paragraph_lines), None, None))

    occurrences: defaultdict[tuple[str, str], int] = defaultdict(int)
    blocks: list[SemanticBlock] = []
    for reading_order, (block_type, block_text, heading_level, block_rows) in enumerate(
        raw_blocks, start=1
    ):
        normalized = _normalized_text(block_text)
        occurrence_key = (block_type, normalized)
        occurrence = occurrences[occurrence_key]
        occurrences[occurrence_key] += 1
        semantic_key = f"{normalized}\x00occurrence={occurrence}"
        block_anchor = _anchor("block", spec.semantic_document_id, semantic_key)
        table: SemanticTable | None = None
        if block_rows is not None:
            table_anchor = _anchor("table", spec.semantic_document_id, semantic_key)
            table_digest = table_anchor.rsplit(".", 1)[-1]
            cells = tuple(
                SemanticCell(
                    semantic_anchor=f"anchor.cell.{table_digest}.r{row_index}.c{column_index}",
                    row_index=row_index,
                    column_index=column_index,
                    text=cell_text,
                    column_header=row_index == 0,
                )
                for row_index, row in enumerate(block_rows)
                for column_index, cell_text in enumerate(row)
            )
            table = SemanticTable(table_anchor, block_rows, cells)
        blocks.append(
            SemanticBlock(
                semantic_anchor=block_anchor,
                block_type=block_type,
                text=block_text,
                reading_order=reading_order,
                heading_level=heading_level,
                table=table,
            )
        )
    return SemanticSource(spec.semantic_document_id, title, tuple(blocks))


class TrackingDocTemplate(BaseDocTemplate):
    """Capture physical pages without altering the rendered semantic anchors."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.anchor_pages: defaultdict[str, list[int]] = defaultdict(list)

    def afterFlowable(self, flowable: Any) -> None:  # noqa: N802 - ReportLab API
        anchor = getattr(flowable, "_mv_semantic_anchor", None)
        if anchor is not None and self.page not in self.anchor_pages[anchor]:
            self.anchor_pages[anchor].append(self.page)


def _benchmark_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "BenchmarkTitle",
            parent=sample["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=NAVY,
            spaceAfter=4 * mm,
        ),
        "heading1": ParagraphStyle(
            "BenchmarkHeading1",
            parent=sample["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=TEAL,
            spaceBefore=2.5 * mm,
            spaceAfter=1.2 * mm,
        ),
        "heading2": ParagraphStyle(
            "BenchmarkHeading2",
            parent=sample["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=TEAL,
            spaceBefore=2 * mm,
            spaceAfter=1 * mm,
        ),
        "body": ParagraphStyle(
            "BenchmarkBody",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=10.4,
            textColor=INK,
            spaceAfter=1.8 * mm,
        ),
        "list": ParagraphStyle(
            "BenchmarkList",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=7.6,
            leading=10,
            leftIndent=3 * mm,
            firstLineIndent=-2 * mm,
            textColor=INK,
            spaceAfter=1 * mm,
        ),
        "cell": ParagraphStyle(
            "BenchmarkCell",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=INK,
        ),
        "cell_header": ParagraphStyle(
            "BenchmarkCellHeader",
            parent=sample["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.5,
            leading=8,
            textColor=WHITE,
        ),
    }


def _benchmark_page_chrome(
    canvas: Any,
    document: BaseDocTemplate,
    *,
    semantic_document_id: str,
    profile: LayoutProfileSpec,
) -> None:
    if not profile.repeated_furniture:
        return
    width, height = document.pagesize
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 14 * mm, width, 14 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawString(16 * mm, height - 8.8 * mm, "LARKSTEAD GOODS CO.")
    canvas.drawRightString(width - 16 * mm, height - 8.8 * mm, "CONTROLLED BENCHMARK")
    canvas.setStrokeColor(RULE)
    canvas.line(16 * mm, 13 * mm, width - 16 * mm, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.8)
    canvas.drawString(16 * mm, 8 * mm, semantic_document_id)
    canvas.drawRightString(width - 16 * mm, 8 * mm, f"Page {document.page}")
    canvas.restoreState()


def _tracked_paragraph(text: str, style: ParagraphStyle, anchor: str) -> Paragraph:
    paragraph = Paragraph(escape(text), style)
    paragraph._mv_semantic_anchor = anchor
    return paragraph


def _column_widths(rows: tuple[tuple[str, ...], ...], available_width: float) -> list[float]:
    weights = [
        max(4, min(28, max(len(row[column]) for row in rows))) for column in range(len(rows[0]))
    ]
    total = sum(weights)
    return [available_width * weight / total for weight in weights]


def _tracked_table(
    block: SemanticBlock,
    styles: dict[str, ParagraphStyle],
    available_width: float,
    profile: LayoutProfileSpec,
) -> Table:
    assert block.table is not None
    rendered = [
        [
            Paragraph(
                escape(cell),
                styles["cell_header"] if row_index == 0 else styles["cell"],
            )
            for cell in row
        ]
        for row_index, row in enumerate(block.table.rows)
    ]
    table = Table(
        rendered,
        colWidths=_column_widths(block.table.rows, available_width),
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=0,
    )
    header_color = TEAL if profile.table_emphasis else NAVY
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_color),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE_BLUE]),
                ("BOX", (0, 0), (-1, -1), 0.65 if profile.table_emphasis else 0.4, RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.45 if profile.table_emphasis else 0.25, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
            ]
        )
    )
    table._mv_semantic_anchor = block.semantic_anchor
    return table


def render_benchmark_pdf(
    source: SemanticSource,
    profile: LayoutProfileSpec,
    output_path: Path,
) -> dict[str, list[int]]:
    """Render one clean-digital variant and return anchor-to-page observations."""
    rl_config.invariant = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_size = landscape(A4) if profile.landscape else A4
    width, height = page_size
    side_margin = 18 * mm
    top_margin = 18 * mm if not profile.repeated_furniture else 19 * mm
    bottom_margin = 16 * mm
    gap = 8 * mm
    usable_width = width - 2 * side_margin
    frame_width = usable_width if profile.columns == 1 else (usable_width - gap) / profile.columns
    frames = [
        Frame(
            side_margin + column * (frame_width + gap),
            bottom_margin,
            frame_width,
            height - top_margin - bottom_margin,
            id=f"column-{column + 1}",
            leftPadding=0,
            rightPadding=0,
            topPadding=2 * mm,
            bottomPadding=0,
        )
        for column in range(profile.columns)
    ]

    def chrome(canvas: Any, document: BaseDocTemplate) -> None:
        _benchmark_page_chrome(
            canvas,
            document,
            semantic_document_id=source.semantic_document_id,
            profile=profile,
        )

    template = PageTemplate(id=profile.variant_id, frames=frames, onPage=chrome)
    document = TrackingDocTemplate(
        str(output_path),
        pagesize=page_size,
        pageTemplates=[template],
        title=f"Larkstead Goods Co. - {source.title}",
        author="Larkstead Goods Co.",
        subject=f"Synthetic clean-digital benchmark: {source.semantic_document_id}",
        creator="MasterVault deterministic Larkstead PDF generator",
        showBoundary=0,
    )
    styles = _benchmark_styles()
    story: list[Any] = []
    split_before = len(source.blocks) // 2 if profile.force_page_break else -1
    for block_index, block in enumerate(source.blocks):
        if block_index == split_before:
            story.append(PageBreak())
        if block.block_type == "title":
            story.append(_tracked_paragraph(block.text, styles["title"], block.semantic_anchor))
        elif block.block_type == "heading":
            style = styles["heading1" if block.heading_level == 1 else "heading2"]
            story.append(_tracked_paragraph(block.text, style, block.semantic_anchor))
        elif block.block_type == "list-item":
            story.append(_tracked_paragraph(block.text, styles["list"], block.semantic_anchor))
        elif block.block_type == "table":
            story.append(_tracked_table(block, styles, frame_width, profile))
            story.append(Spacer(1, 2 * mm))
        else:
            story.append(_tracked_paragraph(block.text, styles["body"], block.semantic_anchor))
    document.build(story)
    return {anchor: sorted(pages) for anchor, pages in document.anchor_pages.items()}


def _page_texts(pdf_bytes: bytes) -> list[str]:
    return [
        _normalized_text(page.extract_text() or "") for page in PdfReader(BytesIO(pdf_bytes)).pages
    ]


def _locate_pages(source: SemanticSource, pdf_bytes: bytes) -> dict[str, list[int]]:
    """Resolve pages from PDF text; used as a cross-check and legacy observation."""
    pages = _page_texts(pdf_bytes)
    located: dict[str, list[int]] = {}
    for block in source.blocks:
        needles: list[str]
        if block.table is not None:
            non_header_cells = [cell.text for cell in block.table.cells if not cell.column_header]
            needles = sorted(non_header_cells, key=len, reverse=True)[:2]
        else:
            needles = [block.text]
        found = [
            page_number
            for page_number, page_text in enumerate(pages, start=1)
            if any(_normalized_text(needle) in page_text for needle in needles if needle)
        ]
        if not found and len(_normalized_text(block.text)) > 80:
            prefix = _normalized_text(block.text)[:80]
            found = [
                page_number
                for page_number, page_text in enumerate(pages, start=1)
                if prefix in page_text
            ]
        if not found:
            raise ValueError(
                f"could not locate semantic anchor {block.semantic_anchor} in rendered PDF"
            )
        located[block.semantic_anchor] = found
    return located


def _expected_claims(semantic_document_id: str) -> tuple[ExpectedClaim, ...]:
    rows = EXPECTED_CLAIMS.get(semantic_document_id)
    if rows is None:
        raise ValueError(f"missing evaluator claim contract for {semantic_document_id}")
    return tuple(
        ExpectedClaim(
            expected_claim_id=expected_claim_id,
            statement=statement,
            evidence_quote=evidence_quote,
        )
        for expected_claim_id, statement, evidence_quote in rows
    )


def _ground_truth(
    source: SemanticSource,
    asset_id: str,
    profile: LayoutProfileSpec,
    page_observations: dict[str, list[int]],
    pdf_bytes: bytes,
) -> RenditionGroundTruth:
    blocks: list[TruthBlock] = []
    tables: list[TruthTable] = []
    for block in source.blocks:
        page_numbers = page_observations[block.semantic_anchor]
        blocks.append(
            TruthBlock(
                semantic_anchor=block.semantic_anchor,
                block_type=block.block_type,
                reading_order=block.reading_order,
                page_numbers=page_numbers,
                normalized_text_sha256=_sha256(_normalized_text(block.text).encode("utf-8")),
                heading_level=block.heading_level,
            )
        )
        if block.table is not None:
            tables.append(
                TruthTable(
                    semantic_anchor=block.table.semantic_anchor,
                    block_anchor=block.semantic_anchor,
                    reading_order=block.reading_order,
                    page_numbers=page_numbers,
                    num_rows=len(block.table.rows),
                    num_columns=len(block.table.rows[0]),
                    cells=[
                        TruthCell(
                            semantic_anchor=cell.semantic_anchor,
                            row_index=cell.row_index,
                            column_index=cell.column_index,
                            text=cell.text,
                            column_header=cell.column_header,
                        )
                        for cell in block.table.cells
                    ],
                )
            )

    evidence: list[ClaimEvidenceTruth] = []
    extracted_pages = _page_texts(pdf_bytes)
    for claim in _expected_claims(source.semantic_document_id):
        normalized_quote = _normalized_text(claim.evidence_quote)
        matching_cells = [
            (block, cell)
            for block in source.blocks
            if block.table is not None
            for cell in block.table.cells
            if normalized_quote in _normalized_text(cell.text)
        ]
        matching_blocks = [
            block
            for block in source.blocks
            if block.table is None and normalized_quote in _normalized_text(block.text)
        ]
        if len(matching_cells) == 1:
            target_block, target_cell = matching_cells[0]
            target_type = TruthTargetType.CELL
            semantic_anchor = target_cell.semantic_anchor
        elif not matching_cells and len(matching_blocks) == 1:
            target_block = matching_blocks[0]
            target_type = TruthTargetType.BLOCK
            semantic_anchor = target_block.semantic_anchor
        else:
            raise ValueError(
                f"evidence quote {claim.evidence_quote!r} does not resolve uniquely in "
                f"{source.semantic_document_id}"
            )
        candidate_pages = page_observations[target_block.semantic_anchor]
        matching_pages = [
            page_number
            for page_number in candidate_pages
            if normalized_quote in extracted_pages[page_number - 1]
        ]
        if len(matching_pages) != 1:
            raise ValueError(
                f"evidence quote {claim.evidence_quote!r} does not resolve to one PDF page"
            )
        evidence.append(
            ClaimEvidenceTruth(
                expected_claim_id=claim.expected_claim_id,
                target_type=target_type,
                semantic_anchor=semantic_anchor,
                page_number=matching_pages[0],
                quote=claim.evidence_quote,
            )
        )

    legacy_policy = (
        source.semantic_document_id == "sl2-policy-returns-v2"
        and profile.variant_id == "single-column"
    )
    if legacy_policy:
        furniture = [
            "LARKSTEAD GOODS CO.",
            "CONTROLLED POLICY",
            "Internal operating policy | Effective 2026-01-12",
            "Page ",
        ]
    elif profile.repeated_furniture:
        furniture = [
            "LARKSTEAD GOODS CO.",
            "CONTROLLED BENCHMARK",
            source.semantic_document_id,
            "Page ",
        ]
    else:
        furniture = []
    return RenditionGroundTruth(
        asset_id=asset_id,
        variant_id=profile.variant_id,
        blocks=blocks,
        tables=tables,
        claim_evidence=evidence,
        expected_furniture=furniture,
    )


def _render_contract_sha256(profile: LayoutProfileSpec, *, legacy: bool) -> str:
    payload = {
        "renderer": "legacy-clean-digital-a4-v1" if legacy else "structural-clean-digital-v1",
        "profile": profile.model_dump(mode="json"),
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _pdf_name(spec: SemanticDocumentSpec, profile: LayoutProfileSpec) -> str:
    if (
        spec.semantic_document_id == "sl2-policy-returns-v2"
        and profile.variant_id == "single-column"
    ):
        return PDF_NAME
    return f"{spec.semantic_document_id}-{profile.variant_id}.pdf"


@dataclass(frozen=True)
class GeneratedBundle:
    pdf_dir: Path
    manifest_path: Path
    golden_path: Path
    pdf_paths: tuple[Path, ...]


def _canonical_manifest_bytes(manifest: PdfBenchmarkManifest) -> bytes:
    payload = manifest.model_dump(mode="json", exclude_none=False)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_change_impact_sources(golden: ChangeImpactGoldenSet) -> None:
    by_id = {document.document_id: document for document in golden.documents}
    source_text = {
        document_id: _normalized_text(
            (REPO_ROOT / document.source_path).read_text(encoding="utf-8")
        )
        for document_id, document in by_id.items()
    }
    for dependency in golden.dependencies:
        if (
            _normalized_text(dependency.evidence_quote)
            not in source_text[dependency.downstream_document_id]
        ):
            raise ValueError(f"dependency evidence missing: {dependency.evidence_quote!r}")
    for pair in golden.expected_pair_classifications:
        if _normalized_text(pair.source_quote) not in source_text[pair.source_document_id]:
            raise ValueError(f"pair source quote missing: {pair.pair_id}")
        if _normalized_text(pair.target_quote) not in source_text[pair.target_document_id]:
            raise ValueError(f"pair target quote missing: {pair.pair_id}")
    for impact in golden.expected_impacts:
        for patch in impact.patches:
            if _normalized_text(patch.before) not in source_text[impact.target_document_id]:
                raise ValueError(f"patch original missing: {patch.patch_id}")
            if (
                _normalized_text(patch.grounding_quote)
                not in source_text[patch.grounding_document_id]
            ):
                raise ValueError(f"patch grounding quote missing: {patch.patch_id}")


def generate_into(output_root: Path) -> GeneratedBundle:
    """Generate all runtime renditions and evaluator-only layout gold."""
    spec = load_pdf_benchmark_spec(SPEC_PATH)
    if set(EXPECTED_CLAIMS) != {
        document.semantic_document_id for document in spec.semantic_documents
    }:
        raise ValueError("evaluator expected-claim inventory does not match render specification")
    change_golden = load_change_impact_golden(CHANGE_GOLDEN_PATH)
    _validate_change_impact_sources(change_golden)

    pdf_dir = output_root / "pdf"
    golden_dir = output_root / "golden"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    golden_dir.mkdir(parents=True, exist_ok=True)
    profile_by_id = {profile.variant_id: profile for profile in spec.layout_profiles}

    manifest_documents: list[SemanticDocumentManifest] = []
    golden_documents: list[LayoutDocumentTruth] = []
    generated_pdfs: list[Path] = []
    total_pdf_bytes = 0

    for document_spec in spec.semantic_documents:
        source_path = REPO_ROOT / document_spec.source_path
        source_bytes = source_path.read_bytes()
        semantic_source = parse_semantic_source(document_spec, source_bytes.decode("utf-8"))
        renditions: list[PdfRenditionManifest] = []
        rendition_gold: list[RenditionGroundTruth] = []
        for variant_id in sorted(profile_by_id):
            profile = profile_by_id[variant_id]
            pdf_name = _pdf_name(document_spec, profile)
            pdf_path = pdf_dir / pdf_name
            legacy = (
                document_spec.semantic_document_id == "sl2-policy-returns-v2"
                and profile.variant_id == "single-column"
            )
            if legacy:
                legacy_source = parse_policy_source(source_bytes.decode("utf-8"))
                render_pdf(legacy_source, pdf_path)
                page_observations = _locate_pages(semantic_source, pdf_path.read_bytes())
                if _sha256(pdf_path.read_bytes()) != LEGACY_PDF_SHA256:
                    raise ValueError("legacy clean-digital fixture bytes changed")
            else:
                tracked = render_benchmark_pdf(semantic_source, profile, pdf_path)
                observed = _locate_pages(semantic_source, pdf_path.read_bytes())
                page_observations = {
                    block.semantic_anchor: tracked.get(
                        block.semantic_anchor, observed[block.semantic_anchor]
                    )
                    for block in semantic_source.blocks
                }
                if page_observations != observed:
                    raise ValueError(
                        f"tracked/rendered page disagreement for {document_spec.semantic_document_id} "
                        f"{variant_id}"
                    )

            pdf_bytes = pdf_path.read_bytes()
            reader = PdfReader(BytesIO(pdf_bytes))
            if reader.is_encrypted:
                raise ValueError(f"generated PDF is unexpectedly encrypted: {pdf_name}")

            asset_id = f"{document_spec.semantic_document_id}.{variant_id}"
            truth = _ground_truth(
                semantic_source,
                asset_id,
                profile,
                page_observations,
                pdf_bytes,
            )
            if any(len(table.page_numbers) != 1 for table in truth.tables):
                raise ValueError(f"source table spans pages in {pdf_name}")
            pdf_repo_path = f"datasets/larkstead/pdf/{pdf_name}"
            rendition = PdfRenditionManifest(
                asset_id=asset_id,
                variant_id=variant_id,
                pdf_path=pdf_repo_path,
                pdf_sha256=_sha256(pdf_bytes),
                pdf_bytes=len(pdf_bytes),
                page_count=len(reader.pages),
                page_size_points=[round(value, 4) for value in reader.pages[0].mediabox[2:]],
                render_contract_sha256=_render_contract_sha256(profile, legacy=legacy),
                layout_features=[
                    *profile.layout_features,
                    *(
                        ["repeated-header", "repeated-footer", "page-number-footer"]
                        if legacy
                        else []
                    ),
                ],
            )
            if rendition.pdf_bytes > spec.size_budget.max_single_pdf_bytes:
                raise ValueError(f"generated PDF exceeds per-asset budget: {pdf_name}")
            renditions.append(rendition)
            rendition_gold.append(truth)
            generated_pdfs.append(pdf_path)
            total_pdf_bytes += len(pdf_bytes)

        manifest_documents.append(
            SemanticDocumentManifest(
                **document_spec.model_dump(),
                source_sha256=_sha256(source_bytes),
                semantic_projection_sha256=semantic_projection_sha256(semantic_source),
                source_bytes=len(source_bytes),
                renditions=renditions,
            )
        )
        golden_documents.append(
            LayoutDocumentTruth(
                semantic_document_id=document_spec.semantic_document_id,
                expected_claims=list(_expected_claims(document_spec.semantic_document_id)),
                renditions=rendition_gold,
            )
        )

    manifest = PdfBenchmarkManifest(
        schema_version=2,
        dataset_id=spec.dataset_id,
        description=spec.description,
        license=spec.license,
        spec_path=SPEC_REPO_PATH,
        spec_sha256=_sha256(SPEC_PATH.read_bytes()),
        generator=GeneratorIdentity(
            path=GENERATOR_REPO_PATH,
            sha256=_sha256(Path(__file__).read_bytes()),
            reportlab_version=REPORTLAB_VERSION,
            deterministic_pdf=True,
            cross_platform_byte_identity_promised=False,
        ),
        size_budget=spec.size_budget,
        total_pdf_bytes=total_pdf_bytes,
        semantic_documents=manifest_documents,
    )
    manifest_path = pdf_dir / MANIFEST_NAME
    manifest_bytes = _canonical_manifest_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    layout_golden = PdfLayoutGoldenSet(
        schema_version=1,
        dataset_id=spec.dataset_id,
        manifest_sha256=_sha256(manifest_bytes),
        documents=golden_documents,
    )
    golden_path = golden_dir / LAYOUT_GOLDEN_PATH.name
    golden_path.write_bytes(canonical_golden_json_bytes(layout_golden))
    if total_pdf_bytes > spec.size_budget.max_total_pdf_bytes:
        raise ValueError("generated PDF inventory exceeds total size budget")
    return GeneratedBundle(pdf_dir, manifest_path, golden_path, tuple(generated_pdfs))


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(source.read_bytes())
        handle.flush()
    temp_path.replace(target)
    target.chmod(0o644)


def write_committed_fixtures() -> None:
    with tempfile.TemporaryDirectory(prefix="larkstead-pdf-") as temp_dir:
        generated = generate_into(Path(temp_dir))
        for generated_pdf in generated.pdf_paths:
            _atomic_copy(generated_pdf, OUTPUT_DIR / generated_pdf.name)
        _atomic_copy(generated.manifest_path, OUTPUT_DIR / MANIFEST_NAME)
        _atomic_copy(generated.golden_path, LAYOUT_GOLDEN_PATH)
    print("wrote 24 PDFs and datasets/larkstead/pdf/manifest.json")
    print("wrote evaluator-only datasets/larkstead/golden/pdf_layout.json")


def check_committed_fixtures() -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="larkstead-pdf-check-") as temp_dir:
        generated_bundle = generate_into(Path(temp_dir))
        comparisons = [
            *((path, OUTPUT_DIR / path.name) for path in generated_bundle.pdf_paths),
            (generated_bundle.manifest_path, OUTPUT_DIR / MANIFEST_NAME),
            (generated_bundle.golden_path, LAYOUT_GOLDEN_PATH),
        ]
        for generated, committed in comparisons:
            if not committed.exists():
                errors.append(f"missing committed fixture: {committed.relative_to(REPO_ROOT)}")
            elif generated.read_bytes() != committed.read_bytes():
                errors.append(f"fixture drift: {committed.relative_to(REPO_ROOT)}")

    spec = load_pdf_benchmark_spec(SPEC_PATH)
    declared = {
        _pdf_name(document, profile)
        for document in spec.semantic_documents
        for profile in spec.layout_profiles
    }
    present = {path.name for path in OUTPUT_DIR.glob("*.pdf")} if OUTPUT_DIR.exists() else set()
    if present != declared:
        errors.append(
            f"PDF inventory mismatch: expected {sorted(declared)}, found {sorted(present)}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify committed bytes (default)")
    mode.add_argument("--write", action="store_true", help="regenerate committed fixture files")
    args = parser.parse_args(argv)

    if args.write:
        write_committed_fixtures()
        return 0

    errors = check_committed_fixtures()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    manifest = PdfBenchmarkManifest.model_validate_json(
        (OUTPUT_DIR / MANIFEST_NAME).read_text(encoding="utf-8")
    )
    print(
        "Larkstead PDF benchmark: 6 families, 24 clean-digital PDFs, "
        f"{manifest.total_pdf_bytes} bytes verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
