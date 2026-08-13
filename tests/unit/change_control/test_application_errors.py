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
from mastervault.change_control.store import ChangeControlIdempotencyError


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
    source = ChangeControlIdempotencyError("operation ID differs")

    try:
        raise_mapped_application_error(source)
    except ChangeControlApplicationConflictError as error:
        assert error.code is ChangeControlApplicationErrorCode.CONFLICT
        assert error.__cause__ is source
    else:  # pragma: no cover - mapping must raise
        raise AssertionError("application mapper returned")
