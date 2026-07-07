"""`mvault demo` — load the shipped Larkstead demo dataset (fast path).

`demo load` copies datasets/larkstead/processed/ into the configured
workspace and imports its precomputed embeddings.jsonl.gz sidecar instead of
recomputing vectors — the full CPU embed pass over this corpus runs on the
order of 16 minutes; the sidecar import runs in a few seconds. Query-time
embedding is unaffected: `mvault search` still embeds the query itself
through the configured provider (one vector per query).

Thin wrapper: all orchestration lives in `mastervault.sync.load`, defined on
`demo_app` but registered as `mvault demo` (see app.py).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import Settings, load_settings
from mastervault.providers import get_embedding_provider
from mastervault.storage import SchemaMismatchError, get_backend
from mastervault.sync import load_demo_dataset

demo_app = typer.Typer(help="Load the shipped demo dataset (fast path: precomputed vectors).")

_console = Console()

# src/mastervault/cli/demo.py -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET_DIR = REPO_ROOT / "datasets" / "larkstead"

_COUNT_ROWS = (
    ("documents", "documents"),
    ("claims", "claims"),
    ("wiki", "wiki"),
    ("chunks", "chunks"),
    ("embeddings", "embeddings"),
    ("pending_review", "pending review"),
)


def _init_backend(settings: Settings):
    """Mirrors admin._init_backend: a schema-validated backend or a clean CLI error."""
    provider = get_embedding_provider(settings)
    backend = get_backend(settings)
    try:
        backend.init_schema(provider.dimensions, provider.model_version)
    except SchemaMismatchError as exc:
        backend.close()
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return backend, provider


@demo_app.command("load")
def load_cmd(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Target workspace directory (default: configured paths.workspace)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-copy domain directories even if the workspace already has them."
    ),
) -> None:
    """Copy the shipped Larkstead dataset into the workspace and import its
    precomputed embeddings sidecar. No embedding computation happens here."""
    if not DATASET_DIR.is_dir():
        typer.echo(f"error: dataset not found at {DATASET_DIR}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    if workspace is not None and workspace != "auto":
        settings.paths.workspace = Path(workspace)

    backend, provider = _init_backend(settings)
    try:
        report = load_demo_dataset(
            dataset_dir=DATASET_DIR,
            vault_dir=settings.paths.vault_dir,
            review_pending_dir=settings.paths.review_pending,
            backend=backend,
            embedder=provider,
            force=force,
            progress=lambda message: typer.echo(f"  {message}", err=True),
        )
    finally:
        backend.close()

    table = Table(title="demo load")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key, label in _COUNT_ROWS:
        table.add_row(label, str(report.counts.get(key, 0)))
    table.add_row("wall time (s)", str(report.wall_time_s))
    _console.print(table)

    if report.sync_skipped:
        typer.echo(f"  {len(report.sync_skipped)} files skipped during sync:", err=True)
        for line in report.sync_skipped:
            typer.echo(f"    {line}", err=True)

    emb = report.embeddings
    if emb.skipped_hash_mismatch or emb.skipped_unknown_record or emb.skipped_model_mismatch:
        typer.echo(
            "  embeddings sidecar: "
            f"{emb.skipped_hash_mismatch} hash mismatch, "
            f"{emb.skipped_unknown_record} unknown record, "
            f"{emb.skipped_model_mismatch} model mismatch (skipped, not imported)",
            err=True,
        )

    if report.counts.get("embeddings", 0) <= 0:
        typer.echo(
            "error: demo load finished but the index has zero embeddings; "
            "check that embedding.provider/model matches the sidecar's "
            f"model ({provider.model_version}) and that the sidecar file exists",
            err=True,
        )
        raise typer.Exit(code=1)
