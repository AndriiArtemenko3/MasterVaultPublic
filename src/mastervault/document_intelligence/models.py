"""Parser-independent document models for page-grounded source evidence.

The v1 schema is intentionally modest: the baseline pypdf parser preserves one
text block per page.  Layout regions, tables, and OCR metadata can be added by
future parsers without coupling the canonical vault to a parser-specific type.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

DOCUMENT_SCHEMA_VERSION = 1
LATEST_DOCUMENT_SCHEMA_VERSION = 2
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCK_ID_RE = re.compile(r"^page-(?P<page>\d{4})-block-(?P<block>\d{4})$")
V2_ID_PATTERNS = {
    "block": re.compile(r"^block-(\d{4})$"),
    "section": re.compile(r"^section-(\d{4})$"),
    "table": re.compile(r"^table-(\d{4})$"),
    "row": re.compile(r"^row-(\d{4})$"),
    "cell": re.compile(r"^cell-(\d{4})$"),
}


def _workspace_relative(value: str) -> str:
    """Validate a portable workspace-relative path stored in frontmatter."""
    raw = value.strip()
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if (
        not raw
        or "\x00" in raw
        or candidate.is_absolute()
        or Path(raw).is_absolute()
        or Path(raw).drive
        or ".." in candidate.parts
    ):
        raise ValueError(f"must be a safe workspace-relative path, got {value!r}")
    return candidate.as_posix()


class DocumentBlockType(StrEnum):
    """Closed v1 block vocabulary; pypdf emits only ``page_text``."""

    PAGE_TEXT = "page_text"
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    IMAGE = "image"
    UNKNOWN = "unknown"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentResourceLimits(_StrictModel):
    """Resource ceilings frozen into the schema-v2 parser identity."""

    timeout_seconds: float = Field(default=120.0, gt=0)
    max_source_bytes: int = Field(default=52_428_800, ge=1)
    max_pages: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def _fixed_profile(self) -> DocumentResourceLimits:
        if (
            self.timeout_seconds != 120.0
            or self.max_source_bytes != 52_428_800
            or self.max_pages != 200
        ):
            raise ValueError("resource limits must match clean-digital-layout-table-v2")
        return self


class SourceAssetRef(_StrictModel):
    """Reference to an immutable, content-addressed source PDF."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    stored_path: str
    media_type: Literal["application/pdf"] = "application/pdf"
    original_filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)

    @field_validator("stored_path")
    @classmethod
    def _safe_stored_path(cls, value: str) -> str:
        return _workspace_relative(value)

    @field_validator("original_filename")
    @classmethod
    def _plain_filename(cls, value: str) -> str:
        portable = PurePosixPath(value.replace("\\", "/"))
        if "\x00" in value or portable.name != value or value in {".", ".."}:
            raise ValueError("must be a plain filename without path components")
        return value


