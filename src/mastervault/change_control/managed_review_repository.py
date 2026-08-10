"""Repository-backed authority for managed-review evidence revalidation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mastervault.change_control._repository_files import (
    read_repository_file,
    verified_repository_root,
)
from mastervault.change_control.analysis_binding import AnalysisBootstrapBinding
from mastervault.change_control.bootstrap import incoming_claim_evidence_sha256
from mastervault.change_control.claim_scopes import claim_scopes_v1
from mastervault.change_control.impact_analysis import ImpactInferenceShard
from mastervault.change_control.impact_results import ImpactDecision, ImpactOutputShardRef
from mastervault.change_control.incoming import (
    MANIFEST_RELATIVE_PATH,
    load_verified_incoming_event,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
)
from mastervault.change_control.managed_review import (
    MAX_MANAGED_ARTIFACT_BYTES_V1,
    ClaimReconciliationAction,
    GroundedArtifactCitation,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedGoverningSourceAdoptionBinding,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedImpactBatchMemberBinding,
    ManagedImpactOutputRefBinding,
    ManagedInferenceContractBinding,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    PatchReconstructionAttestation,
    SourceNoteProjectionBinding,
)
from mastervault.change_control.managed_revision_admission import (
    reopen_revision_planning_admission,
    revision_planning_staging_completion,
)
from mastervault.change_control.managed_source_note import (
    MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
    MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
    parse_managed_source_note,
    render_managed_source_note,
)
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository
from mastervault.change_control.models import DocumentSpanReference, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    ImpactWireResponse,
    RecordedInferenceOutcome,
    RecordedInferenceTask,
)
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.models import content_hash

_PROCESSED_PREFIX = PurePosixPath("datasets/larkstead/processed")


@dataclass(frozen=True)
class ApprovedManagedInferenceContractAuthority:
    """Explicit operator approval, kept separate from an untrusted review bundle."""

    contract: ManagedInferenceContractBinding
    algorithm_manifest_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.contract) is not ManagedInferenceContractBinding:
            raise TypeError("approved contract authority requires an exact contract binding")
        if type(self.algorithm_manifest_bytes) is not bytes:
            raise TypeError("approved algorithm manifest must be exact bytes")
        exact_contract = ManagedInferenceContractBinding.model_validate_json(
            canonical_json_bytes(self.contract.model_dump(mode="json"))
        )
        if exact_contract != self.contract:
            raise ValueError("approved inference contract is not an exact validated binding")
        if not self.algorithm_manifest_bytes or len(self.algorithm_manifest_bytes) > (
            MAX_MANAGED_ARTIFACT_BYTES_V1
        ):
            raise ValueError("approved algorithm manifest exceeds fixed artifact bounds")
        if hashlib.sha256(self.algorithm_manifest_bytes).hexdigest() != (
            self.contract.algorithm_manifest_sha256
        ):
            raise ValueError("approved algorithm bytes differ from the contract manifest SHA")


def derive_managed_governing_source_adoption(
    *,
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority,
    analysis_bootstrap: AnalysisBootstrapBinding,
    repo_root: Path,
    manifest_path: Path,
    evidence_repository_id: str,
) -> ManagedGoverningSourceAdoptionBinding:
    """Reopen and bind the already-reviewed incoming source without copying it."""

    if type(reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
        raise TypeError("governing-source adoption requires exact reviewed authority")
    reviewed = reviewed_snapshot.verify()
    exact_bootstrap = AnalysisBootstrapBinding.model_validate_json(
        canonical_json_bytes(analysis_bootstrap.model_dump(mode="json"))
    )
    if exact_bootstrap != analysis_bootstrap:
        raise ValueError("governing-source bootstrap binding is not canonical")
    root = verified_repository_root(repo_root)
    manifest_relative = MANIFEST_RELATIVE_PATH
    expected_manifest_path = root / manifest_relative
    if manifest_path != expected_manifest_path:
        raise ValueError("governing-source manifest path differs from the allowlisted input")
    incoming = load_verified_incoming_event(repo_root=root, manifest_path=manifest_path)
    _manifest_file, manifest_bytes = read_repository_file(
        repo_root=root,
        relative=manifest_relative,
        limit=MAX_MANAGED_ARTIFACT_BYTES_V1,
        label="managed governing-source manifest",
    )
    inventory = reviewed.source_note_capability.verify(snapshot=reviewed.snapshot)
    note_matches = tuple(
        item
        for item in inventory.notes
        if item.document.document_version_id == incoming.document.document_version_id
    )
    if len(note_matches) != 1:
        raise ValueError("reviewed inventory lacks one exact incoming SourceNote")
    note = note_matches[0]
    processed_path = incoming.manifest.document.processed_path
    try:
        logical_note_path = PurePosixPath(processed_path).relative_to(_PROCESSED_PREFIX).as_posix()
    except ValueError as exc:
        raise ValueError("incoming SourceNote is outside the canonical processed root") from exc
    aggregate_documents = {
        item.document_version_id: item for item in reviewed.snapshot.aggregate.documents.documents
    }
    aggregate_claims = tuple(
        sorted(
            (
                item
                for item in reviewed.snapshot.aggregate.claims.revisions
                if item.document.document_version_id == incoming.document.document_version_id
            ),
            key=lambda item: item.claim_revision_id,
        )
    )
    incoming_claims = tuple(
        sorted(incoming.claim_revisions, key=lambda item: item.claim_revision_id)
    )
    if (
        reviewed.binding.evidence_repository_id != evidence_repository_id
        or reviewed.binding.analysis_head.aggregate_id != exact_bootstrap.aggregate_id
        or reviewed.binding.analysis_head.revision != exact_bootstrap.analysis_revision
        or reviewed.binding.analysis_head.aggregate_sha256
        != exact_bootstrap.analysis_aggregate_sha256
        or reviewed.binding.reviewed_head != reviewed.temporal_prerequisite.review_open_head
        or inventory.inventory_sha256 != reviewed.binding.reviewed_inventory_sha256
        or aggregate_documents.get(incoming.document.document_version_id) != incoming.document
        or aggregate_claims != incoming_claims
        or note.document != incoming.document
        or note.source_note_path != logical_note_path
        or note.source_note_sha256 != incoming.manifest.document.processed_sha256
        or note.source_note_utf8.encode("utf-8") != incoming.processed_snapshot
        or incoming.source_note.provenance != incoming.document.source_path
        or incoming.source_note.provenance_hash
        != content_hash(incoming.source_snapshot.decode("utf-8"))
        or incoming.manifest.event_id != exact_bootstrap.incoming_event_id
        or incoming.event_identity != exact_bootstrap.incoming_event_identity
        or incoming.manifest_sha256 != exact_bootstrap.incoming_manifest_sha256
        or incoming.alignment_attestation_id != exact_bootstrap.alignment_attestation_id
        or incoming.alignment_attestation_sha256
        != exact_bootstrap.alignment_attestation_sha256
        or incoming.alignment_policy_version != exact_bootstrap.alignment_policy_version
        or incoming.alignment_payload_sha256 != exact_bootstrap.alignment_payload_sha256
        or incoming_claim_evidence_sha256(incoming)
        != exact_bootstrap.incoming_claim_evidence_sha256
        or incoming.document.document_version_id != exact_bootstrap.incoming_document_version_id
        or incoming.document.document_id != exact_bootstrap.incoming_document_id
    ):
        raise ValueError("incoming governing source differs from reviewed bootstrap lineage")
    raw_artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=incoming.document.source_path,
        sha256=incoming.document.source_sha256,
        byte_count=len(incoming.source_snapshot),
    )
    note_artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.SOURCE_NOTE,
        path=processed_path,
        sha256=incoming.manifest.document.processed_sha256,
        byte_count=len(incoming.processed_snapshot),
    )
    return ManagedGoverningSourceAdoptionBinding.create(
        evidence_repository_id=evidence_repository_id,
        analysis_bootstrap_binding_id=exact_bootstrap.binding_id,
        analysis_bootstrap_binding_sha256=exact_bootstrap.binding_sha256,
        incoming_logical_event_id=incoming.manifest.event_id,
        incoming_event_identity=incoming.event_identity,
        incoming_manifest_path=manifest_relative,
        incoming_manifest_sha256=incoming.manifest_sha256,
        incoming_manifest_byte_count=len(manifest_bytes),
        alignment_attestation_id=incoming.alignment_attestation_id,
        alignment_attestation_sha256=incoming.alignment_attestation_sha256,
        alignment_policy_version=incoming.alignment_policy_version,
        alignment_payload_sha256=incoming.alignment_payload_sha256,
        incoming_claim_evidence_sha256=incoming_claim_evidence_sha256(incoming),
        document=incoming.document,
        raw_artifact=raw_artifact,
        source_note_artifact=note_artifact,
        source_note_logical_path=logical_note_path,
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
class ApprovedManagedGoverningSourceAuthority:
    """Process-local inputs needed to freshly rederive one adoption binding."""

    adoption: ManagedGoverningSourceAdoptionBinding
    reviewed_snapshot: ReviewedTemporalSnapshotAuthority
    analysis_bootstrap: AnalysisBootstrapBinding

    def __post_init__(self) -> None:
        if type(self.adoption) is not ManagedGoverningSourceAdoptionBinding:
            raise TypeError("approved governing source requires an exact adoption binding")
        if type(self.reviewed_snapshot) is not ReviewedTemporalSnapshotAuthority:
            raise TypeError("approved governing source requires exact reviewed authority")
        exact = ManagedGoverningSourceAdoptionBinding.model_validate_json(
            canonical_json_bytes(self.adoption.model_dump(mode="json"))
        )
        if exact != self.adoption:
            raise ValueError("approved governing-source adoption is not canonical")
        self.reviewed_snapshot.verify()


def _batch_member_identities(
    outcomes: tuple[RecordedInferenceOutcome, ...],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                item.execution.execution_id,
                item.execution.receipt_artifact.artifact_id,
                hashlib.sha256(
                    canonical_json_bytes(item.model_dump(mode="json"))
                ).hexdigest(),
            )
            for item in outcomes
        )
    )


def _reopen_impact_input_shard(content: bytes) -> ImpactInferenceShard:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("impact inference input is not canonical JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.pop("namespace", None) != "mastervault.impact-input-shard.v1"
    ):
        raise ValueError("impact inference input has the wrong namespace")
    digest = hashlib.sha256(content).hexdigest()
    shard = ImpactInferenceShard.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "shard_id": f"impactin:{digest}",
                "shard_sha256": digest,
            }
        )
    )
    if shard.canonical_bytes() != content:
        raise ValueError("impact inference input bytes are not canonical")
    return shard


class RepositoryBackedManagedReviewResolver:
    """Fail-closed resolver for store-owned managed-review authority checks."""

    def __init__(
        self,
        *,
        evidence_repository: FilesystemInferenceEvidenceRepository,
        staging_repository: ManagedStagingRepository,
        canonical_root: Path,
        approved_contracts: tuple[ApprovedManagedInferenceContractAuthority, ...],
        revision_admissions: tuple[ManagedRevisionPlanningAdmissionBinding, ...] = (),
        governing_sources: tuple[ApprovedManagedGoverningSourceAuthority, ...] = (),
    ) -> None:
        if evidence_repository.root != staging_repository.root or (
            evidence_repository.repository_id != staging_repository.repository_id
        ):
            raise ValueError("managed-review resolver repositories must share one root")
        self._evidence = evidence_repository
        self._staging = staging_repository
        self._canonical_root = verified_repository_root(canonical_root)
        contracts = {item.contract.contract_binding_id: item for item in approved_contracts}
        if len(contracts) != len(approved_contracts):
            raise ValueError("approved managed inference contract identities must be unique")
        if not contracts:
            raise ValueError("managed-review resolver requires explicit contract approval")
        self._approved_contracts = contracts
        source_authorities = {item.adoption.adoption_id: item for item in governing_sources}
        if len(source_authorities) != len(governing_sources):
            raise ValueError("approved governing-source adoption identities must be unique")
        self._governing_sources: dict[str, ApprovedManagedGoverningSourceAuthority] = {}
        for key, authority in source_authorities.items():
            reopened = derive_managed_governing_source_adoption(
                reviewed_snapshot=authority.reviewed_snapshot,
                analysis_bootstrap=authority.analysis_bootstrap,
                repo_root=self._canonical_root,
                manifest_path=self._canonical_root / authority.adoption.incoming_manifest_path,
                evidence_repository_id=authority.adoption.evidence_repository_id,
            )
            if reopened != authority.adoption:
                raise ValueError("approved governing-source adoption changed on initial reopen")
            self._governing_sources[key] = authority
        admissions = {item.admission_id: item for item in revision_admissions}
        if len(admissions) != len(revision_admissions):
            raise ValueError("managed revision admission identities must be unique")
        self._admission_sources: dict[str, ApprovedManagedGoverningSourceAuthority] = {}
        self._admissions: dict[str, ManagedRevisionPlanningAdmissionBinding] = {}
        for key, value in admissions.items():
            matching_sources = tuple(
                authority
                for authority in self._governing_sources.values()
                if (
                    authority.adoption.evidence_repository_id == value.repository_id
                    and authority.adoption.analysis_bootstrap_binding_id
                    == value.analysis_set.analysis_bootstrap.binding_id
                    and authority.adoption.reviewed_snapshot_binding_id
                    == value.reviewed_snapshot_binding_id
                    and authority.adoption.reviewed_snapshot_binding_sha256
                    == value.reviewed_snapshot_binding_sha256
                    and authority.adoption.temporal_decision_record_sha256
                    == value.temporal_decision_record_sha256
                )
            )
            if len(matching_sources) != 1:
                raise ValueError(
                    "revision admission requires one exact approved reviewed-source authority"
                )
            authority = matching_sources[0]
            self._admissions[key] = reopen_revision_planning_admission(
                value,
                reviewed_snapshot=authority.reviewed_snapshot,
                evidence_repository=self._evidence,
                staging_repository=self._staging,
            )
            self._admission_sources[key] = authority

    def open_algorithm_manifest(self, binding: ManagedInferenceContractBinding) -> bytes:
        approved = self._approved_contracts.get(binding.contract_binding_id)
        if approved is None or approved.contract != binding:
            raise ValueError("algorithm manifest contract is not operator-approved")
        content = approved.algorithm_manifest_bytes
        artifact = ManagedArtifactRef.create(
            kind=ManagedArtifactKind.INFERENCE_INPUT,
            path=f"inference/algorithms/{binding.algorithm_manifest_sha256}.json",
            sha256=binding.algorithm_manifest_sha256,
            byte_count=len(content),
        )
        reopened = self._evidence.open_artifact(artifact)
        if reopened != content:
            raise ValueError("repository algorithm manifest differs from approved bytes")
        return reopened

    def resolve_approved_inference_contract(
        self, binding: ManagedInferenceContractBinding
    ) -> ManagedInferenceContractBinding:
        approved = self._approved_contracts.get(binding.contract_binding_id)
        if approved is None or approved.contract != binding:
            raise ValueError("managed inference contract is not operator-approved")
        return approved.contract

    def resolve_revision_planning_admission(
        self, binding: ManagedRevisionPlanningAdmissionBinding
    ) -> ManagedRevisionPlanningAdmissionBinding:
        configured = self._admissions.get(binding.admission_id)
        if configured != binding:
            raise ValueError("revision planning admission was not explicitly configured")
        source = self._admission_sources.get(binding.admission_id)
        if source is None:
            raise ValueError("revision planning admission lacks reviewed-source authority")
        return reopen_revision_planning_admission(
            binding,
            reviewed_snapshot=source.reviewed_snapshot,
            evidence_repository=self._evidence,
            staging_repository=self._staging,
        )

    def resolve_governing_source_adoption(
        self, binding: ManagedGoverningSourceAdoptionBinding
    ) -> ManagedGoverningSourceAdoptionBinding:
        if type(binding) is not ManagedGoverningSourceAdoptionBinding:
            raise TypeError("governing-source resolver requires the exact binding type")
        configured = self._governing_sources.get(binding.adoption_id)
        if configured is None or configured.adoption != binding:
            raise ValueError("governing-source adoption was not explicitly configured")
        return derive_managed_governing_source_adoption(
            reviewed_snapshot=configured.reviewed_snapshot,
            analysis_bootstrap=configured.analysis_bootstrap,
            repo_root=self._canonical_root,
            manifest_path=self._canonical_root / binding.incoming_manifest_path,
            evidence_repository_id=binding.evidence_repository_id,
        )

    def resolve_impact_analysis_evidence(
        self, binding: ManagedImpactAnalysisEvidenceBinding
    ) -> ManagedImpactAnalysisEvidenceBinding:
        if type(binding) is not ManagedImpactAnalysisEvidenceBinding:
            raise TypeError("impact evidence resolver requires the exact typed binding")
        exact_binding = ManagedImpactAnalysisEvidenceBinding.model_validate_json(
            canonical_json_bytes(binding.model_dump(mode="json"))
        )
        if exact_binding != binding:
            raise ValueError("impact evidence is not an exact validated binding")
        binding = exact_binding
        if binding.repository_id != self._evidence.repository_id:
            raise ValueError("impact evidence belongs to another repository")
        outcomes, capability = self._evidence.resolve_verified_batch(
            batch_id=binding.batch_id,
            batch_sha256=binding.batch_sha256,
        )
        expected_members = tuple(
            (
                item.execution_id,
                item.receipt_artifact_id,
                item.outcome_sha256,
            )
            for item in binding.batch_members
        )
        if _batch_member_identities(outcomes) != expected_members or (
            capability.repository_id != binding.repository_id
        ):
            raise ValueError("impact evidence batch membership changed on reopen")
        contracts = {
            item.execution.contract.contract_binding_id: item.execution.contract
            for item in outcomes
        }
        if len(contracts) != 1:
            raise ValueError("impact evidence requires one exact inference contract")
        impact_contract = next(iter(contracts.values()))
        self.resolve_approved_inference_contract(impact_contract)
        self.open_algorithm_manifest(impact_contract)
        outputs = []
        refs = []
        for outcome in outcomes:
            output = outcome.impact_output
            if outcome.execution.task != RecordedInferenceTask.IMPACT or output is None:
                raise ValueError("impact evidence batch contains another inference task")
            if (
                output.workload_id != binding.workload_id
                or output.workload_sha256 != binding.workload_sha256
                or outcome.execution.input_envelope.workload_id != binding.workload_id
                or outcome.execution.input_envelope.workload_sha256 != binding.workload_sha256
            ):
                raise ValueError("impact evidence output binds another workload")
            envelope = outcome.execution.input_envelope
            input_path = f"inference/inputs/{envelope.input_shard_sha256}.json"
            input_refs = tuple(
                item
                for item in envelope.input_artifacts
                if item.kind == ManagedArtifactKind.INFERENCE_INPUT
                and item.path == input_path
                and item.sha256 == envelope.input_shard_sha256
            )
            if len(input_refs) != 1:
                raise ValueError("impact evidence omits its exact inference input artifact")
            input_bytes = self._evidence.open_artifact(input_refs[0])
            shard = _reopen_impact_input_shard(input_bytes)
            if (
                shard.shard_id != envelope.input_shard_id
                or shard.shard_sha256 != envelope.input_shard_sha256
                or output.input_shard_id != shard.shard_id
                or output.input_shard_sha256 != shard.shard_sha256
                or output.document_version_id
                != shard.target_note.document.document_version_id
            ):
                raise ValueError("impact output differs from its exact reopened input shard")
            questions = {item.question_id: item for item in shard.questions}
            if len(questions) != len(shard.questions) or {
                item.question_id for item in output.decisions
            } != set(questions):
                raise ValueError("impact output does not exactly cover its reopened questions")
            raw_bytes = self._evidence.open_artifact(outcome.execution.raw_output_artifact)
            raw_response = ImpactWireResponse.model_validate_json(raw_bytes)
            raw_decisions = {item.question_id: item for item in raw_response.decisions}
            if len(raw_decisions) != len(raw_response.decisions) or set(raw_decisions) != set(
                questions
            ):
                raise ValueError("impact raw provider output does not cover exact questions")
            decisions_by_question = {item.question_id: item for item in output.decisions}
            for decision in output.decisions:
                reconstructed = ImpactDecision.create(
                    input_shard=shard,
                    question=questions[decision.question_id],
                    disposition=decision.disposition,
                    evidence_spans=decision.evidence_spans,
                    rationale=decision.rationale,
                    attention_path_context_ids=decision.attention_path_context_ids,
                    dependency_context_ids=decision.dependency_context_ids,
                )
                if reconstructed != decision:
                    raise ValueError(
                        "impact decision differs from exact input/span reconstruction"
                    )
            note = shard.target_note
            for question_id in sorted(questions):
                raw_decision = raw_decisions[question_id]
                spans = tuple(
                    DocumentSpanReference(
                        document_version_id=note.document.document_version_id,
                        source_note_path=note.source_note_path,
                        source_note_sha256=note.source_note_sha256,
                        quote=note.source_note_utf8[item.start_char : item.end_char],
                        start_char=item.start_char,
                        end_char=item.end_char,
                    )
                    for item in raw_decision.spans
                )
                reconstructed_from_raw = ImpactDecision.create(
                    input_shard=shard,
                    question=questions[question_id],
                    disposition=raw_decision.disposition,
                    evidence_spans=spans,
                    rationale=raw_decision.rationale,
                    attention_path_context_ids=raw_decision.attention_path_context_ids,
                    dependency_context_ids=raw_decision.dependency_context_ids,
                )
                if reconstructed_from_raw != decisions_by_question[question_id]:
                    raise ValueError(
                        "impact typed decision differs from exact raw provider output"
                    )
            outputs.append(
                ManagedImpactOutputRefBinding(
                    document_version_id=output.document_version_id,
                    input_shard_id=output.input_shard_id,
                    input_shard_sha256=output.input_shard_sha256,
                    output_shard_id=output.output_shard_id,
                    output_shard_sha256=output.output_shard_sha256,
                    decision_count=len(output.decisions),
                    document_disposition=output.document_disposition.value,
                )
            )
            refs.append(ImpactOutputShardRef.create(output))
        ordered_outputs = tuple(
            sorted(outputs, key=lambda item: (item.document_version_id, item.input_shard_id))
        )
        if ordered_outputs != binding.output_shards:
            raise ValueError("impact evidence output references changed on reopen")
        ordered_refs = tuple(
            sorted(refs, key=lambda item: (item.document_version_id, item.input_shard_id))
        )
        result_payload = {
            "namespace": "mastervault.actual-impact-result-index.v1",
            "schema_version": 1,
            "workload_id": binding.workload_id,
            "workload_sha256": binding.workload_sha256,
            "decision_count": sum(item.decision_count for item in binding.output_shards),
            "output_shards": [item.model_dump(mode="json") for item in ordered_refs],
        }
        result_sha256 = hashlib.sha256(canonical_json_bytes(result_payload)).hexdigest()
        if (
            binding.result_sha256 != result_sha256
            or binding.result_id != f"impactresult:{result_sha256}"
        ):
            raise ValueError("impact result index identity changed on reopen")
        members = tuple(
            ManagedImpactBatchMemberBinding(
                execution_id=execution_id,
                receipt_artifact_id=receipt_artifact_id,
                outcome_sha256=outcome_sha256,
            )
            for execution_id, receipt_artifact_id, outcome_sha256 in expected_members
        )
        return ManagedImpactAnalysisEvidenceBinding.create(
            repository_id=binding.repository_id,
            batch_id=binding.batch_id,
            batch_sha256=binding.batch_sha256,
            batch_members=members,
            workload_id=binding.workload_id,
            workload_sha256=binding.workload_sha256,
            result_id=binding.result_id,
            result_sha256=binding.result_sha256,
            output_shards=ordered_outputs,
        )

    def open_artifact(self, artifact: ManagedArtifactRef) -> bytes:
        if type(artifact) is not ManagedArtifactRef:
            raise TypeError("managed artifact reopen requires an exact artifact reference")
        parts = PurePosixPath(artifact.path).parts
        if parts[:2] == ("staging", "managed-review"):
            if artifact.kind not in {
                ManagedArtifactKind.INFERENCE_INPUT,
                ManagedArtifactKind.INFERENCE_OUTPUT,
                ManagedArtifactKind.RAW_SOURCE,
                ManagedArtifactKind.SOURCE_NOTE,
            }:
                raise ValueError("managed staging artifact has an unsupported kind")
            if len(parts) < 5:
                raise ValueError("managed staging artifact path omits run/target identity")
            run_id = parts[2]
            admissions = [item for item in self._admissions.values() if item.run_id == run_id]
            if len(admissions) != 1:
                raise ValueError("managed staging artifact lacks one configured admission")
            return self._staging.open_member(
                completion=revision_planning_staging_completion(admissions[0]),
                artifact=artifact,
            )
        reserved = (
            (parts and parts[0] == "inference")
            or parts[:2] == ("receipts", "inference")
            or parts[:2] == ("temporal", "evidence")
        )
        if artifact.kind in {ManagedArtifactKind.RAW_SOURCE, ManagedArtifactKind.SOURCE_NOTE}:
            if reserved:
                raise ValueError("canonical source artifacts cannot use evidence-reserved roots")
        elif artifact.kind == ManagedArtifactKind.INFERENCE_INPUT:
            allowed = (
                len(parts) >= 3
                and parts[0] == "inference"
                and parts[1] in {"algorithms", "prompts", "schemas", "inputs", "citations"}
            ) or parts[:3] == ("temporal", "evidence", "analyses")
            if not allowed:
                raise ValueError("inference-input artifact uses an unsupported evidence root")
            return self._evidence.open_artifact(artifact)
        elif artifact.kind == ManagedArtifactKind.INFERENCE_OUTPUT:
            if not (
                len(parts) >= 3
                and parts[0] == "inference"
                and parts[1] in {"raw", "outputs"}
            ):
                raise ValueError("inference-output artifact uses an unsupported evidence root")
            return self._evidence.open_artifact(artifact)
        elif artifact.kind == ManagedArtifactKind.INFERENCE_RECEIPT:
            if parts[:2] != ("receipts", "inference"):
                raise ValueError("inference-receipt artifact uses an unsupported evidence root")
            return self._evidence.open_artifact(artifact)
        else:
            raise ValueError("managed artifact kind is unsupported outside staging")
        _path, content = read_repository_file(
            repo_root=self._canonical_root,
            relative=artifact.path,
            limit=artifact.byte_count,
            label="managed canonical predecessor",
        )
        if len(content) != artifact.byte_count or hashlib.sha256(content).hexdigest() != (
            artifact.sha256
        ):
            raise ValueError("canonical predecessor differs from its exact artifact receipt")
        return content

    def verify_patch_reconstruction(
        self,
        plan: ManagedRevisionPlan,
        *,
        base_bytes: bytes,
        result_bytes: bytes,
    ) -> PatchReconstructionAttestation:
        rebuilt = bytearray()
        cursor = 0
        citation_artifacts = {
            item.artifact_id: item for item in plan.inference_receipt.input_artifacts
        }
        for hunk in plan.hunks:
            before = hunk.before_text.encode("utf-8")
            if hunk.start_byte < cursor or base_bytes[hunk.start_byte : hunk.end_byte] != before:
                raise ValueError("managed patch hunk differs from reopened predecessor bytes")
            for citation in hunk.citations:
                self._verify_citation(citation, artifacts=citation_artifacts)
            rebuilt.extend(base_bytes[cursor : hunk.start_byte])
            rebuilt.extend(hunk.replacement_text.encode("utf-8"))
            cursor = hunk.end_byte
        rebuilt.extend(base_bytes[cursor:])
        if bytes(rebuilt) != result_bytes:
            raise ValueError("managed patch hunks do not reconstruct proposed raw bytes")
        diff_sha256 = hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "start_byte": item.start_byte,
                        "end_byte": item.end_byte,
                        "before_sha256": item.before_sha256,
                        "replacement_sha256": item.replacement_sha256,
                    }
                    for item in plan.hunks
                ]
            )
        ).hexdigest()
        return PatchReconstructionAttestation.create_from_verifier_output(
            base_artifact=plan.predecessor_raw,
            result_artifact=plan.proposed_raw,
            hunks=plan.hunks,
            complete_diff_sha256=diff_sha256,
        )

    def _verify_citation(
        self,
        citation: GroundedArtifactCitation,
        *,
        artifacts: dict[str, ManagedArtifactRef],
    ) -> None:
        artifact = artifacts.get(citation.artifact_id)
        if artifact is None or artifact.sha256 != citation.artifact_sha256:
            raise ValueError("managed citation does not bind a recorded input artifact")
        payload = self.open_artifact(artifact)
        if payload[citation.start_byte : citation.end_byte] != citation.quote.encode("utf-8"):
            raise ValueError("managed citation differs from reopened exact evidence bytes")

    def verify_source_note_projection(
        self,
        projection: SourceNoteProjectionBinding,
        *,
        raw_bytes: bytes,
        note_bytes: bytes,
    ) -> SourceNoteProjectionBinding:
        if (
            projection.validator_version != MANAGED_SOURCE_NOTE_VALIDATOR_VERSION
            or projection.source_note_schema_sha256 != MANAGED_SOURCE_NOTE_SCHEMA_SHA256
        ):
            raise ValueError("SourceNote projection uses an unapproved validator contract")
        for artifact, payload in (
            (projection.raw_artifact, raw_bytes),
            (projection.note_artifact, note_bytes),
        ):
            if len(payload) != artifact.byte_count or hashlib.sha256(payload).hexdigest() != (
                artifact.sha256
            ):
                raise ValueError("SourceNote projection bytes differ from artifact receipt")
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("managed projection raw source must be UTF-8") from exc
        note = parse_managed_source_note(note_bytes)
        if (
            note.provenance != projection.canonical_raw_path
            or note.provenance_hash != content_hash(raw_text)
        ):
            raise ValueError("SourceNote provenance differs from exact projected raw bytes")
        note_claims = {item.id: item for item in note.key_claims}
        projected_claims = {
            item.source.source_claim_id: item for item in projection.projected_claims
        }
        if len(note_claims) != len(note.key_claims) or set(note_claims) != set(projected_claims):
            raise ValueError("SourceNote claims do not exactly cover projected claim revisions")
        for source_claim_id, projected in projected_claims.items():
            claim = note_claims[source_claim_id]
            if claim.statement != projected.statement or projected.scopes != claim_scopes_v1(
                document_family=projected.document.document_family,
                affects=tuple(claim.affects),
            ):
                raise ValueError("SourceNote claim semantics differ from projected revisions")
        report = canonical_json_bytes(
            {
                "namespace": "mastervault.managed-source-note-projection-validation.v1",
                "validator_version": MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
                "source_note_schema_sha256": MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
                "raw": projection.raw_artifact.model_dump(mode="json"),
                "note": projection.note_artifact.model_dump(mode="json"),
                "claims": [
                    item.model_dump(mode="json") for item in projection.projected_claims
                ],
            }
        )
        result_sha256 = hashlib.sha256(report).hexdigest()
        if result_sha256 != projection.validator_result_sha256:
            raise ValueError("SourceNote projection validator result was not reproduced")
        return SourceNoteProjectionBinding.create_from_validator_output(
            raw_artifact=projection.raw_artifact,
            note_artifact=projection.note_artifact,
            canonical_raw_path=projection.canonical_raw_path,
            canonical_note_path=projection.canonical_note_path,
            validator_version=projection.validator_version,
            source_note_schema_sha256=projection.source_note_schema_sha256,
            validator_result_sha256=result_sha256,
            projected_claims=projection.projected_claims,
        )

    def verify_revision_plan_source_note(
        self,
        plan: ManagedRevisionPlan,
        *,
        predecessor_note_bytes: bytes,
        result_raw_bytes: bytes,
        proposed_note_bytes: bytes,
    ) -> SourceNoteProjectionBinding:
        """Re-run the frozen PR13 renderer; projections alone are not full-note proof."""

        if type(plan) is not ManagedRevisionPlan:
            raise TypeError("managed SourceNote rendering requires an exact revision plan")
        predecessor_by_key = {
            item.source.source_claim_id: item
            for item in plan.predecessor_projection.projected_claims
        }
        successor_by_key = {
            item.source.source_claim_id: item
            for item in plan.successor_projection.projected_claims
        }
        if (
            len(predecessor_by_key) != len(plan.predecessor_projection.projected_claims)
            or len(successor_by_key) != len(plan.successor_projection.projected_claims)
            or set(predecessor_by_key) != set(successor_by_key)
            or len(plan.claim_reconciliation.entries) != len(predecessor_by_key)
        ):
            raise ValueError("managed plan reconciliation is not the exact PR13 claim shape")
        rewrites: dict[str, str] = {}
        seen: set[str] = set()
        for entry in plan.claim_reconciliation.entries:
            predecessor = entry.predecessor
            successor = entry.successor
            if (
                predecessor is None
                or successor is None
                or entry.action
                not in {
                    ClaimReconciliationAction.CARRIED_FORWARD,
                    ClaimReconciliationAction.REWORDED,
                }
            ):
                raise ValueError("managed plan reconciliation uses a non-PR13 action")
            source_key = predecessor.source.source_claim_id
            if (
                source_key in seen
                or successor.source.source_claim_id != source_key
                or predecessor_by_key.get(source_key) != predecessor
                or successor_by_key.get(source_key) != successor
                or predecessor.scopes != successor.scopes
            ):
                raise ValueError("managed plan reconciliation does not preserve exact claim keys")
            seen.add(source_key)
            changed = predecessor.statement != successor.statement
            if (entry.action == ClaimReconciliationAction.REWORDED) != changed:
                raise ValueError("managed plan reconciliation action differs from statement change")
            if changed:
                rewrites[source_key] = successor.statement
        rendered = render_managed_source_note(
            predecessor_note_bytes=predecessor_note_bytes,
            successor_raw_bytes=result_raw_bytes,
            successor_raw_path=plan.raw_destination.path,
            analysis_as_of=plan.successor.declared_effective_from,
            statement_rewrites=rewrites,
        )
        if (
            rendered.note_bytes != proposed_note_bytes
            or rendered.model != parse_managed_source_note(proposed_note_bytes)
        ):
            raise ValueError("proposed SourceNote differs from deterministic PR13 rendering")
        return self.verify_source_note_projection(
            plan.successor_projection,
            raw_bytes=result_raw_bytes,
            note_bytes=proposed_note_bytes,
        )


__all__ = [
    "ApprovedManagedGoverningSourceAuthority",
    "ApprovedManagedInferenceContractAuthority",
    "RepositoryBackedManagedReviewResolver",
    "derive_managed_governing_source_adoption",
]
