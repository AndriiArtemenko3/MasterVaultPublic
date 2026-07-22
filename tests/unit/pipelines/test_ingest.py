"""run_ingest: plan/dry-run, budget exhaustion + resume, resume conflicts,
hard-fail continuation, and dedupe-on-reingest."""

from __future__ import annotations

from pathlib import Path

from mastervault.contracts.claims import ClaimCandidate, ClaimExtractionOut
from mastervault.contracts.wiki_draft import WikiDraftOut
from mastervault.core.errors import EXIT_CODES, UnreadableDocument
from mastervault.models import Domain
from mastervault.pipelines.ingest import run_ingest


def _good_claims(n: int = 1) -> ClaimExtractionOut:
    return ClaimExtractionOut(
        claims=[
            ClaimCandidate(statement=f"Fact number {i} is stated plainly in the document.", confidence="high")
            for i in range(n)
        ]
    )


def test_dry_run_writes_only_the_plan(settings, backend, embedder, llm, write_raw):
    write_raw("a.txt", "Some raw content about a refund policy for widgets.")
    raw_dir = write_raw("b.txt", "More raw content.").parent
    outcome = run_ingest(
        raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm, dry_run=True,
    )
    assert outcome.exit_code == EXIT_CODES["ok"]
    assert outcome.summary["dry_run"] is True
    assert outcome.summary["units_planned"] == 2
    assert (outcome.run_dir / "plan.json").is_file()
    vault_dir = settings.paths.vault_dir
    assert not any(vault_dir.rglob("*.md"))
    assert llm.calls == []


def test_hard_fail_unit_continues_and_exits_1(settings, backend, embedder, llm, write_raw):
    raw_dir = write_raw("a-bad.txt", "Content that will fail extraction.").parent
    write_raw("b-good.txt", "Content that will succeed nicely for extraction.")

    # a-bad.txt: hard_fail twice (empty claims -> retried once -> still empty).
    llm.push("claim_extraction", ClaimExtractionOut(claims=[]))
    llm.push("claim_extraction", ClaimExtractionOut(claims=[]))
    # b-good.txt: succeeds on first attempt.
    llm.push("claim_extraction", _good_claims(1))

    outcome = run_ingest(raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm)

    assert outcome.exit_code == EXIT_CODES["completed-with-failures"]
    assert outcome.summary["units_completed"] == 1
    vault_dir = settings.paths.vault_dir
    notes = sorted(p.name for p in (vault_dir / "operations" / "sources").glob("*.md"))
    assert notes == ["b-good.md"]


def test_budget_exhaustion_exits_3_then_resume_completes_rest(settings, backend, embedder, llm, write_raw):
    raw_dir = write_raw("a.txt", "First document about shipping.").parent
    write_raw("b.txt", "Second document about returns.")

    llm.push("claim_extraction", _good_claims(1))
    llm.push("claim_extraction", _good_claims(1))

    exhausted = run_ingest(
        raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm, budget_usd=0.0
    )
    assert exhausted.exit_code == EXIT_CODES["budget-exhausted"]
    assert exhausted.summary["units_completed"] == 0
    vault_dir = settings.paths.vault_dir
    sources_dir = vault_dir / "operations" / "sources"
    assert not sources_dir.is_dir() or not any(sources_dir.glob("*.md"))

    # The originally-pushed responses are still queued (never consumed, since
    # the budget pre-flight check raises before any LLM call is made).
    resumed = run_ingest(
        raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm,
        resume_run_id=exhausted.run_id, budget_usd=100.0,
    )
    assert resumed.exit_code == EXIT_CODES["ok"]
    assert resumed.summary["units_completed"] == 2
    notes = sorted(p.name for p in (vault_dir / "operations" / "sources").glob("*.md"))
    assert notes == ["a.md", "b.md"]


def test_resume_conflict_on_source_drift_exits_4(settings, backend, embedder, llm, write_raw):
    raw_file = write_raw("a.txt", "Original content before drift.")
    raw_dir = raw_file.parent
    llm.push("claim_extraction", _good_claims(1))

    exhausted = run_ingest(
        raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm, budget_usd=0.0
    )
    assert exhausted.exit_code == EXIT_CODES["budget-exhausted"]

    raw_file.write_text("Drifted content that no longer matches the frozen plan's sha.", encoding="utf-8")

    conflicted = run_ingest(
        raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm,
        resume_run_id=exhausted.run_id, budget_usd=100.0,
    )
    assert conflicted.exit_code == EXIT_CODES["resume-conflict"]


