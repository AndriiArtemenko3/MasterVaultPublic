"""run_ask: round-merging judge guards, novelty floor, followup dedup,
citation gate, extractive fallback, and the zero-evidence path."""

from __future__ import annotations

from pathlib import Path

from mastervault.config import RetrievalCfg
from mastervault.contracts.judge import SufficiencyVerdictOut
from mastervault.contracts.synthesis import GroundedAnswerOut
from mastervault.pipelines.ask import run_ask
from mastervault.sync import sync_vault

SOURCE_A = """---
domain: operations
type: source
source_type: policy
title: Widget Policy
tags: [policy]
status: processed
created: 2026-01-01
updated: 2026-01-01
key_claims:
  - id: source-a-01
    statement: "Widget policy allows returns within 10 days for defective widgets."
    confidence: high
    affects: []
---

# Widget Policy

Widget policy allows returns within 10 days for defective widgets.
"""

SOURCE_B = """---
domain: operations
type: source
source_type: policy
title: Gadget Policy
tags: [policy]
status: processed
created: 2026-01-01
updated: 2026-01-01
key_claims:
  - id: source-b-01
    statement: "Gadget policy allows exchanges within 20 days for defective gadgets."
    confidence: high
    affects: []
---

# Gadget Policy

Gadget policy allows exchanges within 20 days for defective gadgets.
"""


def _seed_two_claims(vault_dir: Path, backend, embedder) -> None:
    (vault_dir / "operations" / "sources").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "sources" / "source-a.md").write_text(SOURCE_A, encoding="utf-8")
    (vault_dir / "operations" / "sources" / "source-b.md").write_text(SOURCE_B, encoding="utf-8")
    sync_vault(vault_dir, backend, embedder)


def test_insufficient_then_sufficient_merges_evidence_across_rounds(settings, backend, embedder, llm):
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)

    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(
            sufficient=False,
            missing_aspects=["gadget exchange terms"],
            followup_queries=["gadget exchange window details"],
            rationale="Only widget policy is covered so far.",
        ),
    )
    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(sufficient=True, rationale="Both policies are now covered."),
    )

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm)

    assert outcome.rounds == 2
    assert outcome.zero_evidence is False
    record_ids = {e["record_id"] for e in outcome.evidence}
    assert "claim:source-a-01" in record_ids
    assert "claim:source-b-01" in record_ids


def test_novelty_floor_forces_stop_despite_insufficient_verdict(settings, backend, embedder, llm):
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)

    # Both rounds say insufficient; the second round's followup query hits the
    # same tiny corpus and surfaces zero new record_ids, so the novelty floor
    # must force a stop after round 2 regardless of what the judge says.
    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(
            sufficient=False,
            missing_aspects=["more detail"],
            followup_queries=["alternate phrasing entirely"],
            rationale="Still missing detail.",
        ),
    )
    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(
            sufficient=False,
            missing_aspects=["even more detail"],
            followup_queries=["yet another distinct rephrasing"],
            rationale="Still missing detail.",
        ),
    )
    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(sufficient=False, missing_aspects=["x"], rationale="Should never be reached."),
    )

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm)

    assert outcome.rounds == 2  # stopped after round 2, never reached round 3
    judge_calls = [c for c in llm.calls if c[0] == "sufficiency_judge"]
    assert len(judge_calls) == 2


def test_followup_dropped_by_stopword_dedup_forces_stop(settings, backend, embedder, llm):
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)

    llm.push(
        "sufficiency_judge",
        SufficiencyVerdictOut(
            sufficient=False,
            missing_aspects=["more detail"],
            # Only stopwords differ from the original question -> dropped.
            followup_queries=["What is the widget policy for"],
            rationale="Still missing detail.",
        ),
    )

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm)

    assert outcome.rounds == 1
    judge_calls = [c for c in llm.calls if c[0] == "sufficiency_judge"]
    assert len(judge_calls) == 1


def test_hallucinated_citation_is_stripped_but_answer_survives(settings, backend, embedder, llm):
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)

    llm.push("sufficiency_judge", SufficiencyVerdictOut(sufficient=True, rationale="Enough evidence."))
    llm.push(
        "grounded_synthesis",
        GroundedAnswerOut(
            answer_markdown=(
                "Widget policy allows returns within 10 days [claim:source-a-01]. "
                "It also allegedly requires a signed form [claim:does-not-exist-01]."
            ),
            confidence="high",
            gaps=[],
        ),
    )

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm)

    assert outcome.extractive is False
    assert "claim:does-not-exist-01" not in outcome.answer_markdown
    assert "claim:source-a-01" in outcome.answer_markdown
    assert any("does-not-exist-01" in w for w in outcome.warnings)


def test_extractive_fallback_when_nothing_pushed_for_synthesis(settings, backend, embedder, llm):
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)
    llm.push("sufficiency_judge", SufficiencyVerdictOut(sufficient=True, rationale="Enough."))
    # Nothing pushed for "grounded_synthesis" -> MockLLM's empty-registry
    # fallback yields parsed=None -> hard-fail -> extractive.

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm)

    assert outcome.extractive is True
    assert outcome.answer_markdown.startswith("Extractive answer (no generative synthesis):")


def test_zero_evidence_path_exits_cleanly(settings, backend, embedder, llm):
    # No documents synced at all: the index is genuinely empty.
    outcome = run_ask("Anything at all?", settings, backend, embedder, llm)

    assert outcome.zero_evidence is True
    assert outcome.exit_code == 0
    assert outcome.answer_markdown == "corpus has no grounding for this question"
    assert llm.calls == []


def test_max_rounds_cap_is_enforced(settings, backend, embedder, llm):
    settings = settings.model_copy(update={"retrieval": RetrievalCfg(k=5)})
    _seed_two_claims(settings.paths.vault_dir, backend, embedder)

    for i in range(5):
        llm.push(
            "sufficiency_judge",
            SufficiencyVerdictOut(
                sufficient=False,
                missing_aspects=["more"],
                followup_queries=[f"distinct followup phrasing number {i}"],
                rationale="Never satisfied.",
            ),
        )

    outcome = run_ask("What is the widget policy?", settings, backend, embedder, llm, max_rounds=2)

    assert outcome.rounds == 2
