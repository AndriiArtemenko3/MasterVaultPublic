"""Pure, grounded result contracts for reviewed actual-impact questions.

Every selected :class:`~mastervault.change_control.impact_analysis.ImpactQuestion`
has exactly one decision.  ``AFFECTED`` requires one or more exact body spans
from the complete SourceNote carried by its input shard.  The other complete
dispositions may be spanless; every supplied span is still exact-grounded.
Attention-path and dependency IDs may be retained as optional context, but
never supply evidence or imply a disposition.

The models in this module are immutable, bounded, content-addressed, and
deterministically regenerated from an exact Step 10a workload.  Standalone
deserialization proves only locally knowable structural and content integrity;
exact grounding is established by ``ImpactDecision.create``, enclosing result
validation, and ultimately ``validate_impact_results`` against sealed authority.
The module performs no provider calls, I/O, persistence, review, staging,
mutation, publication, or orchestration.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import mastervault.change_control as change_control_types
from mastervault.change_control.impact_analysis import (
    MAX_IMPACT_DOCUMENT_SHARDS_V1,
    MAX_IMPACT_QUESTIONS_V1,
    ImpactInferenceShard,
    ImpactQuestion,
    ImpactWorkload,
    validate_impact_workload,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    DocumentSpanReference,
    canonical_json_bytes,
)

MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1 = 64
MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1 = 16 * 1024
MAX_IMPACT_RATIONALE_UTF8_BYTES_V1 = 4_000
MAX_IMPACT_DECISION_CANONICAL_BYTES_V1 = 128 * 1024
MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1 = 256 * 1024
MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1 = 1024 * 1024

_QUESTION_ID = r"^impactq:[0-9a-f]{64}$"
_WORKLOAD_ID = r"^impactwork:[0-9a-f]{64}$"
_INPUT_SHARD_ID = r"^impactin:[0-9a-f]{64}$"
_DECISION_ID = r"^impactdecision:[0-9a-f]{64}$"
_OUTPUT_SHARD_ID = r"^impactout:[0-9a-f]{64}$"
_RESULT_ID = r"^impactresult:[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ImpactResultLimitError(RuntimeError):
    """A fixed Step 10b result limit was exceeded."""

    def __init__(self, *, category: str, limit: int, observed: int) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        super().__init__(f"impact result limit exceeded: {category}={observed} > {limit}")


class ImpactDisposition(StrEnum):
    """The frozen v1 actual-impact vocabulary."""

    AFFECTED = "AFFECTED"
    NO_CHANGE_REQUIRED = "NO_CHANGE_REQUIRED"
    UNRESOLVED = "UNRESOLVED"


_DOCUMENT_PRECEDENCE = {
    ImpactDisposition.NO_CHANGE_REQUIRED: 0,
    ImpactDisposition.UNRESOLVED: 1,
    ImpactDisposition.AFFECTED: 2,
}


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _require_content_id(*, content_id: str, prefix: str, sha256: str, label: str) -> None:
    if content_id != f"{prefix}{sha256}":
        raise ValueError(f"{label} ID suffix differs from its bound SHA")


def _canonical_rationale(value: str) -> str:
    if not value or value != " ".join(value.split()):
        raise ValueError("impact rationale must be canonical non-empty text")
    observed = len(value.encode("utf-8"))
    if observed > MAX_IMPACT_RATIONALE_UTF8_BYTES_V1:
        raise ValueError("impact rationale exceeds the fixed v1 UTF-8 byte limit")
    return value


def _span_key(span: DocumentSpanReference) -> bytes:
    return canonical_json_bytes(span.model_dump(mode="json"))


def _document_disposition(decisions: tuple[ImpactDecision, ...]) -> ImpactDisposition:
    """Apply the frozen per-document precedence to a non-empty decision set."""

    if not decisions:
        raise ValueError("document impact disposition requires at least one decision")
    return max((item.disposition for item in decisions), key=_DOCUMENT_PRECEDENCE.__getitem__)


class ImpactDecision(_StrictFrozenModel):
    """One complete disposition for one exact selected impact question."""

    schema_version: Literal[1] = 1
    question_id: str = Field(pattern=_QUESTION_ID)
    question_sha256: str = Field(pattern=SHA256_PATTERN)
    disposition: ImpactDisposition
    evidence_spans: tuple[DocumentSpanReference, ...] = Field(
        max_length=MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1
    )
    attention_path_context_ids: tuple[str, ...] = ()
    dependency_context_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)
    decision_id: str = Field(pattern=_DECISION_ID)
    decision_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.actual-impact-decision.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "decision_id", "decision_sha256"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_rationale(value)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_content_id(
            content_id=self.question_id,
            prefix="impactq:",
            sha256=self.question_sha256,
            label="impact question",
        )
        span_keys = tuple(_span_key(item) for item in self.evidence_spans)
        if span_keys != tuple(sorted(set(span_keys))):
            raise ValueError("impact evidence spans must be unique and canonical")
        if any(
            len(item) > MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1 for item in span_keys
        ):
            raise ValueError("impact evidence span exceeds the fixed v1 byte limit")
        if self.disposition == ImpactDisposition.AFFECTED and not self.evidence_spans:
            raise ValueError("AFFECTED impact decisions require exact SourceNote body evidence")
        for values, label in (
            (self.attention_path_context_ids, "attention-path context"),
            (self.dependency_context_ids, "dependency context"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"impact {label} IDs must be unique and canonical")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_DECISION_CANONICAL_BYTES_V1:
            raise ValueError("impact decision exceeds the fixed v1 canonical-byte limit")
        digest = _sha256(payload)
        if self.decision_sha256 != digest or self.decision_id != f"impactdecision:{digest}":
            raise ValueError("impact decision ID/SHA differs from its exact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        input_shard: ImpactInferenceShard,
        question: ImpactQuestion,
        disposition: ImpactDisposition,
        evidence_spans: tuple[DocumentSpanReference, ...],
        rationale: str,
        attention_path_context_ids: tuple[str, ...] = (),
        dependency_context_ids: tuple[str, ...] = (),
    ) -> Self:
        if question not in input_shard.questions:
            raise ValueError("impact decision question is absent from the exact input shard")
        spans = tuple(sorted(evidence_spans, key=_span_key))
        if disposition == ImpactDisposition.AFFECTED and not spans:
            raise ValueError("AFFECTED impact decisions require exact SourceNote body evidence")
        if len(spans) > MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1:
            raise ImpactResultLimitError(
                category="evidence-spans-per-decision",
                limit=MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1,
                observed=len(spans),
            )
        for span in spans:
            observed = len(_span_key(span))
            if observed > MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1:
                raise ImpactResultLimitError(
                    category="evidence-span-canonical-bytes",
                    limit=MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1,
                    observed=observed,
                )
            input_shard.target_note.validate_span(span)
        attention_ids = tuple(sorted(attention_path_context_ids))
        dependency_ids = tuple(sorted(dependency_context_ids))
        allowed_attention = {item.path_id for item in question.attention_paths}
        allowed_dependencies = {item.dependency_id for item in question.existing_dependencies}
        if not set(attention_ids) <= allowed_attention:
            raise ValueError("impact decision names attention context absent from its question")
        if not set(dependency_ids) <= allowed_dependencies:
            raise ValueError("impact decision names dependency context absent from its question")
        canonical_rationale = _canonical_rationale(rationale)
        values: dict[str, Any] = {
            "question_id": question.question_id,
            "question_sha256": question.question_sha256,
            "disposition": disposition.value,
            "evidence_spans": [item.model_dump(mode="json") for item in spans],
            "attention_path_context_ids": list(attention_ids),
            "dependency_context_ids": list(dependency_ids),
            "rationale": canonical_rationale,
        }
        payload = {
            "namespace": "mastervault.actual-impact-decision.v1",
            "schema_version": 1,
            **values,
        }
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_DECISION_CANONICAL_BYTES_V1:
            raise ImpactResultLimitError(
                category="decision-canonical-bytes",
                limit=MAX_IMPACT_DECISION_CANONICAL_BYTES_V1,
                observed=observed,
            )
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    **values,
                    "decision_id": f"impactdecision:{digest}",
                    "decision_sha256": digest,
                }
            )
        )


def _require_decision_matches_question(
    *,
    decision: ImpactDecision,
    question: ImpactQuestion,
    input_shard: ImpactInferenceShard,
) -> None:
    if (
        decision.question_id != question.question_id
        or decision.question_sha256 != question.question_sha256
    ):
        raise ValueError("impact decision binds a substituted question")
    for span in decision.evidence_spans:
        input_shard.target_note.validate_span(span)
    allowed_attention = {item.path_id for item in question.attention_paths}
    allowed_dependencies = {item.dependency_id for item in question.existing_dependencies}
    if not set(decision.attention_path_context_ids) <= allowed_attention:
        raise ValueError("impact decision names attention context absent from its question")
    if not set(decision.dependency_context_ids) <= allowed_dependencies:
        raise ValueError("impact decision names dependency context absent from its question")


class ImpactOutputShard(_StrictFrozenModel):
    """Every root decision and the derived disposition for one selected document."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    document_version_id: str = Field(pattern=r"^docv:[0-9a-f]{64}$")
    document_disposition: ImpactDisposition
    decisions: tuple[ImpactDecision, ...] = Field(
        min_length=1,
        max_length=MAX_IMPACT_QUESTIONS_V1,
    )
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.actual-impact-output-shard.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "output_shard_id", "output_shard_sha256"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @classmethod
    def create(
        cls,
        *,
        workload: ImpactWorkload,
        input_shard: ImpactInferenceShard,
        decisions: tuple[ImpactDecision, ...],
    ) -> ImpactOutputShard:
        """Build one complete document output without relying on private helpers."""

        exact_workload = ImpactWorkload.model_validate_json(
            canonical_json_bytes(workload.model_dump(mode="json"))
        )
        exact_input = ImpactInferenceShard.model_validate_json(
            canonical_json_bytes(input_shard.model_dump(mode="json"))
        )
        expected = next(
            (item for item in exact_workload.input_shards if item.shard_id == exact_input.shard_id),
            None,
        )
        if expected != exact_input:
            raise ValueError("impact output input is not an exact shard of its workload")
        canonical_decisions = tuple(
            ImpactDecision.model_validate_json(canonical_json_bytes(item.model_dump(mode="json")))
            for item in decisions
        )
        question_by_id = {item.question_id: item for item in exact_input.questions}
        decision_ids = tuple(item.question_id for item in canonical_decisions)
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("impact output contains duplicate question decisions")
        if set(decision_ids) != set(question_by_id):
            raise ValueError("impact output must decide every input-shard question exactly once")
        for decision in canonical_decisions:
            _require_decision_matches_question(
                decision=decision,
                question=question_by_id[decision.question_id],
                input_shard=exact_input,
            )
        return _output_shard(
            workload=exact_workload,
            input_shard=exact_input,
            decisions=canonical_decisions,
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_content_id(
            content_id=self.workload_id,
            prefix="impactwork:",
            sha256=self.workload_sha256,
            label="impact workload",
        )
        _require_content_id(
            content_id=self.input_shard_id,
            prefix="impactin:",
            sha256=self.input_shard_sha256,
            label="impact input shard",
        )
        question_ids = tuple(item.question_id for item in self.decisions)
        if question_ids != tuple(sorted(set(question_ids))):
            raise ValueError("impact output decisions must use unique canonical question order")
        spans = tuple(span for decision in self.decisions for span in decision.evidence_spans)
        if any(span.document_version_id != self.document_version_id for span in spans):
            raise ValueError("impact output evidence names a different document")
        source_bindings = {(span.source_note_path, span.source_note_sha256) for span in spans}
        if len(source_bindings) > 1:
            raise ValueError("impact output evidence has incoherent SourceNote bindings")
        if self.document_disposition != _document_disposition(self.decisions):
            raise ValueError("document impact disposition differs from frozen precedence")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("impact output shard exceeds the fixed v1 canonical-byte limit")
        digest = _sha256(payload)
        if self.output_shard_sha256 != digest or self.output_shard_id != f"impactout:{digest}":
            raise ValueError("impact output shard ID/SHA differs from its exact content")
        return self


class ImpactOutputShardRef(_StrictFrozenModel):
    document_version_id: str = Field(pattern=r"^docv:[0-9a-f]{64}$")
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_canonical_bytes: int = Field(
        gt=0,
        le=MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1,
    )
    decision_count: int = Field(gt=0, le=MAX_IMPACT_QUESTIONS_V1)
    document_disposition: ImpactDisposition

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_content_id(
            content_id=self.input_shard_id,
            prefix="impactin:",
            sha256=self.input_shard_sha256,
            label="impact input shard ref",
        )
        _require_content_id(
            content_id=self.output_shard_id,
            prefix="impactout:",
            sha256=self.output_shard_sha256,
            label="impact output shard ref",
        )
        return self

    @classmethod
    def create(cls, shard: ImpactOutputShard) -> Self:
        return cls(
            document_version_id=shard.document_version_id,
            input_shard_id=shard.input_shard_id,
            input_shard_sha256=shard.input_shard_sha256,
            output_shard_id=shard.output_shard_id,
            output_shard_sha256=shard.output_shard_sha256,
            output_shard_canonical_bytes=len(shard.canonical_bytes()),
            decision_count=len(shard.decisions),
            document_disposition=shard.document_disposition,
        )


class ImpactResultIndex(_StrictFrozenModel):
    """Compact content-addressed root for the complete Step 10b result."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_count: int = Field(ge=0, le=MAX_IMPACT_QUESTIONS_V1)
    output_shards: tuple[ImpactOutputShardRef, ...] = Field(
        max_length=MAX_IMPACT_DOCUMENT_SHARDS_V1
    )
    result_id: str = Field(pattern=_RESULT_ID)
    result_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.actual-impact-result-index.v1",
            "schema_version": 1,
            **self.model_dump(
                mode="json",
                exclude={"schema_version", "result_id", "result_sha256"},
            ),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_content_id(
            content_id=self.workload_id,
            prefix="impactwork:",
            sha256=self.workload_sha256,
            label="impact result workload",
        )
        document_ids = tuple(item.document_version_id for item in self.output_shards)
        input_ids = tuple(item.input_shard_id for item in self.output_shards)
        output_ids = tuple(item.output_shard_id for item in self.output_shards)
        if document_ids != tuple(sorted(set(document_ids))):
            raise ValueError("impact result refs must use unique canonical document order")
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("impact result refs must use unique input shard IDs")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("impact result refs must use unique output shard IDs")
        if sum(item.decision_count for item in self.output_shards) != self.decision_count:
            raise ValueError("impact result shard counts differ from its decision count")
        payload = self._payload()
        observed = len(canonical_json_bytes(payload))
        if observed > MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1:
            raise ValueError("impact result index exceeds the fixed v1 canonical-byte limit")
        digest = _sha256(payload)
        if self.result_sha256 != digest or self.result_id != f"impactresult:{digest}":
            raise ValueError("impact result index ID/SHA differs from its exact content")
        return self


def _output_shard(
    *,
    workload: ImpactWorkload,
    input_shard: ImpactInferenceShard,
    decisions: tuple[ImpactDecision, ...],
) -> ImpactOutputShard:
    canonical = tuple(sorted(decisions, key=lambda item: item.question_id))
    values: dict[str, Any] = {
        "workload_id": workload.index.workload_id,
        "workload_sha256": workload.index.workload_sha256,
        "input_shard_id": input_shard.shard_id,
        "input_shard_sha256": input_shard.shard_sha256,
        "document_version_id": input_shard.target_note.document.document_version_id,
        "document_disposition": _document_disposition(canonical).value,
        "decisions": [item.model_dump(mode="json") for item in canonical],
    }
    payload = {
        "namespace": "mastervault.actual-impact-output-shard.v1",
        "schema_version": 1,
        **values,
    }
    observed = len(canonical_json_bytes(payload))
    if observed > MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1:
        raise ImpactResultLimitError(
            category="complete-document-output-bytes",
            limit=MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1,
            observed=observed,
        )
    digest = _sha256(payload)
    return ImpactOutputShard.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": 1,
                **values,
                "output_shard_id": f"impactout:{digest}",
                "output_shard_sha256": digest,
            }
        )
    )


class ImpactResultSet(_StrictFrozenModel):
    """In-memory envelope over an exact workload and all complete decisions."""

    schema_version: Literal[1] = 1
    workload: ImpactWorkload
    result_index: ImpactResultIndex
    output_shards: tuple[ImpactOutputShard, ...] = Field(
        max_length=MAX_IMPACT_DOCUMENT_SHARDS_V1
    )

    @property
    def result_id(self) -> str:
        return self.result_index.result_id

    @property
    def result_sha256(self) -> str:
        return self.result_index.result_sha256

    @property
    def decisions(self) -> tuple[ImpactDecision, ...]:
        return tuple(
            sorted(
                (decision for shard in self.output_shards for decision in shard.decisions),
                key=lambda item: item.question_id,
            )
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.result_index.workload_id != self.workload.index.workload_id
            or self.result_index.workload_sha256 != self.workload.index.workload_sha256
        ):
            raise ValueError("impact result index binds a different workload")
        expected_inputs = {item.shard_id: item for item in self.workload.input_shards}
        supplied_keys = tuple(
            (item.document_version_id, item.input_shard_id) for item in self.output_shards
        )
        expected_keys = tuple(
            sorted(
                (
                    item.target_note.document.document_version_id,
                    item.shard_id,
                )
                for item in self.workload.input_shards
            )
        )
        if supplied_keys != expected_keys:
            raise ValueError("impact outputs must exactly cover workload input shards")
        refs = tuple(ImpactOutputShardRef.create(item) for item in self.output_shards)
        if self.result_index.output_shards != refs:
            raise ValueError("impact result index contains a substituted output shard")
        all_question_ids: list[str] = []
        for output in self.output_shards:
            source = expected_inputs[output.input_shard_id]
            if (
                output.workload_id != self.workload.index.workload_id
                or output.workload_sha256 != self.workload.index.workload_sha256
                or output.input_shard_sha256 != source.shard_sha256
                or output.document_version_id
                != source.target_note.document.document_version_id
            ):
                raise ValueError("impact output shard binds a substituted exact input")
            questions = {item.question_id: item for item in source.questions}
            output_ids = tuple(item.question_id for item in output.decisions)
            if output_ids != tuple(sorted(questions)):
                raise ValueError("impact output does not decide every document question once")
            for decision in output.decisions:
                _require_decision_matches_question(
                    decision=decision,
                    question=questions[decision.question_id],
                    input_shard=source,
                )
            all_question_ids.extend(output_ids)
        selected_ids = tuple(sorted(item.question_id for item in self.workload.questions))
        if tuple(sorted(all_question_ids)) != selected_ids or len(all_question_ids) != len(
            set(all_question_ids)
        ):
            raise ValueError("impact decisions must exactly cover selected workload questions")
        if self.result_index.decision_count != len(all_question_ids):
            raise ValueError("impact result index decision count differs from exact coverage")
        total_bytes = sum(len(item.canonical_bytes()) for item in self.output_shards)
        if total_bytes > MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1:
            raise ValueError("impact results exceed the fixed v1 aggregate output-byte limit")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: ImpactWorkload,
        decisions: tuple[ImpactDecision, ...],
    ) -> Self:
        exact_workload = ImpactWorkload.model_validate_json(
            canonical_json_bytes(workload.model_dump(mode="json"))
        )
        canonical_decisions = tuple(
            ImpactDecision.model_validate_json(canonical_json_bytes(item.model_dump(mode="json")))
            for item in decisions
        )
        supplied_question_ids = tuple(item.question_id for item in canonical_decisions)
        if len(supplied_question_ids) != len(set(supplied_question_ids)):
            raise ValueError("impact results contain duplicate question decisions")
        by_question = {item.question_id: item for item in canonical_decisions}
        expected_question_ids = {item.question_id for item in exact_workload.questions}
        missing = tuple(sorted(expected_question_ids - set(by_question)))
        surplus = tuple(sorted(set(by_question) - expected_question_ids))
        if missing or surplus:
            raise ValueError(
                f"impact results must exactly cover selected questions; "
                f"missing={missing}, surplus={surplus}"
            )
        outputs: list[ImpactOutputShard] = []
        for input_shard in exact_workload.input_shards:
            shard_decisions = tuple(by_question[item.question_id] for item in input_shard.questions)
            questions = {item.question_id: item for item in input_shard.questions}
            for decision in shard_decisions:
                _require_decision_matches_question(
                    decision=decision,
                    question=questions[decision.question_id],
                    input_shard=input_shard,
                )
            outputs.append(
                _output_shard(
                    workload=exact_workload,
                    input_shard=input_shard,
                    decisions=shard_decisions,
                )
            )
        canonical_outputs = tuple(
            sorted(
                outputs,
                key=lambda item: (item.document_version_id, item.input_shard_id),
            )
        )
        total_bytes = sum(len(item.canonical_bytes()) for item in canonical_outputs)
        if total_bytes > MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1:
            raise ImpactResultLimitError(
                category="total-output-bytes",
                limit=MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1,
                observed=total_bytes,
            )
        refs = tuple(ImpactOutputShardRef.create(item) for item in canonical_outputs)
        index_values: dict[str, Any] = {
            "workload_id": exact_workload.index.workload_id,
            "workload_sha256": exact_workload.index.workload_sha256,
            "decision_count": len(canonical_decisions),
            "output_shards": [item.model_dump(mode="json") for item in refs],
        }
        index_payload = {
            "namespace": "mastervault.actual-impact-result-index.v1",
            "schema_version": 1,
            **index_values,
        }
        observed = len(canonical_json_bytes(index_payload))
        if observed > MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1:
            raise ImpactResultLimitError(
                category="result-index-bytes",
                limit=MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1,
                observed=observed,
            )
        digest = _sha256(index_payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "workload": exact_workload.model_dump(mode="json"),
                    "result_index": {
                        "schema_version": 1,
                        **index_values,
                        "result_id": f"impactresult:{digest}",
                        "result_sha256": digest,
                    },
                    "output_shards": [item.model_dump(mode="json") for item in canonical_outputs],
                }
            )
        )


def validate_impact_results(
    authority: change_control_types.ReviewedTemporalSnapshotAuthority,
    *,
    workload: ImpactWorkload,
    results: ImpactResultSet,
) -> ImpactResultSet:
    """Regenerate Step 10a from sealed authority, then regenerate Step 10b."""

    exact_workload = validate_impact_workload(authority, workload)
    validated = ImpactResultSet.model_validate_json(
        canonical_json_bytes(results.model_dump(mode="json"))
    )
    if validated.workload != exact_workload:
        raise ValueError("impact results embed a different workload")
    expected = ImpactResultSet.create(
        workload=exact_workload,
        decisions=validated.decisions,
    )
    if validated != expected:
        raise ValueError("impact results differ from deterministic canonical regeneration")
    return validated


__all__ = [
    "MAX_IMPACT_DECISION_CANONICAL_BYTES_V1",
    "MAX_IMPACT_EVIDENCE_SPAN_CANONICAL_BYTES_V1",
    "MAX_IMPACT_EVIDENCE_SPANS_PER_DECISION_V1",
    "MAX_IMPACT_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_IMPACT_RATIONALE_UTF8_BYTES_V1",
    "MAX_IMPACT_RESULT_INDEX_CANONICAL_BYTES_V1",
    "MAX_IMPACT_TOTAL_OUTPUT_BYTES_V1",
    "ImpactDecision",
    "ImpactDisposition",
    "ImpactOutputShard",
    "ImpactOutputShardRef",
    "ImpactResultIndex",
    "ImpactResultLimitError",
    "ImpactResultSet",
    "validate_impact_results",
]
