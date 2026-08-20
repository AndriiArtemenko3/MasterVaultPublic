from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import mastervault.change_control.managed_review as managed_review_module
import mastervault.change_control.models as change_models_module
from mastervault.change_control.analysis_binding import AnalysisBootstrapBinding
from mastervault.change_control.managed_review import (
    MAX_MANAGED_ARTIFACT_BYTES_V1,
    MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1,
    MAX_MANAGED_CHANGED_CLAIMS_V1,
    MAX_MANAGED_CITATION_QUOTE_BYTES_V1,
    MAX_MANAGED_DECISION_CANONICAL_BYTES_V1,
    MAX_MANAGED_INFERENCE_INPUT_BYTES_V1,
    MAX_MANAGED_INFERENCE_INPUTS_V1,
    MAX_MANAGED_RECONCILIATION_ENTRIES_V1,
    MAX_MANAGED_TARGETS_V1,
    AggregateHeadBinding,
    AuthorityRevisionBinding,
    ClaimReconciliationAction,
    ClaimReconciliationBinding,
    ClaimReconciliationEntry,
    ContentAddressedGenerationBinding,
    ContentAddressedInferenceReceipt,
    GenerationPublicationBinding,
    GenerationZeroOriginBasis,
    GroundedArtifactCitation,
    InferenceExecutionMode,
    InferenceUsage,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedBundleOutcome,
    ManagedGenerationManifestBinding,
    ManagedInferenceContractBinding,
    ManagedReviewBaseBinding,
    ManagedReviewLifecycleStatus,
    ManagedRevisionDecisionCommand,
    ManagedRevisionDecisionReceipt,
    ManagedRevisionDecisionRecord,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionReviewBundle,
    ManagedRevisionReviewOutcome,
    ManagedRevisionReviewRequestCommand,
    ManagedRevisionReviewRequestReceipt,
    ManagedRevisionReviewRequestRecord,
    ManagedRevisionReviewTarget,
    ManagedRevisionReviewView,
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
from mastervault.change_control.models import (
    ClaimSourceReference,
    DocumentAuthority,
    DocumentRole,
    DocumentVersionMetadata,
    VersionedClaimRevision,
    canonical_json_bytes,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
INCOMING_ID = "incoming:" + "1" * 64
RUN_ID = "run-alpha"


@dataclass(frozen=True)
class Context:
    analysis_set: ManagedAnalysisSetBinding
    run: ManagedRunBinding
    review_base: ManagedReviewBaseBinding
    prerequisite: TemporalDecisionPrerequisite


def _artifact(
    key: str,
    kind: ManagedArtifactKind,
    *,
    path: str | None = None,
    sha: str = SHA_A,
    byte_count: int = 128,
) -> ManagedArtifactRef:
    return ManagedArtifactRef.create(
        kind=kind,
        path=path or f"fixtures/{key}.json",
        sha256=sha,
        byte_count=byte_count,
    )


def _bootstrap_binding() -> AnalysisBootstrapBinding:
    changed = tuple(f"claimrev:{index:064x}" for index in range(10))
    fields = AnalysisBootstrapBinding.model_fields
    inputs = {
        "aggregate_id": "larkstead.sl2-returns",
        "analysis_as_of": "2031-01-01",
        "analysis_operation_id": "analysis:op",
        "alignment_attestation_id": fields["alignment_attestation_id"].default,
        "alignment_attestation_sha256": fields["alignment_attestation_sha256"].default,
        "alignment_payload_sha256": SHA_C,
        "alignment_policy_version": fields["alignment_policy_version"].default,
        "incoming_document_id": "returns-policy-v2",
        "incoming_document_version_id": "docv:" + "7" * 64,
        "incoming_claim_evidence_sha256": SHA_F,
        "incoming_event_id": "returns-policy-v2",
        "incoming_event_identity": INCOMING_ID,
        "incoming_manifest_sha256": SHA_E,
        "prechange_operation_id": "prechange:op",
        "schema_version": 1,
        "scope_policy_version": "claim-scopes-v1",
        "seed_as_of": "2030-01-01",
        "seed_manifest_sha256": SHA_D,
        "seed_scenario_id": "scenario",
    }
    values = {
        **inputs,
        "analysis_aggregate_sha256": SHA_B,
        "analysis_revision": 2,
        "canonical_input_sha256": hashlib.sha256(canonical_json_bytes(inputs)).hexdigest(),
        "changed_claim_revision_ids": changed,
        "prechange_aggregate_sha256": SHA_A,
        "prechange_revision": 1,
    }
    digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
    return AnalysisBootstrapBinding.model_validate(
        {
            "binding_id": f"analysis-bootstrap:{digest}",
            "binding_sha256": digest,
            **values,
            "analysis_as_of": date(2031, 1, 1),
            "seed_as_of": date(2030, 1, 1),
        }
    )


def _analysis_set(*, impact_sha: str = SHA_E) -> ManagedAnalysisSetBinding:
    bootstrap = _bootstrap_binding()
    return ManagedAnalysisSetBinding.create(
        analysis_bootstrap=bootstrap,
        candidate_result_sha256=SHA_C,
        classification_result_sha256=SHA_D,
        attention_result_sha256=SHA_F,
        impact_result_sha256=impact_sha,
        global_relevant_claim_revision_ids=bootstrap.changed_claim_revision_ids,
    )


def _context(*, impact_sha: str = SHA_E) -> Context:
    analysis = _analysis_set(impact_sha=impact_sha)
    prechange = AggregateHeadBinding.create(
        aggregate_id="larkstead.sl2-returns", revision=1, aggregate_sha256=SHA_A
    )
    analysis_head = AggregateHeadBinding.create(
        aggregate_id="larkstead.sl2-returns", revision=2, aggregate_sha256=SHA_B
    )
    inference_contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=SHA_C,
        contract_id="managed-revision",
        contract_version=1,
        mode=InferenceExecutionMode.REPLAY,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=SHA_B,
        response_schema_sha256=SHA_C,
    )
    run = ManagedRunBinding.create(
        run_id=RUN_ID,
        operation_id="managed-run:alpha",
        prechange_head=prechange,
        analysis_head=analysis_head,
        algorithm_manifest_sha256=SHA_C,
        inference_contract=inference_contract,
        analysis_set=analysis,
    )
    review_open = AggregateHeadBinding.create(
        aggregate_id="larkstead.sl2-returns", revision=3, aggregate_sha256=SHA_C
    )
    authority = AuthorityRevisionBinding.create_generation_zero(
        analysis_bootstrap=analysis.analysis_bootstrap,
        prechange_head=prechange,
    )
    review_base = ManagedReviewBaseBinding.create(review_open_head=review_open, authority=authority)
    prerequisite = TemporalDecisionPrerequisite(
        review_open_head=review_open,
        temporal_decision_record_sha256=SHA_E,
    )
    return Context(analysis, run, review_base, prerequisite)


def _predecessor(
    key: str,
    *,
    raw_path: str | None = None,
    note_path: str | None = None,
    raw_sha: str = SHA_A,
    note_sha: str = SHA_B,
) -> tuple[DocumentVersionMetadata, ManagedArtifactRef, ManagedArtifactRef]:
    raw = _artifact(
        f"{key}-raw",
        ManagedArtifactKind.RAW_SOURCE,
        path=raw_path or f"datasets/raw/{key}.md",
        sha=raw_sha,
    )
    note = _artifact(
        f"{key}-note",
        ManagedArtifactKind.SOURCE_NOTE,
        path=note_path or f"datasets/processed/{key}.md",
        sha=note_sha,
    )
    document = DocumentVersionMetadata.create(
        document_id=key,
        document_family=f"customer-support.{key}",
        version_label="v1",
        source_path=raw.path,
        source_sha256=raw.sha256,
        declared_effective_from=date(2030, 1, 1),
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )
    return document, raw, note


def _claim(
    *,
    document: DocumentVersionMetadata,
    note_path: str,
    note_sha: str,
    local_id: str,
    statement: str,
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=note_path,
            source_note_sha256=note_sha,
            source_claim_id=local_id,
            evidence=(),
        ),
        statement=statement,
        declared_effective_from=document.declared_effective_from,
        scopes=(document.document_family,),
    )


def _projection(
    *,
    raw: ManagedArtifactRef,
    note: ManagedArtifactRef,
    canonical_raw_path: str,
    canonical_note_path: str,
    claims: tuple[VersionedClaimRevision, ...],
) -> SourceNoteProjectionBinding:
    return SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=raw,
        note_artifact=note,
        canonical_raw_path=canonical_raw_path,
        canonical_note_path=canonical_note_path,
        validator_version="source-note-v1",
        source_note_schema_sha256=SHA_C,
        validator_result_sha256=SHA_D,
        projected_claims=claims,
    )


def test_projection_accepts_only_exact_suffixless_workspace_bootstrap_raw_locator() -> None:
    workspace_path = f"bootstrap-sources/workspace-root/{SHA_A}"
    raw = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=workspace_path,
        sha256=SHA_B,
        byte_count=12,
    )
    note = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.SOURCE_NOTE,
        path="datasets/example/processed/source.md",
        sha256=SHA_C,
        byte_count=12,
    )

    projection = _projection(
        raw=raw,
        note=note,
        canonical_raw_path=workspace_path,
        canonical_note_path=note.path,
        claims=(),
    )
    assert projection.canonical_raw_path == workspace_path

    for invalid in (
        "datasets/example/raw/source",
        f"bootstrap-sources/workspace-root/not-{SHA_A}",
        f"bootstrap-sources/workspace-root/extra/{SHA_A}",
    ):
        invalid_raw = ManagedArtifactRef.create(
            kind=ManagedArtifactKind.RAW_SOURCE,
            path=invalid,
            sha256=SHA_B,
            byte_count=12,
        )
        with pytest.raises(ValueError, match="must be Markdown"):
            _projection(
                raw=invalid_raw,
                note=note,
                canonical_raw_path=invalid,
                canonical_note_path=note.path,
                claims=(),
            )


