"""`mvault ask` — agentic multi-round grounded retrieval + synthesis.

Defined on `ask_app` but registered at the CLI top level (see app.py)."""

from __future__ import annotations

import json

import typer

from mastervault.config import load_settings
from mastervault.pipelines.ask import run_ask
from mastervault.providers import get_embedding_provider, get_llm, get_reranker
from mastervault.storage import get_backend

ask_app = typer.Typer(help="Ask a grounded question against the vault.")


@ask_app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="The question to ask."),
    domain: str | None = typer.Option(None, "--domain", help="Restrict retrieval to one domain."),
    max_rounds: int | None = typer.Option(None, "--max-rounds", help="Cap search rounds (default: ask.max_rounds)."),
    budget: float | None = typer.Option(None, "--budget", help="USD cap (default: ask.budget_usd)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the full structured result as JSON."),
    show_evidence: bool = typer.Option(False, "--show-evidence", help="Print every evidence item gathered."),
) -> None:
    """Ask a question; get a grounded, cited answer from the vault."""
    settings = load_settings()
    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    llm = get_llm(settings)
    reranker = get_reranker(settings)
    try:
        outcome = run_ask(
            question, settings, backend, embedder, llm, reranker,
            domain=domain, max_rounds=max_rounds, budget_usd=budget,
        )
    finally:
        backend.close()

    if json_out:
        payload = {
            "run_id": outcome.run_id,
            "answer_markdown": outcome.answer_markdown,
            "confidence": outcome.confidence,
            "gaps": outcome.gaps,
            "sources": outcome.sources,
            "trace": outcome.trace,
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
    typer.echo(f"\n{outcome.trace}")
    raise typer.Exit(outcome.exit_code)
