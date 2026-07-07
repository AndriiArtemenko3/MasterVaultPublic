"""HITL review layer: file-backed queue + guarded apply."""

from mastervault.review.apply import (
    AppliedResult,
    ApplyResult,
    ConflictResult,
    apply,
    apply_unified_diff,
)
from mastervault.review.queue import LoadedReview, ReviewQueue, dedupe_key

__all__ = [
    "AppliedResult",
    "ApplyResult",
    "ConflictResult",
    "LoadedReview",
    "ReviewQueue",
    "apply",
    "apply_unified_diff",
    "dedupe_key",
]
