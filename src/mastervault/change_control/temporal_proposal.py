"""Evidence-bound revision-3 temporal proposal and ADR 0006 review seam.

This module composes already validated analysis results.  It does not call a
provider, resolve repository files, adjudicate impact, publish knowledge,
advance a managed generation, or orchestrate a checkpointed workflow.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.analysis_binding import AnalysisBootstrapAuthority
from mastervault.change_control.analysis_capability import (
    VerifiedAnalysisAuthorityCapability,
    verify_analysis_authority_snapshot,
)
from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationOutputShard,
    ClassificationResultSet,
    GraphMaterializationStatus,
    materialize_relation_assessments,
    validate_classification_results,
)
from mastervault.change_control.dependency_analysis import (
    DependencyClassificationResultSet,
    DependencyOutputShard,
    DependencyWorkload,
    VerifiedSourceNoteInventoryCapability,
    materialize_dependencies,
    validate_dependency_results,
)
from mastervault.change_control.discovery import RelationshipCandidateSet
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedArtifactRef,
    TemporalDecisionPrerequisite,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    ChangeControlAggregate,
    DependencyAssessment,
    DependencyRegistry,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentVersionMetadata,
    PairDisposition,
    PersistedRelationType,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalState,
    aggregate_sha256,
    canonical_json_bytes,
    resolve_document_temporality,
)
from mastervault.change_control.recorded_inference import (
    RecordedInferenceOutcome,
    RecordedInferenceTask,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    HumanReviewDecisionReceipt,
    HumanReviewRequestCommand,
    HumanReviewRequestReceipt,
    ReviewSubjectKind,
    ReviewSubjectRef,
    apply_human_review_decision,
    review_subject_sha256,
)
from mastervault.change_control.store import (
    ChangeControlSnapshot,
    SqliteChangeControlStore,
)

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_EXECUTION_REF_ID = r"^temporal-exec-ref:[0-9a-f]{64}$"
_REPLACEMENT_CANDIDATE_ID = r"^temporal-doc-replacement:[0-9a-f]{64}$"
_PROPOSAL_BINDING_ID = r"^temporal-proposal:[0-9a-f]{64}$"
_EVIDENCE_BATCH_ID = r"^inference-batch:[0-9a-f]{64}$"


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _require_operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("operation ID uses an unsafe or unsupported shape")
    return value


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InferenceExecutionRef(_StrictFrozenModel):
    """Compact content identity for one exact recorded inference shard."""

    schema_version: Literal[1] = 1
    ref_id: str = Field(pattern=_EXECUTION_REF_ID)
    ref_sha256: str = Field(pattern=SHA256_PATTERN)
    task: RecordedInferenceTask
    input_shard_id: str
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_id: str = Field(pattern=r"^inference-exec:[0-9a-f]{64}$")
    execution_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_id: str = Field(pattern=r"^minference:[0-9a-f]{64}$")
    receipt_artifact: ManagedArtifactRef
    validated_output_artifact: ManagedArtifactRef

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"ref_id", "ref_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.validated_output_artifact.sha256 != self.output_shard_sha256:
            raise ValueError("execution ref output artifact differs from its output shard")
        digest = _sha256(self._payload())
        if self.ref_sha256 != digest or self.ref_id != f"temporal-exec-ref:{digest}":
            raise ValueError("execution ref ID/SHA does not match its exact evidence")
        return self

    @classmethod
    def create(cls, outcome: RecordedInferenceOutcome) -> Self:
        validated = RecordedInferenceOutcome.model_validate(outcome.model_dump(mode="python"))
        output: ClassificationOutputShard | DependencyOutputShard | None
        if validated.execution.task == RecordedInferenceTask.CLASSIFICATION:
            output = validated.classification_output
        elif validated.execution.task == RecordedInferenceTask.DEPENDENCY:
            output = validated.dependency_output
        else:
            raise ValueError("temporal execution refs cannot bind actual-impact inference")
        if output is None:
            raise ValueError("temporal execution ref omits its exact typed output")
        input_shard_id = output.input_shard_id
        input_shard_sha256 = output.input_shard_sha256
        output_shard_id = output.output_shard_id
        output_shard_sha256 = output.output_shard_sha256
        values: dict[str, Any] = {
            "schema_version": 1,
            "task": validated.execution.task.value,
            "input_shard_id": input_shard_id,
            "input_shard_sha256": input_shard_sha256,
            "output_shard_id": output_shard_id,
            "output_shard_sha256": output_shard_sha256,
            "execution_id": validated.execution.execution_id,
            "execution_sha256": validated.execution.execution_sha256,
            "receipt_id": validated.execution.receipt.receipt_id,
            "receipt_artifact": validated.execution.receipt_artifact.model_dump(mode="json"),
            "validated_output_artifact": (
                validated.execution.validated_output_artifact.model_dump(mode="json")
            ),
        }
        digest = _sha256(values)
        return cls(
            ref_id=f"temporal-exec-ref:{digest}",
            ref_sha256=digest,
            task=validated.execution.task,
            input_shard_id=input_shard_id,
            input_shard_sha256=input_shard_sha256,
            output_shard_id=output_shard_id,
            output_shard_sha256=output_shard_sha256,
            execution_id=validated.execution.execution_id,
            execution_sha256=validated.execution.execution_sha256,
            receipt_id=validated.execution.receipt.receipt_id,
            receipt_artifact=validated.execution.receipt_artifact,
            validated_output_artifact=validated.execution.validated_output_artifact,
        )


class DocumentReplacementProposalCandidate(_StrictFrozenModel):
    """Advisory whole-document proposal supported by exact claim classifications."""

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=_REPLACEMENT_CANDIDATE_ID)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    newer_document: DocumentVersionMetadata
    older_document: DocumentVersionMetadata
    supporting_classification_ids: tuple[str, ...] = Field(min_length=1)
    supporting_classification_sha256s: tuple[str, ...] = Field(min_length=1)
    supporting_relation_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        if not value or value != " ".join(value.split()):
            raise ValueError("replacement rationale must be canonical non-empty text")
        return value

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"candidate_id", "candidate_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if (
            self.newer_document.document_version_id == self.older_document.document_version_id
            or self.newer_document.document_family != self.older_document.document_family
            or self.newer_document.declared_effective_from
            <= self.older_document.declared_effective_from
        ):
            raise ValueError("replacement candidate requires a later distinct same-family version")
        collections = (
            self.supporting_classification_ids,
            self.supporting_classification_sha256s,
            self.supporting_relation_ids,
        )
        if any(values != tuple(sorted(set(values))) for values in collections):
            raise ValueError("replacement support bindings must be canonically ordered and unique")
        if len({len(values) for values in collections}) != 1:
            raise ValueError("replacement support ID/SHA/relation coverage must align exactly")
        digest = _sha256(self._payload())
        if (
            self.candidate_sha256 != digest
            or self.candidate_id != f"temporal-doc-replacement:{digest}"
        ):
            raise ValueError("replacement candidate ID/SHA does not match its exact support")
        return self

    @classmethod
    def create(
        cls,
        *,
        newer_document: DocumentVersionMetadata,
        older_document: DocumentVersionMetadata,
        supporting_classifications: tuple[ClaimPairClassification, ...],
        rationale: str,
        confidence: float,
    ) -> Self:
        ordered = tuple(sorted(supporting_classifications, key=lambda item: item.classification_id))
        if not ordered:
            raise ValueError("replacement candidate requires supporting classifications")
        relations: list[str] = []
        for item in ordered:
            assessment = item.relation_assessment
            if (
                item.disposition != PairDisposition.SUPERSEDES
                or item.materialization_status != GraphMaterializationStatus.GRAPH_VALID
                or assessment is None
                or assessment.relation_type != PersistedRelationType.SUPERSEDES
                or assessment.relation_id is None
                or assessment.endpoint_ids is None
            ):
                raise ValueError("replacement support must be graph-valid SUPERSEDES")
            newer = assessment.pair.revision(assessment.endpoint_ids[0])
            older = assessment.pair.revision(assessment.endpoint_ids[1])
            if newer.document != newer_document or older.document != older_document:
                raise ValueError("replacement support endpoints differ from proposed documents")
            relations.append(assessment.relation_id)
        classification_ids = tuple(item.classification_id for item in ordered)
        classification_shas = tuple(sorted(item.classification_sha256 for item in ordered))
        relation_ids = tuple(sorted(relations))
        values: dict[str, Any] = {
            "schema_version": 1,
            "newer_document": newer_document.model_dump(mode="json"),
            "older_document": older_document.model_dump(mode="json"),
            "supporting_classification_ids": classification_ids,
            "supporting_classification_sha256s": classification_shas,
            "supporting_relation_ids": relation_ids,
            "rationale": rationale,
            "confidence": confidence,
        }
        digest = _sha256(values)
        return cls(
            candidate_id=f"temporal-doc-replacement:{digest}",
            candidate_sha256=digest,
            **values,
        )

    def proposed_assessment(self) -> DocumentReplacementAssessment:
        return DocumentReplacementAssessment.create(
            newer_document=self.newer_document,
            older_document=self.older_document,
            status=TemporalConstraintStatus.PROPOSED,
            rationale=self.rationale,
            confidence=self.confidence,
        )


class TemporalProposalBinding(_StrictFrozenModel):
    """Complete content identity for the exact revision-2 to revision-3 proposal."""

    schema_version: Literal[1] = 1
    algorithm_version: Literal["temporal-proposal-v1"] = "temporal-proposal-v1"
    binding_id: str = Field(pattern=_PROPOSAL_BINDING_ID)
    binding_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_bootstrap: AnalysisBootstrapAuthority
    analysis_head: AggregateHeadBinding
    candidate_result_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_result_id: str = Field(pattern=r"^classresult:[0-9a-f]{64}$")
    classification_result_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_evidence_batch_id: str = Field(pattern=_EVIDENCE_BATCH_ID)
    classification_evidence_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    dependency_workload_id: str = Field(pattern=r"^depwork:[0-9a-f]{64}$")
    dependency_workload_sha256: str = Field(pattern=SHA256_PATTERN)
    dependency_result_id: str = Field(pattern=r"^depresult:[0-9a-f]{64}$")
    dependency_result_sha256: str = Field(pattern=SHA256_PATTERN)
    dependency_evidence_batch_id: str = Field(pattern=_EVIDENCE_BATCH_ID)
    dependency_evidence_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    replacement_candidate_id: str = Field(pattern=_REPLACEMENT_CANDIDATE_ID)
    replacement_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_executions: tuple[InferenceExecutionRef, ...] = Field(min_length=1)
    dependency_executions: tuple[InferenceExecutionRef, ...] = ()
    relation_assessment_sha256s: tuple[str, ...]
    dependency_assessment_sha256s: tuple[str, ...]
    replacement_subject_sha256s: tuple[str, ...] = Field(min_length=1, max_length=1)
    constraint_subject_sha256s: tuple[str, ...] = Field(min_length=1)
    proposed_aggregate_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"binding_id", "binding_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        bootstrap = self.analysis_bootstrap
        if (
            self.analysis_head.aggregate_id != bootstrap.aggregate_id
            or self.analysis_head.revision != bootstrap.analysis_revision
            or self.analysis_head.aggregate_sha256 != bootstrap.analysis_aggregate_sha256
        ):
            raise ValueError("temporal proposal head differs from its verified bootstrap")
        for batch_id, batch_sha256 in (
            (
                self.classification_evidence_batch_id,
                self.classification_evidence_batch_sha256,
            ),
            (self.dependency_evidence_batch_id, self.dependency_evidence_batch_sha256),
        ):
            if batch_id != f"inference-batch:{batch_sha256}":
                raise ValueError("evidence batch ID does not match its exact batch SHA")
        for task, refs in (
            (RecordedInferenceTask.CLASSIFICATION, self.classification_executions),
            (RecordedInferenceTask.DEPENDENCY, self.dependency_executions),
        ):
            if any(item.task != task for item in refs):
                raise ValueError("temporal proposal execution task coverage is mixed")
            keys = tuple(item.input_shard_id for item in refs)
            if keys != tuple(sorted(set(keys))):
                raise ValueError("temporal proposal execution refs must use unique input order")
        for values in (
            self.relation_assessment_sha256s,
            self.dependency_assessment_sha256s,
            self.replacement_subject_sha256s,
            self.constraint_subject_sha256s,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("temporal proposal SHA ledgers must be sorted and unique")
        digest = _sha256(self._payload())
        if self.binding_sha256 != digest or self.binding_id != f"temporal-proposal:{digest}":
            raise ValueError("temporal proposal binding ID/SHA does not match exact inputs/result")
        return self


class TemporalProposal(_StrictFrozenModel):
    binding: TemporalProposalBinding
    proposed_aggregate: ChangeControlAggregate
    review_subjects: tuple[ReviewSubjectRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if aggregate_sha256(self.proposed_aggregate) != self.binding.proposed_aggregate_sha256:
            raise ValueError("temporal proposal aggregate differs from its binding")
        expected = _all_proposed_review_refs(self.proposed_aggregate)
        if self.review_subjects != expected:
            raise ValueError("temporal proposal review subjects are not exact and complete")
        return self


class TemporalProposalCommit(_StrictFrozenModel):
    proposal: TemporalProposal
    operation_id: str
    temporal_analysis_manifest_id: str = Field(pattern=r"^temporal-analysis:[0-9a-f]{64}$")
    temporal_analysis_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    temporal_analysis_manifest_path: str = Field(
        pattern=r"^temporal/evidence/analyses/[0-9a-f]{64}\.json$"
    )
    evidence_repository_id: str = Field(pattern=SHA256_PATTERN)
    aggregate_id: str
    revision: Literal[3]
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    changed: Literal[True] = True
    committed_at: str
    replayed: bool = False

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _require_operation_id(value)

    @model_validator(mode="after")
    def _receipt(self) -> Self:
        if (
            self.aggregate_id != self.proposal.proposed_aggregate.aggregate_id
            or self.aggregate_sha256 != self.proposal.binding.proposed_aggregate_sha256
        ):
            raise ValueError("temporal proposal commit differs from its exact proposal")
        manifest_sha = self.temporal_analysis_manifest_sha256
        if (
            self.operation_id != f"temporal-commit:{manifest_sha}"
            or self.temporal_analysis_manifest_id != f"temporal-analysis:{manifest_sha}"
            or self.temporal_analysis_manifest_path
            != f"temporal/evidence/analyses/{manifest_sha}.json"
        ):
            raise ValueError(
                "temporal proposal commit operation and manifest locators must derive "
                "from the exact temporal-analysis SHA"
            )
        return self


def _assessment_sha256(value: BaseModel) -> str:
    return _sha256(value.model_dump(mode="json"))


def _all_proposed_review_refs(aggregate: ChangeControlAggregate) -> tuple[ReviewSubjectRef, ...]:
    refs = [
        ReviewSubjectRef(
            kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
            subject_id=item.relation_id,
        )
        for item in aggregate.document_replacements.assessments
        if item.status == TemporalConstraintStatus.PROPOSED
    ]
    refs.extend(
        ReviewSubjectRef(
            kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
            subject_id=item.constraint_id,
        )
        for item in aggregate.temporal_constraints.constraints
        if item.status == TemporalConstraintStatus.PROPOSED
    )
    return tuple(sorted(refs, key=lambda item: (item.kind.value, item.subject_id)))


def _merge_relations(
    existing: tuple[RelationAssessment, ...], additions: tuple[RelationAssessment, ...]
) -> RelationGraph:
    by_pair = {item.pair.pair_id: item for item in existing}
    for item in additions:
        prior = by_pair.get(item.pair.pair_id)
        if prior is not None and prior != item:
            raise ValueError("classification conflicts with an existing relation assessment")
        by_pair[item.pair.pair_id] = item
    return RelationGraph.create(tuple(by_pair.values()))


def _merge_dependencies(
    existing: tuple[DependencyAssessment, ...], additions: tuple[DependencyAssessment, ...]
) -> DependencyRegistry:
    by_id = {item.dependency_id: item for item in existing}
    for item in additions:
        prior = by_id.get(item.dependency_id)
        if prior is not None and prior != item:
            raise ValueError("dependency result conflicts with an existing semantic dependency")
        by_id[item.dependency_id] = item
    return DependencyRegistry.create(tuple(by_id.values()))


def _validated_execution_refs(
    *,
    task: RecordedInferenceTask,
    outcomes: tuple[RecordedInferenceOutcome, ...],
    expected_outputs: tuple[Any, ...],
) -> tuple[InferenceExecutionRef, ...]:
    if task not in {
        RecordedInferenceTask.CLASSIFICATION,
        RecordedInferenceTask.DEPENDENCY,
    }:
        raise ValueError("temporal proposals cannot consume actual-impact inference")
    validated = tuple(
        RecordedInferenceOutcome.model_validate(item.model_dump(mode="python")) for item in outcomes
    )
    if any(item.execution.task != task for item in validated):
        raise ValueError("recorded inference outcomes contain a mixed task")
    actual_outputs = tuple(
        item.classification_output
        if task == RecordedInferenceTask.CLASSIFICATION
        else item.dependency_output
        for item in validated
    )
    if any(item is None for item in actual_outputs):
        raise ValueError("recorded inference outcome omits its typed output")
    for outcome, output in zip(validated, actual_outputs, strict=True):
        assert output is not None
        envelope = outcome.execution.input_envelope
        if (
            envelope.input_shard_id != output.input_shard_id
            or envelope.input_shard_sha256 != output.input_shard_sha256
            or envelope.workload_id != output.workload_id
            or envelope.workload_sha256 != output.workload_sha256
        ):
            raise ValueError("recorded inference envelope differs from its typed output binding")
    expected_by_id = {item.output_shard_id: item for item in expected_outputs}
    actual_by_id = {item.output_shard_id: item for item in actual_outputs if item is not None}
    if len(expected_by_id) != len(expected_outputs) or len(actual_by_id) != len(actual_outputs):
        raise ValueError("recorded inference output shard coverage contains duplicates")
    if set(actual_by_id) != set(expected_by_id):
        raise ValueError("recorded inference outcomes do not exactly cover result shards")
    if any(actual_by_id[key] != expected_by_id[key] for key in expected_by_id):
        raise ValueError("recorded inference outcome substitutes a result shard")
    if not validated:
        if expected_outputs:
            raise ValueError("empty inference evidence cannot cover non-empty output shards")
        return ()
    refs = tuple(
        sorted(
            (InferenceExecutionRef.create(item) for item in validated),
            key=lambda x: x.input_shard_id,
        )
    )
    contracts = {
        canonical_json_bytes(item.execution.contract.model_dump(mode="json")) for item in validated
    }
    if len(contracts) != 1:
        raise ValueError("one inference task must use one exact execution contract")
    return refs


def build_temporal_proposal(
    *,
    verified_bootstrap: VerifiedAnalysisAuthorityCapability,
    snapshot: ChangeControlSnapshot,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    classification_outcomes: tuple[RecordedInferenceOutcome, ...],
    classification_evidence_batch_id: str,
    classification_evidence_batch_sha256: str,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
    dependency_workload: DependencyWorkload,
    dependency_results: DependencyClassificationResultSet,
    dependency_outcomes: tuple[RecordedInferenceOutcome, ...],
    dependency_evidence_batch_id: str,
    dependency_evidence_batch_sha256: str,
    replacement_candidate: DocumentReplacementProposalCandidate,
) -> TemporalProposal:
    """Build the exact inert revision-3 payload from verified revision-2 evidence."""

    bootstrap = verify_analysis_authority_snapshot(verified_bootstrap, snapshot)
    validated_classifications = validate_classification_results(
        snapshot, candidates=candidates, results=classification_results
    )
    classification_refs = _validated_execution_refs(
        task=RecordedInferenceTask.CLASSIFICATION,
        outcomes=classification_outcomes,
        expected_outputs=validated_classifications.output_shards,
    )
    validated_dependencies = validate_dependency_results(
        snapshot,
        candidates=candidates,
        classification_results=validated_classifications,
        workload=dependency_workload,
        results=dependency_results,
        inventory_capability=inventory_capability,
    )
    dependency_refs = _validated_execution_refs(
        task=RecordedInferenceTask.DEPENDENCY,
        outcomes=dependency_outcomes,
        expected_outputs=validated_dependencies.output_shards,
    )
    relations = materialize_relation_assessments(
        snapshot, candidates=candidates, results=validated_classifications
    )
    dependencies = materialize_dependencies(
        snapshot,
        candidates=candidates,
        classification_results=validated_classifications,
        workload=dependency_workload,
        results=validated_dependencies,
        inventory_capability=inventory_capability,
    )

    documents = {item.document_version_id: item for item in snapshot.aggregate.documents.documents}
    incoming = documents.get(bootstrap.incoming_document_version_id)
    if incoming is None or replacement_candidate.newer_document != incoming:
        raise ValueError("replacement candidate newer document is not the verified incoming event")
    current_same_family = tuple(
        item
        for item in snapshot.aggregate.documents.documents
        if item.document_version_id != incoming.document_version_id
        and item.document_family == incoming.document_family
        and resolve_document_temporality(
            item,
            snapshot.aggregate.validated_temporal_constraints(),
            as_of=bootstrap.analysis_as_of,
        ).state
        == TemporalState.CURRENT
    )
    if (
        len(current_same_family) != 1
        or replacement_candidate.older_document != current_same_family[0]
    ):
        raise ValueError(
            "replacement candidate must bind the unique current same-family predecessor"
        )

    classifications_by_id = {
        item.classification_id: item for item in validated_classifications.classifications
    }
    supports: list[ClaimPairClassification] = []
    for classification_id in replacement_candidate.supporting_classification_ids:
        item = classifications_by_id.get(classification_id)
        if item is None:
            raise ValueError("replacement candidate names an absent classification")
        supports.append(item)
    rebuilt_candidate = DocumentReplacementProposalCandidate.create(
        newer_document=incoming,
        older_document=current_same_family[0],
        supporting_classifications=tuple(supports),
        rationale=replacement_candidate.rationale,
        confidence=replacement_candidate.confidence,
    )
    if rebuilt_candidate != replacement_candidate:
        raise ValueError(
            "replacement candidate support does not match exact classification results"
        )

    changed_ids = set(bootstrap.changed_claim_revision_ids)
    claim_constraints: list[TemporalConstraint] = []
    for assessment in relations:
        if (
            assessment.relation_type != PersistedRelationType.SUPERSEDES
            or assessment.endpoint_ids is None
            or assessment.relation_id is None
        ):
            continue
        newer_id, older_id = assessment.endpoint_ids
        if newer_id not in changed_ids or older_id in changed_ids:
            continue
        claim_constraints.append(
            TemporalConstraint.from_supersession(
                assessment,
                status=TemporalConstraintStatus.PROPOSED,
                rationale=assessment.rationale,
            )
        )
    if not claim_constraints:
        raise ValueError("temporal proposal requires a graph-valid changed-to-older supersession")

    replacement = replacement_candidate.proposed_assessment()
    document_constraint = TemporalConstraint.propose_from_document_replacement(
        replacement,
        rationale=replacement_candidate.rationale,
    )
    relation_graph = _merge_relations(snapshot.aggregate.relation_graph.assessments, relations)
    dependency_registry = _merge_dependencies(
        snapshot.aggregate.dependencies.assessments, dependencies
    )
    replacements = DocumentReplacementSet.create(
        (*snapshot.aggregate.document_replacements.assessments, replacement)
    )
    constraints = TemporalConstraintSet.create(
        (
            *snapshot.aggregate.temporal_constraints.constraints,
            *claim_constraints,
            document_constraint,
        )
    )
    proposed = ChangeControlAggregate.create(
        aggregate_id=snapshot.aggregate.aggregate_id,
        documents=snapshot.aggregate.documents,
        claims=snapshot.aggregate.claims,
        relation_graph=relation_graph,
        dependencies=dependency_registry,
        document_replacements=replacements,
        temporal_constraints=constraints,
    )
    if (
        proposed.documents != snapshot.aggregate.documents
        or proposed.claims != snapshot.aggregate.claims
    ):
        raise ValueError("temporal proposal must preserve revision-2 document and claim roots")

    review_refs = tuple(
        sorted(
            (
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
                    subject_id=replacement.relation_id,
                ),
                *(
                    ReviewSubjectRef(
                        kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
                        subject_id=item.constraint_id,
                    )
                    for item in (*claim_constraints, document_constraint)
                ),
            ),
            key=lambda item: (item.kind.value, item.subject_id),
        )
    )
    proposed_sha = aggregate_sha256(proposed)
    values: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "temporal-proposal-v1",
        "analysis_bootstrap": bootstrap.model_dump(mode="json"),
        "analysis_head": AggregateHeadBinding.create(
            aggregate_id=snapshot.aggregate.aggregate_id,
            revision=snapshot.revision,
            aggregate_sha256=snapshot.aggregate_sha256,
        ).model_dump(mode="json"),
        "candidate_result_sha256": candidates.result_sha256,
        "classification_result_id": validated_classifications.result_set_id,
        "classification_result_sha256": validated_classifications.result_sha256,
        "classification_evidence_batch_id": classification_evidence_batch_id,
        "classification_evidence_batch_sha256": classification_evidence_batch_sha256,
        "dependency_workload_id": dependency_workload.index.workload_id,
        "dependency_workload_sha256": dependency_workload.index.workload_sha256,
        "dependency_result_id": validated_dependencies.result_index.result_id,
        "dependency_result_sha256": validated_dependencies.result_index.result_sha256,
        "dependency_evidence_batch_id": dependency_evidence_batch_id,
        "dependency_evidence_batch_sha256": dependency_evidence_batch_sha256,
        "replacement_candidate_id": replacement_candidate.candidate_id,
        "replacement_candidate_sha256": replacement_candidate.candidate_sha256,
        "classification_executions": [item.model_dump(mode="json") for item in classification_refs],
        "dependency_executions": [item.model_dump(mode="json") for item in dependency_refs],
        "relation_assessment_sha256s": tuple(
            sorted(_assessment_sha256(item) for item in relations)
        ),
        "dependency_assessment_sha256s": tuple(
            sorted(_assessment_sha256(item) for item in dependencies)
        ),
        "replacement_subject_sha256s": (
            review_subject_sha256(ReviewSubjectKind.DOCUMENT_REPLACEMENT, replacement),
        ),
        "constraint_subject_sha256s": tuple(
            sorted(
                review_subject_sha256(ReviewSubjectKind.TEMPORAL_CONSTRAINT, item)
                for item in (*claim_constraints, document_constraint)
            )
        ),
        "proposed_aggregate_sha256": proposed_sha,
    }
    digest = _sha256(values)
    binding = TemporalProposalBinding.model_validate_json(
        canonical_json_bytes(
            {
                "binding_id": f"temporal-proposal:{digest}",
                "binding_sha256": digest,
                **values,
            }
        )
    )
    return TemporalProposal(
        binding=binding,
        proposed_aggregate=proposed,
        review_subjects=review_refs,
    )


def open_temporal_review(
    store: SqliteChangeControlStore,
    commit: TemporalProposalCommit,
    *,
    requester_id: str,
    rationale: str,
    operation_id: str,
) -> HumanReviewRequestReceipt:
    """Open one exact review over every subject introduced by the proposal."""

    operation_id = _require_operation_id(operation_id)
    commit = TemporalProposalCommit.model_validate(commit.model_dump(mode="python"))
    if operation_id == commit.operation_id:
        raise ValueError("proposal and review request require distinct operation IDs")
    result = store.create_review_request(
        HumanReviewRequestCommand(
            aggregate_id=commit.aggregate_id,
            expected_revision=commit.revision,
            expected_aggregate_sha256=commit.aggregate_sha256,
            subjects=commit.proposal.review_subjects,
            requester_id=requester_id,
            rationale=rationale,
        ),
        operation_id=operation_id,
    )
    observed = tuple(
        ReviewSubjectRef(kind=item.kind, subject_id=item.subject_id)
        for item in result.request.subjects
    )
    if observed != commit.proposal.review_subjects:
        raise ValueError("authoritative temporal request does not cover the exact proposal")
    return result


def temporal_prerequisite_from_decision(
    *,
    commit: TemporalProposalCommit,
    request: HumanReviewRequestReceipt,
    decision: HumanReviewDecisionReceipt,
) -> TemporalDecisionPrerequisite:
    """Bind the exact authoritative revision-3 to revision-4 decision."""

    commit = TemporalProposalCommit.model_validate(commit.model_dump(mode="python"))
    request = HumanReviewRequestReceipt.model_validate(request.model_dump(mode="python"))
    decision = HumanReviewDecisionReceipt.model_validate(decision.model_dump(mode="python"))
    expected_refs = commit.proposal.review_subjects
    request_refs = tuple(
        ReviewSubjectRef(kind=item.kind, subject_id=item.subject_id)
        for item in request.request.subjects
    )
    if (
        request.request.aggregate_id != commit.aggregate_id
        or request.request.base_revision != 3
        or request.request.base_aggregate_sha256 != commit.aggregate_sha256
        or request.request.base_aggregate != commit.proposal.proposed_aggregate
        or request_refs != expected_refs
    ):
        raise ValueError("temporal review request does not bind the exact revision-3 proposal")
    decided = decision.decision
    if (
        decided.request_id != request.request.request_id
        or decision.aggregate_revision != 4
        or decided.decided_revision != 4
        or decided.decided_aggregate.aggregate_id != commit.aggregate_id
        or decision.aggregate_sha256 != decided.decided_aggregate_sha256
    ):
        raise ValueError("temporal decision is not the exact authoritative revision-4 result")
    item_keys = tuple((item.kind, item.subject_id) for item in decided.items)
    expected_keys = tuple((item.kind, item.subject_id) for item in expected_refs)
    if item_keys != expected_keys:
        raise ValueError("temporal decision does not cover the exact proposal subjects")
    command = HumanReviewDecisionCommand(
        request_id=decided.request_id,
        reviewer_id=decided.reviewer_id,
        rationale=decided.rationale,
        items=decided.items,
    )
    if apply_human_review_decision(request.request, command) != decided.decided_aggregate:
        raise ValueError("temporal decision result is not derived from the bound review request")
    if len({commit.operation_id, request.request.operation_id, decided.operation_id}) != 3:
        raise ValueError("proposal, request, and decision require distinct operation IDs")
    head = AggregateHeadBinding.create(
        aggregate_id=commit.aggregate_id,
        revision=decision.aggregate_revision,
        aggregate_sha256=decision.aggregate_sha256,
    )
    decision_sha = hashlib.sha256(canonical_json_bytes(decided.model_dump(mode="json"))).hexdigest()
    return TemporalDecisionPrerequisite(
        review_open_head=head,
        temporal_decision_record_sha256=decision_sha,
    )


__all__ = [
    "DocumentReplacementProposalCandidate",
    "InferenceExecutionRef",
    "TemporalProposal",
    "TemporalProposalBinding",
    "TemporalProposalCommit",
    "build_temporal_proposal",
    "open_temporal_review",
    "temporal_prerequisite_from_decision",
]
