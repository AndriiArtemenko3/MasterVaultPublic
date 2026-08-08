"""Authoritative evidence-first commit seam for one temporal proposal.

The filesystem evidence repository and SQLite store cannot share one atomic
transaction. This service therefore verifies every durable inference batch,
persists and reopens the exact temporal-analysis manifest, and only then issues
the revision-2 to revision-3 compare-and-swap. An orphaned manifest is inert;
an aggregate revision is never authorized by absent or unverified evidence.
"""

from __future__ import annotations

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    RepositoryVerifiedInferenceEvidenceBatch,
)
from mastervault.change_control.source_note_inventory import (
    RepositorySourceNoteInventoryResolver,
)
from mastervault.change_control.store import ChangeControlSnapshot, SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import (
    TemporalAnalysisEvidence,
    verify_temporal_analysis_evidence,
)
from mastervault.change_control.temporal_proposal import (
    TemporalProposal,
    TemporalProposalCommit,
)


class TemporalProposalAuthorityError(ValueError):
    """The proposal lacks exact durable evidence or the bound live head."""


def _require_exact_batch_capability(
    *,
    capability: RepositoryVerifiedInferenceEvidenceBatch,
    expected_batch_id: str,
    expected_batch_sha256: str,
    label: str,
) -> None:
    if type(capability) is not RepositoryVerifiedInferenceEvidenceBatch:
        raise TemporalProposalAuthorityError(f"{label} capability is not repository verified")
    if capability.batch_id != expected_batch_id or capability.batch_sha256 != expected_batch_sha256:
        raise TemporalProposalAuthorityError(
            f"{label} capability differs from the temporal-analysis batch reference"
        )


