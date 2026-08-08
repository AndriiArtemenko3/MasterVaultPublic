"""Pure advisory discovery over one immutable change-control snapshot.

The functions in this module do not classify relationships, assert that a
document is affected, or mutate authoritative state.  They deterministically
surface unassessed claim pairs and current downstream documents that deserve
attention because canonical graph facts lead to them.
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from collections.abc import Mapping
from datetime import date
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.models import (
    ChangeControlAggregate,
    ComparableClaimPair,
    DependencyAssessment,
    DependencyKind,
    DocumentSpanReference,
    PersistedRelationType,
    TemporalResolution,
    TemporalResolutionContext,
    TemporalState,
    TemporalTargetKind,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from mastervault.change_control.store import ChangeControlSnapshot

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PAIR_ID_PATTERN = r"^pair:[0-9a-f]{64}$"
_CLAIM_ID_PATTERN = r"^claim:[0-9a-f]{64}$"
_CLAIM_REVISION_ID_PATTERN = r"^claimrev:[0-9a-f]{64}$"
_DOCUMENT_VERSION_ID_PATTERN = r"^docv:[0-9a-f]{64}$"
_RELATION_ID_PATTERN = r"^rel:[0-9a-f]{64}$"
_DEPENDENCY_ID_PATTERN = r"^dep:[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisAlgorithm(StrEnum):
    RELATIONSHIP_CANDIDATES_V1 = "relationship-candidates-v1"
    DOCUMENT_ATTENTION_RANKING_V1 = "document-attention-ranking-v1"


class AnalysisMode(StrEnum):
    LIVE = "live"
    PREVIEW = "preview"


class CandidateStatus(StrEnum):
    UNASSESSED = "unassessed"


class TemporalOverlap(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class AttentionAnchorKind(StrEnum):
    CHANGED_ROOT = "changed-root"
    CANONICAL_RELATION = "canonical-relation"
    UNASSESSED_CANDIDATE = "unassessed-candidate"


class RelationTraversalDirection(StrEnum):
    FORWARD = "forward"
    REVERSE = "reverse"
    SYMMETRIC = "symmetric"


class AttentionExclusionReason(StrEnum):
    CHANGED_DOCUMENT = "changed-document"
    FUTURE = "future"
    HISTORICAL = "historical"
    EXPIRED = "expired"
    UNRESOLVED = "unresolved"
    HISTORICAL_REFERENCE_ONLY = "historical-reference-only"


_TEMPORAL_RANK = {
    TemporalState.CURRENT: 0,
    TemporalState.FUTURE: 1,
    TemporalState.HISTORICAL: 2,
    TemporalState.EXPIRED: 3,
    TemporalState.UNRESOLVED: 4,
}
_TEMPORAL_OVERLAP_RANK = {
    TemporalOverlap.TRUE: 0,
    TemporalOverlap.FALSE: 1,
    TemporalOverlap.UNKNOWN: 2,
}
_ANCHOR_KIND_RANK = {
    AttentionAnchorKind.CHANGED_ROOT: 0,
    AttentionAnchorKind.CANONICAL_RELATION: 0,
    AttentionAnchorKind.UNASSESSED_CANDIDATE: 1,
}
_DEPENDENCY_KIND_BUCKET = {
    DependencyKind.QUOTES: 0,
    DependencyKind.IMPLEMENTS: 0,
    DependencyKind.SUMMARIZES: 1,
    DependencyKind.HISTORICAL_REFERENCE: 2,
}
_EXCLUSION_ORDER = {
    AttentionExclusionReason.CHANGED_DOCUMENT: 0,
    AttentionExclusionReason.FUTURE: 1,
    AttentionExclusionReason.HISTORICAL: 2,
    AttentionExclusionReason.EXPIRED: 3,
    AttentionExclusionReason.UNRESOLVED: 4,
    AttentionExclusionReason.HISTORICAL_REFERENCE_ONLY: 5,
}

MAX_CHANGED_ROOTS_V1 = 64
MAX_RELATIONSHIP_CANDIDATES_V1 = 20_000
MAX_RELATION_FACTS_V1 = 20_000
MAX_DEPENDENCY_FACTS_V1 = 20_000
MAX_RELATION_HOPS_V1 = 16
MAX_ANCHORS_V1 = 20_000
MAX_DEPENDENCY_DEPTH_V1 = 8
MAX_PATHS_PER_DOCUMENT_V1 = 128
MAX_TOTAL_GENERATED_PATHS_V1 = 4_096
MAX_SPANS_PER_DEPENDENCY_V1 = 64
MAX_SPAN_CANONICAL_BYTES_V1 = 16_384
MAX_DEPENDENCY_PROJECTION_BYTES_V1 = 131_072
MAX_TOTAL_UNIQUE_DEPENDENCY_PROJECTION_BYTES_V1 = 1_048_576
MAX_RELATIONSHIP_CANDIDATE_BYTES_V1 = 16_384
MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1 = 2_097_152
MAX_ATTENTION_PATH_BYTES_V1 = 262_144
MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1 = 1_048_576
MAX_ATTENTION_TARGET_RECORD_BYTES_V1 = 393_216
MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1 = 1_179_648
MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1 = 2_359_296
MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1 = 1_310_720


class DiscoveryLimitError(RuntimeError):
    """A fixed versioned discovery limit was exceeded; no partial result exists."""

    def __init__(self, *, category: str, limit: int, observed: int) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        super().__init__(f"discovery limit exceeded: {category}={observed} > {limit}")


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _canonical_projection_charge(payload: Any) -> int:
    """Canonical work/output bytes for one fixed v1 projection envelope.

    This deterministic charge includes UTF-8 JSON syntax, escaping, and all
    nested payloads. It is an output/work budget, not a Python heap estimate.
    """

    return len(
        canonical_json_bytes(
            {
                "namespace": "mastervault.discovery-projection-charge.v1",
                "projection": payload,
            }
        )
    )


def _enforce_byte_limit(
    *,
    category: str,
    limit: int,
    observed: int,
) -> None:
    if observed > limit:
        raise DiscoveryLimitError(category=category, limit=limit, observed=observed)


def _projection_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class _RawProjectionShapeError(TypeError):
    """A raw projection cannot be normalized without consuming arbitrary objects."""


def _json_compatible_raw_projection(
    value: Any,
    *,
    depth: int = 0,
    active_ids: set[int] | None = None,
) -> Any:
    """Normalize bounded Python-mode model data without consuming nested iterables."""

    if depth > 32:
        raise _RawProjectionShapeError("raw projection nesting is too deep")
    if isinstance(value, BaseModel):
        return _json_compatible_raw_projection(
            value.model_dump(),
            depth=depth + 1,
            active_ids=active_ids,
        )
    if isinstance(value, Enum):
        return _json_compatible_raw_projection(
            value.value,
            depth=depth + 1,
            active_ids=active_ids,
        )
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as error:
            raise _RawProjectionShapeError("raw projection bytes must be UTF-8") from error
    if not isinstance(value, (dict, list, tuple)):
        raise _RawProjectionShapeError(f"unsupported raw projection value: {type(value).__name__}")

    if active_ids is None:
        active_ids = set()
    value_id = id(value)
    if value_id in active_ids:
        raise _RawProjectionShapeError("raw projections must not contain cycles")
    active_ids.add(value_id)
    try:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise _RawProjectionShapeError("raw projection mapping keys must be strings")
            return {
                key: _json_compatible_raw_projection(
                    item,
                    depth=depth + 1,
                    active_ids=active_ids,
                )
                for key, item in value.items()
            }
        return [
            _json_compatible_raw_projection(
                item,
                depth=depth + 1,
                active_ids=active_ids,
            )
            for item in value
        ]
    finally:
        active_ids.remove(value_id)


def _target_paths(target: Any) -> Any:
    if isinstance(target, BaseModel):
        typed_target: Any = target
        return typed_target.paths
    return target["paths"]


def _path_dependency_steps(path: Any) -> Any:
    if isinstance(path, BaseModel):
        typed_path: Any = path
        return typed_path.dependency_steps
    return path["dependency_steps"]


def _iter_attention_targets(
    attention_candidates: Any,
    excluded_targets: Any,
) -> Any:
    yield from attention_candidates
    yield from excluded_targets


def _iter_attention_paths(
    attention_candidates: Any,
    excluded_targets: Any,
) -> Any:
    for target in _iter_attention_targets(attention_candidates, excluded_targets):
        yield from _target_paths(target)


def _enforce_retained_path_count(
    attention_candidates: Any,
    excluded_targets: Any,
) -> None:
    for observed, _ in enumerate(
        _iter_attention_paths(attention_candidates, excluded_targets),
        start=1,
    ):
        if observed > MAX_TOTAL_GENERATED_PATHS_V1:
            raise DiscoveryLimitError(
                category="total-retained-paths",
                limit=MAX_TOTAL_GENERATED_PATHS_V1,
                observed=observed,
            )


def _enforce_attention_target_record_budget(target: Any) -> int:
    charge = _canonical_projection_charge(_projection_payload(target))
    _enforce_byte_limit(
        category="attention-target-record-bytes",
        limit=MAX_ATTENTION_TARGET_RECORD_BYTES_V1,
        observed=charge,
    )
    return charge


def _enforce_attention_target_budgets(
    attention_candidates: Any,
    excluded_targets: Any,
) -> None:
    cumulative_bytes = 0
    for target in _iter_attention_targets(attention_candidates, excluded_targets):
        cumulative_bytes += _enforce_attention_target_record_budget(target)
        _enforce_byte_limit(
            category="total-attention-target-record-bytes",
            limit=MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1,
            observed=cumulative_bytes,
        )


def _enforce_attention_projection_budgets(
    attention_candidates: Any,
    excluded_targets: Any,
) -> None:
    path_count = 0
    cumulative_path_bytes = 0
    cumulative_unique_dependency_bytes = 0
    dependency_projection_by_id: dict[str, bytes] = {}
    for path in _iter_attention_paths(attention_candidates, excluded_targets):
        path_count += 1
        if path_count > MAX_TOTAL_GENERATED_PATHS_V1:
            raise DiscoveryLimitError(
                category="total-retained-paths",
                limit=MAX_TOTAL_GENERATED_PATHS_V1,
                observed=path_count,
            )
        path_payload = _projection_payload(path)
        path_bytes = _canonical_projection_charge(path_payload)
        _enforce_byte_limit(
            category="attention-path-bytes",
            limit=MAX_ATTENTION_PATH_BYTES_V1,
            observed=path_bytes,
        )
        cumulative_path_bytes += path_bytes
        _enforce_byte_limit(
            category="total-attention-path-bytes",
            limit=MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1,
            observed=cumulative_path_bytes,
        )
        for step in _path_dependency_steps(path):
            dependency_payload = _projection_payload(step)
            dependency_bytes = _canonical_projection_charge(dependency_payload)
            _enforce_byte_limit(
                category="dependency-projection-bytes",
                limit=MAX_DEPENDENCY_PROJECTION_BYTES_V1,
                observed=dependency_bytes,
            )
            projection = canonical_json_bytes(dependency_payload)
            typed_step: Any = step
            dependency_id = (
                typed_step.dependency_id if isinstance(step, BaseModel) else step["dependency_id"]
            )
            previous = dependency_projection_by_id.get(dependency_id)
            if previous is not None:
                if previous != projection:
                    raise ValueError("one dependency ID has conflicting attention-path projections")
                continue
            cumulative_unique_dependency_bytes += dependency_bytes
            _enforce_byte_limit(
                category="total-unique-dependency-projection-bytes",
                limit=MAX_TOTAL_UNIQUE_DEPENDENCY_PROJECTION_BYTES_V1,
                observed=cumulative_unique_dependency_bytes,
            )
            dependency_projection_by_id[dependency_id] = projection


class AnalysisBinding(_StrictFrozenModel):
    """Exact immutable input binding for one advisory analysis result."""

    schema_version: Literal[1] = 1
    algorithm_version: AnalysisAlgorithm
    aggregate_id: str
    snapshot_revision: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    as_of: date
    changed_claim_revision_ids: tuple[str, ...] = Field(min_length=1)
    changed_temporal_resolutions: tuple[TemporalResolution, ...] = Field(min_length=1)
    mode: AnalysisMode
    source_candidate_set_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    canonical_input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("changed_claim_revision_ids")
    @classmethod
    def _canonical_changed_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("changed claim revision IDs must be sorted and unique")
        if len(values) > MAX_CHANGED_ROOTS_V1:
            raise DiscoveryLimitError(
                category="changed-roots",
                limit=MAX_CHANGED_ROOTS_V1,
                observed=len(values),
            )
        return values

    def _input_payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.advisory-analysis-input.v1",
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version.value,
            "aggregate_id": self.aggregate_id,
            "snapshot_revision": self.snapshot_revision,
            "aggregate_sha256": self.aggregate_sha256,
            "as_of": self.as_of.isoformat(),
            "changed_claim_revision_ids": list(self.changed_claim_revision_ids),
            "changed_temporal_resolutions": [
                resolution.model_dump(mode="json")
                for resolution in self.changed_temporal_resolutions
            ],
            "mode": self.mode.value,
            "source_candidate_set_sha256": self.source_candidate_set_sha256,
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.changed_temporal_resolutions) != len(self.changed_claim_revision_ids):
            raise ValueError("changed temporal resolutions must align with changed claim IDs")
        for claim_id, resolution in zip(
            self.changed_claim_revision_ids,
            self.changed_temporal_resolutions,
            strict=True,
        ):
            if (
                resolution.target.kind != TemporalTargetKind.CLAIM_REVISION
                or resolution.target.target_id != claim_id
                or resolution.as_of != self.as_of
            ):
                raise ValueError("changed temporal resolution is bound to the wrong claim or date")
        if any(
            resolution.state not in {TemporalState.CURRENT, TemporalState.FUTURE}
            for resolution in self.changed_temporal_resolutions
        ):
            raise ValueError("changed roots must be current or future")
        expected_mode = (
            AnalysisMode.PREVIEW
            if any(
                resolution.state == TemporalState.FUTURE
                for resolution in self.changed_temporal_resolutions
            )
            else AnalysisMode.LIVE
        )
        if self.mode != expected_mode:
            raise ValueError("analysis mode does not match changed-root temporality")
        if (self.algorithm_version == AnalysisAlgorithm.RELATIONSHIP_CANDIDATES_V1) != (
            self.source_candidate_set_sha256 is None
        ):
            raise ValueError("only document-attention ranking binds a source candidate-set SHA")
        if self.canonical_input_sha256 != _canonical_sha256(self._input_payload()):
            raise ValueError("canonical_input_sha256 does not match analysis inputs")
        return self

    @classmethod
    def create(
        cls,
        *,
        algorithm_version: AnalysisAlgorithm,
        aggregate_id: str,
        snapshot_revision: int,
        aggregate_sha256: str,
        as_of: date,
        changed_claim_revision_ids: tuple[str, ...],
        changed_temporal_resolutions: tuple[TemporalResolution, ...],
        source_candidate_set_sha256: str | None = None,
    ) -> Self:
        mode = (
            AnalysisMode.PREVIEW
            if any(
                resolution.state == TemporalState.FUTURE
                for resolution in changed_temporal_resolutions
            )
            else AnalysisMode.LIVE
        )
        data: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": algorithm_version,
            "aggregate_id": aggregate_id,
            "snapshot_revision": snapshot_revision,
            "aggregate_sha256": aggregate_sha256,
            "as_of": as_of,
            "changed_claim_revision_ids": changed_claim_revision_ids,
            "changed_temporal_resolutions": changed_temporal_resolutions,
            "mode": mode,
            "source_candidate_set_sha256": source_candidate_set_sha256,
        }
        payload = {
            "namespace": "mastervault.advisory-analysis-input.v1",
            "schema_version": 1,
            "algorithm_version": algorithm_version.value,
            "aggregate_id": aggregate_id,
            "snapshot_revision": snapshot_revision,
            "aggregate_sha256": aggregate_sha256,
            "as_of": as_of.isoformat(),
            "changed_claim_revision_ids": list(changed_claim_revision_ids),
            "changed_temporal_resolutions": [
                resolution.model_dump(mode="json") for resolution in changed_temporal_resolutions
            ],
            "mode": mode.value,
            "source_candidate_set_sha256": source_candidate_set_sha256,
        }
        return cls(**data, canonical_input_sha256=_canonical_sha256(payload))


class CandidateScoreV1(_StrictFrozenModel):
    """Integer-only, lexicographic relationship-candidate evidence."""

    schema_version: Literal[1] = 1
    same_claim_identity: int = Field(ge=0, le=1)
    same_document_family: int = Field(ge=0, le=1)
    shared_scope_count: int = Field(ge=0)
    temporal_overlap: TemporalOverlap
    incumbent_temporal_rank: int = Field(ge=0, le=4)


class RelationshipCandidate(_StrictFrozenModel):
    pair_id: str = Field(pattern=_PAIR_ID_PATTERN)
    claim_revision_ids: tuple[str, str]
    changed_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    incumbent_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    changed_claim_identity_id: str = Field(pattern=_CLAIM_ID_PATTERN)
    incumbent_claim_identity_id: str = Field(pattern=_CLAIM_ID_PATTERN)
    changed_document_family: str
    incumbent_document_family: str
    shared_scopes: tuple[str, ...] = ()
    changed_temporal_resolution: TemporalResolution
    incumbent_temporal_resolution: TemporalResolution
    status: Literal[CandidateStatus.UNASSESSED] = CandidateStatus.UNASSESSED
    score: CandidateScoreV1

    @model_validator(mode="after")
    def _pair_integrity(self) -> Self:
        if self.claim_revision_ids != tuple(sorted(set(self.claim_revision_ids))):
            raise ValueError("candidate pair endpoints must be two sorted unique IDs")
        if set(self.claim_revision_ids) != {
            self.changed_claim_revision_id,
            self.incumbent_claim_revision_id,
        }:
            raise ValueError("candidate roles must exactly cover pair endpoints")
        expected_pair_id = "pair:" + _canonical_sha256(
            {
                "namespace": "mastervault.claim-pair.v1",
                "claim_revision_ids": list(self.claim_revision_ids),
            }
        )
        if self.pair_id != expected_pair_id:
            raise ValueError("pair_id does not match candidate endpoints")
        if self.shared_scopes != tuple(sorted(set(self.shared_scopes))):
            raise ValueError("shared scopes must be sorted and unique")
        if self.score.shared_scope_count != len(self.shared_scopes):
            raise ValueError("shared-scope score does not match candidate scopes")
        if self.score.same_claim_identity != int(
            self.changed_claim_identity_id == self.incumbent_claim_identity_id
        ):
            raise ValueError("same-identity score does not match candidate identities")
        if self.score.same_document_family != int(
            self.changed_document_family == self.incumbent_document_family
        ):
            raise ValueError("same-family score does not match candidate families")
        for claim_id, resolution in (
            (self.changed_claim_revision_id, self.changed_temporal_resolution),
            (self.incumbent_claim_revision_id, self.incumbent_temporal_resolution),
        ):
            if (
                resolution.target.kind != TemporalTargetKind.CLAIM_REVISION
                or resolution.target.target_id != claim_id
            ):
                raise ValueError("candidate temporal resolution is bound to the wrong claim")
        if self.changed_temporal_resolution.as_of != self.incumbent_temporal_resolution.as_of:
            raise ValueError("candidate temporal resolutions must use one analysis date")
        if self.score.temporal_overlap != _intervals_overlap(
            self.changed_temporal_resolution,
            self.incumbent_temporal_resolution,
        ):
            raise ValueError("temporal-overlap score does not match candidate resolutions")
        if (
            self.score.incumbent_temporal_rank
            != _TEMPORAL_RANK[self.incumbent_temporal_resolution.state]
        ):
            raise ValueError("incumbent temporal rank does not match its resolution")
        _enforce_byte_limit(
            category="relationship-candidate-bytes",
            limit=MAX_RELATIONSHIP_CANDIDATE_BYTES_V1,
            observed=_canonical_projection_charge(self.model_dump(mode="json")),
        )
        return self


def _relationship_candidate_sort_key(candidate: RelationshipCandidate) -> tuple[Any, ...]:
    score = candidate.score
    return (
        -score.same_claim_identity,
        -score.same_document_family,
        -score.shared_scope_count,
        _TEMPORAL_OVERLAP_RANK[score.temporal_overlap],
        score.incumbent_temporal_rank,
        candidate.pair_id,
    )


def _enforce_relationship_candidate_projection_budgets(
    candidates: Any,
) -> None:
    _enforce_relationship_candidate_count(len(candidates))
    cumulative_bytes = 0
    for candidate in candidates:
        cumulative_bytes = _enforce_relationship_candidate_projection_budget(
            _projection_payload(candidate),
            cumulative_bytes=cumulative_bytes,
        )


def _enforce_relationship_candidate_count(observed: int) -> None:
    if observed > MAX_RELATIONSHIP_CANDIDATES_V1:
        raise DiscoveryLimitError(
            category="relationship-candidates",
            limit=MAX_RELATIONSHIP_CANDIDATES_V1,
            observed=observed,
        )


def _enforce_relationship_candidate_projection_budget(
    candidate_payload: Any,
    *,
    cumulative_bytes: int,
) -> int:
    candidate_bytes = _canonical_projection_charge(candidate_payload)
    _enforce_byte_limit(
        category="relationship-candidate-bytes",
        limit=MAX_RELATIONSHIP_CANDIDATE_BYTES_V1,
        observed=candidate_bytes,
    )
    cumulative_bytes += candidate_bytes
    _enforce_byte_limit(
        category="total-relationship-candidate-bytes",
        limit=MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1,
        observed=cumulative_bytes,
    )
    return cumulative_bytes


def _preflight_raw_relationship_candidate_projections(
    candidates: list[Any] | tuple[Any, ...],
) -> tuple[Any, ...]:
    _enforce_relationship_candidate_count(len(candidates))
    cumulative_bytes = 0
    projections: list[Any] = []
    malformed_projection = False
    for candidate in candidates:
        try:
            projection = _json_compatible_raw_projection(candidate)
        except _RawProjectionShapeError:
            # Keep scanning this already-bounded top-level collection so one
            # malformed item cannot disable limits on later candidates.
            malformed_projection = True
            continue
        cumulative_bytes = _enforce_relationship_candidate_projection_budget(
            projection,
            cumulative_bytes=cumulative_bytes,
        )
        projections.append(projection)
    if malformed_projection:
        raise ValueError(
            "relationship candidate inputs must contain bounded JSON-compatible values"
        )
    return tuple(projections)


def _relationship_candidate_set_payload(
    *,
    schema_version: Any,
    binding: Any,
    candidates: Any,
) -> dict[str, Any]:
    return {
        "namespace": "mastervault.relationship-candidate-set.v1",
        "schema_version": schema_version,
        "binding": _projection_payload(binding),
        "candidates": [_projection_payload(candidate) for candidate in candidates],
    }


def _enforce_relationship_candidate_set_payload_budget(payload: Any) -> None:
    _enforce_byte_limit(
        category="candidate-set-payload-bytes",
        limit=MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1,
        observed=len(canonical_json_bytes(payload)),
    )


class RelationshipCandidateSet(_StrictFrozenModel):
    """Canonical unassessed pair inventory, never a classification result."""

    schema_version: Literal[1] = 1
    binding: AnalysisBinding
    candidates: tuple[RelationshipCandidate, ...]
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def _serialized_prefix_budgets(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        if "candidates" not in data:
            return data
        candidates = data["candidates"]
        if not isinstance(candidates, (list, tuple)):
            raise ValueError("relationship candidates must be provided as a list or tuple")
        candidate_projections = _preflight_raw_relationship_candidate_projections(candidates)
        if "binding" not in data:
            return data
        try:
            schema_version = _json_compatible_raw_projection(data.get("schema_version", 1))
            binding = _json_compatible_raw_projection(data["binding"])
        except _RawProjectionShapeError as error:
            raise ValueError(
                "relationship candidate-set inputs must contain JSON-compatible values"
            ) from error
        payload = _relationship_candidate_set_payload(
            schema_version=schema_version,
            binding=binding,
            candidates=candidate_projections,
        )
        _enforce_relationship_candidate_set_payload_budget(payload)
        return data

    def _result_payload(self) -> dict[str, Any]:
        return _relationship_candidate_set_payload(
            schema_version=self.schema_version,
            binding=self.binding,
            candidates=self.candidates,
        )

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.binding.algorithm_version != AnalysisAlgorithm.RELATIONSHIP_CANDIDATES_V1:
            raise ValueError("relationship candidate set has the wrong algorithm binding")
        pair_ids = [candidate.pair_id for candidate in self.candidates]
        _enforce_relationship_candidate_projection_budgets(self.candidates)
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("relationship candidate pair IDs must be unique")
        if self.candidates != tuple(sorted(self.candidates, key=_relationship_candidate_sort_key)):
            raise ValueError("relationship candidates must use canonical score order")
        changed_ids = set(self.binding.changed_claim_revision_ids)
        if any(
            candidate.changed_claim_revision_id not in changed_ids for candidate in self.candidates
        ):
            raise ValueError("candidate changed endpoint is absent from its analysis binding")
        changed_resolutions = dict(
            zip(
                self.binding.changed_claim_revision_ids,
                self.binding.changed_temporal_resolutions,
                strict=True,
            )
        )
        if any(
            candidate.changed_temporal_resolution
            != changed_resolutions[candidate.changed_claim_revision_id]
            or candidate.incumbent_temporal_resolution.as_of != self.binding.as_of
            for candidate in self.candidates
        ):
            raise ValueError("candidate temporal resolutions differ from their analysis binding")
        payload = self._result_payload()
        _enforce_relationship_candidate_set_payload_budget(payload)
        if self.result_sha256 != _canonical_sha256(payload):
            raise ValueError("relationship candidate result SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: AnalysisBinding,
        candidates: tuple[RelationshipCandidate, ...],
    ) -> Self:
        _enforce_relationship_candidate_projection_budgets(candidates)
        canonical = tuple(sorted(candidates, key=_relationship_candidate_sort_key))
        payload = _relationship_candidate_set_payload(
            schema_version=1,
            binding=binding,
            candidates=canonical,
        )
        _enforce_relationship_candidate_set_payload_budget(payload)
        return cls(binding=binding, candidates=canonical, result_sha256=_canonical_sha256(payload))


class RelationPathStep(_StrictFrozenModel):
    relation_id: str = Field(pattern=_RELATION_ID_PATTERN)
    relation_type: PersistedRelationType
    canonical_endpoint_ids: tuple[str, str]
    traversed_from_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    traversed_to_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    traversal_direction: RelationTraversalDirection

    @model_validator(mode="after")
    def _semantics(self) -> Self:
        if self.relation_type not in {
            PersistedRelationType.SUPERSEDES,
            PersistedRelationType.CONTRADICTS,
        }:
            raise ValueError("discovery relation paths support only canonical claim edges")
        if set(self.canonical_endpoint_ids) != {
            self.traversed_from_claim_revision_id,
            self.traversed_to_claim_revision_id,
        }:
            raise ValueError("relation traversal must use its canonical endpoints")
        if len(set(self.canonical_endpoint_ids)) != 2 or any(
            re.fullmatch(_CLAIM_REVISION_ID_PATTERN, endpoint_id) is None
            for endpoint_id in self.canonical_endpoint_ids
        ):
            raise ValueError("canonical relation endpoints must be two distinct claim revisions")
        if self.relation_type == PersistedRelationType.CONTRADICTS:
            if self.canonical_endpoint_ids != tuple(sorted(self.canonical_endpoint_ids)):
                raise ValueError("CONTRADICTS endpoints must be canonical")
            if self.traversal_direction != RelationTraversalDirection.SYMMETRIC:
                raise ValueError("CONTRADICTS traversal must remain symmetric")
        else:
            expected = (
                RelationTraversalDirection.FORWARD
                if self.traversed_from_claim_revision_id == self.canonical_endpoint_ids[0]
                else RelationTraversalDirection.REVERSE
            )
            if self.traversal_direction != expected:
                raise ValueError("SUPERSEDES traversal direction is inconsistent")
        expected_relation_id = "rel:" + _canonical_sha256(
            {
                "namespace": "mastervault.relation.v1",
                "relation_type": self.relation_type.value,
                "endpoint_ids": list(self.canonical_endpoint_ids),
            }
        )
        if self.relation_id != expected_relation_id:
            raise ValueError("relation_id does not match canonical relation semantics")
        return self


class DependencyPathStep(_StrictFrozenModel):
    dependency_id: str = Field(pattern=_DEPENDENCY_ID_PATTERN)
    dependency_kind: DependencyKind
    upstream_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    downstream_document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID_PATTERN)
    downstream_spans: tuple[DocumentSpanReference, ...] = Field(min_length=1)
    exposed_downstream_claim_revision_ids: tuple[str, ...] = ()

    @field_validator("exposed_downstream_claim_revision_ids")
    @classmethod
    def _canonical_exposed_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("exposed downstream claim IDs must be sorted and unique")
        if any(re.fullmatch(_CLAIM_REVISION_ID_PATTERN, value) is None for value in values):
            raise ValueError("exposed downstream claim IDs must be claim-revision IDs")
        return values

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.downstream_spans) > MAX_SPANS_PER_DEPENDENCY_V1:
            raise DiscoveryLimitError(
                category="spans-per-dependency",
                limit=MAX_SPANS_PER_DEPENDENCY_V1,
                observed=len(self.downstream_spans),
            )
        span_keys: list[bytes] = []
        for span in self.downstream_spans:
            span_bytes = canonical_json_bytes(span.model_dump(mode="json"))
            _enforce_byte_limit(
                category="span-canonical-bytes",
                limit=MAX_SPAN_CANONICAL_BYTES_V1,
                observed=len(span_bytes),
            )
            span_keys.append(span_bytes)
        if span_keys != sorted(set(span_keys)):
            raise ValueError("downstream spans must be canonical, sorted, and unique")
        if any(
            span.document_version_id != self.downstream_document_version_id
            for span in self.downstream_spans
        ):
            raise ValueError("downstream spans must name the dependency document")
        expected_dependency_id = "dep:" + _canonical_sha256(
            {
                "namespace": "mastervault.dependency.v1",
                "source_document_version_id": self.downstream_document_version_id,
                "target_claim_revision_id": self.upstream_claim_revision_id,
                "dependency_kind": self.dependency_kind.value,
            }
        )
        if self.dependency_id != expected_dependency_id:
            raise ValueError("dependency_id does not match canonical dependency semantics")
        _enforce_byte_limit(
            category="dependency-projection-bytes",
            limit=MAX_DEPENDENCY_PROJECTION_BYTES_V1,
            observed=_canonical_projection_charge(self.model_dump(mode="json")),
        )
        return self


class AttentionPath(_StrictFrozenModel):
    path_id: str = Field(pattern=_SHA256_PATTERN)
    anchor_kind: AttentionAnchorKind
    anchor_rank: int = Field(ge=0)
    changed_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    anchor_claim_revision_id: str = Field(pattern=_CLAIM_REVISION_ID_PATTERN)
    anchor_pair_id: str | None = Field(default=None, pattern=_PAIR_ID_PATTERN)
    relation_steps: tuple[RelationPathStep, ...] = ()
    dependency_steps: tuple[DependencyPathStep, ...] = Field(min_length=1)
    target_document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID_PATTERN)
    target_temporal_resolution: TemporalResolution
    eligible_for_attention: bool

    @model_validator(mode="after")
    def _path_integrity(self) -> Self:
        if len(self.relation_steps) > MAX_RELATION_HOPS_V1:
            raise DiscoveryLimitError(
                category="relation-hops",
                limit=MAX_RELATION_HOPS_V1,
                observed=len(self.relation_steps),
            )
        if len(self.dependency_steps) > MAX_DEPENDENCY_DEPTH_V1:
            raise DiscoveryLimitError(
                category="dependency-depth",
                limit=MAX_DEPENDENCY_DEPTH_V1,
                observed=len(self.dependency_steps),
            )
        if (self.anchor_kind == AttentionAnchorKind.UNASSESSED_CANDIDATE) != (
            self.anchor_pair_id is not None
        ):
            raise ValueError("only an unassessed-candidate anchor carries a pair ID")
        current_claim = self.anchor_claim_revision_id
        for step in self.relation_steps:
            if step.traversed_from_claim_revision_id != current_claim:
                raise ValueError("relation path steps are not contiguous")
            current_claim = step.traversed_to_claim_revision_id
        for index, dependency_step in enumerate(self.dependency_steps):
            if dependency_step.upstream_claim_revision_id != current_claim:
                raise ValueError("dependency path steps are not contiguous")
            if index < len(self.dependency_steps) - 1:
                next_claim = self.dependency_steps[index + 1].upstream_claim_revision_id
                if next_claim not in dependency_step.exposed_downstream_claim_revision_ids:
                    raise ValueError("dependency path traversed a non-exposed downstream claim")
                current_claim = next_claim
        if (
            self.dependency_steps[-1].downstream_document_version_id
            != self.target_document_version_id
        ):
            raise ValueError("attention target must equal the terminal downstream document")
        if (
            self.target_temporal_resolution.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or self.target_temporal_resolution.target.target_id != self.target_document_version_id
        ):
            raise ValueError("attention target temporal resolution is bound to the wrong document")
        expected_eligibility = (
            self.dependency_steps[-1].dependency_kind != DependencyKind.HISTORICAL_REFERENCE
        )
        if self.eligible_for_attention != expected_eligibility:
            raise ValueError("attention eligibility must follow the terminal dependency kind")
        payload = {
            "namespace": "mastervault.attention-path.v1",
            "anchor_kind": self.anchor_kind.value,
            "anchor_rank": self.anchor_rank,
            "changed_claim_revision_id": self.changed_claim_revision_id,
            "anchor_claim_revision_id": self.anchor_claim_revision_id,
            "anchor_pair_id": self.anchor_pair_id,
            "relation_steps": [step.model_dump(mode="json") for step in self.relation_steps],
            "dependency_steps": [step.model_dump(mode="json") for step in self.dependency_steps],
            "target_document_version_id": self.target_document_version_id,
            "target_temporal_resolution": self.target_temporal_resolution.model_dump(mode="json"),
            "eligible_for_attention": self.eligible_for_attention,
        }
        _enforce_byte_limit(
            category="attention-path-bytes",
            limit=MAX_ATTENTION_PATH_BYTES_V1,
            observed=_canonical_projection_charge(self.model_dump(mode="json")),
        )
        if self.path_id != _canonical_sha256(payload):
            raise ValueError("path_id does not match the canonical attention path")
        return self


class AttentionScoreV1(_StrictFrozenModel):
    """Integer-only attention ordering; lower is better except support count."""

    schema_version: Literal[1] = 1
    anchor_kind_rank: int = Field(ge=0, le=1)
    dependency_depth: int = Field(gt=0)
    relation_hops: int = Field(ge=0)
    dependency_kind_bucket: int = Field(ge=0, le=1)
    anchor_rank: int = Field(ge=0)
    supporting_dependency_count: int = Field(gt=0)


class DocumentAttentionCandidate(_StrictFrozenModel):
    """A current document attention candidate; this is not an impact verdict."""

    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID_PATTERN)
    document_id: str
    temporal_resolution: TemporalResolution
    score: AttentionScoreV1
    supporting_dependency_ids: tuple[str, ...] = Field(min_length=1)
    paths: tuple[AttentionPath, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.paths) > MAX_PATHS_PER_DOCUMENT_V1:
            raise DiscoveryLimitError(
                category="paths-per-document",
                limit=MAX_PATHS_PER_DOCUMENT_V1,
                observed=len(self.paths),
            )
        if self.supporting_dependency_ids != tuple(sorted(set(self.supporting_dependency_ids))):
            raise ValueError("supporting dependency IDs must be sorted and unique")
        if self.score.supporting_dependency_count != len(self.supporting_dependency_ids):
            raise ValueError("support count does not match supporting dependency IDs")
        if self.paths != tuple(sorted(self.paths, key=lambda path: path.path_id)):
            raise ValueError("attention paths must use canonical path-ID order")
        if len({path.path_id for path in self.paths}) != len(self.paths):
            raise ValueError("attention paths must have unique path IDs")
        if any(path.target_document_version_id != self.document_version_id for path in self.paths):
            raise ValueError("all paths must terminate at the candidate document")
        eligible_paths = tuple(path for path in self.paths if path.eligible_for_attention)
        if not eligible_paths:
            raise ValueError("attention candidates require an eligible dependency path")
        if (
            self.temporal_resolution.state != TemporalState.CURRENT
            or self.temporal_resolution.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or self.temporal_resolution.target.target_id != self.document_version_id
        ):
            raise ValueError("attention candidates require the current target resolution")
        if any(path.target_temporal_resolution != self.temporal_resolution for path in self.paths):
            raise ValueError("attention paths must retain the exact target temporal resolution")
        expected_dependency_ids = tuple(
            sorted({path.dependency_steps[-1].dependency_id for path in eligible_paths})
        )
        if self.supporting_dependency_ids != expected_dependency_ids:
            raise ValueError("supporting dependency IDs do not match eligible paths")
        best = min(
            eligible_paths,
            key=lambda path: (
                _ANCHOR_KIND_RANK[path.anchor_kind],
                len(path.dependency_steps),
                len(path.relation_steps),
                _DEPENDENCY_KIND_BUCKET[path.dependency_steps[-1].dependency_kind],
                path.anchor_rank,
                path.path_id,
            ),
        )
        expected_score = AttentionScoreV1(
            anchor_kind_rank=_ANCHOR_KIND_RANK[best.anchor_kind],
            dependency_depth=len(best.dependency_steps),
            relation_hops=len(best.relation_steps),
            dependency_kind_bucket=_DEPENDENCY_KIND_BUCKET[
                best.dependency_steps[-1].dependency_kind
            ],
            anchor_rank=best.anchor_rank,
            supporting_dependency_count=len(expected_dependency_ids),
        )
        if self.score != expected_score:
            raise ValueError("attention score does not match canonical eligible paths")
        _enforce_attention_target_record_budget(self)
        return self


def _attention_sort_key(candidate: DocumentAttentionCandidate) -> tuple[Any, ...]:
    score = candidate.score
    return (
        score.anchor_kind_rank,
        score.dependency_depth,
        score.relation_hops,
        score.dependency_kind_bucket,
        score.anchor_rank,
        -score.supporting_dependency_count,
        candidate.document_version_id,
    )


class ExcludedAttentionTarget(_StrictFrozenModel):
    document_version_id: str = Field(pattern=_DOCUMENT_VERSION_ID_PATTERN)
    document_id: str
    temporal_resolution: TemporalResolution
    reasons: tuple[AttentionExclusionReason, ...] = Field(min_length=1)
    paths: tuple[AttentionPath, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if len(self.paths) > MAX_PATHS_PER_DOCUMENT_V1:
            raise DiscoveryLimitError(
                category="paths-per-document",
                limit=MAX_PATHS_PER_DOCUMENT_V1,
                observed=len(self.paths),
            )
        expected = tuple(sorted(set(self.reasons), key=_EXCLUSION_ORDER.__getitem__))
        if self.reasons != expected:
            raise ValueError("attention exclusion reasons must use canonical order")
        if self.paths != tuple(sorted(self.paths, key=lambda path: path.path_id)):
            raise ValueError("excluded attention paths must use canonical path-ID order")
        if len({path.path_id for path in self.paths}) != len(self.paths):
            raise ValueError("excluded attention paths must have unique path IDs")
        if any(path.target_document_version_id != self.document_version_id for path in self.paths):
            raise ValueError("all exclusion paths must terminate at the excluded document")
        if (
            self.temporal_resolution.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or self.temporal_resolution.target.target_id != self.document_version_id
        ):
            raise ValueError("excluded target resolution is bound to the wrong document")
        if any(path.target_temporal_resolution != self.temporal_resolution for path in self.paths):
            raise ValueError("exclusion paths must retain the exact target temporal resolution")
        temporal_reasons = {
            AttentionExclusionReason.FUTURE,
            AttentionExclusionReason.HISTORICAL,
            AttentionExclusionReason.EXPIRED,
            AttentionExclusionReason.UNRESOLVED,
        }
        expected_temporal_reason = (
            None
            if self.temporal_resolution.state == TemporalState.CURRENT
            else AttentionExclusionReason(self.temporal_resolution.state.value)
        )
        present_temporal_reasons = set(self.reasons) & temporal_reasons
        if present_temporal_reasons != (
            {expected_temporal_reason} if expected_temporal_reason is not None else set()
        ):
            raise ValueError("temporal exclusion reason does not match target resolution")
        historical_only = not any(path.eligible_for_attention for path in self.paths)
        if (AttentionExclusionReason.HISTORICAL_REFERENCE_ONLY in self.reasons) != historical_only:
            raise ValueError("historical-reference exclusion does not match target paths")
        _enforce_attention_target_record_budget(self)
        return self


class DocumentAttentionRanking(_StrictFrozenModel):
    """Canonical advisory attention ranking and typed ineligible targets."""

    schema_version: Literal[1] = 1
    binding: AnalysisBinding
    attention_candidates: tuple[DocumentAttentionCandidate, ...]
    excluded_targets: tuple[ExcludedAttentionTarget, ...]
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def _serialized_prefix_budgets(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        attention_candidates = data.get("attention_candidates", ())
        excluded_targets = data.get("excluded_targets", ())
        try:
            _enforce_attention_projection_budgets(
                attention_candidates,
                excluded_targets,
            )
            _enforce_attention_target_budgets(
                attention_candidates,
                excluded_targets,
            )
        except (TypeError, KeyError, AttributeError):
            # Python-mode dictionaries can contain dates or other already-typed
            # values, while malformed raw shapes can omit nested fields. The
            # validated-model checks below report ordinary Pydantic errors for
            # those inputs; budget and semantic failures still propagate.
            pass
        return data

    def _result_payload(self) -> dict[str, Any]:
        return {
            "namespace": "mastervault.document-attention-ranking.v1",
            "schema_version": self.schema_version,
            "binding": self.binding.model_dump(mode="json"),
            "attention_candidates": [
                candidate.model_dump(mode="json") for candidate in self.attention_candidates
            ],
            "excluded_targets": [
                target.model_dump(mode="json") for target in self.excluded_targets
            ],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.binding.algorithm_version != AnalysisAlgorithm.DOCUMENT_ATTENTION_RANKING_V1:
            raise ValueError("document-attention ranking has the wrong algorithm binding")
        if self.attention_candidates != tuple(
            sorted(self.attention_candidates, key=_attention_sort_key)
        ):
            raise ValueError("attention candidates must use canonical score order")
        attention_ids = [candidate.document_version_id for candidate in self.attention_candidates]
        if len(attention_ids) != len(set(attention_ids)):
            raise ValueError("attention candidates must have unique document-version IDs")
        excluded_ids = [target.document_version_id for target in self.excluded_targets]
        if excluded_ids != sorted(set(excluded_ids)):
            raise ValueError("excluded targets must use unique canonical document order")
        if set(attention_ids) & set(excluded_ids):
            raise ValueError("a document cannot be both ranked and excluded")
        _enforce_attention_projection_budgets(
            self.attention_candidates,
            self.excluded_targets,
        )
        _enforce_attention_target_budgets(
            self.attention_candidates,
            self.excluded_targets,
        )
        payload = self._result_payload()
        _enforce_byte_limit(
            category="attention-ranking-payload-bytes",
            limit=MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1,
            observed=len(canonical_json_bytes(payload)),
        )
        if self.result_sha256 != _canonical_sha256(payload):
            raise ValueError("document-attention result SHA does not match its content")
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: AnalysisBinding,
        attention_candidates: tuple[DocumentAttentionCandidate, ...],
        excluded_targets: tuple[ExcludedAttentionTarget, ...],
    ) -> Self:
        _enforce_retained_path_count(attention_candidates, excluded_targets)
        canonical_candidates = tuple(sorted(attention_candidates, key=_attention_sort_key))
        canonical_excluded = tuple(
            sorted(excluded_targets, key=lambda target: target.document_version_id)
        )
        _enforce_attention_projection_budgets(canonical_candidates, canonical_excluded)
        _enforce_attention_target_budgets(canonical_candidates, canonical_excluded)
        payload = {
            "namespace": "mastervault.document-attention-ranking.v1",
            "schema_version": 1,
            "binding": binding.model_dump(mode="json"),
            "attention_candidates": [
                candidate.model_dump(mode="json") for candidate in canonical_candidates
            ],
            "excluded_targets": [target.model_dump(mode="json") for target in canonical_excluded],
        }
        _enforce_byte_limit(
            category="attention-ranking-payload-bytes",
            limit=MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1,
            observed=len(canonical_json_bytes(payload)),
        )
        return cls(
            binding=binding,
            attention_candidates=canonical_candidates,
            excluded_targets=canonical_excluded,
            result_sha256=_canonical_sha256(payload),
        )


def _dependency_step_payload(dependency: DependencyAssessment) -> dict[str, Any]:
    return {
        "dependency_id": dependency.dependency_id,
        "dependency_kind": dependency.dependency_kind.value,
        "upstream_claim_revision_id": dependency.upstream.claim_revision_id,
        "downstream_document_version_id": dependency.downstream.document_version_id,
        "downstream_spans": [span.model_dump(mode="json") for span in dependency.downstream_spans],
        "exposed_downstream_claim_revision_ids": [
            revision.claim_revision_id for revision in dependency.downstream_claim_revisions
        ],
    }


def _validate_dependency_projection_budgets(
    dependencies: tuple[DependencyAssessment, ...],
) -> None:
    cumulative_bytes = 0
    for dependency in dependencies:
        if len(dependency.downstream_spans) > MAX_SPANS_PER_DEPENDENCY_V1:
            raise DiscoveryLimitError(
                category="spans-per-dependency",
                limit=MAX_SPANS_PER_DEPENDENCY_V1,
                observed=len(dependency.downstream_spans),
            )
        for span in dependency.downstream_spans:
            _enforce_byte_limit(
                category="span-canonical-bytes",
                limit=MAX_SPAN_CANONICAL_BYTES_V1,
                observed=len(canonical_json_bytes(span.model_dump(mode="json"))),
            )
        projection_bytes = _canonical_projection_charge(_dependency_step_payload(dependency))
        _enforce_byte_limit(
            category="dependency-projection-bytes",
            limit=MAX_DEPENDENCY_PROJECTION_BYTES_V1,
            observed=projection_bytes,
        )
        cumulative_bytes += projection_bytes
        _enforce_byte_limit(
            category="total-unique-dependency-projection-bytes",
            limit=MAX_TOTAL_UNIQUE_DEPENDENCY_PROJECTION_BYTES_V1,
            observed=cumulative_bytes,
        )


def _validate_snapshot(snapshot: ChangeControlSnapshot) -> ChangeControlAggregate:
    if snapshot.revision < 1:
        raise ValueError("change-control snapshot revision must be positive")
    raw_fact_counts = (
        (
            "relation-facts",
            len(snapshot.aggregate.relation_graph.assessments),
            MAX_RELATION_FACTS_V1,
        ),
        (
            "dependency-facts",
            len(snapshot.aggregate.dependencies.assessments),
            MAX_DEPENDENCY_FACTS_V1,
        ),
    )
    for category, observed, limit in raw_fact_counts:
        if observed > limit:
            raise DiscoveryLimitError(category=category, limit=limit, observed=observed)
    _validate_dependency_projection_budgets(snapshot.aggregate.dependencies.assessments)
    aggregate = ChangeControlAggregate.model_validate(snapshot.aggregate.model_dump(mode="json"))
    digest = aggregate_sha256(aggregate)
    if snapshot.aggregate_sha256 != digest:
        raise ValueError("change-control snapshot aggregate SHA does not match its aggregate")
    fact_counts = (
        (
            "relation-facts",
            len(aggregate.relation_graph.assessments),
            MAX_RELATION_FACTS_V1,
        ),
        (
            "dependency-facts",
            len(aggregate.dependencies.assessments),
            MAX_DEPENDENCY_FACTS_V1,
        ),
    )
    for category, observed, limit in fact_counts:
        if observed > limit:
            raise DiscoveryLimitError(category=category, limit=limit, observed=observed)
    return aggregate


def _validate_changed_roots(
    aggregate: ChangeControlAggregate,
    *,
    changed_claim_revision_ids: tuple[str, ...],
    temporal_context: TemporalResolutionContext,
) -> tuple[tuple[VersionedClaimRevision, ...], tuple[TemporalResolution, ...]]:
    if not changed_claim_revision_ids:
        raise ValueError("changed claim revision IDs must not be empty")
    if len(changed_claim_revision_ids) > MAX_CHANGED_ROOTS_V1:
        raise DiscoveryLimitError(
            category="changed-roots",
            limit=MAX_CHANGED_ROOTS_V1,
            observed=len(changed_claim_revision_ids),
        )
    if changed_claim_revision_ids != tuple(sorted(set(changed_claim_revision_ids))):
        raise ValueError("changed claim revision IDs must be sorted and unique")
    by_id = {revision.claim_revision_id: revision for revision in aggregate.claims.revisions}
    missing = [
        revision_id for revision_id in changed_claim_revision_ids if revision_id not in by_id
    ]
    if missing:
        raise ValueError(f"changed claim revision IDs are not exact aggregate roots: {missing}")
    roots = tuple(by_id[revision_id] for revision_id in changed_claim_revision_ids)
    resolutions = tuple(temporal_context.resolve_claim(revision) for revision in roots)
    invalid = [
        (revision.claim_revision_id, resolution.state.value)
        for revision, resolution in zip(roots, resolutions, strict=True)
        if resolution.state not in {TemporalState.CURRENT, TemporalState.FUTURE}
    ]
    if invalid:
        raise ValueError(f"changed roots must be current or future, got {invalid}")
    return roots, resolutions


def _intervals_overlap(first: Any, second: Any) -> TemporalOverlap:
    if first.state == TemporalState.UNRESOLVED or second.state == TemporalState.UNRESOLVED:
        return TemporalOverlap.UNKNOWN
    start = max(first.valid_from_inclusive, second.valid_from_inclusive)
    ends = [value for value in (first.valid_to_exclusive, second.valid_to_exclusive) if value]
    return TemporalOverlap.TRUE if not ends or start < min(ends) else TemporalOverlap.FALSE


def generate_relationship_candidates(
    snapshot: ChangeControlSnapshot,
    *,
    changed_claim_revision_ids: tuple[str, ...],
    as_of: date,
) -> RelationshipCandidateSet:
    """Exhaustively enumerate unassessed changed-to-incumbent claim pairs."""

    aggregate = _validate_snapshot(snapshot)
    temporal_context = TemporalResolutionContext.from_aggregate(aggregate, as_of=as_of)
    return _generate_relationship_candidates(
        snapshot,
        aggregate=aggregate,
        temporal_context=temporal_context,
        changed_claim_revision_ids=changed_claim_revision_ids,
    )


def _generate_relationship_candidates(
    snapshot: ChangeControlSnapshot,
    *,
    aggregate: ChangeControlAggregate,
    temporal_context: TemporalResolutionContext,
    changed_claim_revision_ids: tuple[str, ...],
) -> RelationshipCandidateSet:
    """Generate from one already-validated aggregate and temporal context."""

    changed_roots, changed_resolutions = _validate_changed_roots(
        aggregate,
        changed_claim_revision_ids=changed_claim_revision_ids,
        temporal_context=temporal_context,
    )
    binding = AnalysisBinding.create(
        algorithm_version=AnalysisAlgorithm.RELATIONSHIP_CANDIDATES_V1,
        aggregate_id=aggregate.aggregate_id,
        snapshot_revision=snapshot.revision,
        aggregate_sha256=snapshot.aggregate_sha256,
        as_of=temporal_context.as_of,
        changed_claim_revision_ids=changed_claim_revision_ids,
        changed_temporal_resolutions=changed_resolutions,
    )
    changed_ids = set(changed_claim_revision_ids)
    assessed_pair_ids = {
        assessment.pair.pair_id for assessment in aggregate.relation_graph.assessments
    }
    emittable_count = 0
    for changed in changed_roots:
        for incumbent in aggregate.claims.revisions:
            if incumbent.claim_revision_id in changed_ids:
                continue
            pair = ComparableClaimPair.create(changed, incumbent)
            if pair.pair_id in assessed_pair_ids:
                continue
            emittable_count += 1
            if emittable_count > MAX_RELATIONSHIP_CANDIDATES_V1:
                raise DiscoveryLimitError(
                    category="relationship-candidates",
                    limit=MAX_RELATIONSHIP_CANDIDATES_V1,
                    observed=emittable_count,
                )

    resolution_by_id = {
        revision.claim_revision_id: temporal_context.resolve_claim(revision)
        for revision in aggregate.claims.revisions
    }
    candidates: list[RelationshipCandidate] = []
    cumulative_candidate_bytes = 0
    changed_resolution_by_id = dict(
        zip(changed_claim_revision_ids, changed_resolutions, strict=True)
    )
    for changed in changed_roots:
        changed_resolution = resolution_by_id[changed.claim_revision_id]
        for incumbent in aggregate.claims.revisions:
            if incumbent.claim_revision_id in changed_ids:
                continue
            pair = ComparableClaimPair.create(changed, incumbent)
            if pair.pair_id in assessed_pair_ids:
                continue
            incumbent_resolution = resolution_by_id[incumbent.claim_revision_id]
            score = CandidateScoreV1(
                same_claim_identity=int(changed.claim_identity_id == incumbent.claim_identity_id),
                same_document_family=int(
                    changed.document.document_family == incumbent.document.document_family
                ),
                shared_scope_count=len(pair.shared_scopes),
                temporal_overlap=_intervals_overlap(changed_resolution, incumbent_resolution),
                incumbent_temporal_rank=_TEMPORAL_RANK[incumbent_resolution.state],
            )
            candidate = RelationshipCandidate(
                pair_id=pair.pair_id,
                claim_revision_ids=(
                    pair.claim_revisions[0].claim_revision_id,
                    pair.claim_revisions[1].claim_revision_id,
                ),
                changed_claim_revision_id=changed.claim_revision_id,
                incumbent_claim_revision_id=incumbent.claim_revision_id,
                changed_claim_identity_id=changed.claim_identity_id,
                incumbent_claim_identity_id=incumbent.claim_identity_id,
                changed_document_family=changed.document.document_family,
                incumbent_document_family=incumbent.document.document_family,
                shared_scopes=pair.shared_scopes,
                changed_temporal_resolution=changed_resolution_by_id[changed.claim_revision_id],
                incumbent_temporal_resolution=incumbent_resolution,
                score=score,
            )
            candidate_bytes = _canonical_projection_charge(candidate.model_dump(mode="json"))
            cumulative_candidate_bytes += candidate_bytes
            _enforce_byte_limit(
                category="total-relationship-candidate-bytes",
                limit=MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1,
                observed=cumulative_candidate_bytes,
            )
            candidates.append(candidate)
    return RelationshipCandidateSet.create(binding=binding, candidates=tuple(candidates))


def _candidate_is_anchor(candidate: RelationshipCandidate) -> bool:
    return bool(
        candidate.score.same_claim_identity
        or candidate.score.same_document_family
        or candidate.score.shared_scope_count
    )


def _relation_adjacency(
    aggregate: ChangeControlAggregate,
) -> dict[str, tuple[RelationPathStep, ...]]:
    adjacency: dict[str, list[RelationPathStep]] = {}
    for assessment in aggregate.relation_graph.assessments:
        if assessment.relation_type not in {
            PersistedRelationType.SUPERSEDES,
            PersistedRelationType.CONTRADICTS,
        }:
            continue
        assert assessment.relation_id is not None and assessment.endpoint_ids is not None
        first, second = assessment.endpoint_ids
        if assessment.relation_type == PersistedRelationType.CONTRADICTS:
            directions = (
                (first, second, RelationTraversalDirection.SYMMETRIC),
                (second, first, RelationTraversalDirection.SYMMETRIC),
            )
        else:
            directions = (
                (first, second, RelationTraversalDirection.FORWARD),
                (second, first, RelationTraversalDirection.REVERSE),
            )
        for source, target, direction in directions:
            adjacency.setdefault(source, []).append(
                RelationPathStep(
                    relation_id=assessment.relation_id,
                    relation_type=assessment.relation_type,
                    canonical_endpoint_ids=assessment.endpoint_ids,
                    traversed_from_claim_revision_id=source,
                    traversed_to_claim_revision_id=target,
                    traversal_direction=direction,
                )
            )
    return {
        source: tuple(
            sorted(
                steps,
                key=lambda step: (
                    step.relation_id,
                    step.traversed_to_claim_revision_id,
                    step.traversal_direction.value,
                ),
            )
        )
        for source, steps in adjacency.items()
    }


def _canonical_relation_paths(
    start_claim_revision_id: str,
    adjacency: dict[str, tuple[RelationPathStep, ...]],
) -> dict[str, tuple[RelationPathStep, ...]]:
    paths: dict[str, tuple[RelationPathStep, ...]] = {start_claim_revision_id: ()}
    queue: deque[str] = deque((start_claim_revision_id,))
    while queue:
        current = queue.popleft()
        current_path = paths[current]
        for step in adjacency.get(current, ()):
            target = step.traversed_to_claim_revision_id
            candidate_path = (*current_path, step)
            previous = paths.get(target)
            candidate_key = tuple(item.relation_id for item in candidate_path)
            previous_key = tuple(item.relation_id for item in previous) if previous else ()
            if previous is not None and (
                len(previous) < len(candidate_path)
                or (len(previous) == len(candidate_path) and previous_key <= candidate_key)
            ):
                continue
            if len(candidate_path) > MAX_RELATION_HOPS_V1:
                raise DiscoveryLimitError(
                    category="relation-hops",
                    limit=MAX_RELATION_HOPS_V1,
                    observed=len(candidate_path),
                )
            paths[target] = candidate_path
            queue.append(target)
    return paths


class _Anchor(_StrictFrozenModel):
    kind: AttentionAnchorKind
    rank: int = Field(ge=0)
    changed_claim_revision_id: str
    anchor_claim_revision_id: str
    traversal_claim_revision_id: str
    pair_id: str | None = None
    relation_steps: tuple[RelationPathStep, ...] = ()


def _anchors(
    aggregate: ChangeControlAggregate,
    candidates: RelationshipCandidateSet,
) -> tuple[_Anchor, ...]:
    adjacency = _relation_adjacency(aggregate)
    raw: list[tuple[tuple[Any, ...], _Anchor]] = []
    for changed_id in candidates.binding.changed_claim_revision_ids:
        for target_id, relation_steps in _canonical_relation_paths(changed_id, adjacency).items():
            kind = (
                AttentionAnchorKind.CHANGED_ROOT
                if not relation_steps
                else AttentionAnchorKind.CANONICAL_RELATION
            )
            canonical_key: tuple[Any, ...] = (
                _ANCHOR_KIND_RANK[kind],
                len(relation_steps),
                changed_id,
                target_id,
                tuple(step.relation_id for step in relation_steps),
            )
            raw.append(
                (
                    canonical_key,
                    _Anchor(
                        kind=kind,
                        rank=0,
                        changed_claim_revision_id=changed_id,
                        anchor_claim_revision_id=changed_id,
                        traversal_claim_revision_id=target_id,
                        relation_steps=relation_steps,
                    ),
                )
            )
            if len(raw) > MAX_ANCHORS_V1:
                raise DiscoveryLimitError(
                    category="anchors",
                    limit=MAX_ANCHORS_V1,
                    observed=len(raw),
                )
    for candidate_rank, candidate in enumerate(candidates.candidates):
        if not _candidate_is_anchor(candidate):
            continue
        for target_id, relation_steps in _canonical_relation_paths(
            candidate.incumbent_claim_revision_id, adjacency
        ).items():
            candidate_key: tuple[Any, ...] = (
                _ANCHOR_KIND_RANK[AttentionAnchorKind.UNASSESSED_CANDIDATE],
                candidate_rank,
                len(relation_steps),
                candidate.pair_id,
                target_id,
                tuple(step.relation_id for step in relation_steps),
            )
            raw.append(
                (
                    candidate_key,
                    _Anchor(
                        kind=AttentionAnchorKind.UNASSESSED_CANDIDATE,
                        rank=0,
                        changed_claim_revision_id=candidate.changed_claim_revision_id,
                        anchor_claim_revision_id=candidate.incumbent_claim_revision_id,
                        traversal_claim_revision_id=target_id,
                        pair_id=candidate.pair_id,
                        relation_steps=relation_steps,
                    ),
                )
            )
            if len(raw) > MAX_ANCHORS_V1:
                raise DiscoveryLimitError(
                    category="anchors",
                    limit=MAX_ANCHORS_V1,
                    observed=len(raw),
                )
    ordered = [anchor for _, anchor in sorted(raw, key=lambda item: item[0])]
    return tuple(anchor.model_copy(update={"rank": rank}) for rank, anchor in enumerate(ordered))


def _reverse_dependencies(
    aggregate: ChangeControlAggregate,
) -> dict[str, tuple[DependencyPathStep, ...]]:
    by_upstream: dict[str, list[DependencyPathStep]] = {}
    for dependency in aggregate.dependencies.assessments:
        step = DependencyPathStep.model_validate(_dependency_step_payload(dependency))
        by_upstream.setdefault(dependency.upstream.claim_revision_id, []).append(step)
    return {
        claim_id: tuple(sorted(items, key=lambda step: step.dependency_id))
        for claim_id, items in by_upstream.items()
    }


def _make_attention_path(
    *,
    anchor: _Anchor,
    dependency_steps: tuple[DependencyPathStep, ...],
    target_temporal_resolution: TemporalResolution,
    cumulative_path_bytes: int,
) -> tuple[AttentionPath, int]:
    target_document_version_id = dependency_steps[-1].downstream_document_version_id
    eligible_for_attention = (
        dependency_steps[-1].dependency_kind != DependencyKind.HISTORICAL_REFERENCE
    )
    payload = {
        "namespace": "mastervault.attention-path.v1",
        "anchor_kind": anchor.kind.value,
        "anchor_rank": anchor.rank,
        "changed_claim_revision_id": anchor.changed_claim_revision_id,
        "anchor_claim_revision_id": anchor.anchor_claim_revision_id,
        "anchor_pair_id": anchor.pair_id,
        "relation_steps": [step.model_dump(mode="json") for step in anchor.relation_steps],
        "dependency_steps": [step.model_dump(mode="json") for step in dependency_steps],
        "target_document_version_id": target_document_version_id,
        "target_temporal_resolution": target_temporal_resolution.model_dump(mode="json"),
        "eligible_for_attention": eligible_for_attention,
    }
    projection_payload = {
        "path_id": "0" * 64,
        "anchor_kind": anchor.kind.value,
        "anchor_rank": anchor.rank,
        "changed_claim_revision_id": anchor.changed_claim_revision_id,
        "anchor_claim_revision_id": anchor.anchor_claim_revision_id,
        "anchor_pair_id": anchor.pair_id,
        "relation_steps": payload["relation_steps"],
        "dependency_steps": payload["dependency_steps"],
        "target_document_version_id": target_document_version_id,
        "target_temporal_resolution": payload["target_temporal_resolution"],
        "eligible_for_attention": eligible_for_attention,
    }
    path_bytes = _canonical_projection_charge(projection_payload)
    _enforce_byte_limit(
        category="attention-path-bytes",
        limit=MAX_ATTENTION_PATH_BYTES_V1,
        observed=path_bytes,
    )
    prospective_path_bytes = cumulative_path_bytes + path_bytes
    _enforce_byte_limit(
        category="total-attention-path-bytes",
        limit=MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1,
        observed=prospective_path_bytes,
    )
    path = AttentionPath(
        path_id=_canonical_sha256(payload),
        anchor_kind=anchor.kind,
        anchor_rank=anchor.rank,
        changed_claim_revision_id=anchor.changed_claim_revision_id,
        anchor_claim_revision_id=anchor.anchor_claim_revision_id,
        anchor_pair_id=anchor.pair_id,
        relation_steps=anchor.relation_steps,
        dependency_steps=dependency_steps,
        target_document_version_id=target_document_version_id,
        target_temporal_resolution=target_temporal_resolution,
        eligible_for_attention=eligible_for_attention,
    )
    return path, prospective_path_bytes


def _attention_paths(
    aggregate: ChangeControlAggregate,
    candidates: RelationshipCandidateSet,
    *,
    temporal_context: TemporalResolutionContext,
) -> dict[str, tuple[AttentionPath, ...]]:
    reverse_dependencies = _reverse_dependencies(aggregate)
    documents = {
        document.document_version_id: document for document in aggregate.documents.documents
    }
    document_resolutions: dict[str, TemporalResolution] = {}
    paths_by_document: dict[str, dict[str, AttentionPath]] = {}
    generated_path_count = 0
    generated_path_bytes = 0
    for anchor in _anchors(aggregate, candidates):
        queue: deque[tuple[str, tuple[DependencyPathStep, ...], frozenset[str]]] = deque(
            (
                (
                    anchor.traversal_claim_revision_id,
                    (),
                    frozenset((anchor.traversal_claim_revision_id,)),
                ),
            )
        )
        best_depth: dict[str, int] = {anchor.traversal_claim_revision_id: 0}
        seen_routes: set[tuple[str, tuple[str, ...]]] = {(anchor.traversal_claim_revision_id, ())}
        while queue:
            claim_id, previous_steps, visited_claim_ids = queue.popleft()
            for step in reverse_dependencies.get(claim_id, ()):
                dependency_steps = (*previous_steps, step)
                depth = len(dependency_steps)
                if depth > MAX_DEPENDENCY_DEPTH_V1:
                    raise DiscoveryLimitError(
                        category="dependency-depth",
                        limit=MAX_DEPENDENCY_DEPTH_V1,
                        observed=depth,
                    )
                generated_path_count += 1
                if generated_path_count > MAX_TOTAL_GENERATED_PATHS_V1:
                    raise DiscoveryLimitError(
                        category="total-generated-paths",
                        limit=MAX_TOTAL_GENERATED_PATHS_V1,
                        observed=generated_path_count,
                    )
                target_document_id = step.downstream_document_version_id
                target_resolution = document_resolutions.get(target_document_id)
                if target_resolution is None:
                    target_resolution = temporal_context.resolve_document(
                        documents[target_document_id]
                    )
                    document_resolutions[target_document_id] = target_resolution
                path, generated_path_bytes = _make_attention_path(
                    anchor=anchor,
                    dependency_steps=dependency_steps,
                    target_temporal_resolution=target_resolution,
                    cumulative_path_bytes=generated_path_bytes,
                )
                document_paths = paths_by_document.setdefault(
                    path.target_document_version_id,
                    {},
                )
                if path.path_id not in document_paths:
                    observed_paths = len(document_paths) + 1
                    if observed_paths > MAX_PATHS_PER_DOCUMENT_V1:
                        raise DiscoveryLimitError(
                            category="paths-per-document",
                            limit=MAX_PATHS_PER_DOCUMENT_V1,
                            observed=observed_paths,
                        )
                    document_paths[path.path_id] = path
                for exposed_id in step.exposed_downstream_claim_revision_ids:
                    if exposed_id in visited_claim_ids:
                        continue
                    previous_depth = best_depth.get(exposed_id)
                    if previous_depth is not None and previous_depth < depth:
                        continue
                    route_key = (
                        exposed_id,
                        tuple(item.dependency_id for item in dependency_steps),
                    )
                    if route_key in seen_routes:
                        continue
                    if previous_depth is None or depth < previous_depth:
                        best_depth[exposed_id] = depth
                    seen_routes.add(route_key)
                    queue.append(
                        (
                            exposed_id,
                            dependency_steps,
                            visited_claim_ids | {exposed_id},
                        )
                    )
    return {
        document_id: tuple(paths[path_id] for path_id in sorted(paths))
        for document_id, paths in paths_by_document.items()
    }


def _best_eligible_path(paths: tuple[AttentionPath, ...]) -> AttentionPath:
    eligible = tuple(path for path in paths if path.eligible_for_attention)
    return min(
        eligible,
        key=lambda path: (
            _ANCHOR_KIND_RANK[path.anchor_kind],
            len(path.dependency_steps),
            len(path.relation_steps),
            _DEPENDENCY_KIND_BUCKET[path.dependency_steps[-1].dependency_kind],
            path.anchor_rank,
            path.path_id,
        ),
    )


def rank_document_attention(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
) -> DocumentAttentionRanking:
    """Rank current downstream attention candidates from canonical graph facts."""

    aggregate = _validate_snapshot(snapshot)
    _enforce_relationship_candidate_projection_budgets(candidates.candidates)
    _enforce_byte_limit(
        category="candidate-set-payload-bytes",
        limit=MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1,
        observed=len(canonical_json_bytes(candidates._result_payload())),
    )
    candidates = RelationshipCandidateSet.model_validate(candidates.model_dump(mode="json"))
    candidate_binding = candidates.binding
    if (
        candidate_binding.aggregate_id != aggregate.aggregate_id
        or candidate_binding.snapshot_revision != snapshot.revision
        or candidate_binding.aggregate_sha256 != snapshot.aggregate_sha256
    ):
        raise ValueError("relationship candidates are stale for this change-control snapshot")
    temporal_context = TemporalResolutionContext.from_aggregate(
        aggregate,
        as_of=candidate_binding.as_of,
    )
    _, changed_resolutions = _validate_changed_roots(
        aggregate,
        changed_claim_revision_ids=candidate_binding.changed_claim_revision_ids,
        temporal_context=temporal_context,
    )
    if changed_resolutions != candidate_binding.changed_temporal_resolutions:
        raise ValueError("relationship candidate changed-root temporality is stale")
    expected_candidates = _generate_relationship_candidates(
        snapshot,
        aggregate=aggregate,
        temporal_context=temporal_context,
        changed_claim_revision_ids=candidate_binding.changed_claim_revision_ids,
    )
    if candidates != expected_candidates:
        raise ValueError(
            "relationship candidates are not the complete deterministic set for this snapshot"
        )
    binding = AnalysisBinding.create(
        algorithm_version=AnalysisAlgorithm.DOCUMENT_ATTENTION_RANKING_V1,
        aggregate_id=aggregate.aggregate_id,
        snapshot_revision=snapshot.revision,
        aggregate_sha256=snapshot.aggregate_sha256,
        as_of=candidate_binding.as_of,
        changed_claim_revision_ids=candidate_binding.changed_claim_revision_ids,
        changed_temporal_resolutions=changed_resolutions,
        source_candidate_set_sha256=candidates.result_sha256,
    )
    documents = {
        document.document_version_id: document for document in aggregate.documents.documents
    }
    changed_document_ids = {
        aggregate.claims.get(claim_id).document.document_version_id
        for claim_id in candidate_binding.changed_claim_revision_ids
    }
    attention: list[DocumentAttentionCandidate] = []
    excluded: list[ExcludedAttentionTarget] = []
    cumulative_target_bytes = 0
    for document_version_id, paths in _attention_paths(
        aggregate,
        candidates,
        temporal_context=temporal_context,
    ).items():
        document = documents[document_version_id]
        resolution = paths[0].target_temporal_resolution
        reasons: list[AttentionExclusionReason] = []
        if document_version_id in changed_document_ids:
            reasons.append(AttentionExclusionReason.CHANGED_DOCUMENT)
        if resolution.state != TemporalState.CURRENT:
            reasons.append(AttentionExclusionReason(resolution.state.value))
        eligible_paths = tuple(path for path in paths if path.eligible_for_attention)
        if not eligible_paths:
            reasons.append(AttentionExclusionReason.HISTORICAL_REFERENCE_ONLY)
        if reasons:
            canonical_reasons = tuple(sorted(set(reasons), key=_EXCLUSION_ORDER.__getitem__))
            excluded_target = ExcludedAttentionTarget(
                document_version_id=document_version_id,
                document_id=document.document_id,
                temporal_resolution=resolution,
                reasons=canonical_reasons,
                paths=paths,
            )
            target_bytes = _enforce_attention_target_record_budget(excluded_target)
            prospective_target_bytes = cumulative_target_bytes + target_bytes
            _enforce_byte_limit(
                category="total-attention-target-record-bytes",
                limit=MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1,
                observed=prospective_target_bytes,
            )
            excluded.append(excluded_target)
            cumulative_target_bytes = prospective_target_bytes
            continue
        best = _best_eligible_path(paths)
        supporting_dependency_ids = tuple(
            sorted({path.dependency_steps[-1].dependency_id for path in eligible_paths})
        )
        attention_target = DocumentAttentionCandidate(
            document_version_id=document_version_id,
            document_id=document.document_id,
            temporal_resolution=resolution,
            score=AttentionScoreV1(
                anchor_kind_rank=_ANCHOR_KIND_RANK[best.anchor_kind],
                dependency_depth=len(best.dependency_steps),
                relation_hops=len(best.relation_steps),
                dependency_kind_bucket=_DEPENDENCY_KIND_BUCKET[
                    best.dependency_steps[-1].dependency_kind
                ],
                anchor_rank=best.anchor_rank,
                supporting_dependency_count=len(supporting_dependency_ids),
            ),
            supporting_dependency_ids=supporting_dependency_ids,
            paths=paths,
        )
        target_bytes = _enforce_attention_target_record_budget(attention_target)
        prospective_target_bytes = cumulative_target_bytes + target_bytes
        _enforce_byte_limit(
            category="total-attention-target-record-bytes",
            limit=MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1,
            observed=prospective_target_bytes,
        )
        attention.append(attention_target)
        cumulative_target_bytes = prospective_target_bytes
    return DocumentAttentionRanking.create(
        binding=binding,
        attention_candidates=tuple(attention),
        excluded_targets=tuple(excluded),
    )


def validate_document_attention_ranking(
    snapshot: ChangeControlSnapshot,
    *,
    candidates: RelationshipCandidateSet,
    ranking: DocumentAttentionRanking,
) -> DocumentAttentionRanking:
    """Verify ranking consistency against deterministic snapshot regeneration.

    A result SHA proves only canonical content integrity because callers can
    recompute it. Snapshot-relative validity for this pure layer requires exact
    regeneration from the supplied snapshot and complete candidate set.
    """

    _enforce_attention_projection_budgets(
        ranking.attention_candidates,
        ranking.excluded_targets,
    )
    _enforce_attention_target_budgets(
        ranking.attention_candidates,
        ranking.excluded_targets,
    )
    _enforce_byte_limit(
        category="attention-ranking-payload-bytes",
        limit=MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1,
        observed=len(canonical_json_bytes(ranking._result_payload())),
    )
    validated = DocumentAttentionRanking.model_validate(ranking.model_dump(mode="json"))
    expected = rank_document_attention(snapshot, candidates=candidates)
    if validated != expected:
        raise ValueError(
            "document-attention ranking is not the deterministic result for this snapshot"
        )
    return validated


__all__ = [
    "AttentionAnchorKind",
    "AttentionExclusionReason",
    "AttentionPath",
    "AttentionScoreV1",
    "AnalysisAlgorithm",
    "AnalysisBinding",
    "AnalysisMode",
    "CandidateStatus",
    "CandidateScoreV1",
    "DependencyPathStep",
    "DiscoveryLimitError",
    "DocumentAttentionCandidate",
    "DocumentAttentionRanking",
    "ExcludedAttentionTarget",
    "MAX_ANCHORS_V1",
    "MAX_ATTENTION_PATH_BYTES_V1",
    "MAX_ATTENTION_RANKING_PAYLOAD_BYTES_V1",
    "MAX_ATTENTION_TARGET_RECORD_BYTES_V1",
    "MAX_CANDIDATE_SET_PAYLOAD_BYTES_V1",
    "MAX_CHANGED_ROOTS_V1",
    "MAX_DEPENDENCY_DEPTH_V1",
    "MAX_DEPENDENCY_FACTS_V1",
    "MAX_DEPENDENCY_PROJECTION_BYTES_V1",
    "MAX_PATHS_PER_DOCUMENT_V1",
    "MAX_RELATIONSHIP_CANDIDATE_BYTES_V1",
    "MAX_RELATIONSHIP_CANDIDATES_V1",
    "MAX_RELATION_FACTS_V1",
    "MAX_RELATION_HOPS_V1",
    "MAX_SPAN_CANONICAL_BYTES_V1",
    "MAX_SPANS_PER_DEPENDENCY_V1",
    "MAX_TOTAL_GENERATED_ATTENTION_PATH_BYTES_V1",
    "MAX_TOTAL_GENERATED_PATHS_V1",
    "MAX_TOTAL_ATTENTION_TARGET_RECORD_BYTES_V1",
    "MAX_TOTAL_RELATIONSHIP_CANDIDATE_BYTES_V1",
    "MAX_TOTAL_UNIQUE_DEPENDENCY_PROJECTION_BYTES_V1",
    "RelationPathStep",
    "RelationTraversalDirection",
    "RelationshipCandidate",
    "RelationshipCandidateSet",
    "TemporalOverlap",
    "generate_relationship_candidates",
    "rank_document_attention",
    "validate_document_attention_ranking",
]
