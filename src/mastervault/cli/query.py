"""Query commands: search / claims / wiki.

Defined on `query_app` but registered at the CLI top level (see app.py).
Human output is one line per hit; channel provenance and timings ship in
--json only.
"""

from __future__ import annotations

import json

import typer

from mastervault.config import load_settings
from mastervault.models import Hit
from mastervault.providers import get_embedding_provider, get_reranker
from mastervault.retrieval import hybrid_search
from mastervault.storage import get_backend

query_app = typer.Typer(help="Query the index.")

_RECORD_TYPES = ("claim", "chunk", "wiki", "all")
_CONFIDENCES = ("low", "medium", "high")
_CLAIMS_FETCH_K = 50


def _one_line(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]


def _render_hit(hit: Hit) -> str:
    confidence = hit.confidence.value if hit.confidence is not None else "-"
    return f"[{hit.record_type.value}] ({confidence}) {_one_line(hit.text)} -> {hit.rel_path}"


@query_app.command()
def search(
    query: str = typer.Argument(..., help="Free-text query."),
    domain: str | None = typer.Option(None, "--domain", help="Restrict to one domain."),
    k: int | None = typer.Option(None, "--k", help="Number of hits (default: retrieval.k)."),
    record_type: str = typer.Option(
        "all", "--type", help="Filter hits: claim | chunk | wiki | all."
    ),
    rerank: bool = typer.Option(False, "--rerank", help="Rerank the top pool."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON with channel provenance."),
) -> None:
    """Hybrid search across claims, chunks, and wiki entries."""
    if record_type not in _RECORD_TYPES:
        typer.echo(f"error: --type must be one of {', '.join(_RECORD_TYPES)}", err=True)
        raise typer.Exit(code=2)
    settings = load_settings()
    backend = get_backend(settings)
    embedder = get_embedding_provider(settings)
    reranker = get_reranker(settings) if rerank else None
    try:
        result = hybrid_search(
            query,
            settings,
            backend,
            embedder,
            reranker,
            k=k,
            domain=domain,
            record_types=None if record_type == "all" else [record_type],
            rerank=rerank,
        )
    finally:
        backend.close()

    if json_out:
        payload = {
            "query": query,
            "wiki_card": (
                result.wiki_card.model_dump(mode="json") if result.wiki_card else None
            ),
            "hits": [hit.model_dump(mode="json") for hit in result.hits],
            "channel_counts": result.channel_counts,
            "timings": result.timings,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    if result.wiki_card is not None:
        card = result.wiki_card
        typer.echo(f"=== {card.doc_id} -> {card.rel_path}")
        typer.echo(f"    {_one_line(card.text, 300)}")
    if not result.hits:
        typer.echo("no hits")
        return
    for hit in result.hits:
        typer.echo(_render_hit(hit))


@query_app.command()
def claims(
    query: str = typer.Argument(..., help="Free-text query (lexical only)."),
    affects: str | None = typer.Option(None, "--affects", help="Filter by affected wiki slug."),
    confidence: str | None = typer.Option(
        None, "--confidence", help="Filter: low | medium | high."
    ),
    domain: str | None = typer.Option(None, "--domain", help="Restrict to one domain."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Lexical search over the claims layer."""
    if confidence is not None and confidence not in _CONFIDENCES:
        typer.echo(f"error: --confidence must be one of {', '.join(_CONFIDENCES)}", err=True)
        raise typer.Exit(code=2)
    settings = load_settings()
    backend = get_backend(settings)
    try:
        claim_ids = backend.lexical_claims(query, _CLAIMS_FETCH_K, domain)
        rows = backend.get_claims(claim_ids)
    finally:
        backend.close()
    if affects is not None:
        rows = [r for r in rows if affects in r.affects]
    if confidence is not None:
        rows = [r for r in rows if r.confidence == confidence]

    if json_out:
        payload = [
            {
                "claim_id": r.claim_id,
                "statement": r.statement,
                "confidence": r.confidence,
                "affects": r.affects,
                "doc_id": r.doc_id,
                "rel_path": r.rel_path,
                "domain": r.domain,
            }
            for r in rows
        ]
        typer.echo(json.dumps(payload, indent=2))
        return
    if not rows:
        typer.echo("no claims matched")
        return
    for r in rows:
        typer.echo(f"[{r.claim_id}] ({r.confidence}) {_one_line(r.statement)} -> {r.rel_path}")


@query_app.command()
def wiki(
    action: str | None = typer.Argument(None, help="Omit to list; 'show' to display one entry."),
    slug: str | None = typer.Argument(None, help="Wiki slug for 'show'."),
) -> None:
    """List wiki entries per domain, or `wiki show <slug>` for one entry."""
    settings = load_settings()
    backend = get_backend(settings)
    try:
        alias_index = backend.alias_index()
        pairs = sorted({(domain, wiki_slug) for wiki_slug, domain in alias_index.values()})

        if action is None:
            if not pairs:
                typer.echo("no wiki entries indexed")
                return
            docs = backend.get_documents([f"wiki:{d}:{s}" for d, s in pairs])
            titles = {doc.doc_id: doc.title for doc in docs}
            current_domain = None
            for domain, wiki_slug in pairs:
                if domain != current_domain:
                    typer.echo(f"{domain}:")
                    current_domain = domain
                title = titles.get(f"wiki:{domain}:{wiki_slug}", "")
                typer.echo(f"  {wiki_slug} — {title}")
            return

        if action != "show" or slug is None:
            typer.echo("usage: wiki           (list per domain)", err=True)
            typer.echo("       wiki show <slug>", err=True)
            raise typer.Exit(code=2)

        match = next(((d, s) for d, s in pairs if s == slug), None)
        if match is None:
            typer.echo(f"no wiki entry with slug {slug!r}", err=True)
            raise typer.Exit(code=1)
        domain, wiki_slug = match
        docs = backend.get_documents([f"wiki:{domain}:{wiki_slug}"])
        if not docs:
            typer.echo(f"wiki entry {slug!r} has aliases but no document row", err=True)
            raise typer.Exit(code=1)
        doc = docs[0]
        aliases = sorted(a for a, (s, _d) in alias_index.items() if s == wiki_slug)
        typer.echo(f"# {doc.title}")
        typer.echo(f"domain: {doc.domain}")
        typer.echo(f"path: {doc.rel_path}")
        typer.echo(f"aliases: {', '.join(aliases)}")
        typer.echo("")
        typer.echo(doc.body.strip())
    finally:
        backend.close()
