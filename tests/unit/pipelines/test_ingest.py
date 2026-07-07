"""run_ingest: plan/dry-run, budget exhaustion + resume, resume conflicts,
hard-fail continuation, and dedupe-on-reingest."""

from __future__ import annotations

from mastervault.contracts.claims import ClaimCandidate, ClaimExtractionOut
from mastervault.core.errors import EXIT_CODES
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
