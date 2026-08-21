"""Run-scoped reconstruction of exact generic SourceNote inventory authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mastervault.change_control.analysis_binding import GenericAnalysisBootstrapBindingV2
from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.dependency_analysis import CanonicalSourceNoteSnapshot
from mastervault.change_control.generic_analysis import (
    GenericSourceNoteInventoryResolverV2,
    reopen_generic_analysis_capability_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.store import ChangeControlSnapshot
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.workspace_bootstrap import VerifiedWorkspaceBootstrapCapability


class ApplicationSourceNoteResolverError(ValueError):
    """A run's exact generic SourceNote authority cannot be reconstructed."""


@dataclass(frozen=True)
class GenericApplicationSourceNoteResolverLoader:
    """Build the exact resolver bound to each run's temporal analysis evidence."""

    evidence_root: Path
    workspace_capability: VerifiedWorkspaceBootstrapCapability
    workspace_source_notes: tuple[CanonicalSourceNoteSnapshot, ...]

    def __post_init__(self) -> None:
        if type(self.workspace_capability) is not VerifiedWorkspaceBootstrapCapability:
            raise TypeError("generic resolver loader requires exact workspace authority")
        if type(self.workspace_source_notes) is not tuple or any(
            type(note) is not CanonicalSourceNoteSnapshot
            for note in self.workspace_source_notes
        ):
            raise TypeError("generic resolver loader requires exact SourceNote snapshots")

    def __call__(self, run_id: str) -> GenericSourceNoteInventoryResolverV2:
        try:
            root = Path(self.evidence_root)
            index = FilesystemLifecycleEvidenceIndex(root, create=False, read_only=True)
            temporal = index.reopen(run_id, LifecycleEvidenceStageV1.TEMPORAL)
            owners = tuple(
                owner for owner in temporal.owners if owner.owner_kind == "temporal-analysis"
            )
            if len(owners) != 1:
                raise ApplicationSourceNoteResolverError(
                    "run does not bind one exact temporal analysis owner"
                )
            owner = owners[0]
            if owner.owner_id != f"temporal-analysis:{owner.owner_sha256}":
                raise ApplicationSourceNoteResolverError(
                    "temporal analysis owner identity is inconsistent"
                )
            evidence = FilesystemInferenceEvidenceRepository(
                root, create=False, read_only=True
            )
            payload = evidence.resolve_temporal_analysis_manifest(
                manifest_id=owner.owner_id,
                manifest_sha256=owner.owner_sha256,
            )
            temporal_analysis = TemporalAnalysisEvidence.from_canonical_bytes(payload)
            if not (
                temporal_analysis.manifest_id == owner.owner_id
                and temporal_analysis.manifest_sha256 == owner.owner_sha256
            ):
                raise ApplicationSourceNoteResolverError(
                    "temporal analysis differs from its lifecycle owner"
                )
            binding = temporal_analysis.proposal.binding.analysis_bootstrap
            if type(binding) is not GenericAnalysisBootstrapBindingV2:
                raise ApplicationSourceNoteResolverError(
                    "public lifecycle run does not use generic analysis authority"
                )
            generic = FilesystemGenericIncomingRepositoryV2(
                root, create=False, read_only=True
            )
            generic_evidence = generic.reopen(binding.incoming_bundle_id)
            if generic_evidence.bundle_sha256 != binding.incoming_bundle_sha256:
                raise ApplicationSourceNoteResolverError(
                    "generic bundle differs from temporal analysis authority"
                )
            snapshot = ChangeControlSnapshot(
                aggregate=temporal_analysis.analysis_aggregate,
                revision=temporal_analysis.analysis_head.revision,
                aggregate_sha256=temporal_analysis.analysis_head.aggregate_sha256,
            )
            capability = reopen_generic_analysis_capability_v2(
                binding=binding,
                analysis_snapshot=snapshot,
                repository=generic,
                workspace_capability=self.workspace_capability,
                evidence_capability=generic_evidence,
            )
            resolver = GenericSourceNoteInventoryResolverV2(
                verified_bootstrap=capability,
                workspace_source_notes=self.workspace_source_notes,
            )
            resolver.resolve_source_note_inventory(snapshot=snapshot).verify(snapshot=snapshot)
            return resolver
        except ApplicationSourceNoteResolverError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ApplicationSourceNoteResolverError(
                "run SourceNote authority cannot be reopened exactly"
            ) from exc


__all__ = [
    "ApplicationSourceNoteResolverError",
    "GenericApplicationSourceNoteResolverLoader",
]
