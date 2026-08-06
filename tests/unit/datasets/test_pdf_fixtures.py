"""Determinism and ground-truth checks for the shipped Larkstead PDF fixture."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "datasets/larkstead/qa/generate_pdf_fixtures.py"
PDF_DIR = REPO_ROOT / "datasets/larkstead/pdf"
PDF_PATH = PDF_DIR / "sl2-policy-returns-v2-clean-digital.pdf"
MANIFEST_PATH = PDF_DIR / "manifest.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("larkstead_pdf_generator", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_pdf_fixture_is_byte_reproducible(tmp_path):
    generator = _load_generator()

    generated_pdf, generated_manifest = generator.generate_into(tmp_path)

    assert generated_pdf.read_bytes() == PDF_PATH.read_bytes()
    assert generated_manifest.read_bytes() == MANIFEST_PATH.read_bytes()
    assert generator.check_committed_fixtures() == []


def test_pdf_manifest_declares_exact_bytes_and_semantic_source():
    generator = _load_generator()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["dataset"] == "larkstead"
    assert manifest["generator"]["deterministic_pdf"] is True
    assert manifest["generator"]["path"] == ("datasets/larkstead/qa/generate_pdf_fixtures.py")
    assert manifest["generator"]["reportlab_version"] == generator.REPORTLAB_VERSION
    assert manifest["generator"]["sha256"] == generator._sha256(
        generator.SCRIPT_DIR.joinpath("generate_pdf_fixtures.py").read_bytes()
    )
    assert len(manifest["documents"]) == 1
    document = manifest["documents"][0]
    assert document["document_id"] == "sl2-policy-returns-v2"
    assert document["document_family"] == "returns-policy"
    assert document["family_id"] == "SL2-refund-window-change"
    assert document["role"] == "parser-smoke"
    assert document["split"] == "development"
    assert document["variant"] == "clean-digital"
    assert document["source_sha256"] == generator._sha256(generator.SOURCE_PATH.read_bytes())
    assert document["pdf_sha256"] == generator._sha256(PDF_PATH.read_bytes())
    assert document["page_count"] == 2
    assert document["source_bytes"] == generator.SOURCE_PATH.stat().st_size
    assert document["pdf_bytes"] == PDF_PATH.stat().st_size
    assert document["native_text"] is True
    assert document["encrypted"] is False
    assert [page["block_id"] for page in document["pages"]] == [
        "page-0001-block-0001",
        "page-0002-block-0001",
    ]
    assert document["license"] == "CC-BY-4.0"


def test_pdf_pages_contain_declared_evidence_on_the_declared_page():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    document = manifest["documents"][0]
    reader = PdfReader(PDF_PATH)

    assert len(reader.pages) == document["page_count"] == 2
    page_text = [page.extract_text() or "" for page in reader.pages]
    for evidence in document["golden_evidence"]:
        expected_page = evidence["page"]
        assert evidence["block_id"] == f"page-{expected_page:04d}-block-0001"
        assert evidence["section"] in page_text[expected_page - 1]
        assert evidence["text"] in page_text[expected_page - 1]
        assert all(
            evidence["text"] not in text
            for index, text in enumerate(page_text, start=1)
            if index != expected_page
        )

    assert "Page 1 of 2" in page_text[0]
    assert "Page 2 of 2" in page_text[1]


def test_generator_check_mode_is_non_mutating_and_clean():
    before_pdf = PDF_PATH.read_bytes()
    before_manifest = MANIFEST_PATH.read_bytes()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 document, 2 pages, deterministic bytes verified" in result.stdout
    assert PDF_PATH.read_bytes() == before_pdf
    assert MANIFEST_PATH.read_bytes() == before_manifest
