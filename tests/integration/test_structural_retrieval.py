"""Keyless schema-v2 PDF -> structural row -> exact hydrated citation slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.cli.app import app
from mastervault.config import EmbeddingCfg, LLMCfg, PathsCfg, Settings, StorageCfg
from mastervault.contracts.page_grounded_claims import (
    EvidenceCandidate,
    PageGroundedClaimCandidate,
    PageGroundedClaimExtractionOut,
)
from mastervault.core.errors import EvidenceGroundingError
from mastervault.document_intelligence.docling_normalizer import normalize_docling_export
from mastervault.document_intelligence.structural_records import structural_records
from mastervault.models import Domain
from mastervault.pipelines.ask import run_ask
from mastervault.pipelines.ingest import run_ingest
from mastervault.providers.embedding import MockEmbedding
from mastervault.providers.llm import MockLLM
from mastervault.retrieval.search import hybrid_search
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import sync_vault

pytestmark = pytest.mark.integration

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)


def _prov(left: float, top: float, right: float, bottom: float) -> list[dict]:
    return [{
        "page_no": 1,
        "bbox": {
            "l": left, "t": top, "r": right, "b": bottom,
            "coord_origin": "TOPLEFT",
        },
    }]


def _cell(row: int, column: int, text: str) -> dict:
    return {
        "bbox": {
            "l": 50 + column * 220, "t": 180 + row * 30,
            "r": 250 + column * 220, "b": 205 + row * 30,
            "coord_origin": "TOPLEFT",
        },
        "row_span": 1,
        "col_span": 1,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": column,
        "end_col_offset_idx": column + 1,
        "text": text,
        "column_header": row == 0,
        "row_header": column == 0 and row > 0,
    }


def _return_policy_document(asset_sha256: str):
    cells = [
        _cell(0, 0, "Customer tier"), _cell(0, 1, "Return window"),
        _cell(1, 0, "Standard"), _cell(1, 1, "30 days"),
        _cell(2, 0, "Premium"), _cell(2, 1, "45 days"),
    ]
    by_slot = {
        (cell["start_row_offset_idx"], cell["start_col_offset_idx"]): cell
        for cell in cells
    }
    export = {
        "pages": {"1": {"size": {"width": 612.0, "height": 792.0}}},
        "texts": [
            {
                "label": "section_header", "level": 1,
                "text": "Returns and refunds", "prov": _prov(50, 80, 560, 110),
            },
            {
                "label": "text",
                "text": "Return windows vary by customer tier.",
                "prov": _prov(50, 125, 560, 150),
            },
        ],
        "tables": [{
            "label": "table", "prov": _prov(50, 170, 500, 280),
            "data": {
                "num_rows": 3, "num_cols": 2, "table_cells": cells,
                "grid": [[by_slot[(row, column)] for column in range(2)] for row in range(3)],
            },
        }],
    }
    return normalize_docling_export(
        export,
        asset_sha256=asset_sha256,
        parser_version="2.118.0",
        parser_core_version="2.91.0",
        model_identity="sha256:" + "b" * 64,
    )


def _table_document(cells: list[dict], grid: list[list[dict]]):
    return normalize_docling_export(
        {
            "pages": {"1": {"size": {"width": 612.0, "height": 792.0}}},
            "texts": [],
            "tables": [{
                "label": "table", "prov": _prov(50, 170, 500, 320),
                "data": {
                    "num_rows": len(grid), "num_cols": len(grid[0]),
                    "table_cells": cells, "grid": grid,
                },
            }],
        },
        asset_sha256="d" * 64,
        parser_version="2.118.0",
        parser_core_version="2.91.0",
        model_identity="sha256:" + "e" * 64,
    )


class _ReplayParser:
    name = "docling"
    parser_version = "2.118.0"
    profile = "clean-digital-layout-table-v2"

    def parse(self, source):
        return _return_policy_document(source.asset_sha256)


def _environment(tmp_path: Path):
    settings = Settings(
        paths=PathsCfg(workspace=tmp_path / "workspace"),
        storage=StorageCfg(backend="sqlite"),
        embedding=EmbeddingCfg(provider="mock"),
        llm=LLMCfg(provider="mock"),
    )
    embedder = MockEmbedding()
    backend = SqliteBackend(settings.paths.sqlite_path)
    backend.init_schema(embedder.dimensions, embedder.model_version)
    return settings, backend, embedder


def _ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings, backend, embedder = _environment(tmp_path)
    monkeypatch.setattr(
        "mastervault.pipelines.ingest.make_document_parser",
        lambda *_args, **_kwargs: _ReplayParser(),
    )
    llm = MockLLM()
    llm.push(
        "page_grounded_claim_extraction",
        PageGroundedClaimExtractionOut(claims=[PageGroundedClaimCandidate(
            statement="Premium customers have a 45-day return window.",
            confidence="high",
            evidence=[EvidenceCandidate(cell_id="cell-0006", quote="45 days")],
        )]),
    )
    outcome = run_ingest(
        FIXTURE, Domain.CUSTOMER_SUPPORT, settings, backend, embedder, llm,
        pdf_parser_name="docling",
    )
    assert outcome.exit_code == 0, outcome.summary
    return settings, backend, embedder


def test_structural_ids_bind_asset_artifact_owner_and_location() -> None:
    document = _return_policy_document("a" * 64)
    first = structural_records(
        document,
        doc_id="source:customer-support/sources/first.md",
        domain="customer-support",
        parsed_artifact_sha256="b" * 64,
    )
    second_owner = structural_records(
        document,
        doc_id="source:customer-support/sources/second.md",
        domain="customer-support",
        parsed_artifact_sha256="b" * 64,
    )
    reparsed = structural_records(
        document,
        doc_id="source:customer-support/sources/first.md",
        domain="customer-support",
        parsed_artifact_sha256="c" * 64,
    )
    assert len(first) == len(second_owner) == len(reparsed)
    assert set(row.record_id for row in first).isdisjoint(
        row.record_id for row in second_owner
    )
    assert set(row.record_id for row in first).isdisjoint(
        row.record_id for row in reparsed
    )


@pytest.mark.parametrize("fully_covered", [False, True])
def test_table_rows_include_partial_and_full_row_span_occupancy(
    fully_covered: bool,
) -> None:
    header_tier = _cell(0, 0, "Customer tier")
    header_window = _cell(0, 1, "Return window")
    tier = _cell(1, 0, "Premium")
    tier.update(row_span=2, end_row_offset_idx=3)
    window = _cell(1, 1, "45 days")
    cells = [header_tier, header_window, tier, window]
    if fully_covered:
        window.update(row_span=2, end_row_offset_idx=3)
        final_window = window
    else:
        final_window = _cell(2, 1, "No fee")
        cells.append(final_window)
    document = _table_document(
        cells,
        [
            [header_tier, header_window],
            [tier, window],
            [tier, final_window],
        ],
    )
    rows = {
        row.row_id: row
        for row in structural_records(
            document,
            doc_id="source:customer-support/sources/spans.md",
            domain="customer-support",
            parsed_artifact_sha256="f" * 64,
        )
        if row.record_kind == "table_row"
    }
    covered = rows["row-0003"]
    assert "Customer tier: Premium" in covered.text
    assert covered.evidence
    assert "cell-0003" in covered.cell_ids
    spanning_tier = next(item for item in covered.evidence if item.cell_id == "cell-0003")
    assert (spanning_tier.row_id, spanning_tier.row_index) == ("row-0002", 1)
    if fully_covered:
        assert "Return window: 45 days" in covered.text
        assert covered.cell_ids == ["cell-0003", "cell-0004"]
        assert {item.cell_id for item in covered.evidence} == {
            "cell-0001",
            "cell-0002",
            "cell-0003",
            "cell-0004",
        }
    else:
        assert "Return window: No fee" in covered.text
        assert covered.cell_ids == ["cell-0003", "cell-0005"]
        assert {item.cell_id for item in covered.evidence} == {
            "cell-0001",
            "cell-0002",
            "cell-0003",
            "cell-0005",
        }


def test_structurally_blank_row_is_not_emitted_without_evidence() -> None:
    header_tier = _cell(0, 0, "Customer tier")
    header_window = _cell(0, 1, "Return window")
    blank_tier = _cell(1, 0, "")
    blank_window = _cell(1, 1, "")
    blank_tier.pop("bbox")
    blank_window.pop("bbox")
    document = _table_document(
        [header_tier, header_window, blank_tier, blank_window],
        [[header_tier, header_window], [blank_tier, blank_window]],
    )
    rows = [
        row
        for row in structural_records(
            document,
            doc_id="source:customer-support/sources/blank.md",
            domain="customer-support",
            parsed_artifact_sha256="f" * 64,
        )
        if row.record_kind == "table_row"
    ]
    assert [row.row_id for row in rows] == ["row-0001"]
    assert all(row.evidence for row in rows)


def test_return_policy_pdf_replay_retrieves_header_scoped_row_and_exact_cli_citation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, backend, embedder = _ingest(tmp_path, monkeypatch)
    result = hybrid_search("Premium 45 days", settings, backend, embedder, k=10)
    row_hit = next(hit for hit in result.hits if hit.structural_kind == "table_row")
    claim_hit = next(hit for hit in result.hits if hit.record_type.value == "claim")
    assert row_hit.source_identity is not None
    owner = hashlib.sha256(row_hit.doc_id.encode("utf-8")).hexdigest()
    assert row_hit.record_id == (
        f"struct:{row_hit.source_identity['asset_sha256']}:artifact:"
        f"{row_hit.source_identity['parsed_artifact_sha256']}:owner:{owner}:"
        "table:table-0001:row:row-0003"
    )
    assert row_hit.channels.structural == 1
    assert row_hit.text.endswith("Customer tier: Premium | Return window: 45 days")
    assert row_hit.text != "45 days"
    assert row_hit.source_identity["asset_sha256"] == row_hit.evidence[0].asset_sha256
    assert {item.cell_id for item in row_hit.evidence} == {
        "cell-0001", "cell-0002", "cell-0005", "cell-0006"
    }
    assert {item.row_id for item in row_hit.evidence} == {
        "row-0001", "row-0003"
    }
    assert {item.page_number for item in row_hit.evidence} == {1}
    assert claim_hit.evidence[0].model_dump(mode="json") == {
        "schema_version": 2,
        "target_type": "cell",
        "asset_sha256": row_hit.source_identity["asset_sha256"],
        "page_number": 1,
        "block_id": "block-0003",
        "table_id": "table-0001",
        "row_id": "row-0003",
        "cell_id": "cell-0006",
        "row_index": 2,
        "column_index": 1,
        "bbox": {
            "origin": "top-left", "x0": 0.441176, "y0": 0.30303,
            "x1": 0.767974, "y1": 0.334596,
        },
        "quote": "45 days", "start_char": 0, "end_char": 7,
    }

    backend.close()
    cli = CliRunner().invoke(
        app,
        ["search", "Premium 45 days", "--type", "structural", "--json"],
        env={
            "MV_PATHS__WORKSPACE": str(settings.paths.workspace),
            "MV_STORAGE__BACKEND": "sqlite",
            "MV_EMBEDDING__PROVIDER": "mock",
            "MV_LLM__PROVIDER": "mock",
        },
    )
    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.output)
    assert payload["hits"][0]["structural_kind"] == "table_row"
    assert payload["hits"][0]["channels"]["structural"] == 1
    assert payload["hits"][0]["evidence"][-1]["cell_id"] == "cell-0006"
    assert payload["hits"][0]["evidence"][-1]["quote"] == "45 days"


def test_structural_sync_is_idempotent_and_empty_legacy_channel_shape_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, backend, embedder = _ingest(tmp_path, monkeypatch)
    first_ids = [
        row[0] for row in backend.conn.execute(
            "SELECT record_id FROM structural_records ORDER BY ordinal"
        )
    ]
    report = sync_vault(settings.paths.vault_dir, backend, embedder)
    second_ids = [
        row[0] for row in backend.conn.execute(
            "SELECT record_id FROM structural_records ORDER BY ordinal"
        )
    ]
    assert first_ids == second_ids
    assert report.records_embedded == 0
    backend.close()

    legacy_settings, legacy_backend, legacy_embedder = _environment(tmp_path / "legacy")
    empty = hybrid_search("Premium 45 days", legacy_settings, legacy_backend, legacy_embedder)
    assert empty.channel_counts == {
        "lexical_claims": 0,
        "lexical_docs": 0,
        "vector": 0,
        "graph": 0,
    }
    legacy_backend.close()


def test_run_ask_and_cli_json_return_exact_structural_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, backend, embedder = _ingest(tmp_path, monkeypatch)
    outcome = run_ask(
        "Premium 45 days",
        settings,
        backend,
        embedder,
        MockLLM(),
        max_rounds=1,
    )
    structural_source = next(
        item for item in outcome.sources if item["record_id"].startswith("struct:")
    )
    assert structural_source["source_identity"]["asset_sha256"]
    assert {item["cell_id"] for item in structural_source["evidence"]} == {
        "cell-0001", "cell-0002", "cell-0005", "cell-0006"
    }
    grounded_claim = next(
        item for item in outcome.sources if item["record_id"].startswith("claim:")
    )
    assert grounded_claim["evidence"][0]["cell_id"] == "cell-0006"

    backend.close()
    cli = CliRunner().invoke(
        app,
        ["ask", "Premium 45 days", "--max-rounds", "1", "--json"],
        env={
            "MV_PATHS__WORKSPACE": str(settings.paths.workspace),
            "MV_STORAGE__BACKEND": "sqlite",
            "MV_EMBEDDING__PROVIDER": "mock",
            "MV_LLM__PROVIDER": "mock",
        },
    )
    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.output)
    cli_structural = next(
        item for item in payload["sources"] if item["record_id"].startswith("struct:")
    )
    assert cli_structural["evidence"][-1]["quote"] == "45 days"
    assert cli_structural["source_identity"]["parsed_artifact_sha256"]


@pytest.mark.parametrize("tamper", ["page", "asset", "parser", "cell"])
def test_structural_hydration_and_citation_gate_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    settings, backend, embedder = _ingest(tmp_path, monkeypatch)
    result = hybrid_search("Premium 45 days", settings, backend, embedder, k=10)
    row_hit = next(hit for hit in result.hits if hit.structural_kind == "table_row")
    with backend.conn:
        if tamper == "page":
            backend.conn.execute(
                "UPDATE structural_records SET page_number = 99 WHERE record_id = ?",
                (row_hit.record_id,),
            )
        elif tamper == "asset":
            backend.conn.execute(
                "UPDATE structural_records SET asset_sha256 = ? WHERE record_id = ?",
                ("0" * 64, row_hit.record_id),
            )
        elif tamper == "parser":
            backend.conn.execute(
                "UPDATE structural_records SET parser = 'forged' WHERE record_id = ?",
                (row_hit.record_id,),
            )
        else:
            raw = backend.conn.execute(
                "SELECT evidence FROM structural_records WHERE record_id = ?",
                (row_hit.record_id,),
            ).fetchone()[0]
            evidence = json.loads(raw)
            evidence[-1]["cell_id"] = "cell-9999"
            backend.conn.execute(
                "UPDATE structural_records SET evidence = ? WHERE record_id = ?",
                (json.dumps(evidence), row_hit.record_id),
            )
    with pytest.raises(
        EvidenceGroundingError,
        match="does not match its verified parsed document",
    ):
        hybrid_search("Premium 45 days", settings, backend, embedder, k=10)
    backend.close()
