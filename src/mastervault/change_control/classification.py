"""Pure, content-addressed contracts for relationship classification.

Discovery remains exhaustive and advisory.  This module deterministically
selects a bounded classifier workload without truncation, records a reason for
every excluded candidate, and validates provider-independent classification
results before exposing graph-compatible ``RelationAssessment`` values.

There is deliberately no provider, persistence, evaluator, or orchestration
code here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.discovery import (
    MAX_CHANGED_ROOTS_V1,
    MAX_RELATIONSHIP_CANDIDATES_V1,
    RelationshipCandidate,
    RelationshipCandidateSet,
    generate_relationship_candidates,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    ComparableClaimPair,
    PairDisposition,
    RelationAssessment,
    VersionedClaimRevision,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from mastervault.change_control.store import ChangeControlSnapshot

MAX_CLASSIFIER_WORKLOAD_PAIRS_V1 = 256
MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1 = 4_000
# Backwards-compatible import alias.  The enforced contract is bytes, not
# Python code points; callers should prefer the explicitly named constant.
MAX_CLASSIFICATION_RATIONALE_CHARS_V1 = MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1
MAX_LEXICAL_POLICY_SLOT_PAIRS_PER_CHANGED_ROOT_V1 = 8
MAX_COVERAGE_SAMPLE_PAIRS_PER_CHANGED_ROOT_V1 = 4
MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1 = 1_048_576
MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1 = 2_048
MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1 = 256 * 1024
MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1 = 256 * 1024
MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1 = 256 * 1024

_PAIR_ID_PATTERN = r"^pair:[0-9a-f]{64}$"
_WORKLOAD_ID_PATTERN = r"^classwork:[0-9a-f]{64}$"
_CLASSIFICATION_ID_PATTERN = r"^pairclass:[0-9a-f]{64}$"
_RESULT_SET_ID_PATTERN = r"^classresult:[0-9a-f]{64}$"
_SHARD_ID_PATTERN = r"^classshard:[0-9a-f]{64}$"
_OUTPUT_SHARD_ID_PATTERN = r"^classout:[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassificationLimitError(RuntimeError):
    """A fixed classifier-workload limit was exceeded; no prefix was selected."""

    def __init__(
        self,
        *,
        limit: int,
        observed: int,
        category: str = "selected-pairs",
    ) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        super().__init__(f"classification workload limit exceeded: {category}={observed} > {limit}")


class ClassifierSelectorVersion(StrEnum):
    BOUNDED_POLICY_CONTEXT_V1 = "bounded-policy-context-v1"


class CandidateSelectionReason(StrEnum):
    SAME_CLAIM_IDENTITY = "same-claim-identity"
    SAME_DOCUMENT_FAMILY = "same-document-family"
    SHARED_SCOPE = "shared-scope"
    LEXICAL_POLICY_SLOT = "lexical-policy-slot"
    DETERMINISTIC_COVERAGE_SAMPLE = "deterministic-coverage-sample"


class CandidateExclusionReason(StrEnum):
    LEXICAL_POLICY_SLOT_QUOTA = "lexical-policy-slot-quota"
    COVERAGE_SAMPLE_QUOTA = "coverage-sample-quota"


class GraphMaterializationStatus(StrEnum):
    GRAPH_VALID = "graph-valid"
    ADVISORY_ONLY = "advisory-only"
    NO_EDGE = "no-edge"


_SELECTION_REASON_ORDER = {
    CandidateSelectionReason.SAME_CLAIM_IDENTITY: 0,
    CandidateSelectionReason.SAME_DOCUMENT_FAMILY: 1,
    CandidateSelectionReason.SHARED_SCOPE: 2,
    CandidateSelectionReason.LEXICAL_POLICY_SLOT: 3,
    CandidateSelectionReason.DETERMINISTIC_COVERAGE_SAMPLE: 4,
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ISO_DATE_PATTERN = re.compile(r"(?<![0-9])\d{4}-\d{2}-\d{2}(?![0-9])")
_NUMBER_UNIT_PATTERN = re.compile(
    r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*"
    r"(days?|hours?|weeks?|months?|years?|percent|%|usd|gbp|eur|dollars?|pounds?|euros?)"
    r"(?![a-z0-9])"
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "all",
        "are",
        "as",
        "at",
        "any",
        "be",
        "by",
        "each",
        "every",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "may",
        "must",
        "no",
        "not",
        "of",
        "on",
        "or",
        "shall",
        "should",
        "some",
        "that",
        "the",
        "their",
        "this",
        "to",
        "with",
    }
)
_UNIT_CONTEXT_TOKENS = frozenset(
    {
        "day",
        "days",
        "hour",
        "hours",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "percent",
        "usd",
        "gbp",
        "eur",
        "dollar",
        "dollars",
        "pound",
        "pounds",
        "euro",
        "euros",
    }
)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _content_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{_sha256(payload)}"


def _canonical_rationale(value: str) -> str:
    if value != " ".join(value.split()) or not value:
        raise ValueError("classification rationale must be canonical non-empty text")
    if len(value.encode("utf-8")) > MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1:
        raise ValueError("classification rationale exceeds the fixed v1 UTF-8 byte limit")
    return value


def relationship_candidate_sha256(candidate: RelationshipCandidate) -> str:
    """Hash one complete canonical candidate projection."""

    return _sha256(
        {
            "namespace": "mastervault.relationship-candidate-binding.v1",
            "candidate": candidate.model_dump(mode="json"),
        }
    )


class SelectedCandidateRef(_StrictFrozenModel):
    pair_id: str = Field(pattern=_PAIR_ID_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    reasons: tuple[CandidateSelectionReason, ...] = Field(min_length=1, max_length=3)

    @field_validator("reasons")
    @classmethod
    def _canonical_reasons(
        cls, values: tuple[CandidateSelectionReason, ...]
    ) -> tuple[CandidateSelectionReason, ...]:
        expected = tuple(sorted(set(values), key=_SELECTION_REASON_ORDER.__getitem__))
        if values != expected:
            raise ValueError("selection reasons must be unique and use canonical policy order")
        return values

    @model_validator(mode="after")
    def _bounded_projection(self) -> Self:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > (
            MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1
        ):
            raise ValueError("selected candidate ledger entry exceeds the fixed v1 byte limit")
        return self


class ExcludedCandidateRef(_StrictFrozenModel):
    pair_id: str = Field(pattern=_PAIR_ID_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    reason: CandidateExclusionReason

    @model_validator(mode="after")
    def _bounded_projection(self) -> Self:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > (
            MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1
        ):
            raise ValueError("excluded candidate ledger entry exceeds the fixed v1 byte limit")
        return self


class ClassificationInferencePair(_StrictFrozenModel):
    """Exact semantic/provider input for one selected advisory candidate."""

    schema_version: Literal[1] = 1
    candidate: RelationshipCandidate
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    endpoint_revisions: tuple[VersionedClaimRevision, VersionedClaimRevision]
    input_pair_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.classification-inference-pair.v1",
            "schema_version": self.schema_version,
            "candidate": self.candidate.model_dump(mode="json"),
            "candidate_sha256": self.candidate_sha256,
            "endpoint_revisions": [
                revision.model_dump(mode="json") for revision in self.endpoint_revisions
            ],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.candidate_sha256 != relationship_candidate_sha256(self.candidate):
            raise ValueError("inference-pair candidate SHA does not match its candidate")
        endpoint_ids = tuple(revision.claim_revision_id for revision in self.endpoint_revisions)
        if endpoint_ids != tuple(sorted(set(endpoint_ids))):
            raise ValueError("inference-pair endpoints must use two unique canonical IDs")
        if endpoint_ids != self.candidate.claim_revision_ids:
            raise ValueError("inference-pair endpoints do not exactly match its candidate")
        pair = ComparableClaimPair.create(*self.endpoint_revisions)
        if pair.pair_id != self.candidate.pair_id:
            raise ValueError("inference-pair claim payloads do not match its candidate pair")
        if self.input_pair_sha256 != _sha256(self._payload()):
            raise ValueError("inference-pair SHA does not match its exact semantic inputs")
        return self

    @classmethod
    def create(
        cls,
        *,
        candidate: RelationshipCandidate,
        revisions_by_id: dict[str, VersionedClaimRevision],
    ) -> Self:
        endpoints = tuple(
            revisions_by_id[revision_id] for revision_id in candidate.claim_revision_ids
        )
        typed_endpoints = (endpoints[0], endpoints[1])
        candidate_sha = relationship_candidate_sha256(candidate)
        payload = {
            "namespace": "mastervault.classification-inference-pair.v1",
            "schema_version": 1,
            "candidate": candidate.model_dump(mode="json"),
            "candidate_sha256": candidate_sha,
            "endpoint_revisions": [
                revision.model_dump(mode="json") for revision in typed_endpoints
            ],
        }
        return cls(
            candidate=candidate,
            candidate_sha256=candidate_sha,
            endpoint_revisions=typed_endpoints,
            input_pair_sha256=_sha256(payload),
        )


class ClassificationInferenceShard(_StrictFrozenModel):
    """Compact per-changed-root input inventory below the managed-artifact ceiling."""

    schema_version: Literal[1] = 1
    changed_claim_revision_id: str = Field(pattern=r"^claimrev:[0-9a-f]{64}$")
    pairs: tuple[ClassificationInferencePair, ...] = Field(
        min_length=1,
        max_length=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
    )
    shard_id: str = Field(pattern=_SHARD_ID_PATTERN)
    shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.classification-inference-shard.v1",
            "schema_version": self.schema_version,
            "changed_claim_revision_id": self.changed_claim_revision_id,
            "pairs": [pair.model_dump(mode="json") for pair in self.pairs],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        pair_ids = tuple(pair.candidate.pair_id for pair in self.pairs)
        if pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("inference-shard pairs must be unique and canonically ordered")
        if any(
            pair.candidate.changed_claim_revision_id != self.changed_claim_revision_id
            for pair in self.pairs
        ):
            raise ValueError("inference-shard pairs must share its exact changed root")
        payload = self._payload()
        if len(canonical_json_bytes(payload)) > MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("classification inference shard exceeds the managed-artifact ceiling")
        digest = _sha256(payload)
        if self.shard_sha256 != digest or self.shard_id != f"classshard:{digest}":
            raise ValueError("classification inference shard ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        changed_claim_revision_id: str,
        pairs: tuple[ClassificationInferencePair, ...],
    ) -> Self:
        canonical = tuple(sorted(pairs, key=lambda item: item.candidate.pair_id))
        payload = {
            "namespace": "mastervault.classification-inference-shard.v1",
            "schema_version": 1,
            "changed_claim_revision_id": changed_claim_revision_id,
            "pairs": [pair.model_dump(mode="json") for pair in canonical],
        }
        digest = _sha256(payload)
        return cls(
            changed_claim_revision_id=changed_claim_revision_id,
            pairs=canonical,
            shard_id=f"classshard:{digest}",
            shard_sha256=digest,
        )


def _workload_payload(
    *,
    schema_version: int,
    selector_version: ClassifierSelectorVersion,
    aggregate_id: str,
    snapshot_revision: int,
    aggregate_sha256: str,
    source_candidate_set_sha256: str,
    source_candidate_count: int,
    selected: tuple[SelectedCandidateRef, ...],
    excluded: tuple[ExcludedCandidateRef, ...],
    inference_shards: tuple[ClassificationInferenceShard, ...],
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.classifier-workload.v1",
        "schema_version": schema_version,
        "selector_version": selector_version.value,
        "aggregate_id": aggregate_id,
        "snapshot_revision": snapshot_revision,
        "aggregate_sha256": aggregate_sha256,
        "source_candidate_set_sha256": source_candidate_set_sha256,
        "source_candidate_count": source_candidate_count,
        "selected": [item.model_dump(mode="json") for item in selected],
        "excluded": [item.model_dump(mode="json") for item in excluded],
        "inference_shards": [item.model_dump(mode="json") for item in inference_shards],
    }


class ClassificationWorkload(_StrictFrozenModel):
    """Complete selected/excluded partition of one exhaustive candidate set."""

    schema_version: Literal[1] = 1
    selector_version: Literal[ClassifierSelectorVersion.BOUNDED_POLICY_CONTEXT_V1] = (
        ClassifierSelectorVersion.BOUNDED_POLICY_CONTEXT_V1
    )
    aggregate_id: str
    snapshot_revision: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=SHA256_PATTERN)
    source_candidate_set_sha256: str = Field(pattern=SHA256_PATTERN)
    source_candidate_count: int = Field(ge=0, le=MAX_RELATIONSHIP_CANDIDATES_V1)
    selected: tuple[SelectedCandidateRef, ...] = Field(max_length=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1)
    excluded: tuple[ExcludedCandidateRef, ...] = Field(max_length=MAX_RELATIONSHIP_CANDIDATES_V1)
    inference_shards: tuple[ClassificationInferenceShard, ...] = Field(
        max_length=MAX_CHANGED_ROOTS_V1
    )
    workload_id: str = Field(pattern=_WORKLOAD_ID_PATTERN)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return _workload_payload(
            schema_version=self.schema_version,
            selector_version=self.selector_version,
            aggregate_id=self.aggregate_id,
            snapshot_revision=self.snapshot_revision,
            aggregate_sha256=self.aggregate_sha256,
            source_candidate_set_sha256=self.source_candidate_set_sha256,
            source_candidate_count=self.source_candidate_count,
            selected=self.selected,
            excluded=self.excluded,
            inference_shards=self.inference_shards,
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        selected_ids = tuple(item.pair_id for item in self.selected)
        excluded_ids = tuple(item.pair_id for item in self.excluded)
        if selected_ids != tuple(sorted(set(selected_ids))):
            raise ValueError("selected candidate references must use unique pair-ID order")
        if excluded_ids != tuple(sorted(set(excluded_ids))):
            raise ValueError("excluded candidate references must use unique pair-ID order")
        if set(selected_ids) & set(excluded_ids):
            raise ValueError("selected and excluded candidate references must be disjoint")
        if len(selected_ids) + len(excluded_ids) != self.source_candidate_count:
            raise ValueError(
                "workload partition does not cover its declared source candidate count"
            )
        changed_ids = tuple(item.changed_claim_revision_id for item in self.inference_shards)
        if changed_ids != tuple(sorted(set(changed_ids))):
            raise ValueError("inference shards must use unique changed-root ID order")
        sharded_pair_ids = tuple(
            pair.candidate.pair_id for shard in self.inference_shards for pair in shard.pairs
        )
        if len(sharded_pair_ids) != len(set(sharded_pair_ids)):
            raise ValueError("selected pair IDs must occur in exactly one inference shard")
        if set(sharded_pair_ids) != set(selected_ids):
            raise ValueError("inference shards must exactly cover selected candidate pairs")
        payload = self._payload()
        if len(canonical_json_bytes(payload)) > MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1:
            raise ValueError("classifier workload exceeds the fixed v1 canonical byte limit")
        digest = _sha256(payload)
        if self.workload_sha256 != digest or self.workload_id != f"classwork:{digest}":
            raise ValueError("classifier workload ID/SHA does not match its complete partition")
        return self

    @classmethod
    def create(
        cls,
        *,
        candidates: RelationshipCandidateSet,
        revisions_by_id: dict[str, VersionedClaimRevision],
        selected: tuple[SelectedCandidateRef, ...],
        excluded: tuple[ExcludedCandidateRef, ...],
    ) -> Self:
        candidates = RelationshipCandidateSet.model_validate(candidates.model_dump(mode="json"))
        canonical_selected = tuple(
            sorted(
                (
                    SelectedCandidateRef.model_validate(item.model_dump(mode="json"))
                    for item in selected
                ),
                key=lambda item: item.pair_id,
            )
        )
        canonical_excluded = tuple(
            sorted(
                (
                    ExcludedCandidateRef.model_validate(item.model_dump(mode="json"))
                    for item in excluded
                ),
                key=lambda item: item.pair_id,
            )
        )
        candidate_by_id = {candidate.pair_id: candidate for candidate in candidates.candidates}
        selected_pair_ids = tuple(item.pair_id for item in canonical_selected)
        excluded_pair_ids = tuple(item.pair_id for item in canonical_excluded)
        if len(selected_pair_ids) != len(set(selected_pair_ids)):
            raise ValueError("selected candidate references must be unique")
        if len(excluded_pair_ids) != len(set(excluded_pair_ids)):
            raise ValueError("excluded candidate references must be unique")
        if set(selected_pair_ids) & set(excluded_pair_ids):
            raise ValueError("selected and excluded candidate references must be disjoint")
        if set(selected_pair_ids) | set(excluded_pair_ids) != set(candidate_by_id):
            raise ValueError("selected and excluded references must exactly partition candidates")
        for reference in canonical_selected:
            candidate = candidate_by_id[reference.pair_id]
            if reference.candidate_sha256 != relationship_candidate_sha256(candidate):
                raise ValueError("candidate reference SHA differs from its exact candidate")
        for excluded_reference in canonical_excluded:
            candidate = candidate_by_id[excluded_reference.pair_id]
            if excluded_reference.candidate_sha256 != relationship_candidate_sha256(candidate):
                raise ValueError("candidate reference SHA differs from its exact candidate")
        if len(canonical_selected) > MAX_CLASSIFIER_WORKLOAD_PAIRS_V1:
            raise ClassificationLimitError(
                limit=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
                observed=len(canonical_selected),
            )
        selected_ids = {item.pair_id for item in canonical_selected}
        pairs_by_changed: dict[str, list[ClassificationInferencePair]] = {}
        for candidate in candidates.candidates:
            if candidate.pair_id in selected_ids:
                pairs_by_changed.setdefault(
                    candidate.changed_claim_revision_id,
                    [],
                ).append(
                    ClassificationInferencePair.create(
                        candidate=candidate,
                        revisions_by_id=revisions_by_id,
                    )
                )
        inference_shards = tuple(
            ClassificationInferenceShard.create(
                changed_claim_revision_id=changed_id,
                pairs=tuple(pairs_by_changed[changed_id]),
            )
            for changed_id in sorted(pairs_by_changed)
        )
        binding = candidates.binding
        payload = _workload_payload(
            schema_version=1,
            selector_version=ClassifierSelectorVersion.BOUNDED_POLICY_CONTEXT_V1,
            aggregate_id=binding.aggregate_id,
            snapshot_revision=binding.snapshot_revision,
            aggregate_sha256=binding.aggregate_sha256,
            source_candidate_set_sha256=candidates.result_sha256,
            source_candidate_count=len(candidates.candidates),
            selected=canonical_selected,
            excluded=canonical_excluded,
            inference_shards=inference_shards,
        )
        if len(canonical_json_bytes(payload)) > MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1:
            raise ClassificationLimitError(
                limit=MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1,
                observed=len(canonical_json_bytes(payload)),
                category="canonical-bytes",
            )
        digest = _sha256(payload)
        return cls(
            aggregate_id=binding.aggregate_id,
            snapshot_revision=binding.snapshot_revision,
            aggregate_sha256=binding.aggregate_sha256,
            source_candidate_set_sha256=candidates.result_sha256,
            source_candidate_count=len(candidates.candidates),
            selected=canonical_selected,
            excluded=canonical_excluded,
            inference_shards=inference_shards,
            workload_id=f"classwork:{digest}",
            workload_sha256=digest,
        )


def _selection_reasons(candidate: RelationshipCandidate) -> tuple[CandidateSelectionReason, ...]:
    reasons: list[CandidateSelectionReason] = []
    if candidate.score.same_claim_identity:
        reasons.append(CandidateSelectionReason.SAME_CLAIM_IDENTITY)
    if candidate.score.same_document_family:
        reasons.append(CandidateSelectionReason.SAME_DOCUMENT_FAMILY)
    if candidate.shared_scopes:
        reasons.append(CandidateSelectionReason.SHARED_SCOPE)
    return tuple(reasons)


def _content_tokens(statement: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", statement).casefold()
    return frozenset(
        token
        for token in _TOKEN_PATTERN.findall(normalized)
        if len(token) >= 3 and token not in _STOPWORDS and not token.isdigit()
    )


def _number_units(statement: str) -> dict[str, frozenset[str]]:
    normalized = unicodedata.normalize("NFKC", statement).casefold()
    values: dict[str, set[str]] = {}
    for number, raw_unit in _NUMBER_UNIT_PATTERN.findall(normalized):
        unit = raw_unit.rstrip("s")
        if unit in {"percent", "%"}:
            unit = "percent"
        elif unit in {"dollar", "usd"}:
            unit = "usd"
        elif unit in {"pound", "gbp"}:
            unit = "gbp"
        elif unit in {"euro", "eur"}:
            unit = "eur"
        values.setdefault(unit, set()).add(number)
    return {unit: frozenset(numbers) for unit, numbers in values.items()}


def _lexical_policy_slot_score(
    first: VersionedClaimRevision,
    second: VersionedClaimRevision,
    *,
    document_frequency: Counter[str],
    claim_count: int,
) -> tuple[int, int, int, int, int] | None:
    first_tokens = _content_tokens(first.statement)
    second_tokens = _content_tokens(second.statement)
    shared = first_tokens & second_tokens
    rare_threshold = max(2, claim_count // 5)
    rare_shared = tuple(token for token in shared if document_frequency[token] <= rare_threshold)
    rare_context = tuple(token for token in rare_shared if token not in _UNIT_CONTEXT_TOKENS)
    rarity_score = sum(1_000_000 // document_frequency[token] for token in rare_shared)

    first_units = _number_units(first.statement)
    second_units = _number_units(second.statement)
    differing_unit_slots = sum(
        1
        for unit in first_units.keys() & second_units.keys()
        if first_units[unit] != second_units[unit]
    )
    shared_dates = len(
        set(_ISO_DATE_PATTERN.findall(first.statement))
        & set(_ISO_DATE_PATTERN.findall(second.statement))
    )
    has_rare_context = bool(rare_context)
    qualifies = (len(shared) >= 2 and bool(rare_shared)) or (
        has_rare_context and bool(differing_unit_slots or shared_dates)
    )
    if not qualifies:
        return None
    return rarity_score, len(rare_context), len(shared), differing_unit_slots, shared_dates


def _coverage_sample_key(
    *,
    candidate: RelationshipCandidate,
    aggregate_sha256: str,
    candidate_set_sha256: str,
) -> tuple[str, str]:
    digest = _sha256(
        {
            "namespace": "mastervault.deterministic-classification-coverage-sample.v1",
            "selector_version": ClassifierSelectorVersion.BOUNDED_POLICY_CONTEXT_V1.value,
            "aggregate_sha256": aggregate_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "changed_claim_revision_id": candidate.changed_claim_revision_id,
            "pair_id": candidate.pair_id,
            "candidate_sha256": relationship_candidate_sha256(candidate),
        }
    )
    return digest, candidate.pair_id


def _revalidate_candidate_set(
    snapshot: ChangeControlSnapshot,
    candidates: RelationshipCandidateSet,
) -> RelationshipCandidateSet:
    validated = RelationshipCandidateSet.model_validate(candidates.model_dump(mode="json"))
    binding = validated.binding
    expected = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=binding.changed_claim_revision_ids,
        as_of=binding.as_of,
    )
    if validated != expected:
        raise ValueError(
            "relationship candidates are not the complete deterministic set for this snapshot"
        )
    return validated


def select_classification_workload(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
) -> ClassificationWorkload:
    """Select every shared-context pair or fail without returning a truncated prefix."""

    candidates = _revalidate_candidate_set(snapshot, candidates)
    revisions = {
        revision.claim_revision_id: revision for revision in snapshot.aggregate.claims.revisions
    }
    token_sets = tuple(_content_tokens(revision.statement) for revision in revisions.values())
    document_frequency: Counter[str] = Counter(token for tokens in token_sets for token in tokens)
    selected_reasons: dict[str, tuple[CandidateSelectionReason, ...]] = {}
    excluded_reasons: dict[str, CandidateExclusionReason] = {}
    remaining_by_changed: dict[str, list[RelationshipCandidate]] = {}
    for candidate in candidates.candidates:
        reasons = _selection_reasons(candidate)
        if reasons:
            selected_reasons[candidate.pair_id] = reasons
        else:
            remaining_by_changed.setdefault(candidate.changed_claim_revision_id, []).append(
                candidate
            )

    if len(selected_reasons) > MAX_CLASSIFIER_WORKLOAD_PAIRS_V1:
        raise ClassificationLimitError(
            limit=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
            observed=len(selected_reasons),
        )

    for changed_id in sorted(remaining_by_changed):
        lexical: list[tuple[tuple[int, int, int, int, int], RelationshipCandidate]] = []
        zero_signal: list[RelationshipCandidate] = []
        changed = revisions[changed_id]
        for candidate in remaining_by_changed[changed_id]:
            incumbent = revisions[candidate.incumbent_claim_revision_id]
            score = _lexical_policy_slot_score(
                changed,
                incumbent,
                document_frequency=document_frequency,
                claim_count=len(revisions),
            )
            if score is None:
                zero_signal.append(candidate)
            else:
                lexical.append((score, candidate))
        lexical.sort(
            key=lambda item: (
                -item[0][0],
                -item[0][1],
                -item[0][2],
                -item[0][3],
                -item[0][4],
                item[1].pair_id,
            )
        )
        for _score, candidate in lexical[:MAX_LEXICAL_POLICY_SLOT_PAIRS_PER_CHANGED_ROOT_V1]:
            selected_reasons[candidate.pair_id] = (CandidateSelectionReason.LEXICAL_POLICY_SLOT,)
        for _score, candidate in lexical[MAX_LEXICAL_POLICY_SLOT_PAIRS_PER_CHANGED_ROOT_V1:]:
            excluded_reasons[candidate.pair_id] = CandidateExclusionReason.LEXICAL_POLICY_SLOT_QUOTA
        zero_signal.sort(
            key=lambda candidate: _coverage_sample_key(
                candidate=candidate,
                aggregate_sha256=candidates.binding.aggregate_sha256,
                candidate_set_sha256=candidates.result_sha256,
            )
        )
        for candidate in zero_signal[:MAX_COVERAGE_SAMPLE_PAIRS_PER_CHANGED_ROOT_V1]:
            selected_reasons[candidate.pair_id] = (
                CandidateSelectionReason.DETERMINISTIC_COVERAGE_SAMPLE,
            )
        for candidate in zero_signal[MAX_COVERAGE_SAMPLE_PAIRS_PER_CHANGED_ROOT_V1:]:
            excluded_reasons[candidate.pair_id] = CandidateExclusionReason.COVERAGE_SAMPLE_QUOTA

    if len(selected_reasons) > MAX_CLASSIFIER_WORKLOAD_PAIRS_V1:
        raise ClassificationLimitError(
            limit=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
            observed=len(selected_reasons),
        )

    selected: list[SelectedCandidateRef] = []
    excluded: list[ExcludedCandidateRef] = []
    for candidate in candidates.candidates:
        binding_sha = relationship_candidate_sha256(candidate)
        selected_reason = selected_reasons.get(candidate.pair_id)
        if selected_reason is not None:
            selected.append(
                SelectedCandidateRef(
                    pair_id=candidate.pair_id,
                    candidate_sha256=binding_sha,
                    reasons=selected_reason,
                )
            )
        else:
            excluded.append(
                ExcludedCandidateRef(
                    pair_id=candidate.pair_id,
                    candidate_sha256=binding_sha,
                    reason=excluded_reasons[candidate.pair_id],
                )
            )
    return ClassificationWorkload.create(
        candidates=candidates,
        revisions_by_id=revisions,
        selected=tuple(selected),
        excluded=tuple(excluded),
    )


def validate_classification_workload(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    workload: ClassificationWorkload,
) -> ClassificationWorkload:
    """Recompute discovery and v1 selection, rejecting any altered partition."""

    validated_candidates = _revalidate_candidate_set(snapshot, candidates)
    validated = ClassificationWorkload.model_validate(workload.model_dump(mode="json"))
    expected = select_classification_workload(snapshot, candidates=validated_candidates)
    if validated != expected:
        raise ValueError("classification workload is not the exact deterministic v1 selection")
    return validated


class EndpointEvidenceBinding(_StrictFrozenModel):
    """Complete immutable claim revision plus hashes of its evidence-bearing projections."""

    claim_revision: VersionedClaimRevision
    claim_projection_sha256: str = Field(pattern=SHA256_PATTERN)
    source_evidence_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        projection = self.claim_revision.model_dump(mode="json")
        if self.claim_projection_sha256 != _sha256(
            {
                "namespace": "mastervault.classification-endpoint.v1",
                "claim_revision": projection,
            }
        ):
            raise ValueError("claim endpoint projection SHA does not match its revision")
        if self.source_evidence_sha256 != _sha256(
            {
                "namespace": "mastervault.classification-source-evidence.v1",
                "source": self.claim_revision.source.model_dump(mode="json"),
            }
        ):
            raise ValueError("claim endpoint evidence SHA does not match its source evidence")
        return self

    @classmethod
    def create(cls, claim_revision: VersionedClaimRevision) -> Self:
        return cls(
            claim_revision=claim_revision,
            claim_projection_sha256=_sha256(
                {
                    "namespace": "mastervault.classification-endpoint.v1",
                    "claim_revision": claim_revision.model_dump(mode="json"),
                }
            ),
            source_evidence_sha256=_sha256(
                {
                    "namespace": "mastervault.classification-source-evidence.v1",
                    "source": claim_revision.source.model_dump(mode="json"),
                }
            ),
        )


def _derive_materialization(
    *,
    pair: ComparableClaimPair,
    disposition: PairDisposition,
    rationale: str,
    confidence: float,
    newer_revision_id: str | None,
) -> tuple[GraphMaterializationStatus, RelationAssessment | None]:
    if disposition in {PairDisposition.COEXISTS, PairDisposition.UNRELATED}:
        try:
            assessment = RelationAssessment.create(
                pair=pair,
                disposition=disposition,
                rationale=rationale,
                confidence=confidence,
                newer_revision_id=None,
            )
        except ValueError:
            return GraphMaterializationStatus.ADVISORY_ONLY, None
        return GraphMaterializationStatus.NO_EDGE, assessment

    first, second = pair.claim_revisions
    # Cross-family classifications can be useful advisories, but v1 does not
    # publish graph edges whose policy-family semantics have not been proved.
    if first.document.document_family != second.document.document_family:
        return GraphMaterializationStatus.ADVISORY_ONLY, None
    try:
        assessment = RelationAssessment.create(
            pair=pair,
            disposition=disposition,
            rationale=rationale,
            confidence=confidence,
            newer_revision_id=newer_revision_id,
        )
    except ValueError:
        return GraphMaterializationStatus.ADVISORY_ONLY, None
    return GraphMaterializationStatus.GRAPH_VALID, assessment


def _require_valid_supersedes_direction(
    pair: ComparableClaimPair,
    newer_revision_id: str | None,
) -> None:
    if newer_revision_id is None:
        raise ValueError("SUPERSEDES classification requires one newer endpoint")
    newer = pair.revision(newer_revision_id)
    older = next(
        revision
        for revision in pair.claim_revisions
        if revision.claim_revision_id != newer_revision_id
    )
    if newer.document.document_family != older.document.document_family:
        raise ValueError("cross-family SUPERSEDES classification is invalid")
    if newer.declared_effective_from <= older.declared_effective_from:
        raise ValueError("SUPERSEDES must be directed from strictly newer to older")


def _classification_payload(
    *,
    schema_version: int,
    candidate: RelationshipCandidate,
    candidate_sha256: str,
    endpoints: tuple[EndpointEvidenceBinding, EndpointEvidenceBinding],
    disposition: PairDisposition,
    newer_revision_id: str | None,
    rationale: str,
    confidence: float,
    materialization_status: GraphMaterializationStatus,
    relation_assessment: RelationAssessment | None,
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.claim-pair-classification.v1",
        "schema_version": schema_version,
        "candidate": candidate.model_dump(mode="json"),
        "candidate_sha256": candidate_sha256,
        "endpoints": [item.model_dump(mode="json") for item in endpoints],
        "disposition": disposition.value,
        "newer_revision_id": newer_revision_id,
        "rationale": rationale,
        "confidence": confidence,
        "materialization_status": materialization_status.value,
        "relation_assessment": (
            relation_assessment.model_dump(mode="json") if relation_assessment else None
        ),
    }


class ClaimPairClassification(_StrictFrozenModel):
    """One endpoint/evidence-bound classification with derived graph status."""

    schema_version: Literal[1] = 1
    candidate: RelationshipCandidate
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    endpoints: tuple[EndpointEvidenceBinding, EndpointEvidenceBinding]
    disposition: PairDisposition
    newer_revision_id: str | None = Field(default=None, pattern=r"^claimrev:[0-9a-f]{64}$")
    rationale: str = Field(min_length=1, max_length=MAX_CLASSIFICATION_RATIONALE_CHARS_V1)
    confidence: float = Field(ge=0.0, le=1.0)
    materialization_status: GraphMaterializationStatus
    relation_assessment: RelationAssessment | None = None
    classification_id: str = Field(pattern=_CLASSIFICATION_ID_PATTERN)
    classification_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _canonical_rationale(value)

    def _payload(self) -> dict[str, Any]:
        return _classification_payload(
            schema_version=self.schema_version,
            candidate=self.candidate,
            candidate_sha256=self.candidate_sha256,
            endpoints=self.endpoints,
            disposition=self.disposition,
            newer_revision_id=self.newer_revision_id,
            rationale=self.rationale,
            confidence=self.confidence,
            materialization_status=self.materialization_status,
            relation_assessment=self.relation_assessment,
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.candidate_sha256 != relationship_candidate_sha256(self.candidate):
            raise ValueError("classification candidate SHA does not match its candidate")
        endpoint_ids = tuple(
            endpoint.claim_revision.claim_revision_id for endpoint in self.endpoints
        )
        if endpoint_ids != tuple(sorted(set(endpoint_ids))):
            raise ValueError("classification endpoints must use two unique canonical IDs")
        if endpoint_ids != self.candidate.claim_revision_ids:
            raise ValueError("classification endpoints do not exactly match the candidate pair")
        pair = ComparableClaimPair.create(
            self.endpoints[0].claim_revision,
            self.endpoints[1].claim_revision,
        )
        if pair.pair_id != self.candidate.pair_id:
            raise ValueError("classification pair does not match its candidate ID")
        if self.disposition == PairDisposition.SUPERSEDES:
            if self.newer_revision_id not in endpoint_ids:
                raise ValueError("SUPERSEDES classification requires one newer endpoint")
            _require_valid_supersedes_direction(pair, self.newer_revision_id)
        elif self.newer_revision_id is not None:
            raise ValueError("only SUPERSEDES accepts a newer endpoint")
        expected_status, expected_assessment = _derive_materialization(
            pair=pair,
            disposition=self.disposition,
            rationale=self.rationale,
            confidence=self.confidence,
            newer_revision_id=self.newer_revision_id,
        )
        if (
            self.materialization_status != expected_status
            or self.relation_assessment != expected_assessment
        ):
            raise ValueError("classification graph materialization does not match v1 invariants")
        digest = _sha256(self._payload())
        if len(canonical_json_bytes(self._payload())) > MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1:
            raise ValueError("claim-pair classification exceeds the fixed v1 byte limit")
        if self.classification_sha256 != digest or self.classification_id != f"pairclass:{digest}":
            raise ValueError("classification ID/SHA does not match its complete content")
        return self

    @classmethod
    def create(
        cls,
        *,
        candidate: RelationshipCandidate,
        endpoint_revisions: tuple[VersionedClaimRevision, VersionedClaimRevision],
        disposition: PairDisposition,
        rationale: str,
        confidence: float,
        newer_revision_id: str | None = None,
    ) -> Self:
        canonical_rationale = _canonical_rationale(rationale)
        endpoint_by_id = {item.claim_revision_id: item for item in endpoint_revisions}
        if len(endpoint_by_id) != 2 or set(endpoint_by_id) != set(candidate.claim_revision_ids):
            raise ValueError("classification endpoints must exactly match the candidate pair")
        endpoints = tuple(
            EndpointEvidenceBinding.create(endpoint_by_id[revision_id])
            for revision_id in candidate.claim_revision_ids
        )
        typed_endpoints = (endpoints[0], endpoints[1])
        pair = ComparableClaimPair.create(
            typed_endpoints[0].claim_revision,
            typed_endpoints[1].claim_revision,
        )
        if disposition == PairDisposition.SUPERSEDES:
            if newer_revision_id not in candidate.claim_revision_ids:
                raise ValueError("SUPERSEDES classification requires one newer endpoint")
            _require_valid_supersedes_direction(pair, newer_revision_id)
        elif newer_revision_id is not None:
            raise ValueError("only SUPERSEDES accepts a newer endpoint")
        status, assessment = _derive_materialization(
            pair=pair,
            disposition=disposition,
            rationale=canonical_rationale,
            confidence=confidence,
            newer_revision_id=newer_revision_id,
        )
        candidate_sha = relationship_candidate_sha256(candidate)
        payload = _classification_payload(
            schema_version=1,
            candidate=candidate,
            candidate_sha256=candidate_sha,
            endpoints=typed_endpoints,
            disposition=disposition,
            newer_revision_id=newer_revision_id,
            rationale=canonical_rationale,
            confidence=confidence,
            materialization_status=status,
            relation_assessment=assessment,
        )
        if len(canonical_json_bytes(payload)) > MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1:
            raise ClassificationLimitError(
                limit=MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1,
                observed=len(canonical_json_bytes(payload)),
                category="pair-classification-bytes",
            )
        digest = _sha256(payload)
        return cls(
            candidate=candidate,
            candidate_sha256=candidate_sha,
            endpoints=typed_endpoints,
            disposition=disposition,
            newer_revision_id=newer_revision_id,
            rationale=canonical_rationale,
            confidence=confidence,
            materialization_status=status,
            relation_assessment=assessment,
            classification_id=f"pairclass:{digest}",
            classification_sha256=digest,
        )


class ClassificationOutputItem(_StrictFrozenModel):
    """One classification bound to the exact input-pair artifact projection."""

    input_pair_sha256: str = Field(pattern=SHA256_PATTERN)
    classification: ClaimPairClassification


def _output_shard_payload(
    *,
    schema_version: int,
    workload_id: str,
    workload_sha256: str,
    input_shard_id: str,
    input_shard_sha256: str,
    changed_claim_revision_id: str,
    items: tuple[ClassificationOutputItem, ...],
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.classification-output-shard.v1",
        "schema_version": schema_version,
        "workload_id": workload_id,
        "workload_sha256": workload_sha256,
        "input_shard_id": input_shard_id,
        "input_shard_sha256": input_shard_sha256,
        "changed_claim_revision_id": changed_claim_revision_id,
        "items": [item.model_dump(mode="json") for item in items],
    }


class ClassificationOutputShard(_StrictFrozenModel):
    """One changed-root inference output artifact, always at most 256 KiB."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_WORKLOAD_ID_PATTERN)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    input_shard_id: str = Field(pattern=_SHARD_ID_PATTERN)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    changed_claim_revision_id: str = Field(pattern=r"^claimrev:[0-9a-f]{64}$")
    items: tuple[ClassificationOutputItem, ...] = Field(
        min_length=1,
        max_length=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1,
    )
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID_PATTERN)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return _output_shard_payload(
            schema_version=self.schema_version,
            workload_id=self.workload_id,
            workload_sha256=self.workload_sha256,
            input_shard_id=self.input_shard_id,
            input_shard_sha256=self.input_shard_sha256,
            changed_claim_revision_id=self.changed_claim_revision_id,
            items=self.items,
        )

    def canonical_bytes(self) -> bytes:
        """Return the exact content-addressed inference-output artifact bytes."""

        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        pair_ids = tuple(item.classification.candidate.pair_id for item in self.items)
        if pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("output-shard classifications must be unique and canonically ordered")
        if any(
            item.classification.candidate.changed_claim_revision_id
            != self.changed_claim_revision_id
            for item in self.items
        ):
            raise ValueError("output-shard classifications must share its exact changed root")
        payload = self._payload()
        payload_bytes = len(canonical_json_bytes(payload))
        if payload_bytes > MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1:
            raise ValueError("classification output shard exceeds the managed-artifact ceiling")
        digest = _sha256(payload)
        if self.output_shard_sha256 != digest or self.output_shard_id != f"classout:{digest}":
            raise ValueError("classification output shard ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: ClassificationWorkload,
        input_shard: ClassificationInferenceShard,
        classifications: tuple[ClaimPairClassification, ...],
    ) -> Self:
        workload = ClassificationWorkload.model_validate(workload.model_dump(mode="json"))
        input_shard = ClassificationInferenceShard.model_validate(
            input_shard.model_dump(mode="json")
        )
        expected_input = next(
            (
                item
                for item in workload.inference_shards
                if item.changed_claim_revision_id == input_shard.changed_claim_revision_id
            ),
            None,
        )
        if expected_input != input_shard:
            raise ValueError("output shard input is not an exact shard of its workload")
        canonical = tuple(
            sorted(
                (
                    ClaimPairClassification.model_validate(item.model_dump(mode="json"))
                    for item in classifications
                ),
                key=lambda item: item.candidate.pair_id,
            )
        )
        input_by_pair = {pair.candidate.pair_id: pair for pair in input_shard.pairs}
        if tuple(item.candidate.pair_id for item in canonical) != tuple(sorted(input_by_pair)):
            raise ValueError("output shard must classify every exact input-shard pair once")
        items: list[ClassificationOutputItem] = []
        for classification in canonical:
            inference_pair = input_by_pair[classification.candidate.pair_id]
            _require_classification_matches_inference_pair(classification, inference_pair)
            items.append(
                ClassificationOutputItem(
                    input_pair_sha256=inference_pair.input_pair_sha256,
                    classification=classification,
                )
            )
        typed_items = tuple(items)
        payload = _output_shard_payload(
            schema_version=1,
            workload_id=workload.workload_id,
            workload_sha256=workload.workload_sha256,
            input_shard_id=input_shard.shard_id,
            input_shard_sha256=input_shard.shard_sha256,
            changed_claim_revision_id=input_shard.changed_claim_revision_id,
            items=typed_items,
        )
        payload_bytes = len(canonical_json_bytes(payload))
        if payload_bytes > MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1:
            raise ClassificationLimitError(
                limit=MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1,
                observed=payload_bytes,
                category="output-shard-bytes",
            )
        digest = _sha256(payload)
        return cls(
            workload_id=workload.workload_id,
            workload_sha256=workload.workload_sha256,
            input_shard_id=input_shard.shard_id,
            input_shard_sha256=input_shard.shard_sha256,
            changed_claim_revision_id=input_shard.changed_claim_revision_id,
            items=typed_items,
            output_shard_id=f"classout:{digest}",
            output_shard_sha256=digest,
        )


