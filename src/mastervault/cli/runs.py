"""`mvault runs` — list run directories, or `runs show <run-id>` for detail.

Registered via `add_typer`, the same pattern as `review` (see app.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import load_settings
from mastervault.core.events import EventName, read_events

runs_app = typer.Typer(
    name="runs",
    help="Inspect pipeline run directories.",
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)

_console = Console()

_STATUS_BY_EXIT_CODE = {0: "ok", 1: "failed", 3: "budget-exhausted", 4: "resume-conflict"}


def _run_dirs(runs_dir: Path) -> list[Path]:
    if not runs_dir.is_dir():
        return []
    return sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)


def _pipeline_of(run_id: str) -> str:
    return run_id.split("-", 1)[0]


def _started_of(run_dir: Path) -> str:
    for ev in read_events(run_dir / "events.jsonl"):
        return ev.ts
    return "-"


def _load_summary(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "summary.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _status_of(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return "in-progress"
    code = summary.get("exit_code")
    if code is None:
        return "done"
    return _STATUS_BY_EXIT_CODE.get(code, f"exit-{code}")


def _cost_of(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return "-"
    cost = summary.get("cost_usd")
    return f"${cost:.4f}" if isinstance(cost, int | float) else "-"


@runs_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """List every run directory: pipeline, started, status, cost."""
    if ctx.invoked_subcommand is not None:
        return
    settings = load_settings()
    dirs = _run_dirs(Path(settings.paths.runs_dir))
    table = Table(title=f"runs ({len(dirs)})")
    for col in ("run_id", "pipeline", "started", "status", "cost"):
        table.add_column(col)
    for run_dir in dirs:
        summary = _load_summary(run_dir)
        table.add_row(
            run_dir.name,
            _pipeline_of(run_dir.name),
            _started_of(run_dir),
            _status_of(summary),
            _cost_of(summary),
        )
    _console.print(table)


@runs_app.command("show")
def show_cmd(run_id: str = typer.Argument(..., help="Run id (directory name under runs/).")) -> None:
    """Show one run's summary, failed units, and budget snapshot."""
    settings = load_settings()
    run_dir = Path(settings.paths.runs_dir) / run_id
    if not run_dir.is_dir():
        typer.echo(f"error: no run directory: {run_dir}", err=True)
        raise typer.Exit(code=1)

    summary = _load_summary(run_dir)
    typer.echo(f"run:     {run_id}")
    typer.echo(f"status:  {_status_of(summary)}")
    typer.echo(f"cost:    {_cost_of(summary)}")
    if summary:
        typer.echo("\nsummary:")
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))

    events_path = run_dir / "events.jsonl"
    failed = [ev for ev in read_events(events_path) if ev.event == EventName.UNIT_FAILED]
    if failed:
        typer.echo(f"\nfailed units ({len(failed)}):")
        for ev in failed:
            typer.echo(f"  - {ev.unit}: {ev.payload}")

    budget_events = [ev for ev in read_events(events_path) if ev.event == EventName.BUDGET_EXHAUSTED]
    if budget_events:
        typer.echo("\nbudget snapshot at exhaustion:")
        typer.echo(json.dumps(budget_events[-1].payload, indent=2, ensure_ascii=False))


__all__ = ["runs_app"]
