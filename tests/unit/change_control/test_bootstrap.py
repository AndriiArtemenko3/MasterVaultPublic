from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import mastervault.change_control.bootstrap as bootstrap_module
import mastervault.change_control.claim_scopes as claim_scopes_module
from mastervault.change_control.analysis_binding import AnalysisBootstrapBinding
from mastervault.change_control.bootstrap import (
    ANALYSIS_AGGREGATE_ID,
    AnalysisBootstrapIntegrityError,
    AnalysisBootstrapStaleError,
    bootstrap_analysis_aggregate,
    build_verified_prechange_aggregate,
    create_verified_analysis_bootstrap_binding,
    create_verified_analysis_bootstrap_capability,
    incoming_claim_evidence_sha256,
    verify_generation_zero_authority,
)
from mastervault.change_control.claim_scopes import (
    CLAIM_SCOPE_POLICY_VERSION,
    claim_scopes_v1,
)
from mastervault.change_control.incoming import (
    ALIGNMENT_ATTESTATION_ID,
    ALIGNMENT_ATTESTATION_RELATIVE_PATH,
    ALIGNMENT_POLICY_VERSION,
    MANIFEST_RELATIVE_PATH,
    PINNED_ALIGNMENT_ATTESTATION_SHA256,
    IncomingIntegrityError,
    load_verified_incoming_event,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    DocumentAuthority,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    VersionedClaimRevision,
    aggregate_sha256,
)
from mastervault.change_control.seed import (
    load_verified_prechange_seed_manifest,
    verify_seed_document_context,
)
from mastervault.change_control.store import (
    ChangeControlConflictError,
    ChangeControlIdempotencyError,
    SqliteChangeControlStore,
)
from mastervault.vaultfs.frontmatter import parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / MANIFEST_RELATIVE_PATH
PRECHANGE_OPERATION_ID = "analysis-bootstrap:prechange-v1"
ANALYSIS_OPERATION_ID = "analysis-bootstrap:incoming-v2"


def _store(path: Path) -> SqliteChangeControlStore:
    store = SqliteChangeControlStore(path)
    store.init_schema()
    return store


def _bootstrap(store: SqliteChangeControlStore):
    return bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id=PRECHANGE_OPERATION_ID,
        analysis_operation_id=ANALYSIS_OPERATION_ID,
    )


def _repository_source_hashes() -> dict[str, str]:
    prechange = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    incoming = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    paths = {
        path
        for document in prechange.manifest.documents
        for path in (document.source_path, document.processed_path)
    }
    paths.update(
        {
            incoming.manifest.document.source_path,
            incoming.manifest.document.processed_path,
            PRECHANGE_MANIFEST.relative_to(REPO_ROOT).as_posix(),
            INCOMING_MANIFEST.relative_to(REPO_ROOT).as_posix(),
            ALIGNMENT_ATTESTATION_RELATIVE_PATH,
        }
    )
    return {
        relative: hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        for relative in sorted(paths)
    }


def test_claim_scopes_v1_is_strict_sorted_and_unique() -> None:
    assert CLAIM_SCOPE_POLICY_VERSION == "claim-scopes-v1"
    assert claim_scopes_v1(
        document_family="customer-support.returns-policy",
        affects=("return-policy", "customer-support.returns-policy", "return-policy"),
    ) == ("customer-support.returns-policy", "return-policy")
    with pytest.raises(ValueError, match="already be normalized"):
        claim_scopes_v1(document_family="Customer Support", affects=("return-policy",))
    with pytest.raises(TypeError, match="tuple of strings"):
        claim_scopes_v1(
            document_family="customer-support.returns-policy",
            affects=["return-policy"],  # type: ignore[arg-type]
        )