def _require_classification_matches_inference_pair(
    classification: ClaimPairClassification,
    inference_pair: ClassificationInferencePair,
) -> None:
    if classification.candidate != inference_pair.candidate:
        raise ValueError("classification does not bind the exact inference candidate")
    if classification.candidate_sha256 != inference_pair.candidate_sha256:
        raise ValueError("classification candidate SHA differs from its inference input")
    expected_endpoints = tuple(
        EndpointEvidenceBinding.create(revision) for revision in inference_pair.endpoint_revisions
    )
    if classification.endpoints != expected_endpoints:
        raise ValueError("classification endpoints differ from exact inference evidence")


def _require_output_shard_matches_input(
    *,
    workload: ClassificationWorkload,
    output_shard: ClassificationOutputShard,
    input_shard: ClassificationInferenceShard,
) -> None:
    if (
        output_shard.workload_id != workload.workload_id
        or output_shard.workload_sha256 != workload.workload_sha256
        or output_shard.changed_claim_revision_id != input_shard.changed_claim_revision_id
        or output_shard.input_shard_id != input_shard.shard_id
        or output_shard.input_shard_sha256 != input_shard.shard_sha256
    ):
        raise ValueError("classification output shard binds a different exact input")
    input_by_pair = {pair.candidate.pair_id: pair for pair in input_shard.pairs}
    item_pair_ids = tuple(item.classification.candidate.pair_id for item in output_shard.items)
    if item_pair_ids != tuple(sorted(input_by_pair)):
        raise ValueError("output shard must classify each input pair exactly once")
    for item in output_shard.items:
        input_pair = input_by_pair[item.classification.candidate.pair_id]
        if item.input_pair_sha256 != input_pair.input_pair_sha256:
            raise ValueError("classification output item binds a different input pair")
        _require_classification_matches_inference_pair(item.classification, input_pair)


