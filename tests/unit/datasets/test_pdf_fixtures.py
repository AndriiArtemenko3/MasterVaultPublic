"""Integrity gates for the bounded Larkstead PDF/change benchmark."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

from mastervault.document_intelligence.benchmark import (
    BenchmarkSplit,
    load_pdf_benchmark_manifest,
    load_pdf_benchmark_spec,
    repo_relative_path,
)
from mastervault.evals.pdf_benchmark import (
    PairClassification,
    load_change_impact_golden,
    load_pdf_layout_golden,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "datasets/larkstead/qa/generate_pdf_fixtures.py"
PDF_DIR = REPO_ROOT / "datasets/larkstead/pdf"
SPEC_PATH = PDF_DIR / "benchmark.yaml"
MANIFEST_PATH = PDF_DIR / "manifest.json"
LAYOUT_GOLDEN_PATH = REPO_ROOT / "datasets/larkstead/golden/pdf_layout.json"
CHANGE_GOLDEN_PATH = REPO_ROOT / "datasets/larkstead/golden/change_impact.yaml"
LEGACY_PDF = PDF_DIR / "sl2-policy-returns-v2-clean-digital.pdf"
LEGACY_SHA256 = "d12dc2de2b5a9fff9bba869c80cec305e5fc3744a1559302c3bbadf147e4332e"
FORBIDDEN_RUNTIME_KEYS = {
    "claim_evidence",
    "expected_affected_document_ids",
    "expected_claims",
    "expected_impacts",
    "expected_pair_classifications",
    "expected_relations",
    "ground_truth",
    "heading_level",
    "review_decision",
    "semantic_anchor",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_generator():
    spec = importlib.util.spec_from_file_location("larkstead_pdf_generator", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _pdf_text(path: Path) -> list[str]:
    return [" ".join((page.extract_text() or "").split()) for page in PdfReader(path).pages]


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "",
        " ",
        ".",
        "./.",
        "../outside.pdf",
        "safe/../../outside.pdf",
        "/tmp/outside.pdf",
        "C:/temp/outside.pdf",
        r"C:\temp\outside.pdf",
        "C:relative-but-drive-qualified.pdf",
        "//server/share/outside.pdf",
        r"\\server\share\outside.pdf",
        "safe/\x00outside.pdf",
    ],
)
def test_repo_relative_path_rejects_anchored_traversal_and_degenerate_paths(
    unsafe_path: str,
) -> None:
    with pytest.raises(ValueError, match="safe repository-relative path"):
        repo_relative_path(unsafe_path)


@pytest.mark.parametrize(
    ("portable_path", "expected"),
    [
        ("datasets/larkstead/pdf/example.pdf", "datasets/larkstead/pdf/example.pdf"),
        (r"datasets\larkstead\pdf\example.pdf", "datasets/larkstead/pdf/example.pdf"),
        ("./datasets/larkstead/pdf/example.pdf", "datasets/larkstead/pdf/example.pdf"),
    ],
)
def test_repo_relative_path_normalizes_safe_portable_paths(
    portable_path: str, expected: str
) -> None:
    assert repo_relative_path(portable_path) == expected


def test_committed_benchmark_is_deterministically_regenerated(tmp_path: Path) -> None:
    generator = _load_generator()
    generated = generator.generate_into(tmp_path)

    assert {path.name for path in generated.pdf_paths} == {
        path.name for path in PDF_DIR.glob("*.pdf")
    }
    for path in generated.pdf_paths:
        assert path.read_bytes() == (PDF_DIR / path.name).read_bytes()
    assert generated.manifest_path.read_bytes() == MANIFEST_PATH.read_bytes()
    assert generated.golden_path.read_bytes() == LAYOUT_GOLDEN_PATH.read_bytes()
    assert generator.check_committed_fixtures() == []


def test_runtime_manifest_is_bounded_family_separated_and_has_no_answers() -> None:
    spec = load_pdf_benchmark_spec(SPEC_PATH)
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    raw_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert len(spec.semantic_documents) == len(manifest.semantic_documents) == 6
    assert sum(len(document.renditions) for document in manifest.semantic_documents) == 24
    split_counts = {
        split: sum(document.split == split for document in manifest.semantic_documents)
        for split in BenchmarkSplit
    }
    assert split_counts == {BenchmarkSplit.DEVELOPMENT: 3, BenchmarkSplit.HELD_OUT: 3}
    assert len({document.document_family_id for document in manifest.semantic_documents}) == 6
    assert manifest.total_pdf_bytes == sum(
        rendition.pdf_bytes
        for document in manifest.semantic_documents
        for rendition in document.renditions
    )
    assert manifest.total_pdf_bytes < 1_048_576
    assert (
        max(
            rendition.pdf_bytes
            for document in manifest.semantic_documents
            for rendition in document.renditions
        )
        < 65_536
    )
    assert not (_keys(raw_manifest) & FORBIDDEN_RUNTIME_KEYS)
    assert "golden" not in MANIFEST_PATH.read_text(encoding="utf-8").lower()
    runtime_module = REPO_ROOT / "src/mastervault/document_intelligence/benchmark.py"
    assert not any(
        module == "mastervault.evals" or module.startswith("mastervault.evals.")
        for module in _imported_modules(runtime_module)
    )
    table_profile = next(
        profile for profile in spec.layout_profiles if profile.variant_id == "table-emphasis"
    )
    assert "table-forward-styling" in table_profile.layout_features
    assert "repeated-table-header" not in table_profile.layout_features


def test_runtime_manifest_hashes_sources_assets_and_render_contracts() -> None:
    generator = _load_generator()
    spec = load_pdf_benchmark_spec(SPEC_PATH)
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    spec_by_id = {document.semantic_document_id: document for document in spec.semantic_documents}

    assert manifest.spec_sha256 == _sha256(SPEC_PATH.read_bytes())
    assert manifest.generator.sha256 == _sha256(SCRIPT.read_bytes())
    assert manifest.generator.reportlab_version == generator.REPORTLAB_VERSION
    assert manifest.generator.cross_platform_byte_identity_promised is False
    for document in manifest.semantic_documents:
        source_path = REPO_ROOT / document.source_path
        assert document.source_sha256 == _sha256(source_path.read_bytes())
        semantic_source = generator.parse_semantic_source(
            spec_by_id[document.semantic_document_id], source_path.read_text(encoding="utf-8")
        )
        assert document.semantic_projection_sha256 == generator.semantic_projection_sha256(
            semantic_source
        )
        assert document.semantic_projection_sha256 != document.source_sha256
        assert document.source_bytes == source_path.stat().st_size
        for rendition in document.renditions:
            pdf_path = REPO_ROOT / rendition.pdf_path
            assert rendition.pdf_sha256 == _sha256(pdf_path.read_bytes())
            assert rendition.pdf_bytes == pdf_path.stat().st_size
            assert len(rendition.render_contract_sha256) == 64


def test_legacy_policy_fixture_bytes_and_sha_are_unchanged() -> None:
    assert LEGACY_PDF.stat().st_size == 5172
    assert _sha256(LEGACY_PDF.read_bytes()) == LEGACY_SHA256
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    rendition = next(
        rendition
        for document in manifest.semantic_documents
        if document.semantic_document_id == "sl2-policy-returns-v2"
        for rendition in document.renditions
        if rendition.variant_id == "single-column"
    )
    assert rendition.pdf_path.endswith("sl2-policy-returns-v2-clean-digital.pdf")
    assert rendition.pdf_sha256 == LEGACY_SHA256
    assert {"repeated-header", "repeated-footer", "page-number-footer"} <= set(
        rendition.layout_features
    )


def test_evaluator_layout_gold_is_separate_explicit_and_asset_complete() -> None:
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    golden = load_pdf_layout_golden(LAYOUT_GOLDEN_PATH)

    assert golden.manifest_sha256 == _sha256(MANIFEST_PATH.read_bytes())
    assert {document.semantic_document_id for document in golden.documents} == {
        document.semantic_document_id for document in manifest.semantic_documents
    }
    assert sum(len(document.renditions) for document in golden.documents) == 24
    assert any(
        block.heading_level is not None
        for document in golden.documents
        for rendition in document.renditions
        for block in rendition.blocks
    )
    assert any(
        table.num_rows >= 7 and table.num_columns >= 2
        for document in golden.documents
        for rendition in document.renditions
        for table in rendition.tables
    )

    for document in golden.documents:
        anchor_sets = [
            {block.semantic_anchor for block in rendition.blocks}
            for rendition in document.renditions
        ]
        assert all(anchors == anchor_sets[0] for anchors in anchor_sets[1:])
        for rendition in document.renditions:
            assert [block.reading_order for block in rendition.blocks] == list(
                range(1, len(rendition.blocks) + 1)
            )
            assert all("block-000" not in block.semantic_anchor for block in rendition.blocks)
            for table in rendition.tables:
                assert len(table.page_numbers) == 1
                assert len(table.cells) == table.num_rows * table.num_columns


def test_every_golden_evidence_quote_exists_on_its_declared_pdf_page() -> None:
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    golden = load_pdf_layout_golden(LAYOUT_GOLDEN_PATH)
    pdf_by_asset = {
        rendition.asset_id: REPO_ROOT / rendition.pdf_path
        for document in manifest.semantic_documents
        for rendition in document.renditions
    }
    for document in golden.documents:
        for rendition in document.renditions:
            pages = _pdf_text(pdf_by_asset[rendition.asset_id])
            for evidence in rendition.claim_evidence:
                quote = " ".join(evidence.quote.split())
                assert quote in pages[evidence.page_number - 1]


def test_every_declared_furniture_token_appears_on_every_rendition_page() -> None:
    manifest = load_pdf_benchmark_manifest(MANIFEST_PATH)
    golden = load_pdf_layout_golden(LAYOUT_GOLDEN_PATH)
    pdf_by_asset = {
        rendition.asset_id: REPO_ROOT / rendition.pdf_path
        for document in manifest.semantic_documents
        for rendition in document.renditions
    }

    for document in golden.documents:
        for rendition in document.renditions:
            pages = _pdf_text(pdf_by_asset[rendition.asset_id])
            for token in rendition.expected_furniture:
                assert all(_normalized_text(token) in page for page in pages)

    legacy = next(
        rendition
        for document in golden.documents
        if document.semantic_document_id == "sl2-policy-returns-v2"
        for rendition in document.renditions
        if rendition.variant_id == "single-column"
    )
    assert {
        "LARKSTEAD GOODS CO.",
        "CONTROLLED POLICY",
        "Internal operating policy | Effective 2026-01-12",
        "Page ",
    } == set(legacy.expected_furniture)


def test_change_impact_gold_preserves_temporal_overlay_and_hard_negatives() -> None:
    golden = load_change_impact_golden(CHANGE_GOLDEN_PATH)
    documents = {document.document_id: document for document in golden.documents}
    phases = {phase.phase_id: phase for phase in golden.temporal_phases}

    assert documents["sl2-policy-returns-v1"].effective_to == date(2026, 1, 11)
    assert phases["original-policy-window"].effective_to == date(2025, 11, 2)
    assert phases["holiday-exception"].announced_on == date(2025, 10, 27)
    assert phases["holiday-exception"].effective_from == date(2025, 11, 3)
    assert phases["holiday-exception"].effective_to == date(2026, 1, 11)
    assert phases["permanent-policy-v2"].effective_from == date(2026, 1, 12)
    assert phases["holiday-exception"].return_window_days == 45
    assert phases["permanent-policy-v2"].return_window_days == 45
    assert set(golden.expected_affected_document_ids) == {
        "sl2-faq-returns",
        "sl2-macros-returns-helprise",
        "process-showroom-demo-unit-rotation",
    }
    assert {dependency.edge_label for dependency in golden.dependencies} == {"DEPENDS_ON"}
    assert {pair.classification for pair in golden.expected_pair_classifications} == set(
        PairClassification
    )
    coexists = next(
        pair
        for pair in golden.expected_pair_classifications
        if pair.classification == PairClassification.COEXISTS
    )
    unrelated = next(
        pair
        for pair in golden.expected_pair_classifications
        if pair.classification == PairClassification.UNRELATED
    )
    assert coexists.target_document_id == "sl3-proposal-v1"
    assert coexists.edge_label is None
    assert unrelated.pair_scope == "claim"
    assert unrelated.edge_label is None
    assert unrelated.target_document_id == ("sop-returns-receiving-restock-grading")
    assert all(
        pair.edge_label == pair.classification.value
        for pair in golden.expected_pair_classifications
        if pair.classification in {PairClassification.SUPERSEDES, PairClassification.CONTRADICTS}
    )
    showroom = next(
        impact
        for impact in golden.expected_impacts
        if impact.target_document_id == "process-showroom-demo-unit-rotation"
    )
    expected_showroom_text = (
        "Open-box sales carry the standard 45-day refund window like any order "
        "(permanent effective 2026-01-12; in force since 2025-11-03)"
    )
    assert showroom.patches[0].proposed_after == expected_showroom_text
    assert showroom.patches[0].expected_after == expected_showroom_text


def test_macro_expected_patches_remove_all_stale_routing_without_collateral_changes() -> None:
    golden = load_change_impact_golden(CHANGE_GOLDEN_PATH)
    macro_impact = next(
        impact
        for impact in golden.expected_impacts
        if impact.target_document_id == "sl2-macros-returns-helprise"
    )
    expected_after = {
        "macro-ret-01-step": (
            "2. Delivery date 45 days or less from today: send macro RET-01. Reply text reads "
            '"within 45 days of delivery" and includes the restocking fee line: 10% on opened, '
            "non-defective returns."
        ),
        "macro-ret-02-step": (
            "3. Delivery date over 45 days from today: send macro RET-02. Reply text reads "
            '"outside our 45-day return window."'
        ),
        "macro-policy-reference": "- Returns and refunds policy, effective 2026-01-12",
    }
    assert {
        patch.patch_id: patch.expected_after for patch in macro_impact.patches
    } == expected_after
    assert any(patch.proposed_after != patch.expected_after for patch in macro_impact.patches), (
        "the seed's edit decision must require a real reviewer correction"
    )

    document = next(
        document
        for document in golden.documents
        if document.document_id == macro_impact.target_document_id
    )
    updated = _normalized_text((REPO_ROOT / document.source_path).read_text(encoding="utf-8"))
    for patch in macro_impact.patches:
        assert updated.count(patch.before) == 1
        updated = updated.replace(patch.before, patch.expected_after)

    assert "30-day" not in updated
    assert "30 days" not in updated
    assert "2024-01-15" not in updated
    assert "10% on opened, non-defective returns." in updated
    assert "Confirm the delivery date in ParcelPoint before quoting eligibility." in updated
    assert "Never quote from the order date or the ship date." in updated
    assert "ParcelPoint read access to pull delivery dates" in updated


def test_generator_check_mode_is_non_mutating_and_clean() -> None:
    before = {
        path: path.read_bytes()
        for path in [*PDF_DIR.glob("*.pdf"), MANIFEST_PATH, LAYOUT_GOLDEN_PATH]
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "6 families, 24 clean-digital PDFs" in result.stdout
    assert {path: path.read_bytes() for path in before} == before