def _target_analysis(
    key: str,
    context: Context,
) -> TargetAnalysisBinding:
    target_result_sha256 = hashlib.sha256(key.encode()).hexdigest()
    envelope = {
        "schema_version": 1,
        "target_key": key,
        "analysis_set_id": context.analysis_set.analysis_set_id,
        "analysis_set_sha256": context.analysis_set.analysis_set_sha256,
        "impact_result_sha256": context.analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha256,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
    input_artifact = _artifact(
        f"{key}-analysis-input",
        ManagedArtifactKind.INFERENCE_INPUT,
        path=(f"staging/managed-review/{RUN_ID}/{key}/analysis-input-{envelope_sha}.json"),
        sha=envelope_sha,
        byte_count=len(envelope_bytes),
    )
    return TargetAnalysisBinding.create(
        target_key=key,
        analysis_set=context.analysis_set,
        target_result_sha256=target_result_sha256,
        inference_input=input_artifact,
    )


def test_target_analysis_v1_bytes_and_identity_remain_legacy_compatible() -> None:
    context = _context()
    key = "legacy-target-analysis"
    binding = _target_analysis(key, context)
    target_result_sha256 = hashlib.sha256(key.encode()).hexdigest()
    envelope = {
        "schema_version": 1,
        "target_key": key,
        "analysis_set_id": context.analysis_set.analysis_set_id,
        "analysis_set_sha256": context.analysis_set.analysis_set_sha256,
        "impact_result_sha256": context.analysis_set.impact_result_sha256,
        "target_result_sha256": target_result_sha256,
    }
    envelope_bytes = canonical_json_bytes(envelope)
    envelope_sha256 = hashlib.sha256(envelope_bytes).hexdigest()
    legacy_values = {
        **envelope,
        "inference_input": binding.inference_input.model_dump(mode="json"),
        "input_envelope_sha256": envelope_sha256,
    }
    expected_id = "mtargetanalysis:" + hashlib.sha256(
        canonical_json_bytes(legacy_values)
    ).hexdigest()
    expected_bytes = canonical_json_bytes(
        {"target_analysis_id": expected_id, **legacy_values}
    )

    assert binding.schema_version == 1
    assert binding.target_analysis_id == expected_id
    assert binding.staged_input_sha256 is None
    assert canonical_json_bytes(binding.model_dump(mode="json")) == expected_bytes
    assert b"staged_input_sha256" not in expected_bytes


def _receipt_pair(
    analysis: TargetAnalysisBinding,
    *,
    provider: str = "fixture-provider",
    output_sha: str = SHA_D,
) -> tuple[ContentAddressedInferenceReceipt, ContentAddressedInferenceReceipt]:
    live = ContentAddressedInferenceReceipt.create(
        contract_id="managed-revision",
        contract_version=1,
        mode=InferenceExecutionMode.LIVE,
        provider=provider,
        model="fixture-model",
        provider_request_id="provider-request:fixture",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=SHA_B,
        response_schema_sha256=SHA_C,
        input_artifacts=(analysis.inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output_sha,
        validated_output_sha256=output_sha,
        usage=InferenceUsage(
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    live_sha = hashlib.sha256(live_bytes).hexdigest()
    replay_receipt = _artifact(
        "trusted-replay-receipt",
        ManagedArtifactKind.INFERENCE_RECEIPT,
        path=f"receipts/inference/{live_sha}.json",
        sha=live_sha,
        byte_count=len(live_bytes),
    )
    replay = ContentAddressedInferenceReceipt.create(
        contract_id="managed-revision",
        contract_version=1,
        mode=InferenceExecutionMode.REPLAY,
        provider=provider,
        model="fixture-model",
        provider_request_id=None,
        replay_source_receipt_sha256=live_sha,
        replay_source_receipt_artifact=replay_receipt,
        prompt_sha256=SHA_B,
        response_schema_sha256=SHA_C,
        input_artifacts=(analysis.inference_input,),
        input_envelope_sha256=analysis.input_envelope_sha256,
        raw_output_sha256=output_sha,
        validated_output_sha256=output_sha,
        usage=InferenceUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd_micros=0,
            latency_ms=0,
        ),
    )
    replay.verify_replay_source(live)
    return live, replay


def _receipt(
    analysis: TargetAnalysisBinding,
    *,
    provider: str = "fixture-provider",
    output_sha: str = SHA_D,
) -> ContentAddressedInferenceReceipt:
    return _receipt_pair(analysis, provider=provider, output_sha=output_sha)[1]


def _plan(
    key: str,
    context: Context,
    *,
    raw_sha: str = SHA_C,
    note_sha: str = SHA_D,
    staging_label: str = "draft",
    provider: str = "fixture-provider",
    analysis_context: Context | None = None,
    effective_from: date = date(2031, 1, 1),
    hunk_count: int = 1,
    predecessor_raw_path: str | None = None,
    replacement_size: int = 3,
    rationale: str = "Propose one bounded create-only managed revision.",
) -> ManagedRevisionPlan:
    predecessor, predecessor_raw, predecessor_note = _predecessor(
        key, raw_path=predecessor_raw_path
    )
    proposed_raw = _artifact(
        f"{key}-proposed-raw",
        ManagedArtifactKind.RAW_SOURCE,
        path=(f"staging/managed-review/{RUN_ID}/{key}/raw-{raw_sha}.md"),
        sha=raw_sha,
        byte_count=200,
    )
    proposed_note = _artifact(
        f"{key}-proposed-note",
        ManagedArtifactKind.SOURCE_NOTE,
        path=(f"staging/managed-review/{RUN_ID}/{key}/note-{note_sha}.md"),
        sha=note_sha,
        byte_count=220,
    )
    raw_destination = PublicationDestination.create(
        target_key=key,
        kind=PublicationKind.RAW_SOURCE,
        expected_sha256=proposed_raw.sha256,
        expected_byte_count=proposed_raw.byte_count,
    )
    note_destination = PublicationDestination.create(
        target_key=key,
        kind=PublicationKind.SOURCE_NOTE,
        expected_sha256=proposed_note.sha256,
        expected_byte_count=proposed_note.byte_count,
    )
    successor = derive_managed_successor(
        predecessor=predecessor,
        target_key=key,
        proposed_raw=proposed_raw,
        raw_destination=raw_destination,
        effective_from=effective_from,
    )
    predecessor_claim = _claim(
        document=predecessor,
        note_path=predecessor_note.path,
        note_sha=predecessor_note.sha256,
        local_id=f"{key}-01",
        statement="The return window is thirty days.",
    )
    successor_claim = _claim(
        document=successor,
        note_path=note_destination.path,
        note_sha=proposed_note.sha256,
        local_id=f"{key}-managed-01",
        statement="The return window is thirty days.",
    )
    predecessor_projection = _projection(
        raw=predecessor_raw,
        note=predecessor_note,
        canonical_raw_path=predecessor_raw.path,
        canonical_note_path=predecessor_note.path,
        claims=(predecessor_claim,),
    )
    successor_projection = _projection(
        raw=proposed_raw,
        note=proposed_note,
        canonical_raw_path=raw_destination.path,
        canonical_note_path=note_destination.path,
        claims=(successor_claim,),
    )
    reconciliation = ClaimReconciliationBinding.create(
        predecessor_projection=predecessor_projection,
        successor_projection=successor_projection,
        entries=(
            ClaimReconciliationEntry(
                action=ClaimReconciliationAction.CARRIED_FORWARD,
                predecessor=predecessor_claim,
                successor=successor_claim,
            ),
        ),
    )
    analysis = _target_analysis(key, analysis_context or context)
    inference_input = analysis.inference_input
    citation = GroundedArtifactCitation.create(
        artifact=inference_input,
        start_byte=0,
        quote="policy evidence",
    )
    hunks = tuple(
        ManagedSemanticHunk.create(
            semantic_key=f"policy-{index:02d}",
            base_artifact=predecessor_raw,
            result_artifact=proposed_raw,
            start_byte=index * 4,
            before_text="old",
            replacement_text="n" * replacement_size,
            citations=(citation,),
        )
        for index in range(hunk_count)
    )
    patch = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=predecessor_raw,
        result_artifact=proposed_raw,
        hunks=hunks,
        complete_diff_sha256=SHA_F,
    )
    semantic = {
        "run_id": RUN_ID,
        "target_key": key,
        "predecessor": predecessor,
        "predecessor_raw": predecessor_raw,
        "predecessor_note": predecessor_note,
        "successor": successor,
        "proposed_raw": proposed_raw,
        "proposed_note": proposed_note,
        "raw_destination": raw_destination,
        "note_destination": note_destination,
        "analysis": analysis,
        "predecessor_projection": predecessor_projection,
        "successor_projection": successor_projection,
        "patch_attestation": patch,
        "claim_reconciliation": reconciliation,
        "rationale": rationale,
        "hunks": hunks,
    }
    output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    validated_output = _artifact(
        f"{key}-validated-output",
        ManagedArtifactKind.INFERENCE_OUTPUT,
        path=(f"staging/managed-review/{RUN_ID}/{key}/validated-output-{output_sha}.json"),
        sha=output_sha,
        byte_count=len(output_bytes),
    )
    receipt = _receipt(analysis, provider=provider, output_sha=output_sha)
    return ManagedRevisionPlan.create(
        **semantic,
        inference_receipt=receipt,
        validated_output=validated_output,
    )


def _negative(
    key: str,
    context: Context,
    *,
    raw_path: str | None = None,
    note_path: str | None = None,
    raw_sha: str = SHA_A,
) -> NoChangeImpactCard:
    predecessor, raw, note = _predecessor(
        key,
        raw_path=raw_path,
        note_path=note_path,
        raw_sha=raw_sha,
    )
    claim = _claim(
        document=predecessor,
        note_path=note.path,
        note_sha=note.sha256,
        local_id=f"{key}-01",
        statement="This policy remains unchanged after analysis.",
    )
    projection = _projection(
        raw=raw,
        note=note,
        canonical_raw_path=raw.path,
        canonical_note_path=note.path,
        claims=(claim,),
    )
    analysis = _target_analysis(key, context)
    inference_input = analysis.inference_input
    citation = GroundedArtifactCitation.create(
        artifact=inference_input, start_byte=0, quote="policy evidence"
    )
    semantic = {
        "run_id": RUN_ID,
        "target_key": key,
        "predecessor": predecessor,
        "predecessor_raw": raw,
        "predecessor_note": note,
        "predecessor_projection": projection,
        "analysis": analysis,
        "rationale": "Analysis found no justified managed revision for this target.",
        "citations": (citation,),
    }
    output_bytes = NoChangeImpactCard.proposal_output_bytes(**semantic)
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    validated_output = _artifact(
        f"{key}-validated-output",
        ManagedArtifactKind.INFERENCE_OUTPUT,
        path=(f"staging/managed-review/{RUN_ID}/{key}/validated-output-{output_sha}.json"),
        sha=output_sha,
        byte_count=len(output_bytes),
    )
    return NoChangeImpactCard.create(
        **semantic,
        inference_receipt=_receipt(analysis, output_sha=output_sha),
        validated_output=validated_output,
    )


def _bundle(
    context: Context,
    *subjects: ManagedRevisionPlan | NoChangeImpactCard,
) -> ManagedRevisionReviewBundle:
    return ManagedRevisionReviewBundle.create(
        run_binding=context.run,
        review_base=context.review_base,
        temporal_prerequisite=context.prerequisite,
        targets=tuple(ManagedRevisionReviewTarget.create(item) for item in subjects),
    )


def _request_record(
    bundle: ManagedRevisionReviewBundle,
) -> ManagedRevisionReviewRequestRecord:
    command = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id="managed-request:open",
        requester_id="requester@example.test",
        rationale="Open the exact managed revision bundle for human review.",
    )
    return ManagedRevisionReviewRequestRecord.create(
        command,
        requested_at="2032-02-03T04:05:06+00:00",
        committed_authority=bundle.review_base.authority,
    )


def _outcome(
    target: ManagedRevisionReviewTarget,
    disposition: ManagedRevisionDisposition,
    *,
    edited_plan: ManagedRevisionPlan | None = None,
) -> ManagedRevisionReviewOutcome:
    return ManagedRevisionReviewOutcome(
        target_id=target.target_id,
        original_target_sha256=target.target_sha256,
        disposition=disposition,
        edited_plan=edited_plan,
    )


def test_v1_bundle_decision_and_manifest_canonical_identities_are_frozen() -> None:
    context = _context()
    bundle = _bundle(
        context,
        _plan("frozen-v1-plan", context),
        _negative("frozen-v1-negative", context),
    )
    request = _request_record(bundle)
    by_key = {item.target_key: item for item in bundle.targets}
    decision = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:frozen-v1",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Freeze the exact legacy V1 decision representation.",
        items=(
            _outcome(
                by_key["frozen-v1-negative"],
                ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
            ),
            _outcome(
                by_key["frozen-v1-plan"],
                ManagedRevisionDisposition.APPROVE,
            ),
        ),
    )
    bundle_bytes = canonical_json_bytes(bundle.model_dump(mode="json"))
    decision_bytes = canonical_json_bytes(decision.model_dump(mode="json"))
    manifest_bytes = canonical_json_bytes(decision.generation_manifest.model_dump(mode="json"))

    assert bundle.bundle_id == (
        "mbundle:ddcb42db7290802eb7e6ffa947884a8424249c414cdbc37b3c9723401c96957a"
    )
    assert hashlib.sha256(bundle_bytes).hexdigest() == (
        "4e369d9fe341c6f0e116e6717529eda724505bdb2ce65ab4e20eae7f518d9d5e"
    )
    assert decision.decision_id == (
        "mdecision:a01a47b4b75c121db8a927f5c84db5e7688778d14350cf40ee7d10aa29179978"
    )
    assert hashlib.sha256(decision_bytes).hexdigest() == (
        "bf292b6ac0ace523a3b086f8828fc8146f19227067c55363303a6620dfa5be0b"
    )
    assert decision.generation_manifest.manifest_id == (
        "mgenerationmanifest:f8e248214d5a6a0f99840ce8a56d1e331b714c245693048e9d188cd88e201870"
    )
    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "5135091003eaa25ee40e7d21b5101ee0ef379f1f1741c12f7a84dc15eaefca1f"
    )
    assert b'"schema_version":1' in bundle_bytes
    assert b"governing_source_adoption" not in bundle_bytes
    assert b"governing_source_adoption" not in manifest_bytes


@pytest.mark.parametrize("schema_version", (None, 3, 2))
def test_run_discriminator_rejects_missing_unknown_or_mismatched_version(
    schema_version: int | None,
) -> None:
    context = _context()
    bundle = _bundle(context, _negative("run-discriminator", context))
    payload = bundle.model_dump(mode="json")
    if schema_version is None:
        del payload["run_binding"]["schema_version"]
    else:
        payload["run_binding"]["schema_version"] = schema_version
    with pytest.raises(ValidationError):
        ManagedRevisionReviewBundle.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("schema_version", (None, 3, 2))
def test_manifest_discriminator_rejects_missing_unknown_or_mismatched_version(
    schema_version: int | None,
) -> None:
    context = _context()
    bundle = _bundle(context, _negative("manifest-discriminator", context))
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:manifest-discriminator",
        request_record=_request_record(bundle),
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Exercise the exact persisted manifest discriminator.",
        items=(
            _outcome(
                bundle.targets[0],
                ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
            ),
        ),
    )
    payload = command.model_dump(mode="json")
    if schema_version is None:
        del payload["generation_manifest"]["schema_version"]
    else:
        payload["generation_manifest"]["schema_version"] = schema_version
    with pytest.raises(ValidationError):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(payload))


def _recreate_kwargs(model: ManagedRevisionPlan | NoChangeImpactCard) -> dict[str, object]:
    excluded = {
        "plan_id",
        "plan_sha256",
        "proposal_id",
        "proposal_sha256",
        "card_id",
        "card_sha256",
        "kind",
        "schema_version",
    }
    return {name: getattr(model, name) for name in type(model).model_fields if name not in excluded}


def _plan_with_changes(plan: ManagedRevisionPlan, **changes: object) -> ManagedRevisionPlan:
    semantic = _recreate_kwargs(plan)
    semantic.pop("inference_receipt")
    semantic.pop("validated_output")
    semantic.update(changes)
    output_bytes = ManagedRevisionPlan.proposal_output_bytes(**semantic)
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    validated_output = _artifact(
        "changed-validated-output",
        ManagedArtifactKind.INFERENCE_OUTPUT,
        path=(
            f"staging/managed-review/{plan.run_id}/{plan.target_key}/"
            f"validated-output-{output_sha}.json"
        ),
        sha=output_sha,
        byte_count=len(output_bytes),
    )
    analysis = semantic["analysis"]
    assert isinstance(analysis, TargetAnalysisBinding)
    return ManagedRevisionPlan.create(
        **semantic,
        inference_receipt=_receipt(analysis, output_sha=output_sha),
        validated_output=validated_output,
    )


def _content_id(prefix: str, payload: object) -> str:
    return f"{prefix}:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def _rehash_decision_payload(payload: dict[str, object]) -> dict[str, object]:
    manifest = payload["generation_manifest"]
    assert isinstance(manifest, dict)
    manifest_identity = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifest_id", "authorized_generation"}
    }
    manifest["manifest_id"] = _content_id("mgenerationmanifest", manifest_identity)

    activation = payload.get("activation_plan")
    if isinstance(activation, dict):
        activation_identity = {
            key: value for key, value in activation.items() if key != "activation_plan_id"
        }
        activation["activation_plan_id"] = _content_id("mauthorityplan", activation_identity)

    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"decision_id", "decision_payload_sha256", "operation_id"}
    }
    payload["decision_id"] = _content_id("mdecision", identity)
    payload["decision_payload_sha256"] = hashlib.sha256(
        canonical_json_bytes({**identity, "operation_id": payload["operation_id"]})
    ).hexdigest()
    return payload


def _rehash_authority_payload(payload: dict[str, object]) -> dict[str, object]:
    pointer = {
        key: value
        for key, value in payload.items()
        if key not in {"authority_id", "active_pointer_sha256"}
    }
    pointer_sha = hashlib.sha256(canonical_json_bytes(pointer)).hexdigest()
    payload["active_pointer_sha256"] = pointer_sha
    payload["authority_id"] = _content_id(
        "mauthority", {**pointer, "active_pointer_sha256": pointer_sha}
    )
    return payload


