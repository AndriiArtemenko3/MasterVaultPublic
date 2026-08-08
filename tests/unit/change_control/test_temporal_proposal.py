from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from mastervault.change_control.bootstrap import bootstrap_analysis_aggregate
from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationResultSet,
    GraphMaterializationStatus,
    select_classification_workload,
)
from mastervault.change_control.dependency_analysis import (
    DependencyClassification,
    DependencyClassificationResultSet,
    generate_dependency_workload,
)
from mastervault.change_control.discovery import generate_relationship_candidates
from mastervault.change_control.incoming import MANIFEST_RELATIVE_PATH
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    InferenceUsage,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import (
    DocumentAuthority,
    DocumentReplacementAssessment,
    DocumentRole,
    DocumentVersionMetadata,
    PairDisposition,
    TemporalConstraint,
    TemporalConstraintStatus,
    canonical_json_bytes,
)
from mastervault.change_control.recorded_inference import (
    InferenceProviderRequest,
    ProviderCallResult,
    RecordedInferenceOutcome,
    run_classification_inference,
    run_dependency_inference,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    HumanReviewDecisionReceipt,
    HumanReviewRequestReceipt,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectEdit,
    ReviewSubjectKind,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
)
from mastervault.change_control.store import (
    ChangeControlReviewStaleError,
    SqliteChangeControlStore,
)
from mastervault.change_control.temporal_analysis import (
    TemporalAnalysisEvidence,
    build_temporal_analysis_evidence,
)
from mastervault.change_control.temporal_commit import (
    TemporalProposalAuthorityError,
    commit_temporal_proposal,
)
from mastervault.change_control.temporal_proposal import (
    DocumentReplacementProposalCandidate,
    TemporalProposal,
    build_temporal_proposal,
    open_temporal_review,
    temporal_prerequisite_from_decision,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / MANIFEST_RELATIVE_PATH
ALGORITHM = b'{"algorithm":"temporal-proposal-test-v1"}'
PROMPT = b"Return one exact decision per supplied identifier."
SCHEMA = b'{"type":"object","additionalProperties":false}'


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contract() -> ManagedInferenceContractBinding:
    return ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=_sha256(ALGORITHM),
        contract_id="temporal-proposal-test-v1",
        contract_version=1,
        mode=InferenceExecutionMode.LIVE,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=_sha256(PROMPT),
        response_schema_sha256=_sha256(SCHEMA),
    )


@dataclass
class _Provider:
    output: str
    request_id: str
    provider: str = "fixture-provider"
    model: str = "fixture-model"

    def complete(self, *, request: bytes) -> ProviderCallResult:
        InferenceProviderRequest.model_validate_json(request)
        return ProviderCallResult(
            provider=self.provider,
            model=self.model,
            provider_request_id=self.request_id,
            raw_output_utf8=self.output,
            usage=InferenceUsage(
                input_tokens=10,
                output_tokens=10,
                cached_input_tokens=0,
                cost_usd_micros=1,
                latency_ms=1,
            ),
        )


@dataclass(frozen=True)
class _Case:
    store: SqliteChangeControlStore
    evidence_repository: FilesystemInferenceEvidenceRepository
    classification_batch: RepositoryVerifiedInferenceEvidenceBatch
    dependency_batch: RepositoryVerifiedInferenceEvidenceBatch
    proposal: TemporalProposal
    classification_outcomes: tuple[RecordedInferenceOutcome, ...]
    build_inputs: dict[str, Any]


