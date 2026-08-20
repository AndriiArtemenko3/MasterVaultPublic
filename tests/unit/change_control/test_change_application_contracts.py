from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault import change_control
from mastervault.change_control.change_application_contracts import (
    REVIEW_DECISION_DOCUMENT_V1_ADAPTER,
    AuthoritySummaryV1,
    ChangeEvidenceCompletenessV1,
    ChangeExecutionModeV1,
    ChangeReviewCitationV1,
    ChangeReviewEvidenceSummaryV1,
    ChangeReviewPacketV1,
    ChangeReviewStageV1,
    ChangeReviewSubjectKindV1,
    ChangeReviewSubjectV1,
    ChangeRunNextActionV1,
    ChangeRunOutcomeV1,
    ChangeRunPageV1,
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
    ChangeRunSummaryV1,
    ChangeVerificationResultV1,
    GenerationZeroBaselineSummaryV1,
    IncomingEvidenceSummaryV1,
    ManagedReviewChoiceV1,
    ManagedReviewDecisionDocumentV1,
    ManagedReviewDecisionItemV1,
    RegressionSuiteEvidenceSummaryV1,
    StartChangeRequestV1,
    TemporalReviewChoiceV1,
    TemporalReviewDecisionDocumentV1,
    TemporalReviewDecisionItemV1,
    parse_review_decision_document_v1,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.models import Domain

SHA = "a" * 64
RUN_ID = f"operatorrun:{SHA}"


def _authority(*, active: bool = True) -> AuthoritySummaryV1:
    return AuthoritySummaryV1(
        authority_id=f"mauthority:{SHA}",
        revision=0,
        generation_id=f"mgeneration:{SHA}",
        generation_number=0,
        manifest_sha256=SHA,
        active_pointer_sha256=SHA,
        is_active=active,
    )


def _status() -> ChangeRunStatusV1:
    return ChangeRunStatusV1(
        run_id=RUN_ID,
        phase=ChangeRunPhaseV1.BOOTSTRAPPED,
        outcome=ChangeRunOutcomeV1.IN_PROGRESS,
        next_action=ChangeRunNextActionV1.RESUME,
        created_at="2026-08-20T10:00:00+00:00",
        base_authority=_authority(),
        current_authority=_authority(),
        completeness=ChangeEvidenceCompletenessV1(
            incoming_complete=False,
            suite_complete=False,
            baseline_complete=False,
            temporal_review_complete=False,
            managed_review_complete=False,
            activation_complete=False,
            regression_case_count=0,
            temporal_subject_count=0,
            managed_subject_count=0,
        ),
    )


def test_start_request_paths_are_runtime_only_and_mode_is_exact(tmp_path: Path) -> None:
    assert change_control.StartChangeRequestV1 is StartChangeRequestV1
    assert change_control.parse_review_decision_document_v1 is parse_review_decision_document_v1
    source = tmp_path / "source.md"
    suite = tmp_path / "suite.json"
    replay = tmp_path / "replay.json"
    request = StartChangeRequestV1(
        operation_id="change:start-1",
        source=source,
        domain=Domain.OPERATIONS,
        regression_suite=suite,
        mode=ChangeExecutionModeV1.REPLAY,
        replay_bundle=replay,
    )

    assert request.model_dump(mode="json") == {
        "schema_version": 1,
        "operation_id": "change:start-1",
        "requested_run_id": None,
        "domain": "operations",
        "mode": "replay",
    }
    assert str(tmp_path) not in request.model_dump_json()

    with pytest.raises(ValidationError, match="replay mode requires"):
        StartChangeRequestV1(
            operation_id="change:start-2",
            source=source,
            domain=Domain.OPERATIONS,
            regression_suite=suite,
            mode=ChangeExecutionModeV1.REPLAY,
        )
    with pytest.raises(ValidationError, match="distinct files"):
        StartChangeRequestV1(
            operation_id="change:start-3",
            source=source,
            domain=Domain.OPERATIONS,
            regression_suite=source,
            mode=ChangeExecutionModeV1.LIVE,
        )


def test_strict_json_round_trip_rejects_unknown_coercion_and_duplicate() -> None:
    request = TemporalReviewDecisionDocumentV1.create(
        run_id=RUN_ID,
        request_id=f"reviewreq:{SHA}",
        request_sha256=SHA,
        operation_id="review:temporal-1",
        reviewer_id="reviewer@example.com",
        rationale="The exact temporal subjects are accepted.",
        decisions=(
            TemporalReviewDecisionItemV1(
                subject_id=f"tempc:{SHA}",
                subject_sha256=SHA,
                subject_kind=ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT,
                choice=TemporalReviewChoiceV1.ACCEPT,
            ),
        ),
    )
    encoded = request.model_dump_json()
    assert TemporalReviewDecisionDocumentV1.model_validate_json(encoded) == request
    assert parse_review_decision_document_v1(encoded.encode()) == request

    duplicate = encoded[:-1] + ',"request_id":"reviewreq:' + SHA + '"}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_review_decision_document_v1(duplicate.encode())

    payload = request.model_dump(mode="json")
    payload["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_review_decision_document_v1(json.dumps(payload).encode())
    payload.pop("unknown")
    payload["schema_version"] = "1"
    with pytest.raises(ValidationError):
        parse_review_decision_document_v1(json.dumps(payload).encode())
    with pytest.raises(ValueError, match="non-finite"):
        parse_review_decision_document_v1(encoded[:-1].encode() + b',"bad":NaN}')


def test_decision_documents_are_stage_discriminated_canonical_and_content_bound() -> None:
    plan = ManagedReviewDecisionItemV1(
        subject_id=f"mtarget:{'b' * 64}",
        subject_sha256="b" * 64,
        subject_kind=ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN,
        choice=ManagedReviewChoiceV1.APPROVE,
    )
    card = ManagedReviewDecisionItemV1(
        subject_id=f"mtarget:{'c' * 64}",
        subject_sha256="c" * 64,
        subject_kind=ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD,
        choice=ManagedReviewChoiceV1.CONFIRM_NO_CHANGE,
    )
    document = ManagedReviewDecisionDocumentV1.create(
        run_id=RUN_ID,
        request_id=f"mrequest:{SHA}",
        request_sha256=SHA,
        operation_id="review:managed-1",
        reviewer_id="reviewer:one",
        rationale="Approve the revision and confirm the no-change card.",
        decisions=(card, plan),
    )

    assert document.decisions == (plan, card)
    assert (
        document.canonical_sha256
        == hashlib.sha256(canonical_json_bytes(document.model_dump(mode="json"))).hexdigest()
    )
    assert (
        REVIEW_DECISION_DOCUMENT_V1_ADAPTER.validate_json(document.model_dump_json(), strict=True)
        == document
    )
    assert parse_review_decision_document_v1(document.model_dump_json().encode()) == document

    with pytest.raises(ValidationError, match="confirm-no-change"):
        ManagedReviewDecisionItemV1(
            subject_id=f"mtarget:{SHA}",
            subject_sha256=SHA,
            subject_kind=ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN,
            choice=ManagedReviewChoiceV1.CONFIRM_NO_CHANGE,
        )
    with pytest.raises(ValidationError):
        ManagedReviewDecisionDocumentV1.model_validate(
            {**document.model_dump(), "decisions": [*document.decisions, document.decisions[0]]}
        )
    assert "edit" not in {item.value for item in ManagedReviewChoiceV1}
    assert "edit" not in {item.value for item in TemporalReviewChoiceV1}


def test_review_packet_is_path_free_stage_exact_and_preserves_source_text() -> None:
    citation = ChangeReviewCitationV1(
        locator="incoming/source.md",
        sha256=SHA,
        start_byte=0,
        end_byte=18,
        quote="A grounded sentence.",
    )
    subject = ChangeReviewSubjectV1(
        subject_id=f"tempc:{SHA}",
        subject_sha256=SHA,
        subject_kind=ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT,
        statement="The governing interval begins on the declared date.",
        rationale="The source explicitly declares the effective date.",
        citations=(citation,),
    )
    packet = ChangeReviewPacketV1(
        run_id=RUN_ID,
        stage=ChangeReviewStageV1.TEMPORAL,
        request_id=f"reviewreq:{SHA}",
        request_sha256=SHA,
        subjects=(subject,),
    )
    assert ChangeReviewPacketV1.model_validate_json(packet.model_dump_json()) == packet
    assert "/Users/" not in packet.model_dump_json()

    exact = ChangeReviewCitationV1(
        locator="incoming/quoted.md",
        sha256=SHA,
        start_byte=1,
        end_byte=32,
        quote="Read /published/example and cafe\u0301.",
    )
    assert exact.quote == "Read /published/example and cafe\u0301."
    with pytest.raises(ValidationError, match="prefix differs|wrong stage|managed subject"):
        ChangeReviewPacketV1(
            run_id=RUN_ID,
            stage=ChangeReviewStageV1.TEMPORAL,
            request_id=f"reviewreq:{SHA}",
            request_sha256=SHA,
            subjects=(
                subject.model_copy(
                    update={"subject_kind": ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN}
                ),
            ),
        )


def test_status_page_and_verification_are_deterministic() -> None:
    status = _status()
    status_sha = hashlib.sha256(canonical_json_bytes(status.model_dump(mode="json"))).hexdigest()
    result = ChangeVerificationResultV1(
        run_id=RUN_ID,
        phase=status.phase,
        outcome=status.outcome,
        status_sha256=status_sha,
        status=status,
    )
    assert result.model_dump(mode="json")["verified"] is True
    assert "checked_at" not in result.model_dump(mode="json")

    earlier = ChangeRunSummaryV1(
        run_id=f"operatorrun:{'b' * 64}",
        created_at="2026-08-19T10:00:00+00:00",
        phase=status.phase,
        outcome=status.outcome,
        next_action=status.next_action,
        base_authority=status.base_authority,
        current_authority=status.current_authority,
    )
    later = ChangeRunSummaryV1(
        run_id=status.run_id,
        created_at=status.created_at,
        phase=status.phase,
        outcome=status.outcome,
        next_action=status.next_action,
        base_authority=status.base_authority,
        current_authority=status.current_authority,
    )
    assert ChangeRunPageV1(items=(later, earlier)).items == (later, earlier)
    with pytest.raises(ValidationError, match="newest-first"):
        ChangeRunPageV1(items=(earlier, later))


def test_status_requires_exact_phase_evidence_and_matching_completeness() -> None:
    incoming = IncomingEvidenceSummaryV1(
        receipt_id=f"incomingreceipt:{SHA}",
        receipt_sha256=SHA,
        bundle_id=f"generic-bundle-v2:{SHA}",
        bundle_sha256=SHA,
        admission_sha256=SHA,
        source_receipt_sha256=SHA,
        projection_sha256=SHA,
        inference_sha256=SHA,
        source_byte_count=100,
    )
    assert set(incoming.model_dump(mode="json")) == {
        "schema_version",
        "receipt_id",
        "receipt_sha256",
        "bundle_id",
        "bundle_sha256",
        "admission_sha256",
        "source_receipt_sha256",
        "projection_sha256",
        "inference_sha256",
        "source_byte_count",
    }
    suite = RegressionSuiteEvidenceSummaryV1(
        receipt_id=f"suitereceipt:{SHA}",
        receipt_sha256=SHA,
        suite_id="suite-one",
        suite_version=1,
        original_sha256=SHA,
        original_byte_count=200,
        canonical_sha256=SHA,
        case_count=2,
    )
    baseline = GenerationZeroBaselineSummaryV1(
        baseline_id=f"regbaseline:{SHA}",
        receipt_id=f"regreceipt:{SHA}",
        receipt_sha256=SHA,
        case_count=2,
        captured_at="2026-08-20T10:00:00+00:00",
    )
    temporal = ChangeReviewEvidenceSummaryV1(
        stage=ChangeReviewStageV1.TEMPORAL,
        request_id=f"reviewreq:{SHA}",
        request_sha256=SHA,
        subject_count=1,
    )
    status = ChangeRunStatusV1(
        run_id=RUN_ID,
        phase=ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW,
        outcome=ChangeRunOutcomeV1.IN_PROGRESS,
        next_action=ChangeRunNextActionV1.SUBMIT_TEMPORAL_REVIEW,
        created_at="2026-08-20T10:00:00+00:00",
        base_authority=_authority(),
        current_authority=_authority(),
        incoming=incoming,
        suite=suite,
        baseline=baseline,
        temporal_review=temporal,
        completeness=ChangeEvidenceCompletenessV1(
            incoming_complete=True,
            suite_complete=True,
            baseline_complete=True,
            temporal_review_complete=False,
            managed_review_complete=False,
            activation_complete=False,
            regression_case_count=2,
            temporal_subject_count=1,
            managed_subject_count=0,
        ),
    )
    assert status.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW

    invalid = status.model_dump(mode="json")
    invalid["completeness"]["incoming_complete"] = False
    with pytest.raises(ValidationError, match="completeness flags"):
        ChangeRunStatusV1.model_validate_json(json.dumps(invalid))
