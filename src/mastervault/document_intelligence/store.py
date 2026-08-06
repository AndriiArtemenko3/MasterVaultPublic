"""Immutable source-asset and parsed-document artefact storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from mastervault.core.errors import DocumentIntegrityError
from mastervault.core.paths import resolve_within
from mastervault.document_intelligence.models import (
    ParsedDocument,
    ParsedDocumentRef,
    SourceAssetRef,
)
from mastervault.document_intelligence.parser import PdfSource


def parsed_document_bytes(document: ParsedDocument) -> bytes:
    """Deterministic JSON representation used for hashing and persistence."""
    return (document.model_dump_json(indent=2) + "\n").encode("utf-8")


def parsed_document_sha256(document: ParsedDocument) -> str:
    return hashlib.sha256(parsed_document_bytes(document)).hexdigest()


def _write_immutable(path: Path, payload: bytes) -> None:
    """Atomically create or verify a content-addressed file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise DocumentIntegrityError(f"immutable artefact differs from expected bytes: {path}")
        return

    fd, tmp_name = tempfile.mkstemp(prefix=".mvault-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Publish without ever replacing an existing digest path. A
            # concurrent writer may win; in that case its bytes must match.
            os.link(tmp, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != payload:
                raise DocumentIntegrityError(
                    f"immutable artefact differs from expected bytes: {path}"
                ) from None
    finally:
        if tmp.exists():
            tmp.unlink()


def store_source_asset(source: PdfSource, workspace: Path | str) -> SourceAssetRef:
    workspace = Path(workspace)
    actual_sha = hashlib.sha256(source.data).hexdigest()
    if actual_sha != source.asset_sha256:
        raise DocumentIntegrityError(
            f"source snapshot hash mismatch: expected {source.asset_sha256}, found {actual_sha}"
        )
    relative = Path("assets") / "sha256" / source.asset_sha256[:2] / f"{source.asset_sha256}.pdf"
    target = resolve_within(workspace, relative)
    _write_immutable(target, source.data)
    return SourceAssetRef(
        asset_sha256=source.asset_sha256,
        stored_path=relative.as_posix(),
        original_filename=source.path.name,
        size_bytes=len(source.data),
    )


def store_parsed_document(document: ParsedDocument, workspace: Path | str) -> ParsedDocumentRef:
    workspace = Path(workspace)
    payload = parsed_document_bytes(document)
    artifact_sha = hashlib.sha256(payload).hexdigest()
    relative = (
        Path("parsed")
        / "sha256"
        / document.asset_sha256[:2]
        / document.asset_sha256
        / f"{artifact_sha}.json"
    )
    target = resolve_within(workspace, relative)
    _write_immutable(target, payload)
    return ParsedDocumentRef(
        asset_sha256=document.asset_sha256,
        parser=document.parser,
        parser_version=document.parser_version,
        parser_profile=document.parser_profile,
        artifact_path=relative.as_posix(),
        artifact_sha256=artifact_sha,
    )


def load_parsed_document(reference: ParsedDocumentRef, workspace: Path | str) -> ParsedDocument:
    path = resolve_within(Path(workspace), reference.artifact_path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DocumentIntegrityError(f"parsed artefact cannot be read: {path}") from exc
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != reference.artifact_sha256:
        raise DocumentIntegrityError(
            f"parsed artefact hash mismatch: expected {reference.artifact_sha256}, "
            f"found {actual_sha}"
        )
    try:
        document = ParsedDocument.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise DocumentIntegrityError(f"parsed artefact does not validate: {path}") from exc
    if document.asset_sha256 != reference.asset_sha256:
        raise DocumentIntegrityError("parsed artefact points to a different source asset")
    if (
        document.parser != reference.parser
        or document.parser_version != reference.parser_version
        or document.parser_profile != reference.parser_profile
    ):
        raise DocumentIntegrityError("parsed artefact parser identity does not match its reference")
    return document


def verify_source_asset(reference: SourceAssetRef, workspace: Path | str) -> Path:
    path = resolve_within(Path(workspace), reference.stored_path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DocumentIntegrityError(f"source asset cannot be read: {path}") from exc
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != reference.asset_sha256 or len(payload) != reference.size_bytes:
        raise DocumentIntegrityError(
            f"source asset integrity mismatch: expected {reference.asset_sha256} "
            f"({reference.size_bytes} bytes), found {actual_sha} ({len(payload)} bytes)"
        )
    return path
