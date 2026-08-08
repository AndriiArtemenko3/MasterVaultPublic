"""Deterministic routing-scope policy shared by change-control projections.

The policy is mechanical: it does not infer semantic relevance or entailment.
Incoming ``affects`` values require separate source/fixture review before this
helper may turn them into routing annotations.
"""

from __future__ import annotations

from typing import Final

from mastervault.change_control.models import normalize_logical_key

CLAIM_SCOPE_POLICY_VERSION: Final = "claim-scopes-v1"


def claim_scopes_v1(*, document_family: str, affects: tuple[str, ...]) -> tuple[str, ...]:
    """Return the canonical union of one document family and source claim affects.

    Inputs are reviewed routing identifiers, so this boundary refuses to repair
    non-canonical values. Duplicate affects are harmless and collapse before the
    stable lexical ordering is applied; stricter fixture boundaries may reject
    duplicates before calling this shared mechanical helper.
    """

    if not isinstance(document_family, str):
        raise TypeError("document_family must be a string")
    if not isinstance(affects, tuple) or any(not isinstance(item, str) for item in affects):
        raise TypeError("affects must be a tuple of strings")
    values = (document_family, *affects)
    for value in values:
        normalized = normalize_logical_key(value)
        if value != normalized:
            raise ValueError(f"claim scope inputs must already be normalized as {normalized!r}")
    return tuple(sorted(set(values)))


__all__ = ["CLAIM_SCOPE_POLICY_VERSION", "claim_scopes_v1"]