def commit_temporal_proposal(
    store: SqliteChangeControlStore,
    proposal: TemporalProposal,
    *,
    temporal_analysis: TemporalAnalysisEvidence,
    evidence_repository: FilesystemInferenceEvidenceRepository,
    classification_batch: RepositoryVerifiedInferenceEvidenceBatch,
    dependency_batch: RepositoryVerifiedInferenceEvidenceBatch,
    source_note_resolver: RepositorySourceNoteInventoryResolver,
) -> TemporalProposalCommit:
    """Commit one exactly reproduced proposal after durable evidence verification.

    The operation ID is derived from the temporal-analysis manifest SHA; callers
    cannot choose a second idempotency identity for the same analysis evidence.
    """

    if type(store) is not SqliteChangeControlStore:
        raise TemporalProposalAuthorityError(
            "temporal proposal authority requires the SQLite aggregate store"
        )
    if type(evidence_repository) is not FilesystemInferenceEvidenceRepository:
        raise TemporalProposalAuthorityError(
            "temporal proposal authority requires the filesystem evidence repository"
        )
    if type(source_note_resolver) is not RepositorySourceNoteInventoryResolver:
        raise TemporalProposalAuthorityError(
            "SourceNote inventory resolver is not repository backed"
        )
    if type(proposal) is not TemporalProposal:
        raise TemporalProposalAuthorityError(
            "temporal proposal authority requires the exact proposal model"
        )
    if type(temporal_analysis) is not TemporalAnalysisEvidence:
        raise TemporalProposalAuthorityError(
            "temporal proposal authority requires the exact analysis-evidence model"
        )
    exact_proposal = TemporalProposal.model_validate(proposal.model_dump(mode="python"))
    exact_analysis = TemporalAnalysisEvidence.from_canonical_bytes(
        temporal_analysis.canonical_bytes()
    )
    if exact_analysis != temporal_analysis or exact_analysis.proposal != exact_proposal:
        raise TemporalProposalAuthorityError(
            "temporal analysis does not contain the exact proposal being committed"
        )

    analysis_snapshot = ChangeControlSnapshot(
        aggregate=exact_analysis.analysis_aggregate,
        revision=exact_analysis.analysis_head.revision,
        aggregate_sha256=exact_analysis.analysis_head.aggregate_sha256,
    )
    inventory_capability = source_note_resolver.resolve_source_note_inventory(
        snapshot=analysis_snapshot
    )
    verified_bootstrap = source_note_resolver.verified_bootstrap

    _require_exact_batch_capability(
        capability=classification_batch,
        expected_batch_id=exact_analysis.classification_evidence_batch_id,
        expected_batch_sha256=exact_analysis.classification_evidence_batch_sha256,
        label="classification evidence batch",
    )
    _require_exact_batch_capability(
        capability=dependency_batch,
        expected_batch_id=exact_analysis.dependency_evidence_batch_id,
        expected_batch_sha256=exact_analysis.dependency_evidence_batch_sha256,
        label="dependency evidence batch",
    )

    classification_outcomes = evidence_repository.resolve_batch(
        batch_id=exact_analysis.classification_evidence_batch_id,
        batch_sha256=exact_analysis.classification_evidence_batch_sha256,
    )
    dependency_outcomes = evidence_repository.resolve_batch(
        batch_id=exact_analysis.dependency_evidence_batch_id,
        batch_sha256=exact_analysis.dependency_evidence_batch_sha256,
    )
    classification_outcomes = classification_batch.verify(
        repository=evidence_repository,
        outcomes=classification_outcomes,
    )
    dependency_outcomes = dependency_batch.verify(
        repository=evidence_repository,
        outcomes=dependency_outcomes,
    )
    reproduced = verify_temporal_analysis_evidence(
        exact_analysis,
        verified_bootstrap=verified_bootstrap,
        inventory_capability=inventory_capability,
        classification_outcomes=classification_outcomes,
        dependency_outcomes=dependency_outcomes,
    )
    if reproduced != exact_proposal:
        raise TemporalProposalAuthorityError(
            "durable inference evidence does not reproduce the exact temporal proposal"
        )

    authoritative = store.load(exact_proposal.proposed_aggregate.aggregate_id)
    analysis_head = exact_proposal.binding.analysis_head
    is_bound_analysis_head = (
        authoritative is not None
        and authoritative.revision == analysis_head.revision
        and authoritative.aggregate_sha256 == analysis_head.aggregate_sha256
        and authoritative.aggregate == exact_analysis.analysis_aggregate
    )
    is_exact_lost_ack_replay = (
        authoritative is not None
        and authoritative.revision == 3
        and authoritative.aggregate_sha256 == exact_proposal.binding.proposed_aggregate_sha256
        and authoritative.aggregate == exact_proposal.proposed_aggregate
    )
    if not is_bound_analysis_head and not is_exact_lost_ack_replay:
        raise TemporalProposalAuthorityError(
            "authoritative head differs from the temporal analysis and proposal"
        )

    manifest_bytes = exact_analysis.canonical_bytes()
    if is_bound_analysis_head:
        manifest_path = evidence_repository.persist_temporal_analysis_manifest(
            manifest_id=exact_analysis.manifest_id,
            manifest_sha256=exact_analysis.manifest_sha256,
            content=manifest_bytes,
        )
    else:
        manifest_path = f"temporal/evidence/analyses/{exact_analysis.manifest_sha256}.json"
    durable_bytes = evidence_repository.resolve_temporal_analysis_manifest(
        manifest_id=exact_analysis.manifest_id,
        manifest_sha256=exact_analysis.manifest_sha256,
    )
    durable_analysis = TemporalAnalysisEvidence.from_canonical_bytes(durable_bytes)
    if durable_analysis != exact_analysis:
        raise TemporalProposalAuthorityError(
            "persisted temporal analysis differs from the exact verified evidence"
        )
    durable_reproduction = verify_temporal_analysis_evidence(
        durable_analysis,
        verified_bootstrap=verified_bootstrap,
        inventory_capability=inventory_capability,
        classification_outcomes=classification_outcomes,
        dependency_outcomes=dependency_outcomes,
    )
    if durable_reproduction != exact_proposal:
        raise TemporalProposalAuthorityError(
            "persisted temporal analysis does not reproduce the exact proposal"
        )

    operation_id = f"temporal-commit:{durable_analysis.manifest_sha256}"
    receipt = store.compare_and_swap(
        exact_proposal.proposed_aggregate,
        expected_revision=analysis_head.revision,
        operation_id=operation_id,
    )
    if not receipt.changed or receipt.revision != 3:
        raise TemporalProposalAuthorityError(
            "temporal proposal must be the changed revision-2 to revision-3 transition"
        )
    return TemporalProposalCommit(
        proposal=exact_proposal,
        operation_id=operation_id,
        temporal_analysis_manifest_id=durable_analysis.manifest_id,
        temporal_analysis_manifest_sha256=durable_analysis.manifest_sha256,
        temporal_analysis_manifest_path=manifest_path,
        evidence_repository_id=evidence_repository.repository_id,
        aggregate_id=receipt.aggregate_id,
        revision=3,
        aggregate_sha256=receipt.aggregate_sha256,
        changed=receipt.changed,
        committed_at=receipt.committed_at,
        replayed=receipt.replayed,
    )


__all__ = [
    "TemporalProposalAuthorityError",
    "commit_temporal_proposal",
]
