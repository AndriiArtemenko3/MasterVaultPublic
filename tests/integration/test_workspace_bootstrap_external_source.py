"""Production-path acceptance for explicit external bootstrap source roots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.workspace_bootstrap_repository import BootstrapSourceRoot
from mastervault.config import Settings
from mastervault.contracts.claims import ClaimCandidate, ClaimExtractionOut
from mastervault.core.errors import EXIT_CODES
from mastervault.models import Confidence, Domain
from mastervault.pipelines.ingest import run_ingest
from mastervault.providers import MockEmbedding
from mastervault.providers.llm import MockLLM
from mastervault.storage.sqlite import SqliteBackend
from mastervault.vaultfs.frontmatter import parse_frontmatter

pytestmark = pytest.mark.integration


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot(
    *, workspace: Path, manifest: Path, raw_source: Path
) -> dict[str, tuple[int, bytes]]:
    paths = [
        *sorted((workspace / "vault").rglob("*.md")),
        workspace / "index.db",
        manifest,
        raw_source,
    ]
    return {
        str(path): (path.stat().st_ino, path.read_bytes())
        for path in paths
    }


def _assert_exact_state_cardinality(state_db: Path) -> None:
    expected = {
        "change_control_aggregates": 1,
        "change_control_operations": 1,
        "change_control_document_versions": 1,
        "change_control_claim_identities": 1,
        "change_control_claim_revisions": 1,
        "change_control_claim_scopes": 1,
        "change_control_claim_pairs": 0,
        "change_control_relation_assessments": 0,
        "change_control_dependencies": 0,
        "change_control_document_replacements": 0,
        "change_control_temporal_constraints": 0,
        "change_control_workspace_bootstrap_intents": 1,
        "change_control_workspace_inventories": 1,
        "change_control_workspace_inventory_receipts": 1,
        "change_control_legacy_index_readiness_receipts": 1,
        "change_control_generation_manifests": 1,
        "change_control_active_generation": 1,
        "change_control_schema_migrations": 5,
        "change_control_operator_runs": 1,
        "change_control_operator_run_links": 4,
    }
    connection = sqlite3.connect(state_db)
    try:
        actual = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in expected
        }
    finally:
        connection.close()
    assert actual == expected


def test_real_ingest_bootstraps_external_source_without_rewriting_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external_root = tmp_path / "operator inputs Ω"
    external_root.mkdir(mode=0o700)
    raw_source = external_root / "Policy Source.md"
    raw_source.write_text(
        "Returns are accepted for thirty days when the product is unused.\n",
        encoding="utf-8",
    )
    raw_source.chmod(0o600)

    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {"provider": "mock"},
            "paths": {"workspace": workspace},
        }
    )
    embedder = MockEmbedding()
    backend = SqliteBackend(workspace / "index.db")
    backend.init_schema(embedder.dimensions, embedder.model_version)
    llm = MockLLM()
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Unused products are returnable for thirty days.",
                    confidence=Confidence.HIGH,
                    affects_candidates=[],
                )
            ]
        ),
    )
    frozen_now = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)
    try:
        outcome = run_ingest(
            raw_source,
            Domain.CUSTOMER_SUPPORT,
            settings,
            backend,
            embedder,
            llm,
            clock=lambda: frozen_now,
        )
        assert outcome.exit_code == EXIT_CODES["ok"]
        assert outcome.summary["units_completed"] == 1
        assert backend.stats()["counts"] == {
            "documents": 1,
            "claims": 1,
            "claim_affects": 0,
            "wiki_aliases": 0,
            "chunks": 3,
            "embeddings": 4,
            "structural_records": 0,
        }
    finally:
        backend.close()

    note_path = next((workspace / "vault").rglob("*.md"))
    note_bytes = note_path.read_bytes()
    note_frontmatter, _body = parse_frontmatter(note_bytes.decode("utf-8"))
    exact_provenance = str(raw_source)
    assert note_frontmatter["provenance"] == exact_provenance
    assert not raw_source.is_relative_to(workspace)

    raw_bytes = raw_source.read_bytes()
    index_bytes = (workspace / "index.db").read_bytes()
    manifest_payload = {
        "schema_version": 1,
        "aggregate_id": "production-ingest-bootstrap",
        "legacy_index_file_sha256": _sha256(index_bytes),
        "legacy_index_file_byte_count": len(index_bytes),
        "managed_source_notes": [
            {
                "logical_path": note_path.relative_to(workspace / "vault").as_posix(),
                "source_note_sha256": _sha256(note_bytes),
                "source_note_byte_count": len(note_bytes),
                "source_root_id": "ingest",
                "source_relative_path": raw_source.relative_to(external_root).as_posix(),
                "source_note_provenance": exact_provenance,
                "raw_source_sha256": _sha256(raw_bytes),
                "raw_source_byte_count": len(raw_bytes),
                "document_id": "policy-source",
                "document_family": "policy-source",
                "version_label": "v1",
                "declared_effective_from": "2026-08-12",
                "declared_effective_to": None,
                "role": "policy",
                "authority": "primary",
            }
        ],
    }
    manifest = workspace / "bootstrap.yaml"
    manifest.write_text(
        yaml.safe_dump(manifest_payload, sort_keys=False),
        encoding="utf-8",
    )
    evidence_before = _snapshot(
        workspace=workspace,
        manifest=manifest,
        raw_source=raw_source,
    )

    source_roots = (BootstrapSourceRoot(root_id="ingest", path=external_root),)
    application = ChangeControlApplication(settings)
    first = application.bootstrap(
        manifest,
        "workspace-bootstrap:production-ingest-v1",
        source_roots=source_roots,
    )
    replay = application.bootstrap(
        manifest,
        "workspace-bootstrap:production-ingest-v1",
        source_roots=source_roots,
    )

    assert replay == first
    assert _snapshot(
        workspace=workspace,
        manifest=manifest,
        raw_source=raw_source,
    ) == evidence_before
    assert not (workspace / "raw").exists()

    authority_digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.workspace-bootstrap-source.v1",
                "source_root_id": "ingest",
                "source_relative_path": "Policy Source.md",
                "source_note_provenance": exact_provenance,
            }
        )
    ).hexdigest()
    authority_locator = f"bootstrap-sources/ingest/{authority_digest}"

    state_db = workspace / "change_control" / "state.sqlite3"
    connection = sqlite3.connect(state_db)
    try:
        inventory_payload = json.loads(
            str(
                connection.execute(
                    "SELECT payload_json FROM change_control_workspace_inventories"
                ).fetchone()[0]
            )
        )
        persisted_source_path = str(
            connection.execute(
                "SELECT source_path FROM change_control_document_versions"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    persisted = inventory_payload["managed_source_notes"][0]
    assert persisted["source_root_id"] == "ingest"
    assert persisted["source_relative_path"] == "Policy Source.md"
    assert persisted["source_note_provenance"] == exact_provenance
    assert persisted["raw_source_path"] == authority_locator
    assert persisted["document"]["source_path"] == authority_locator
    assert persisted_source_path == authority_locator
    assert exact_provenance not in authority_locator
    _assert_exact_state_cardinality(state_db)
