"""Temporal change-control contracts and bounded runtime authorities."""

from typing import TYPE_CHECKING, Any

from mastervault.change_control.claim_scopes import (
    CLAIM_SCOPE_POLICY_VERSION,
    claim_scopes_v1,
)
from mastervault.change_control.classification import (
    MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1,
    MAX_CLASSIFICATION_RATIONALE_CHARS_V1,
    MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1,
    MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1,
    MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1,
    MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
    MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1,
    CandidateExclusionReason,
    CandidateSelectionReason,
    ClaimPairClassification,
    ClassificationInferencePair,
    ClassificationInferenceShard,
    ClassificationLimitError,
    ClassificationOutputItem,
    ClassificationOutputShard,
    ClassificationOutputShardRef,
    ClassificationResultIndex,
    ClassificationResultSet,
    ClassificationWorkload,
    ClassifierSelectorVersion,
    EndpointEvidenceBinding,
    ExcludedCandidateRef,
    GraphMaterializationStatus,
    SelectedCandidateRef,
    materialize_relation_assessments,
    relationship_candidate_sha256,
    select_classification_workload,
    validate_classification_results,
    validate_classification_workload,
)
from mastervault.change_control.dependency_analysis import (
    MAX_DEPENDENCY_CANDIDATES_V1,
    MAX_DEPENDENCY_DOCUMENT_SHARDS_V1,
    MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1,
    MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1,
    MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1,
    MAX_DEPENDENCY_RATIONALE_UTF8_BYTES_V1,
    MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1,
    CanonicalSourceNoteSnapshot,
    DependencyAnalysisLimitError,
    DependencyCandidate,
    DependencyCandidateExclusionReason,
    DependencyClassification,
    DependencyClassificationResultSet,
    DependencyDisposition,
    DependencyExclusionRef,
    DependencyInferenceShard,
    DependencyOutputShard,
    DependencyOutputShardRef,
    DependencyResultIndex,
    DependencySourceInventoryResolver,
    DependencyWorkload,
    DependencyWorkloadIndex,
    ExcludedDependencyDocument,
    GoverningSupersessionRef,
    SelectedNeighbourRef,
    SourceNoteInventory,
    VerifiedSourceNoteInventoryCapability,
    generate_dependency_workload,
    materialize_dependencies,
    validate_dependency_results,
    validate_dependency_workload,
)
from mastervault.change_control.discovery import (
    DiscoveryLimitError,
    DocumentAttentionCandidate,
    DocumentAttentionRanking,
    RelationshipCandidate,
    RelationshipCandidateSet,
    generate_relationship_candidates,
    rank_document_attention,
    validate_document_attention_ranking,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyAssessment,
    DependencyKind,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentRole,
    DocumentSpanReference,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    PersistedRelationType,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalResolution,
    TemporalState,
    TemporalTarget,
    TemporalTargetKind,
    ValidatedTemporalConstraintSet,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
    normalize_logical_key,
    normalize_semantic_text,
    resolve_claim_temporality,
    resolve_document_temporality,
    stable_content_id,
)
from mastervault.change_control.review import (
    HumanReviewDecision,
    HumanReviewDecisionCommand,
    HumanReviewDecisionReceipt,
    HumanReviewRequest,
    HumanReviewRequestCommand,
    HumanReviewRequestReceipt,
    HumanReviewRequestView,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewLifecycle,
    ReviewSubjectEdit,
    ReviewSubjectKind,
    ReviewSubjectRef,
    ReviewSubjectSnapshot,
    apply_human_review_decision,
    human_review_decision_payload_sha256,
    human_review_request_id,
    normalize_actor_id,
    normalize_review_rationale,
    review_subject_sha256,
)

