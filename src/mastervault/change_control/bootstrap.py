"""Deterministic PR-A bootstrap from sealed runtime source capabilities."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, SupportsIndex

from mastervault.change_control.analysis_binding import (
    ANALYSIS_AGGREGATE_ID,
    MAX_INCOMING_CLAIMS,
    AnalysisBootstrapBinding,
    AnalysisBootstrapError,
    AnalysisBootstrapIntegrityError,
)
from mastervault.change_control.claim_scopes import (
    CLAIM_SCOPE_POLICY_VERSION,
    claim_scopes_v1,
)
from mastervault.change_control.incoming import (
    VerifiedIncomingEvent,
    load_verified_incoming_event,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    DependencyRegistry,
    DocumentReplacementSet,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    RelationGraph,
    TemporalConstraintSet,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.seed import (
    VerifiedPrechangeSeedManifest,
    load_verified_prechange_seed_manifest,
    resolve_claim_revision,
    verify_seed_document_context,
)
from mastervault.change_control.store import (
    ChangeControlCommit,
    ChangeControlSnapshot,
    SqliteChangeControlStore,
)

EXPECTED_PRECHANGE_DOCUMENTS: Final = 7
EXPECTED_PRECHANGE_CLAIMS: Final = 69
EXPECTED_ANALYSIS_DOCUMENTS: Final = 8
EXPECTED_ANALYSIS_CLAIMS: Final = 79
_VERIFIED_BOOTSTRAP_TOKEN = object()
_VERIFIED_BOOTSTRAP_SECRET = os.urandom(32)

if TYPE_CHECKING:
    from mastervault.change_control.managed_review import (
        AggregateHeadBinding,
        AuthorityRevisionBinding,
    )


class AnalysisBootstrapStaleError(AnalysisBootstrapError):
    """The live aggregate head has moved beyond the bootstrap-owned revision."""


@dataclass(frozen=True, eq=False)
class VerifiedAnalysisBootstrapCapability:
    """Process-local proof that the pure binding was rederived from repository roots."""

    binding: AnalysisBootstrapBinding
    prechange_aggregate_sha256: str
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if self._token is not _VERIFIED_BOOTSTRAP_TOKEN:
            raise TypeError("verified bootstrap capabilities are service-created only")

    def __reduce__(self) -> Any:
        raise TypeError("verified bootstrap capabilities are process-local and non-serializable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("verified bootstrap capabilities are process-local and non-serializable")


def _bootstrap_capability_seal(
    *, binding: AnalysisBootstrapBinding, prechange_aggregate_sha256: str
) -> str:
    payload = {
        "namespace": "mastervault.verified-analysis-bootstrap-capability.v1",
        "binding": binding.model_dump(mode="json"),
        "prechange_aggregate_sha256": prechange_aggregate_sha256,
    }
    return hmac.new(
        _VERIFIED_BOOTSTRAP_SECRET,
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def _verify_bootstrap_capability(
    capability: VerifiedAnalysisBootstrapCapability,
) -> AnalysisBootstrapBinding:
    if capability._token is not _VERIFIED_BOOTSTRAP_TOKEN:
        raise AnalysisBootstrapIntegrityError("bootstrap capability is not repository verified")
    try:
        binding = AnalysisBootstrapBinding.model_validate(
            capability.binding.model_dump(mode="python")
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise AnalysisBootstrapIntegrityError("bootstrap binding was altered") from exc
    if binding != capability.binding:
        raise AnalysisBootstrapIntegrityError("bootstrap binding was altered")
    expected_seal = _bootstrap_capability_seal(
        binding=binding,
        prechange_aggregate_sha256=capability.prechange_aggregate_sha256,
    )
    if not hmac.compare_digest(capability._seal, expected_seal):
        raise AnalysisBootstrapIntegrityError("bootstrap capability seal was altered")
    if capability.prechange_aggregate_sha256 != binding.prechange_aggregate_sha256:
        raise AnalysisBootstrapIntegrityError("bootstrap pre-change SHA binding was altered")
    return binding


def verify_analysis_bootstrap_snapshot(
    capability: VerifiedAnalysisBootstrapCapability,
    snapshot: ChangeControlSnapshot,
) -> AnalysisBootstrapBinding:
    """Authenticate one process-local bootstrap against its exact revision-2 snapshot."""

    binding = _verify_bootstrap_capability(capability)
    if (
        snapshot.aggregate.aggregate_id != binding.aggregate_id
        or snapshot.revision != binding.analysis_revision
        or snapshot.aggregate_sha256 != binding.analysis_aggregate_sha256
        or aggregate_sha256(snapshot.aggregate) != snapshot.aggregate_sha256
    ):
        raise AnalysisBootstrapIntegrityError(
            "analysis snapshot does not match the verified bootstrap binding"
        )
    return binding


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def create_verified_analysis_bootstrap_binding(
    *,
    repo_root: Path,
    seed_context: VerifiedPrechangeSeedManifest,
    incoming_event: VerifiedIncomingEvent,
    prechange_aggregate: ChangeControlAggregate,
    analysis_aggregate: ChangeControlAggregate,
    prechange_operation_id: str,
    analysis_operation_id: str,
) -> AnalysisBootstrapBinding:
    """Construct the pure binding after verifying every repository-backed input.

    Callers compare or persist bindings by ``binding_id`` and
    ``binding_sha256``. The service deliberately returns the exact pure model
    rather than a service-layer subclass whose Python identity could differ
    after canonical validation.
    """

    verified_prechange = build_verified_prechange_aggregate(
        repo_root=repo_root,
        manifest_context=seed_context,
    )
    if prechange_aggregate != verified_prechange:
        raise AnalysisBootstrapIntegrityError(
            "pre-change aggregate is not the exact projection of the sealed seed context"
        )
    if (
        prechange_aggregate.aggregate_id != ANALYSIS_AGGREGATE_ID
        or len(prechange_aggregate.documents.documents) != EXPECTED_PRECHANGE_DOCUMENTS
        or len(prechange_aggregate.claims.revisions) != EXPECTED_PRECHANGE_CLAIMS
        or prechange_aggregate.relation_graph.assessments
        or prechange_aggregate.dependencies.assessments
        or prechange_aggregate.document_replacements.assessments
        or prechange_aggregate.temporal_constraints.constraints
    ):
        raise AnalysisBootstrapIntegrityError(
            "pre-change aggregate is not the exact empty-graph revision-1 contract"
        )
    expected_analysis, changed_claim_revision_ids = _build_analysis_aggregate(
        verified_prechange,
        incoming_event,
    )
    if analysis_aggregate != expected_analysis:
        raise AnalysisBootstrapIntegrityError(
            "analysis aggregate is not the exact projection of pre-change plus sealed incoming roots"
        )
    prechange_aggregate_sha256 = aggregate_sha256(prechange_aggregate)
    analysis_aggregate_sha256 = aggregate_sha256(analysis_aggregate)
    evidence_sha256 = incoming_claim_evidence_sha256(incoming_event)
    manifest = seed_context.manifest
    incoming_manifest = incoming_event.manifest
    incoming_document = incoming_event.document
    ordered_claim_ids = tuple(sorted(changed_claim_revision_ids))
    inputs = {
        "aggregate_id": ANALYSIS_AGGREGATE_ID,
        "analysis_as_of": incoming_manifest.arrived_on.isoformat(),
        "analysis_operation_id": analysis_operation_id,
        "alignment_attestation_id": incoming_event.alignment_attestation_id,
        "alignment_attestation_sha256": incoming_event.alignment_attestation_sha256,
        "alignment_payload_sha256": incoming_event.alignment_payload_sha256,
        "alignment_policy_version": incoming_event.alignment_policy_version,
        "incoming_document_id": incoming_document.document_id,
        "incoming_document_version_id": incoming_document.document_version_id,
        "incoming_claim_evidence_sha256": evidence_sha256,
        "incoming_event_id": incoming_manifest.event_id,
        "incoming_event_identity": incoming_event.event_identity,
        "incoming_manifest_sha256": incoming_event.manifest_sha256,
        "prechange_operation_id": prechange_operation_id,
        "schema_version": 1,
        "scope_policy_version": CLAIM_SCOPE_POLICY_VERSION,
        "seed_as_of": manifest.as_of.isoformat(),
        "seed_manifest_sha256": seed_context.manifest_sha256,
        "seed_scenario_id": manifest.scenario_id,
    }
    values = {
        **inputs,
        "analysis_aggregate_sha256": analysis_aggregate_sha256,
        "analysis_revision": 2,
        "canonical_input_sha256": _sha256(inputs),
        "changed_claim_revision_ids": ordered_claim_ids,
        "prechange_aggregate_sha256": prechange_aggregate_sha256,
        "prechange_revision": 1,
    }
    digest = _sha256(values)
    model_values = {
        **values,
        "analysis_as_of": incoming_manifest.arrived_on,
        "seed_as_of": manifest.as_of,
    }
    return AnalysisBootstrapBinding.model_validate(
        {
            "binding_id": f"analysis-bootstrap:{digest}",
            "binding_sha256": digest,
            **model_values,
        }
    )


def create_verified_analysis_bootstrap_capability(
    *,
    repo_root: Path,
    seed_context: VerifiedPrechangeSeedManifest,
    incoming_event: VerifiedIncomingEvent,
    prechange_aggregate: ChangeControlAggregate,
    analysis_aggregate: ChangeControlAggregate,
    prechange_operation_id: str,
    analysis_operation_id: str,
) -> VerifiedAnalysisBootstrapCapability:
    """Return non-serializable authority evidence after full repository verification."""

    binding = create_verified_analysis_bootstrap_binding(
        repo_root=repo_root,
        seed_context=seed_context,
        incoming_event=incoming_event,
        prechange_aggregate=prechange_aggregate,
        analysis_aggregate=analysis_aggregate,
        prechange_operation_id=prechange_operation_id,
        analysis_operation_id=analysis_operation_id,
    )
    prechange_sha256 = aggregate_sha256(prechange_aggregate)
    return VerifiedAnalysisBootstrapCapability(
        binding=binding,
        prechange_aggregate_sha256=prechange_sha256,
        _token=_VERIFIED_BOOTSTRAP_TOKEN,
        _seal=_bootstrap_capability_seal(
            binding=binding,
            prechange_aggregate_sha256=prechange_sha256,
        ),
    )


def verify_generation_zero_authority(
    *,
    authority: AuthorityRevisionBinding,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability,
    prechange_head: AggregateHeadBinding,
) -> None:
    """Resolve a generation-zero pointer against repository-verified bootstrap evidence."""

    binding = _verify_bootstrap_capability(verified_bootstrap)
    if (
        prechange_head.aggregate_id != binding.aggregate_id
        or prechange_head.revision != binding.prechange_revision
        or prechange_head.aggregate_sha256 != verified_bootstrap.prechange_aggregate_sha256
        or prechange_head.aggregate_sha256 != binding.prechange_aggregate_sha256
    ):
        raise AnalysisBootstrapIntegrityError(
            "generation-zero resolution requires the exact verified prechange head"
        )
    authority.verify_generation_zero_origin(
        analysis_bootstrap=binding,
        prechange_head=prechange_head,
    )


@dataclass(frozen=True)
class AnalysisBootstrapResult:
    """Committed receipts plus the exact reloaded revision-2 snapshot."""

    binding: AnalysisBootstrapBinding
    verification_capability: VerifiedAnalysisBootstrapCapability
    incoming_event: VerifiedIncomingEvent
    prechange_commit: ChangeControlCommit
    analysis_commit: ChangeControlCommit
    snapshot: ChangeControlSnapshot


def _empty_aggregate(
    *,
    documents: tuple[DocumentVersionMetadata, ...],
    claims: tuple[VersionedClaimRevision, ...],
) -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id=ANALYSIS_AGGREGATE_ID,
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )


def build_verified_prechange_aggregate(
    *,
    repo_root: Path,
    manifest_context: VerifiedPrechangeSeedManifest,
) -> ChangeControlAggregate:
    """Resolve the complete seed inventory from sealed raw/note contexts."""

    documents = []
    claims: list[VersionedClaimRevision] = []
    for seed_document in sorted(
        manifest_context.manifest.documents,
        key=lambda item: item.document_id,
    ):
        context = verify_seed_document_context(
            repo_root=repo_root,
            manifest_context=manifest_context,
            document_id=seed_document.document_id,
        )
        documents.append(context.document)
        for claim in sorted(context.source_note.key_claims, key=lambda item: item.id):
            claims.append(
                resolve_claim_revision(
                    context=context,
                    source_claim_id=claim.id,
                    declared_effective_from=seed_document.declared_effective_from,
                    declared_effective_to=seed_document.declared_effective_to,
                    scopes=claim_scopes_v1(
                        document_family=seed_document.document_family,
                        affects=tuple(claim.affects),
                    ),
                )
            )
    if len(documents) != EXPECTED_PRECHANGE_DOCUMENTS:
        raise AnalysisBootstrapIntegrityError(
            f"pre-change seed must resolve {EXPECTED_PRECHANGE_DOCUMENTS} documents"
        )
    if len(claims) != EXPECTED_PRECHANGE_CLAIMS:
        raise AnalysisBootstrapIntegrityError(
            f"pre-change seed must resolve {EXPECTED_PRECHANGE_CLAIMS} claims"
        )
    if len({item.document_version_id for item in documents}) != len(documents):
        raise AnalysisBootstrapIntegrityError("pre-change seed contains duplicate document roots")
    if len({item.claim_revision_id for item in claims}) != len(claims):
        raise AnalysisBootstrapIntegrityError("pre-change seed contains duplicate claim roots")
    return _empty_aggregate(documents=tuple(documents), claims=tuple(claims))


def _build_analysis_aggregate(
    prechange: ChangeControlAggregate,
    incoming_event: VerifiedIncomingEvent,
) -> tuple[ChangeControlAggregate, tuple[str, ...]]:
    incoming_document = incoming_event.document
    incoming_claims = incoming_event.claim_revisions
    if len(incoming_claims) != MAX_INCOMING_CLAIMS:
        raise AnalysisBootstrapIntegrityError(
            f"incoming event must resolve {MAX_INCOMING_CLAIMS} changed claims"
        )
    documents = (*prechange.documents.documents, incoming_document)
    claims = (*prechange.claims.revisions, *incoming_claims)
    if len({item.document_version_id for item in documents}) != len(documents):
        raise AnalysisBootstrapIntegrityError("analysis input contains duplicate document roots")
    if len({item.claim_revision_id for item in claims}) != len(claims):
        raise AnalysisBootstrapIntegrityError("analysis input contains duplicate claim roots")
    analysis = _empty_aggregate(documents=documents, claims=claims)
    if len(analysis.documents.documents) != EXPECTED_ANALYSIS_DOCUMENTS:
        raise AnalysisBootstrapIntegrityError(
            f"analysis aggregate must contain {EXPECTED_ANALYSIS_DOCUMENTS} documents"
        )
    if len(analysis.claims.revisions) != EXPECTED_ANALYSIS_CLAIMS:
        raise AnalysisBootstrapIntegrityError(
            f"analysis aggregate must contain {EXPECTED_ANALYSIS_CLAIMS} claims"
        )
    changed = tuple(sorted(item.claim_revision_id for item in incoming_claims))
    return analysis, changed


def incoming_claim_evidence_sha256(incoming_event: VerifiedIncomingEvent) -> str:
    """Hash the exact sealed changed-claim semantics and raw byte evidence."""

    grounded = tuple(
        sorted(
            incoming_event.grounded_claims,
            key=lambda item: item.revision.claim_revision_id,
        )
    )
    payload = {
        "alignment_attestation_id": incoming_event.alignment_attestation_id,
        "alignment_attestation_sha256": incoming_event.alignment_attestation_sha256,
        "alignment_payload_sha256": incoming_event.alignment_payload_sha256,
        "alignment_policy_version": incoming_event.alignment_policy_version,
        "claim_scope_policy_version": incoming_event.claim_scope_policy_version,
        "claims": [
            {
                "claim_identity_id": item.revision.claim_identity_id,
                "claim_revision_id": item.revision.claim_revision_id,
                "raw_evidence": [span.model_dump(mode="json") for span in item.raw_evidence],
                "scopes": list(item.revision.scopes),
                "source_claim_id": item.revision.source.source_claim_id,
                "statement": item.revision.statement,
                "processed_statement_sha256": item.processed_statement_sha256,
                "extractive_statement_sha256": item.extractive_statement_sha256,
            }
            for item in grounded
        ],
        "event_identity": incoming_event.event_identity,
        "schema_version": 1,
    }
    return _sha256(payload)


def _require_snapshot(
    snapshot: ChangeControlSnapshot | None,
    *,
    expected_revision: int,
    expected_aggregate: ChangeControlAggregate,
    phase: str,
) -> ChangeControlSnapshot:
    if snapshot is None:
        raise AnalysisBootstrapIntegrityError(f"{phase} aggregate snapshot is missing")
    expected_sha256 = aggregate_sha256(expected_aggregate)
    if snapshot.revision > expected_revision:
        raise AnalysisBootstrapStaleError(
            f"{phase} head advanced to revision {snapshot.revision}; expected {expected_revision}"
        )
    if (
        snapshot.revision != expected_revision
        or snapshot.aggregate_sha256 != expected_sha256
        or snapshot.aggregate != expected_aggregate
    ):
        raise AnalysisBootstrapIntegrityError(f"{phase} aggregate snapshot is not exact")
    return snapshot


def bootstrap_analysis_aggregate(
    *,
    repo_root: Path,
    prechange_manifest_path: Path,
    incoming_manifest_path: Path,
    store: SqliteChangeControlStore,
    prechange_operation_id: str,
    analysis_operation_id: str,
) -> AnalysisBootstrapResult:
    """Persist and reload the exact deterministic PR-A analysis snapshot.

    All source capabilities and both aggregate payloads are fully constructed
    before the first store mutation. Revision 1 is the sole safe recovery point;
    a returned result always owns and exactly reloaded revision 2.
    """

    seed_context = load_verified_prechange_seed_manifest(prechange_manifest_path)
    incoming_event = load_verified_incoming_event(
        repo_root=repo_root,
        manifest_path=incoming_manifest_path,
    )
    prechange = build_verified_prechange_aggregate(
        repo_root=repo_root,
        manifest_context=seed_context,
    )
    analysis, _changed_claim_ids = _build_analysis_aggregate(prechange, incoming_event)
    verification_capability = create_verified_analysis_bootstrap_capability(
        repo_root=repo_root,
        seed_context=seed_context,
        incoming_event=incoming_event,
        prechange_aggregate=prechange,
        analysis_aggregate=analysis,
        prechange_operation_id=prechange_operation_id,
        analysis_operation_id=analysis_operation_id,
    )
    binding = verification_capability.binding

    prechange_commit = store.create(prechange, operation_id=prechange_operation_id)
    live = store.load(ANALYSIS_AGGREGATE_ID)
    if live is not None and live.revision > 2:
        raise AnalysisBootstrapStaleError(
            f"analysis head advanced to revision {live.revision}; bootstrap owns only revision 2"
        )
    if live is not None and live.revision == 1:
        _require_snapshot(
            live,
            expected_revision=1,
            expected_aggregate=prechange,
            phase="pre-change",
        )
    analysis_commit = store.compare_and_swap(
        analysis,
        expected_revision=1,
        operation_id=analysis_operation_id,
    )
    snapshot = _require_snapshot(
        store.load(ANALYSIS_AGGREGATE_ID),
        expected_revision=2,
        expected_aggregate=analysis,
        phase="analysis",
    )
    if (
        prechange_commit.revision != binding.prechange_revision
        or prechange_commit.aggregate_sha256 != binding.prechange_aggregate_sha256
        or analysis_commit.revision != binding.analysis_revision
        or analysis_commit.aggregate_sha256 != binding.analysis_aggregate_sha256
    ):
        raise AnalysisBootstrapIntegrityError("store receipts do not match the bootstrap binding")
    return AnalysisBootstrapResult(
        binding=binding,
        verification_capability=verification_capability,
        incoming_event=incoming_event,
        prechange_commit=prechange_commit,
        analysis_commit=analysis_commit,
        snapshot=snapshot,
    )


__all__ = [
    "ANALYSIS_AGGREGATE_ID",
    "AnalysisBootstrapBinding",
    "AnalysisBootstrapError",
    "AnalysisBootstrapIntegrityError",
    "AnalysisBootstrapResult",
    "AnalysisBootstrapStaleError",
    "VerifiedAnalysisBootstrapCapability",
    "bootstrap_analysis_aggregate",
    "build_verified_prechange_aggregate",
    "create_verified_analysis_bootstrap_binding",
    "create_verified_analysis_bootstrap_capability",
    "incoming_claim_evidence_sha256",
    "verify_analysis_bootstrap_snapshot",
    "verify_generation_zero_authority",
]
