"""Pure eligibility and provider-wire contracts for managed revision planning.

This module deliberately performs no provider calls, filesystem access, staging,
SourceNote rendering, review creation, or orchestration.  Provider-authored data
is limited to semantic edit intent over exact caller-supplied text.  Paths,
hashes, provenance, content identities, successor metadata, dates, scopes, and
complete SourceNote bytes remain locally derived in a later implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from mastervault.change_control.impact_results import (
    ImpactDisposition,
    ImpactResultSet,
)
from mastervault.change_control.models import (
    DocumentVersionMetadata,
    VersionedClaimRevision,
    canonical_json_bytes,
    normalize_logical_key,
    normalize_semantic_text,
)
from mastervault.change_control.repository_files import canonical_repo_relative

MAX_REVISION_PLANNING_TARGETS_V1 = 16
MAX_REVISION_PLANNING_QUESTIONS_V1 = 64
MAX_REVISION_PLANNING_EDITS_V1 = 16
MAX_REVISION_PLANNING_CITATIONS_PER_EDIT_V1 = 16
MAX_REVISION_PLANNING_NO_CHANGE_CITATIONS_V1 = 16
MAX_REVISION_PLANNING_CITATION_INPUTS_V1 = 16
MAX_REVISION_PLANNING_CITATION_INPUT_UTF8_BYTES_V1 = 256 * 1024
MAX_REVISION_PLANNING_CLAIM_REWRITES_V1 = 256
MAX_REVISION_PLANNING_TEXT_UTF8_BYTES_V1 = 64 * 1024
MAX_REVISION_PLANNING_RATIONALE_UTF8_BYTES_V1 = 4_000
MAX_REVISION_PLANNING_LOGICAL_KEY_UTF8_BYTES_V1 = 512

_QUESTION_ID = r"^impactq:[0-9a-f]{64}$"
_DOCUMENT_VERSION_ID = r"^docv:[0-9a-f]{64}$"
_INPUT_SHARD_ID = r"^impactin:[0-9a-f]{64}$"
_OUTPUT_SHARD_ID = r"^impactout:[0-9a-f]{64}$"
_WORKLOAD_ID = r"^impactwork:[0-9a-f]{64}$"
_RESULT_ID = r"^impactresult:[0-9a-f]{64}$"
_REVISION_WORKLOAD_ID = r"^revisionwork:[0-9a-f]{64}$"
_REVISION_INPUT_ID = r"^revisionin:[0-9a-f]{64}$"
_REVISION_OUTPUT_ID = r"^revisionout:[0-9a-f]{64}$"
_SHA256 = r"^[0-9a-f]{64}$"
_OPERATOR_RUN_ID = re.compile(r"^operatorrun:[0-9a-f]{64}$")

QuestionId = Annotated[str, Field(pattern=_QUESTION_ID)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _exact_key(value: str, *, label: str) -> str:
    if not value or value != normalize_logical_key(value):
        raise ValueError(f"{label} must be an exact normalized logical key")
    if len(value.encode("utf-8")) > MAX_REVISION_PLANNING_LOGICAL_KEY_UTF8_BYTES_V1:
        raise ValueError(f"{label} exceeds the fixed v1 UTF-8 byte limit")
    return value


def _exact_run_id(value: str) -> str:
    if isinstance(value, str) and _OPERATOR_RUN_ID.fullmatch(value) is not None:
        return value
    return _exact_key(value, label="run_id")


def _bounded_text(value: str, *, label: str, maximum: int, allow_empty: bool) -> str:
    if not allow_empty and not value:
        raise ValueError(f"{label} must be non-empty")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds the fixed v1 UTF-8 byte limit")
    return value


def _canonical_rationale(value: str) -> str:
    _bounded_text(
        value,
        label="revision-planning rationale",
        maximum=MAX_REVISION_PLANNING_RATIONALE_UTF8_BYTES_V1,
        allow_empty=False,
    )
    if value != " ".join(value.split()):
        raise ValueError("revision-planning rationale must be canonical text")
    return value


class RevisionPlanningCitationSelector(_StrictFrozenModel):
    """Character range in one locally named inference-input artifact."""

    input_selector: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @field_validator("input_selector")
    @classmethod
    def _selector(cls, value: str) -> str:
        return _exact_key(value, label="input_selector")

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("citation selector must cover a non-empty Python-character range")
        return self


class RevisionPlanningCitationInputRole(StrEnum):
    """Locally assigned role; providers receive only its allowlisted selector."""

    GOVERNING_EVIDENCE = "governing-evidence"
    TARGET_EVIDENCE = "target-evidence"


class RevisionPlanningCitationInput(_StrictFrozenModel):
    """One immutable local citation input omitted from the provider response wire."""

    input_selector: str
    role: RevisionPlanningCitationInputRole
    text_utf8: str

    @field_validator("input_selector")
    @classmethod
    def _selector(cls, value: str) -> str:
        return _exact_key(value, label="input_selector")

    @field_validator("text_utf8")
    @classmethod
    def _text(cls, value: str) -> str:
        return _bounded_text(
            value,
            label="citation input",
            maximum=MAX_REVISION_PLANNING_CITATION_INPUT_UTF8_BYTES_V1,
            allow_empty=False,
        )


class RevisionPlanningCitationInputSet(_StrictFrozenModel):
    """Canonical, typed allowlist used to reopen provider citation selectors."""

    inputs: tuple[RevisionPlanningCitationInput, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_CITATION_INPUTS_V1,
    )

    @field_validator("inputs")
    @classmethod
    def _inputs(
        cls, values: tuple[RevisionPlanningCitationInput, ...]
    ) -> tuple[RevisionPlanningCitationInput, ...]:
        selectors = tuple(item.input_selector for item in values)
        if selectors != tuple(sorted(set(selectors))):
            raise ValueError("citation inputs must be unique and canonically ordered")
        return values


class AffectedRevisionEditWire(_StrictFrozenModel):
    """One semantic replacement over exact predecessor-raw Python characters."""

    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    replacement_text: str
    citations: tuple[RevisionPlanningCitationSelector, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_CITATIONS_PER_EDIT_V1,
    )

    @field_validator("replacement_text")
    @classmethod
    def _replacement(cls, value: str) -> str:
        return _bounded_text(
            value,
            label="replacement_text",
            maximum=MAX_REVISION_PLANNING_TEXT_UTF8_BYTES_V1,
            allow_empty=True,
        )

    @field_validator("citations")
    @classmethod
    def _citations(
        cls, values: tuple[RevisionPlanningCitationSelector, ...]
    ) -> tuple[RevisionPlanningCitationSelector, ...]:
        keys = tuple((item.input_selector, item.start_char, item.end_char) for item in values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("edit citation selectors must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end_char < self.start_char:
            raise ValueError("revision edit end_char cannot precede start_char")
        return self


NonNegativeEditOrdinal = Annotated[int, Field(ge=0)]


class StableSourceClaimStatementRewriteWire(_StrictFrozenModel):
    """A statement-only rewrite of one existing stable source-claim key."""

    source_claim_id: str
    replacement_statement: str
    edit_ordinals: tuple[NonNegativeEditOrdinal, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_EDITS_V1,
    )

    @field_validator("source_claim_id")
    @classmethod
    def _claim(cls, value: str) -> str:
        return _exact_key(value, label="source_claim_id")

    @field_validator("replacement_statement")
    @classmethod
    def _statement(cls, value: str) -> str:
        bounded = _bounded_text(
            value,
            label="replacement_statement",
            maximum=MAX_REVISION_PLANNING_TEXT_UTF8_BYTES_V1,
            allow_empty=False,
        )
        normalized = normalize_semantic_text(bounded)
        if len(normalized) < 8:
            raise ValueError(
                "replacement_statement must contain at least 8 non-whitespace characters"
            )
        if bounded != normalized:
            raise ValueError(
                "replacement_statement must be NFKC-normalized with canonical whitespace"
            )
        return bounded

    @field_validator("edit_ordinals")
    @classmethod
    def _ordinals(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("claim rewrite edit ordinals must be unique and ordered")
        return values


class AffectedRevisionWireResponse(_StrictFrozenModel):
    """Locally selected affected-target response; the provider cannot choose disposition."""

    kind: Literal["affected-revision"] = "affected-revision"
    target_key: str
    question_ids: tuple[QuestionId, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_QUESTIONS_V1,
    )
    edits: tuple[AffectedRevisionEditWire, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_EDITS_V1,
    )
    source_claim_statement_rewrites: tuple[StableSourceClaimStatementRewriteWire, ...] = Field(
        max_length=MAX_REVISION_PLANNING_CLAIM_REWRITES_V1
    )
    rationale: str = Field(min_length=1)

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_key(value, label="target_key")

    @field_validator("question_ids")
    @classmethod
    def _questions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("question IDs must be unique and canonically ordered")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_rationale(value)

    @model_validator(mode="after")
    def _ordered_complete_intent(self) -> Self:
        edit_keys = tuple((item.start_char, item.end_char) for item in self.edits)
        if edit_keys != tuple(sorted(edit_keys)) or len(set(edit_keys)) != len(edit_keys):
            raise ValueError("revision edits must be unique and canonically ordered")
        previous_end = 0
        for edit in self.edits:
            if edit.start_char < previous_end:
                raise ValueError("revision edits must not overlap")
            previous_end = edit.end_char
        claim_ids = tuple(item.source_claim_id for item in self.source_claim_statement_rewrites)
        if claim_ids != tuple(sorted(set(claim_ids))):
            raise ValueError("source-claim rewrites must be unique and canonically ordered")
        for rewrite in self.source_claim_statement_rewrites:
            if rewrite.edit_ordinals[-1] >= len(self.edits):
                raise ValueError("source-claim rewrite names an absent edit ordinal")
        covered_ordinals = tuple(
            ordinal
            for rewrite in self.source_claim_statement_rewrites
            for ordinal in rewrite.edit_ordinals
        )
        if tuple(sorted(covered_ordinals)) != tuple(range(len(self.edits))):
            raise ValueError("every revision edit must bind exactly one source-claim rewrite")
        return self


class NoChangeRevisionWireResponse(_StrictFrozenModel):
    """Locally selected no-change response with evidence selectors only."""

    kind: Literal["no-change"] = "no-change"
    target_key: str
    question_ids: tuple[QuestionId, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_QUESTIONS_V1,
    )
    citations: tuple[RevisionPlanningCitationSelector, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_NO_CHANGE_CITATIONS_V1,
    )
    rationale: str = Field(min_length=1)

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_key(value, label="target_key")

    @field_validator("question_ids")
    @classmethod
    def _questions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("question IDs must be unique and canonically ordered")
        return values

    @field_validator("citations")
    @classmethod
    def _citations(
        cls, values: tuple[RevisionPlanningCitationSelector, ...]
    ) -> tuple[RevisionPlanningCitationSelector, ...]:
        keys = tuple((item.input_selector, item.start_char, item.end_char) for item in values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("no-change citation selectors must be unique and ordered")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_rationale(value)


RevisionPlanningWireResponse = Annotated[
    AffectedRevisionWireResponse | NoChangeRevisionWireResponse,
    Field(discriminator="kind"),
]
_WIRE_RESPONSE_ADAPTER: TypeAdapter[RevisionPlanningWireResponse] = TypeAdapter(
    RevisionPlanningWireResponse
)


class RevisionPlanningEligibilityStatus(StrEnum):
    NO_WORK = "no-work"
    ELIGIBLE = "eligible"


class RevisionPlanningTarget(_StrictFrozenModel):
    target_key: str
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    input_shard_id: str = Field(pattern=_INPUT_SHARD_ID)
    input_shard_sha256: str = Field(pattern=_SHA256)
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    output_shard_sha256: str = Field(pattern=_SHA256)
    question_ids: tuple[QuestionId, ...] = Field(
        min_length=1,
        max_length=MAX_REVISION_PLANNING_QUESTIONS_V1,
    )
    required_response_kind: Literal["affected-revision", "no-change"]

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return _exact_key(value, label="target_key")

    @field_validator("question_ids")
    @classmethod
    def _questions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("eligible target questions must be unique and ordered")
        return values

    @model_validator(mode="after")
    def _content_ids(self) -> Self:
        if self.input_shard_id != f"impactin:{self.input_shard_sha256}":
            raise ValueError("revision-planning input shard ID differs from its SHA")
        if self.output_shard_id != f"impactout:{self.output_shard_sha256}":
            raise ValueError("revision-planning output shard ID differs from its SHA")
        return self


class RevisionPlanningEligibility(_StrictFrozenModel):
    status: RevisionPlanningEligibilityStatus
    workload_id: str = Field(pattern=_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=_SHA256)
    result_id: str = Field(pattern=_RESULT_ID)
    result_sha256: str = Field(pattern=_SHA256)
    targets: tuple[RevisionPlanningTarget, ...] = Field(
        max_length=MAX_REVISION_PLANNING_TARGETS_V1
    )

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.workload_id != f"impactwork:{self.workload_sha256}":
            raise ValueError("revision-planning workload ID differs from its SHA")
        if self.result_id != f"impactresult:{self.result_sha256}":
            raise ValueError("revision-planning result ID differs from its SHA")
        target_pairs = tuple((item.target_key, item.document_version_id) for item in self.targets)
        if target_pairs != tuple(sorted(target_pairs)):
            raise ValueError("revision-planning targets must be canonically ordered")
        target_keys = tuple(item.target_key for item in self.targets)
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("revision-planning target keys must be unique")
        document_version_ids = tuple(item.document_version_id for item in self.targets)
        if len(document_version_ids) != len(set(document_version_ids)):
            raise ValueError("revision-planning document versions must be unique")
        if (self.status == RevisionPlanningEligibilityStatus.NO_WORK) != (not self.targets):
            raise ValueError("NO_WORK requires no targets and ELIGIBLE requires targets")
        return self


class RevisionPlanningExistingClaimInput(_StrictFrozenModel):
    """Exact predecessor claim semantics exposed to the planning provider."""

    source_claim_id: str
    claim_identity_id: str = Field(pattern=r"^claim:[0-9a-f]{64}$")
    claim_revision_id: str = Field(pattern=r"^claimrev:[0-9a-f]{64}$")
    statement: str = Field(min_length=8)
    source_note_path: str
    source_note_sha256: str = Field(pattern=_SHA256)
    scopes: tuple[str, ...]

    @field_validator("source_claim_id")
    @classmethod
    def _claim_key(cls, value: str) -> str:
        return _exact_key(value, label="source_claim_id")

    @field_validator("source_note_path")
    @classmethod
    def _note_path(cls, value: str) -> str:
        return canonical_repo_relative(value)

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("revision input claim scopes must be unique and canonical")
        return values


class RevisionPlanningInferenceShard(_StrictFrozenModel):
    """One complete, content-addressed semantic planning input."""

    schema_version: Literal[1] = 1
    algorithm_version: Literal["recorded-revision-planning-v1"] = (
        "recorded-revision-planning-v1"
    )
    run_id: str
    impact_workload_id: str = Field(pattern=_WORKLOAD_ID)
    impact_workload_sha256: str = Field(pattern=_SHA256)
    impact_result_id: str = Field(pattern=_RESULT_ID)
    impact_result_sha256: str = Field(pattern=_SHA256)
    analysis_set_id: str = Field(pattern=r"^manalysis:[0-9a-f]{64}$")
    analysis_set_sha256: str = Field(pattern=_SHA256)
    analysis_as_of: date
    target: RevisionPlanningTarget
    predecessor: DocumentVersionMetadata
    predecessor_raw_utf8: str
    predecessor_source_note_path: str
    predecessor_source_note_utf8: str
    citation_inputs: RevisionPlanningCitationInputSet
    existing_claims: tuple[RevisionPlanningExistingClaimInput, ...]
    shard_id: str = Field(pattern=_REVISION_INPUT_ID)
    shard_sha256: str = Field(pattern=_SHA256)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.recorded-revision-planning-input.v1",
            **self.model_dump(mode="json", exclude={"shard_id", "shard_sha256"}),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _exact_run_id(self.run_id)
        if self.impact_workload_id != f"impactwork:{self.impact_workload_sha256}":
            raise ValueError("revision input impact workload ID differs from its SHA")
        if self.impact_result_id != f"impactresult:{self.impact_result_sha256}":
            raise ValueError("revision input impact result ID differs from its SHA")
        if self.analysis_set_id != f"manalysis:{self.analysis_set_sha256}":
            raise ValueError("revision input analysis-set ID differs from its SHA")
        if self.predecessor.document_id != self.target.target_key or (
            self.predecessor.document_version_id != self.target.document_version_id
        ):
            raise ValueError("revision input predecessor differs from selected target")
        canonical_repo_relative(self.predecessor_source_note_path)
        claim_keys = tuple(item.source_claim_id for item in self.existing_claims)
        if claim_keys != tuple(sorted(set(claim_keys))):
            raise ValueError("revision input claims must be unique and canonical")
        digest = hashlib.sha256(self.canonical_bytes()).hexdigest()
        if self.shard_sha256 != digest or self.shard_id != f"revisionin:{digest}":
            raise ValueError("revision input ID/SHA differs from its exact bytes")
        return self

    @classmethod
    def create(
        cls,
        *,
        eligibility: RevisionPlanningEligibility,
        run_id: str,
        analysis_set_id: str,
        analysis_set_sha256: str,
        analysis_as_of: date,
        target: RevisionPlanningTarget,
        predecessor: DocumentVersionMetadata,
        predecessor_raw_utf8: str,
        predecessor_source_note_path: str,
        predecessor_source_note_utf8: str,
        citation_inputs: RevisionPlanningCitationInputSet,
        existing_claim_revisions: tuple[VersionedClaimRevision, ...],
    ) -> Self:
        claims = tuple(
            RevisionPlanningExistingClaimInput(
                source_claim_id=item.source.source_claim_id,
                claim_identity_id=item.claim_identity_id,
                claim_revision_id=item.claim_revision_id,
                statement=item.statement,
                source_note_path=item.source.source_note_path,
                source_note_sha256=item.source.source_note_sha256,
                scopes=item.scopes,
            )
            for item in sorted(
                existing_claim_revisions,
                key=lambda value: value.source.source_claim_id,
            )
        )
        values: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": "recorded-revision-planning-v1",
            "run_id": _exact_run_id(run_id),
            "impact_workload_id": eligibility.workload_id,
            "impact_workload_sha256": eligibility.workload_sha256,
            "impact_result_id": eligibility.result_id,
            "impact_result_sha256": eligibility.result_sha256,
            "analysis_set_id": analysis_set_id,
            "analysis_set_sha256": analysis_set_sha256,
            "analysis_as_of": analysis_as_of.isoformat(),
            "target": target.model_dump(mode="json"),
            "predecessor": predecessor.model_dump(mode="json"),
            "predecessor_raw_utf8": predecessor_raw_utf8,
            "predecessor_source_note_path": canonical_repo_relative(
                predecessor_source_note_path
            ),
            "predecessor_source_note_utf8": predecessor_source_note_utf8,
            "citation_inputs": citation_inputs.model_dump(mode="json"),
            "existing_claims": [item.model_dump(mode="json") for item in claims],
        }
        payload = {
            "namespace": "mastervault.recorded-revision-planning-input.v1",
            **values,
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    **values,
                    "shard_id": f"revisionin:{digest}",
                    "shard_sha256": digest,
                }
            )
        )


class RevisionPlanningWorkload(_StrictFrozenModel):
    """Canonical all-target workload created only after the global gate passes."""

    schema_version: Literal[1] = 1
    eligibility: RevisionPlanningEligibility
    input_shards: tuple[RevisionPlanningInferenceShard, ...]
    workload_id: str = Field(pattern=_REVISION_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=_SHA256)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.recorded-revision-planning-workload.v1",
            "schema_version": 1,
            "eligibility": self.eligibility.model_dump(mode="json"),
            "input_shard_refs": [
                {"shard_id": item.shard_id, "shard_sha256": item.shard_sha256}
                for item in self.input_shards
            ],
        }

    @model_validator(mode="after")
    def _identity(self) -> Self:
        target_pairs = tuple(
            (item.target.target_key, item.target.document_version_id)
            for item in self.input_shards
        )
        if target_pairs != tuple(sorted(set(target_pairs))):
            raise ValueError("revision workload shards must be unique and canonical")
        if (self.eligibility.status == RevisionPlanningEligibilityStatus.NO_WORK) != (
            not self.input_shards
        ):
            raise ValueError("revision workload must preserve the eligibility gate")
        if tuple(item.target for item in self.input_shards) != self.eligibility.targets:
            raise ValueError("revision workload must cover every eligible target exactly once")
        digest = hashlib.sha256(canonical_json_bytes(self._payload())).hexdigest()
        if self.workload_sha256 != digest or self.workload_id != f"revisionwork:{digest}":
            raise ValueError("revision workload ID/SHA differs from its exact ledger")
        return self

    @classmethod
    def create(
        cls,
        *,
        eligibility: RevisionPlanningEligibility,
        input_shards: tuple[RevisionPlanningInferenceShard, ...],
    ) -> Self:
        ordered = tuple(
            sorted(
                input_shards,
                key=lambda item: (item.target.target_key, item.target.document_version_id),
            )
        )
        payload = {
            "namespace": "mastervault.recorded-revision-planning-workload.v1",
            "schema_version": 1,
            "eligibility": eligibility.model_dump(mode="json"),
            "input_shard_refs": [
                {"shard_id": item.shard_id, "shard_sha256": item.shard_sha256}
                for item in ordered
            ],
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls(
            eligibility=eligibility,
            input_shards=ordered,
            workload_id=f"revisionwork:{digest}",
            workload_sha256=digest,
        )


class RevisionPlanningOutputShard(_StrictFrozenModel):
    """Typed receipt-free output whose bytes are the exact C0 proposal envelope."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_REVISION_WORKLOAD_ID)
    workload_sha256: str = Field(pattern=_SHA256)
    input_shard_id: str = Field(pattern=_REVISION_INPUT_ID)
    input_shard_sha256: str = Field(pattern=_SHA256)
    target_key: str
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID)
    impact_output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID)
    impact_output_shard_sha256: str = Field(pattern=_SHA256)
    validated_response: RevisionPlanningWireResponse
    proposal_output_utf8: str
    output_shard_id: str = Field(pattern=_REVISION_OUTPUT_ID)
    output_shard_sha256: str = Field(pattern=_SHA256)

    @field_validator("target_key")
    @classmethod
    def _output_target(cls, value: str) -> str:
        return _exact_key(value, label="target_key")

    def canonical_bytes(self) -> bytes:
        return self.proposal_output_utf8.encode("utf-8")

    @model_validator(mode="after")
    def _output_identity(self) -> Self:
        if self.workload_id != f"revisionwork:{self.workload_sha256}":
            raise ValueError("revision output workload ID differs from its SHA")
        if self.input_shard_id != f"revisionin:{self.input_shard_sha256}":
            raise ValueError("revision output input ID differs from its SHA")
        if self.impact_output_shard_id != f"impactout:{self.impact_output_shard_sha256}":
            raise ValueError("revision output impact shard ID differs from its SHA")
        if self.validated_response.target_key != self.target_key:
            raise ValueError("revision output response differs from its exact target")
        try:
            decoded = json.loads(self.proposal_output_utf8)
        except (TypeError, ValueError) as exc:
            raise ValueError("revision proposal output is not JSON") from exc
        if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != self.canonical_bytes():
            raise ValueError("revision proposal output must be exact canonical JSON")
        expected_kind = (
            "proposed-revision"
            if self.validated_response.kind == "affected-revision"
            else "no-change"
        )
        if decoded.get("kind") != expected_kind:
            raise ValueError("revision proposal kind differs from validated semantic response")
        digest = hashlib.sha256(self.canonical_bytes()).hexdigest()
        if self.output_shard_sha256 != digest or (
            self.output_shard_id != f"revisionout:{digest}"
        ):
            raise ValueError("revision output ID/SHA differs from exact proposal bytes")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: RevisionPlanningWorkload,
        input_shard: RevisionPlanningInferenceShard,
        validated_response: RevisionPlanningWireResponse,
        proposal_output_bytes: bytes,
    ) -> Self:
        try:
            proposal_utf8 = proposal_output_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("revision proposal output must be UTF-8") from exc
        digest = hashlib.sha256(proposal_output_bytes).hexdigest()
        return cls(
            workload_id=workload.workload_id,
            workload_sha256=workload.workload_sha256,
            input_shard_id=input_shard.shard_id,
            input_shard_sha256=input_shard.shard_sha256,
            target_key=input_shard.target.target_key,
            document_version_id=input_shard.target.document_version_id,
            impact_output_shard_id=input_shard.target.output_shard_id,
            impact_output_shard_sha256=input_shard.target.output_shard_sha256,
            validated_response=validated_response,
            proposal_output_utf8=proposal_utf8,
            output_shard_id=f"revisionout:{digest}",
            output_shard_sha256=digest,
        )


