from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from mastervault.core.errors import DocumentIntegrityError, UnreadableDocument
from mastervault.document_intelligence import (
    ParsedDocument,
    ParsedPage,
    PdfSource,
    PypdfParser,
    load_parsed_document,
    load_pdf_source,
    parsed_document_bytes,
    store_parsed_document,
    store_source_asset,
    verify_source_asset,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
EXPECTED_SHA = "d12dc2de2b5a9fff9bba869c80cec305e5fc3744a1559302c3bbadf147e4332e"


def test_pypdf_preserves_pages_blocks_and_legacy_flattening() -> None:
    source = load_pdf_source(FIXTURE)
    document = PypdfParser().parse(source)

    assert source.asset_sha256 == EXPECTED_SHA
    assert [page.page_number for page in document.pages] == [1, 2]
    assert [page.blocks[0].block_id for page in document.pages] == [
        "page-0001-block-0001",
        "page-0002-block-0001",
    ]
    assert all(page.blocks[0].block_type.value == "page_text" for page in document.pages)
    assert document.parser == "pypdf"
    assert document.parser_version
    assert document.parser_profile == "page-text-v1"

    reader = PdfReader(str(FIXTURE))
    legacy = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    assert document.flattened_text() == legacy


def test_parsed_json_is_deterministic_strict_and_round_trips() -> None:
    document = PypdfParser().parse(load_pdf_source(FIXTURE))
    first = parsed_document_bytes(document)
    restored = ParsedDocument.model_validate_json(first)
    assert parsed_document_bytes(restored) == first
    assert first.endswith(b"\n")
    with pytest.raises(ValidationError):
        ParsedDocument.model_validate({**document.model_dump(), "unknown": True})


def test_page_rejects_a_block_id_that_encodes_another_page() -> None:
    document = PypdfParser().parse(load_pdf_source(FIXTURE))
    first = document.pages[0].blocks[0]
    with pytest.raises(ValidationError, match="block ids encode another page"):
        ParsedPage(
            page_number=1,
            blocks=[first.model_copy(update={"block_id": "page-0002-block-0001"})],
        )


def test_content_addressed_store_dedupes_and_detects_tampering(
    tmp_path: Path,
) -> None:
    source = load_pdf_source(FIXTURE)
    document = PypdfParser().parse(source)
    first_asset = store_source_asset(source, tmp_path)
    first_parse = store_parsed_document(document, tmp_path)

    renamed = tmp_path / "same-content-another-name.pdf"
    renamed.write_bytes(source.data)
    second_asset = store_source_asset(load_pdf_source(renamed), tmp_path)
    assert second_asset.asset_sha256 == first_asset.asset_sha256
    assert second_asset.stored_path == first_asset.stored_path
    assert load_parsed_document(first_parse, tmp_path) == document

    asset_path = verify_source_asset(first_asset, tmp_path)
    asset_path.write_bytes(b"tampered")
    with pytest.raises(DocumentIntegrityError, match="integrity mismatch"):
        verify_source_asset(first_asset, tmp_path)
    with pytest.raises(DocumentIntegrityError, match="differs from expected"):
        store_source_asset(source, tmp_path)


def test_store_rejects_an_internally_inconsistent_source_snapshot(tmp_path: Path) -> None:
    source = PdfSource(path=tmp_path / "forged.pdf", data=b"forged", asset_sha256="0" * 64)
    with pytest.raises(DocumentIntegrityError, match="source snapshot hash mismatch"):
        store_source_asset(source, tmp_path)
    assert not (tmp_path / "assets").exists()


def test_corrupt_encrypted_and_textless_pdfs_fail_visibly(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(UnreadableDocument, match="corrupt.pdf"):
        PypdfParser().parse(load_pdf_source(corrupt))

    blank_writer = PdfWriter()
    blank_writer.add_blank_page(width=200, height=200)
    blank = tmp_path / "blank.pdf"
    with blank.open("wb") as handle:
        blank_writer.write(handle)
    with pytest.raises(UnreadableDocument, match="no extractable native text"):
        PypdfParser().parse(load_pdf_source(blank))

    encrypted_writer = PdfWriter()
    for page in PdfReader(str(FIXTURE)).pages:
        encrypted_writer.add_page(page)
    encrypted_writer.encrypt("secret")
    encrypted = tmp_path / "encrypted.pdf"
    with encrypted.open("wb") as handle:
        encrypted_writer.write(handle)
    with pytest.raises(UnreadableDocument, match="encrypted PDFs are not accepted"):
        PypdfParser().parse(load_pdf_source(encrypted))


def test_one_changed_pdf_byte_has_a_distinct_asset_identity() -> None:
    source = load_pdf_source(FIXTURE)
    changed = source.data + b"\n"
    assert hashlib.sha256(changed).hexdigest() != source.asset_sha256

    # The original remains parseable from the exact in-memory source snapshot.
    assert len(PypdfParser().parse(source).pages) == 2
