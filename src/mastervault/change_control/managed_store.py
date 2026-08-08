"""Authoritative SQLite persistence for managed-revision review (ADR 0009 PR-A).

This module deliberately owns no filesystem roots and no approved model registry.
Callers inject a repository resolver which must reopen typed artifacts and resolve
the reviewed inference contract.  The store repeats byte/hash checks before it
accepts or replays authority-bearing records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from mastervault.change_control.bootstrap import (
    VerifiedAnalysisBootstrapCapability,
    verify_generation_zero_authority,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ContentAddressedInferenceReceipt,
    GenerationZeroManifestBinding,
    GenerationZeroOriginBasis,
    GroundedArtifactCitation,
    InferenceExecutionMode,
    ManagedArtifactRef,
    ManagedGenerationManifestBinding,
    ManagedInferenceContractBinding,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDecisionReceipt,
    ManagedRevisionDecisionRecord,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewRequestReceipt,
    ManagedRevisionReviewRequestRecord,
    ManagedRevisionReviewView,
    NoChangeImpactCard,
    PatchReconstructionAttestation,
    SourceNoteProjectionBinding,
)
from mastervault.change_control.models import (
    TemporalState,
    canonical_json_bytes,
    resolve_document_temporality,
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


class ManagedReviewAuthorityError(ChangeControlConflictError):
    """Repository evidence does not authorize the proposed managed review."""


class ManagedReviewStaleError(ChangeControlReviewStaleError):
    """An undecided managed request no longer binds both live authority heads."""


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


def _canonical_model_json(model: BaseModel) -> str:
    return canonical_json_bytes(model.model_dump(mode="json")).decode("utf-8")


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
    """PR-A managed-review authority in the existing change-control database."""

    def _operation_owner(self, operation_id: str) -> tuple[str, str] | None:
        return self._global_operation_owner(operation_id)

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
                    verified_bootstrap=verified_bootstrap,
                    prechange_head=prechange_head,
                )
                if existing != candidate:
                    raise ChangeControlIdempotencyError(
                        "generation-zero operation_id was reused for different authority"
                    )
                self.conn.execute("COMMIT")
                return existing
            if existing_row is not None:
                existing = self._read_active_authority(
                    candidate.aggregate_id,
                    verified_bootstrap=verified_bootstrap,
                    prechange_head=prechange_head,
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
            self.conn.execute("COMMIT")
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
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
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
        manifest_row = self.conn.execute(
            "SELECT * FROM change_control_generation_manifests WHERE generation_id=?",
            (authority.active_generation.generation_id,),
        ).fetchone()
        if manifest_row is None:
            raise ChangeControlCorruptionError("active generation manifest is absent")
        manifest = _decode_model(
            GenerationZeroManifestBinding,
            str(manifest_row["payload_json"]),
            label="generation-zero manifest",
        )
        if not (
            str(row["authority_id"]) == authority.authority_id
            and int(row["authority_revision"]) == authority.authority_revision == 0
            and str(row["origin_kind"])
            == authority.origin_basis.origin_kind
            == "verified-seed-bootstrap"
            and str(row["active_generation_id"]) == authority.active_generation.generation_id
            and int(row["active_generation_number"])
            == authority.active_generation.generation_number
            == 0
            and str(row["active_manifest_sha256"])
            == authority.active_generation.manifest_sha256
            == manifest.manifest_sha256
            and str(row["active_pointer_sha256"]) == authority.active_pointer_sha256
            and int(row["authority_schema_version"]) == authority.schema_version == 1
            and str(manifest_row["manifest_id"]) == manifest.manifest_id
            and str(manifest_row["aggregate_id"]) == aggregate_id == authority.aggregate_id
            and str(manifest_row["generation_id"]) == authority.active_generation.generation_id
            and int(manifest_row["generation_number"]) == 0
            and str(manifest_row["manifest_sha256"]) == manifest.manifest_sha256
            and str(manifest_row["manifest_kind"]) == "generation-zero"
            and int(manifest_row["created_inactive"]) == 0
            and manifest_row["source_request_id"] is None
            and int(manifest_row["payload_schema_version"]) == 1
        ):
            raise ChangeControlCorruptionError(
                "active generation columns differ from canonical authority evidence"
            )
        _require_canonical_utc(str(row["initialized_at"]))
        _require_canonical_utc(str(manifest_row["created_at"]))
        if str(manifest_row["created_at"]) != str(row["initialized_at"]):
            raise ChangeControlCorruptionError(
                "generation-zero manifest and pointer creation times differ"
            )
        expected_operation_id = _require_operation_id(
            f"managed-generation-zero:{authority.active_pointer_sha256}"
        )
        if str(row["initialization_operation_id"]) != expected_operation_id:
            raise ChangeControlCorruptionError(
                "generation-zero initialization operation is not deterministic"
            )
        self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
        verify_generation_zero_authority(
            authority=authority,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )
        return authority

    def get_active_generation(
        self,
        aggregate_id: str,
        *,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> AuthorityRevisionBinding:
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
                verified_bootstrap=verified_bootstrap,
                prechange_head=prechange_head,
            )
            self.conn.execute("COMMIT")
            return result
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise

    def _assert_live_review_base(
        self,
        bundle: ManagedRevisionReviewBundle,
        *,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
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
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
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
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> ManagedRevisionReviewRequestReceipt:
        command = ManagedRevisionReviewRequestCommand.model_validate_json(command.model_dump_json())
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
                    verified_bootstrap=verified_bootstrap,
                    prechange_head=prechange_head,
                )
                receipt = self._append_request_delivery(existing, replayed=True)
                self.conn.execute("COMMIT")
                return receipt

            authority = self._assert_live_review_base(
                command.bundle,
                verified_bootstrap=verified_bootstrap,
                prechange_head=prechange_head,
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
            self.conn.execute("COMMIT")
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
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> ManagedRevisionDecisionReceipt:
        command = ManagedRevisionDecisionCommand.model_validate_json(command.model_dump_json())
        self._resolve_contract_and_artifacts(command, resolver)
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
                self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
                verify_generation_zero_authority(
                    authority=existing.command.expected_authority,
                    verified_bootstrap=verified_bootstrap,
                    prechange_head=prechange_head,
                )
                receipt = self._append_decision_delivery(existing, replayed=True)
                self.conn.execute("COMMIT")
                return receipt

            request_id = command.request_record.command.request_id
            stored = self._read_request_record(request_id)
            self._initial_request_receipt(stored)
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
                verified_bootstrap=verified_bootstrap,
                prechange_head=prechange_head,
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
            self.conn.execute("COMMIT")
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
    ) -> ManagedGenerationManifestBinding:
        row = self.conn.execute(
            "SELECT * FROM change_control_generation_manifests WHERE manifest_id=?",
            (manifest_id,),
        ).fetchone()
        if row is None:
            raise ChangeControlCorruptionError("inactive managed generation manifest is absent")
        manifest = _decode_model(
            ManagedGenerationManifestBinding,
            str(row["payload_json"]),
            label="inactive managed generation manifest",
        )
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
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> None:
        # Exact operation replay is a delivery fact, not a second attempt to
        # open the request. It remains replayable after aggregate staleness.
        self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
        verify_generation_zero_authority(
            authority=record.command.bundle.review_base.authority,
            verified_bootstrap=verified_bootstrap,
            prechange_head=prechange_head,
        )

    def get_managed_review(
        self,
        request_id: str,
        *,
        resolver: ManagedReviewRepositoryResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
    ) -> ManagedRevisionReviewStoreView:
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
                verified_bootstrap=verified_bootstrap,
                prechange_head=prechange_head,
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
                        verified_bootstrap=verified_bootstrap,
                        prechange_head=prechange_head,
                    )
                except ManagedReviewStaleError:
                    lifecycle = ManagedRevisionStoreLifecycle.STALE
                else:
                    lifecycle = ManagedRevisionStoreLifecycle.OPEN
            else:
                self._resolve_contract_and_artifacts(decision.command, resolver)
                self._assert_temporal_prerequisite(decision.command.bundle)
                self._verify_bootstrap_operations(verified_bootstrap, prechange_head)
                verify_generation_zero_authority(
                    authority=decision.command.expected_authority,
                    verified_bootstrap=verified_bootstrap,
                    prechange_head=prechange_head,
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
            self.conn.execute("COMMIT")
            return view
        except BaseException as exc:
            self._rollback_operation_error(exc)
            raise


__all__ = [
    "ManagedReviewAuthorityError",
    "ManagedReviewRepositoryResolver",
    "ManagedReviewStaleError",
    "ManagedRevisionReviewStoreView",
    "ManagedRevisionStoreLifecycle",
    "SqliteManagedChangeControlStore",
]
