"""PR19 read-only reconstruction of sealed-seed query authority."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_temporal_proposal import REPO_ROOT, _build_case, _build_temporal_evidence, _commit

import mastervault.change_control.managed_query_resolver as managed_query_resolver_module
from mastervault.change_control.managed_query_resolver import (
    ManagedQueryResolverRestartError,
    SealedSeedQueryBootstrap,
    build_read_only_managed_query_resolver,
    reopen_sealed_seed_query_bootstrap,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedRevisionDecisionRecord,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    SqliteManagedChangeControlStore,
)


def _tree_signature(root: Path) -> dict[str, tuple[int, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            stat.S_IFMT(path.lstat().st_mode),
            path.lstat().st_size,
            path.lstat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


class _QueryOnlyConnection:
    def execute(self, statement: str) -> _QueryOnlyConnection:
        assert statement == "PRAGMA query_only"
        return self

    @staticmethod
    def fetchone() -> tuple[int]:
        return (1,)


def _early_restart_inputs() -> tuple[
    SqliteManagedChangeControlStore,
    ManagedRevisionDecisionRecord,
    SealedSeedQueryBootstrap,
]:
    digest = "a" * 64
    bootstrap_binding = SimpleNamespace(binding_id="bootstrap-lineage")
    head = AggregateHeadBinding.create(
        aggregate_id="orthogonal-context",
        revision=1,
        aggregate_sha256="b" * 64,
    )
    run_binding = ManagedRunBindingV2.model_construct(
        analysis_set=SimpleNamespace(analysis_bootstrap=bootstrap_binding),
        prechange_head=head,
        operation_id=f"temporal-commit:{digest}",
    )
    decision = ManagedRevisionDecisionRecord.model_construct(
        record_id="mdecisionrecord:" + "c" * 64,
        command=SimpleNamespace(bundle=SimpleNamespace(run_binding=run_binding)),
        decided_at="2026-01-01T00:00:00Z",
        record_sha256="c" * 64,
    )
    bootstrap = object.__new__(SealedSeedQueryBootstrap)
    object.__setattr__(
        bootstrap,
        "temporal_analysis",
        SimpleNamespace(manifest_id=f"temporal-analysis:{digest}", manifest_sha256=digest),
    )
    object.__setattr__(
        bootstrap,
        "verified_bootstrap",
        SimpleNamespace(binding=bootstrap_binding),
    )
    object.__setattr__(bootstrap, "prechange_head", head)
    object.__setattr__(bootstrap, "evidence_repository", None)
    object.__setattr__(bootstrap, "source_note_resolver", None)
    store = object.__new__(SqliteManagedChangeControlStore)
    store._read_only = True  # noqa: SLF001
    store.conn = cast(Any, _QueryOnlyConnection())
    return store, decision, bootstrap


def test_sealed_seed_query_bootstrap_reopens_exactly_without_repository_writes(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path / "temporal")
    try:
        temporal_analysis = _build_temporal_evidence(case)
        commit = _commit(case)
        evidence_root = case.evidence_repository.root
        before = _tree_signature(evidence_root)

        reopened = reopen_sealed_seed_query_bootstrap(
            seed_repository_root=REPO_ROOT,
            evidence_repository_root=evidence_root,
            temporal_analysis_manifest_sha256=(
                commit.temporal_analysis_manifest_sha256
            ),
        )

        assert reopened.evidence_repository.read_only
        assert reopened.temporal_analysis == temporal_analysis
        assert reopened.verified_bootstrap.binding == (
            temporal_analysis.proposal.binding.analysis_bootstrap
        )
        durable_binding = case.proposal.binding.analysis_bootstrap
        assert reopened.prechange_head.aggregate_id == durable_binding.aggregate_id
        assert reopened.prechange_head.revision == durable_binding.prechange_revision
        assert (
            reopened.prechange_head.aggregate_sha256
            == durable_binding.prechange_aggregate_sha256
        )
        assert reopened.authority_context.verified_bootstrap is reopened.verified_bootstrap
        assert _tree_signature(evidence_root) == before

        with pytest.raises(ManagedQueryResolverRestartError, match="cannot be reopened"):
            reopen_sealed_seed_query_bootstrap(
                seed_repository_root=REPO_ROOT,
                evidence_repository_root=evidence_root,
                temporal_analysis_manifest_sha256="0" * 64,
            )
        assert _tree_signature(evidence_root) == before
    finally:
        case.store.close()


def test_sealed_seed_query_bootstrap_rejects_invalid_locator_before_filesystem_access(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "must-not-be-created"

    with pytest.raises(ManagedQueryResolverRestartError, match="exact SHA-256"):
        reopen_sealed_seed_query_bootstrap(
            seed_repository_root=REPO_ROOT,
            evidence_repository_root=missing,
            temporal_analysis_manifest_sha256="not-a-sha",
        )

    assert not missing.exists()


def test_resolver_uses_supplied_authority_context_for_both_store_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, decision, bootstrap = _early_restart_inputs()
    supplied = AuthorityVerificationContext.legacy(
        verified_bootstrap=cast(Any, object()),
        prechange_head=AggregateHeadBinding.create(
            aggregate_id="workspace-authority",
            revision=7,
            aggregate_sha256="d" * 64,
        ),
    )
    observed: list[AuthorityVerificationContext | None] = []

    def active_read(
        _store: SqliteManagedChangeControlStore,
        _aggregate_id: str,
        **kwargs: Any,
    ) -> ManagedRevisionDecisionRecord:
        observed.append(kwargs.get("authority_context"))
        return decision

    def temporal_read(
        _store: SqliteManagedChangeControlStore,
        _aggregate_id: str,
        **kwargs: Any,
    ) -> str:
        observed.append(kwargs.get("authority_context"))
        raise RuntimeError("stop after authority-context reads")

    monkeypatch.setattr(
        managed_query_resolver_module,
        "_exact_active_decision",
        lambda value: value,
    )
    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_managed_decision_record",
        active_read,
    )
    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_managed_temporal_request_id",
        temporal_read,
    )

    with pytest.raises(RuntimeError, match="stop after authority-context reads"):
        build_read_only_managed_query_resolver(
            store=store,
            active_decision=decision,
            bootstrap=bootstrap,
            canonical_repository_root=tmp_path,
            authority_context=supplied,
        )

    assert observed == [supplied, supplied]
    assert supplied != bootstrap.authority_context


def test_resolver_defaults_to_bootstrap_context_and_rejects_decision_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, decision, bootstrap = _early_restart_inputs()
    mismatched = decision.model_copy(update={"record_id": "mdecisionrecord:" + "e" * 64})
    observed: list[AuthorityVerificationContext | None] = []
    temporal_called = False

    def active_read(
        _store: SqliteManagedChangeControlStore,
        _aggregate_id: str,
        **kwargs: Any,
    ) -> ManagedRevisionDecisionRecord:
        observed.append(kwargs.get("authority_context"))
        return mismatched

    def temporal_read(
        _store: SqliteManagedChangeControlStore,
        _aggregate_id: str,
        **_kwargs: Any,
    ) -> str:
        nonlocal temporal_called
        temporal_called = True
        return "must-not-be-read"

    monkeypatch.setattr(
        managed_query_resolver_module,
        "_exact_active_decision",
        lambda value: value,
    )
    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_managed_decision_record",
        active_read,
    )
    monkeypatch.setattr(
        SqliteManagedChangeControlStore,
        "get_active_managed_temporal_request_id",
        temporal_read,
    )

    with pytest.raises(ManagedQueryResolverRestartError, match="not the exact active"):
        build_read_only_managed_query_resolver(
            store=store,
            active_decision=decision,
            bootstrap=bootstrap,
            canonical_repository_root=tmp_path,
        )

    assert observed == [bootstrap.authority_context]
    assert temporal_called is False
