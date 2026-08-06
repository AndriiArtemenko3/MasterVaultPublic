"""Deterministic resolution of model-proposed quotes to parsed PDF evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from mastervault.core.errors import EvidenceGroundingError
from mastervault.document_intelligence.models import EvidenceRef, ParsedDocument


class EvidenceCandidateLike(Protocol):
    block_id: str
    quote: str


def _quote_pattern(quote: str) -> re.Pattern[str]:
    tokens = quote.split()
    if not tokens:
        raise EvidenceGroundingError("evidence quote is empty")
    return re.compile(r"\s+".join(re.escape(token) for token in tokens))


def evidence_errors(
    document: ParsedDocument, candidates: Iterable[EvidenceCandidateLike]
) -> list[str]:
    """Return all mechanical grounding failures without trusting model page data."""
    errors: list[str] = []
    blocks = document.block_index()
    seen: set[tuple[str, str]] = set()
    values = list(candidates)
    if not values:
        return ["claim has no evidence"]
    for idx, candidate in enumerate(values, start=1):
        block_id = candidate.block_id.strip()
        quote = candidate.quote.strip()
        key = (block_id, " ".join(quote.split()).casefold())
        if key in seen:
            errors.append(f"evidence {idx}: duplicate block/quote reference")
            continue
        seen.add(key)
        block = blocks.get(block_id)
        if block is None:
            errors.append(f"evidence {idx}: unknown block_id {block_id!r}")
            continue
        try:
            pattern = _quote_pattern(quote)
        except EvidenceGroundingError as exc:
            errors.append(f"evidence {idx}: {exc}")
            continue
        if pattern.search(block.text) is None:
            errors.append(f"evidence {idx}: quote is not present in block {block_id!r}")
    return errors


def resolve_evidence(
    document: ParsedDocument, candidates: Iterable[EvidenceCandidateLike]
) -> list[EvidenceRef]:
    values = list(candidates)
    errors = evidence_errors(document, values)
    if errors:
        raise EvidenceGroundingError("; ".join(errors))
    blocks = document.block_index()
    refs: list[EvidenceRef] = []
    for candidate in values:
        block = blocks[candidate.block_id.strip()]
        match = _quote_pattern(candidate.quote.strip()).search(block.text)
        if match is None:  # guarded above; keeps type narrowing explicit
            raise EvidenceGroundingError("validated evidence unexpectedly stopped resolving")
        refs.append(
            EvidenceRef(
                asset_sha256=document.asset_sha256,
                page_number=block.page_number,
                block_id=block.block_id,
                quote=match.group(0),
                start_char=match.start(),
                end_char=match.end(),
            )
        )
    return refs


def validate_resolved_evidence(document: ParsedDocument, refs: list[EvidenceRef]) -> None:
    """Re-resolve canonical evidence loaded from frontmatter or an index."""
    if any(ref.asset_sha256 != document.asset_sha256 for ref in refs):
        raise EvidenceGroundingError("evidence points to a different source asset")
    blocks = document.block_index()
    for ref in refs:
        block = blocks.get(ref.block_id)
        if block is None:
            raise EvidenceGroundingError(f"unknown evidence block {ref.block_id!r}")
        if ref.page_number != block.page_number:
            raise EvidenceGroundingError(
                f"evidence page {ref.page_number} does not match block page {block.page_number}"
            )
        if ref.end_char > len(block.text) or block.text[ref.start_char : ref.end_char] != ref.quote:
            raise EvidenceGroundingError(
                f"evidence offsets/quote do not resolve in block {ref.block_id!r}"
            )
