"""End-to-end `ask` evaluation: deterministic, keyless, mechanically graded.

Deliberately separate from the retrieval harness in `evals.harness`. That one
asks "did hybrid_search rank the right documents"; this one asks "did the whole
ask pipeline — multi-round retrieval, the sufficiency judge, its mechanical
guards, grounded synthesis, and the citation gate — behave". Mixing the two
would make a headline number that improves for one reason and regresses for
another, so they stay apart and are reported apart.

Determinism and keylessness come from driving `run_ask` with `MockLLM`. Each
case may script the judge and synthesis turn by turn, which is what makes the
adversarial cases reachable at all: malformed model output, a synthesis that
cites record ids which were never retrieved, a judge that loops on a reworded
query, and a judge that never stops. A case with no script exercises the cold
mock path a keyless user actually gets — empty registry, judge hard-fail,
synthesis hard-fail, deterministic extractive answer.

Grading is mechanical: set membership, counts, booleans over the AskOutcome.
No LLM-as-judge anywhere. Two invariants are asserted on *every* case, scripted
or not, because they are the pipeline's core safety properties:

- every emitted citation resolves to a record that was actually retrieved
  (`citations_within_evidence`), and
- the same case run twice produces the same answer (`deterministic`).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from mastervault.config import Settings
from mastervault.contracts.judge import SufficiencyJudgeContract, SufficiencyVerdictOut
from mastervault.contracts.synthesis import GroundedAnswerOut, GroundedSynthesisContract
from mastervault.pipelines.ask import AskOutcome, run_ask
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.providers.llm import MockLLM
from mastervault.storage.base import StorageBackend

#: Case classes the frozen set is required to cover, in report order.
ASK_CASE_CLASSES: tuple[str, ...] = (
    "direct-factual",
    "semantic-paraphrase",
    "cross-domain-multi-hop",
    "active-contradiction",
    "superseded-policy",
    "negative-no-answer",
    "malformed-synthesis",
    "citation-hallucination",
    "repeated-followup",
    "no-new-evidence",
    "max-rounds",
)

#: task name (== Contract.contract_id) -> the model MockLLM should hand back.
_SCRIPTABLE: dict[str, type[BaseModel]] = {
    SufficiencyJudgeContract.contract_id: SufficiencyVerdictOut,
    GroundedSynthesisContract.contract_id: GroundedAnswerOut,
}


class AskEvalError(ValueError):
    """A malformed ask-eval case file. A build error, never a soft warning."""


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AskCase:
    id: str
    cls: str
    question: str
    notes: str = ""
    script: dict[str, list[Any]] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    max_rounds: int | None = None
    #: A behaviour this case pins that is NOT the behaviour we would want.
    #: Graded expectations always describe what the pipeline actually
    #: guarantees today; this text says what it still does not, and the report
    #: prints it so a green suite can never be mistaken for a complete one.
    known_limitation: str = ""


def load_ask_cases(path: Path | str) -> list[AskCase]:
    """Parse and structurally validate the frozen ask-eval set."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise AskEvalError("ask-eval file must be a list of cases")

    cases: list[AskCase] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise AskEvalError(f"case must be a mapping, got {type(entry).__name__}")
        for required in ("id", "class", "question"):
            if not entry.get(required):
                raise AskEvalError(f"case is missing required field {required!r}: {entry!r}")
        case_id = str(entry["id"])
        if case_id in seen:
            raise AskEvalError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        cls = str(entry["class"])
        if cls not in ASK_CASE_CLASSES:
            raise AskEvalError(f"{case_id}: unknown class {cls!r}; expected one of {ASK_CASE_CLASSES}")

        script = entry.get("script") or {}
        if not isinstance(script, dict):
            raise AskEvalError(f"{case_id}: script must be a mapping of task -> responses")
        for task in script:
            if task not in _SCRIPTABLE:
                raise AskEvalError(
                    f"{case_id}: script targets unknown task {task!r};"
                    f" expected one of {sorted(_SCRIPTABLE)}"
                )
        cases.append(
            AskCase(
                id=case_id,
                cls=cls,
                question=str(entry["question"]),
                notes=str(entry.get("notes") or ""),
                script={k: list(v or []) for k, v in script.items()},
                expect=dict(entry.get("expect") or {}),
                max_rounds=entry.get("max_rounds"),
                known_limitation=str(entry.get("known_limitation") or "").strip(),
            )
        )
    return cases


def missing_case_classes(cases: Sequence[AskCase]) -> list[str]:
    """Classes the spec requires that the frozen set does not cover."""
    covered = {c.cls for c in cases}
    return [cls for cls in ASK_CASE_CLASSES if cls not in covered]


