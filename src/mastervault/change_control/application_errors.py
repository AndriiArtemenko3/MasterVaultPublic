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
    imported.  Unknown failures are integrity failures, the fail-closed
    category; programming ``AssertionError``/``TypeError`` defects are not
    accepted here by application code and therefore retain their traceback.
    """

    from mastervault.change_control.store import (
        ChangeControlConflictError,
        ChangeControlIdempotencyError,
        ChangeControlPlatformUnsupportedError,
        ChangeControlReviewStaleError,
        ChangeControlStoreError,
    )
    from mastervault.storage import StorageError

    if isinstance(exc, ChangeControlApplicationError):
        raise exc
    if isinstance(exc, ChangeControlPlatformUnsupportedError):
        raise ChangeControlApplicationUnsupportedOperationError(str(exc)) from exc
    if isinstance(
        exc,
        (ChangeControlConflictError, ChangeControlIdempotencyError, ChangeControlReviewStaleError),
    ):
        raise ChangeControlApplicationConflictError(str(exc)) from exc
    if isinstance(exc, (ChangeControlStoreError, StorageError, OSError, ValueError)):
        raise ChangeControlApplicationIntegrityError(str(exc)) from exc
    raise ChangeControlApplicationIntegrityError(
        "change-control evidence could not be verified"
    ) from exc


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
