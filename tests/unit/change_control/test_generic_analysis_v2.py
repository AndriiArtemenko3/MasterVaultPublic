from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from mastervault.change_control import workspace_bootstrap as workspace_bootstrap_module
from mastervault.change_control.analysis_binding import GenericAnalysisBootstrapBindingV2
from mastervault.change_control.claim_scopes import claim_scopes_v1
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.discovery import generate_relationship_candidates
from mastervault.change_control.generic_analysis import (
    GenericAnalysisStaleError,
    reopen_generic_analysis_capability_v2,
    start_generic_analysis_v2,
    verify_generic_analysis_snapshot_v2,
)
from mastervault.change_control.generic_governing_source import (
    CompositeManagedReviewResolverV2,
    GenericGoverningSourceResolverV2,
)
from mastervault.change_control.generic_incoming import (
    GenericExtractionModeV2,
    GenericGroundedExtractionV2,
    admit_generic_incoming_markdown_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    GenericEvidenceBundleReceiptV2,
    GenericIncomingRepositoryError,
    _digest_model,
)
from mastervault.change_control.managed_review import AggregateHeadBinding
from mastervault.change_control.managed_review_repository import (
    RepositoryBackedManagedReviewResolver,
)
from mastervault.change_control.managed_review_service import (
    _require_production_resolver,
    _store_authority_context,
)
from mastervault.change_control.managed_store import AuthorityVerificationContext
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    TemporalConstraintSet,
    VersionedClaimRevision,
    canonical_json_bytes,
)
from mastervault.change_control.store import ChangeControlSnapshot, SqliteChangeControlStore
from mastervault.change_control.workspace_bootstrap import (
    LegacyIndexExpectation,
    LegacyIndexReadinessReceipt,
    ManagedSourceNoteBootstrapMetadata,
    WorkspaceBootstrapIntent,
    WorkspaceBootstrapInventory,
    WorkspaceBootstrapState,
    WorkspaceInventoryReceipt,
    WorkspaceNoteKind,
    WorkspaceVaultMember,
)

_SHA = "1" * 64


class _StableGuard:
    def verify(self) -> None:
        pass


