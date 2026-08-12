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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from mastervault.document_intelligence import (
    ParsedDocumentV2,
    load_parsed_document,
    load_parsed_document_bytes,
    structural_records,
    verify_source_asset,
    verify_source_asset_bytes,
)
from mastervault.models import Domain, NoteType, RecordType, SourceNote, WikiEntry, content_hash
from mastervault.providers import EmbeddingProvider
from mastervault.storage.base import (
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    StorageBackend,
    StructuralRecordRow,
)
from mastervault.vaultfs.frontmatter import FrontmatterError, parse_frontmatter
from mastervault.vaultfs.notes import MODEL_BY_TYPE, LoadedNote, extract_title, read_note
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
    structural: list[StructuralRecordRow] = field(default_factory=list)


# Public, read-only inspection aliases.  Generation/bootstrap verification
# needs the exact projection produced by the normal synchroniser, but must not
# reach through an underscore-prefixed sibling API to get it.
PreparedIndexUnit = _Unit
PreparedIndexDocument = _Prepared


@dataclass
class SyncReport:
    docs_upserted: int = 0
    docs_deleted: int = 0
    records_embedded: int = 0
    records_reused: int = 0
    skipped: list[SkippedFile] = field(default_factory=list)
    # Positive evidence of the notes that passed both the walker and parser
    # gates in this run.  Callers that need to prove a particular file was
    # indexed must not infer that merely from its absence in ``skipped``:
    # walk_vault intentionally ignores dot-directories and non-Markdown files.
    prepared_paths: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ExactWorkspaceFileInput:
    """One exact auxiliary workspace file already read by a hardened caller."""

    rel_path: str
    content: bytes


@dataclass(frozen=True)
class ExactVaultNoteInput:
    """One explicitly authorized note for a closed vault inventory."""

    rel_path: str
    content: bytes
    workspace: Path
    supporting_files: tuple[ExactWorkspaceFileInput, ...] = ()


@dataclass(frozen=True)
class ExactSourceNoteInput(ExactVaultNoteInput):
    """One explicitly authorized SourceNote for a closed generation inventory."""


@dataclass
class ExactSourceNoteSyncReport(SyncReport):
    """Positive evidence that one closed inventory was indexed with no skips."""

    doc_ids: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()


def _prepare(
    note: NoteRef,
    loaded: LoadedNote,
    *,
    workspace: Path,
    exact_workspace_files: Mapping[str, bytes] | None = None,
) -> _Prepared:
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
            name.lower().strip() for name in (*model.aliases, model.title, slug) if name.strip()
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
    structural: list[StructuralRecordRow] = []
    if (
        isinstance(model, SourceNote)
        and model.parsed_document is not None
        and (
            exact_workspace_files is not None
            or model.parsed_document.document_schema_version == 2
        )
    ):
        if model.source_asset is None:
            raise ValueError("parsed PDF source is missing its immutable asset reference")
        if exact_workspace_files is None:
            verify_source_asset(model.source_asset, workspace)
            parsed = load_parsed_document(model.parsed_document, workspace)
        else:
            expected_paths = {
                model.source_asset.stored_path,
                model.parsed_document.artifact_path,
            }
            if set(exact_workspace_files) != expected_paths:
                raise ValueError(
                    "exact PDF projection requires only its bound asset and parsed artifact"
                )
            source_bytes = exact_workspace_files[model.source_asset.stored_path]
            parsed_bytes = exact_workspace_files[model.parsed_document.artifact_path]
            verify_source_asset_bytes(model.source_asset, source_bytes)
            parsed = load_parsed_document_bytes(model.parsed_document, parsed_bytes)
        if isinstance(parsed, ParsedDocumentV2):
            structural = structural_records(
                parsed,
                doc_id=doc_id,
                domain=domain,
                parsed_artifact_sha256=model.parsed_document.artifact_sha256,
            )
    return _Prepared(
        doc=doc,
        claims=claims,
        chunks=chunks,
        aliases=aliases,
        units=units,
        structural=structural,
    )