if TYPE_CHECKING:
    from mastervault.change_control.analysis_binding import (
        ANALYSIS_AGGREGATE_ID as ANALYSIS_AGGREGATE_ID,
    )
    from mastervault.change_control.analysis_binding import (
        AnalysisBootstrapBinding as AnalysisBootstrapBinding,
    )
    from mastervault.change_control.analysis_binding import (
        AnalysisBootstrapError as AnalysisBootstrapError,
    )
    from mastervault.change_control.analysis_binding import (
        AnalysisBootstrapIntegrityError as AnalysisBootstrapIntegrityError,
    )
    from mastervault.change_control.bootstrap import (
        AnalysisBootstrapResult as AnalysisBootstrapResult,
    )
    from mastervault.change_control.bootstrap import (
        AnalysisBootstrapStaleError as AnalysisBootstrapStaleError,
    )
    from mastervault.change_control.bootstrap import (
        VerifiedAnalysisBootstrapCapability as VerifiedAnalysisBootstrapCapability,
    )
    from mastervault.change_control.bootstrap import (
        bootstrap_analysis_aggregate as bootstrap_analysis_aggregate,
    )
    from mastervault.change_control.bootstrap import (
        build_verified_prechange_aggregate as build_verified_prechange_aggregate,
    )
    from mastervault.change_control.bootstrap import (
        create_verified_analysis_bootstrap_binding as create_verified_analysis_bootstrap_binding,
    )
    from mastervault.change_control.bootstrap import (
        create_verified_analysis_bootstrap_capability as create_verified_analysis_bootstrap_capability,
    )
    from mastervault.change_control.bootstrap import (
        incoming_claim_evidence_sha256 as incoming_claim_evidence_sha256,
    )
    from mastervault.change_control.bootstrap import (
        verify_analysis_bootstrap_snapshot as verify_analysis_bootstrap_snapshot,
    )
    from mastervault.change_control.bootstrap import (
        verify_generation_zero_authority as verify_generation_zero_authority,
    )
    from mastervault.change_control.inference_repository import (
        MAX_COMMITTED_BATCH_MANIFESTS_V1,
        MAX_COMMITTED_BATCH_SCAN_BYTES_V1,
        MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1,
        MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1,
        MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1,
        MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1,
        MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1,
        MAX_INFERENCE_EVIDENCE_BATCH_V1,
        MAX_INFERENCE_INPUT_METADATA_DEPTH_V1,
        MAX_INFERENCE_INPUT_METADATA_NODES_V1,
        MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1,
        MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1,
        MAX_PENDING_FILES_PER_DIRECTORY_V1,
        FilesystemInferenceEvidenceRepository,
        InferenceEvidenceConflictError,
        InferenceEvidenceRepositoryError,
        InferenceEvidenceResolutionError,
        InferenceEvidenceUnsupportedPlatformError,
        RepositoryVerifiedInferenceEvidenceBatch,
    )
    from mastervault.change_control.managed_store import (
        ManagedReviewAuthorityError as ManagedReviewAuthorityError,
    )
    from mastervault.change_control.managed_store import (
        ManagedReviewRepositoryResolver as ManagedReviewRepositoryResolver,
    )
    from mastervault.change_control.managed_store import (
        ManagedReviewStaleError as ManagedReviewStaleError,
    )
    from mastervault.change_control.managed_store import (
        ManagedRevisionReviewStoreView as ManagedRevisionReviewStoreView,
    )
    from mastervault.change_control.managed_store import (
        ManagedRevisionStoreLifecycle as ManagedRevisionStoreLifecycle,
    )
    from mastervault.change_control.managed_store import (
        SqliteManagedChangeControlStore as SqliteManagedChangeControlStore,
    )
    from mastervault.change_control.recorded_inference import (
        MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1,
        MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1,
        MAX_OUTCOME_ARTIFACTS_V1,
        MAX_PROVIDER_OUTPUT_BYTES_V1,
        MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1,
        ClassificationWireDecision,
        ClassificationWireResponse,
        DependencySpanWireDecision,
        DependencyWireDecision,
        DependencyWireResponse,
        InferenceArtifactPayload,
        InferenceAttemptEvidence,
        InferenceCorrection,
        InferenceExecutionFailed,
        InferenceInputEnvelope,
        InferenceProviderRequest,
        ProviderCallResult,
        RecordedInferenceExecution,
        RecordedInferenceOutcome,
        RecordedInferenceProvider,
        RecordedInferenceTask,
        ReplayEvidenceResolver,
        run_classification_inference,
        run_dependency_inference,
    )
    from mastervault.change_control.seed import (
        PrechangeSeedDocument as PrechangeSeedDocument,
    )
    from mastervault.change_control.seed import (
        PrechangeSeedManifest as PrechangeSeedManifest,
    )
    from mastervault.change_control.seed import SeedBoundaryError as SeedBoundaryError
    from mastervault.change_control.seed import SeedIntegrityError as SeedIntegrityError
    from mastervault.change_control.seed import (
        SeedMaterializationReport as SeedMaterializationReport,
    )
    from mastervault.change_control.seed import SeedReuseError as SeedReuseError
    from mastervault.change_control.seed import (
        VerifiedDocumentContext as VerifiedDocumentContext,
    )
    from mastervault.change_control.seed import (
        VerifiedPrechangeSeedManifest as VerifiedPrechangeSeedManifest,
    )
    from mastervault.change_control.seed import (
        load_prechange_seed_manifest as load_prechange_seed_manifest,
    )
    from mastervault.change_control.seed import (
        load_verified_prechange_seed_manifest as load_verified_prechange_seed_manifest,
    )
    from mastervault.change_control.seed import (
        materialize_prechange_seed as materialize_prechange_seed,
    )
    from mastervault.change_control.seed import resolve_claim_revision as resolve_claim_revision
    from mastervault.change_control.seed import resolve_document_span as resolve_document_span
    from mastervault.change_control.seed import (
        verify_seed_document_context as verify_seed_document_context,
    )
    from mastervault.change_control.source_note_inventory import (
        PRECHANGE_MANIFEST_RELATIVE_PATH,
        RepositorySourceNoteInventoryResolver,
        RepositoryVerifiedSourceNoteInventoryCapability,
        SourceNoteInventoryResolutionError,
    )
    from mastervault.change_control.store import (
        ChangeControlBusyError as ChangeControlBusyError,
    )
    from mastervault.change_control.store import ChangeControlCommit as ChangeControlCommit
    from mastervault.change_control.store import (
        ChangeControlConflictError as ChangeControlConflictError,
    )
    from mastervault.change_control.store import (
        ChangeControlCorruptionError as ChangeControlCorruptionError,
    )
    from mastervault.change_control.store import (
        ChangeControlIdempotencyError as ChangeControlIdempotencyError,
    )
    from mastervault.change_control.store import (
        ChangeControlReviewAlreadyDecidedError as ChangeControlReviewAlreadyDecidedError,
    )
    from mastervault.change_control.store import (
        ChangeControlReviewMissingError as ChangeControlReviewMissingError,
    )
    from mastervault.change_control.store import (
        ChangeControlReviewStaleError as ChangeControlReviewStaleError,
    )
    from mastervault.change_control.store import (
        ChangeControlReviewTransitionError as ChangeControlReviewTransitionError,
    )
    from mastervault.change_control.store import (
        ChangeControlSnapshot as ChangeControlSnapshot,
    )
    from mastervault.change_control.store import ChangeControlStoreError as ChangeControlStoreError
    from mastervault.change_control.store import (
        SqliteChangeControlStore as SqliteChangeControlStore,
    )
    from mastervault.change_control.temporal_analysis import (
        MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
        TemporalAnalysisEvidence,
        build_temporal_analysis_evidence,
        verify_temporal_analysis_evidence,
    )
    from mastervault.change_control.temporal_commit import (
        TemporalProposalAuthorityError,
        commit_temporal_proposal,
    )
    from mastervault.change_control.temporal_proposal import (
        DocumentReplacementProposalCandidate,
        InferenceExecutionRef,
        TemporalProposal,
        TemporalProposalBinding,
        TemporalProposalCommit,
        build_temporal_proposal,
        open_temporal_review,
        temporal_prerequisite_from_decision,
    )
    from mastervault.change_control.workflow import CheckpointHealth as CheckpointHealth
    from mastervault.change_control.workflow import OrchestrationPhase as OrchestrationPhase
    from mastervault.change_control.workflow import (
        TemporalReviewAuthorityError as TemporalReviewAuthorityError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewCheckpointCorruptionError as TemporalReviewCheckpointCorruptionError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewCheckpointError as TemporalReviewCheckpointError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewClosedError as TemporalReviewClosedError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewNotStartedError as TemporalReviewNotStartedError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewPathConflictError as TemporalReviewPathConflictError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewWorkflow as TemporalReviewWorkflow,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewWorkflowError as TemporalReviewWorkflowError,
    )
    from mastervault.change_control.workflow import (
        TemporalReviewWorkflowStatus as TemporalReviewWorkflowStatus,
    )
    from mastervault.change_control.workflow import (
        temporal_review_workflow_id as temporal_review_workflow_id,
    )

