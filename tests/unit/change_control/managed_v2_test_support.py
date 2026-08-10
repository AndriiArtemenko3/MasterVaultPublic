"""Shared real recorded-run fixture support for managed PR-A service tests."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from test_managed_revision_admission import _bind, _recorded_scenario, _resolver
from test_temporal_proposal import _build_case, _build_temporal_evidence

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.impact_analysis import build_impact_workload
from mastervault.change_control.incoming import MANIFEST_RELATIVE_PATH
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedRevisionPlan,
    ManagedRunBindingV2,
    NoChangeImpactCard,
)
from mastervault.change_control.managed_review_repository import (
    ApprovedManagedGoverningSourceAuthority,
    RepositoryBackedManagedReviewResolver,
    derive_managed_governing_source_adoption,
)
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    ReviewDecisionItem,
    ReviewDisposition,
)
from mastervault.change_control.reviewed_snapshot import (
    ReviewedTemporalSnapshotAuthority,
    resolve_reviewed_temporal_snapshot,
)
from mastervault.change_control.temporal_commit import commit_temporal_proposal
from mastervault.change_control.temporal_proposal import open_temporal_review


@dataclass(frozen=True)
class RealManagedV2Scenario:
    store: SqliteManagedChangeControlStore
    authority_path: Path
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority
    run_binding: ManagedRunBindingV2
    subjects: tuple[ManagedRevisionPlan | NoChangeImpactCard, ...]
    resolver: RepositoryBackedManagedReviewResolver
    verified_bootstrap: VerifiedAnalysisBootstrapCapability
    prechange_head: AggregateHeadBinding
    canonical_root: Path


def build_real_managed_v2_scenario(tmp_path: Path) -> RealManagedV2Scenario:
    case = _build_case(tmp_path / "temporal-case")
    temporal_evidence = _build_temporal_evidence(case)
    temporal_commit = commit_temporal_proposal(
        case.store,
        case.proposal,
        temporal_analysis=temporal_evidence,
        evidence_repository=case.evidence_repository,
        classification_batch=case.classification_batch,
        dependency_batch=case.dependency_batch,
        source_note_resolver=case.build_inputs["inventory_resolver"],
    )
    temporal_request = open_temporal_review(
        case.store,
        temporal_commit,
        requester_id="managed.v2.fixture.requester",
        rationale="Adjudicate the exact temporal prerequisite for managed review.",
        operation_id="managed-v2-fixture:temporal-request",
    )
    case.store.decide_review(
        HumanReviewDecisionCommand(
            request_id=temporal_request.request.request_id,
            reviewer_id="managed.v2.fixture.reviewer",
            rationale="Accept the complete temporal prerequisite for the managed fixture.",
            items=tuple(
                ReviewDecisionItem(
                    kind=subject.kind,
                    subject_id=subject.subject_id,
                    original_subject_sha256=subject.subject_sha256,
                    disposition=ReviewDisposition.ACCEPTED,
                )
                for subject in temporal_request.request.subjects
            ),
        ),
        operation_id="managed-v2-fixture:temporal-decision",
    )
    reviewed = resolve_reviewed_temporal_snapshot(
        case.store,
        temporal_analysis_manifest_id=temporal_evidence.manifest_id,
        temporal_analysis_manifest_sha256=temporal_evidence.manifest_sha256,
        temporal_request_id=temporal_request.request.request_id,
        evidence_repository=case.evidence_repository,
        source_note_resolver=case.build_inputs["inventory_resolver"],
    )
    workload = build_impact_workload(reviewed)
    recorded = _recorded_scenario(
        authority=reviewed,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=case.evidence_repository.root,
    )
    admission = _bind(recorded)
    analysis = recorded.run.analysis_set
    assert analysis is not None
    bootstrap_binding = analysis.analysis_bootstrap
    adoption = derive_managed_governing_source_adoption(
        reviewed_snapshot=reviewed,
        analysis_bootstrap=bootstrap_binding,
        repo_root=recorded.canonical_root,
        manifest_path=recorded.canonical_root / MANIFEST_RELATIVE_PATH,
        evidence_repository_id=admission.repository_id,
    )
    resolver = _resolver(
        recorded,
        admission,
        governing_sources=(
            ApprovedManagedGoverningSourceAuthority(
                adoption=adoption,
                reviewed_snapshot=reviewed,
                analysis_bootstrap=bootstrap_binding,
            ),
        ),
    )
    prechange_head = AggregateHeadBinding.create(
        aggregate_id=bootstrap_binding.aggregate_id,
        revision=bootstrap_binding.prechange_revision,
        aggregate_sha256=bootstrap_binding.prechange_aggregate_sha256,
    )
    run_binding = ManagedRunBindingV2.create(
        run_id=admission.run_id,
        operation_id=temporal_commit.operation_id,
        prechange_head=prechange_head,
        analysis_head=reviewed.binding.analysis_head,
        algorithm_manifest_sha256=recorded.run.outcomes[
            0
        ].execution.contract.algorithm_manifest_sha256,
        inference_contract=recorded.run.outcomes[0].execution.contract,
        analysis_set=analysis,
        revision_planning_admission=admission,
        governing_source_adoption=adoption,
    )
    authority_path = case.store.db_path
    case.store.close()
    store = SqliteManagedChangeControlStore(authority_path)
    verified_bootstrap = case.build_inputs["verified_bootstrap"]
    assert isinstance(verified_bootstrap, VerifiedAnalysisBootstrapCapability)
    store.initialize_generation_zero(
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )
    return RealManagedV2Scenario(
        store=store,
        authority_path=authority_path,
        reviewed_snapshot=reviewed,
        run_binding=run_binding,
        subjects=recorded.run.subjects,
        resolver=resolver,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
        canonical_root=recorded.canonical_root,
    )


def clone_real_managed_v2_scenario(
    seed: RealManagedV2Scenario, tmp_path: Path
) -> RealManagedV2Scenario:
    """Clone only SQLite authority; immutable evidence/canonical roots remain shared."""

    destination = tmp_path / "change-control.sqlite3"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed.authority_path, destination)
    return RealManagedV2Scenario(
        store=SqliteManagedChangeControlStore(destination),
        authority_path=destination,
        reviewed_snapshot=seed.reviewed_snapshot,
        run_binding=seed.run_binding,
        subjects=seed.subjects,
        resolver=seed.resolver,
        verified_bootstrap=seed.verified_bootstrap,
        prechange_head=seed.prechange_head,
        canonical_root=seed.canonical_root,
    )


__all__ = [
    "RealManagedV2Scenario",
    "build_real_managed_v2_scenario",
    "clone_real_managed_v2_scenario",
]
