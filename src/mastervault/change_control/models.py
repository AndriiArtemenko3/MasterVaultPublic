"""Provider-independent temporal contracts for knowledge change control.

Logical identities, source bindings, semantic revisions, and derived temporal
state are deliberately separate. Corrections to paths, hashes, evidence, or
inferred currentness therefore cannot silently rewrite durable graph identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.document_intelligence.models import EvidenceRef, StructuralEvidenceRef
from mastervault.models import CLAIM_ID_RE

SHA256_PATTERN = r"^[0-9a-f]{64}$"
CONTENT_ID_PATTERNS = {
    "docv": r"^docv:[0-9a-f]{64}$",
    "claim": r"^claim:[0-9a-f]{64}$",
    "claimrev": r"^claimrev:[0-9a-f]{64}$",
    "pair": r"^pair:[0-9a-f]{64}$",
    "rel": r"^rel:[0-9a-f]{64}$",
    "dep": r"^dep:[0-9a-f]{64}$",
    "tempc": r"^tempc:[0-9a-f]{64}$",
}

DOCUMENT_VERSION_NAMESPACE: Final = "mastervault.document-version.v1"
CLAIM_IDENTITY_NAMESPACE: Final = "mastervault.claim-identity.v1"
CLAIM_REVISION_NAMESPACE: Final = "mastervault.claim-revision.v1"
CLAIM_PAIR_NAMESPACE: Final = "mastervault.claim-pair.v1"
RELATION_NAMESPACE: Final = "mastervault.relation.v1"
DOCUMENT_REPLACEMENT_NAMESPACE: Final = "mastervault.document-replacement.v1"
DEPENDENCY_NAMESPACE: Final = "mastervault.dependency.v1"
TEMPORAL_CONSTRAINT_NAMESPACE: Final = "mastervault.temporal-constraint.v1"

_LOGICAL_KEY_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def canonical_json_bytes(payload: Any) -> bytes:
    """Encode an already JSON-compatible value with one canonical profile."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_content_id(
    prefix: Literal["docv", "claim", "claimrev", "pair", "rel", "dep", "tempc"],
    payload: Any,
) -> str:
    """Return a readable prefix plus the full SHA-256 of canonical JSON."""
    return f"{prefix}:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def normalize_logical_key(value: str) -> str:
    """Normalize a family/version/scope key without guessing punctuation."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"[\s_]+", "-", normalized)
    if _LOGICAL_KEY_RE.fullmatch(normalized) is None:
        raise ValueError(f"must normalize to lowercase dot/kebab identity, got {value!r}")
    return normalized


def normalize_semantic_text(value: str) -> str:
    """Canonical semantic text: NFKC and exactly one whitespace separator."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _require_normalized_text(value: str, *, label: str, min_length: int = 1) -> str:
    normalized = normalize_semantic_text(value)
    if len(normalized) < min_length:
        raise ValueError(f"{label} must contain at least {min_length} non-whitespace characters")
    if value != normalized:
        raise ValueError(f"{label} must be normalized without surrounding or repeated whitespace")
    return value


def _canonical_semantic_input(value: str, *, label: str, min_length: int = 1) -> str:
    """Canonicalize Unicode while refusing whitespace repair at API boundaries."""
    whitespace_canonical = " ".join(value.split())
    if value != whitespace_canonical:
        raise ValueError(f"{label} must not contain surrounding or repeated whitespace")
    normalized = normalize_semantic_text(value)
    if len(normalized) < min_length:
        raise ValueError(f"{label} must contain at least {min_length} non-whitespace characters")
    return normalized


def _require_normalized_key(value: str, *, label: str) -> str:
    normalized = normalize_logical_key(value)
    if value != normalized:
        raise ValueError(f"{label} must already be normalized as {normalized!r}")
    return value


def _safe_relative_path(value: str) -> str:
    raw = value.strip()
    candidate = PurePosixPath(raw.replace("\\", "/"))
    windows = PureWindowsPath(raw)
    if (
        not raw
        or "\x00" in raw
        or not candidate.parts
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
    ):
        raise ValueError(f"must be a safe relative path, got {value!r}")
    return candidate.as_posix()


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PairDisposition(StrEnum):
    SUPERSEDES = "SUPERSEDES"
    CONTRADICTS = "CONTRADICTS"
    COEXISTS = "COEXISTS"
    UNRELATED = "UNRELATED"


class PersistedRelationType(StrEnum):
    SUPERSEDES = "SUPERSEDES"
    CONTRADICTS = "CONTRADICTS"
    DEPENDS_ON = "DEPENDS_ON"


class DocumentRole(StrEnum):
    POLICY = "policy"
    MEMO = "memo"
    FAQ = "faq"
    SOP = "sop"
    PROCESS = "process"
    PROPOSAL = "proposal"
    OTHER = "other"


class DocumentAuthority(StrEnum):
    PRIMARY = "primary"
    DELEGATED = "delegated"
    TRANSACTIONAL = "transactional"
    INFORMATIONAL = "informational"


class DependencyKind(StrEnum):
    QUOTES = "quotes"
    IMPLEMENTS = "implements"
    SUMMARIZES = "summarizes"
    HISTORICAL_REFERENCE = "historical-reference"


class TemporalConstraintStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TemporalState(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    FUTURE = "future"
    EXPIRED = "expired"
    UNRESOLVED = "unresolved"


class TemporalTargetKind(StrEnum):
    DOCUMENT_VERSION = "document-version"
    CLAIM_REVISION = "claim-revision"


class DocumentVersionMetadata(_StrictFrozenModel):
    """One immutable binding for a stable logical document-version identity."""

    identity_namespace: Literal["mastervault.document-version.v1"] = DOCUMENT_VERSION_NAMESPACE
    document_version_id: str = Field(pattern=CONTENT_ID_PATTERNS["docv"])
    document_id: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
    document_family: str
    version_label: str
    source_path: str
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    declared_effective_from: date
    declared_effective_to: date | None = None
    role: DocumentRole
    authority: DocumentAuthority

    @field_validator("document_id", "document_family", "version_label")
    @classmethod
    def _logical_keys(cls, value: str, info: Any) -> str:
        return _require_normalized_key(value, label=info.field_name)

    @field_validator("source_path")
    @classmethod
    def _source_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.identity_namespace,
            "document_family": self.document_family,
            "version_label": self.version_label,
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.declared_effective_to is not None
            and self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("declared_effective_to must follow declared_effective_from")
        if self.document_version_id != stable_content_id("docv", self._identity_payload()):
            raise ValueError("document_version_id does not match logical family/version identity")
        return self

    @classmethod
    def create(
        cls,
        *,
        document_id: str,
        document_family: str,
        version_label: str,
        source_path: str,
        source_sha256: str,
        declared_effective_from: date,
        declared_effective_to: date | None = None,
        role: DocumentRole,
        authority: DocumentAuthority,
    ) -> Self:
        normalized_document_id = normalize_logical_key(document_id)
        normalized_family = normalize_logical_key(document_family)
        normalized_version = normalize_logical_key(version_label)
        payload = {
            "namespace": DOCUMENT_VERSION_NAMESPACE,
            "document_family": normalized_family,
            "version_label": normalized_version,
        }
        return cls(
            document_version_id=stable_content_id("docv", payload),
            document_id=normalized_document_id,
            document_family=normalized_family,
            version_label=normalized_version,
            source_path=_safe_relative_path(source_path),
            source_sha256=source_sha256,
            declared_effective_from=declared_effective_from,
            declared_effective_to=declared_effective_to,
            role=role,
            authority=authority,
        )


class DocumentVersionRegistry(_StrictFrozenModel):
    """One current binding per logical family/version, with byte conflict checks."""

    documents: tuple[DocumentVersionMetadata, ...]

    @model_validator(mode="after")
    def _unique_bindings(self) -> Self:
        by_id: dict[str, DocumentVersionMetadata] = {}
        for document in self.documents:
            previous = by_id.get(document.document_version_id)
            if previous is None:
                by_id[document.document_version_id] = document
                continue
            if previous.source_sha256 != document.source_sha256:
                raise ValueError("different source bytes claim the same document family/version")
            raise ValueError("registry requires exactly one binding per document family/version")
        if [item.document_version_id for item in self.documents] != sorted(by_id):
            raise ValueError("registry documents must use canonical document_version_id order")
        return self

    @classmethod
    def create(cls, documents: tuple[DocumentVersionMetadata, ...]) -> Self:
        by_id: dict[str, DocumentVersionMetadata] = {}
        for document in documents:
            previous = by_id.get(document.document_version_id)
            if previous is None:
                by_id[document.document_version_id] = document
            elif previous.source_sha256 != document.source_sha256:
                raise ValueError("different source bytes claim the same document family/version")
            elif previous != document:
                raise ValueError("logical document version has multiple non-identical bindings")
        return cls(documents=tuple(by_id[key] for key in sorted(by_id)))

    def get(self, document_version_id: str) -> DocumentVersionMetadata:
        for document in self.documents:
            if document.document_version_id == document_version_id:
                return document
        raise KeyError(document_version_id)


class ClaimSourceReference(_StrictFrozenModel):
    """Verified canonical-note claim binding plus unchanged M1-M3 evidence."""

    source_note_path: str
    source_note_sha256: str = Field(pattern=SHA256_PATTERN)
    source_claim_id: str = Field(pattern=CLAIM_ID_RE.pattern)
    evidence: tuple[EvidenceRef | StructuralEvidenceRef, ...] = ()

    @field_validator("source_note_path")
    @classmethod
    def _source_note_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class DocumentSpanReference(_StrictFrozenModel):
    """Exact canonical-document body span, optionally tied to PDF evidence.

    Offsets are file-relative and must be resolved from one verified note byte
    snapshot. Record-level binding is deferred until a storage-backed resolver
    can verify record ownership.
    """

    document_version_id: str = Field(pattern=CONTENT_ID_PATTERNS["docv"])
    source_note_path: str
    source_note_sha256: str = Field(pattern=SHA256_PATTERN)
    record_id: None = None
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    evidence: tuple[EvidenceRef | StructuralEvidenceRef, ...] = ()

    @field_validator("source_note_path")
    @classmethod
    def _source_note_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("quote")
    @classmethod
    def _quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quote must contain non-whitespace evidence")
        return value

    @model_validator(mode="after")
    def _exact_span(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("document evidence end_char must exceed start_char")
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("document evidence offsets must exactly span the quote")
        if self.evidence and any(item.quote != self.quote for item in self.evidence):
            raise ValueError("PDF evidence quotes must exactly match the document span quote")
        return self


class VersionedClaimRevision(_StrictFrozenModel):
    """A stable claim identity plus one content-addressed semantic revision."""

    identity_namespace: Literal["mastervault.claim-identity.v1"] = CLAIM_IDENTITY_NAMESPACE
    revision_namespace: Literal["mastervault.claim-revision.v1"] = CLAIM_REVISION_NAMESPACE
    claim_identity_id: str = Field(pattern=CONTENT_ID_PATTERNS["claim"])
    claim_revision_id: str = Field(pattern=CONTENT_ID_PATTERNS["claimrev"])
    document: DocumentVersionMetadata
    source: ClaimSourceReference
    statement: str = Field(min_length=8)
    declared_effective_from: date
    declared_effective_to: date | None = None
    scopes: tuple[str, ...] = Field(min_length=1)

    @field_validator("statement")
    @classmethod
    def _statement(cls, value: str) -> str:
        return _require_normalized_text(value, label="statement", min_length=8)

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_logical_key(value) for value in values)
        if values != normalized or values != tuple(sorted(set(values))):
            raise ValueError("claim scopes must be normalized, sorted, and unique")
        return values

    def _claim_identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.identity_namespace,
            "document_version_id": self.document.document_version_id,
            "local_key": self.source.source_claim_id,
        }

    def _revision_identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.revision_namespace,
            "claim_identity_id": self.claim_identity_id,
            "statement": self.statement,
            "scopes": list(self.scopes),
            "declared_effective_from": self.declared_effective_from.isoformat(),
            "declared_effective_to": (
                self.declared_effective_to.isoformat() if self.declared_effective_to else None
            ),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if (
            self.declared_effective_to is not None
            and self.declared_effective_to <= self.declared_effective_from
        ):
            raise ValueError("claim declared_effective_to must follow declared_effective_from")
        if self.declared_effective_from < self.document.declared_effective_from:
            raise ValueError("claim cannot become effective before its document version")
        if self.document.declared_effective_to is not None:
            if self.declared_effective_from >= self.document.declared_effective_to:
                raise ValueError("claim cannot start after its document version ends")
            if (
                self.declared_effective_to is None
                or self.declared_effective_to > self.document.declared_effective_to
            ):
                raise ValueError("claim declared interval must fit its closed document version")
        if self.claim_identity_id != stable_content_id("claim", self._claim_identity_payload()):
            raise ValueError("claim_identity_id does not match document/local-key identity")
        if self.claim_revision_id != stable_content_id(
            "claimrev", self._revision_identity_payload()
        ):
            raise ValueError("claim_revision_id does not match semantic revision identity")
        return self

    @classmethod
    def create(
        cls,
        *,
        document: DocumentVersionMetadata,
        source: ClaimSourceReference,
        statement: str,
        declared_effective_from: date,
        declared_effective_to: date | None = None,
        scopes: tuple[str, ...],
    ) -> Self:
        canonical_statement = _canonical_semantic_input(
            statement,
            label="statement",
            min_length=8,
        )
        canonical_scopes = tuple(sorted({normalize_logical_key(scope) for scope in scopes}))
        identity_payload = {
            "namespace": CLAIM_IDENTITY_NAMESPACE,
            "document_version_id": document.document_version_id,
            "local_key": source.source_claim_id,
        }
        claim_identity_id = stable_content_id("claim", identity_payload)
        revision_payload = {
            "namespace": CLAIM_REVISION_NAMESPACE,
            "claim_identity_id": claim_identity_id,
            "statement": canonical_statement,
            "scopes": list(canonical_scopes),
            "declared_effective_from": declared_effective_from.isoformat(),
            "declared_effective_to": (
                declared_effective_to.isoformat() if declared_effective_to else None
            ),
        }
        return cls(
            claim_identity_id=claim_identity_id,
            claim_revision_id=stable_content_id("claimrev", revision_payload),
            document=document,
            source=source,
            statement=canonical_statement,
            declared_effective_from=declared_effective_from,
            declared_effective_to=declared_effective_to,
            scopes=canonical_scopes,
        )


def _require_consistent_document_bindings(
    documents: tuple[DocumentVersionMetadata, ...],
    *,
    context: str,
) -> None:
    by_id: dict[str, DocumentVersionMetadata] = {}
    for document in documents:
        previous = by_id.get(document.document_version_id)
        if previous is None:
            by_id[document.document_version_id] = document
        elif previous != document:
            raise ValueError(
                f"{context} embeds conflicting bindings for document_version_id "
                f"{document.document_version_id}"
            )


