"""`mvault review` — HITL queue triage. Self-contained typer sub-app.

Exported as `review_app`; the root CLI registers it during integration.
Tier gates: a tier-3 item needs an explicit --yes on approve, and no batch
verb (approve-pattern, spot-check) will touch a group containing tier-3
items — those are always reviewed one by one.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mastervault.config import Settings, load_settings
from mastervault.core.errors import EXIT_CODES
from mastervault.models import ReviewItem, ReviewStatus
from mastervault.review.apply import ApplyResult, ConflictResult
from mastervault.review.apply import apply as apply_item
from mastervault.review.queue import LoadedReview, ReviewQueue

review_app = typer.Typer(
    name="review",
    help="Triage the human-in-the-loop review queue.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

_console = Console()


def _queue(settings: Settings | None = None) -> tuple[ReviewQueue, Settings]:
    settings = settings or load_settings()
    return ReviewQueue.from_settings(settings), settings


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


def _resolve(queue: ReviewQueue, item_id: str) -> Path:
    """Resolve an id by filename-stem prefix match against the pending dir."""
    matches = [p for p, _ in queue.list_items() if p.stem.startswith(item_id)]
    if not matches:
        raise _fail(f"no pending item matches id prefix {item_id!r}", EXIT_CODES["usage"])
    if len(matches) > 1:
        stems = ", ".join(p.stem for p in matches)
        raise _fail(
            f"id prefix {item_id!r} is ambiguous: {stems}", EXIT_CODES["usage"]
        )
    return matches[0]


def _age(created: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    seconds = max(0, int((now - created).total_seconds()))
    if seconds >= 86_400:
        return f"{seconds // 86_400}d"
    if seconds >= 3_600:
        return f"{seconds // 3_600}h"
    return f"{seconds // 60}m"


def _apply_one(path: Path, item: ReviewItem, settings: Settings) -> ApplyResult:
    result = apply_item(path, vault_root=settings.paths.vault_dir)
    if isinstance(result, ConflictResult):
        typer.echo(f"CONFLICT {item.id}: {result.reason}", err=True)
    else:
        typer.echo(f"applied {item.id} -> {item.target}")
    return result


def _render_loaded(loaded: LoadedReview) -> None:
    item = loaded.item
    typer.echo(f"id:          {item.id}")
    typer.echo(f"status:      {item.status.value}")
    typer.echo(f"tier:        {item.tier}")
    typer.echo(f"producer:    {item.producer}  (run {item.run_id})")
    typer.echo(f"change_type: {item.change_type.value}")
    typer.echo(f"target:      {item.target}")
    typer.echo(f"pattern:     {item.pattern_key}")
    typer.echo(f"importance:  {item.importance}")
    typer.echo(f"created:     {item.created.isoformat()}")
    typer.echo("")
    typer.echo("## Rationale")
    typer.echo(loaded.rationale or "(none)")
    typer.echo("")
    typer.echo(f"## Proposal ({loaded.kind})")
    typer.echo(loaded.proposal)
    if loaded.resolution:
        typer.echo("")
        typer.echo("## Resolution")
        typer.echo(loaded.resolution)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@review_app.command("list")
def list_cmd(
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    pattern: str | None = typer.Option(None, "--pattern", help="Filter by pattern_key."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List queued review items."""
    queue, _ = _queue()
    try:
        parsed_status = ReviewStatus(status) if status else None
    except ValueError:
        raise _fail(
            f"unknown status {status!r} (expected one of "
            f"{[s.value for s in ReviewStatus]})",
            EXIT_CODES["usage"],
        ) from None

    rows = queue.list_items(status=parsed_status, pattern=pattern)
    if as_json:
        typer.echo(
            json.dumps(
                [item.model_dump(mode="json") for _, item in rows],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    table = Table(title=f"review queue ({len(rows)} item{'s' if len(rows) != 1 else ''})")
    for column in ("id", "tier", "change_type", "target", "pattern", "age"):
        table.add_column(column)
    for _, item in rows:
        table.add_row(
            item.id,
            str(item.tier),
            item.change_type.value,
            item.target,
            item.pattern_key,
            _age(item.created),
        )
    _console.print(table)


@review_app.command("show")
def show_cmd(item_id: str = typer.Argument(..., help="Item id (stem prefix ok).")) -> None:
    """Render one item: frontmatter summary, rationale, proposal."""
    queue, _ = _queue()
    loaded = queue.load(_resolve(queue, item_id))
    _render_loaded(loaded)


@review_app.command("approve")
def approve_cmd(
    item_id: str = typer.Argument(..., help="Item id (stem prefix ok)."),
    yes: bool = typer.Option(False, "--yes", help="Required for tier-3 items."),
) -> None:
    """Approve one item and apply it to its target."""
    queue, settings = _queue()
    path = _resolve(queue, item_id)
    loaded = queue.load(path)
    if loaded.item.tier >= 3 and not yes:
        raise _fail(
            f"{loaded.item.id} is tier-3 (explicit-confirm): re-run with --yes",
            EXIT_CODES["usage"],
        )
    result = _apply_one(path, loaded.item, settings)
    if isinstance(result, ConflictResult):
        raise typer.Exit(EXIT_CODES["completed-with-failures"])


@review_app.command("reject")
def reject_cmd(
    item_id: str = typer.Argument(..., help="Item id (stem prefix ok)."),
    reason: str = typer.Option(..., "--reason", help="Why this proposal is rejected."),
) -> None:
    """Reject one item and archive it with the given reason."""
    queue, _ = _queue()
    path = _resolve(queue, item_id)
    loaded = queue.load(path)
    queue.archive(path, outcome="rejected", note=reason)
    typer.echo(f"rejected {loaded.item.id}: {reason}")


@review_app.command("approve-pattern")
def approve_pattern_cmd(
    pattern: str = typer.Argument(..., help="pattern_key of the batch to approve."),
) -> None:
    """Approve every pending item in one pattern group (refuses tier-3 groups)."""
    queue, settings = _queue()
    rows = queue.list_items(status=ReviewStatus.PENDING, pattern=pattern)
    if not rows:
        raise _fail(f"no pending items with pattern {pattern!r}", EXIT_CODES["usage"])
    tier3 = [item.id for _, item in rows if item.tier >= 3]
    if tier3:
        raise _fail(
            f"pattern {pattern!r} contains tier-3 items ({', '.join(tier3)}); "
            "approve those one by one with --yes",
            EXIT_CODES["usage"],
        )
    conflicts = sum(
        isinstance(_apply_one(path, item, settings), ConflictResult) for path, item in rows
    )
    typer.echo(f"pattern {pattern!r}: {len(rows) - conflicts} applied, {conflicts} conflicts")
    if conflicts:
        raise typer.Exit(EXIT_CODES["completed-with-failures"])


@review_app.command("spot-check")
def spot_check_cmd(
    pattern: str = typer.Argument(..., help="pattern_key of the batch to spot-check."),
) -> None:
    """Show min(3, n) random items from a pattern group, then offer to apply the rest."""
    queue, settings = _queue()
    rows = queue.list_items(status=ReviewStatus.PENDING, pattern=pattern)
    if not rows:
        raise _fail(f"no pending items with pattern {pattern!r}", EXIT_CODES["usage"])
    tier3 = [item.id for _, item in rows if item.tier >= 3]
    if tier3:
        raise _fail(
            f"pattern {pattern!r} contains tier-3 items ({', '.join(tier3)}); "
            "approve those one by one with --yes",
            EXIT_CODES["usage"],
        )

    sample = random.sample(rows, min(3, len(rows)))
    for i, (path, _) in enumerate(sample):
        if i:
            typer.echo("\n" + "-" * 60 + "\n")
        _render_loaded(queue.load(path))

    if not typer.confirm(f"\nApply remaining {len(rows)} pending item(s) in {pattern!r}?"):
        typer.echo("nothing applied")
        return
    conflicts = sum(
        isinstance(_apply_one(path, item, settings), ConflictResult) for path, item in rows
    )
    typer.echo(f"pattern {pattern!r}: {len(rows) - conflicts} applied, {conflicts} conflicts")
    if conflicts:
        raise typer.Exit(EXIT_CODES["completed-with-failures"])


def main() -> None:  # pragma: no cover — direct-module convenience
    review_app()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["review_app"]