def test_bootstrap_builds_complete_exact_empty_graph_inventories(tmp_path: Path) -> None:
    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    prechange = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=seed,
    )
    expected_seed_claims: set[tuple[str, str]] = set()
    for item in seed.manifest.documents:
        context = verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=seed,
            document_id=item.document_id,
        )
        expected_seed_claims.update(
            (item.document_id, claim.id) for claim in context.source_note.key_claims
        )
    assert len(prechange.documents.documents) == 7
    assert len(prechange.claims.revisions) == 69
    assert {item.document_id for item in prechange.documents.documents} == {
        item.document_id for item in seed.manifest.documents
    }
    assert {
        (item.document.document_id, item.source.source_claim_id)
        for item in prechange.claims.revisions
    } == expected_seed_claims

    store = _store(tmp_path / "change_control" / "state.sqlite3")
    try:
        result = _bootstrap(store)
        reloaded = store.load(ANALYSIS_AGGREGATE_ID)
    finally:
        store.close()

    assert result.prechange_commit.revision == 1
    assert result.analysis_commit.revision == 2
    assert result.binding.prechange_aggregate_sha256 == aggregate_sha256(prechange)
    assert result.binding.alignment_attestation_id == ALIGNMENT_ATTESTATION_ID
    assert (
        result.binding.alignment_attestation_sha256
        == PINNED_ALIGNMENT_ATTESTATION_SHA256
        == result.incoming_event.alignment_attestation_sha256
    )
    assert result.binding.alignment_policy_version == ALIGNMENT_POLICY_VERSION
    assert result.binding.alignment_payload_sha256 == result.incoming_event.alignment_payload_sha256
    assert result.snapshot == reloaded
    assert result.snapshot.revision == 2
    assert result.snapshot.aggregate_sha256 == aggregate_sha256(result.snapshot.aggregate)
    assert len(result.snapshot.aggregate.documents.documents) == 8
    assert len(result.snapshot.aggregate.claims.revisions) == 79
    assert not result.snapshot.aggregate.relation_graph.assessments
    assert not result.snapshot.aggregate.dependencies.assessments
    assert not result.snapshot.aggregate.document_replacements.assessments
    assert not result.snapshot.aggregate.temporal_constraints.constraints

    incoming_claims = result.incoming_event.claim_revisions
    assert result.binding.changed_claim_revision_ids == tuple(
        sorted(item.claim_revision_id for item in incoming_claims)
    )
    for item in incoming_claims:
        assert result.snapshot.aggregate.claims.get(item.claim_revision_id) == item
    assert (
        result.snapshot.aggregate.documents.get(result.incoming_event.document.document_version_id)
        == result.incoming_event.document
    )


def test_iteration_order_is_aggregate_deterministic(tmp_path: Path) -> None:
    original = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    payload = yaml.safe_load(PRECHANGE_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["documents"] = list(reversed(payload["documents"]))
    reversed_path = tmp_path / "sl2_prechange_reversed.yaml"
    reversed_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    reversed_context = load_verified_prechange_seed_manifest(reversed_path)

    first = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=original,
    )
    second = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=reversed_context,
    )
    assert first == second
    assert aggregate_sha256(first) == aggregate_sha256(second)


def test_seed_and_incoming_claims_share_the_exact_scope_policy() -> None:
    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    policy = verify_seed_document_context(
        repo_root=REPO_ROOT,
        manifest_context=seed,
        document_id="sl2-policy-returns-v1",
    )
    prechange = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=seed,
    )
    v1 = next(
        item
        for item in prechange.claims.revisions
        if item.source.source_claim_id == "policy-sl2-policy-returns-v1-01"
    )
    v1_source = next(
        item for item in policy.source_note.key_claims if item.id == v1.source.source_claim_id
    )
    assert v1.scopes == claim_scopes_v1(
        document_family=policy.document.document_family,
        affects=tuple(v1_source.affects),
    )

    incoming = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    note_data, _body = parse_frontmatter(incoming.processed_snapshot.decode("utf-8"))
    claims_by_id = {item["id"]: item for item in note_data["key_claims"]}
    for revision in incoming.claim_revisions:
        source = claims_by_id[revision.source.source_claim_id]
        assert revision.scopes == claim_scopes_v1(
            document_family=incoming.document.document_family,
            affects=tuple(source["affects"]),
        )
    v2 = next(
        item
        for item in incoming.claim_revisions
        if item.source.source_claim_id == "policy-sl2-policy-returns-v2-01"
    )
    assert v1.scopes == v2.scopes