def _require_consistent_claim_bindings(
    revisions: tuple[VersionedClaimRevision, ...],
    *,
    context: str,
) -> None:
    by_id: dict[str, VersionedClaimRevision] = {}
    for revision in revisions:
        previous = by_id.get(revision.claim_revision_id)
        if previous is None:
            by_id[revision.claim_revision_id] = revision
        elif previous != revision:
            raise ValueError(
                f"{context} embeds conflicting bindings for claim_revision_id "
                f"{revision.claim_revision_id}"
            )
    _require_consistent_document_bindings(
        tuple(revision.document for revision in revisions),
        context=context,
    )


class ClaimRevisionRegistry(_StrictFrozenModel):
    """Canonical source binding for each semantic claim revision."""

    revisions: tuple[VersionedClaimRevision, ...]

    @model_validator(mode="after")
    def _unique_bindings(self) -> Self:
        _require_consistent_claim_bindings(
            self.revisions,
            context="claim revision registry",
        )
        by_id: dict[str, VersionedClaimRevision] = {}
        for revision in self.revisions:
            if revision.claim_revision_id in by_id:
                raise ValueError("claim registry requires one binding per claim_revision_id")
            by_id[revision.claim_revision_id] = revision
        if [item.claim_revision_id for item in self.revisions] != sorted(by_id):
            raise ValueError("claim revisions must use canonical claim_revision_id order")
        return self

    @classmethod
    def create(cls, revisions: tuple[VersionedClaimRevision, ...]) -> Self:
        by_id: dict[str, VersionedClaimRevision] = {}
        for revision in revisions:
            previous = by_id.get(revision.claim_revision_id)
            if previous is None:
                by_id[revision.claim_revision_id] = revision
            elif previous != revision:
                raise ValueError("semantic claim revision has multiple source bindings")
        return cls(revisions=tuple(by_id[key] for key in sorted(by_id)))

    def get(self, claim_revision_id: str) -> VersionedClaimRevision:
        for revision in self.revisions:
            if revision.claim_revision_id == claim_revision_id:
                return revision
        raise KeyError(claim_revision_id)


class ComparableClaimPair(_StrictFrozenModel):
    """Direction-free endpoint pair with mechanically derived shared scopes."""

    identity_namespace: Literal["mastervault.claim-pair.v1"] = CLAIM_PAIR_NAMESPACE
    pair_id: str = Field(pattern=CONTENT_ID_PATTERNS["pair"])
    claim_revisions: tuple[VersionedClaimRevision, VersionedClaimRevision]
    shared_scopes: tuple[str, ...] = ()

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.identity_namespace,
            "claim_revision_ids": [revision.claim_revision_id for revision in self.claim_revisions],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        revision_ids = [revision.claim_revision_id for revision in self.claim_revisions]
        if len(set(revision_ids)) != 2:
            raise ValueError("a comparable pair requires two distinct claim revisions")
        if revision_ids != sorted(revision_ids):
            raise ValueError("claim pair endpoints must use canonical ID order")
        expected_scopes = tuple(
            sorted(set(self.claim_revisions[0].scopes) & set(self.claim_revisions[1].scopes))
        )
        if self.shared_scopes != expected_scopes:
            raise ValueError("shared_scopes must equal the endpoint scope intersection")
        if self.pair_id != stable_content_id("pair", self._identity_payload()):
            raise ValueError("pair_id does not match its endpoint-only identity")
        return self

    @classmethod
    def create(
        cls,
        first: VersionedClaimRevision,
        second: VersionedClaimRevision,
    ) -> Self:
        ordered = sorted((first, second), key=lambda item: item.claim_revision_id)
        revisions = (ordered[0], ordered[1])
        if len({item.claim_revision_id for item in revisions}) != 2:
            raise ValueError("a comparable pair requires two distinct claim revisions")
        shared_scopes = tuple(sorted(set(revisions[0].scopes) & set(revisions[1].scopes)))
        payload = {
            "namespace": CLAIM_PAIR_NAMESPACE,
            "claim_revision_ids": [item.claim_revision_id for item in revisions],
        }
        return cls(
            pair_id=stable_content_id("pair", payload),
            claim_revisions=revisions,
            shared_scopes=shared_scopes,
        )

    def revision(self, revision_id: str) -> VersionedClaimRevision:
        for revision in self.claim_revisions:
            if revision.claim_revision_id == revision_id:
                return revision
        raise ValueError(f"claim revision is not an endpoint of {self.pair_id}: {revision_id}")


class RelationAssessment(_StrictFrozenModel):
    """One current pair classification; only two dispositions persist edges."""

    pair: ComparableClaimPair
    disposition: PairDisposition
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    relation_type: PersistedRelationType | None = None
    relation_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERNS["rel"])
    endpoint_ids: tuple[str, str] | None = None

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _require_normalized_text(value, label="rationale")

    @model_validator(mode="after")
    def _relation_semantics(self) -> Self:
        if (
            self.disposition
            in {PairDisposition.SUPERSEDES, PairDisposition.CONTRADICTS, PairDisposition.COEXISTS}
            and not self.pair.shared_scopes
        ):
            raise ValueError(f"{self.disposition.value} requires a shared semantic scope")

        pair_ids = tuple(item.claim_revision_id for item in self.pair.claim_revisions)
        if self.disposition in {PairDisposition.COEXISTS, PairDisposition.UNRELATED}:
            if any(
                value is not None
                for value in (self.relation_type, self.relation_id, self.endpoint_ids)
            ):
                raise ValueError("COEXISTS and UNRELATED are dispositions, never persisted edges")
            return self

        expected_type = PersistedRelationType(self.disposition.value)
        if (
            self.relation_type != expected_type
            or self.relation_id is None
            or self.endpoint_ids is None
        ):
            raise ValueError(
                "persisted dispositions require a matching relation type, ID, and endpoints"
            )
        if set(self.endpoint_ids) != set(pair_ids):
            raise ValueError("relation endpoints must exactly match the comparable pair")

        if self.relation_type == PersistedRelationType.CONTRADICTS:
            if self.endpoint_ids != tuple(sorted(self.endpoint_ids)):
                raise ValueError("CONTRADICTS endpoints must use canonical symmetric order")
        elif self.relation_type == PersistedRelationType.SUPERSEDES:
            newer = self.pair.revision(self.endpoint_ids[0])
            older = self.pair.revision(self.endpoint_ids[1])
            if newer.document.document_family != older.document.document_family:
                raise ValueError("SUPERSEDES requires revisions in the same document family")
            if newer.declared_effective_from <= older.declared_effective_from:
                raise ValueError("SUPERSEDES must be directed from newer to older")

        expected_id = stable_content_id(
            "rel",
            {
                "namespace": RELATION_NAMESPACE,
                "relation_type": self.relation_type.value,
                "endpoint_ids": list(self.endpoint_ids),
            },
        )
        if self.relation_id != expected_id:
            raise ValueError("relation_id does not match its type and endpoints")
        return self

    @classmethod
    def create(
        cls,
        *,
        pair: ComparableClaimPair,
        disposition: PairDisposition,
        rationale: str,
        confidence: float,
        newer_revision_id: str | None = None,
    ) -> Self:
        canonical_rationale = _canonical_semantic_input(rationale, label="rationale")
        if disposition in {PairDisposition.COEXISTS, PairDisposition.UNRELATED}:
            return cls(
                pair=pair,
                disposition=disposition,
                rationale=canonical_rationale,
                confidence=confidence,
            )
        relation_type = PersistedRelationType(disposition.value)
        canonical_ids = (
            pair.claim_revisions[0].claim_revision_id,
            pair.claim_revisions[1].claim_revision_id,
        )
        endpoint_ids: tuple[str, str]
        if disposition == PairDisposition.SUPERSEDES:
            if newer_revision_id not in canonical_ids:
                raise ValueError("SUPERSEDES requires a newer revision endpoint")
            endpoint_ids = (
                newer_revision_id,
                next(item for item in canonical_ids if item != newer_revision_id),
            )
        else:
            if newer_revision_id is not None:
                raise ValueError("CONTRADICTS is symmetric and does not accept a newer endpoint")
            endpoint_ids = canonical_ids
        relation_id = stable_content_id(
            "rel",
            {
                "namespace": RELATION_NAMESPACE,
                "relation_type": relation_type.value,
                "endpoint_ids": list(endpoint_ids),
            },
        )
        return cls(
            pair=pair,
            disposition=disposition,
            rationale=canonical_rationale,
            confidence=confidence,
            relation_type=relation_type,
            relation_id=relation_id,
            endpoint_ids=endpoint_ids,
        )


