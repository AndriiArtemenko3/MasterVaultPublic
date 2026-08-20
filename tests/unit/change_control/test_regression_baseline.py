from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import mastervault.change_control.regression_baseline as baseline_module
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
    ResolvedQueryGeneration,
)
from mastervault.change_control.regression_baseline import (
    GenerationZeroBaselineRepository,
    RegressionAuthorityBindingV1,
    RegressionBaselineError,
    VerifiedGenerationZeroBaselineCapability,
    execute_generation_zero_baseline,
)
from mastervault.change_control.regression_suite import parse_regression_suite_bytes
from mastervault.config import Settings
from mastervault.models import ChannelRank, Confidence, Domain, Hit, RecordType
from mastervault.pipelines.ask import AskOutcome
from mastervault.providers import MockEmbedding, MockLLM
from mastervault.retrieval.search import SearchResult


def _suite() -> Any:
    payload = {
        "schema_version": 1,
        "suite_id": "operator-suite",
        "suite_version": 3,
        "cases": [
            {
                "case_id": "control-ask",
                "role": "control",
                "kind": "ask",
                "query": "What ships unchanged?",
                "domain": "support",
                "max_rounds": 2,
                "budget_usd_micros": 12345,
            },
            {
                "case_id": "target-search",
                "role": "targeted",
                "kind": "search",
                "query": "What changed?",
                "domain": "support",
                "k": 4,
                "record_types": ["claim", "wiki"],
                "rerank": False,
            },
        ],
    }
    return parse_regression_suite_bytes(json.dumps(payload).encode())


def _metadata() -> QueryGenerationMetadataV1:
    generation_id = f"mgeneration:{'1' * 64}"
    return QueryGenerationMetadataV1(
        selection=QueryGenerationSelectionV1(selector=QueryGenerationSelector.LEGACY),
        backend="sqlite",
        generation_kind=QueryGenerationKind.GENERATION_ZERO,
        generation_id=generation_id,
        generation_number=0,
        active_generation_id=generation_id,
        active_authority_revision=0,
        is_active=True,
        manifest_sha256="2" * 64,
        index_logical_fingerprint="3" * 64,
        index_file_sha256="4" * 64,
        index_file_byte_count=4096,
        storage_schema_version=3,
        embedding_model="mock-hashing-trick-v1",
        embedding_dimensions=384,
    )


def _authority(metadata: QueryGenerationMetadataV1 | None = None) -> RegressionAuthorityBindingV1:
    return RegressionAuthorityBindingV1(
        run_id="operatorrun:test-baseline",
        source_admission_id="incoming:test-source",
        source_admission_sha256="5" * 64,
        workspace_inventory_id="inventory:test-workspace",
        workspace_inventory_sha256="6" * 64,
        legacy_readiness_id="legacyindex:test-ready",
        legacy_readiness_sha256="7" * 64,
        query_generation=metadata or _metadata(),
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small-v1",
                "model_medium": "mock-medium-v1",
                "model_large": "mock-large-v1",
            },
            "paths": {"workspace": tmp_path / "private-runtime-root"},
        }
    )