def test_binding_factory_derives_exact_aggregate_evidence_and_changed_roots() -> None:
    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    event = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    prechange = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=seed,
    )
    analysis, changed = bootstrap_module._build_analysis_aggregate(prechange, event)

    binding = create_verified_analysis_bootstrap_binding(
        repo_root=REPO_ROOT,
        seed_context=seed,
        incoming_event=event,
        prechange_aggregate=prechange,
        analysis_aggregate=analysis,
        prechange_operation_id=PRECHANGE_OPERATION_ID,
        analysis_operation_id=ANALYSIS_OPERATION_ID,
    )
    assert binding.prechange_aggregate_sha256 == aggregate_sha256(prechange)
    assert binding.analysis_aggregate_sha256 == aggregate_sha256(analysis)
    assert binding.incoming_claim_evidence_sha256 == incoming_claim_evidence_sha256(event)
    assert binding.changed_claim_revision_ids == changed
    assert type(binding) is AnalysisBootstrapBinding

    from mastervault.change_control.managed_review import ManagedAnalysisSetBinding

    analysis_set = ManagedAnalysisSetBinding.create(
        analysis_bootstrap=binding,
        candidate_result_sha256="a" * 64,
        classification_result_sha256="b" * 64,
        attention_result_sha256="c" * 64,
        impact_result_sha256="d" * 64,
        global_relevant_claim_revision_ids=binding.changed_claim_revision_ids,
    )
    canonical = ManagedAnalysisSetBinding.model_validate_json(analysis_set.model_dump_json())
    assert type(canonical.analysis_bootstrap) is AnalysisBootstrapBinding
    assert canonical.analysis_bootstrap.binding_id == binding.binding_id
    assert canonical.analysis_bootstrap.binding_sha256 == binding.binding_sha256

    with pytest.raises(AnalysisBootstrapIntegrityError, match="exact projection"):
        create_verified_analysis_bootstrap_binding(
            repo_root=REPO_ROOT,
            seed_context=seed,
            incoming_event=event,
            prechange_aggregate=prechange,
            analysis_aggregate=prechange,
            prechange_operation_id=PRECHANGE_OPERATION_ID,
            analysis_operation_id=ANALYSIS_OPERATION_ID,
        )


def test_package_root_exposes_pure_binding_without_loading_bootstrap_dependencies() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import mastervault.change_control as package; "
                "from mastervault.change_control.analysis_binding import "
                "AnalysisBootstrapBinding, AnalysisBootstrapError, "
                "AnalysisBootstrapIntegrityError; "
                "assert package.AnalysisBootstrapBinding is AnalysisBootstrapBinding; "
                "assert package.AnalysisBootstrapError is AnalysisBootstrapError; "
                "assert package.AnalysisBootstrapIntegrityError is "
                "AnalysisBootstrapIntegrityError; "
                "assert package.ANALYSIS_AGGREGATE_ID == 'larkstead.sl2-returns'; "
                "forbidden=('sqlite3','mastervault.change_control.bootstrap',"
                "'mastervault.change_control.incoming','mastervault.change_control.seed',"
                "'mastervault.change_control.store','mastervault.change_control.workflow'); "
                "bad=[name for name in forbidden if name in sys.modules]; "
                "assert not bad, bad"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_binding_factory_rejects_counterfactual_prechange_with_genuine_seed() -> None:
    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    event = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    verified = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=seed,
    )
    original = verified.claims.revisions[0]
    counterfactual_claim = VersionedClaimRevision.create(
        document=original.document,
        source=original.source,
        statement=f"{original.statement} Counterfactual seed mutation.",
        declared_effective_from=original.declared_effective_from,
        declared_effective_to=original.declared_effective_to,
        scopes=original.scopes,
    )
    counterfactual_prechange = bootstrap_module._empty_aggregate(
        documents=verified.documents.documents,
        claims=(counterfactual_claim, *verified.claims.revisions[1:]),
    )
    counterfactual_analysis, _changed = bootstrap_module._build_analysis_aggregate(
        counterfactual_prechange,
        event,
    )

    assert counterfactual_prechange != verified
    with pytest.raises(AnalysisBootstrapIntegrityError, match="sealed seed context"):
        create_verified_analysis_bootstrap_binding(
            repo_root=REPO_ROOT,
            seed_context=seed,
            incoming_event=event,
            prechange_aggregate=counterfactual_prechange,
            analysis_aggregate=counterfactual_analysis,
            prechange_operation_id=PRECHANGE_OPERATION_ID,
            analysis_operation_id=ANALYSIS_OPERATION_ID,
        )


