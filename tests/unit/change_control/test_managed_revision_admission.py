"""Durable admission and repository resolver tests for PR14 Stage A."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_impact_analysis import ImpactWorkload, _AuthorityVariants
from test_managed_review import _context
from test_recorded_inference import ALGORITHM, PROMPT, SCHEMA, _contract, _Provider
from test_revision_planning_inference import (
    _AFFECTED_TARGET,
    _no_change_response,
    _persist_temporal_authority,
    _predecessor_snapshots,
    _recorded_impact_run,
    _revision_outputs,
    _subworkload,
)
from test_temporal_proposal import _build_case, _build_temporal_evidence

import mastervault.change_control.revision_planning_inference as planning_inference
from mastervault.change_control.analysis_binding import AnalysisBootstrapBinding
from mastervault.change_control.impact_analysis import (
    ImpactInferenceShard,
    build_impact_workload,
)
from mastervault.change_control.impact_inference import RecordedImpactInferenceRun
from mastervault.change_control.impact_results import ImpactDisposition, ImpactResultSet
from mastervault.change_control.incoming import (
    ALIGNMENT_ATTESTATION_RELATIVE_PATH,
    MANIFEST_RELATIVE_PATH,
    load_verified_incoming_event,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceResolutionError,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedGoverningSourceAdoptionBinding,
    ManagedInferenceContractBinding,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    ManagedRevisionPlanningBatchMemberBinding,
)
from mastervault.change_control.managed_review_repository import (
    ApprovedManagedGoverningSourceAuthority,
    ApprovedManagedInferenceContractAuthority,
    RepositoryBackedManagedReviewResolver,
    derive_managed_governing_source_adoption,
)
from mastervault.change_control.managed_revision_admission import (
    bind_recorded_revision_planning_run,
    reopen_revision_planning_admission,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningCitationInput,
    RevisionPlanningCitationInputSet,
    RevisionPlanningEligibility,
    RevisionPlanningInferenceShard,
    RevisionPlanningTarget,
    RevisionPlanningWorkload,
    evaluate_revision_planning_eligibility,
)
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    ReviewDecisionItem,
    ReviewDisposition,
)
from mastervault.change_control.reviewed_snapshot import (
    ReviewedTemporalSnapshotAuthority,
    resolve_reviewed_temporal_snapshot,
)
from mastervault.change_control.revision_planning_inference import (
    RecordedRevisionPlanningInferenceRun,
    RevisionPlanningPredecessorSnapshot,
    execute_revision_planning,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
)
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_commit import commit_temporal_proposal
from mastervault.change_control.temporal_proposal import open_temporal_review

pytest_plugins = ("test_impact_analysis",)


@dataclass(frozen=True)
class _RecordedScenario:
    root: Path
    canonical_root: Path
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority
    run: RecordedRevisionPlanningInferenceRun
    evidence: FilesystemInferenceEvidenceRepository
    staging: ManagedStagingRepository


@dataclass(frozen=True)
class _ExactImpactFixture:
    authority: ReviewedTemporalSnapshotAuthority
    workload: ImpactWorkload
    repository_root: Path
    authority_path: Path
    temporal_analysis_manifest_id: str
    temporal_analysis_manifest_sha256: str
    temporal_request_id: str
    source_note_resolver: RepositorySourceNoteInventoryResolver


@pytest.fixture
def exact_impact_fixture(tmp_path: Path) -> Iterator[_ExactImpactFixture]:
    """Build Step 10 and all later evidence in its genuine authority repository."""

    case = _build_case(tmp_path / "exact-impact-authority")
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
    request = open_temporal_review(
        case.store,
        temporal_commit,
        requester_id="managed.admission.fixture.requester",
        rationale="Adjudicate the exact temporal prerequisite for admission tests.",
        operation_id="managed-admission-fixture:temporal-request",
    )
    case.store.decide_review(
        HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="managed.admission.fixture.reviewer",
            rationale="Accept the complete temporal prerequisite for admission tests.",
            items=tuple(
                ReviewDecisionItem(
                    kind=subject.kind,
                    subject_id=subject.subject_id,
                    original_subject_sha256=subject.subject_sha256,
                    disposition=ReviewDisposition.ACCEPTED,
                )
                for subject in request.request.subjects
            ),
        ),
        operation_id="managed-admission-fixture:temporal-decision",
    )
    authority = resolve_reviewed_temporal_snapshot(
        case.store,
        temporal_analysis_manifest_id=temporal_evidence.manifest_id,
        temporal_analysis_manifest_sha256=temporal_evidence.manifest_sha256,
        temporal_request_id=request.request.request_id,
        evidence_repository=case.evidence_repository,
        source_note_resolver=case.build_inputs["inventory_resolver"],
    )
    try:
        yield _ExactImpactFixture(
            authority=authority,
            workload=build_impact_workload(authority),
            repository_root=case.evidence_repository.root,
            authority_path=case.store.db_path,
            temporal_analysis_manifest_id=temporal_evidence.manifest_id,
            temporal_analysis_manifest_sha256=temporal_evidence.manifest_sha256,
            temporal_request_id=request.request.request_id,
            source_note_resolver=case.build_inputs["inventory_resolver"],
        )
    finally:
        case.store.close()


def _recorded_scenario(
    *,
    authority: ReviewedTemporalSnapshotAuthority,
    workload: ImpactWorkload,
    tmp_path: Path,
    repository_root: Path,
) -> _RecordedScenario:
    root = repository_root
    canonical_root = tmp_path / "canonical"
    canonical_root.mkdir()
    source_root = Path(__file__).resolve().parents[3]
    incoming = load_verified_incoming_event(
        repo_root=source_root,
        manifest_path=source_root / MANIFEST_RELATIVE_PATH,
    )
    for relative in (
        MANIFEST_RELATIVE_PATH,
        ALIGNMENT_ATTESTATION_RELATIVE_PATH,
        incoming.document.source_path,
        incoming.manifest.document.processed_path,
    ):
        destination = canonical_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    evidence = FilesystemInferenceEvidenceRepository(root)
    staging = ManagedStagingRepository(root)
    _persist_temporal_authority(authority, evidence)
    impact_run = _recorded_impact_run(
        workload=workload,
        repository=evidence,
        dispositions={
            shard.target_note.document.document_id: (
                ImpactDisposition.AFFECTED
                if shard.target_note.document.document_id == _AFFECTED_TARGET
                else ImpactDisposition.NO_CHANGE_REQUIRED
            )
            for shard in workload.input_shards
        },
    )
    snapshots = _predecessor_snapshots(impact_run.results.workload)
    for snapshot in snapshots:
        for relative, content in (
            (snapshot.raw_path, snapshot.raw_bytes),
            (snapshot.source_note_path, snapshot.source_note_bytes),
        ):
            destination = canonical_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    run = execute_revision_planning(
        run_id=(
            "m4-admission-full-"
            + hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:16]
        ),
        impact_run=impact_run,
        predecessor_snapshots=snapshots,
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=evidence,
        staging_repository=staging,
        provider=_Provider(_revision_outputs(impact_run, snapshots)),
    )
    return _RecordedScenario(
        root=root,
        canonical_root=canonical_root,
        reviewed_snapshot=authority,
        run=run,
        evidence=evidence,
        staging=staging,
    )


def _bind(scenario: _RecordedScenario) -> ManagedRevisionPlanningAdmissionBinding:
    return bind_recorded_revision_planning_run(
        scenario.run,
        reviewed_snapshot=scenario.reviewed_snapshot,
        evidence_repository=scenario.evidence,
        staging_repository=scenario.staging,
    )


def _rebuild_planning_input(
    shard: RevisionPlanningInferenceShard,
    *,
    eligibility: RevisionPlanningEligibility,
    target: RevisionPlanningTarget,
    citation_inputs: RevisionPlanningCitationInputSet,
    source: ImpactInferenceShard,
) -> RevisionPlanningInferenceShard:
    return RevisionPlanningInferenceShard.create(
        eligibility=eligibility,
        run_id=shard.run_id,
        analysis_set_id=shard.analysis_set_id,
        analysis_set_sha256=shard.analysis_set_sha256,
        analysis_as_of=shard.analysis_as_of,
        target=target,
        predecessor=shard.predecessor,
        predecessor_raw_utf8=shard.predecessor_raw_utf8,
        predecessor_source_note_path=shard.predecessor_source_note_path,
        predecessor_source_note_utf8=shard.predecessor_source_note_utf8,
        citation_inputs=citation_inputs,
        existing_claim_revisions=source.target_claim_revisions,
    )


def _recreate_admission(
    binding: ManagedRevisionPlanningAdmissionBinding,
    **updates: object,
) -> ManagedRevisionPlanningAdmissionBinding:
    values: dict[str, object] = {
        "run_id": binding.run_id,
        "repository_id": binding.repository_id,
        "workload_id": binding.workload_id,
        "workload_sha256": binding.workload_sha256,
        "analysis_set": binding.analysis_set,
        "analysis_set_id": binding.analysis_set_id,
        "analysis_set_sha256": binding.analysis_set_sha256,
        "reviewed_snapshot_binding_id": binding.reviewed_snapshot_binding_id,
        "reviewed_snapshot_binding_sha256": binding.reviewed_snapshot_binding_sha256,
        "temporal_decision_record_sha256": binding.temporal_decision_record_sha256,
        "contract_binding_id": binding.contract_binding_id,
        "batch_id": binding.batch_id,
        "batch_sha256": binding.batch_sha256,
        "batch_members": binding.batch_members,
        "staging_manifest_id": binding.staging_manifest_id,
        "staging_manifest_sha256": binding.staging_manifest_sha256,
        "staging_manifest_path": binding.staging_manifest_path,
        "staging_completion_id": binding.staging_completion_id,
        "staging_completion_sha256": binding.staging_completion_sha256,
        "staging_completion_path": binding.staging_completion_path,
        "targets": binding.targets,
    }
    values.update(updates)
    return ManagedRevisionPlanningAdmissionBinding.create(**values)


def _rehashed_bootstrap_with_alternate_seed(
    bootstrap: AnalysisBootstrapBinding,
) -> AnalysisBootstrapBinding:
    """Create a valid but foreign bootstrap whose revision-2 head is unchanged."""

    payload = bootstrap.model_dump(mode="python")
    payload["seed_scenario_id"] = "larkstead-sl2-alternate-seed"
    provisional = AnalysisBootstrapBinding.model_construct(**payload)
    payload["canonical_input_sha256"] = hashlib.sha256(
        canonical_json_bytes(provisional._canonical_input_payload())
    ).hexdigest()
    rehashed = AnalysisBootstrapBinding.model_construct(**payload)
    digest = hashlib.sha256(
        canonical_json_bytes(rehashed._identity_payload())
    ).hexdigest()
    payload["binding_id"] = f"analysis-bootstrap:{digest}"
    payload["binding_sha256"] = digest
    return AnalysisBootstrapBinding.model_validate(payload)


def _recreate_governing_adoption(
    binding: ManagedGoverningSourceAdoptionBinding,
    **updates: object,
) -> ManagedGoverningSourceAdoptionBinding:
    excluded = {"adoption_id", "adoption_sha256", "source_repository_binding_sha256"}
    values = {
        name: getattr(binding, name)
        for name in type(binding).model_fields
        if name not in excluded
    }
    values.update(updates)
    return ManagedGoverningSourceAdoptionBinding.create(**values)


@pytest.mark.parametrize("selector", ("governing-evidence", "target-evidence"))
def test_admission_rejects_internally_consistent_forged_planning_citations(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    original = planning_inference.build_revision_planning_workload_from_impact_evidence

    def forged_builder(**kwargs: object) -> tuple[object, object, object]:
        exact, snapshots, sources = original(**kwargs)
        first = exact.input_shards[0]
        forged_inputs = RevisionPlanningCitationInputSet(
            inputs=tuple(
                RevisionPlanningCitationInput(
                    input_selector=item.input_selector,
                    role=item.role,
                    text_utf8=(
                        item.text_utf8 + " forged"
                        if item.input_selector == selector
                        else item.text_utf8
                    ),
                )
                for item in first.citation_inputs.inputs
            )
        )
        replacement = _rebuild_planning_input(
            first,
            eligibility=exact.eligibility,
            target=first.target,
            citation_inputs=forged_inputs,
            source=sources[first.target.target_key],
        )
        forged_workload = RevisionPlanningWorkload.create(
            eligibility=exact.eligibility,
            input_shards=(replacement, *exact.input_shards[1:]),
        )
        return forged_workload, snapshots, sources

    monkeypatch.setattr(
        planning_inference,
        "build_revision_planning_workload_from_impact_evidence",
        forged_builder,
    )
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )

    with pytest.raises(ValueError, match="exact Step-10 batch derivation"):
        _bind(scenario)


@pytest.mark.parametrize("mutation", ("required-kind", "question-ids"))
def test_admission_rejects_internally_consistent_forged_planning_target(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    original = planning_inference.build_revision_planning_workload_from_impact_evidence
    original_outputs = _revision_outputs

    def forged_eligibility(exact: RevisionPlanningEligibility) -> RevisionPlanningEligibility:
        index = next(
            index
            for index, target in enumerate(exact.targets)
            if target.required_response_kind == "affected-revision"
        )
        original_target = exact.targets[index]
        replacement: object
        if mutation == "required-kind":
            replacement = "no-change"
            field = "required_response_kind"
        else:
            replacement = next(
                item.question_ids for item in exact.targets if item != original_target
            )
            field = "question_ids"
        forged_target = RevisionPlanningTarget.model_validate_json(
            canonical_json_bytes(
                {
                    **original_target.model_dump(mode="json"),
                    field: replacement,
                }
            )
        )
        targets = list(exact.targets)
        targets[index] = forged_target
        return RevisionPlanningEligibility(
            status=exact.status,
            workload_id=exact.workload_id,
            workload_sha256=exact.workload_sha256,
            result_id=exact.result_id,
            result_sha256=exact.result_sha256,
            targets=tuple(targets),
        )

    def forged_builder(**kwargs: object) -> tuple[object, object, object]:
        exact, snapshots, sources = original(**kwargs)
        forged = forged_eligibility(exact.eligibility)
        targets = {item.target_key: item for item in forged.targets}
        inputs = tuple(
            _rebuild_planning_input(
                shard,
                eligibility=forged,
                target=targets[shard.target.target_key],
                citation_inputs=shard.citation_inputs,
                source=sources[shard.target.target_key],
            )
            for shard in exact.input_shards
        )
        forged_workload = RevisionPlanningWorkload.create(
            eligibility=forged,
            input_shards=inputs,
        )
        return forged_workload, snapshots, sources

    def forged_gate(results: ImpactResultSet) -> RevisionPlanningEligibility:
        return forged_eligibility(evaluate_revision_planning_eligibility(results))

    def forged_outputs(
        impact_run: RecordedImpactInferenceRun,
        snapshots: tuple[RevisionPlanningPredecessorSnapshot, ...],
    ) -> list[str]:
        outputs = original_outputs(impact_run, snapshots)
        exact = evaluate_revision_planning_eligibility(impact_run.results)
        forged = forged_eligibility(exact)
        sources = {
            item.target_note.document.document_id: item
            for item in impact_run.results.workload.input_shards
        }
        forged_outputs: list[str] = []
        for target, forged_target, output in zip(
            exact.targets,
            forged.targets,
            outputs,
            strict=True,
        ):
            if target.required_response_kind != "affected-revision":
                forged_outputs.append(output)
            elif mutation == "required-kind":
                forged_outputs.append(
                    _no_change_response(
                        target=forged_target,
                        source=sources[target.target_key],
                    )
                )
            else:
                payload = json.loads(output)
                payload["question_ids"] = list(forged_target.question_ids)
                forged_outputs.append(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return forged_outputs

    monkeypatch.setattr(
        planning_inference,
        "build_revision_planning_workload_from_impact_evidence",
        forged_builder,
    )
    monkeypatch.setattr(
        planning_inference,
        "evaluate_revision_planning_eligibility",
        forged_gate,
    )
    monkeypatch.setattr(sys.modules[__name__], "_revision_outputs", forged_outputs)
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )

    with pytest.raises(ValueError, match="exact Step-10 batch derivation"):
        _bind(scenario)


def _resolver(
    scenario: _RecordedScenario,
    binding: ManagedRevisionPlanningAdmissionBinding,
    *,
    governing_sources: tuple[ApprovedManagedGoverningSourceAuthority, ...] | None = None,
) -> RepositoryBackedManagedReviewResolver:
    contract = scenario.run.outcomes[0].execution.contract
    if governing_sources is None:
        bootstrap = binding.analysis_set.analysis_bootstrap
        adoption = derive_managed_governing_source_adoption(
            reviewed_snapshot=scenario.reviewed_snapshot,
            analysis_bootstrap=bootstrap,
            repo_root=scenario.canonical_root,
            manifest_path=scenario.canonical_root / MANIFEST_RELATIVE_PATH,
            evidence_repository_id=binding.repository_id,
        )
        governing_sources = (
            ApprovedManagedGoverningSourceAuthority(
                adoption=adoption,
                reviewed_snapshot=scenario.reviewed_snapshot,
                analysis_bootstrap=bootstrap,
            ),
        )
    return RepositoryBackedManagedReviewResolver(
        evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
        staging_repository=ManagedStagingRepository(scenario.root),
        canonical_root=scenario.canonical_root,
        approved_contracts=(
            ApprovedManagedInferenceContractAuthority(
                contract=contract,
                algorithm_manifest_bytes=ALGORITHM,
            ),
        ),
        revision_admissions=(binding,),
        governing_sources=governing_sources,
    )


def test_managed_run_v1_canonical_identity_is_frozen() -> None:
    run = _context().run
    canonical = canonical_json_bytes(run.model_dump(mode="json"))

    assert run.run_binding_id == (
        "mrun:e5f2fee256f6615a235f6e9117d9c1ba7b26f1ff3233165897e5c34af4324ff4"
    )
    assert hashlib.sha256(canonical).hexdigest() == (
        "fc68dff05d45bdc30cb45401722dbf7f92854d621926fd5eb550255e819d1242"
    )
    assert b'"schema_version":1' in canonical
    assert b"revision_planning_admission" not in canonical


def test_eligible_run_retains_analysis_and_binds_after_fresh_process_reopen(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )

    assert scenario.run.analysis_set is not None
    binding = _bind(scenario)
    fresh = reopen_revision_planning_admission(
        binding,
        reviewed_snapshot=scenario.reviewed_snapshot,
        evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
        staging_repository=ManagedStagingRepository(scenario.root),
    )
    assert scenario.run.analysis_set is not None
    assert fresh == binding
    assert tuple(item.target_key for item in binding.targets) == tuple(
        item.target_key for item in scenario.run.subjects
    )


def test_admission_rejects_another_verified_reviewed_authority(
    exact_impact_fixture: _ExactImpactFixture,
    authority_variants: _AuthorityVariants,
    tmp_path: Path,
) -> None:
    scenario = _recorded_scenario(
        authority=exact_impact_fixture.authority,
        workload=exact_impact_fixture.workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    with pytest.raises(ValueError, match="differs from reviewed authority"):
        bind_recorded_revision_planning_run(
            scenario.run,
            reviewed_snapshot=authority_variants.edited_claim,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )
    binding = _bind(scenario)
    with pytest.raises(ValueError, match="differs from reviewed authority"):
        reopen_revision_planning_admission(
            binding,
            reviewed_snapshot=authority_variants.edited_claim,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )


def test_admission_rejects_reviewed_identity_and_step10_index_substitution(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    scenario = _recorded_scenario(
        authority=exact_impact_fixture.authority,
        workload=exact_impact_fixture.workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    forged_lineages = (
        _recreate_admission(
            binding,
            reviewed_snapshot_binding_id="reviewed-snapshot:" + "f" * 64,
            reviewed_snapshot_binding_sha256="f" * 64,
        ),
        _recreate_admission(
            binding,
            temporal_decision_record_sha256="f" * 64,
        ),
    )
    for forged in forged_lineages:
        with pytest.raises(ValueError, match="names another reviewed authority"):
            reopen_revision_planning_admission(
                forged,
                reviewed_snapshot=scenario.reviewed_snapshot,
                evidence_repository=scenario.evidence,
                staging_repository=scenario.staging,
            )

    analysis = binding.analysis_set
    assert analysis.impact_evidence is not None
    for field in (
        "candidate_result_sha256",
        "classification_result_sha256",
        "attention_result_sha256",
    ):
        values = {
            "candidate_result_sha256": analysis.candidate_result_sha256,
            "classification_result_sha256": analysis.classification_result_sha256,
            "attention_result_sha256": analysis.attention_result_sha256,
        }
        values[field] = "f" * 64
        forged_analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
            analysis_bootstrap=analysis.analysis_bootstrap,
            candidate_result_sha256=values["candidate_result_sha256"],
            classification_result_sha256=values["classification_result_sha256"],
            attention_result_sha256=values["attention_result_sha256"],
            impact_evidence=analysis.impact_evidence,
            global_relevant_claim_revision_ids=analysis.global_relevant_claim_revision_ids,
        )
        forged = _recreate_admission(
            binding,
            analysis_set=forged_analysis,
            analysis_set_id=forged_analysis.analysis_set_id,
            analysis_set_sha256=forged_analysis.analysis_set_sha256,
        )
        with pytest.raises(ValueError, match="differs from reviewed authority"):
            reopen_revision_planning_admission(
                forged,
                reviewed_snapshot=scenario.reviewed_snapshot,
                evidence_repository=scenario.evidence,
                staging_repository=scenario.staging,
            )

    foreign_claim_id = "claimrev:" + "f" * 64
    assert foreign_claim_id not in analysis.global_relevant_claim_revision_ids
    forged_relevant_claims = tuple(
        sorted((*analysis.global_relevant_claim_revision_ids, foreign_claim_id))
    )
    forged_relevance = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=analysis.analysis_bootstrap,
        candidate_result_sha256=analysis.candidate_result_sha256,
        classification_result_sha256=analysis.classification_result_sha256,
        attention_result_sha256=analysis.attention_result_sha256,
        impact_evidence=analysis.impact_evidence,
        global_relevant_claim_revision_ids=forged_relevant_claims,
    )
    forged_relevance_binding = _recreate_admission(
        binding,
        analysis_set=forged_relevance,
        analysis_set_id=forged_relevance.analysis_set_id,
        analysis_set_sha256=forged_relevance.analysis_set_sha256,
    )
    with pytest.raises(ValueError, match="differs from reviewed authority"):
        reopen_revision_planning_admission(
            forged_relevance_binding,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )

    forged_bootstrap = _rehashed_bootstrap_with_alternate_seed(
        analysis.analysis_bootstrap
    )
    assert forged_bootstrap.analysis_aggregate_sha256 == (
        analysis.analysis_bootstrap.analysis_aggregate_sha256
    )
    forged_bootstrap_analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=forged_bootstrap,
        candidate_result_sha256=analysis.candidate_result_sha256,
        classification_result_sha256=analysis.classification_result_sha256,
        attention_result_sha256=analysis.attention_result_sha256,
        impact_evidence=analysis.impact_evidence,
        global_relevant_claim_revision_ids=analysis.global_relevant_claim_revision_ids,
    )
    forged_bootstrap_binding = _recreate_admission(
        binding,
        analysis_set=forged_bootstrap_analysis,
        analysis_set_id=forged_bootstrap_analysis.analysis_set_id,
        analysis_set_sha256=forged_bootstrap_analysis.analysis_set_sha256,
    )
    with pytest.raises(ValueError, match="differs from reviewed authority"):
        reopen_revision_planning_admission(
            forged_bootstrap_binding,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )


def test_no_work_run_has_no_analysis_and_cannot_be_admitted(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    full_workload = exact_impact_fixture.workload
    workload = _subworkload(full_workload, target_keys=())
    impact_run = RecordedImpactInferenceRun(
        results=ImpactResultSet.create(workload=workload, decisions=()),
        outcomes=(),
        evidence_batch=None,
    )
    root = tmp_path / "no-work"
    run = execute_revision_planning(
        run_id="m4-no-work-admission",
        impact_run=impact_run,
        predecessor_snapshots=(),
        contract=_contract(InferenceExecutionMode.LIVE),
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        evidence_repository=FilesystemInferenceEvidenceRepository(root),
        staging_repository=ManagedStagingRepository(root),
    )

    assert run.analysis_set is None
    with pytest.raises(ValueError, match="NO_WORK"):
        bind_recorded_revision_planning_run(
            run,
            reviewed_snapshot=authority,
            evidence_repository=FilesystemInferenceEvidenceRepository(root),
            staging_repository=ManagedStagingRepository(root),
        )


@pytest.mark.parametrize("tamper", ("receipt", "completion", "member"))
def test_fresh_bind_rejects_tampered_or_incomplete_durable_evidence(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
    tamper: str,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    if tamper == "receipt":
        path = scenario.root / scenario.run.outcomes[0].execution.receipt_artifact.path
    elif tamper == "completion":
        assert scenario.run.staging_completion is not None
        path = scenario.root / scenario.run.staging_completion.completion_path
    else:
        subject = scenario.run.subjects[0]
        path = scenario.root / subject.validated_output.path
    path.write_bytes(b"{}")

    with pytest.raises((InferenceEvidenceResolutionError, ValueError)):
        _bind(scenario)


def test_admission_rejects_wrong_repository_batch_and_target_coverage(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    other_root = tmp_path / "other-repository"
    with pytest.raises(ValueError, match="another repository"):
        bind_recorded_revision_planning_run(
            scenario.run,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=FilesystemInferenceEvidenceRepository(other_root),
            staging_repository=ManagedStagingRepository(other_root),
        )


def test_reopen_revalidates_admission_and_rejects_forged_analysis_or_subject(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)

    invalid_id = "mrevisionadmission:" + "0" * 64
    forged_instances = (
        binding.model_copy(update={"admission_id": invalid_id}),
        ManagedRevisionPlanningAdmissionBinding.model_construct(
            **{
                field: getattr(binding, field)
                for field in ManagedRevisionPlanningAdmissionBinding.model_fields
                if field != "admission_id"
            },
            admission_id=invalid_id,
        ),
    )
    for forged in forged_instances:
        with pytest.raises(ValueError):
            reopen_revision_planning_admission(
                forged,
                reviewed_snapshot=scenario.reviewed_snapshot,
                evidence_repository=scenario.evidence,
                staging_repository=scenario.staging,
            )

    analysis = binding.analysis_set
    assert analysis.impact_evidence is not None
    forged_analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=analysis.analysis_bootstrap,
        candidate_result_sha256=analysis.candidate_result_sha256,
        classification_result_sha256=analysis.classification_result_sha256,
        attention_result_sha256="f" * 64,
        impact_evidence=analysis.impact_evidence,
        global_relevant_claim_revision_ids=analysis.global_relevant_claim_revision_ids,
    )
    forged_analysis_binding = _recreate_admission(
        binding,
        analysis_set=forged_analysis,
        analysis_set_id=forged_analysis.analysis_set_id,
        analysis_set_sha256=forged_analysis.analysis_set_sha256,
    )
    with pytest.raises(ValueError, match="differs from reviewed authority"):
        reopen_revision_planning_admission(
            forged_analysis_binding,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )

    target = binding.targets[0]
    forged_sha = "f" * 64
    prefix = "mplan" if target.subject_kind == "managed-revision-plan" else "mnochange"
    forged_target = target.model_copy(
        update={"subject_id": f"{prefix}:{forged_sha}", "subject_sha256": forged_sha}
    )
    forged_subject_binding = _recreate_admission(
        binding,
        targets=(forged_target, *binding.targets[1:]),
    )
    with pytest.raises(ValueError, match="subject differs"):
        reopen_revision_planning_admission(
            forged_subject_binding,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )

    analysis = scenario.run.analysis_set
    assert analysis is not None and analysis.impact_evidence is not None
    impact = analysis.impact_evidence
    wrong_members = tuple(
        ManagedRevisionPlanningBatchMemberBinding(
            execution_id=item.execution_id,
            receipt_artifact_id=item.receipt_artifact_id,
            outcome_sha256=item.outcome_sha256,
        )
        for item in impact.batch_members
    )
    wrong_batch = binding.model_copy(
        update={
            "batch_id": impact.batch_id,
            "batch_sha256": impact.batch_sha256,
            "batch_members": wrong_members,
        }
    )
    with pytest.raises(ValueError):
        reopen_revision_planning_admission(
            wrong_batch,
            reviewed_snapshot=scenario.reviewed_snapshot,
            evidence_repository=scenario.evidence,
            staging_repository=scenario.staging,
        )

    with pytest.raises(ValueError, match="exactly cover"):
        ManagedRevisionPlanningAdmissionBinding.create(
            run_id=binding.run_id,
            repository_id=binding.repository_id,
            workload_id=binding.workload_id,
            workload_sha256=binding.workload_sha256,
            analysis_set=binding.analysis_set,
            analysis_set_id=binding.analysis_set_id,
            analysis_set_sha256=binding.analysis_set_sha256,
            reviewed_snapshot_binding_id=binding.reviewed_snapshot_binding_id,
            reviewed_snapshot_binding_sha256=binding.reviewed_snapshot_binding_sha256,
            temporal_decision_record_sha256=binding.temporal_decision_record_sha256,
            contract_binding_id=binding.contract_binding_id,
            batch_id=binding.batch_id,
            batch_sha256=binding.batch_sha256,
            batch_members=binding.batch_members,
            staging_manifest_id=binding.staging_manifest_id,
            staging_manifest_sha256=binding.staging_manifest_sha256,
            staging_manifest_path=binding.staging_manifest_path,
            staging_completion_id=binding.staging_completion_id,
            staging_completion_sha256=binding.staging_completion_sha256,
            staging_completion_path=binding.staging_completion_path,
            targets=binding.targets[:-1],
        )


def test_repository_resolver_reopens_contract_impact_patch_and_projections(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    resolver = _resolver(scenario, binding)
    contract = scenario.run.outcomes[0].execution.contract
    analysis = scenario.run.analysis_set
    assert analysis is not None and analysis.impact_evidence is not None

    assert resolver.open_algorithm_manifest(contract) == ALGORITHM
    assert resolver.resolve_approved_inference_contract(contract) == contract
    assert resolver.resolve_revision_planning_admission(binding) == binding
    assert resolver.resolve_impact_analysis_evidence(analysis.impact_evidence) == (
        analysis.impact_evidence
    )
    plan = next(item for item in scenario.run.subjects if isinstance(item, ManagedRevisionPlan))
    base = resolver.open_artifact(plan.predecessor_raw)
    proposed = resolver.open_artifact(plan.proposed_raw)
    predecessor_note = resolver.open_artifact(plan.predecessor_note)
    proposed_note = resolver.open_artifact(plan.proposed_note)
    assert (
        resolver.verify_patch_reconstruction(
            plan,
            base_bytes=base,
            result_bytes=proposed,
        )
        == plan.patch_attestation
    )
    assert (
        resolver.verify_source_note_projection(
            plan.predecessor_projection,
            raw_bytes=base,
            note_bytes=predecessor_note,
        )
        == plan.predecessor_projection
    )
    assert (
        resolver.verify_source_note_projection(
            plan.successor_projection,
            raw_bytes=proposed,
            note_bytes=proposed_note,
        )
        == plan.successor_projection
    )
    assert (
        resolver.verify_revision_plan_source_note(
            plan,
            predecessor_note_bytes=predecessor_note,
            result_raw_bytes=proposed,
            proposed_note_bytes=proposed_note,
        )
        == plan.successor_projection
    )


def test_production_resolver_requires_governing_allowlist_and_rebuilds_after_restart(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    scenario = _recorded_scenario(
        authority=exact_impact_fixture.authority,
        workload=exact_impact_fixture.workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    contract = scenario.run.outcomes[0].execution.contract
    with pytest.raises(ValueError, match="approved reviewed-source authority"):
        RepositoryBackedManagedReviewResolver(
            evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
            staging_repository=ManagedStagingRepository(scenario.root),
            canonical_root=scenario.canonical_root,
            approved_contracts=(
                ApprovedManagedInferenceContractAuthority(
                    contract=contract,
                    algorithm_manifest_bytes=ALGORITHM,
                ),
            ),
            revision_admissions=(binding,),
            governing_sources=(),
        )

    restarted_store = SqliteChangeControlStore(exact_impact_fixture.authority_path)
    try:
        reminted = resolve_reviewed_temporal_snapshot(
            restarted_store,
            temporal_analysis_manifest_id=(
                exact_impact_fixture.temporal_analysis_manifest_id
            ),
            temporal_analysis_manifest_sha256=(
                exact_impact_fixture.temporal_analysis_manifest_sha256
            ),
            temporal_request_id=exact_impact_fixture.temporal_request_id,
            evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
            source_note_resolver=exact_impact_fixture.source_note_resolver,
        )
    finally:
        restarted_store.close()
    assert reminted is not exact_impact_fixture.authority
    assert reminted.binding == exact_impact_fixture.authority.binding
    bootstrap = binding.analysis_set.analysis_bootstrap
    adoption = derive_managed_governing_source_adoption(
        reviewed_snapshot=reminted,
        analysis_bootstrap=bootstrap,
        repo_root=scenario.canonical_root,
        manifest_path=scenario.canonical_root / MANIFEST_RELATIVE_PATH,
        evidence_repository_id=binding.repository_id,
    )
    restarted = RepositoryBackedManagedReviewResolver(
        evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
        staging_repository=ManagedStagingRepository(scenario.root),
        canonical_root=scenario.canonical_root,
        approved_contracts=(
            ApprovedManagedInferenceContractAuthority(
                contract=contract,
                algorithm_manifest_bytes=ALGORITHM,
            ),
        ),
        revision_admissions=(binding,),
        governing_sources=(
            ApprovedManagedGoverningSourceAuthority(
                adoption=adoption,
                reviewed_snapshot=reminted,
                analysis_bootstrap=bootstrap,
            ),
        ),
    )
    assert restarted.resolve_revision_planning_admission(binding) == binding
    assert restarted.resolve_governing_source_adoption(adoption) == adoption


def test_production_resolver_rejects_valid_but_wrong_governing_authority(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    scenario = _recorded_scenario(
        authority=exact_impact_fixture.authority,
        workload=exact_impact_fixture.workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    bootstrap = binding.analysis_set.analysis_bootstrap
    adoption = derive_managed_governing_source_adoption(
        reviewed_snapshot=scenario.reviewed_snapshot,
        analysis_bootstrap=bootstrap,
        repo_root=scenario.canonical_root,
        manifest_path=scenario.canonical_root / MANIFEST_RELATIVE_PATH,
        evidence_repository_id=binding.repository_id,
    )
    foreign_note = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.SOURCE_NOTE,
        path="datasets/larkstead/processed/foreign-governing-note.md",
        sha256=adoption.source_note_artifact.sha256,
        byte_count=adoption.source_note_artifact.byte_count,
    )
    mutations: tuple[dict[str, object], ...] = (
        {"evidence_repository_id": "f" * 64},
        {"incoming_logical_event_id": "foreign-reviewed-event"},
        {"incoming_manifest_sha256": "f" * 64},
        {
            "reviewed_snapshot_binding_id": "reviewed-snapshot:" + "f" * 64,
            "reviewed_snapshot_binding_sha256": "f" * 64,
        },
        {
            "source_note_logical_path": "foreign-governing-note.md",
            "source_note_artifact": foreign_note,
        },
    )
    for updates in mutations:
        foreign = _recreate_governing_adoption(adoption, **updates)
        with pytest.raises(ValueError):
            RepositoryBackedManagedReviewResolver(
                evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
                staging_repository=ManagedStagingRepository(scenario.root),
                canonical_root=scenario.canonical_root,
                approved_contracts=(
                    ApprovedManagedInferenceContractAuthority(
                        contract=scenario.run.outcomes[0].execution.contract,
                        algorithm_manifest_bytes=ALGORITHM,
                    ),
                ),
                revision_admissions=(binding,),
                governing_sources=(
                    ApprovedManagedGoverningSourceAuthority(
                        adoption=foreign,
                        reviewed_snapshot=scenario.reviewed_snapshot,
                        analysis_bootstrap=bootstrap,
                    ),
                ),
            )


def test_resolver_rejects_unapproved_contract_and_patch_projection_tamper(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    resolver = _resolver(scenario, binding)
    contract = scenario.run.outcomes[0].execution.contract
    unapproved = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=contract.algorithm_manifest_sha256,
        contract_id=contract.contract_id,
        contract_version=contract.contract_version + 1,
        mode=contract.mode,
        provider=contract.provider,
        model=contract.model,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
    )
    with pytest.raises(ValueError, match="not operator-approved"):
        resolver.resolve_approved_inference_contract(unapproved)

    plan = next(item for item in scenario.run.subjects if isinstance(item, ManagedRevisionPlan))
    base = resolver.open_artifact(plan.predecessor_raw)
    proposed = resolver.open_artifact(plan.proposed_raw)
    proposed_note = resolver.open_artifact(plan.proposed_note)
    with pytest.raises(ValueError, match="do not reconstruct"):
        resolver.verify_patch_reconstruction(
            plan,
            base_bytes=base,
            result_bytes=proposed + b"x",
        )
    with pytest.raises(ValueError, match="artifact receipt"):
        resolver.verify_source_note_projection(
            plan.successor_projection,
            raw_bytes=proposed,
            note_bytes=proposed_note + b"x",
        )
    title_offset = proposed_note.index(b"\n# ") + len(b"\n# ")
    tampered_title = bytearray(proposed_note)
    tampered_title[title_offset] = (
        ord("Z") if tampered_title[title_offset] != ord("Z") else ord("Y")
    )
    with pytest.raises(ValueError, match="deterministic PR13 rendering"):
        resolver.verify_revision_plan_source_note(
            plan,
            predecessor_note_bytes=resolver.open_artifact(plan.predecessor_note),
            result_raw_bytes=proposed,
            proposed_note_bytes=bytes(tampered_title),
        )


def test_contract_authority_revalidates_identity_and_impact_requires_approval(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    binding = _bind(scenario)
    contract = scenario.run.outcomes[0].execution.contract
    invalid = contract.model_copy(update={"contract_binding_id": "mcontract:" + "0" * 64})
    with pytest.raises(ValueError):
        ApprovedManagedInferenceContractAuthority(
            contract=invalid,
            algorithm_manifest_bytes=ALGORITHM,
        )

    other_algorithm = b'{"algorithm":"other"}'
    other = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=hashlib.sha256(other_algorithm).hexdigest(),
        contract_id="managed-review-other",
        contract_version=1,
        mode=InferenceExecutionMode.LIVE,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
    )
    resolver = RepositoryBackedManagedReviewResolver(
        evidence_repository=FilesystemInferenceEvidenceRepository(scenario.root),
        staging_repository=ManagedStagingRepository(scenario.root),
        canonical_root=scenario.canonical_root,
        approved_contracts=(
            ApprovedManagedInferenceContractAuthority(
                contract=other,
                algorithm_manifest_bytes=other_algorithm,
            ),
        ),
        revision_admissions=(),
    )
    analysis = binding.analysis_set
    assert analysis.impact_evidence is not None
    with pytest.raises(ValueError, match="not operator-approved"):
        resolver.resolve_impact_analysis_evidence(analysis.impact_evidence)


def test_public_repository_helpers_reject_cross_boundary_artifacts(
    exact_impact_fixture: _ExactImpactFixture,
    tmp_path: Path,
) -> None:
    authority = exact_impact_fixture.authority
    workload = exact_impact_fixture.workload
    scenario = _recorded_scenario(
        authority=authority,
        workload=workload,
        tmp_path=tmp_path,
        repository_root=exact_impact_fixture.repository_root,
    )
    subject = scenario.run.subjects[0]
    assert scenario.run.staging_completion is not None
    with pytest.raises(InferenceEvidenceResolutionError, match="rejects"):
        scenario.evidence.open_artifact(subject.validated_output)
    inference_artifact = scenario.run.outcomes[0].execution.receipt_artifact
    with pytest.raises(ValueError, match="not an exact completed-manifest member"):
        scenario.staging.open_member(
            completion=scenario.run.staging_completion,
            artifact=inference_artifact,
        )
    unsafe = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path="vault/not-an-inference-artifact.md",
        sha256="0" * 64,
        byte_count=1,
    )
    with pytest.raises(InferenceEvidenceResolutionError, match="rejects"):
        scenario.evidence.open_artifact(unsafe)
    wrong_kind = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=inference_artifact.path,
        sha256=inference_artifact.sha256,
        byte_count=inference_artifact.byte_count,
    )
    with pytest.raises(InferenceEvidenceResolutionError, match="rejects"):
        scenario.evidence.open_artifact(wrong_kind)

    binding = _bind(scenario)
    resolver = _resolver(scenario, binding)
    algorithm = scenario.run.outcomes[0].execution.input_envelope.input_artifacts
    algorithm_ref = next(
        item for item in algorithm if item.path.startswith("inference/algorithms/")
    )
    inverse_source = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=algorithm_ref.path,
        sha256=algorithm_ref.sha256,
        byte_count=algorithm_ref.byte_count,
    )
    with pytest.raises(ValueError, match="reserved roots"):
        resolver.open_artifact(inverse_source)
    inverse_staging = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_RECEIPT,
        path=subject.validated_output.path,
        sha256=subject.validated_output.sha256,
        byte_count=subject.validated_output.byte_count,
    )
    with pytest.raises(ValueError, match="unsupported kind"):
        resolver.open_artifact(inverse_staging)