#: expect keys that name a document path in the corpus.
_DOC_KEYS = ("evidence_docs", "cited_docs", "conflicting_docs")


def resolve_ask_cases(cases: Sequence[AskCase], processed_dir: Path | str) -> list[str]:
    """Verify every corpus reference in `cases` against the live corpus.

    The retrieval harness treats an unresolvable golden id as a build error
    (`resolve_golden_set`); without the same step here, a case that names a
    document or claim the corpus no longer has fails as
    "evidence_docs: never retrieved", which is indistinguishable from a genuine
    retrieval regression. Returns the list of errors; empty means clean.
    """
    from .harness import build_claim_index  # local import: avoids a cycle at module load

    processed_dir = Path(processed_dir)
    claim_index = build_claim_index(processed_dir)
    errors: list[str] = []
    for case in cases:
        for key in _DOC_KEYS:
            for rel in case.expect.get(key) or []:
                if not (processed_dir / rel).is_file():
                    errors.append(f"{case.id}: {key} path does not exist in the corpus: {rel}")
        for record_id in case.expect.get("evidence_claims") or []:
            claim_id = str(record_id).removeprefix("claim:")
            if claim_id not in claim_index:
                errors.append(
                    f"{case.id}: evidence_claims id not found in any source note: {record_id}"
                )
    return errors


# ---------------------------------------------------------------------------
# Scripted, keyless LLM
# ---------------------------------------------------------------------------


def build_scripted_llm(settings: Settings, script: dict[str, list[Any]]) -> MockLLM:
    """A fresh MockLLM preloaded with one case's scripted turns.

    A mapping entry is validated into the task's output model, so a scripted
    "good" response cannot silently drift from the contract's schema. A string
    entry is handed back as raw text — that is how malformed model output is
    expressed, and it reaches the pipeline exactly the way a real provider's
    unparseable response would.
    """
    llm = MockLLM(settings)
    for task, responses in script.items():
        model = _SCRIPTABLE[task]
        for response in responses:
            if isinstance(response, str):
                llm.push(task, response)
                continue
            if not isinstance(response, dict):
                raise AskEvalError(
                    f"scripted response for {task!r} must be a mapping or a string,"
                    f" got {type(response).__name__}"
                )
            try:
                llm.push(task, model.model_validate(response))
            except ValidationError as exc:
                raise AskEvalError(f"scripted {task!r} response does not match its schema: {exc}") from exc
    return llm


# ---------------------------------------------------------------------------
# Observation + mechanical checks
# ---------------------------------------------------------------------------


#: Same shape the ask pipeline's own citation gate recognises.
_EMITTED_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")

#: The record-id namespaces the index issues. A bracketed token outside these
#: is ordinary prose ("[sic]"), not a citation.
_RECORD_NAMESPACES = ("claim:", "chunk:", "wiki:")


def _emitted_citations(answer_markdown: str) -> list[str]:
    """Record-shaped citations a reader would see in the rendered answer."""
    return [
        token
        for token in _EMITTED_CITATION_RE.findall(answer_markdown)
        if token.startswith(_RECORD_NAMESPACES)
    ]


def _domain_of(rel_path: str) -> str:
    return rel_path.split("/", 1)[0] if "/" in rel_path else ""


def observe(outcome: AskOutcome) -> dict[str, Any]:
    """The deterministic, comparable projection of one ask run.

    Excludes run_id (a timestamp) and run_dir (a path); everything else about
    the answer is expected to reproduce exactly under mock providers.
    """
    return {
        "answer_markdown": outcome.answer_markdown,
        "confidence": outcome.confidence,
        "gaps": list(outcome.gaps),
        "sources": [dict(s) for s in outcome.sources],
        "evidence": [dict(e) for e in outcome.evidence],
        "warnings": list(outcome.warnings),
        "extractive": outcome.extractive,
        "zero_evidence": outcome.zero_evidence,
        "rounds": outcome.rounds,
        "nearest_wiki_titles": list(outcome.nearest_wiki_titles),
    }


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(name: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail if not passed else "")


