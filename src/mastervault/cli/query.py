"""Query commands: search / claims / wiki.

Defined on `query_app` but registered at the CLI top level (see app.py).
Human output is one line per hit; channel provenance and timings ship in
--json only.
"""

from __future__ import annotations

import json

import typer

from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_errors import (
    ChangeControlApplicationError,
    ChangeControlApplicationIntegrityError,
)
from mastervault.change_control.query_generation import QueryGenerationMetadataV1
from mastervault.config import load_settings
from mastervault.models import Hit
from mastervault.providers import get_embedding_provider, get_reranker
from mastervault.retrieval import hybrid_search

query_app = typer.Typer(help="Query the index.")

_RECORD_TYPES = ("claim", "chunk", "wiki", "structural", "all")
_CONFIDENCES = ("low", "medium", "high")
_CLAIMS_FETCH_K = 50


def _one_line(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]


def _render_hit(hit: Hit) -> str:
    confidence = hit.confidence.value if hit.confidence is not None else "-"
    return f"[{hit.record_type.value}] ({confidence}) {_one_line(hit.text)} -> {hit.rel_path}"


def _generation_error(exc: ChangeControlApplicationError) -> None:
    typer.echo(f"error [{exc.code.value}]: {exc}", err=True)
    raise typer.Exit(code=2 if exc.code.value == "usage-error" else 1)


def _verify_embedder_identity(
    metadata: QueryGenerationMetadataV1,
    *,
    model_version: str,
    dimensions: int,
) -> None:
    if metadata.embedding_model is None:
        return
    if (
        metadata.embedding_model != model_version
        or metadata.embedding_dimensions != dimensions
    ):
        raise ChangeControlApplicationIntegrityError(
            "configured embedding provider differs from the resolved generation"
        )


def _render_generation(metadata: QueryGenerationMetadataV1) -> None:
    typer.echo(f"knowledge generation: {metadata.human_label}")


