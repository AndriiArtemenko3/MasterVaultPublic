"""`mvault lint` — mechanical vault-health checks + optional semantic
contradiction detection. Defined on `lint_app` but registered at the CLI top
level (see app.py)."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import load_settings
from mastervault.pipelines.lint import run_lint
from mastervault.providers import get_llm

lint_app = typer.Typer(help="Vault health check: mechanical + semantic.")

_console = Console()


@lint_app.command("lint")
def lint_cmd(
    mechanical_only: bool = typer.Option(False, "--mechanical-only", help="Skip the semantic (LLM) pass."),
    budget: float | None = typer.Option(None, "--budget", help="USD cap (default: budgets.lint)."),
    no_queue: bool = typer.Option(False, "--no-queue", help="Report only; never write to the review queue."),
    json_out: bool = typer.Option(False, "--json", help="Emit the full report as JSON."),
) -> None:
    """Run mechanical + (optionally) semantic vault-health checks."""
    settings = load_settings()
    llm = get_llm(settings)
    outcome = run_lint(settings, llm, mechanical_only=mechanical_only, budget_usd=budget, no_queue=no_queue)

    if json_out:
        typer.echo(json.dumps(outcome.summary, indent=2, ensure_ascii=False))
        raise typer.Exit(outcome.exit_code)

    mech = outcome.summary["mechanical"]
    sem = outcome.summary["semantic"]
    table = Table(title=f"lint report ({outcome.run_id})")
    table.add_column("check")
    table.add_column("count", justify="right")
    table.add_row("frontmatter skipped", str(mech["frontmatter_skipped"]))
    table.add_row("broken affects", str(mech["broken_affects"]))
    table.add_row("duplicate claim ids", str(mech["duplicate_claim_ids"]))
    table.add_row("orphan wiki entries", str(mech["orphan_wikis"]))
    table.add_row("review conflicts marked", str(mech["review_conflicts_marked"]))
    if not mechanical_only:
        table.add_row("contradiction pairs examined", str(sem["pairs_examined"]))
        table.add_row("contradictions queued", str(sem["contradictions_queued"]))
    _console.print(table)

    for action in outcome.summary["top_actions"]:
        typer.echo(f"  - {action}")
    typer.echo(f"\nnext: {outcome.summary['next']}")
    raise typer.Exit(outcome.exit_code)