def _workspace_capability() -> tuple[
    workspace_bootstrap_module.VerifiedWorkspaceBootstrapCapability,
    ChangeControlAggregate,
    tuple[CanonicalSourceNoteSnapshot, ...],
]:
    note_text = "---\ntitle: Existing Policy\ntype: source\n---\n\nExisting policy applies.\n"
    note_sha = hashlib.sha256(note_text.encode()).hexdigest()
    document = DocumentVersionMetadata.create(
        document_id="existing-policy",
        document_family="returns-policy",
        version_label="v1",
        source_path="raw/existing-policy.md",
        source_sha256=_SHA,
        declared_effective_from=date(2025, 1, 1),
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )
    claim = VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path="customer-support/sources/existing-policy.md",
            source_note_sha256=note_sha,
            source_claim_id="existing-policy-01",
        ),
        statement="Existing policy applies.",
        declared_effective_from=date(2025, 1, 1),
        scopes=claim_scopes_v1(document_family="returns-policy", affects=("refund-policy",)),
    )
    aggregate = ChangeControlAggregate.create(
        aggregate_id="workspace-change-control",
        documents=DocumentVersionRegistry.create((document,)),
        claims=ClaimRevisionRegistry.create((claim,)),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    note = CanonicalSourceNoteSnapshot.create(
        document=document,
        source_note_path="customer-support/sources/existing-policy.md",
        source_note_utf8=note_text,
        body_start_char=note_text.index("\n---\n") + len("\n---\n"),
    )
    inventory = WorkspaceBootstrapInventory.create(
        manifest_schema_version=1,
        manifest_sha256="2" * 64,
        vault_members=(
            WorkspaceVaultMember(
                logical_path=note.source_note_path,
                note_kind=WorkspaceNoteKind.SOURCE,
                content_sha256=note_sha,
                byte_count=len(note_text.encode()),
            ),
        ),
        managed_source_notes=(
            ManagedSourceNoteBootstrapMetadata(
                logical_path=note.source_note_path,
                source_note_sha256=note_sha,
                source_note_byte_count=len(note_text.encode()),
                source_root_id="workspace",
                source_relative_path="raw/existing-policy.md",
                source_note_provenance="raw/existing-policy.md",
                raw_source_path="raw/existing-policy.md",
                raw_source_sha256=_SHA,
                raw_source_byte_count=64,
                document=document,
            ),
        ),
        legacy_index=LegacyIndexExpectation(
            index_file_sha256="3" * 64,
            index_file_byte_count=4096,
            index_schema_version=1,
            embedding_model="test-model",
            embedding_dimensions=3,
        ),
    )
    intent = WorkspaceBootstrapIntent.create(
        operation_id="bootstrap-workspace",
        aggregate_id=aggregate.aggregate_id,
        inventory=inventory,
    )
    inventory_receipt = WorkspaceInventoryReceipt.create(
        operation_id="bootstrap-inventory",
        bootstrap_id=intent.bootstrap_id,
        aggregate_operation_id="bootstrap-aggregate",
        aggregate_id=aggregate.aggregate_id,
        aggregate_revision=1,
        aggregate_sha256=hashlib.sha256(
            canonical_json_bytes(aggregate.model_dump(mode="json"))
        ).hexdigest(),
        inventory_id=inventory.inventory_id,
        inventory_sha256=inventory.inventory_sha256,
        recorded_at="2026-08-20T00:00:00+00:00",
    )
    readiness = LegacyIndexReadinessReceipt.create(
        operation_id="bootstrap-index",
        bootstrap_id=intent.bootstrap_id,
        inventory_receipt_id=inventory_receipt.receipt_id,
        inventory_receipt_sha256=inventory_receipt.receipt_sha256,
        index_logical_fingerprint="4" * 64,
        index_file_sha256=inventory.legacy_index.index_file_sha256,
        index_file_byte_count=inventory.legacy_index.index_file_byte_count,
        index_schema_version=inventory.legacy_index.index_schema_version,
        embedding_model=inventory.legacy_index.embedding_model,
        embedding_dimensions=inventory.legacy_index.embedding_dimensions,
        ready_at="2026-08-20T00:00:01+00:00",
    )
    state = WorkspaceBootstrapState(
        intent=intent,
        inventory=inventory,
        inventory_receipt=inventory_receipt,
        index_readiness_receipt=readiness,
    )
    verifier = workspace_bootstrap_module._mint_verified_workspace_bootstrap_evidence_verifier(
        _StableGuard(),
        resolved_inventory=inventory,
        resolved_aggregate=aggregate,
        legacy_attestation=object(),
    )
    capability = workspace_bootstrap_module._mint_verified_workspace_bootstrap_capability(
        state, evidence_verifier=verifier
    )
    return capability, aggregate, (note,)


def _generic_evidence(
    tmp_path: Path,
) -> tuple[
    FilesystemGenericIncomingRepositoryV2,
    object,
    object,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    source = tmp_path / "returns-policy-v2.md"
    source.write_text(
        """---
mastervault_change:
  schema_version: 1
  event_id: returns-event-v2
  document_id: returns-policy-v2
  document_family: returns-policy
  version_label: v2
  title: Returns Policy
  domain: customer-support
  source_type: policy
  declared_effective_from: 2026-08-20
  role: policy
  authority: primary
  operator_intent: Admit this governing document.
---
Customers receive refunds within five days.
""",
        encoding="utf-8",
    )
    source.chmod(0o600)
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    live = ground_generic_extraction_v2(
        admission,
        {
            "claims": [
                {
                    "quote": "Customers receive refunds within five days.",
                    "confidence": "high",
                    "affects": ["refund-policy"],
                }
            ]
        },
    )
    repository = FilesystemGenericIncomingRepositoryV2(tmp_path / "evidence")
    return repository, repository.persist(admission, live), admission


def test_generic_analysis_cas_retry_and_complete_reconstruction(tmp_path: Path) -> None:
    workspace_capability, prechange, workspace_notes = _workspace_capability()
    store = SqliteChangeControlStore(tmp_path / "authority" / "change-control.sqlite3")
    store.init_schema()
    store.create(prechange, operation_id="bootstrap-aggregate")
    repository, evidence_capability, admission = _generic_evidence(tmp_path)

    first = start_generic_analysis_v2(
        store=store,
        repository=repository,
        workspace_capability=workspace_capability,
        evidence_capability=evidence_capability,  # type: ignore[arg-type]
        workspace_source_notes=workspace_notes,
        analysis_operation_id="generic-analysis",
    )
    assert type(first.binding) is GenericAnalysisBootstrapBindingV2
    assert first.snapshot.revision == 2
    assert len(first.snapshot.aggregate.documents.documents) == 2
    assert len(first.inventory_capability.verify(snapshot=first.snapshot).notes) == 2
    candidates = generate_relationship_candidates(
        first.snapshot,
        changed_claim_revision_ids=first.binding.changed_claim_revision_ids,
        as_of=first.binding.analysis_as_of,
    )
    assert candidates.binding.aggregate_id == prechange.aggregate_id
    assert candidates.binding.changed_claim_revision_ids == first.binding.changed_claim_revision_ids
    serialized = canonical_json_bytes(first.binding.model_dump(mode="json"))
    assert b"larkstead" not in serialized.lower() and b"sl2" not in serialized.lower()
    assert str(tmp_path).encode() not in serialized

    retry = start_generic_analysis_v2(
        store=store,
        repository=repository,
        workspace_capability=workspace_capability,
        evidence_capability=evidence_capability,  # type: ignore[arg-type]
        workspace_source_notes=workspace_notes,
        analysis_operation_id="generic-analysis",
    )
    assert retry.binding == first.binding
    assert retry.analysis_commit.replayed is True
    assert first.binding.analysis_operation_id != "generic-analysis"

    reopened = repository.resolve_verified_evidence(evidence_capability)  # type: ignore[arg-type]
    recorded_live = GenericGroundedExtractionV2(
        mode=GenericExtractionModeV2.LIVE,
        source_sha256=reopened.inference.source_sha256,
        request_sha256=reopened.inference.request_sha256,
        provider_result_sha256=reopened.inference.provider_result_sha256,
        provider_contract=reopened.inference.provider_contract,
        claims=reopened.inference.claims,
    )
    replay = ground_generic_extraction_v2(
        admission,  # type: ignore[arg-type]
        reopened.inference.provider_contract,
        mode=GenericExtractionModeV2.REPLAY,
        replay_of=recorded_live,
    )
    replay_capability = repository.persist(admission, replay)  # type: ignore[arg-type]
    with pytest.raises(GenericAnalysisStaleError, match="exactly one"):
        start_generic_analysis_v2(
            store=store,
            repository=repository,
            workspace_capability=workspace_capability,
            evidence_capability=replay_capability,
            workspace_source_notes=workspace_notes,
            analysis_operation_id="generic-analysis",
        )

    with pytest.raises(GenericAnalysisStaleError, match="exactly one"):
        start_generic_analysis_v2(
            store=store,
            repository=repository,
            workspace_capability=workspace_capability,
            evidence_capability=evidence_capability,  # type: ignore[arg-type]
            workspace_source_notes=workspace_notes,
            analysis_operation_id="second-generic-analysis",
        )


def test_recorded_sanitized_contract_supports_fresh_exact_replay(tmp_path: Path) -> None:
    repository, capability, admission = _generic_evidence(tmp_path)
    fresh_repository = FilesystemGenericIncomingRepositoryV2(
        repository.root, create=False, read_only=True
    )
    fresh_capability = fresh_repository.reopen(capability.bundle_id)  # type: ignore[attr-defined]
    evidence = fresh_repository.resolve_verified_evidence(fresh_capability)
    recorded_live = GenericGroundedExtractionV2(
        mode=GenericExtractionModeV2.LIVE,
        source_sha256=evidence.inference.source_sha256,
        request_sha256=evidence.inference.request_sha256,
        provider_result_sha256=evidence.inference.provider_result_sha256,
        provider_contract=evidence.inference.provider_contract,
        claims=evidence.inference.claims,
    )
    replay = ground_generic_extraction_v2(
        admission,  # type: ignore[arg-type]
        evidence.inference.provider_contract,
        mode=GenericExtractionModeV2.REPLAY,
        replay_of=recorded_live,
    )
    assert replay.provider_result_sha256 == evidence.inference.provider_result_sha256
    assert replay.claims == evidence.inference.claims


def test_generic_analysis_capability_reopens_after_live_head_reaches_revision_four(
    tmp_path: Path,
) -> None:
    workspace_capability, prechange, workspace_notes = _workspace_capability()
    store = SqliteChangeControlStore(tmp_path / "authority" / "change-control.sqlite3")
    store.init_schema()
    store.create(prechange, operation_id="bootstrap-aggregate")
    repository, evidence_capability, _admission = _generic_evidence(tmp_path)
    started = start_generic_analysis_v2(
        store=store,
        repository=repository,
        workspace_capability=workspace_capability,
        evidence_capability=evidence_capability,  # type: ignore[arg-type]
        workspace_source_notes=workspace_notes,
        analysis_operation_id="generic-analysis",
    )
    durable_binding = GenericAnalysisBootstrapBindingV2.model_validate_json(
        canonical_json_bytes(started.binding.model_dump(mode="json"))
    )
    durable_analysis_snapshot = ChangeControlSnapshot(
        aggregate=ChangeControlAggregate.model_validate_json(
            canonical_json_bytes(started.snapshot.aggregate.model_dump(mode="json"))
        ),
        revision=started.snapshot.revision,
        aggregate_sha256=started.snapshot.aggregate_sha256,
    )
    pair = ComparableClaimPair.create(*started.snapshot.aggregate.claims.revisions)
    proposed = started.snapshot.aggregate.model_copy(
        update={
            "relation_graph": RelationGraph.create(
                (
                    RelationAssessment.create(
                        pair=pair,
                        disposition=PairDisposition.UNRELATED,
                        rationale="Temporal proposal evidence.",
                        confidence=1.0,
                    ),
                )
            )
        }
    )
    store.compare_and_swap(
        proposed,
        expected_revision=2,
        operation_id="temporal-proposal-placeholder",
    )
    reviewed = proposed.model_copy(
        update={
            "relation_graph": RelationGraph.create(
                (
                    RelationAssessment.create(
                        pair=pair,
                        disposition=PairDisposition.UNRELATED,
                        rationale="Reviewed temporal evidence.",
                        confidence=1.0,
                    ),
                )
            )
        }
    )
    store.compare_and_swap(
        reviewed,
        expected_revision=3,
        operation_id="temporal-review-placeholder",
    )
    assert store.load(prechange.aggregate_id).revision == 4  # type: ignore[union-attr]

    fresh_repository = FilesystemGenericIncomingRepositoryV2(
        repository.root, create=False, read_only=True
    )
    fresh_evidence_capability = fresh_repository.reopen(evidence_capability.bundle_id)  # type: ignore[attr-defined]
    reminted = reopen_generic_analysis_capability_v2(
        binding=durable_binding,
        analysis_snapshot=durable_analysis_snapshot,
        repository=fresh_repository,
        workspace_capability=workspace_capability,
        evidence_capability=fresh_evidence_capability,
    )
    assert reminted is not started.verification_capability
    assert (
        verify_generic_analysis_snapshot_v2(reminted, durable_analysis_snapshot) == durable_binding
    )

    with pytest.raises(GenericAnalysisStaleError, match="exactly one"):
        start_generic_analysis_v2(
            store=store,
            repository=fresh_repository,
            workspace_capability=workspace_capability,
            evidence_capability=fresh_evidence_capability,
            workspace_source_notes=workspace_notes,
            analysis_operation_id="generic-analysis-after-review",
        )


def test_semantic_reopen_rejects_self_consistent_provider_grounding_substitution(
    tmp_path: Path,
) -> None:
    repository, capability, _admission = _generic_evidence(tmp_path)
    evidence = repository.resolve_verified_evidence(capability)  # type: ignore[arg-type]
    candidate = evidence.inference.provider_contract.claims[0].model_copy(
        update={"affects": ("different-policy",)}
    )
    contract = evidence.inference.provider_contract.model_copy(update={"claims": (candidate,)})
    provisional = evidence.inference.model_copy(
        update={
            "inference_sha256": "0" * 64,
            "provider_contract": contract,
            "provider_result_sha256": hashlib.sha256(
                canonical_json_bytes(contract.model_dump(mode="json"))
            ).hexdigest(),
        }
    )
    altered = provisional.model_copy(
        update={"inference_sha256": _digest_model(provisional, "inference_sha256")}
    )
    bundle = repository._make_bundle(
        evidence.admission,
        evidence.source,
        evidence.projection,
        altered,
    )
    inference_path = repository.root / bundle.inference_receipt_locator
    inference_path.write_bytes(repository._receipt_bytes(altered))
    inference_path.chmod(0o600)
    bundle_path = (
        repository.root / "generic-incoming" / "v2" / "bundles" / f"{bundle.bundle_sha256}.json"
    )
    bundle_path.write_bytes(repository._receipt_bytes(bundle))
    bundle_path.chmod(0o600)

    substituted = repository.reopen(bundle.bundle_id)
    with pytest.raises(GenericIncomingRepositoryError, match="provider suggestions"):
        repository.resolve_verified_evidence(substituted)


def test_capability_verification_uses_one_locked_semantic_reopen(tmp_path: Path) -> None:
    repository, capability, _admission = _generic_evidence(tmp_path)
    original = repository._read_model
    bundle_reads = 0

    def _count_bundle_reads(locator: str, model: type[object], label: str) -> object:
        nonlocal bundle_reads
        if model is GenericEvidenceBundleReceiptV2:
            bundle_reads += 1
        return original(locator, model, label)  # type: ignore[arg-type]

    repository._read_model = _count_bundle_reads  # type: ignore[method-assign]
    assert repository.verify_capability(capability).bundle_id == capability.bundle_id
    assert bundle_reads == 1

    with pytest.raises(GenericIncomingRepositoryError, match="capability is invalid"):
        repository.verify_capability(object())  # type: ignore[arg-type]
    with pytest.raises(GenericIncomingRepositoryError, match="capability is invalid"):
        repository.resolve_verified_evidence(object())  # type: ignore[arg-type]


def test_generic_managed_review_service_accepts_exact_composite_workspace_authority() -> None:
    workspace_capability, aggregate, _notes = _workspace_capability()
    sealed = object.__new__(RepositoryBackedManagedReviewResolver)
    generic = object.__new__(GenericGoverningSourceResolverV2)
    composite = CompositeManagedReviewResolverV2(sealed=sealed, generic=generic)
    assert _require_production_resolver(composite) is composite

    head = AggregateHeadBinding.create(
        aggregate_id=aggregate.aggregate_id,
        revision=1,
        aggregate_sha256=hashlib.sha256(
            canonical_json_bytes(aggregate.model_dump(mode="json"))
        ).hexdigest(),
    )
    workspace_authority = AuthorityVerificationContext.workspace(workspace_capability)
    assert (
        _store_authority_context(
            verified_bootstrap=None,
            prechange_head=head,
            authority_context=workspace_authority,
        )
        is workspace_authority
    )
