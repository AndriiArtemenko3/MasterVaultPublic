"""Read-only restart construction for sealed-seed managed query authority.

The managed-review resolver deliberately keeps process-local approval objects
out of SQLite.  This module reconstructs those objects for the query path from
the exact durable run binding and immutable repository evidence.  It never
creates repository roots, lock files, staging members, or aggregate commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mastervault.change_control.bootstrap import (
    VerifiedAnalysisBootstrapCapability,
    build_verified_prechange_aggregate,
    create_verified_analysis_bootstrap_capability,
)
from mastervault.change_control.incoming import (
    MANIFEST_RELATIVE_PATH,
    load_verified_incoming_event,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedRevisionDecisionRecord,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_review_repository import (
    ApprovedManagedGoverningSourceAuthority,
    ApprovedManagedInferenceContractAuthority,
    RepositoryBackedManagedReviewResolver,
    derive_managed_governing_source_adoption,
)
from mastervault.change_control.managed_staging_repository import (
    ManagedStagingRepository,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.reviewed_snapshot import (
    ReviewedTemporalSnapshotAuthority,
    resolve_reviewed_temporal_snapshot,
)
from mastervault.change_control.seed import (
    load_verified_prechange_seed_manifest_from_repository,
)
from mastervault.change_control.source_note_inventory import (
    PRECHANGE_MANIFEST_RELATIVE_PATH,
    RepositorySourceNoteInventoryResolver,
)
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence


class ManagedQueryResolverRestartError(ValueError):
    """Durable query authority could not be reconstructed exactly."""


def _exact_sha256(value: str, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ManagedQueryResolverRestartError(f"{label} must be one exact SHA-256")
    return value


def _exact_active_decision(
    value: ManagedRevisionDecisionRecord,
) -> ManagedRevisionDecisionRecord:
    if type(value) is not ManagedRevisionDecisionRecord:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires an exact active decision record"
        )
    try:
        exact = ManagedRevisionDecisionRecord.model_validate_json(
            canonical_json_bytes(value.model_dump(mode="json"))
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ManagedQueryResolverRestartError(
            "active managed decision is not canonical"
        ) from exc
    if exact != value:
        raise ManagedQueryResolverRestartError(
            "active managed decision changed during canonical reopening"
        )
    return exact


def _require_read_only_store(store: SqliteManagedChangeControlStore) -> None:
    if type(store) is not SqliteManagedChangeControlStore:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires the exact SQLite managed store"
        )
    if not getattr(store, "_read_only", False):
        raise ManagedQueryResolverRestartError(
            "managed query restart requires a read-only authority store"
        )
    try:
        query_only = store.conn.execute("PRAGMA query_only").fetchone()
    except Exception as exc:
        raise ManagedQueryResolverRestartError(
            "managed query authority query-only state cannot be verified"
        ) from exc
    if query_only is None or len(query_only) != 1 or int(query_only[0]) != 1:
        raise ManagedQueryResolverRestartError(
            "managed query authority connection is not query-only"
        )


@dataclass(frozen=True)
class SealedSeedQueryBootstrap:
    """Fresh process-local authority reconstructed from one temporal manifest."""

    temporal_analysis: TemporalAnalysisEvidence
    verified_bootstrap: VerifiedAnalysisBootstrapCapability
    prechange_head: AggregateHeadBinding
    evidence_repository: FilesystemInferenceEvidenceRepository
    source_note_resolver: RepositorySourceNoteInventoryResolver

    def __post_init__(self) -> None:
        if type(self.temporal_analysis) is not TemporalAnalysisEvidence:
            raise TypeError("sealed-seed query bootstrap requires exact temporal evidence")
        if type(self.verified_bootstrap) is not VerifiedAnalysisBootstrapCapability:
            raise TypeError("sealed-seed query bootstrap requires exact verified bootstrap")
        if type(self.prechange_head) is not AggregateHeadBinding:
            raise TypeError("sealed-seed query bootstrap requires exact pre-change head")
        if type(self.evidence_repository) is not FilesystemInferenceEvidenceRepository or (
            not self.evidence_repository.read_only
        ):
            raise TypeError("sealed-seed query bootstrap requires a read-only evidence repository")
        if type(self.source_note_resolver) is not RepositorySourceNoteInventoryResolver:
            raise TypeError("sealed-seed query bootstrap requires exact SourceNote resolver")
        binding = self.verified_bootstrap.binding
        if (
            self.temporal_analysis.proposal.binding.analysis_bootstrap != binding
            or self.prechange_head.aggregate_id != binding.aggregate_id
            or self.prechange_head.revision != binding.prechange_revision
            or self.prechange_head.aggregate_sha256 != binding.prechange_aggregate_sha256
            or self.source_note_resolver.verified_bootstrap is not self.verified_bootstrap
        ):
            raise ValueError("sealed-seed query bootstrap identities do not agree")

    @property
    def authority_context(self) -> AuthorityVerificationContext:
        return AuthorityVerificationContext.legacy(
            verified_bootstrap=self.verified_bootstrap,
            prechange_head=self.prechange_head,
        )

    @property
    def temporal_analysis_manifest_id(self) -> str:
        return self.temporal_analysis.manifest_id

    @property
    def temporal_analysis_manifest_sha256(self) -> str:
        return self.temporal_analysis.manifest_sha256


@dataclass(frozen=True)
class ManagedQueryResolverResolution:
    """Fresh resolver and the exact authority identities used to construct it."""

    resolver: RepositoryBackedManagedReviewResolver
    bootstrap: SealedSeedQueryBootstrap
    authority_context: AuthorityVerificationContext
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority
    active_decision: ManagedRevisionDecisionRecord
    temporal_request_id: str

    def __post_init__(self) -> None:
        if type(self.resolver) is not RepositoryBackedManagedReviewResolver:
            raise TypeError("managed query restart returned a substituted resolver")
        if type(self.bootstrap) is not SealedSeedQueryBootstrap:
            raise TypeError("managed query restart returned a substituted bootstrap")
        if type(self.authority_context) is not AuthorityVerificationContext:
            raise TypeError("managed query restart returned a substituted authority context")
        if type(self.reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
            raise TypeError("managed query restart returned substituted reviewed authority")
        if type(self.active_decision) is not ManagedRevisionDecisionRecord:
            raise TypeError("managed query restart returned a substituted decision")
        if type(self.temporal_request_id) is not str or not self.temporal_request_id:
            raise TypeError("managed query restart returned an invalid temporal request ID")


def reopen_sealed_seed_query_bootstrap(
    *,
    seed_repository_root: Path,
    evidence_repository_root: Path,
    temporal_analysis_manifest_sha256: str,
) -> SealedSeedQueryBootstrap:
    """Reconstruct generation-zero authority without creating filesystem state."""

    manifest_sha256 = _exact_sha256(
        temporal_analysis_manifest_sha256,
        label="temporal_analysis_manifest_sha256",
    )
    evidence = FilesystemInferenceEvidenceRepository(
        Path(evidence_repository_root),
        create=False,
        read_only=True,
    )
    manifest_id = f"temporal-analysis:{manifest_sha256}"
    try:
        manifest_bytes = evidence.resolve_temporal_analysis_manifest(
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
        )
        temporal_analysis = TemporalAnalysisEvidence.from_canonical_bytes(manifest_bytes)
    except (TypeError, ValueError) as exc:
        raise ManagedQueryResolverRestartError(
            "temporal analysis cannot be reopened from immutable evidence"
        ) from exc
    if (
        temporal_analysis.manifest_id != manifest_id
        or temporal_analysis.manifest_sha256 != manifest_sha256
    ):
        raise ManagedQueryResolverRestartError(
            "temporal analysis differs from the configured exact locator"
        )

    seed_root = Path(seed_repository_root)
    prechange_manifest = seed_root / PRECHANGE_MANIFEST_RELATIVE_PATH
    incoming_manifest = seed_root / MANIFEST_RELATIVE_PATH
    try:
        seed_context = load_verified_prechange_seed_manifest_from_repository(
            repo_root=seed_root,
            manifest_path=prechange_manifest,
        )
        incoming = load_verified_incoming_event(
            repo_root=seed_root,
            manifest_path=incoming_manifest,
        )
        prechange_aggregate = build_verified_prechange_aggregate(
            repo_root=seed_root,
            manifest_context=seed_context,
        )
        durable_binding = temporal_analysis.proposal.binding.analysis_bootstrap
        verified_bootstrap = create_verified_analysis_bootstrap_capability(
            repo_root=seed_root,
            seed_context=seed_context,
            incoming_event=incoming,
            prechange_aggregate=prechange_aggregate,
            analysis_aggregate=temporal_analysis.analysis_aggregate,
            prechange_operation_id=durable_binding.prechange_operation_id,
            analysis_operation_id=durable_binding.analysis_operation_id,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ManagedQueryResolverRestartError(
            "sealed seed roots do not reproduce the temporal analysis bootstrap"
        ) from exc
    if verified_bootstrap.binding != durable_binding:
        raise ManagedQueryResolverRestartError(
            "fresh sealed-seed bootstrap differs from durable temporal authority"
        )
    prechange_head = AggregateHeadBinding.create(
        aggregate_id=durable_binding.aggregate_id,
        revision=durable_binding.prechange_revision,
        aggregate_sha256=durable_binding.prechange_aggregate_sha256,
    )
    source_note_resolver = RepositorySourceNoteInventoryResolver(
        repo_root=seed_root,
        prechange_manifest_path=prechange_manifest,
        incoming_manifest_path=incoming_manifest,
        verified_bootstrap=verified_bootstrap,
    )
    return SealedSeedQueryBootstrap(
        temporal_analysis=temporal_analysis,
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
        evidence_repository=evidence,
        source_note_resolver=source_note_resolver,
    )


def _algorithm_artifact(
    active_decision: ManagedRevisionDecisionRecord,
) -> ManagedArtifactRef:
    bundle = active_decision.command.bundle
    run_binding = bundle.run_binding
    if type(run_binding) is not ManagedRunBindingV2:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires an active v2 run binding"
        )
    contract = run_binding.inference_contract
    expected_path = f"inference/algorithms/{contract.algorithm_manifest_sha256}.json"
    candidates: list[ManagedArtifactRef] = []
    for target in bundle.targets:
        receipt = target.subject.inference_receipt
        try:
            contract.require_receipt(receipt)
        except ValueError as exc:
            raise ManagedQueryResolverRestartError(
                "active managed target differs from its run-level inference contract"
            ) from exc
        matches = tuple(
            artifact
            for artifact in receipt.input_artifacts
            if artifact.path == expected_path
            and artifact.sha256 == contract.algorithm_manifest_sha256
            and artifact.kind == ManagedArtifactKind.INFERENCE_INPUT
        )
        if len(matches) != 1:
            raise ManagedQueryResolverRestartError(
                "active managed target does not bind one exact algorithm artifact"
            )
        candidates.append(matches[0])
    if not candidates or any(candidate != candidates[0] for candidate in candidates[1:]):
        raise ManagedQueryResolverRestartError(
            "active managed targets disagree on algorithm artifact identity"
        )
    return candidates[0]


def build_read_only_managed_query_resolver(
    *,
    store: SqliteManagedChangeControlStore,
    active_decision: ManagedRevisionDecisionRecord,
    bootstrap: SealedSeedQueryBootstrap,
    canonical_repository_root: Path,
    authority_context: AuthorityVerificationContext | None = None,
) -> ManagedQueryResolverResolution:
    """Freshly rebuild one active v2 resolver exclusively through query-only reads."""

    _require_read_only_store(store)
    decision = _exact_active_decision(active_decision)
    if type(bootstrap) is not SealedSeedQueryBootstrap:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires an exact sealed-seed bootstrap"
        )
    context = bootstrap.authority_context if authority_context is None else authority_context
    if type(context) is not AuthorityVerificationContext:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires an exact authority context"
        )
    run_binding = decision.command.bundle.run_binding
    if type(run_binding) is not ManagedRunBindingV2:
        raise ManagedQueryResolverRestartError(
            "managed query restart requires an active v2 run binding"
        )
    if (
        run_binding.analysis_set.analysis_bootstrap != bootstrap.verified_bootstrap.binding
        or run_binding.prechange_head != bootstrap.prechange_head
        or run_binding.operation_id
        != f"temporal-commit:{bootstrap.temporal_analysis_manifest_sha256}"
    ):
        raise ManagedQueryResolverRestartError(
            "active managed run differs from the configured sealed-seed bootstrap"
        )

    authoritative = store.get_active_managed_decision_record(
        run_binding.prechange_head.aggregate_id,
        authority_context=context,
    )
    if authoritative != decision:
        raise ManagedQueryResolverRestartError(
            "supplied managed decision is not the exact active generation origin"
        )
    temporal_request_id = store.get_active_managed_temporal_request_id(
        run_binding.prechange_head.aggregate_id,
        active_decision=decision,
        authority_context=context,
    )

    aggregate_store = SqliteChangeControlStore(
        store.db_path,
        migrations_dir=store.migrations_dir,
        secure_open=True,
        read_only=True,
    )
    try:
        reviewed_snapshot = resolve_reviewed_temporal_snapshot(
            aggregate_store,
            temporal_analysis_manifest_id=bootstrap.temporal_analysis_manifest_id,
            temporal_analysis_manifest_sha256=(
                bootstrap.temporal_analysis_manifest_sha256
            ),
            temporal_request_id=temporal_request_id,
            evidence_repository=bootstrap.evidence_repository,
            source_note_resolver=bootstrap.source_note_resolver,
            read_only=True,
        )
    finally:
        aggregate_store.close()

    bundle = decision.command.bundle
    adoption = run_binding.governing_source_adoption
    admission = run_binding.revision_planning_admission
    if (
        reviewed_snapshot.binding.binding_id != adoption.reviewed_snapshot_binding_id
        or reviewed_snapshot.binding.binding_sha256
        != adoption.reviewed_snapshot_binding_sha256
        or reviewed_snapshot.binding.temporal_decision_record_sha256
        != adoption.temporal_decision_record_sha256
        or reviewed_snapshot.binding.reviewed_head != adoption.reviewed_head
        or reviewed_snapshot.binding.reviewed_inventory_sha256
        != adoption.reviewed_inventory_sha256
        or reviewed_snapshot.binding.evidence_repository_id
        != adoption.evidence_repository_id
        or reviewed_snapshot.temporal_prerequisite != bundle.temporal_prerequisite
        or admission.reviewed_snapshot_binding_id
        != reviewed_snapshot.binding.binding_id
        or admission.reviewed_snapshot_binding_sha256
        != reviewed_snapshot.binding.binding_sha256
        or admission.temporal_decision_record_sha256
        != reviewed_snapshot.binding.temporal_decision_record_sha256
    ):
        raise ManagedQueryResolverRestartError(
            "fresh reviewed snapshot differs from active managed bindings"
        )

    canonical_root = Path(canonical_repository_root)
    reconstructed_adoption = derive_managed_governing_source_adoption(
        reviewed_snapshot=reviewed_snapshot,
        analysis_bootstrap=run_binding.analysis_set.analysis_bootstrap,
        repo_root=canonical_root,
        manifest_path=canonical_root / MANIFEST_RELATIVE_PATH,
        evidence_repository_id=admission.repository_id,
    )
    if reconstructed_adoption != adoption:
        raise ManagedQueryResolverRestartError(
            "canonical repository roots differ from active governing-source adoption"
        )

    algorithm_artifact = _algorithm_artifact(decision)
    algorithm_bytes = bootstrap.evidence_repository.open_artifact(algorithm_artifact)
    approved_contract = ApprovedManagedInferenceContractAuthority(
        contract=run_binding.inference_contract,
        algorithm_manifest_bytes=algorithm_bytes,
    )
    approved_source = ApprovedManagedGoverningSourceAuthority(
        adoption=adoption,
        reviewed_snapshot=reviewed_snapshot,
        analysis_bootstrap=run_binding.analysis_set.analysis_bootstrap,
    )
    staging_repository = ManagedStagingRepository(
        bootstrap.evidence_repository.root,
        create=False,
        read_only=True,
    )
    resolver = RepositoryBackedManagedReviewResolver(
        evidence_repository=bootstrap.evidence_repository,
        staging_repository=staging_repository,
        canonical_root=canonical_root,
        approved_contracts=(approved_contract,),
        revision_admissions=(admission,),
        governing_sources=(approved_source,),
    )
    if (
        resolver.resolve_revision_planning_admission(admission) != admission
        or resolver.resolve_governing_source_adoption(adoption) != adoption
        or resolver.resolve_approved_inference_contract(run_binding.inference_contract)
        != run_binding.inference_contract
    ):
        raise ManagedQueryResolverRestartError(
            "fresh repository resolver differs from active managed authority"
        )
    return ManagedQueryResolverResolution(
        resolver=resolver,
        bootstrap=bootstrap,
        authority_context=context,
        reviewed_snapshot=reviewed_snapshot,
        active_decision=decision,
        temporal_request_id=temporal_request_id,
    )


__all__ = [
    "ManagedQueryResolverResolution",
    "ManagedQueryResolverRestartError",
    "SealedSeedQueryBootstrap",
    "build_read_only_managed_query_resolver",
    "reopen_sealed_seed_query_bootstrap",
]
