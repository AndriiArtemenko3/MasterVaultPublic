"""Deterministic Markdown-only SourceNote successor rendering for M4."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from mastervault.change_control.models import canonical_json_bytes
from mastervault.models import Claim, SourceNote, content_hash
from mastervault.vaultfs.frontmatter import (
    join_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)

MANAGED_SOURCE_NOTE_VALIDATOR_VERSION = "managed-source-note-v1"
MANAGED_SOURCE_NOTE_SCHEMA_BYTES = canonical_json_bytes(SourceNote.model_json_schema())
MANAGED_SOURCE_NOTE_SCHEMA_SHA256 = hashlib.sha256(MANAGED_SOURCE_NOTE_SCHEMA_BYTES).hexdigest()


@dataclass(frozen=True)
class RenderedManagedSourceNote:
    model: SourceNote
    note_bytes: bytes


def parse_managed_source_note(note_bytes: bytes) -> SourceNote:
    try:
        text = note_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("predecessor SourceNote must be exact UTF-8") from exc
    try:
        frontmatter, _body = parse_frontmatter(text)
        note = SourceNote.model_validate(frontmatter)
    except (ValidationError, ValueError, RecursionError) as exc:
        raise ValueError("predecessor SourceNote is invalid") from exc
    if note.source_asset is not None or note.parsed_document is not None:
        raise ValueError("managed Markdown revision cannot rewrite a PDF-grounded SourceNote")
    if any(claim.evidence for claim in note.key_claims):
        raise ValueError("managed Markdown revision rejects grounded claim evidence")
    return note


def render_managed_source_note(
    *,
    predecessor_note_bytes: bytes,
    successor_raw_bytes: bytes,
    successor_raw_path: str,
    analysis_as_of: date,
    statement_rewrites: Mapping[str, str],
) -> RenderedManagedSourceNote:
    """Render one canonical SourceNote without normalizing the raw source bytes."""

    predecessor = parse_managed_source_note(predecessor_note_bytes)
    try:
        raw_text = successor_raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("successor managed raw source must be exact UTF-8") from exc
    if "\x00" in raw_text or any(0xD800 <= ord(character) <= 0xDFFF for character in raw_text):
        raise ValueError("successor managed raw source contains unsafe characters")
    existing = {claim.id for claim in predecessor.key_claims}
    if set(statement_rewrites) - existing:
        raise ValueError("statement rewrite names an absent predecessor claim")
    claims = [
        Claim(
            id=claim.id,
            statement=statement_rewrites.get(claim.id, claim.statement),
            confidence=claim.confidence,
            affects=list(claim.affects),
            evidence=[],
        )
        for claim in predecessor.key_claims
    ]
    updated = max(predecessor.created, predecessor.updated, analysis_as_of)
    successor = SourceNote(
        domain=predecessor.domain,
        type=predecessor.type,
        title=predecessor.title,
        tags=list(predecessor.tags),
        status=predecessor.status,
        created=predecessor.created,
        updated=updated,
        source_type=predecessor.source_type,
        key_claims=claims,
        provenance=successor_raw_path,
        provenance_hash=content_hash(raw_text),
        source_asset=None,
        parsed_document=None,
    )
    summary = " ".join(claim.statement for claim in successor.key_claims)
    body = f"\n# {successor.title}\n\n## Summary\n\n{summary}\n\n## Content\n\n{raw_text}"
    frontmatter = serialize_frontmatter(successor.model_dump(mode="json", exclude_none=True))
    note_bytes = join_frontmatter(frontmatter, body).encode("utf-8")

    # Reopen through the public parser and prove that Content is the exact raw string.
    reopened_data, reopened_body = parse_frontmatter(note_bytes.decode("utf-8"))
    reopened = SourceNote.model_validate(reopened_data)
    marker = "\n## Content\n\n"
    _preamble, found, reopened_raw = reopened_body.partition(marker)
    if not found or reopened_raw.encode("utf-8") != successor_raw_bytes or reopened != successor:
        raise ValueError("rendered managed SourceNote failed exact reopening")
    return RenderedManagedSourceNote(
        model=successor,
        note_bytes=note_bytes,
    )


__all__ = [
    "MANAGED_SOURCE_NOTE_SCHEMA_BYTES",
    "MANAGED_SOURCE_NOTE_SCHEMA_SHA256",
    "MANAGED_SOURCE_NOTE_VALIDATOR_VERSION",
    "RenderedManagedSourceNote",
    "parse_managed_source_note",
    "render_managed_source_note",
]
