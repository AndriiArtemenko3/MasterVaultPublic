"""Stable retrieval records derived from MasterVault's parser-neutral schema-v2 IR."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from mastervault.document_intelligence.models import (
    DocumentBlockType,
    DocumentTableV2,
    ParsedDocumentV2,
    StructuralEvidenceRef,
    TableCellV2,
)
from mastervault.storage.base import StructuralRecordRow


def _cell_evidence(
    document: ParsedDocumentV2,
    table: DocumentTableV2,
    cells: Iterable[TableCellV2],
) -> list[StructuralEvidenceRef]:
    evidence: list[StructuralEvidenceRef] = []
    for cell in cells:
        if not cell.text.strip() or cell.bbox is None:
            continue
        evidence.append(
            StructuralEvidenceRef(
                target_type="cell",
                asset_sha256=document.asset_sha256,
                page_number=table.page_number,
                block_id=table.block_id,
                table_id=table.table_id,
                row_id=cell.row_id,
                cell_id=cell.cell_id,
                row_index=cell.row_index,
                column_index=cell.column_index,
                bbox=cell.bbox,
                quote=cell.text,
                start_char=0,
                end_char=len(cell.text),
            )
        )
    return evidence


def _header_for_column(table: DocumentTableV2, column_index: int, row_index: int) -> TableCellV2 | None:
    candidates = [
        cell
        for cell in table.cells
        if cell.column_header
        and cell.row_index < row_index
        and cell.column_index <= column_index < cell.column_index + cell.column_span
        and cell.text.strip()
    ]
    return max(candidates, key=lambda cell: cell.row_index, default=None)


def _occupied_cells(table: DocumentTableV2, row_index: int) -> list[TableCellV2]:
    """Canonical cells whose declared row span occupies ``row_index``."""
    return [
        cell
        for cell in table.cells
        if cell.row_index <= row_index < cell.row_index + cell.row_span
    ]


def _unique_cells(cells: Iterable[TableCellV2]) -> list[TableCellV2]:
    return list({cell.cell_id: cell for cell in cells}.values())


def _row_text(
    table: DocumentTableV2, row_index: int
) -> tuple[str, list[TableCellV2], list[TableCellV2]] | None:
    occupied = _occupied_cells(table, row_index)
    meaningful = [cell for cell in occupied if cell.text.strip() and cell.bbox is not None]
    if not meaningful:
        # A structurally blank row has no exact evidence and is not retrievable.
        return None
    if all(cell.column_header for cell in meaningful):
        labels = [cell.text.strip() for cell in meaningful]
        return (
            f"Table {table.table_id} header row: " + " | ".join(labels),
            meaningful,
            occupied,
        )

    parts: list[str] = []
    header_cells: list[TableCellV2] = []
    for cell in meaningful:
        # A spanning header is context for this occupied row, never a data
        # value. It will still be retained in the exact evidence below.
        if cell.column_header:
            header_cells.append(cell)
            continue
        value = cell.text.strip()
        header = _header_for_column(table, cell.column_index, row_index)
        if header is not None:
            header_cells.append(header)
            parts.append(f"{header.text.strip()}: {value}")
        elif cell.row_header:
            parts.append(f"Row: {value}")
        else:
            parts.append(f"Column {cell.column_index + 1}: {value}")
    if not parts:
        return None
    # Header cells are included in evidence because their text is part of the
    # retrieval record; callers can therefore resolve every displayed label.
    evidence_cells = _unique_cells([*header_cells, *meaningful])
    return (
        f"Table {table.table_id}, row {row_index + 1}: " + " | ".join(parts),
        evidence_cells,
        occupied,
    )


def _record_id(
    *,
    asset_sha256: str,
    parsed_artifact_sha256: str,
    doc_id: str,
    location: str,
) -> str:
    """Globally unique immutable owner + parse + structural location identity."""
    owner_sha256 = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()
    return (
        f"struct:{asset_sha256}:artifact:{parsed_artifact_sha256}:"
        f"owner:{owner_sha256}:{location}"
    )


def structural_records(
    document: ParsedDocumentV2,
    *,
    doc_id: str,
    domain: str,
    parsed_artifact_sha256: str,
) -> list[StructuralRecordRow]:
    """Derive deterministic section, block, and first-class table-row records."""
    records: list[StructuralRecordRow] = []
    ordinal = 0
    block_by_order = {block.reading_order: block for block in document.blocks}
    for section in document.sections:
        block = block_by_order[section.reading_order]
        ordinal += 1
        records.append(
            StructuralRecordRow(
                record_id=_record_id(
                    asset_sha256=document.asset_sha256,
                    parsed_artifact_sha256=parsed_artifact_sha256,
                    doc_id=doc_id,
                    location=f"section:{section.section_id}",
                ),
                doc_id=doc_id,
                ordinal=ordinal,
                record_kind="section",
                text=f"Section: {section.title}",
                asset_sha256=document.asset_sha256,
                parsed_artifact_sha256=parsed_artifact_sha256,
                page_number=block.page_number,
                block_id=block.block_id,
                section_id=section.section_id,
                evidence=[
                    StructuralEvidenceRef(
                        target_type="block",
                        asset_sha256=document.asset_sha256,
                        page_number=block.page_number,
                        block_id=block.block_id,
                        bbox=block.bbox,
                        quote=block.text,
                        start_char=0,
                        end_char=len(block.text),
                    )
                ],
                domain=domain,
                parser=document.parser,
                parser_version=document.parser_version,
                parser_core_version=document.parser_core_version,
                parser_profile=document.parser_profile,
                normalization_profile=document.normalization.profile,
                model_identity=document.model_identity,
                resource_limits=document.resource_limits.model_dump(mode="json"),
            )
        )
    for block in document.blocks:
        # Table rows below are the authoritative retrieval units for a table;
        # indexing its flattened compatibility block would reintroduce naked
        # values and duplicate the same evidence at a weaker scope.
        if block.block_type == DocumentBlockType.TABLE:
            continue
        ordinal += 1
        records.append(
            StructuralRecordRow(
                record_id=_record_id(
                    asset_sha256=document.asset_sha256,
                    parsed_artifact_sha256=parsed_artifact_sha256,
                    doc_id=doc_id,
                    location=f"block:{block.block_id}",
                ),
                doc_id=doc_id,
                ordinal=ordinal,
                record_kind="block",
                text=block.text,
                asset_sha256=document.asset_sha256,
                parsed_artifact_sha256=parsed_artifact_sha256,
                page_number=block.page_number,
                block_id=block.block_id,
                section_id=block.section_id,
                evidence=[
                    StructuralEvidenceRef(
                        target_type="block",
                        asset_sha256=document.asset_sha256,
                        page_number=block.page_number,
                        block_id=block.block_id,
                        bbox=block.bbox,
                        quote=block.text,
                        start_char=0,
                        end_char=len(block.text),
                    )
                ],
                domain=domain,
                parser=document.parser,
                parser_version=document.parser_version,
                parser_core_version=document.parser_core_version,
                parser_profile=document.parser_profile,
                normalization_profile=document.normalization.profile,
                model_identity=document.model_identity,
                resource_limits=document.resource_limits.model_dump(mode="json"),
            )
        )
    for table in document.tables:
        table_block = document.block_index()[table.block_id]
        for row in table.rows:
            row_record = _row_text(table, row.row_index)
            if row_record is None:
                continue
            text, evidence_cells, occupied_cells = row_record
            ordinal += 1
            records.append(
                StructuralRecordRow(
                    record_id=_record_id(
                        asset_sha256=document.asset_sha256,
                        parsed_artifact_sha256=parsed_artifact_sha256,
                        doc_id=doc_id,
                        location=f"table:{table.table_id}:row:{row.row_id}",
                    ),
                    doc_id=doc_id,
                    ordinal=ordinal,
                    record_kind="table_row",
                    text=text,
                    asset_sha256=document.asset_sha256,
                    parsed_artifact_sha256=parsed_artifact_sha256,
                    page_number=table.page_number,
                    block_id=table.block_id,
                    section_id=table_block.section_id,
                    table_id=table.table_id,
                    row_id=row.row_id,
                    cell_ids=[cell.cell_id for cell in occupied_cells],
                    evidence=_cell_evidence(document, table, evidence_cells),
                    domain=domain,
                    parser=document.parser,
                    parser_version=document.parser_version,
                    parser_core_version=document.parser_core_version,
                    parser_profile=document.parser_profile,
                    normalization_profile=document.normalization.profile,
                    model_identity=document.model_identity,
                    resource_limits=document.resource_limits.model_dump(mode="json"),
                )
            )
    return records
