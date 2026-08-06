"""Document-intelligence substrate: immutable assets, parsed pages, evidence."""

from mastervault.document_intelligence.grounding import (
    evidence_errors,
    resolve_evidence,
    validate_resolved_evidence,
)
from mastervault.document_intelligence.models import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentBlockType,
    EvidenceRef,
    ParsedDocument,
    ParsedDocumentRef,
    ParsedPage,
    ParseWarning,
    SourceAssetRef,
)
from mastervault.document_intelligence.parser import (
    DocumentParser,
    PdfSource,
    PypdfParser,
    load_pdf_source,
    parse_pdf,
)
from mastervault.document_intelligence.store import (
    load_parsed_document,
    parsed_document_bytes,
    parsed_document_sha256,
    store_parsed_document,
    store_source_asset,
    verify_source_asset,
)

__all__ = [
    "DOCUMENT_SCHEMA_VERSION",
    "DocumentBlock",
    "DocumentBlockType",
    "DocumentParser",
    "EvidenceRef",
    "ParsedDocument",
    "ParsedDocumentRef",
    "ParsedPage",
    "ParseWarning",
    "PdfSource",
    "PypdfParser",
    "SourceAssetRef",
    "evidence_errors",
    "load_parsed_document",
    "load_pdf_source",
    "parse_pdf",
    "parsed_document_bytes",
    "parsed_document_sha256",
    "resolve_evidence",
    "store_parsed_document",
    "store_source_asset",
    "validate_resolved_evidence",
    "verify_source_asset",
]
