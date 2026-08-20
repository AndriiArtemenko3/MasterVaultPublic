"""Strict, path-free public contracts for the synchronous change lifecycle.

Runtime filesystem paths occur only on :class:`StartChangeRequestV1`.  Pydantic
excludes those fields from every serialization, so public and durable
projections cannot accidentally disclose a host path.  The application layer
is responsible for admitting the bytes at those paths and content-binding its
own durable commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.operator_run import decode_operator_run_cursor
from mastervault.change_control.review import normalize_actor_id
from mastervault.models import Domain

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_SAFE_LOCATOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_LOGICAL_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_MAX_TEXT_CHARS = 4000


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value!r} is unsupported")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validate strict JSON after a duplicate/non-finite syntax scan."""

        if strict is False or extra not in {None, "forbid"}:
            raise ValueError("strict public JSON validation cannot be relaxed")
        try:
            json.loads(
                json_data,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON document") from exc
        return super().model_validate_json(
            json_data,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


def _safe_identifier(value: str, *, label: str) -> str:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        _SAFE_ID_RE.fullmatch(value) is None
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
    ):
        raise ValueError(f"{label} is not a safe canonical identifier")
    return value


def _operation_id(value: str) -> str:
    if _OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("operation_id is not a safe canonical identifier")
    return value


def _canonical_text(value: str, *, label: str) -> str:
    if not value or len(value) > _MAX_TEXT_CHARS or value != " ".join(value.split()):
        raise ValueError(f"{label} must be canonical non-empty text")
    return value


def _phase_invariant(
    phase: ChangeRunPhaseV1,
    outcome: ChangeRunOutcomeV1,
    next_action: ChangeRunNextActionV1,
) -> None:
    terminal = {
        ChangeRunPhaseV1.ACTIVATED: ChangeRunOutcomeV1.ACTIVATED,
        ChangeRunPhaseV1.REJECTED_NO_OP: ChangeRunOutcomeV1.REJECTED_NO_OP,
        ChangeRunPhaseV1.COMPLETED_NO_OP: ChangeRunOutcomeV1.COMPLETED_NO_OP,
    }
    if phase in terminal:
        if outcome != terminal[phase] or next_action != ChangeRunNextActionV1.NONE:
            raise ValueError("terminal phase requires its matching outcome and no next action")
        return
    if outcome != ChangeRunOutcomeV1.IN_PROGRESS:
        raise ValueError("non-terminal phase requires in-progress outcome")
    expected_action = {
        ChangeRunPhaseV1.BOOTSTRAPPED: ChangeRunNextActionV1.RESUME,
        ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW: ChangeRunNextActionV1.SUBMIT_TEMPORAL_REVIEW,
        ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW: ChangeRunNextActionV1.SUBMIT_MANAGED_REVIEW,
        ChangeRunPhaseV1.READY_TO_ACTIVATE: ChangeRunNextActionV1.ACTIVATE,
    }[phase]
    if next_action != expected_action:
        raise ValueError("phase and next_action disagree")


def _canonical_utc(value: str) -> str:
    from datetime import datetime

    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("timestamp must be canonical UTC with second precision")
    return value


def _safe_locator(value: str) -> str:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        _SAFE_LOCATOR_RE.fullmatch(value) is None
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or posix.as_posix() != value
        or any(part.startswith(".") for part in posix.parts)
    ):
        raise ValueError("locator must be a safe canonical relative POSIX path")
    return value