def grade(case: AskCase, obs: dict[str, Any], repeat: dict[str, Any]) -> list[CheckResult]:
    """Every mechanical check for one case. Order is stable for reporting."""
    checks: list[CheckResult] = []
    expect = case.expect

    evidence_ids = {e["record_id"] for e in obs["evidence"]}
    evidence_docs = {e["rel_path"] for e in obs["evidence"]}
    cited_ids = {s["record_id"] for s in obs["sources"]}
    cited_docs = {s["rel_path"] for s in obs["sources"]}

    # --- universal safety invariants ---------------------------------------

    # Read the citations out of the ANSWER TEXT, not out of `sources`. `sources`
    # is derived from the evidence pool by construction, so comparing it against
    # that same pool can never fail -- it would report a green invariant with the
    # citation gate ripped out. What a reader actually sees is the bracketed
    # tokens in answer_markdown, and those are what must resolve.
    emitted = _emitted_citations(obs["answer_markdown"])
    stray = sorted(set(emitted) - evidence_ids)
    checks.append(
        _check(
            "citations_within_evidence",
            not stray,
            f"answer cites record ids that were never retrieved: {stray}",
        )
    )
    checks.append(
        _check(
            "sources_within_evidence",
            not (cited_ids - evidence_ids),
            f"listed sources absent from the evidence pool: {sorted(cited_ids - evidence_ids)}",
        )
    )
    checks.append(
        _check(
            "deterministic",
            obs == repeat,
            "a second identical run produced a different answer: "
            + ", ".join(k for k in obs if obs[k] != repeat.get(k)),
        )
    )

    # --- evidence collection ------------------------------------------------

    if (want := expect.get("evidence_docs")) is not None:
        missing = sorted(set(want) - evidence_docs)
        checks.append(_check("evidence_docs", not missing, f"never retrieved: {missing}"))

    if (want := expect.get("evidence_claims")) is not None:
        missing = sorted(set(want) - evidence_ids)
        checks.append(_check("evidence_claims", not missing, f"never retrieved: {missing}"))

    if (want := expect.get("conflicting_docs")) is not None:
        missing = sorted(set(want) - evidence_docs)
        checks.append(
            _check(
                "conflicting_docs",
                not missing,
                f"contradiction case did not surface both sides; missing: {missing}",
            )
        )

    if (want := expect.get("evidence_min")) is not None:
        checks.append(
            _check(
                "evidence_min",
                len(obs["evidence"]) >= int(want),
                f"collected {len(obs['evidence'])} evidence item(s), wanted at least {want}",
            )
        )

    if (want := expect.get("evidence_domains_min")) is not None:
        domains = {_domain_of(d) for d in evidence_docs if _domain_of(d)}
        checks.append(
            _check(
                "evidence_domains_min",
                len(domains) >= int(want),
                f"evidence spans {sorted(domains)}, wanted at least {want} domains",
            )
        )

    # --- answer + citation shape -------------------------------------------

    if (want := expect.get("min_citations")) is not None:
        checks.append(
            _check(
                "min_citations",
                len(cited_ids) >= int(want),
                f"{len(cited_ids)} citation(s), wanted at least {want}",
            )
        )

    if (want := expect.get("cited_docs")) is not None:
        missing = sorted(set(want) - cited_docs)
        checks.append(_check("cited_docs", not missing, f"required doc(s) not cited: {missing}"))

    if (want := expect.get("abstains")) is not None:
        abstained = obs["zero_evidence"] or not obs["sources"]
        checks.append(
            _check(
                "abstains",
                abstained is bool(want),
                f"abstained={abstained}, wanted {want}"
                + (f"; answered with {len(obs['sources'])} citation(s)" if not want else ""),
            )
        )

    if (want := expect.get("confidence")) is not None:
        checks.append(
            _check("confidence", obs["confidence"] == want, f"confidence={obs['confidence']!r}, wanted {want!r}")
        )

    if (want := expect.get("answer_contains")) is not None:
        missing = [s for s in want if s not in obs["answer_markdown"]]
        checks.append(_check("answer_contains", not missing, f"answer lacks: {missing}"))

    if (want := expect.get("answer_excludes")) is not None:
        lowered = obs["answer_markdown"].lower()
        present = [s for s in want if s.lower() in lowered]
        checks.append(
            _check("answer_excludes", not present, f"answer asserts what it cannot support: {present}")
        )

    if (want := expect.get("warnings_contain")) is not None:
        joined = " | ".join(obs["warnings"])
        missing = [s for s in want if s not in joined]
        checks.append(
            _check("warnings_contain", not missing, f"missing warning(s) {missing}; got: {joined!r}")
        )

    # --- guards -------------------------------------------------------------

    if (want := expect.get("extractive")) is not None:
        checks.append(
            _check("extractive", obs["extractive"] is bool(want), f"extractive={obs['extractive']}, wanted {want}")
        )

    if (want := expect.get("rounds")) is not None:
        checks.append(_check("rounds", obs["rounds"] == int(want), f"ran {obs['rounds']} round(s), wanted {want}"))

    if (want := expect.get("rounds_max")) is not None:
        checks.append(
            _check("rounds_max", obs["rounds"] <= int(want), f"ran {obs['rounds']} round(s), cap was {want}")
        )

    return checks


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@dataclass
class AskCaseResult:
    id: str
    cls: str
    checks: list[CheckResult]
    rounds: int
    extractive: bool
    zero_evidence: bool
    evidence_count: int
    citation_count: int
    known_limitation: str = ""

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.cls,
            "passed": self.passed,
            "rounds": self.rounds,
            "extractive": self.extractive,
            "zero_evidence": self.zero_evidence,
            "evidence_count": self.evidence_count,
            "citation_count": self.citation_count,
            "known_limitation": self.known_limitation,
            "checks": [c.to_dict() for c in self.checks],
        }