def test_three_heads_authority_and_generation_are_distinct() -> None:
    context = _context()
    bundle = _bundle(context, _plan("returns-policy", context))
    assert context.run.prechange_head.revision == 1
    assert context.run.analysis_head.revision == 2
    assert bundle.review_base.review_open_head.revision == 3
    assert bundle.temporal_prerequisite.review_open_head == bundle.review_base.review_open_head
    assert bundle.temporal_prerequisite.review_open_head not in {
        context.run.prechange_head,
        context.run.analysis_head,
    }
    assert context.review_base.authority.authority_revision == 0
    assert context.review_base.authority.active_generation.generation_number == 0
    assert context.review_base.authority.active_generation.generation_id.startswith("mgeneration:")

    wrong = context.prerequisite.model_copy(update={"review_open_head": context.run.analysis_head})
    with pytest.raises(ValidationError, match="review-open head"):
        ManagedRevisionReviewBundle.create(
            run_binding=context.run,
            review_base=context.review_base,
            temporal_prerequisite=wrong,
            targets=bundle.targets,
        )


def test_authority_contracts_reject_python_side_type_coercion() -> None:
    head = AggregateHeadBinding.create(
        aggregate_id="larkstead.sl2-returns",
        revision=1,
        aggregate_sha256=SHA_A,
    )
    tampered = head.model_dump()
    tampered["revision"] = "1"
    with pytest.raises(ValidationError, match="valid integer"):
        AggregateHeadBinding.model_validate(tampered)


def test_every_subject_binds_exact_analysis_and_edits_preserve_it() -> None:
    context = _context()
    other_context = _context(impact_sha=SHA_F)
    rebound = _plan("analysis-target", context, analysis_context=other_context)
    with pytest.raises(ValidationError, match="exact analysis set"):
        _bundle(context, rebound)

    original = _plan("edited-analysis", context)
    edited = _plan(
        "edited-analysis",
        context,
        raw_sha=SHA_E,
        note_sha=SHA_F,
        staging_label="edited",
        analysis_context=other_context,
    )
    bundle = _bundle(context, original)
    target = bundle.targets[0]
    record = _request_record(bundle)
    with pytest.raises(ValidationError, match="preserve run, analysis"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:bad-analysis-edit",
            request_record=record,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Reject an edited plan rebound to arbitrary analysis hashes.",
            items=(
                _outcome(
                    target,
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=edited,
                ),
            ),
        )


def test_no_change_predecessors_participate_in_same_path_collision_checks() -> None:
    context = _context()
    first = _negative("first-target", context)
    second = _negative(
        "second-target",
        context,
        raw_path=first.predecessor_raw.path,
        raw_sha=SHA_F,
    )
    assert first.predecessor_raw.sha256 != second.predecessor_raw.sha256
    with pytest.raises(ValidationError, match="artifact locator path cannot bind conflicting"):
        _bundle(context, first, second)

    exact_reuse = _negative(
        "exact-reuse-target",
        context,
        raw_path=first.predecessor_raw.path,
        raw_sha=first.predecessor_raw.sha256,
    )
    shared = _bundle(context, first, exact_reuse)
    assert shared.targets[0].subject.predecessor_raw == shared.targets[1].subject.predecessor_raw


def test_replay_receipt_rejects_conflicting_content_addressed_locator() -> None:
    context = _context()
    first = _negative("receipt-locator-first", context)
    second = _negative("receipt-locator-second", context)
    conflicting_replay = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_RECEIPT,
        path=first.inference_receipt.replay_source_receipt_artifact.path,
        sha256=SHA_B,
        byte_count=128,
    )
    with pytest.raises(ValueError, match="content-addressed locator"):
        ContentAddressedInferenceReceipt.create(
            contract_id="managed-revision",
            contract_version=1,
            mode=InferenceExecutionMode.REPLAY,
            provider="fixture-provider",
            model="fixture-model",
            provider_request_id=None,
            replay_source_receipt_sha256=SHA_B,
            replay_source_receipt_artifact=conflicting_replay,
            prompt_sha256=SHA_B,
            response_schema_sha256=SHA_C,
            input_artifacts=second.inference_receipt.input_artifacts,
            input_envelope_sha256=second.analysis.input_envelope_sha256,
            raw_output_sha256=second.validated_output.sha256,
            validated_output_sha256=second.validated_output.sha256,
            usage=second.inference_receipt.usage,
        )


@pytest.mark.parametrize(
    "path",
    (
        " staging/managed-review/run-alpha/target/file.md",
        "staging/managed-review/run-alpha/target/file.md ",
        "staging/managed-review/run-alpha/target/internal space.md",
        "staging/managed-review/run-alpha/target/internal\tspace.md",
        "staging/managed-review/run-alpha/target/internal\nspace.md",
        "staging/managed-review/run-alpha/target/control\u200bspace.md",
        r"staging\managed-review\run-alpha\target\file.md",
        "../escape.md",
        "/absolute.md",
    ),
)
def test_paths_reject_whitespace_backslashes_and_escapes(path: str) -> None:
    with pytest.raises(ValueError):
        _artifact("unsafe", ManagedArtifactKind.RAW_SOURCE, path=path)


def test_attacker_controlled_path_key_and_claim_text_preflights() -> None:
    exact_artifact = _artifact(
        "exact-artifact-bytes",
        ManagedArtifactKind.RAW_SOURCE,
        byte_count=MAX_MANAGED_ARTIFACT_BYTES_V1,
    )
    assert exact_artifact.byte_count == MAX_MANAGED_ARTIFACT_BYTES_V1
    with pytest.raises(ValueError):
        _artifact(
            "oversized-artifact-bytes",
            ManagedArtifactKind.RAW_SOURCE,
            byte_count=MAX_MANAGED_ARTIFACT_BYTES_V1 + 1,
        )

    exact_path = "p" * 1021 + ".md"
    assert (
        len(_artifact("exact-path", ManagedArtifactKind.RAW_SOURCE, path=exact_path).path) == 1024
    )
    assert managed_review_module._exact_logical_key("k" * 512, label="boundary") == "k" * 512
    with pytest.raises(ValueError, match="MAX_MANAGED_PATH_BYTES_V1"):
        _artifact(
            "oversized-path",
            ManagedArtifactKind.RAW_SOURCE,
            path=f"fixtures/{'p' * 1024}.md",
        )
    with pytest.raises(ValueError, match="MAX_MANAGED_LOGICAL_KEY_BYTES_V1"):
        PublicationDestination.create(
            target_key="k" * 513,
            kind=PublicationKind.RAW_SOURCE,
            expected_sha256=SHA_A,
            expected_byte_count=1,
        )

    plan = _plan("claim-text-preflight", _context())
    exact_claim = _claim(
        document=plan.predecessor,
        note_path=plan.predecessor_note.path,
        note_sha=plan.predecessor_note.sha256,
        local_id="exact-claim-01",
        statement="c" * (64 * 1024),
    )
    exact_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=plan.predecessor_raw,
        note_artifact=plan.predecessor_note,
        canonical_raw_path=plan.predecessor_raw.path,
        canonical_note_path=plan.predecessor_note.path,
        validator_version="source-note-v1",
        source_note_schema_sha256=SHA_C,
        validator_result_sha256=SHA_D,
        projected_claims=(exact_claim,),
    )
    assert len(exact_projection.projected_claims[0].statement.encode("utf-8")) == 64 * 1024
    oversized_claim = _claim(
        document=plan.predecessor,
        note_path=plan.predecessor_note.path,
        note_sha=plan.predecessor_note.sha256,
        local_id="oversized-claim-01",
        statement="c" * (64 * 1024 + 1),
    )
    with pytest.raises(ValueError, match="MAX_MANAGED_CLAIM_TEXT_BYTES_V1"):
        SourceNoteProjectionBinding.create_from_validator_output(
            raw_artifact=plan.predecessor_raw,
            note_artifact=plan.predecessor_note,
            canonical_raw_path=plan.predecessor_raw.path,
            canonical_note_path=plan.predecessor_note.path,
            validator_version="source-note-v1",
            source_note_schema_sha256=SHA_C,
            validator_result_sha256=SHA_D,
            projected_claims=(oversized_claim,),
        )


@pytest.mark.parametrize(
    "invalid_byte_count",
    (0, MAX_MANAGED_ARTIFACT_BYTES_V1 + 1, False, True),
)
def test_declared_artifact_bytes_reject_before_identity_hashing(
    invalid_byte_count: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path="fixtures/exact-prehash-artifact.md",
        sha256=SHA_A,
        byte_count=MAX_MANAGED_ARTIFACT_BYTES_V1,
    )
    exact_destination = PublicationDestination.create(
        target_key="exact-prehash-destination",
        kind=PublicationKind.RAW_SOURCE,
        expected_sha256=SHA_A,
        expected_byte_count=MAX_MANAGED_ARTIFACT_BYTES_V1,
    )
    assert exact_artifact.byte_count == MAX_MANAGED_ARTIFACT_BYTES_V1
    assert exact_destination.expected_byte_count == MAX_MANAGED_ARTIFACT_BYTES_V1

    def fail_hash(_payload: object) -> str:
        raise AssertionError("identity hashing ran")

    monkeypatch.setattr(managed_review_module, "_sha256", fail_hash)
    with pytest.raises(ValueError, match="non-bool integer"):
        ManagedArtifactRef.create(
            kind=ManagedArtifactKind.RAW_SOURCE,
            path="fixtures/invalid-prehash-artifact.md",
            sha256=SHA_A,
            byte_count=invalid_byte_count,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-bool integer"):
        PublicationDestination.create(
            target_key="invalid-prehash-destination",
            kind=PublicationKind.RAW_SOURCE,
            expected_sha256=SHA_A,
            expected_byte_count=invalid_byte_count,  # type: ignore[arg-type]
        )


def test_staging_is_separate_from_full_sha_publication_destination() -> None:
    context = _context()
    plan = _plan("staged-target", context)
    assert plan.proposed_raw.path.startswith("staging/managed-review/run-alpha/staged-target/")
    assert plan.raw_destination.path == (
        f"managed_sources/staged-target/staged-target-{plan.proposed_raw.sha256}.md"
    )
    assert plan.note_destination.path == (
        f"vault/staged-target/staged-target-{plan.proposed_note.sha256}.md"
    )
    assert plan.successor.source_path == plan.raw_destination.path
    assert plan.raw_destination.expected_byte_count == plan.proposed_raw.byte_count

    bad = plan.model_dump(mode="json")
    bad["proposed_raw"] = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=plan.raw_destination.path,
        sha256=plan.proposed_raw.sha256,
        byte_count=plan.proposed_raw.byte_count,
    ).model_dump(mode="json")
    with pytest.raises(ValidationError, match="staging root"):
        ManagedRevisionPlan.model_validate_json(json.dumps(bad))


def test_predecessor_cannot_alias_managed_review_staging() -> None:
    with pytest.raises(ValidationError, match="cannot resolve from managed-review staging"):
        _plan(
            "predecessor-alias",
            _context(),
            predecessor_raw_path=(
                f"staging/managed-review/{RUN_ID}/predecessor-alias/raw-{SHA_C}.md"
            ),
        )


@pytest.mark.parametrize(
    ("destination_kind", "artifact_kind"),
    (
        (PublicationKind.RAW_SOURCE, ManagedArtifactKind.SOURCE_NOTE),
        (PublicationKind.SOURCE_NOTE, ManagedArtifactKind.RAW_SOURCE),
    ),
)
def test_generation_publication_rejects_artifact_kind_swap(
    destination_kind: PublicationKind,
    artifact_kind: ManagedArtifactKind,
) -> None:
    artifact = _artifact("kind-swap", artifact_kind, sha=SHA_E)
    destination = PublicationDestination.create(
        target_key="kind-swap",
        kind=destination_kind,
        expected_sha256=artifact.sha256,
        expected_byte_count=artifact.byte_count,
    )
    with pytest.raises(ValidationError, match="exact staged SHA/bytes"):
        GenerationPublicationBinding(
            target_key="kind-swap",
            staged_artifact=artifact,
            destination=destination,
        )


def test_generation_manifest_rejects_duplicate_target_kind_overrides() -> None:
    publications: list[GenerationPublicationBinding] = []
    for sha in (SHA_E, SHA_F):
        artifact = _artifact(
            f"ambiguous-{sha[0]}",
            ManagedArtifactKind.RAW_SOURCE,
            path=f"staging/managed-review/{RUN_ID}/ambiguous/raw-{sha}.md",
            sha=sha,
        )
        destination = PublicationDestination.create(
            target_key="ambiguous",
            kind=PublicationKind.RAW_SOURCE,
            expected_sha256=sha,
            expected_byte_count=artifact.byte_count,
        )
        publications.append(
            GenerationPublicationBinding(
                target_key="ambiguous",
                staged_artifact=artifact,
                destination=destination,
            )
        )
    with pytest.raises(ValueError, match="unique by target and kind"):
        ManagedGenerationManifestBinding.create(
            request_id="mrequest:" + "1" * 64,
            bundle_id="mbundle:" + "2" * 64,
            prior_generation=_context().review_base.authority.active_generation,
            publications=tuple(publications),
            retained_review_target_keys=(),
        )


def test_byte_attestations_derive_hashes_and_cover_complete_diff() -> None:
    plan = _plan("attested-target", _context(), hunk_count=2)
    first = plan.hunks[0]
    assert first.before_sha256 == hashlib.sha256(first.before_text.encode()).hexdigest()
    assert first.replacement_sha256 == hashlib.sha256(first.replacement_text.encode()).hexdigest()
    assert plan.patch_attestation.ordered_hunk_ids == tuple(item.hunk_id for item in plan.hunks)
    assert plan.patch_attestation.complete_diff_hunk_ids == (
        plan.patch_attestation.ordered_hunk_ids
    )
    assert plan.patch_attestation.ordered_citation_ids == tuple(
        citation.citation_id for hunk in plan.hunks for citation in hunk.citations
    )
    assert plan.patch_attestation.uncovered_diff_byte_count == 0
    assert plan.patch_attestation.store_revalidation_required is True
    assert plan.successor_projection.store_revalidation_required is True

    tampered = plan.patch_attestation.model_dump(mode="json")
    tampered["complete_diff_hunk_ids"] = tampered["complete_diff_hunk_ids"][:-1]
    with pytest.raises(ValidationError, match="complete raw diff"):
        PatchReconstructionAttestation.model_validate_json(json.dumps(tampered))


