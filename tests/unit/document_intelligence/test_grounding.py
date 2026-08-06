from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.contracts.page_grounded_claims import EvidenceCandidate
from mastervault.core.errors import EvidenceGroundingError
from mastervault.document_intelligence import (
    EvidenceRef,
    ParsedDocumentRef,
    SourceAssetRef,
    parse_pdf,
    resolve_evidence,
    validate_resolved_evidence,
)
from mastervault.models import Claim, SourceNote

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
QUOTE = "Customers may return any item within 45 days of the delivery date."


def test_quote_resolves_to_authoritative_page_and_offsets() -> None:
    document = parse_pdf(FIXTURE)
    refs = resolve_evidence(
        document,
        [EvidenceCandidate(block_id="page-0001-block-0001", quote=QUOTE)],
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.asset_sha256 == document.asset_sha256
    assert ref.page_number == 1
    assert ref.quote == QUOTE
    block = document.block_index()[ref.block_id]
    assert block.text[ref.start_char : ref.end_char] == QUOTE
    validate_resolved_evidence(document, refs)


@pytest.mark.parametrize(
    "candidates, message",
    [
        ([], "no evidence"),
        ([EvidenceCandidate(block_id="page-9999-block-0001", quote=QUOTE)], "unknown block"),
        (
            [EvidenceCandidate(block_id="page-0001-block-0001", quote="not in the PDF")],
            "not present",
        ),
        (
            [
                EvidenceCandidate(block_id="page-0001-block-0001", quote=QUOTE),
                EvidenceCandidate(block_id="page-0001-block-0001", quote=QUOTE),
            ],
            "duplicate",
        ),
    ],
)
def test_invalid_model_evidence_fails_closed(candidates, message: str) -> None:
    with pytest.raises(EvidenceGroundingError, match=message):
        resolve_evidence(parse_pdf(FIXTURE), candidates)


def test_persisted_asset_page_and_offsets_are_revalidated() -> None:
    document = parse_pdf(FIXTURE)
    ref = resolve_evidence(
        document,
        [EvidenceCandidate(block_id="page-0001-block-0001", quote=QUOTE)],
    )[0]
    wrong_asset = ref.model_copy(update={"asset_sha256": "0" * 64})
    with pytest.raises(EvidenceGroundingError, match="different source asset"):
        validate_resolved_evidence(document, [wrong_asset])

    wrong_page = ref.model_copy(update={"page_number": 2})
    with pytest.raises(EvidenceGroundingError, match="does not match block page"):
        validate_resolved_evidence(document, [wrong_page])

    wrong_offsets = EvidenceRef(**{**ref.model_dump(), "start_char": 0, "end_char": len(ref.quote)})
    with pytest.raises(EvidenceGroundingError, match="offsets/quote"):
        validate_resolved_evidence(document, [wrong_offsets])


def test_source_note_rejects_mismatched_asset_and_parse_identity() -> None:
    asset = SourceAssetRef(
        asset_sha256="1" * 64,
        stored_path="assets/sha256/11/source.pdf",
        original_filename="source.pdf",
        size_bytes=1,
    )
    parsed = ParsedDocumentRef(
        asset_sha256="2" * 64,
        parser="pypdf",
        parser_version="1",
        parser_profile="page-text-v1",
        artifact_path="parsed/sha256/22/source/parse.json",
        artifact_sha256="3" * 64,
    )
    with pytest.raises(ValidationError, match="different PDF bytes"):
        SourceNote(
            domain="operations",
            title="Mismatched source chain",
            created=date(2026, 8, 6),
            updated=date(2026, 8, 6),
            source_type="policy",
            source_asset=asset,
            parsed_document=parsed,
        )


def test_pdf_source_note_rejects_a_claim_without_evidence() -> None:
    asset = SourceAssetRef(
        asset_sha256="1" * 64,
        stored_path="assets/sha256/11/source.pdf",
        original_filename="source.pdf",
        size_bytes=1,
    )
    parsed = ParsedDocumentRef(
        asset_sha256=asset.asset_sha256,
        parser="pypdf",
        parser_version="1",
        parser_profile="page-text-v1",
        artifact_path="parsed/sha256/11/source/parse.json",
        artifact_sha256="3" * 64,
    )
    claim = Claim(
        id="ungrounded-pdf-01",
        statement="This PDF claim deliberately has no supporting evidence.",
        confidence="high",
    )
    with pytest.raises(ValidationError, match="every claim extracted from a PDF"):
        SourceNote(
            domain="operations",
            title="Ungrounded PDF source",
            created=date(2026, 8, 6),
            updated=date(2026, 8, 6),
            source_type="policy",
            key_claims=[claim],
            source_asset=asset,
            parsed_document=parsed,
        )
