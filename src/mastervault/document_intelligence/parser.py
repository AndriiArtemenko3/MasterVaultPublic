"""PDF parser protocol and the page-preserving pypdf baseline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable

from mastervault.core.errors import UnreadableDocument
from mastervault.document_intelligence.models import (
    DocumentBlock,
    DocumentBlockType,
    ParsedDocument,
    ParsedPage,
    ParseWarning,
)

PYPDF_PARSER_NAME = "pypdf"
PYPDF_PARSER_PROFILE = "page-text-v1"


@dataclass(frozen=True)
class PdfSource:
    """One exact source-byte snapshot used for hashing, parsing, and storage."""

    path: Path
    data: bytes
    asset_sha256: str


def load_pdf_source(path: Path | str) -> PdfSource:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UnreadableDocument(f"{path.name}: cannot be read ({exc.strerror or exc}).") from exc
    if not data:
        raise UnreadableDocument(f"{path.name}: PDF file is empty.")
    return PdfSource(path=path, data=data, asset_sha256=hashlib.sha256(data).hexdigest())


@runtime_checkable
class DocumentParser(Protocol):
    name: str
    parser_version: str
    profile: str

    def parse(self, source: PdfSource) -> ParsedDocument: ...


class PypdfParser:
    """Fast baseline: one addressable text block per physical PDF page."""

    name = PYPDF_PARSER_NAME
    parser_version = version("pypdf")
    profile = PYPDF_PARSER_PROFILE

    def parse(self, source: PdfSource) -> ParsedDocument:
        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(source.data))
            if reader.is_encrypted:
                raise UnreadableDocument(
                    f"{source.path.name}: encrypted PDFs are not accepted; provide an "
                    "unencrypted export."
                )
            pages: list[ParsedPage] = []
            warnings: list[ParseWarning] = []
            for page_number, pdf_page in enumerate(reader.pages, start=1):
                # Preserve pypdf's text exactly so ``read_raw_text`` remains
                # compatible with v0.2. Only line-ending representation is
                # normalized for portable JSON artefacts.
                text = (pdf_page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
                blocks: list[DocumentBlock] = []
                if text.strip():
                    blocks.append(
                        DocumentBlock(
                            block_id=f"page-{page_number:04d}-block-0001",
                            block_type=DocumentBlockType.PAGE_TEXT,
                            page_number=page_number,
                            reading_order=1,
                            text=text,
                        )
                    )
                else:
                    warnings.append(
                        ParseWarning(
                            code="empty-page-text",
                            message="pypdf recovered no native text from this page",
                            page_number=page_number,
                        )
                    )
                pages.append(ParsedPage(page_number=page_number, blocks=blocks))
        except UnreadableDocument:
            raise
        except Exception as exc:
            raise UnreadableDocument(
                f"{source.path.name}: not a readable PDF ({type(exc).__name__}: {exc}). "
                "Re-export it as an unencrypted digital PDF, or convert it to UTF-8 text."
            ) from exc

        if not pages:
            raise UnreadableDocument(f"{source.path.name}: PDF contains no pages.")
        if not any(page.blocks for page in pages):
            raise UnreadableDocument(
                f"{source.path.name}: PDF contains no extractable native text; OCR is not "
                "available in the pypdf baseline."
            )
        return ParsedDocument(
            asset_sha256=source.asset_sha256,
            parser=self.name,
            parser_version=self.parser_version,
            parser_profile=self.profile,
            pages=pages,
            warnings=warnings,
        )


def parse_pdf(path: Path | str, parser: DocumentParser | None = None) -> ParsedDocument:
    """Convenience boundary for callers that do not need the source payload."""
    source = load_pdf_source(path)
    return (parser or PypdfParser()).parse(source)