def test_plan_and_no_change_reject_citation_sha_and_bounds_mismatches() -> None:
    context = _context()
    plan = _plan("citation-plan", context)
    citation = plan.hunks[0].citations[0]
    citation_values = citation.model_dump(mode="json", exclude={"citation_id"})
    citation_values["artifact_sha256"] = SHA_A
    wrong_sha = GroundedArtifactCitation.model_validate(
        {
            "citation_id": "mcitation:"
            + hashlib.sha256(canonical_json_bytes(citation_values)).hexdigest(),
            **citation_values,
        }
    )
    source_hunk = plan.hunks[0]
    wrong_hunk = ManagedSemanticHunk.create(
        semantic_key=source_hunk.semantic_key,
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        start_byte=source_hunk.start_byte,
        before_text=source_hunk.before_text,
        replacement_text=source_hunk.replacement_text,
        citations=(wrong_sha,),
    )
    wrong_patch = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        hunks=(wrong_hunk,),
        complete_diff_sha256=SHA_F,
    )
    with pytest.raises(ValidationError, match="citation must bind an inference input"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(plan),
                "hunks": (wrong_hunk,),
                "patch_attestation": wrong_patch,
            }
        )

    negative = _negative("citation-negative", context)
    citation_values = negative.citations[0].model_dump(mode="json", exclude={"citation_id"})
    citation_values["start_byte"] = negative.analysis.inference_input.byte_count
    citation_values["end_byte"] = citation_values["start_byte"] + len(
        citation_values["quote"].encode()
    )
    out_of_bounds = GroundedArtifactCitation.model_validate(
        {
            "citation_id": "mcitation:"
            + hashlib.sha256(canonical_json_bytes(citation_values)).hexdigest(),
            **citation_values,
        }
    )
    with pytest.raises(ValidationError, match="analysis/citations must bind exact input envelope"):
        NoChangeImpactCard.create(
            **{
                **_recreate_kwargs(negative),
                "citations": (out_of_bounds,),
            }
        )


def test_plan_rejects_patch_artifact_and_hunk_extent_mismatches() -> None:
    plan = _plan("patch-cross-binding", _context())
    patch_values = plan.patch_attestation.model_dump(mode="json", exclude={"attestation_id"})
    patch_values["base_sha256"] = SHA_F
    patch_values["hunk_program_sha256"] = hashlib.sha256(
        canonical_json_bytes(
            {
                "base_sha256": SHA_F,
                "hunk_ids": patch_values["ordered_hunk_ids"],
                "result_sha256": patch_values["result_sha256"],
            }
        )
    ).hexdigest()
    wrong_patch = PatchReconstructionAttestation.model_validate_json(
        json.dumps(
            {
                "attestation_id": "mpatch:"
                + hashlib.sha256(canonical_json_bytes(patch_values)).hexdigest(),
                **patch_values,
            }
        )
    )
    with pytest.raises(ValidationError, match="patch attestation must cover"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(plan),
                "patch_attestation": wrong_patch,
            }
        )

    oversized_hunk = ManagedSemanticHunk.create(
        semantic_key="oversized",
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        start_byte=0,
        before_text="x" * (plan.predecessor_raw.byte_count + 1),
        replacement_text="replacement",
        citations=plan.hunks[0].citations,
    )
    oversized_patch = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        hunks=(oversized_hunk,),
        complete_diff_sha256=SHA_F,
    )
    with pytest.raises(ValidationError, match="beyond predecessor raw bytes"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(plan),
                "hunks": (oversized_hunk,),
                "patch_attestation": oversized_patch,
            }
        )


def test_plan_and_no_change_require_exact_projection_paths_and_documents() -> None:
    context = _context()
    plan = _plan("projection-plan", context)
    wrong_path_projection = plan.predecessor_projection.model_copy(
        update={"canonical_note_path": "vault/unrelated.md"}
    )
    with pytest.raises(ValidationError, match="canonical note path|predecessor projection"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(plan),
                "predecessor_projection": wrong_path_projection,
            }
        )

    negative = _negative("projection-negative", context)
    foreign = _plan("projection-foreign", context).predecessor_projection.projected_claims[0]
    wrong_document_projection = negative.predecessor_projection.model_copy(
        update={"projected_claims": (foreign,)}
    )
    with pytest.raises(ValidationError, match="canonical raw path|another predecessor document"):
        NoChangeImpactCard.create(
            **{
                **_recreate_kwargs(negative),
                "predecessor_projection": wrong_document_projection,
            }
        )


def test_claim_reconciliation_binds_full_projection_sets_and_semantics() -> None:
    plan = _plan("claims-target", _context())
    reconciliation = plan.claim_reconciliation
    assert reconciliation.predecessor_revisions == (plan.predecessor_projection.projected_claims)
    assert reconciliation.successor_revisions == plan.successor_projection.projected_claims

    predecessor = reconciliation.predecessor_revisions[0]
    successor = reconciliation.successor_revisions[0]
    changed_successor = VersionedClaimRevision.create(
        document=successor.document,
        source=successor.source,
        statement="The return window is forty-five days.",
        declared_effective_from=successor.declared_effective_from,
        scopes=successor.scopes,
    )
    with pytest.raises(ValidationError, match="preserve statement and scopes"):
        ClaimReconciliationEntry(
            action=ClaimReconciliationAction.CARRIED_FORWARD,
            predecessor=predecessor,
            successor=changed_successor,
        )

    with pytest.raises(ValidationError, match="coverage must be nonempty"):
        ClaimReconciliationBinding.model_validate(
            {
                "schema_version": 1,
                "reconciliation_id": "mclaims:" + "0" * 64,
                "predecessor_projection_id": plan.predecessor_projection.projection_id,
                "successor_projection_id": plan.successor_projection.projection_id,
                "predecessor_revisions": reconciliation.predecessor_revisions,
                "successor_revisions": reconciliation.successor_revisions,
                "entries": (),
            }
        )


def test_empty_reconciliation_is_allowed_only_for_two_empty_projections() -> None:
    plan = _plan("empty-claims", _context())
    predecessor_projection = _projection(
        raw=plan.predecessor_raw,
        note=plan.predecessor_note,
        canonical_raw_path=plan.predecessor_raw.path,
        canonical_note_path=plan.predecessor_note.path,
        claims=(),
    )
    successor_projection = _projection(
        raw=plan.proposed_raw,
        note=plan.proposed_note,
        canonical_raw_path=plan.raw_destination.path,
        canonical_note_path=plan.note_destination.path,
        claims=(),
    )
    empty = ClaimReconciliationBinding.create(
        predecessor_projection=predecessor_projection,
        successor_projection=successor_projection,
        entries=(),
    )
    assert empty.entries == ()


def test_successor_version_identity_ignores_staging_note_and_inference_metadata() -> None:
    context = _context()
    first = _plan("stable-version", context)
    second = _plan(
        "stable-version",
        context,
        note_sha=SHA_F,
        staging_label="counterfactual-location",
        provider="counterfactual-provider",
    )
    assert first.proposed_raw.sha256 == second.proposed_raw.sha256
    assert first.proposed_raw.path == second.proposed_raw.path
    assert first.proposed_note.sha256 != second.proposed_note.sha256
    assert first.inference_receipt.provider != second.inference_receipt.provider
    assert first.successor.document_version_id == second.successor.document_version_id
    assert first.successor.version_label == second.successor.version_label
    assert first.plan_id != second.plan_id

    changed_raw = _plan(
        "stable-version",
        context,
        raw_sha=SHA_E,
        note_sha=SHA_F,
        staging_label="changed-raw",
    )
    assert changed_raw.successor.document_version_id != first.successor.document_version_id


def test_lifecycle_binds_request_authority_generation_manifest_receipt_and_view() -> None:
    context = _context()
    plan = _plan("lifecycle-target", context)
    negative = _negative("lifecycle-negative", context)
    bundle = _bundle(context, plan, negative)
    request_record = _request_record(bundle)
    request_receipt = ManagedRevisionReviewRequestReceipt.create(request_record, replayed=True)
    by_key = {target.target_key: target for target in bundle.targets}
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:lifecycle",
        request_record=request_record,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Publish the exact approved plan and confirm the negative impact card.",
        items=(
            _outcome(
                by_key["lifecycle-negative"],
                ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
            ),
            _outcome(
                by_key["lifecycle-target"],
                ManagedRevisionDisposition.APPROVE,
            ),
        ),
    )
    assert command.expected_authority == context.review_base.authority
    assert command.activation_plan is not None
    activation_plan = command.activation_plan
    assert activation_plan.authorized_authority_revision == 1
    assert activation_plan.status == "authorized-inactive-until-pr-b"
    assert activation_plan.authorized_generation == (
        command.generation_manifest.authorized_generation
    )
    assert command.generation_manifest.authorized_generation.manifest_sha256 == (
        command.generation_manifest.manifest_sha256
    )
    assert len(command.generation_manifest.publication_delta) == 2
    assert command.generation_manifest.retained_review_target_keys == ("lifecycle-negative",)
    assert command.generation_manifest.prior_manifest_sha256 == (
        context.review_base.authority.active_generation.manifest_sha256
    )

    record = ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:06:00+00:00")
    receipt = ManagedRevisionDecisionReceipt.create(record, replayed=True)
    view = ManagedRevisionReviewView.create(
        request_record=request_record,
        request_receipt=request_receipt,
        decision_record=record,
        receipt=receipt,
    )
    assert view.status == ManagedReviewLifecycleStatus.DECIDED
    assert request_receipt.replayed and receipt.replayed
    assert receipt.decision_committed is True
    assert receipt.generation_activated is False
    assert ManagedRevisionReviewView.model_validate_json(view.model_dump_json()) == view

    tampered = command.model_dump(mode="json")
    tampered["generation_manifest"]["manifest_sha256"] = SHA_A
    with pytest.raises(ValidationError, match="generation manifest"):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(tampered))


def test_replay_is_a_store_receipt_fact_not_caller_authored_intent() -> None:
    bundle = _bundle(_context(), _plan("replay-target", _context()))
    first = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id="managed-request:first",
        requester_id="requester@example.test",
        rationale="Open this exact bundle using the first delivery operation.",
    )
    second = ManagedRevisionReviewRequestCommand.create(
        bundle=bundle,
        operation_id="managed-request:second",
        requester_id="requester@example.test",
        rationale="Open this exact bundle using a distinct delivery operation.",
    )
    assert first.request_id == second.request_id
    assert first.request_payload_sha256 != second.request_payload_sha256
    record = ManagedRevisionReviewRequestRecord.create(
        first,
        requested_at="2032-02-03T04:05:06+00:00",
        committed_authority=bundle.review_base.authority,
    )
    original = ManagedRevisionReviewRequestReceipt.create(record, replayed=False)
    replay = ManagedRevisionReviewRequestReceipt.create(record, replayed=True)
    assert original.request_record_sha256 == replay.request_record_sha256
    assert original.request_id == replay.request_id
    assert original.receipt_id != replay.receipt_id
    assert "is_replay" not in ManagedRevisionReviewRequestCommand.model_fields
    assert "is_replay" not in ManagedRevisionDecisionCommand.model_fields


@pytest.mark.parametrize("accepted_no_change", (False, True))
def test_zero_override_decision_does_not_advance_generation_or_authority(
    accepted_no_change: bool,
) -> None:
    context = _context()
    negative = _negative("no-op-target", context)
    bundle = _bundle(context, negative)
    request = _request_record(bundle)
    command = ManagedRevisionDecisionCommand.create(
        operation_id=f"managed-decision:no-op-{accepted_no_change}",
        request_record=request,
        bundle_outcome=(
            ManagedBundleOutcome.ACCEPTED if accepted_no_change else ManagedBundleOutcome.REJECTED
        ),
        reviewer_id="reviewer@example.test",
        rationale="Record a complete no-op review without activating a new generation.",
        items=(
            _outcome(
                bundle.targets[0],
                (
                    ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                    if accepted_no_change
                    else ManagedRevisionDisposition.REJECT
                ),
            ),
        ),
    )
    manifest = command.generation_manifest
    assert manifest.publication_delta == ()
    assert manifest.retained_review_target_keys == ("no-op-target",)
    assert manifest.requires_activation is False
    assert (
        manifest.manifest_sha256 == context.review_base.authority.active_generation.manifest_sha256
    )
    assert manifest.authorized_generation == context.review_base.authority.active_generation
    assert command.activation_plan is None


@pytest.mark.parametrize("active", (True, False))
def test_rehashed_decision_rejects_non_successor_generation_number(active: bool) -> None:
    context = _context()
    subject = _plan("generation-step", context) if active else _negative("generation-step", context)
    bundle = _bundle(context, subject)
    disposition = (
        ManagedRevisionDisposition.APPROVE
        if active
        else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
    )
    command = ManagedRevisionDecisionCommand.create(
        operation_id=f"managed-decision:generation-step-{active}",
        request_record=_request_record(bundle),
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Bind the authorized generation to the exact prior generation step.",
        items=(_outcome(bundle.targets[0], disposition),),
    )
    tampered = command.model_dump(mode="json")
    manifest = tampered["generation_manifest"]
    assert isinstance(manifest, dict)
    wrong_generation = ContentAddressedGenerationBinding.create(
        generation_number=999,
        manifest_sha256=str(manifest["manifest_sha256"]),
    )
    manifest["generation_number"] = 999
    manifest["authorized_generation"] = wrong_generation.model_dump(mode="json")
    activation = tampered.get("activation_plan")
    if isinstance(activation, dict):
        activation["authorized_generation"] = wrong_generation.model_dump(mode="json")
    _rehash_decision_payload(tampered)
    expected = (
        "activating generation must be exactly the next generation"
        if active
        else "no-op manifest must retain the exact prior generation"
    )
    with pytest.raises(ValidationError, match=expected):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(tampered))


def test_generation_manifest_retained_keys_fail_closed_at_exact_limit() -> None:
    prior = _context().review_base.authority.active_generation
    retained = tuple(f"retained-{index:02d}" for index in range(MAX_MANAGED_TARGETS_V1))
    manifest = ManagedGenerationManifestBinding.create(
        request_id="mrequest:" + "1" * 64,
        bundle_id="mbundle:" + "2" * 64,
        prior_generation=prior,
        publications=(),
        retained_review_target_keys=retained,
    )
    assert manifest.retained_review_target_keys == retained
    with pytest.raises(ValueError, match="retained review target keys count"):
        ManagedGenerationManifestBinding.create(
            request_id="mrequest:" + "1" * 64,
            bundle_id="mbundle:" + "2" * 64,
            prior_generation=prior,
            publications=(),
            retained_review_target_keys=retained + ("retained-16",),
        )
    with pytest.raises(ValueError, match="retained review target keys count"):
        ManagedGenerationManifestBinding.create(
            request_id="mrequest:" + "1" * 64,
            bundle_id="mbundle:" + "2" * 64,
            prior_generation=prior,
            publications=(),
            retained_review_target_keys=("same-retained",) * 17,
        )


