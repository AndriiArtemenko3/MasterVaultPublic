"""mastervault.sync.load: sidecar embedding import + demo-dataset loader.

Two layers: hermetic tests against synthetic tiny fixtures (built in-test, no
network, cover the hash/model-mismatch gates precisely) for `load_embeddings`
and `load_demo_dataset`, plus one CLI-level test against the real shipped
`datasets/larkstead` corpus proving the actual `mvault demo load` command
works end to end and finishes well under a minute. The real-corpus test uses
`MV_EMBEDDING__PROVIDER=local` (matching the sidecar's model) but never
triggers a model download: `demo load` never calls `embedder.embed()` — only
its constant `.dimensions` / `.model_version` properties are read — so it
stays network-free even with the real local provider configured.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.config import EmbeddingCfg, PathsCfg, Settings
from mastervault.providers import MockEmbedding
from mastervault.storage import get_backend
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync import record_content_hashes
from mastervault.sync.load import load_demo_dataset, load_embeddings

pytestmark = pytest.mark.integration

DIM = 8
MODEL = "test-embed-v1"  # matches tests/conftest.py's TEST_MODEL, for the shared `backend` fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "larkstead"

# Stable dataset facts (see datasets/larkstead/processed/MANIFEST.md and
# datasets/larkstead/embeddings/manifest.json): 352 sources + 43 wiki + 10
# decisions + 4 strategy = 409 documents; claims/chunks/embeddings per the
# embeddings sidecar's own record_type_counts.
EXPECTED_DOCUMENTS = 409
EXPECTED_CLAIMS = 3412
EXPECTED_WIKI = 43
EXPECTED_CHUNKS = 1897
EXPECTED_EMBEDDINGS = 5352
EXPECTED_PENDING_REVIEW = 4


def _write_sidecar(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _row(record_id: str, content_hash: str, *, model_version: str = MODEL) -> dict:
    return {
        "record_id": record_id,
        "record_type": "claim",
        "doc_id": None,
        "domain": "customer-support",
        "content_hash": content_hash,
        "model_version": model_version,
        "vector": [0.1] * DIM,
    }


# ---------------------------------------------------------------------------
# load_embeddings: hermetic, synthetic sidecar fixtures
# ---------------------------------------------------------------------------


class TestLoadEmbeddings:
    def test_imports_matching_rows(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(
            path, [_row("claim:a-01", "h1"), _row("claim:b-01", "h2")]
        )
        report = load_embeddings(
            path, backend, expected_hashes={"claim:a-01": "h1", "claim:b-01": "h2"}, model_version=MODEL
        )
        assert report.total_rows == 2
        assert report.imported == 2
        assert report.skipped_hash_mismatch == 0
        assert report.skipped_unknown_record == 0
        assert report.skipped_model_mismatch == 0
        assert backend.stats()["counts"]["embeddings"] == 2

    def test_skips_hash_mismatch_instead_of_indexing_stale_text(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [_row("claim:a-01", "STALE-HASH")])
        report = load_embeddings(
            path, backend, expected_hashes={"claim:a-01": "FRESH-HASH"}, model_version=MODEL
        )
        assert report.total_rows == 1
        assert report.imported == 0
        assert report.skipped_hash_mismatch == 1
        assert backend.stats()["counts"]["embeddings"] == 0

    def test_skips_record_absent_from_the_live_vault(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [_row("claim:ghost-01", "h1")])
        report = load_embeddings(path, backend, expected_hashes={}, model_version=MODEL)
        assert report.skipped_unknown_record == 1
        assert report.imported == 0

    def test_skips_model_version_mismatch(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [_row("claim:a-01", "h1", model_version="some-other-model")])
        report = load_embeddings(
            path, backend, expected_hashes={"claim:a-01": "h1"}, model_version=MODEL
        )
        assert report.skipped_model_mismatch == 1
        assert report.imported == 0

    def test_no_expected_hashes_disables_the_hash_gate(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [_row("claim:a-01", "anything-goes")])
        report = load_embeddings(path, backend, expected_hashes=None, model_version=MODEL)
        assert report.imported == 1
        assert report.skipped_unknown_record == 0

    def test_no_model_version_disables_the_model_gate(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [_row("claim:a-01", "h1", model_version="whatever")])
        report = load_embeddings(path, backend, expected_hashes={"claim:a-01": "h1"}, model_version=None)
        assert report.imported == 1

    def test_batches_across_multiple_flushes(self, backend, tmp_path):
        rows = [_row(f"claim:x-{i:02d}", f"h{i}") for i in range(7)]
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, rows)
        expected_hashes = {r["record_id"]: r["content_hash"] for r in rows}
        report = load_embeddings(
            path, backend, expected_hashes=expected_hashes, model_version=MODEL, batch_size=3
        )
        assert report.imported == 7
        assert backend.stats()["counts"]["embeddings"] == 7

    def test_empty_sidecar_is_a_no_op(self, backend, tmp_path):
        path = tmp_path / "emb.jsonl.gz"
        _write_sidecar(path, [])
        report = load_embeddings(path, backend, model_version=MODEL)
        assert report.total_rows == 0
        assert report.imported == 0


# ---------------------------------------------------------------------------
# load_demo_dataset: hermetic, tiny synthetic dataset_dir
# ---------------------------------------------------------------------------

_FIXTURE_DOC = """---
domain: customer-support
type: source
source_type: policy
title: Tiny Fixture Policy
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
key_claims:
  - id: tiny-fixture-01
    statement: The tiny fixture policy grants a full refund within ten days.
    confidence: high
    affects: []
