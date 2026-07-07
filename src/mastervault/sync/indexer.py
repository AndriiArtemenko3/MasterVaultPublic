"""Vault -> index synchroniser.

Change detection runs at two granularities:

- Document level: the full-file content hash (frontmatter + body) decides
  whether a document row (with its claims, chunks, and aliases) is re-upserted.
- Record level: each embeddable unit (claim statement, wiki definition, body
  chunk) carries a content hash of exactly the text that gets embedded, and
  `backend.needs_embedding` gates the paid embed call per record.

The two layers are independent on purpose: `full=True` re-upserts every
document but still embeds nothing when no text changed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from mastervault.models import NoteType, RecordType, SourceNote, WikiEntry, content_hash
from mastervault.providers import EmbeddingProvider
from mastervault.storage.base import (
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    StorageBackend,
)
from mastervault.vaultfs.frontmatter import FrontmatterError
from mastervault.vaultfs.notes import LoadedNote, read_note
from mastervault.vaultfs.segmenter import segment
from mastervault.vaultfs.walker import NoteRef, SkippedFile, walk_vault

WIKI_DEFINITION_FALLBACK_CHARS = 600

_DEFINITION_RE = re.compile(r"^##\s+Definition\s*\n(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)

Progress = Callable[[str], None]


def doc_id_for(note: NoteRef) -> str:
    """Canonical doc_id: "wiki:<domain>:<slug>" for wiki, "<type>:<rel_path>" otherwise."""
    if note.note_type is NoteType.WIKI:
        slug = Path(note.rel_path).stem
        return f"wiki:{note.domain.value}:{slug}"
    return f"{note.note_type.value}:{note.rel_path}"


def wiki_definition_text(body: str) -> str:
    """Text of the first `## Definition` section, else the first 600 body chars."""
    m = _DEFINITION_RE.search(body)
    if m:
        text = m.group(1).strip()
        if text:
            return text
    return body.strip()[:WIKI_DEFINITION_FALLBACK_CHARS]


class _Unit(NamedTuple):
    """One embeddable record: (record_id, text) plus the FK context for its row."""

    record_id: str
    record_type: str
    doc_id: str
    domain: str
    text: str
    content_hash: str


@dataclass
class _Prepared:
    doc: DocumentRow
    claims: list[ClaimRow]
    chunks: list[ChunkRow]
    aliases: list[AliasRow]
    units: list[_Unit]


@dataclass
class SyncReport:
    docs_upserted: int = 0
    docs_deleted: int = 0
    records_embedded: int = 0
    records_reused: int = 0
    skipped: list[SkippedFile] = field(default_factory=list)


def _prepare(note: NoteRef, loaded: LoadedNote) -> _Prepared:
    model, body = loaded
    doc_id = doc_id_for(note)
    domain = note.domain.value
    doc = DocumentRow(
        doc_id=doc_id,
        doc_type=note.note_type.value,
        domain=domain,
        rel_path=note.rel_path,
        title=model.title,
        frontmatter=model.model_dump(mode="json", exclude_none=True),
        body=body,
        content_hash=note.content_hash,
    )

    claims: list[ClaimRow] = []
    if isinstance(model, SourceNote):
        claims = [
            ClaimRow(
                claim_id=claim.id,
                doc_id=doc_id,
                ordinal=ordinal,
                statement=claim.statement,
                confidence=claim.confidence.value,
                content_hash=content_hash(claim.statement),
                affects=list(claim.affects),
            )
            for ordinal, claim in enumerate(model.key_claims)
        ]

    chunks = [
        ChunkRow(
            chunk_id=f"chunk:{doc_id}#{chunk.ordinal}",
            doc_id=doc_id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            content_hash=content_hash(chunk.text),
        )
        for chunk in segment(body)
    ]

    aliases: list[AliasRow] = []
    units: list[_Unit] = [
        _Unit(
            record_id=f"claim:{row.claim_id}",
            record_type=RecordType.CLAIM.value,
            doc_id=doc_id,
            domain=domain,
            text=row.statement,
            content_hash=row.content_hash,
        )
        for row in claims
    ]
    if isinstance(model, WikiEntry):
        slug = Path(note.rel_path).stem
        names = dict.fromkeys(
            name.lower().strip()
            for name in (*model.aliases, model.title, slug)
            if name.strip()
        )
        aliases = [AliasRow(alias=alias, wiki_slug=slug, domain=domain) for alias in names]
        wiki_text = f"{model.title}\n\n{wiki_definition_text(body)}"
        units.append(
            _Unit(
                record_id=doc_id,
                record_type=RecordType.WIKI.value,
                doc_id=doc_id,
                domain=domain,
                text=wiki_text,
                content_hash=content_hash(wiki_text),
            )
        )
    units.extend(
        _Unit(
            record_id=row.chunk_id,
            record_type=RecordType.CHUNK.value,
            doc_id=doc_id,
            domain=domain,
            text=row.text,
            content_hash=row.content_hash,
        )
        for row in chunks
    )
    return _Prepared(doc=doc, claims=claims, chunks=chunks, aliases=aliases, units=units)


def prepare_vault(
    vault_dir: Path | str, *, progress: Progress | None = None
) -> tuple[list[_Prepared], list[SkippedFile]]:
    """Walk + prepare every indexable note, without touching backend or embedder.

    Shared by `sync_vault` (which upserts the result) and any caller that only
    needs the current record_id/content_hash space of the vault, e.g. the
    sidecar-embedding importer matching a precomputed vector file against the
    live vault's current text.
    """

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    walk = walk_vault(vault_dir)
    skipped = list(walk.skipped)
    emit(f"walked {len(walk.notes)} notes ({len(walk.skipped)} skipped)")

    prepared: list[_Prepared] = []
    for note in walk.notes:
        try:
            loaded = read_note(note.abs_path)
        except (FrontmatterError, ValidationError) as exc:
            skipped.append(SkippedFile(note.rel_path, f"invalid note: {exc}"))
            continue
        prepared.append(_prepare(note, loaded))
    return prepared, skipped


def record_content_hashes(vault_dir: Path | str) -> dict[str, str]:
    """{record_id: content_hash} for every current embeddable unit in the vault.

    Used to validate a precomputed embeddings sidecar against the live vault
    text before importing a vector — a record_id whose stored content_hash
    disagrees with this map is stale and must not be imported blind.
    """
    prepared, _skipped = prepare_vault(vault_dir)
    hashes: dict[str, str] = {}
    for p in prepared:
        for unit in p.units:
            hashes.setdefault(unit.record_id, unit.content_hash)
    return hashes


def sync_vault(
    vault_dir: Path | str,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    *,
    full: bool = False,
    embed: bool = True,
    progress: Progress | None = None,
) -> SyncReport:
    """Synchronise the vault tree into the storage index.

    Steps: walk -> upsert changed documents -> delete absent documents ->
    hash-gated embedding pass over every current embeddable unit. Skipped
    files (walker gate or note-load failure) are reported, not indexed; a
    previously indexed version of a now-broken file is removed like any
    other absent document.

    `embed=False` skips the embedding pass entirely (no `needs_embedding` /
    `embed` / `upsert_embeddings` calls) — a metadata-only sync that populates
    documents/claims/chunks/aliases without paying the embed-provider cost.
    Pair it with a sidecar vector import (`mastervault.sync.load`) or a later
    `embed=True` sync to backfill embeddings.
    """

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    prepared, skipped = prepare_vault(vault_dir, progress=progress)
    report = SyncReport(skipped=skipped)

    stored_hashes = {
        row.doc_id: row.content_hash
        for row in backend.get_documents([p.doc.doc_id for p in prepared])
    }
    changed = [
        p for p in prepared if full or stored_hashes.get(p.doc.doc_id) != p.doc.content_hash
    ]
    for p in changed:
        backend.upsert_document(p.doc, p.claims, p.chunks, p.aliases)
    report.docs_upserted = len(changed)
    emit(f"upserted {report.docs_upserted} documents")

    present = {p.doc.rel_path for p in prepared}
    report.docs_deleted = len(backend.delete_documents_not_in(present))
    emit(f"deleted {report.docs_deleted} documents")

    if not embed:
        emit("embedding pass skipped (embed=False)")
        return report

    # Embedding pass over every current unit, not just changed documents, so a
    # previously interrupted run converges. Duplicate record_ids keep the first
    # occurrence (walk order is deterministic).
    units_by_id: dict[str, _Unit] = {}
    for p in prepared:
        for unit in p.units:
            units_by_id.setdefault(unit.record_id, unit)
    units = list(units_by_id.values())

    stale = set(
        backend.needs_embedding(
            [(u.record_id, u.content_hash) for u in units], embedder.model_version
        )
    )
    to_embed = [u for u in units if u.record_id in stale]
    report.records_reused = len(units) - len(to_embed)
    if to_embed:
        vectors = embedder.embed([u.text for u in to_embed])
        backend.upsert_embeddings(
            [
                EmbeddingRow(
                    record_id=u.record_id,
                    record_type=u.record_type,
                    doc_id=u.doc_id,
                    domain=u.domain,
                    content_hash=u.content_hash,
                    model_version=embedder.model_version,
                    vector=vector,
                )
                for u, vector in zip(to_embed, vectors, strict=True)
            ]
        )
    report.records_embedded = len(to_embed)
    emit(f"embedded {report.records_embedded} records ({report.records_reused} reused)")
    return report