class UnresolvedImpactForRevisionPlanningError(RuntimeError):
    """At least one selected output remains unresolved, so no side effect is safe."""

    def __init__(self, target_keys: tuple[str, ...]) -> None:
        self.target_keys = target_keys
        super().__init__(
            "managed revision planning is blocked by unresolved impact targets: "
            + ", ".join(target_keys)
        )


def evaluate_revision_planning_eligibility(
    results: ImpactResultSet,
) -> RevisionPlanningEligibility:
    """Freeze the all-target gate before any later provider or staging side effect."""

    exact = ImpactResultSet.model_validate_json(
        canonical_json_bytes(results.model_dump(mode="json"))
    )
    workload = exact.workload
    common: dict[str, Any] = {
        "workload_id": workload.index.workload_id,
        "workload_sha256": workload.index.workload_sha256,
        "result_id": exact.result_id,
        "result_sha256": exact.result_sha256,
    }
    if not workload.questions:
        return RevisionPlanningEligibility(
            status=RevisionPlanningEligibilityStatus.NO_WORK,
            targets=(),
            **common,
        )

    inputs = {item.shard_id: item for item in workload.input_shards}
    unresolved: list[str] = []
    targets: list[RevisionPlanningTarget] = []
    for output in exact.output_shards:
        source = inputs[output.input_shard_id]
        target_key = source.target_note.document.document_id
        if output.document_disposition == ImpactDisposition.UNRESOLVED:
            unresolved.append(target_key)
            continue
        kind: Literal["affected-revision", "no-change"] = (
            "affected-revision"
            if output.document_disposition == ImpactDisposition.AFFECTED
            else "no-change"
        )
        targets.append(
            RevisionPlanningTarget(
                target_key=target_key,
                document_version_id=output.document_version_id,
                input_shard_id=output.input_shard_id,
                input_shard_sha256=output.input_shard_sha256,
                output_shard_id=output.output_shard_id,
                output_shard_sha256=output.output_shard_sha256,
                question_ids=tuple(item.question_id for item in output.decisions),
                required_response_kind=kind,
            )
        )
    if unresolved:
        raise UnresolvedImpactForRevisionPlanningError(tuple(sorted(set(unresolved))))
    return RevisionPlanningEligibility(
        status=RevisionPlanningEligibilityStatus.ELIGIBLE,
        targets=tuple(sorted(targets, key=lambda item: (item.target_key, item.document_version_id))),
        **common,
    )


