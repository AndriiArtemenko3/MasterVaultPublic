"""Index administration commands: init / sync / status / reset / drop.

Defined on `admin_app` but registered at the CLI top level (see app.py), so
users type `mvault init`, not `mvault admin init`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import Settings, load_settings
from mastervault.providers import get_embedding_provider
from mastervault.storage import SchemaMismatchError, SqliteBackend, get_backend
from mastervault.sync import SyncReport, sync_vault

admin_app = typer.Typer(help="Index administration.")

_console = Console()


def _workspace_dirs(settings: Settings) -> tuple:
    return (
        settings.paths.vault_dir,
        settings.paths.review_pending,
        settings.paths.review_archive,
        settings.paths.runs_dir,
    )


def _init_backend(settings: Settings):
    """Backend with a validated schema, or a clean CLI error on model mismatch."""
    provider = get_embedding_provider(settings)
    backend = get_backend(settings)
    try:
        backend.init_schema(provider.dimensions, provider.model_version)
    except SchemaMismatchError as exc:
        backend.close()
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return backend, provider


def _print_report(report: SyncReport) -> None:
    table = Table(title="sync report")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("docs upserted", str(report.docs_upserted))
    table.add_row("docs deleted", str(report.docs_deleted))
    table.add_row("records embedded", str(report.records_embedded))
    table.add_row("records reused", str(report.records_reused))
    table.add_row("files skipped", str(len(report.skipped)))
    _console.print(table)
    for skip in report.skipped:
        typer.echo(f"  skipped {skip.rel_path}: {skip.reason}", err=True)


@admin_app.command()
def init() -> None:
    """Initialize the index schema and workspace directories. Idempotent."""
    settings = load_settings()
    backend, provider = _init_backend(settings)
    backend.close()
    for directory in _workspace_dirs(settings):
        directory.mkdir(parents=True, exist_ok=True)
    typer.echo(
        f"initialized: workspace={settings.paths.workspace}"
        f" embedding={provider.name}/{provider.model_version} ({provider.dimensions}d)"
    )


@admin_app.command()
def sync(
    full: bool = typer.Option(False, "--full", help="Re-upsert every document."),
) -> None:
    """Sync the vault into the index (changed files only unless --full)."""
    settings = load_settings()
    backend, provider = _init_backend(settings)
    try:
        report = sync_vault(
            settings.paths.vault_dir,
            backend,
            provider,
            full=full,
            progress=lambda message: typer.echo(f"  {message}", err=True),
        )
    finally:
        backend.close()
    _print_report(report)


@admin_app.command()
def status() -> None:
    """Show backend stats and the active configuration."""
    settings = load_settings()
    backend = get_backend(settings)
    try:
        stats = backend.stats()
    finally:
        backend.close()
    table = Table(title="mastervault status")
    table.add_column("key")
    table.add_column("value")
    table.add_row("storage backend", str(stats.get("backend")))
    table.add_row(
        "embedding", f"{settings.embedding.provider} / {stats.get('embedding_model')}"
    )
    table.add_row("dimensions", str(stats.get("dimensions")))
    table.add_row("llm provider", settings.llm.provider)
    table.add_row("workspace", str(settings.paths.workspace))
    for name, count in (stats.get("counts") or {}).items():
        table.add_row(name, str(count))
    _console.print(table)


@admin_app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Wipe the index and rebuild it with a full sync."""
    if not yes:
        typer.confirm("Wipe the index and re-sync the whole vault?", abort=True)
    settings = load_settings()
    backend, provider = _init_backend(settings)
    try:
        backend.wipe()
        report = sync_vault(settings.paths.vault_dir, backend, provider, full=True)
    finally:
        backend.close()
    _print_report(report)


@admin_app.command()
def drop(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Drop the index entirely (delete the SQLite file / drop the pg tables)."""
    if not yes:
        typer.confirm("Drop the whole index? This cannot be undone.", abort=True)
    settings = load_settings()
    backend = get_backend(settings)
    if isinstance(backend, SqliteBackend):
        db_path = backend.db_path
        backend.close()
        db_path.unlink(missing_ok=True)
        typer.echo(f"dropped sqlite index at {db_path}")
        return
    try:
        backend.conn.execute(
            "DROP TABLE IF EXISTS meta, documents, claims, claim_affects,"
            " wiki_aliases, chunks, embeddings CASCADE"
        )
    finally:
        backend.close()
    typer.echo("dropped postgres index tables")
