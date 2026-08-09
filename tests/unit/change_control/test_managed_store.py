from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from mastervault.change_control import store as store_module
from mastervault.change_control.analysis_binding import AnalysisBootstrapIntegrityError
from mastervault.change_control.bootstrap import (
    AnalysisBootstrapResult,
    VerifiedAnalysisBootstrapCapability,
    bootstrap_analysis_aggregate,
)
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ClaimReconciliationAction,
    ClaimReconciliationBinding,
    ClaimReconciliationEntry,
    ContentAddressedInferenceReceipt,
    GroundedArtifactCitation,
    InferenceExecutionMode,
    InferenceUsage,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedBundleOutcome,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedImpactBatchMemberBinding,
    ManagedImpactOutputRefBinding,
    ManagedInferenceContractBinding,
    ManagedReviewBaseBinding,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewOutcome,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewTarget,
    ManagedRunBinding,
    ManagedSemanticHunk,
    NoChangeImpactCard,
    PatchReconstructionAttestation,
    PublicationDestination,
    PublicationKind,
    SourceNoteProjectionBinding,
    TargetAnalysisBinding,
    TemporalDecisionPrerequisite,
    derive_managed_successor,
)
from mastervault.change_control.managed_store import (
    ManagedReviewAuthorityError,
    ManagedReviewStaleError,
    ManagedRevisionStoreLifecycle,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimSourceReference,
    ComparableClaimPair,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    VersionedClaimRevision,
    canonical_json_bytes,
)
from mastervault.change_control.review import (
    HumanReviewDecisionCommand,
    HumanReviewRequestCommand,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectKind,
    ReviewSubjectRef,
)
from mastervault.change_control.store import (
    _DEFAULT_MIGRATIONS_DIR,
    ChangeControlConflictError,
    ChangeControlCorruptionError,
    ChangeControlIdempotencyError,
    ChangeControlReviewAlreadyDecidedError,
    SqliteChangeControlStore,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"
INCOMING_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_incoming_returns_v2.yaml"


def _bootstrapped_store(path: Path):
    store = SqliteManagedChangeControlStore(path)
    store.init_schema()
    result = bootstrap_analysis_aggregate(
        repo_root=REPO_ROOT,
        prechange_manifest_path=PRECHANGE_MANIFEST,
        incoming_manifest_path=INCOMING_MANIFEST,
        store=store,
        prechange_operation_id="managed-store:prechange",
        analysis_operation_id="managed-store:analysis",
    )
    prechange_head = AggregateHeadBinding.create(
        aggregate_id=result.binding.aggregate_id,
        revision=result.binding.prechange_revision,
        aggregate_sha256=result.binding.prechange_aggregate_sha256,
    )
    return store, result, prechange_head


class _Resolver:
    def __init__(
        self,
        contract,
        manifest: bytes,
        artifacts: dict[str, bytes],
        *,
        approved_projection_ids: set[str] | None = None,
        impact_evidence: ManagedImpactAnalysisEvidenceBinding | None = None,
    ) -> None:
        self.contract = contract
        self.manifest = manifest
        self.artifacts = artifacts
        self.approved_projection_ids = approved_projection_ids or set()
        self.impact_evidence = impact_evidence

    def open_algorithm_manifest(self, binding):
        if binding != self.contract:
            raise ValueError("algorithm manifest binding is not approved")
        return self.manifest

    def resolve_approved_inference_contract(self, binding):
        return self.contract

    def resolve_impact_analysis_evidence(self, binding):
        if self.impact_evidence is None:
            raise ValueError("impact evidence is not approved")
        return self.impact_evidence

    def open_artifact(self, artifact):
        return self.artifacts[artifact.path]

    def verify_patch_reconstruction(
        self, plan, *, base_bytes: bytes, result_bytes: bytes
    ) -> PatchReconstructionAttestation:
        rebuilt = bytearray()
        cursor = 0
        for hunk in plan.hunks:
            before = hunk.before_text.encode("utf-8")
            if base_bytes[hunk.start_byte : hunk.end_byte] != before:
                raise ValueError("patch hunk does not match base bytes")
            rebuilt.extend(base_bytes[cursor : hunk.start_byte])
            rebuilt.extend(hunk.replacement_text.encode("utf-8"))
            cursor = hunk.end_byte
        rebuilt.extend(base_bytes[cursor:])
        if bytes(rebuilt) != result_bytes:
            raise ValueError("patch hunks do not reconstruct result bytes")
        return PatchReconstructionAttestation.create_from_verifier_output(
            base_artifact=plan.predecessor_raw,
            result_artifact=plan.proposed_raw,
            hunks=plan.hunks,
            complete_diff_sha256=plan.patch_attestation.complete_diff_sha256,
        )

    def verify_source_note_projection(
        self, projection, *, raw_bytes: bytes, note_bytes: bytes
    ) -> SourceNoteProjectionBinding:
        if (
            self.approved_projection_ids
            and projection.projection_id not in self.approved_projection_ids
        ):
            raise ValueError("SourceNote projection was not validator-approved")
        assert hashlib.sha256(raw_bytes).hexdigest() == projection.raw_artifact.sha256
        assert hashlib.sha256(note_bytes).hexdigest() == projection.note_artifact.sha256
        return projection


@dataclass(frozen=True)
class _Scenario:
    store: SqliteManagedChangeControlStore
    bootstrap: AnalysisBootstrapResult
    prechange_head: AggregateHeadBinding
    authority: AuthorityRevisionBinding
    resolver: _Resolver
    bundle: ManagedRevisionReviewBundle
    request_command: ManagedRevisionReviewRequestCommand


def _artifact(kind: ManagedArtifactKind, path: str, payload: bytes) -> ManagedArtifactRef:
    return ManagedArtifactRef.create(
        kind=kind,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
    )


def test_generation_zero_is_repository_resolved_insert_only_and_replayable(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replay = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    loaded = store.get_active_generation(
        authority.aggregate_id,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )

    assert replay == loaded == authority
    assert authority.authority_revision == 0
    assert authority.active_generation.generation_number == 0
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_active_generation").fetchone()[0]
        == 1
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 1
    )
    operation_id = str(
        store.conn.execute(
            "SELECT initialization_operation_id FROM change_control_active_generation"
        ).fetchone()[0]
    )
    snapshot = store.load(authority.aggregate_id)
    assert snapshot is not None
    with pytest.raises(ChangeControlIdempotencyError, match="another authority"):
        store.compare_and_swap(
            snapshot.aggregate,
            expected_revision=snapshot.revision,
            operation_id=operation_id,
        )
    store.close()


def test_generation_zero_read_rejects_normalized_authority_tamper(tmp_path: Path) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    with store.conn:
        store.conn.execute(
            "UPDATE change_control_active_generation SET active_pointer_sha256=?",
            ("0" * 64,),
        )
    with pytest.raises(ChangeControlCorruptionError):
        store.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
    store.close()


def test_schema_v2_upgrades_to_v3_and_failed_upgrade_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in (
        "001_change_control_aggregate.sql",
        "002_authoritative_human_review.sql",
    ):
        shutil.copy(_DEFAULT_MIGRATIONS_DIR / name, migrations / name)
    database = tmp_path / "state.sqlite3"
    with monkeypatch.context() as v2:
        v2.setattr(store_module, "_SCHEMA_VERSION", 2)
        old = SqliteChangeControlStore(database, migrations)
        old.init_schema()
        old.close()

    third = migrations / "003_managed_revision_review.sql"
    source = (_DEFAULT_MIGRATIONS_DIR / third.name).read_text(encoding="utf-8")
    third.write_text(source + "\nNOT VALID SQL;\n", encoding="utf-8")
    broken = SqliteChangeControlStore(database, migrations)
    with pytest.raises(sqlite3.Error):
        broken.init_schema()
    assert broken._read_meta()["schema_version"] == "2"  # type: ignore[index]
    assert broken._user_tables() == store_module._V2_EXPECTED_TABLES
    broken.close()

    third.write_text(source, encoding="utf-8")
    upgraded = SqliteChangeControlStore(database, migrations)
    upgraded.init_schema()
    assert upgraded._read_meta()["schema_version"] == "3"  # type: ignore[index]
    assert upgraded._user_tables() == store_module._EXPECTED_TABLES
    assert [
        int(row[0])
        for row in upgraded.conn.execute(
            "SELECT version FROM change_control_schema_migrations ORDER BY version"
        )
    ] == [1, 2, 3]
    upgraded.close()


def _no_change_scenario(path: Path) -> _Scenario:
    store, bootstrap, prechange_head = _bootstrapped_store(path)
    authority = store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    analysis = bootstrap.snapshot.aggregate
    newer = bootstrap.incoming_event.document
    older = next(
        document
        for document in analysis.documents.documents
        if document.document_family == newer.document_family
        and document.document_version_id != newer.document_version_id
    )
    replacement = DocumentReplacementAssessment.create(
        newer_document=newer,
        older_document=older,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The newly effective returns policy replaces the earlier version.",
        confidence=1.0,
    )
    constraint = TemporalConstraint.from_document_replacement(
        replacement.with_status(TemporalConstraintStatus.ACCEPTED),
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The replacement closes the older policy interval.",
    )
    proposed = ChangeControlAggregate.create(
        aggregate_id=analysis.aggregate_id,
        documents=analysis.documents,
        claims=analysis.claims,
        relation_graph=analysis.relation_graph,
        dependencies=analysis.dependencies,
        document_replacements=DocumentReplacementSet.create((replacement,)),
        temporal_constraints=TemporalConstraintSet.create((constraint,)),
    )
    proposed_commit = store.compare_and_swap(
        proposed,
        expected_revision=bootstrap.snapshot.revision,
        operation_id="managed-store:run",
    )
    temporal_request = store.create_review_request(
        HumanReviewRequestCommand(
            aggregate_id=analysis.aggregate_id,
            expected_revision=proposed_commit.revision,
            expected_aggregate_sha256=proposed_commit.aggregate_sha256,
            subjects=(
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
                    subject_id=replacement.relation_id,
                ),
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.TEMPORAL_CONSTRAINT,
                    subject_id=constraint.constraint_id,
                ),
            ),
            requester_id="operator@example.test",
            rationale="Review the exact temporal prerequisite.",
        ),
        operation_id="managed-store:temporal-request",
    )
    temporal_decision = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=temporal_request.request.request_id,
            reviewer_id="reviewer@example.test",
            rationale="Accept the exact replacement and derived temporal bound.",
            items=tuple(
                ReviewDecisionItem(
                    kind=subject.kind,
                    subject_id=subject.subject_id,
                    original_subject_sha256=subject.subject_sha256,
                    disposition=ReviewDisposition.ACCEPTED,
                )
                for subject in temporal_request.request.subjects
            ),
        ),
        operation_id="managed-store:temporal-decision",
    )
    review_head = AggregateHeadBinding.create(
        aggregate_id=analysis.aggregate_id,
        revision=temporal_decision.aggregate_revision,
        aggregate_sha256=temporal_decision.aggregate_sha256,
    )
    temporal_sha = hashlib.sha256(
        canonical_json_bytes(temporal_decision.decision.model_dump(mode="json"))
    ).hexdigest()

    algorithm_manifest = b'{"algorithm":"managed-review-test-v1"}'
    algorithm_sha = hashlib.sha256(algorithm_manifest).hexdigest()
    prompt_sha = hashlib.sha256(b"prompt").hexdigest()
    response_schema_sha = hashlib.sha256(b"schema").hexdigest()
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=algorithm_sha,
        contract_id="managed-revision",
        contract_version=1,
        mode=InferenceExecutionMode.REPLAY,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=prompt_sha,
        response_schema_sha256=response_schema_sha,
    )
    target_key = newer.document_id
    target_result_sha = hashlib.sha256(target_key.encode()).hexdigest()
    impact_result_sha = hashlib.sha256(b"impact").hexdigest()
    impact_batch_sha = hashlib.sha256(b"impact-batch").hexdigest()
    impact_workload_sha = hashlib.sha256(b"impact-workload").hexdigest()
    impact_evidence = ManagedImpactAnalysisEvidenceBinding.create(
        repository_id=hashlib.sha256(b"impact-repository").hexdigest(),
        batch_id=f"inference-batch:{impact_batch_sha}",
        batch_sha256=impact_batch_sha,
        batch_members=(
            ManagedImpactBatchMemberBinding(
                execution_id="inference-exec:" + hashlib.sha256(b"impact-exec").hexdigest(),
                receipt_artifact_id="martifact:" + hashlib.sha256(b"impact-receipt").hexdigest(),
                outcome_sha256=hashlib.sha256(b"impact-outcome").hexdigest(),
            ),
        ),
        workload_id=f"impactwork:{impact_workload_sha}",
        workload_sha256=impact_workload_sha,
        result_id=f"impactresult:{impact_result_sha}",
        result_sha256=impact_result_sha,
        output_shards=(
            ManagedImpactOutputRefBinding(
                document_version_id=newer.document_version_id,
                input_shard_id="impactin:" + hashlib.sha256(b"impact-input").hexdigest(),
                input_shard_sha256=hashlib.sha256(b"impact-input").hexdigest(),
                output_shard_id=f"impactout:{target_result_sha}",
                output_shard_sha256=target_result_sha,
                decision_count=1,
                document_disposition="NO_CHANGE_REQUIRED",
            ),
        ),
    )
    analysis_set = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=bootstrap.binding,
        candidate_result_sha256=hashlib.sha256(b"candidates").hexdigest(),
        classification_result_sha256=hashlib.sha256(b"classifications").hexdigest(),
        attention_result_sha256=hashlib.sha256(b"attention").hexdigest(),
        impact_evidence=impact_evidence,
        global_relevant_claim_revision_ids=bootstrap.binding.changed_claim_revision_ids,
    )
    run = ManagedRunBinding.create(
        run_id="managed-store-run",
        operation_id="managed-store:run",
        prechange_head=prechange_head,
        analysis_head=AggregateHeadBinding.create(
            aggregate_id=analysis.aggregate_id,
            revision=bootstrap.snapshot.revision,
            aggregate_sha256=bootstrap.snapshot.aggregate_sha256,
        ),
        algorithm_manifest_sha256=algorithm_sha,
        inference_contract=contract,
        analysis_set=analysis_set,
    )
    envelope = {
        "schema_version": 1,
        "target_key": target_key,
        "analysis_set_id": analysis_set.analysis_set_id,
        "analysis_set_sha256": analysis_set.analysis_set_sha256,
        "impact_result_sha256": analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    input_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_INPUT,
        (
            f"staging/managed-review/{run.run_id}/{target_key}/analysis-input-"
            f"{hashlib.sha256(envelope_bytes).hexdigest()}.json"
        ),
        envelope_bytes,
    )
    target_analysis = TargetAnalysisBinding.create(
        target_key=target_key,
        analysis_set=analysis_set,
        target_result_sha256=target_result_sha,
        inference_input=input_artifact,
    )
    raw_bytes = (REPO_ROOT / newer.source_path).read_bytes()
    predecessor_raw = _artifact(ManagedArtifactKind.RAW_SOURCE, newer.source_path, raw_bytes)
    claims = tuple(
        claim
        for claim in temporal_decision.decision.decided_aggregate.claims.revisions
        if claim.document == newer
    )
    note_path = claims[0].source.source_note_path
    note_bytes = bootstrap.incoming_event.processed_snapshot
    predecessor_note = _artifact(ManagedArtifactKind.SOURCE_NOTE, note_path, note_bytes)
    projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=predecessor_raw,
        note_artifact=predecessor_note,
        canonical_raw_path=newer.source_path,
        canonical_note_path=note_path,
        validator_version="source-note-v1",
        source_note_schema_sha256=hashlib.sha256(b"note-schema").hexdigest(),
        validator_result_sha256=hashlib.sha256(b"note-validation").hexdigest(),
        projected_claims=claims,
    )
    citation = GroundedArtifactCitation.create(
        artifact=input_artifact,
        start_byte=0,
        quote="{",
    )
    semantic = {
        "run_id": run.run_id,
        "target_key": target_key,
        "predecessor": newer,
        "predecessor_raw": predecessor_raw,
        "predecessor_note": predecessor_note,
        "predecessor_projection": projection,
        "analysis": target_analysis,
        "rationale": "The current policy requires no downstream managed revision.",
        "citations": (citation,),
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{run.run_id}/{target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:live-request",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(input_artifact,),
        input_envelope_sha256=target_analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(input_artifact,),
        input_envelope_sha256=target_analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    card = NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output_artifact,
    )
    bundle = ManagedRevisionReviewBundle.create(
        run_binding=run,
        review_base=ManagedReviewBaseBinding.create(
            review_open_head=review_head,
            authority=authority,
        ),
        temporal_prerequisite=TemporalDecisionPrerequisite(
            review_open_head=review_head,
            temporal_decision_record_sha256=temporal_sha,
        ),
        targets=(ManagedRevisionReviewTarget.create(card),),
    )
    resolver = _Resolver(
        contract,
        algorithm_manifest,
        {
            predecessor_raw.path: raw_bytes,
            predecessor_note.path: note_bytes,
            input_artifact.path: envelope_bytes,
            output_artifact.path: output_bytes,
            replay_artifact.path: live_bytes,
        },
        approved_projection_ids={projection.projection_id},
        impact_evidence=impact_evidence,
    )
    request_command = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id="managed-store:request",
        requester_id="operator@example.test",
        rationale="Review the complete no-change evidence.",
    )
    return _Scenario(
        store=store,
        bootstrap=bootstrap,
        prechange_head=prechange_head,
        authority=authority,
        resolver=resolver,
        bundle=bundle,
        request_command=request_command,
    )


