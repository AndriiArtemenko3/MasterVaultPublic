"""PR19 read-only reconstruction of sealed-seed query authority."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_generic_analysis_v2 import _generic_evidence, _workspace_capability
from test_temporal_proposal import REPO_ROOT, _build_case, _build_temporal_evidence, _commit

import mastervault.change_control.managed_query_resolver as managed_query_resolver_module
from mastervault.change_control.generic_analysis import start_generic_analysis_v2
from mastervault.change_control.managed_query_resolver import (
    ManagedQueryResolverRestartError,
    SealedSeedQueryBootstrap,
    WorkspaceQueryBootstrapV2,
    build_read_only_managed_query_resolver,
    reopen_sealed_seed_query_bootstrap,
    reopen_workspace_query_bootstrap_v2,
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
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence


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
            temporal_analysis_manifest_sha256=(commit.temporal_analysis_manifest_sha256),
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
            reopened.prechange_head.aggregate_sha256 == durable_binding.prechange_aggregate_sha256
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


def _generic_restart_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    wrong_binding_type: bool = False,
) -> tuple[AuthorityVerificationContext, tuple[Any, ...], Path, str, Path]:
    workspace_capability, prechange, workspace_notes = _workspace_capability()
    aggregate_store = SqliteChangeControlStore(tmp_path / "authority" / "change-control.sqlite3")
    aggregate_store.init_schema()
    aggregate_store.create(prechange, operation_id="bootstrap-aggregate")
    repository, evidence_capability, _admission = _generic_evidence(tmp_path)
    started = start_generic_analysis_v2(
        store=aggregate_store,
        repository=repository,
        workspace_capability=workspace_capability,
        evidence_capability=evidence_capability,  # type: ignore[arg-type]
        workspace_source_notes=workspace_notes,
        analysis_operation_id="generic-query-restart",
    )
    aggregate_store.close()
    manifest_bytes = b"generic-query-temporal-placeholder"
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = (
        repository.root / "temporal" / "evidence" / "analyses" / f"{manifest_sha256}.json"
    )
    manifest_path.parent.mkdir(parents=True, mode=0o700)
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o600)
    temporal = TemporalAnalysisEvidence.model_construct(
        manifest_id=f"temporal-analysis:{manifest_sha256}",
        manifest_sha256=manifest_sha256,
        analysis_aggregate=started.snapshot.aggregate,
        analysis_head=AggregateHeadBinding.create(
            aggregate_id=started.snapshot.aggregate.aggregate_id,
            revision=started.snapshot.revision,
            aggregate_sha256=started.snapshot.aggregate_sha256,
        ),
        proposal=SimpleNamespace(
            binding=SimpleNamespace(
                analysis_bootstrap=(object() if wrong_binding_type else started.binding)
            )
        ),
    )
    monkeypatch.setattr(
        TemporalAnalysisEvidence,
        "from_canonical_bytes",
        classmethod(lambda _cls, _content: temporal),
    )
    return (
        AuthorityVerificationContext.workspace(workspace_capability),
        workspace_notes,
        repository.root,
        manifest_sha256,
        repository.root
        / repository.resolve_verified_evidence(evidence_capability).source.source_note_locator,
    )


def test_workspace_query_bootstrap_reconstructs_complete_generic_inventory_and_pins_inodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context, notes, evidence_root, temporal_sha256, incoming_note_path = _generic_restart_inputs(
        monkeypatch, tmp_path
    )
    bootstrap = reopen_workspace_query_bootstrap_v2(
        authority_context=context,
        workspace_source_notes=cast(Any, notes),
        evidence_repository_root=evidence_root,
        generic_evidence_repository_root=evidence_root,
        temporal_analysis_manifest_sha256=temporal_sha256,
    )
    assert type(bootstrap) is WorkspaceQueryBootstrapV2
    bootstrap.verify()

    original = incoming_note_path.read_bytes()
    incoming_note_path.unlink()
    incoming_note_path.write_bytes(original)
    incoming_note_path.chmod(0o600)
    with pytest.raises(ManagedQueryResolverRestartError, match="inode was substituted"):
        bootstrap.verify()
    bootstrap.close()


@pytest.mark.parametrize("source_notes", [(), "surplus"])
def test_workspace_query_bootstrap_rejects_incomplete_or_surplus_source_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_notes: object,
) -> None:
    context, notes, evidence_root, temporal_sha256, _incoming_note_path = _generic_restart_inputs(
        monkeypatch, tmp_path
    )
    supplied = () if source_notes == () else (*notes, notes[0])
    with pytest.raises(ManagedQueryResolverRestartError, match="does not reproduce"):
        reopen_workspace_query_bootstrap_v2(
            authority_context=context,
            workspace_source_notes=cast(Any, supplied),
            evidence_repository_root=evidence_root,
            generic_evidence_repository_root=evidence_root,
            temporal_analysis_manifest_sha256=temporal_sha256,
        )


def test_workspace_query_bootstrap_rejects_wrong_analysis_kind_and_tampered_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context, notes, evidence_root, temporal_sha256, _incoming_note_path = _generic_restart_inputs(
        monkeypatch, tmp_path, wrong_binding_type=True
    )
    with pytest.raises(ManagedQueryResolverRestartError, match="generic v2 analysis binding"):
        reopen_workspace_query_bootstrap_v2(
            authority_context=context,
            workspace_source_notes=cast(Any, notes),
            evidence_repository_root=evidence_root,
            generic_evidence_repository_root=evidence_root,
            temporal_analysis_manifest_sha256=temporal_sha256,
        )

    context, notes, evidence_root, temporal_sha256, incoming_note_path = _generic_restart_inputs(
        monkeypatch, tmp_path / "tampered"
    )
    incoming_note_path.write_bytes(b"tampered generic source note")
    incoming_note_path.chmod(0o600)
    with pytest.raises(ManagedQueryResolverRestartError, match="does not reproduce"):
        reopen_workspace_query_bootstrap_v2(
            authority_context=context,
            workspace_source_notes=cast(Any, notes),
            evidence_repository_root=evidence_root,
            generic_evidence_repository_root=evidence_root,
            temporal_analysis_manifest_sha256=temporal_sha256,
        )
