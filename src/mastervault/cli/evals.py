"""`mvault eval` — run the golden retrieval query set through `hybrid_search`
under one or more channel-ablation configs, print a metrics table, and
optionally diff against a frozen baseline.

Defined on `eval_app` but registered at the CLI top level (see app.py):
`mvault eval`, not `mvault evals eval`.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import load_settings
from mastervault.evals import (
    ALL_CONFIGS,
    NEGATIVE_CLASS,
    AskEvalError,
    available_configs,
    compare_ask_to_baseline,
    compare_to_baseline,
    load_ask_cases,
    load_golden_queries,
    missing_case_classes,
    resolve_ask_cases,
    resolve_golden_set,
    run_ask_suite,
    run_config,
    write_resolved_yaml,
)
from mastervault.providers import get_embedding_provider, get_reranker
from mastervault.providers.reranker import RerankerUnavailable
from mastervault.storage import get_backend

eval_app = typer.Typer(help="Retrieval and end-to-end ask eval harnesses.")
_console = Console()

# src/mastervault/cli/evals.py -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "datasets" / "larkstead" / "golden"
QUERIES_PATH = GOLDEN_DIR / "queries.yaml"
RESOLVED_PATH = GOLDEN_DIR / "resolved.yaml"
ASK_CASES_PATH = GOLDEN_DIR / "ask_cases.yaml"
PROCESSED_DIR = REPO_ROOT / "datasets" / "larkstead" / "processed"

_CONFIG_NAMES = [c.name for c in ALL_CONFIGS]
_METRIC_COLS = ("recall_at_5", "recall_at_10", "ndcg_at_10", "mrr")
_METRIC_LABELS = ("recall@5", "recall@10", "nDCG@10", "MRR")


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _print_report_table(name: str, report_dict: dict) -> None:
    table = Table(title=f"eval: {name}")
    table.add_column("class")
    table.add_column("n", justify="right")
    for label in _METRIC_LABELS:
        table.add_column(label, justify="right")

    overall = report_dict["overall"]
    graded_n = sum(1 for q in report_dict["queries"] if q["class"] != NEGATIVE_CLASS)
    table.add_row("overall", str(graded_n), *[_fmt(overall.get(m)) for m in _METRIC_COLS])
    for cls, m in report_dict["per_class"].items():
        if cls == NEGATIVE_CLASS:
            continue
        table.add_row(cls, str(m["n"]), *[_fmt(m.get(c)) for c in _METRIC_COLS])
    _console.print(table)

    neg = report_dict["per_class"].get(NEGATIVE_CLASS)
    if neg:
        typer.echo(
            f"  {NEGATIVE_CLASS}: abstention_rate={neg['abstention_rate']:.3f} (n={neg['n']})"
        )
    if "abstention_rate" in overall:
        typer.echo(f"  overall abstention_rate={overall['abstention_rate']:.3f}")


@eval_app.command("eval")
def eval_cmd(
    config: str = typer.Option(
        "all", "--config", help=f"One of {_CONFIG_NAMES} or 'all' (default)."
    ),
    compare: str | None = typer.Option(
        None, "--compare", help="Path to a baseline.json to diff current metrics against."
    ),
    tolerance: float = typer.Option(
        0.02, "--tolerance", help="Max allowed metric drop vs. baseline before it's a regression."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of rich tables."),
) -> None:
    """Run the golden query set through hybrid_search and report recall@5/10,
    nDCG@10, and MRR per config, with a per-class breakdown. Exits 1 if the
    golden set fails to resolve, or (with --compare) if any metric regressed
    beyond --tolerance.
    """
    if not QUERIES_PATH.is_file():
        typer.echo(f"error: golden query set not found at {QUERIES_PATH}", err=True)
        raise typer.Exit(code=1)

    queries = load_golden_queries(QUERIES_PATH)
    resolve_report = resolve_golden_set(queries, PROCESSED_DIR)
    write_resolved_yaml(resolve_report, RESOLVED_PATH)
    if not resolve_report.ok:
        typer.echo(
            f"error: {len(resolve_report.errors)} golden-set relevant_docs/relevant_claims "
            "entries failed to resolve against the live corpus:",
            err=True,
        )
        for e in resolve_report.errors[:20]:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    try:
        reranker = get_reranker(settings)
    except RerankerUnavailable:
        reranker = None

    stats = backend.stats()
    if (stats.get("counts") or {}).get("embeddings", 0) <= 0:
        backend.close()
        typer.echo(
            "error: the index has zero embeddings; run `mvault init` then "
            "`mvault demo load` before `mvault eval`",
            err=True,
        )
        raise typer.Exit(code=1)

    configs, notes = available_configs(settings, reranker)
    if config != "all":
        matches = [c for c in configs if c.name == config]
        if not matches:
            backend.close()
            available_names = [c.name for c in configs]
            typer.echo(
                f"error: --config must be one of {available_names} or 'all'", err=True
            )
            raise typer.Exit(code=2)
        configs = matches

    try:
        reports = {
            c.name: run_config(c, queries, settings, backend, embedder, reranker) for c in configs
        }
    finally:
        backend.close()

    baseline = None
    compare_path = Path(compare) if compare is not None else None
    if compare_path is not None:
        if not compare_path.is_file():
            typer.echo(f"error: baseline file not found: {compare_path}", err=True)
            raise typer.Exit(code=1)
        baseline = json.loads(compare_path.read_text(encoding="utf-8"))

    cmp_result = compare_to_baseline(reports, baseline, tolerance=tolerance) if baseline else None

    if json_out:
        payload = {
            "resolve": resolve_report.to_dict()["summary"],
            "configs": {name: r.to_dict() for name, r in reports.items()},
            "notes": notes,
        }
        if cmp_result is not None:
            payload["compare"] = cmp_result
        typer.echo(json.dumps(payload, indent=2))
        if cmp_result is not None and cmp_result["regressed"]:
            raise typer.Exit(code=1)
        return

    for name, r in reports.items():
        _print_report_table(name, r.to_dict())
    for note in notes:
        typer.echo(f"note: {note}")

    if cmp_result is not None:
        typer.echo(f"\ncompare vs {compare}:")
        for name, deltas in cmp_result["deltas"].items():
            if "note" in deltas:
                typer.echo(f"  {name}: {deltas['note']}")
                continue
            parts = [
                f"{metric}={d['current']:.3f} (Δ{d['delta']:+.3f})" for metric, d in deltas.items()
            ]
            typer.echo(f"  {name}: {', '.join(parts)}")
        if cmp_result["regressed"]:
            typer.echo("\nREGRESSED beyond tolerance:")
            for line in cmp_result["regressed"]:
                typer.echo(f"  - {line}")
            raise typer.Exit(code=1)
        typer.echo("\nno regressions beyond tolerance")


# ---------------------------------------------------------------------------
# `mvault ask-eval` -- end-to-end ask pipeline evaluation
# ---------------------------------------------------------------------------


def _require_loaded_index(settings) -> None:
    """Both eval commands need a populated index; fail with the fix, not a stack."""
    backend = get_backend(settings)
    try:
        stats = backend.stats()
    finally:
        backend.close()
    if (stats.get("counts") or {}).get("embeddings", 0) <= 0:
        typer.echo(
            "error: the index has zero embeddings; run `mvault init` then "
            "`mvault demo load` before evaluating",
            err=True,
        )
        raise typer.Exit(code=1)


@eval_app.command("ask-eval")
def ask_eval_cmd(
    cases: str | None = typer.Option(
        None, "--cases", help=f"Ask-eval case file (default: {ASK_CASES_PATH})."
    ),
    compare: str | None = typer.Option(
        None, "--compare", help="Path to an ask baseline.json to diff this run against."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of rich tables."),
) -> None:
    """Run the frozen end-to-end `ask` evaluation.

    Deterministic and keyless: every case drives the real ask pipeline with a
    scripted MockLLM and is graded mechanically -- evidence collected, citations
    resolvable, abstention, the round and novelty guards, and the malformed-output
    fallback. Kept separate from `mvault eval`, which grades retrieval ranking
    only. Exits 1 if any case fails, or (with --compare) if a case or check that
    the baseline recorded as passing now fails.
    """
    cases_path = Path(cases) if cases is not None else ASK_CASES_PATH
    if not cases_path.is_file():
        typer.echo(f"error: ask-eval case file not found at {cases_path}", err=True)
        raise typer.Exit(code=1)

    try:
        ask_cases = load_ask_cases(cases_path)
    except AskEvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not ask_cases:
        typer.echo(f"error: no cases in {cases_path}", err=True)
        raise typer.Exit(code=1)

    # Same contract as `mvault eval`: a case naming a document or claim the
    # corpus no longer has is a build error, not a retrieval regression.
    resolve_errors = resolve_ask_cases(ask_cases, PROCESSED_DIR)
    if resolve_errors:
        typer.echo(
            f"error: {len(resolve_errors)} ask-eval reference(s) failed to resolve against"
            " the live corpus:",
            err=True,
        )
        for line in resolve_errors[:20]:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    _require_loaded_index(settings)

    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    try:
        report = run_ask_suite(ask_cases, settings, backend, embedder)
    finally:
        backend.close()

    uncovered = missing_case_classes(ask_cases)
    cmp_result = None
    if compare is not None:
        compare_path = Path(compare)
        if not compare_path.is_file():
            typer.echo(f"error: ask baseline not found: {compare_path}", err=True)
            raise typer.Exit(code=1)
        cmp_result = compare_ask_to_baseline(
            report, json.loads(compare_path.read_text(encoding="utf-8"))
        )

    if json_out:
        payload = report.to_dict()
        payload["uncovered_classes"] = uncovered
        if cmp_result is not None:
            payload["compare"] = cmp_result
        typer.echo(json.dumps(payload, indent=2))
    else:
        _print_ask_report(report, uncovered, cmp_result, compare)

    failed = not report.passed
    regressed = bool(cmp_result and cmp_result["regressed"])
    if failed or regressed:
        raise typer.Exit(code=1)


def _print_ask_report(report, uncovered: list[str], cmp_result, compare: str | None) -> None:
    overall = report.overall()
    table = Table(title="ask eval (end-to-end, deterministic, keyless)")
    table.add_column("class")
    table.add_column("cases", justify="right")
    table.add_column("passed", justify="right")
    for cls, m in report.per_class().items():
        table.add_row(cls, str(m["n"]), str(m["passed"]))
    table.add_row(
        "[bold]overall[/bold]",
        f"[bold]{overall['cases']}[/bold]",
        f"[bold]{overall['cases_passed']}[/bold]",
    )
    _console.print(table)
    typer.echo(f"  checks: {overall['checks_passed']}/{overall['checks']} passed")

    for result in report.results:
        for check in result.failures():
            typer.echo(f"  FAIL {result.id} [{check.name}]: {check.detail}")

    if uncovered:
        typer.echo(f"  note: case classes with no coverage: {', '.join(uncovered)}")

    for lim in report.limitations():
        typer.echo(f"\n  known limitation ({lim['id']}): {' '.join(lim['limitation'].split())}")

    if cmp_result is not None:
        typer.echo(f"\ncompare vs {compare}:")
        for key in ("fixed", "new_cases", "dropped_cases"):
            if cmp_result[key]:
                typer.echo(f"  {key}: {', '.join(cmp_result[key])}")
        if cmp_result["regressed"]:
            typer.echo("\nREGRESSED vs baseline:")
            for line in cmp_result["regressed"]:
                typer.echo(f"  - {line}")
        else:
            typer.echo("  no regressions vs baseline")