def test_dedupe_skips_already_ingested_content(settings, backend, embedder, llm, write_raw):
    raw_dir = write_raw("a.txt", "Content that gets ingested exactly once.").parent
    llm.push("claim_extraction", _good_claims(1))

    first = run_ingest(raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm)
    assert first.exit_code == EXIT_CODES["ok"]
    assert first.summary["units_completed"] == 1

    calls_after_first = len(llm.calls)
    second = run_ingest(raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm)
    assert second.exit_code == EXIT_CODES["ok"]
    assert second.summary["units_total"] == 0
    assert len(llm.calls) == calls_after_first  # no new extraction dispatch for the deduped file


# ---------------------------------------------------------------------------
# Regressions found in the 0.2.0 architecture + security audits
# ---------------------------------------------------------------------------


def test_a_new_concepts_slug_survives_the_affects_reconcile(
    settings, backend, embedder, llm, write_raw
):
    """Reconciling `affects:` must happen AFTER new wiki concepts are drafted.

    A claim routed as "new" is tallied under its own affects label, and
    `_draft_new_concepts` then creates a wiki stub named with exactly that
    slug. Reconciling first deleted the claim->concept edge for the concept the
    claim had just caused to exist -- and since reconcile only ever drops and
    ingest dedupes by content hash, no re-run could repair it.
    """
    from mastervault.vaultfs.frontmatter import parse_frontmatter

    raw_dir = write_raw(
        "widget.txt", "Widget recycling is handled by the regional depot every quarter."
    ).parent
    # `_draft_new_concepts` only drafts a concept seen by at least two claims.
    llm.push(
        "claim_extraction",
        ClaimExtractionOut(
            claims=[
                ClaimCandidate(
                    statement="Widget recycling is handled by the regional depot quarterly.",
                    confidence="high",
                    affects_candidates=["widget-recycling"],
                ),
                ClaimCandidate(
                    statement="Widget recycling collections are logged by the depot supervisor.",
                    confidence="high",
                    affects_candidates=["widget-recycling"],
                ),
            ]
        ),
    )
    llm.push(
        "wiki_draft",
        WikiDraftOut(
            body_markdown=(
                "## Definition\n\n**Operating:** Widget recycling runs quarterly.\n\n"
                "## Main Insights\n\n- The depot handles it.\n\n"
                "## Why It Compounds\n\nFewer ad-hoc collections.\n\n"
                "## Cross-Refs\n\n- none\n"
            ),
            aliases=["widget recycling"],
        ),
    )

    outcome = run_ingest(raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm)
    assert outcome.exit_code == EXIT_CODES["ok"]

    vault_dir = settings.paths.vault_dir
    wiki_stub = vault_dir / "operations" / "wiki" / "widget-recycling.md"
    assert wiki_stub.is_file(), "the new-concept stub should have been drafted"

    note = vault_dir / "operations" / "sources" / "widget.md"
    data, _ = parse_frontmatter(note.read_text(encoding="utf-8"))
    affects = data["key_claims"][0]["affects"]
    assert affects == ["widget-recycling"], (
        "the claim must keep the edge to the concept it created; "
        f"got {affects!r} and affects_dropped={outcome.summary['affects_dropped']}"
    )
    assert outcome.summary["affects_dropped"] == 0


def test_a_file_that_becomes_unreadable_fails_its_unit_not_the_run(
    settings, backend, embedder, llm, write_raw
):
    """read_raw_text raises UnreadableDocument, which is NOT an OSError.

    The execute loop guarded only `except OSError`, so a source that stopped
    being readable between the plan freeze and the extract pass escaped
    run_ingest entirely: traceback, no unit.failed event, no summary.
    """
    good = write_raw("good.txt", "A perfectly readable document about depots.")
    raw_dir = good.parent
    bad = raw_dir / "bad.txt"
    bad.write_text("readable at plan time", encoding="utf-8")

    llm.push("claim_extraction", _good_claims(1))

    original = run_ingest.__globals__["read_raw_text"]
    calls = {"n": 0}

    def flaky(path):
        # Readable during the plan pass; corrupt by the time the unit runs.
        calls["n"] += 1
        if calls["n"] > 2 and Path(path).name == "bad.txt":
            raise UnreadableDocument("bad.txt: not valid UTF-8 text")
        return original(path)

    run_ingest.__globals__["read_raw_text"] = flaky
    try:
        outcome = run_ingest(raw_dir, Domain.OPERATIONS, settings, backend, embedder, llm)
    finally:
        run_ingest.__globals__["read_raw_text"] = original

    # The bad unit failed; the good one still landed.
    assert outcome.exit_code == EXIT_CODES["completed-with-failures"]
    notes = sorted(p.name for p in (settings.paths.vault_dir / "operations" / "sources").glob("*.md"))
    assert notes == ["good.md"]
