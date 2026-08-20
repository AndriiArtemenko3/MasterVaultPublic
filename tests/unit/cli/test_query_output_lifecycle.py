"""CLI output stays behind the generation-resolution verification boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import mastervault.cli.ask as ask_cli
import mastervault.cli.query as query_cli
from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.application_errors import (
    ChangeControlApplicationIntegrityError,
)
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
)
from mastervault.cli.app import app


class _QueryScope:
    def __init__(
        self,
        backend: Any,
        metadata: QueryGenerationMetadataV1,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.resolved = SimpleNamespace(
            backend=backend,
            metadata=metadata,
            evidence_workspaces={},
        )
        self.close_error = close_error
        self.active = False
        self.closed = False
        self.body_exception: BaseException | None = None
        self.events: list[tuple[str, bool, str]] = []

    def __enter__(self) -> Any:
        self.active = True
        return self.resolved

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        self.body_exception = exc
        self.active = False
        self.closed = True
        self.events.append(("close", self.active, ""))
        if self.close_error is not None:
            raise self.close_error


def _unmanaged_metadata() -> QueryGenerationMetadataV1:
    return QueryGenerationMetadataV1(
        selection=QueryGenerationSelectionV1(selector=QueryGenerationSelector.AUTO),
        backend="sqlite",
        generation_kind=QueryGenerationKind.UNMANAGED,
        is_active=False,
    )


def _managed_metadata() -> QueryGenerationMetadataV1:
    generation_id = f"mgeneration:{'a' * 64}"
    return QueryGenerationMetadataV1(
        selection=QueryGenerationSelectionV1(selector=QueryGenerationSelector.AUTO),
        backend="sqlite",
        generation_kind=QueryGenerationKind.MANAGED,
        generation_id=generation_id,
        generation_number=1,
        active_generation_id=generation_id,
        active_authority_revision=1,
        is_active=True,
        manifest_sha256="b" * 64,
        index_logical_fingerprint="c" * 64,
        index_file_sha256="d" * 64,
        index_file_byte_count=1,
        storage_schema_version=1,
        embedding_model="sealed-embedding",
        embedding_dimensions=384,
    )


def _install_resolution(
    monkeypatch: pytest.MonkeyPatch,
    scope: _QueryScope,
) -> None:
    settings = SimpleNamespace(llm=SimpleNamespace(provider="mock"))
    monkeypatch.setattr(query_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(ask_cli, "load_settings", lambda: settings)

    def resolve_query_generation(
        _application: ChangeControlApplication,
        selection: str = "auto",
    ) -> _QueryScope:
        assert selection == "auto"
        return scope

    monkeypatch.setattr(
        ChangeControlApplication,
        "resolve_query_generation",
        resolve_query_generation,
    )


def _record_echoes(monkeypatch: pytest.MonkeyPatch, scope: _QueryScope) -> None:
    original_echo = typer.echo

    def echo(message: object | None = None, **kwargs: Any) -> None:
        scope.events.append(("echo", scope.active, str(message)))
        original_echo(message, **kwargs)

    monkeypatch.setattr(typer, "echo", echo)


def test_unmanaged_claims_json_keeps_the_v02_list_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        claim_id="claim-01",
        statement="Needle statement",
        confidence="high",
        affects=["needle-wiki"],
        doc_id="source:needle",
        rel_path="operations/sources/needle.md",
        domain="operations",
    )
    backend = SimpleNamespace(
        lexical_claims=lambda _query, _k, _domain: [row.claim_id],
        get_claims=lambda _claim_ids: [row],
    )
    scope = _QueryScope(backend, _unmanaged_metadata())
    _install_resolution(monkeypatch, scope)

    result = CliRunner().invoke(app, ["claims", "needle", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "claim_id": "claim-01",
            "statement": "Needle statement",
            "confidence": "high",
            "affects": ["needle-wiki"],
            "doc_id": "source:needle",
            "rel_path": "operations/sources/needle.md",
            "domain": "operations",
        }
    ]
    assert scope.closed


@pytest.mark.parametrize(
    "case",
    [
        "search-success",
        "claims-not-found",
        "wiki-not-found",
        "ask-usage",
        "embedding-mismatch",
    ],
)
def test_close_time_integrity_failure_is_the_only_cli_output(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SimpleNamespace()
    metadata = _managed_metadata() if case == "embedding-mismatch" else _unmanaged_metadata()
    close_error = ChangeControlApplicationIntegrityError(
        "active generation failed final integrity verification"
    )
    scope = _QueryScope(backend, metadata, close_error=close_error)
    _install_resolution(monkeypatch, scope)
    _record_echoes(monkeypatch, scope)

    embedder = SimpleNamespace(model_version="mock-embedding-v1", dimensions=384)
    monkeypatch.setattr(query_cli, "get_embedding_provider", lambda _settings: embedder)
    monkeypatch.setattr(ask_cli, "get_embedding_provider", lambda _settings: embedder)
    monkeypatch.setattr(ask_cli, "get_llm", lambda _settings: object())

    if case == "search-success":
        monkeypatch.setattr(
            query_cli,
            "hybrid_search",
            lambda *args, **kwargs: SimpleNamespace(
                wiki_card=None,
                hits=[],
                channel_counts={},
                timings={},
            ),
        )
        arguments = ["search", "needle", "--json"]
    elif case == "claims-not-found":
        backend.lexical_claims = lambda _query, _k, _domain: []
        backend.get_claims = lambda _claim_ids: []
        arguments = ["claims", "needle"]
    elif case == "wiki-not-found":
        backend.alias_index = lambda: {}
        arguments = ["wiki", "show", "missing"]
    elif case == "ask-usage":
        monkeypatch.setattr(
            ask_cli,
            "run_ask",
            lambda *args, **kwargs: SimpleNamespace(
                exit_code=2,
                run_id="query-run",
                run_dir=Path(),
                answer_markdown="usage result must stay hidden",
                confidence=None,
                gaps=[],
                sources=[],
                trace="usage trace must stay hidden",
                extractive=True,
                zero_evidence=True,
                rounds=0,
                cost_usd=0.0,
                warnings=[],
                evidence=[],
                nearest_wiki_titles=[],
            ),
        )
        arguments = ["ask", "needle"]
    else:
        monkeypatch.setattr(
            query_cli,
            "hybrid_search",
            lambda *args, **kwargs: pytest.fail("mismatched embeddings must stop retrieval"),
        )
        arguments = ["search", "needle"]

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 1
    assert result.output == (
        "error [integrity-failure]: active generation failed final integrity verification\n"
    )
    assert scope.closed
    assert all(not active for event, active, _message in scope.events if event == "echo")
    if case == "embedding-mismatch":
        assert isinstance(scope.body_exception, ChangeControlApplicationIntegrityError)
        assert "configured embedding provider differs" in str(scope.body_exception)
    else:
        assert scope.body_exception is None


def test_wiki_not_found_is_rendered_only_after_the_query_scope_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SimpleNamespace(alias_index=lambda: {})
    scope = _QueryScope(backend, _unmanaged_metadata())
    _install_resolution(monkeypatch, scope)
    _record_echoes(monkeypatch, scope)

    result = CliRunner().invoke(app, ["wiki", "show", "missing"])

    assert result.exit_code == 1
    assert result.output == "no wiki entry with slug 'missing'\n"
    assert scope.closed
    assert scope.events == [
        ("close", False, ""),
        ("echo", False, "no wiki entry with slug 'missing'"),
    ]