def test_managed_no_change_request_decision_replay_and_reopen(tmp_path: Path) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    store = scenario.store
    bootstrap = scenario.bootstrap
    prechange_head = scenario.prechange_head
    authority = scenario.authority
    resolver = scenario.resolver
    bundle = scenario.bundle
    request_command = scenario.request_command
    first_request = store.create_managed_review_request(
        request_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replayed_request = store.create_managed_review_request(
        request_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert not first_request.replayed and replayed_request.replayed
    open_view = store.get_managed_review(
        request_command.request_id,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert open_view.lifecycle == ManagedRevisionStoreLifecycle.OPEN
    outcome = ManagedRevisionReviewOutcome(
        target_id=bundle.targets[0].target_id,
        original_target_sha256=bundle.targets[0].target_sha256,
        disposition=ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
    )
    decision_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:decision",
        request_record=open_view.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Confirm the evidence-backed no-change result.",
        items=(outcome,),
    )
    first_decision = store.decide_managed_review(
        decision_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    replayed_decision = store.decide_managed_review(
        decision_command,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert not first_decision.replayed and replayed_decision.replayed
    decided = store.get_managed_review(
        request_command.request_id,
        resolver=resolver,
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    assert decided.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
    assert decided.authoritative_view is not None
    assert (
        store.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
        == authority
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 1
    )
    store.close()


def _create_managed_request(scenario: _Scenario):
    return scenario.store.create_managed_review_request(
        scenario.request_command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )


def _advance_non_review_aggregate(scenario: _Scenario) -> None:
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    old_claim = next(
        item
        for item in snapshot.aggregate.claims.revisions
        if item.document.document_id == "sl2-policy-returns-v1" and "return-policy" in item.scopes
    )
    new_claim = next(
        item
        for item in snapshot.aggregate.claims.revisions
        if item.document.document_id == "sl2-policy-returns-v2" and "return-policy" in item.scopes
    )
    relation = RelationAssessment.create(
        pair=ComparableClaimPair.create(old_claim, new_claim),
        disposition=PairDisposition.COEXISTS,
        rationale="A later analysis pass records an independent relation.",
        confidence=0.75,
    )
    replacement = ChangeControlAggregate.create(
        aggregate_id=snapshot.aggregate.aggregate_id,
        documents=snapshot.aggregate.documents,
        claims=snapshot.aggregate.claims,
        relation_graph=RelationGraph.create((relation,)),
        dependencies=snapshot.aggregate.dependencies,
        document_replacements=snapshot.aggregate.document_replacements,
        temporal_constraints=snapshot.aggregate.temporal_constraints,
    )
    scenario.store.compare_and_swap(
        replacement,
        expected_revision=snapshot.revision,
        operation_id="managed-store:post-open-analysis",
    )


def _no_change_variant_bundle(scenario: _Scenario) -> ManagedRevisionReviewBundle:
    card = scenario.bundle.targets[0].subject
    assert isinstance(card, NoChangeImpactCard)
    semantic = {
        "run_id": card.run_id,
        "target_key": card.target_key,
        "predecessor": card.predecessor,
        "predecessor_raw": card.predecessor_raw,
        "predecessor_note": card.predecessor_note,
        "predecessor_projection": card.predecessor_projection,
        "analysis": card.analysis,
        "rationale": "A second independently validated no-change explanation.",
        "citations": card.citations,
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:no-change-variant",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    variant = NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output,
    )
    scenario.resolver.artifacts[output.path] = output_bytes
    scenario.resolver.artifacts[replay_artifact.path] = live_bytes
    return ManagedRevisionReviewBundle.create(
        run_binding=scenario.bundle.run_binding,
        review_base=scenario.bundle.review_base,
        temporal_prerequisite=scenario.bundle.temporal_prerequisite,
        targets=(ManagedRevisionReviewTarget.create(variant),),
    )


def _additional_no_change_card(
    scenario: _Scenario, *, document_id: str = "sl2-faq-returns"
) -> NoChangeImpactCard:
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    document = next(
        item for item in snapshot.aggregate.documents.documents if item.document_id == document_id
    )
    claims = tuple(
        item for item in snapshot.aggregate.claims.revisions if item.document == document
    )
    raw_bytes = (REPO_ROOT / document.source_path).read_bytes()
    raw = _artifact(ManagedArtifactKind.RAW_SOURCE, document.source_path, raw_bytes)
    note_path = claims[0].source.source_note_path
    note_bytes = (REPO_ROOT / "datasets/larkstead/processed" / note_path).read_bytes()
    note = _artifact(ManagedArtifactKind.SOURCE_NOTE, note_path, note_bytes)
    projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=raw,
        note_artifact=note,
        canonical_raw_path=document.source_path,
        canonical_note_path=note_path,
        validator_version="source-note-v1",
        source_note_schema_sha256=hashlib.sha256(b"note-schema").hexdigest(),
        validator_result_sha256=hashlib.sha256(b"faq-note-validation").hexdigest(),
        projected_claims=claims,
    )
    analysis_set = scenario.bundle.run_binding.analysis_set
    target_result_sha = hashlib.sha256(document_id.encode()).hexdigest()
    envelope = {
        "schema_version": 1,
        "target_key": document_id,
        "analysis_set_id": analysis_set.analysis_set_id,
        "analysis_set_sha256": analysis_set.analysis_set_sha256,
        "impact_result_sha256": analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    inference_input = _artifact(
        ManagedArtifactKind.INFERENCE_INPUT,
        (
            f"staging/managed-review/{scenario.bundle.run_binding.run_id}/{document_id}/"
            f"analysis-input-{hashlib.sha256(envelope_bytes).hexdigest()}.json"
        ),
        envelope_bytes,
    )
    analysis = TargetAnalysisBinding.create(
        target_key=document_id,
        analysis_set=analysis_set,
        target_result_sha256=target_result_sha,
        inference_input=inference_input,
    )
    citation = GroundedArtifactCitation.create(
        artifact=inference_input,
        start_byte=0,
        quote="{",
    )
    semantic = {
        "run_id": scenario.bundle.run_binding.run_id,
        "target_key": document_id,
        "predecessor": document,
        "predecessor_raw": raw,
        "predecessor_note": note,
        "predecessor_projection": projection,
        "analysis": analysis,
        "rationale": "The FAQ is reviewed in the same bundle and requires no revision.",
        "citations": (citation,),
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{scenario.bundle.run_binding.run_id}/{document_id}/"
            f"validated-output-{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:additional-no-change",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    scenario.resolver.artifacts.update(
        {
            raw.path: raw_bytes,
            note.path: note_bytes,
            inference_input.path: envelope_bytes,
            output.path: output_bytes,
            replay_artifact.path: live_bytes,
        }
    )
    scenario.resolver.approved_projection_ids.add(projection.projection_id)
    return NoChangeImpactCard.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output,
    )


def _rebind_scenario_impact_subjects(
    scenario: _Scenario,
    subjects: tuple[ManagedRevisionPlan | NoChangeImpactCard, ...],
    dispositions: tuple[str, ...],
    *,
    request_operation_id: str,
    request_rationale: str,
) -> _Scenario:
    """Test-only reproduction of subjects after the exact Step 10b evidence changes."""

    assert len(subjects) == len(dispositions)
    original_analysis = scenario.bundle.run_binding.analysis_set
    result_sha = original_analysis.impact_result_sha256
    batch_sha = hashlib.sha256(
        canonical_json_bytes(
            [
                (subject.target_key, disposition)
                for subject, disposition in zip(subjects, dispositions, strict=True)
            ]
        )
    ).hexdigest()
    workload_sha = hashlib.sha256(("workload:" + batch_sha).encode()).hexdigest()
    members = tuple(
        ManagedImpactBatchMemberBinding(
            execution_id="inference-exec:"
            + hashlib.sha256(f"execution:{subject.target_key}".encode()).hexdigest(),
            receipt_artifact_id="martifact:"
            + hashlib.sha256(f"receipt:{subject.target_key}".encode()).hexdigest(),
            outcome_sha256=hashlib.sha256(f"outcome:{subject.target_key}".encode()).hexdigest(),
        )
        for subject in subjects
    )
    outputs = tuple(
        ManagedImpactOutputRefBinding(
            document_version_id=subject.predecessor.document_version_id,
            input_shard_id="impactin:"
            + hashlib.sha256(f"input:{subject.target_key}".encode()).hexdigest(),
            input_shard_sha256=hashlib.sha256(f"input:{subject.target_key}".encode()).hexdigest(),
            output_shard_id=f"impactout:{subject.analysis.target_result_sha256}",
            output_shard_sha256=subject.analysis.target_result_sha256,
            decision_count=1,
            document_disposition=disposition,
        )
        for subject, disposition in zip(subjects, dispositions, strict=True)
    )
    evidence = ManagedImpactAnalysisEvidenceBinding.create(
        repository_id=hashlib.sha256(b"impact-repository").hexdigest(),
        batch_id=f"inference-batch:{batch_sha}",
        batch_sha256=batch_sha,
        batch_members=members,
        workload_id=f"impactwork:{workload_sha}",
        workload_sha256=workload_sha,
        result_id=f"impactresult:{result_sha}",
        result_sha256=result_sha,
        output_shards=outputs,
    )
    analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=original_analysis.analysis_bootstrap,
        candidate_result_sha256=original_analysis.candidate_result_sha256,
        classification_result_sha256=original_analysis.classification_result_sha256,
        attention_result_sha256=original_analysis.attention_result_sha256,
        impact_evidence=evidence,
        global_relevant_claim_revision_ids=original_analysis.global_relevant_claim_revision_ids,
    )
    old_run = scenario.bundle.run_binding
    run = ManagedRunBinding.create(
        run_id=old_run.run_id,
        operation_id=old_run.operation_id,
        prechange_head=old_run.prechange_head,
        analysis_head=old_run.analysis_head,
        algorithm_manifest_sha256=old_run.algorithm_manifest_sha256,
        inference_contract=old_run.inference_contract,
        analysis_set=analysis,
    )
    artifacts = dict(scenario.resolver.artifacts)
    rebound: list[ManagedRevisionPlan | NoChangeImpactCard] = []
    for subject in subjects:
        target_result_sha = subject.analysis.target_result_sha256
        envelope = {
            "schema_version": 1,
            "target_key": subject.target_key,
            "analysis_set_id": analysis.analysis_set_id,
            "analysis_set_sha256": analysis.analysis_set_sha256,
            "impact_result_sha256": analysis.impact_result_sha256,
            "target_result_sha256": target_result_sha,
        }
        envelope_bytes = canonical_json_bytes(envelope)
        envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
        inference_input = _artifact(
            ManagedArtifactKind.INFERENCE_INPUT,
            (
                f"staging/managed-review/{run.run_id}/{subject.target_key}/"
                f"analysis-input-{envelope_sha}.json"
            ),
            envelope_bytes,
        )
        target_analysis = TargetAnalysisBinding.create(
            target_key=subject.target_key,
            analysis_set=analysis,
            target_result_sha256=target_result_sha,
            inference_input=inference_input,
        )
        artifacts[inference_input.path] = envelope_bytes

        def rebound_citation(
            citation: GroundedArtifactCitation,
            *,
            expected_artifact_id: str = subject.analysis.inference_input.artifact_id,
            rebound_artifact: ManagedArtifactRef = inference_input,
        ) -> GroundedArtifactCitation:
            assert citation.artifact_id == expected_artifact_id
            return GroundedArtifactCitation.create(
                artifact=rebound_artifact,
                start_byte=citation.start_byte,
                quote=citation.quote,
            )

        if isinstance(subject, NoChangeImpactCard):
            semantic = {
                "run_id": subject.run_id,
                "target_key": subject.target_key,
                "predecessor": subject.predecessor,
                "predecessor_raw": subject.predecessor_raw,
                "predecessor_note": subject.predecessor_note,
                "predecessor_projection": subject.predecessor_projection,
                "analysis": target_analysis,
                "rationale": subject.rationale,
                "citations": tuple(rebound_citation(item) for item in subject.citations),
            }
            output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
            output_kind = NoChangeImpactCard
        else:
            hunks = tuple(
                ManagedSemanticHunk.create(
                    semantic_key=hunk.semantic_key,
                    base_artifact=subject.predecessor_raw,
                    result_artifact=subject.proposed_raw,
                    start_byte=hunk.start_byte,
                    before_text=hunk.before_text,
                    replacement_text=hunk.replacement_text,
                    citations=tuple(rebound_citation(item) for item in hunk.citations),
                )
                for hunk in subject.hunks
            )
            patch = PatchReconstructionAttestation.create_from_verifier_output(
                base_artifact=subject.predecessor_raw,
                result_artifact=subject.proposed_raw,
                hunks=hunks,
                complete_diff_sha256=subject.patch_attestation.complete_diff_sha256,
            )
            semantic = {
                "run_id": subject.run_id,
                "target_key": subject.target_key,
                "predecessor": subject.predecessor,
                "predecessor_raw": subject.predecessor_raw,
                "predecessor_note": subject.predecessor_note,
                "successor": subject.successor,
                "proposed_raw": subject.proposed_raw,
                "proposed_note": subject.proposed_note,
                "raw_destination": subject.raw_destination,
                "note_destination": subject.note_destination,
                "analysis": target_analysis,
                "predecessor_projection": subject.predecessor_projection,
                "successor_projection": subject.successor_projection,
                "patch_attestation": patch,
                "claim_reconciliation": subject.claim_reconciliation,
                "rationale": subject.rationale,
                "hunks": hunks,
            }
            output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
            output_kind = ManagedRevisionPlan
        output = _artifact(
            ManagedArtifactKind.INFERENCE_OUTPUT,
            (
                f"staging/managed-review/{run.run_id}/{subject.target_key}/validated-output-"
                f"{hashlib.sha256(output_bytes).hexdigest()}.json"
            ),
            output_bytes,
        )
        contract = run.inference_contract
        live = ContentAddressedInferenceReceipt.create(
            contract_id=contract.contract_id,
            contract_version=contract.contract_version,
            mode=InferenceExecutionMode.LIVE,
            provider=contract.provider,
            model=contract.model,
            provider_request_id=f"fixture:rebound:{subject.target_key}",
            replay_source_receipt_sha256=None,
            replay_source_receipt_artifact=None,
            prompt_sha256=contract.prompt_sha256,
            response_schema_sha256=contract.response_schema_sha256,
            input_artifacts=(inference_input,),
            input_envelope_sha256=target_analysis.input_envelope_sha256,
            raw_output_sha256=output.sha256,
            validated_output_sha256=output.sha256,
            usage=InferenceUsage(
                input_tokens=1,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd_micros=1,
                latency_ms=1,
            ),
        )
        live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
        replay_artifact = _artifact(
            ManagedArtifactKind.INFERENCE_RECEIPT,
            f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
            live_bytes,
        )
        replay = ContentAddressedInferenceReceipt.create(
            contract_id=contract.contract_id,
            contract_version=contract.contract_version,
            mode=InferenceExecutionMode.REPLAY,
            provider=contract.provider,
            model=contract.model,
            provider_request_id=None,
            replay_source_receipt_sha256=replay_artifact.sha256,
            replay_source_receipt_artifact=replay_artifact,
            prompt_sha256=contract.prompt_sha256,
            response_schema_sha256=contract.response_schema_sha256,
            input_artifacts=(inference_input,),
            input_envelope_sha256=target_analysis.input_envelope_sha256,
            raw_output_sha256=output.sha256,
            validated_output_sha256=output.sha256,
            usage=InferenceUsage(
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                cost_usd_micros=0,
                latency_ms=0,
            ),
        )
        rebound.append(
            output_kind.create(
                **semantic,
                inference_receipt=replay,
                validated_output=output,
            )
        )
        artifacts[output.path] = output_bytes
        artifacts[replay_artifact.path] = live_bytes

    bundle = ManagedRevisionReviewBundle.create(
        run_binding=run,
        review_base=scenario.bundle.review_base,
        temporal_prerequisite=scenario.bundle.temporal_prerequisite,
        targets=tuple(ManagedRevisionReviewTarget.create(item) for item in rebound),
    )
    projection_ids = {
        item.predecessor_projection.projection_id for item in rebound
    } | {
        item.successor_projection.projection_id
        for item in rebound
        if isinstance(item, ManagedRevisionPlan)
    }
    resolver = _Resolver(
        run.inference_contract,
        scenario.resolver.manifest,
        artifacts,
        approved_projection_ids=projection_ids,
        impact_evidence=evidence,
    )
    request = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id=request_operation_id,
        requester_id="operator@example.test",
        rationale=request_rationale,
    )
    return _Scenario(
        store=scenario.store,
        bootstrap=scenario.bootstrap,
        prechange_head=scenario.prechange_head,
        authority=scenario.authority,
        resolver=resolver,
        bundle=bundle,
        request_command=request,
    )


def test_open_request_becomes_stale_but_exact_replay_survives_fresh_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    scenario = _no_change_scenario(database)
    initial = _create_managed_request(scenario)
    _advance_non_review_aggregate(scenario)

    stale = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert stale.lifecycle == ManagedRevisionStoreLifecycle.STALE
    replay = _create_managed_request(scenario)
    assert replay.replayed
    assert replay.request_record_sha256 == initial.request_record_sha256
    scenario.store.close()

    reopened = SqliteManagedChangeControlStore(database)
    reopened.init_schema()
    replay_after_reopen = reopened.create_managed_review_request(
        scenario.request_command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert replay_after_reopen.replayed
    assert replay_after_reopen.request_record_sha256 == initial.request_record_sha256
    assert (
        reopened.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        ).lifecycle
        == ManagedRevisionStoreLifecycle.STALE
    )
    reopened.close()


@pytest.mark.parametrize(
    "failure", ("impact", "contract", "manifest", "missing", "tampered")
)
def test_repository_resolution_failure_creates_no_managed_review_rows(
    tmp_path: Path, failure: str
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    resolver = _Resolver(
        scenario.resolver.contract,
        scenario.resolver.manifest,
        dict(scenario.resolver.artifacts),
        approved_projection_ids=set(scenario.resolver.approved_projection_ids),
        impact_evidence=scenario.resolver.impact_evidence,
    )
    if failure == "impact":
        evidence = scenario.resolver.impact_evidence
        assert evidence is not None
        resolver.impact_evidence = ManagedImpactAnalysisEvidenceBinding.create(
            repository_id="0" * 64,
            batch_id=evidence.batch_id,
            batch_sha256=evidence.batch_sha256,
            batch_members=evidence.batch_members,
            workload_id=evidence.workload_id,
            workload_sha256=evidence.workload_sha256,
            result_id=evidence.result_id,
            result_sha256=evidence.result_sha256,
            output_shards=evidence.output_shards,
        )
    elif failure == "contract":
        resolver.contract = resolver.contract.model_copy(update={"provider": "unapproved-provider"})
    elif failure == "manifest":
        resolver.manifest = b"tampered algorithm manifest"
    else:
        path = scenario.bundle.targets[0].subject.validated_output.path
        if failure == "missing":
            del resolver.artifacts[path]
        else:
            resolver.artifacts[path] = b"tampered validated output"

    with pytest.raises(ManagedReviewAuthorityError):
        scenario.store.create_managed_review_request(
            scenario.request_command,
            resolver=resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    for table in (
        "change_control_managed_review_bundles",
        "change_control_managed_review_targets",
        "change_control_managed_review_request_records",
        "change_control_managed_review_request_delivery_receipts",
    ):
        assert scenario.store.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    scenario.store.close()


def test_generation_zero_rejects_wrong_head_and_forged_capability_before_insert(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    wrong_head = AggregateHeadBinding.create(
        aggregate_id=prechange_head.aggregate_id,
        revision=prechange_head.revision,
        aggregate_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="exact bootstrap prechange head"):
        store.initialize_generation_zero(
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=wrong_head,
        )

    forged = object.__new__(VerifiedAnalysisBootstrapCapability)
    object.__setattr__(forged, "binding", bootstrap.binding)
    object.__setattr__(forged, "prechange_aggregate_sha256", prechange_head.aggregate_sha256)
    object.__setattr__(forged, "_token", object())
    with pytest.raises(AnalysisBootstrapIntegrityError, match="not repository verified"):
        store.initialize_generation_zero(
            verified_bootstrap=forged,
            prechange_head=prechange_head,
        )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_active_generation").fetchone()[0]
        == 0
    )
    assert (
        store.conn.execute("SELECT count(*) FROM change_control_generation_manifests").fetchone()[0]
        == 0
    )
    store.close()


def test_operation_collision_and_overlapping_open_target_fail_closed(tmp_path: Path) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    snapshot = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    assert snapshot is not None
    with pytest.raises(ChangeControlIdempotencyError, match="another authority"):
        scenario.store.compare_and_swap(
            snapshot.aggregate,
            expected_revision=snapshot.revision,
            operation_id=scenario.request_command.operation_id,
        )

    second_bundle = _no_change_variant_bundle(scenario)
    overlapping = ManagedRevisionReviewRequestCommand.create(
        bundle=second_bundle,
        operation_id="managed-store:overlapping-request",
        requester_id="operator@example.test",
        rationale="Attempt a second request over the same still-open target.",
    )
    with pytest.raises(ChangeControlConflictError, match="overlaps an open target"):
        scenario.store.create_managed_review_request(
            overlapping,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0]
        == 1
    )
    scenario.store.close()


def test_temporal_prerequisite_rejects_unrelated_proposal_transition_lineage(
    tmp_path: Path,
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    run = scenario.bundle.run_binding
    unrelated_run = ManagedRunBinding.create(
        run_id=run.run_id,
        operation_id="managed-store:unrelated-proposal-transition",
        prechange_head=run.prechange_head,
        analysis_head=run.analysis_head,
        algorithm_manifest_sha256=run.algorithm_manifest_sha256,
        inference_contract=run.inference_contract,
        analysis_set=run.analysis_set,
    )
    unrelated_bundle = ManagedRevisionReviewBundle.create(
        run_binding=unrelated_run,
        review_base=scenario.bundle.review_base,
        temporal_prerequisite=scenario.bundle.temporal_prerequisite,
        targets=scenario.bundle.targets,
    )
    command = ManagedRevisionReviewRequestCommand.create(
        bundle=unrelated_bundle,
        operation_id="managed-store:unrelated-lineage-request",
        requester_id="operator@example.test",
        rationale="This request deliberately binds an unrelated proposal transition.",
    )
    with pytest.raises(ManagedReviewAuthorityError, match="does not authorize"):
        scenario.store.create_managed_review_request(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def _activating_scenario(path: Path) -> _Scenario:
    scenario = _no_change_scenario(path)
    card = scenario.bundle.targets[0].subject
    assert isinstance(card, NoChangeImpactCard)
    raw_bytes = scenario.resolver.artifacts[card.predecessor_raw.path]
    note_bytes = scenario.resolver.artifacts[card.predecessor_note.path]
    before = b"45 days"
    start = raw_bytes.index(before)
    replacement = b"46 days"
    proposed_raw_bytes = raw_bytes[:start] + replacement + raw_bytes[start + len(before) :]
    proposed_note_bytes = note_bytes + b"\n<!-- managed persistence fixture -->\n"
    proposed_raw = _artifact(
        ManagedArtifactKind.RAW_SOURCE,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/raw-"
            f"{hashlib.sha256(proposed_raw_bytes).hexdigest()}.md"
        ),
        proposed_raw_bytes,
    )
    proposed_note = _artifact(
        ManagedArtifactKind.SOURCE_NOTE,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/note-"
            f"{hashlib.sha256(proposed_note_bytes).hexdigest()}.md"
        ),
        proposed_note_bytes,
    )
    raw_destination = PublicationDestination.create(
        target_key=card.target_key,
        kind=PublicationKind.RAW_SOURCE,
        expected_sha256=proposed_raw.sha256,
        expected_byte_count=proposed_raw.byte_count,
    )
    note_destination = PublicationDestination.create(
        target_key=card.target_key,
        kind=PublicationKind.SOURCE_NOTE,
        expected_sha256=proposed_note.sha256,
        expected_byte_count=proposed_note.byte_count,
    )
    successor = derive_managed_successor(
        predecessor=card.predecessor,
        target_key=card.target_key,
        proposed_raw=proposed_raw,
        raw_destination=raw_destination,
        effective_from=card.predecessor.declared_effective_from,
    )
    successor_claims = tuple(
        VersionedClaimRevision.create(
            document=successor,
            source=ClaimSourceReference(
                source_note_path=note_destination.path,
                source_note_sha256=proposed_note.sha256,
                source_claim_id=f"managed-{index:02d}",
                evidence=(),
            ),
            statement=claim.statement,
            declared_effective_from=successor.declared_effective_from,
            scopes=claim.scopes,
        )
        for index, claim in enumerate(card.predecessor_projection.projected_claims)
    )
    successor_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=proposed_raw,
        note_artifact=proposed_note,
        canonical_raw_path=raw_destination.path,
        canonical_note_path=note_destination.path,
        validator_version="source-note-v1",
        source_note_schema_sha256=card.predecessor_projection.source_note_schema_sha256,
        validator_result_sha256=hashlib.sha256(b"managed-note-validation").hexdigest(),
        projected_claims=successor_claims,
    )
    reconciliation = ClaimReconciliationBinding.create(
        predecessor_projection=card.predecessor_projection,
        successor_projection=successor_projection,
        entries=tuple(
            ClaimReconciliationEntry(
                action=ClaimReconciliationAction.CARRIED_FORWARD,
                predecessor=old,
                successor=next(
                    new
                    for new in successor_projection.projected_claims
                    if new.statement == old.statement and new.scopes == old.scopes
                ),
            )
            for old in card.predecessor_projection.projected_claims
        ),
    )
    citation = GroundedArtifactCitation.create(
        artifact=card.analysis.inference_input,
        start_byte=0,
        quote="{",
    )
    hunk = ManagedSemanticHunk.create(
        semantic_key="return-window",
        base_artifact=card.predecessor_raw,
        result_artifact=proposed_raw,
        start_byte=start,
        before_text=before.decode(),
        replacement_text=replacement.decode(),
        citations=(citation,),
    )
    patch = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=card.predecessor_raw,
        result_artifact=proposed_raw,
        hunks=(hunk,),
        complete_diff_sha256=hashlib.sha256(
            canonical_json_bytes(
                {"start": start, "before": before.decode(), "replacement": replacement.decode()}
            )
        ).hexdigest(),
    )
    semantic = {
        "run_id": card.run_id,
        "target_key": card.target_key,
        "predecessor": card.predecessor,
        "predecessor_raw": card.predecessor_raw,
        "predecessor_note": card.predecessor_note,
        "successor": successor,
        "proposed_raw": proposed_raw,
        "proposed_note": proposed_note,
        "raw_destination": raw_destination,
        "note_destination": note_destination,
        "analysis": card.analysis,
        "predecessor_projection": card.predecessor_projection,
        "successor_projection": successor_projection,
        "patch_attestation": patch,
        "claim_reconciliation": reconciliation,
        "rationale": "Propose one evidence-grounded create-only policy revision.",
        "hunks": (hunk,),
    }
    output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
    output_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{card.run_id}/{card.target_key}/validated-output-"
            f"{hashlib.sha256(output_bytes).hexdigest()}.json"
        ),
        output_bytes,
    )
    contract = scenario.bundle.run_binding.inference_contract
    live = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=contract.provider,
        model=contract.model,
        provider_request_id="fixture:activating-live-request",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    replay_artifact = _artifact(
        ManagedArtifactKind.INFERENCE_RECEIPT,
        f"receipts/inference/{hashlib.sha256(live_bytes).hexdigest()}.json",
        live_bytes,
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=contract.provider,
        model=contract.model,
        provider_request_id=None,
        replay_source_receipt_sha256=replay_artifact.sha256,
        replay_source_receipt_artifact=replay_artifact,
        prompt_sha256=contract.prompt_sha256,
        response_schema_sha256=contract.response_schema_sha256,
        input_artifacts=(card.analysis.inference_input,),
        input_envelope_sha256=card.analysis.input_envelope_sha256,
        raw_output_sha256=output_artifact.sha256,
        validated_output_sha256=output_artifact.sha256,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    plan = ManagedRevisionPlan.create(
        **semantic,
        inference_receipt=replay,
        validated_output=output_artifact,
    )
    scenario.resolver.artifacts.update(
        {
            proposed_raw.path: proposed_raw_bytes,
            proposed_note.path: proposed_note_bytes,
        }
    )
    return _rebind_scenario_impact_subjects(
        scenario,
        (plan,),
        ("AFFECTED",),
        request_operation_id="managed-store:activating-request",
        request_rationale="Review one exact create-only managed revision.",
    )


def _open_activating_decision(
    scenario: _Scenario, *, operation_id: str = "managed-store:activating-decision"
) -> ManagedRevisionDecisionCommand:
    _create_managed_request(scenario)
    opened = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    target = scenario.bundle.targets[0]
    return ManagedRevisionDecisionCommand.create(
        operation_id=operation_id,
        request_record=opened.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Approve the exact staged revision without publishing it in PR-A.",
        items=(
            ManagedRevisionReviewOutcome(
                target_id=target.target_id,
                original_target_sha256=target.target_sha256,
                disposition=ManagedRevisionDisposition.APPROVE,
            ),
        ),
    )


def _multi_target_scenario(path: Path) -> _Scenario:
    scenario = _activating_scenario(path)
    additional = _additional_no_change_card(scenario)
    return _rebind_scenario_impact_subjects(
        scenario,
        (scenario.bundle.targets[0].subject, additional),
        ("AFFECTED", "NO_CHANGE_REQUIRED"),
        request_operation_id="managed-store:multi-target-request",
        request_rationale=(
            "Review the activating plan and explicit no-change result atomically."
        ),
    )


def test_all_targets_round_trip_in_one_atomic_activating_decision(tmp_path: Path) -> None:
    scenario = _multi_target_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    opened = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    outcomes = tuple(
        sorted(
            (
                ManagedRevisionReviewOutcome(
                    target_id=target.target_id,
                    original_target_sha256=target.target_sha256,
                    disposition=(
                        ManagedRevisionDisposition.APPROVE
                        if isinstance(target.subject, ManagedRevisionPlan)
                        else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                    ),
                )
                for target in scenario.bundle.targets
            ),
            key=lambda item: item.target_id,
        )
    )
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:multi-target-decision",
        request_record=opened.request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Approve or confirm every target as one all-or-none decision.",
        items=outcomes,
    )
    scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    view = scenario.store.get_managed_review(
        scenario.request_command.request_id,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert view.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
    assert view.decision_record is not None
    assert view.decision_record.command.items == outcomes
    assert len(command.generation_manifest.publication_delta) == 2
    assert command.generation_manifest.retained_review_target_keys == ("sl2-faq-returns",)
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decision_items"
        ).fetchone()[0]
        == 2
    )
    scenario.store.close()


