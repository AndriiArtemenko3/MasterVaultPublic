"""Resolve canonical claim evidence through verified assets and parse artefacts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from mastervault.core.errors import DocumentIntegrityError, EvidenceGroundingError
from mastervault.document_intelligence import (
    EvidenceRef,
    ParsedDocumentAny,
    ParsedDocumentRef,
    SourceAssetRef,
    StructuralEvidenceRef,
    load_parsed_document,
    structural_records,
    validate_resolved_evidence,
    verify_source_asset,
)
from mastervault.models import SourceNote
from mastervault.storage.base import (
    DocumentRow,
    HydratedClaimRow,
    StorageBackend,
    StructuralRecordRow,
)


class EvidenceBundle(BaseModel):
    claim_id: str
    statement: str
    document_id: str
    document_title: str
    document_path: str
    source_asset: SourceAssetRef
    parsed_document: ParsedDocumentRef
    evidence: list[EvidenceRef | StructuralEvidenceRef]


def _load_source_note(row: DocumentRow) -> SourceNote:
    try:
        return SourceNote.model_validate(row.frontmatter)
    except Exception as exc:
        raise DocumentIntegrityError(
            f"canonical source frontmatter does not validate for {row.rel_path}"
        ) from exc


def evidence_by_claim(
    claims: list[HydratedClaimRow],
    backend: StorageBackend,
    workspace: Path | str,
) -> dict[str, list[EvidenceRef | StructuralEvidenceRef]]:
    """Batch-resolve evidence for hydrated claims; legacy claims map to ``[]``."""
    document_rows = {
        row.doc_id: row for row in backend.get_documents(sorted({claim.doc_id for claim in claims}))
    }
    notes: dict[str, SourceNote] = {}
    parsed: dict[str, ParsedDocumentAny] = {}
    result: dict[str, list[EvidenceRef | StructuralEvidenceRef]] = {}
    for claim in claims:
        row = document_rows.get(claim.doc_id)
        if row is None or row.doc_type != "source":
            result[claim.claim_id] = []
            continue
        note = notes.get(claim.doc_id)
        if note is None:
            note = _load_source_note(row)
            notes[claim.doc_id] = note
        canonical = next((item for item in note.key_claims if item.id == claim.claim_id), None)
        if canonical is None:
            raise DocumentIntegrityError(
                f"indexed claim {claim.claim_id!r} is absent from {row.rel_path}"
            )
        if not canonical.evidence:
            result[claim.claim_id] = []
            continue
        if note.source_asset is None or note.parsed_document is None:
            raise DocumentIntegrityError(
                f"grounded claim {claim.claim_id!r} has no asset/parse reference"
            )
        if claim.doc_id not in parsed:
            verify_source_asset(note.source_asset, workspace)
            parsed[claim.doc_id] = load_parsed_document(note.parsed_document, workspace)
        validate_resolved_evidence(parsed[claim.doc_id], canonical.evidence)
        result[claim.claim_id] = list(canonical.evidence)
    return result


def resolve_claim_evidence(
    claim_id: str,
    backend: StorageBackend,
    workspace: Path | str,
) -> EvidenceBundle:
    """Resolve one claim for the inspection CLI, failing closed on stale evidence."""
    normalized_id = claim_id.removeprefix("claim:")
    claims = backend.get_claims([normalized_id])
    if not claims:
        raise EvidenceGroundingError(f"claim not found: {normalized_id}")
    claim = claims[0]
    rows = backend.get_documents([claim.doc_id])
    if not rows:
        raise DocumentIntegrityError(f"parent document not found for claim {normalized_id}")
    row = rows[0]
    note = _load_source_note(row)
    canonical = next((item for item in note.key_claims if item.id == normalized_id), None)
    if canonical is None:
        raise DocumentIntegrityError(
            f"indexed claim {normalized_id!r} is absent from {row.rel_path}"
        )
    if not canonical.evidence:
        raise EvidenceGroundingError(
            f"claim {normalized_id!r} is a legacy or ungrounded claim with no PDF evidence"
        )
    if note.source_asset is None or note.parsed_document is None:
        raise DocumentIntegrityError(
            f"claim {normalized_id!r} has evidence but no asset/parse reference"
        )
    verify_source_asset(note.source_asset, workspace)
    document = load_parsed_document(note.parsed_document, workspace)
    validate_resolved_evidence(document, canonical.evidence)
    return EvidenceBundle(
        claim_id=normalized_id,
        statement=claim.statement,
        document_id=claim.doc_id,
        document_title=row.title,
        document_path=row.rel_path,
        source_asset=note.source_asset,
        parsed_document=note.parsed_document,
        evidence=list(canonical.evidence),
    )


def validate_structural_hits(
    rows: list[StructuralRecordRow],
    backend: StorageBackend,
    workspace: Path | str,
) -> None:
    """Re-derive structural hits from verified artefacts; reject stale/forged rows."""
    documents = {
        row.doc_id: row for row in backend.get_documents(sorted({item.doc_id for item in rows}))
    }
    expected_by_doc: dict[str, dict[str, StructuralRecordRow]] = {}
    for item in rows:
        document_row = documents.get(item.doc_id)
        if document_row is None or document_row.doc_type != "source":
            raise DocumentIntegrityError(f"structural hit parent is not a source: {item.doc_id}")
        if item.doc_id not in expected_by_doc:
            note = _load_source_note(document_row)
            if note.source_asset is None or note.parsed_document is None:
                raise DocumentIntegrityError("structural hit has no asset/parse reference")
            verify_source_asset(note.source_asset, workspace)
            parsed = load_parsed_document(note.parsed_document, workspace)
            from mastervault.document_intelligence.models import ParsedDocumentV2

            if not isinstance(parsed, ParsedDocumentV2):
                raise DocumentIntegrityError("structural hit points to a non-v2 parse")
            expected_by_doc[item.doc_id] = {
                value.record_id: value
                for value in structural_records(
                    parsed,
                    doc_id=item.doc_id,
                    domain=document_row.domain,
                    parsed_artifact_sha256=note.parsed_document.artifact_sha256,
                )
            }
        expected = expected_by_doc[item.doc_id].get(item.record_id)
        if expected is None:
            raise EvidenceGroundingError(f"unknown structural record: {item.record_id}")
        # Hydration-only document fields are excluded; every persisted identity,
        # text, row scope, cell and evidence location must match exactly.
        actual_payload = {**item.__dict__, "rel_path": "", "domain": document_row.domain}
        expected_payload = {**expected.__dict__, "rel_path": ""}
        if actual_payload != expected_payload:
            raise EvidenceGroundingError(
                f"structural record does not match its verified parsed document: {item.record_id}"
            )