def prepare_vault(
    vault_dir: Path | str,
    *,
    progress: Progress | None = None,
    workspace: Path | str | None = None,
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

    vault_dir = Path(vault_dir)
    resolved_workspace = Path(workspace) if workspace is not None else vault_dir.parent
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
        prepared.append(_prepare(note, loaded, workspace=resolved_workspace))
    return prepared, skipped


def _prepare_exact_vault_notes(
    notes: tuple[ExactVaultNoteInput, ...],
    *,
    source_notes_only: bool,
) -> list[PreparedIndexDocument]:
    """Prepare caller-authorized bytes without walking or skipping members.

    The caller owns inventory closure.  This function parses every supplied
    byte string and returns exactly the projection used by ``sync_vault``.
    """

    if not notes:
        raise ValueError("exact vault inventory must not be empty")
    ordered = tuple(sorted(notes, key=lambda item: item.rel_path))
    if notes != ordered or len({item.rel_path for item in notes}) != len(notes):
        raise ValueError("exact vault notes must use unique canonical path order")
    prepared: list[PreparedIndexDocument] = []
    for item in notes:
        rel = item.rel_path
        candidate = Path(rel)
        if (
            not rel
            or candidate.is_absolute()
            or candidate.as_posix() != rel
            or ".." in candidate.parts
            or any(part.startswith(".") for part in candidate.parts)
            or not rel.endswith(".md")
        ):
            raise ValueError("exact vault note path must be canonical relative Markdown")
        try:
            text = item.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"exact vault note is not UTF-8: {rel}") from exc
        data, body = parse_frontmatter(text)
        raw_type = data.get("type")
        raw_domain = data.get("domain")
        if not isinstance(raw_type, str) or not isinstance(raw_domain, str):
            raise ValueError(f"exact vault note has invalid type/domain: {rel}")
        try:
            note_type = NoteType(raw_type)
            domain = Domain(raw_domain)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"exact vault note has invalid type/domain: {rel}") from exc
        if source_notes_only and note_type != NoteType.SOURCE:
            raise ValueError("managed generation inventory may contain SourceNotes only")
        if not data.get("title"):
            data = {**data, "title": extract_title(body, candidate.stem)}
        model = MODEL_BY_TYPE[note_type].model_validate(data)
        exact_workspace_files = {
            supporting.rel_path: supporting.content for supporting in item.supporting_files
        }
        if len(exact_workspace_files) != len(item.supporting_files):
            raise ValueError("exact workspace supporting-file paths must be unique")
        for supporting in item.supporting_files:
            supporting_path = Path(supporting.rel_path)
            if (
                not supporting.rel_path
                or supporting_path.is_absolute()
                or supporting_path.as_posix() != supporting.rel_path
                or ".." in supporting_path.parts
                or any(part.startswith(".") for part in supporting_path.parts)
            ):
                raise ValueError("exact workspace supporting-file path must be canonical")
        if not isinstance(model, SourceNote) or model.source_asset is None:
            if exact_workspace_files:
                raise ValueError("non-PDF exact note cannot carry supporting workspace files")
            exact_workspace_files_arg: Mapping[str, bytes] | None = None
        else:
            exact_workspace_files_arg = exact_workspace_files
        note = NoteRef(
            abs_path=item.workspace / rel,
            rel_path=rel,
            note_type=note_type,
            domain=domain,
            content_hash=content_hash(text),
        )
        prepared.append(
            _prepare(
                note,
                LoadedNote(model=model, body=body),
                workspace=item.workspace,
                exact_workspace_files=exact_workspace_files_arg,
            )
        )
    doc_ids = [item.doc.doc_id for item in prepared]
    if len(set(doc_ids)) != len(doc_ids):
        raise ValueError("exact vault inventory produces duplicate document IDs")
    record_ids = [unit.record_id for item in prepared for unit in item.units]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("exact vault inventory produces duplicate record IDs")
    return prepared


def prepare_exact_vault_notes(
    notes: tuple[ExactVaultNoteInput, ...],
) -> list[PreparedIndexDocument]:
    """Project one complete, explicit all-note inventory with no skip channel."""

    return _prepare_exact_vault_notes(notes, source_notes_only=False)


def prepare_exact_source_notes(
    notes: tuple[ExactSourceNoteInput, ...],
) -> list[PreparedIndexDocument]:
    """Project an explicit managed SourceNote inventory with no skip channel."""

    return _prepare_exact_vault_notes(notes, source_notes_only=True)