class DependencyAssessment(_StrictFrozenModel):
    """A directed ``downstream document --DEPENDS_ON--> upstream claim`` edge."""

    dependency_id: str = Field(pattern=CONTENT_ID_PATTERNS["dep"])
    relation_type: Literal[PersistedRelationType.DEPENDS_ON] = PersistedRelationType.DEPENDS_ON
    downstream: DocumentVersionMetadata
    upstream: VersionedClaimRevision
    downstream_spans: tuple[DocumentSpanReference, ...] = Field(min_length=1)
    downstream_claim_revisions: tuple[VersionedClaimRevision, ...] = ()
    dependency_kind: DependencyKind
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _require_normalized_text(value, label="rationale")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": DEPENDENCY_NAMESPACE,
            "source_document_version_id": self.downstream.document_version_id,
            "target_claim_revision_id": self.upstream.claim_revision_id,
            "dependency_kind": self.dependency_kind.value,
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.downstream.document_version_id == self.upstream.document.document_version_id:
            raise ValueError("DEPENDS_ON requires distinct downstream and upstream documents")
        span_keys = [
            canonical_json_bytes(span.model_dump(mode="json")) for span in self.downstream_spans
        ]
        if len(span_keys) != len(set(span_keys)) or span_keys != sorted(span_keys):
            raise ValueError("downstream spans must be canonical, sorted, and unique")
        note_bindings = {
            (span.source_note_path, span.source_note_sha256) for span in self.downstream_spans
        }
        if len(note_bindings) != 1:
            raise ValueError("all downstream spans must bind the same canonical note snapshot")
        for span in self.downstream_spans:
            if span.document_version_id != self.downstream.document_version_id:
                raise ValueError("DEPENDS_ON evidence must name the downstream document version")

        claim_ids = [revision.claim_revision_id for revision in self.downstream_claim_revisions]
        if len(claim_ids) != len(set(claim_ids)) or claim_ids != sorted(claim_ids):
            raise ValueError("downstream claim revisions must be canonical, sorted, and unique")
        _require_consistent_claim_bindings(
            (self.upstream, *self.downstream_claim_revisions),
            context="dependency assessment",
        )
        _require_consistent_document_bindings(
            (self.downstream, self.upstream.document),
            context="dependency assessment",
        )
        for downstream_claim in self.downstream_claim_revisions:
            if downstream_claim.document != self.downstream:
                raise ValueError(
                    "associated downstream claim must belong to the downstream document"
                )
            if any(
                downstream_claim.source.source_note_path != span.source_note_path
                or downstream_claim.source.source_note_sha256 != span.source_note_sha256
                for span in self.downstream_spans
            ):
                raise ValueError(
                    "every associated downstream claim and span must bind the same note path and SHA"
                )
        if self.dependency_id != stable_content_id("dep", self._identity_payload()):
            raise ValueError("dependency_id does not match dependency semantics")
        return self

    @classmethod
    def create(
        cls,
        *,
        downstream: DocumentVersionMetadata,
        upstream: VersionedClaimRevision,
        dependency_kind: DependencyKind,
        downstream_spans: tuple[DocumentSpanReference, ...],
        downstream_claim_revisions: tuple[VersionedClaimRevision, ...] = (),
        rationale: str,
        confidence: float,
    ) -> Self:
        canonical_rationale = _canonical_semantic_input(rationale, label="rationale")
        spans_by_key = {
            canonical_json_bytes(span.model_dump(mode="json")): span for span in downstream_spans
        }
        canonical_spans = tuple(spans_by_key[key] for key in sorted(spans_by_key))
        claims_by_id: dict[str, VersionedClaimRevision] = {}
        for revision in downstream_claim_revisions:
            previous = claims_by_id.get(revision.claim_revision_id)
            if previous is None:
                claims_by_id[revision.claim_revision_id] = revision
            elif previous != revision:
                raise ValueError("downstream claim revision has conflicting source bindings")
        canonical_claims = tuple(claims_by_id[key] for key in sorted(claims_by_id))
        payload = {
            "namespace": DEPENDENCY_NAMESPACE,
            "source_document_version_id": downstream.document_version_id,
            "target_claim_revision_id": upstream.claim_revision_id,
            "dependency_kind": dependency_kind.value,
        }
        return cls(
            dependency_id=stable_content_id("dep", payload),
            downstream=downstream,
            upstream=upstream,
            downstream_spans=canonical_spans,
            downstream_claim_revisions=canonical_claims,
            dependency_kind=dependency_kind,
            rationale=canonical_rationale,
            confidence=confidence,
        )


class DependencyRegistry(_StrictFrozenModel):
    """One current binding aggregate per semantic dependency identity."""

    assessments: tuple[DependencyAssessment, ...]

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        ids = [assessment.dependency_id for assessment in self.assessments]
        if len(ids) != len(set(ids)):
            raise ValueError("dependency registry requires one binding per dependency_id")
        if ids != sorted(ids):
            raise ValueError("dependency assessments must use canonical dependency_id order")
        revisions = tuple(
            revision
            for assessment in self.assessments
            for revision in (assessment.upstream, *assessment.downstream_claim_revisions)
        )
        _require_consistent_claim_bindings(revisions, context="dependency registry")
        _require_consistent_document_bindings(
            tuple(assessment.downstream for assessment in self.assessments)
            + tuple(revision.document for revision in revisions),
            context="dependency registry",
        )
        return self

    @classmethod
    def create(cls, assessments: tuple[DependencyAssessment, ...]) -> Self:
        by_id: dict[str, DependencyAssessment] = {}
        for assessment in assessments:
            previous = by_id.get(assessment.dependency_id)
            if previous is None:
                by_id[assessment.dependency_id] = assessment
            elif previous != assessment:
                raise ValueError("semantic dependency has multiple non-identical bindings")
        return cls(assessments=tuple(by_id[key] for key in sorted(by_id)))

    def get(self, dependency_id: str) -> DependencyAssessment:
        for assessment in self.assessments:
            if assessment.dependency_id == dependency_id:
                return assessment
        raise KeyError(dependency_id)

    def replace_binding(
        self,
        *,
        expected: DependencyAssessment,
        replacement: DependencyAssessment,
    ) -> DependencyRegistry:
        if expected.dependency_id != replacement.dependency_id:
            raise ValueError("replacement must preserve semantic dependency identity")
        current = self.get(expected.dependency_id)
        if current != expected:
            raise ValueError("dependency binding changed since the expected snapshot")
        if (
            replacement.downstream != expected.downstream
            or replacement.upstream != expected.upstream
        ):
            raise ValueError(
                "dependency replacement must preserve exact semantic endpoint bindings"
            )
        return DependencyRegistry(
            assessments=tuple(
                replacement if item.dependency_id == expected.dependency_id else item
                for item in self.assessments
            )
        )


