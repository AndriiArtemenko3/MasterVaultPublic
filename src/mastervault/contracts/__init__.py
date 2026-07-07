"""Contracts: versioned prompt + typed output + autofix/hard-fail guards."""

from mastervault.contracts.base import Contract, DispatchResult, schema_section
from mastervault.contracts.claims import (
    ClaimCandidate,
    ClaimExtractionContract,
    ClaimExtractionOut,
)

__all__ = [
    "ClaimCandidate",
    "ClaimExtractionContract",
    "ClaimExtractionOut",
    "Contract",
    "DispatchResult",
    "schema_section",
]
