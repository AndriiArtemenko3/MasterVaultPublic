"""`mvault eval` CLI, against the real shipped Larkstead demo dataset + the
real golden query set.

A module-scoped fixture loads the demo dataset once (`load_demo_dataset`
directly, not through the CLI, and not through `.embed()` — see
test_demo_load.py's docstring: the load path is network-free even with the
real local embedding provider configured). Tests that only need
`--config lexical-only` stay network-free too, since that config never calls
`embedder.embed()` on the query either. Tests that exercise the vector
channel (`--config all`) guard themselves with `_require_local_embedder`,
which skips (rather than fails) when the local model can't be loaded —
mirroring this repo's own `pg_test_url` skip-if-unreachable convention for a
dependency this suite can't guarantee in every environment.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.cli.app import app
from mastervault.config import EmbeddingCfg, PathsCfg, Settings, StorageCfg
from mastervault.providers import get_embedding_provider
from mastervault.storage import get_backend
from mastervault.sync import load_demo_dataset

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "larkstead"

EXPECTED_CLASS_COUNTS = {
    "easy-lexical": 14,
    "semantic-paraphrase": 12,
    "cross-domain-multi-hop": 10,
    "contradiction": 8,
    "negative-no-answer": 8,
}


def _require_local_embedder() -> None:
    """Skip the calling test if the real local embedding model can't be
    constructed/loaded (e.g. no network in this environment)."""
    try:
        from mastervault.providers.embedding import LocalEmbedding

        LocalEmbedding().embed(["warmup"])
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure means "skip"
        pytest.skip(f"local embedding model unavailable in this environment: {exc}")


@pytest.fixture(scope="module")
def loaded_workspace(tmp_path_factory):
    """A workspace with the real demo dataset loaded once for the whole module."""
    workspace = tmp_path_factory.mktemp("eval-ws")
    settings = Settings(
        paths=PathsCfg(workspace=workspace),
        embedding=EmbeddingCfg(provider="local"),
        storage=StorageCfg(backend="sqlite"),
    )
    provider = get_embedding_provider(settings)
    backend = get_backend(settings)
    backend.init_schema(provider.dimensions, provider.model_version)
    try:
        report = load_demo_dataset(
            dataset_dir=DATASET_DIR,
            vault_dir=settings.paths.vault_dir,
            review_pending_dir=settings.paths.review_pending,
            backend=backend,
            embedder=provider,
        )
        assert report.counts.get("embeddings", 0) > 0
    finally:
        backend.close()
    for d in (settings.paths.review_pending, settings.paths.review_archive, settings.paths.runs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def eval_env(loaded_workspace, monkeypatch):
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "local")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(loaded_workspace))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return loaded_workspace


# ---------------------------------------------------------------------------
# lexical-only: no network / no query embedding at all
# ---------------------------------------------------------------------------


def test_eval_lexical_only_json_shape_and_class_breakdown(eval_env):
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "--config", "lexical-only", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    assert payload["resolve"]["class_counts"] == EXPECTED_CLASS_COUNTS
    assert payload["resolve"]["errors"] == []

    report = payload["configs"]["lexical-only"]
    overall = report["overall"]
    for metric in ("recall_at_5", "recall_at_10", "ndcg_at_10", "mrr"):
        assert 0.0 <= overall[metric] <= 1.0
    assert 0.0 <= overall["abstention_rate"] <= 1.0

    per_class = report["per_class"]
    assert set(per_class) == set(EXPECTED_CLASS_COUNTS)
    for cls, expected_n in EXPECTED_CLASS_COUNTS.items():
        assert per_class[cls]["n"] == expected_n

    # A lexical channel should find at least some exact-phrase queries.
    assert per_class["easy-lexical"]["recall_at_10"] > 0.0


def test_eval_human_table_output(eval_env):
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "--config", "lexical-only"])
    assert result.exit_code == 0, result.output
    assert "eval: lexical-only" in result.output
    assert "recall@5" in result.output
    assert "negative-no-answer" in result.output


def test_eval_invalid_config_name_exits_2(eval_env):
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "--config", "not-a-real-config"])
    assert result.exit_code == 2


def test_eval_compare_flags_regression_and_passes_when_not_worse(eval_env, tmp_path):
    runner = CliRunner()
    baseline_result = runner.invoke(app, ["eval", "--config", "lexical-only", "--json"])
    assert baseline_result.exit_code == 0, baseline_result.output
    current_overall = json.loads(baseline_result.output)["configs"]["lexical-only"]["overall"]

    inflated_path = tmp_path / "inflated.json"
    inflated_path.write_text(
        json.dumps(
            {
                "configs": {
                    "lexical-only": {
                        "overall": {k: min(1.0, v + 0.5) for k, v in current_overall.items()}
                    }
                }
            }
        )
    )
    regressed = runner.invoke(
        app, ["eval", "--config", "lexical-only", "--compare", str(inflated_path), "--json"]
    )
    assert regressed.exit_code == 1, regressed.output
    assert json.loads(regressed.output)["compare"]["regressed"]

    deflated_path = tmp_path / "deflated.json"
    deflated_path.write_text(
        json.dumps(
            {
                "configs": {
                    "lexical-only": {
                        "overall": {k: max(0.0, v - 0.5) for k, v in current_overall.items()}
                    }
                }
            }
        )
    )
    passed = runner.invoke(
        app, ["eval", "--config", "lexical-only", "--compare", str(deflated_path), "--json"]
    )
    assert passed.exit_code == 0, passed.output
    assert json.loads(passed.output)["compare"]["regressed"] == []


# ---------------------------------------------------------------------------
# Failure paths that never need the workspace / embedder at all
# ---------------------------------------------------------------------------


def test_eval_resolver_failure_is_a_build_error(tmp_path, monkeypatch):
    import mastervault.cli.evals as evals_mod

    broken_processed = tmp_path / "processed"
    broken_processed.mkdir()
    broken_queries = tmp_path / "queries.yaml"
    broken_queries.write_text(
        "- id: q1\n"
        "  class: easy-lexical\n"
        "  query: test query\n"
        "  relevant_docs: [does/not/exist.md]\n"
        "  relevant_claims: [ghost-claim-01]\n"
        "  notes: ''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(evals_mod, "QUERIES_PATH", broken_queries)
    monkeypatch.setattr(evals_mod, "PROCESSED_DIR", broken_processed)

    runner = CliRunner()
    result = runner.invoke(app, ["eval"])
    assert result.exit_code == 1
    assert "failed to resolve" in result.output


def test_eval_errors_when_index_has_zero_embeddings(tmp_path, monkeypatch):
    workspace = tmp_path / "empty-ws"
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "local")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(workspace))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["eval", "--config", "lexical-only"])
    assert result.exit_code == 1
    assert "zero embeddings" in result.output


# ---------------------------------------------------------------------------
# Full config set, including the vector channel — skips without a usable
# local embedding model rather than failing the suite.
# ---------------------------------------------------------------------------


def test_eval_all_configs_marks_rerank_na_without_a_reranker(eval_env):
    _require_local_embedder()
    runner = CliRunner()
    start = time.perf_counter()
    result = runner.invoke(app, ["eval", "--config", "all", "--json"])
    elapsed = time.perf_counter() - start
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    configs = payload["configs"]
    assert set(configs) == {"lexical-only", "vector-only", "hybrid"}
    assert any("hybrid+rerank" in note and "N/A" in note for note in payload["notes"])

    for name, report in configs.items():
        overall = report["overall"]
        for metric in ("recall_at_5", "recall_at_10", "ndcg_at_10", "mrr"):
            assert 0.0 <= overall[metric] <= 1.0, f"{name}.{metric} out of range"

    # Sanity budget, not a strict perf assertion: 52 queries x 3 configs
    # against a local corpus should not be slow.
    assert elapsed < 120, f"mvault eval --config all took {elapsed:.1f}s"