class RelationGraph(_StrictFrozenModel):
    """Exactly one current assessment per pair plus acyclic supersession."""

    assessments: tuple[RelationAssessment, ...]

    @model_validator(mode="after")
    def _graph_integrity(self) -> Self:
        embedded_revisions = tuple(
            revision
            for assessment in self.assessments
            for revision in assessment.pair.claim_revisions
        )
        _require_consistent_claim_bindings(
            embedded_revisions,
            context="relation graph",
        )
        pair_ids = [item.pair.pair_id for item in self.assessments]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("relation graph requires exactly one current assessment per pair_id")
        if pair_ids != sorted(pair_ids):
            raise ValueError("relation assessments must use canonical pair_id order")
        relation_ids = [item.relation_id for item in self.assessments if item.relation_id]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("persisted relation IDs must be unique")

        edges: dict[str, set[str]] = {}
        for assessment in self.assessments:
            if assessment.relation_type != PersistedRelationType.SUPERSEDES:
                continue
            assert assessment.endpoint_ids is not None
            newer, older = assessment.endpoint_ids
            edges.setdefault(newer, set()).add(older)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("SUPERSEDES relation graph must be acyclic")
            if node in visited:
                return
            visiting.add(node)
            for target in edges.get(node, set()):
                visit(target)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)
        return self

    @classmethod
    def create(cls, assessments: tuple[RelationAssessment, ...]) -> Self:
        by_pair: dict[str, RelationAssessment] = {}
        for assessment in assessments:
            pair_id = assessment.pair.pair_id
            previous = by_pair.get(pair_id)
            if previous is None:
                by_pair[pair_id] = assessment
            elif previous != assessment:
                raise ValueError("claim pair has conflicting current assessments")
        return cls(assessments=tuple(by_pair[key] for key in sorted(by_pair)))


class DocumentReplacementAssessment(_StrictFrozenModel):
    """Reviewed document-version replacement, separate from claim relations."""

    identity_namespace: Literal["mastervault.document-replacement.v1"] = (
        DOCUMENT_REPLACEMENT_NAMESPACE
    )
    relation_id: str = Field(pattern=CONTENT_ID_PATTERNS["rel"])
    relation_type: Literal[PersistedRelationType.SUPERSEDES] = PersistedRelationType.SUPERSEDES
    newer_document: DocumentVersionMetadata
    older_document: DocumentVersionMetadata
    status: TemporalConstraintStatus
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _require_normalized_text(value, label="rationale")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.identity_namespace,
            "relation_type": self.relation_type.value,
            "endpoint_ids": [
                self.newer_document.document_version_id,
                self.older_document.document_version_id,
            ],
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        _require_consistent_document_bindings(
            (self.newer_document, self.older_document),
            context="document replacement assessment",
        )
        if self.newer_document.document_version_id == self.older_document.document_version_id:
            raise ValueError("document replacement requires distinct document versions")
        if self.newer_document.document_family != self.older_document.document_family:
            raise ValueError("document replacement requires one document family")
        if (
            self.newer_document.declared_effective_from
            <= self.older_document.declared_effective_from
        ):
            raise ValueError("document replacement must be directed from newer to older")
        if self.relation_id != stable_content_id("rel", self._identity_payload()):
            raise ValueError("relation_id does not match document replacement endpoints")
        return self

    @classmethod
    def create(
        cls,
        *,
        newer_document: DocumentVersionMetadata,
        older_document: DocumentVersionMetadata,
        status: TemporalConstraintStatus,
        rationale: str,
        confidence: float,
    ) -> Self:
        canonical_rationale = _canonical_semantic_input(rationale, label="rationale")
        payload = {
            "namespace": DOCUMENT_REPLACEMENT_NAMESPACE,
            "relation_type": PersistedRelationType.SUPERSEDES.value,
            "endpoint_ids": [
                newer_document.document_version_id,
                older_document.document_version_id,
            ],
        }
        return cls(
            relation_id=stable_content_id("rel", payload),
            newer_document=newer_document,
            older_document=older_document,
            status=status,
            rationale=canonical_rationale,
            confidence=confidence,
        )

    def with_status(
        self,
        status: TemporalConstraintStatus,
    ) -> DocumentReplacementAssessment:
        return DocumentReplacementAssessment.model_validate(
            {**self.model_dump(mode="json"), "status": status}
        )


class DocumentReplacementSet(_StrictFrozenModel):
    """One current reviewed document-replacement row per stable relation ID."""

    assessments: tuple[DocumentReplacementAssessment, ...]

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        ids = [assessment.relation_id for assessment in self.assessments]
        if len(ids) != len(set(ids)):
            raise ValueError("document replacements require one current row per relation_id")
        if ids != sorted(ids):
            raise ValueError("document replacements must use canonical relation_id order")
        _require_consistent_document_bindings(
            tuple(
                document
                for assessment in self.assessments
                for document in (assessment.newer_document, assessment.older_document)
            ),
            context="document replacement set",
        )

        edges: dict[str, set[str]] = {}
        for assessment in self.assessments:
            edges.setdefault(
                assessment.newer_document.document_version_id,
                set(),
            ).add(assessment.older_document.document_version_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("document replacement graph must be acyclic")
            if node in visited:
                return
            visiting.add(node)
            for target in edges.get(node, set()):
                visit(target)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)
        return self

    @classmethod
    def create(
        cls,
        assessments: tuple[DocumentReplacementAssessment, ...],
    ) -> Self:
        by_id: dict[str, DocumentReplacementAssessment] = {}
        for assessment in assessments:
            previous = by_id.get(assessment.relation_id)
            if previous is None:
                by_id[assessment.relation_id] = assessment
            elif previous != assessment:
                raise ValueError("document replacement has conflicting current rows")
        return cls(assessments=tuple(by_id[key] for key in sorted(by_id)))

    def get(self, relation_id: str) -> DocumentReplacementAssessment:
        for assessment in self.assessments:
            if assessment.relation_id == relation_id:
                return assessment
        raise KeyError(relation_id)

    def transition_status(
        self,
        relation_id: str,
        *,
        expected_status: TemporalConstraintStatus,
        new_status: TemporalConstraintStatus,
    ) -> DocumentReplacementSet:
        current = self.get(relation_id)
        if current.status != expected_status:
            raise ValueError("document replacement status changed since the expected snapshot")
        replacement = current.with_status(new_status)
        return DocumentReplacementSet(
            assessments=tuple(
                replacement if item.relation_id == relation_id else item
                for item in self.assessments
            )
        )


