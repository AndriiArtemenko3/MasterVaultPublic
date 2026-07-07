"""`_route_claim`: corpus-check routing (supports/extends/challenges) and the
new-concept tally, exercised directly against a synthetic MatchResult so the
routing logic is tested without depending on fragile embedding-similarity
band engineering (that path is covered end to end by test_ingest_e2e.py's
alias-exact and new-concept scenarios)."""

from __future__ import annotations

from mastervault.contracts.corpus_check import CorpusCheckOut
from mastervault.contracts.wiki_draft import WikiDraftOut
from mastervault.core.events import utc_now
from mastervault.core.runctx import RunContext
from mastervault.ingest.concept_match import MatchResult
from mastervault.models import ChangeType, ReviewStatus
from mastervault.pipelines.ingest import _route_claim
from mastervault.review.queue import ReviewQueue
from mastervault.sync import sync_vault

CLAIM_STATEMENT = "Restocking of opened items carries a 15 percent fee under the new policy."


def _seed(settings, backend, embedder, write_wiki_note) -> None:
    write_wiki_note(
        settings.paths.vault_dir, "operations", "restocking-fee", "Restocking Fee",
        aliases=["restock charge"], body="The restocking fee applies to opened returns.",
    )
    sync_vault(settings.paths.vault_dir, backend, embedder)


def _ctx(tmp_path) -> RunContext:
    return RunContext.create(tmp_path / "runs", "ingest")


def _match() -> MatchResult:
    return MatchResult(kind="candidate", wiki_slug="restocking-fee", domain="operations", similarity=0.7)


def test_supports_relation_enqueues_tier2_cross_ref(settings, backend, embedder, llm, tmp_path, write_wiki_note):
    _seed(settings, backend, embedder, write_wiki_note)
    llm.push(
        "corpus_check",
        CorpusCheckOut(relation="supports", rationale="Both describe the same 15 percent restocking fee."),
    )
    ctx = _ctx(tmp_path)
    queue = ReviewQueue.from_settings(settings)

    _route_claim(
        claim_id="c-01", statement=CLAIM_STATEMENT, match=_match(), llm=llm, backend=backend,
        ctx=ctx, review_queue=queue, counter=[0], tick=utc_now,
        unit_id="unit-1", source_rel_path="operations/sources/unit-1.md", current_body="body",
        new_concept_tally={}, affects_candidates=["restocking-fee"], auto_approve=False,
        vault_dir=settings.paths.vault_dir, announce=lambda _m: None,
        counters={"linked": 0, "tier2": 0, "tier3": 0},
    )

    pending = queue.list_items(status=ReviewStatus.PENDING)
    assert len(pending) == 1
    _, item = pending[0]
    assert item.tier == 2
    assert item.change_type == ChangeType.ADD_CROSS_REF
    assert item.pattern_key == "ingest-cross-ref::unit-1"


def test_extends_relation_enqueues_tier2_edit_wiki_body(settings, backend, embedder, llm, tmp_path, write_wiki_note):
    _seed(settings, backend, embedder, write_wiki_note)
    llm.push(
        "corpus_check",
        CorpusCheckOut(relation="extends", rationale="The claim adds a new 15 percent figure the entry lacks."),
    )
    llm.push(
        "wiki_draft",
        WikiDraftOut(body_markdown="Opened returns now carry a 15 percent restocking fee under the updated policy."),
    )
    ctx = _ctx(tmp_path)
    queue = ReviewQueue.from_settings(settings)

    _route_claim(
        claim_id="c-02", statement=CLAIM_STATEMENT, match=_match(), llm=llm, backend=backend,
        ctx=ctx, review_queue=queue, counter=[0], tick=utc_now,
        unit_id="unit-2", source_rel_path="operations/sources/unit-2.md", current_body="body",
        new_concept_tally={}, affects_candidates=["restocking-fee"], auto_approve=False,
        vault_dir=settings.paths.vault_dir, announce=lambda _m: None,
        counters={"linked": 0, "tier2": 0, "tier3": 0},
    )

    pending = queue.list_items(status=ReviewStatus.PENDING)
    assert len(pending) == 1
    _, item = pending[0]
    assert item.tier == 2
    assert item.change_type == ChangeType.EDIT_WIKI_BODY
    assert item.pattern_key == "ingest-extend::unit-2"


def test_challenges_relation_enqueues_tier3_contradiction(settings, backend, embedder, llm, tmp_path, write_wiki_note):
    _seed(settings, backend, embedder, write_wiki_note)
    llm.push(
        "corpus_check",
        CorpusCheckOut(relation="challenges", rationale="15 percent contradicts the entry's stated 10 percent fee."),
    )
    ctx = _ctx(tmp_path)
    queue = ReviewQueue.from_settings(settings)

    _route_claim(
        claim_id="c-03", statement=CLAIM_STATEMENT, match=_match(), llm=llm, backend=backend,
        ctx=ctx, review_queue=queue, counter=[0], tick=utc_now,
        unit_id="unit-3", source_rel_path="operations/sources/unit-3.md", current_body="body",
        new_concept_tally={}, affects_candidates=["restocking-fee"], auto_approve=False,
        vault_dir=settings.paths.vault_dir, announce=lambda _m: None,
        counters={"linked": 0, "tier2": 0, "tier3": 0},
    )

    pending = queue.list_items(status=ReviewStatus.PENDING)
    assert len(pending) == 1
    _, item = pending[0]
    assert item.tier == 3
    assert item.change_type == ChangeType.ADD_OPEN_CONTRADICTION
    assert item.importance == "high"
    assert item.pattern_key == "ingest-contradiction::unit-3"


def test_new_match_tallies_by_label_without_enqueueing(settings, backend, embedder, llm, tmp_path):
    ctx = _ctx(tmp_path)
    queue = ReviewQueue.from_settings(settings)
    tally: dict[str, list[tuple[str, str]]] = {}

    new_match = MatchResult(kind="new")
    for i in range(2):
        _route_claim(
            claim_id=f"c-new-{i}", statement=f"Some brand new concept fact number {i}.", match=new_match,
            llm=llm, backend=backend, ctx=ctx, review_queue=queue, counter=[0], tick=utc_now,
            unit_id=f"unit-new-{i}", source_rel_path=f"operations/sources/unit-new-{i}.md",
            current_body="body", new_concept_tally=tally, affects_candidates=["shiny-new-concept"],
            auto_approve=False, vault_dir=settings.paths.vault_dir, announce=lambda _m: None,
            counters={"linked": 0, "tier2": 0, "tier3": 0},
        )

    assert list(tally) == ["shiny-new-concept"]
    assert len(tally["shiny-new-concept"]) == 2
    assert queue.list_items(status=ReviewStatus.PENDING) == []  # tallying alone never enqueues