class ClassificationOutputShardRef(_StrictFrozenModel):
    """Compact immutable reference used by the result-index artifact."""

    changed_claim_revision_id: str = Field(pattern=r"^claimrev:[0-9a-f]{64}$")
    input_shard_id: str = Field(pattern=_SHARD_ID_PATTERN)
    input_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_id: str = Field(pattern=_OUTPUT_SHARD_ID_PATTERN)
    output_shard_sha256: str = Field(pattern=SHA256_PATTERN)
    output_shard_canonical_bytes: int = Field(
        gt=0,
        le=MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1,
    )
    pair_count: int = Field(gt=0, le=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1)

    @classmethod
    def create(cls, shard: ClassificationOutputShard) -> Self:
        return cls(
            changed_claim_revision_id=shard.changed_claim_revision_id,
            input_shard_id=shard.input_shard_id,
            input_shard_sha256=shard.input_shard_sha256,
            output_shard_id=shard.output_shard_id,
            output_shard_sha256=shard.output_shard_sha256,
            output_shard_canonical_bytes=len(shard.canonical_bytes()),
            pair_count=len(shard.items),
        )


def _result_index_payload(
    *,
    schema_version: int,
    workload_id: str,
    workload_sha256: str,
    source_candidate_set_sha256: str,
    classification_count: int,
    output_shards: tuple[ClassificationOutputShardRef, ...],
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.classification-result-index.v1",
        "schema_version": schema_version,
        "workload_id": workload_id,
        "workload_sha256": workload_sha256,
        "source_candidate_set_sha256": source_candidate_set_sha256,
        "classification_count": classification_count,
        "output_shards": [item.model_dump(mode="json") for item in output_shards],
    }


