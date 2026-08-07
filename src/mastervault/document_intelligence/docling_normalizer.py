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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Docling export has no numeric {name}")
    return float(value)


def _integer_field(
    value: Mapping[str, Any], key: str, *, name: str, default: int | None = None
) -> int:
    if key not in value:
        if default is None:
            raise ValueError(f"Docling export has no integer {name}")
        return default
    candidate = value[key]
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(f"Docling export has no integer {name}")
    return candidate


def _boolean_field(
    value: Mapping[str, Any], key: str, *, name: str, default: bool = False
) -> bool:
    if key not in value:
        return default
    candidate = value[key]
    if not isinstance(candidate, bool):
        raise ValueError(f"Docling export has no boolean {name}")
    return candidate


def _page_key(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Docling export page key is not a canonical integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        number = int(value)
        if str(number) == value:
            return number
    raise ValueError("Docling export page key is not a canonical integer")


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
        number = _page_key(raw_number)
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
    if not isinstance(prov, list) or not prov:
        raise ValueError("Docling item must have at least one page provenance region")
    if len(prov) != 1:
        raise ValueError(
            "Docling item has multiple provenance regions; schema-v2 requires "
            "one exact visual region and will not fabricate an enclosing bbox"
        )
    region = prov[0]
    if not isinstance(region, Mapping):
        raise ValueError("Docling item provenance contains a non-object")
    page_number = _integer_field(region, "page_no", name="provenance page_no")
    raw_bbox = region.get("bbox")
    if not isinstance(raw_bbox, Mapping):
        raise ValueError("Docling item provenance has no bbox")
    return page_number, raw_bbox


def _canonical_table_cells(
    raw_data: Mapping[str, Any], *, num_rows: int, num_columns: int
) -> list[Mapping[str, Any]]:
    """Return one verified canonical cell per grid origin, including blanks."""
    raw_cells = raw_data.get("table_cells")
    if not isinstance(raw_cells, list) or any(not isinstance(cell, Mapping) for cell in raw_cells):
        raise ValueError("Docling table cells are invalid")
    cells_by_origin = {
        (
            _integer_field(cell, "start_row_offset_idx", name="cell start row"),
            _integer_field(cell, "start_col_offset_idx", name="cell start column"),
        ): cell
        for cell in raw_cells
    }
    if len(cells_by_origin) != len(raw_cells):
        raise ValueError("Docling table contains duplicate cell origins")

    raw_grid = raw_data.get("grid")
    if (
        not isinstance(raw_grid, list)
        or len(raw_grid) != num_rows
        or any(not isinstance(row, list) or len(row) != num_columns for row in raw_grid)
    ):
        raise ValueError("Docling table grid does not match its declared shape")

    def contract(cell: Mapping[str, Any]) -> tuple[Any, ...]:
        start_row = _integer_field(cell, "start_row_offset_idx", name="cell start row")
        end_row = _integer_field(cell, "end_row_offset_idx", name="cell end row")
        start_column = _integer_field(
            cell, "start_col_offset_idx", name="cell start column"
        )
        end_column = _integer_field(cell, "end_col_offset_idx", name="cell end column")
        row_span = _integer_field(cell, "row_span", name="cell row_span", default=1)
        column_span = _integer_field(cell, "col_span", name="cell col_span", default=1)
        if end_row - start_row != row_span or end_column - start_column != column_span:
            raise ValueError("Docling table cell offsets conflict with its declared span")
        bbox = cell.get("bbox")
        if bbox is not None and not isinstance(bbox, Mapping):
            raise ValueError("Docling table cell bbox is invalid")
        bbox_contract = (
            None
            if bbox is None
            else (
                _number(bbox.get("l"), name="bbox.l"),
                _number(bbox.get("t"), name="bbox.t"),
                _number(bbox.get("r"), name="bbox.r"),
                _number(bbox.get("b"), name="bbox.b"),
                str(bbox.get("coord_origin", "")).upper(),
            )
        )
        return (
            start_row,
            end_row,
            start_column,
            end_column,
            row_span,
            column_span,
            _text(cell.get("text")),
            _boolean_field(cell, "column_header", name="cell column_header"),
            _boolean_field(cell, "row_header", name="cell row_header"),
            bbox_contract,
        )

    grid_origins: set[tuple[int, int]] = set()
    for row_index, row in enumerate(raw_grid):
        for column_index, raw_cell in enumerate(row):
            if not isinstance(raw_cell, Mapping):
                raise ValueError("Docling table grid contains a non-object")
            origin = (
                _integer_field(
                    raw_cell, "start_row_offset_idx", name="grid cell start row"
                ),
                _integer_field(
                    raw_cell, "start_col_offset_idx", name="grid cell start column"
                ),
            )
            origin_cell = cells_by_origin.setdefault(origin, raw_cell)
            if contract(raw_cell) != contract(origin_cell):
                raise ValueError("Docling table grid conflicts with its canonical cell")
            row_span = _integer_field(
                origin_cell, "row_span", name="cell row_span", default=1
            )
            column_span = _integer_field(
                origin_cell, "col_span", name="cell col_span", default=1
            )
            if not (
                origin[0] <= row_index < origin[0] + row_span
                and origin[1] <= column_index < origin[1] + column_span
            ):
                raise ValueError("Docling table grid slot is outside its cell span")
            grid_origins.add(origin)
    if grid_origins != set(cells_by_origin):
        raise ValueError("Docling canonical table cells are not represented in its grid")
    return [cells_by_origin[origin] for origin in sorted(cells_by_origin)]


def _canonical_table_text(cells: list[TableCellV2], *, num_rows: int) -> str:
    """Render the parser-independent row-major text view frozen by schema-v2.

    The structured grid remains authoritative.  This compatibility view uses
    one explicit delimiter between every cell, including row boundaries and
    empty cells, while retaining raw row cues independently of a parser's line
    formatting.
    """
    return " |\n".join(
        " | ".join(cell.text for cell in cells if cell.row_index == row_index)
        for row_index in range(num_rows)
    )


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
                raw_level = _integer_field(
                    item, "level", name="heading level", default=1
                )
                level = min(6, max(1, raw_level))
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
        num_rows = _integer_field(raw_data, "num_rows", name="table num_rows")
        num_columns = _integer_field(raw_data, "num_cols", name="table num_cols")
        if num_rows < 1 or num_columns < 1:
            raise ValueError("Docling table has an invalid grid shape")
        raw_cells = _canonical_table_cells(
            raw_data, num_rows=num_rows, num_columns=num_columns
        )
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
                _integer_field(cell, "start_row_offset_idx", name="cell start row"),
                _integer_field(cell, "start_col_offset_idx", name="cell start column"),
            ),
        ):
            row_index = _integer_field(
                raw_cell, "start_row_offset_idx", name="cell start row"
            )
            column_index = _integer_field(
                raw_cell, "start_col_offset_idx", name="cell start column"
            )
            if row_index not in row_ids:
                raise ValueError("Docling table cell row is outside the grid")
            cell_bbox_value = raw_cell.get("bbox")
            cell_text = _text(raw_cell.get("text"))
            if cell_bbox_value is not None and not isinstance(cell_bbox_value, Mapping):
                raise ValueError("Docling table cell bbox is invalid")
            if cell_text and cell_bbox_value is None:
                raise ValueError("Docling non-empty table cell is missing its bbox")
            row_id = row_ids[row_index]
            cell_counter += 1
            cell_id = f"cell-{cell_counter:04d}"
            cell = TableCellV2(
                cell_id=cell_id,
                row_id=row_id,
                row_index=row_index,
                column_index=column_index,
                row_span=_integer_field(
                    raw_cell, "row_span", name="cell row_span", default=1
                ),
                column_span=_integer_field(
                    raw_cell, "col_span", name="cell col_span", default=1
                ),
                text=cell_text,
                column_header=_boolean_field(
                    raw_cell, "column_header", name="cell column_header"
                ),
                row_header=_boolean_field(raw_cell, "row_header", name="cell row_header"),
                bbox=(
                    _bbox(cell_bbox_value, dimensions[page_number])
                    if cell_bbox_value is not None
                    else None
                ),
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
        table_text = _canonical_table_text(cells, num_rows=num_rows)
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