class TemporalTarget(_StrictFrozenModel):
    kind: TemporalTargetKind
    target_id: str

    @model_validator(mode="after")
    def _target_pattern(self) -> Self:
        pattern = (
            CONTENT_ID_PATTERNS["docv"]
            if self.kind == TemporalTargetKind.DOCUMENT_VERSION
            else CONTENT_ID_PATTERNS["claimrev"]
        )
        if re.fullmatch(pattern, self.target_id) is None:
            raise ValueError(f"{self.kind.value} target has the wrong stable-ID prefix")
        return self


class TemporalConstraint(_StrictFrozenModel):
    """Proposed or reviewed half-open temporal closure derived from relations."""

    identity_namespace: Literal["mastervault.temporal-constraint.v1"] = (
        TEMPORAL_CONSTRAINT_NAMESPACE
    )
    resolver_version: Literal["temporal-resolution-v1"] = "temporal-resolution-v1"
    constraint_id: str = Field(pattern=CONTENT_ID_PATTERNS["tempc"])
    target: TemporalTarget
    inferred_valid_to_exclusive: date
    basis_relation_ids: tuple[str, ...] = Field(min_length=1)
    status: TemporalConstraintStatus
    rationale: str = Field(min_length=1)

    @field_validator("basis_relation_ids")
    @classmethod
    def _basis_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("basis_relation_ids must be sorted and unique")
        if any(re.fullmatch(CONTENT_ID_PATTERNS["rel"], value) is None for value in values):
            raise ValueError("basis_relation_ids must contain stable relation IDs")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _require_normalized_text(value, label="rationale")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "namespace": self.identity_namespace,
            "resolver_version": self.resolver_version,
            "target": self.target.model_dump(mode="json"),
            "inferred_valid_to_exclusive": self.inferred_valid_to_exclusive.isoformat(),
            "basis_relation_ids": list(self.basis_relation_ids),
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.constraint_id != stable_content_id("tempc", self._identity_payload()):
            raise ValueError("constraint_id does not match temporal constraint identity")
        return self

    @classmethod
    def from_supersession(
        cls,
        assessment: RelationAssessment,
        *,
        status: TemporalConstraintStatus,
        rationale: str,
    ) -> Self:
        canonical_rationale = _canonical_semantic_input(rationale, label="rationale")
        if assessment.relation_type != PersistedRelationType.SUPERSEDES:
            raise ValueError("temporal supersession constraint requires a SUPERSEDES relation")
        assert assessment.endpoint_ids is not None and assessment.relation_id is not None
        newer = assessment.pair.revision(assessment.endpoint_ids[0])
        older = assessment.pair.revision(assessment.endpoint_ids[1])
        inferred_valid_to_exclusive = newer.declared_effective_from
        target = TemporalTarget(
            kind=TemporalTargetKind.CLAIM_REVISION,
            target_id=older.claim_revision_id,
        )
        basis = (assessment.relation_id,)
        payload = {
            "namespace": TEMPORAL_CONSTRAINT_NAMESPACE,
            "resolver_version": "temporal-resolution-v1",
            "target": target.model_dump(mode="json"),
            "inferred_valid_to_exclusive": inferred_valid_to_exclusive.isoformat(),
            "basis_relation_ids": list(basis),
        }
        return cls(
            constraint_id=stable_content_id("tempc", payload),
            target=target,
            inferred_valid_to_exclusive=inferred_valid_to_exclusive,
            basis_relation_ids=basis,
            status=status,
            rationale=canonical_rationale,
        )

    @classmethod
    def from_document_replacement(
        cls,
        assessment: DocumentReplacementAssessment,
        *,
        status: TemporalConstraintStatus,
        rationale: str,
    ) -> Self:
        canonical_rationale = _canonical_semantic_input(rationale, label="rationale")
        if assessment.status != TemporalConstraintStatus.ACCEPTED:
            raise ValueError("document temporal constraints require an accepted replacement")
        target = TemporalTarget(
            kind=TemporalTargetKind.DOCUMENT_VERSION,
            target_id=assessment.older_document.document_version_id,
        )
        basis = (assessment.relation_id,)
        bound = assessment.newer_document.declared_effective_from
        payload = {
            "namespace": TEMPORAL_CONSTRAINT_NAMESPACE,
            "resolver_version": "temporal-resolution-v1",
            "target": target.model_dump(mode="json"),
            "inferred_valid_to_exclusive": bound.isoformat(),
            "basis_relation_ids": list(basis),
        }
        return cls(
            constraint_id=stable_content_id("tempc", payload),
            target=target,
            inferred_valid_to_exclusive=bound,
            basis_relation_ids=basis,
            status=status,
            rationale=canonical_rationale,
        )

    def with_status(self, status: TemporalConstraintStatus) -> TemporalConstraint:
        return TemporalConstraint.model_validate({**self.model_dump(mode="json"), "status": status})


class TemporalConstraintSet(_StrictFrozenModel):
    """Current constraint rows; exact replay is idempotently collapsed."""

    constraints: tuple[TemporalConstraint, ...]

    @model_validator(mode="after")
    def _unique_constraints(self) -> Self:
        ids = [constraint.constraint_id for constraint in self.constraints]
        if len(ids) != len(set(ids)):
            raise ValueError("constraint set requires one current row per constraint_id")
        if ids != sorted(ids):
            raise ValueError("constraints must use canonical constraint_id order")
        return self

    @classmethod
    def create(cls, constraints: tuple[TemporalConstraint, ...]) -> Self:
        by_id: dict[str, TemporalConstraint] = {}
        for constraint in constraints:
            previous = by_id.get(constraint.constraint_id)
            if previous is None:
                by_id[constraint.constraint_id] = constraint
            elif previous != constraint:
                raise ValueError("constraint ID has conflicting current rows")
        return cls(constraints=tuple(by_id[key] for key in sorted(by_id)))

    def transition_status(
        self,
        constraint_id: str,
        *,
        expected_status: TemporalConstraintStatus,
        new_status: TemporalConstraintStatus,
    ) -> TemporalConstraintSet:
        current = next(
            (item for item in self.constraints if item.constraint_id == constraint_id),
            None,
        )
        if current is None:
            raise KeyError(constraint_id)
        if current.status != expected_status:
            raise ValueError("temporal constraint status changed since the expected snapshot")
        replacement = current.with_status(new_status)
        return TemporalConstraintSet(
            constraints=tuple(
                replacement if item.constraint_id == constraint_id else item
                for item in self.constraints
            )
        )