def test_activating_decision_persists_only_inactive_manifest_and_replays(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    scenario = _activating_scenario(database)
    aggregate_before = scenario.store.load(scenario.bootstrap.binding.aggregate_id)
    authority_before = scenario.store.get_active_generation(
        scenario.bootstrap.binding.aggregate_id,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    command = _open_activating_decision(scenario)
    first = scenario.store.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert not first.replayed
    assert command.generation_manifest.requires_activation
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 1
    )
    assert scenario.store.load(scenario.bootstrap.binding.aggregate_id) == aggregate_before
    assert (
        scenario.store.get_active_generation(
            scenario.bootstrap.binding.aggregate_id,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
        == authority_before
    )
    scenario.store.close()

    reopened = SqliteManagedChangeControlStore(database)
    reopened.init_schema()
    replay = reopened.decide_managed_review(
        command,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.bootstrap.verification_capability,
        prechange_head=scenario.prechange_head,
    )
    assert replay.replayed
    assert replay.decision_record_sha256 == first.decision_record_sha256
    second_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-store:second-decision",
        request_record=command.request_record,
        bundle_outcome=command.bundle_outcome,
        reviewer_id=command.reviewer_id,
        rationale=command.rationale,
        items=command.items,
    )
    with pytest.raises(ChangeControlReviewAlreadyDecidedError):
        reopened.decide_managed_review(
            second_command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    reopened.close()


@pytest.mark.parametrize(
    ("decided", "mutation"),
    (
        (
            False,
            "UPDATE change_control_managed_review_targets SET ordinal=7 WHERE ordinal=0",
        ),
        (False, "UPDATE change_control_managed_review_bundles SET payload_json='{}'"),
        (
            False,
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivery_sequence=4 WHERE delivery_sequence=0",
        ),
        (
            False,
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivered_at='2000-01-01T00:00:00+00:00' WHERE delivery_sequence=0",
        ),
        (
            True,
            "UPDATE change_control_managed_review_decision_items SET ordinal=9 WHERE ordinal=0",
        ),
        (
            True,
            "UPDATE change_control_managed_review_decision_delivery_receipts "
            "SET payload_json='{}' WHERE delivery_sequence=0",
        ),
        (
            True,
            "UPDATE change_control_generation_manifests SET manifest_sha256='"
            + "0" * 64
            + "' WHERE manifest_kind='managed-overlay'",
        ),
        (
            True,
            "UPDATE change_control_generation_manifests "
            "SET created_at='2000-01-01T00:00:00+00:00' "
            "WHERE manifest_kind='managed-overlay'",
        ),
    ),
)
def test_normalized_managed_evidence_tamper_fails_closed(
    tmp_path: Path, decided: bool, mutation: str
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    if decided:
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    with scenario.store.conn:
        scenario.store.conn.execute(mutation)
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    scenario.store.close()


@pytest.mark.parametrize(
    ("trigger", "table"),
    (
        (
            "CREATE TEMP TRIGGER fail_managed_request BEFORE INSERT ON "
            "change_control_managed_review_request_records BEGIN "
            "SELECT RAISE(ABORT, 'request rollback'); END",
            "request",
        ),
        (
            "CREATE TEMP TRIGGER fail_managed_decision_item BEFORE INSERT ON "
            "change_control_managed_review_decision_items BEGIN "
            "SELECT RAISE(ABORT, 'decision rollback'); END",
            "decision",
        ),
    ),
)
def test_managed_request_and_decision_failures_roll_back_atomically(
    tmp_path: Path, trigger: str, table: str
) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    if table == "request":
        scenario.store.conn.execute(trigger)
        with pytest.raises(sqlite3.IntegrityError, match="request rollback"):
            _create_managed_request(scenario)
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_bundles"
            ).fetchone()[0]
            == 0
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_targets"
            ).fetchone()[0]
            == 0
        )
    else:
        command = _open_activating_decision(scenario)
        scenario.store.conn.execute(trigger)
        with pytest.raises(sqlite3.IntegrityError, match="decision rollback"):
            scenario.store.decide_managed_review(
                command,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.bootstrap.verification_capability,
                prechange_head=scenario.prechange_head,
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_decisions"
            ).fetchone()[0]
            == 0
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
            ).fetchone()[0]
            == 0
        )
        view = scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
        assert view.lifecycle == ManagedRevisionStoreLifecycle.OPEN
    scenario.store.close()


