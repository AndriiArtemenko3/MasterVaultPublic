"""Inspect the immutable page evidence behind a grounded PDF claim."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import load_settings
from mastervault.core.errors import DocumentIntegrityError, EvidenceGroundingError
from mastervault.evidence import resolve_claim_evidence
from mastervault.storage import get_backend

evidence_app = typer.Typer(help="Inspect verified evidence behind PDF claims.")
_console = Console()


@evidence_app.command("show")
def show(
    claim_id: str = typer.Argument(..., help="Claim id, with or without the claim: prefix."),
    as_json: bool = typer.Option(False, "--json", help="Emit the resolved bundle as JSON."),
) -> None:
    """Show the exact page, block, and quote supporting one claim."""
    settings = load_settings()
    backend = get_backend(settings)
    try:
        bundle = resolve_claim_evidence(claim_id, backend, settings.paths.workspace)
    except (DocumentIntegrityError, EvidenceGroundingError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        backend.close()

    if as_json:
        typer.echo(bundle.model_dump_json(indent=2))
        return

    typer.echo(bundle.statement)
    typer.echo(f"source: {bundle.document_title} ({bundle.source_asset.original_filename})")
    typer.echo(
        "parser: "
        f"{bundle.parsed_document.parser} {bundle.parsed_document.parser_version} "
        f"[{bundle.parsed_document.parser_profile}]"
    )
    table = Table(title=f"evidence for {bundle.claim_id}")
    table.add_column("page", justify="right")
    table.add_column("block")
    table.add_column("quote")
    for ref in bundle.evidence:
        table.add_row(str(ref.page_number), ref.block_id, ref.quote)
    _console.print(table)
