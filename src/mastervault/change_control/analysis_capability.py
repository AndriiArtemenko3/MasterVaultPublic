"""Typed adapter over sealed V1 and verified generic V2 analysis authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mastervault.change_control.analysis_binding import AnalysisBootstrapAuthority
from mastervault.change_control.bootstrap import (
    VerifiedAnalysisBootstrapCapability,
    verify_analysis_bootstrap_snapshot,
)
from mastervault.change_control.store import ChangeControlSnapshot

if TYPE_CHECKING:
    from mastervault.change_control.generic_analysis import (
        VerifiedGenericAnalysisBootstrapCapabilityV2,
    )

    type VerifiedAnalysisAuthorityCapability = (
        VerifiedAnalysisBootstrapCapability | VerifiedGenericAnalysisBootstrapCapabilityV2
    )
else:
    type VerifiedAnalysisAuthorityCapability = Any


def verify_analysis_authority_snapshot(
    capability: VerifiedAnalysisAuthorityCapability,
    snapshot: ChangeControlSnapshot,
) -> AnalysisBootstrapAuthority:
    """Dispatch only across the two explicit, independently sealed authority types."""

    if type(capability) is VerifiedAnalysisBootstrapCapability:
        return verify_analysis_bootstrap_snapshot(capability, snapshot)
    from mastervault.change_control.generic_analysis import (
        VerifiedGenericAnalysisBootstrapCapabilityV2,
        verify_generic_analysis_snapshot_v2,
    )

    if type(capability) is VerifiedGenericAnalysisBootstrapCapabilityV2:
        return verify_generic_analysis_snapshot_v2(capability, snapshot)
    raise TypeError("analysis authority capability type is unsupported")


__all__ = ["VerifiedAnalysisAuthorityCapability", "verify_analysis_authority_snapshot"]