def run_ask_case(
    case: AskCase,
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
) -> AskCaseResult:
    """Run one case twice (same script, fresh LLM each time) and grade it."""

    def once() -> dict[str, Any]:
        llm = build_scripted_llm(settings, copy.deepcopy(case.script))
        outcome = run_ask(
            case.question,
            settings,
            backend,
            embedder,
            llm,
            max_rounds=case.max_rounds,
        )
        return observe(outcome)

    first = once()
    second = once()
    checks = grade(case, first, second)
    return AskCaseResult(
        id=case.id,
        cls=case.cls,
        checks=checks,
        rounds=first["rounds"],
        extractive=first["extractive"],
        zero_evidence=first["zero_evidence"],
        evidence_count=len(first["evidence"]),
        citation_count=len(first["sources"]),
        known_limitation=case.known_limitation,
    )


@dataclass
class AskSuiteReport:
    results: list[AskCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def per_class(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for cls in ASK_CASE_CLASSES:
            rows = [r for r in self.results if r.cls == cls]
            if not rows:
                continue
            out[cls] = {"n": len(rows), "passed": sum(1 for r in rows if r.passed)}
        return out

    def overall(self) -> dict[str, int]:
        checks = [c for r in self.results for c in r.checks]
        return {
            "cases": len(self.results),
            "cases_passed": sum(1 for r in self.results if r.passed),
            "checks": len(checks),
            "checks_passed": sum(1 for c in checks if c.passed),
        }

    def limitations(self) -> list[dict[str, str]]:
        """Behaviours pinned by a passing case that are still not what we want."""
        return [
            {"id": r.id, "class": r.cls, "limitation": r.known_limitation}
            for r in self.results
            if r.known_limitation
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall(),
            "per_class": self.per_class(),
            "limitations": self.limitations(),
            "cases": [r.to_dict() for r in self.results],
        }


def run_ask_suite(
    cases: Sequence[AskCase],
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
) -> AskSuiteReport:
    return AskSuiteReport(
        results=[run_ask_case(c, settings, backend, embedder) for c in cases]
    )


# ---------------------------------------------------------------------------
# Regression comparison
# ---------------------------------------------------------------------------


def compare_ask_to_baseline(current: AskSuiteReport, baseline: dict[str, Any]) -> dict[str, Any]:
    """Diff a suite run against a frozen baseline.

    A regression is a case (or a named check) that the baseline recorded as
    passing and this run does not. New cases absent from the baseline are
    reported separately rather than counted as regressions, so adding coverage
    never fails the gate on its own.
    """
    base_cases = {c["id"]: c for c in baseline.get("cases", [])}
    regressed: list[str] = []
    fixed: list[str] = []
    new: list[str] = []
    dropped = sorted(set(base_cases) - {r.id for r in current.results})

    # Deleting a passing case, or deleting checks from one, weakens the gate
    # exactly as much as breaking it. Both are regressions.
    for case_id in dropped:
        if base_cases[case_id].get("passed"):
            regressed.append(f"{case_id}: was passing and has been removed from the suite")

    for result in current.results:
        base = base_cases.get(result.id)
        if base is None:
            new.append(result.id)
            continue
        if base.get("passed") and not result.passed:
            failing = ", ".join(c.name for c in result.failures())
            regressed.append(f"{result.id}: was passing, now fails [{failing}]")
        elif not base.get("passed") and result.passed:
            fixed.append(result.id)
            continue
        base_checks = {c["name"]: c["passed"] for c in base.get("checks", [])}
        for check in result.checks:
            if base_checks.get(check.name) and not check.passed:
                entry = f"{result.id}.{check.name}: was passing, now fails ({check.detail})"
                if entry not in regressed:
                    regressed.append(entry)
        removed = sorted(
            name
            for name, passed in base_checks.items()
            if passed and name not in {c.name for c in result.checks}
        )
        if removed:
            regressed.append(f"{result.id}: passing check(s) removed from the case: {removed}")

    return {
        "regressed": regressed,
        "fixed": fixed,
        "new_cases": new,
        "dropped_cases": dropped,
    }