@query_app.command()
def search(
    query: str = typer.Argument(..., help="Free-text query."),
    domain: str | None = typer.Option(None, "--domain", help="Restrict to one domain."),
    k: int | None = typer.Option(None, "--k", help="Number of hits (default: retrieval.k)."),
    record_type: str = typer.Option(
        "all", "--type", help="Filter hits: claim | chunk | wiki | structural | all."
    ),
    rerank: bool = typer.Option(False, "--rerank", help="Rerank the top pool."),
    generation: str = typer.Option(
        "auto",
        "--generation",
        help="Serve auto, legacy, active, or one exact mgeneration:<sha256>.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON with channel provenance."),
) -> None:
    """Hybrid search across claims, chunks, wiki, and PDF structural records."""
    if record_type not in _RECORD_TYPES:
        typer.echo(f"error: --type must be one of {', '.join(_RECORD_TYPES)}", err=True)
        raise typer.Exit(code=2)
    settings = load_settings()
    try:
        with ChangeControlApplication(settings).resolve_query_generation(
            generation
        ) as resolved:
            embedder = get_embedding_provider(settings)
            _verify_embedder_identity(
                resolved.metadata,
                model_version=embedder.model_version,
                dimensions=embedder.dimensions,
            )
            reranker = get_reranker(settings) if rerank else None
            result = hybrid_search(
                query,
                settings,
                resolved.backend,
                embedder,
                reranker,
                k=k,
                domain=domain,
                record_types=None if record_type == "all" else [record_type],
                rerank=rerank,
                evidence_workspaces=resolved.evidence_workspaces or None,
            )
            generation_metadata = resolved.metadata
    except ChangeControlApplicationError as exc:
        _generation_error(exc)

    if json_out:
        payload = {
            "query": query,
            "generation": generation_metadata.model_dump(mode="json"),
            "wiki_card": (
                result.wiki_card.model_dump(mode="json") if result.wiki_card else None
            ),
            "hits": [hit.model_dump(mode="json") for hit in result.hits],
            "channel_counts": result.channel_counts,
            "timings": result.timings,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    _render_generation(generation_metadata)
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
    generation: str = typer.Option(
        "auto",
        "--generation",
        help="Serve auto, legacy, active, or one exact mgeneration:<sha256>.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Lexical search over the claims layer."""
    if confidence is not None and confidence not in _CONFIDENCES:
        typer.echo(f"error: --confidence must be one of {', '.join(_CONFIDENCES)}", err=True)
        raise typer.Exit(code=2)
    settings = load_settings()
    try:
        with ChangeControlApplication(settings).resolve_query_generation(
            generation
        ) as resolved:
            claim_ids = resolved.backend.lexical_claims(query, _CLAIMS_FETCH_K, domain)
            rows = resolved.backend.get_claims(claim_ids)
            generation_metadata = resolved.metadata
    except ChangeControlApplicationError as exc:
        _generation_error(exc)
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
    _render_generation(generation_metadata)
    if not rows:
        typer.echo("no claims matched")
        return
    for r in rows:
        typer.echo(f"[{r.claim_id}] ({r.confidence}) {_one_line(r.statement)} -> {r.rel_path}")


@query_app.command()
def wiki(
    action: str | None = typer.Argument(None, help="Omit to list; 'show' to display one entry."),
    slug: str | None = typer.Argument(None, help="Wiki slug for 'show'."),
    generation: str = typer.Option(
        "auto",
        "--generation",
        help="Serve auto, legacy, active, or one exact mgeneration:<sha256>.",
    ),
) -> None:
    """List wiki entries per domain, or `wiki show <slug>` for one entry."""
    if action is not None and (action != "show" or slug is None):
        typer.echo("usage: wiki           (list per domain)", err=True)
        typer.echo("       wiki show <slug>", err=True)
        raise typer.Exit(code=2)
    settings = load_settings()
    wiki_error: str | None = None
    try:
        with ChangeControlApplication(settings).resolve_query_generation(
            generation
        ) as resolved:
            backend = resolved.backend
            generation_metadata = resolved.metadata
            alias_index = backend.alias_index()
            pairs = sorted({(domain, wiki_slug) for wiki_slug, domain in alias_index.values()})

            if action is None:
                docs = backend.get_documents([f"wiki:{d}:{s}" for d, s in pairs])
                titles = {doc.doc_id: doc.title for doc in docs}
            else:
                docs = []
                titles = {}

            match = next(((d, s) for d, s in pairs if s == slug), None)
            if action is not None:
                if match is None:
                    wiki_error = f"no wiki entry with slug {slug!r}"
                else:
                    domain, wiki_slug = match
                    docs = backend.get_documents([f"wiki:{domain}:{wiki_slug}"])
                    if not docs:
                        wiki_error = f"wiki entry {slug!r} has aliases but no document row"
    except ChangeControlApplicationError as exc:
        _generation_error(exc)

    if wiki_error is not None:
        typer.echo(wiki_error, err=True)
        raise typer.Exit(code=1)
    _render_generation(generation_metadata)
    if action is None:
        if not pairs:
            typer.echo("no wiki entries indexed")
            return
        current_domain = None
        for domain, wiki_slug in pairs:
            if domain != current_domain:
                typer.echo(f"{domain}:")
                current_domain = domain
            title = titles.get(f"wiki:{domain}:{wiki_slug}", "")
            typer.echo(f"  {wiki_slug} — {title}")
        return

    assert action == "show" and slug is not None
    assert match is not None and docs
    doc = docs[0]
    aliases = sorted(a for a, (s, _d) in alias_index.items() if s == match[1])
    typer.echo(f"# {doc.title}")
    typer.echo(f"domain: {doc.domain}")
    typer.echo(f"path: {doc.rel_path}")
    typer.echo(f"aliases: {', '.join(aliases)}")
    typer.echo("")
    typer.echo(doc.body.strip())
