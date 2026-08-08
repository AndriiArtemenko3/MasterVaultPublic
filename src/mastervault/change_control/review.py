"""Strict domain contracts for authoritative human review audit records."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import (
    SHA256_PATTERN,
    ChangeControlAggregate,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    TemporalConstraint,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    canonical_json_bytes,
    normalize_logical_key,
    normalize_semantic_text,
)

_ACTOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_REQUEST_ID_RE = re.compile(r"^reviewreq:[0-9a-f]{64}$")
_SUBJECT_ID_RE = re.compile(r"^(?:rel|tempc):[0-9a-f]{64}$")
_MAX_RATIONALE_LENGTH = 4000


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewSubjectKind(StrEnum):
    DOCUMENT_REPLACEMENT = "document-replacement"
    TEMPORAL_CONSTRAINT = "temporal-constraint"


class ReviewDisposition(StrEnum):
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


class ReviewLifecycle(StrEnum):
    OPEN = "open"
    STALE = "stale"
    DECIDED = "decided"


def normalize_actor_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value or _ACTOR_ID_RE.fullmatch(normalized) is None:
        raise ValueError(
            "actor ID must be NFKC-normalized and contain 1-128 conservative ASCII characters"
        )
    return normalized


def normalize_review_rationale(value: str) -> str:
    normalized = normalize_semantic_text(value)
    if value != normalized:
        raise ValueError("review rationale must be NFKC-normalized with canonical whitespace")
    if not normalized or len(normalized) > _MAX_RATIONALE_LENGTH:
        raise ValueError("review rationale must contain 1-4000 characters")
    return normalized


def _require_canonical_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("review timestamp must be canonical UTC with second precision")
    return value


class ReviewSubjectRef(_StrictFrozenModel):
    kind: ReviewSubjectKind
    subject_id: str = Field(pattern=_SUBJECT_ID_RE.pattern)

    @model_validator(mode="after")
    def _matching_prefix(self) -> Self:
        prefix = "rel:" if self.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT else "tempc:"
        if not self.subject_id.startswith(prefix):
            raise ValueError("review subject ID prefix does not match its kind")
        return self


ReviewSubject = DocumentReplacementAssessment | TemporalConstraint


class ReviewSubjectSnapshot(_StrictFrozenModel):
    kind: ReviewSubjectKind
    subject_id: str = Field(pattern=_SUBJECT_ID_RE.pattern)
    payload_schema_version: Literal[1] = 1
    subject: ReviewSubject
    subject_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _canonical_snapshot(self) -> Self:
        if self.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT:
            if not isinstance(self.subject, DocumentReplacementAssessment):
                raise ValueError("document-replacement snapshot has the wrong payload type")
            actual_id = self.subject.relation_id
        else:
            if not isinstance(self.subject, TemporalConstraint):
                raise ValueError("temporal-constraint snapshot has the wrong payload type")
            actual_id = self.subject.constraint_id
        if self.subject_id != actual_id:
            raise ValueError("review snapshot subject ID does not match its payload")
        if self.subject_sha256 != review_subject_sha256(self.kind, self.subject):
            raise ValueError("review snapshot SHA does not match its canonical payload")
        return self

    @classmethod
    def create(cls, kind: ReviewSubjectKind, subject: ReviewSubject) -> Self:
        subject_id = (
            subject.relation_id
            if isinstance(subject, DocumentReplacementAssessment)
            else subject.constraint_id
        )
        return cls(
            kind=kind,
            subject_id=subject_id,
            subject=subject,
            subject_sha256=review_subject_sha256(kind, subject),
        )


def review_subject_sha256(kind: ReviewSubjectKind, subject: ReviewSubject) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "kind": kind.value,
                "payload_schema_version": 1,
                "subject": subject.model_dump(mode="json"),
            }
        )
    ).hexdigest()


class HumanReviewRequestCommand(_StrictFrozenModel):
    aggregate_id: str
    expected_revision: int = Field(ge=1)
    expected_aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    subjects: tuple[ReviewSubjectRef, ...] = Field(min_length=1)
    requester_id: str
    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE_LENGTH)

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate_id(cls, value: str) -> str:
        if normalize_logical_key(value) != value:
            raise ValueError("aggregate_id must already be normalized")
        return value

    @field_validator("requester_id")
    @classmethod
    def _requester(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("subjects")
    @classmethod
    def _subjects(cls, values: tuple[ReviewSubjectRef, ...]) -> tuple[ReviewSubjectRef, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.kind.value, item.subject_id)))
        if len(set(ordered)) != len(ordered):
            raise ValueError("review request subjects must be unique")
        return ordered


def human_review_request_id(
    *,
    aggregate_id: str,
    base_revision: int,
    base_aggregate_sha256: str,
    subjects: tuple[ReviewSubjectSnapshot, ...],
) -> str:
    payload = {
        "aggregate_id": aggregate_id,
        "base_aggregate_sha256": base_aggregate_sha256,
        "base_revision": base_revision,
        "subjects": [
            {
                "kind": item.kind.value,
                "subject_id": item.subject_id,
                "subject_sha256": item.subject_sha256,
            }
            for item in sorted(subjects, key=lambda item: (item.kind.value, item.subject_id))
        ],
    }
    return f"reviewreq:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def human_review_request_payload_sha256(
    *, request_id: str, operation_id: str, requester_id: str, rationale: str
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "rationale": rationale,
                "request_id": request_id,
                "requester_id": requester_id,
                "operation_id": operation_id,
            }
        )
    ).hexdigest()


class HumanReviewRequest(_StrictFrozenModel):
    request_id: str = Field(pattern=_REQUEST_ID_RE.pattern)
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    aggregate_id: str
    base_revision: int = Field(ge=1)
    base_aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    base_aggregate: ChangeControlAggregate
    subjects: tuple[ReviewSubjectSnapshot, ...] = Field(min_length=1)
    requester_id: str
    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE_LENGTH)
    requested_at: str
    request_payload_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("requester_id")
    @classmethod
    def _requester(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("requested_at")
    @classmethod
    def _requested_at(cls, value: str) -> str:
        return _require_canonical_utc(value)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        from mastervault.change_control.models import aggregate_sha256

        if (
            self.base_aggregate.aggregate_id != self.aggregate_id
            or aggregate_sha256(self.base_aggregate) != self.base_aggregate_sha256
        ):
            raise ValueError("review request base aggregate does not match its binding")
        ordered = tuple(sorted(self.subjects, key=lambda item: (item.kind.value, item.subject_id)))
        keys = [(item.kind, item.subject_id) for item in self.subjects]
        if self.subjects != ordered or len(keys) != len(set(keys)):
            raise ValueError("review request snapshots must be canonically ordered and unique")
        expected_id = human_review_request_id(
            aggregate_id=self.aggregate_id,
            base_revision=self.base_revision,
            base_aggregate_sha256=self.base_aggregate_sha256,
            subjects=self.subjects,
        )
        if self.request_id != expected_id:
            raise ValueError("review request ID does not match its immutable subject binding")
        expected_payload = human_review_request_payload_sha256(
            request_id=self.request_id,
            operation_id=self.operation_id,
            requester_id=self.requester_id,
            rationale=self.rationale,
        )
        if self.request_payload_sha256 != expected_payload:
            raise ValueError("review request payload SHA does not match its metadata")
        return self


class ReviewSubjectEdit(_StrictFrozenModel):
    kind: ReviewSubjectKind
    subject_id: str = Field(pattern=_SUBJECT_ID_RE.pattern)
    rationale: str | None = Field(default=None, min_length=1, max_length=_MAX_RATIONALE_LENGTH)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str | None) -> str | None:
        return normalize_review_rationale(value) if value is not None else None

    @model_validator(mode="after")
    def _permitted_shape(self) -> Self:
        ReviewSubjectRef(kind=self.kind, subject_id=self.subject_id)
        if self.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT:
            if self.rationale is None and self.confidence is None:
                raise ValueError("document replacement edit must change rationale or confidence")
        elif self.rationale is None or self.confidence is not None:
            raise ValueError("temporal constraint edit may change rationale only")
        return self


class ReviewDecisionItem(_StrictFrozenModel):
    kind: ReviewSubjectKind
    subject_id: str = Field(pattern=_SUBJECT_ID_RE.pattern)
    original_subject_sha256: str = Field(pattern=SHA256_PATTERN)
    disposition: ReviewDisposition
    edit: ReviewSubjectEdit | None = None

    @model_validator(mode="after")
    def _outcome_shape(self) -> Self:
        ReviewSubjectRef(kind=self.kind, subject_id=self.subject_id)
        if self.disposition == ReviewDisposition.EDITED:
            if self.edit is None:
                raise ValueError("edited review outcome requires one permitted edit")
            if (self.edit.kind, self.edit.subject_id) != (self.kind, self.subject_id):
                raise ValueError("review outcome edit must bind the same subject")
        elif self.edit is not None:
            raise ValueError("accepted or rejected review outcome cannot contain an edit")
        return self


class HumanReviewDecisionCommand(_StrictFrozenModel):
    request_id: str = Field(pattern=_REQUEST_ID_RE.pattern)
    reviewer_id: str
    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE_LENGTH)
    items: tuple[ReviewDecisionItem, ...] = Field(min_length=1)

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("items")
    @classmethod
    def _items(cls, values: tuple[ReviewDecisionItem, ...]) -> tuple[ReviewDecisionItem, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.kind.value, item.subject_id)))
        keys = [(item.kind, item.subject_id) for item in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("review decision outcomes must be unique")
        if values != ordered:
            raise ValueError("review decision outcomes must use canonical subject order")
        return values


def human_review_decision_payload_sha256(command: HumanReviewDecisionCommand) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "items": [item.model_dump(mode="json") for item in command.items],
                "rationale": command.rationale,
                "request_id": command.request_id,
                "reviewer_id": command.reviewer_id,
            }
        )
    ).hexdigest()


class HumanReviewDecision(_StrictFrozenModel):
    request_id: str = Field(pattern=_REQUEST_ID_RE.pattern)
    operation_id: str = Field(pattern=_OPERATION_ID_RE.pattern)
    reviewer_id: str
    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE_LENGTH)
    items: tuple[ReviewDecisionItem, ...] = Field(min_length=1)
    decision_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    decided_revision: int = Field(ge=2)
    decided_aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    decided_aggregate: ChangeControlAggregate
    decided_at: str

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return normalize_actor_id(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_review_rationale(value)

    @field_validator("decided_at")
    @classmethod
    def _decided_at(cls, value: str) -> str:
        return _require_canonical_utc(value)

    @field_validator("items")
    @classmethod
    def _items(cls, values: tuple[ReviewDecisionItem, ...]) -> tuple[ReviewDecisionItem, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.kind.value, item.subject_id)))
        keys = [(item.kind, item.subject_id) for item in values]
        if values != ordered or len(keys) != len(set(keys)):
            raise ValueError("persisted decision outcomes must be canonically ordered and unique")
        return values

    @model_validator(mode="after")
    def _payload(self) -> Self:
        from mastervault.change_control.models import aggregate_sha256

        command = HumanReviewDecisionCommand(
            request_id=self.request_id,
            reviewer_id=self.reviewer_id,
            rationale=self.rationale,
            items=self.items,
        )
        if self.decision_payload_sha256 != human_review_decision_payload_sha256(command):
            raise ValueError("review decision payload SHA does not match its human metadata")
        if aggregate_sha256(self.decided_aggregate) != self.decided_aggregate_sha256:
            raise ValueError("review decision aggregate snapshot does not match its SHA")
        return self


class HumanReviewRequestReceipt(_StrictFrozenModel):
    request: HumanReviewRequest
    lifecycle: ReviewLifecycle
    replayed: bool = False


class HumanReviewDecisionReceipt(_StrictFrozenModel):
    decision: HumanReviewDecision
    aggregate_revision: int = Field(ge=2)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    replayed: bool = False

    @model_validator(mode="after")
    def _matches_decision(self) -> Self:
        if (
            self.aggregate_revision != self.decision.decided_revision
            or self.aggregate_sha256 != self.decision.decided_aggregate_sha256
        ):
            raise ValueError("decision receipt does not match the committed aggregate")
        return self


class HumanReviewRequestView(_StrictFrozenModel):
    request: HumanReviewRequest
    lifecycle: ReviewLifecycle
    decision: HumanReviewDecision | None = None

    @model_validator(mode="after")
    def _lifecycle(self) -> Self:
        if (self.lifecycle == ReviewLifecycle.DECIDED) != (self.decision is not None):
            raise ValueError("decided lifecycle must have exactly one decision")
        if self.decision is not None:
            if self.decision.request_id != self.request.request_id:
                raise ValueError("review decision does not belong to this request")
            if self.decision.decided_revision != self.request.base_revision + 1:
                raise ValueError("review decision revision does not follow its request base")
            command = HumanReviewDecisionCommand(
                request_id=self.decision.request_id,
                reviewer_id=self.decision.reviewer_id,
                rationale=self.decision.rationale,
                items=self.decision.items,
            )
            expected = apply_human_review_decision(self.request, command)
            if expected != self.decision.decided_aggregate:
                raise ValueError("review decision result does not match this request")
        return self


def subject_from_aggregate(
    aggregate: ChangeControlAggregate, ref: ReviewSubjectRef
) -> ReviewSubject | None:
    if ref.kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT:
        return next(
            (
                item
                for item in aggregate.document_replacements.assessments
                if item.relation_id == ref.subject_id
            ),
            None,
        )
    return next(
        (
            item
            for item in aggregate.temporal_constraints.constraints
            if item.constraint_id == ref.subject_id
        ),
        None,
    )


def apply_human_review_decision(
    request: HumanReviewRequest,
    command: HumanReviewDecisionCommand,
) -> ChangeControlAggregate:
    """Mechanically apply one exact, complete outcome batch to its bound base."""

    if command.request_id != request.request_id:
        raise ValueError("review decision command does not bind this request")
    requested = {(item.kind, item.subject_id): item for item in request.subjects}
    outcomes = {(item.kind, item.subject_id): item for item in command.items}
    if set(outcomes) != set(requested):
        raise ValueError("review decision requires one outcome per requested subject")
    if any(
        item.original_subject_sha256 != requested[key].subject_sha256
        for key, item in outcomes.items()
    ):
        raise ValueError("review outcome does not bind the original subject SHA")

    replacements: list[DocumentReplacementAssessment] = []
    for current_replacement in request.base_aggregate.document_replacements.assessments:
        key = (ReviewSubjectKind.DOCUMENT_REPLACEMENT, current_replacement.relation_id)
        snapshot = requested.get(key)
        if snapshot is None:
            replacements.append(current_replacement)
            continue
        if (
            current_replacement != snapshot.subject
            or current_replacement.status != TemporalConstraintStatus.PROPOSED
        ):
            raise ValueError("review replacement snapshot does not bind the base aggregate")
        outcome = outcomes[key]
        target_status = (
            TemporalConstraintStatus.REJECTED
            if outcome.disposition == ReviewDisposition.REJECTED
            else TemporalConstraintStatus.ACCEPTED
        )
        payload = current_replacement.model_dump(mode="json")
        if outcome.edit is not None:
            edit = outcome.edit
            changed = False
            if edit.rationale is not None:
                changed = changed or edit.rationale != current_replacement.rationale
                payload["rationale"] = edit.rationale
            if edit.confidence is not None:
                changed = changed or edit.confidence != current_replacement.confidence
                payload["confidence"] = edit.confidence
            if not changed:
                raise ValueError("edited replacement outcome must make a real change")
        payload["status"] = target_status.value
        replacements.append(DocumentReplacementAssessment.model_validate(payload))

    constraints: list[TemporalConstraint] = []
    for current_constraint in request.base_aggregate.temporal_constraints.constraints:
        key = (ReviewSubjectKind.TEMPORAL_CONSTRAINT, current_constraint.constraint_id)
        snapshot = requested.get(key)
        if snapshot is None:
            constraints.append(current_constraint)
            continue
        if (
            current_constraint != snapshot.subject
            or current_constraint.status != TemporalConstraintStatus.PROPOSED
        ):
            raise ValueError("review constraint snapshot does not bind the base aggregate")
        outcome = outcomes[key]
        target_status = (
            TemporalConstraintStatus.REJECTED
            if outcome.disposition == ReviewDisposition.REJECTED
            else TemporalConstraintStatus.ACCEPTED
        )
        payload = current_constraint.model_dump(mode="json")
        if outcome.edit is not None:
            edit = outcome.edit
            assert edit.rationale is not None
            if edit.rationale == current_constraint.rationale:
                raise ValueError("edited constraint outcome must make a real change")
            payload["rationale"] = edit.rationale
        payload["status"] = target_status.value
        constraints.append(TemporalConstraint.model_validate(payload))

    payload = request.base_aggregate.model_dump(mode="json")
    payload["document_replacements"] = DocumentReplacementSet.create(
        tuple(replacements)
    ).model_dump(mode="json")
    payload["temporal_constraints"] = TemporalConstraintSet.create(tuple(constraints)).model_dump(
        mode="json"
    )
    return ChangeControlAggregate.model_validate(payload)


def snapshot_payload_json(snapshot: ReviewSubjectSnapshot) -> str:
    return canonical_json_bytes(snapshot.subject.model_dump(mode="json")).decode("utf-8")


def snapshot_from_payload(
    *,
    kind: ReviewSubjectKind,
    subject_id: str,
    payload_schema_version: int,
    payload: Any,
    sha256: str,
) -> ReviewSubjectSnapshot:
    if payload_schema_version != 1:
        raise ValueError("unsupported review subject snapshot schema version")
    subject: ReviewSubject
    if kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT:
        subject = DocumentReplacementAssessment.model_validate(payload)
    else:
        subject = TemporalConstraint.model_validate(payload)
    return ReviewSubjectSnapshot(
        kind=kind,
        subject_id=subject_id,
        payload_schema_version=1,
        subject=subject,
        subject_sha256=sha256,
    )
