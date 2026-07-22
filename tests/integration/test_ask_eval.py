"""The frozen end-to-end ask evaluation, run against the shipped demo corpus.

This is the gate `mvault ask-eval` runs in CI, exercised from the test suite so
a pipeline change that breaks citation validity, evidence collection, the round
guards, or the malformed-output fallback fails `pytest` too -- not only the
separate eval job.

Uses the shipped `datasets/larkstead/processed/` tree loaded into a tmp
workspace via the sidecar, so it is keyless and needs no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mastervault.config import EmbeddingCfg, PathsCfg, Settings
from mastervault.evals.ask_harness import (
    ASK_CASE_CLASSES,
    compare_ask_to_baseline,
    load_ask_cases,
    missing_case_classes,
    run_ask_suite,
)
from mastervault.providers import get_embedding_provider
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync import load_demo_dataset

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "larkstead"
CASES_PATH = DATASET_DIR / "golden" / "ask_cases.yaml"
ASK_BASELINE_PATH = DATASET_DIR / "golden" / "ask_baseline.json"


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory):
    """The shipped demo loaded once into a throwaway sqlite workspace."""
    workspace = tmp_path_factory.mktemp("ask_eval_ws")
    settings = Settings(
        paths=PathsCfg(workspace=workspace),
        embedding=EmbeddingCfg(provider="local"),
    )
    embedder = get_embedding_provider(settings)
    backend = SqliteBackend(settings.paths.sqlite_path)
    backend.init_schema(embedder.dimensions, embedder.model_version)
    report = load_demo_dataset(
        dataset_dir=DATASET_DIR,
        vault_dir=settings.paths.vault_dir,
        review_pending_dir=settings.paths.review_pending,
        backend=backend,
        embedder=embedder,
    )
    assert report.counts["embeddings"] > 0, "demo sidecar import produced no embeddings"
    yield settings, backend, embedder
    backend.close()


@pytest.fixture(scope="module")
def suite(demo_env):
    settings, backend, embedder = demo_env
    return run_ask_suite(load_ask_cases(CASES_PATH), settings, backend, embedder)


def test_every_required_case_class_is_covered():
    assert missing_case_classes(load_ask_cases(CASES_PATH)) == []
    assert len(ASK_CASE_CLASSES) == 11


def test_the_frozen_ask_suite_passes(suite):
    failures = [
        f"{r.id} [{c.name}]: {c.detail}" for r in suite.results for c in r.failures()
    ]
    assert failures == [], "\n".join(failures)


def test_no_case_emits_a_citation_outside_its_evidence_pool(suite):
    """The single most important property: answers cannot invent sources."""
    for result in suite.results:
        check = next(c for c in result.checks if c.name == "citations_within_evidence")
        assert check.passed, f"{result.id}: {check.detail}"


def test_every_case_is_deterministic(suite):
    for result in suite.results:
        check = next(c for c in result.checks if c.name == "deterministic")
        assert check.passed, f"{result.id}: {check.detail}"


def test_suite_does_not_regress_against_the_frozen_baseline(suite):
    baseline = json.loads(ASK_BASELINE_PATH.read_text(encoding="utf-8"))
    comparison = compare_ask_to_baseline(suite, baseline)
    assert comparison["regressed"] == []
    assert comparison["dropped_cases"] == [], (
        "the frozen baseline names cases this run no longer has; "
        "removing coverage must be deliberate"
    )


def test_known_limitations_are_declared_not_hidden(suite):
    """A limitation must carry an explanation, so a green suite stays honest."""
    for entry in suite.limitations():
        assert len(entry["limitation"]) > 80, f"{entry['id']}: limitation text is too thin"