class ClassificationResultIndex(_StrictFrozenModel):
    """Compact content-addressed root for all classification output shards."""

    schema_version: Literal[1] = 1
    workload_id: str = Field(pattern=_WORKLOAD_ID_PATTERN)
    workload_sha256: str = Field(pattern=SHA256_PATTERN)
    source_candidate_set_sha256: str = Field(pattern=SHA256_PATTERN)
    classification_count: int = Field(ge=0, le=MAX_CLASSIFIER_WORKLOAD_PAIRS_V1)
    output_shards: tuple[ClassificationOutputShardRef, ...] = Field(max_length=MAX_CHANGED_ROOTS_V1)
    result_set_id: str = Field(pattern=_RESULT_SET_ID_PATTERN)
    result_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, Any]:
        return _result_index_payload(
            schema_version=self.schema_version,
            workload_id=self.workload_id,
            workload_sha256=self.workload_sha256,
            source_candidate_set_sha256=self.source_candidate_set_sha256,
            classification_count=self.classification_count,
            output_shards=self.output_shards,
        )

    def canonical_bytes(self) -> bytes:
        """Return the exact compact result-index artifact bytes."""

        return canonical_json_bytes(self._payload())

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        changed_ids = tuple(item.changed_claim_revision_id for item in self.output_shards)
        if changed_ids != tuple(sorted(set(changed_ids))):
            raise ValueError("result-index shard refs must use unique changed-root order")
        input_ids = tuple(item.input_shard_id for item in self.output_shards)
        output_ids = tuple(item.output_shard_id for item in self.output_shards)
        if len(input_ids) != len(set(input_ids)) or len(output_ids) != len(set(output_ids)):
            raise ValueError("result-index input/output shard refs must be unique")
        if sum(item.pair_count for item in self.output_shards) != self.classification_count:
            raise ValueError("result-index shard counts do not match classification count")
        payload = self._payload()
        payload_bytes = len(canonical_json_bytes(payload))
        if payload_bytes > MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1:
            raise ValueError("classification result index exceeds the managed-artifact ceiling")
        digest = _sha256(payload)
        if self.result_sha256 != digest or self.result_set_id != f"classresult:{digest}":
            raise ValueError("classification result-index ID/SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: ClassificationWorkload,
        output_shards: tuple[ClassificationOutputShard, ...],
    ) -> Self:
        workload = ClassificationWorkload.model_validate(workload.model_dump(mode="json"))
        output_shards = tuple(
            ClassificationOutputShard.model_validate(item.model_dump(mode="json"))
            for item in output_shards
        )
        expected_inputs = {
            shard.changed_claim_revision_id: shard for shard in workload.inference_shards
        }
        supplied_changed_ids = tuple(shard.changed_claim_revision_id for shard in output_shards)
        if len(supplied_changed_ids) != len(set(supplied_changed_ids)) or set(
            supplied_changed_ids
        ) != set(expected_inputs):
            raise ValueError("output shards must exactly cover workload input shards")
        for output_shard in output_shards:
            _require_output_shard_matches_input(
                workload=workload,
                output_shard=output_shard,
                input_shard=expected_inputs[output_shard.changed_claim_revision_id],
            )
        refs = tuple(
            ClassificationOutputShardRef.create(shard)
            for shard in sorted(
                output_shards,
                key=lambda item: item.changed_claim_revision_id,
            )
        )
        payload = _result_index_payload(
            schema_version=1,
            workload_id=workload.workload_id,
            workload_sha256=workload.workload_sha256,
            source_candidate_set_sha256=workload.source_candidate_set_sha256,
            classification_count=sum(item.pair_count for item in refs),
            output_shards=refs,
        )
        payload_bytes = len(canonical_json_bytes(payload))
        if payload_bytes > MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1:
            raise ClassificationLimitError(
                limit=MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1,
                observed=payload_bytes,
                category="result-index-bytes",
            )
        digest = _sha256(payload)
        return cls(
            workload_id=workload.workload_id,
            workload_sha256=workload.workload_sha256,
            source_candidate_set_sha256=workload.source_candidate_set_sha256,
            classification_count=sum(item.pair_count for item in refs),
            output_shards=refs,
            result_set_id=f"classresult:{digest}",
            result_sha256=digest,
        )