def _prepared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata: QueryGenerationMetadataV1 | None = None,
) -> tuple[Any, list[str], ResolvedQueryGeneration]:
    actual_metadata = metadata or _metadata()
    hit = Hit(
        record_id="claim:returns-01",
        record_type=RecordType.CLAIM,
        doc_id="source:returns",
        domain=Domain.CUSTOMER_SUPPORT,
        text="Returns remain available for thirty days.",
        rel_path="support/sources/returns.md",
        confidence=Confidence.HIGH,
        channels=ChannelRank(lexical_claims=1, vector=2),
        rrf_score=0.031,
    )

    def fake_search(*args: Any, **kwargs: Any) -> SearchResult:
        assert kwargs["rerank"] is False
        return SearchResult(
            wiki_card=hit.model_copy(update={"record_id": "wiki:support:returns"}),
            hits=[hit],
            timings={"vector": 999.0},
            channel_counts={"vector": 1, "lexical_claims": 1},
        )

    def fake_ask(*args: Any, **kwargs: Any) -> AskOutcome:
        assert kwargs["persist_run"] is False
        assert kwargs["budget_usd"] == 0.012345
        return AskOutcome(
            exit_code=0,
            run_id="random-process-id-must-not-persist",
            run_dir=tmp_path / "private-runtime-root" / "runs" / "random",
            answer_markdown="The policy is grounded. [claim:returns-01]",
            confidence="high",
            gaps=[],
            sources=[
                {
                    "record_id": hit.record_id,
                    "rel_path": hit.rel_path,
                    "evidence": [],
                }
            ],
            trace="secret timing trace",
            extractive=False,
            zero_evidence=False,
            rounds=2,
            cost_usd=0.012345,
            warnings=[],
            evidence=[
                {
                    "record_id": hit.record_id,
                    "rel_path": hit.rel_path,
                    "evidence": [],
                    "source_identity": {"asset_sha256": "8" * 64},
                }
            ],
            nearest_wiki_titles=["Returns"],
        )

    monkeypatch.setattr(baseline_module, "hybrid_search", fake_search)
    monkeypatch.setattr(baseline_module, "run_ask", fake_ask)
    verified: list[str] = []
    backend = object()
    resolved = ResolvedQueryGeneration(
        backend=backend,  # type: ignore[arg-type]
        metadata=actual_metadata,
        _verify_callbacks=(lambda: verified.append("verified"),),
        _close_backend=lambda: pytest.fail("executor must not close caller-owned backend"),
    )
    prepared = execute_generation_zero_baseline(
        resolved=resolved,
        authority=_authority(actual_metadata),
        suite=_suite(),
        settings=_settings(tmp_path),
        embedder=MockEmbedding(),
        llm=MockLLM(_settings(tmp_path)),
    )
    return prepared, verified, resolved


def test_executor_buffers_complete_path_safe_pipeline_evidence_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, verified, resolved = _prepared(tmp_path, monkeypatch)
    assert verified == ["verified", "verified"]
    assert not resolved._closed  # noqa: SLF001
    assert [item.case_id for item in prepared.cases] == ["control-ask", "target-search"]

    ask = prepared.cases[0].payload
    assert ask["cost_usd_micros"] == 12345
    assert ask["sources"][0]["rel_path"] == "support/sources/returns.md"
    search = prepared.cases[1].payload
    assert search["hits"][0]["rel_path"] == "support/sources/returns.md"
    encoded = b"".join(
        baseline_module.canonical_json_bytes(item.payload) for item in prepared.cases
    )
    assert b"random-process-id" not in encoded
    assert b"secret timing trace" not in encoded
    assert b"timings" not in encoded
    assert str(tmp_path).encode() not in encoded


def test_executor_rejects_generation_runtime_and_reranker_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _verified, resolved = _prepared(tmp_path, monkeypatch)
    with pytest.raises(RegressionBaselineError, match="differs"):
        execute_generation_zero_baseline(
            resolved=resolved,
            authority=prepared.authority.model_copy(
                update={
                    "query_generation": prepared.authority.query_generation.model_copy(
                        update={"manifest_sha256": "9" * 64}
                    )
                }
            ),
            suite=_suite(),
            settings=_settings(tmp_path),
            embedder=MockEmbedding(),
            llm=MockLLM(),
        )
    with pytest.raises(RegressionBaselineError, match="reranking"):
        execute_generation_zero_baseline(
            resolved=resolved,
            authority=prepared.authority,
            suite=_suite(),
            settings=_settings(tmp_path),
            embedder=MockEmbedding(),
            llm=MockLLM(),
            reranker=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "unsafe_reference",
    [
        {"source_identity": {"nested": {"api_key": "must-not-persist"}}},
        {"evidence": [{"locator": "/private/vault/source.md"}]},
        {"rel_path": "../outside.md"},
    ],
)
def test_ask_reference_recursively_rejects_secrets_and_unsafe_paths(
    unsafe_reference: dict[str, Any],
) -> None:
    with pytest.raises(RegressionBaselineError):
        baseline_module._ask_reference(unsafe_reference)  # noqa: SLF001


