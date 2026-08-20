"""`mvault ask` — agentic multi-round grounded retrieval + synthesis.

Defined on `ask_app` but registered at the CLI top level (see app.py)."""

from __future__ import annotations

import json

import typer

from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_errors import (
    ChangeControlApplicationError,
    ChangeControlApplicationIntegrityError,
)
from mastervault.change_control.query_generation import QueryGenerationKind
from mastervault.config import load_settings
from mastervault.pipelines.ask import run_ask
from mastervault.providers import get_embedding_provider, get_llm

ask_app = typer.Typer(help="Ask a grounded question against the vault.")


@ask_app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="The question to ask."),
    domain: str | None = typer.Option(None, "--domain", help="Restrict retrieval to one domain."),
    max_rounds: int | None = typer.Option(None, "--max-rounds", help="Cap search rounds (default: ask.max_rounds)."),
    budget: float | None = typer.Option(None, "--budget", help="USD cap (default: ask.budget_usd)."),
    generation: str = typer.Option(
        "auto",
        "--generation",
        help="Serve auto, legacy, active, or one exact mgeneration:<sha256>.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the full structured result as JSON."),
    show_evidence: bool = typer.Option(False, "--show-evidence", help="Print every evidence item gathered."),
) -> None:
    """Ask a question; get a grounded, cited answer from the vault."""
    settings = load_settings()
    try:
        with ChangeControlApplication(settings).resolve_query_generation(
            generation
        ) as resolved:
            embedder = get_embedding_provider(settings)
            if resolved.metadata.embedding_model is not None and (
                resolved.metadata.embedding_model != embedder.model_version
                or resolved.metadata.embedding_dimensions != embedder.dimensions
            ):
                raise ChangeControlApplicationIntegrityError(
                    "configured embedding provider differs from the resolved generation"
                )
            llm = get_llm(settings)
            outcome = run_ask(
                question,
                settings,
                resolved.backend,
                embedder,
                llm,
                domain=domain,
                max_rounds=max_rounds,
                budget_usd=budget,
                evidence_workspaces=resolved.evidence_workspaces or None,
                persist_run=(
                    resolved.metadata.generation_kind == QueryGenerationKind.UNMANAGED
                ),
            )
            generation_metadata = resolved.metadata
    except ChangeControlApplicationError as exc:
        typer.echo(f"error [{exc.code.value}]: {exc}", err=True)
        raise typer.Exit(code=2 if exc.code.value == "usage-error" else 1) from exc

    generation_trace = (
        f"{outcome.trace} | knowledge generation: {generation_metadata.human_label}"
    )
    if json_out:
        payload = {
            "run_id": outcome.run_id,
            "generation": generation_metadata.model_dump(mode="json"),
            "answer_markdown": outcome.answer_markdown,
            "confidence": outcome.confidence,
            "gaps": outcome.gaps,
            "sources": outcome.sources,
            "trace": generation_trace,
            "extractive": outcome.extractive,
            "zero_evidence": outcome.zero_evidence,
            "rounds": outcome.rounds,
            "cost_usd": outcome.cost_usd,
            "warnings": outcome.warnings,
            "nearest_wiki_titles": outcome.nearest_wiki_titles,
            "evidence": outcome.evidence if show_evidence else None,
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(outcome.exit_code)

    typer.echo(f"knowledge generation: {generation_metadata.human_label}")
    if outcome.zero_evidence:
        typer.echo(outcome.answer_markdown)
        if outcome.nearest_wiki_titles:
            typer.echo("nearest wiki entries:")
            for title in outcome.nearest_wiki_titles:
                typer.echo(f"  - {title}")
        raise typer.Exit(outcome.exit_code)

    typer.echo(outcome.answer_markdown)
    if outcome.confidence:
        typer.echo(f"\nconfidence: {outcome.confidence}")
    if outcome.gaps:
        typer.echo("gaps: " + "; ".join(outcome.gaps))
    if outcome.sources:
        typer.echo("\nSources:")
        for s in outcome.sources:
            typer.echo(f"  - [{s['record_id']}] {s['rel_path']}")
    if show_evidence and outcome.evidence:
        typer.echo("\nAll evidence gathered:")
        for e in outcome.evidence:
            typer.echo(f"  - [{e['record_id']}] {e['rel_path']}")
    for w in outcome.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(f"\n{generation_trace}")
    if settings.llm.provider == "mock" and not outcome.zero_evidence:
        typer.echo(
            "\nnote: llm.provider=mock — this is the deterministic extractive fallback"
            " (retrieval is real; the answer is stitched from evidence, not generated)."
            " Set ANTHROPIC_API_KEY or OPENAI_API_KEY for generated synthesis.",
            err=True,
        )
    raise typer.Exit(outcome.exit_code)
