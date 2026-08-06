"""Deterministic, MasterVault-owned Markdown rendering for schema-v2 IR."""

from __future__ import annotations

from mastervault.document_intelligence.models import (
    DocumentBlockType,
    DocumentTableV2,
    ParsedDocumentV2,
)


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _rectangular(table: DocumentTableV2) -> bool:
    if any(cell.row_span != 1 or cell.column_span != 1 for cell in table.cells):
        return False
    slots = {(cell.row_index, cell.column_index) for cell in table.cells}
    return slots == {
        (row_index, column_index)
        for row_index in range(table.num_rows)
        for column_index in range(table.num_columns)
    }


def _render_table(table: DocumentTableV2) -> str:
    if not _rectangular(table):
        lines = [f"```table-grid {table.table_id}"]
        for cell in sorted(table.cells, key=lambda value: (value.row_index, value.column_index)):
            flags = []
            if cell.column_header:
                flags.append("column-header")
            if cell.row_header:
                flags.append("row-header")
            suffix = f" flags={','.join(flags)}" if flags else ""
            lines.append(
                f"[{cell.cell_id} row={cell.row_index} column={cell.column_index} "
                f"rowspan={cell.row_span} colspan={cell.column_span}{suffix}] {cell.text}"
            )
        lines.append("```")
        return "\n".join(lines)

    grid = {
        (cell.row_index, cell.column_index): _escape_cell(cell.text) for cell in table.cells
    }
    headers = [f"Column {index + 1}" for index in range(table.num_columns)]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row_index in range(table.num_rows):
        lines.append(
            "| "
            + " | ".join(grid[(row_index, column_index)] for column_index in range(table.num_columns))
            + " |"
        )
    return "\n".join(lines)


def render_document_markdown(
    document: ParsedDocumentV2, *, include_furniture: bool = False
) -> str:
    """Render stable human-readable Markdown without trusting vendor Markdown."""
    tables = {table.table_id: table for table in document.tables}
    sections = {section.section_id: section for section in document.sections}
    parts: list[str] = []
    for block in document.blocks:
        if block.furniture is not None and not include_furniture:
            continue
        if block.block_type == DocumentBlockType.TABLE:
            parts.append(_render_table(tables[block.table_id or ""]))
        elif block.block_type == DocumentBlockType.TITLE:
            parts.append(f"# {block.text}")
        elif block.block_type == DocumentBlockType.HEADING:
            section = sections.get(block.section_id or "")
            level = min(6, (section.level + 1) if section is not None else 2)
            parts.append(f"{'#' * level} {block.text}")
        elif block.block_type == DocumentBlockType.LIST_ITEM:
            parts.append(f"- {block.text}")
        elif block.block_type == DocumentBlockType.CAPTION:
            parts.append(f"*{block.text}*")
        else:
            parts.append(block.text)
    return "\n\n".join(parts).strip()