class ValidatedTemporalConstraintSet(_StrictFrozenModel):
    """Temporal rows whose accepted bases exist in the supplied current graphs."""

    constraints: TemporalConstraintSet
    relation_graph: RelationGraph
    document_replacements: DocumentReplacementSet

    @model_validator(mode="after")
    def _accepted_bases_are_current_and_exact(self) -> Self:
        relation_revisions = tuple(
            revision
            for assessment in self.relation_graph.assessments
            for revision in assessment.pair.claim_revisions
        )
        replacement_documents = tuple(
            document
            for assessment in self.document_replacements.assessments
            for document in (assessment.newer_document, assessment.older_document)
        )
        _require_consistent_document_bindings(
            tuple(revision.document for revision in relation_revisions) + replacement_documents,
            context="validated temporal constraint set",
        )
        claim_relations = {
            assessment.relation_id: assessment
            for assessment in self.relation_graph.assessments
            if assessment.relation_id is not None
        }
        document_relations = {
            assessment.relation_id: assessment
            for assessment in self.document_replacements.assessments
        }
        for constraint in self.constraints.constraints:
            if constraint.status != TemporalConstraintStatus.ACCEPTED:
                continue
            for basis_id in constraint.basis_relation_ids:
                if constraint.target.kind == TemporalTargetKind.CLAIM_REVISION:
                    claim_assessment = claim_relations.get(basis_id)
                    if claim_assessment is None:
                        raise ValueError(
                            "accepted claim temporal constraint basis is absent from current graph"
                        )
                    if claim_assessment.relation_type != PersistedRelationType.SUPERSEDES:
                        raise ValueError(
                            "accepted claim temporal constraint requires SUPERSEDES bases"
                        )
                    assert claim_assessment.endpoint_ids is not None
                    newer_id, older_id = claim_assessment.endpoint_ids
                    newer = claim_assessment.pair.revision(newer_id)
                    if older_id != constraint.target.target_id:
                        raise ValueError(
                            "accepted claim temporal constraint targets the wrong older endpoint"
                        )
                    if newer.declared_effective_from != constraint.inferred_valid_to_exclusive:
                        raise ValueError(
                            "accepted claim temporal constraint has the wrong inferred bound"
                        )
                else:
                    document_assessment = document_relations.get(basis_id)
                    if document_assessment is None:
                        raise ValueError(
                            "accepted document temporal constraint basis is absent from current set"
                        )
                    if document_assessment.status != TemporalConstraintStatus.ACCEPTED:
                        raise ValueError(
                            "accepted document temporal constraint requires an accepted replacement"
                        )
                    if (
                        document_assessment.older_document.document_version_id
                        != constraint.target.target_id
                    ):
                        raise ValueError(
                            "accepted document temporal constraint targets the wrong older endpoint"
                        )
                    if (
                        document_assessment.newer_document.declared_effective_from
                        != constraint.inferred_valid_to_exclusive
                    ):
                        raise ValueError(
                            "accepted document temporal constraint has the wrong inferred bound"
                        )
        return self

    @classmethod
    def create(
        cls,
        *,
        constraints: TemporalConstraintSet,
        relation_graph: RelationGraph,
        document_replacements: DocumentReplacementSet,
    ) -> Self:
        return cls(
            constraints=constraints,
            relation_graph=relation_graph,
            document_replacements=document_replacements,
        )


class ChangeControlAggregate(_StrictFrozenModel):
    """One closed, atomically persisted change-control state.

    Registries own the canonical document and claim bindings. Every graph view
    must reference those exact objects so a binding correction cannot update a
    root row while leaving an embedded snapshot stale.
    """

    schema_version: Literal[1] = 1
    aggregate_id: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
    documents: DocumentVersionRegistry
    claims: ClaimRevisionRegistry
    relation_graph: RelationGraph
    dependencies: DependencyRegistry
    document_replacements: DocumentReplacementSet
    temporal_constraints: TemporalConstraintSet

    @field_validator("aggregate_id")
    @classmethod
    def _aggregate_id(cls, value: str) -> str:
        return _require_normalized_key(value, label="aggregate_id")

    @model_validator(mode="after")
    def _closed_binding_graph(self) -> Self:
        documents = {
            document.document_version_id: document for document in self.documents.documents
        }
        claims = {revision.claim_revision_id: revision for revision in self.claims.revisions}

        def require_document(document: DocumentVersionMetadata, *, context: str) -> None:
            root = documents.get(document.document_version_id)
            if root is None:
                raise ValueError(
                    f"{context} references absent document root {document.document_version_id}"
                )
            if root != document:
                raise ValueError(
                    f"{context} has a binding that differs from document root "
                    f"{document.document_version_id}"
                )

        def require_claim(revision: VersionedClaimRevision, *, context: str) -> None:
            root = claims.get(revision.claim_revision_id)
            if root is None:
                raise ValueError(
                    f"{context} references absent claim root {revision.claim_revision_id}"
                )
            if root != revision:
                raise ValueError(
                    f"{context} has a binding that differs from claim root "
                    f"{revision.claim_revision_id}"
                )
            require_document(revision.document, context=context)

        for revision in self.claims.revisions:
            require_document(revision.document, context="claim registry")
        for relation_assessment in self.relation_graph.assessments:
            for revision in relation_assessment.pair.claim_revisions:
                require_claim(revision, context="relation graph")
        for dependency_assessment in self.dependencies.assessments:
            require_document(dependency_assessment.downstream, context="dependency registry")
            require_claim(dependency_assessment.upstream, context="dependency registry")
            for revision in dependency_assessment.downstream_claim_revisions:
                require_claim(revision, context="dependency registry")
            for span in dependency_assessment.downstream_spans:
                span_document = documents.get(span.document_version_id)
                if span_document is None:
                    raise ValueError(
                        "dependency span references absent document root "
                        f"{span.document_version_id}"
                    )
                if span_document != dependency_assessment.downstream:
                    raise ValueError(
                        "dependency span document root differs from its downstream binding"
                    )
        for replacement_assessment in self.document_replacements.assessments:
            require_document(
                replacement_assessment.newer_document,
                context="document replacement set",
            )
            require_document(
                replacement_assessment.older_document,
                context="document replacement set",
            )
        for constraint in self.temporal_constraints.constraints:
            if constraint.target.kind == TemporalTargetKind.DOCUMENT_VERSION:
                if constraint.target.target_id not in documents:
                    raise ValueError(
                        "temporal constraint references absent document target "
                        f"{constraint.target.target_id}"
                    )
            elif constraint.target.target_id not in claims:
                raise ValueError(
                    "temporal constraint references absent claim target "
                    f"{constraint.target.target_id}"
                )

        ValidatedTemporalConstraintSet.create(
            constraints=self.temporal_constraints,
            relation_graph=self.relation_graph,
            document_replacements=self.document_replacements,
        )
        return self

    @classmethod
    def create(
        cls,
        *,
        aggregate_id: str,
        documents: DocumentVersionRegistry,
        claims: ClaimRevisionRegistry,
        relation_graph: RelationGraph,
        dependencies: DependencyRegistry,
        document_replacements: DocumentReplacementSet,
        temporal_constraints: TemporalConstraintSet,
    ) -> Self:
        return cls(
            aggregate_id=normalize_logical_key(aggregate_id),
            documents=documents,
            claims=claims,
            relation_graph=relation_graph,
            dependencies=dependencies,
            document_replacements=document_replacements,
            temporal_constraints=temporal_constraints,
        )

    def validated_temporal_constraints(self) -> ValidatedTemporalConstraintSet:
        return ValidatedTemporalConstraintSet.create(
            constraints=self.temporal_constraints,
            relation_graph=self.relation_graph,
            document_replacements=self.document_replacements,
        )


def aggregate_sha256(aggregate: ChangeControlAggregate) -> str:
    """Canonical full-SHA digest used by the aggregate CAS store."""
    validated = ChangeControlAggregate.model_validate(aggregate.model_dump(mode="json"))
    return hashlib.sha256(canonical_json_bytes(validated.model_dump(mode="json"))).hexdigest()


def _revalidate_temporal_constraints(
    constraints: ValidatedTemporalConstraintSet,
) -> ValidatedTemporalConstraintSet:
    """Defend resolver entry points from unchecked ``model_copy`` mutations."""
    return ValidatedTemporalConstraintSet.model_validate(constraints.model_dump(mode="json"))


