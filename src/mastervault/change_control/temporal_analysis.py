"""Pure durable-reproduction envelope for one revision-3 temporal proposal.

The envelope deliberately remains separate from :class:`TemporalProposal`.
It duplicates bounded workload and input metadata so a later process can
reconstruct selection and exclusion ledgers without consulting transient
memory. Provider output shards and their artifact bytes are not duplicated;
they remain in the separately persisted inference evidence batches.

This module performs no filesystem, database, review, CAS, provider, or
workflow operations. A repository adapter may persist ``canonical_bytes()``
create-only and must reopen it through ``from_canonical_bytes()`` before use.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control.analysis_capability import (
    VerifiedAnalysisAuthorityCapability,
    verify_analysis_authority_snapshot,
)
from mastervault.change_control.classification import (
    ClassificationResultIndex,
    ClassificationResultSet,
    ClassificationWorkload,
    validate_classification_results,
)
from mastervault.change_control.dependency_analysis import (
    DependencyClassificationResultSet,
    DependencyResultIndex,
    DependencyWorkload,
    SourceNoteInventory,
    VerifiedSourceNoteInventoryCapability,
    validate_dependency_results,
)
from mastervault.change_control.discovery import RelationshipCandidateSet
from mastervault.change_control.managed_review import AggregateHeadBinding
from mastervault.change_control.models import (
    SHA256_PATTERN,
    ChangeControlAggregate,
    DependencyAssessment,
    RelationAssessment,
    TemporalConstraintStatus,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.recorded_inference import (
    RecordedInferenceOutcome,
    RecordedInferenceTask,
)
from mastervault.change_control.review import ReviewSubjectKind, review_subject_sha256
from mastervault.change_control.store import ChangeControlSnapshot
from mastervault.change_control.temporal_proposal import (
    DocumentReplacementProposalCandidate,
    TemporalProposal,
    build_temporal_proposal,
)

MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1 = 16 * 1024 * 1024

_MANIFEST_ID = r"^temporal-analysis:[0-9a-f]{64}$"
_EVIDENCE_BATCH_ID = r"^inference-batch:[0-9a-f]{64}$"


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _assessment_sha256(value: BaseModel) -> str:
    return _sha256(value.model_dump(mode="json"))


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TemporalAnalysisEvidence(_StrictFrozenModel):
    """Exact bounded inputs needed to reproduce one persisted temporal proposal."""

    schema_version: Literal[1] = 1
    algorithm_version: Literal["temporal-analysis-evidence-v1"] = "temporal-analysis-evidence-v1"
    manifest_id: str = Field(pattern=_MANIFEST_ID)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    analysis_head: AggregateHeadBinding
    analysis_aggregate: ChangeControlAggregate
    source_note_inventory: SourceNoteInventory
    relationship_candidates: RelationshipCandidateSet
    classification_workload: ClassificationWorkload
    classification_result_index: ClassificationResultIndex
    dependency_workload: DependencyWorkload
    dependency_result_index: DependencyResultIndex
    replacement_candidate: DocumentReplacementProposalCandidate
    proposal: TemporalProposal
    classification_evidence_batch_id: str = Field(pattern=_EVIDENCE_BATCH_ID)
    classification_evidence_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    dependency_evidence_batch_id: str = Field(pattern=_EVIDENCE_BATCH_ID)
    dependency_evidence_batch_sha256: str = Field(pattern=SHA256_PATTERN)

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id", "manifest_sha256"})

    def canonical_bytes(self) -> bytes:
        """Return the exact persisted bytes whose digest is ``manifest_sha256``."""

        return canonical_json_bytes(self._identity_payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        payload = self.canonical_bytes()
        if len(payload) > MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1:
            raise ValueError("temporal analysis evidence exceeds the fixed 16 MiB limit")
        digest = hashlib.sha256(payload).hexdigest()
        if self.manifest_sha256 != digest or self.manifest_id != f"temporal-analysis:{digest}":
            raise ValueError("temporal analysis manifest ID/SHA differs from its exact bytes")
        self._cross_validate()
        return self

    def _cross_validate(self) -> None:
        proposal = self.proposal
        binding = proposal.binding
        bootstrap = binding.analysis_bootstrap
        analysis_sha = aggregate_sha256(self.analysis_aggregate)
        if (
            self.analysis_head.aggregate_id != self.analysis_aggregate.aggregate_id
            or self.analysis_head.revision != 2
            or self.analysis_head.aggregate_sha256 != analysis_sha
            or self.analysis_head != binding.analysis_head
            or self.analysis_head.aggregate_id != bootstrap.aggregate_id
            or self.analysis_head.revision != bootstrap.analysis_revision
            or self.analysis_head.aggregate_sha256 != bootstrap.analysis_aggregate_sha256
        ):
            raise ValueError("temporal analysis evidence binds a different revision-2 head")
        if (
            proposal.proposed_aggregate.aggregate_id != self.analysis_aggregate.aggregate_id
            or proposal.proposed_aggregate.documents != self.analysis_aggregate.documents
            or proposal.proposed_aggregate.claims != self.analysis_aggregate.claims
        ):
            raise ValueError("temporal proposal does not preserve revision-2 documents and claims")
        self._assert_preserved_analysis_roots()

        inventory = self.source_note_inventory
        if (
            inventory.aggregate_id != self.analysis_head.aggregate_id
            or inventory.snapshot_revision != self.analysis_head.revision
            or inventory.aggregate_sha256 != self.analysis_head.aggregate_sha256
        ):
            raise ValueError("SourceNote inventory binds a different revision-2 head")

        candidates = self.relationship_candidates
        candidate_binding = candidates.binding
        if (
            candidate_binding.aggregate_id != self.analysis_head.aggregate_id
            or candidate_binding.snapshot_revision != self.analysis_head.revision
            or candidate_binding.aggregate_sha256 != self.analysis_head.aggregate_sha256
            or candidate_binding.as_of != bootstrap.analysis_as_of
            or candidate_binding.changed_claim_revision_ids != bootstrap.changed_claim_revision_ids
            or candidates.result_sha256 != binding.candidate_result_sha256
        ):
            raise ValueError("relationship candidate set differs from proposal analysis authority")

        workload = self.classification_workload
        result_index = self.classification_result_index
        if (
            workload.aggregate_id != self.analysis_head.aggregate_id
            or workload.snapshot_revision != self.analysis_head.revision
            or workload.aggregate_sha256 != self.analysis_head.aggregate_sha256
            or workload.source_candidate_set_sha256 != candidates.result_sha256
            or result_index.workload_id != workload.workload_id
            or result_index.workload_sha256 != workload.workload_sha256
            or result_index.source_candidate_set_sha256 != candidates.result_sha256
            or result_index.result_set_id != binding.classification_result_id
            or result_index.result_sha256 != binding.classification_result_sha256
        ):
            raise ValueError("classification workload/result differs from proposal binding")

        dependency = self.dependency_workload
        dependency_index = dependency.index
        dependency_result = self.dependency_result_index
        if (
            dependency_index.aggregate_id != self.analysis_head.aggregate_id
            or dependency_index.snapshot_revision != self.analysis_head.revision
            or dependency_index.aggregate_sha256 != self.analysis_head.aggregate_sha256
            or dependency_index.inventory_sha256 != inventory.inventory_sha256
            or dependency_index.source_candidate_set_sha256 != candidates.result_sha256
            or dependency_index.source_classification_result_id != result_index.result_set_id
            or dependency_index.source_classification_result_sha256 != result_index.result_sha256
            or dependency_index.workload_id != binding.dependency_workload_id
            or dependency_index.workload_sha256 != binding.dependency_workload_sha256
            or dependency_result.workload_id != dependency_index.workload_id
            or dependency_result.workload_sha256 != dependency_index.workload_sha256
            or dependency_result.result_id != binding.dependency_result_id
            or dependency_result.result_sha256 != binding.dependency_result_sha256
        ):
            raise ValueError("dependency workload/result differs from proposal binding")

        replacement = self.replacement_candidate
        if (
            replacement.candidate_id != binding.replacement_candidate_id
            or replacement.candidate_sha256 != binding.replacement_candidate_sha256
        ):
            raise ValueError("document replacement candidate differs from proposal binding")

        batch_refs = (
            (
                self.classification_evidence_batch_id,
                self.classification_evidence_batch_sha256,
                binding.classification_evidence_batch_id,
                binding.classification_evidence_batch_sha256,
            ),
            (
                self.dependency_evidence_batch_id,
                self.dependency_evidence_batch_sha256,
                binding.dependency_evidence_batch_id,
                binding.dependency_evidence_batch_sha256,
            ),
        )
        if any(
            batch_id != f"inference-batch:{batch_sha}"
            or (batch_id, batch_sha) != (bound_id, bound_sha)
            for batch_id, batch_sha, bound_id, bound_sha in batch_refs
        ):
            raise ValueError("inference evidence batch differs from proposal binding")
        self._assert_resolved_proposal_ledgers()

    def _assert_preserved_analysis_roots(self) -> None:
        analysis = self.analysis_aggregate
        proposed = self.proposal.proposed_aggregate
        collections = (
            (
                {item.pair.pair_id: item for item in analysis.relation_graph.assessments},
                {item.pair.pair_id: item for item in proposed.relation_graph.assessments},
            ),
            (
                {item.dependency_id: item for item in analysis.dependencies.assessments},
                {item.dependency_id: item for item in proposed.dependencies.assessments},
            ),
            (
                {item.relation_id: item for item in analysis.document_replacements.assessments},
                {item.relation_id: item for item in proposed.document_replacements.assessments},
            ),
            (
                {item.constraint_id: item for item in analysis.temporal_constraints.constraints},
                {item.constraint_id: item for item in proposed.temporal_constraints.constraints},
            ),
        )
        if any(
            any(after.get(key) != value for key, value in before.items())
            for before, after in collections
        ):
            raise ValueError("temporal proposal alters an existing revision-2 semantic root")

    def _assert_resolved_proposal_ledgers(self) -> None:
        analysis = self.analysis_aggregate
        proposed = self.proposal.proposed_aggregate
        old_relations = {item.pair.pair_id for item in analysis.relation_graph.assessments}
        new_relations: tuple[RelationAssessment, ...] = tuple(
            item
            for item in proposed.relation_graph.assessments
            if item.pair.pair_id not in old_relations
        )
        old_dependencies = {item.dependency_id for item in analysis.dependencies.assessments}
        new_dependencies: tuple[DependencyAssessment, ...] = tuple(
            item
            for item in proposed.dependencies.assessments
            if item.dependency_id not in old_dependencies
        )
        if tuple(sorted(_assessment_sha256(item) for item in new_relations)) != (
            self.proposal.binding.relation_assessment_sha256s
        ):
            raise ValueError("proposal relation assessment SHA ledger is unresolved")
        if tuple(sorted(_assessment_sha256(item) for item in new_dependencies)) != (
            self.proposal.binding.dependency_assessment_sha256s
        ):
            raise ValueError("proposal dependency assessment SHA ledger is unresolved")

        proposed_replacements = tuple(
            item
            for item in proposed.document_replacements.assessments
            if item.status == TemporalConstraintStatus.PROPOSED
        )
        proposed_constraints = tuple(
            item
            for item in proposed.temporal_constraints.constraints
            if item.status == TemporalConstraintStatus.PROPOSED
        )
        replacement_shas = tuple(
            sorted(
                review_subject_sha256(ReviewSubjectKind.DOCUMENT_REPLACEMENT, item)
                for item in proposed_replacements
            )
        )
        constraint_shas = tuple(
            sorted(
                review_subject_sha256(ReviewSubjectKind.TEMPORAL_CONSTRAINT, item)
                for item in proposed_constraints
            )
        )
        if replacement_shas != self.proposal.binding.replacement_subject_sha256s:
            raise ValueError("proposal replacement subject SHA ledger is unresolved")
        if constraint_shas != self.proposal.binding.constraint_subject_sha256s:
            raise ValueError("proposal constraint subject SHA ledger is unresolved")
        expected_replacement = self.replacement_candidate.proposed_assessment()
        if expected_replacement not in proposed_replacements:
            raise ValueError("proposal omits its exact document replacement candidate result")

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> Self:
        """Strictly reopen exact canonical identity bytes and derive their ID/SHA."""

        if not isinstance(payload, bytes):
            raise TypeError("temporal analysis canonical payload must be bytes")
        if len(payload) > MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1:
            raise ValueError("temporal analysis evidence exceeds the fixed 16 MiB limit")
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("temporal analysis evidence is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("temporal analysis evidence must be one JSON object")
        if "manifest_id" in decoded or "manifest_sha256" in decoded:
            raise ValueError("canonical identity payload must exclude derived manifest identity")
        if canonical_json_bytes(decoded) != payload:
            raise ValueError("temporal analysis evidence is not exact canonical JSON")
        digest = hashlib.sha256(payload).hexdigest()
        complete = {
            "manifest_id": f"temporal-analysis:{digest}",
            "manifest_sha256": digest,
            **decoded,
        }
        try:
            return cls.model_validate_json(canonical_json_bytes(complete))
        except ValueError as exc:
            raise ValueError("temporal analysis evidence failed exact validation") from exc


def build_temporal_analysis_evidence(
    *,
    verified_bootstrap: VerifiedAnalysisAuthorityCapability,
    snapshot: ChangeControlSnapshot,
    candidates: RelationshipCandidateSet,
    classification_results: ClassificationResultSet,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
    dependency_workload: DependencyWorkload,
    dependency_results: DependencyClassificationResultSet,
    replacement_candidate: DocumentReplacementProposalCandidate,
    proposal: TemporalProposal,
) -> TemporalAnalysisEvidence:
    """Build one bounded exact reproduction envelope from already produced evidence."""

    verify_analysis_authority_snapshot(verified_bootstrap, snapshot)
    classifications = validate_classification_results(
        snapshot,
        candidates=candidates,
        results=classification_results,
    )
    dependencies = validate_dependency_results(
        snapshot,
        candidates=candidates,
        classification_results=classifications,
        workload=dependency_workload,
        results=dependency_results,
        inventory_capability=inventory_capability,
    )
    inventory = SourceNoteInventory.model_validate(
        inventory_capability.verify(snapshot=snapshot).model_dump(mode="json")
    )
    exact_proposal = TemporalProposal.model_validate(proposal.model_dump(mode="python"))
    values: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "temporal-analysis-evidence-v1",
        "analysis_head": AggregateHeadBinding.create(
            aggregate_id=snapshot.aggregate.aggregate_id,
            revision=snapshot.revision,
            aggregate_sha256=snapshot.aggregate_sha256,
        ).model_dump(mode="json"),
        "analysis_aggregate": snapshot.aggregate.model_dump(mode="json"),
        "source_note_inventory": inventory.model_dump(mode="json"),
        "relationship_candidates": candidates.model_dump(mode="json"),
        "classification_workload": classifications.workload.model_dump(mode="json"),
        "classification_result_index": classifications.result_index.model_dump(mode="json"),
        "dependency_workload": dependency_workload.model_dump(mode="json"),
        "dependency_result_index": dependencies.result_index.model_dump(mode="json"),
        "replacement_candidate": replacement_candidate.model_dump(mode="json"),
        "proposal": exact_proposal.model_dump(mode="json"),
        "classification_evidence_batch_id": (
            exact_proposal.binding.classification_evidence_batch_id
        ),
        "classification_evidence_batch_sha256": (
            exact_proposal.binding.classification_evidence_batch_sha256
        ),
        "dependency_evidence_batch_id": exact_proposal.binding.dependency_evidence_batch_id,
        "dependency_evidence_batch_sha256": (
            exact_proposal.binding.dependency_evidence_batch_sha256
        ),
    }
    payload = canonical_json_bytes(values)
    if len(payload) > MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1:
        raise ValueError("temporal analysis evidence exceeds the fixed 16 MiB limit")
    digest = hashlib.sha256(payload).hexdigest()
    return TemporalAnalysisEvidence.model_validate_json(
        canonical_json_bytes(
            {
                "manifest_id": f"temporal-analysis:{digest}",
                "manifest_sha256": digest,
                **values,
            }
        )
    )


def _classification_outputs(
    outcomes: tuple[RecordedInferenceOutcome, ...],
) -> tuple[Any, ...]:
    validated = tuple(
        RecordedInferenceOutcome.model_validate(item.model_dump(mode="python")) for item in outcomes
    )
    if any(item.execution.task != RecordedInferenceTask.CLASSIFICATION for item in validated):
        raise ValueError("classification evidence contains another inference task")
    outputs = tuple(item.classification_output for item in validated)
    if any(item is None for item in outputs):
        raise ValueError("classification evidence omits a typed output shard")
    return tuple(
        sorted(
            (item for item in outputs if item is not None),
            key=lambda x: x.changed_claim_revision_id,
        )
    )


def _dependency_outputs(
    outcomes: tuple[RecordedInferenceOutcome, ...],
) -> tuple[Any, ...]:
    validated = tuple(
        RecordedInferenceOutcome.model_validate(item.model_dump(mode="python")) for item in outcomes
    )
    if any(item.execution.task != RecordedInferenceTask.DEPENDENCY for item in validated):
        raise ValueError("dependency evidence contains another inference task")
    outputs = tuple(item.dependency_output for item in validated)
    if any(item is None for item in outputs):
        raise ValueError("dependency evidence omits a typed output shard")
    return tuple(
        sorted((item for item in outputs if item is not None), key=lambda x: x.input_shard_id)
    )


def verify_temporal_analysis_evidence(
    evidence: TemporalAnalysisEvidence,
    *,
    verified_bootstrap: VerifiedAnalysisAuthorityCapability,
    inventory_capability: VerifiedSourceNoteInventoryCapability,
    classification_outcomes: tuple[RecordedInferenceOutcome, ...],
    dependency_outcomes: tuple[RecordedInferenceOutcome, ...],
) -> TemporalProposal:
    """Reproduce and return the exact proposal from freshly reopened authorities."""

    reopened = TemporalAnalysisEvidence.from_canonical_bytes(evidence.canonical_bytes())
    if reopened != evidence:
        raise ValueError("temporal analysis evidence differs after exact canonical reopen")
    snapshot = ChangeControlSnapshot(
        aggregate=reopened.analysis_aggregate,
        revision=reopened.analysis_head.revision,
        aggregate_sha256=reopened.analysis_head.aggregate_sha256,
    )
    verify_analysis_authority_snapshot(verified_bootstrap, snapshot)
    fresh_inventory = SourceNoteInventory.model_validate(
        inventory_capability.verify(snapshot=snapshot).model_dump(mode="json")
    )
    if fresh_inventory != reopened.source_note_inventory:
        raise ValueError("fresh SourceNote inventory differs from persisted temporal evidence")

    classification_results = ClassificationResultSet(
        workload=reopened.classification_workload,
        result_index=reopened.classification_result_index,
        output_shards=_classification_outputs(classification_outcomes),
    )
    dependency_results = DependencyClassificationResultSet(
        result_index=reopened.dependency_result_index,
        output_shards=_dependency_outputs(dependency_outcomes),
    )
    rebuilt = build_temporal_proposal(
        verified_bootstrap=verified_bootstrap,
        snapshot=snapshot,
        candidates=reopened.relationship_candidates,
        classification_results=classification_results,
        classification_outcomes=classification_outcomes,
        classification_evidence_batch_id=reopened.classification_evidence_batch_id,
        classification_evidence_batch_sha256=(reopened.classification_evidence_batch_sha256),
        inventory_capability=inventory_capability,
        dependency_workload=reopened.dependency_workload,
        dependency_results=dependency_results,
        dependency_outcomes=dependency_outcomes,
        dependency_evidence_batch_id=reopened.dependency_evidence_batch_id,
        dependency_evidence_batch_sha256=reopened.dependency_evidence_batch_sha256,
        replacement_candidate=reopened.replacement_candidate,
    )
    if rebuilt != reopened.proposal:
        raise ValueError("reproduced temporal proposal differs from persisted canonical proposal")
    return rebuilt


__all__ = [
    "MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1",
    "TemporalAnalysisEvidence",
    "build_temporal_analysis_evidence",
    "verify_temporal_analysis_evidence",
]