def sync_exact_source_notes(
    notes: tuple[ExactSourceNoteInput, ...],
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    *,
    force_embeddings: bool = False,
) -> ExactSourceNoteSyncReport:
    """Build one complete isolated index from an explicit closed inventory."""

    prepared = prepare_exact_source_notes(notes)
    expected_paths = {item.rel_path for item in notes}
    prepared_paths = {item.doc.rel_path for item in prepared}
    if prepared_paths != expected_paths:
        raise ValueError("prepared SourceNote set differs from exact generation inventory")
    conn = getattr(backend, "conn", None)
    if conn is None or not callable(getattr(conn, "execute", None)):
        raise ValueError("managed generation indexing requires inspectable SQLite storage")
    existing_paths = {
        str(row[0]) for row in conn.execute("SELECT rel_path FROM documents").fetchall()
    }
    if existing_paths - expected_paths:
        raise ValueError("isolated generation index unexpectedly contains extra documents")
    atomic_structural_upsert = getattr(backend, "upsert_document_with_structural", None)
    if not callable(atomic_structural_upsert):
        raise ValueError("managed generation indexing requires atomic structural storage")
    for item in prepared:
        atomic_structural_upsert(
            item.doc,
            item.claims,
            item.chunks,
            item.aliases,
            item.structural,
        )
    deleted = backend.delete_documents_not_in(expected_paths)
    if deleted:
        raise RuntimeError("exact preflight failed to prevent unexpected document deletion")
    units = [unit for item in prepared for unit in item.units]
    record_ids = tuple(sorted(unit.record_id for unit in units))
    stale = (
        [unit.record_id for unit in units]
        if force_embeddings
        else backend.needs_embedding(
            [(unit.record_id, unit.content_hash) for unit in units],
            embedder.model_version,
        )
    )
    stale_ids = set(stale)
    to_embed = [unit for unit in units if unit.record_id in stale_ids]
    if to_embed:
        vectors = embedder.embed([unit.text for unit in to_embed])
        if len(vectors) != len(to_embed):
            raise ValueError("embedding provider returned the wrong vector count")
        backend.upsert_embeddings(
            [
                EmbeddingRow(
                    record_id=unit.record_id,
                    record_type=unit.record_type,
                    doc_id=unit.doc_id,
                    domain=unit.domain,
                    content_hash=unit.content_hash,
                    model_version=embedder.model_version,
                    vector=vector,
                )
                for unit, vector in zip(to_embed, vectors, strict=True)
            ]
        )
    return ExactSourceNoteSyncReport(
        docs_upserted=len(prepared),
        docs_deleted=0,
        records_embedded=len(to_embed),
        records_reused=len(units) - len(to_embed),
        skipped=[],
        prepared_paths=prepared_paths,
        doc_ids=tuple(sorted(item.doc.doc_id for item in prepared)),
        record_ids=record_ids,
    )


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
    report = SyncReport(
        skipped=skipped,
        prepared_paths={item.doc.rel_path for item in prepared},
    )

    stored_hashes = {
        row.doc_id: row.content_hash
        for row in backend.get_documents([p.doc.doc_id for p in prepared])
    }
    changed = [p for p in prepared if full or stored_hashes.get(p.doc.doc_id) != p.doc.content_hash]
    atomic_structural_upsert = getattr(backend, "upsert_document_with_structural", None)
    replace_structural = getattr(backend, "replace_structural_records", None)
    for p in changed:
        if callable(atomic_structural_upsert):
            atomic_structural_upsert(p.doc, p.claims, p.chunks, p.aliases, p.structural)
        else:
            # Legacy duck-typed backends retain their original four-argument
            # write path. A partial structural capability is best-effort only;
            # official backends use the atomic method above.
            backend.upsert_document(p.doc, p.claims, p.chunks, p.aliases)
            if callable(replace_structural):
                replace_structural(p.doc.doc_id, p.structural)
    report.docs_upserted = len(changed)
    emit(f"upserted {report.docs_upserted} documents")

    present = {p.doc.rel_path for p in prepared}
    report.docs_deleted = len(backend.delete_documents_not_in(present))
    emit(f"deleted {report.docs_deleted} documents")

    # Structural records are cheap deterministic derivatives of immutable v2
    # artefacts. Replace them on every sync so schema upgrades and interrupted
    # runs converge without changing legacy record/embedding identities.
    changed_ids = {p.doc.doc_id for p in changed}
    if callable(replace_structural):
        for p in prepared:
            if p.doc.doc_id not in changed_ids:
                replace_structural(p.doc.doc_id, p.structural)

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
