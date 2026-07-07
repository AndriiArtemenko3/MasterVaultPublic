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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
