"""Pure deterministic materialization of recorded C0 revision-planning intent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast

from mastervault.change_control.managed_review import (
    ClaimReconciliationAction,
    ClaimReconciliationBinding,
    ClaimReconciliationEntry,
    GenericManagedAnalysisSetBindingV3,
    GroundedArtifactCitation,
    ManagedAnalysisSetAuthority,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedRevisionPlan,
    ManagedSemanticHunk,
    NoChangeImpactCard,
    PatchReconstructionAttestation,
    PublicationDestination,
    PublicationKind,
    SourceNoteProjectionBinding,
    TargetAnalysisBinding,
    derive_managed_successor,
)
from mastervault.change_control.managed_revision_planning import (
    AffectedRevisionWireResponse,
    NoChangeRevisionWireResponse,
    RevisionPlanningInferenceShard,
    RevisionPlanningOutputShard,
    RevisionPlanningWireResponse,
    RevisionPlanningWorkload,
    validate_revision_planning_wire_response,
)
from mastervault.change_control.managed_source_note import (
    MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
    MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
    RenderedManagedSourceNote,
    render_managed_source_note,
)
from mastervault.change_control.models import (
    ClaimSourceReference,
    VersionedClaimRevision,
    canonical_json_bytes,
)
from mastervault.change_control.recorded_inference import (
    InferenceArtifactPayload,
    InferenceInputEnvelope,
)


@dataclass(frozen=True)
class MaterializedRevisionTarget:
    """Receipt-free subject plus all bytes that must be staged create-only."""

    output: RevisionPlanningOutputShard
    subject_kwargs: dict[str, Any]
    staged_artifacts: tuple[tuple[ManagedArtifactRef, bytes], ...]


def _artifact(kind: ManagedArtifactKind, path: str, content: bytes) -> ManagedArtifactRef:
    if not content:
        raise ValueError("managed revision artifacts must be non-empty")
    return ManagedArtifactRef.create(
        kind=kind,
        path=path,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
    )


def _projection_report_bytes(
    *, raw: ManagedArtifactRef, note: ManagedArtifactRef, claims: tuple[VersionedClaimRevision, ...]
) -> bytes:
    """Canonical validator output shared by predecessor and successor projections."""

    return canonical_json_bytes(
        {
            "namespace": "mastervault.managed-source-note-projection-validation.v1",
            "validator_version": MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
            "source_note_schema_sha256": MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
            "raw": raw.model_dump(mode="json"),
            "note": note.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in claims],
        }
    )


def _citation_artifact(
    *,
    shard: RevisionPlanningInferenceShard,
    selector: str,
    artifacts: tuple[InferenceArtifactPayload, ...],
) -> tuple[ManagedArtifactRef, str]:
    item = next(
        (value for value in shard.citation_inputs.inputs if value.input_selector == selector),
        None,
    )
    if item is None:
        raise ValueError("citation selector names an absent exact input")
    digest = hashlib.sha256(item.text_utf8.encode("utf-8")).hexdigest()
    path = f"inference/citations/{item.role.value}/{digest}.txt"
    payload = next((value for value in artifacts if value.artifact.path == path), None)
    if payload is None or payload.content_utf8 != item.text_utf8:
        raise ValueError("citation selector does not reopen exact recorded input bytes")
    return payload.artifact, item.text_utf8


def _grounded_citation(
    *,
    shard: RevisionPlanningInferenceShard,
    selector: Any,
    artifacts: tuple[InferenceArtifactPayload, ...],
) -> GroundedArtifactCitation:
    artifact, text = _citation_artifact(
        shard=shard,
        selector=selector.input_selector,
        artifacts=artifacts,
    )
    quote = text[selector.start_char : selector.end_char]
    start_byte = len(text[: selector.start_char].encode("utf-8"))
    return GroundedArtifactCitation.create(
        artifact=artifact,
        start_byte=start_byte,
        quote=quote,
    )


def _analysis(
    *,
    shard: RevisionPlanningInferenceShard,
    analysis_set: ManagedAnalysisSetAuthority,
    envelope: InferenceInputEnvelope,
) -> TargetAnalysisBinding:
    staged = next(
        (
            item
            for item in envelope.input_artifacts
            if item.path.startswith(
                f"staging/managed-review/{shard.run_id}/{shard.target.target_key}/"
            )
        ),
        None,
    )
    if staged is None:
        raise ValueError("recorded revision input omits its staged analysis artifact")
    return TargetAnalysisBinding.create_recorded(
        target_key=shard.target.target_key,
        analysis_set=analysis_set,
        target_result_sha256=shard.target.output_shard_sha256,
        inference_input=staged,
        input_envelope_sha256=envelope.envelope_sha256,
    )


def _apply_edits(raw: str, response: AffectedRevisionWireResponse) -> str:
    pieces: list[str] = []
    cursor = 0
    for edit in response.edits:
        pieces.append(raw[cursor : edit.start_char])
        pieces.append(edit.replacement_text)
        cursor = edit.end_char
    pieces.append(raw[cursor:])
    result = "".join(pieces)
    if result == raw:
        raise ValueError("complete revision edit program is a no-op")
    return result


def materialize_revision_planning_response(
    *,
    workload: RevisionPlanningWorkload,
    shard: RevisionPlanningInferenceShard,
    response: RevisionPlanningWireResponse,
    analysis_set: ManagedAnalysisSetAuthority,
    predecessor_claims: tuple[VersionedClaimRevision, ...],
    envelope: InferenceInputEnvelope,
    inference_artifacts: tuple[InferenceArtifactPayload, ...],
) -> MaterializedRevisionTarget:
    """Derive every path/hash/projection locally from one validated C0 response."""

    if type(analysis_set) not in (
        ManagedAnalysisSetBinding,
        GenericManagedAnalysisSetBindingV3,
    ):
        raise ValueError("revision materialization requires non-empty impact authority")
    analysis_set = cast(
        ManagedAnalysisSetBinding | GenericManagedAnalysisSetBindingV3,
        analysis_set,
    )
    impact_evidence = analysis_set.impact_evidence
    if (
        analysis_set.analysis_set_id != shard.analysis_set_id
        or analysis_set.analysis_set_sha256 != shard.analysis_set_sha256
        or analysis_set.analysis_bootstrap.analysis_as_of != shard.analysis_as_of
        or impact_evidence is None
        or impact_evidence.workload_id != shard.impact_workload_id
        or impact_evidence.workload_sha256 != shard.impact_workload_sha256
        or impact_evidence.result_id != shard.impact_result_id
        or impact_evidence.result_sha256 != shard.impact_result_sha256
    ):
        raise ValueError("analysis set differs from the content-addressed planning input")
    impact_output = next(
        (
            item
            for item in impact_evidence.output_shards
            if item.input_shard_id == shard.target.input_shard_id
        ),
        None,
    )
    if impact_output is None or (
        impact_output.document_version_id != shard.target.document_version_id
        or impact_output.input_shard_sha256 != shard.target.input_shard_sha256
        or impact_output.output_shard_id != shard.target.output_shard_id
        or impact_output.output_shard_sha256 != shard.target.output_shard_sha256
    ):
        raise ValueError("analysis set omits the exact target impact output")

    response = validate_revision_planning_wire_response(
        response,
        target=shard.target,
        predecessor_raw_utf8=shard.predecessor_raw_utf8,
        citation_inputs=shard.citation_inputs,
        existing_claim_statements={
            item.source_claim_id: item.statement for item in shard.existing_claims
        },
    )

    predecessor_note_path = shard.predecessor_source_note_path
    raw_bytes = shard.predecessor_raw_utf8.encode("utf-8")
    note_bytes = shard.predecessor_source_note_utf8.encode("utf-8")
    predecessor_raw = _artifact(
        ManagedArtifactKind.RAW_SOURCE,
        shard.predecessor.source_path,
        raw_bytes,
    )
    predecessor_note = _artifact(
        ManagedArtifactKind.SOURCE_NOTE,
        predecessor_note_path,
        note_bytes,
    )
    ordered_predecessor_claims = tuple(
        sorted(predecessor_claims, key=lambda item: item.claim_revision_id)
    )
    if any(
        item.document != shard.predecessor
        or item.source.source_note_path != predecessor_note_path
        or item.source.source_note_sha256 != predecessor_note.sha256
        or item.source.evidence
        for item in ordered_predecessor_claims
    ):
        raise ValueError("predecessor claims differ from exact Markdown SourceNote authority")
    predecessor_by_key = {item.source.source_claim_id: item for item in ordered_predecessor_claims}
    if set(predecessor_by_key) != {item.source_claim_id for item in shard.existing_claims}:
        raise ValueError("predecessor claims differ from the recorded planning input")
    for recorded in shard.existing_claims:
        actual = predecessor_by_key[recorded.source_claim_id]
        if (
            actual.claim_identity_id != recorded.claim_identity_id
            or actual.claim_revision_id != recorded.claim_revision_id
            or actual.statement != recorded.statement
            or actual.source.source_note_path != recorded.source_note_path
            or actual.source.source_note_sha256 != recorded.source_note_sha256
            or actual.scopes != recorded.scopes
        ):
            raise ValueError("predecessor claim semantics differ from recorded planning input")
    predecessor_report = _projection_report_bytes(
        raw=predecessor_raw,
        note=predecessor_note,
        claims=ordered_predecessor_claims,
    )
    predecessor_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=predecessor_raw,
        note_artifact=predecessor_note,
        canonical_raw_path=predecessor_raw.path,
        canonical_note_path=predecessor_note.path,
        validator_version=MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
        source_note_schema_sha256=MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
        validator_result_sha256=hashlib.sha256(predecessor_report).hexdigest(),
        projected_claims=ordered_predecessor_claims,
    )
    analysis = _analysis(shard=shard, analysis_set=analysis_set, envelope=envelope)
    common: dict[str, Any] = {
        "run_id": shard.run_id,
        "target_key": shard.target.target_key,
        "predecessor": shard.predecessor,
        "predecessor_raw": predecessor_raw,
        "predecessor_note": predecessor_note,
        "predecessor_projection": predecessor_projection,
        "analysis": analysis,
        "rationale": response.rationale,
    }

    if isinstance(response, NoChangeRevisionWireResponse):
        citations = tuple(
            sorted(
                (
                    _grounded_citation(
                        shard=shard,
                        selector=selector,
                        artifacts=inference_artifacts,
                    )
                    for selector in response.citations
                ),
                key=lambda item: item.citation_id,
            )
        )
        kwargs = {**common, "citations": citations}
        proposal_bytes = NoChangeImpactCard.proposal_output_bytes(**kwargs)
        output = RevisionPlanningOutputShard.create(
            workload=workload,
            input_shard=shard,
            validated_response=response,
            proposal_output_bytes=proposal_bytes,
        )
        return MaterializedRevisionTarget(output=output, subject_kwargs=kwargs, staged_artifacts=())

    assert isinstance(response, AffectedRevisionWireResponse)
    successor_raw_bytes = _apply_edits(shard.predecessor_raw_utf8, response).encode("utf-8")
    raw_sha = hashlib.sha256(successor_raw_bytes).hexdigest()
    proposed_raw = _artifact(
        ManagedArtifactKind.RAW_SOURCE,
        (f"staging/managed-review/{shard.run_id}/{shard.target.target_key}/raw-{raw_sha}.md"),
        successor_raw_bytes,
    )
    raw_destination = PublicationDestination.create(
        target_key=shard.target.target_key,
        kind=PublicationKind.RAW_SOURCE,
        expected_sha256=proposed_raw.sha256,
        expected_byte_count=proposed_raw.byte_count,
    )
    rewrites = {
        item.source_claim_id: item.replacement_statement
        for item in response.source_claim_statement_rewrites
    }
    rendered: RenderedManagedSourceNote = render_managed_source_note(
        predecessor_note_bytes=note_bytes,
        successor_raw_bytes=successor_raw_bytes,
        successor_raw_path=raw_destination.path,
        analysis_as_of=shard.analysis_as_of,
        statement_rewrites=rewrites,
    )
    note_sha = hashlib.sha256(rendered.note_bytes).hexdigest()
    proposed_note = _artifact(
        ManagedArtifactKind.SOURCE_NOTE,
        (f"staging/managed-review/{shard.run_id}/{shard.target.target_key}/note-{note_sha}.md"),
        rendered.note_bytes,
    )
    note_destination = PublicationDestination.create(
        target_key=shard.target.target_key,
        kind=PublicationKind.SOURCE_NOTE,
        expected_sha256=proposed_note.sha256,
        expected_byte_count=proposed_note.byte_count,
    )
    successor = derive_managed_successor(
        predecessor=shard.predecessor,
        target_key=shard.target.target_key,
        proposed_raw=proposed_raw,
        raw_destination=raw_destination,
        effective_from=shard.analysis_as_of,
    )
    successor_claims = tuple(
        sorted(
            (
                VersionedClaimRevision.create(
                    document=successor,
                    source=ClaimSourceReference(
                        source_note_path=note_destination.path,
                        source_note_sha256=proposed_note.sha256,
                        source_claim_id=claim.id,
                        evidence=(),
                    ),
                    statement=claim.statement,
                    declared_effective_from=shard.analysis_as_of,
                    scopes=predecessor_by_key[claim.id].scopes,
                )
                for claim in rendered.model.key_claims
            ),
            key=lambda item: item.claim_revision_id,
        )
    )
    successor_report = _projection_report_bytes(
        raw=proposed_raw,
        note=proposed_note,
        claims=successor_claims,
    )
    successor_projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=proposed_raw,
        note_artifact=proposed_note,
        canonical_raw_path=raw_destination.path,
        canonical_note_path=note_destination.path,
        validator_version=MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
        source_note_schema_sha256=MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
        validator_result_sha256=hashlib.sha256(successor_report).hexdigest(),
        projected_claims=successor_claims,
    )
    successor_by_key = {item.source.source_claim_id: item for item in successor_claims}
    reconciliation = ClaimReconciliationBinding.create(
        predecessor_projection=predecessor_projection,
        successor_projection=successor_projection,
        entries=tuple(
            ClaimReconciliationEntry(
                action=(
                    ClaimReconciliationAction.REWORDED
                    if key in rewrites
                    else ClaimReconciliationAction.CARRIED_FORWARD
                ),
                predecessor=predecessor_by_key[key],
                successor=successor_by_key[key],
            )
            for key in sorted(predecessor_by_key)
        ),
    )
    hunks: list[ManagedSemanticHunk] = []
    for ordinal, edit in enumerate(response.edits):
        before = shard.predecessor_raw_utf8[edit.start_char : edit.end_char]
        start_byte = len(shard.predecessor_raw_utf8[: edit.start_char].encode("utf-8"))
        citations = tuple(
            sorted(
                (
                    _grounded_citation(
                        shard=shard,
                        selector=selector,
                        artifacts=inference_artifacts,
                    )
                    for selector in edit.citations
                ),
                key=lambda item: item.citation_id,
            )
        )
        hunks.append(
            ManagedSemanticHunk.create(
                semantic_key=f"revision-edit-{ordinal:02d}",
                base_artifact=predecessor_raw,
                result_artifact=proposed_raw,
                start_byte=start_byte,
                before_text=before,
                replacement_text=edit.replacement_text,
                citations=citations,
            )
        )
    ordered_hunks = tuple(hunks)
    diff_sha = hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "start_byte": item.start_byte,
                    "end_byte": item.end_byte,
                    "before_sha256": item.before_sha256,
                    "replacement_sha256": item.replacement_sha256,
                }
                for item in ordered_hunks
            ]
        )
    ).hexdigest()
    attestation = PatchReconstructionAttestation.create_from_verifier_output(
        base_artifact=predecessor_raw,
        result_artifact=proposed_raw,
        hunks=ordered_hunks,
        complete_diff_sha256=diff_sha,
    )
    kwargs = {
        **common,
        "successor": successor,
        "proposed_raw": proposed_raw,
        "proposed_note": proposed_note,
        "raw_destination": raw_destination,
        "note_destination": note_destination,
        "successor_projection": successor_projection,
        "patch_attestation": attestation,
        "claim_reconciliation": reconciliation,
        "hunks": ordered_hunks,
    }
    proposal_bytes = ManagedRevisionPlan.proposal_output_bytes(**kwargs)
    output = RevisionPlanningOutputShard.create(
        workload=workload,
        input_shard=shard,
        validated_response=response,
        proposal_output_bytes=proposal_bytes,
    )
    validation_ref = _artifact(
        ManagedArtifactKind.INFERENCE_OUTPUT,
        (
            f"staging/managed-review/{shard.run_id}/{shard.target.target_key}/"
            f"source-note-validation-{hashlib.sha256(successor_report).hexdigest()}.json"
        ),
        successor_report,
    )
    return MaterializedRevisionTarget(
        output=output,
        subject_kwargs=kwargs,
        staged_artifacts=(
            (proposed_raw, successor_raw_bytes),
            (proposed_note, rendered.note_bytes),
            (validation_ref, successor_report),
        ),
    )


__all__ = ["MaterializedRevisionTarget", "materialize_revision_planning_response"]