def parse_revision_planning_wire_response(payload: str | bytes) -> RevisionPlanningWireResponse:
    """Parse a strict provider response without deriving any authoritative artifact."""

    return _WIRE_RESPONSE_ADAPTER.validate_json(payload, strict=True)


def validate_revision_planning_wire_response(
    response: RevisionPlanningWireResponse,
    *,
    target: RevisionPlanningTarget,
    predecessor_raw_utf8: str,
    citation_inputs: RevisionPlanningCitationInputSet,
    existing_claim_statements: Mapping[str, str],
) -> RevisionPlanningWireResponse:
    """Validate locally knowable IDs and Python-character ranges against exact inputs."""

    exact = _WIRE_RESPONSE_ADAPTER.validate_json(
        canonical_json_bytes(response.model_dump(mode="json")), strict=True
    )
    exact_target = RevisionPlanningTarget.model_validate_json(
        canonical_json_bytes(target.model_dump(mode="json")), strict=True
    )
    exact_citation_inputs = RevisionPlanningCitationInputSet.model_validate_json(
        canonical_json_bytes(citation_inputs.model_dump(mode="json")), strict=True
    )
    if (
        exact.target_key != exact_target.target_key
        or exact.question_ids != exact_target.question_ids
    ):
        raise ValueError("revision-planning response does not cover the exact selected target")
    if exact.kind != exact_target.required_response_kind:
        raise ValueError("revision-planning response kind differs from locally selected impact")

    citation_by_selector = {
        item.input_selector: item for item in exact_citation_inputs.inputs
    }

    def validate_citation(
        citation: RevisionPlanningCitationSelector,
    ) -> RevisionPlanningCitationInputRole:
        citation_input = citation_by_selector.get(citation.input_selector)
        if citation_input is None:
            raise ValueError("citation selector names an unavailable inference input")
        if citation.end_char > len(citation_input.text_utf8):
            raise ValueError("citation selector exceeds its exact Python-character input")
        cited_text = citation_input.text_utf8[citation.start_char : citation.end_char]
        if not cited_text.strip():
            raise ValueError("citation selector must resolve to non-whitespace evidence")
        return citation_input.role

    if isinstance(exact, NoChangeRevisionWireResponse):
        roles = {validate_citation(citation) for citation in exact.citations}
        if RevisionPlanningCitationInputRole.TARGET_EVIDENCE not in roles:
            raise ValueError("no-change response requires target-evidence grounding")
        return exact

    for edit in exact.edits:
        if edit.end_char > len(predecessor_raw_utf8):
            raise ValueError("revision edit exceeds exact predecessor Python characters")
        before = predecessor_raw_utf8[edit.start_char : edit.end_char]
        if edit.replacement_text == before:
            raise ValueError("revision edit must change the exact predecessor characters")
        roles = {validate_citation(citation) for citation in edit.citations}
        if RevisionPlanningCitationInputRole.GOVERNING_EVIDENCE not in roles:
            raise ValueError("affected revision edit requires governing-evidence grounding")
    for rewrite in exact.source_claim_statement_rewrites:
        current = existing_claim_statements.get(rewrite.source_claim_id)
        if current is None:
            raise ValueError("claim rewrite names a non-existing stable source claim")
        if rewrite.replacement_statement == current:
            raise ValueError("claim rewrite must change the existing statement")
        replacements: list[tuple[int, int, str]] = []
        for ordinal in rewrite.edit_ordinals:
            edit = exact.edits[ordinal]
            before = predecessor_raw_utf8[edit.start_char : edit.end_char]
            if not before:
                raise ValueError("claim-linked revision edits cannot be empty insertions")
            first = current.find(before)
            if first < 0 or current.find(before, first + 1) >= 0:
                raise ValueError(
                    "claim-linked edit before_text must occur exactly once in its predecessor claim"
                )
            replacements.append((first, first + len(before), edit.replacement_text))
        ordered_replacements = sorted(replacements)
        if any(
            right[0] < left[1]
            for left, right in zip(
                ordered_replacements,
                ordered_replacements[1:],
                strict=False,
            )
        ):
            raise ValueError("claim-linked revision edits overlap in predecessor claim text")
        derived = current
        for start, end, replacement in reversed(ordered_replacements):
            derived = derived[:start] + replacement + derived[end:]
        if derived != rewrite.replacement_statement:
            raise ValueError(
                "replacement_statement must equal the mechanically rewritten predecessor claim"
            )
    return exact