class ParsedDocumentRef(_StrictModel):
    """Reference to a verified, schema-versioned parsed-document artefact."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_profile: str = Field(min_length=1)
    document_schema_version: Literal[1, 2] = 1
    normalization_profile: str = "page-text-v1"
    parser_core_version: str | None = None
    model_identity: str | None = None
    resource_limits: DocumentResourceLimits | None = None
    artifact_path: str
    artifact_sha256: str = Field(pattern=SHA256_RE.pattern)

    @field_validator("artifact_path")
    @classmethod
    def _safe_artifact_path(cls, value: str) -> str:
        return _workspace_relative(value)

    @model_validator(mode="after")
    def _schema_identity(self) -> ParsedDocumentRef:
        if self.document_schema_version == 1:
            if (
                self.normalization_profile != self.parser_profile
                or self.parser_core_version is not None
                or self.model_identity is not None
                or self.resource_limits is not None
            ):
                raise ValueError("schema-v1 reference must carry only its parser profile identity")
        elif (
            self.parser != "docling"
            or self.parser_profile != "clean-digital-layout-table-v2"
            or self.normalization_profile
            not in {"mv-clean-digital-v1", "mv-clean-digital-v2"}
            or self.parser_core_version is None
            or self.model_identity is None
            or self.resource_limits is None
        ):
            raise ValueError("schema-v2 reference requires the complete Docling identity")
        return self


class DocumentBlock(_StrictModel):
    """One ordered, addressable unit of text on a page."""

    block_id: str = Field(pattern=BLOCK_ID_RE.pattern)
    block_type: DocumentBlockType
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=1)
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("block text must contain a non-whitespace character")
        return value


class ParsedPage(_StrictModel):
    """One physical PDF page, numbered from one."""

    page_number: int = Field(ge=1)
    blocks: list[DocumentBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def _block_consistency(self) -> ParsedPage:
        expected_order = list(range(1, len(self.blocks) + 1))
        actual_order = [block.reading_order for block in self.blocks]
        if actual_order != expected_order:
            raise ValueError(f"page {self.page_number}: reading_order must be contiguous from one")
        mismatched = [
            block.block_id for block in self.blocks if block.page_number != self.page_number
        ]
        if mismatched:
            raise ValueError(
                f"page {self.page_number}: blocks carry another page number: {mismatched}"
            )
        encoded_elsewhere: list[str] = []
        for block in self.blocks:
            match = BLOCK_ID_RE.fullmatch(block.block_id)
            if match is None:  # field validation already guards this path
                raise ValueError(f"invalid block id: {block.block_id!r}")
            if int(match.group("page")) != self.page_number:
                encoded_elsewhere.append(block.block_id)
        if encoded_elsewhere:
            raise ValueError(
                f"page {self.page_number}: block ids encode another page: {encoded_elsewhere}"
            )
        return self

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


class ParseWarning(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


class ParsedDocument(_StrictModel):
    """Canonical parser-neutral representation persisted as deterministic JSON."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_profile: str = Field(min_length=1)
    pages: list[ParsedPage] = Field(min_length=1)
    warnings: list[ParseWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _document_consistency(self) -> ParsedDocument:
        expected_pages = list(range(1, len(self.pages) + 1))
        actual_pages = [page.page_number for page in self.pages]
        if actual_pages != expected_pages:
            raise ValueError("page_number must be contiguous from one in physical order")
        block_ids = [block.block_id for page in self.pages for block in page.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id values must be unique within a parsed document")
        return self

    def flattened_text(self) -> str:
        """Compatibility view matching the v0.2 PDF flattening convention."""
        return "\n\n".join(page.text for page in self.pages).strip()

    def block_index(self) -> dict[str, DocumentBlock]:
        return {block.block_id: block for page in self.pages for block in page.blocks}

    def prompt_text(self) -> str:
        """Addressable blocks supplied to the page-grounded extraction prompt."""
        parts: list[str] = []
        for page in self.pages:
            for block in page.blocks:
                parts.append(f"[BLOCK {block.block_id} | PAGE {page.page_number}]\n{block.text}")
        return "\n\n".join(parts)


class EvidenceRef(_StrictModel):
    """A claim's mechanically resolved supporting span in a parsed PDF block."""

    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    page_number: int = Field(ge=1)
    block_id: str = Field(pattern=BLOCK_ID_RE.pattern)
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @field_validator("quote")
    @classmethod
    def _meaningful_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence quote must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def _ordered_offsets(self) -> EvidenceRef:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


# Schema v2 is deliberately separate from the frozen v1 types above.  In
# particular, no v2-only field is optional on ParsedDocument/ParsedPage/
# DocumentBlock, so a v1 artefact remains byte- and shape-compatible.


class NormalizedBBox(_StrictModel):
    """Page-relative top-left coordinates, quantized to six decimal places."""

    origin: Literal["top-left"] = "top-left"
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    @field_validator("x0", "y0", "x1", "y1")
    @classmethod
    def _quantized(cls, value: float) -> float:
        if round(value, 6) != value:
            raise ValueError("coordinates must be quantized to six decimal places")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> NormalizedBBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox maxima must not precede minima")
        return self


class PageDimensions(_StrictModel):
    width_points: float = Field(gt=0)
    height_points: float = Field(gt=0)

    @field_validator("width_points", "height_points")
    @classmethod
    def _quantized(cls, value: float) -> float:
        if round(value, 3) != value:
            raise ValueError("page dimensions must be quantized to three decimal places")
        return value


class FurnitureKind(StrEnum):
    HEADER = "header"
    FOOTER = "footer"


class DocumentSectionV2(_StrictModel):
    section_id: str = Field(pattern=V2_ID_PATTERNS["section"].pattern)
    title: str = Field(min_length=1)
    level: int = Field(ge=1, le=6)
    parent_section_id: str | None = Field(
        default=None, pattern=V2_ID_PATTERNS["section"].pattern
    )
    reading_order: int = Field(ge=1)


class DocumentBlockV2(_StrictModel):
    block_id: str = Field(pattern=V2_ID_PATTERNS["block"].pattern)
    block_type: DocumentBlockType
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=1)
    text: str = Field(min_length=1)
    bbox: NormalizedBBox
    section_id: str | None = Field(default=None, pattern=V2_ID_PATTERNS["section"].pattern)
    furniture: FurnitureKind | None = None
    table_id: str | None = Field(default=None, pattern=V2_ID_PATTERNS["table"].pattern)

    @field_validator("text")
    @classmethod
    def _meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("block text must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def _table_reference(self) -> DocumentBlockV2:
        if (self.block_type == DocumentBlockType.TABLE) != (self.table_id is not None):
            raise ValueError("only table blocks must carry table_id")
        return self


class TableCellV2(_StrictModel):
    cell_id: str = Field(pattern=V2_ID_PATTERNS["cell"].pattern)
    row_id: str = Field(pattern=V2_ID_PATTERNS["row"].pattern)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str
    column_header: bool = False
    row_header: bool = False
    # Docling retains explicitly empty grid cells but has no text region from
    # which to derive their coordinates.  Preserve the cell without inventing
    # a box; any cell carrying content must remain visually grounded.
    bbox: NormalizedBBox | None

    @model_validator(mode="after")
    def _ground_non_empty_cell(self) -> TableCellV2:
        if self.text.strip() and self.bbox is None:
            raise ValueError("non-empty table cells require a bbox")
        return self


class TableRowV2(_StrictModel):
    row_id: str = Field(pattern=V2_ID_PATTERNS["row"].pattern)
    row_index: int = Field(ge=0)
    # A row can have no starting cells when it is fully covered by a row span
    # originating above it.  The table occupancy validator still requires the
    # normalized grid to be non-overlapping and within bounds.
    cell_ids: list[str] = Field(default_factory=list)

    @field_validator("cell_ids")
    @classmethod
    def _cell_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("row cell_ids must be unique")
        if any(V2_ID_PATTERNS["cell"].fullmatch(value) is None for value in values):
            raise ValueError("row contains an invalid cell_id")
        return values


class DocumentTableV2(_StrictModel):
    table_id: str = Field(pattern=V2_ID_PATTERNS["table"].pattern)
    block_id: str = Field(pattern=V2_ID_PATTERNS["block"].pattern)
    page_number: int = Field(ge=1)
    bbox: NormalizedBBox
    num_rows: int = Field(ge=1)
    num_columns: int = Field(ge=1)
    rows: list[TableRowV2] = Field(min_length=1)
    cells: list[TableCellV2] = Field(min_length=1)

    @model_validator(mode="after")
    def _grid_consistency(self) -> DocumentTableV2:
        if len(self.rows) != self.num_rows:
            raise ValueError("table row count does not match num_rows")
        if [row.row_index for row in self.rows] != list(range(self.num_rows)):
            raise ValueError("table row_index values must be contiguous from zero")
        row_ids = {row.row_id for row in self.rows}
        row_by_id = {row.row_id: row for row in self.rows}
        cell_ids = {cell.cell_id for cell in self.cells}
        if len(row_ids) != len(self.rows) or len(cell_ids) != len(self.cells):
            raise ValueError("table row_id and cell_id values must be unique")
        if any(cell.row_id not in row_ids for cell in self.cells):
            raise ValueError("table cell references an unknown row_id")
        if any(
            row_by_id[cell.row_id].row_index != cell.row_index
            for cell in self.cells
            if cell.row_id in row_by_id
        ):
            raise ValueError("table cell row_id and row_index name different rows")
        if any(cell.row_index >= self.num_rows for cell in self.cells):
            raise ValueError("table cell row_index is outside the grid")
        if any(cell.column_index + cell.column_span > self.num_columns for cell in self.cells):
            raise ValueError("table cell column span is outside the grid")
        if any(cell.row_index + cell.row_span > self.num_rows for cell in self.cells):
            raise ValueError("table cell row span is outside the grid")
        expected_by_row = {row.row_id: row.cell_ids for row in self.rows}
        actual_by_row = {
            row_id: [cell.cell_id for cell in self.cells if cell.row_id == row_id]
            for row_id in row_ids
        }
        if expected_by_row != actual_by_row:
            raise ValueError("table row cell_ids do not match cells")
        coordinates = [(cell.row_index, cell.column_index) for cell in self.cells]
        if coordinates != sorted(coordinates):
            raise ValueError("table cells must be in canonical row/column order")
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for column_index in range(
                    cell.column_index, cell.column_index + cell.column_span
                ):
                    slot = (row_index, column_index)
                    if slot in occupied:
                        raise ValueError("table cells overlap in the normalized grid")
                    occupied.add(slot)
        expected_grid = {
            (row_index, column_index)
            for row_index in range(self.num_rows)
            for column_index in range(self.num_columns)
        }
        if occupied != expected_grid:
            raise ValueError("table cells do not cover the declared normalized grid")
        return self


class ParsedPageV2(_StrictModel):
    page_number: int = Field(ge=1)
    dimensions: PageDimensions
    block_ids: list[str]

    @field_validator("block_ids")
    @classmethod
    def _block_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("page block_ids must be unique")
        if any(V2_ID_PATTERNS["block"].fullmatch(value) is None for value in values):
            raise ValueError("page contains an invalid block_id")
        return values


class NormalizationIdentity(_StrictModel):
    profile: Literal["mv-clean-digital-v1", "mv-clean-digital-v2"] = (
        "mv-clean-digital-v2"
    )
    coordinate_origin: Literal["top-left"] = "top-left"
    coordinate_precision: Literal[6] = 6
    whitespace_profile: Literal["unicode-lines-v1"] = "unicode-lines-v1"
    furniture_profile: Literal["docling-labels-v1"] = "docling-labels-v1"
    table_profile: Literal["grid-v1", "grid-v2"] = "grid-v2"

    @model_validator(mode="after")
    def _matched_profile_versions(self) -> NormalizationIdentity:
        expected = {
            "mv-clean-digital-v1": "grid-v1",
            "mv-clean-digital-v2": "grid-v2",
        }
        if self.table_profile != expected[self.profile]:
            raise ValueError("normalization and table profile versions must match")
        return self


class ParsedDocumentV2(_StrictModel):
    """Strict layout/table IR owned by MasterVault, never by a parser vendor."""

    schema_version: Literal[2] = 2
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    parser: Literal["docling"] = "docling"
    parser_version: str = Field(min_length=1)
    parser_core_version: str = Field(min_length=1)
    parser_profile: Literal["clean-digital-layout-table-v2"] = (
        "clean-digital-layout-table-v2"
    )
    model_identity: str = Field(min_length=1)
    resource_limits: DocumentResourceLimits = Field(default_factory=DocumentResourceLimits)
    normalization: NormalizationIdentity = Field(default_factory=NormalizationIdentity)
    pages: list[ParsedPageV2] = Field(min_length=1)
    sections: list[DocumentSectionV2] = Field(default_factory=list)
    blocks: list[DocumentBlockV2] = Field(min_length=1)
    tables: list[DocumentTableV2] = Field(default_factory=list)
    warnings: list[ParseWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _document_consistency(self) -> ParsedDocumentV2:
        if [page.page_number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("page_number must be contiguous from one in physical order")
        if [block.reading_order for block in self.blocks] != list(range(1, len(self.blocks) + 1)):
            raise ValueError("block reading_order must be contiguous from one")
        expected_block_ids = [f"block-{idx:04d}" for idx in range(1, len(self.blocks) + 1)]
        if [block.block_id for block in self.blocks] != expected_block_ids:
            raise ValueError("block_id values must be canonical and contiguous")
        page_ids = {page.page_number for page in self.pages}
        block_by_id = {block.block_id: block for block in self.blocks}
        if any(block.page_number not in page_ids for block in self.blocks):
            raise ValueError("block references an unknown page")
        for page in self.pages:
            expected = [block.block_id for block in self.blocks if block.page_number == page.page_number]
            if page.block_ids != expected:
                raise ValueError("page block_ids do not match document reading order")
        section_ids = {section.section_id for section in self.sections}
        if len(section_ids) != len(self.sections):
            raise ValueError("section_id values must be unique")
        expected_section_ids = [f"section-{idx:04d}" for idx in range(1, len(self.sections) + 1)]
        if [section.section_id for section in self.sections] != expected_section_ids:
            raise ValueError("section_id values must be canonical and contiguous")
        if [section.reading_order for section in self.sections] != sorted(
            section.reading_order for section in self.sections
        ):
            raise ValueError("sections must be in document reading order")
        section_stack: list[DocumentSectionV2] = []
        for section in self.sections:
            while section_stack and section_stack[-1].level >= section.level:
                section_stack.pop()
            expected_parent = section_stack[-1].section_id if section_stack else None
            if section.parent_section_id != expected_parent:
                raise ValueError("section parent must be the active nearest shallower section")
            heading = next(
                (
                    block
                    for block in self.blocks
                    if block.reading_order == section.reading_order
                ),
                None,
            )
            if (
                heading is None
                or heading.block_type != DocumentBlockType.HEADING
                or heading.section_id != section.section_id
                or heading.text != section.title
            ):
                raise ValueError("section must be owned by its matching heading block")
            section_stack.append(section)
        if any(block.section_id not in section_ids for block in self.blocks if block.section_id):
            raise ValueError("block references an unknown section")
        section_by_id = {section.section_id: section for section in self.sections}
        if any(
            section_by_id[block.section_id].reading_order > block.reading_order
            for block in self.blocks
            if block.section_id is not None
        ):
            raise ValueError("block cannot belong to a section that starts later")
        section_by_order = {section.reading_order: section for section in self.sections}
        active_sections: list[DocumentSectionV2] = []
        for block in self.blocks:
            active_section = section_by_order.get(block.reading_order)
            if active_section is not None:
                while active_sections and active_sections[-1].level >= active_section.level:
                    active_sections.pop()
                active_sections.append(active_section)
            expected_section_id = active_sections[-1].section_id if active_sections else None
            if block.section_id != expected_section_id:
                raise ValueError("block must belong to the active deepest section")
        table_ids = {table.table_id for table in self.tables}
        if len(table_ids) != len(self.tables):
            raise ValueError("table_id values must be unique")
        expected_table_ids = [f"table-{idx:04d}" for idx in range(1, len(self.tables) + 1)]
        if [table.table_id for table in self.tables] != expected_table_ids:
            raise ValueError("table_id values must be canonical and contiguous")
        row_ids = [row.row_id for table in self.tables for row in table.rows]
        cell_ids = [cell.cell_id for table in self.tables for cell in table.cells]
        if len(row_ids) != len(set(row_ids)) or len(cell_ids) != len(set(cell_ids)):
            raise ValueError("row_id and cell_id values must be document-unique")
        expected_row_ids = [f"row-{idx:04d}" for idx in range(1, len(row_ids) + 1)]
        expected_cell_ids = [f"cell-{idx:04d}" for idx in range(1, len(cell_ids) + 1)]
        if row_ids != expected_row_ids or cell_ids != expected_cell_ids:
            raise ValueError("row_id and cell_id values must be canonical and contiguous")
        if self.normalization.profile == "mv-clean-digital-v1" and any(
            cell.bbox is None for table in self.tables for cell in table.cells
        ):
            raise ValueError("normalization v1 table cells must carry bounding boxes")
        for table in self.tables:
            table_block = block_by_id.get(table.block_id)
            if (
                table_block is None
                or table_block.table_id != table.table_id
                or table_block.page_number != table.page_number
                or table_block.bbox != table.bbox
            ):
                raise ValueError("table identity/page/bbox must match its table block")
        if {block.table_id for block in self.blocks if block.table_id} != table_ids:
            raise ValueError("table blocks and tables must have a one-to-one identity")
        return self

    def flattened_text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.furniture is None).strip()

    def block_index(self) -> dict[str, DocumentBlockV2]:
        return {block.block_id: block for block in self.blocks}

    def cell_index(self) -> dict[str, tuple[DocumentTableV2, TableCellV2]]:
        return {
            cell.cell_id: (table, cell)
            for table in self.tables
            for cell in table.cells
        }

    def prompt_text(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            parts.append(f"[BLOCK {block.block_id} | PAGE {block.page_number}]\n{block.text}")
            if block.table_id is not None:
                table = next(item for item in self.tables if item.table_id == block.table_id)
                for cell in table.cells:
                    parts.append(
                        f"[CELL {cell.cell_id} | TABLE {table.table_id} | "
                        f"ROW {cell.row_index} | COLUMN {cell.column_index}]\n{cell.text}"
                    )
        return "\n\n".join(parts)


type ParsedDocumentAny = Annotated[
    ParsedDocument | ParsedDocumentV2, Field(discriminator="schema_version")
]
PARSED_DOCUMENT_ADAPTER: TypeAdapter[ParsedDocumentAny] = TypeAdapter(ParsedDocumentAny)


class StructuralEvidenceRef(_StrictModel):
    """Resolved schema-v2 block/cell evidence; all location fields are derived."""

    schema_version: Literal[2] = 2
    target_type: Literal["block", "cell"]
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    page_number: int = Field(ge=1)
    block_id: str = Field(pattern=V2_ID_PATTERNS["block"].pattern)
    table_id: str | None = Field(default=None, pattern=V2_ID_PATTERNS["table"].pattern)
    row_id: str | None = Field(default=None, pattern=V2_ID_PATTERNS["row"].pattern)
    cell_id: str | None = Field(default=None, pattern=V2_ID_PATTERNS["cell"].pattern)
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    bbox: NormalizedBBox
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _consistent_target(self) -> StructuralEvidenceRef:
        cell_fields = (
            self.table_id,
            self.row_id,
            self.cell_id,
            self.row_index,
            self.column_index,
        )
        if self.target_type == "block" and any(value is not None for value in cell_fields):
            raise ValueError("block evidence must not carry table/cell fields")
        if self.target_type == "cell" and any(value is None for value in cell_fields):
            raise ValueError("cell evidence requires table, row, column, and cell identity")
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        if not self.quote.strip():
            raise ValueError("evidence quote must contain a non-whitespace character")
        return self
