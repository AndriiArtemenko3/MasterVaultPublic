"""Document-intelligence substrate with dependency-isolated lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mastervault.document_intelligence.docling_adapter import (
        DoclingParser as DoclingParser,
    )
    from mastervault.document_intelligence.docling_adapter import (
        doctor_docling as doctor_docling,
    )
    from mastervault.document_intelligence.grounding import (
        evidence_errors as evidence_errors,
    )
    from mastervault.document_intelligence.grounding import (
        resolve_evidence as resolve_evidence,
    )
    from mastervault.document_intelligence.grounding import (
        validate_resolved_evidence as validate_resolved_evidence,
    )
    from mastervault.document_intelligence.models import (
        DOCUMENT_SCHEMA_VERSION as DOCUMENT_SCHEMA_VERSION,
    )
    from mastervault.document_intelligence.models import (
        LATEST_DOCUMENT_SCHEMA_VERSION as LATEST_DOCUMENT_SCHEMA_VERSION,
    )
    from mastervault.document_intelligence.models import (
        DocumentBlock as DocumentBlock,
    )
    from mastervault.document_intelligence.models import (
        DocumentBlockType as DocumentBlockType,
    )
    from mastervault.document_intelligence.models import (
        DocumentBlockV2 as DocumentBlockV2,
    )
    from mastervault.document_intelligence.models import (
        DocumentResourceLimits as DocumentResourceLimits,
    )
    from mastervault.document_intelligence.models import (
        DocumentSectionV2 as DocumentSectionV2,
    )
    from mastervault.document_intelligence.models import (
        DocumentTableV2 as DocumentTableV2,
    )
    from mastervault.document_intelligence.models import (
        EvidenceRef as EvidenceRef,
    )
    from mastervault.document_intelligence.models import (
        NormalizedBBox as NormalizedBBox,
    )
    from mastervault.document_intelligence.models import (
        ParsedDocument as ParsedDocument,
    )
    from mastervault.document_intelligence.models import (
        ParsedDocumentAny as ParsedDocumentAny,
    )
    from mastervault.document_intelligence.models import (
        ParsedDocumentRef as ParsedDocumentRef,
    )
    from mastervault.document_intelligence.models import (
        ParsedDocumentV2 as ParsedDocumentV2,
    )
    from mastervault.document_intelligence.models import (
        ParsedPage as ParsedPage,
    )
    from mastervault.document_intelligence.models import (
        ParsedPageV2 as ParsedPageV2,
    )
    from mastervault.document_intelligence.models import (
        ParseWarning as ParseWarning,
    )
    from mastervault.document_intelligence.models import (
        SourceAssetRef as SourceAssetRef,
    )
    from mastervault.document_intelligence.models import (
        StructuralEvidenceRef as StructuralEvidenceRef,
    )
    from mastervault.document_intelligence.models import (
        TableCellV2 as TableCellV2,
    )
    from mastervault.document_intelligence.models import (
        TableRowV2 as TableRowV2,
    )
    from mastervault.document_intelligence.parser import (
        DocumentParser as DocumentParser,
    )
    from mastervault.document_intelligence.parser import (
        PdfSource as PdfSource,
    )
    from mastervault.document_intelligence.parser import (
        PypdfParser as PypdfParser,
    )
    from mastervault.document_intelligence.parser import (
        load_pdf_source as load_pdf_source,
    )
    from mastervault.document_intelligence.parser import (
        make_document_parser as make_document_parser,
    )
    from mastervault.document_intelligence.parser import (
        parse_pdf as parse_pdf,
    )
    from mastervault.document_intelligence.renderer import (
        render_document_markdown as render_document_markdown,
    )
    from mastervault.document_intelligence.store import (
        load_parsed_document as load_parsed_document,
    )
    from mastervault.document_intelligence.store import (
        parsed_document_bytes as parsed_document_bytes,
    )
    from mastervault.document_intelligence.store import (
        parsed_document_sha256 as parsed_document_sha256,
    )
    from mastervault.document_intelligence.store import (
        store_parsed_document as store_parsed_document,
    )
    from mastervault.document_intelligence.store import (
        store_source_asset as store_source_asset,
    )
    from mastervault.document_intelligence.store import (
        verify_source_asset as verify_source_asset,
    )
    from mastervault.document_intelligence.structural_records import (
        structural_records as structural_records,
    )

_EXPORT_GROUPS = {
    "docling_adapter": ("DoclingParser", "doctor_docling"),
    "grounding": ("evidence_errors", "resolve_evidence", "validate_resolved_evidence"),
    "models": (
        "DOCUMENT_SCHEMA_VERSION",
        "LATEST_DOCUMENT_SCHEMA_VERSION",
        "DocumentBlock",
        "DocumentBlockType",
        "DocumentBlockV2",
        "DocumentResourceLimits",
        "DocumentSectionV2",
        "DocumentTableV2",
        "EvidenceRef",
        "NormalizedBBox",
        "ParsedDocument",
        "ParsedDocumentAny",
        "ParsedDocumentRef",
        "ParsedDocumentV2",
        "ParsedPage",
        "ParsedPageV2",
        "ParseWarning",
        "SourceAssetRef",
        "StructuralEvidenceRef",
        "TableCellV2",
        "TableRowV2",
    ),
    "parser": (
        "DocumentParser",
        "PdfSource",
        "PypdfParser",
        "load_pdf_source",
        "make_document_parser",
        "parse_pdf",
    ),
    "renderer": ("render_document_markdown",),
    "store": (
        "load_parsed_document",
        "parsed_document_bytes",
        "parsed_document_sha256",
        "store_parsed_document",
        "store_source_asset",
        "verify_source_asset",
    ),
    "structural_records": ("structural_records",),
}
_LAZY_EXPORTS = {
    name: (f"mastervault.document_intelligence.{module}", name)
    for module, names in _EXPORT_GROUPS.items()
    for name in names
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY_EXPORTS)
