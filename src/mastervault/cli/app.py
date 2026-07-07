"""CLI root. Subcommand modules register themselves here as they land."""

from __future__ import annotations

import typer

from mastervault import __version__
from mastervault.cli.admin import admin_app
from mastervault.cli.query import query_app

try:  # the review CLI lands separately; the root app must not depend on it
    from mastervault.cli.review import review_app
except ImportError:
    review_app = None

try:  # the pipeline CLIs land separately; the root app must not depend on them
    from mastervault.cli.ask import ask_app
except ImportError:
    ask_app = None

try:
    from mastervault.cli.ingest import ingest_app
except ImportError:
    ingest_app = None

try:
    from mastervault.cli.lint import lint_app
except ImportError:
    lint_app = None

try:
    from mastervault.cli.runs import runs_app
except ImportError:
    runs_app = None

try:
    from mastervault.cli.demo import demo_app
except ImportError:
    demo_app = None

try:
    from mastervault.cli.evals import eval_app
except ImportError:
    eval_app = None

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
