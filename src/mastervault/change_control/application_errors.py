"""Stable errors exposed by the change-control application boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import NoReturn


class ChangeControlApplicationErrorCode(StrEnum):
    """Small public taxonomy independent of internal implementation errors."""

    USAGE = "usage-error"
    REVIEW_REQUIRED = "review-required"
    CONFLICT = "conflict-or-stale-authority"
    INTEGRITY = "integrity-failure"
    UNSUPPORTED = "unsupported-operation"


class ChangeControlApplicationError(RuntimeError):
    """Base error safe for application and future CLI callers to classify."""

    code: ChangeControlApplicationErrorCode

    def __init__(self, message: str, *, code: ChangeControlApplicationErrorCode):
        super().__init__(message)
        self.code = code


class ChangeControlApplicationUsageError(ChangeControlApplicationError):
    def __init__(self, message: str):
        super().__init__(message, code=ChangeControlApplicationErrorCode.USAGE)


class ChangeControlApplicationReviewRequiredError(ChangeControlApplicationError):
    def __init__(self, message: str):
        super().__init__(message, code=ChangeControlApplicationErrorCode.REVIEW_REQUIRED)


class ChangeControlApplicationConflictError(ChangeControlApplicationError):
    def __init__(self, message: str):
        super().__init__(message, code=ChangeControlApplicationErrorCode.CONFLICT)


class ChangeControlApplicationIntegrityError(ChangeControlApplicationError):
    def __init__(self, message: str):
        super().__init__(message, code=ChangeControlApplicationErrorCode.INTEGRITY)


class ChangeControlApplicationUnsupportedOperationError(ChangeControlApplicationError):
    def __init__(self, message: str):
        super().__init__(message, code=ChangeControlApplicationErrorCode.UNSUPPORTED)


def raise_mapped_application_error(exc: BaseException) -> NoReturn:
    """Map internal failures without exposing their classes as public API.

    Imports are intentionally local: the application taxonomy is dependency
    light and remains usable even when an optional workflow component is not
    imported. Only explicitly recognized domain failures are translated;
    unexpected programming failures retain their exact type and traceback.
    """

    from mastervault.change_control.application_activation_verifier import (
        ActivatedEvidenceVerificationError,
    )
    from mastervault.change_control.application_authority_resolver import (
        ApplicationAuthorityResolutionError,
    )
    from mastervault.change_control.application_extraction_calls import (
        ApplicationExtractionCallConflictError,
        ApplicationExtractionCallError,
        ApplicationExtractionCallIndeterminateError,
    )
    from mastervault.change_control.application_generic_extraction import (
        ApplicationGenericExtractionError,
    )
    from mastervault.change_control.application_lifecycle_evidence import (
        LifecycleEvidenceIndexConflictError,
        LifecycleEvidenceIndexError,
        LifecycleEvidenceIndexUnsupportedError,
    )
    from mastervault.change_control.application_mechanical_no_change import (
        MechanicalNoChangeEvidenceError,
    )
    from mastervault.change_control.application_no_work import NoWorkPlanningEvidenceError
    from mastervault.change_control.application_provider_bridge import (
        ApplicationProviderBridgeError,
    )
    from mastervault.change_control.application_provider_calls import (
        ApplicationProviderCallConflictError,
        ApplicationProviderCallError,
        ApplicationProviderCallIndeterminateError,
    )
    from mastervault.change_control.application_replay import (
        ChangeReplayBundleUsageError,
        ChangeReplayEvidenceIntegrityError,
    )
    from mastervault.change_control.application_source_note_resolver import (
        ApplicationSourceNoteResolverError,
    )
    from mastervault.change_control.application_stage_evidence import (
        ApplicationStageEvidenceError,
    )
    from mastervault.change_control.application_start_command import (
        ApplicationStartCommandConflictError,
        ApplicationStartCommandError,
    )
    from mastervault.change_control.application_start_lifecycle import (
        ApplicationStartLifecycleError,
    )
    from mastervault.change_control.generation_resolution import ManagedActivationServiceError
    from mastervault.change_control.generic_governing_source import (
        GenericGoverningSourceIntegrityError,
    )
    from mastervault.change_control.generic_incoming import (
        GenericIncomingBoundaryError,
        GenericIncomingIntegrityError,
    )
    from mastervault.change_control.generic_incoming_repository import (
        GenericIncomingRepositoryError,
    )
    from mastervault.change_control.inference_repository import (
        InferenceEvidenceConflictError,
        InferenceEvidenceRepositoryError,
        InferenceEvidenceUnsupportedPlatformError,
    )
    from mastervault.change_control.legacy_index import LegacyIndexIntegrityError
    from mastervault.change_control.managed_activation_service import (
        ManagedActivationBackendUnsupportedError,
    )
    from mastervault.change_control.managed_generation_repository import (
        ManagedGenerationRepositoryError,
    )
    from mastervault.change_control.managed_query_resolver import (
        ManagedQueryResolverRestartError,
    )
    from mastervault.change_control.managed_review_service import (
        ManagedReviewSelectionError,
        ManagedReviewServiceError,
    )
    from mastervault.change_control.recorded_inference import InferenceExecutionFailed
    from mastervault.change_control.regression_baseline import RegressionBaselineError
    from mastervault.change_control.regression_suite import (
        RegressionSuiteBoundaryError,
        RegressionSuiteError,
        RegressionSuiteIntegrityError,
        RegressionSuiteUnsupportedError,
    )
    from mastervault.change_control.store import (
        ChangeControlConflictError,
        ChangeControlIdempotencyError,
        ChangeControlPlatformUnsupportedError,
        ChangeControlReviewStaleError,
        ChangeControlStoreError,
    )
    from mastervault.change_control.workspace_bootstrap_repository import (
        WorkspaceBootstrapManifestError,
        WorkspaceBootstrapPlatformUnsupportedError,
        WorkspaceBootstrapRepositoryError,
    )
    from mastervault.storage import StorageError

    if isinstance(exc, ChangeControlApplicationError):
        raise exc
    if isinstance(exc, ChangeReplayBundleUsageError):
        raise ChangeControlApplicationUsageError("change replay bundle is invalid") from exc
    if isinstance(exc, ChangeReplayEvidenceIntegrityError):
        raise ChangeControlApplicationIntegrityError(
            "change replay evidence could not be verified"
        ) from exc
    if isinstance(
        exc,
        (
            GenericIncomingBoundaryError,
            RegressionSuiteBoundaryError,
            WorkspaceBootstrapManifestError,
        ),
    ):
        raise ChangeControlApplicationUsageError("change-control input is invalid") from exc
    if isinstance(
        exc,
        (
            ChangeControlPlatformUnsupportedError,
            InferenceEvidenceUnsupportedPlatformError,
            LifecycleEvidenceIndexUnsupportedError,
            ManagedActivationBackendUnsupportedError,
            RegressionSuiteUnsupportedError,
            WorkspaceBootstrapPlatformUnsupportedError,
        ),
    ):
        raise ChangeControlApplicationUnsupportedOperationError(
            "change-control operation is unsupported"
        ) from exc
    if isinstance(exc, ChangeControlIdempotencyError):
        # Store-owned idempotency messages are bounded, path-free domain
        # constants used to distinguish exact replay from operation reuse.
        raise ChangeControlApplicationConflictError(str(exc)) from exc
    if isinstance(
        exc,
        (
            ApplicationProviderCallConflictError,
            ApplicationProviderCallIndeterminateError,
            ApplicationExtractionCallConflictError,
            ApplicationExtractionCallIndeterminateError,
            ApplicationStartCommandConflictError,
            ChangeControlConflictError,
            ChangeControlReviewStaleError,
            InferenceEvidenceConflictError,
            LifecycleEvidenceIndexConflictError,
            ManagedActivationServiceError,
            ManagedReviewSelectionError,
        ),
    ):
        raise ChangeControlApplicationConflictError(
            "change-control authority changed or operation conflicts"
        ) from exc
    if isinstance(exc, (StorageError, OSError)):
        raise ChangeControlApplicationIntegrityError(
            "change-control evidence could not be verified"
        ) from exc
    if isinstance(
        exc,
        (
            ActivatedEvidenceVerificationError,
            ApplicationAuthorityResolutionError,
            ApplicationExtractionCallError,
            ApplicationGenericExtractionError,
            ApplicationProviderBridgeError,
            ApplicationProviderCallError,
            ApplicationSourceNoteResolverError,
            ApplicationStageEvidenceError,
            ApplicationStartCommandError,
            ApplicationStartLifecycleError,
            GenericGoverningSourceIntegrityError,
            GenericIncomingIntegrityError,
            GenericIncomingRepositoryError,
            InferenceEvidenceRepositoryError,
            InferenceExecutionFailed,
            LegacyIndexIntegrityError,
            LifecycleEvidenceIndexError,
            ManagedGenerationRepositoryError,
            ManagedQueryResolverRestartError,
            ManagedReviewServiceError,
            MechanicalNoChangeEvidenceError,
            NoWorkPlanningEvidenceError,
            RegressionBaselineError,
            RegressionSuiteError,
            RegressionSuiteIntegrityError,
            ChangeControlStoreError,
            WorkspaceBootstrapRepositoryError,
        ),
    ):
        raise ChangeControlApplicationIntegrityError(
            "change-control evidence could not be verified"
        ) from exc
    raise exc


__all__ = [
    "ChangeControlApplicationError",
    "ChangeControlApplicationErrorCode",
    "ChangeControlApplicationConflictError",
    "ChangeControlApplicationIntegrityError",
    "ChangeControlApplicationReviewRequiredError",
    "ChangeControlApplicationUnsupportedOperationError",
    "ChangeControlApplicationUsageError",
    "raise_mapped_application_error",
]