class ChangeExecutionModeV1(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class ChangeRunPhaseV1(StrEnum):
    BOOTSTRAPPED = "bootstrapped"
    AWAITING_TEMPORAL_REVIEW = "awaiting-temporal-review"
    AWAITING_MANAGED_REVIEW = "awaiting-managed-review"
    READY_TO_ACTIVATE = "ready-to-activate"
    ACTIVATED = "activated"
    REJECTED_NO_OP = "rejected-no-op"
    COMPLETED_NO_OP = "completed-no-op"


class ChangeRunOutcomeV1(StrEnum):
    IN_PROGRESS = "in-progress"
    ACTIVATED = "activated"
    REJECTED_NO_OP = "rejected-no-op"
    COMPLETED_NO_OP = "completed-no-op"


class ChangeRunNextActionV1(StrEnum):
    RESUME = "resume"
    SUBMIT_TEMPORAL_REVIEW = "submit-temporal-review"
    SUBMIT_MANAGED_REVIEW = "submit-managed-review"
    ACTIVATE = "activate"
    NONE = "none"


class ChangeReviewStageV1(StrEnum):
    TEMPORAL = "temporal"
    MANAGED = "managed"


class ChangeReviewSubjectKindV1(StrEnum):
    DOCUMENT_REPLACEMENT = "document-replacement"
    TEMPORAL_CONSTRAINT = "temporal-constraint"
    MANAGED_REVISION_PLAN = "managed-revision-plan"
    NO_CHANGE_IMPACT_CARD = "no-change-impact-card"


class TemporalReviewChoiceV1(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class ManagedReviewChoiceV1(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CONFIRM_NO_CHANGE = "confirm-no-change"


class StartChangeRequestV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    operation_id: str
    requested_run_id: str | None = Field(default=None, pattern=_RUN_ID_RE.pattern)
    source: Path = Field(exclude=True)
    domain: Domain
    regression_suite: Path = Field(exclude=True)
    mode: ChangeExecutionModeV1
    replay_bundle: Path | None = Field(default=None, exclude=True)

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _operation_id(value)

    @model_validator(mode="after")
    def _runtime_shape(self) -> Self:
        if (self.mode == ChangeExecutionModeV1.REPLAY) != (self.replay_bundle is not None):
            raise ValueError("replay mode requires replay_bundle and live mode forbids it")
        source = Path(os.path.abspath(os.fspath(self.source)))
        suite = Path(os.path.abspath(os.fspath(self.regression_suite)))
        if source == suite:
            raise ValueError("source and regression_suite must be distinct files")
        return self


class AuthoritySummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    authority_id: str = Field(pattern=r"^mauthority:[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    generation_id: str = Field(pattern=r"^mgeneration:[0-9a-f]{64}$")
    generation_number: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    active_pointer_sha256: str = Field(pattern=SHA256_PATTERN)
    is_active: bool


class IncomingEvidenceSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^incomingreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle_id: str = Field(pattern=r"^generic-bundle-v2:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    admission_sha256: str = Field(pattern=SHA256_PATTERN)
    source_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_sha256: str = Field(pattern=SHA256_PATTERN)
    inference_sha256: str = Field(pattern=SHA256_PATTERN)
    source_byte_count: int = Field(ge=1, le=64 * 1024)

    @model_validator(mode="after")
    def _bundle(self) -> Self:
        if (
            self.receipt_id != f"incomingreceipt:{self.receipt_sha256}"
            or self.bundle_id != f"generic-bundle-v2:{self.bundle_sha256}"
        ):
            raise ValueError("incoming receipt or bundle ID differs from its exact SHA")
        return self


class RegressionSuiteEvidenceSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^suitereceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    suite_id: str
    suite_version: int = Field(ge=1)
    original_sha256: str = Field(pattern=SHA256_PATTERN)
    original_byte_count: int = Field(ge=1, le=1024 * 1024)
    canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    case_count: int = Field(ge=1, le=128)

    @field_validator("suite_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if _LOGICAL_ID_RE.fullmatch(value) is None:
            raise ValueError("suite_id is not a canonical logical identifier")
        return value

    @model_validator(mode="after")
    def _receipt(self) -> Self:
        if self.receipt_id != f"suitereceipt:{self.receipt_sha256}":
            raise ValueError("suite receipt ID differs from its exact SHA")
        return self


class GenerationZeroBaselineSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    baseline_id: str = Field(pattern=r"^regbaseline:[0-9a-f]{64}$")
    receipt_id: str = Field(pattern=r"^regreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    case_count: int = Field(ge=1, le=128)
    captured_at: str

    @field_validator("captured_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _receipt(self) -> Self:
        if self.receipt_id != f"regreceipt:{self.receipt_sha256}":
            raise ValueError("baseline receipt ID differs from its exact SHA")
        return self


class ChangeReviewEvidenceSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    stage: ChangeReviewStageV1
    request_id: str
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    subject_count: int = Field(ge=1)
    decision_id: str | None = None
    decision_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("request_id", "decision_id")
    @classmethod
    def _ids(cls, value: str | None) -> str | None:
        return _safe_identifier(value, label="review evidence ID") if value is not None else None

    @model_validator(mode="after")
    def _decision_pair(self) -> Self:
        if (self.decision_id is None) != (self.decision_sha256 is None):
            raise ValueError("decision_id and decision_sha256 must appear together")
        request_prefix = "reviewreq:" if self.stage == ChangeReviewStageV1.TEMPORAL else "mrequest:"
        if re.fullmatch(rf"{request_prefix}[0-9a-f]{{64}}", self.request_id) is None:
            raise ValueError("review request ID prefix differs from its exact stage")
        if self.decision_id is not None:
            decision_prefix = (
                "reviewreq:" if self.stage == ChangeReviewStageV1.TEMPORAL else "mdecision:"
            )
            if re.fullmatch(rf"{decision_prefix}[0-9a-f]{{64}}", self.decision_id) is None:
                raise ValueError("review decision ID prefix differs from its exact stage")
        return self


class ChangeActivationEvidenceSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^mgenerationactivation:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_id: str = Field(pattern=r"^mgeneration:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _receipt(self) -> Self:
        if self.receipt_id != f"mgenerationactivation:{self.receipt_sha256}":
            raise ValueError("activation receipt ID differs from its exact SHA")
        return self


class ChangeEvidenceCompletenessV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    incoming_complete: bool
    suite_complete: bool
    baseline_complete: bool
    temporal_review_complete: bool
    managed_review_complete: bool
    activation_complete: bool
    regression_case_count: int = Field(ge=0, le=128)
    temporal_subject_count: int = Field(ge=0)
    managed_subject_count: int = Field(ge=0)


class ChangeRunStatusV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    phase: ChangeRunPhaseV1
    outcome: ChangeRunOutcomeV1
    next_action: ChangeRunNextActionV1
    created_at: str
    base_authority: AuthoritySummaryV1
    current_authority: AuthoritySummaryV1
    incoming: IncomingEvidenceSummaryV1 | None = None
    suite: RegressionSuiteEvidenceSummaryV1 | None = None
    baseline: GenerationZeroBaselineSummaryV1 | None = None
    temporal_review: ChangeReviewEvidenceSummaryV1 | None = None
    managed_review: ChangeReviewEvidenceSummaryV1 | None = None
    activation: ChangeActivationEvidenceSummaryV1 | None = None
    completeness: ChangeEvidenceCompletenessV1

    @field_validator("created_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _lifecycle(self) -> Self:
        _phase_invariant(self.phase, self.outcome, self.next_action)
        if self.temporal_review is not None and self.temporal_review.stage != "temporal":
            raise ValueError("temporal_review carries the wrong stage")
        if self.managed_review is not None and self.managed_review.stage != "managed":
            raise ValueError("managed_review carries the wrong stage")

        completeness = self.completeness
        temporal_decided = (
            self.temporal_review is not None and self.temporal_review.decision_id is not None
        )
        managed_decided = (
            self.managed_review is not None and self.managed_review.decision_id is not None
        )
        exact_flags = (
            (completeness.incoming_complete, self.incoming is not None),
            (completeness.suite_complete, self.suite is not None),
            (completeness.baseline_complete, self.baseline is not None),
            (completeness.temporal_review_complete, temporal_decided),
            (completeness.managed_review_complete, managed_decided),
            (completeness.activation_complete, self.activation is not None),
        )
        if any(flag != present for flag, present in exact_flags):
            raise ValueError("completeness flags differ from exact evidence summaries")
        if completeness.regression_case_count != (self.suite.case_count if self.suite else 0):
            raise ValueError("regression case count differs from suite evidence")
        if self.baseline is not None and (
            self.baseline.case_count != completeness.regression_case_count
        ):
            raise ValueError("baseline case count differs from admitted suite")
        if completeness.temporal_subject_count != (
            self.temporal_review.subject_count if self.temporal_review else 0
        ):
            raise ValueError("temporal subject count differs from review evidence")
        if completeness.managed_subject_count != (
            self.managed_review.subject_count if self.managed_review else 0
        ):
            raise ValueError("managed subject count differs from review evidence")

        base_evidence = (
            self.incoming is not None and self.suite is not None and self.baseline is not None
        )
        if self.phase == ChangeRunPhaseV1.BOOTSTRAPPED:
            if self.activation is not None or self.current_authority.generation_number != 0:
                raise ValueError("bootstrapped phase cannot carry successor activation")
        elif self.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW:
            if not base_evidence or self.temporal_review is None or temporal_decided:
                raise ValueError("temporal review phase requires one exact open temporal request")
            if self.managed_review is not None or self.activation is not None:
                raise ValueError("temporal review phase cannot contain later-stage evidence")
        elif self.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW:
            if (
                not base_evidence
                or not temporal_decided
                or self.managed_review is None
                or managed_decided
            ):
                raise ValueError(
                    "managed review phase requires decided temporal and open managed evidence"
                )
            if self.activation is not None:
                raise ValueError("managed review phase cannot contain activation evidence")
        elif self.phase == ChangeRunPhaseV1.READY_TO_ACTIVATE:
            if not base_evidence or not temporal_decided or not managed_decided:
                raise ValueError("ready phase requires both exact review decisions")
            if self.activation is not None:
                raise ValueError("ready phase cannot contain activation evidence")
        elif self.phase == ChangeRunPhaseV1.ACTIVATED:
            if (
                not base_evidence
                or not temporal_decided
                or not managed_decided
                or self.activation is None
                or self.activation.generation_id != self.current_authority.generation_id
                or self.current_authority.generation_number != 1
                or not self.current_authority.is_active
            ):
                raise ValueError(
                    "activated phase requires complete evidence and active generation one"
                )
        elif not base_evidence or self.activation is not None:
            raise ValueError("terminal no-op requires base evidence and forbids activation")
        return self


class ChangeRunSummaryV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    created_at: str
    phase: ChangeRunPhaseV1
    outcome: ChangeRunOutcomeV1
    next_action: ChangeRunNextActionV1
    base_authority: AuthoritySummaryV1
    current_authority: AuthoritySummaryV1

    @field_validator("created_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _lifecycle(self) -> Self:
        _phase_invariant(self.phase, self.outcome, self.next_action)
        return self


class ChangeRunPageV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    items: tuple[ChangeRunSummaryV1, ...]
    next_cursor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,2048}$")

    @field_validator("next_cursor")
    @classmethod
    def _cursor(cls, value: str | None) -> str | None:
        if value is not None:
            decode_operator_run_cursor(value)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        keys = tuple((item.created_at, item.run_id) for item in self.items)
        if keys != tuple(sorted(keys, reverse=True)) or len(set(keys)) != len(keys):
            raise ValueError("run page items must be unique in deterministic newest-first order")
        return self


class ChangeReviewCitationV1(_StrictFrozenModel):
    locator: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)

    @field_validator("locator")
    @classmethod
    def _locator(cls, value: str) -> str:
        return _safe_locator(value)

    @field_validator("quote")
    @classmethod
    def _quote(cls, value: str) -> str:
        if not value.encode("utf-8"):
            raise ValueError("citation quote must contain exact UTF-8 text")
        return value

    @model_validator(mode="after")
    def _span(self) -> Self:
        if self.end_byte <= self.start_byte:
            raise ValueError("citation byte span must be non-empty")
        return self


class ChangeReviewSubjectV1(_StrictFrozenModel):
    subject_id: str
    subject_sha256: str = Field(pattern=SHA256_PATTERN)
    subject_kind: ChangeReviewSubjectKindV1
    target_key: str | None = None
    document_version_id: str | None = None
    statement: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    citations: tuple[ChangeReviewCitationV1, ...] = ()

    @field_validator("subject_id", "target_key", "document_version_id")
    @classmethod
    def _ids(cls, value: str | None) -> str | None:
        return _safe_identifier(value, label="review subject identifier") if value else value

    @field_validator("statement", "rationale")
    @classmethod
    def _text(cls, value: str) -> str:
        return _canonical_text(value, label="review subject text")

    @model_validator(mode="after")
    def _kind_identity(self) -> Self:
        prefixes = {
            ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT: "rel:",
            ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT: "tempc:",
            ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN: "mtarget:",
            ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD: "mtarget:",
        }
        if re.fullmatch(rf"{prefixes[self.subject_kind]}[0-9a-f]{{64}}", self.subject_id) is None:
            raise ValueError("review subject ID prefix differs from its exact kind")
        if self.subject_kind in {
            ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN,
            ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD,
        }:
            if self.subject_id != f"mtarget:{self.subject_sha256}":
                raise ValueError("managed target ID differs from its exact subject SHA")
            if self.target_key is None or self.document_version_id is None:
                raise ValueError("managed review subjects require target and document identities")
        if self.target_key is not None and _LOGICAL_ID_RE.fullmatch(self.target_key) is None:
            raise ValueError("target_key is not a canonical logical identifier")
        if (
            self.document_version_id is not None
            and re.fullmatch(r"docv:[0-9a-f]{64}", self.document_version_id) is None
        ):
            raise ValueError("document_version_id is not an exact content ID")
        return self

    @field_validator("citations")
    @classmethod
    def _citations(
        cls, values: tuple[ChangeReviewCitationV1, ...]
    ) -> tuple[ChangeReviewCitationV1, ...]:
        keys = tuple((item.locator, item.start_byte, item.end_byte, item.sha256) for item in values)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("citations must be unique and canonically ordered")
        return values


class ChangeReviewPacketV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    stage: ChangeReviewStageV1
    request_id: str
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    subjects: tuple[ChangeReviewSubjectV1, ...] = Field(min_length=1)

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return _safe_identifier(value, label="request_id")

    @field_validator("subjects")
    @classmethod
    def _subjects(
        cls, values: tuple[ChangeReviewSubjectV1, ...]
    ) -> tuple[ChangeReviewSubjectV1, ...]:
        keys = tuple((item.subject_kind.value, item.subject_id) for item in values)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("review subjects must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def _stage_subjects(self) -> Self:
        temporal = {
            ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT,
            ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT,
        }
        if self.stage == ChangeReviewStageV1.TEMPORAL and any(
            item.subject_kind not in temporal for item in self.subjects
        ):
            raise ValueError("temporal packet contains a managed subject")
        if self.stage == ChangeReviewStageV1.MANAGED and any(
            item.subject_kind in temporal for item in self.subjects
        ):
            raise ValueError("managed packet contains a temporal subject")
        request_prefix = "reviewreq:" if self.stage == ChangeReviewStageV1.TEMPORAL else "mrequest:"
        if re.fullmatch(rf"{request_prefix}[0-9a-f]{{64}}", self.request_id) is None:
            raise ValueError("review packet request ID differs from its exact stage")
        return self


class TemporalReviewDecisionItemV1(_StrictFrozenModel):
    subject_id: str
    subject_sha256: str = Field(pattern=SHA256_PATTERN)
    subject_kind: Literal[
        ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT,
        ChangeReviewSubjectKindV1.TEMPORAL_CONSTRAINT,
    ]
    choice: TemporalReviewChoiceV1

    @field_validator("subject_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_identifier(value, label="temporal subject_id")

    @model_validator(mode="after")
    def _kind_identity(self) -> Self:
        prefix = "rel:" if self.subject_kind == "document-replacement" else "tempc:"
        if re.fullmatch(rf"{prefix}[0-9a-f]{{64}}", self.subject_id) is None:
            raise ValueError("temporal subject ID prefix differs from its exact kind")
        return self


class ManagedReviewDecisionItemV1(_StrictFrozenModel):
    subject_id: str
    subject_sha256: str = Field(pattern=SHA256_PATTERN)
    subject_kind: Literal[
        ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN,
        ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD,
    ]
    choice: ManagedReviewChoiceV1

    @field_validator("subject_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_identifier(value, label="managed subject_id")

    @model_validator(mode="after")
    def _choice_shape(self) -> Self:
        is_no_change = self.subject_kind == ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD
        if self.subject_id != f"mtarget:{self.subject_sha256}":
            raise ValueError("managed target ID differs from its exact subject SHA")
        if self.choice == ManagedReviewChoiceV1.CONFIRM_NO_CHANGE and not is_no_change:
            raise ValueError("confirm-no-change is valid only for a no-change subject")
        if self.choice == ManagedReviewChoiceV1.APPROVE and is_no_change:
            raise ValueError("a no-change subject cannot be approved as a revision plan")
        return self


class _ReviewDecisionDocumentBase(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    request_id: str
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str
    reviewer_id: str
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)

    @field_validator("request_id")
    @classmethod
    def _request(cls, value: str) -> str:
        return _safe_identifier(value, label="request_id")

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _operation_id(value)

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_text(value, label="review rationale")

    @property
    def canonical_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class TemporalReviewDecisionDocumentV1(_ReviewDecisionDocumentBase):
    stage: Literal[ChangeReviewStageV1.TEMPORAL] = ChangeReviewStageV1.TEMPORAL
    decisions: tuple[TemporalReviewDecisionItemV1, ...] = Field(min_length=1)

    @field_validator("decisions")
    @classmethod
    def _decisions(
        cls, values: tuple[TemporalReviewDecisionItemV1, ...]
    ) -> tuple[TemporalReviewDecisionItemV1, ...]:
        keys = tuple((item.subject_kind.value, item.subject_id) for item in values)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("temporal decisions must be canonically ordered and unique")
        return values

    @model_validator(mode="after")
    def _request_stage(self) -> Self:
        if re.fullmatch(r"reviewreq:[0-9a-f]{64}", self.request_id) is None:
            raise ValueError("temporal decision requires an exact temporal request ID")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        decisions = tuple(
            sorted(
                values.pop("decisions"), key=lambda item: (item.subject_kind.value, item.subject_id)
            )
        )
        return cls(stage=ChangeReviewStageV1.TEMPORAL, decisions=decisions, **values)


class ManagedReviewDecisionDocumentV1(_ReviewDecisionDocumentBase):
    stage: Literal[ChangeReviewStageV1.MANAGED] = ChangeReviewStageV1.MANAGED
    decisions: tuple[ManagedReviewDecisionItemV1, ...] = Field(min_length=1)

    @field_validator("decisions")
    @classmethod
    def _decisions(
        cls, values: tuple[ManagedReviewDecisionItemV1, ...]
    ) -> tuple[ManagedReviewDecisionItemV1, ...]:
        keys = tuple((item.subject_kind.value, item.subject_id) for item in values)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("managed decisions must be canonically ordered and unique")
        return values

    @model_validator(mode="after")
    def _request_stage(self) -> Self:
        if re.fullmatch(r"mrequest:[0-9a-f]{64}", self.request_id) is None:
            raise ValueError("managed decision requires an exact managed request ID")
        return self

    @classmethod
    def create(cls, **values: Any) -> Self:
        decisions = tuple(
            sorted(
                values.pop("decisions"), key=lambda item: (item.subject_kind.value, item.subject_id)
            )
        )
        return cls(stage=ChangeReviewStageV1.MANAGED, decisions=decisions, **values)


ReviewDecisionDocumentV1 = Annotated[
    TemporalReviewDecisionDocumentV1 | ManagedReviewDecisionDocumentV1,
    Field(discriminator="stage"),
]
REVIEW_DECISION_DOCUMENT_V1_ADAPTER: TypeAdapter[ReviewDecisionDocumentV1] = TypeAdapter(
    ReviewDecisionDocumentV1
)


def parse_review_decision_document_v1(payload: bytes) -> ReviewDecisionDocumentV1:
    """Parse one untrusted strict decision document without losing duplicate keys."""

    if type(payload) is not bytes:
        raise TypeError("review decision document must be exact bytes")
    try:
        json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid review decision JSON document") from exc
    return REVIEW_DECISION_DOCUMENT_V1_ADAPTER.validate_json(payload, strict=True)


class ActivateChangeRequestV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    operation_id: str

    @field_validator("operation_id")
    @classmethod
    def _operation(cls, value: str) -> str:
        return _operation_id(value)


class ChangeActivationResultV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    outcome: ChangeRunOutcomeV1
    phase: ChangeRunPhaseV1
    baseline_id: str
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)
    activation_receipt_id: str | None = Field(
        default=None, pattern=r"^mgenerationactivation:[0-9a-f]{64}$"
    )
    activation_receipt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    authority: AuthoritySummaryV1 | None = None

    @field_validator("baseline_id")
    @classmethod
    def _baseline_id(cls, value: str) -> str:
        if re.fullmatch(r"regbaseline:[0-9a-f]{64}", value) is None:
            raise ValueError("baseline_id is not an exact regression baseline ID")
        return value

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.baseline_id != f"regbaseline:{self.baseline_sha256}":
            raise ValueError("baseline ID differs from its exact SHA")
        receipt = self.activation_receipt_id is not None
        if receipt != (self.activation_receipt_sha256 is not None):
            raise ValueError("activation receipt ID and SHA must appear together")
        if receipt and self.activation_receipt_id != (
            f"mgenerationactivation:{self.activation_receipt_sha256}"
        ):
            raise ValueError("activation receipt ID differs from its exact SHA")
        if self.phase == ChangeRunPhaseV1.ACTIVATED:
            if (
                self.outcome != ChangeRunOutcomeV1.ACTIVATED
                or not receipt
                or self.authority is None
                or self.authority.generation_number != 1
                or not self.authority.is_active
            ):
                raise ValueError("activated result requires exact receipt and authority")
        elif self.phase == ChangeRunPhaseV1.REJECTED_NO_OP:
            if self.outcome != ChangeRunOutcomeV1.REJECTED_NO_OP or receipt:
                raise ValueError("rejected no-op result cannot carry an activation receipt")
        elif self.phase == ChangeRunPhaseV1.COMPLETED_NO_OP:
            if self.outcome != ChangeRunOutcomeV1.COMPLETED_NO_OP or receipt:
                raise ValueError("completed no-op result cannot carry an activation receipt")
        else:
            raise ValueError("activation result must be activated or a terminal no-op")
        return self


class ChangeVerificationResultV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_RE.pattern)
    verified: Literal[True] = True
    phase: ChangeRunPhaseV1
    outcome: ChangeRunOutcomeV1
    status_sha256: str = Field(pattern=SHA256_PATTERN)
    status: ChangeRunStatusV1

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if (
            self.status.run_id != self.run_id
            or self.status.phase != self.phase
            or self.status.outcome != self.outcome
        ):
            raise ValueError("verification identity differs from exact status")
        digest = hashlib.sha256(
            canonical_json_bytes(self.status.model_dump(mode="json"))
        ).hexdigest()
        if self.status_sha256 != digest:
            raise ValueError("status_sha256 differs from exact public status")
        return self


__all__ = [
    "ActivateChangeRequestV1",
    "AuthoritySummaryV1",
    "ChangeActivationEvidenceSummaryV1",
    "ChangeActivationResultV1",
    "ChangeEvidenceCompletenessV1",
    "ChangeExecutionModeV1",
    "ChangeReviewCitationV1",
    "ChangeReviewEvidenceSummaryV1",
    "ChangeReviewPacketV1",
    "ChangeReviewStageV1",
    "ChangeReviewSubjectKindV1",
    "ChangeReviewSubjectV1",
    "ChangeRunNextActionV1",
    "ChangeRunOutcomeV1",
    "ChangeRunPageV1",
    "ChangeRunPhaseV1",
    "ChangeRunStatusV1",
    "ChangeRunSummaryV1",
    "ChangeVerificationResultV1",
    "GenerationZeroBaselineSummaryV1",
    "IncomingEvidenceSummaryV1",
    "ManagedReviewChoiceV1",
    "ManagedReviewDecisionDocumentV1",
    "ManagedReviewDecisionItemV1",
    "REVIEW_DECISION_DOCUMENT_V1_ADAPTER",
    "RegressionSuiteEvidenceSummaryV1",
    "ReviewDecisionDocumentV1",
    "StartChangeRequestV1",
    "TemporalReviewChoiceV1",
    "TemporalReviewDecisionDocumentV1",
    "TemporalReviewDecisionItemV1",
    "parse_review_decision_document_v1",
]
