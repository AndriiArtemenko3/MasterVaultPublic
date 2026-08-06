"""Clean digital PDF -> immutable asset -> grounded claim -> verified hit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from typer.testing import CliRunner

from mastervault.cli.app import app
from mastervault.config import EmbeddingCfg, LLMCfg, PathsCfg, Settings, StorageCfg
from mastervault.contracts.page_grounded_claims import (
    EvidenceCandidate,
    PageGroundedClaimCandidate,
    PageGroundedClaimExtractionOut,
)
from mastervault.core.errors import EXIT_CODES, DocumentIntegrityError
from mastervault.document_intelligence import load_parsed_document, verify_source_asset
from mastervault.evidence import resolve_claim_evidence
from mastervault.ingest.convert import read_raw_text
from mastervault.models import Domain
from mastervault.pipelines.ingest import run_ingest
from mastervault.providers.embedding import MockEmbedding
from mastervault.providers.llm import MockLLM
from mastervault.retrieval.search import hybrid_search
from mastervault.storage.sqlite import SqliteBackend
from mastervault.vaultfs.notes import read_note

pytestmark = pytest.mark.integration

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
QUOTE = "Customers may return any item within 45 days of the delivery date."


class _BackendParityEmbedding(MockEmbedding):
    @property
    def model_version(self) -> str:
        return "test-embed-v1"


def _environment(tmp_path: Path):
    workspace = tmp_path / "workspace"
    settings = Settings(
        paths=PathsCfg(workspace=workspace),
        storage=StorageCfg(backend="sqlite"),
        embedding=EmbeddingCfg(provider="mock"),
        llm=LLMCfg(provider="mock"),
    )
    embedder = MockEmbedding(8)
    backend = SqliteBackend(settings.paths.sqlite_path)
    backend.init_schema(embedder.dimensions, embedder.model_version)
    return settings, backend, embedder, MockLLM()


def _grounded_output() -> PageGroundedClaimExtractionOut:
    return PageGroundedClaimExtractionOut(
        claims=[
            PageGroundedClaimCandidate(
                statement=QUOTE,
                confidence="high",
                affects_candidates=[],
                evidence=[
                    EvidenceCandidate(
                        block_id="page-0001-block-0001",
                        quote=QUOTE,
                    )
                ],
            )
        ]
    )


def _byte_distinct_copy(source: Path, target: Path, label: str) -> None:
    writer = PdfWriter()
    for page in PdfReader(str(source)).pages:
        writer.add_page(page)
    writer.add_metadata({"/MasterVaultVariant": label})
    with target.open("wb") as handle:
        writer.write(handle)


def test_pdf_ingest_resolves_source_note_search_hit_and_cli(
    tmp_path: Path,
) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    llm.push("page_grounded_claim_extraction", _grounded_output())
    outcome = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert outcome.exit_code == EXIT_CODES["ok"]
    assert outcome.summary["units_completed"] == 1

    note_path = next((settings.paths.vault_dir / "customer-support/sources").glob("*.md"))
    note = read_note(note_path).model
    assert note.source_asset is not None
    assert note.parsed_document is not None
    assert len(note.key_claims) == 1
    assert note.key_claims[0].evidence[0].page_number == 1
    verify_source_asset(note.source_asset, settings.paths.workspace)
    parsed = load_parsed_document(note.parsed_document, settings.paths.workspace)
    assert parsed.pages[0].blocks[0].block_id == "page-0001-block-0001"

    bundle = resolve_claim_evidence(note.key_claims[0].id, backend, settings.paths.workspace)
    assert bundle.evidence[0].quote == QUOTE
    result = hybrid_search(QUOTE, settings, backend, embedder, k=5)
    claim_hit = next(hit for hit in result.hits if hit.record_id.startswith("claim:"))
    assert claim_hit.evidence == bundle.evidence

    cli = CliRunner().invoke(
        app,
        ["evidence", "show", note.key_claims[0].id, "--json"],
        env={
            "MV_PATHS__WORKSPACE": str(settings.paths.workspace),
            "MV_STORAGE__BACKEND": "sqlite",
            "MV_EMBEDDING__PROVIDER": "mock",
            "MV_LLM__PROVIDER": "mock",
        },
    )
    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.output)
    assert payload["evidence"][0]["page_number"] == 1
    assert payload["evidence"][0]["quote"] == QUOTE
    backend.close()


def test_normal_docling_selection_parses_each_pdf_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planning and execution share one parse; resume has a separate parse gate."""
    from mastervault.document_intelligence import PypdfParser

    settings, backend, embedder, llm = _environment(tmp_path)
    llm.push("page_grounded_claim_extraction", _grounded_output())

    class CountingParser:
        name = "docling"
        parser_version = "test"
        profile = "test"

        def __init__(self) -> None:
            self.calls = 0

        def parse(self, source):
            self.calls += 1
            return PypdfParser().parse(source)

    parser = CountingParser()
    monkeypatch.setattr(
        "mastervault.pipelines.ingest.make_document_parser",
        lambda *_args, **_kwargs: parser,
    )
    outcome = run_ingest(
        FIXTURE,
        Domain.CUSTOMER_SUPPORT,
        settings,
        backend,
        embedder,
        llm,
        pdf_parser_name="docling",
    )
    assert outcome.exit_code == EXIT_CODES["ok"]
    assert parser.calls == 1
    plan = json.loads((outcome.run_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["args"]["pdf_parser"] == "docling"
    backend.close()


def test_pdf_byte_identity_dedupes_exact_bytes_but_not_equivalent_text(
    tmp_path: Path,
) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    first = tmp_path / "policy-original.pdf"
    first.write_bytes(FIXTURE.read_bytes())
    llm.push("page_grounded_claim_extraction", _grounded_output())
    initial = run_ingest(first, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert initial.summary["units_completed"] == 1

    same = tmp_path / "renamed-identical.pdf"
    same.write_bytes(FIXTURE.read_bytes())
    calls_before = len(llm.calls)
    duplicate = run_ingest(same, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert duplicate.summary["units_total"] == 0
    assert len(llm.calls) == calls_before

    variant = tmp_path / "policy-byte-distinct.pdf"
    _byte_distinct_copy(first, variant, "same-text-different-bytes")
    assert variant.read_bytes() != first.read_bytes()
    assert read_raw_text(variant) == read_raw_text(first)
    llm.push("page_grounded_claim_extraction", _grounded_output())
    distinct = run_ingest(variant, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert distinct.summary["units_completed"] == 1
    notes = sorted((settings.paths.vault_dir / "customer-support/sources").glob("*.md"))
    assert len(notes) == 2
    asset_hashes = {read_note(path).model.source_asset.asset_sha256 for path in notes}
    assert len(asset_hashes) == 2
    backend.close()


def test_exact_pdf_reingest_repairs_a_missing_immutable_chain(tmp_path: Path) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    llm.push("page_grounded_claim_extraction", _grounded_output())
    first = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert first.exit_code == EXIT_CODES["ok"]
    note_path = next((settings.paths.vault_dir / "customer-support/sources").glob("*.md"))
    note = read_note(note_path).model
    assert note.source_asset is not None
    asset_path = verify_source_asset(note.source_asset, settings.paths.workspace)
    asset_path.unlink()

    llm.push("page_grounded_claim_extraction", _grounded_output())
    repaired = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert repaired.exit_code == EXIT_CODES["ok"]
    assert repaired.summary["units_total"] == 1
    assert repaired.summary["units_completed"] == 1
    verify_source_asset(note.source_asset, settings.paths.workspace)
    backend.close()


def test_exact_pdf_reingest_reports_immutable_tampering(tmp_path: Path) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    llm.push("page_grounded_claim_extraction", _grounded_output())
    first = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert first.exit_code == EXIT_CODES["ok"]
    note_path = next((settings.paths.vault_dir / "customer-support/sources").glob("*.md"))
    note = read_note(note_path).model
    assert note.source_asset is not None
    asset_path = verify_source_asset(note.source_asset, settings.paths.workspace)
    asset_path.write_bytes(b"tampered")

    llm.push("page_grounded_claim_extraction", _grounded_output())
    rejected = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert rejected.exit_code == EXIT_CODES["completed-with-failures"]
    assert rejected.summary["units_total"] == 1
    assert rejected.summary["units_completed"] == 0
    backend.close()


def test_pdf_ingest_and_evidence_hydration_have_backend_parity(tmp_path: Path, backend) -> None:
    settings = Settings(
        paths=PathsCfg(workspace=tmp_path / "workspace"),
        storage=StorageCfg(backend=backend.name),
        embedding=EmbeddingCfg(provider="mock"),
        llm=LLMCfg(provider="mock"),
    )
    embedder = _BackendParityEmbedding(8)
    llm = MockLLM()
    llm.push("page_grounded_claim_extraction", _grounded_output())

    outcome = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert outcome.exit_code == EXIT_CODES["ok"]
    claim = backend.get_claims(["sl2-policy-returns-v2-clean-digital-01"])[0]
    bundle = resolve_claim_evidence(claim.claim_id, backend, settings.paths.workspace)
    assert bundle.evidence[0].quote == QUOTE
    hit = next(
        item
        for item in hybrid_search(QUOTE, settings, backend, embedder, k=5).hits
        if item.record_id == f"claim:{claim.claim_id}"
    )
    assert hit.evidence == bundle.evidence


def test_pdf_resume_uses_byte_hash_even_when_extracted_text_is_unchanged(
    tmp_path: Path,
) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    source = tmp_path / "resume-policy.pdf"
    source.write_bytes(FIXTURE.read_bytes())
    llm.push("page_grounded_claim_extraction", _grounded_output())
    frozen_text = read_raw_text(source)
    exhausted = run_ingest(
        source,
        Domain.CUSTOMER_SUPPORT,
        settings,
        backend,
        embedder,
        llm,
        budget_usd=0.0,
    )
    assert exhausted.exit_code == EXIT_CODES["budget-exhausted"]

    _byte_distinct_copy(FIXTURE, source, "resume-drift")
    assert read_raw_text(source) == frozen_text
    resumed = run_ingest(
        source,
        Domain.CUSTOMER_SUPPORT,
        settings,
        backend,
        embedder,
        llm,
        resume_run_id=exhausted.run_id,
        budget_usd=100.0,
    )
    assert resumed.exit_code == EXIT_CODES["resume-conflict"]
    assert "source PDF bytes changed" in resumed.summary["error"]
    backend.close()


def test_forged_pdf_evidence_creates_no_canonical_note_or_asset(
    tmp_path: Path,
) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    forged = PageGroundedClaimExtractionOut(
        claims=[
            PageGroundedClaimCandidate(
                statement=QUOTE,
                confidence="high",
                evidence=[
                    EvidenceCandidate(
                        block_id="page-0001-block-0001",
                        quote="This sentence is not in the PDF.",
                    )
                ],
            )
        ]
    )
    llm.push("page_grounded_claim_extraction", forged)
    llm.push("page_grounded_claim_extraction", forged)
    outcome = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert outcome.exit_code == EXIT_CODES["completed-with-failures"]
    assert not any(settings.paths.vault_dir.rglob("*.md"))
    assert not settings.paths.assets_dir.exists()
    assert not settings.paths.parsed_documents_dir.exists()
    assert backend.stats()["counts"]["claims"] == 0
    backend.close()


def test_textless_pdf_cli_fails_visibly_instead_of_reporting_success(tmp_path: Path) -> None:
    settings, backend, _embedder, _llm = _environment(tmp_path)
    textless = tmp_path / "textless.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with textless.open("wb") as handle:
        writer.write(handle)
    backend.close()

    cli = CliRunner().invoke(
        app,
        ["ingest", str(textless), "--domain", "customer-support"],
        env={
            "MV_PATHS__WORKSPACE": str(settings.paths.workspace),
            "MV_STORAGE__BACKEND": "sqlite",
            "MV_EMBEDDING__PROVIDER": "mock",
            "MV_LLM__PROVIDER": "mock",
        },
    )
    assert cli.exit_code == EXIT_CODES["completed-with-failures"]
    assert "unreadable inputs" in cli.output
    assert "no extractable native text" in cli.output


def test_indexed_pdf_claim_cannot_be_downgraded_to_legacy_evidence(
    tmp_path: Path,
) -> None:
    settings, backend, embedder, llm = _environment(tmp_path)
    llm.push("page_grounded_claim_extraction", _grounded_output())
    outcome = run_ingest(FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm)
    assert outcome.exit_code == EXIT_CODES["ok"]

    note_path = next((settings.paths.vault_dir / "customer-support/sources").glob("*.md"))
    note = read_note(note_path).model
    claim_id = note.key_claims[0].id
    parent = backend.get_claims([claim_id])[0].doc_id
    row = backend.get_documents([parent])[0]
    tampered = json.loads(json.dumps(row.frontmatter))
    tampered["key_claims"][0].pop("evidence")
    with backend.conn:
        backend.conn.execute(
            "UPDATE documents SET frontmatter = ? WHERE doc_id = ?",
            (json.dumps(tampered), parent),
        )

    with pytest.raises(DocumentIntegrityError, match="frontmatter does not validate"):
        resolve_claim_evidence(claim_id, backend, settings.paths.workspace)
    with pytest.raises(DocumentIntegrityError, match="frontmatter does not validate"):
        hybrid_search(QUOTE, settings, backend, embedder, k=5)
    backend.close()
