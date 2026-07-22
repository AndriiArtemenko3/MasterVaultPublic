"""`mvault demo` — load, inspect, reset, or delete the shipped Larkstead demo
dataset.

`demo load` copies datasets/larkstead/processed/ into the configured
workspace and imports its precomputed embeddings.jsonl.gz sidecar instead of
recomputing vectors — the full CPU embed pass over this corpus runs on the
order of 16 minutes; the sidecar import runs in a few seconds. Query-time
embedding is unaffected: `mvault search` still embeds the query itself
through the configured provider (one vector per query).

`demo status` compares the live workspace against counts derived straight
from the shipped `datasets/larkstead/processed/` tree and the embeddings
sidecar manifest, so it never drifts from whatever ships on disk.

`demo reset` wipes the index and re-runs `load_demo_dataset(force=True)`
against a cleared review queue, discarding anything a demo session did:
edited domain files, approved/rejected review items, or ingest/lint output
written into the same workspace. `demo delete` removes the workspace
directory (and, for Postgres, drops the index tables) entirely.

Thin wrapper: all orchestration lives in `mastervault.sync.load`, defined on
`demo_app` but registered as `mvault demo` (see app.py).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import Settings, load_settings
from mastervault.providers import get_embedding_provider
from mastervault.storage import (
    FileBackedBackend,
    SchemaMismatchError,
    StorageBackend,
    StorageError,
    get_backend,
)
from mastervault.sync import load_demo_dataset
from mastervault.sync.load import DOMAIN_DIRS

demo_app = typer.Typer(help="Load, inspect, reset, or delete the shipped demo dataset.")

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
        typer.echo(
            f"error: demo dataset not found at {DATASET_DIR}.\n"
            "The Larkstead demo ships with the repository, not with the installed\n"
            "package: clone https://github.com/AndriiArtemenko3/MasterVaultPublic and\n"
            "run `mvault demo load` from the checkout, or point --workspace at a vault\n"
            "you populate with `mvault ingest` / `mvault sync` instead.",
            err=True,
        )
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


def _resolve_workspace(settings: Settings, workspace: str | None) -> None:
    if workspace is not None and workspace != "auto":
        settings.paths.workspace = Path(workspace)


def _wiki_count(backend: StorageBackend) -> int:
    return len({(domain, slug) for slug, domain in backend.alias_index().values()})


def _pending_review_count(review_pending_dir: Path) -> int:
    return sum(1 for _ in review_pending_dir.glob("*.md")) if review_pending_dir.is_dir() else 0


def _expected_counts(dataset_dir: Path) -> dict[str, int]:
    """Counts derived straight from the shipped dataset files: the
    processed/ domain trees for documents/wiki, `_review/pending/` for the
    review queue, and the embeddings sidecar's own manifest.json for
    claims/chunks/embeddings. No number here is hand-maintained, so `demo
    status` can never drift from what actually ships in datasets/larkstead/.
    """
    processed_dir = dataset_dir / "processed"
    documents = 0
    wiki = 0
    for domain in DOMAIN_DIRS:
        domain_dir = processed_dir / domain
        if not domain_dir.is_dir():
            continue
        for sub in ("sources", "wiki", "decisions", "strategy"):
            subdir = domain_dir / sub
            if not subdir.is_dir():
                continue
            n = sum(1 for _ in subdir.glob("*.md"))
            documents += n
            if sub == "wiki":
                wiki += n

    pending_review = _pending_review_count(processed_dir / "_review" / "pending")

    claims = chunks = embeddings = 0
    manifest_path = dataset_dir / "embeddings" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        embeddings = manifest.get("count", 0)
        record_type_counts = manifest.get("record_type_counts", {})
        claims = record_type_counts.get("claim", 0)
        chunks = record_type_counts.get("chunk", 0)

    return {
        "documents": documents,
        "claims": claims,
        "wiki": wiki,
        "chunks": chunks,
        "embeddings": embeddings,
        "pending_review": pending_review,
    }


@demo_app.command("status")
def status_cmd(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Target workspace directory (default: configured paths.workspace)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Compare the loaded workspace against the shipped demo dataset: counts
    per record type, plus whether the embeddings sidecar is fully imported."""
    if not DATASET_DIR.is_dir():
        typer.echo(f"error: dataset not found at {DATASET_DIR}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    _resolve_workspace(settings, workspace)
    expected = _expected_counts(DATASET_DIR)

    # `mvault init` already creates an empty vault_dir, so "loaded" means at
    # least one of the demo's domain directories was actually copied in.
    demo_loaded = any((settings.paths.vault_dir / domain).is_dir() for domain in DOMAIN_DIRS)
    if not demo_loaded:
        if json_out:
            typer.echo(json.dumps({"loaded": False, "expected": expected}, indent=2))
        else:
            typer.echo(f"demo not loaded: no domain directories under {settings.paths.vault_dir}")
            typer.echo("run: mvault init && mvault demo load")
        raise typer.Exit(code=1)

    backend = get_backend(settings)
    try:
        stats = backend.stats()
        wiki_count = _wiki_count(backend)
    finally:
        backend.close()

    live = dict(stats.get("counts", {}))
    live["wiki"] = wiki_count
    live["pending_review"] = _pending_review_count(settings.paths.review_pending)

    if json_out:
        payload = {
            "loaded": True,
            "workspace": str(settings.paths.workspace),
            "embedding_model": stats.get("embedding_model"),
            "live": {key: live.get(key, 0) for key, _label in _COUNT_ROWS},
            "expected": expected,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    table = Table(title="demo status")
    table.add_column("metric")
    table.add_column("loaded", justify="right")
    table.add_column("shipped", justify="right")
    table.add_column("match", justify="center")
    for key, label in _COUNT_ROWS:
        loaded_v = live.get(key, 0)
        expected_v = expected.get(key, 0)
        table.add_row(label, str(loaded_v), str(expected_v), "ok" if loaded_v == expected_v else "differs")
    _console.print(table)
    typer.echo(f"workspace: {settings.paths.workspace}")
    typer.echo(f"embedding: {settings.embedding.provider} / {stats.get('embedding_model')}")

    live_embeddings = live.get("embeddings", 0)
    expected_embeddings = expected.get("embeddings", 0)
    if live_embeddings <= 0:
        typer.echo("embeddings: none imported — run `mvault demo load`")
    elif live_embeddings != expected_embeddings:
        typer.echo("embeddings: present but count differs from the shipped sidecar (partial load or local edits)")
    else:
        typer.echo("embeddings: fully imported, matches the shipped sidecar")


@demo_app.command("reset")
def reset_cmd(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Target workspace directory (default: configured paths.workspace)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Restore the demo workspace to its pristine shipped state.

    Wipes the index, discards any local edits to the copied domain files,
    clears the review queue (pending + archive), and re-imports the demo
    dataset from scratch. Idempotent: safe to run again on an already-
    pristine workspace, or on one that was never loaded."""
    if not DATASET_DIR.is_dir():
        typer.echo(f"error: dataset not found at {DATASET_DIR}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    _resolve_workspace(settings, workspace)

    if not yes:
        typer.confirm(
            f"Wipe the index at {settings.paths.workspace} and restore the pristine "
            "demo dataset? Local edits and review decisions will be discarded.",
            abort=True,
        )

    backend, provider = _init_backend(settings)
    backend.wipe()

    for review_dir in (settings.paths.review_pending, settings.paths.review_archive):
        if review_dir.exists():
            shutil.rmtree(review_dir)

    try:
        report = load_demo_dataset(
            dataset_dir=DATASET_DIR,
            vault_dir=settings.paths.vault_dir,
            review_pending_dir=settings.paths.review_pending,
            backend=backend,
            embedder=provider,
            force=True,
            progress=lambda message: typer.echo(f"  {message}", err=True),
        )
    finally:
        backend.close()

    table = Table(title="demo reset")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key, label in _COUNT_ROWS:
        table.add_row(label, str(report.counts.get(key, 0)))
    _console.print(table)
    typer.echo(f"workspace restored to the shipped demo state: {settings.paths.workspace}")


@demo_app.command("delete")
def delete_cmd(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Target workspace directory (default: configured paths.workspace)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Remove the demo workspace entirely: close the index (postgres index
    tables are dropped first; the sqlite file lives inside the workspace and
    goes with it) and delete the workspace directory tree (vault files,
    review queue, run logs). Idempotent: a second run against an
    already-deleted workspace is a no-op."""
    settings = load_settings()
    _resolve_workspace(settings, workspace)
    ws = settings.paths.workspace

    if not ws.exists():
        typer.echo(f"nothing to delete: no workspace at {ws}")
        return

    if not yes:
        typer.confirm(f"Delete the workspace at {ws}? This cannot be undone.", abort=True)

    try:
        backend = get_backend(settings)
    except StorageError:
        backend = None
    if backend is not None:
        # A file-backed index lives inside the workspace and goes with the
        # rmtree below; a server-backed one has to give its schema back first.
        if isinstance(backend, FileBackedBackend):
            backend.close()
        else:
            try:
                backend.drop_schema()
            finally:
                backend.close()

    shutil.rmtree(ws)
    typer.echo(f"deleted demo workspace at {ws}")
