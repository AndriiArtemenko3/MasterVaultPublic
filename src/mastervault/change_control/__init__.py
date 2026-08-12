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
    from mastervault.change_control.application import BootstrapResult as BootstrapResult
    from mastervault.change_control.application import (
        ChangeControlApplication as ChangeControlApplication,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationConflictError as ChangeControlApplicationConflictError,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationError as ChangeControlApplicationError,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationErrorCode as ChangeControlApplicationErrorCode,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationIntegrityError as ChangeControlApplicationIntegrityError,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationReviewRequiredError as ChangeControlApplicationReviewRequiredError,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationUnsupportedOperationError as ChangeControlApplicationUnsupportedOperationError,
    )
    from mastervault.change_control.application_errors import (
        ChangeControlApplicationUsageError as ChangeControlApplicationUsageError,
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
    from mastervault.change_control.impact_analysis import (
        MAX_IMPACT_DOCUMENT_SHARDS_V1,
        MAX_IMPACT_INDEX_CANONICAL_BYTES_V1,
        MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1,
        MAX_IMPACT_QUESTIONS_V1,
        MAX_IMPACT_TOTAL_INPUT_BYTES_V1,
        AcceptedGoverningChange,
        ExcludedImpactQuestion,
        ImpactAnalysisLimitError,
        ImpactAttentionStatus,
        ImpactExclusionReason,
        ImpactExclusionRef,
        ImpactInferenceShard,
        ImpactQuestion,
        ImpactQuestionRef,
        ImpactWorkload,
        ImpactWorkloadBinding,
        ImpactWorkloadIndex,
        build_impact_workload,
        validate_impact_workload,
    )
    from mastervault.change_control.impact_inference import (
        ImpactReplaySourceBinding,
        RecordedImpactInferenceRun,
        execute_impact_workload,
    )
    from mastervault.change_control.impact_results import (
        MAX_IMPACT_DECISION_CANONICAL_BYTES_V1,
        MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1,
        MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1,
        MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1,
        MAX_IMPACT_RATIONALE_UTF8_BYTES_V1,
        MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1,
        MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1,
        ImpactDecision,
        ImpactDisposition,
        ImpactOutputShard,
        ImpactOutputShardRef,
        ImpactResultIndex,
        ImpactResultLimitError,
        ImpactResultSet,
        validate_impact_results,
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
    from mastervault.change_control.managed_activation_service import (
        ManagedActivationBackendUnsupportedError as ManagedActivationBackendUnsupportedError,
    )
    from mastervault.change_control.managed_activation_service import (
        ManagedActivationOutcome as ManagedActivationOutcome,
    )
    from mastervault.change_control.managed_activation_service import (
        ManagedActivationServiceError as ManagedActivationServiceError,
    )
    from mastervault.change_control.managed_activation_service import (
        ManagedActivationServiceResult as ManagedActivationServiceResult,
    )
    from mastervault.change_control.managed_activation_service import (
        activate_reviewed_managed_generation as activate_reviewed_managed_generation,
    )
    from mastervault.change_control.managed_generation import (
        ManagedActivationCommand as ManagedActivationCommand,
    )
    from mastervault.change_control.managed_generation import (
        ManagedGenerationActivationReceipt as ManagedGenerationActivationReceipt,
    )
    from mastervault.change_control.managed_generation import (
        ManagedIndexReadinessReceipt as ManagedIndexReadinessReceipt,
    )
    from mastervault.change_control.managed_generation import (
        ManagedPublicationEvent as ManagedPublicationEvent,
    )
    from mastervault.change_control.managed_generation import (
        ResolvedManagedGenerationProjection as ResolvedManagedGenerationProjection,
    )
    from mastervault.change_control.managed_generation import (
        derive_managed_generation_projection as derive_managed_generation_projection,
    )
    from mastervault.change_control.managed_generation_repository import (
        ManagedGenerationIndexError as ManagedGenerationIndexError,
    )
    from mastervault.change_control.managed_generation_repository import (
        ManagedGenerationRepository as ManagedGenerationRepository,
    )
    from mastervault.change_control.managed_generation_repository import (
        ManagedGenerationRepositoryConflictError as ManagedGenerationRepositoryConflictError,
    )
    from mastervault.change_control.managed_generation_repository import (
        ManagedGenerationRepositoryError as ManagedGenerationRepositoryError,
    )
    from mastervault.change_control.managed_generation_repository import (
        RepositoryVerifiedManagedGenerationEffects as RepositoryVerifiedManagedGenerationEffects,
    )
    from mastervault.change_control.managed_review import (
        ManagedGenerationManifest as ManagedGenerationManifest,
    )
    from mastervault.change_control.managed_review import (
        ManagedGenerationManifestBindingV2 as ManagedGenerationManifestBindingV2,
    )
    from mastervault.change_control.managed_review import (
        ManagedGoverningSourceAdoptionBinding as ManagedGoverningSourceAdoptionBinding,
    )
    from mastervault.change_control.managed_review import ManagedRun as ManagedRun
    from mastervault.change_control.managed_review_repository import (
        ApprovedManagedGoverningSourceAuthority as ApprovedManagedGoverningSourceAuthority,
    )
    from mastervault.change_control.managed_review_repository import (
        ApprovedManagedInferenceContractAuthority as ApprovedManagedInferenceContractAuthority,
    )
    from mastervault.change_control.managed_review_repository import (
        RepositoryBackedManagedReviewResolver as RepositoryBackedManagedReviewResolver,
    )
    from mastervault.change_control.managed_review_repository import (
        derive_managed_governing_source_adoption as derive_managed_governing_source_adoption,
    )
    from mastervault.change_control.managed_review_service import (
        ManagedReviewSelectionError as ManagedReviewSelectionError,
    )
    from mastervault.change_control.managed_review_service import (
        ManagedReviewServiceError as ManagedReviewServiceError,
    )
    from mastervault.change_control.managed_review_service import (
        ManagedRevisionReviewSelection as ManagedRevisionReviewSelection,
    )
    from mastervault.change_control.managed_review_service import (
        decide_managed_revision_review as decide_managed_revision_review,
    )
    from mastervault.change_control.managed_review_service import (
        open_managed_revision_review as open_managed_revision_review,
    )
    from mastervault.change_control.managed_serving import (
        ManagedServingError as ManagedServingError,
    )
    from mastervault.change_control.managed_serving import (
        ManagedServingGenerationZeroError as ManagedServingGenerationZeroError,
    )
    from mastervault.change_control.managed_serving import (
        open_active_managed_sqlite_index as open_active_managed_sqlite_index,
    )
    from mastervault.change_control.managed_staging_repository import (
        ManagedStagingCompletionBinding,
        ManagedStagingManifest,
        ManagedStagingRepository,
        VerifiedManagedStagingCapability,
    )
    from mastervault.change_control.managed_store import (
        ManagedGenerationActivationError as ManagedGenerationActivationError,
    )
    from mastervault.change_control.managed_store import (
        ManagedGenerationActivationStaleError as ManagedGenerationActivationStaleError,
    )
    from mastervault.change_control.managed_store import (
        ManagedGenerationActivationState as ManagedGenerationActivationState,
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
        ManagedReviewWriteVersionError as ManagedReviewWriteVersionError,
    )
    from mastervault.change_control.managed_store import (
        ManagedRevisionEditDeferredError as ManagedRevisionEditDeferredError,
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
    from mastervault.change_control.managed_workflow import (
        ManagedReviewCheckpointHealth as ManagedReviewCheckpointHealth,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewOrchestrationPhase as ManagedReviewOrchestrationPhase,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflow as ManagedReviewWorkflow,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowAuthorityError as ManagedReviewWorkflowAuthorityError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowCheckpointCorruptionError as ManagedReviewWorkflowCheckpointCorruptionError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowCheckpointError as ManagedReviewWorkflowCheckpointError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowClosedError as ManagedReviewWorkflowClosedError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowError as ManagedReviewWorkflowError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowNotStartedError as ManagedReviewWorkflowNotStartedError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowPathConflictError as ManagedReviewWorkflowPathConflictError,
    )
    from mastervault.change_control.managed_workflow import (
        ManagedReviewWorkflowStatus as ManagedReviewWorkflowStatus,
    )
    from mastervault.change_control.managed_workflow import (
        managed_review_workflow_id as managed_review_workflow_id,
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
        ImpactSpanWireDecision,
        ImpactWireDecision,
        ImpactWireResponse,
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
        run_impact_inference,
        run_revision_planning_inference,
    )
    from mastervault.change_control.reviewed_snapshot import (
        RepositoryVerifiedReviewedSourceNoteInventoryCapability,
        ReviewedTemporalSnapshotAuthority,
        ReviewedTemporalSnapshotAuthorityError,
        ReviewedTemporalSnapshotBinding,
        resolve_reviewed_temporal_snapshot,
    )
    from mastervault.change_control.revision_planning_inference import (
        RecordedRevisionPlanningInferenceRun,
        RevisionPlanningPredecessorSnapshot,
        RevisionPlanningReplaySourceBinding,
        RevisionPlanningSubject,
        execute_revision_planning,
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
        ChangeControlPlatformUnsupportedError as ChangeControlPlatformUnsupportedError,
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
    from mastervault.change_control.workspace_bootstrap_repository import (
        BootstrapSourceRoot as BootstrapSourceRoot,
    )

_LAZY_EXPORTS = {
    **{
        name: ("mastervault.change_control.application", name)
        for name in (
            "BootstrapResult",
            "ChangeControlApplication",
        )
    },
    **{
        name: ("mastervault.change_control.application_errors", name)
        for name in (
            "ChangeControlApplicationConflictError",
            "ChangeControlApplicationError",
            "ChangeControlApplicationErrorCode",
            "ChangeControlApplicationIntegrityError",
            "ChangeControlApplicationReviewRequiredError",
            "ChangeControlApplicationUnsupportedOperationError",
            "ChangeControlApplicationUsageError",
        )
    },
    **{
        name: ("mastervault.change_control.workspace_bootstrap_repository", name)
        for name in ("BootstrapSourceRoot",)
    },
    **{
        name: ("mastervault.change_control.managed_activation_service", name)
        for name in (
            "ManagedActivationBackendUnsupportedError",
            "ManagedActivationOutcome",
            "ManagedActivationServiceError",
            "ManagedActivationServiceResult",
            "activate_reviewed_managed_generation",
        )
    },
    **{
        name: ("mastervault.change_control.managed_generation", name)
        for name in (
            "ManagedActivationCommand",
            "ManagedGenerationActivationReceipt",
            "ManagedIndexReadinessReceipt",
            "ManagedPublicationEvent",
            "ResolvedManagedGenerationProjection",
            "derive_managed_generation_projection",
        )
    },
    **{
        name: ("mastervault.change_control.managed_generation_repository", name)
        for name in (
            "ManagedGenerationIndexError",
            "ManagedGenerationRepository",
            "ManagedGenerationRepositoryConflictError",
            "ManagedGenerationRepositoryError",
            "RepositoryVerifiedManagedGenerationEffects",
        )
    },
    **{
        name: ("mastervault.change_control.managed_serving", name)
        for name in (
            "ManagedServingError",
            "ManagedServingGenerationZeroError",
            "open_active_managed_sqlite_index",
        )
    },
    **{
        name: ("mastervault.change_control.impact_analysis", name)
        for name in (
            "MAX_IMPACT_DOCUMENT_SHARDS_V1",
            "MAX_IMPACT_INDEX_CANONICAL_BYTES_V1",
            "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
            "MAX_IMPACT_QUESTIONS_V1",
            "MAX_IMPACT_TOTAL_INPUT_BYTES_V1",
            "ExcludedImpactQuestion",
            "AcceptedGoverningChange",
            "ImpactAnalysisLimitError",
            "ImpactAttentionStatus",
            "ImpactExclusionReason",
            "ImpactExclusionRef",
            "ImpactInferenceShard",
            "ImpactQuestion",
            "ImpactQuestionRef",
            "ImpactWorkload",
            "ImpactWorkloadBinding",
            "ImpactWorkloadIndex",
            "build_impact_workload",
            "validate_impact_workload",
        )
    },
    **{
        name: ("mastervault.change_control.impact_results", name)
        for name in (
            "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
            "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
            "MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1",
            "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
            "MAX_IMPACT_RATIONALE_UTF8_BYTES_V1",
            "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
            "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
            "ImpactDecision",
            "ImpactDisposition",
            "ImpactOutputShard",
            "ImpactOutputShardRef",
            "ImpactResultIndex",
            "ImpactResultLimitError",
            "ImpactResultSet",
            "validate_impact_results",
        )
    },
    **{
        name: ("mastervault.change_control.impact_inference", name)
        for name in (
            "ImpactReplaySourceBinding",
            "RecordedImpactInferenceRun",
            "execute_impact_workload",
        )
    },
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
        name: ("mastervault.change_control.reviewed_snapshot", name)
        for name in (
            "RepositoryVerifiedReviewedSourceNoteInventoryCapability",
            "ReviewedTemporalSnapshotAuthority",
            "ReviewedTemporalSnapshotAuthorityError",
            "ReviewedTemporalSnapshotBinding",
            "resolve_reviewed_temporal_snapshot",
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
            "ImpactSpanWireDecision",
            "ImpactWireDecision",
            "ImpactWireResponse",
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
            "run_impact_inference",
            "run_revision_planning_inference",
        )
    },
    **{
        name: ("mastervault.change_control.revision_planning_inference", name)
        for name in (
            "RecordedRevisionPlanningInferenceRun",
            "RevisionPlanningPredecessorSnapshot",
            "RevisionPlanningReplaySourceBinding",
            "RevisionPlanningSubject",
            "execute_revision_planning",
        )
    },
    **{
        name: ("mastervault.change_control.managed_staging_repository", name)
        for name in (
            "ManagedStagingCompletionBinding",
            "ManagedStagingManifest",
            "ManagedStagingRepository",
            "VerifiedManagedStagingCapability",
        )
    },
    **{
        name: ("mastervault.change_control.managed_review", name)
        for name in (
            "ManagedGenerationManifest",
            "ManagedGenerationManifestBindingV2",
            "ManagedGoverningSourceAdoptionBinding",
            "ManagedRun",
        )
    },
    **{
        name: ("mastervault.change_control.managed_review_repository", name)
        for name in (
            "ApprovedManagedGoverningSourceAuthority",
            "ApprovedManagedInferenceContractAuthority",
            "RepositoryBackedManagedReviewResolver",
            "derive_managed_governing_source_adoption",
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
            "ChangeControlPlatformUnsupportedError",
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
            "ManagedGenerationActivationError",
            "ManagedGenerationActivationStaleError",
            "ManagedGenerationActivationState",
            "ManagedReviewAuthorityError",
            "ManagedReviewRepositoryResolver",
            "ManagedReviewStaleError",
            "ManagedReviewWriteVersionError",
            "ManagedRevisionEditDeferredError",
            "ManagedRevisionReviewStoreView",
            "ManagedRevisionStoreLifecycle",
            "SqliteManagedChangeControlStore",
        )
    },
    **{
        name: ("mastervault.change_control.managed_review_service", name)
        for name in (
            "ManagedReviewSelectionError",
            "ManagedReviewServiceError",
            "ManagedRevisionReviewSelection",
            "decide_managed_revision_review",
            "open_managed_revision_review",
        )
    },
    **{
        name: ("mastervault.change_control.managed_workflow", name)
        for name in (
            "ManagedReviewCheckpointHealth",
            "ManagedReviewOrchestrationPhase",
            "ManagedReviewWorkflow",
            "ManagedReviewWorkflowAuthorityError",
            "ManagedReviewWorkflowCheckpointCorruptionError",
            "ManagedReviewWorkflowCheckpointError",
            "ManagedReviewWorkflowClosedError",
            "ManagedReviewWorkflowError",
            "ManagedReviewWorkflowNotStartedError",
            "ManagedReviewWorkflowPathConflictError",
            "ManagedReviewWorkflowStatus",
            "managed_review_workflow_id",
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
    "BootstrapSourceRoot",
    "BootstrapResult",
    "ChangeControlApplication",
    "ChangeControlApplicationConflictError",
    "ChangeControlApplicationError",
    "ChangeControlApplicationErrorCode",
    "ChangeControlApplicationIntegrityError",
    "ChangeControlApplicationReviewRequiredError",
    "ChangeControlApplicationUnsupportedOperationError",
    "ChangeControlApplicationUsageError",
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
    "MAX_IMPACT_DOCUMENT_SHARDS_V1",
    "MAX_IMPACT_INDEX_CANONICAL_BYTES_V1",
    "MAX_IMPACT_INPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_IMPACT_QUESTIONS_V1",
    "MAX_IMPACT_TOTAL_INPUT_BYTES_V1",
    "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
    "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
    "MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1",
    "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_IMPACT_RATIONALE_UTF8_BYTES_V1",
    "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
    "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
    "MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1",
    "AnalysisBootstrapBinding",
    "AnalysisBootstrapError",
    "AnalysisBootstrapIntegrityError",
    "AnalysisBootstrapResult",
    "AnalysisBootstrapStaleError",
    "ApprovedManagedGoverningSourceAuthority",
    "ApprovedManagedInferenceContractAuthority",
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
    "ChangeControlPlatformUnsupportedError",
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
    "ExcludedImpactQuestion",
    "AcceptedGoverningChange",
    "ImpactAnalysisLimitError",
    "ImpactAttentionStatus",
    "ImpactExclusionReason",
    "ImpactExclusionRef",
    "ImpactInferenceShard",
    "ImpactQuestion",
    "ImpactQuestionRef",
    "ImpactWorkload",
    "ImpactWorkloadBinding",
    "ImpactWorkloadIndex",
    "ImpactDecision",
    "ImpactDisposition",
    "ImpactOutputShard",
    "ImpactOutputShardRef",
    "ImpactResultIndex",
    "ImpactResultLimitError",
    "ImpactResultSet",
    "ImpactReplaySourceBinding",
    "RecordedImpactInferenceRun",
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
    "ManagedActivationBackendUnsupportedError",
    "ManagedActivationCommand",
    "ManagedActivationOutcome",
    "ManagedActivationServiceError",
    "ManagedActivationServiceResult",
    "ManagedGenerationActivationReceipt",
    "ManagedGenerationActivationError",
    "ManagedGenerationActivationStaleError",
    "ManagedGenerationActivationState",
    "ManagedGenerationIndexError",
    "ManagedGenerationRepository",
    "ManagedGenerationRepositoryConflictError",
    "ManagedGenerationRepositoryError",
    "RepositoryVerifiedManagedGenerationEffects",
    "ManagedIndexReadinessReceipt",
    "ManagedPublicationEvent",
    "ManagedServingError",
    "ManagedServingGenerationZeroError",
    "ResolvedManagedGenerationProjection",
    "ManagedReviewAuthorityError",
    "ManagedReviewCheckpointHealth",
    "ManagedReviewOrchestrationPhase",
    "ManagedReviewRepositoryResolver",
    "ManagedReviewSelectionError",
    "ManagedReviewServiceError",
    "ManagedReviewStaleError",
    "ManagedReviewWorkflow",
    "ManagedReviewWorkflowAuthorityError",
    "ManagedReviewWorkflowCheckpointCorruptionError",
    "ManagedReviewWorkflowCheckpointError",
    "ManagedReviewWorkflowClosedError",
    "ManagedReviewWorkflowError",
    "ManagedReviewWorkflowNotStartedError",
    "ManagedReviewWorkflowPathConflictError",
    "ManagedReviewWorkflowStatus",
    "ManagedReviewWriteVersionError",
    "ManagedGenerationManifest",
    "ManagedGenerationManifestBindingV2",
    "ManagedGoverningSourceAdoptionBinding",
    "ManagedRevisionEditDeferredError",
    "ManagedRevisionReviewSelection",
    "ManagedRevisionReviewStoreView",
    "ManagedRevisionStoreLifecycle",
    "ManagedRun",
    "OrchestrationPhase",
    "PairDisposition",
    "PersistedRelationType",
    "PrechangeSeedDocument",
    "PrechangeSeedManifest",
    "RelationAssessment",
    "RelationGraph",
    "RelationshipCandidate",
    "RelationshipCandidateSet",
    "RepositoryBackedManagedReviewResolver",
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
    "activate_reviewed_managed_generation",
    "apply_human_review_decision",
    "canonical_json_bytes",
    "claim_scopes_v1",
    "derive_managed_governing_source_adoption",
    "derive_managed_generation_projection",
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
    "build_impact_workload",
    "decide_managed_revision_review",
    "materialize_dependencies",
    "managed_review_workflow_id",
    "open_managed_revision_review",
    "open_active_managed_sqlite_index",
    "validate_dependency_results",
    "validate_dependency_workload",
    "validate_impact_workload",
    "validate_impact_results",
    "execute_impact_workload",
    "PRECHANGE_MANIFEST_RELATIVE_PATH",
    "RepositorySourceNoteInventoryResolver",
    "RepositoryVerifiedSourceNoteInventoryCapability",
    "SourceNoteInventoryResolutionError",
    "ClassificationWireDecision",
    "ClassificationWireResponse",
    "DependencySpanWireDecision",
    "DependencyWireDecision",
    "DependencyWireResponse",
    "ImpactSpanWireDecision",
    "ImpactWireDecision",
    "ImpactWireResponse",
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
    "RepositoryVerifiedReviewedSourceNoteInventoryCapability",
    "ReviewedTemporalSnapshotAuthority",
    "ReviewedTemporalSnapshotAuthorityError",
    "ReviewedTemporalSnapshotBinding",
    "run_classification_inference",
    "run_dependency_inference",
    "run_impact_inference",
    "run_revision_planning_inference",
    "RecordedRevisionPlanningInferenceRun",
    "RevisionPlanningPredecessorSnapshot",
    "RevisionPlanningReplaySourceBinding",
    "RevisionPlanningSubject",
    "execute_revision_planning",
    "ManagedStagingCompletionBinding",
    "ManagedStagingManifest",
    "ManagedStagingRepository",
    "VerifiedManagedStagingCapability",
    "resolve_reviewed_temporal_snapshot",
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