def test_edited_plan_preserves_interval_and_all_target_atomicity() -> None:
    context = _context()
    original = _plan("editable-target", context)
    edited = _plan(
        "editable-target",
        context,
        raw_sha=SHA_E,
        note_sha=SHA_F,
        staging_label="edited",
    )
    bundle = _bundle(context, original)
    target = bundle.targets[0]
    request = _request_record(bundle)
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:edit",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Apply one bounded human correction without changing the interval.",
        items=(
            _outcome(
                target,
                ManagedRevisionDisposition.EDIT,
                edited_plan=edited,
            ),
        ),
    )
    assert command.generation_manifest.publication_delta[0].target_key == "editable-target"

    changed_interval = _plan(
        "editable-target",
        context,
        raw_sha=SHA_E,
        note_sha=SHA_F,
        staging_label="changed-interval",
        effective_from=date(2032, 1, 1),
    )
    with pytest.raises(ValidationError, match="preserve run, analysis"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:bad-interval",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="An edit cannot change the reviewed effective interval.",
            items=(
                _outcome(
                    target,
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=changed_interval,
                ),
            ),
        )


def test_final_edit_checks_no_change_predecessor_path_collisions() -> None:
    context = _context()
    original = _plan("collision-owner", context)
    edited = _plan(
        "collision-owner",
        context,
        raw_sha=SHA_E,
        note_sha=SHA_F,
        staging_label="collision-edit",
    )
    retained = _negative(
        "retained-negative",
        context,
        raw_path=edited.raw_destination.path,
        raw_sha=SHA_A,
    )
    bundle = _bundle(context, original, retained)
    request = _request_record(bundle)
    by_key = {item.target_key: item for item in bundle.targets}
    with pytest.raises(ValidationError, match="collide.*predecessor"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:final-collision",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="An edited destination cannot replace a retained negative predecessor.",
            items=(
                _outcome(
                    by_key["collision-owner"],
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=edited,
                ),
                _outcome(
                    by_key["retained-negative"],
                    ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
                ),
            ),
        )

    second = _negative("atomic-negative", context)
    multi_bundle = _bundle(context, original, second)
    multi_request = _request_record(multi_bundle)
    original_target = next(
        item for item in multi_bundle.targets if item.target_key == original.target_key
    )
    with pytest.raises(ValidationError, match="exactly one outcome"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:missing",
            request_record=multi_request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Omitting a target must fail the atomic decision boundary.",
            items=(_outcome(original_target, ManagedRevisionDisposition.APPROVE),),
        )


def test_reconciliation_accepts_exact_512_and_rejects_513_before_identity() -> None:
    plan = _plan("bounded-target", _context())
    predecessor_claims = tuple(
        _claim(
            document=plan.predecessor,
            note_path=plan.predecessor_note.path,
            note_sha=plan.predecessor_note.sha256,
            local_id=f"retired-{index:03d}-01",
            statement=f"Retired policy statement {index}.",
        )
        for index in range(256)
    )
    successor_claims = tuple(
        _claim(
            document=plan.successor,
            note_path=plan.note_destination.path,
            note_sha=plan.proposed_note.sha256,
            local_id=f"added-{index:03d}-01",
            statement=f"Added policy statement {index}.",
        )
        for index in range(256)
    )
    predecessor_projection = _projection(
        raw=plan.predecessor_raw,
        note=plan.predecessor_note,
        canonical_raw_path=plan.predecessor_raw.path,
        canonical_note_path=plan.predecessor_note.path,
        claims=predecessor_claims,
    )
    assert len(predecessor_projection.projected_claims) == 256
    with pytest.raises(ValueError, match="projected claims count"):
        _projection(
            raw=plan.predecessor_raw,
            note=plan.predecessor_note,
            canonical_raw_path=plan.predecessor_raw.path,
            canonical_note_path=plan.predecessor_note.path,
            claims=predecessor_claims + (predecessor_claims[0],),
        )
    successor_projection = _projection(
        raw=plan.proposed_raw,
        note=plan.proposed_note,
        canonical_raw_path=plan.raw_destination.path,
        canonical_note_path=plan.note_destination.path,
        claims=successor_claims,
    )
    entries = tuple(
        ClaimReconciliationEntry(
            action=ClaimReconciliationAction.RETIRED,
            predecessor=claim,
        )
        for claim in predecessor_claims
    ) + tuple(
        ClaimReconciliationEntry(
            action=ClaimReconciliationAction.ADDED,
            successor=claim,
        )
        for claim in successor_claims
    )
    exact = ClaimReconciliationBinding.create(
        predecessor_projection=predecessor_projection,
        successor_projection=successor_projection,
        entries=entries,
    )
    assert len(exact.entries) == MAX_MANAGED_RECONCILIATION_ENTRIES_V1

    reconciliation = exact.model_dump(mode="json")
    reconciliation["entries"].append(reconciliation["entries"][0])
    with pytest.raises(ValidationError) as reconciliation_error:
        ClaimReconciliationBinding.model_validate_json(json.dumps(reconciliation))
    assert reconciliation_error.value.errors()[0]["loc"] == ()
    assert "entries exceed managed count limit" in reconciliation_error.value.errors()[0]["msg"]
    with pytest.raises(ValueError, match="claim reconciliation entries count"):
        ClaimReconciliationBinding.create(
            predecessor_projection=predecessor_projection,
            successor_projection=successor_projection,
            entries=exact.entries + (exact.entries[0],),
        )


def test_bundle_canonical_byte_gate_accepts_1_mib_and_rejects_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    exact_plans = tuple(
        _plan(
            f"budget-{index}",
            context,
            hunk_count=16,
            replacement_size=13_800,
            rationale=(
                "x" * 93 if index == 0 else "Propose one bounded create-only managed revision."
            ),
        )
        for index in range(4)
    )
    exact = _bundle(context, *exact_plans)
    assert len(canonical_json_bytes(exact.model_dump(mode="json"))) == (
        MAX_MANAGED_BUNDLE_CANONICAL_BYTES_V1
    )
    oversized_plans = (
        _plan(
            "budget-0",
            context,
            hunk_count=16,
            replacement_size=13_800,
            rationale="x" * 94,
        ),
        *exact_plans[1:],
    )
    oversized_targets = tuple(ManagedRevisionReviewTarget.create(plan) for plan in oversized_plans)
    monkeypatch.setattr(
        managed_review_module,
        "_sha256",
        lambda _payload: (_ for _ in ()).throw(AssertionError("identity hashing ran")),
    )
    with pytest.raises(ValueError, match="before identity hashing"):
        ManagedRevisionReviewBundle.create(
            run_binding=context.run,
            review_base=context.review_base,
            temporal_prerequisite=context.prerequisite,
            targets=oversized_targets,
        )


def test_decision_canonical_byte_gate_accepts_1_mib_and_rejects_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    plans = tuple(
        _plan(
            f"decbudget-{index}",
            context,
            hunk_count=16,
            replacement_size=13_480,
        )
        for index in range(4)
    )
    bundle = _bundle(context, *plans)
    request = _request_record(bundle)
    by_key = {target.target_key: target for target in bundle.targets}
    items = tuple(
        _outcome(
            by_key[f"decbudget-{index}"],
            ManagedRevisionDisposition.APPROVE,
        )
        for index in range(4)
    )
    exact = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:budget",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="x" * 578,
        items=items,
    )
    assert len(canonical_json_bytes(exact.model_dump(mode="json"))) == (
        MAX_MANAGED_DECISION_CANONICAL_BYTES_V1
    )
    monkeypatch.setattr(
        managed_review_module,
        "_sha256",
        lambda _payload: (_ for _ in ()).throw(AssertionError("identity hashing ran")),
    )
    with pytest.raises(ValueError, match="before identity hashing"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:budget",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="x" * 579,
            items=items,
        )


def test_plan_and_hunk_aggregate_limits_reject_before_identity_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    nine_plan_targets = tuple(
        ManagedRevisionReviewTarget.create(_plan(f"plan-limit-{index}", context))
        for index in range(9)
    )
    sixty_five_hunk_targets = tuple(
        ManagedRevisionReviewTarget.create(
            _plan(
                f"hunk-limit-{index}",
                context,
                hunk_count=(16 if index < 4 else 1),
            )
        )
        for index in range(5)
    )
    seventeen_targets = tuple(
        ManagedRevisionReviewTarget.create(_negative(f"target-limit-{index}", context))
        for index in range(17)
    )
    exact_plan_bundle = ManagedRevisionReviewBundle.create(
        run_binding=context.run,
        review_base=context.review_base,
        temporal_prerequisite=context.prerequisite,
        targets=nine_plan_targets[:8],
    )
    assert (
        sum(isinstance(item.subject, ManagedRevisionPlan) for item in exact_plan_bundle.targets)
        == 8
    )
    exact_hunk_bundle = ManagedRevisionReviewBundle.create(
        run_binding=context.run,
        review_base=context.review_base,
        temporal_prerequisite=context.prerequisite,
        targets=sixty_five_hunk_targets[:4],
    )
    assert (
        sum(
            len(item.subject.hunks)
            for item in exact_hunk_bundle.targets
            if isinstance(item.subject, ManagedRevisionPlan)
        )
        == 64
    )
    exact_target_bundle = ManagedRevisionReviewBundle.create(
        run_binding=context.run,
        review_base=context.review_base,
        temporal_prerequisite=context.prerequisite,
        targets=seventeen_targets[:16],
    )
    assert len(exact_target_bundle.targets) == 16
    publication_plans = tuple(
        target.subject
        for target in nine_plan_targets
        if isinstance(target.subject, ManagedRevisionPlan)
    )
    exact_publications = tuple(
        GenerationPublicationBinding(
            target_key=plan.target_key,
            staged_artifact=artifact,
            destination=destination,
        )
        for plan in publication_plans[:8]
        for artifact, destination in (
            (plan.proposed_raw, plan.raw_destination),
            (plan.proposed_note, plan.note_destination),
        )
    )
    exact_manifest = ManagedGenerationManifestBinding.create(
        request_id="mrequest:" + "1" * 64,
        bundle_id="mbundle:" + "2" * 64,
        prior_generation=context.review_base.authority.active_generation,
        publications=exact_publications,
        retained_review_target_keys=(),
    )
    assert len(exact_manifest.publication_delta) == 16
    ninth = publication_plans[8]
    seventeenth_publication = GenerationPublicationBinding(
        target_key=ninth.target_key,
        staged_artifact=ninth.proposed_raw,
        destination=ninth.raw_destination,
    )
    with pytest.raises(ValueError, match="generation publications count"):
        ManagedGenerationManifestBinding.create(
            request_id="mrequest:" + "1" * 64,
            bundle_id="mbundle:" + "2" * 64,
            prior_generation=context.review_base.authority.active_generation,
            publications=exact_publications + (seventeenth_publication,),
            retained_review_target_keys=(),
        )
    with pytest.raises(ValueError, match="attested hunks count"):
        _plan("per-plan-hunk-limit", context, hunk_count=17)

    def fail_hash(_payload: object) -> str:
        raise AssertionError("identity hashing ran")

    monkeypatch.setattr(managed_review_module, "_sha256", fail_hash)
    common = {
        "run_binding": context.run,
        "review_base": context.review_base,
        "temporal_prerequisite": context.prerequisite,
    }
    with pytest.raises(ValueError, match="revision plan limit"):
        ManagedRevisionReviewBundle.create(**common, targets=nine_plan_targets)
    with pytest.raises(ValueError, match="total semantic hunk limit"):
        ManagedRevisionReviewBundle.create(**common, targets=sixty_five_hunk_targets)
    with pytest.raises(ValueError, match="review targets count"):
        ManagedRevisionReviewBundle.create(**common, targets=seventeen_targets)


def test_final_edited_hunk_limit_rejects_before_generation_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    original_plans = tuple(
        _plan(f"edited-hunk-limit-{index}", context, hunk_count=8) for index in range(8)
    )
    bundle = _bundle(context, *original_plans)
    request = _request_record(bundle)
    by_key = {target.target_key: target for target in bundle.targets}
    edited = _plan("edited-hunk-limit-0", context, hunk_count=9)
    items = tuple(
        _outcome(
            by_key[f"edited-hunk-limit-{index}"],
            (ManagedRevisionDisposition.EDIT if index == 0 else ManagedRevisionDisposition.APPROVE),
            edited_plan=edited if index == 0 else None,
        )
        for index in range(8)
    )

    def fail_hash(_payload: object) -> str:
        raise AssertionError("generation hashing ran")

    monkeypatch.setattr(managed_review_module, "_sha256", fail_hash)
    with pytest.raises(ValueError, match="aggregate semantic hunk limit"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:edited-hunk-limit",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Reject an oversized final edited-plan aggregate.",
            items=items,
        )


def test_analysis_changed_claim_bound_is_enforced() -> None:
    assert MAX_MANAGED_CHANGED_CLAIMS_V1 == 16
    analysis = _analysis_set()
    assert (
        analysis.changed_claim_revision_ids
        == analysis.analysis_bootstrap.changed_claim_revision_ids
    )

    too_many = tuple(f"claimrev:{index:064x}" for index in range(17))
    tampered = analysis.model_dump(mode="json")
    tampered["changed_claim_revision_ids"] = too_many
    with pytest.raises(ValidationError):
        ManagedAnalysisSetBinding.model_validate_json(json.dumps(tampered))