def test_generation_zero_requires_repository_capability_and_stable_base_manifest() -> None:
    from mastervault.change_control.managed_review import (
        AggregateHeadBinding,
        AuthorityRevisionBinding,
    )

    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    event = load_verified_incoming_event(repo_root=REPO_ROOT, manifest_path=INCOMING_MANIFEST)
    prechange = build_verified_prechange_aggregate(repo_root=REPO_ROOT, manifest_context=seed)
    analysis, _changed = bootstrap_module._build_analysis_aggregate(prechange, event)
    capability = create_verified_analysis_bootstrap_capability(
        repo_root=REPO_ROOT,
        seed_context=seed,
        incoming_event=event,
        prechange_aggregate=prechange,
        analysis_aggregate=analysis,
        prechange_operation_id=PRECHANGE_OPERATION_ID,
        analysis_operation_id=ANALYSIS_OPERATION_ID,
    )
    head = AggregateHeadBinding.create(
        aggregate_id=capability.binding.aggregate_id,
        revision=capability.binding.prechange_revision,
        aggregate_sha256=capability.binding.prechange_aggregate_sha256,
    )
    genuine = AuthorityRevisionBinding.create_generation_zero(
        analysis_bootstrap=capability.binding,
        prechange_head=head,
    )
    verify_generation_zero_authority(
        authority=genuine,
        verified_bootstrap=capability,
        prechange_head=head,
    )

    fake_values = capability.binding.model_dump(mode="python")
    fake_values["incoming_event_identity"] = "incoming:" + "9" * 64
    fake_values["incoming_manifest_sha256"] = "8" * 64
    provisional = AnalysisBootstrapBinding.model_construct(**fake_values)
    fake_values["canonical_input_sha256"] = hashlib.sha256(
        bootstrap_module.canonical_json_bytes(provisional._canonical_input_payload())
    ).hexdigest()
    provisional = AnalysisBootstrapBinding.model_construct(**fake_values)
    digest = hashlib.sha256(
        bootstrap_module.canonical_json_bytes(provisional._identity_payload())
    ).hexdigest()
    fake_values["binding_id"] = f"analysis-bootstrap:{digest}"
    fake_values["binding_sha256"] = digest
    fake = AnalysisBootstrapBinding.model_validate(fake_values)
    structural = AuthorityRevisionBinding.create_generation_zero(
        analysis_bootstrap=fake,
        prechange_head=head,
    )
    assert structural.active_generation == genuine.active_generation
    assert structural.origin_basis != genuine.origin_basis
    with pytest.raises(ValueError, match="does not resolve to exact verified bootstrap roots"):
        verify_generation_zero_authority(
            authority=structural,
            verified_bootstrap=capability,
            prechange_head=head,
        )


