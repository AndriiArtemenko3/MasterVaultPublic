"""Authoritative SQLite persistence for managed review and activation.

This module deliberately owns no filesystem roots and no approved model registry.
Callers inject a repository resolver which must reopen typed artifacts and resolve
the reviewed inference contract.  The store repeats byte/hash checks before it
accepts or replays authority-bearing records.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from mastervault.change_control.bootstrap import (
    VerifiedAnalysisBootstrapCapability,
    verify_generation_zero_authority,
)
from mastervault.change_control.managed_generation import (
    ManagedActivationCommand,
    ManagedActivationIntentRecord,
    ManagedGenerationActivationReceipt,
    ManagedIndexReadinessReceipt,
    ManagedPublicationEvent,
    derive_managed_generation_projection,
    publication_set_sha256,
)
from mastervault.change_control.managed_generation_repository import (
    RepositoryVerifiedManagedGenerationEffects,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ContentAddressedInferenceReceipt,
    GenerationZeroManifestBinding,
    GenerationZeroOriginBasis,
    GroundedArtifactCitation,
    InferenceExecutionMode,
    ManagedAnalysisSetBinding,
    ManagedArtifactRef,
    ManagedGenerationManifestBinding,
    ManagedGenerationManifestBindingV2,
    ManagedGoverningSourceAdoptionBinding,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedInferenceContractBinding,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDecisionReceipt,
    ManagedRevisionDecisionRecord,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewRequestReceipt,
    ManagedRevisionReviewRequestRecord,
    ManagedRevisionReviewView,
    ManagedRunBindingV2,
    NoChangeImpactCard,
    PatchReconstructionAttestation,
    SourceNoteProjectionBinding,
    WorkspaceGenerationZeroManifestBinding,
    WorkspaceGenerationZeroOriginBasis,
)
from mastervault.change_control.models import (
    TemporalState,
    canonical_json_bytes,
    resolve_document_temporality,
)
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunRecord,
    OperatorRunView,
)
from mastervault.change_control.store import (
    ChangeControlConflictError,
    ChangeControlCorruptionError,
    ChangeControlIdempotencyError,
    ChangeControlReviewAlreadyDecidedError,
    ChangeControlReviewMissingError,
    ChangeControlReviewStaleError,
    SqliteChangeControlStore,
    _now,
    _require_canonical_utc,
    _require_contiguous,
    _require_operation_id,
)
from mastervault.change_control.workspace_bootstrap import (
    MAX_WORKSPACE_INVENTORY_PAYLOAD_BYTES_V1,
    LegacyIndexReadinessReceipt,
    VerifiedWorkspaceBootstrapCapability,
    WorkspaceBootstrapIntent,
    WorkspaceBootstrapInventory,
    WorkspaceBootstrapState,
    WorkspaceInventoryReceipt,
)
from mastervault.storage.base import SCHEMA_VERSION


class ManagedReviewRepositoryResolver(Protocol):
    """Repository authority required before a managed record is trusted.

    Implementations own typed roots, no-follow/open safety, and approved-contract
    storage. Returning bytes or a binding is an assertion that those values were
    reopened from the authoritative repository, not reconstructed from a bundle.
    """

    def open_algorithm_manifest(self, binding: ManagedInferenceContractBinding) -> bytes: ...

    def resolve_approved_inference_contract(
        self, binding: ManagedInferenceContractBinding
    ) -> ManagedInferenceContractBinding: ...

    def resolve_impact_analysis_evidence(
        self, binding: ManagedImpactAnalysisEvidenceBinding
    ) -> ManagedImpactAnalysisEvidenceBinding: ...

    def resolve_revision_planning_admission(
        self, binding: ManagedRevisionPlanningAdmissionBinding
    ) -> ManagedRevisionPlanningAdmissionBinding: ...

    def resolve_governing_source_adoption(
        self, binding: ManagedGoverningSourceAdoptionBinding
    ) -> ManagedGoverningSourceAdoptionBinding: ...

    def open_artifact(self, artifact: ManagedArtifactRef) -> bytes: ...

    def verify_patch_reconstruction(
        self,
        plan: ManagedRevisionPlan,
        *,
        base_bytes: bytes,
        result_bytes: bytes,
    ) -> PatchReconstructionAttestation: ...

    def verify_source_note_projection(
        self,
        projection: SourceNoteProjectionBinding,
        *,
        raw_bytes: bytes,
        note_bytes: bytes,
    ) -> SourceNoteProjectionBinding: ...

    def verify_revision_plan_source_note(
        self,
        plan: ManagedRevisionPlan,
        *,
        predecessor_note_bytes: bytes,
        result_raw_bytes: bytes,
        proposed_note_bytes: bytes,
    ) -> SourceNoteProjectionBinding: ...

    def resolve_reviewed_generation_source(
        self, binding: ManagedGoverningSourceAdoptionBinding
    ) -> Any: ...


class ManagedReviewAuthorityError(ChangeControlConflictError):
    """Repository evidence does not authorize the proposed managed review."""


class ManagedReviewStaleError(ChangeControlReviewStaleError):
    """An undecided managed request no longer binds both live authority heads."""


class ManagedReviewWriteVersionError(ManagedReviewAuthorityError):
    """A legacy run binding was presented to a new authority-bearing write."""


class ManagedRevisionEditDeferredError(ManagedReviewAuthorityError):
    """Managed EDIT remains decodable but is not an authorized PR-A write."""


class ManagedGenerationActivationError(ManagedReviewAuthorityError):
    """Managed generation effects do not authorize an exact authority transition."""


class ManagedGenerationActivationStaleError(ManagedGenerationActivationError):
    """A different managed successor won the exact expected-authority CAS."""


class ManagedRevisionStoreLifecycle(StrEnum):
    OPEN = "open"
    STALE = "stale"
    DECIDED = "decided"


@dataclass(frozen=True)
class ManagedRevisionReviewStoreView:
    """Store-owned lifecycle; unlike the pure view it can truthfully expose stale."""

    request_record: ManagedRevisionReviewRequestRecord
    request_receipt: ManagedRevisionReviewRequestReceipt
    lifecycle: ManagedRevisionStoreLifecycle
    current_head: AggregateHeadBinding
    current_authority: AuthorityRevisionBinding
    decision_record: ManagedRevisionDecisionRecord | None = None
    receipt: ManagedRevisionDecisionReceipt | None = None

    def __post_init__(self) -> None:
        decided = self.decision_record is not None and self.receipt is not None
        if (self.decision_record is None) != (self.receipt is None):
            raise ValueError("managed store view requires decision and receipt together")
        if (self.lifecycle == ManagedRevisionStoreLifecycle.DECIDED) != decided:
            raise ValueError("managed store lifecycle disagrees with decision evidence")
        if self.request_receipt.replayed:
            raise ValueError("stable managed store view must use its initial request receipt")
        if self.receipt is not None and self.receipt.replayed:
            raise ValueError("stable managed store view must use its initial decision receipt")

    @property
    def authoritative_view(self) -> ManagedRevisionReviewView | None:
        if self.lifecycle == ManagedRevisionStoreLifecycle.STALE:
            return None
        return ManagedRevisionReviewView.create(
            request_record=self.request_record,
            request_receipt=self.request_receipt,
            decision_record=self.decision_record,
            receipt=self.receipt,
        )


@dataclass(frozen=True)
class ManagedGenerationActivationState:
    """Exact durable evidence currently recorded for one activation operation."""

    intent: ManagedActivationIntentRecord
    publication_events: tuple[ManagedPublicationEvent, ...]
    index_receipt: ManagedIndexReadinessReceipt | None
    activation_receipt: ManagedGenerationActivationReceipt | None


@dataclass(frozen=True)
class AuthorityVerificationContext:
    """Exactly one trusted generation-zero reconstruction context."""

    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None
    prechange_head: AggregateHeadBinding | None = None
    verified_workspace_bootstrap: VerifiedWorkspaceBootstrapCapability | None = None

    def __post_init__(self) -> None:
        legacy = self.verified_bootstrap is not None or self.prechange_head is not None
        workspace = self.verified_workspace_bootstrap is not None
        if legacy == workspace:
            raise TypeError("exactly one legacy or workspace bootstrap context is required")
        if legacy and (
            self.verified_bootstrap is None or self.prechange_head is None
        ):
            raise TypeError("legacy authority context requires capability and prechange head")

    @classmethod
    def legacy(
        cls,
        *,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> AuthorityVerificationContext:
        return cls(verified_bootstrap=verified_bootstrap, prechange_head=prechange_head)

    @classmethod
    def workspace(
        cls,
        capability: VerifiedWorkspaceBootstrapCapability,
    ) -> AuthorityVerificationContext:
        return cls(verified_workspace_bootstrap=capability)


def _authority_context(
    *,
    authority_context: AuthorityVerificationContext | None,
    verified_bootstrap: VerifiedAnalysisBootstrapCapability | None,
    prechange_head: AggregateHeadBinding | None,
) -> AuthorityVerificationContext:
    if authority_context is not None:
        if verified_bootstrap is not None or prechange_head is not None:
            raise TypeError(
                "authority_context cannot be mixed with legacy bootstrap arguments"
            )
        return authority_context
    if verified_bootstrap is None or prechange_head is None:
        raise TypeError(
            "either authority_context or the complete legacy bootstrap pair is required"
        )
    return AuthorityVerificationContext.legacy(
        verified_bootstrap=verified_bootstrap,
        prechange_head=prechange_head,
    )


def _canonical_model_json(model: BaseModel) -> str:
    return canonical_json_bytes(model.model_dump(mode="json")).decode("utf-8")


def _same_inventory_receipt_inputs(
    left: WorkspaceInventoryReceipt, right: WorkspaceInventoryReceipt
) -> bool:
    excluded = {"receipt_id", "receipt_sha256", "recorded_at"}
    return left.model_dump(mode="json", exclude=excluded) == right.model_dump(
        mode="json", exclude=excluded
    )


def _same_index_readiness_inputs(
    left: LegacyIndexReadinessReceipt, right: LegacyIndexReadinessReceipt
) -> bool:
    excluded = {"receipt_id", "receipt_sha256", "ready_at"}
    return left.model_dump(mode="json", exclude=excluded) == right.model_dump(
        mode="json", exclude=excluded
    )


def _require_v2_managed_review_write(bundle: ManagedRevisionReviewBundle) -> None:
    if type(bundle.run_binding) is not ManagedRunBindingV2:
        raise ManagedReviewWriteVersionError(
            "new managed review writes require an exact v2 admitted run binding"
        )


def _reject_deferred_managed_edits(command: ManagedRevisionDecisionCommand) -> None:
    if any(item.disposition == ManagedRevisionDisposition.EDIT for item in command.items):
        raise ManagedRevisionEditDeferredError(
            "managed review EDIT is deferred until a separately admitted edited plan exists"
        )


def _decode_model[ModelT: BaseModel](
    model: type[ModelT], payload_json: str, *, label: str
) -> ModelT:
    try:
        payload = json.loads(payload_json)
        if canonical_json_bytes(payload).decode("utf-8") != payload_json:
            raise ValueError("JSON is not canonical")
        result = model.model_validate_json(payload_json)
        if _canonical_model_json(result) != payload_json:
            raise ValueError("JSON is not typed-canonical")
        return result
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ChangeControlCorruptionError(f"persisted {label} is invalid") from exc


def _decode_generation_manifest(
    payload_json: str,
) -> ManagedGenerationManifestBinding | ManagedGenerationManifestBindingV2:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ChangeControlCorruptionError(
            "persisted inactive managed generation manifest is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise ChangeControlCorruptionError(
            "persisted inactive managed generation manifest is invalid"
        )
    schema_version = payload.get("schema_version")
    if schema_version == 1:
        return _decode_model(
            ManagedGenerationManifestBinding,
            payload_json,
            label="inactive managed generation manifest",
        )
    if schema_version == 2:
        return _decode_model(
            ManagedGenerationManifestBindingV2,
            payload_json,
            label="inactive managed generation manifest",
        )
    raise ChangeControlCorruptionError(
        "persisted inactive managed generation manifest has unsupported schema"
    )


def _walk_models(value: Any) -> Any:
    if isinstance(value, BaseModel):
        yield value
        for field_name in type(value).model_fields:
            yield from _walk_models(getattr(value, field_name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_models(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_models(item)


def _subject_identity(subject: ManagedRevisionPlan | NoChangeImpactCard) -> tuple[str, str]:
    if isinstance(subject, ManagedRevisionPlan):
        return subject.plan_id, subject.plan_sha256
    return subject.card_id, subject.card_sha256


class SqliteManagedChangeControlStore(SqliteChangeControlStore):
    """Managed review and generation authority in one change-control database."""

    def _operation_owner(self, operation_id: str) -> tuple[str, str] | None:
        return self._global_operation_owner(operation_id)

    def _read_workspace_bootstrap_in_transaction(
        self, bootstrap_id: str
    ) -> WorkspaceBootstrapState | None:
        intent_row = self.conn.execute(
            "SELECT * FROM change_control_workspace_bootstrap_intents WHERE bootstrap_id=?",
            (bootstrap_id,),
        ).fetchone()
        if intent_row is None:
            return None
        intent = _decode_model(
            WorkspaceBootstrapIntent,
            str(intent_row["payload_json"]),
            label="workspace bootstrap intent",
        )
        if not (
            str(intent_row["bootstrap_id"]) == intent.bootstrap_id == bootstrap_id
            and str(intent_row["intent_sha256"]) == intent.intent_sha256
            and str(intent_row["operation_id"]) == intent.operation_id
            and str(intent_row["aggregate_id"]) == intent.aggregate_id
            and str(intent_row["inventory_id"]) == intent.inventory_id
            and str(intent_row["inventory_sha256"]) == intent.inventory_sha256
            and int(intent_row["payload_schema_version"]) == intent.schema_version == 1
        ):
            raise ChangeControlCorruptionError(
                "workspace bootstrap intent columns differ from canonical evidence"
            )
        _require_canonical_utc(str(intent_row["created_at"]))

        inventory_row = self.conn.execute(
            "SELECT * FROM change_control_workspace_inventories WHERE bootstrap_id=?",
            (bootstrap_id,),
        ).fetchone()
        if inventory_row is None:
            raise ChangeControlCorruptionError(
                "workspace bootstrap intent lacks its immutable inventory"
            )
        if (
            len(str(inventory_row["payload_json"]).encode("utf-8"))
            > MAX_WORKSPACE_INVENTORY_PAYLOAD_BYTES_V1
        ):
            raise ChangeControlCorruptionError(
                "workspace bootstrap inventory exceeds its canonical payload limit"
            )
        inventory = _decode_model(
            WorkspaceBootstrapInventory,
            str(inventory_row["payload_json"]),
            label="workspace bootstrap inventory",
        )
        if not (
            str(inventory_row["inventory_id"])
            == inventory.inventory_id
            == intent.inventory_id
            and str(inventory_row["inventory_sha256"])
            == inventory.inventory_sha256
            == intent.inventory_sha256
            and str(inventory_row["bootstrap_id"]) == bootstrap_id
            and int(inventory_row["payload_schema_version"])
            == inventory.schema_version
            == 1
        ):
            raise ChangeControlCorruptionError(
                "workspace inventory columns differ from canonical evidence"
            )
        _require_canonical_utc(str(inventory_row["stored_at"]))

        inventory_receipt_row = self.conn.execute(
            "SELECT * FROM change_control_workspace_inventory_receipts "
            "WHERE bootstrap_id=?",
            (bootstrap_id,),
        ).fetchone()
        inventory_receipt: WorkspaceInventoryReceipt | None = None
        if inventory_receipt_row is not None:
            inventory_receipt = _decode_model(
                WorkspaceInventoryReceipt,
                str(inventory_receipt_row["payload_json"]),
                label="workspace inventory receipt",
            )
            if not (
                str(inventory_receipt_row["bootstrap_id"])
                == inventory_receipt.bootstrap_id
                == bootstrap_id
                and str(inventory_receipt_row["receipt_id"])
                == inventory_receipt.receipt_id
                and str(inventory_receipt_row["receipt_sha256"])
                == inventory_receipt.receipt_sha256
                and str(inventory_receipt_row["operation_id"])
                == inventory_receipt.operation_id
                and str(inventory_receipt_row["aggregate_operation_id"])
                == inventory_receipt.aggregate_operation_id
                and str(inventory_receipt_row["aggregate_id"])
                == inventory_receipt.aggregate_id
                and int(inventory_receipt_row["aggregate_revision"])
                == inventory_receipt.aggregate_revision
                and str(inventory_receipt_row["aggregate_sha256"])
                == inventory_receipt.aggregate_sha256
                and str(inventory_receipt_row["inventory_id"])
                == inventory_receipt.inventory_id
                and str(inventory_receipt_row["inventory_sha256"])
                == inventory_receipt.inventory_sha256
                and int(inventory_receipt_row["payload_schema_version"])
                == inventory_receipt.schema_version
                == 1
                and _require_canonical_utc(str(inventory_receipt_row["recorded_at"]))
                == inventory_receipt.recorded_at
            ):
                raise ChangeControlCorruptionError(
                    "workspace inventory receipt columns differ from canonical evidence"
                )

        readiness_row = self.conn.execute(
            "SELECT * FROM change_control_legacy_index_readiness_receipts "
            "WHERE bootstrap_id=?",
            (bootstrap_id,),
        ).fetchone()
        readiness: LegacyIndexReadinessReceipt | None = None
        if readiness_row is not None:
            readiness = _decode_model(
                LegacyIndexReadinessReceipt,
                str(readiness_row["payload_json"]),
                label="legacy index readiness receipt",
            )
            if not (
                str(readiness_row["bootstrap_id"]) == readiness.bootstrap_id == bootstrap_id
                and str(readiness_row["receipt_id"]) == readiness.receipt_id
                and str(readiness_row["receipt_sha256"]) == readiness.receipt_sha256
                and str(readiness_row["operation_id"]) == readiness.operation_id
                and str(readiness_row["inventory_receipt_id"])
                == readiness.inventory_receipt_id
                and str(readiness_row["inventory_receipt_sha256"])
                == readiness.inventory_receipt_sha256
                and str(readiness_row["index_logical_fingerprint"])
                == readiness.index_logical_fingerprint
                and str(readiness_row["index_file_sha256"])
                == readiness.index_file_sha256
                and int(readiness_row["index_file_byte_count"])
                == readiness.index_file_byte_count
                and int(readiness_row["index_schema_version"])
                == readiness.index_schema_version
                and str(readiness_row["embedding_model"]) == readiness.embedding_model
                and int(readiness_row["embedding_dimensions"])
                == readiness.embedding_dimensions
                and int(readiness_row["payload_schema_version"])
                == readiness.schema_version
                == 1
                and _require_canonical_utc(str(readiness_row["ready_at"]))
                == readiness.ready_at
            ):
                raise ChangeControlCorruptionError(
                    "legacy index readiness columns differ from canonical evidence"
                )
        try:
            return WorkspaceBootstrapState(
                intent=intent,
                inventory=inventory,
                inventory_receipt=inventory_receipt,
                index_readiness_receipt=readiness,
            )
        except ValueError as exc:
            raise ChangeControlCorruptionError(
                "workspace bootstrap persistence chain is inconsistent"
            ) from exc

    def claim_workspace_bootstrap(
        self,
        *,
        intent: WorkspaceBootstrapIntent,
        inventory: WorkspaceBootstrapInventory,
    ) -> WorkspaceBootstrapState:
        """Claim one inert immutable manifest and its bounded inventory."""

        intent = WorkspaceBootstrapIntent.model_validate_json(
            canonical_json_bytes(intent.model_dump(mode="json"))
        )
        inventory = WorkspaceBootstrapInventory.model_validate_json(
            canonical_json_bytes(inventory.model_dump(mode="json"))
        )
        candidate = WorkspaceBootstrapState(intent=intent, inventory=inventory)
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(intent.operation_id)
            if owner is not None:
                if owner != ("workspace-bootstrap-intent", intent.bootstrap_id):
                    raise ChangeControlIdempotencyError(
                        "workspace-bootstrap operation_id is already owned by another write"
                    )
                existing = self._read_workspace_bootstrap_in_transaction(intent.bootstrap_id)
                if (
                    existing is None
                    or existing.intent != intent
                    or existing.inventory != inventory
                ):
                    raise ChangeControlIdempotencyError(
                        "workspace-bootstrap operation_id was reused for different inputs"
                    )
                self._commit()
                return existing
            if self._read_workspace_bootstrap_in_transaction(intent.bootstrap_id) is not None:
                raise ChangeControlIdempotencyError(
                    "workspace bootstrap already exists under another operation_id"
                )
            inventory_owner = self.conn.execute(
                "SELECT bootstrap_id FROM change_control_workspace_bootstrap_intents "
                "WHERE inventory_id=?",
                (intent.inventory_id,),
            ).fetchone()
            if inventory_owner is not None:
                raise ChangeControlIdempotencyError(
                    "workspace inventory is already claimed under another operation_id"
                )
            created_at = _now()
            self.conn.execute(
                "INSERT INTO change_control_workspace_bootstrap_intents VALUES "
                "(?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    intent.bootstrap_id,
                    intent.intent_sha256,
                    intent.operation_id,
                    intent.aggregate_id,
                    intent.inventory_id,
                    intent.inventory_sha256,
                    _canonical_model_json(intent),
                    created_at,
                ),
            )
            self.conn.execute(
                "INSERT INTO change_control_workspace_inventories VALUES "
                "(?, ?, ?, 1, ?, ?)",
                (
                    inventory.inventory_id,
                    inventory.inventory_sha256,
                    intent.bootstrap_id,
                    _canonical_model_json(inventory),
                    created_at,
                ),
            )
            result = self._read_workspace_bootstrap_in_transaction(intent.bootstrap_id)
            assert result == candidate
            self._assert_foreign_keys()
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def record_workspace_inventory(
        self, receipt: WorkspaceInventoryReceipt
    ) -> WorkspaceBootstrapState:
        """Record exact verified workspace bytes and their temporal aggregate."""

        receipt = WorkspaceInventoryReceipt.model_validate_json(
            canonical_json_bytes(receipt.model_dump(mode="json"))
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            state = self._read_workspace_bootstrap_in_transaction(receipt.bootstrap_id)
            if state is None:
                raise ChangeControlReviewMissingError("workspace bootstrap intent does not exist")
            owner = self._operation_owner(receipt.operation_id)
            if owner is not None:
                if owner[0] != "workspace-inventory":
                    raise ChangeControlIdempotencyError(
                        "workspace-inventory operation_id is already owned by another write"
                    )
                if state.inventory_receipt is None or not _same_inventory_receipt_inputs(
                    state.inventory_receipt, receipt
                ):
                    raise ChangeControlIdempotencyError(
                        "workspace-inventory operation_id was reused for different inputs"
                    )
                self._commit()
                return state
            if state.inventory_receipt is not None:
                raise ChangeControlIdempotencyError(
                    "workspace inventory receipt already exists under another operation_id"
                )
            expected = WorkspaceBootstrapState(
                intent=state.intent,
                inventory=state.inventory,
                inventory_receipt=receipt,
            )
            aggregate_operation = self.conn.execute(
                "SELECT * FROM change_control_operations WHERE operation_id=?",
                (receipt.aggregate_operation_id,),
            ).fetchone()
            snapshot = self._snapshot_in_transaction(receipt.aggregate_id)
            if aggregate_operation is None or snapshot is None or not (
                str(aggregate_operation["aggregate_id"]) == receipt.aggregate_id
                and aggregate_operation["expected_revision"] is None
                and str(aggregate_operation["aggregate_sha256"])
                == receipt.aggregate_sha256
                and int(aggregate_operation["committed_revision"])
                == receipt.aggregate_revision
                and int(aggregate_operation["changed"]) == 1
                and snapshot.revision == receipt.aggregate_revision
                and snapshot.aggregate_sha256 == receipt.aggregate_sha256
            ):
                raise ManagedReviewAuthorityError(
                    "workspace inventory does not bind an exact create-only aggregate commit"
                )
            declared_documents = {
                item.document.document_version_id: item.document
                for item in state.inventory.managed_source_notes
            }
            persisted_documents = {
                item.document_version_id: item
                for item in snapshot.aggregate.documents.documents
            }
            if persisted_documents != declared_documents:
                raise ManagedReviewAuthorityError(
                    "workspace aggregate documents differ from explicit managed SourceNotes"
                )
            self.conn.execute(
                "INSERT INTO change_control_workspace_inventory_receipts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    receipt.bootstrap_id,
                    receipt.receipt_id,
                    receipt.receipt_sha256,
                    receipt.operation_id,
                    receipt.aggregate_operation_id,
                    receipt.aggregate_id,
                    receipt.aggregate_revision,
                    receipt.aggregate_sha256,
                    receipt.inventory_id,
                    receipt.inventory_sha256,
                    _canonical_model_json(receipt),
                    receipt.recorded_at,
                ),
            )
            result = self._read_workspace_bootstrap_in_transaction(receipt.bootstrap_id)
            assert result == expected
            self._assert_foreign_keys()
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def record_legacy_index_readiness(
        self, receipt: LegacyIndexReadinessReceipt
    ) -> WorkspaceBootstrapState:
        """Record verifier-derived logical and physical legacy-index readiness."""

        receipt = LegacyIndexReadinessReceipt.model_validate_json(
            canonical_json_bytes(receipt.model_dump(mode="json"))
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            state = self._read_workspace_bootstrap_in_transaction(receipt.bootstrap_id)
            if state is None or state.inventory_receipt is None:
                raise ChangeControlReviewMissingError(
                    "legacy-index readiness requires a workspace inventory receipt"
                )
            owner = self._operation_owner(receipt.operation_id)
            if owner is not None:
                if owner[0] != "legacy-index-readiness":
                    raise ChangeControlIdempotencyError(
                        "legacy-index operation_id is already owned by another write"
                    )
                if state.index_readiness_receipt is None or not _same_index_readiness_inputs(
                    state.index_readiness_receipt, receipt
                ):
                    raise ChangeControlIdempotencyError(
                        "legacy-index operation_id was reused for different inputs"
                    )
                self._commit()
                return state
            if state.index_readiness_receipt is not None:
                raise ChangeControlIdempotencyError(
                    "legacy-index readiness already exists under another operation_id"
                )
            expected = WorkspaceBootstrapState(
                intent=state.intent,
                inventory=state.inventory,
                inventory_receipt=state.inventory_receipt,
                index_readiness_receipt=receipt,
            )
            self.conn.execute(
                "INSERT INTO change_control_legacy_index_readiness_receipts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    receipt.bootstrap_id,
                    receipt.receipt_id,
                    receipt.receipt_sha256,
                    receipt.operation_id,
                    receipt.inventory_receipt_id,
                    receipt.inventory_receipt_sha256,
                    receipt.index_logical_fingerprint,
                    receipt.index_file_sha256,
                    receipt.index_file_byte_count,
                    receipt.index_schema_version,
                    receipt.embedding_model,
                    receipt.embedding_dimensions,
                    _canonical_model_json(receipt),
                    receipt.ready_at,
                ),
            )
            result = self._read_workspace_bootstrap_in_transaction(receipt.bootstrap_id)
            assert result == expected
            self._assert_foreign_keys()
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def get_workspace_bootstrap(self, bootstrap_id: str) -> WorkspaceBootstrapState | None:
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            result = self._read_workspace_bootstrap_in_transaction(bootstrap_id)
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _read_operator_run_in_transaction(self, run_id: str) -> OperatorRunView | None:
        row = self.conn.execute(
            "SELECT * FROM change_control_operator_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        record = _decode_model(
            OperatorRunRecord,
            str(row["payload_json"]),
            label="operator run",
        )
        command = record.command
        if not (
            str(row["run_id"]) == command.run_id
            and str(row["run_sha256"]) == command.run_sha256
            and str(row["operation_id"]) == command.operation_id
            and str(row["aggregate_id"]) == command.aggregate_id
            and str(row["base_authority_id"]) == command.base_authority_id
            and int(row["base_authority_revision"]) == command.base_authority_revision
            and str(row["base_active_pointer_sha256"])
            == command.base_active_pointer_sha256
            and int(row["payload_schema_version"])
            == record.schema_version
            == command.schema_version
            == 1
            and _require_canonical_utc(str(row["created_at"])) == record.created_at
        ):
            raise ChangeControlCorruptionError(
                "operator run columns differ from canonical navigation evidence"
            )
        link_rows = self.conn.execute(
            "SELECT * FROM change_control_operator_run_links "
            "WHERE run_id=? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        _require_contiguous(link_rows, "sequence")
        links: list[OperatorRunLinkRecord] = []
        for link_row in link_rows:
            link = _decode_model(
                OperatorRunLinkRecord,
                str(link_row["payload_json"]),
                label="operator run link",
            )
            item = link.command
            if not (
                str(link_row["run_id"]) == item.run_id == run_id
                and int(link_row["sequence"]) == link.sequence
                and str(link_row["link_id"]) == item.link_id
                and str(link_row["link_sha256"]) == item.link_sha256
                and str(link_row["operation_id"]) == item.operation_id
                and str(link_row["link_kind"]) == item.kind.value
                and str(link_row["target_id"]) == item.target_id
                and str(link_row["target_sha256"]) == item.target_sha256
                and int(link_row["payload_schema_version"])
                == link.schema_version
                == item.schema_version
                == 1
                and _require_canonical_utc(str(link_row["recorded_at"]))
                == link.recorded_at
            ):
                raise ChangeControlCorruptionError(
                    "operator link columns differ from canonical navigation evidence"
                )
            links.append(link)
        try:
            return OperatorRunView(record=record, links=tuple(links))
        except ValueError as exc:
            raise ChangeControlCorruptionError(
                "operator run navigation links are inconsistent"
            ) from exc

    def _validate_extension_records(self) -> None:
        bootstrap_rows = self.conn.execute(
            "SELECT bootstrap_id FROM change_control_workspace_bootstrap_intents "
            "ORDER BY bootstrap_id"
        ).fetchall()
        for row in bootstrap_rows:
            if self._read_workspace_bootstrap_in_transaction(str(row["bootstrap_id"])) is None:
                raise ChangeControlCorruptionError(
                    "workspace bootstrap disappeared during validation"
                )
        run_rows = self.conn.execute(
            "SELECT run_id FROM change_control_operator_runs ORDER BY run_id"
        ).fetchall()
        for row in run_rows:
            if self._read_operator_run_in_transaction(str(row["run_id"])) is None:
                raise ChangeControlCorruptionError("operator run disappeared during validation")

    def create_operator_run(self, command: OperatorRunCommand) -> OperatorRunView:
        """Create or replay navigation rooted at an exact committed authority."""

        command = OperatorRunCommand.model_validate_json(
            canonical_json_bytes(command.model_dump(mode="json"))
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(command.operation_id)
            if owner is not None:
                if owner != ("operator-run", command.run_id):
                    raise ChangeControlIdempotencyError(
                        "operator-run operation_id is already owned by another write"
                    )
                existing = self._read_operator_run_in_transaction(command.run_id)
                if existing is None or existing.record.command != command:
                    raise ChangeControlIdempotencyError(
                        "operator-run operation_id was reused for different inputs"
                    )
                self._commit()
                return existing
            existing = self._read_operator_run_in_transaction(command.run_id)
            if existing is not None:
                raise ChangeControlIdempotencyError(
                    "operator run already exists under another operation_id"
                )
            authority = self.conn.execute(
                "SELECT authority_id,authority_revision,active_generation_number,"
                "active_pointer_sha256 FROM change_control_active_generation "
                "WHERE aggregate_id=?",
                (command.aggregate_id,),
            ).fetchone()
            if authority is None or not (
                str(authority["authority_id"]) == command.base_authority_id
                and int(authority["authority_revision"])
                == command.base_authority_revision
                == 0
                and int(authority["active_generation_number"]) == 0
                and str(authority["active_pointer_sha256"])
                == command.base_active_pointer_sha256
            ):
                raise ManagedReviewStaleError(
                    "operator run requires the exact committed generation-zero authority"
                )
            record = OperatorRunRecord(command=command, created_at=_now())
            self.conn.execute(
                "INSERT INTO change_control_operator_runs VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    command.run_id,
                    command.run_sha256,
                    command.operation_id,
                    command.aggregate_id,
                    command.base_authority_id,
                    command.base_authority_revision,
                    command.base_active_pointer_sha256,
                    _canonical_model_json(record),
                    record.created_at,
                ),
            )
            result = self._read_operator_run_in_transaction(command.run_id)
            assert result is not None
            self._assert_foreign_keys()
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _verify_operator_link_target(
        self,
        command: OperatorRunLinkCommand,
        run: OperatorRunView,
    ) -> None:
        table_binding = {
            OperatorRunLinkKind.BOOTSTRAP_INTENT: (
                "change_control_workspace_bootstrap_intents",
                "bootstrap_id",
                "intent_sha256",
            ),
            OperatorRunLinkKind.WORKSPACE_INVENTORY: (
                "change_control_workspace_inventory_receipts",
                "receipt_id",
                "receipt_sha256",
            ),
            OperatorRunLinkKind.LEGACY_INDEX_READINESS: (
                "change_control_legacy_index_readiness_receipts",
                "receipt_id",
                "receipt_sha256",
            ),
        }.get(command.kind)
        if table_binding is not None:
            table, identity_column, sha_column = table_binding
            row = self.conn.execute(
                f"SELECT {sha_column} AS sha FROM {table} WHERE {identity_column}=?",
                (command.target_id,),
            ).fetchone()
            if row is None or str(row["sha"]) != command.target_sha256:
                raise ChangeControlCorruptionError(
                    "operator link target cannot be reopened exactly"
                )
        elif command.kind == OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY:
            base = run.record.command
            authority = self.conn.execute(
                "SELECT authority_id,authority_revision,active_generation_number,"
                "active_pointer_sha256 FROM change_control_active_generation "
                "WHERE aggregate_id=?",
                (base.aggregate_id,),
            ).fetchone()
            if not (
                command.target_id == base.base_authority_id
                and command.target_sha256 == base.base_active_pointer_sha256
                and authority is not None
                and str(authority["authority_id"]) == command.target_id
                and int(authority["authority_revision"])
                == base.base_authority_revision
                == 0
                and int(authority["active_generation_number"]) == 0
                and str(authority["active_pointer_sha256"])
                == command.target_sha256
            ):
                raise ChangeControlCorruptionError(
                    "operator authority link target cannot be reopened exactly"
                )
        else:
            raise ManagedReviewAuthorityError(
                "operator link kind has no authoritative target resolver"
            )

    def record_operator_run_link(
        self, command: OperatorRunLinkCommand
    ) -> OperatorRunView:
        """Append or replay one typed navigation link without granting authority."""

        command = OperatorRunLinkCommand.model_validate_json(
            canonical_json_bytes(command.model_dump(mode="json"))
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            run = self._read_operator_run_in_transaction(command.run_id)
            if run is None:
                raise ChangeControlReviewMissingError("operator run does not exist")
            owner = self._operation_owner(command.operation_id)
            if owner is not None:
                if owner != ("operator-run-link", command.link_id):
                    raise ChangeControlIdempotencyError(
                        "operator-link operation_id is already owned by another write"
                    )
                existing = next(
                    (item for item in run.links if item.command.link_id == command.link_id),
                    None,
                )
                if existing is None or existing.command != command:
                    raise ChangeControlIdempotencyError(
                        "operator-link operation_id was reused for different inputs"
                    )
                self._verify_operator_link_target(existing.command, run)
                self._commit()
                return run
            if any(item.command.kind == command.kind for item in run.links):
                raise ChangeControlIdempotencyError(
                    "operator run link kind is already bound by another operation"
                )
            self._verify_operator_link_target(command, run)
            record = OperatorRunLinkRecord(
                command=command,
                sequence=len(run.links),
                recorded_at=_now(),
            )
            self.conn.execute(
                "INSERT INTO change_control_operator_run_links VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    command.run_id,
                    record.sequence,
                    command.link_id,
                    command.link_sha256,
                    command.operation_id,
                    command.kind.value,
                    command.target_id,
                    command.target_sha256,
                    _canonical_model_json(record),
                    record.recorded_at,
                ),
            )
            result = self._read_operator_run_in_transaction(command.run_id)
            assert result is not None
            self._assert_foreign_keys()
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def get_operator_run(self, run_id: str) -> OperatorRunView | None:
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            result = self._read_operator_run_in_transaction(run_id)
            if result is not None:
                for link in result.links:
                    self._verify_operator_link_target(link.command, result)
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    @staticmethod
    def _resolve_contract_and_artifacts(
        value: BaseModel, resolver: ManagedReviewRepositoryResolver
    ) -> None:
        models = tuple(_walk_models(value))
        contracts = {
            item.contract_binding_id: item
            for item in models
            if isinstance(item, ManagedInferenceContractBinding)
        }
        if len(contracts) != 1:
            raise ManagedReviewAuthorityError(
                "managed evidence must bind exactly one inference contract"
            )
        contract = next(iter(contracts.values()))
        try:
            analyses = {
                item.analysis_set_id: item
                for item in models
                if isinstance(item, ManagedAnalysisSetBinding)
            }
            if len(analyses) != 1:
                raise ValueError("managed evidence must bind exactly one analysis set")
            analysis = next(iter(analyses.values()))
            impact_evidence = analysis.impact_evidence
            if analysis.schema_version != 2 or impact_evidence is None:
                raise ValueError("new managed review authority requires v2 impact evidence")
            resolved_impact = resolver.resolve_impact_analysis_evidence(impact_evidence)
            if resolved_impact != impact_evidence:
                raise ValueError("resolved impact evidence differs from the managed analysis")
            planning_admissions = {
                item.admission_id: item
                for item in models
                if isinstance(item, ManagedRevisionPlanningAdmissionBinding)
            }
            if planning_admissions:
                if len(planning_admissions) != 1:
                    raise ValueError("managed evidence must bind exactly one planning admission")
                planning = next(iter(planning_admissions.values()))
                resolved_planning = resolver.resolve_revision_planning_admission(planning)
                if resolved_planning != planning:
                    raise ValueError(
                        "resolved revision-planning admission differs from the run binding"
                    )
            governing_sources = {
                item.adoption_id: item
                for item in models
                if isinstance(item, ManagedGoverningSourceAdoptionBinding)
            }
            if governing_sources:
                if len(governing_sources) != 1:
                    raise ValueError("managed evidence must bind exactly one governing source")
                governing_source = next(iter(governing_sources.values()))
                resolved_source = resolver.resolve_governing_source_adoption(governing_source)
                if resolved_source != governing_source:
                    raise ValueError(
                        "resolved governing-source adoption differs from the run binding"
                    )
            approved = resolver.resolve_approved_inference_contract(contract)
            if approved != contract:
                raise ValueError("approved inference contract differs from the run binding")
            manifest_bytes = resolver.open_algorithm_manifest(contract)
            if hashlib.sha256(manifest_bytes).hexdigest() != contract.algorithm_manifest_sha256:
                raise ValueError("algorithm manifest bytes differ from the approved contract")

            artifacts: dict[str, ManagedArtifactRef] = {}
            for item in models:
                if not isinstance(item, ManagedArtifactRef):
                    continue
                previous = artifacts.setdefault(item.artifact_id, item)
                if previous != item:
                    raise ValueError("artifact identity resolves to conflicting receipts")
            artifact_bytes: dict[str, bytes] = {}
            for artifact in artifacts.values():
                payload = resolver.open_artifact(artifact)
                if not isinstance(payload, bytes):
                    raise TypeError("artifact resolver must return bytes")
                if len(payload) != artifact.byte_count or (
                    hashlib.sha256(payload).hexdigest() != artifact.sha256
                ):
                    raise ValueError("reopened artifact bytes differ from their receipt")
                artifact_bytes[artifact.artifact_id] = payload

            for item in models:
                if isinstance(item, GroundedArtifactCitation):
                    cited = artifact_bytes.get(item.artifact_id)
                    if cited is None or (
                        cited[item.start_byte : item.end_byte] != item.quote.encode("utf-8")
                    ):
                        raise ValueError("grounded citation does not match reopened bytes")

            projections = {
                item.projection_id: item
                for item in models
                if isinstance(item, SourceNoteProjectionBinding)
            }
            for projection in projections.values():
                verified_projection = resolver.verify_source_note_projection(
                    projection,
                    raw_bytes=artifact_bytes[projection.raw_artifact.artifact_id],
                    note_bytes=artifact_bytes[projection.note_artifact.artifact_id],
                )
                if verified_projection != projection:
                    raise ValueError("SourceNote projection failed authoritative revalidation")

            plans = {item.plan_id: item for item in models if isinstance(item, ManagedRevisionPlan)}
            for plan in plans.values():
                base = artifact_bytes[plan.predecessor_raw.artifact_id]
                result = artifact_bytes[plan.proposed_raw.artifact_id]
                rebuilt = bytearray()
                cursor = 0
                for hunk in plan.hunks:
                    before = hunk.before_text.encode("utf-8")
                    if hunk.start_byte < cursor or base[hunk.start_byte : hunk.end_byte] != before:
                        raise ValueError("managed hunk does not match reopened predecessor bytes")
                    rebuilt.extend(base[cursor : hunk.start_byte])
                    rebuilt.extend(hunk.replacement_text.encode("utf-8"))
                    cursor = hunk.end_byte
                rebuilt.extend(base[cursor:])
                if bytes(rebuilt) != result:
                    raise ValueError("managed hunks do not reconstruct proposed raw bytes")
                if artifact_bytes[plan.validated_output.artifact_id] != canonical_json_bytes(
                    plan._proposal_payload()
                ):
                    raise ValueError("validated plan output differs from its canonical envelope")
                verified_patch = resolver.verify_patch_reconstruction(
                    plan,
                    base_bytes=base,
                    result_bytes=result,
                )
                if verified_patch != plan.patch_attestation:
                    raise ValueError("patch reconstruction attestation was not reproduced")
                if planning_admissions:
                    verified_note = resolver.verify_revision_plan_source_note(
                        plan,
                        predecessor_note_bytes=artifact_bytes[plan.predecessor_note.artifact_id],
                        result_raw_bytes=result,
                        proposed_note_bytes=artifact_bytes[plan.proposed_note.artifact_id],
                    )
                    if verified_note != plan.successor_projection:
                        raise ValueError(
                            "managed successor SourceNote rendering was not reproduced"
                        )

            cards = {item.card_id: item for item in models if isinstance(item, NoChangeImpactCard)}
            for card in cards.values():
                if artifact_bytes[card.validated_output.artifact_id] != canonical_json_bytes(
                    card._output_payload()
                ):
                    raise ValueError(
                        "validated no-change output differs from its canonical envelope"
                    )

            receipts = {
                item.receipt_id: item
                for item in models
                if isinstance(item, ContentAddressedInferenceReceipt)
            }
            for receipt in receipts.values():
                contract.require_receipt(receipt)
                if receipt.mode != InferenceExecutionMode.REPLAY:
                    continue
                replay_artifact = receipt.replay_source_receipt_artifact
                if replay_artifact is None:
                    raise ValueError("replay receipt lacks its source artifact")
                source_payload = artifact_bytes[replay_artifact.artifact_id]
                source = _decode_model(
                    ContentAddressedInferenceReceipt,
                    source_payload.decode("utf-8"),
                    label="replay inference receipt",
                )
                receipt.verify_replay_source(source)
        except ManagedReviewAuthorityError:
            raise
        except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise ManagedReviewAuthorityError(
                "managed contract or artifact authority could not be resolved"
            ) from exc

    def _verify_bootstrap_operations(
        self,
        capability: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> None:
        binding = capability.binding
        receipt = self.conn.execute(
            "SELECT * FROM change_control_operations WHERE operation_id=?",
            (binding.prechange_operation_id,),
        ).fetchone()
        if receipt is None or not (
            str(receipt["aggregate_id"]) == prechange_head.aggregate_id
            and receipt["expected_revision"] is None
            and str(receipt["aggregate_sha256"]) == prechange_head.aggregate_sha256
            and int(receipt["committed_revision"]) == prechange_head.revision == 1
            and int(receipt["changed"]) == 1
        ):
            raise ManagedReviewAuthorityError(
                "generation zero requires the exact historical pre-change operation receipt"
            )
        analysis_receipt = self.conn.execute(
            "SELECT * FROM change_control_operations WHERE operation_id=?",
            (binding.analysis_operation_id,),
        ).fetchone()
        if analysis_receipt is None or not (
            str(analysis_receipt["aggregate_id"]) == binding.aggregate_id
            and int(analysis_receipt["expected_revision"]) == binding.prechange_revision
            and str(analysis_receipt["aggregate_sha256"]) == binding.analysis_aggregate_sha256
            and int(analysis_receipt["committed_revision"]) == binding.analysis_revision == 2
            and int(analysis_receipt["changed"]) == 1
        ):
            raise ManagedReviewAuthorityError(
                "generation zero requires the exact historical analysis operation receipt"
            )

    def initialize_generation_zero(
        self,
        *,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> AuthorityRevisionBinding:
        context = AuthorityVerificationContext.legacy(
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        candidate = AuthorityRevisionBinding.create_generation_zero(
            analysis_bootstrap=verified_bootstrap.binding,
            prechange_head=prechange_head,
        )
        verify_generation_zero_authority(
            authority=candidate,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        operation_id = _require_operation_id(
            f"managed-generation-zero:{candidate.active_pointer_sha256}"
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(operation_id)
            existing_row = self.conn.execute(
                "SELECT * FROM change_control_active_generation WHERE aggregate_id=?",
                (candidate.aggregate_id,),
            ).fetchone()
            if owner is not None:
                if owner != ("generation-zero", candidate.aggregate_id):
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                existing = self._read_active_authority(
                    candidate.aggregate_id,
                    authority_context=context,
                )
                if existing != candidate:
                    raise ChangeControlIdempotencyError(
                        "generation-zero operation_id was reused for different authority"
                    )
                self._commit()
                return existing
            if existing_row is not None:
                existing = self._read_active_authority(
                    candidate.aggregate_id,
                    authority_context=context,
                )
                if existing == candidate:
                    raise ChangeControlIdempotencyError(
                        "generation zero already exists under another operation_id"
                    )
                raise ChangeControlConflictError(
                    "a different active-generation pointer already exists"
                )
            self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
            if not isinstance(candidate.origin_basis, GenerationZeroOriginBasis):
                raise ChangeControlCorruptionError(
                    "generation-zero candidate has an invalid origin kind"
                )
            manifest = candidate.origin_basis.generation_zero_manifest
            created_at = _now()
            self.conn.execute(
                "INSERT INTO change_control_generation_manifests VALUES "
                "(?, ?, ?, ?, ?, 'generation-zero', 0, NULL, 1, ?, ?)",
                (
                    manifest.manifest_id,
                    candidate.aggregate_id,
                    candidate.active_generation.generation_id,
                    candidate.active_generation.generation_number,
                    manifest.manifest_sha256,
                    _canonical_model_json(manifest),
                    created_at,
                ),
            )
            self.conn.execute(
                "INSERT INTO change_control_active_generation VALUES "
                "(?, ?, ?, ?, 'verified-seed-bootstrap', ?, ?, ?, ?, 1, ?, ?)",
                (
                    candidate.aggregate_id,
                    operation_id,
                    candidate.authority_id,
                    candidate.authority_revision,
                    candidate.active_generation.generation_id,
                    candidate.active_generation.generation_number,
                    candidate.active_generation.manifest_sha256,
                    candidate.active_pointer_sha256,
                    _canonical_model_json(candidate),
                    created_at,
                ),
            )
            self._assert_foreign_keys()
            self._commit()
            return candidate
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def initialize_workspace_generation_zero(
        self,
        *,
        verified_workspace_bootstrap: VerifiedWorkspaceBootstrapCapability,
        evidence_guard: Callable[[], None] | None = None,
    ) -> AuthorityRevisionBinding:
        """Initialize generation zero from exact, freshly guarded workspace evidence.

        ``evidence_guard`` is the application-owned cross-resource handoff.  It
        runs while this store holds ``BEGIN IMMEDIATE`` and again after a new
        authority row has been staged but before commit.  A filesystem drift
        failure therefore rolls the authority transaction back instead of
        leaving a pointer based on evidence that changed during the handoff.
        The capability itself also owns a mandatory fresh verifier; an optional
        caller guard can only add a stricter check, never replace that verifier.
        """

        try:
            capability_state = verified_workspace_bootstrap.verify()
            inventory_receipt, index_receipt = capability_state.require_complete()
            candidate = AuthorityRevisionBinding.create_workspace_generation_zero(
                intent=capability_state.intent,
                inventory_receipt=inventory_receipt,
                index_receipt=index_receipt,
            )
        except (TypeError, ValueError) as exc:
            raise ChangeControlCorruptionError(
                "workspace generation zero requires a valid complete verifier capability"
            ) from exc
        operation_id = _require_operation_id(
            f"managed-generation-zero:{candidate.active_pointer_sha256}"
        )
        context = AuthorityVerificationContext.workspace(verified_workspace_bootstrap)

        def verify_current_evidence() -> None:
            try:
                current_state = verified_workspace_bootstrap.verify()
            except (TypeError, ValueError) as exc:
                raise ManagedReviewAuthorityError(
                    "workspace generation-zero evidence cannot be freshly verified"
                ) from exc
            if current_state != capability_state:
                raise ManagedReviewAuthorityError(
                    "workspace generation-zero evidence changed during authority handoff"
                )
            if evidence_guard is not None:
                evidence_guard()

        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            durable_state = self._read_workspace_bootstrap_in_transaction(
                capability_state.intent.bootstrap_id
            )
            if durable_state != capability_state:
                raise ManagedReviewAuthorityError(
                    "workspace capability differs from exact durable bootstrap evidence"
                )
            verify_current_evidence()
            owner = self._operation_owner(operation_id)
            existing_row = self.conn.execute(
                "SELECT * FROM change_control_active_generation WHERE aggregate_id=?",
                (candidate.aggregate_id,),
            ).fetchone()
            if owner is not None:
                if owner != ("generation-zero", candidate.aggregate_id):
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                existing = self._read_active_authority(
                    candidate.aggregate_id,
                    authority_context=context,
                )
                if existing != candidate:
                    raise ChangeControlIdempotencyError(
                        "workspace generation-zero operation_id was reused for different authority"
                    )
                verify_current_evidence()
                self._commit()
                verify_current_evidence()
                return existing
            if existing_row is not None:
                existing = self._read_active_authority(
                    candidate.aggregate_id,
                    authority_context=context,
                )
                if existing == candidate:
                    raise ChangeControlIdempotencyError(
                        "workspace generation zero exists under another operation_id"
                    )
                raise ChangeControlConflictError(
                    "a different active-generation pointer already exists"
                )
            if not isinstance(candidate.origin_basis, WorkspaceGenerationZeroOriginBasis):
                raise ChangeControlCorruptionError(
                    "workspace generation-zero candidate has an invalid origin kind"
                )
            manifest = candidate.origin_basis.generation_zero_manifest
            created_at = _now()
            self.conn.execute(
                "INSERT INTO change_control_generation_manifests VALUES "
                "(?, ?, ?, ?, ?, 'generation-zero', 0, NULL, 1, ?, ?)",
                (
                    manifest.manifest_id,
                    candidate.aggregate_id,
                    candidate.active_generation.generation_id,
                    candidate.active_generation.generation_number,
                    manifest.manifest_sha256,
                    _canonical_model_json(manifest),
                    created_at,
                ),
            )
            self.conn.execute(
                "INSERT INTO change_control_active_generation VALUES "
                "(?, ?, ?, ?, 'verified-workspace-bootstrap', ?, ?, ?, ?, 1, ?, ?)",
                (
                    candidate.aggregate_id,
                    operation_id,
                    candidate.authority_id,
                    candidate.authority_revision,
                    candidate.active_generation.generation_id,
                    candidate.active_generation.generation_number,
                    candidate.active_generation.manifest_sha256,
                    candidate.active_pointer_sha256,
                    _canonical_model_json(candidate),
                    created_at,
                ),
            )
            verify_current_evidence()
            self._assert_foreign_keys()
            self._commit()
            verify_current_evidence()
            return candidate
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def _read_active_authority(
        self,
        aggregate_id: str,
        *,
        authority_context: AuthorityVerificationContext,
    ) -> AuthorityRevisionBinding:
        row = self.conn.execute(
            "SELECT * FROM change_control_active_generation WHERE aggregate_id=?",
            (aggregate_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlReviewMissingError("active generation is not initialized")
        authority = _decode_model(
            AuthorityRevisionBinding,
            str(row["authority_json"]),
            label="active generation authority",
        )
        if not (
            str(row["authority_id"]) == authority.authority_id
            and int(row["authority_revision"]) == authority.authority_revision
            and str(row["origin_kind"]) == authority.origin_basis.origin_kind
            and str(row["active_generation_id"]) == authority.active_generation.generation_id
            and int(row["active_generation_number"])
            == authority.active_generation.generation_number
            and str(row["active_manifest_sha256"]) == authority.active_generation.manifest_sha256
            and str(row["active_pointer_sha256"]) == authority.active_pointer_sha256
            and int(row["authority_schema_version"]) == authority.schema_version == 1
            and aggregate_id == authority.aggregate_id
        ):
            raise ChangeControlCorruptionError(
                "active generation columns differ from canonical authority evidence"
            )
        self._verify_authority_chain(
            authority,
            initialization_operation_id=str(row["initialization_operation_id"]),
            initialized_at=_require_canonical_utc(str(row["initialized_at"])),
            authority_context=authority_context,
        )
        return authority

    def _verify_stored_authority_chain(
        self,
        authority: AuthorityRevisionBinding,
        *,
        authority_context: AuthorityVerificationContext,
    ) -> None:
        """Reopen any bounded historical authority through immutable evidence."""

        row = self.conn.execute(
            "SELECT initialization_operation_id, initialized_at "
            "FROM change_control_active_generation WHERE aggregate_id=?",
            (authority.aggregate_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlCorruptionError(
                "authority chain has no initialized active-generation aggregate"
            )
        self._verify_authority_chain(
            authority,
            initialization_operation_id=str(row["initialization_operation_id"]),
            initialized_at=_require_canonical_utc(str(row["initialized_at"])),
            authority_context=authority_context,
        )

    def _verify_authority_chain(
        self,
        authority: AuthorityRevisionBinding,
        *,
        initialization_operation_id: str,
        initialized_at: str,
        authority_context: AuthorityVerificationContext,
    ) -> None:
        """Verify one authority through at most 32 exact managed successors."""

        aggregate_id = authority.aggregate_id
        current = authority
        seen_authorities: set[str] = set()
        seen_generations: set[str] = set()
        for _depth in range(33):
            if (
                current.authority_id in seen_authorities
                or current.active_generation.generation_id in seen_generations
                or current.aggregate_id != aggregate_id
            ):
                raise ChangeControlCorruptionError(
                    "managed active-authority chain is cyclic or cross-aggregate"
                )
            seen_authorities.add(current.authority_id)
            seen_generations.add(current.active_generation.generation_id)
            if isinstance(
                current.origin_basis,
                (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
            ):
                manifest_row = self.conn.execute(
                    "SELECT * FROM change_control_generation_manifests WHERE generation_id=?",
                    (current.active_generation.generation_id,),
                ).fetchone()
                if manifest_row is None:
                    raise ChangeControlCorruptionError(
                        "generation-zero manifest is absent from authority chain"
                    )
                manifest: (
                    GenerationZeroManifestBinding | WorkspaceGenerationZeroManifestBinding
                )
                if isinstance(current.origin_basis, GenerationZeroOriginBasis):
                    manifest = _decode_model(
                        GenerationZeroManifestBinding,
                        str(manifest_row["payload_json"]),
                        label="generation-zero manifest",
                    )
                else:
                    manifest = _decode_model(
                        WorkspaceGenerationZeroManifestBinding,
                        str(manifest_row["payload_json"]),
                        label="generation-zero manifest",
                    )
                if not (
                    current.authority_revision == 0
                    and current.active_generation.generation_number == 0
                    and current.active_generation.manifest_sha256 == manifest.manifest_sha256
                    and str(manifest_row["manifest_id"]) == manifest.manifest_id
                    and str(manifest_row["aggregate_id"]) == aggregate_id
                    and str(manifest_row["generation_id"])
                    == current.active_generation.generation_id
                    and int(manifest_row["generation_number"]) == 0
                    and str(manifest_row["manifest_sha256"]) == manifest.manifest_sha256
                    and str(manifest_row["manifest_kind"]) == "generation-zero"
                    and int(manifest_row["created_inactive"]) == 0
                    and manifest_row["source_request_id"] is None
                    and int(manifest_row["payload_schema_version"]) == 1
                    and _require_canonical_utc(str(manifest_row["created_at"])) == initialized_at
                ):
                    raise ChangeControlCorruptionError(
                        "generation-zero authority chain evidence is inconsistent"
                    )
                expected_operation_id = _require_operation_id(
                    f"managed-generation-zero:{current.active_pointer_sha256}"
                )
                if initialization_operation_id != expected_operation_id:
                    raise ChangeControlCorruptionError(
                        "generation-zero initialization operation is not deterministic"
                    )
                if isinstance(current.origin_basis, GenerationZeroOriginBasis):
                    verified_bootstrap = authority_context.verified_bootstrap
                    prechange_head = authority_context.prechange_head
                    if verified_bootstrap is None or prechange_head is None:
                        raise ManagedReviewAuthorityError(
                            "seed authority requires the exact legacy bootstrap context"
                        )
                    self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
                    verify_generation_zero_authority(
                        authority=current,
                        verified_bootstrap=verified_bootstrap,
                        prechange_head=prechange_head,
                    )
                else:
                    capability = authority_context.verified_workspace_bootstrap
                    if capability is None:
                        raise ManagedReviewAuthorityError(
                            "workspace authority requires the exact workspace capability"
                        )
                    try:
                        state = capability.verify()
                        inventory_receipt, index_receipt = state.require_complete()
                    except (TypeError, ValueError) as exc:
                        raise ManagedReviewAuthorityError(
                            "workspace bootstrap capability cannot be verified"
                        ) from exc
                    durable = self._read_workspace_bootstrap_in_transaction(
                        state.intent.bootstrap_id
                    )
                    if durable != state:
                        raise ManagedReviewAuthorityError(
                            "workspace capability differs from durable bootstrap evidence"
                        )
                    try:
                        current.verify_workspace_generation_zero_origin(
                            intent=state.intent,
                            inventory_receipt=inventory_receipt,
                            index_receipt=index_receipt,
                        )
                    except ValueError as exc:
                        raise ManagedReviewAuthorityError(
                            "workspace generation zero differs from verified evidence"
                        ) from exc
                return

            origin = current.origin_basis
            decision_row = self.conn.execute(
                "SELECT request_id FROM change_control_managed_review_decisions "
                "WHERE decision_id=? AND record_sha256=?",
                (origin.decision_id, origin.decision_record_sha256),
            ).fetchone()
            if decision_row is None:
                raise ChangeControlCorruptionError(
                    "managed authority origin decision cannot be reopened"
                )
            decision = self._read_decision_record(str(decision_row["request_id"]))
            prior = decision.command.expected_authority
            try:
                current.verify_managed_successor_origin(
                    expected_authority=prior,
                    decision_record=decision,
                )
            except ValueError as exc:
                raise ChangeControlCorruptionError(
                    "managed authority successor does not reproduce from its decision"
                ) from exc
            receipt = self._read_generation_activation_receipt_by_authority(current.authority_id)
            if (
                receipt.activated_authority != current
                or receipt.prior_authority != prior
                or receipt.decision_record_sha256 != decision.record_sha256
            ):
                raise ChangeControlCorruptionError(
                    "managed authority chain activation receipt is inconsistent"
                )
            current = prior
        raise ChangeControlCorruptionError(
            "managed active-authority chain exceeds the fixed 32-successor limit"
        )

    def get_active_generation(
        self,
        aggregate_id: str,
        *,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> AuthorityRevisionBinding:
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            result = self._read_active_authority(
                aggregate_id,
                authority_context=context,
            )
            self._commit()
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _assert_live_review_base(
        self,
        bundle: ManagedRevisionReviewBundle,
        *,
        authority_context: AuthorityVerificationContext,
    ) -> AuthorityRevisionBinding:
        expected_head = bundle.review_base.review_open_head
        snapshot = self._snapshot_in_transaction(expected_head.aggregate_id)
        if snapshot is None or not (
            snapshot.revision == expected_head.revision
            and snapshot.aggregate_sha256 == expected_head.aggregate_sha256
        ):
            raise ManagedReviewStaleError("managed review aggregate head is stale")
        for target in bundle.targets:
            predecessor = snapshot.aggregate.documents.get(target.predecessor.document_version_id)
            if predecessor != target.predecessor:
                raise ManagedReviewAuthorityError(
                    "managed target predecessor is not exact review-open aggregate evidence"
                )
            temporal = resolve_document_temporality(
                target.predecessor,
                snapshot.aggregate.validated_temporal_constraints(),
                as_of=bundle.run_binding.analysis_set.analysis_bootstrap.analysis_as_of,
            )
            if temporal.state != TemporalState.CURRENT:
                raise ManagedReviewAuthorityError(
                    "managed target predecessor is not current at the analysis date"
                )
            aggregate_claims = tuple(
                sorted(
                    (
                        claim
                        for claim in snapshot.aggregate.claims.revisions
                        if claim.document == target.predecessor
                    ),
                    key=lambda claim: claim.claim_revision_id,
                )
            )
            if target.subject.predecessor_projection.projected_claims != aggregate_claims:
                raise ManagedReviewAuthorityError(
                    "managed predecessor projection is not the complete review-open claim set"
                )
        authority = self._read_active_authority(
            expected_head.aggregate_id,
            authority_context=authority_context,
        )
        if authority != bundle.review_base.authority:
            raise ManagedReviewStaleError("managed review active-generation authority is stale")
        return authority

    def _assert_temporal_prerequisite(self, bundle: ManagedRevisionReviewBundle) -> None:
        prerequisite = bundle.temporal_prerequisite
        head = prerequisite.review_open_head
        analysis_head = bundle.run_binding.analysis_head
        rows = self.conn.execute(
            "SELECT request_id FROM change_control_review_decisions "
            "WHERE aggregate_id=? AND decided_revision=? AND decided_aggregate_sha256=?",
            (head.aggregate_id, head.revision, head.aggregate_sha256),
        ).fetchall()
        if len(rows) != 1:
            raise ManagedReviewAuthorityError(
                "managed review requires one exact authoritative temporal decision"
            )
        decision = self._read_review_decision(str(rows[0]["request_id"]))
        if decision is None:
            raise ManagedReviewAuthorityError(
                "managed review temporal decision record cannot be reopened"
            )
        request = self._read_review_request(decision.request_id)
        if request is None:
            raise ManagedReviewAuthorityError(
                "managed review temporal request record cannot be reopened"
            )
        proposal_receipt = self.conn.execute(
            "SELECT * FROM change_control_operations WHERE operation_id=?",
            (bundle.run_binding.operation_id,),
        ).fetchone()
        if not (
            request.aggregate_id == analysis_head.aggregate_id == head.aggregate_id
            and request.base_revision == analysis_head.revision + 1
            and proposal_receipt is not None
            and str(proposal_receipt["aggregate_id"]) == analysis_head.aggregate_id
            and proposal_receipt["expected_revision"] is not None
            and int(proposal_receipt["expected_revision"]) == analysis_head.revision
            and int(proposal_receipt["committed_revision"]) == request.base_revision
            and str(proposal_receipt["aggregate_sha256"]) == request.base_aggregate_sha256
            and int(proposal_receipt["changed"]) == 1
            and decision.decided_revision == request.base_revision + 1 == head.revision
            and decision.decided_aggregate_sha256 == head.aggregate_sha256
        ):
            raise ManagedReviewAuthorityError(
                "managed run operation does not authorize the exact proposal transition "
                "into temporal review"
            )
        digest = hashlib.sha256(canonical_json_bytes(decision.model_dump(mode="json"))).hexdigest()
        if digest != prerequisite.temporal_decision_record_sha256:
            raise ManagedReviewAuthorityError(
                "managed review temporal prerequisite hash is not authoritative"
            )

    def _insert_bundle(self, bundle: ManagedRevisionReviewBundle) -> None:
        head = bundle.review_base.review_open_head
        authority = bundle.review_base.authority
        self.conn.execute(
            "INSERT INTO change_control_managed_review_bundles VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                bundle.bundle_id,
                bundle.bundle_sha256,
                head.aggregate_id,
                head.revision,
                head.aggregate_sha256,
                authority.authority_id,
                authority.authority_revision,
                authority.active_generation.generation_id,
                authority.active_generation.manifest_sha256,
                _canonical_model_json(bundle),
            ),
        )
        rows = []
        for ordinal, target in enumerate(bundle.targets):
            identity, digest = _subject_identity(target.subject)
            rows.append(
                (
                    bundle.bundle_id,
                    ordinal,
                    target.target_id,
                    target.target_key,
                    target.target_sha256,
                    target.subject.kind,
                    identity,
                    digest,
                    1,
                    _canonical_model_json(target),
                )
            )
        self.conn.executemany(
            "INSERT INTO change_control_managed_review_targets VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def _read_bundle(self, bundle_id: str) -> ManagedRevisionReviewBundle:
        row = self.conn.execute(
            "SELECT * FROM change_control_managed_review_bundles WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlCorruptionError("managed request bundle is absent")
        bundle = _decode_model(
            ManagedRevisionReviewBundle,
            str(row["payload_json"]),
            label="managed review bundle",
        )
        head = bundle.review_base.review_open_head
        authority = bundle.review_base.authority
        if not (
            str(row["bundle_id"]) == bundle.bundle_id
            and str(row["bundle_sha256"]) == bundle.bundle_sha256
            and str(row["aggregate_id"]) == head.aggregate_id
            and int(row["base_revision"]) == head.revision
            and str(row["base_aggregate_sha256"]) == head.aggregate_sha256
            and str(row["authority_id"]) == authority.authority_id
            and int(row["authority_revision"]) == authority.authority_revision
            and str(row["active_generation_id"]) == authority.active_generation.generation_id
            and str(row["active_manifest_sha256"]) == authority.active_generation.manifest_sha256
            and int(row["payload_schema_version"]) == bundle.schema_version == 1
        ):
            raise ChangeControlCorruptionError(
                "managed bundle columns differ from canonical evidence"
            )
        target_rows = self.conn.execute(
            "SELECT * FROM change_control_managed_review_targets "
            "WHERE bundle_id=? ORDER BY ordinal",
            (bundle_id,),
        ).fetchall()
        _require_contiguous(target_rows)
        if len(target_rows) != len(bundle.targets):
            raise ChangeControlCorruptionError("managed normalized target count is invalid")
        for target, target_row in zip(bundle.targets, target_rows, strict=True):
            decoded = _decode_model(
                type(target),
                str(target_row["payload_json"]),
                label="managed review target",
            )
            identity, digest = _subject_identity(target.subject)
            if decoded != target or not (
                str(target_row["target_id"]) == target.target_id
                and str(target_row["target_key"]) == target.target_key
                and str(target_row["target_sha256"]) == target.target_sha256
                and str(target_row["subject_kind"]) == target.subject.kind
                and str(target_row["subject_identity"]) == identity
                and str(target_row["subject_sha256"]) == digest
                and int(target_row["payload_schema_version"]) == 1
            ):
                raise ChangeControlCorruptionError(
                    "managed target rows differ from canonical bundle evidence"
                )
        return bundle

    def _append_request_delivery(
        self, record: ManagedRevisionReviewRequestRecord, *, replayed: bool
    ) -> ManagedRevisionReviewRequestReceipt:
        receipt = ManagedRevisionReviewRequestReceipt.create(record, replayed=replayed)
        row = self.conn.execute(
            "SELECT count(*) AS deliveries "
            "FROM change_control_managed_review_request_delivery_receipts WHERE request_id=?",
            (record.command.request_id,),
        ).fetchone()
        assert row is not None
        sequence = int(row["deliveries"])
        if replayed != (sequence > 0):
            raise ChangeControlCorruptionError(
                "managed request delivery sequence/replay shape is invalid"
            )
        self.conn.execute(
            "INSERT INTO change_control_managed_review_request_delivery_receipts "
            "(request_id, delivery_sequence, receipt_id, replayed, delivered_at, "
            "payload_schema_version, payload_json) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (
                record.command.request_id,
                sequence,
                receipt.receipt_id,
                int(replayed),
                _now(),
                _canonical_model_json(receipt),
            ),
        )
        return receipt

    def create_managed_review_request(
        self,
        command: ManagedRevisionReviewRequestCommand,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedRevisionReviewRequestReceipt:
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        command = ManagedRevisionReviewRequestCommand.model_validate_json(command.model_dump_json())
        _require_v2_managed_review_write(command.bundle)
        self._resolve_contract_and_artifacts(command.bundle, resolver)
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(command.operation_id)
            if owner is not None:
                if owner[0] != "managed-request":
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                existing = self._read_request_record(owner[1])
                if existing.command != command:
                    raise ChangeControlIdempotencyError(
                        "managed request operation_id was reused for different inputs"
                    )
                self._initial_request_receipt(existing)
                self._assert_temporal_prerequisite(existing.command.bundle)
                self._assert_live_or_decided_request_authority(
                    existing,
                    authority_context=context,
                )
                receipt = self._append_request_delivery(existing, replayed=True)
                self._commit()
                return receipt

            authority = self._assert_live_review_base(
                command.bundle,
                authority_context=context,
            )
            self._assert_temporal_prerequisite(command.bundle)
            if (
                self.conn.execute(
                    "SELECT 1 FROM change_control_managed_review_request_records WHERE request_id=?",
                    (command.request_id,),
                ).fetchone()
                is not None
            ):
                raise ChangeControlIdempotencyError(
                    "logical managed review request already exists under another operation_id"
                )
            overlap = self.conn.execute(
                "SELECT 1 FROM change_control_managed_review_targets AS target "
                "JOIN change_control_managed_review_bundles AS bundle "
                "ON bundle.bundle_id=target.bundle_id "
                "JOIN change_control_managed_review_request_records AS request "
                "ON request.bundle_id=bundle.bundle_id "
                "LEFT JOIN change_control_managed_review_decisions AS decision "
                "ON decision.request_id=request.request_id "
                "WHERE bundle.aggregate_id=? AND bundle.authority_id=? "
                "AND bundle.base_revision=? AND bundle.base_aggregate_sha256=? "
                "AND target.target_key IN ("
                + ",".join("?" for _ in command.bundle.targets)
                + ") AND decision.request_id IS NULL LIMIT 1",
                (
                    command.bundle.review_base.review_open_head.aggregate_id,
                    authority.authority_id,
                    command.bundle.review_base.review_open_head.revision,
                    command.bundle.review_base.review_open_head.aggregate_sha256,
                    *(target.target_key for target in command.bundle.targets),
                ),
            ).fetchone()
            if overlap is not None:
                raise ChangeControlConflictError(
                    "managed review overlaps an open target at the same authority"
                )
            self._insert_bundle(command.bundle)
            record = ManagedRevisionReviewRequestRecord.create(
                command,
                requested_at=_now(),
                committed_authority=authority,
            )
            self.conn.execute(
                "INSERT INTO change_control_managed_review_request_records VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    command.request_id,
                    record.record_id,
                    record.record_sha256,
                    command.bundle.bundle_id,
                    command.operation_id,
                    command.request_payload_sha256,
                    record.requested_at,
                    _canonical_model_json(record),
                ),
            )
            receipt = self._append_request_delivery(record, replayed=False)
            self._assert_foreign_keys()
            self._commit()
            return receipt
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def _read_request_record(self, request_id: str) -> ManagedRevisionReviewRequestRecord:
        row = self.conn.execute(
            "SELECT * FROM change_control_managed_review_request_records WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlReviewMissingError("managed review request does not exist")
        record = _decode_model(
            ManagedRevisionReviewRequestRecord,
            str(row["payload_json"]),
            label="managed request record",
        )
        bundle = self._read_bundle(str(row["bundle_id"]))
        command = record.command
        if command.bundle != bundle or not (
            str(row["request_id"]) == command.request_id
            and str(row["record_id"]) == record.record_id
            and str(row["record_sha256"]) == record.record_sha256
            and str(row["operation_id"]) == command.operation_id
            and str(row["request_payload_sha256"]) == command.request_payload_sha256
            and str(row["requested_at"]) == record.requested_at
            and int(row["payload_schema_version"]) == record.schema_version == 1
        ):
            raise ChangeControlCorruptionError(
                "managed request columns differ from canonical request evidence"
            )
        _require_canonical_utc(record.requested_at)
        if self._operation_owner(command.operation_id) != ("managed-request", command.request_id):
            raise ChangeControlCorruptionError("managed request operation ownership is invalid")
        return record

    def _initial_request_receipt(
        self, record: ManagedRevisionReviewRequestRecord
    ) -> ManagedRevisionReviewRequestReceipt:
        rows = self.conn.execute(
            "SELECT * FROM change_control_managed_review_request_delivery_receipts "
            "WHERE request_id=? ORDER BY delivery_sequence",
            (record.command.request_id,),
        ).fetchall()
        if not rows:
            raise ChangeControlCorruptionError("managed request has no delivery receipt")
        _require_contiguous(rows, "delivery_sequence")
        previous_delivered_at: str | None = None
        for sequence, row in enumerate(rows):
            receipt = _decode_model(
                ManagedRevisionReviewRequestReceipt,
                str(row["payload_json"]),
                label="managed request delivery receipt",
            )
            if not (
                str(row["receipt_id"]) == receipt.receipt_id
                and int(row["replayed"]) == int(receipt.replayed)
                and receipt.replayed == (sequence > 0)
                and int(row["payload_schema_version"]) == 1
                and receipt
                == ManagedRevisionReviewRequestReceipt.create(record, replayed=receipt.replayed)
            ):
                raise ChangeControlCorruptionError("managed request delivery receipt is invalid")
            delivered_at = _require_canonical_utc(str(row["delivered_at"]))
            if previous_delivered_at is not None and delivered_at < previous_delivered_at:
                raise ChangeControlCorruptionError(
                    "managed request delivery timestamps are not monotonic"
                )
            previous_delivered_at = delivered_at
        initial = _decode_model(
            ManagedRevisionReviewRequestReceipt,
            str(rows[0]["payload_json"]),
            label="initial managed request receipt",
        )
        if initial.replayed:
            raise ChangeControlCorruptionError(
                "initial managed request delivery must be non-replayed"
            )
        if str(rows[0]["delivered_at"]) < record.requested_at:
            raise ChangeControlCorruptionError(
                "initial managed request delivery predates the committed request"
            )
        return initial

    def find_managed_review_request_by_operation_id(
        self,
        operation_id: str,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedRevisionReviewRequestRecord | None:
        """Reopen immutable request evidence without consulting mutable live heads."""

        operation_id = _require_operation_id(operation_id)
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(operation_id)
            if owner is None:
                self._commit()
                return None
            if owner[0] != "managed-request":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another write"
                )
            record = self._read_request_record(owner[1])
            _require_v2_managed_review_write(record.command.bundle)
            self._resolve_contract_and_artifacts(record.command.bundle, resolver)
            self._initial_request_receipt(record)
            self._assert_temporal_prerequisite(record.command.bundle)
            self._assert_live_or_decided_request_authority(
                record,
                authority_context=context,
            )
            self._commit()
            return record
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _append_decision_delivery(
        self, record: ManagedRevisionDecisionRecord, *, replayed: bool
    ) -> ManagedRevisionDecisionReceipt:
        receipt = ManagedRevisionDecisionReceipt.create(record, replayed=replayed)
        request_id = record.command.request_record.command.request_id
        row = self.conn.execute(
            "SELECT count(*) AS deliveries "
            "FROM change_control_managed_review_decision_delivery_receipts WHERE request_id=?",
            (request_id,),
        ).fetchone()
        assert row is not None
        sequence = int(row["deliveries"])
        if replayed != (sequence > 0):
            raise ChangeControlCorruptionError(
                "managed decision delivery sequence/replay shape is invalid"
            )
        self.conn.execute(
            "INSERT INTO change_control_managed_review_decision_delivery_receipts "
            "(request_id, delivery_sequence, receipt_id, replayed, delivered_at, "
            "payload_schema_version, payload_json) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (
                request_id,
                sequence,
                receipt.receipt_id,
                int(replayed),
                _now(),
                _canonical_model_json(receipt),
            ),
        )
        return receipt

    def decide_managed_review(
        self,
        command: ManagedRevisionDecisionCommand,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedRevisionDecisionReceipt:
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        command = ManagedRevisionDecisionCommand.model_validate_json(command.model_dump_json())
        _require_v2_managed_review_write(command.bundle)
        _reject_deferred_managed_edits(command)
        self._resolve_contract_and_artifacts(command, resolver)
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            request_id = command.request_record.command.request_id
            stored = self._read_request_record(request_id)
            _require_v2_managed_review_write(stored.command.bundle)
            self._initial_request_receipt(stored)
            owner = self._operation_owner(command.operation_id)
            if owner is not None:
                if owner[0] != "managed-decision":
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                existing = self._read_decision_record(owner[1])
                if existing.command != command:
                    raise ChangeControlIdempotencyError(
                        "managed decision operation_id was reused for different inputs"
                    )
                self._initial_decision_receipt(existing)
                self._assert_temporal_prerequisite(existing.command.bundle)
                self._verify_stored_authority_chain(
                    existing.command.expected_authority,
                    authority_context=context,
                )
                receipt = self._append_decision_delivery(existing, replayed=True)
                self._commit()
                return receipt

            if command.request_record != stored:
                raise ChangeControlConflictError(
                    "managed decision must bind the exact stored request record"
                )
            if (
                self.conn.execute(
                    "SELECT 1 FROM change_control_managed_review_decisions WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                is not None
            ):
                raise ChangeControlReviewAlreadyDecidedError(
                    "managed review already has an immutable decision"
                )
            self._assert_live_review_base(
                stored.command.bundle,
                authority_context=context,
            )
            self._assert_temporal_prerequisite(stored.command.bundle)
            rebuilt = ManagedRevisionDecisionCommand.create(
                operation_id=command.operation_id,
                request_record=stored,
                bundle_outcome=command.bundle_outcome,
                reviewer_id=command.reviewer_id,
                rationale=command.rationale,
                items=command.items,
            )
            if rebuilt != command:
                raise ChangeControlConflictError(
                    "managed decision differs from its mechanically derived stored-request outcome"
                )
            record = ManagedRevisionDecisionRecord.create(rebuilt, decided_at=_now())
            manifest = rebuilt.generation_manifest
            resulting_manifest_id: str | None = None
            activation_plan_id: str | None = None
            if manifest.requires_activation:
                resulting_manifest_id = manifest.manifest_id
                assert rebuilt.activation_plan is not None
                activation_plan_id = rebuilt.activation_plan.activation_plan_id
                self.conn.execute(
                    "INSERT INTO change_control_generation_manifests VALUES "
                    "(?, ?, ?, ?, ?, 'managed-overlay', 1, ?, 1, ?, ?)",
                    (
                        manifest.manifest_id,
                        rebuilt.expected_authority.aggregate_id,
                        manifest.authorized_generation.generation_id,
                        manifest.generation_number,
                        manifest.manifest_sha256,
                        request_id,
                        _canonical_model_json(manifest),
                        record.decided_at,
                    ),
                )
            self.conn.execute(
                "INSERT INTO change_control_managed_review_decisions VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    request_id,
                    rebuilt.decision_id,
                    record.record_id,
                    record.record_sha256,
                    rebuilt.operation_id,
                    rebuilt.decision_payload_sha256,
                    rebuilt.expected_authority.authority_id,
                    rebuilt.expected_authority.authority_revision,
                    resulting_manifest_id,
                    activation_plan_id,
                    record.decided_at,
                    _canonical_model_json(record),
                ),
            )
            item_rows = []
            for ordinal, item in enumerate(rebuilt.items):
                final_plan_id = (
                    item.edited_plan.plan_id
                    if item.disposition == ManagedRevisionDisposition.EDIT
                    and item.edited_plan is not None
                    else None
                )
                final_plan_sha = (
                    item.edited_plan.plan_sha256
                    if item.disposition == ManagedRevisionDisposition.EDIT
                    and item.edited_plan is not None
                    else None
                )
                item_rows.append(
                    (
                        request_id,
                        ordinal,
                        item.target_id,
                        item.original_target_sha256,
                        item.disposition.value,
                        final_plan_id,
                        final_plan_sha,
                        1,
                        _canonical_model_json(item),
                    )
                )
            self.conn.executemany(
                "INSERT INTO change_control_managed_review_decision_items VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                item_rows,
            )
            receipt = self._append_decision_delivery(record, replayed=False)
            self._assert_foreign_keys()
            self._commit()
            return receipt
        except BaseException as exc:
            if not self.conn.in_transaction:
                raise
            self._rollback_operation_error(exc)
            raise

    def _read_decision_record(self, request_id: str) -> ManagedRevisionDecisionRecord:
        row = self.conn.execute(
            "SELECT * FROM change_control_managed_review_decisions WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlReviewMissingError("managed decision does not exist")
        record = _decode_model(
            ManagedRevisionDecisionRecord,
            str(row["payload_json"]),
            label="managed decision record",
        )
        command = record.command
        request = self._read_request_record(request_id)
        if command.request_record != request or not (
            str(row["decision_id"]) == command.decision_id
            and str(row["record_id"]) == record.record_id
            and str(row["record_sha256"]) == record.record_sha256
            and str(row["operation_id"]) == command.operation_id
            and str(row["decision_payload_sha256"]) == command.decision_payload_sha256
            and str(row["expected_authority_id"]) == command.expected_authority.authority_id
            and int(row["expected_authority_revision"])
            == command.expected_authority.authority_revision
            and str(row["decided_at"]) == record.decided_at
            and int(row["payload_schema_version"]) == record.schema_version == 1
        ):
            raise ChangeControlCorruptionError(
                "managed decision columns differ from canonical decision evidence"
            )
        _require_canonical_utc(record.decided_at)
        if self._operation_owner(command.operation_id) != ("managed-decision", request_id):
            raise ChangeControlCorruptionError("managed decision operation ownership is invalid")
        item_rows = self.conn.execute(
            "SELECT * FROM change_control_managed_review_decision_items "
            "WHERE request_id=? ORDER BY ordinal",
            (request_id,),
        ).fetchall()
        _require_contiguous(item_rows)
        if len(item_rows) != len(command.items):
            raise ChangeControlCorruptionError("managed decision item count is invalid")
        for item, item_row in zip(command.items, item_rows, strict=True):
            decoded = _decode_model(
                type(item),
                str(item_row["payload_json"]),
                label="managed decision item",
            )
            final_plan_id = item.edited_plan.plan_id if item.edited_plan is not None else None
            final_plan_sha = item.edited_plan.plan_sha256 if item.edited_plan is not None else None
            if decoded != item or not (
                str(item_row["target_id"]) == item.target_id
                and str(item_row["original_target_sha256"]) == item.original_target_sha256
                and str(item_row["disposition"]) == item.disposition.value
                and item_row["final_plan_id"] == final_plan_id
                and item_row["final_plan_sha256"] == final_plan_sha
                and int(item_row["payload_schema_version"]) == 1
            ):
                raise ChangeControlCorruptionError(
                    "managed decision item rows differ from canonical evidence"
                )
        if command.generation_manifest.requires_activation:
            manifest = self._read_inactive_manifest(
                command.generation_manifest.manifest_id,
                aggregate_id=command.expected_authority.aggregate_id,
                request_id=request_id,
            )
            activation_plan = command.activation_plan
            if activation_plan is None:
                raise ChangeControlCorruptionError(
                    "activating managed decision has no activation plan"
                )
            if manifest != command.generation_manifest or not (
                row["resulting_manifest_id"] == manifest.manifest_id
                and row["activation_plan_id"] == activation_plan.activation_plan_id
            ):
                raise ChangeControlCorruptionError(
                    "managed decision inactive generation evidence is invalid"
                )
        elif row["resulting_manifest_id"] is not None or row["activation_plan_id"] is not None:
            raise ChangeControlCorruptionError(
                "no-op managed decision cannot persist successor generation evidence"
            )
        return record

    def _read_inactive_manifest(
        self, manifest_id: str, *, aggregate_id: str, request_id: str
    ) -> ManagedGenerationManifestBinding | ManagedGenerationManifestBindingV2:
        row = self.conn.execute(
            "SELECT * FROM change_control_generation_manifests WHERE manifest_id=?",
            (manifest_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlCorruptionError("inactive managed generation manifest is absent")
        manifest = _decode_generation_manifest(str(row["payload_json"]))
        if not (
            str(row["manifest_id"]) == manifest.manifest_id
            and str(row["aggregate_id"]) == aggregate_id
            and str(row["generation_id"]) == manifest.authorized_generation.generation_id
            and int(row["generation_number"]) == manifest.generation_number
            and str(row["manifest_sha256"]) == manifest.manifest_sha256
            and str(row["manifest_kind"]) == "managed-overlay"
            and int(row["created_inactive"]) == 1
            and str(row["source_request_id"]) == request_id == manifest.request_id
            and int(row["payload_schema_version"]) == 1
        ):
            raise ChangeControlCorruptionError(
                "inactive generation columns differ from canonical manifest"
            )
        _require_canonical_utc(str(row["created_at"]))
        decision_row = self.conn.execute(
            "SELECT decided_at FROM change_control_managed_review_decisions WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if decision_row is None or str(row["created_at"]) != str(decision_row["decided_at"]):
            raise ChangeControlCorruptionError(
                "managed generation creation time differs from its decision"
            )
        return manifest

    def _initial_decision_receipt(
        self, record: ManagedRevisionDecisionRecord
    ) -> ManagedRevisionDecisionReceipt:
        request_id = record.command.request_record.command.request_id
        rows = self.conn.execute(
            "SELECT * FROM change_control_managed_review_decision_delivery_receipts "
            "WHERE request_id=? ORDER BY delivery_sequence",
            (request_id,),
        ).fetchall()
        if not rows:
            raise ChangeControlCorruptionError("managed decision has no delivery receipt")
        _require_contiguous(rows, "delivery_sequence")
        previous_delivered_at: str | None = None
        for sequence, row in enumerate(rows):
            receipt = _decode_model(
                ManagedRevisionDecisionReceipt,
                str(row["payload_json"]),
                label="managed decision delivery receipt",
            )
            if not (
                str(row["receipt_id"]) == receipt.receipt_id
                and int(row["replayed"]) == int(receipt.replayed)
                and receipt.replayed == (sequence > 0)
                and int(row["payload_schema_version"]) == 1
                and receipt
                == ManagedRevisionDecisionReceipt.create(record, replayed=receipt.replayed)
            ):
                raise ChangeControlCorruptionError("managed decision delivery receipt is invalid")
            delivered_at = _require_canonical_utc(str(row["delivered_at"]))
            if previous_delivered_at is not None and delivered_at < previous_delivered_at:
                raise ChangeControlCorruptionError(
                    "managed decision delivery timestamps are not monotonic"
                )
            previous_delivered_at = delivered_at
        initial = _decode_model(
            ManagedRevisionDecisionReceipt,
            str(rows[0]["payload_json"]),
            label="initial managed decision receipt",
        )
        if initial.replayed:
            raise ChangeControlCorruptionError(
                "initial managed decision delivery must be non-replayed"
            )
        if str(rows[0]["delivered_at"]) < record.decided_at:
            raise ChangeControlCorruptionError(
                "initial managed decision delivery predates the committed decision"
            )
        return initial

    def _assert_live_or_decided_request_authority(
        self,
        record: ManagedRevisionReviewRequestRecord,
        *,
        authority_context: AuthorityVerificationContext,
    ) -> None:
        # Exact operation replay is a delivery fact, not a second attempt to
        # open the request. It remains replayable after aggregate staleness.
        self._verify_stored_authority_chain(
            record.command.bundle.review_base.authority,
            authority_context=authority_context,
        )

    def get_managed_review(
        self,
        request_id: str,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedRevisionReviewStoreView:
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            request = self._read_request_record(request_id)
            self._resolve_contract_and_artifacts(request.command.bundle, resolver)
            self._assert_temporal_prerequisite(request.command.bundle)
            request_receipt = self._initial_request_receipt(request)
            current_snapshot = self._snapshot_in_transaction(
                request.command.bundle.review_base.review_open_head.aggregate_id
            )
            if current_snapshot is None:
                raise ChangeControlCorruptionError(
                    "managed review aggregate disappeared from the authoritative store"
                )
            current_head = AggregateHeadBinding.create(
                aggregate_id=current_snapshot.aggregate.aggregate_id,
                revision=current_snapshot.revision,
                aggregate_sha256=current_snapshot.aggregate_sha256,
            )
            current_authority = self._read_active_authority(
                request.command.bundle.review_base.authority.aggregate_id,
                authority_context=context,
            )
            decision_row = self.conn.execute(
                "SELECT 1 FROM change_control_managed_review_decisions WHERE request_id=?",
                (request_id,),
            ).fetchone()
            decision = self._read_decision_record(request_id) if decision_row is not None else None
            if decision is None:
                receipt = None
                try:
                    self._assert_live_review_base(
                        request.command.bundle,
                        authority_context=context,
                    )
                except ManagedReviewStaleError:
                    lifecycle = ManagedRevisionStoreLifecycle.STALE
                else:
                    lifecycle = ManagedRevisionStoreLifecycle.OPEN
            else:
                self._resolve_contract_and_artifacts(decision.command, resolver)
                self._assert_temporal_prerequisite(decision.command.bundle)
                self._verify_stored_authority_chain(
                    decision.command.expected_authority,
                    authority_context=context,
                )
                receipt = self._initial_decision_receipt(decision)
                lifecycle = ManagedRevisionStoreLifecycle.DECIDED
            view = ManagedRevisionReviewStoreView(
                request_record=request,
                request_receipt=request_receipt,
                lifecycle=lifecycle,
                current_head=current_head,
                current_authority=current_authority,
                decision_record=decision,
                receipt=receipt,
            )
            self._commit()
            return view
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _read_activation_intent(self, activation_id: str) -> ManagedActivationIntentRecord:
        row = self.conn.execute(
            "SELECT * FROM change_control_managed_activation_intents WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlReviewMissingError("managed activation intent does not exist")
        record = _decode_model(
            ManagedActivationIntentRecord,
            str(row["payload_json"]),
            label="managed activation intent",
        )
        command = record.command
        authority = command.expected_authority
        if not (
            str(row["activation_id"]) == command.activation_id == activation_id
            and str(row["activation_sha256"]) == command.activation_sha256
            and str(row["operation_id"]) == command.operation_id
            and str(row["request_id"]) == command.request_id
            and str(row["decision_id"]) == command.decision_id
            and str(row["decision_record_sha256"]) == command.decision_record_sha256
            and str(row["manifest_id"]) == command.manifest_id
            and str(row["manifest_sha256"]) == command.manifest_sha256
            and str(row["generation_id"]) == command.projection.generation_id
            and str(row["expected_authority_id"]) == authority.authority_id
            and int(row["expected_authority_revision"]) == authority.authority_revision
            and str(row["expected_active_pointer_sha256"]) == authority.active_pointer_sha256
            and str(row["projection_id"]) == command.projection.projection_id
            and str(row["projection_sha256"]) == command.projection.projection_sha256
            and str(row["generation_repository_id"]) == command.generation_repository_id
            and int(row["payload_schema_version"]) == record.schema_version == 1
            and _require_canonical_utc(str(row["created_at"])) == record.created_at
        ):
            raise ChangeControlCorruptionError(
                "managed activation intent columns differ from canonical evidence"
            )
        return record

    def _read_activation_intent_by_operation(
        self, operation_id: str
    ) -> ManagedActivationIntentRecord | None:
        row = self.conn.execute(
            "SELECT activation_id FROM change_control_managed_activation_intents "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else self._read_activation_intent(str(row["activation_id"]))

    def _read_publication_events(self, activation_id: str) -> tuple[ManagedPublicationEvent, ...]:
        intent = self._read_activation_intent(activation_id)
        rows = self.conn.execute(
            "SELECT * FROM change_control_revision_publication_events "
            "WHERE activation_id=? ORDER BY ordinal",
            (activation_id,),
        ).fetchall()
        _require_contiguous(rows, "ordinal")
        decision = self._read_decision_record(intent.command.request_id)
        manifest = decision.command.generation_manifest
        if not isinstance(manifest, ManagedGenerationManifestBindingV2):
            raise ChangeControlCorruptionError(
                "managed publication events require a v2 generation manifest"
            )
        events: list[ManagedPublicationEvent] = []
        for ordinal, row in enumerate(rows):
            event = _decode_model(
                ManagedPublicationEvent,
                str(row["payload_json"]),
                label="managed publication event",
            )
            destination = event.publication.destination
            if not (
                event.activation_id == activation_id
                and event.ordinal == ordinal
                and str(row["event_id"]) == event.event_id
                and str(row["event_sha256"]) == event.event_sha256
                and str(row["destination_id"]) == destination.destination_id
                and str(row["repository_relative_path"]) == event.repository_relative_path
                and str(row["published_sha256"]) == event.published_sha256
                and int(row["published_byte_count"]) == event.published_byte_count
                and int(row["payload_schema_version"]) == event.schema_version == 1
                and _require_canonical_utc(str(row["published_at"])) == event.published_at
            ):
                raise ChangeControlCorruptionError(
                    "managed publication columns differ from canonical evidence"
                )
            expected_path = (
                f"generations/{intent.command.projection.generation_id}/canonical/"
                f"{event.publication.destination.path}"
            )
            if event.repository_relative_path != expected_path:
                raise ChangeControlCorruptionError(
                    "managed publication event has a non-canonical generation path"
                )
            if ordinal >= len(manifest.publication_delta) or (
                event.publication != manifest.publication_delta[ordinal]
            ):
                raise ChangeControlCorruptionError(
                    "managed publication event differs from its manifest ordinal"
                )
            events.append(event)
        if len(events) > len(manifest.publication_delta):
            raise ChangeControlCorruptionError(
                "managed publication set exceeds its bounded manifest"
            )
        if any(event.activation_id != intent.command.activation_id for event in events):
            raise ChangeControlCorruptionError(
                "managed publication event belongs to another activation"
            )
        return tuple(events)

    def _read_index_receipt(self, activation_id: str) -> ManagedIndexReadinessReceipt | None:
        row = self.conn.execute(
            "SELECT * FROM change_control_index_generation_receipts WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if row is None:
            return None
        receipt = _decode_model(
            ManagedIndexReadinessReceipt,
            str(row["payload_json"]),
            label="managed index readiness receipt",
        )
        if not (
            receipt.activation_id == activation_id
            and str(row["receipt_id"]) == receipt.receipt_id
            and str(row["receipt_sha256"]) == receipt.receipt_sha256
            and str(row["generation_id"]) == receipt.generation_id
            and str(row["manifest_sha256"]) == receipt.manifest_sha256
            and str(row["projection_id"]) == receipt.projection_id
            and str(row["projection_sha256"]) == receipt.projection_sha256
            and str(row["index_relative_path"]) == receipt.index_relative_path
            and str(row["index_file_sha256"]) == receipt.index_file_sha256
            and str(row["logical_index_fingerprint"]) == receipt.logical_index_fingerprint
            and int(row["payload_schema_version"]) == receipt.schema_version == 1
            and _require_canonical_utc(str(row["ready_at"])) == receipt.ready_at
        ):
            raise ChangeControlCorruptionError(
                "managed index readiness columns differ from canonical evidence"
            )
        return receipt

    def _read_activation_receipt(
        self, activation_id: str
    ) -> ManagedGenerationActivationReceipt | None:
        row = self.conn.execute(
            "SELECT * FROM change_control_generation_activation_receipts WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if row is None:
            return None
        receipt = _decode_model(
            ManagedGenerationActivationReceipt,
            str(row["payload_json"]),
            label="managed generation activation receipt",
        )
        if not (
            receipt.activation_id == activation_id
            and str(row["receipt_id"]) == receipt.receipt_id
            and str(row["receipt_sha256"]) == receipt.receipt_sha256
            and str(row["generation_id"])
            == receipt.activated_authority.active_generation.generation_id
            and str(row["authority_id"]) == receipt.activated_authority.authority_id
            and int(row["authority_revision"]) == receipt.activated_authority.authority_revision
            and str(row["publication_set_sha256"]) == receipt.publication_set_sha256
            and int(row["publication_count"]) == receipt.publication_count
            and str(row["index_receipt_id"]) == receipt.index_receipt_id
            and str(row["index_receipt_sha256"]) == receipt.index_receipt_sha256
            and int(row["payload_schema_version"]) == receipt.schema_version == 1
            and _require_canonical_utc(str(row["activated_at"])) == receipt.activated_at
        ):
            raise ChangeControlCorruptionError(
                "managed generation activation columns differ from canonical evidence"
            )
        intent = self._read_activation_intent(activation_id)
        decision = self._read_decision_record(intent.command.request_id)
        index_receipt = self._read_index_receipt(activation_id)
        events = self._read_publication_events(activation_id)
        if index_receipt is None or not (
            receipt.operation_id == intent.command.operation_id
            and receipt.decision_record_sha256 == decision.record_sha256
            and receipt.prior_authority == intent.command.expected_authority
            and receipt.index_receipt_id == index_receipt.receipt_id
            and receipt.index_receipt_sha256 == index_receipt.receipt_sha256
            and receipt.publication_count == len(events)
            and receipt.publication_set_sha256 == publication_set_sha256(events)
        ):
            raise ChangeControlCorruptionError(
                "managed generation activation receipt does not bind exact durable effects"
            )
        try:
            receipt.activated_authority.verify_managed_successor_origin(
                expected_authority=receipt.prior_authority,
                decision_record=decision,
            )
        except ValueError as exc:
            raise ChangeControlCorruptionError(
                "managed generation activation successor is not reproducible"
            ) from exc
        return receipt

    def _read_generation_activation_receipt_by_authority(
        self, authority_id: str
    ) -> ManagedGenerationActivationReceipt:
        row = self.conn.execute(
            "SELECT activation_id FROM change_control_generation_activation_receipts "
            "WHERE authority_id=?",
            (authority_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlCorruptionError(
                "managed authority has no immutable activation receipt"
            )
        receipt = self._read_activation_receipt(str(row["activation_id"]))
        assert receipt is not None
        return receipt

    def _validate_activation_command(
        self,
        command: ManagedActivationCommand,
        *,
        resolver: ManagedReviewRepositoryResolver,
        authority_context: AuthorityVerificationContext,
    ) -> ManagedRevisionDecisionRecord:
        expected = command.expected_authority
        if not (
            isinstance(
                expected.origin_basis,
                (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
            )
            and expected.authority_revision == 0
            and expected.active_generation.generation_number == 0
        ):
            raise ManagedGenerationActivationError(
                "PR15 activation supports exactly one managed successor from generation zero"
            )
        decision = self._read_decision_record(command.request_id)
        manifest = decision.command.generation_manifest
        if not isinstance(manifest, ManagedGenerationManifestBindingV2):
            raise ManagedGenerationActivationError(
                "managed activation requires an accepted v2 generation manifest"
            )
        if not (
            decision.command.decision_id == command.decision_id
            and decision.record_sha256 == command.decision_record_sha256
            and decision.command.expected_authority == command.expected_authority
            and manifest.manifest_id == command.manifest_id
            and manifest.manifest_sha256 == command.manifest_sha256
            and manifest.authorized_generation.generation_id == command.projection.generation_id
            and manifest.authorized_generation.generation_number
            == command.projection.generation_number
            and command.projection.request_id == command.request_id
            and command.projection.decision_id == command.decision_id
            and command.projection.decision_record_sha256 == decision.record_sha256
            and command.projection.manifest_id == manifest.manifest_id
            and command.projection.manifest_sha256 == manifest.manifest_sha256
        ):
            raise ManagedGenerationActivationError(
                "managed activation command differs from its authoritative decision"
            )
        self._resolve_contract_and_artifacts(decision.command, resolver)
        try:
            source = resolver.resolve_reviewed_generation_source(manifest.governing_source_adoption)
            exact_projection = derive_managed_generation_projection(
                decision=decision,
                reviewed_inventory=source.inventory,
                temporal_constraints=(source.snapshot.aggregate.validated_temporal_constraints()),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ManagedGenerationActivationError(
                "managed activation projection authority cannot be reopened"
            ) from exc
        if exact_projection != command.projection:
            raise ManagedGenerationActivationError(
                "managed activation projection differs from reviewed source authority"
            )
        self._assert_temporal_prerequisite(decision.command.bundle)
        self._verify_stored_authority_chain(
            command.expected_authority,
            authority_context=authority_context,
        )
        try:
            successor = AuthorityRevisionBinding.create_managed_successor(
                expected_authority=command.expected_authority,
                decision_record=decision,
            )
        except ValueError as exc:
            raise ManagedGenerationActivationError(
                "managed activation decision does not reproduce an exact successor"
            ) from exc
        if successor.active_generation.generation_id != command.projection.generation_id:
            raise ManagedGenerationActivationError(
                "managed projection does not identify the authorized successor generation"
            )
        return decision

    def claim_managed_activation(
        self,
        command: ManagedActivationCommand,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedActivationIntentRecord:
        """Create or exactly replay the sole operation-owned activation intent."""

        command = ManagedActivationCommand.model_validate_json(command.model_dump_json())
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            self._validate_activation_command(
                command,
                resolver=resolver,
                authority_context=context,
            )
            owner = self._operation_owner(command.operation_id)
            if owner is not None:
                if owner[0] != "managed-activation":
                    raise ChangeControlIdempotencyError(
                        "operation_id is already owned by another write"
                    )
                existing = self._read_activation_intent(owner[1])
                if existing.command != command:
                    raise ChangeControlIdempotencyError(
                        "managed activation operation_id was reused for different inputs"
                    )
                self._commit()
                return existing
            if (
                self.conn.execute(
                    "SELECT 1 FROM change_control_managed_activation_intents WHERE request_id=?",
                    (command.request_id,),
                ).fetchone()
                is not None
            ):
                raise ChangeControlIdempotencyError(
                    "managed decision is already owned by another activation operation"
                )
            current = self._read_active_authority(
                command.expected_authority.aggregate_id,
                authority_context=context,
            )
            if current != command.expected_authority:
                raise ManagedGenerationActivationStaleError(
                    "managed activation expected authority is no longer active"
                )
            record = ManagedActivationIntentRecord.create(command=command, created_at=_now())
            self.conn.execute(
                "INSERT INTO change_control_managed_activation_intents VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    command.activation_id,
                    command.activation_sha256,
                    command.operation_id,
                    command.request_id,
                    command.decision_id,
                    command.decision_record_sha256,
                    command.manifest_id,
                    command.manifest_sha256,
                    command.projection.generation_id,
                    command.expected_authority.authority_id,
                    command.expected_authority.authority_revision,
                    command.expected_authority.active_pointer_sha256,
                    command.projection.projection_id,
                    command.projection.projection_sha256,
                    command.generation_repository_id,
                    _canonical_model_json(record),
                    record.created_at,
                ),
            )
            self._assert_foreign_keys()
            self._commit()
            return record
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def record_managed_publication(
        self,
        event: ManagedPublicationEvent,
        *,
        capability: RepositoryVerifiedManagedGenerationEffects,
    ) -> ManagedPublicationEvent:
        """Commit one exact create-only publication event, or replay it."""

        event = ManagedPublicationEvent.model_validate_json(event.model_dump_json())
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_global_operation_ownership()
            intent = self._read_activation_intent(event.activation_id)
            capability.verify(
                command=intent.command,
                publication_events=(event,),
                index_receipt=None,
            )
            decision = self._read_decision_record(intent.command.request_id)
            manifest = decision.command.generation_manifest
            if not isinstance(manifest, ManagedGenerationManifestBindingV2):
                raise ManagedGenerationActivationError(
                    "publication requires an exact v2 activation intent"
                )
            publications = manifest.publication_delta
            if (
                event.ordinal >= len(publications)
                or event.publication != publications[event.ordinal]
            ):
                raise ManagedGenerationActivationError(
                    "publication event differs from exact manifest ordinal"
                )
            expected_path = (
                f"generations/{intent.command.projection.generation_id}/canonical/"
                f"{event.publication.destination.path}"
            )
            if event.repository_relative_path != expected_path:
                raise ManagedGenerationActivationError(
                    "publication event has a non-canonical generation path"
                )
            existing = self.conn.execute(
                "SELECT payload_json FROM change_control_revision_publication_events "
                "WHERE activation_id=? AND ordinal=?",
                (event.activation_id, event.ordinal),
            ).fetchone()
            if existing is not None:
                persisted = _decode_model(
                    ManagedPublicationEvent,
                    str(existing["payload_json"]),
                    label="managed publication event",
                )
                if persisted != event:
                    raise ChangeControlIdempotencyError(
                        "managed publication ordinal was reused for different evidence"
                    )
                self._read_publication_events(event.activation_id)
                self._commit()
                return persisted
            if event.ordinal != len(self._read_publication_events(event.activation_id)):
                raise ManagedGenerationActivationError(
                    "managed publications must commit in exact contiguous manifest order"
                )
            self.conn.execute(
                "INSERT INTO change_control_revision_publication_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    event.activation_id,
                    event.ordinal,
                    event.event_id,
                    event.event_sha256,
                    event.publication.destination.destination_id,
                    event.repository_relative_path,
                    event.published_sha256,
                    event.published_byte_count,
                    _canonical_model_json(event),
                    event.published_at,
                ),
            )
            self._assert_foreign_keys()
            self._commit()
            return event
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def record_managed_index_readiness(
        self,
        receipt: ManagedIndexReadinessReceipt,
        *,
        capability: RepositoryVerifiedManagedGenerationEffects,
    ) -> ManagedIndexReadinessReceipt:
        """Commit one exact isolated-index readiness receipt, or replay it."""

        receipt = ManagedIndexReadinessReceipt.model_validate_json(receipt.model_dump_json())
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_global_operation_ownership()
            intent = self._read_activation_intent(receipt.activation_id)
            command = intent.command
            events = self._read_publication_events(receipt.activation_id)
            capability.verify(
                command=command,
                publication_events=events,
                index_receipt=receipt,
            )
            if not (
                receipt.generation_id == command.projection.generation_id
                and receipt.manifest_sha256 == command.manifest_sha256
                and receipt.projection_id == command.projection.projection_id
                and receipt.projection_sha256 == command.projection.projection_sha256
                and receipt.serving_content_fingerprint
                == command.projection.serving_content_fingerprint
                and receipt.embedding_model_version == command.embedding_model_version
                and receipt.embedding_dimensions == command.embedding_dimensions
                and receipt.storage_schema_version == SCHEMA_VERSION
            ):
                raise ManagedGenerationActivationError(
                    "managed index receipt differs from exact activation command"
                )
            existing = self._read_index_receipt(receipt.activation_id)
            if existing is not None:
                if existing != receipt:
                    raise ChangeControlIdempotencyError(
                        "managed index readiness was reused for different evidence"
                    )
                self._commit()
                return existing
            self.conn.execute(
                "INSERT INTO change_control_index_generation_receipts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    receipt.activation_id,
                    receipt.receipt_id,
                    receipt.receipt_sha256,
                    receipt.generation_id,
                    receipt.manifest_sha256,
                    receipt.projection_id,
                    receipt.projection_sha256,
                    receipt.index_relative_path,
                    receipt.index_file_sha256,
                    receipt.logical_index_fingerprint,
                    _canonical_model_json(receipt),
                    receipt.ready_at,
                ),
            )
            self._assert_foreign_keys()
            self._commit()
            return receipt
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def get_managed_generation_activation(
        self,
        operation_id: str,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedGenerationActivationState | None:
        """Reopen exact durable activation evidence without writing a delivery row."""

        operation_id = _require_operation_id(operation_id)
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(operation_id)
            if owner is None:
                self._commit()
                return None
            if owner[0] != "managed-activation":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another write"
                )
            intent = self._read_activation_intent(owner[1])
            self._validate_activation_command(
                intent.command,
                resolver=resolver,
                authority_context=context,
            )
            state = ManagedGenerationActivationState(
                intent=intent,
                publication_events=self._read_publication_events(intent.command.activation_id),
                index_receipt=self._read_index_receipt(intent.command.activation_id),
                activation_receipt=self._read_activation_receipt(intent.command.activation_id),
            )
            self._commit()
            return state
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def get_managed_activation_operation_request_id(
        self,
        operation_id: str,
    ) -> str | None:
        """Preflight one activation operation without reopening repository evidence."""

        operation_id = _require_operation_id(operation_id)
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            owner = self._operation_owner(operation_id)
            if owner is None:
                self._commit()
                return None
            if owner[0] != "managed-activation":
                raise ChangeControlIdempotencyError(
                    "operation_id is already owned by another write"
                )
            request_id = self._read_activation_intent(owner[1]).command.request_id
            self._commit()
            return request_id
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def get_active_managed_generation_state(
        self,
        aggregate_id: str,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
    ) -> ManagedGenerationActivationState | None:
        """Return exact effect evidence for the active managed successor, if any."""

        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            authority = self._read_active_authority(
                aggregate_id,
                authority_context=context,
            )
            if isinstance(
                authority.origin_basis,
                (GenerationZeroOriginBasis, WorkspaceGenerationZeroOriginBasis),
            ):
                self._commit()
                return None
            receipt = self._read_generation_activation_receipt_by_authority(authority.authority_id)
            intent = self._read_activation_intent(receipt.activation_id)
            self._validate_activation_command(
                intent.command,
                resolver=resolver,
                authority_context=context,
            )
            state = ManagedGenerationActivationState(
                intent=intent,
                publication_events=self._read_publication_events(receipt.activation_id),
                index_receipt=self._read_index_receipt(receipt.activation_id),
                activation_receipt=receipt,
            )
            self._commit()
            return state
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def activate_managed_generation(
        self,
        command: ManagedActivationCommand,
        *,
        capability: RepositoryVerifiedManagedGenerationEffects,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability | None = None,
        prechange_head: AggregateHeadBinding | None = None,
        authority_context: AuthorityVerificationContext | None = None,
        failure_hook: Callable[[str], None] | None = None,
    ) -> ManagedGenerationActivationReceipt:
        """Atomically CAS authority and commit its exact immutable activation receipt."""

        command = ManagedActivationCommand.model_validate_json(command.model_dump_json())
        context = _authority_context(
            authority_context=authority_context,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        self._require_ready()
        self._begin("BEGIN IMMEDIATE")
        try:
            self._validate_identity()
            self._assert_foreign_keys()
            self._validate_receipts()
            self._validate_reviews()
            self._validate_global_operation_ownership()
            decision = self._validate_activation_command(
                command,
                resolver=resolver,
                authority_context=context,
            )
            intent = self._read_activation_intent(command.activation_id)
            if intent.command != command:
                raise ChangeControlIdempotencyError(
                    "managed activation intent differs from CAS command"
                )
            manifest = decision.command.generation_manifest
            assert isinstance(manifest, ManagedGenerationManifestBindingV2)
            events = self._read_publication_events(command.activation_id)
            if len(events) != len(manifest.publication_delta) or any(
                event.ordinal != ordinal or event.publication != publication
                for ordinal, (event, publication) in enumerate(
                    zip(events, manifest.publication_delta, strict=True)
                )
            ):
                raise ManagedGenerationActivationError(
                    "managed activation lacks the exact complete publication set"
                )
            index_receipt = self._read_index_receipt(command.activation_id)
            if index_receipt is None:
                raise ManagedGenerationActivationError(
                    "managed activation requires an immutable index readiness receipt"
                )
            capability.verify(
                command=command,
                publication_events=events,
                index_receipt=index_receipt,
            )
            existing = self._read_activation_receipt(command.activation_id)
            if existing is not None:
                active = self._read_active_authority(
                    command.expected_authority.aggregate_id,
                    authority_context=context,
                )
                if active != existing.activated_authority:
                    raise ChangeControlCorruptionError(
                        "recorded activation is not the exact active authority"
                    )
                self._commit()
                return existing
            current = self._read_active_authority(
                command.expected_authority.aggregate_id,
                authority_context=context,
            )
            if current != command.expected_authority:
                raise ManagedGenerationActivationStaleError(
                    "managed activation lost the expected-authority CAS"
                )
            successor = AuthorityRevisionBinding.create_managed_successor(
                expected_authority=current,
                decision_record=decision,
            )
            receipt = ManagedGenerationActivationReceipt.create(
                activation_id=command.activation_id,
                operation_id=command.operation_id,
                decision_record_sha256=decision.record_sha256,
                publication_set_sha256=publication_set_sha256(events),
                publication_count=len(events),
                index_receipt_id=index_receipt.receipt_id,
                index_receipt_sha256=index_receipt.receipt_sha256,
                prior_authority=current,
                activated_authority=successor,
                activated_at=_now(),
            )
            cursor = self.conn.execute(
                "UPDATE change_control_active_generation SET "
                "authority_id=?, authority_revision=?, origin_kind='managed-decision', "
                "active_generation_id=?, active_generation_number=?, "
                "active_manifest_sha256=?, active_pointer_sha256=?, authority_json=? "
                "WHERE aggregate_id=? AND authority_id=? AND authority_revision=? "
                "AND active_generation_id=? AND active_manifest_sha256=? "
                "AND active_pointer_sha256=?",
                (
                    successor.authority_id,
                    successor.authority_revision,
                    successor.active_generation.generation_id,
                    successor.active_generation.generation_number,
                    successor.active_generation.manifest_sha256,
                    successor.active_pointer_sha256,
                    _canonical_model_json(successor),
                    current.aggregate_id,
                    current.authority_id,
                    current.authority_revision,
                    current.active_generation.generation_id,
                    current.active_generation.manifest_sha256,
                    current.active_pointer_sha256,
                ),
            )
            if cursor.rowcount != 1:
                raise ManagedGenerationActivationStaleError(
                    "managed activation lost the exact authority CAS"
                )
            if failure_hook is not None:
                failure_hook("authority-updated-before-receipt")
            self.conn.execute(
                "INSERT INTO change_control_generation_activation_receipts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    command.activation_id,
                    receipt.receipt_id,
                    receipt.receipt_sha256,
                    successor.active_generation.generation_id,
                    successor.authority_id,
                    successor.authority_revision,
                    receipt.publication_set_sha256,
                    receipt.publication_count,
                    receipt.index_receipt_id,
                    receipt.index_receipt_sha256,
                    _canonical_model_json(receipt),
                    receipt.activated_at,
                ),
            )
            self._assert_foreign_keys()
            self._commit()
            return receipt
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise


__all__ = [
    "AuthorityVerificationContext",
    "ManagedGenerationActivationError",
    "ManagedGenerationActivationStaleError",
    "ManagedGenerationActivationState",
    "ManagedReviewAuthorityError",
    "ManagedReviewRepositoryResolver",
    "ManagedReviewStaleError",
    "ManagedReviewWriteVersionError",
    "ManagedRevisionEditDeferredError",
    "ManagedRevisionReviewStoreView",
    "ManagedRevisionStoreLifecycle",
    "SqliteManagedChangeControlStore",
]