def test_target_and_output_envelopes_reject_valid_receipt_rebinding() -> None:
    context = _context()
    first = _plan("provenance-first", context)
    other_target = _plan("provenance-other", context)
    rebound_receipt = _receipt(other_target.analysis, output_sha=first.validated_output.sha256)
    with pytest.raises(ValidationError, match="exact analysis input artifact"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(first),
                "inference_receipt": rebound_receipt,
            }
        )

    other_proposal = _plan("provenance-first", context, note_sha=SHA_F)
    with pytest.raises(ValidationError, match="validated output|proposal envelope"):
        ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(first),
                "inference_receipt": other_proposal.inference_receipt,
                "validated_output": other_proposal.validated_output,
            }
        )

    no_change = _negative("provenance-no-change", context)
    other_no_change = _negative("provenance-no-change", context, raw_sha=SHA_B)
    with pytest.raises(ValidationError, match="canonical output envelope"):
        NoChangeImpactCard.create(
            **{
                **_recreate_kwargs(no_change),
                "inference_receipt": other_no_change.inference_receipt,
                "validated_output": other_no_change.validated_output,
            }
        )

    wrong_replay_artifact = _artifact(
        "wrong-replay",
        ManagedArtifactKind.INFERENCE_OUTPUT,
        path=f"receipts/inference/{SHA_B}.json",
        sha=SHA_B,
    )
    with pytest.raises(ValidationError, match="resolvable replay receipt artifact"):
        ContentAddressedInferenceReceipt.create(
            contract_id="managed-revision",
            contract_version=1,
            mode=InferenceExecutionMode.REPLAY,
            provider="fixture-provider",
            model="fixture-model",
            provider_request_id=None,
            replay_source_receipt_sha256=SHA_A,
            replay_source_receipt_artifact=wrong_replay_artifact,
            prompt_sha256=SHA_B,
            response_schema_sha256=SHA_C,
            input_artifacts=(first.analysis.inference_input,),
            input_envelope_sha256=first.analysis.input_envelope_sha256,
            raw_output_sha256=first.proposal_sha256,
            validated_output_sha256=first.proposal_sha256,
            usage=first.inference_receipt.usage,
        )


@pytest.mark.parametrize(
    "override",
    (
        {"contract_id": "attacker-contract"},
        {"contract_version": 999},
        {"mode": InferenceExecutionMode.LIVE},
        {"provider": "attacker-provider"},
        {"model": "attacker-model"},
        {"prompt_sha256": SHA_A},
        {"response_schema_sha256": SHA_A},
    ),
)
def test_bundle_and_final_edit_require_exact_run_inference_contract(
    override: dict[str, object],
) -> None:
    context = _context()
    original = _plan("contract-bound", context)
    base = original.inference_receipt
    values: dict[str, object] = {
        "contract_id": base.contract_id,
        "contract_version": base.contract_version,
        "mode": base.mode,
        "provider": base.provider,
        "model": base.model,
        "provider_request_id": base.provider_request_id,
        "replay_source_receipt_sha256": base.replay_source_receipt_sha256,
        "replay_source_receipt_artifact": base.replay_source_receipt_artifact,
        "prompt_sha256": base.prompt_sha256,
        "response_schema_sha256": base.response_schema_sha256,
        "input_artifacts": base.input_artifacts,
        "input_envelope_sha256": base.input_envelope_sha256,
        "raw_output_sha256": base.raw_output_sha256,
        "validated_output_sha256": base.validated_output_sha256,
        "usage": base.usage,
    }
    values.update(override)
    if override.get("mode") == InferenceExecutionMode.LIVE:
        values.update(
            {
                "provider_request_id": "provider-request:attacker-live",
                "replay_source_receipt_sha256": None,
                "replay_source_receipt_artifact": None,
                "usage": InferenceUsage(
                    input_tokens=10,
                    output_tokens=5,
                    cached_input_tokens=0,
                    cost_usd_micros=1,
                    latency_ms=1,
                ),
            }
        )
    attacker = ContentAddressedInferenceReceipt.create(**values)  # type: ignore[arg-type]
    altered = ManagedRevisionPlan.create(
        **{
            **_recreate_kwargs(original),
            "inference_receipt": attacker,
        }
    )
    with pytest.raises(ValidationError, match="exact run-level contract"):
        _bundle(context, altered)

    bundle = _bundle(context, original)
    request = _request_record(bundle)
    with pytest.raises(ValueError, match="exact run-level contract"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:attacker-contract",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="An edited final plan cannot replace the sealed run contract.",
            items=(
                _outcome(
                    bundle.targets[0],
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=altered,
                ),
            ),
        )

    valid_edit = _plan_with_changes(
        original,
        rationale="Produce a distinct but contract-bound edited plan for persisted validation.",
    )
    valid_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:persisted-attacker-contract",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Persist one valid edit before simulating a rehashed contract substitution.",
        items=(
            _outcome(
                bundle.targets[0],
                ManagedRevisionDisposition.EDIT,
                edited_plan=valid_edit,
            ),
        ),
    )
    persisted = valid_command.model_dump(mode="json")
    persisted["items"][0]["edited_plan"] = altered.model_dump(mode="json")
    _rehash_decision_payload(persisted)
    with pytest.raises(ValidationError, match="exact run-level contract"):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(persisted))


def _resolved_replay_with_extra_input(
    plan: ManagedRevisionPlan | NoChangeImpactCard,
    extra_input: ManagedArtifactRef,
) -> ContentAddressedInferenceReceipt:
    base = plan.inference_receipt
    inputs = tuple(
        sorted((plan.analysis.inference_input, extra_input), key=lambda item: item.artifact_id)
    )
    live = ContentAddressedInferenceReceipt.create(
        contract_id=base.contract_id,
        contract_version=base.contract_version,
        mode=InferenceExecutionMode.LIVE,
        provider=base.provider,
        model=base.model,
        provider_request_id=f"provider-request:{plan.target_key}",
        replay_source_receipt_sha256=None,
        replay_source_receipt_artifact=None,
        prompt_sha256=base.prompt_sha256,
        response_schema_sha256=base.response_schema_sha256,
        input_artifacts=inputs,
        input_envelope_sha256=base.input_envelope_sha256,
        raw_output_sha256=base.raw_output_sha256,
        validated_output_sha256=base.validated_output_sha256,
        usage=InferenceUsage(
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            cost_usd_micros=1,
            latency_ms=1,
        ),
    )
    live_bytes = canonical_json_bytes(live.model_dump(mode="json"))
    live_sha = hashlib.sha256(live_bytes).hexdigest()
    replay = ContentAddressedInferenceReceipt.create(
        contract_id=base.contract_id,
        contract_version=base.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=base.provider,
        model=base.model,
        provider_request_id=None,
        replay_source_receipt_sha256=live_sha,
        replay_source_receipt_artifact=ManagedArtifactRef.create(
            kind=ManagedArtifactKind.INFERENCE_RECEIPT,
            path=f"receipts/inference/{live_sha}.json",
            sha256=live_sha,
            byte_count=len(live_bytes),
        ),
        prompt_sha256=base.prompt_sha256,
        response_schema_sha256=base.response_schema_sha256,
        input_artifacts=inputs,
        input_envelope_sha256=base.input_envelope_sha256,
        raw_output_sha256=base.raw_output_sha256,
        validated_output_sha256=base.validated_output_sha256,
        usage=base.usage,
    )
    replay.verify_replay_source(live)
    return replay


@pytest.mark.parametrize("conflict_kind", ("extra-input", "replay-receipt"))
def test_final_edits_reject_conflicting_global_artifact_locators(
    conflict_kind: str,
) -> None:
    context = _context()
    originals = (
        _plan("locator-edit-first", context),
        _plan("locator-edit-second", context),
    )
    bundle = _bundle(context, *originals)
    request = _request_record(bundle)
    conflicting_receipts: list[ContentAddressedInferenceReceipt] = []
    for index, original in enumerate(originals):
        if conflict_kind == "extra-input":
            extra = ManagedArtifactRef.create(
                kind=ManagedArtifactKind.RAW_SOURCE,
                path="fixtures/shared-edited-input.json",
                sha256=(SHA_E if index == 0 else SHA_F),
                byte_count=128 + index,
            )
            conflicting_receipts.append(_resolved_replay_with_extra_input(original, extra))
            continue
        base = original.inference_receipt
        conflicting_receipts.append(
            ContentAddressedInferenceReceipt.create(
                contract_id=base.contract_id,
                contract_version=base.contract_version,
                mode=base.mode,
                provider=base.provider,
                model=base.model,
                provider_request_id=base.provider_request_id,
                replay_source_receipt_sha256=SHA_A,
                replay_source_receipt_artifact=ManagedArtifactRef.create(
                    kind=ManagedArtifactKind.INFERENCE_RECEIPT,
                    path=f"receipts/inference/{SHA_A}.json",
                    sha256=SHA_A,
                    byte_count=128 + index,
                ),
                prompt_sha256=base.prompt_sha256,
                response_schema_sha256=base.response_schema_sha256,
                input_artifacts=base.input_artifacts,
                input_envelope_sha256=base.input_envelope_sha256,
                raw_output_sha256=base.raw_output_sha256,
                validated_output_sha256=base.validated_output_sha256,
                usage=base.usage,
            )
        )

    altered_by_key = {
        original.target_key: ManagedRevisionPlan.create(
            **{
                **_recreate_kwargs(original),
                "inference_receipt": receipt,
            }
        )
        for original, receipt in zip(originals, conflicting_receipts, strict=True)
    }
    conflicting_items = tuple(
        sorted(
            (
                _outcome(
                    target,
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=altered_by_key[target.target_key],
                )
                for target in bundle.targets
            ),
            key=lambda item: item.target_id,
        )
    )
    with pytest.raises(ValueError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.create(
            operation_id=f"managed-decision:{conflict_kind}",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Reject conflicting locators introduced only by final edited plans.",
            items=conflicting_items,
        )

    valid_edits = {
        original.target_key: _plan_with_changes(
            original,
            rationale=f"Valid distinct persisted edit for {original.target_key}.",
        )
        for original in originals
    }
    valid_items = tuple(
        sorted(
            (
                _outcome(
                    target,
                    ManagedRevisionDisposition.EDIT,
                    edited_plan=valid_edits[target.target_key],
                )
                for target in bundle.targets
            ),
            key=lambda item: item.target_id,
        )
    )
    valid_command = ManagedRevisionDecisionCommand.create(
        operation_id=f"managed-decision:persisted-{conflict_kind}",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Persist valid edits before simulating conflicting locator substitution.",
        items=valid_items,
    )
    persisted = valid_command.model_dump(mode="json")
    for item in persisted["items"]:
        target = next(target for target in bundle.targets if target.target_id == item["target_id"])
        item["edited_plan"] = altered_by_key[target.target_key].model_dump(mode="json")
    _rehash_decision_payload(persisted)
    with pytest.raises(ValidationError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(persisted))


@pytest.mark.parametrize("conflict_kind", ("no-change-input", "no-change-replay"))
def test_final_edit_cannot_conflict_with_retained_no_change_artifacts(
    conflict_kind: str,
) -> None:
    context = _context()
    original = _plan("retained-baseline-plan", context)
    negative = _negative("retained-baseline-no-change", context)
    if conflict_kind == "no-change-input":
        shared_path = "fixtures/retained-no-change-extra.json"
        negative_receipt = _resolved_replay_with_extra_input(
            negative,
            ManagedArtifactRef.create(
                kind=ManagedArtifactKind.RAW_SOURCE,
                path=shared_path,
                sha256=SHA_E,
                byte_count=128,
            ),
        )
        negative = NoChangeImpactCard.create(
            **{
                **_recreate_kwargs(negative),
                "inference_receipt": negative_receipt,
            }
        )
        conflicting_receipt = _resolved_replay_with_extra_input(
            original,
            ManagedArtifactRef.create(
                kind=ManagedArtifactKind.RAW_SOURCE,
                path=shared_path,
                sha256=SHA_F,
                byte_count=129,
            ),
        )
    else:
        base = original.inference_receipt
        negative_replay = negative.inference_receipt.replay_source_receipt_artifact
        assert negative_replay is not None
        conflicting_receipt = ContentAddressedInferenceReceipt.create(
            contract_id=base.contract_id,
            contract_version=base.contract_version,
            mode=base.mode,
            provider=base.provider,
            model=base.model,
            provider_request_id=base.provider_request_id,
            replay_source_receipt_sha256=negative_replay.sha256,
            replay_source_receipt_artifact=ManagedArtifactRef.create(
                kind=ManagedArtifactKind.INFERENCE_RECEIPT,
                path=negative_replay.path,
                sha256=negative_replay.sha256,
                byte_count=negative_replay.byte_count + 1,
            ),
            prompt_sha256=base.prompt_sha256,
            response_schema_sha256=base.response_schema_sha256,
            input_artifacts=base.input_artifacts,
            input_envelope_sha256=base.input_envelope_sha256,
            raw_output_sha256=base.raw_output_sha256,
            validated_output_sha256=base.validated_output_sha256,
            usage=base.usage,
        )
    bundle = _bundle(context, original, negative)
    request = _request_record(bundle)
    altered = ManagedRevisionPlan.create(
        **{
            **_recreate_kwargs(original),
            "inference_receipt": conflicting_receipt,
        }
    )
    by_key = {target.target_key: target for target in bundle.targets}

    def outcomes(edited_plan: ManagedRevisionPlan) -> tuple[ManagedRevisionReviewOutcome, ...]:
        return tuple(
            sorted(
                (
                    _outcome(
                        by_key[original.target_key],
                        ManagedRevisionDisposition.EDIT,
                        edited_plan=edited_plan,
                    ),
                    _outcome(
                        by_key[negative.target_key],
                        ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
                    ),
                ),
                key=lambda item: item.target_id,
            )
        )

    with pytest.raises(ValueError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.create(
            operation_id=f"managed-decision:{conflict_kind}",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Reject an edited plan that conflicts with retained no-change evidence.",
            items=outcomes(altered),
        )

    valid_edit = _plan_with_changes(
        original,
        rationale="Create a valid distinct edit before persisted locator substitution.",
    )
    valid_command = ManagedRevisionDecisionCommand.create(
        operation_id=f"managed-decision:persisted-{conflict_kind}",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Persist a valid edit while retaining the no-change subject.",
        items=outcomes(valid_edit),
    )
    persisted = valid_command.model_dump(mode="json")
    edited_item = next(
        item
        for item in persisted["items"]
        if item["disposition"] == ManagedRevisionDisposition.EDIT.value
    )
    edited_item["edited_plan"] = altered.model_dump(mode="json")
    _rehash_decision_payload(persisted)
    with pytest.raises(ValidationError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(persisted))


def test_bundle_rejects_no_change_input_conflicting_with_publication_destination() -> None:
    context = _context()
    plan = _plan("destination-closure-plan", context)
    negative = _negative("destination-closure-no-change", context)
    conflicting_receipt = _resolved_replay_with_extra_input(
        negative,
        ManagedArtifactRef.create(
            kind=ManagedArtifactKind.RAW_SOURCE,
            path=plan.raw_destination.path,
            sha256=SHA_F,
            byte_count=plan.raw_destination.expected_byte_count + 1,
        ),
    )
    altered_negative = NoChangeImpactCard.create(
        **{
            **_recreate_kwargs(negative),
            "inference_receipt": conflicting_receipt,
        }
    )
    with pytest.raises(ValidationError, match="conflict"):
        _bundle(context, plan, altered_negative)


def test_final_edit_cannot_conflict_with_original_publication_destination() -> None:
    context = _context()
    originals = (
        _plan("destination-final-first", context),
        _plan("destination-final-second", context),
    )
    bundle = _bundle(context, *originals)
    request = _request_record(bundle)
    altered_receipt = _resolved_replay_with_extra_input(
        originals[0],
        ManagedArtifactRef.create(
            kind=ManagedArtifactKind.RAW_SOURCE,
            path=originals[1].raw_destination.path,
            sha256=SHA_F,
            byte_count=originals[1].raw_destination.expected_byte_count + 1,
        ),
    )
    altered = ManagedRevisionPlan.create(
        **{
            **_recreate_kwargs(originals[0]),
            "inference_receipt": altered_receipt,
        }
    )
    by_key = {target.target_key: target for target in bundle.targets}

    def outcomes(edited: ManagedRevisionPlan) -> tuple[ManagedRevisionReviewOutcome, ...]:
        return tuple(
            sorted(
                (
                    _outcome(
                        by_key[originals[0].target_key],
                        ManagedRevisionDisposition.EDIT,
                        edited_plan=edited,
                    ),
                    _outcome(
                        by_key[originals[1].target_key],
                        ManagedRevisionDisposition.APPROVE,
                    ),
                ),
                key=lambda item: item.target_id,
            )
        )

    with pytest.raises(ValueError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:destination-final-conflict",
            request_record=request,
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="Reject evidence that conflicts with an original publication locator.",
            items=outcomes(altered),
        )

    valid_edit = _plan_with_changes(
        originals[0],
        rationale="Create one valid edit before persisted destination conflict substitution.",
    )
    valid_command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:persisted-destination-final-conflict",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Persist a valid edit before rehydrating a destination conflict.",
        items=outcomes(valid_edit),
    )
    persisted = valid_command.model_dump(mode="json")
    edited_item = next(
        item
        for item in persisted["items"]
        if item["disposition"] == ManagedRevisionDisposition.EDIT.value
    )
    edited_item["edited_plan"] = altered.model_dump(mode="json")
    _rehash_decision_payload(persisted)
    with pytest.raises(ValidationError, match="artifact locator path cannot bind conflicting"):
        ManagedRevisionDecisionCommand.model_validate_json(json.dumps(persisted))


@pytest.mark.parametrize("subject_kind", ("plan-self", "no-change-cross-target"))
def test_subject_rejects_resolved_replay_inputs_from_managed_staging(
    subject_kind: str,
) -> None:
    context = _context()
    plan = _plan("self-grounding-plan", context)
    if subject_kind == "plan-self":
        subject: ManagedRevisionPlan | NoChangeImpactCard = plan
        forbidden_input = plan.proposed_raw
    else:
        subject = _negative("self-grounding-no-change", context)
        forbidden_input = plan.proposed_note
    malicious_receipt = _resolved_replay_with_extra_input(subject, forbidden_input)
    recreate = {
        **_recreate_kwargs(subject),
        "inference_receipt": malicious_receipt,
    }
    with pytest.raises(ValueError, match="exact analysis input artifact"):
        type(subject).create(**recreate)

    persisted = subject.model_dump(mode="json")
    persisted["inference_receipt"] = malicious_receipt.model_dump(mode="json")
    if isinstance(subject, ManagedRevisionPlan):
        identity = {
            key: value for key, value in persisted.items() if key not in {"plan_id", "plan_sha256"}
        }
        digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        persisted["plan_id"] = f"mplan:{digest}"
        persisted["plan_sha256"] = digest
        model = ManagedRevisionPlan
    else:
        identity = {
            key: value for key, value in persisted.items() if key not in {"card_id", "card_sha256"}
        }
        digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        persisted["card_id"] = f"mnochange:{digest}"
        persisted["card_sha256"] = digest
        model = NoChangeImpactCard
    with pytest.raises(ValidationError, match="exact analysis input artifact"):
        model.model_validate_json(json.dumps(persisted))


@pytest.mark.parametrize("kind", ("plan", "no-change"))
def test_replay_receipt_cannot_self_reference_current_validated_output(kind: str) -> None:
    context = _context()
    subject = (
        _plan("circular-plan", context)
        if kind == "plan"
        else _negative("circular-no-change", context)
    )
    output = subject.validated_output
    circular_artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_RECEIPT,
        path=f"receipts/inference/{output.sha256}.json",
        sha256=output.sha256,
        byte_count=output.byte_count,
    )
    base = subject.inference_receipt
    circular = ContentAddressedInferenceReceipt.create(
        contract_id=base.contract_id,
        contract_version=base.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=base.provider,
        model=base.model,
        provider_request_id=None,
        replay_source_receipt_sha256=output.sha256,
        replay_source_receipt_artifact=circular_artifact,
        prompt_sha256=base.prompt_sha256,
        response_schema_sha256=base.response_schema_sha256,
        input_artifacts=base.input_artifacts,
        input_envelope_sha256=base.input_envelope_sha256,
        raw_output_sha256=output.sha256,
        validated_output_sha256=output.sha256,
        usage=base.usage,
    )
    with pytest.raises(ValidationError, match="cannot be the current validated output"):
        type(subject).create(
            **{
                **_recreate_kwargs(subject),
                "inference_receipt": circular,
            }
        )