class ClassificationResultSet(_StrictFrozenModel):
    """In-memory validation envelope for one compact index and its output shards.

    The envelope deliberately has no content identity.  The managed inference
    output artifacts are the individually bounded ``output_shards`` and the
    compact ``result_index``; future services bind ``result_index.result_sha256``.
    """

    schema_version: Literal[1] = 1
    workload: ClassificationWorkload
    result_index: ClassificationResultIndex
    output_shards: tuple[ClassificationOutputShard, ...] = Field(max_length=MAX_CHANGED_ROOTS_V1)

    @property
    def result_set_id(self) -> str:
        return self.result_index.result_set_id

    @property
    def result_sha256(self) -> str:
        return self.result_index.result_sha256

    @property
    def classifications(self) -> tuple[ClaimPairClassification, ...]:
        return tuple(
            sorted(
                (item.classification for shard in self.output_shards for item in shard.items),
                key=lambda item: item.candidate.pair_id,
            )
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.result_index.workload_id != self.workload.workload_id
            or self.result_index.workload_sha256 != self.workload.workload_sha256
            or self.result_index.source_candidate_set_sha256
            != self.workload.source_candidate_set_sha256
        ):
            raise ValueError("classification result index binds a different workload")
        changed_ids = tuple(item.changed_claim_revision_id for item in self.output_shards)
        if changed_ids != tuple(sorted(set(changed_ids))):
            raise ValueError("classification output shards must use unique changed-root order")
        expected_inputs = {
            shard.changed_claim_revision_id: shard for shard in self.workload.inference_shards
        }
        if set(changed_ids) != set(expected_inputs):
            raise ValueError("output shards must exactly cover workload input shards")
        refs_by_changed = {
            ref.changed_claim_revision_id: ref for ref in self.result_index.output_shards
        }
        if set(refs_by_changed) != set(expected_inputs):
            raise ValueError("result index must exactly cover workload input shards")
        all_pair_ids: list[str] = []
        for output_shard in self.output_shards:
            input_shard = expected_inputs[output_shard.changed_claim_revision_id]
            _require_output_shard_matches_input(
                workload=self.workload,
                output_shard=output_shard,
                input_shard=input_shard,
            )
            item_pair_ids = tuple(
                item.classification.candidate.pair_id for item in output_shard.items
            )
            expected_ref = ClassificationOutputShardRef.create(output_shard)
            if refs_by_changed[output_shard.changed_claim_revision_id] != expected_ref:
                raise ValueError("classification result index contains a substituted shard ref")
            all_pair_ids.extend(item_pair_ids)
        selected_ids = tuple(sorted(item.pair_id for item in self.workload.selected))
        if tuple(sorted(all_pair_ids)) != selected_ids or len(all_pair_ids) != len(
            set(all_pair_ids)
        ):
            raise ValueError("classification coverage must exactly equal selected workload pairs")
        expected_index = ClassificationResultIndex.create(
            workload=self.workload,
            output_shards=self.output_shards,
        )
        if self.result_index != expected_index:
            raise ValueError("classification result index is not the exact canonical index")
        return self

    @classmethod
    def create(
        cls,
        *,
        workload: ClassificationWorkload,
        classifications: tuple[ClaimPairClassification, ...],
    ) -> Self:
        workload = ClassificationWorkload.model_validate(workload.model_dump(mode="json"))
        canonical = tuple(
            sorted(
                (
                    ClaimPairClassification.model_validate(item.model_dump(mode="json"))
                    for item in classifications
                ),
                key=lambda item: item.candidate.pair_id,
            )
        )
        pair_ids = tuple(item.candidate.pair_id for item in canonical)
        selected_ids = tuple(sorted(item.pair_id for item in workload.selected))
        if pair_ids != selected_ids or len(pair_ids) != len(set(pair_ids)):
            raise ValueError("classification coverage must exactly equal selected workload pairs")
        classification_by_pair = {item.candidate.pair_id: item for item in canonical}
        output_shards = tuple(
            ClassificationOutputShard.create(
                workload=workload,
                input_shard=input_shard,
                classifications=tuple(
                    classification_by_pair[pair.candidate.pair_id] for pair in input_shard.pairs
                ),
            )
            for input_shard in workload.inference_shards
        )
        result_index = ClassificationResultIndex.create(
            workload=workload,
            output_shards=output_shards,
        )
        return cls(
            workload=workload,
            result_index=result_index,
            output_shards=output_shards,
        )


