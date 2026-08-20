"""Generic V2 analysis bootstrap and complete SourceNote reconstruction.

This module is an additive authority path.  It consumes only a freshly verified
workspace generation-zero capability and a freshly reopened generic evidence
capability; the sealed fixture bootstrap remains unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Any, SupportsIndex

from mastervault.change_control.analysis_binding import (
    AnalysisBootstrapIntegrityError,
    GenericAnalysisBootstrapBindingV2,
)
from mastervault.change_control.claim_scopes import claim_scopes_v1
from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    SourceNoteInventory,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    ReopenedGenericEvidenceV2,
    RepositoryVerifiedGenericEvidenceV2,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
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
from mastervault.change_control.store import (
    ChangeControlCommit,
    ChangeControlSnapshot,
    SqliteChangeControlStore,
)
from mastervault.change_control.workspace_bootstrap import (
    VerifiedWorkspaceBootstrapCapability,
    verify_workspace_bootstrap_capability,
)
from mastervault.vaultfs.frontmatter import split_frontmatter

_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)


class GenericAnalysisIntegrityError(AnalysisBootstrapIntegrityError):
    """Generic evidence, workspace authority, or aggregate state is not exact."""


class GenericAnalysisStaleError(GenericAnalysisIntegrityError):
    """The workspace aggregate has already advanced beyond this one-event seam."""


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _empty_graph_projection(
    prechange: ChangeControlAggregate,
    *,
    document: DocumentVersionMetadata,
    claims: tuple[VersionedClaimRevision, ...],
) -> ChangeControlAggregate:
    if (
        prechange.relation_graph.assessments
        or prechange.dependencies.assessments
        or prechange.document_replacements.assessments
        or prechange.temporal_constraints.constraints
    ):
        raise GenericAnalysisIntegrityError(
            "generic analysis requires the exact generation-zero empty-graph aggregate"
        )
    documents = (*prechange.documents.documents, document)
    revisions = (*prechange.claims.revisions, *claims)
    if len({item.document_version_id for item in documents}) != len(documents):
        raise GenericAnalysisIntegrityError(
            "generic incoming document duplicates a generation-zero document version"
        )
    if len({item.claim_revision_id for item in revisions}) != len(revisions):
        raise GenericAnalysisIntegrityError(
            "generic incoming claims duplicate generation-zero claim revisions"
        )
    return ChangeControlAggregate.create(
        aggregate_id=prechange.aggregate_id,
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(revisions),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )


def _generic_projection(
    evidence: ReopenedGenericEvidenceV2,
) -> tuple[DocumentVersionMetadata, tuple[VersionedClaimRevision, ...]]:
    metadata = evidence.admission.metadata
    document = DocumentVersionMetadata.create(
        document_id=metadata.document_id,
        document_family=metadata.document_family,
        version_label=metadata.version_label,
        source_path=evidence.source.source_locator,
        source_sha256=evidence.source.source_sha256,
        declared_effective_from=metadata.declared_effective_from,
        declared_effective_to=metadata.declared_effective_to,
        role=metadata.role,
        authority=metadata.authority,
    )
    revisions = tuple(
        VersionedClaimRevision.create(
            document=document,
            source=ClaimSourceReference(
                source_note_path=evidence.source.source_note_locator,
                source_note_sha256=evidence.source.source_note_sha256,
                source_claim_id=claim.claim_id,
                evidence=(),
            ),
            statement=claim.statement,
            declared_effective_from=metadata.declared_effective_from,
            declared_effective_to=metadata.declared_effective_to,
            scopes=claim_scopes_v1(
                document_family=metadata.document_family,
                affects=claim.affects,
            ),
        )
        for claim in evidence.projection.claims
    )
    registry = ClaimRevisionRegistry.create(revisions)
    return document, registry.revisions


def _incoming_evidence_sha256(evidence: ReopenedGenericEvidenceV2) -> str:
    return _sha256(
        {
            "namespace": "mastervault.generic-analysis-grounded-evidence.v2",
            "admission": evidence.admission.model_dump(mode="json"),
            "source": evidence.source.model_dump(mode="json"),
            "projection": evidence.projection.model_dump(mode="json"),
            "inference": evidence.inference.model_dump(mode="json"),
            "raw_source_sha256": hashlib.sha256(evidence.raw_source).hexdigest(),
            "source_note_sha256": hashlib.sha256(evidence.source_note).hexdigest(),
        }
    )


def _create_binding(
    *,
    workspace_capability: VerifiedWorkspaceBootstrapCapability,
    evidence: ReopenedGenericEvidenceV2,
    prechange: ChangeControlSnapshot,
    analysis: ChangeControlAggregate,
    document: DocumentVersionMetadata,
    claims: tuple[VersionedClaimRevision, ...],
    operation_seed_sha256: str,
) -> GenericAnalysisBootstrapBindingV2:
    state = verify_workspace_bootstrap_capability(workspace_capability)
    inventory_receipt, readiness = state.require_complete()
    metadata = evidence.admission.metadata
    event_identity = "event:" + _sha256(
        {
            "namespace": "mastervault.generic-incoming-event.v2",
            "event_id": metadata.event_id,
            "bundle_id": evidence.bundle.bundle_id,
            "projection_sha256": evidence.projection.projection_sha256,
        }
    )
    values: dict[str, Any] = {
        "schema_version": 2,
        "binding_kind": "generic-analysis-bootstrap-v2",
        "scope_policy_version": "claim-scopes-v1",
        "aggregate_id": prechange.aggregate.aggregate_id,
        "workspace_bootstrap_id": state.intent.bootstrap_id,
        "workspace_intent_sha256": state.intent.intent_sha256,
        "workspace_inventory_id": state.inventory.inventory_id,
        "workspace_inventory_sha256": state.inventory.inventory_sha256,
        "workspace_inventory_receipt_id": inventory_receipt.receipt_id,
        "workspace_inventory_receipt_sha256": inventory_receipt.receipt_sha256,
        "workspace_readiness_receipt_id": readiness.receipt_id,
        "workspace_readiness_receipt_sha256": readiness.receipt_sha256,
        "prechange_revision": 1,
        "prechange_aggregate_sha256": prechange.aggregate_sha256,
        "incoming_event_id": metadata.event_id,
        "incoming_event_identity": event_identity,
        "incoming_bundle_id": evidence.bundle.bundle_id,
        "incoming_bundle_sha256": evidence.bundle.bundle_sha256,
        "incoming_admission_sha256": evidence.admission.admission_sha256,
        "incoming_metadata_sha256": _sha256(metadata.model_dump(mode="json")),
        "incoming_source_receipt_sha256": evidence.source.source_receipt_sha256,
        "incoming_projection_sha256": evidence.projection.projection_sha256,
        "incoming_inference_sha256": evidence.inference.inference_sha256,
        "incoming_claim_evidence_sha256": _incoming_evidence_sha256(evidence),
        "incoming_document_id": document.document_id,
        "incoming_document_version_id": document.document_version_id,
        "incoming_document_family": document.document_family,
        "incoming_version_label": document.version_label,
        "incoming_title_sha256": hashlib.sha256(metadata.title.encode("utf-8")).hexdigest(),
        "incoming_operator_intent_sha256": hashlib.sha256(
            metadata.operator_intent.encode("utf-8")
        ).hexdigest(),
        "domain": metadata.domain,
        "source_type": metadata.source_type,
        "declared_effective_from": metadata.declared_effective_from,
        "declared_effective_to": metadata.declared_effective_to,
        "role": metadata.role,
        "authority": metadata.authority,
        "analysis_as_of": metadata.declared_effective_from,
        "analysis_revision": 2,
        "analysis_aggregate_sha256": aggregate_sha256(analysis),
        "changed_claim_revision_ids": tuple(sorted(item.claim_revision_id for item in claims)),
        "analysis_operation_seed_sha256": operation_seed_sha256,
        "analysis_operation_id": "generic-analysis-v2:" + "0" * 64,
    }
    provisional = GenericAnalysisBootstrapBindingV2.model_construct(
        binding_id=f"generic-analysis-bootstrap-v2:{'0' * 64}",
        binding_sha256="0" * 64,
        canonical_input_sha256="0" * 64,
        **values,
    )
    with_operation = provisional.model_copy(
        update={
            "analysis_operation_id": (
                "generic-analysis-v2:" + _sha256(provisional._operation_identity_payload())
            )
        }
    )
    with_input = with_operation.model_copy(
        update={"canonical_input_sha256": _sha256(with_operation._canonical_input_payload())}
    )
    digest = _sha256(with_input.model_dump(mode="json", exclude={"binding_id", "binding_sha256"}))
    return GenericAnalysisBootstrapBindingV2.model_validate(
        with_input.model_copy(
            update={
                "binding_id": f"generic-analysis-bootstrap-v2:{digest}",
                "binding_sha256": digest,
            }
        ).model_dump(mode="python")
    )


def _capability_seal(
    *,
    binding: GenericAnalysisBootstrapBindingV2,
    repository_id: str,
    workspace_capability: VerifiedWorkspaceBootstrapCapability,
    evidence_capability: RepositoryVerifiedGenericEvidenceV2,
) -> str:
    return hmac.new(
        _CAPABILITY_SECRET,
        canonical_json_bytes(
            {
                "namespace": "mastervault.verified-generic-analysis-capability.v2",
                "binding": binding.model_dump(mode="json"),
                "repository_id": repository_id,
                "workspace_capability_object_id": id(workspace_capability),
                "evidence_capability_object_id": id(evidence_capability),
            }
        ),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True, eq=False)
class VerifiedGenericAnalysisBootstrapCapabilityV2:
    """Live proof retaining the workspace and generic repository authorities."""

    binding: GenericAnalysisBootstrapBindingV2
    _repository: FilesystemGenericIncomingRepositoryV2 = field(repr=False, compare=False)
    _workspace_capability: VerifiedWorkspaceBootstrapCapability = field(repr=False, compare=False)
    _evidence_capability: RepositoryVerifiedGenericEvidenceV2 = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("generic analysis capabilities are service-created only")

    def __reduce__(self) -> Any:
        raise TypeError("generic analysis capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("generic analysis capabilities are process-local")


def verify_generic_analysis_snapshot_v2(
    capability: VerifiedGenericAnalysisBootstrapCapabilityV2,
    snapshot: ChangeControlSnapshot,
) -> GenericAnalysisBootstrapBindingV2:
    """Freshly reopen both parent authorities and authenticate revision 2."""

    if (
        type(capability) is not VerifiedGenericAnalysisBootstrapCapabilityV2
        or capability._token is not _CAPABILITY_TOKEN
    ):
        raise GenericAnalysisIntegrityError("generic analysis capability is invalid")
    try:
        binding = GenericAnalysisBootstrapBindingV2.model_validate_json(
            canonical_json_bytes(capability.binding.model_dump(mode="json"))
        )
        state = verify_workspace_bootstrap_capability(capability._workspace_capability)
        evidence = capability._repository.resolve_verified_evidence(capability._evidence_capability)
    except (TypeError, ValueError) as exc:
        raise GenericAnalysisIntegrityError(
            "generic analysis parent authority cannot be freshly verified"
        ) from exc
    expected_seal = _capability_seal(
        binding=binding,
        repository_id=capability._repository.repository_id,
        workspace_capability=capability._workspace_capability,
        evidence_capability=capability._evidence_capability,
    )
    inventory_receipt, readiness = state.require_complete()
    if not hmac.compare_digest(capability._seal, expected_seal) or not (
        binding == capability.binding
        and binding.workspace_bootstrap_id == state.intent.bootstrap_id
        and binding.workspace_intent_sha256 == state.intent.intent_sha256
        and binding.workspace_inventory_id == state.inventory.inventory_id
        and binding.workspace_inventory_sha256 == state.inventory.inventory_sha256
        and binding.workspace_inventory_receipt_id == inventory_receipt.receipt_id
        and binding.workspace_inventory_receipt_sha256 == inventory_receipt.receipt_sha256
        and binding.workspace_readiness_receipt_id == readiness.receipt_id
        and binding.workspace_readiness_receipt_sha256 == readiness.receipt_sha256
        and binding.incoming_bundle_id == evidence.bundle.bundle_id
        and binding.incoming_bundle_sha256 == evidence.bundle.bundle_sha256
        and binding.incoming_claim_evidence_sha256 == _incoming_evidence_sha256(evidence)
        and snapshot.aggregate.aggregate_id == binding.aggregate_id
        and snapshot.revision == binding.analysis_revision
        and snapshot.aggregate_sha256 == binding.analysis_aggregate_sha256
        and aggregate_sha256(snapshot.aggregate) == snapshot.aggregate_sha256
    ):
        raise GenericAnalysisIntegrityError(
            "generic analysis capability differs from its exact revision-2 snapshot"
        )
    return binding


def reopen_generic_analysis_capability_v2(
    *,
    binding: GenericAnalysisBootstrapBindingV2,
    analysis_snapshot: ChangeControlSnapshot,
    repository: FilesystemGenericIncomingRepositoryV2,
    workspace_capability: VerifiedWorkspaceBootstrapCapability,
    evidence_capability: RepositoryVerifiedGenericEvidenceV2,
) -> VerifiedGenericAnalysisBootstrapCapabilityV2:
    """Remint process-local authority from durable revision-2 temporal evidence.

    This performs no store read or write.  Callers must supply the exact analysis
    snapshot embedded in reopened temporal-analysis evidence, even when the live
    aggregate has since advanced through proposal and review.
    """

    if type(binding) is not GenericAnalysisBootstrapBindingV2:
        raise GenericAnalysisIntegrityError("generic analysis binding type is invalid")
    if type(analysis_snapshot) is not ChangeControlSnapshot:
        raise GenericAnalysisIntegrityError("generic analysis snapshot type is invalid")
    if type(repository) is not FilesystemGenericIncomingRepositoryV2:
        raise GenericAnalysisIntegrityError("generic analysis repository type is invalid")
    if type(workspace_capability) is not VerifiedWorkspaceBootstrapCapability:
        raise GenericAnalysisIntegrityError("workspace bootstrap capability type is invalid")
    if type(evidence_capability) is not RepositoryVerifiedGenericEvidenceV2:
        raise GenericAnalysisIntegrityError("generic evidence capability type is invalid")
    try:
        exact_binding = GenericAnalysisBootstrapBindingV2.model_validate_json(
            canonical_json_bytes(binding.model_dump(mode="json"))
        )
        exact_snapshot = ChangeControlSnapshot(
            aggregate=ChangeControlAggregate.model_validate_json(
                canonical_json_bytes(analysis_snapshot.aggregate.model_dump(mode="json"))
            ),
            revision=analysis_snapshot.revision,
            aggregate_sha256=analysis_snapshot.aggregate_sha256,
        )
        state = verify_workspace_bootstrap_capability(workspace_capability)
        inventory_receipt, _readiness = state.require_complete()
        evidence = repository.resolve_verified_evidence(evidence_capability)
        document, claims = _generic_projection(evidence)
    except (TypeError, ValueError) as exc:
        raise GenericAnalysisIntegrityError(
            "generic analysis durable parent authority cannot be reopened"
        ) from exc
    if (
        exact_binding != binding
        or exact_snapshot != analysis_snapshot
        or not (
            exact_snapshot.revision == 2
            and exact_snapshot.aggregate.aggregate_id == binding.aggregate_id
            and exact_snapshot.aggregate_sha256 == binding.analysis_aggregate_sha256
            and aggregate_sha256(exact_snapshot.aggregate) == exact_snapshot.aggregate_sha256
        )
    ):
        raise GenericAnalysisIntegrityError(
            "generic analysis durable evidence is not the exact revision-2 snapshot"
        )
    analysis_documents = exact_snapshot.aggregate.documents.documents
    analysis_claims = exact_snapshot.aggregate.claims.revisions
    matching_documents = tuple(
        item
        for item in analysis_documents
        if item.document_version_id == document.document_version_id
    )
    matching_claims = tuple(
        item
        for item in analysis_claims
        if item.document.document_version_id == document.document_version_id
    )
    if matching_documents != (document,) or matching_claims != claims:
        raise GenericAnalysisIntegrityError(
            "generic analysis projection differs from durable revision-2 evidence"
        )
    prechange_aggregate = ChangeControlAggregate.create(
        aggregate_id=exact_snapshot.aggregate.aggregate_id,
        documents=DocumentVersionRegistry.create(
            tuple(item for item in analysis_documents if item != document)
        ),
        claims=ClaimRevisionRegistry.create(
            tuple(item for item in analysis_claims if item.document != document)
        ),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    prechange = ChangeControlSnapshot(
        aggregate=prechange_aggregate,
        revision=1,
        aggregate_sha256=aggregate_sha256(prechange_aggregate),
    )
    reproduced_analysis = _empty_graph_projection(
        prechange.aggregate,
        document=document,
        claims=claims,
    )
    reproduced_binding = _create_binding(
        workspace_capability=workspace_capability,
        evidence=evidence,
        prechange=prechange,
        analysis=reproduced_analysis,
        document=document,
        claims=claims,
        operation_seed_sha256=binding.analysis_operation_seed_sha256,
    )
    if not (
        prechange.aggregate_sha256 == inventory_receipt.aggregate_sha256
        and prechange.aggregate_sha256 == binding.prechange_aggregate_sha256
        and reproduced_analysis == exact_snapshot.aggregate
        and reproduced_binding == binding
    ):
        raise GenericAnalysisIntegrityError(
            "generic analysis binding cannot be reproduced from durable evidence"
        )
    capability = VerifiedGenericAnalysisBootstrapCapabilityV2(
        binding=binding,
        _repository=repository,
        _workspace_capability=workspace_capability,
        _evidence_capability=evidence_capability,
        _token=_CAPABILITY_TOKEN,
        _seal=_capability_seal(
            binding=binding,
            repository_id=repository.repository_id,
            workspace_capability=workspace_capability,
            evidence_capability=evidence_capability,
        ),
    )
    verify_generic_analysis_snapshot_v2(capability, exact_snapshot)
    return capability


def _validate_prechange_inventory(
    snapshot: ChangeControlSnapshot,
    notes: tuple[CanonicalSourceNoteSnapshot, ...],
) -> None:
    if type(notes) is not tuple:
        raise GenericAnalysisIntegrityError("workspace SourceNote inventory must be a tuple")
    documents = {item.document_version_id: item for item in snapshot.aggregate.documents.documents}
    by_document = {item.document.document_version_id: item for item in notes}
    if len(by_document) != len(notes) or set(by_document) != set(documents):
        raise GenericAnalysisIntegrityError(
            "workspace SourceNote inventory does not exactly cover generation zero"
        )
    source_claims = {
        (claim.document.document_version_id, claim.source.source_claim_id)
        for claim in snapshot.aggregate.claims.revisions
    }
    for document_id, note in by_document.items():
        if note.document != documents[document_id]:
            raise GenericAnalysisIntegrityError(
                "workspace SourceNote metadata differs from generation zero"
            )
        for claim in snapshot.aggregate.claims.revisions:
            if claim.document.document_version_id == document_id and not (
                claim.source.source_note_path == note.source_note_path
                and claim.source.source_note_sha256 == note.source_note_sha256
            ):
                raise GenericAnalysisIntegrityError(
                    "workspace claim source differs from exact SourceNote bytes"
                )
    if len(source_claims) != len(snapshot.aggregate.claims.revisions):
        raise GenericAnalysisIntegrityError("workspace claim source identities are duplicated")


@dataclass(frozen=True, eq=False)
class GenericVerifiedSourceNoteInventoryCapabilityV2:
    """In-memory exact inventory for dependency and governing-source resolution."""

    _inventory: SourceNoteInventory = field(repr=False)
    _analysis_capability: VerifiedGenericAnalysisBootstrapCapabilityV2 = field(
        repr=False, compare=False
    )
    _seal: str = field(repr=False, compare=False)

    def verify(self, *, snapshot: ChangeControlSnapshot) -> SourceNoteInventory:
        binding = verify_generic_analysis_snapshot_v2(self._analysis_capability, snapshot)
        inventory = SourceNoteInventory.model_validate_json(
            canonical_json_bytes(self._inventory.model_dump(mode="json"))
        )
        expected = hmac.new(
            _CAPABILITY_SECRET,
            canonical_json_bytes(
                {
                    "namespace": "mastervault.generic-source-note-inventory-capability.v2",
                    "binding_sha256": binding.binding_sha256,
                    "inventory": inventory.model_dump(mode="json"),
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self._seal, expected) or not (
            inventory == self._inventory
            and inventory.aggregate_id == snapshot.aggregate.aggregate_id
            and inventory.snapshot_revision == snapshot.revision
            and inventory.aggregate_sha256 == snapshot.aggregate_sha256
        ):
            raise GenericAnalysisIntegrityError("generic SourceNote inventory was altered")
        return inventory

    def __reduce__(self) -> Any:
        raise TypeError("generic SourceNote capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("generic SourceNote capabilities are process-local")


@dataclass(frozen=True)
class GenericSourceNoteInventoryResolverV2:
    """Typed resolver adapter consumed by temporal review reconstruction."""

    verified_bootstrap: VerifiedGenericAnalysisBootstrapCapabilityV2
    workspace_source_notes: tuple[CanonicalSourceNoteSnapshot, ...]

    def resolve_source_note_inventory(
        self, *, snapshot: ChangeControlSnapshot
    ) -> GenericVerifiedSourceNoteInventoryCapabilityV2:
        return resolve_generic_source_note_inventory_v2(
            analysis_capability=self.verified_bootstrap,
            snapshot=snapshot,
            workspace_source_notes=self.workspace_source_notes,
        )


def resolve_generic_source_note_inventory_v2(
    *,
    analysis_capability: VerifiedGenericAnalysisBootstrapCapabilityV2,
    snapshot: ChangeControlSnapshot,
    workspace_source_notes: tuple[CanonicalSourceNoteSnapshot, ...],
) -> GenericVerifiedSourceNoteInventoryCapabilityV2:
    """Reconstruct complete active analysis notes without seed fixtures or root walking."""

    binding = verify_generic_analysis_snapshot_v2(analysis_capability, snapshot)
    evidence = analysis_capability._repository.resolve_verified_evidence(
        analysis_capability._evidence_capability
    )
    prechange = ChangeControlSnapshot(
        aggregate=ChangeControlAggregate.create(
            aggregate_id=snapshot.aggregate.aggregate_id,
            documents=DocumentVersionRegistry.create(
                tuple(
                    item
                    for item in snapshot.aggregate.documents.documents
                    if item.document_version_id != binding.incoming_document_version_id
                )
            ),
            claims=ClaimRevisionRegistry.create(
                tuple(
                    item
                    for item in snapshot.aggregate.claims.revisions
                    if item.claim_revision_id not in set(binding.changed_claim_revision_ids)
                )
            ),
            relation_graph=RelationGraph.create(()),
            dependencies=DependencyRegistry.create(()),
            document_replacements=DocumentReplacementSet.create(()),
            temporal_constraints=TemporalConstraintSet.create(()),
        ),
        revision=1,
        aggregate_sha256=binding.prechange_aggregate_sha256,
    )
    if aggregate_sha256(prechange.aggregate) != prechange.aggregate_sha256:
        raise GenericAnalysisIntegrityError(
            "revision-2 snapshot does not reconstruct the exact generation-zero aggregate"
        )
    _validate_prechange_inventory(prechange, workspace_source_notes)
    try:
        text = evidence.source_note.decode("utf-8")
    except UnicodeDecodeError as exc:  # already checked by repository, defense in depth
        raise GenericAnalysisIntegrityError("generic SourceNote is not UTF-8") from exc
    _frontmatter, body, present = split_frontmatter(text)
    if not present:
        raise GenericAnalysisIntegrityError("generic SourceNote has no body boundary")
    incoming_document = snapshot.aggregate.documents.get(binding.incoming_document_version_id)
    incoming_note = CanonicalSourceNoteSnapshot.create(
        document=incoming_document,
        source_note_path=evidence.source.source_note_locator,
        source_note_utf8=text,
        body_start_char=len(text) - len(body),
    )
    notes = tuple(
        sorted(
            (*workspace_source_notes, incoming_note),
            key=lambda item: item.document.document_version_id,
        )
    )
    inventory = SourceNoteInventory.create(snapshot=snapshot, notes=notes)
    seal = hmac.new(
        _CAPABILITY_SECRET,
        canonical_json_bytes(
            {
                "namespace": "mastervault.generic-source-note-inventory-capability.v2",
                "binding_sha256": binding.binding_sha256,
                "inventory": inventory.model_dump(mode="json"),
            }
        ),
        hashlib.sha256,
    ).hexdigest()
    capability = GenericVerifiedSourceNoteInventoryCapabilityV2(
        _inventory=inventory,
        _analysis_capability=analysis_capability,
        _seal=seal,
    )
    capability.verify(snapshot=snapshot)
    return capability


@dataclass(frozen=True)
class GenericAnalysisStartResultV2:
    binding: GenericAnalysisBootstrapBindingV2
    verification_capability: VerifiedGenericAnalysisBootstrapCapabilityV2
    inventory_capability: GenericVerifiedSourceNoteInventoryCapabilityV2
    analysis_commit: ChangeControlCommit
    snapshot: ChangeControlSnapshot


def start_generic_analysis_v2(
    *,
    store: SqliteChangeControlStore,
    repository: FilesystemGenericIncomingRepositoryV2,
    workspace_capability: VerifiedWorkspaceBootstrapCapability,
    evidence_capability: RepositoryVerifiedGenericEvidenceV2,
    workspace_source_notes: tuple[CanonicalSourceNoteSnapshot, ...],
    analysis_operation_id: str,
) -> GenericAnalysisStartResultV2:
    """CAS the exact workspace aggregate from revision 1 to revision 2 once."""

    state = verify_workspace_bootstrap_capability(workspace_capability)
    inventory_receipt, _readiness = state.require_complete()
    live = store.load(state.intent.aggregate_id)
    if live is None:
        raise GenericAnalysisIntegrityError("workspace generation-zero aggregate is missing")
    if live.revision > 2:
        raise GenericAnalysisStaleError(
            "generic analysis accepts exactly one generation-zero incoming event"
        )
    if live.revision not in {1, 2}:
        raise GenericAnalysisIntegrityError("workspace aggregate revision is not admissible")
    if live.revision == 1 and not (
        live.aggregate.aggregate_id == state.intent.aggregate_id
        and inventory_receipt.aggregate_id == live.aggregate.aggregate_id
        and inventory_receipt.aggregate_revision == 1
        and inventory_receipt.aggregate_sha256 == live.aggregate_sha256
        and aggregate_sha256(live.aggregate) == live.aggregate_sha256
    ):
        raise GenericAnalysisIntegrityError(
            "workspace aggregate differs from exact bootstrap revision 1"
        )
    store.get_operation_commit(analysis_operation_id)
    operation_seed_sha256 = hashlib.sha256(analysis_operation_id.encode("ascii")).hexdigest()
    evidence = repository.resolve_verified_evidence(evidence_capability)
    document, claims = _generic_projection(evidence)
    if live.revision == 1:
        prechange = live
    else:
        prechange_aggregate = ChangeControlAggregate.create(
            aggregate_id=live.aggregate.aggregate_id,
            documents=DocumentVersionRegistry.create(
                tuple(
                    item
                    for item in live.aggregate.documents.documents
                    if item.document_version_id != document.document_version_id
                )
            ),
            claims=ClaimRevisionRegistry.create(
                tuple(
                    item
                    for item in live.aggregate.claims.revisions
                    if item.document.document_version_id != document.document_version_id
                )
            ),
            relation_graph=RelationGraph.create(()),
            dependencies=DependencyRegistry.create(()),
            document_replacements=DocumentReplacementSet.create(()),
            temporal_constraints=TemporalConstraintSet.create(()),
        )
        prechange = ChangeControlSnapshot(
            aggregate=prechange_aggregate,
            revision=1,
            aggregate_sha256=aggregate_sha256(prechange_aggregate),
        )
    if not (
        prechange.aggregate_sha256 == inventory_receipt.aggregate_sha256
        and prechange.aggregate.aggregate_id == inventory_receipt.aggregate_id
    ):
        raise GenericAnalysisIntegrityError(
            "generic retry does not reconstruct the workspace generation-zero aggregate"
        )
    _validate_prechange_inventory(prechange, workspace_source_notes)
    analysis = _empty_graph_projection(prechange.aggregate, document=document, claims=claims)
    binding = _create_binding(
        workspace_capability=workspace_capability,
        evidence=evidence,
        prechange=prechange,
        analysis=analysis,
        document=document,
        claims=claims,
        operation_seed_sha256=operation_seed_sha256,
    )
    existing_operation = store.get_operation_commit(binding.analysis_operation_id)
    if live.revision == 2 and existing_operation is None:
        raise GenericAnalysisStaleError(
            "generic analysis accepts exactly one generation-zero incoming event"
        )
    capability = VerifiedGenericAnalysisBootstrapCapabilityV2(
        binding=binding,
        _repository=repository,
        _workspace_capability=workspace_capability,
        _evidence_capability=evidence_capability,
        _token=_CAPABILITY_TOKEN,
        _seal=_capability_seal(
            binding=binding,
            repository_id=repository.repository_id,
            workspace_capability=workspace_capability,
            evidence_capability=evidence_capability,
        ),
    )
    commit = store.compare_and_swap(
        analysis,
        expected_revision=1,
        operation_id=binding.analysis_operation_id,
    )
    snapshot = store.load(state.intent.aggregate_id)
    if snapshot is None:
        raise GenericAnalysisIntegrityError("generic analysis commit disappeared")
    verify_generic_analysis_snapshot_v2(capability, snapshot)
    if not (commit.revision == 2 and commit.aggregate_sha256 == binding.analysis_aggregate_sha256):
        raise GenericAnalysisIntegrityError("generic analysis commit differs from binding")
    inventory_capability = resolve_generic_source_note_inventory_v2(
        analysis_capability=capability,
        snapshot=snapshot,
        workspace_source_notes=workspace_source_notes,
    )
    return GenericAnalysisStartResultV2(
        binding=binding,
        verification_capability=capability,
        inventory_capability=inventory_capability,
        analysis_commit=commit,
        snapshot=snapshot,
    )


__all__ = [
    "GenericAnalysisIntegrityError",
    "GenericAnalysisStaleError",
    "GenericAnalysisStartResultV2",
    "GenericSourceNoteInventoryResolverV2",
    "GenericVerifiedSourceNoteInventoryCapabilityV2",
    "VerifiedGenericAnalysisBootstrapCapabilityV2",
    "resolve_generic_source_note_inventory_v2",
    "reopen_generic_analysis_capability_v2",
    "start_generic_analysis_v2",
    "verify_generic_analysis_snapshot_v2",
]
