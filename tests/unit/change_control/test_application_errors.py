import pytest
from pydantic import BaseModel

from mastervault.change_control.application_activation_verifier import (
    ActivatedEvidenceVerificationError,
)
from mastervault.change_control.application_authority_resolver import (
    ApplicationAuthorityResolutionError,
)
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationError,
    ChangeControlApplicationErrorCode,
    ChangeControlApplicationIntegrityError,
    ChangeControlApplicationReviewRequiredError,
    ChangeControlApplicationUnsupportedOperationError,
    ChangeControlApplicationUsageError,
    raise_mapped_application_error,
)
from mastervault.change_control.application_extraction_calls import (
    ApplicationExtractionCallError,
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
from mastervault.change_control.application_provider_calls import ApplicationProviderCallError
from mastervault.change_control.application_replay import (
    ChangeReplayBundleUsageError,
    ChangeReplayEvidenceIntegrityError,
)
from mastervault.change_control.application_source_note_resolver import (
    ApplicationSourceNoteResolverError,
)
from mastervault.change_control.application_stage_evidence import ApplicationStageEvidenceError
from mastervault.change_control.application_start_command import (
    ApplicationStartCommandConflictError,
    ApplicationStartCommandError,
)
from mastervault.change_control.application_start_lifecycle import ApplicationStartLifecycleError
from mastervault.change_control.generation_resolution import ManagedActivationServiceError
from mastervault.change_control.generic_governing_source import (
    GenericGoverningSourceIntegrityError,
)
from mastervault.change_control.generic_incoming import (
    GenericIncomingBoundaryError,
    GenericIncomingIntegrityError,
)
from mastervault.change_control.generic_incoming_repository import GenericIncomingRepositoryError
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
from mastervault.change_control.managed_query_resolver import ManagedQueryResolverRestartError
from mastervault.change_control.managed_review_service import (
    ManagedReviewSelectionError,
    ManagedReviewServiceError,
)
from mastervault.change_control.regression_suite import (
    RegressionSuiteBoundaryError,
    RegressionSuiteError,
    RegressionSuiteIntegrityError,
    RegressionSuiteUnsupportedError,
)
from mastervault.change_control.store import (
    ChangeControlIdempotencyError,
    ChangeControlPlatformUnsupportedError,
)
from mastervault.change_control.workspace_bootstrap_repository import (
    WorkspaceBootstrapManifestError,
    WorkspaceBootstrapPlatformUnsupportedError,
    WorkspaceBootstrapRepositoryError,
)


def test_application_error_taxonomy_is_stable() -> None:
    cases = (
        (ChangeControlApplicationUsageError, ChangeControlApplicationErrorCode.USAGE),
        (
            ChangeControlApplicationReviewRequiredError,
            ChangeControlApplicationErrorCode.REVIEW_REQUIRED,
        ),
        (ChangeControlApplicationConflictError, ChangeControlApplicationErrorCode.CONFLICT),
        (ChangeControlApplicationIntegrityError, ChangeControlApplicationErrorCode.INTEGRITY),
        (
            ChangeControlApplicationUnsupportedOperationError,
            ChangeControlApplicationErrorCode.UNSUPPORTED,
        ),
    )

    for error_type, expected_code in cases:
        error = error_type("bounded message")
        assert isinstance(error, ChangeControlApplicationError)
        assert str(error) == "bounded message"
        assert error.code is expected_code


def test_internal_conflict_is_mapped_with_cause() -> None:
    message = "workspace inventory is already claimed under another operation_id"
    sentinel = OSError("/private/customer/workspace/state.sqlite3")
    source = ChangeControlIdempotencyError(message)
    source.__cause__ = sentinel

    try:
        raise_mapped_application_error(source)
    except ChangeControlApplicationConflictError as error:
        assert error.code is ChangeControlApplicationErrorCode.CONFLICT
        assert error.__cause__ is source
        assert str(error) == message
        assert str(sentinel) not in str(error)
    else:  # pragma: no cover - mapping must raise
        raise AssertionError("application mapper returned")


@pytest.mark.parametrize(
    "source_type",
    (OSError, LegacyIndexIntegrityError, ManagedGenerationRepositoryError),
)
def test_untrusted_error_mapping_never_discloses_absolute_path(
    source_type: type[Exception],
) -> None:
    sentinel = "/private/sensitive/operator/workspace/state.sqlite3"
    source = source_type(f"cannot open {sentinel}")

    try:
        raise_mapped_application_error(source)
    except ChangeControlApplicationIntegrityError as error:
        assert error.__cause__ is source
        assert sentinel not in str(error)
        assert str(error) == "change-control evidence could not be verified"
    else:  # pragma: no cover - mapping must raise
        raise AssertionError("application mapper returned")


def test_replay_usage_and_captured_integrity_have_distinct_public_mappings() -> None:
    cases = (
        (ChangeReplayBundleUsageError("malformed replay JSON"), ChangeControlApplicationUsageError),
        (
            ChangeReplayEvidenceIntegrityError("captured replay evidence changed"),
            ChangeControlApplicationIntegrityError,
        ),
    )
    for source, public_type in cases:
        with pytest.raises(public_type) as caught:
            raise_mapped_application_error(source)
        assert caught.value.__cause__ is source


def test_provider_validation_payload_is_retained_only_as_private_cause() -> None:
    sentinel = "RAW_PROVIDER_SECRET_/private/customer/source.md"

    class ProviderWire(BaseModel):
        required_integer: int

    source: ValueError | None = None
    try:
        ProviderWire.model_validate({"required_integer": sentinel})
    except ValueError as error:
        source = error
        assert sentinel in str(error)
    else:  # pragma: no cover - fixture must exercise Pydantic's raw input rendering
        raise AssertionError("malicious provider fixture unexpectedly validated")

    assert source is not None
    boundary = ApplicationGenericExtractionError(
        "provider result failed the structured extraction contract"
    )
    boundary.__cause__ = source
    with pytest.raises(ChangeControlApplicationIntegrityError) as caught:
        raise_mapped_application_error(boundary)
    assert caught.value.__cause__ is boundary
    assert str(caught.value) == "change-control evidence could not be verified"
    assert sentinel not in str(caught.value)


def test_start_command_domain_errors_have_stable_path_free_mappings() -> None:
    sentinel = "/private/customer/start-command.json"
    cases = (
        (
            ApplicationStartCommandConflictError(f"conflict at {sentinel}"),
            ChangeControlApplicationConflictError,
        ),
        (
            ApplicationStartCommandError(f"invalid evidence at {sentinel}"),
            ChangeControlApplicationIntegrityError,
        ),
    )
    for source, public_type in cases:
        with pytest.raises(public_type) as caught:
            raise_mapped_application_error(source)
        assert caught.value.__cause__ is source
        if public_type is ChangeControlApplicationIntegrityError:
            assert str(caught.value) == "change-control evidence could not be verified"
            assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    "source_type",
    (
        GenericIncomingBoundaryError,
        RegressionSuiteBoundaryError,
        WorkspaceBootstrapManifestError,
    ),
)
def test_known_operator_boundary_errors_map_to_path_free_usage(
    source_type: type[Exception],
) -> None:
    sentinel = "/private/customer/operator-input.json RAW_PROVIDER_PAYLOAD"
    source = source_type(sentinel)

    with pytest.raises(ChangeControlApplicationUsageError) as caught:
        raise_mapped_application_error(source)

    assert caught.value.__cause__ is source
    assert str(caught.value) == "change-control input is invalid"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    "source_type",
    (
        ChangeControlPlatformUnsupportedError,
        InferenceEvidenceUnsupportedPlatformError,
        LifecycleEvidenceIndexUnsupportedError,
        ManagedActivationBackendUnsupportedError,
        RegressionSuiteUnsupportedError,
        WorkspaceBootstrapPlatformUnsupportedError,
    ),
)
def test_known_platform_errors_map_to_path_free_unsupported(
    source_type: type[Exception],
) -> None:
    sentinel = "/private/customer/platform-detail"
    source = source_type(sentinel)

    with pytest.raises(ChangeControlApplicationUnsupportedOperationError) as caught:
        raise_mapped_application_error(source)

    assert caught.value.__cause__ is source
    assert str(caught.value) == "change-control operation is unsupported"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    "source_type",
    (
        InferenceEvidenceConflictError,
        LifecycleEvidenceIndexConflictError,
        ManagedActivationServiceError,
        ManagedReviewSelectionError,
    ),
)
def test_known_authority_conflicts_map_without_internal_payloads(
    source_type: type[Exception],
) -> None:
    sentinel = "/private/customer/stale-authority RAW_PROVIDER_PAYLOAD"
    source = source_type(sentinel)

    with pytest.raises(ChangeControlApplicationConflictError) as caught:
        raise_mapped_application_error(source)

    assert caught.value.__cause__ is source
    assert str(caught.value) == "change-control authority changed or operation conflicts"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    "source_type",
    (
        ActivatedEvidenceVerificationError,
        ApplicationAuthorityResolutionError,
        ApplicationExtractionCallError,
        ApplicationProviderCallError,
        ApplicationSourceNoteResolverError,
        ApplicationStageEvidenceError,
        ApplicationStartLifecycleError,
        GenericGoverningSourceIntegrityError,
        GenericIncomingIntegrityError,
        GenericIncomingRepositoryError,
        InferenceEvidenceRepositoryError,
        LifecycleEvidenceIndexError,
        ManagedQueryResolverRestartError,
        ManagedReviewServiceError,
        MechanicalNoChangeEvidenceError,
        NoWorkPlanningEvidenceError,
        RegressionSuiteError,
        RegressionSuiteIntegrityError,
        WorkspaceBootstrapRepositoryError,
    ),
)
def test_known_evidence_failures_map_to_path_free_integrity(
    source_type: type[Exception],
) -> None:
    sentinel = "/private/customer/evidence RAW_PROVIDER_PAYLOAD"
    source = source_type(sentinel)

    with pytest.raises(ChangeControlApplicationIntegrityError) as caught:
        raise_mapped_application_error(source)

    assert caught.value.__cause__ is source
    assert str(caught.value) == "change-control evidence could not be verified"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    "source",
    (
        RuntimeError("unexpected runtime sentinel"),
        KeyError("unexpected key sentinel"),
        AssertionError("unexpected assertion sentinel"),
        TypeError("unexpected type sentinel"),
        ValueError("unexpected value sentinel"),
    ),
)
def test_unexpected_exception_propagates_as_the_exact_object(source: BaseException) -> None:
    with pytest.raises(type(source)) as caught:
        raise_mapped_application_error(source)
    assert caught.value is source
