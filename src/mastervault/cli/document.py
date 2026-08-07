"""Read-only document parser diagnostics."""

# Typer's declarative Option objects intentionally live in signature defaults.
# ruff: noqa: B008

from __future__ import annotations

from pathlib import Path

import typer

from mastervault.config import load_settings
from mastervault.core.errors import EXIT_CODES
from mastervault.document_intelligence.docling_adapter import doctor_docling
from mastervault.document_intelligence.parser import PypdfParser

document_app = typer.Typer(help="Inspect document parser readiness; never download models.")


@document_app.command("doctor")
def doctor_cmd(
    parser: str = typer.Option("pypdf", "--parser", help="One of: pypdf, docling."),
    artifacts_path: Path | None = typer.Option(
        None,
        "--artifacts-path",
        help="Explicit Docling artifacts directory (overrides configuration for this check).",
    ),
) -> None:
    """Check parser packages/artifacts without parsing or changing anything."""
    if parser == "pypdf":
        typer.echo(f"ok: pypdf {PypdfParser.parser_version} ({PypdfParser.profile})")
        return
    if parser != "docling":
        typer.echo("error: --parser must be one of pypdf, docling", err=True)
        raise typer.Exit(EXIT_CODES["usage"])
    settings = load_settings()
    report = doctor_docling(artifacts_path or settings.document.docling_artifacts_path)
    if not report.ok:
        typer.echo(f"error: {report.message}", err=True)
        raise typer.Exit(EXIT_CODES["completed-with-failures"])
    versions = ", ".join(f"{name}={value}" for name, value in report.component_versions.items())
    typer.echo(f"ok: {report.message}")
    typer.echo(f"  components: {versions}")
    typer.echo(f"  model identity: {report.model_identity}")
    typer.echo(f"  verified runtime artifact bytes: {report.artifact_bytes}")
