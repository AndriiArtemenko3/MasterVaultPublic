"""Unit tests for the end-to-end ask-eval harness itself.

These test the grader, not the pipeline: that malformed case files are rejected
as build errors, that the universal safety checks actually fail when violated,
and that baseline comparison distinguishes a regression from added coverage.
The harness running against the real corpus lives in
tests/integration/test_ask_eval.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.evals.ask_harness import (
    ASK_CASE_CLASSES,
    AskCase,
    AskCaseResult,
    AskEvalError,
    AskSuiteReport,
    CheckResult,
    build_scripted_llm,
    compare_ask_to_baseline,
    grade,
    load_ask_cases,
    missing_case_classes,
)


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE_OBS = {
    "answer_markdown": "Answer [claim:a-01]",
    "confidence": "high",
    "gaps": [],
    "sources": [{"record_id": "claim:a-01", "rel_path": "d/sources/a.md"}],
    "evidence": [
        {"record_id": "claim:a-01", "rel_path": "d/sources/a.md"},
        {"record_id": "claim:b-01", "rel_path": "e/sources/b.md"},
    ],
    "warnings": [],
    "extractive": False,
    "zero_evidence": False,
    "rounds": 1,
    "nearest_wiki_titles": [],
}


def case(**expect) -> AskCase:
    return AskCase(id="c1", cls="direct-factual", question="q?", expect=dict(expect))


def named(checks: list[CheckResult]) -> dict[str, bool]:
    return {c.name: c.passed for c in checks}


class TestLoading:
    def test_rejects_a_non_list_file(self, tmp_path: Path):
        with pytest.raises(AskEvalError, match="list of cases"):
            load_ask_cases(write(tmp_path, "id: not-a-list\n"))

    def test_rejects_a_missing_required_field(self, tmp_path: Path):
        with pytest.raises(AskEvalError, match="question"):
            load_ask_cases(write(tmp_path, "- id: a\n  class: direct-factual\n"))

    def test_rejects_an_unknown_class(self, tmp_path: Path):
        body = "- id: a\n  class: made-up\n  question: q\n"
        with pytest.raises(AskEvalError, match="unknown class"):
            load_ask_cases(write(tmp_path, body))

    def test_rejects_duplicate_ids(self, tmp_path: Path):
        body = (
            "- id: a\n  class: direct-factual\n  question: q\n"
            "- id: a\n  class: direct-factual\n  question: q2\n"
        )
        with pytest.raises(AskEvalError, match="duplicate case id"):
            load_ask_cases(write(tmp_path, body))

    def test_rejects_a_script_for_an_unknown_task(self, tmp_path: Path):
        body = "- id: a\n  class: direct-factual\n  question: q\n  script:\n    not_a_task: []\n"
        with pytest.raises(AskEvalError, match="unknown task"):
            load_ask_cases(write(tmp_path, body))

    def test_missing_case_classes_reports_gaps(self):
        cases = [AskCase(id="a", cls="direct-factual", question="q")]
        gaps = missing_case_classes(cases)
        assert "direct-factual" not in gaps
        assert set(gaps) == set(ASK_CASE_CLASSES) - {"direct-factual"}


class TestScriptedLlm:
    def test_a_scripted_response_must_match_its_contract_schema(self, settings):
        # `sufficient` is a bool; a string that pydantic cannot coerce is a
        # build error in the case file, not a silent no-op at run time.
        with pytest.raises(AskEvalError, match="does not match its schema"):
            build_scripted_llm(settings, {"sufficiency_judge": [{"sufficient": "banana"}]})

    def test_a_string_response_is_passed_through_as_raw_text(self, settings):
        llm = build_scripted_llm(settings, {"grounded_synthesis": ["not json"]})
        result = llm.complete("grounded_synthesis", "prompt")
        assert result.text == "not json"
        assert result.parsed is None


class TestUniversalChecks:
    def test_a_citation_in_the_answer_text_outside_the_pool_fails(self):
        """The check must read the ANSWER, not the derived `sources` list.

        `sources` is built from the evidence pool by construction, so grading it
        against that pool can never fail -- it would report green with the
        citation gate removed. What a reader sees is the bracketed tokens in
        answer_markdown.
        """
        obs = {**BASE_OBS, "answer_markdown": "Refunds take 90 days [claim:ghost]."}
        checks = named(grade(case(), obs, obs))
        assert checks["citations_within_evidence"] is False

    def test_ordinary_prose_brackets_are_not_treated_as_citations(self):
        obs = {**BASE_OBS, "answer_markdown": "The policy [sic] applies to [2026] orders."}
        assert named(grade(case(), obs, obs))["citations_within_evidence"] is True

    def test_a_source_outside_the_evidence_pool_fails(self):
        obs = {**BASE_OBS, "sources": [{"record_id": "claim:ghost", "rel_path": "x.md"}]}
        assert named(grade(case(), obs, obs))["sources_within_evidence"] is False

    def test_citations_within_evidence_passes_for_a_real_citation(self):
        assert named(grade(case(), BASE_OBS, BASE_OBS))["citations_within_evidence"] is True

    def test_a_non_reproducing_second_run_fails_determinism(self):
        drifted = {**BASE_OBS, "answer_markdown": "something else"}
        assert named(grade(case(), BASE_OBS, drifted))["deterministic"] is False

    def test_the_universal_checks_run_even_with_no_expectations(self):
        assert set(named(grade(case(), BASE_OBS, BASE_OBS))) == {
            "citations_within_evidence",
            "sources_within_evidence",
            "deterministic",
        }


class TestExpectationChecks:
    def test_evidence_docs_detects_a_document_never_retrieved(self):
        checks = named(grade(case(evidence_docs=["e/sources/b.md", "z/missing.md"]), BASE_OBS, BASE_OBS))
        assert checks["evidence_docs"] is False

    def test_conflicting_docs_requires_every_side(self):
        both = case(conflicting_docs=["d/sources/a.md", "e/sources/b.md"])
        assert named(grade(both, BASE_OBS, BASE_OBS))["conflicting_docs"] is True
        one_missing = case(conflicting_docs=["d/sources/a.md", "z/other.md"])
        assert named(grade(one_missing, BASE_OBS, BASE_OBS))["conflicting_docs"] is False

    def test_evidence_domains_min_counts_distinct_top_level_dirs(self):
        assert named(grade(case(evidence_domains_min=2), BASE_OBS, BASE_OBS))["evidence_domains_min"] is True
        assert named(grade(case(evidence_domains_min=3), BASE_OBS, BASE_OBS))["evidence_domains_min"] is False

    def test_abstains_is_true_only_when_nothing_was_cited(self):
        assert named(grade(case(abstains=True), BASE_OBS, BASE_OBS))["abstains"] is False
        silent = {**BASE_OBS, "sources": []}
        assert named(grade(case(abstains=True), silent, silent))["abstains"] is True

    def test_zero_evidence_counts_as_abstention(self):
        zero = {**BASE_OBS, "sources": [], "evidence": [], "zero_evidence": True}
        assert named(grade(case(abstains=True), zero, zero))["abstains"] is True

    def test_warnings_contain_matches_on_substring(self):
        warned = {**BASE_OBS, "warnings": ["stripped citation not in evidence pool: [claim:x]"]}
        assert named(grade(case(warnings_contain=["stripped citation"]), warned, warned))["warnings_contain"] is True
        assert named(grade(case(warnings_contain=["nope"]), warned, warned))["warnings_contain"] is False

    def test_rounds_max_allows_fewer_but_not_more(self):
        assert named(grade(case(rounds_max=3), BASE_OBS, BASE_OBS))["rounds_max"] is True
        three = {**BASE_OBS, "rounds": 3}
        assert named(grade(case(rounds_max=2), three, three))["rounds_max"] is False


class TestBaselineComparison:
    def _report(self, passed: bool, check_passed: bool) -> AskSuiteReport:
        return AskSuiteReport(
            results=[
                AskCaseResult(
                    id="c1",
                    cls="direct-factual",
                    checks=[CheckResult(name="min_citations", passed=check_passed, detail="d")],
                    rounds=1,
                    extractive=passed,
                    zero_evidence=False,
                    evidence_count=2,
                    citation_count=1,
                )
            ]
        )

    BASELINE = {
        "cases": [
            {
                "id": "c1",
                "passed": True,
                "checks": [{"name": "min_citations", "passed": True}],
            }
        ]
    }

    def test_a_case_that_stops_passing_is_a_regression(self):
        result = compare_ask_to_baseline(self._report(False, False), self.BASELINE)
        assert result["regressed"]
        assert any("c1" in line for line in result["regressed"])

    def test_a_stable_pass_is_not_a_regression(self):
        assert compare_ask_to_baseline(self._report(True, True), self.BASELINE)["regressed"] == []

    def test_a_case_absent_from_the_baseline_is_new_not_regressed(self):
        result = compare_ask_to_baseline(self._report(True, True), {"cases": []})
        assert result["new_cases"] == ["c1"]
        assert result["regressed"] == []

    def test_a_case_removed_from_the_suite_is_reported_as_dropped(self):
        result = compare_ask_to_baseline(AskSuiteReport(results=[]), self.BASELINE)
        assert result["dropped_cases"] == ["c1"]
