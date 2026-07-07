"""`mvault ingest` — raw files/directory -> vault source notes, indexed and
concept-routed. Defined on `ingest_app` but registered at the CLI top level
(see app.py), so users type `mvault ingest`, not `mvault admin ingest`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import load_settings
from mastervault.core.errors import EXIT_CODES
from mastervault.models import Domain
from mastervault.pipelines.ingest import run_ingest
from mastervault.providers import get_embedding_provider, get_llm
from mastervault.storage import get_backend

ingest_app = typer.Typer(help="Ingest raw documents into the vault.")

_console = Console()
_DOMAINS = tuple(d.value for d in Domain)


@ingest_app.command("ingest")
def ingest_cmd(
    path_arg: str = typer.Argument(..., help="Raw file or directory to ingest.", metavar="PATH"),
    domain: str = typer.Option(..., "--domain", help=f"One of: {', '.join(_DOMAINS)}."),
    budget: float | None = typer.Option(None, "--budget", help="USD cap (default: budgets.ingest)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan + cost estimate; write nothing."),
    resume: str | None = typer.Option(None, "--resume", help="Resume a previous run by run_id."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Apply tier-2 review items immediately."),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop at the first unit hard-fail."),
) -> None:
    """Ingest raw .md/.txt/.pdf files into vault source notes."""
    path = Path(path_arg)
    try:
        domain_enum = Domain(domain)
    except ValueError:
        typer.echo(f"error: --domain must be one of {', '.join(_DOMAINS)}", err=True)
        raise typer.Exit(EXIT_CODES["usage"]) from None
    if dry_run and resume:
        typer.echo("error: --dry-run and --resume cannot be combined", err=True)
        raise typer.Exit(EXIT_CODES["usage"])
    if not path.exists():
        typer.echo(f"error: path does not exist: {path}", err=True)
        raise typer.Exit(EXIT_CODES["usage"])

    settings = load_settings()
    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    llm = get_llm(settings)
    try:
        outcome = run_ingest(
            path, domain_enum, settings, backend, embedder, llm,
            budget_usd=budget, dry_run=dry_run, resume_run_id=resume,
            auto_approve=auto_approve, fail_fast=fail_fast,
            announce=lambda msg: typer.echo(f"  {msg}"),
        )
    finally:
        backend.close()

    if outcome.summary.get("error"):
        typer.echo(f"error: {outcome.summary['error']}", err=True)
        raise typer.Exit(outcome.exit_code)

    if outcome.summary.get("dry_run"):
        typer.echo(f"plan: {outcome.run_id}")
        typer.echo(f"  units planned:      {outcome.summary['units_planned']}")
        typer.echo(f"  estimated cost:     ${outcome.summary['estimated_cost_usd']:.4f}")
        typer.echo(f"  budget cap:         ${outcome.summary['budget_cap_usd']:.4f}")
        raise typer.Exit(outcome.exit_code)

    table = Table(title=f"ingest report ({outcome.run_id})")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key in (
        "units_total", "units_completed", "claims_routed", "wikilinks_inserted",
        "tier2_enqueued", "tier3_enqueued", "new_concepts_drafted",
        "docs_upserted", "records_embedded",
    ):
        table.add_row(key.replace("_", " "), str(outcome.summary.get(key, 0)))
    table.add_row("cost usd", f"${outcome.summary.get('cost_usd', 0.0):.4f}")
    _console.print(table)
    if outcome.summary.get("tier2_enqueued") or outcome.summary.get("tier3_enqueued"):
        typer.echo("next: mvault review list")
    raise typer.Exit(outcome.exit_code)
