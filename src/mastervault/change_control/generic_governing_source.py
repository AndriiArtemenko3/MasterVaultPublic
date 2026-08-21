"""Generic governing-source adoption resolved from verified admission evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.generic_analysis import (
    VerifiedGenericAnalysisBootstrapCapabilityV2,
    verify_generic_analysis_snapshot_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    RepositoryVerifiedGenericEvidenceV2,
)
from mastervault.change_control.managed_review import (
    GenericGoverningSourceAdoptionBindingV2,
    GoverningSourceAdoptionAuthority,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedGoverningSourceAdoptionBinding,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedInferenceContractBinding,
    ManagedNoWorkPlanningAdmissionBinding,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionAuthority,
    ManagedRevisionPlanningAdmissionBinding,
    PatchReconstructionAttestation,
    SourceNoteProjectionBinding,
)
from mastervault.change_control.managed_review_repository import (
    RepositoryBackedManagedReviewResolver,
    ResolvedReviewedGenerationSource,
)
from mastervault.change_control.managed_source_note import parse_managed_source_note
from mastervault.change_control.models import VersionedClaimRevision, canonical_json_bytes
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.store import ChangeControlSnapshot
from mastervault.change_control.workspace_bootstrap import ManagedSourceNoteBootstrapMetadata
from mastervault.models import content_hash


class GenericGoverningSourceIntegrityError(ValueError):
    """Reviewed aggregate or generic evidence does not reproduce one adoption."""


@dataclass(frozen=True)
class WorkspaceSourceNoteProjectionAuthority:
    """Process-local exact workspace authority for one bootstrap SourceNote."""

    metadata: ManagedSourceNoteBootstrapMetadata
    snapshot: CanonicalSourceNoteSnapshot
    raw_artifact: ManagedArtifactRef
    raw_bytes: bytes
    note_artifact: ManagedArtifactRef
    note_bytes: bytes
    projected_claims: tuple[VersionedClaimRevision, ...]

    def __post_init__(self) -> None:
        claims = tuple(sorted(self.projected_claims, key=lambda item: item.claim_revision_id))
        note = parse_managed_source_note(self.note_bytes)
        raw_text = self.raw_bytes.decode("utf-8")
        if claims != self.projected_claims or len(
            {item.claim_revision_id for item in claims}
        ) != len(claims):
            raise ValueError("workspace projection claims must be canonical and unique")
        if not (
            self.metadata.document == self.snapshot.document
            and self.metadata.logical_path == self.snapshot.source_note_path
            and self.metadata.raw_source_path == self.raw_artifact.path
            and self.metadata.raw_source_sha256 == self.raw_artifact.sha256
            and self.metadata.raw_source_byte_count == self.raw_artifact.byte_count
            and self.metadata.source_note_sha256 == self.note_artifact.sha256
            and self.metadata.source_note_byte_count == self.note_artifact.byte_count
            and self.note_artifact.path == self.snapshot.source_note_path
            and self.note_artifact.sha256 == self.snapshot.source_note_sha256
            and self.note_artifact.byte_count == self.snapshot.source_note_utf8_bytes
            and self.note_bytes == self.snapshot.source_note_utf8.encode("utf-8")
            and len(self.raw_bytes) == self.raw_artifact.byte_count
            and hashlib.sha256(self.raw_bytes).hexdigest() == self.raw_artifact.sha256
            and len(self.note_bytes) == self.note_artifact.byte_count
            and hashlib.sha256(self.note_bytes).hexdigest() == self.note_artifact.sha256
            and note.provenance == self.metadata.source_note_provenance
            and note.provenance_hash == content_hash(raw_text)
            and all(
                item.document == self.snapshot.document
                and item.source.source_note_path == self.snapshot.source_note_path
                and item.source.source_note_sha256 == self.snapshot.source_note_sha256
                and not item.source.evidence
                for item in claims
            )
        ):
            raise ValueError("workspace projection authority differs from guarded bootstrap")


def derive_generic_governing_source_adoption_v2(
    *,
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    analysis_capability: VerifiedGenericAnalysisBootstrapCapabilityV2,
    repository: FilesystemGenericIncomingRepositoryV2,
    evidence_capability: RepositoryVerifiedGenericEvidenceV2,
) -> GenericGoverningSourceAdoptionBindingV2:
    """Freshly bind the reviewed generic source without a seed repository root."""

    if type(reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
        raise GenericGoverningSourceIntegrityError(
            "generic governing adoption requires exact reviewed authority"
        )
    reviewed = reviewed_snapshot.verify()
    analysis_snapshot = ChangeControlSnapshot(
        aggregate=reviewed.temporal_analysis.analysis_aggregate,
        revision=reviewed.temporal_analysis.analysis_head.revision,
        aggregate_sha256=reviewed.temporal_analysis.analysis_head.aggregate_sha256,
    )
    try:
        bootstrap = verify_generic_analysis_snapshot_v2(analysis_capability, analysis_snapshot)
        evidence = repository.resolve_verified_evidence(evidence_capability)
        inventory = reviewed.source_note_capability.verify(snapshot=reviewed.snapshot)
    except (TypeError, ValueError) as exc:
        raise GenericGoverningSourceIntegrityError(
            "generic governing source authority cannot be freshly verified"
        ) from exc
    notes = tuple(
        item
        for item in inventory.notes
        if item.document.document_version_id == bootstrap.incoming_document_version_id
    )
    documents = {
        item.document_version_id: item for item in reviewed.snapshot.aggregate.documents.documents
    }
    incoming_claims = tuple(
        sorted(
            (
                item
                for item in reviewed.snapshot.aggregate.claims.revisions
                if item.document.document_version_id == bootstrap.incoming_document_version_id
            ),
            key=lambda item: item.claim_revision_id,
        )
    )
    if len(notes) != 1:
        raise GenericGoverningSourceIntegrityError(
            "reviewed generic inventory lacks exactly one incoming SourceNote"
        )
    note = notes[0]
    document = documents.get(bootstrap.incoming_document_version_id)
    if document is None or not (
        document == note.document
        and evidence.bundle.bundle_id == bootstrap.incoming_bundle_id
        and evidence.bundle.bundle_sha256 == bootstrap.incoming_bundle_sha256
        and evidence.admission.admission_sha256 == bootstrap.incoming_admission_sha256
        and evidence.source.source_receipt_sha256 == bootstrap.incoming_source_receipt_sha256
        and evidence.projection.projection_sha256 == bootstrap.incoming_projection_sha256
        and evidence.inference.inference_sha256 == bootstrap.incoming_inference_sha256
        and evidence.admission.metadata.event_id == bootstrap.incoming_event_id
        and evidence.admission.metadata.document_id == bootstrap.incoming_document_id
        and note.source_note_path == evidence.source.source_note_locator
        and note.source_note_sha256 == evidence.source.source_note_sha256
        and note.source_note_utf8.encode("utf-8") == evidence.source_note
        and tuple(item.claim_revision_id for item in incoming_claims)
        == bootstrap.changed_claim_revision_ids
        and reviewed.binding.analysis_head.aggregate_id == bootstrap.aggregate_id
        and reviewed.binding.analysis_head.aggregate_sha256 == bootstrap.analysis_aggregate_sha256
    ):
        raise GenericGoverningSourceIntegrityError(
            "reviewed generic source differs from bootstrap lineage"
        )
    return GenericGoverningSourceAdoptionBindingV2.create(
        evidence_repository_id=repository.repository_id,
        analysis_bootstrap_binding_id=bootstrap.binding_id,
        analysis_bootstrap_binding_sha256=bootstrap.binding_sha256,
        incoming_logical_event_id=bootstrap.incoming_event_id,
        incoming_event_identity=bootstrap.incoming_event_identity,
        incoming_bundle_id=bootstrap.incoming_bundle_id,
        incoming_bundle_sha256=bootstrap.incoming_bundle_sha256,
        incoming_admission_sha256=bootstrap.incoming_admission_sha256,
        incoming_source_receipt_sha256=bootstrap.incoming_source_receipt_sha256,
        incoming_projection_sha256=bootstrap.incoming_projection_sha256,
        incoming_inference_sha256=bootstrap.incoming_inference_sha256,
        incoming_claim_evidence_sha256=bootstrap.incoming_claim_evidence_sha256,
        document=document,
        raw_artifact=ManagedArtifactRef.create(
            kind=ManagedArtifactKind.RAW_SOURCE,
            path=evidence.source.source_locator,
            sha256=evidence.source.source_sha256,
            byte_count=evidence.source.source_byte_count,
        ),
        source_note_artifact=ManagedArtifactRef.create(
            kind=ManagedArtifactKind.SOURCE_NOTE,
            path=evidence.source.source_note_locator,
            sha256=evidence.source.source_note_sha256,
            byte_count=evidence.source.source_note_byte_count,
        ),
        source_note_logical_path=evidence.source.source_note_locator,
        source_note_snapshot_id=note.snapshot_id,
        source_note_snapshot_sha256=note.snapshot_sha256,
        reviewed_snapshot_binding_id=reviewed.binding.binding_id,
        reviewed_snapshot_binding_sha256=reviewed.binding.binding_sha256,
        temporal_decision_record_sha256=reviewed.binding.temporal_decision_record_sha256,
        reviewed_inventory_sha256=inventory.inventory_sha256,
        reviewed_head=reviewed.binding.reviewed_head,
        authoritative_repository_resolution_required=True,
    )


@dataclass(frozen=True)
class GenericGoverningSourceResolverV2:
    """Live resolver retained by managed review/activation reconstruction."""

    reviewed_snapshot: ReviewedTemporalSnapshotAuthority
    analysis_capability: VerifiedGenericAnalysisBootstrapCapabilityV2
    repository: FilesystemGenericIncomingRepositoryV2
    evidence_capability: RepositoryVerifiedGenericEvidenceV2

    def resolve_governing_source_adoption(
        self, binding: GenericGoverningSourceAdoptionBindingV2
    ) -> GenericGoverningSourceAdoptionBindingV2:
        exact = GenericGoverningSourceAdoptionBindingV2.model_validate_json(
            canonical_json_bytes(binding.model_dump(mode="json"))
        )
        reopened = derive_generic_governing_source_adoption_v2(
            reviewed_snapshot=self.reviewed_snapshot,
            analysis_capability=self.analysis_capability,
            repository=self.repository,
            evidence_capability=self.evidence_capability,
        )
        if exact != binding or reopened != binding:
            raise GenericGoverningSourceIntegrityError(
                "generic governing-source adoption differs after exact reopen"
            )
        return reopened

    def resolve_reviewed_generation_source(
        self, binding: GenericGoverningSourceAdoptionBindingV2
    ) -> ResolvedGenericGenerationSourceV2:
        adoption = self.resolve_governing_source_adoption(binding)
        reviewed = self.reviewed_snapshot.verify()
        inventory = reviewed.source_note_capability.verify(snapshot=reviewed.snapshot)
        return ResolvedGenericGenerationSourceV2(
            adoption=adoption,
            snapshot=reviewed.snapshot,
            inventory=inventory,
            workspace_root=self.repository.root,
        )

    def protected_generation_roots(self) -> tuple[Path, ...]:
        """Keep generic admission evidence outside managed generation effects."""

        return (self.repository.root,)


@dataclass(frozen=True)
class ResolvedGenericGenerationSourceV2:
    adoption: GenericGoverningSourceAdoptionBindingV2
    snapshot: ChangeControlSnapshot
    inventory: SourceNoteInventory
    workspace_root: Path


@dataclass(frozen=True)
class CompositeManagedReviewResolverV2:
    """Retain sealed review evidence and generic governing-source authority together."""

    sealed: RepositoryBackedManagedReviewResolver
    generic: GenericGoverningSourceResolverV2
    workspace_projection_authorities: tuple[WorkspaceSourceNoteProjectionAuthority, ...] = ()

    def __post_init__(self) -> None:
        if type(self.sealed) is not RepositoryBackedManagedReviewResolver:
            raise TypeError("composite resolver requires exact sealed repository authority")
        if type(self.generic) is not GenericGoverningSourceResolverV2:
            raise TypeError("composite resolver requires exact generic repository authority")
        paths: set[str] = set()
        artifact_ids: set[str] = set()
        for authority in self.workspace_projection_authorities:
            if type(authority) is not WorkspaceSourceNoteProjectionAuthority:
                raise TypeError("workspace projection authority type was substituted")
            for artifact in (authority.raw_artifact, authority.note_artifact):
                if artifact.path in paths or artifact.artifact_id in artifact_ids:
                    raise ValueError("workspace projection authorities crosswire artifacts")
                paths.add(artifact.path)
                artifact_ids.add(artifact.artifact_id)

    def open_algorithm_manifest(self, binding: ManagedInferenceContractBinding) -> bytes:
        return self.sealed.open_algorithm_manifest(binding)

    def resolve_approved_inference_contract(
        self, binding: ManagedInferenceContractBinding
    ) -> ManagedInferenceContractBinding:
        return self.sealed.resolve_approved_inference_contract(binding)

    def resolve_impact_analysis_evidence(
        self, binding: ManagedImpactAnalysisEvidenceBinding
    ) -> ManagedImpactAnalysisEvidenceBinding:
        return self.sealed.resolve_impact_analysis_evidence(binding)

    def resolve_revision_planning_admission(
        self, binding: ManagedRevisionPlanningAdmissionAuthority
    ) -> ManagedRevisionPlanningAdmissionAuthority:
        if type(binding) not in {
            ManagedRevisionPlanningAdmissionBinding,
            ManagedNoWorkPlanningAdmissionBinding,
        }:
            raise TypeError("revision-planning resolution requires an exact supported binding")
        return self.sealed.resolve_revision_planning_admission(binding)

    def resolve_governing_source_adoption(
        self, binding: GoverningSourceAdoptionAuthority
    ) -> GoverningSourceAdoptionAuthority:
        if type(binding) is ManagedGoverningSourceAdoptionBinding:
            return self.sealed.resolve_governing_source_adoption(binding)
        if type(binding) is GenericGoverningSourceAdoptionBindingV2:
            return self.generic.resolve_governing_source_adoption(binding)
        raise TypeError("governing-source resolution requires an exact supported binding")

    def resolve_reviewed_generation_source(
        self, binding: GoverningSourceAdoptionAuthority
    ) -> ResolvedReviewedGenerationSource | ResolvedGenericGenerationSourceV2:
        if type(binding) is ManagedGoverningSourceAdoptionBinding:
            return self.sealed.resolve_reviewed_generation_source(binding)
        if type(binding) is GenericGoverningSourceAdoptionBindingV2:
            return self.generic.resolve_reviewed_generation_source(binding)
        raise TypeError("generation-source resolution requires an exact supported binding")

    def open_artifact(self, artifact: ManagedArtifactRef) -> bytes:
        for authority in self.workspace_projection_authorities:
            for expected, payload in (
                (authority.raw_artifact, authority.raw_bytes),
                (authority.note_artifact, authority.note_bytes),
            ):
                if artifact.path != expected.path:
                    continue
                if artifact != expected:
                    raise GenericGoverningSourceIntegrityError(
                        "workspace artifact receipt differs from guarded bootstrap authority"
                    )
                return payload
        evidence = self.generic.repository.resolve_verified_evidence(
            self.generic.evidence_capability
        )
        generic_members = (
            (
                ManagedArtifactKind.RAW_SOURCE,
                evidence.source.source_locator,
                evidence.source.source_sha256,
                evidence.source.source_byte_count,
                evidence.raw_source,
            ),
            (
                ManagedArtifactKind.SOURCE_NOTE,
                evidence.source.source_note_locator,
                evidence.source.source_note_sha256,
                evidence.source.source_note_byte_count,
                evidence.source_note,
            ),
        )
        for kind, path, sha256, byte_count, payload in generic_members:
            if artifact.path != path:
                continue
            if not (
                artifact.kind == kind
                and artifact.sha256 == sha256
                and artifact.byte_count == byte_count
            ):
                raise GenericGoverningSourceIntegrityError(
                    "generic artifact receipt differs from reopened bundle authority"
                )
            return payload
        return self.sealed.open_artifact(artifact)

    def verify_patch_reconstruction(
        self,
        plan: ManagedRevisionPlan,
        *,
        base_bytes: bytes,
        result_bytes: bytes,
    ) -> PatchReconstructionAttestation:
        return self.sealed.verify_patch_reconstruction(
            plan,
            base_bytes=base_bytes,
            result_bytes=result_bytes,
        )

    def verify_source_note_projection(
        self,
        projection: SourceNoteProjectionBinding,
        *,
        raw_bytes: bytes,
        note_bytes: bytes,
    ) -> SourceNoteProjectionBinding:
        for authority in self.workspace_projection_authorities:
            paths_match = (
                projection.canonical_raw_path == authority.raw_artifact.path
                and projection.canonical_note_path == authority.note_artifact.path
            )
            if not paths_match:
                continue
            if not (
                projection.raw_artifact == authority.raw_artifact
                and projection.note_artifact == authority.note_artifact
                and raw_bytes == authority.raw_bytes
                and note_bytes == authority.note_bytes
                and projection.projected_claims == authority.projected_claims
            ):
                raise GenericGoverningSourceIntegrityError(
                    "workspace projection differs from its exact guarded authority"
                )
            return self.sealed._verify_source_note_projection(  # noqa: SLF001
                projection,
                raw_bytes=raw_bytes,
                note_bytes=note_bytes,
                expected_provenance=authority.metadata.source_note_provenance,
            )
        return self.sealed.verify_source_note_projection(
            projection,
            raw_bytes=raw_bytes,
            note_bytes=note_bytes,
        )

    def verify_revision_plan_source_note(
        self,
        plan: ManagedRevisionPlan,
        *,
        predecessor_note_bytes: bytes,
        result_raw_bytes: bytes,
        proposed_note_bytes: bytes,
    ) -> SourceNoteProjectionBinding:
        return self.sealed.verify_revision_plan_source_note(
            plan,
            predecessor_note_bytes=predecessor_note_bytes,
            result_raw_bytes=result_raw_bytes,
            proposed_note_bytes=proposed_note_bytes,
        )

    def protected_generation_roots(self) -> tuple[Path, ...]:
        return tuple(
            sorted(
                {
                    *self.sealed.protected_generation_roots(),
                    *self.generic.protected_generation_roots(),
                },
                key=str,
            )
        )


__all__ = [
    "CompositeManagedReviewResolverV2",
    "GenericGoverningSourceIntegrityError",
    "GenericGoverningSourceResolverV2",
    "ResolvedGenericGenerationSourceV2",
    "WorkspaceSourceNoteProjectionAuthority",
    "derive_generic_governing_source_adoption_v2",
]