def _classification_wire(shard: object, supersession_pair_id: str) -> str:
    decisions = []
    for item in shard.pairs:  # type: ignore[attr-defined]
        endpoints = {revision.claim_revision_id: revision for revision in item.endpoint_revisions}
        changed = endpoints[item.candidate.changed_claim_revision_id]
        supersedes = item.candidate.pair_id == supersession_pair_id
        decisions.append(
            {
                "pair_id": item.candidate.pair_id,
                "disposition": "SUPERSEDES" if supersedes else "UNRELATED",
                "newer_revision_id": changed.claim_revision_id if supersedes else None,
                "rationale": (
                    "The incoming returns policy replaces the matching prior policy claim."
                    if supersedes
                    else "The claims do not express a persisted semantic relation."
                ),
                "confidence": 0.95,
            }
        )
    return json.dumps(
        {"schema_version": 1, "task": "classification", "decisions": decisions},
        separators=(",", ":"),
        sort_keys=True,
    )


def _dependency_wire(shard: object, positive_candidate_id: str) -> str:
    text = shard.downstream_note.source_note_utf8  # type: ignore[attr-defined]
    start = shard.downstream_note.body_start_char  # type: ignore[attr-defined]
    while start < len(text) and text[start].isspace():
        start += 1
    end = min(start + 40, len(text))

    def decision(item: object) -> dict[str, Any]:
        positive = item.candidate_id == positive_candidate_id  # type: ignore[attr-defined]
        return {
            "candidate_id": item.candidate_id,  # type: ignore[attr-defined]
            "disposition": "DEPENDS_ON" if positive else "NOT_DEPENDENT",
            "dependency_kind": "summarizes" if positive else None,
            "selected_downstream_claim_revision_ids": [],
            "spans": [{"start_char": start, "end_char": end}] if positive else [],
            "rationale": (
                "The downstream note summarizes the governing policy claim."
                if positive
                else "No exact downstream dependency is supported by this note."
            ),
            "confidence": 0.9,
        }

    return json.dumps(
        {
            "schema_version": 1,
            "task": "dependency",
            "decisions": [decision(item) for item in shard.candidates],  # type: ignore[attr-defined]
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_case(tmp_path: Path) -> _Case:
    store = SqliteChangeControlStore(tmp_path / "change-control.sqlite3")
    store.init_schema()
    bootstrap = bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id="temporal-test:prechange",
        analysis_operation_id="temporal-test:analysis",
    )
    snapshot = bootstrap.snapshot
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=bootstrap.binding.changed_claim_revision_ids,
        as_of=bootstrap.binding.analysis_as_of,
    )
    classification_workload = select_classification_workload(snapshot, candidates=candidates)
    supersession_pair_id = next(
        item.candidate.pair_id
        for shard in classification_workload.inference_shards
        for item in shard.pairs
        if next(
            revision
            for revision in item.endpoint_revisions
            if revision.claim_revision_id == item.candidate.changed_claim_revision_id
        ).document.document_id
        == "sl2-policy-returns-v2"
        and next(
            revision
            for revision in item.endpoint_revisions
            if revision.claim_revision_id == item.candidate.incumbent_claim_revision_id
        ).document.document_id
        == "sl2-policy-returns-v1"
    )
    contract = _contract()
    classification_outcomes = tuple(
        run_classification_inference(
            contract=contract,
            workload=classification_workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=_Provider(
                _classification_wire(shard, supersession_pair_id),
                f"fixture:classification-{index}",
            ),
        )
        for index, shard in enumerate(classification_workload.inference_shards, start=1)
    )
    evidence_repository = FilesystemInferenceEvidenceRepository(tmp_path / "temporal-evidence")
    classification_batch = evidence_repository.persist_batch(classification_outcomes)
    classification_items: list[ClaimPairClassification] = []
    for outcome in classification_outcomes:
        assert outcome.classification_output is not None
        classification_items.extend(
            item.classification for item in outcome.classification_output.items
        )
    classification_results = ClassificationResultSet.create(
        workload=classification_workload,
        classifications=tuple(classification_items),
    )
    supporting = tuple(
        item
        for item in classification_results.classifications
        if item.disposition == PairDisposition.SUPERSEDES
        and item.materialization_status == GraphMaterializationStatus.GRAPH_VALID
    )
    assert supporting
    incoming_document = bootstrap.incoming_event.document
    older_document = next(
        item
        for item in snapshot.aggregate.documents.documents
        if item.document_id == "sl2-policy-returns-v1"
    )
    replacement_candidate = DocumentReplacementProposalCandidate.create(
        newer_document=incoming_document,
        older_document=older_document,
        supporting_classifications=supporting,
        rationale="The incoming returns policy replaces the prior policy version.",
        confidence=0.95,
    )

    inventory_resolver = RepositorySourceNoteInventoryResolver(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        verified_bootstrap=bootstrap.verification_capability,
    )
    inventory_capability = inventory_resolver.resolve_source_note_inventory(snapshot=snapshot)
    dependency_workload = generate_dependency_workload(
        snapshot,
        candidates=candidates,
        classification_results=classification_results,
        inventory_capability=inventory_capability,
    )
    positive_candidate_id = dependency_workload.input_shards[0].candidates[0].candidate_id
    dependency_outcomes = tuple(
        run_dependency_inference(
            contract=contract,
            workload=dependency_workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=_Provider(
                _dependency_wire(shard, positive_candidate_id),
                f"fixture:dependency-{index}",
            ),
        )
        for index, shard in enumerate(dependency_workload.input_shards, start=1)
    )
    dependency_batch = evidence_repository.persist_batch(dependency_outcomes)
    dependency_classifications: list[DependencyClassification] = []
    for outcome in dependency_outcomes:
        assert outcome.dependency_output is not None
        dependency_classifications.extend(outcome.dependency_output.classifications)
    dependency_results = DependencyClassificationResultSet.create(
        workload=dependency_workload,
        classifications=tuple(dependency_classifications),
    )
    build_inputs: dict[str, Any] = {
        "verified_bootstrap": bootstrap.verification_capability,
        "snapshot": snapshot,
        "candidates": candidates,
        "classification_results": classification_results,
        "classification_outcomes": classification_outcomes,
        "classification_evidence_batch_id": classification_batch.batch_id,
        "classification_evidence_batch_sha256": classification_batch.batch_sha256,
        "inventory_capability": inventory_capability,
        "dependency_workload": dependency_workload,
        "dependency_results": dependency_results,
        "dependency_outcomes": dependency_outcomes,
        "dependency_evidence_batch_id": dependency_batch.batch_id,
        "dependency_evidence_batch_sha256": dependency_batch.batch_sha256,
        "replacement_candidate": replacement_candidate,
    }
    proposal = build_temporal_proposal(**build_inputs)
    build_inputs["inventory_resolver"] = inventory_resolver
    return _Case(
        store=store,
        evidence_repository=evidence_repository,
        classification_batch=classification_batch,
        dependency_batch=dependency_batch,
        proposal=proposal,
        classification_outcomes=classification_outcomes,
        build_inputs=build_inputs,
    )


