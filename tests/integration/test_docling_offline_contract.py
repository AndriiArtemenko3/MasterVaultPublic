"""Real optional adapter smoke, enabled only with certified local artifacts."""

from __future__ import annotations

import hashlib
import os
import socket
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

import mastervault
from mastervault.core.errors import UnreadableDocument
from mastervault.document_intelligence import (
    DoclingParser,
    PdfSource,
    parsed_document_bytes,
)
from mastervault.document_intelligence.docling_adapter import (
    DOCLING_MAX_PAGES,
    doctor_docling,
)
from mastervault.document_intelligence.parser import load_pdf_source

pytestmark = pytest.mark.integration

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)


def test_real_docling_profile_is_offline_and_deterministic(monkeypatch) -> None:
    if os.environ.get("MV_EXPECT_INSTALLED_WHEEL") == "1":
        package_path = Path(mastervault.__file__).resolve()
        assert "site-packages" in package_path.parts
        checkout = os.environ.get("GITHUB_WORKSPACE")
        if checkout:
            assert Path(checkout).resolve() not in package_path.parents

    configured = os.environ.get("MV_DOCUMENT__DOCLING_ARTIFACTS_PATH")
    if not configured:
        pytest.skip("certified Docling artifacts path is not configured")
    artifacts = Path(configured)
    report = doctor_docling(artifacts)
    assert report.ok, report.message
    assert report.model_identity == (
        "sha256:a289c24d135c526748d376cdfdd1e7b780fe506c846f7effac380bb0736a373c"
    )

    def _deny_network(*_args, **_kwargs):
        raise AssertionError("network access attempted during offline Docling parse")

    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    source = load_pdf_source(FIXTURE)
    parser = DoclingParser(artifacts)
    first = parser.parse(source)
    second = parser.parse(source)

    assert parsed_document_bytes(first) == parsed_document_bytes(second)
    assert first.schema_version == 2
    assert len(first.pages) == 2
    assert len(first.blocks) == 26
    assert len(first.tables) == 1
    assert (first.tables[0].num_rows, first.tables[0].num_columns) == (6, 2)
    assert len(first.tables[0].cells) == 12
    assert first.resource_limits.model_dump() == {
        "timeout_seconds": 120.0,
        "max_source_bytes": 52_428_800,
        "max_pages": 200,
    }

    sections = {section.title: section for section in first.sections}
    returns = sections["Returns and refunds"]
    return_window = sections["1. Return window"]
    operational = sections["Operational terms"]
    defective = sections["3. Defective items"]
    change_note = sections["Change note"]
    assert returns.level == 1
    assert return_window.parent_section_id == returns.section_id
    assert operational.parent_section_id == returns.section_id
    assert defective.parent_section_id == operational.section_id
    assert change_note.parent_section_id == operational.section_id

    writer = PdfWriter()
    for _page in range(201):
        writer.add_blank_page(width=72, height=72)
    oversized_page_stream = BytesIO()
    writer.write(oversized_page_stream)
    oversized_page_bytes = oversized_page_stream.getvalue()
    with pytest.raises(UnreadableDocument) as exc_info:
        parser.parse(
            PdfSource(
                path=Path("201-pages.pdf"),
                data=oversized_page_bytes,
                asset_sha256=hashlib.sha256(oversized_page_bytes).hexdigest(),
            )
        )
    rejection = str(exc_info.value)
    assert "201-pages.pdf" in rejection
    limit_detail = rejection.replace("201-pages.pdf", "", 1)
    assert "201" in limit_detail
    assert DOCLING_MAX_PAGES == 200
    assert "200" in limit_detail