def validate_classification_results(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    results: ClassificationResultSet,
) -> ClassificationResultSet:
    """Reopen all snapshot, workload, candidate, pair, and endpoint bindings."""

    validated = ClassificationResultSet.model_validate(results.model_dump(mode="json"))
    workload = validate_classification_workload(
        snapshot,
        candidates=candidates,
        workload=validated.workload,
    )
    if workload != validated.workload:
        raise ValueError("classification result set binds a different workload")
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    revision_by_id = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    for classification in validated.classifications:
        expected_candidate = candidate_by_id.get(classification.candidate.pair_id)
        if expected_candidate is None or classification.candidate != expected_candidate:
            raise ValueError("classification does not bind the authoritative candidate")
        for endpoint in classification.endpoints:
            expected_revision = revision_by_id.get(endpoint.claim_revision.claim_revision_id)
            if expected_revision is None or endpoint.claim_revision != expected_revision:
                raise ValueError("classification endpoint differs from the authoritative snapshot")
    return validated


def materialize_relation_assessments(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    results: ClassificationResultSet,
) -> tuple[RelationAssessment, ...]:
    """Return every model-valid assessment; advisory-only results remain outside the graph."""

    validated = validate_classification_results(
        snapshot,
        candidates=candidates,
        results=results,
    )
    assessments = tuple(
        item.relation_assessment
        for item in validated.classifications
        if item.materialization_status
        in {GraphMaterializationStatus.GRAPH_VALID, GraphMaterializationStatus.NO_EDGE}
        and item.relation_assessment is not None
    )
    return tuple(sorted(assessments, key=lambda item: item.pair.pair_id))