def test_replay_source_resolution_requires_exact_prior_live_receipt() -> None:
    analysis = _target_analysis("resolved-replay", _context())
    live, replay = _receipt_pair(analysis, output_sha=SHA_E)
    replay.verify_replay_source(live)
    with pytest.raises(ValueError, match="prior LIVE receipt"):
        replay.verify_replay_source(replay)

    other_live, _other_replay = _receipt_pair(
        analysis,
        provider="other-provider",
        output_sha=SHA_E,
    )
    other_bytes = canonical_json_bytes(other_live.model_dump(mode="json"))
    other_sha = hashlib.sha256(other_bytes).hexdigest()
    rebound = ContentAddressedInferenceReceipt.create(
        contract_id=replay.contract_id,
        contract_version=replay.contract_version,
        mode=InferenceExecutionMode.REPLAY,
        provider=replay.provider,
        model=replay.model,
        provider_request_id=None,
        replay_source_receipt_sha256=other_sha,
        replay_source_receipt_artifact=ManagedArtifactRef.create(
            kind=ManagedArtifactKind.INFERENCE_RECEIPT,
            path=f"receipts/inference/{other_sha}.json",
            sha256=other_sha,
            byte_count=len(other_bytes),
        ),
        prompt_sha256=replay.prompt_sha256,
        response_schema_sha256=replay.response_schema_sha256,
        input_artifacts=replay.input_artifacts,
        input_envelope_sha256=replay.input_envelope_sha256,
        raw_output_sha256=replay.raw_output_sha256,
        validated_output_sha256=replay.validated_output_sha256,
        usage=replay.usage,
    )
    with pytest.raises(ValueError, match="does not exactly match"):
        rebound.verify_replay_source(other_live)


def test_inference_input_count_bytes_and_locator_limits_fail_closed() -> None:
    def inputs(count: int, *, first_byte_count: int) -> tuple[ManagedArtifactRef, ...]:
        return tuple(
            _artifact(
                f"bounded-input-{index}",
                ManagedArtifactKind.RAW_SOURCE,
                path=f"fixtures/bounded-input-{index}.md",
                sha=f"{index + 1:064x}",
                byte_count=(first_byte_count if index == 0 else MAX_MANAGED_ARTIFACT_BYTES_V1),
            )
            for index in range(count)
        )

    def create(input_artifacts: tuple[ManagedArtifactRef, ...]) -> ContentAddressedInferenceReceipt:
        return ContentAddressedInferenceReceipt.create(
            contract_id="bounded-inference",
            contract_version=1,
            mode=InferenceExecutionMode.REPLAY,
            provider="fixture-provider",
            model="fixture-model",
            provider_request_id=None,
            replay_source_receipt_sha256=SHA_A,
            replay_source_receipt_artifact=_artifact(
                "bounded-replay",
                ManagedArtifactKind.INFERENCE_RECEIPT,
                path=f"receipts/inference/{SHA_A}.json",
                sha=SHA_A,
            ),
            prompt_sha256=SHA_B,
            response_schema_sha256=SHA_C,
            input_artifacts=input_artifacts,
            input_envelope_sha256=SHA_D,
            raw_output_sha256=SHA_E,
            validated_output_sha256=SHA_F,
            usage=InferenceUsage(
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                cost_usd_micros=0,
                latency_ms=0,
            ),
        )

    per_input = MAX_MANAGED_INFERENCE_INPUT_BYTES_V1 // MAX_MANAGED_INFERENCE_INPUTS_V1
    exact = tuple(
        _artifact(
            f"exact-input-{index}",
            ManagedArtifactKind.RAW_SOURCE,
            path=f"fixtures/exact-input-{index}.md",
            sha=f"{index + 1:064x}",
            byte_count=per_input,
        )
        for index in range(MAX_MANAGED_INFERENCE_INPUTS_V1)
    )
    assert sum(item.byte_count for item in create(exact).input_artifacts) == (
        MAX_MANAGED_INFERENCE_INPUT_BYTES_V1
    )
    with pytest.raises(ValueError, match="inference inputs count"):
        create(exact + (_artifact("input-33", ManagedArtifactKind.RAW_SOURCE, sha=SHA_F),))
    over_bytes = list(exact)
    over_bytes[0] = _artifact(
        "over-byte-input",
        ManagedArtifactKind.RAW_SOURCE,
        path=exact[0].path,
        sha=SHA_F,
        byte_count=per_input + 1,
    )
    with pytest.raises(ValueError, match="aggregate declared-byte limit"):
        create(tuple(over_bytes))

    first, second = inputs(2, first_byte_count=1)
    second = ManagedArtifactRef.create(
        kind=second.kind,
        path=first.path,
        sha256=second.sha256,
        byte_count=second.byte_count,
    )
    with pytest.raises(ValueError, match="artifact paths must be unique"):
        create((first, second))

    receipt_input = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_RECEIPT,
        path=f"receipts/inference/{SHA_E}.json",
        sha256=SHA_E,
        byte_count=128,
    )
    with pytest.raises(ValidationError, match="output/receipt cannot be an inference input"):
        create((receipt_input,))
    direct = create(exact[:1]).model_dump(mode="json")
    direct["input_artifacts"] = [receipt_input.model_dump(mode="json")]
    with pytest.raises(ValidationError, match="output/receipt cannot be an inference input"):
        ContentAddressedInferenceReceipt.model_validate_json(json.dumps(direct))