def test_result_retains_exact_raw_evidence_across_aggregate_reload(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    try:
        result = _bootstrap(store)
        reloaded = store.load(ANALYSIS_AGGREGATE_ID)
    finally:
        store.close()
    assert reloaded == result.snapshot
    assert result.binding.incoming_claim_evidence_sha256 == incoming_claim_evidence_sha256(
        result.incoming_event
    )
    grounded_by_id = {
        item.revision.claim_revision_id: item for item in result.incoming_event.grounded_claims
    }
    assert set(grounded_by_id) == set(result.binding.changed_claim_revision_ids)
    for revision_id, grounded in grounded_by_id.items():
        assert reloaded is not None
        assert reloaded.aggregate.claims.get(revision_id) == grounded.revision
        assert grounded.raw_evidence
        for span in grounded.raw_evidence:
            assert result.incoming_event.source_snapshot[
                span.start_byte : span.end_byte
            ] == span.quote.encode("utf-8")

    tampered_binding = result.binding.model_dump(mode="python")
    tampered_binding["incoming_claim_evidence_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical input SHA"):
        AnalysisBootstrapBinding.model_validate(tampered_binding)

    first = result.incoming_event.grounded_claims[0]
    missing_span = first.model_copy(update={"raw_evidence": first.raw_evidence[1:]})
    tampered_event = replace(
        result.incoming_event,
        _grounded_claims=(missing_span, *result.incoming_event.grounded_claims[1:]),
    )
    with pytest.raises(IncomingIntegrityError):
        incoming_claim_evidence_sha256(tampered_event)


def test_all_capabilities_validate_before_store_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    tampered = replace(genuine, _event_identity="incoming:" + "0" * 64)
    monkeypatch.setattr(
        bootstrap_module, "load_verified_incoming_event", lambda **_kwargs: tampered
    )
    store = _store(tmp_path / "state.sqlite3")
    try:
        with pytest.raises(IncomingIntegrityError):
            _bootstrap(store)
        assert store.load(ANALYSIS_AGGREGATE_ID) is None
    finally:
        store.close()


def test_exact_replay_and_crash_after_revision_one_resume(tmp_path: Path) -> None:
    replay_store = _store(tmp_path / "replay.sqlite3")
    try:
        first = _bootstrap(replay_store)
        second = _bootstrap(replay_store)
        assert not first.prechange_commit.replayed
        assert not first.analysis_commit.replayed
        assert second.prechange_commit.replayed
        assert second.analysis_commit.replayed
        assert second.binding == first.binding
        assert second.snapshot == first.snapshot
    finally:
        replay_store.close()

    recovery_store = _store(tmp_path / "recovery.sqlite3")
    seed = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    prechange = build_verified_prechange_aggregate(
        repo_root=REPO_ROOT,
        manifest_context=seed,
    )
    try:
        crash_boundary = recovery_store.create(
            prechange,
            operation_id=PRECHANGE_OPERATION_ID,
        )
        assert crash_boundary.revision == 1
        recovered = _bootstrap(recovery_store)
        assert recovered.prechange_commit.replayed
        assert not recovered.analysis_commit.replayed
        assert recovered.snapshot.revision == 2
    finally:
        recovery_store.close()

    stale_store = _store(tmp_path / "stale-recovery.sqlite3")
    try:
        stale_store.create(prechange, operation_id="other-owner:prechange-v1")
        with pytest.raises(ChangeControlConflictError):
            _bootstrap(stale_store)
        stale = stale_store.load(ANALYSIS_AGGREGATE_ID)
        assert stale is not None and stale.revision == 1 and stale.aggregate == prechange
    finally:
        stale_store.close()


def test_operation_reuse_and_foreign_revision_one_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path / "operation.sqlite3")
    try:
        result = _bootstrap(store)
        with pytest.raises(ChangeControlIdempotencyError, match="different aggregate inputs"):
            store.create(
                result.snapshot.aggregate,
                operation_id=PRECHANGE_OPERATION_ID,
            )
    finally:
        store.close()

    foreign_store = _store(tmp_path / "foreign.sqlite3")
    foreign = ChangeControlAggregate.create(
        aggregate_id=ANALYSIS_AGGREGATE_ID,
        documents=DocumentVersionRegistry.create(()),
        claims=ClaimRevisionRegistry.create(()),
        relation_graph=bootstrap_module.RelationGraph.create(()),
        dependencies=bootstrap_module.DependencyRegistry.create(()),
        document_replacements=bootstrap_module.DocumentReplacementSet.create(()),
        temporal_constraints=bootstrap_module.TemporalConstraintSet.create(()),
    )
    try:
        foreign_store.create(foreign, operation_id="foreign:revision-one")
        with pytest.raises(ChangeControlConflictError):
            _bootstrap(foreign_store)
        live = foreign_store.load(ANALYSIS_AGGREGATE_ID)
        assert live is not None and live.revision == 1 and live.aggregate == foreign
    finally:
        foreign_store.close()