__all__ = [
    "MAX_CLASSIFICATION_LEDGER_ENTRY_BYTES_V1",
    "MAX_CLASSIFICATION_RATIONALE_CHARS_V1",
    "MAX_CLASSIFICATION_RATIONALE_UTF8_BYTES_V1",
    "MAX_CLASSIFICATION_RESULT_INDEX_CANONICAL_BYTES_V1",
    "MAX_CLASSIFICATION_WORKLOAD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_INFERENCE_SHARD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_OUTPUT_SHARD_CANONICAL_BYTES_V1",
    "MAX_CLASSIFIER_WORKLOAD_PAIRS_V1",
    "MAX_PAIR_CLASSIFICATION_CANONICAL_BYTES_V1",
    "CandidateExclusionReason",
    "CandidateSelectionReason",
    "ClaimPairClassification",
    "ClassificationLimitError",
    "ClassificationInferencePair",
    "ClassificationInferenceShard",
    "ClassificationResultSet",
    "ClassificationResultIndex",
    "ClassificationOutputItem",
    "ClassificationOutputShard",
    "ClassificationOutputShardRef",
    "ClassificationWorkload",
    "ClassifierSelectorVersion",
    "EndpointEvidenceBinding",
    "ExcludedCandidateRef",
    "GraphMaterializationStatus",
    "SelectedCandidateRef",
    "materialize_relation_assessments",
    "relationship_candidate_sha256",
    "select_classification_workload",
    "validate_classification_results",
    "validate_classification_workload",
]