@pytest.mark.parametrize("nested_limit", ("scopes", "evidence", "quote"))
def test_raw_nested_claim_limits_reject_before_nested_identity_hashing(
    nested_limit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = _plan("raw-nested-preflight", _context()).predecessor_projection
    payload = projection.model_dump(mode="json")
    claim = payload["projected_claims"][0]
    if nested_limit == "scopes":
        claim["scopes"] = [f"scope-{index:02d}" for index in range(17)]
        expected = "scope count limit"
    elif nested_limit == "evidence":
        claim["source"]["evidence"] = [{"quote": "evidence"}] * 17
        expected = "evidence count limit"
    else:
        claim["source"]["evidence"] = [{"quote": "q" * (8 * 1024 + 1)}]
        expected = "evidence quote exceeds"

    def fail_hash(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("nested identity hashing ran")

    monkeypatch.setattr(managed_review_module, "_sha256", fail_hash)
    monkeypatch.setattr(change_models_module, "stable_content_id", fail_hash)
    with pytest.raises(ValidationError, match=expected):
        SourceNoteProjectionBinding.model_validate_json(json.dumps(payload))


def test_attested_text_limit_is_utf8_bytes_and_zero_byte_insertions_are_valid() -> None:
    plan = _plan("byte-limit", _context())
    citation = plan.hunks[0].citations
    exact = "😀" * (managed_review_module.MAX_ATTESTED_TEXT_BYTES_V1 // 4)
    hunk = ManagedSemanticHunk.create(
        semantic_key="exact-byte-limit",
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        start_byte=0,
        before_text="",
        replacement_text=exact,
        citations=citation,
    )
    assert hunk.start_byte == hunk.end_byte == 0
    assert len(hunk.replacement_text.encode("utf-8")) == (
        managed_review_module.MAX_ATTESTED_TEXT_BYTES_V1
    )
    with pytest.raises(ValueError, match="MAX_ATTESTED_TEXT_BYTES_V1"):
        ManagedSemanticHunk.create(
            semantic_key="over-byte-limit",
            base_artifact=plan.predecessor_raw,
            result_artifact=plan.proposed_raw,
            start_byte=0,
            before_text="",
            replacement_text=exact + "😀",
            citations=citation,
        )

    exact_quote = "😀" * (MAX_MANAGED_CITATION_QUOTE_BYTES_V1 // 4)
    quoted = GroundedArtifactCitation.create(
        artifact=plan.analysis.inference_input,
        start_byte=0,
        quote=exact_quote,
    )
    assert len(quoted.quote.encode("utf-8")) == MAX_MANAGED_CITATION_QUOTE_BYTES_V1
    with pytest.raises(ValueError, match="MAX_MANAGED_CITATION_QUOTE_BYTES_V1"):
        GroundedArtifactCitation.create(
            artifact=plan.analysis.inference_input,
            start_byte=0,
            quote=exact_quote + "😀",
        )


def test_hunk_citation_count_accepts_16_and_rejects_17() -> None:
    plan = _plan("citation-count-limit", _context())
    citations = tuple(
        GroundedArtifactCitation.create(
            artifact=plan.analysis.inference_input,
            start_byte=index,
            quote="x",
        )
        for index in range(17)
    )
    exact = ManagedSemanticHunk.create(
        semantic_key="citation-count-exact",
        base_artifact=plan.predecessor_raw,
        result_artifact=plan.proposed_raw,
        start_byte=0,
        before_text="",
        replacement_text="bounded replacement",
        citations=citations[:16],
    )
    assert len(exact.citations) == 16
    with pytest.raises(ValueError, match="hunk citations count"):
        ManagedSemanticHunk.create(
            semantic_key="citation-count-over",
            base_artifact=plan.predecessor_raw,
            result_artifact=plan.proposed_raw,
            start_byte=0,
            before_text="",
            replacement_text="bounded replacement",
            citations=citations,
        )


def test_reconciliation_rejects_reversed_entries_even_when_rehashed() -> None:
    first = _plan("order-first", _context()).claim_reconciliation.entries[0]
    second = _plan("order-second", _context()).claim_reconciliation.entries[0]
    ordered = tuple(
        sorted(
            (first, second),
            key=lambda item: (
                item.predecessor.claim_revision_id if item.predecessor else "",
                item.successor.claim_revision_id if item.successor else "",
                item.action.value,
            ),
        )
    )
    values = {
        "schema_version": 1,
        "predecessor_projection_id": "mprojection:" + "1" * 64,
        "successor_projection_id": "mprojection:" + "2" * 64,
        "predecessor_revisions": tuple(
            sorted(
                (item.predecessor for item in ordered if item.predecessor),
                key=lambda item: item.claim_revision_id,
            )
        ),
        "successor_revisions": tuple(
            sorted(
                (item.successor for item in ordered if item.successor),
                key=lambda item: item.claim_revision_id,
            )
        ),
        "entries": tuple(reversed(ordered)),
    }
    json_values = {
        key: [item.model_dump(mode="json") for item in value] if isinstance(value, tuple) else value
        for key, value in values.items()
    }
    with pytest.raises(ValidationError, match="canonically ordered"):
        ClaimReconciliationBinding.model_validate(
            {
                "reconciliation_id": "mclaims:"
                + hashlib.sha256(canonical_json_bytes(json_values)).hexdigest(),
                **values,
            }
        )


def test_edit_preserves_predecessor_projection_and_staging_paths_are_sha_derived() -> None:
    context = _context()
    original = _plan("edit-projection", context)
    altered_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=original.predecessor_raw,
        note_artifact=original.predecessor_note,
        canonical_raw_path=original.predecessor_raw.path,
        canonical_note_path=original.predecessor_note.path,
        validator_version="source-note-v1",
        source_note_schema_sha256=SHA_C,
        validator_result_sha256=SHA_E,
        projected_claims=original.predecessor_projection.projected_claims,
    )
    reconciliation = ClaimReconciliationBinding.create(
        predecessor_projection=altered_projection,
        successor_projection=original.successor_projection,
        entries=original.claim_reconciliation.entries,
    )
    edited = _plan_with_changes(
        original,
        predecessor_projection=altered_projection,
        claim_reconciliation=reconciliation,
        rationale="Change only verifier provenance while preserving the semantic proposal.",
    )
    bundle = _bundle(context, original)
    with pytest.raises(ValidationError, match="preserve run, analysis, predecessor"):
        ManagedRevisionDecisionCommand.create(
            operation_id="managed-decision:projection-rebind",
            request_record=_request_record(bundle),
            bundle_outcome=ManagedBundleOutcome.ACCEPTED,
            reviewer_id="reviewer@example.test",
            rationale="A human edit cannot replace predecessor projection evidence.",
            items=(
                _outcome(bundle.targets[0], ManagedRevisionDisposition.EDIT, edited_plan=edited),
            ),
        )

    bad_raw = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.RAW_SOURCE,
        path=original.proposed_raw.path,
        sha256=SHA_E,
        byte_count=original.proposed_raw.byte_count,
    )
    with pytest.raises(ValidationError, match="content-addressed by SHA"):
        ManagedRevisionPlan.create(**{**_recreate_kwargs(original), "proposed_raw": bad_raw})


def test_decision_receipt_rebinding_and_backdated_decisions_are_rejected() -> None:
    context = _context()
    bundle = _bundle(context, _plan("receipt-rebound", context))
    request = _request_record(bundle)
    target = bundle.targets[0]
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:receipt-one",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer-one@example.test",
        rationale="Approve the first exact authoritative decision.",
        items=(_outcome(target, ManagedRevisionDisposition.APPROVE),),
    )
    with pytest.raises(ValidationError, match="chronology"):
        ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:05:05+00:00")
    record = ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:06:00+00:00")
    other = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:receipt-two",
        request_record=request,
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer-two@example.test",
        rationale="Approve a second distinct authoritative decision.",
        items=(_outcome(target, ManagedRevisionDisposition.APPROVE),),
    )
    other_record = ManagedRevisionDecisionRecord.create(
        other, decided_at="2032-02-03T04:07:00+00:00"
    )
    rebound_values = ManagedRevisionDecisionReceipt.create(other_record, replayed=False).model_dump(
        mode="json", exclude={"receipt_id"}
    )
    rebound_values["decision_record_sha256"] = record.record_sha256
    rebound = ManagedRevisionDecisionReceipt.model_validate(
        {
            "receipt_id": "mreceipt:"
            + hashlib.sha256(canonical_json_bytes(rebound_values)).hexdigest(),
            **rebound_values,
        }
    )
    with pytest.raises(ValidationError, match="exact decision command receipt"):
        ManagedRevisionReviewView.create(
            request_record=request,
            request_receipt=ManagedRevisionReviewRequestReceipt.create(request, replayed=False),
            decision_record=record,
            receipt=rebound,
        )


def test_authority_origin_is_deterministic_and_managed_successor_binds_decision() -> None:
    context = _context()
    authority = context.review_base.authority
    assert isinstance(authority.origin_basis, GenerationZeroOriginBasis)
    assert AuthorityRevisionBinding.model_validate_json(authority.model_dump_json()) == authority

    bundle = _bundle(context, _plan("authority-successor", context))
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:authority-successor",
        request_record=_request_record(bundle),
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Authorize the exact next inactive authority generation.",
        items=(_outcome(bundle.targets[0], ManagedRevisionDisposition.APPROVE),),
    )
    record = ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:06:00+00:00")
    successor = AuthorityRevisionBinding.create_managed_successor(
        expected_authority=authority, decision_record=record
    )
    assert successor.authority_revision == authority.authority_revision + 1
    assert successor.active_generation.generation_number == 1
    assert AuthorityRevisionBinding.model_validate_json(successor.model_dump_json()) == successor
    successor.verify_managed_successor_origin(
        expected_authority=authority,
        decision_record=record,
    )
    tampered = successor.model_dump(mode="json")
    tampered["origin_basis"]["decision_record_sha256"] = SHA_A
    with pytest.raises(ValidationError, match="active pointer SHA"):
        AuthorityRevisionBinding.model_validate(tampered)

    with pytest.raises(ValidationError, match="valid only for exact generation zero"):
        AuthorityRevisionBinding._create(
            aggregate_id=authority.aggregate_id,
            authority_revision=authority.authority_revision,
            active_generation=ContentAddressedGenerationBinding.create(
                generation_number=1, manifest_sha256=SHA_E
            ),
            origin_basis=authority.origin_basis,
        )
    nonzero = authority.model_dump(mode="json")
    nonzero["authority_revision"] = 1
    _rehash_authority_payload(nonzero)
    with pytest.raises(ValidationError, match="revision must be zero"):
        AuthorityRevisionBinding.model_validate_json(json.dumps(nonzero))


@pytest.mark.parametrize(
    "field",
    (
        "request_record_sha256",
        "decision_id",
        "decision_payload_sha256",
        "decision_record_sha256",
        "activation_plan_id",
        "expected_authority_id",
        "expected_authority_revision",
        "expected_active_pointer_sha256",
        "prior_generation",
        "active_generation",
    ),
)
def test_rehashed_managed_authority_origin_requires_authoritative_resolution(field: str) -> None:
    context = _context()
    authority = context.review_base.authority
    bundle = _bundle(context, _plan("authority-resolve", context))
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:authority-resolve",
        request_record=_request_record(bundle),
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Create exact typed evidence for managed-origin resolution.",
        items=(_outcome(bundle.targets[0], ManagedRevisionDisposition.APPROVE),),
    )
    record = ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:06:00+00:00")
    successor = AuthorityRevisionBinding.create_managed_successor(
        expected_authority=authority,
        decision_record=record,
    )
    tampered = successor.model_dump(mode="json")
    origin = tampered["origin_basis"]
    assert isinstance(origin, dict)
    replacements: dict[str, object] = {
        "request_record_sha256": SHA_A,
        "decision_id": "mdecision:" + "a" * 64,
        "decision_payload_sha256": SHA_A,
        "decision_record_sha256": SHA_A,
        "activation_plan_id": "mauthorityplan:" + "a" * 64,
        "expected_authority_id": "mauthority:" + "a" * 64,
        "expected_authority_revision": 99,
        "expected_active_pointer_sha256": SHA_A,
        "prior_generation": ContentAddressedGenerationBinding.create(
            generation_number=0,
            manifest_sha256=SHA_E,
        ).model_dump(mode="json"),
        "active_generation": ContentAddressedGenerationBinding.create(
            generation_number=1,
            manifest_sha256=SHA_A,
        ).model_dump(mode="json"),
    }
    if field == "active_generation":
        tampered["active_generation"] = replacements[field]
    else:
        origin[field] = replacements[field]
        if field == "expected_authority_revision":
            tampered["authority_revision"] = 100
    _rehash_authority_payload(tampered)
    structural = AuthorityRevisionBinding.model_validate_json(json.dumps(tampered))
    with pytest.raises(ValueError, match="does not resolve to exact prior authority and decision"):
        structural.verify_managed_successor_origin(
            expected_authority=authority,
            decision_record=record,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_record_sha256", SHA_A),
        ("decision_id", "mdecision:" + "a" * 64),
        ("decision_payload_sha256", SHA_A),
        ("decision_record_sha256", SHA_A),
        ("activation_plan_id", "mauthorityplan:" + "a" * 64),
        ("expected_authority_id", "mauthority:" + "a" * 64),
        ("expected_authority_revision", 99),
        ("expected_active_pointer_sha256", SHA_A),
    ),
)
def test_managed_authority_origin_tampering_breaks_identity(field: str, value: object) -> None:
    context = _context()
    bundle = _bundle(context, _plan("authority-tamper", context))
    command = ManagedRevisionDecisionCommand.create(
        operation_id="managed-decision:authority-tamper",
        request_record=_request_record(bundle),
        bundle_outcome=ManagedBundleOutcome.ACCEPTED,
        reviewer_id="reviewer@example.test",
        rationale="Create one authority successor for origin tamper reproduction.",
        items=(_outcome(bundle.targets[0], ManagedRevisionDisposition.APPROVE),),
    )
    record = ManagedRevisionDecisionRecord.create(command, decided_at="2032-02-03T04:06:00+00:00")
    successor = AuthorityRevisionBinding.create_managed_successor(
        expected_authority=context.review_base.authority,
        decision_record=record,
    )
    tampered = successor.model_dump(mode="json")
    tampered["origin_basis"][field] = value
    with pytest.raises(ValidationError):
        AuthorityRevisionBinding.model_validate(tampered)

    discriminator = successor.model_dump(mode="json")
    discriminator["origin_basis"]["origin_kind"] = "verified-seed-bootstrap"
    with pytest.raises(ValidationError):
        AuthorityRevisionBinding.model_validate(discriminator)


def test_public_contracts_are_exported_and_module_is_pure() -> None:
    expected = {
        "AggregateHeadBinding",
        "AuthorityRevisionBinding",
        "ManagedRunBinding",
        "ManagedRun",
        "ManagedGenerationManifest",
        "ManagedGenerationManifestBindingV2",
        "ManagedGoverningSourceAdoptionBinding",
        "ManagedReviewBaseBinding",
        "TargetAnalysisBinding",
        "PatchReconstructionAttestation",
        "SourceNoteProjectionBinding",
        "ManagedRevisionReviewRequestCommand",
        "ManagedRevisionDecisionReceipt",
        "ManagedRevisionReviewView",
    }
    assert expected.issubset(set(managed_review_module.__all__))
    for name in managed_review_module.__all__:
        assert hasattr(managed_review_module, name)

    source = Path(managed_review_module.__file__ or "").read_text(encoding="utf-8")
    imports: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not imports & {"sqlite3", "sqlalchemy", "mastervault.storage"}
    assert "open(" not in source
    assert "write_text(" not in source
    assert "read_bytes(" not in source

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import mastervault.change_control.managed_review; "
                "forbidden=('sqlite3','mastervault.change_control.store',"
                "'mastervault.change_control.incoming','mastervault.change_control.bootstrap',"
                "'mastervault.change_control.seed','mastervault.change_control.workflow',"
                "'mastervault.document_intelligence.store',"
                "'mastervault.document_intelligence.parser','mastervault.vaultfs'); "
                "bad=[name for name in sys.modules if name in forbidden or "
                "name.startswith(('mastervault.evaluator','mastervault.evals'))]; "
                "assert not bad, bad"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr

    from mastervault.change_control import (
        AnalysisBootstrapBinding as PackageAnalysisBootstrapBinding,
    )
    from mastervault.change_control import (
        ChangeControlAggregate,
        VerifiedPrechangeSeedManifest,
    )
    from mastervault.change_control import (
        ManagedGenerationManifest as PackageManagedGenerationManifest,
    )
    from mastervault.change_control import (
        ManagedGenerationManifestBindingV2 as PackageManagedGenerationManifestBindingV2,
    )
    from mastervault.change_control import (
        ManagedGoverningSourceAdoptionBinding as PackageManagedGoverningSourceAdoptionBinding,
    )
    from mastervault.change_control import (
        ManagedRun as PackageManagedRun,
    )

    assert PackageAnalysisBootstrapBinding.__name__ == "AnalysisBootstrapBinding"
    assert ChangeControlAggregate.__name__ == "ChangeControlAggregate"
    assert PackageManagedGenerationManifest is managed_review_module.ManagedGenerationManifest
    assert (
        PackageManagedGenerationManifestBindingV2
        is managed_review_module.ManagedGenerationManifestBindingV2
    )
    assert (
        PackageManagedGoverningSourceAdoptionBinding
        is managed_review_module.ManagedGoverningSourceAdoptionBinding
    )
    assert PackageManagedRun is managed_review_module.ManagedRun
    assert VerifiedPrechangeSeedManifest.__name__ == "VerifiedPrechangeSeedManifest"