def test_repository_exact_replay_lost_ack_and_different_input_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _verified, _resolved = _prepared(tmp_path, monkeypatch)
    root = tmp_path / "baseline-repository"
    repository = GenerationZeroBaselineRepository(root)

    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        repository.publish(
            prepared,
            captured_at="2026-08-20T12:00:00+00:00",
            failure_hook=lambda stage: (
                (_ for _ in ()).throw(RuntimeError("lost acknowledgement"))
                if stage == "complete-published"
                else None
            ),
        )
    replay = repository.publish(prepared, captured_at="2026-08-21T12:00:00+00:00")
    assert replay.receipt.captured_at == "2026-08-20T12:00:00+00:00"
    assert repository.verify_capability(replay) == replay.receipt

    changed_case = prepared.cases[0].model_copy(
        update={
            "payload": {**prepared.cases[0].payload, "answer_markdown": "different"},
            "payload_sha256": "0" * 64,
        }
    )
    changed = prepared.model_copy(update={"cases": (changed_case, *prepared.cases[1:])})
    with pytest.raises((RegressionBaselineError, ValueError)):
        repository.publish(changed, captured_at="2026-08-20T12:00:00+00:00")


@pytest.mark.parametrize("substitution", ["byte", "hardlink", "symlink", "fifo", "cases-dir"])
def test_repository_tamper_and_path_substitution_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, substitution: str
) -> None:
    prepared, _verified, _resolved = _prepared(tmp_path, monkeypatch)
    repository = GenerationZeroBaselineRepository(tmp_path / "repo")
    capability = repository.publish(prepared, captured_at="2026-08-20T12:00:00+00:00")
    artifact = repository.root / capability.receipt.artifacts[0].relative_path

    if substitution == "byte":
        content = bytearray(artifact.read_bytes())
        content[-2] ^= 1
        artifact.write_bytes(content)
    elif substitution == "hardlink":
        os.link(artifact, tmp_path / "alias.json")
    elif substitution == "symlink":
        original = tmp_path / "original.json"
        artifact.rename(original)
        artifact.symlink_to(original)
    elif substitution == "fifo":
        artifact.unlink()
        os.mkfifo(artifact, 0o600)
    else:
        cases = artifact.parent
        moved = tmp_path / "real-cases"
        cases.rename(moved)
        cases.symlink_to(moved, target_is_directory=True)

    with pytest.raises(RegressionBaselineError):
        repository.open(prepared.authority.run_id)


def test_repository_partial_publication_is_not_openable_and_replays_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _verified, _resolved = _prepared(tmp_path, monkeypatch)
    repository = GenerationZeroBaselineRepository(tmp_path / "repo")
    with pytest.raises(RuntimeError, match="crash"):
        repository.publish(
            prepared,
            captured_at="2026-08-20T12:00:00+00:00",
            failure_hook=lambda stage: (
                (_ for _ in ()).throw(RuntimeError("crash"))
                if stage == "case-published:control-ask"
                else None
            ),
        )
    with pytest.raises(RegressionBaselineError):
        repository.open(prepared.authority.run_id)
    completed = repository.publish(prepared, captured_at="2026-08-20T12:00:00+00:00")
    assert completed.receipt.query_inventory == ("control-ask", "target-search")


@pytest.mark.parametrize("mutation", ["surplus", "missing"])
def test_repository_rejects_surplus_and_missing_case_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    prepared, _verified, _resolved = _prepared(tmp_path, monkeypatch)
    repository = GenerationZeroBaselineRepository(tmp_path / "repo")
    capability = repository.publish(prepared, captured_at="2026-08-20T12:00:00+00:00")
    cases = (repository.root / capability.receipt.artifacts[0].relative_path).parent
    if mutation == "surplus":
        extra = cases / "surplus.json"
        extra.write_bytes(b"{}")
        extra.chmod(0o600)
    else:
        (repository.root / capability.receipt.artifacts[0].relative_path).unlink()

    with pytest.raises(RegressionBaselineError):
        repository.open(prepared.authority.run_id)


def test_repository_rejects_forged_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _verified, _resolved = _prepared(tmp_path, monkeypatch)
    repository = GenerationZeroBaselineRepository(tmp_path / "repo")
    capability = repository.publish(prepared, captured_at="2026-08-20T12:00:00+00:00")
    forged = VerifiedGenerationZeroBaselineCapability(
        receipt=capability.receipt,
        repository_id=capability.repository_id,
        _seal=b"forged",
    )

    with pytest.raises(RegressionBaselineError, match="capability"):
        repository.verify_capability(forged)


def test_production_modules_do_not_import_evaluator_or_golden_schemas() -> None:
    for name in ("regression_suite.py", "regression_baseline.py"):
        source = (Path(baseline_module.__file__).parent / name).read_text(encoding="utf-8")
        assert "mastervault.evals" not in source
        assert "datasets/" not in source
        assert "golden/" not in source
