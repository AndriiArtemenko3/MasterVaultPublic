#!/usr/bin/env python3
"""Generate the deterministic Larkstead clean-digital PDF fixture.

The Markdown corpus remains the semantic source of truth. This script renders
one controlled SL2 policy variant, records both source and PDF byte hashes, and
supports a non-mutating check mode suitable for CI.

    uv run python datasets/larkstead/qa/generate_pdf_fixtures.py --check
    uv run python datasets/larkstead/qa/generate_pdf_fixtures.py --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unicodedata
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from reportlab import Version as REPORTLAB_VERSION
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
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

SCRIPT_DIR = Path(__file__).resolve().parent
LARKSTEAD_DIR = SCRIPT_DIR.parent
REPO_ROOT = LARKSTEAD_DIR.parents[1]
SOURCE_PATH = LARKSTEAD_DIR / "raw/customer-support/policy/sl2-policy-returns-v2.md"
OUTPUT_DIR = LARKSTEAD_DIR / "pdf"
PDF_NAME = "sl2-policy-returns-v2-clean-digital.pdf"
MANIFEST_NAME = "manifest.json"

SOURCE_REPO_PATH = "datasets/larkstead/raw/customer-support/policy/sl2-policy-returns-v2.md"
PDF_REPO_PATH = f"datasets/larkstead/pdf/{PDF_NAME}"
GENERATOR_REPO_PATH = "datasets/larkstead/qa/generate_pdf_fixtures.py"

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


def _manifest(source_bytes: bytes, pdf_bytes: bytes) -> dict[str, Any]:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_texts = [page.extract_text() or "" for page in reader.pages]

    def normalized_page_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.strip() for line in normalized.splitlines() if line.strip())

    return {
        "schema_version": 1,
        "dataset": "larkstead",
        "description": (
            "Deterministic PDF renditions with page-level ground truth for document "
            "intelligence evaluation."
        ),
        "generator": {
            "path": GENERATOR_REPO_PATH,
            "sha256": _sha256(Path(__file__).read_bytes()),
            "reportlab_version": REPORTLAB_VERSION,
            "deterministic_pdf": True,
            "render_profile": "clean-digital-a4-v1",
        },
        "documents": [
            {
                "document_id": "sl2-policy-returns-v2",
                "document_family": "returns-policy",
                "family_id": "SL2-refund-window-change",
                "role": "parser-smoke",
                "storyline": "SL2",
                "split": "development",
                "variant": "clean-digital",
                "media_type": "application/pdf",
                "source_path": SOURCE_REPO_PATH,
                "source_sha256": _sha256(source_bytes),
                "source_bytes": len(source_bytes),
                "pdf_path": PDF_REPO_PATH,
                "pdf_sha256": _sha256(pdf_bytes),
                "pdf_bytes": len(pdf_bytes),
                "page_count": 2,
                "page_size_points": [round(value, 4) for value in A4],
                "native_text": True,
                "encrypted": False,
                "text_normalization": "NFKC; CRLF/CR to LF; strip line edges; discard blank lines",
                "pages": [
                    {
                        "page": page_number,
                        "block_id": f"page-{page_number:04d}-block-0001",
                        "block_type": "page_text",
                        "normalized_text_sha256": _sha256(
                            normalized_page_text(text).encode("utf-8")
                        ),
                    }
                    for page_number, text in enumerate(page_texts, start=1)
                ],
                "layout_features": [
                    "digital-text",
                    "single-column",
                    "metadata-table",
                    "repeated-header",
                    "page-footer",
                ],
                "golden_evidence": [
                    {
                        "evidence_id": "sl2-returns-v2-return-window",
                        "page": 1,
                        "block_id": "page-0001-block-0001",
                        "section": "1. Return window",
                        "text": (
                            "Customers may return any item within 45 days of the delivery date."
                        ),
                    },
                    {
                        "evidence_id": "sl2-returns-v2-refund-timing",
                        "page": 2,
                        "block_id": "page-0002-block-0001",
                        "section": "5. Refund timing",
                        "text": (
                            "Refunds go to the original payment method within 5 business days "
                            "of warehouse receipt."
                        ),
                    },
                    {
                        "evidence_id": "sl2-returns-v2-supersession",
                        "page": 2,
                        "block_id": "page-0002-block-0001",
                        "section": "Change note",
                        "text": "This version makes 45 days permanent policy.",
                    },
                ],
                "license": "CC-BY-4.0",
            }
        ],
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def generate_into(output_dir: Path) -> tuple[Path, Path]:
    """Generate fixture and manifest into ``output_dir`` for checks or tests."""
    source_bytes = SOURCE_PATH.read_bytes()
    source = parse_policy_source(source_bytes.decode("utf-8"))
    pdf_path = output_dir / PDF_NAME
    manifest_path = output_dir / MANIFEST_NAME
    render_pdf(source, pdf_path)
    manifest_path.write_bytes(_manifest_bytes(_manifest(source_bytes, pdf_path.read_bytes())))
    return pdf_path, manifest_path


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
        generated_pdf, generated_manifest = generate_into(Path(temp_dir))
        _atomic_copy(generated_pdf, OUTPUT_DIR / PDF_NAME)
        _atomic_copy(generated_manifest, OUTPUT_DIR / MANIFEST_NAME)
    print(f"wrote {PDF_REPO_PATH}")
    print(f"wrote datasets/larkstead/pdf/{MANIFEST_NAME}")


def check_committed_fixtures() -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="larkstead-pdf-check-") as temp_dir:
        generated_pdf, generated_manifest = generate_into(Path(temp_dir))
        for generated, committed in (
            (generated_pdf, OUTPUT_DIR / PDF_NAME),
            (generated_manifest, OUTPUT_DIR / MANIFEST_NAME),
        ):
            if not committed.exists():
                errors.append(f"missing committed fixture: {committed.relative_to(REPO_ROOT)}")
            elif generated.read_bytes() != committed.read_bytes():
                errors.append(f"fixture drift: {committed.relative_to(REPO_ROOT)}")

    declared = {PDF_NAME}
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
    print("Larkstead PDF fixtures: 1 document, 2 pages, deterministic bytes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
