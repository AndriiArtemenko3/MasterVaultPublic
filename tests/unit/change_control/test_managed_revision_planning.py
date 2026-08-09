from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from test_impact_analysis import _AuthorityVariants
from test_impact_results import _decision

import mastervault.change_control as change_control
from mastervault.change_control.impact_analysis import ImpactWorkload
from mastervault.change_control.impact_results import (
    ImpactDisposition,
    ImpactResultSet,
)
from mastervault.change_control.managed_revision_planning import (
    MAX_REVISION_PLANNING_RATIONALE_UTF8_BYTES_V1,
    AffectedRevisionEditWire,
    AffectedRevisionWireResponse,
    NoChangeRevisionWireResponse,
    RevisionPlanningCitationInput,
    RevisionPlanningCitationInputRole,
    RevisionPlanningCitationInputSet,
    RevisionPlanningCitationSelector,
    RevisionPlanningEligibility,
    RevisionPlanningEligibilityStatus,
    RevisionPlanningTarget,
    StableSourceClaimStatementRewriteWire,
    UnresolvedImpactForRevisionPlanningError,
    evaluate_revision_planning_eligibility,
    parse_revision_planning_wire_response,
    validate_revision_planning_wire_response,
)

pytest_plugins = ("test_impact_analysis",)


def _results(
    workload: ImpactWorkload,
    dispositions: tuple[ImpactDisposition, ...],
) -> ImpactResultSet:
    assert len(dispositions) == len(workload.input_shards)
    decisions = tuple(
        _decision(shard, disposition=dispositions[shard_index], question_index=question_index)
        for shard_index, shard in enumerate(workload.input_shards)
        for question_index, _question in enumerate(shard.questions)
    )
    return ImpactResultSet.create(workload=workload, decisions=decisions)


def _selector(
    selector: str = "governing-evidence", start_char: int = 0, end_char: int = 1
) -> RevisionPlanningCitationSelector:
    return RevisionPlanningCitationSelector(
        input_selector=selector,
        start_char=start_char,
        end_char=end_char,
    )


def _citation_inputs(
    *,
    governing: str | None = "012345",
    target: str | None = None,
) -> RevisionPlanningCitationInputSet:
    inputs: list[RevisionPlanningCitationInput] = []
    if governing is not None:
        inputs.append(
            RevisionPlanningCitationInput(
                input_selector="governing-evidence",
                role=RevisionPlanningCitationInputRole.GOVERNING_EVIDENCE,
                text_utf8=governing,
            )
        )
    if target is not None:
        inputs.append(
            RevisionPlanningCitationInput(
                input_selector="target-evidence",
                role=RevisionPlanningCitationInputRole.TARGET_EVIDENCE,
                text_utf8=target,
            )
        )
    return RevisionPlanningCitationInputSet(inputs=tuple(inputs))


def _target(*, kind: str = "affected-revision") -> RevisionPlanningTarget:
    return RevisionPlanningTarget(
        target_key="returns-faq",
        document_version_id="docv:" + "1" * 64,
        input_shard_id="impactin:" + "2" * 64,
        input_shard_sha256="2" * 64,
        output_shard_id="impactout:" + "3" * 64,
        output_shard_sha256="3" * 64,
        question_ids=("impactq:" + "4" * 64,),
        required_response_kind=kind,
    )


def _target_variant(*, target_key: str, document_sha: str) -> RevisionPlanningTarget:
    payload = _target().model_dump(mode="python")
    payload["target_key"] = target_key
    payload["document_version_id"] = "docv:" + document_sha * 64
    return RevisionPlanningTarget(**payload)


def _eligibility_kwargs(
    targets: tuple[RevisionPlanningTarget, ...],
) -> dict[str, object]:
    return {
        "status": RevisionPlanningEligibilityStatus.ELIGIBLE,
        "workload_id": "impactwork:" + "5" * 64,
        "workload_sha256": "5" * 64,
        "result_id": "impactresult:" + "6" * 64,
        "result_sha256": "6" * 64,
        "targets": targets,
    }