class TemporalResolution(_StrictFrozenModel):
    """Derived half-open temporal projection; never canonical source metadata."""

    resolver_version: Literal["temporal-resolution-v1"] = "temporal-resolution-v1"
    target: TemporalTarget
    as_of: date
    state: TemporalState
    valid_from_inclusive: date
    valid_to_exclusive: date | None = None
    applied_constraint_ids: tuple[str, ...] = ()
    basis_relation_ids: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @field_validator("applied_constraint_ids")
    @classmethod
    def _applied_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("applied_constraint_ids must be sorted and unique")
        if any(re.fullmatch(CONTENT_ID_PATTERNS["tempc"], value) is None for value in values):
            raise ValueError("applied_constraint_ids must contain temporal constraint IDs")
        return values

    @field_validator("basis_relation_ids")
    @classmethod
    def _resolution_basis_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("basis_relation_ids must be sorted and unique")
        if any(re.fullmatch(CONTENT_ID_PATTERNS["rel"], value) is None for value in values):
            raise ValueError("basis_relation_ids must contain relation IDs")
        return values

    @field_validator("conflicts")
    @classmethod
    def _conflict_messages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("conflicts must be sorted and unique")
        for value in values:
            _require_normalized_text(value, label="temporal conflict")
        return values

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.state == TemporalState.UNRESOLVED) != bool(self.conflicts):
            raise ValueError("only unresolved temporal projections carry conflicts")
        if bool(self.applied_constraint_ids) != bool(self.basis_relation_ids):
            raise ValueError("applied constraints and basis relations must be present together")
        if (
            self.valid_to_exclusive is not None
            and self.valid_to_exclusive <= self.valid_from_inclusive
        ):
            raise ValueError("valid_to_exclusive must follow valid_from_inclusive")
        if self.state == TemporalState.UNRESOLVED:
            if self.valid_to_exclusive is not None:
                raise ValueError("unresolved temporal projections cannot choose a valid-to bound")
            return self
        if self.state == TemporalState.FUTURE:
            if self.as_of >= self.valid_from_inclusive:
                raise ValueError("future temporal state requires as_of before valid-from")
        elif self.state == TemporalState.CURRENT:
            if self.as_of < self.valid_from_inclusive or (
                self.valid_to_exclusive is not None and self.as_of >= self.valid_to_exclusive
            ):
                raise ValueError(
                    "current temporal state requires as_of inside the half-open interval"
                )
        else:
            if self.valid_to_exclusive is None or self.as_of < self.valid_to_exclusive:
                raise ValueError("closed temporal state requires as_of at or after valid-to")
            if self.state == TemporalState.HISTORICAL and not self.applied_constraint_ids:
                raise ValueError("historical state requires an accepted inferred constraint")
            if self.state == TemporalState.EXPIRED and self.applied_constraint_ids:
                raise ValueError("expired state is reserved for source-declared closure")
        return self


def _resolve_temporality(
    *,
    target: TemporalTarget,
    declared_effective_from: date,
    declared_effective_to: date | None,
    constraints: TemporalConstraintSet,
    as_of: date,
) -> TemporalResolution:
    relevant = tuple(
        constraint
        for constraint in constraints.constraints
        if constraint.target == target and constraint.status == TemporalConstraintStatus.ACCEPTED
    )
    accepted_bounds = {constraint.inferred_valid_to_exclusive for constraint in relevant}
    declared_to_exclusive = declared_effective_to
    conflicts: set[str] = set()
    if len(accepted_bounds) > 1:
        conflicts.add("accepted constraints infer different valid-to bounds")
    inferred_to_exclusive = next(iter(accepted_bounds)) if len(accepted_bounds) == 1 else None
    if inferred_to_exclusive is not None and inferred_to_exclusive <= declared_effective_from:
        conflicts.add("inferred valid-to does not follow declared effective-from")
    if (
        inferred_to_exclusive is not None
        and declared_to_exclusive is not None
        and inferred_to_exclusive != declared_to_exclusive
    ):
        conflicts.add("declared and inferred valid-to bounds disagree")

    applied_ids = tuple(sorted(constraint.constraint_id for constraint in relevant))
    basis_ids = tuple(
        sorted(
            {
                relation_id
                for constraint in relevant
                for relation_id in constraint.basis_relation_ids
            }
        )
    )
    if conflicts:
        return TemporalResolution(
            target=target,
            as_of=as_of,
            state=TemporalState.UNRESOLVED,
            valid_from_inclusive=declared_effective_from,
            valid_to_exclusive=None,
            applied_constraint_ids=applied_ids,
            basis_relation_ids=basis_ids,
            conflicts=tuple(sorted(conflicts)),
        )

    valid_to_exclusive = inferred_to_exclusive or declared_to_exclusive
    if as_of < declared_effective_from:
        state = TemporalState.FUTURE
    elif valid_to_exclusive is not None and as_of >= valid_to_exclusive:
        state = TemporalState.HISTORICAL if relevant else TemporalState.EXPIRED
    else:
        state = TemporalState.CURRENT
    return TemporalResolution(
        target=target,
        as_of=as_of,
        state=state,
        valid_from_inclusive=declared_effective_from,
        valid_to_exclusive=valid_to_exclusive,
        applied_constraint_ids=applied_ids,
        basis_relation_ids=basis_ids,
    )


def resolve_document_temporality(
    document: DocumentVersionMetadata,
    constraints: ValidatedTemporalConstraintSet,
    *,
    as_of: date,
) -> TemporalResolution:
    """Project source-declared and accepted inferred document bounds."""
    validated = _revalidate_temporal_constraints(constraints)
    for constraint in validated.constraints.constraints:
        if (
            constraint.status != TemporalConstraintStatus.ACCEPTED
            or constraint.target.kind != TemporalTargetKind.DOCUMENT_VERSION
            or constraint.target.target_id != document.document_version_id
        ):
            continue
        for basis_id in constraint.basis_relation_ids:
            if validated.document_replacements.get(basis_id).older_document != document:
                raise ValueError(
                    "resolution document binding differs from its accepted replacement basis"
                )
    return _resolve_temporality(
        target=TemporalTarget(
            kind=TemporalTargetKind.DOCUMENT_VERSION,
            target_id=document.document_version_id,
        ),
        declared_effective_from=document.declared_effective_from,
        declared_effective_to=document.declared_effective_to,
        constraints=validated.constraints,
        as_of=as_of,
    )


def resolve_claim_temporality(
    revision: VersionedClaimRevision,
    constraints: ValidatedTemporalConstraintSet,
    *,
    as_of: date,
) -> TemporalResolution:
    """Project source-declared and accepted inferred claim-revision bounds."""
    validated = _revalidate_temporal_constraints(constraints)
    relations = {
        assessment.relation_id: assessment
        for assessment in validated.relation_graph.assessments
        if assessment.relation_id is not None
    }
    for constraint in validated.constraints.constraints:
        if (
            constraint.status != TemporalConstraintStatus.ACCEPTED
            or constraint.target.kind != TemporalTargetKind.CLAIM_REVISION
            or constraint.target.target_id != revision.claim_revision_id
        ):
            continue
        for basis_id in constraint.basis_relation_ids:
            assessment = relations[basis_id]
            assert assessment.endpoint_ids is not None
            if assessment.pair.revision(assessment.endpoint_ids[1]) != revision:
                raise ValueError(
                    "resolution claim binding differs from its accepted relation basis"
                )
    return _resolve_temporality(
        target=TemporalTarget(
            kind=TemporalTargetKind.CLAIM_REVISION,
            target_id=revision.claim_revision_id,
        ),
        declared_effective_from=revision.declared_effective_from,
        declared_effective_to=revision.declared_effective_to,
        constraints=validated.constraints,
        as_of=as_of,
    )
