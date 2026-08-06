"""MasterVault-owned normalization of plain Docling export dictionaries.

This module intentionally knows no Docling classes.  The optional adapter
turns vendor objects into builtin dictionaries before crossing this boundary.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

from mastervault.document_intelligence.models import (
    DocumentBlockType,
    DocumentBlockV2,
    DocumentSectionV2,
    DocumentTableV2,
    FurnitureKind,
    NormalizedBBox,
    PageDimensions,
    ParsedDocumentV2,
    ParsedPageV2,
    ParseWarning,
    TableCellV2,
    TableRowV2,
)

_TEXT_LABELS = {
    "title": DocumentBlockType.TITLE,
    "section_header": DocumentBlockType.HEADING,
    "paragraph": DocumentBlockType.PARAGRAPH,
    "text": DocumentBlockType.PARAGRAPH,
    "list_item": DocumentBlockType.LIST_ITEM,
    "caption": DocumentBlockType.CAPTION,
    "page_header": DocumentBlockType.HEADER,
    "page_footer": DocumentBlockType.FOOTER,
}


def _text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _number(value: object, *, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"Docling export has no numeric {name}")
    return float(value)


def _bbox(raw: Mapping[str, Any], dimensions: PageDimensions) -> NormalizedBBox:
    left = _number(raw.get("l"), name="bbox.l")
    right = _number(raw.get("r"), name="bbox.r")
    top = _number(raw.get("t"), name="bbox.t")
    bottom = _number(raw.get("b"), name="bbox.b")
    if str(raw.get("coord_origin", "")).upper() == "BOTTOMLEFT":
        y0 = (dimensions.height_points - top) / dimensions.height_points
        y1 = (dimensions.height_points - bottom) / dimensions.height_points
    elif str(raw.get("coord_origin", "")).upper() == "TOPLEFT":
        y0 = top / dimensions.height_points
        y1 = bottom / dimensions.height_points
    else:
        raise ValueError("Docling export bbox has an unsupported coordinate origin")

    def q(value: float) -> float:
        return round(min(1.0, max(0.0, value)), 6)

    return NormalizedBBox(
        x0=q(min(left, right) / dimensions.width_points),
        y0=q(min(y0, y1)),
        x1=q(max(left, right) / dimensions.width_points),
        y1=q(max(y0, y1)),
    )


def _page_dimensions(export: Mapping[str, Any]) -> dict[int, PageDimensions]:
    raw_pages = export.get("pages")
    if not isinstance(raw_pages, Mapping) or not raw_pages:
        raise ValueError("Docling export contains no physical pages")
    pages: dict[int, PageDimensions] = {}
    for raw_number, raw_page in raw_pages.items():
        if not isinstance(raw_page, Mapping) or not isinstance(raw_page.get("size"), Mapping):
            raise ValueError("Docling export page has no size")
        number = int(raw_number)
        size = raw_page["size"]
        pages[number] = PageDimensions(
            width_points=round(_number(size.get("width"), name="page width"), 3),
            height_points=round(_number(size.get("height"), name="page height"), 3),
        )
    if sorted(pages) != list(range(1, len(pages) + 1)):
        raise ValueError("Docling export page numbers are not contiguous from one")
    return pages


def _provenance(item: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]]:
    prov = item.get("prov")
    if not isinstance(prov, list) or len(prov) != 1 or not isinstance(prov[0], Mapping):
        raise ValueError("Docling item must have exactly one page provenance region")
    page_number = int(prov[0].get("page_no", 0))
    raw_bbox = prov[0].get("bbox")
    if not isinstance(raw_bbox, Mapping):
        raise ValueError("Docling item provenance has no bbox")
    return page_number, raw_bbox


def normalize_docling_export(
    export: Mapping[str, Any],
    *,
    asset_sha256: str,
    parser_version: str,
    parser_core_version: str,
    model_identity: str,
) -> ParsedDocumentV2:
    """Normalize one plain vendor export into strict deterministic schema-v2."""
    dimensions = _page_dimensions(export)
    warnings: list[ParseWarning] = []
    items: list[tuple[int, NormalizedBBox, str, Mapping[str, Any]]] = []
    for kind in ("texts", "tables"):
        raw_items = export.get(kind, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"Docling export field {kind!r} is not a list")
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ValueError(f"Docling export field {kind!r} contains a non-object")
            page_number, raw_bbox = _provenance(item)
            if page_number not in dimensions:
                raise ValueError("Docling item references an unknown page")
            items.append((page_number, _bbox(raw_bbox, dimensions[page_number]), kind, item))

    # Reading order is MasterVault-owned and never depends on vendor JSON pointers:
    # physical page, top edge, left edge, then a stable kind/text tie break.
    items.sort(
        key=lambda value: (
            value[0],
            value[1].y0,
            value[1].x0,
            1 if value[2] == "tables" else 0,
            _text(value[3].get("text") or value[3].get("label")),
        )
    )

    sections: list[DocumentSectionV2] = []
    blocks: list[DocumentBlockV2] = []
    tables: list[DocumentTableV2] = []
    section_stack: list[DocumentSectionV2] = []
    row_counter = 0
    cell_counter = 0

    for _source_order, (page_number, bbox, kind, item) in enumerate(items, start=1):
        # IDs and reading order cover retained blocks only. Empty vendor items
        # are warned about below without leaving gaps in the canonical IR.
        reading_order = len(blocks) + 1
        block_id = f"block-{reading_order:04d}"
        if kind == "texts":
            text = _text(item.get("text"))
            if not text:
                warnings.append(
                    ParseWarning(
                        code="empty-layout-item",
                        message="Docling emitted an empty text layout item; it was excluded",
                        page_number=page_number,
                    )
                )
                continue
            label = str(item.get("label", "unknown"))
            block_type = _TEXT_LABELS.get(label, DocumentBlockType.UNKNOWN)
            furniture = (
                FurnitureKind.HEADER
                if label == "page_header"
                else FurnitureKind.FOOTER
                if label == "page_footer"
                else None
            )
            if block_type == DocumentBlockType.HEADING:
                raw_level = item.get("level", 1)
                level = min(6, max(1, int(raw_level) if isinstance(raw_level, int) else 1))
                while section_stack and section_stack[-1].level >= level:
                    section_stack.pop()
                section = DocumentSectionV2(
                    section_id=f"section-{len(sections) + 1:04d}",
                    title=text,
                    level=level,
                    parent_section_id=section_stack[-1].section_id if section_stack else None,
                    reading_order=reading_order,
                )
                sections.append(section)
                section_stack.append(section)
            blocks.append(
                DocumentBlockV2(
                    block_id=block_id,
                    block_type=block_type,
                    page_number=page_number,
                    reading_order=len(blocks) + 1,
                    text=text,
                    bbox=bbox,
                    section_id=section_stack[-1].section_id if section_stack else None,
                    furniture=furniture,
                )
            )
            continue

        raw_data = item.get("data")
        if not isinstance(raw_data, Mapping):
            raise ValueError("Docling table has no data object")
        num_rows = int(raw_data.get("num_rows", 0))
        num_columns = int(raw_data.get("num_cols", 0))
        raw_cells = raw_data.get("table_cells")
        if num_rows < 1 or num_columns < 1 or not isinstance(raw_cells, list):
            raise ValueError("Docling table has an invalid grid shape")
        table_id = f"table-{len(tables) + 1:04d}"
        cells: list[TableCellV2] = []
        by_row: dict[int, list[str]] = {idx: [] for idx in range(num_rows)}
        row_ids = {
            row_index: f"row-{row_counter + row_index + 1:04d}"
            for row_index in range(num_rows)
        }
        row_counter += num_rows
        for raw_cell in sorted(
            raw_cells,
            key=lambda cell: (
                int(cell.get("start_row_offset_idx", -1)),
                int(cell.get("start_col_offset_idx", -1)),
            ),
        ):
            if not isinstance(raw_cell, Mapping) or not isinstance(raw_cell.get("bbox"), Mapping):
                raise ValueError("Docling table cell is missing its bbox")
            row_index = int(raw_cell.get("start_row_offset_idx", -1))
            column_index = int(raw_cell.get("start_col_offset_idx", -1))
            if row_index not in row_ids:
                raise ValueError("Docling table cell row is outside the grid")
            row_id = row_ids[row_index]
            cell_counter += 1
            cell_id = f"cell-{cell_counter:04d}"
            cell = TableCellV2(
                cell_id=cell_id,
                row_id=row_id,
                row_index=row_index,
                column_index=column_index,
                row_span=int(raw_cell.get("row_span", 1)),
                column_span=int(raw_cell.get("col_span", 1)),
                text=_text(raw_cell.get("text")),
                column_header=bool(raw_cell.get("column_header", False)),
                row_header=bool(raw_cell.get("row_header", False)),
                bbox=_bbox(raw_cell["bbox"], dimensions[page_number]),
            )
            cells.append(cell)
            by_row[row_index].append(cell_id)
        rows = [
            TableRowV2(
                row_id=row_ids[row_index],
                row_index=row_index,
                cell_ids=by_row[row_index],
            )
            for row_index in range(num_rows)
        ]
        table_text = "\n".join(
            " | ".join(
                cell.text
                for cell in sorted(
                    (candidate for candidate in cells if candidate.row_index == row.row_index),
                    key=lambda candidate: candidate.column_index,
                )
            )
            for row in rows
        )
        blocks.append(
            DocumentBlockV2(
                block_id=block_id,
                block_type=DocumentBlockType.TABLE,
                page_number=page_number,
                reading_order=len(blocks) + 1,
                text=table_text or "[empty table]",
                bbox=bbox,
                section_id=section_stack[-1].section_id if section_stack else None,
                table_id=table_id,
            )
        )
        tables.append(
            DocumentTableV2(
                table_id=table_id,
                block_id=block_id,
                page_number=page_number,
                bbox=bbox,
                num_rows=num_rows,
                num_columns=num_columns,
                rows=rows,
                cells=cells,
            )
        )

    # Empty items were dropped, so renumber block ids/order and table block refs
    # once, based solely on the surviving semantic traversal.
    if not blocks:
        raise ValueError("Docling recovered no native document content")
    remap = {block.block_id: f"block-{idx:04d}" for idx, block in enumerate(blocks, start=1)}
    blocks = [
        block.model_copy(
            update={"block_id": remap[block.block_id], "reading_order": idx}
        )
        for idx, block in enumerate(blocks, start=1)
    ]
    tables = [table.model_copy(update={"block_id": remap[table.block_id]}) for table in tables]
    pages = [
        ParsedPageV2(
            page_number=page_number,
            dimensions=dimensions[page_number],
            block_ids=[block.block_id for block in blocks if block.page_number == page_number],
        )
        for page_number in sorted(dimensions)
    ]
    return ParsedDocumentV2(
        asset_sha256=asset_sha256,
        parser_version=parser_version,
        parser_core_version=parser_core_version,
        model_identity=model_identity,
        pages=pages,
        sections=sections,
        blocks=blocks,
        tables=tables,
        warnings=warnings,
    )