def _affected() -> AffectedRevisionWireResponse:
    return AffectedRevisionWireResponse(
        target_key="returns-faq",
        question_ids=("impactq:" + "4" * 64,),
        edits=(
            AffectedRevisionEditWire(
                start_char=1,
                end_char=3,
                replacement_text="45",
                citations=(_selector(start_char=1, end_char=3),),
            ),
        ),
        source_claim_statement_rewrites=(
            StableSourceClaimStatementRewriteWire(
                source_claim_id="faq-return-window",
                replacement_statement="Premium returns are accepted for 45 days.",
                edit_ordinals=(0,),
            ),
        ),
        rationale="The governing evidence changes the existing return window.",
    )


def test_empty_impact_result_is_a_truthful_no_work_gate(
    authority_variants: _AuthorityVariants,
) -> None:
    workload = change_control.build_impact_workload(authority_variants.all_rejected)
    results = ImpactResultSet.create(workload=workload, decisions=())

    eligibility = evaluate_revision_planning_eligibility(results)

    assert eligibility.status == RevisionPlanningEligibilityStatus.NO_WORK
    assert eligibility.targets == ()
    assert eligibility.workload_id == workload.index.workload_id
    assert eligibility.result_id == results.result_id


def test_unresolved_target_blocks_the_complete_result_before_targets_are_returned(
    impact_fixture: tuple[object, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    dispositions = tuple(
        ImpactDisposition.UNRESOLVED if index == 0 else ImpactDisposition.NO_CHANGE_REQUIRED
        for index, _shard in enumerate(workload.input_shards)
    )
    results = _results(workload, dispositions)
    expected = workload.input_shards[0].target_note.document.document_id

    with pytest.raises(UnresolvedImpactForRevisionPlanningError) as captured:
        evaluate_revision_planning_eligibility(results)

    assert captured.value.target_keys == (expected,)


def test_affected_and_no_change_targets_proceed_once_in_canonical_order(
    impact_fixture: tuple[object, ImpactWorkload],
) -> None:
    _authority, workload = impact_fixture
    dispositions = tuple(
        ImpactDisposition.AFFECTED if index == 0 else ImpactDisposition.NO_CHANGE_REQUIRED
        for index, _shard in enumerate(workload.input_shards)
    )
    results = _results(workload, dispositions)

    eligibility = evaluate_revision_planning_eligibility(results)

    assert eligibility.status == RevisionPlanningEligibilityStatus.ELIGIBLE
    assert len(eligibility.targets) == len(workload.input_shards)
    assert len({item.document_version_id for item in eligibility.targets}) == len(
        eligibility.targets
    )
    assert {item.required_response_kind for item in eligibility.targets} <= {
        "affected-revision",
        "no-change",
    }
    by_input = {item.shard_id: item for item in workload.input_shards}
    for target in eligibility.targets:
        assert target.question_ids == tuple(
            question.question_id for question in by_input[target.input_shard_id].questions
        )


def test_wire_rejects_provider_authored_authority_fields_and_disposition() -> None:
    payload = _affected().model_dump(mode="json")
    forbidden = {
        "path": "vault/returns.md",
        "sha256": "a" * 64,
        "provenance": "provider-authored",
        "plan_id": "mplan:" + "a" * 64,
        "successor": {"version": "v2"},
        "effective_from": "2030-01-01",
        "disposition": "AFFECTED",
        "source_note_utf8": "complete note",
    }
    for field, value in forbidden.items():
        with pytest.raises(ValidationError, match="extra_forbidden"):
            parse_revision_planning_wire_response(
                json.dumps({**payload, field: value}, separators=(",", ":"))
            )


def test_wire_requires_ordered_non_overlapping_edits_and_bound_claim_rewrites() -> None:
    citation = _selector()
    with pytest.raises(ValidationError, match="must not overlap"):
        AffectedRevisionWireResponse(
            target_key="returns-faq",
            question_ids=("impactq:" + "4" * 64,),
            edits=(
                AffectedRevisionEditWire(
                    start_char=0,
                    end_char=3,
                    replacement_text="first",
                    citations=(citation,),
                ),
                AffectedRevisionEditWire(
                    start_char=2,
                    end_char=4,
                    replacement_text="second",
                    citations=(citation,),
                ),
            ),
            source_claim_statement_rewrites=(),
            rationale="Two overlapping edits are unsafe.",
        )
    with pytest.raises(ValidationError, match="absent edit ordinal"):
        AffectedRevisionWireResponse(
            target_key="returns-faq",
            question_ids=("impactq:" + "4" * 64,),
            edits=(_affected().edits[0],),
            source_claim_statement_rewrites=(
                StableSourceClaimStatementRewriteWire(
                    source_claim_id="faq-return-window",
                    replacement_statement="A changed statement.",
                    edit_ordinals=(1,),
                ),
            ),
            rationale="The rewrite must bind a real edit ordinal.",
        )


def test_wire_uses_python_character_ranges_for_unicode_without_normalization() -> None:
    predecessor = "A😀e\u0301漢Z"
    evidence = "X😀e\u0301漢Y"
    response = AffectedRevisionWireResponse(
        target_key="returns-faq",
        question_ids=("impactq:" + "4" * 64,),
        edits=(
            AffectedRevisionEditWire(
                start_char=1,
                end_char=5,
                replacement_text="45 days",
                citations=(_selector(start_char=1, end_char=5),),
            ),
        ),
        source_claim_statement_rewrites=(
            StableSourceClaimStatementRewriteWire(
                source_claim_id="faq-return-window",
                replacement_statement="Prefix 45 days suffix.",
                edit_ordinals=(0,),
            ),
        ),
        rationale="Unicode offsets refer to Python characters in exact input text.",
    )

    validated = validate_revision_planning_wire_response(
        response,
        target=_target(),
        predecessor_raw_utf8=predecessor,
        citation_inputs=_citation_inputs(governing=evidence),
        existing_claim_statements={
            "faq-return-window": "Prefix 😀e\u0301漢 suffix."
        },
    )

    assert predecessor[validated.edits[0].start_char : validated.edits[0].end_char] == "😀e\u0301漢"
    assert len("😀e\u0301漢".encode("utf-8")) > (
        validated.edits[0].end_char - validated.edits[0].start_char
    )


def test_local_validation_rejects_nonempty_and_empty_no_op_edits() -> None:
    affected = _affected()
    nonempty_no_op = affected.model_copy(
        update={
            "edits": (
                affected.edits[0].model_copy(update={"replacement_text": "12"}),
            )
        }
    )
    with pytest.raises(ValueError, match="must change the exact predecessor"):
        validate_revision_planning_wire_response(
            nonempty_no_op,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={"faq-return-window": "Old statement."},
        )

    empty_no_op = AffectedRevisionWireResponse(
        target_key="returns-faq",
        question_ids=("impactq:" + "4" * 64,),
        edits=(
            AffectedRevisionEditWire(
                start_char=2,
                end_char=2,
                replacement_text="",
                citations=(_selector(),),
            ),
        ),
        source_claim_statement_rewrites=(
            StableSourceClaimStatementRewriteWire(
                source_claim_id="faq-return-window",
                replacement_statement="An inserted change.",
                edit_ordinals=(0,),
            ),
        ),
        rationale="An empty insertion cannot be accepted as a semantic revision.",
    )
    with pytest.raises(ValueError, match="must change the exact predecessor"):
        validate_revision_planning_wire_response(
            empty_no_op,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={"faq-return-window": "Current statement."},
        )


def test_affected_revision_requires_complete_mechanical_claim_rewrites() -> None:
    affected = _affected()
    coherent_claim = {
        "faq-return-window": "Premium returns are accepted for 12 days."
    }
    assert (
        validate_revision_planning_wire_response(
            affected,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements=coherent_claim,
        )
        == affected
    )

    omitted = affected.model_copy(update={"source_claim_statement_rewrites": ()})
    with pytest.raises(ValidationError, match="every revision edit"):
        validate_revision_planning_wire_response(
            omitted,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements=coherent_claim,
        )

    fabricated = affected.model_copy(
        update={
            "source_claim_statement_rewrites": (
                affected.source_claim_statement_rewrites[0].model_copy(
                    update={
                        "replacement_statement": (
                            "Premium returns are accepted for 90 days."
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="mechanically rewritten"):
        validate_revision_planning_wire_response(
            fabricated,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements=coherent_claim,
        )


def test_question_and_content_ids_fail_closed_directly_and_after_json_round_trip() -> None:
    with pytest.raises(ValidationError):
        AffectedRevisionWireResponse(
            target_key="returns-faq",
            question_ids=("not-an-impact-question",),
            edits=_affected().edits,
            source_claim_statement_rewrites=(),
            rationale="Malformed question identities cannot cross the direct model boundary.",
        )

    wire_payload = _affected().model_dump(mode="json")
    wire_payload["question_ids"] = ["impactq:not-a-sha"]
    with pytest.raises(ValidationError):
        parse_revision_planning_wire_response(json.dumps(wire_payload))

    target_payload = _target().model_dump(mode="python")
    target_payload["input_shard_id"] = "impactin:" + "9" * 64
    with pytest.raises(ValidationError, match="input shard ID differs"):
        RevisionPlanningTarget(**target_payload)
    target_payload = _target().model_dump(mode="python")
    target_payload["output_shard_id"] = "impactout:" + "9" * 64
    with pytest.raises(ValidationError, match="output shard ID differs"):
        RevisionPlanningTarget(**target_payload)

    eligibility = RevisionPlanningEligibility(
        status=RevisionPlanningEligibilityStatus.ELIGIBLE,
        workload_id="impactwork:" + "5" * 64,
        workload_sha256="5" * 64,
        result_id="impactresult:" + "6" * 64,
        result_sha256="6" * 64,
        targets=(_target(),),
    )
    with pytest.raises(ValidationError):
        RevisionPlanningEligibility(
            status=RevisionPlanningEligibilityStatus.ELIGIBLE,
            workload_id="wrong-prefix:" + "5" * 64,
            workload_sha256="5" * 64,
            result_id="impactresult:" + "6" * 64,
            result_sha256="6" * 64,
            targets=(_target(),),
        )

    eligibility_payload = eligibility.model_dump(mode="json")
    eligibility_payload["workload_id"] = "impactwork:" + "7" * 64
    with pytest.raises(ValidationError, match="workload ID differs"):
        RevisionPlanningEligibility.model_validate_json(json.dumps(eligibility_payload))
    eligibility_payload = eligibility.model_dump(mode="json")
    eligibility_payload["result_id"] = "impactresult:" + "7" * 64
    with pytest.raises(ValidationError, match="result ID differs"):
        RevisionPlanningEligibility.model_validate_json(json.dumps(eligibility_payload))
    eligibility_payload = eligibility.model_dump(mode="json")
    eligibility_payload["targets"][0]["question_ids"] = ["invalid-question"]
    with pytest.raises(ValidationError):
        RevisionPlanningEligibility.model_validate_json(json.dumps(eligibility_payload))


def test_eligibility_rejects_target_and_document_collisions_directly_and_from_json() -> None:
    duplicate_key_targets = (
        _target_variant(target_key="returns-faq", document_sha="1"),
        _target_variant(target_key="returns-faq", document_sha="7"),
    )
    with pytest.raises(ValidationError, match="target keys must be unique"):
        RevisionPlanningEligibility(**_eligibility_kwargs(duplicate_key_targets))
    duplicate_key_payload = {
        **_eligibility_kwargs(duplicate_key_targets),
        "status": RevisionPlanningEligibilityStatus.ELIGIBLE.value,
        "targets": [item.model_dump(mode="json") for item in duplicate_key_targets],
    }
    with pytest.raises(ValidationError, match="target keys must be unique"):
        RevisionPlanningEligibility.model_validate_json(json.dumps(duplicate_key_payload))

    duplicate_document_targets = (
        _target_variant(target_key="returns-faq", document_sha="1"),
        _target_variant(target_key="z-returns-faq", document_sha="1"),
    )
    with pytest.raises(ValidationError, match="document versions must be unique"):
        RevisionPlanningEligibility(**_eligibility_kwargs(duplicate_document_targets))
    duplicate_document_payload = {
        **_eligibility_kwargs(duplicate_document_targets),
        "status": RevisionPlanningEligibilityStatus.ELIGIBLE.value,
        "targets": [item.model_dump(mode="json") for item in duplicate_document_targets],
    }
    with pytest.raises(ValidationError, match="document versions must be unique"):
        RevisionPlanningEligibility.model_validate_json(json.dumps(duplicate_document_payload))


def test_citation_allowlist_roles_and_non_whitespace_spans_are_enforced() -> None:
    affected = _affected()
    target_role_under_governing_selector = RevisionPlanningCitationInputSet(
        inputs=(
            RevisionPlanningCitationInput(
                input_selector="governing-evidence",
                role=RevisionPlanningCitationInputRole.TARGET_EVIDENCE,
                text_utf8="012345",
            ),
        )
    )
    with pytest.raises(ValueError, match="requires governing-evidence grounding"):
        validate_revision_planning_wire_response(
            affected,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=target_role_under_governing_selector,
            existing_claim_statements={"faq-return-window": "Old statement."},
        )

    whitespace_response = affected.model_copy(
        update={
            "edits": (
                affected.edits[0].model_copy(
                    update={"citations": (_selector(start_char=0, end_char=2),)}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="non-whitespace evidence"):
        validate_revision_planning_wire_response(
            whitespace_response,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(governing="  governing evidence"),
            existing_claim_statements={"faq-return-window": "Old statement."},
        )

    no_change = NoChangeRevisionWireResponse(
        target_key="returns-faq",
        question_ids=("impactq:" + "4" * 64,),
        citations=(_selector(),),
        rationale="No exact semantic change is required for this target.",
    )
    with pytest.raises(ValueError, match="requires target-evidence grounding"):
        validate_revision_planning_wire_response(
            no_change,
            target=_target(kind="no-change"),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={},
        )

    target_grounded = no_change.model_copy(
        update={"citations": (_selector("target-evidence", 0, 6),)}
    )
    assert (
        validate_revision_planning_wire_response(
            target_grounded,
            target=_target(kind="no-change"),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(governing=None, target="target evidence"),
            existing_claim_statements={},
        )
        == target_grounded
    )


@pytest.mark.parametrize(
    "statement",
    (
        "short",
        "Repeated  whitespace is invalid.",
        "Ｐｒｅｍｉｕｍ returns remain valid.",
    ),
)
def test_claim_statement_rewrites_match_versioned_claim_text_semantics(statement: str) -> None:
    with pytest.raises(ValidationError):
        StableSourceClaimStatementRewriteWire(
            source_claim_id="faq-return-window",
            replacement_statement=statement,
            edit_ordinals=(0,),
        )


def test_local_validation_rejects_wrong_target_kind_range_selector_and_claim() -> None:
    affected = _affected()
    with pytest.raises(ValueError, match="exact selected target"):
        validate_revision_planning_wire_response(
            affected.model_copy(update={"target_key": "another-target"}),
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={"faq-return-window": "Old statement."},
        )
    with pytest.raises(ValueError, match="exceeds exact predecessor"):
        validate_revision_planning_wire_response(
            affected.model_copy(
                update={"edits": (affected.edits[0].model_copy(update={"end_char": 99}),)}
            ),
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={"faq-return-window": "Old statement."},
        )
    with pytest.raises(ValueError, match="unavailable inference input"):
        validate_revision_planning_wire_response(
            affected,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(governing=None, target="target evidence"),
            existing_claim_statements={"faq-return-window": "Old statement."},
        )
    with pytest.raises(ValueError, match="non-existing stable source claim"):
        validate_revision_planning_wire_response(
            affected,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={},
        )

    no_change = NoChangeRevisionWireResponse(
        target_key="returns-faq",
        question_ids=("impactq:" + "4" * 64,),
        citations=(_selector(),),
        rationale="No exact semantic change is required for this target.",
    )
    with pytest.raises(ValueError, match="kind differs"):
        validate_revision_planning_wire_response(
            no_change,
            target=_target(),
            predecessor_raw_utf8="012345",
            citation_inputs=_citation_inputs(),
            existing_claim_statements={},
        )


def test_wire_enforces_utf8_rationale_and_strict_scalar_types() -> None:
    payload = _affected().model_dump(mode="json")
    payload["rationale"] = "é" * (MAX_REVISION_PLANNING_RATIONALE_UTF8_BYTES_V1 // 2 + 1)
    with pytest.raises(ValidationError, match="UTF-8 byte limit"):
        parse_revision_planning_wire_response(json.dumps(payload))

    scalar = _affected().model_dump(mode="json")
    scalar["edits"][0]["start_char"] = "1"
    with pytest.raises(ValidationError):
        parse_revision_planning_wire_response(json.dumps(scalar))