_LAZY_EXPORTS = {
    **{
        name: ("mastervault.change_control.analysis_binding", name)
        for name in (
            "ANALYSIS_AGGREGATE_ID",
            "AnalysisBootstrapBinding",
            "AnalysisBootstrapError",
            "AnalysisBootstrapIntegrityError",
        )
    },
    **{
        name: ("mastervault.change_control.seed", name)
        for name in (
            "PrechangeSeedDocument",
            "PrechangeSeedManifest",
            "SeedBoundaryError",
            "SeedIntegrityError",
            "SeedMaterializationReport",
            "SeedReuseError",
            "VerifiedDocumentContext",
            "VerifiedPrechangeSeedManifest",
            "load_prechange_seed_manifest",
            "load_verified_prechange_seed_manifest",
            "materialize_prechange_seed",
            "resolve_claim_revision",
            "resolve_document_span",
            "verify_seed_document_context",
        )
    },
    **{
        name: ("mastervault.change_control.bootstrap", name)
        for name in (
            "AnalysisBootstrapResult",
            "AnalysisBootstrapStaleError",
            "VerifiedAnalysisBootstrapCapability",
            "bootstrap_analysis_aggregate",
            "build_verified_prechange_aggregate",
            "create_verified_analysis_bootstrap_binding",
            "create_verified_analysis_bootstrap_capability",
            "incoming_claim_evidence_sha256",
            "verify_analysis_bootstrap_snapshot",
            "verify_generation_zero_authority",
        )
    },
    **{
        name: ("mastervault.change_control.source_note_inventory", name)
        for name in (
            "PRECHANGE_MANIFEST_RELATIVE_PATH",
            "RepositorySourceNoteInventoryResolver",
            "RepositoryVerifiedSourceNoteInventoryCapability",
            "SourceNoteInventoryResolutionError",
        )
    },
    **{
        name: ("mastervault.change_control.recorded_inference", name)
        for name in (
            "ClassificationWireDecision",
            "ClassificationWireResponse",
            "DependencySpanWireDecision",
            "DependencyWireDecision",
            "DependencyWireResponse",
            "InferenceArtifactPayload",
            "InferenceAttemptEvidence",
            "InferenceCorrection",
            "InferenceExecutionFailed",
            "InferenceInputEnvelope",
            "InferenceProviderRequest",
            "MAX_OUTCOME_ARTIFACTS_V1",
            "MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1",
            "MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1",
            "MAX_PROVIDER_OUTPUT_BYTES_V1",
            "MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1",
            "ProviderCallResult",
            "RecordedInferenceExecution",
            "RecordedInferenceOutcome",
            "RecordedInferenceProvider",
            "RecordedInferenceTask",
            "ReplayEvidenceResolver",
            "run_classification_inference",
            "run_dependency_inference",
        )
    },
    **{
        name: ("mastervault.change_control.inference_repository", name)
        for name in (
            "FilesystemInferenceEvidenceRepository",
            "InferenceEvidenceConflictError",
            "InferenceEvidenceRepositoryError",
            "InferenceEvidenceResolutionError",
            "InferenceEvidenceUnsupportedPlatformError",
            "MAX_COMMITTED_BATCH_MANIFESTS_V1",
            "MAX_COMMITTED_BATCH_SCAN_BYTES_V1",
            "MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1",
            "MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1",
            "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1",
            "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1",
            "MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1",
            "MAX_INFERENCE_EVIDENCE_BATCH_V1",
            "MAX_INFERENCE_INPUT_METADATA_DEPTH_V1",
            "MAX_INFERENCE_INPUT_METADATA_NODES_V1",
            "MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1",
            "MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1",
            "MAX_PENDING_FILES_PER_DIRECTORY_V1",
            "RepositoryVerifiedInferenceEvidenceBatch",
        )
    },
    **{
        name: ("mastervault.change_control.temporal_analysis", name)
        for name in (
            "MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1",
            "TemporalAnalysisEvidence",
            "build_temporal_analysis_evidence",
            "verify_temporal_analysis_evidence",
        )
    },
    **{
        name: ("mastervault.change_control.temporal_proposal", name)
        for name in (
            "DocumentReplacementProposalCandidate",
            "InferenceExecutionRef",
            "TemporalProposal",
            "TemporalProposalBinding",
            "TemporalProposalCommit",
            "build_temporal_proposal",
            "open_temporal_review",
            "temporal_prerequisite_from_decision",
        )
    },
    **{
        name: ("mastervault.change_control.temporal_commit", name)
        for name in (
            "TemporalProposalAuthorityError",
            "commit_temporal_proposal",
        )
    },
    **{
        name: ("mastervault.change_control.store", name)
        for name in (
            "ChangeControlBusyError",
            "ChangeControlCommit",
            "ChangeControlConflictError",
            "ChangeControlCorruptionError",
            "ChangeControlIdempotencyError",
            "ChangeControlReviewAlreadyDecidedError",
            "ChangeControlReviewMissingError",
            "ChangeControlReviewStaleError",
            "ChangeControlReviewTransitionError",
            "ChangeControlSnapshot",
            "ChangeControlStoreError",
            "SqliteChangeControlStore",
        )
    },
    **{
        name: ("mastervault.change_control.managed_store", name)
        for name in (
            "ManagedReviewAuthorityError",
            "ManagedReviewRepositoryResolver",
            "ManagedReviewStaleError",
            "ManagedRevisionReviewStoreView",
            "ManagedRevisionStoreLifecycle",
            "SqliteManagedChangeControlStore",
        )
    },
    **{
        name: ("mastervault.change_control.workflow", name)
        for name in (
            "CheckpointHealth",
            "OrchestrationPhase",
            "TemporalReviewAuthorityError",
            "TemporalReviewCheckpointCorruptionError",
            "TemporalReviewCheckpointError",
            "TemporalReviewClosedError",
            "TemporalReviewNotStartedError",
            "TemporalReviewPathConflictError",
            "TemporalReviewWorkflow",
            "TemporalReviewWorkflowError",
            "TemporalReviewWorkflowStatus",
            "temporal_review_workflow_id",
        )
    },
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    from importlib import import_module

    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = [
    "ANALYSIS_AGGREGATE_ID",
    "CLAIM_SCOPE_POLICY_VERSION",
    "MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1",
    "MAX_CLASSIFICATION_RATIONALE_CHARS_V1",
    "MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1",
    "MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1",
    "MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_WORKLOAD_PAIRS_V1",
    "MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1",
    "AnalysisBootstrapBinding",
    "AnalysisBootstrapError",
    "AnalysisBootstrapIntegrityError",
    "AnalysisBootstrapResult",
    "AnalysisBootstrapStaleError",
    "VerifiedAnalysisBootstrapCapability",
    "ChangeControlAggregate",
    "CandidateExclusionReason",
    "CandidateSelectionReason",
    "ClaimPairClassification",
    "ClassificationInferencePair",
    "ClassificationInferenceShard",
    "ClassificationLimitError",
    "ClassificationOutputItem",
    "ClassificationOutputShard",
    "ClassificationOutputShardRef",
    "ClassificationResultIndex",
    "ClassificationResultSet",
    "ClassificationWorkload",
    "ClassifierSelectorVersion",
    "ChangeControlBusyError",
    "ChangeControlCommit",
    "ChangeControlConflictError",
    "ChangeControlCorruptionError",
    "ChangeControlIdempotencyError",
    "ChangeControlReviewAlreadyDecidedError",
    "ChangeControlReviewMissingError",
    "ChangeControlReviewStaleError",
    "ChangeControlReviewTransitionError",
    "ChangeControlSnapshot",
    "ChangeControlStoreError",
    "CheckpointHealth",
    "ClaimRevisionRegistry",
    "ClaimSourceReference",
    "ComparableClaimPair",
    "DependencyAssessment",
    "DependencyKind",
    "DependencyRegistry",
    "DiscoveryLimitError",
    "DocumentAuthority",
    "DocumentReplacementAssessment",
    "DocumentReplacementSet",
    "DocumentRole",
    "DocumentSpanReference",
    "DocumentVersionMetadata",
    "DocumentVersionRegistry",
    "EndpointEvidenceBinding",
    "ExcludedCandidateRef",
    "DocumentAttentionCandidate",
    "DocumentAttentionRanking",
    "HumanReviewDecision",
    "HumanReviewDecisionCommand",
    "HumanReviewDecisionReceipt",
    "HumanReviewRequest",
    "HumanReviewRequestCommand",
    "HumanReviewRequestReceipt",
    "HumanReviewRequestView",
    "GraphMaterializationStatus",
    "ManagedReviewAuthorityError",
    "ManagedReviewRepositoryResolver",
    "ManagedReviewStaleError",
    "ManagedRevisionReviewStoreView",
    "ManagedRevisionStoreLifecycle",
    "OrchestrationPhase",
    "PairDisposition",
    "PersistedRelationType",
    "PrechangeSeedDocument",
    "PrechangeSeedManifest",
    "RelationAssessment",
    "RelationGraph",
    "RelationshipCandidate",
    "RelationshipCandidateSet",
    "SelectedCandidateRef",
    "ReviewDecisionItem",
    "ReviewDisposition",
    "ReviewLifecycle",
    "ReviewSubjectEdit",
    "ReviewSubjectKind",
    "ReviewSubjectRef",
    "ReviewSubjectSnapshot",
    "SeedBoundaryError",
    "SeedIntegrityError",
    "SeedMaterializationReport",
    "SeedReuseError",
    "SqliteChangeControlStore",
    "SqliteManagedChangeControlStore",
    "TemporalConstraint",
    "TemporalConstraintSet",
    "TemporalConstraintStatus",
    "TemporalReviewAuthorityError",
    "TemporalReviewCheckpointCorruptionError",
    "TemporalReviewCheckpointError",
    "TemporalReviewClosedError",
    "TemporalReviewNotStartedError",
    "TemporalReviewPathConflictError",
    "TemporalReviewWorkflow",
    "TemporalReviewWorkflowError",
    "TemporalReviewWorkflowStatus",
    "TemporalResolution",
    "TemporalState",
    "TemporalTarget",
    "TemporalTargetKind",
    "VerifiedDocumentContext",
    "VerifiedPrechangeSeedManifest",
    "ValidatedTemporalConstraintSet",
    "VersionedClaimRevision",
    "aggregate_sha256",
    "apply_human_review_decision",
    "canonical_json_bytes",
    "claim_scopes_v1",
    "bootstrap_analysis_aggregate",
    "build_verified_prechange_aggregate",
    "create_verified_analysis_bootstrap_binding",
    "create_verified_analysis_bootstrap_capability",
    "generate_relationship_candidates",
    "load_prechange_seed_manifest",
    "load_verified_prechange_seed_manifest",
    "materialize_relation_assessments",
    "human_review_decision_payload_sha256",
    "human_review_request_id",
    "incoming_claim_evidence_sha256",
    "verify_generation_zero_authority",
    "materialize_prechange_seed",
    "normalize_logical_key",
    "normalize_actor_id",
    "normalize_review_rationale",
    "normalize_semantic_text",
    "resolve_claim_temporality",
    "resolve_claim_revision",
    "resolve_document_span",
    "resolve_document_temporality",
    "rank_document_attention",
    "relationship_candidate_sha256",
    "review_subject_sha256",
    "select_classification_workload",
    "stable_content_id",
    "temporal_review_workflow_id",
    "validate_document_attention_ranking",
    "validate_classification_results",
    "validate_classification_workload",
    "verify_seed_document_context",
    "MAX_DEPENDENCY_CANDIDATES_V1",
    "MAX_DEPENDENCY_DOCUMENT_SHARDS_V1",
    "MAX_DEPENDENCY_INDEX_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_DEPENDENCY_RATIONALE_UTF8_BYTES_V1",
    "MAX_DEPENDENCY_TOTAL_INPUT_BYTES_V1",
    "CanonicalSourceNoteSnapshot",
    "DependencyAnalysisLimitError",
    "DependencyCandidate",
    "DependencyCandidateExclusionReason",
    "DependencyClassification",
    "DependencyClassificationResultSet",
    "DependencyDisposition",
    "DependencyExclusionRef",
    "DependencyInferenceShard",
    "DependencyOutputShard",
    "DependencyOutputShardRef",
    "DependencyResultIndex",
    "DependencySourceInventoryResolver",
    "DependencyWorkload",
    "DependencyWorkloadIndex",
    "ExcludedDependencyDocument",
    "GoverningSupersessionRef",
    "SelectedNeighbourRef",
    "SourceNoteInventory",
    "VerifiedSourceNoteInventoryCapability",
    "generate_dependency_workload",
    "materialize_dependencies",
    "validate_dependency_results",
    "validate_dependency_workload",
    "PRECHANGE_MANIFEST_RELATIVE_PATH",
    "RepositorySourceNoteInventoryResolver",
    "RepositoryVerifiedSourceNoteInventoryCapability",
    "SourceNoteInventoryResolutionError",
    "ClassificationWireDecision",
    "ClassificationWireResponse",
    "DependencySpanWireDecision",
    "DependencyWireDecision",
    "DependencyWireResponse",
    "InferenceArtifactPayload",
    "InferenceAttemptEvidence",
    "InferenceCorrection",
    "InferenceExecutionFailed",
    "InferenceInputEnvelope",
    "InferenceProviderRequest",
    "MAX_OUTCOME_ARTIFACTS_V1",
    "MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1",
    "MAX_OUTCOME_ARTIFACT_CONTENT_BYTES_V1",
    "MAX_PROVIDER_OUTPUT_BYTES_V1",
    "MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1",
    "ProviderCallResult",
    "RecordedInferenceExecution",
    "RecordedInferenceOutcome",
    "RecordedInferenceProvider",
    "RecordedInferenceTask",
    "ReplayEvidenceResolver",
    "run_classification_inference",
    "run_dependency_inference",
    "FilesystemInferenceEvidenceRepository",
    "InferenceEvidenceConflictError",
    "InferenceEvidenceRepositoryError",
    "InferenceEvidenceResolutionError",
    "InferenceEvidenceUnsupportedPlatformError",
    "MAX_COMMITTED_BATCH_MANIFESTS_V1",
    "MAX_COMMITTED_BATCH_SCAN_BYTES_V1",
    "MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_V1",
    "MAX_INFERENCE_INPUT_METADATA_DEPTH_V1",
    "MAX_INFERENCE_INPUT_METADATA_NODES_V1",
    "MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1",
    "MAX_PENDING_FILES_PER_DIRECTORY_V1",
    "RepositoryVerifiedInferenceEvidenceBatch",
    "MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1",
    "TemporalAnalysisEvidence",
    "build_temporal_analysis_evidence",
    "verify_temporal_analysis_evidence",
    "DocumentReplacementProposalCandidate",
    "InferenceExecutionRef",
    "TemporalProposal",
    "TemporalProposalBinding",
    "TemporalProposalCommit",
    "build_temporal_proposal",
    "open_temporal_review",
    "temporal_prerequisite_from_decision",
    "TemporalProposalAuthorityError",
    "commit_temporal_proposal",
    "verify_analysis_bootstrap_snapshot",
]