---

# Tiny Fixture Policy

## Body

The tiny fixture policy grants a full refund within ten days of purchase.
"""

_REVIEW_ITEM = """---
id: rv-demo-fixture-0001
created: '2026-01-01T00:00:00Z'
producer: lint
run_id: run-fixture
tier: 2
target: customer-support/wiki/tiny.md
change_type: add-open-contradiction
pattern_key: fixture-pattern
importance: normal
rationale: Fixture rationale text for a synthetic pending review item.
base_hash: deadbeef00000000
status: pending
payload: {}
---

## Rationale

Fixture rationale.

## Proposal

````replace
fixture replace block
````

## Resolution
"""


def _build_tiny_dataset(dataset_dir: Path, embedder: MockEmbedding) -> None:
    processed = dataset_dir / "processed"
    sources_dir = processed / "customer-support" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "tiny-fixture-policy.md").write_text(_FIXTURE_DOC, encoding="utf-8")

    pending_dir = processed / "_review" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "rv-demo-fixture-0001.md").write_text(_REVIEW_ITEM, encoding="utf-8")

    hashes = record_content_hashes(processed)
    assert hashes  # sanity: the fixture doc's claim + chunk records were found
    rows = [
        {
            "record_id": record_id,
            "record_type": "claim" if record_id.startswith("claim:") else "chunk",
            "doc_id": None,
            "domain": "customer-support",
            "content_hash": h,
            "model_version": embedder.model_version,
            "vector": embedder.embed(["fixture text"])[0],
        }
        for record_id, h in hashes.items()
    ]
    _write_sidecar(dataset_dir / "embeddings" / "embeddings.jsonl.gz", rows)


class TestLoadDemoDataset:
    def test_end_to_end_against_a_tiny_synthetic_dataset(self, tmp_path):
        dataset_dir = tmp_path / "dataset"
        embedder = MockEmbedding(DIM)
        _build_tiny_dataset(dataset_dir, embedder)

        vault_dir = tmp_path / "ws" / "vault"
        review_pending_dir = tmp_path / "ws" / "review" / "pending"
        backend = SqliteBackend(tmp_path / "ws" / "index.db")
        backend.init_schema(embedder.dimensions, embedder.model_version)
        try:
            report = load_demo_dataset(
                dataset_dir=dataset_dir,
                vault_dir=vault_dir,
                review_pending_dir=review_pending_dir,
                backend=backend,
                embedder=embedder,
            )
        finally:
            backend.close()

        assert report.domains_copied == ["customer-support"]
        assert report.review_items_copied == 1
        assert report.counts["documents"] == 1
        assert report.counts["claims"] == 1
        assert report.counts["chunks"] >= 1
        assert report.counts["embeddings"] > 0
        assert report.embeddings.imported == report.counts["embeddings"]
        assert report.embeddings.skipped_hash_mismatch == 0
        assert report.counts["pending_review"] == 1
        assert report.wall_time_s >= 0
        assert (vault_dir / "customer-support" / "sources" / "tiny-fixture-policy.md").is_file()
        assert (review_pending_dir / "rv-demo-fixture-0001.md").is_file()

    def test_idempotent_rerun_without_force_leaves_existing_domains_alone(self, tmp_path):
        dataset_dir = tmp_path / "dataset"
        embedder = MockEmbedding(DIM)
        _build_tiny_dataset(dataset_dir, embedder)
        vault_dir = tmp_path / "ws" / "vault"
        review_pending_dir = tmp_path / "ws" / "review" / "pending"
        backend = SqliteBackend(tmp_path / "ws" / "index.db")
        backend.init_schema(embedder.dimensions, embedder.model_version)
        try:
            load_demo_dataset(
                dataset_dir=dataset_dir, vault_dir=vault_dir,
                review_pending_dir=review_pending_dir, backend=backend, embedder=embedder,
            )
            # Hand-edit the copied file; a no-force rerun must not touch it.
            doc_path = vault_dir / "customer-support" / "sources" / "tiny-fixture-policy.md"
            doc_path.write_text(doc_path.read_text() + "\nhand-edited\n", encoding="utf-8")

            second = load_demo_dataset(
                dataset_dir=dataset_dir, vault_dir=vault_dir,
                review_pending_dir=review_pending_dir, backend=backend, embedder=embedder,
            )
        finally:
            backend.close()

        assert second.domains_copied == []  # already present, not re-copied
        assert "hand-edited" in doc_path.read_text()

    def test_force_recopies_and_discards_local_edits(self, tmp_path):
        dataset_dir = tmp_path / "dataset"
        embedder = MockEmbedding(DIM)
        _build_tiny_dataset(dataset_dir, embedder)
        vault_dir = tmp_path / "ws" / "vault"
        review_pending_dir = tmp_path / "ws" / "review" / "pending"
        backend = SqliteBackend(tmp_path / "ws" / "index.db")
        backend.init_schema(embedder.dimensions, embedder.model_version)
        try:
            load_demo_dataset(
                dataset_dir=dataset_dir, vault_dir=vault_dir,
                review_pending_dir=review_pending_dir, backend=backend, embedder=embedder,
            )
            doc_path = vault_dir / "customer-support" / "sources" / "tiny-fixture-policy.md"
            doc_path.write_text(doc_path.read_text() + "\nhand-edited\n", encoding="utf-8")

            forced = load_demo_dataset(
                dataset_dir=dataset_dir, vault_dir=vault_dir,
                review_pending_dir=review_pending_dir, backend=backend, embedder=embedder,
                force=True,
            )
        finally:
            backend.close()

        assert forced.domains_copied == ["customer-support"]
        assert "hand-edited" not in doc_path.read_text()


# ---------------------------------------------------------------------------
# CLI end-to-end against the real shipped Larkstead dataset
# ---------------------------------------------------------------------------


def test_cli_demo_load_real_dataset(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "local")  # matches the sidecar's model
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(workspace))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from mastervault.cli.app import app

    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    start = time.perf_counter()
    result = runner.invoke(app, ["demo", "load"])
    elapsed = time.perf_counter() - start
    assert result.exit_code == 0, result.output
    assert elapsed < 55, f"demo load took {elapsed:.1f}s; target is well under 60s"

    for domain in ("customer-support", "sales-crm", "operations", "internal-admin"):
        assert (workspace / "vault" / domain).is_dir()
    pending_items = list((workspace / "review" / "pending").glob("*.md"))
    assert len(pending_items) == EXPECTED_PENDING_REVIEW

    settings = Settings(paths=PathsCfg(workspace=workspace), embedding=EmbeddingCfg(provider="local"))
    backend = get_backend(settings)
    try:
        stats = backend.stats()
        wiki_count = len({(d, s) for d, s in backend.alias_index().values()})
    finally:
        backend.close()

    counts = stats["counts"]
    assert counts["documents"] == EXPECTED_DOCUMENTS
    assert counts["claims"] == EXPECTED_CLAIMS
    assert counts["chunks"] == EXPECTED_CHUNKS
    assert counts["embeddings"] == EXPECTED_EMBEDDINGS
    assert wiki_count == EXPECTED_WIKI

    # idempotent re-run: no crash, no duplication.
    result2 = runner.invoke(app, ["demo", "load"])
    assert result2.exit_code == 0, result2.output
