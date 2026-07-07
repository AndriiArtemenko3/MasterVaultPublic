"""sync_vault against both storage backends, over the mini_vault fixture.

Fixture inventory (keep in step with tests/fixtures/mini_vault/):
10 documents (3 wiki, 5 source, 1 decision, 1 strategy), 12 claims,
8 claim_affects rows, 12 wiki aliases.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mastervault.providers import MockEmbedding
from mastervault.sync import sync_vault

pytestmark = pytest.mark.integration

MINI_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "mini_vault"
DIM = 8  # must match the backend fixture's schema dim

EXPECTED_DOCS = 10
EXPECTED_CLAIMS = 12
EXPECTED_WIKI = 3
EXPECTED_ALIASES = 12
EXPECTED_AFFECTS = 8

TOUCH_FILE = "customer-support/sources/faq-desk-mat-care.md"
OLD_STATEMENT = (
    "Larkstead desk mats should be cleaned with a damp cloth and mild soap,"
    " never machine washed."
)
NEW_STATEMENT = (
    "Larkstead desk mats should be wiped clean with a damp cloth and mild soap,"
    " never machine washed."
)


class CountingEmbedder:
    """MockEmbedding wrapper that records exactly which texts get embedded."""

    def __init__(self, dimensions: int = DIM):
        self._inner = MockEmbedding(dimensions)
        self.embedded_texts: list[str] = []

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model_version(self) -> str:
        return self._inner.model_version

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def estimate_cost_usd(self, texts) -> float:
        return 0.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return self._inner.embed(texts)


@pytest.fixture
def vault_copy(tmp_path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, dest)
    return dest


def test_full_sync_counts_match_fixture(backend):
    report = sync_vault(MINI_VAULT, backend, MockEmbedding(DIM))
    assert report.docs_upserted == EXPECTED_DOCS
    assert report.docs_deleted == 0
    assert report.skipped == []
    assert report.records_reused == 0

    counts = backend.stats()["counts"]
    assert counts["documents"] == EXPECTED_DOCS
    assert counts["claims"] == EXPECTED_CLAIMS
    assert counts["wiki_aliases"] == EXPECTED_ALIASES
    assert counts["claim_affects"] == EXPECTED_AFFECTS
    assert counts["chunks"] > 0
    # one embedding per claim, per wiki entry, per chunk — nothing else
    assert counts["embeddings"] == EXPECTED_CLAIMS + EXPECTED_WIKI + counts["chunks"]
    assert report.records_embedded == counts["embeddings"]


def test_second_sync_is_a_noop(backend):
    first = sync_vault(MINI_VAULT, backend, MockEmbedding(DIM))
    second = sync_vault(MINI_VAULT, backend, MockEmbedding(DIM))
    assert second.docs_upserted == 0
    assert second.docs_deleted == 0
    assert second.records_embedded == 0
    assert second.records_reused == first.records_embedded


def test_full_flag_reupserts_docs_but_reembeds_nothing(backend):
    sync_vault(MINI_VAULT, backend, MockEmbedding(DIM))
    report = sync_vault(MINI_VAULT, backend, MockEmbedding(DIM), full=True)
    assert report.docs_upserted == EXPECTED_DOCS
    assert report.records_embedded == 0


def test_incremental_touch_reembeds_only_the_changed_claim(backend, vault_copy):
    embedder = CountingEmbedder()
    first = sync_vault(vault_copy, backend, embedder)

    path = vault_copy / TOUCH_FILE
    text = path.read_text(encoding="utf-8")
    assert OLD_STATEMENT in text
    path.write_text(text.replace(OLD_STATEMENT, NEW_STATEMENT), encoding="utf-8")

    embedder.embedded_texts.clear()
    report = sync_vault(vault_copy, backend, embedder)
    assert report.docs_upserted == 1  # only the touched file
    assert report.docs_deleted == 0
    assert report.records_embedded == 1  # only the edited claim statement
    assert embedder.embedded_texts == [NEW_STATEMENT]
    assert report.records_reused == first.records_embedded - 1


def test_deleting_a_file_removes_its_document(backend, vault_copy):
    sync_vault(vault_copy, backend, MockEmbedding(DIM))
    (vault_copy / "internal-admin/sources/memo-expense-policy-update.md").unlink()

    report = sync_vault(vault_copy, backend, MockEmbedding(DIM))
    assert report.docs_deleted == 1
    assert report.docs_upserted == 0
    counts = backend.stats()["counts"]
    assert counts["documents"] == EXPECTED_DOCS - 1
    assert counts["claims"] == EXPECTED_CLAIMS - 2  # the memo carried 2 claims
    assert counts["embeddings"] == counts["claims"] + EXPECTED_WIKI + counts["chunks"]


def test_gate_failing_file_is_skipped_not_indexed(backend, vault_copy):
    (vault_copy / "customer-support" / "sources" / "broken.md").write_text(
        "no frontmatter here, just text\n", encoding="utf-8"
    )
    report = sync_vault(vault_copy, backend, MockEmbedding(DIM))
    assert [s.rel_path for s in report.skipped] == ["customer-support/sources/broken.md"]
    assert backend.stats()["counts"]["documents"] == EXPECTED_DOCS
