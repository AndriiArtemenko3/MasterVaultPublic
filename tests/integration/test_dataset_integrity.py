"""Ship gate for the Larkstead dataset itself.

`mvault lint --mechanical-only` on a pristine demo is a promise the README and
the 10-minute tour both make, and 0.1.0 shipped a corpus that failed it: 75
`affects:` references to wiki concepts that never existed. These tests read the
shipped `datasets/larkstead/processed/` tree directly -- no workspace, no index,
no embedding provider -- so a corpus regression is caught by the fast unit-ish
path long before anyone loads the demo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.ingest.affects import existing_wiki_slugs, reconcile_affects
from mastervault.models import SourceNote
from mastervault.pipelines.lint import _broken_affects, _duplicate_claim_ids, _scan_vault
from mastervault.vaultfs.notes import read_note

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = REPO_ROOT / "datasets" / "larkstead" / "processed"

# Shipped inventory; see datasets/larkstead/processed/MANIFEST.md.
EXPECTED_SOURCE_NOTES = 352
EXPECTED_CLAIMS = 3412
EXPECTED_WIKI = 43

# Files under processed/ that are deliberately not vault notes: the curation
# record, and the review-queue items `demo load` copies to review/pending/.
NON_NOTE_FILES = (
    "MANIFEST.md",
    "_review/pending/lint-2026-07-07-1258-rv-0060.md",
    "_review/pending/lint-2026-07-07-1258-rv-0106.md",
    "_review/pending/lint-2026-07-07-1258-rv-0107.md",
    "_review/pending/lint-2026-07-07-1258-rv-0108.md",
)


@pytest.fixture(scope="module")
def scanned():
    assert PROCESSED.is_dir(), f"shipped dataset missing at {PROCESSED}"
    return _scan_vault(PROCESSED)


def test_shipped_corpus_has_no_broken_affects(scanned):
    """The defect that made `mvault lint --mechanical-only` fail in 0.1.0."""
    claims, wikis, _skipped = scanned
    broken = _broken_affects(claims, wikis)
    assert broken == [], (
        f"{len(broken)} broken affects reference(s) would ship, e.g. "
        f"{[(b['slug'], b['source']) for b in broken[:5]]}"
    )


def test_shipped_corpus_has_no_duplicate_claim_ids(scanned):
    claims, _wikis, _skipped = scanned
    assert _duplicate_claim_ids(claims) == {}


def test_only_the_known_non_note_files_are_skipped(scanned):
    """A domain note the frontmatter gate skips is invisible to lint.

    Scanning `processed/` directly also sweeps up files that are deliberately
    not vault notes -- the curation record and the review-queue items, which
    `demo load` copies to `review/pending/` rather than into the vault. Those
    five are expected; anything else means a domain note stopped parsing.
    """
    _claims, _wikis, skipped = scanned
    unexpected = [
        s.rel_path
        for s in skipped
        if s.rel_path != "MANIFEST.md" and not s.rel_path.startswith("_review/")
    ]
    assert unexpected == []
    assert len(skipped) == len(NON_NOTE_FILES)


def test_shipped_inventory_is_stable(scanned):
    claims, wikis, _skipped = scanned
    assert len(claims) == EXPECTED_CLAIMS
    assert len(wikis) == EXPECTED_WIKI


def test_shipped_affects_are_already_reconciled():
    """Running the pipeline's own reconciliation must be a no-op, byte for byte.

    Stronger than the lint check: it also proves the shipped files are exactly
    what the fixed ingest pipeline would emit, so a regeneration cannot
    reintroduce the defect and a hand-edit cannot quietly diverge from it.
    """
    known = existing_wiki_slugs(PROCESSED)
    assert len(known) == EXPECTED_WIKI

    dirty: list[str] = []
    for path in sorted(PROCESSED.rglob("*.md")):
        repair = reconcile_affects(path.read_text(encoding="utf-8"), known)
        if repair.changed:
            dirty.append(
                f"{path.relative_to(PROCESSED)}: "
                f"{[(d.claim_id, d.slug) for d in repair.dropped]}"
            )
    assert dirty == [], f"{len(dirty)} shipped note(s) still carry unreconciled affects: {dirty[:5]}"


def test_every_shipped_claim_affects_slug_resolves_to_a_wiki_file():
    """Belt-and-braces: resolve each slug to an actual file on disk."""
    known = existing_wiki_slugs(PROCESSED)
    checked = 0
    for path in sorted(PROCESSED.rglob("*/sources/*.md")):
        loaded = read_note(path)
        if not isinstance(loaded.model, SourceNote):
            continue
        for claim in loaded.model.key_claims:
            for slug in claim.affects:
                assert slug in known, f"{path.name}:{claim.id} -> {slug!r} has no wiki note"
                checked += 1
    assert checked > 0, "no affects references were checked -- the walk found nothing"


def test_shipped_source_note_count_is_stable(scanned):
    claims, _wikis, _skipped = scanned
    sources = {c.source_rel_path for c in claims}
    assert len(sources) == EXPECTED_SOURCE_NOTES