__all__ = [
    "MAX_REVISION_PLANNING_CITATIONS_PER_EDIT_V1",
    "MAX_REVISION_PLANNING_CITATION_INPUTS_V1",
    "MAX_REVISION_PLANNING_CITATION_INPUT_UTF8_BYTES_V1",
    "MAX_REVISION_PLANNING_CLAIM_REWRITES_V1",
    "MAX_REVISION_PLANNING_EDITS_V1",
    "MAX_REVISION_PLANNING_LOGICAL_KEY_UTF8_BYTES_V1",
    "MAX_REVISION_PLANNING_NO_CHANGE_CITATIONS_V1",
    "MAX_REVISION_PLANNING_QUESTIONS_V1",
    "MAX_REVISION_PLANNING_RATIONALE_UTF8_BYTES_V1",
    "MAX_REVISION_PLANNING_TARGETS_V1",
    "MAX_REVISION_PLANNING_TEXT_UTF8_BYTES_V1",
    "AffectedRevisionEditWire",
    "AffectedRevisionWireResponse",
    "NoChangeRevisionWireResponse",
    "RevisionPlanningCitationInput",
    "RevisionPlanningCitationInputRole",
    "RevisionPlanningCitationInputSet",
    "RevisionPlanningCitationSelector",
    "RevisionPlanningEligibility",
    "RevisionPlanningEligibilityStatus",
    "RevisionPlanningExistingClaimInput",
    "RevisionPlanningInferenceShard",
    "RevisionPlanningOutputShard",
    "RevisionPlanningTarget",
    "RevisionPlanningWorkload",
    "RevisionPlanningWireResponse",
    "StableSourceClaimStatementRewriteWire",
    "UnresolvedImpactForRevisionPlanningError",
    "evaluate_revision_planning_eligibility",
    "parse_revision_planning_wire_response",
    "validate_revision_planning_wire_response",
]