def test_advanced_head_is_never_reported_as_revision_two(tmp_path: Path) -> None:
    store = _store(tmp_path / "advanced.sqlite3")
    try:
        result = _bootstrap(store)
        extra = DocumentVersionMetadata.create(
            document_id="unrelated-v1",
            document_family="unrelated-family",
            version_label="v1",
            source_path="datasets/runtime/unrelated.md",
            source_sha256="f" * 64,
            declared_effective_from=date(2025, 1, 1),
            role=DocumentRole.OTHER,
            authority=DocumentAuthority.TRANSACTIONAL,
        )
        advanced = ChangeControlAggregate.create(
            aggregate_id=ANALYSIS_AGGREGATE_ID,
            documents=DocumentVersionRegistry.create(
                (*result.snapshot.aggregate.documents.documents, extra)
            ),
            claims=result.snapshot.aggregate.claims,
            relation_graph=result.snapshot.aggregate.relation_graph,
            dependencies=result.snapshot.aggregate.dependencies,
            document_replacements=result.snapshot.aggregate.document_replacements,
            temporal_constraints=result.snapshot.aggregate.temporal_constraints,
        )
        committed = store.compare_and_swap(
            advanced,
            expected_revision=2,
            operation_id="analysis-bootstrap:later-owner",
        )
        assert committed.revision == 3
        with pytest.raises(AnalysisBootstrapStaleError, match="owns only revision 2"):
            _bootstrap(store)
        live = store.load(ANALYSIS_AGGREGATE_ID)
        assert live is not None and live.revision == 3 and live.aggregate == advanced
    finally:
        store.close()


def test_duplicate_roots_fail_before_any_store_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = load_verified_incoming_event(
        repo_root=REPO_ROOT,
        manifest_path=INCOMING_MANIFEST,
    )
    duplicate = (*genuine.claim_revisions[:-1], genuine.claim_revisions[0])
    monkeypatch.setattr(
        bootstrap_module.VerifiedIncomingEvent,
        "claim_revisions",
        property(lambda _self: duplicate),
    )
    store = _store(tmp_path / "state.sqlite3")
    try:
        with pytest.raises(AnalysisBootstrapIntegrityError, match="duplicate claim roots"):
            _bootstrap(store)
        assert store.load(ANALYSIS_AGGREGATE_ID) is None
    finally:
        store.close()


def test_bootstrap_mutates_only_caller_selected_state_sqlite(tmp_path: Path) -> None:
    before = _repository_source_hashes()
    state_path = tmp_path / "isolated" / "state.sqlite3"
    store = _store(state_path)
    try:
        _bootstrap(store)
    finally:
        store.close()
    assert _repository_source_hashes() == before
    assert state_path.is_file()
    assert {item.name for item in state_path.parent.iterdir()} == {"state.sqlite3"}


def test_runtime_bootstrap_has_static_evaluator_and_unsealed_loader_isolation() -> None:
    for module in (bootstrap_module, claim_scopes_module):
        path = Path(module.__file__ or "")
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            name == "mastervault.evals" or name.startswith("mastervault.evals.") for name in imports
        )
        assert "golden" not in source.casefold()
        assert "load_prechange_seed_manifest" not in source
        assert "materialize_prechange_seed" not in source