def _build_temporal_evidence(case: _Case) -> TemporalAnalysisEvidence:
    values = case.build_inputs
    return build_temporal_analysis_evidence(
        verified_bootstrap=values["verified_bootstrap"],
        snapshot=values["snapshot"],
        candidates=values["candidates"],
        classification_results=values["classification_results"],
        inventory_capability=values["inventory_capability"],
        dependency_workload=values["dependency_workload"],
        dependency_results=values["dependency_results"],
        replacement_candidate=values["replacement_candidate"],
        proposal=case.proposal,
    )


def _commit(case: _Case):
    values = case.build_inputs
    return commit_temporal_proposal(
        case.store,
        case.proposal,
        temporal_analysis=_build_temporal_evidence(case),
        evidence_repository=case.evidence_repository,
        classification_batch=case.classification_batch,
        dependency_batch=case.dependency_batch,
        source_note_resolver=values["inventory_resolver"],
    )


def test_temporal_proposal_commits_exact_revision_three_and_reviews_every_subject(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    try:
        proposal = case.proposal
        assert proposal.proposed_aggregate.document_replacements.assessments[0].status == (
            TemporalConstraintStatus.PROPOSED
        )
        assert all(
            item.status == TemporalConstraintStatus.PROPOSED
            for item in proposal.proposed_aggregate.temporal_constraints.constraints
        )
        assert (
            proposal.binding.classification_evidence_batch_id == case.classification_batch.batch_id
        )
        assert proposal.binding.dependency_evidence_batch_id == case.dependency_batch.batch_id
        assert len(proposal.proposed_aggregate.dependencies.assessments) == 1

        commit = _commit(case)
        replay = _commit(case)
        assert commit.revision == 3
        assert replay.replayed
        assert commit.operation_id == (
            f"temporal-commit:{commit.temporal_analysis_manifest_sha256}"
        )
        assert case.store.load(commit.aggregate_id).aggregate_sha256 == (  # type: ignore[union-attr]
            proposal.binding.proposed_aggregate_sha256
        )

        request = open_temporal_review(
            case.store,
            commit,
            requester_id="review.requester",
            rationale="Review the exact temporal proposal atomically.",
            operation_id="temporal-test:review-request",
        )
        request_replay = open_temporal_review(
            case.store,
            commit,
            requester_id="review.requester",
            rationale="Review the exact temporal proposal atomically.",
            operation_id="temporal-test:review-request",
        )
        assert request_replay.replayed
        assert len(request.request.subjects) == len(proposal.review_subjects)
        rejected_constraint_id = next(
            item.subject_id
            for item in request.request.subjects
            if item.kind == ReviewSubjectKind.TEMPORAL_CONSTRAINT
        )
        decision_command = HumanReviewDecisionCommand(
            request_id=request.request.request_id,
            reviewer_id="review.approver",
            rationale="The complete temporal proposal was adjudicated as one mixed decision.",
            items=tuple(
                ReviewDecisionItem(
                    kind=item.kind,
                    subject_id=item.subject_id,
                    original_subject_sha256=item.subject_sha256,
                    disposition=(
                        ReviewDisposition.EDITED
                        if item.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT
                        else (
                            ReviewDisposition.REJECTED
                            if item.subject_id == rejected_constraint_id
                            else ReviewDisposition.ACCEPTED
                        )
                    ),
                    edit=(
                        ReviewSubjectEdit(
                            kind=item.kind,
                            subject_id=item.subject_id,
                            rationale="The reviewed evidence supports this replacement.",
                            confidence=0.9,
                        )
                        if item.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT
                        else None
                    ),
                )
                for item in request.request.subjects
            ),
        )
        decision = case.store.decide_review(
            decision_command,
            operation_id="temporal-test:review-decision",
        )
        decision_replay = case.store.decide_review(
            decision_command,
            operation_id="temporal-test:review-decision",
        )
        assert decision_replay.replayed
        incomplete_request = HumanReviewRequestReceipt.model_construct(
            request=request.request.model_copy(update={"subjects": request.request.subjects[:-1]}),
            lifecycle=request.lifecycle,
            replayed=False,
        )
        with pytest.raises(ValueError):
            temporal_prerequisite_from_decision(
                commit=commit,
                request=incomplete_request,
                decision=decision,
            )
        incomplete_decision = HumanReviewDecisionReceipt.model_construct(
            decision=decision.decision.model_copy(update={"items": decision.decision.items[:-1]}),
            aggregate_revision=decision.aggregate_revision,
            aggregate_sha256=decision.aggregate_sha256,
            replayed=False,
        )
        with pytest.raises(ValueError):
            temporal_prerequisite_from_decision(
                commit=commit,
                request=request,
                decision=incomplete_decision,
            )
        prerequisite = temporal_prerequisite_from_decision(
            commit=commit,
            request=request,
            decision=decision,
        )
        assert prerequisite.review_open_head.revision == 4
        assert prerequisite.review_open_head.aggregate_sha256 == decision.aggregate_sha256
        replacements = decision.decision.decided_aggregate.document_replacements.assessments
        constraints = decision.decision.decided_aggregate.temporal_constraints.constraints
        assert replacements[0].status == TemporalConstraintStatus.ACCEPTED
        assert replacements[0].rationale == "The reviewed evidence supports this replacement."
        assert any(item.status == TemporalConstraintStatus.REJECTED for item in constraints)
        assert any(item.status == TemporalConstraintStatus.ACCEPTED for item in constraints)
    finally:
        case.store.close()


def test_temporal_proposal_rejects_missing_execution_coverage_and_stale_commit(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    try:
        missing_coverage = {
            **{
                key: value
                for key, value in case.build_inputs.items()
                if key != "inventory_resolver"
            },
            "classification_outcomes": case.classification_outcomes[:-1],
        }
        with pytest.raises(ValueError, match="exactly cover result shards"):
            build_temporal_proposal(**missing_coverage)

        dumped = case.proposal.model_dump(mode="python")
        dumped["binding"]["classification_executions"] = dumped["binding"][
            "classification_executions"
        ][:-1]
        with pytest.raises(ValueError):
            TemporalProposal.model_validate(dumped)

        first_commit = _commit(case)
        assert _commit(case).replayed

        request = open_temporal_review(
            case.store,
            first_commit,
            requester_id="review.stale",
            rationale="This review will become stale after an intervening revision.",
            operation_id="temporal-test:stale-review-request",
        )
        current = case.store.load(case.proposal.proposed_aggregate.aggregate_id)
        assert current is not None
        payload = current.aggregate.model_dump(mode="json")
        payload["relation_graph"]["assessments"][0]["rationale"] = (
            "An ordinary non-review relation annotation advanced the aggregate."
        )
        advanced = type(current.aggregate).model_validate_json(canonical_json_bytes(payload))
        intervening = case.store.compare_and_swap(
            advanced,
            expected_revision=3,
            operation_id="temporal-test:intervening-revision",
        )
        assert intervening.revision == 4
        with pytest.raises(TemporalProposalAuthorityError, match="authoritative head"):
            _commit(case)
        with pytest.raises(ChangeControlReviewStaleError):
            case.store.decide_review(
                HumanReviewDecisionCommand(
                    request_id=request.request.request_id,
                    reviewer_id="review.stale-approver",
                    rationale="This decision must fail because its review base is stale.",
                    items=tuple(
                        ReviewDecisionItem(
                            kind=item.kind,
                            subject_id=item.subject_id,
                            original_subject_sha256=item.subject_sha256,
                            disposition=ReviewDisposition.ACCEPTED,
                        )
                        for item in request.request.subjects
                    ),
                ),
                operation_id="temporal-test:stale-review-decision",
            )
    finally:
        case.store.close()


def test_proposed_document_constraint_factory_rejects_accepted_replacement() -> None:
    older = DocumentVersionMetadata.create(
        document_id="returns-v1",
        document_family="policy.returns",
        version_label="v1",
        source_path="runtime/raw/returns-v1.md",
        source_sha256="1" * 64,
        declared_effective_from=date(2025, 1, 1),
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )
    newer = DocumentVersionMetadata.create(
        document_id="returns-v2",
        document_family="policy.returns",
        version_label="v2",
        source_path="runtime/raw/returns-v2.md",
        source_sha256="2" * 64,
        declared_effective_from=date(2026, 1, 1),
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )
    replacement = DocumentReplacementAssessment.create(
        newer_document=newer,
        older_document=older,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The later policy replaces the earlier version.",
        confidence=0.95,
    )
    constraint = TemporalConstraint.propose_from_document_replacement(
        replacement,
        rationale=replacement.rationale,
    )
    assert constraint.status == TemporalConstraintStatus.PROPOSED
    accepted = replacement.with_status(TemporalConstraintStatus.ACCEPTED)
    with pytest.raises(ValueError, match="requires a proposed replacement"):
        TemporalConstraint.propose_from_document_replacement(
            accepted,
            rationale=accepted.rationale,
        )
