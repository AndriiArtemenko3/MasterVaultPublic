"""CLI root. Subcommand modules register themselves here as they land."""

from __future__ import annotations

import importlib

import typer

from mastervault import __version__
from mastervault.cli.admin import admin_app
from mastervault.cli.query import query_app


def _optional_typer(module: str, attr: str) -> typer.Typer | None:
    """Load a sub-app that may not be installed, or None.

    The review/pipeline/demo/eval CLIs land separately, so the root app must
    stay importable without them; an ImportError anywhere in a sub-app's own
    import graph simply drops that command group.
    """
    try:
        loaded = importlib.import_module(module)
    except ImportError:
        return None
    sub_app = getattr(loaded, attr, None)
    return sub_app if isinstance(sub_app, typer.Typer) else None


review_app = _optional_typer("mastervault.cli.review", "review_app")
ask_app = _optional_typer("mastervault.cli.ask", "ask_app")
ingest_app = _optional_typer("mastervault.cli.ingest", "ingest_app")
lint_app = _optional_typer("mastervault.cli.lint", "lint_app")
runs_app = _optional_typer("mastervault.cli.runs", "runs_app")
demo_app = _optional_typer("mastervault.cli.demo", "demo_app")
eval_app = _optional_typer("mastervault.cli.evals", "eval_app")

app = typer.Typer(
    name="mvault",
    help="MasterVault: internal-OS agentic RAG for small businesses.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def _root() -> None:
    """MasterVault CLI."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"mastervault {__version__}")


# Admin and query commands live on sub-apps for module hygiene but register at
# the top level: `mvault sync`, not `mvault admin sync`.
app.registered_commands += admin_app.registered_commands
app.registered_commands += query_app.registered_commands

if review_app is not None:
    app.add_typer(review_app, name="review")

if ingest_app is not None:
    app.registered_commands += ingest_app.registered_commands

if ask_app is not None:
    app.registered_commands += ask_app.registered_commands

if lint_app is not None:
    app.registered_commands += lint_app.registered_commands

if runs_app is not None:
    app.add_typer(runs_app, name="runs")

if demo_app is not None:
    app.add_typer(demo_app, name="demo")

if eval_app is not None:
    app.registered_commands += eval_app.registered_commands


def main() -> None:
    app()


if __name__ == "__main__":
    main()
