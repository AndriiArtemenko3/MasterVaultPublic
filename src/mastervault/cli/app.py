"""CLI root. Subcommand modules register themselves here as they land."""

from __future__ import annotations

import typer

from mastervault import __version__

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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
