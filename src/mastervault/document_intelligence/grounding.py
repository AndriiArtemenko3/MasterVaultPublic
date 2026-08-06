"""Deterministic resolution of model-proposed quotes to parsed PDF evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol, cast

from mastervault.core.errors import EvidenceGroundingError
from mastervault.document_intelligence.models import (
    DocumentBlockV2,
    EvidenceRef,
    ParsedDocument,
    ParsedDocumentAny,
    ParsedDocumentV2,
    StructuralEvidenceRef,
)


class EvidenceCandidateLike(Protocol):
    block_id: str | None
    cell_id: str | None
    quote: str


def _quote_pattern(quote: str) -> re.Pattern[str]:
    tokens = quote.split()
    if not tokens:
        raise EvidenceGroundingError("evidence quote is empty")
    return re.compile(r"\s+".join(re.escape(token) for token in tokens))


def _target(candidate: EvidenceCandidateLike) -> tuple[str, str]:
    block_id = (candidate.block_id or "").strip()
    cell_id = (candidate.cell_id or "").strip()
    if bool(block_id) == bool(cell_id):
        raise EvidenceGroundingError("evidence must name exactly one block_id or cell_id")
    return ("block", block_id) if block_id else ("cell", cell_id)


def evidence_errors(
    document: ParsedDocumentAny, candidates: Iterable[EvidenceCandidateLike]
) -> list[str]:
    """Return all mechanical grounding failures without trusting model location data."""
    errors: list[str] = []
    blocks = document.block_index()
    cells = document.cell_index() if isinstance(document, ParsedDocumentV2) else {}
    seen: set[tuple[str, str, str]] = set()
    referenced_tables: set[str] = set()
    values = list(candidates)
    if not values:
        return ["claim has no evidence"]
    for idx, candidate in enumerate(values, start=1):
        try:
            target_type, target_id = _target(candidate)
        except EvidenceGroundingError as exc:
            errors.append(f"evidence {idx}: {exc}")
            continue
        quote = candidate.quote.strip()
        key = (target_type, target_id, " ".join(quote.split()).casefold())
        if key in seen:
            errors.append(f"evidence {idx}: duplicate {target_type}/quote reference")
            continue
        seen.add(key)
        if target_type == "block":
            target = blocks.get(target_id)
            if target is None:
                errors.append(f"evidence {idx}: unknown block_id {target_id!r}")
                continue
            text = target.text
        else:
            pair = cells.get(target_id)
            if pair is None:
                errors.append(f"evidence {idx}: unknown cell_id {target_id!r}")
                continue
            table, cell = pair
            referenced_tables.add(table.table_id)
            if cell.bbox is None:
                errors.append(f"evidence {idx}: cell {target_id!r} has no visual bounding box")
                continue
            text = cell.text
        try:
            pattern = _quote_pattern(quote)
        except EvidenceGroundingError as exc:
            errors.append(f"evidence {idx}: {exc}")
            continue
        if pattern.search(text) is None:
            errors.append(f"evidence {idx}: quote is not present in {target_type} {target_id!r}")
    if len(referenced_tables) > 1:
        errors.append("cell evidence for one claim must not mix tables")
    return errors


def resolve_evidence(
    document: ParsedDocumentAny, candidates: Iterable[EvidenceCandidateLike]
) -> list[EvidenceRef | StructuralEvidenceRef]:
    values = list(candidates)
    errors = evidence_errors(document, values)
    if errors:
        raise EvidenceGroundingError("; ".join(errors))
    blocks = document.block_index()
    refs: list[EvidenceRef | StructuralEvidenceRef] = []
    for candidate in values:
        target_type, target_id = _target(candidate)
        if isinstance(document, ParsedDocument):
            v1_block = blocks[target_id]
            match = _quote_pattern(candidate.quote.strip()).search(v1_block.text)
            if match is None:
                raise EvidenceGroundingError("validated evidence unexpectedly stopped resolving")
            refs.append(
                EvidenceRef(
                    asset_sha256=document.asset_sha256,
                    page_number=v1_block.page_number,
                    block_id=v1_block.block_id,
                    quote=match.group(0),
                    start_char=match.start(),
                    end_char=match.end(),
                )
            )
            continue
        if target_type == "block":
            v2_block = cast(DocumentBlockV2, blocks[target_id])
            match = _quote_pattern(candidate.quote.strip()).search(v2_block.text)
            if match is None:
                raise EvidenceGroundingError("validated evidence unexpectedly stopped resolving")
            refs.append(
                StructuralEvidenceRef(
                    target_type="block",
                    asset_sha256=document.asset_sha256,
                    page_number=v2_block.page_number,
                    block_id=v2_block.block_id,
                    bbox=v2_block.bbox,
                    quote=match.group(0),
                    start_char=match.start(),
                    end_char=match.end(),
                )
            )
        else:
            table, cell = document.cell_index()[target_id]
            v2_block = cast(DocumentBlockV2, blocks[table.block_id])
            match = _quote_pattern(candidate.quote.strip()).search(cell.text)
            if match is None:
                raise EvidenceGroundingError("validated evidence unexpectedly stopped resolving")
            if cell.bbox is None:
                raise EvidenceGroundingError("cell evidence has no visual bounding box")
            refs.append(
                StructuralEvidenceRef(
                    target_type="cell",
                    asset_sha256=document.asset_sha256,
                    page_number=table.page_number,
                    block_id=v2_block.block_id,
                    table_id=table.table_id,
                    row_id=cell.row_id,
                    cell_id=cell.cell_id,
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    bbox=cell.bbox,
                    quote=match.group(0),
                    start_char=match.start(),
                    end_char=match.end(),
                )
            )
    return refs


def validate_resolved_evidence(
    document: ParsedDocumentAny, refs: list[EvidenceRef | StructuralEvidenceRef]
) -> None:
    """Re-resolve canonical evidence loaded from frontmatter or an index."""
    if any(ref.asset_sha256 != document.asset_sha256 for ref in refs):
        raise EvidenceGroundingError("evidence points to a different source asset")
    blocks = document.block_index()
    cells = document.cell_index() if isinstance(document, ParsedDocumentV2) else {}
    referenced_tables: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        if isinstance(ref, EvidenceRef):
            identity = (
                "block",
                ref.block_id,
                " ".join(ref.quote.split()).casefold(),
            )
        else:
            identity = (
                ref.target_type,
                ref.cell_id or ref.block_id,
                " ".join(ref.quote.split()).casefold(),
            )
        if identity in seen:
            raise EvidenceGroundingError("duplicate persisted evidence reference")
        seen.add(identity)
        if isinstance(ref, EvidenceRef):
            if not isinstance(document, ParsedDocument):
                raise EvidenceGroundingError("schema-v1 evidence cannot address a schema-v2 document")
            v1_block = blocks.get(ref.block_id)
            if v1_block is None:
                raise EvidenceGroundingError(f"unknown evidence block {ref.block_id!r}")
            text = v1_block.text
            if ref.page_number != v1_block.page_number:
                raise EvidenceGroundingError(
                    f"evidence page {ref.page_number} does not match block page {v1_block.page_number}"
                )
        else:
            if not isinstance(document, ParsedDocumentV2):
                raise EvidenceGroundingError("schema-v2 evidence cannot address a schema-v1 document")
            v2_block = cast(DocumentBlockV2 | None, blocks.get(ref.block_id))
            if v2_block is None or v2_block.page_number != ref.page_number:
                raise EvidenceGroundingError("structural evidence block/page does not resolve")
            if ref.target_type == "block":
                if ref.bbox != v2_block.bbox:
                    raise EvidenceGroundingError("structural block evidence bbox is forged")
                text = v2_block.text
            else:
                pair = cells.get(ref.cell_id or "")
                if pair is None:
                    raise EvidenceGroundingError(f"unknown evidence cell {ref.cell_id!r}")
                table, cell = pair
                referenced_tables.add(table.table_id)
                if (
                    ref.table_id != table.table_id
                    or ref.row_id != cell.row_id
                    or ref.row_index != cell.row_index
                    or ref.column_index != cell.column_index
                    or ref.page_number != table.page_number
                    or ref.block_id != table.block_id
                    or ref.bbox != cell.bbox
                ):
                    raise EvidenceGroundingError("structural cell evidence contains forged location data")
                text = cell.text
        if ref.end_char > len(text) or text[ref.start_char : ref.end_char] != ref.quote:
            raise EvidenceGroundingError(
                f"evidence offsets/quote do not resolve in {ref.target_type if isinstance(ref, StructuralEvidenceRef) else 'block'}"
            )
    if len(referenced_tables) > 1:
        raise EvidenceGroundingError("cell evidence for one claim must not mix tables")