def test_undecided_request_cannot_commit_after_aggregate_head_moves(tmp_path: Path) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    _advance_non_review_aggregate(scenario)
    with pytest.raises(ManagedReviewStaleError, match="aggregate head is stale"):
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def test_decision_reopens_and_revalidates_staged_plan_before_writing(tmp_path: Path) -> None:
    scenario = _activating_scenario(tmp_path / "state.sqlite3")
    command = _open_activating_decision(scenario)
    plan = scenario.bundle.targets[0].subject
    assert isinstance(plan, ManagedRevisionPlan)
    scenario.resolver.artifacts[plan.proposed_raw.path] = b"tampered after request opened"
    with pytest.raises(ManagedReviewAuthorityError):
        scenario.store.decide_managed_review(
            command,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0]
        == 0
    )
    assert (
        scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_generation_manifests WHERE created_inactive=1"
        ).fetchone()[0]
        == 0
    )
    scenario.store.close()


def test_generation_and_delivery_timestamps_are_cross_bound_and_monotonic(
    tmp_path: Path,
) -> None:
    scenario = _no_change_scenario(tmp_path / "state.sqlite3")
    _create_managed_request(scenario)
    _create_managed_request(scenario)
    with scenario.store.conn:
        scenario.store.conn.execute(
            "UPDATE change_control_managed_review_request_delivery_receipts "
            "SET delivered_at='1999-01-01T00:00:00+00:00' WHERE delivery_sequence=1"
        )
    with pytest.raises(ChangeControlCorruptionError):
        scenario.store.get_managed_review(
            scenario.request_command.request_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.bootstrap.verification_capability,
            prechange_head=scenario.prechange_head,
        )
    scenario.store.close()

    generation, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "generation.sqlite3")
    authority = generation.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    with generation.conn:
        generation.conn.execute(
            "UPDATE change_control_generation_manifests "
            "SET created_at='2000-01-01T00:00:00+00:00' WHERE manifest_kind='generation-zero'"
        )
    with pytest.raises(ChangeControlCorruptionError):
        generation.get_active_generation(
            authority.aggregate_id,
            verified_bootstrap=bootstrap.verification_capability,
            prechange_head=prechange_head,
        )
    generation.close()


def test_active_generation_schema_allows_future_managed_shape_but_rejects_mixed_shape(
    tmp_path: Path,
) -> None:
    store, bootstrap, prechange_head = _bootstrapped_store(tmp_path / "state.sqlite3")
    store.initialize_generation_zero(
        verified_bootstrap=bootstrap.verification_capability,
        prechange_head=prechange_head,
    )
    store.conn.execute("BEGIN")
    store.conn.execute(
        "UPDATE change_control_active_generation SET "
        "origin_kind='managed-decision', authority_revision=1, active_generation_number=1"
    )
    row = store.conn.execute(
        "SELECT origin_kind, authority_revision, active_generation_number "
        "FROM change_control_active_generation"
    ).fetchone()
    assert tuple(row) == ("managed-decision", 1, 1)
    store.conn.execute("ROLLBACK")

    with pytest.raises(sqlite3.IntegrityError), store.conn:
        store.conn.execute(
            "UPDATE change_control_active_generation SET origin_kind='managed-decision'"
        )
    store.close()
