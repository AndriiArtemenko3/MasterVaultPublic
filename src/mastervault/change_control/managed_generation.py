"""Pure contracts for one reviewed managed generation and its effect receipts.

The models in this module deliberately contain no filesystem or SQLite code.
They bind an exact managed decision to a complete generation projection, then
bind the create-only publication, isolated SQLite index, and authority CAS
evidence produced by the effect service.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.dependency_analysis import SourceNoteInventory
from mastervault.change_control.managed_review import (
    AuthorityRevisionBinding,
    GenerationPublicationBinding,
    ManagedGenerationManifestBindingV2,
    ManagedRevisionDecisionRecord,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRunBindingV2,
    PublicationKind,
)
from mastervault.change_control.models import (
    SHA256_PATTERN,
    DocumentVersionMetadata,
    TemporalState,
    ValidatedTemporalConstraintSet,
    canonical_json_bytes,
    normalize_logical_key,
    resolve_document_temporality,
)

MAX_GENERATION_ENTRIES_V1 = 64
MAX_GENERATION_PUBLICATIONS_V1 = 32
MAX_GENERATION_CANONICAL_BYTES_V1 = 2 * 1024 * 1024
MAX_GENERATION_PATH_BYTES_V1 = 1024
MAX_INDEX_FILE_BYTES_V1 = 2 * 1024 * 1024 * 1024
MAX_INDEX_COUNTS_V1 = 1_000_000
INDEX_COUNT_KEYS_V1 = (
    "chunks",
    "claim_affects",
    "claims",
    "claims_fts",
    "documents",
    "documents_fts",
    "embeddings",
    "schema_migrations",
    "structural_records",
    "structural_records_fts",
    "vec_records",
    "wiki_aliases",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _content_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{_sha256(payload)}"


def _canonical_time(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("timestamp must be canonical UTC with second precision")
    return value


def _canonical_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_GENERATION_PATH_BYTES_V1:
        raise ValueError(f"{label} exceeds its UTF-8 byte limit")
    candidate = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or candidate.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in candidate.parts
        or any(part.startswith(".") for part in candidate.parts)
        or candidate.as_posix() != value
    ):
        raise ValueError(f"{label} must be one canonical relative POSIX path")
    return value


class GenerationSourceKind(StrEnum):
    REVIEWED = "reviewed-source-note"
    GOVERNING = "governing-source-adoption"
    PUBLISHED = "managed-publication"


class ReviewedSourceBinding(_StrictFrozenModel):
    source_kind: Literal[GenerationSourceKind.REVIEWED] = GenerationSourceKind.REVIEWED
    reviewed_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    source_note_snapshot_id: str = Field(pattern=r"^depsource:[0-9a-f]{64}$")
    source_note_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.source_note_snapshot_id != f"depsource:{self.source_note_snapshot_sha256}":
            raise ValueError("reviewed SourceNote snapshot ID differs from its SHA")
        return self


class GoverningSourceBinding(_StrictFrozenModel):
    source_kind: Literal[GenerationSourceKind.GOVERNING] = GenerationSourceKind.GOVERNING
    adoption_id: str = Field(pattern=r"^mgoverningsource:[0-9a-f]{64}$")
    adoption_sha256: str = Field(pattern=SHA256_PATTERN)
    source_note_artifact_id: str = Field(pattern=r"^martifact:[0-9a-f]{64}$")
    source_note_snapshot_id: str = Field(pattern=r"^depsource:[0-9a-f]{64}$")
    source_note_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.adoption_id != f"mgoverningsource:{self.adoption_sha256}":
            raise ValueError("governing adoption ID differs from its SHA")
        if self.source_note_snapshot_id != f"depsource:{self.source_note_snapshot_sha256}":
            raise ValueError("governing SourceNote snapshot ID differs from its SHA")
        return self


class PublishedSourceBinding(_StrictFrozenModel):
    source_kind: Literal[GenerationSourceKind.PUBLISHED] = GenerationSourceKind.PUBLISHED
    target_key: str
    predecessor_document_version_id: str = Field(pattern=r"^docv:[0-9a-f]{64}$")
    staged_artifact_id: str = Field(pattern=r"^martifact:[0-9a-f]{64}$")
    destination_id: str = Field(pattern=r"^mdestination:[0-9a-f]{64}$")
    destination_path: str

    @field_validator("target_key")
    @classmethod
    def _target(cls, value: str) -> str:
        return normalize_logical_key(value)

    @field_validator("destination_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _canonical_path(value, label="published SourceNote destination")


GenerationSourceBinding = Annotated[
    ReviewedSourceBinding | GoverningSourceBinding | PublishedSourceBinding,
    Field(discriminator="source_kind"),
]


class GenerationSourceNoteEntry(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    entry_id: str = Field(pattern=r"^mgensource:[0-9a-f]{64}$")
    logical_path: str
    document: DocumentVersionMetadata
    source_note_sha256: str = Field(pattern=SHA256_PATTERN)
    source_note_byte_count: int = Field(ge=1, le=MAX_GENERATION_CANONICAL_BYTES_V1)
    temporal_state: TemporalState
    included_in_serving_index: bool
    source: GenerationSourceBinding

    @field_validator("logical_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _canonical_path(value, label="logical SourceNote path")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"entry_id"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.included_in_serving_index != (self.temporal_state == TemporalState.CURRENT):
            raise ValueError("serving inclusion must exactly follow resolved CURRENT state")
        if self.entry_id != _content_id("mgensource", self._payload()):
            raise ValueError("generation SourceNote entry ID differs from its content")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        return cls.model_validate_json(
            canonical_json_bytes({"entry_id": _content_id("mgensource", payload), **payload})
        )


class ResolvedManagedGenerationProjection(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    projection_id: str = Field(pattern=r"^mgenerationprojection:[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=SHA256_PATTERN)
    request_id: str = Field(pattern=r"^mrequest:[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^mdecision:[0-9a-f]{64}$")
    decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_id: str = Field(pattern=r"^mgenerationmanifest:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_id: str = Field(pattern=r"^mgeneration:[0-9a-f]{64}$")
    generation_number: int = Field(ge=1)
    reviewed_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    entries: tuple[GenerationSourceNoteEntry, ...] = Field(
        min_length=1, max_length=MAX_GENERATION_ENTRIES_V1
    )
    serving_entry_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_GENERATION_ENTRIES_V1)
    complete_content_fingerprint: str = Field(pattern=SHA256_PATTERN)
    serving_content_fingerprint: str = Field(pattern=SHA256_PATTERN)

    @field_validator("entries")
    @classmethod
    def _entries(
        cls, values: tuple[GenerationSourceNoteEntry, ...]
    ) -> tuple[GenerationSourceNoteEntry, ...]:
        ordered = tuple(sorted(values, key=lambda item: (item.logical_path, item.entry_id)))
        if values != ordered:
            raise ValueError("generation entries must use canonical logical-path order")
        if len({item.document.document_version_id for item in values}) != len(values):
            raise ValueError("generation document versions must be unique")
        serving_paths = [item.logical_path for item in values if item.included_in_serving_index]
        if len(set(serving_paths)) != len(serving_paths):
            raise ValueError("current serving SourceNote paths must be unique")
        return values

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"projection_id", "projection_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        expected_serving = tuple(
            item.entry_id for item in self.entries if item.included_in_serving_index
        )
        if self.serving_entry_ids != expected_serving:
            raise ValueError("serving entry IDs differ from exact CURRENT projection")
        complete = _sha256(
            {
                "namespace": "mastervault.managed-generation-complete-content.v1",
                "entries": [item.model_dump(mode="json") for item in self.entries],
            }
        )
        serving = _sha256(
            {
                "namespace": "mastervault.managed-generation-serving-content.v1",
                "entries": [
                    item.model_dump(mode="json")
                    for item in self.entries
                    if item.included_in_serving_index
                ],
            }
        )
        if (
            self.complete_content_fingerprint != complete
            or self.serving_content_fingerprint != serving
        ):
            raise ValueError("generation content fingerprints are not reproducible")
        digest = _sha256(self._payload())
        if self.projection_sha256 != digest or self.projection_id != (
            f"mgenerationprojection:{digest}"
        ):
            raise ValueError("generation projection ID/SHA differs from exact content")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > (
            MAX_GENERATION_CANONICAL_BYTES_V1
        ):
            raise ValueError("generation projection exceeds its canonical byte limit")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        entries = tuple(
            sorted(
                kwargs.pop("entries"),
                key=lambda item: (item.logical_path, item.entry_id),
            )
        )
        serving_ids = tuple(item.entry_id for item in entries if item.included_in_serving_index)
        complete = _sha256(
            {
                "namespace": "mastervault.managed-generation-complete-content.v1",
                "entries": [item.model_dump(mode="json") for item in entries],
            }
        )
        serving = _sha256(
            {
                "namespace": "mastervault.managed-generation-serving-content.v1",
                "entries": [
                    item.model_dump(mode="json")
                    for item in entries
                    if item.included_in_serving_index
                ],
            }
        )
        values = {
            "schema_version": 1,
            **kwargs,
            "entries": entries,
            "serving_entry_ids": serving_ids,
            "complete_content_fingerprint": complete,
            "serving_content_fingerprint": serving,
        }
        payload = {
            key: [item.model_dump(mode="json") for item in value] if key == "entries" else value
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "projection_id": f"mgenerationprojection:{digest}",
                    "projection_sha256": digest,
                    **payload,
                }
            )
        )


def approved_revision_plans(
    decision: ManagedRevisionDecisionRecord,
) -> tuple[ManagedRevisionPlan, ...]:
    """Return only exact approved plans; managed EDIT remains out of scope."""

    targets = {item.target_id: item for item in decision.command.bundle.targets}
    plans: list[ManagedRevisionPlan] = []
    for outcome in decision.command.items:
        target = targets[outcome.target_id]
        if outcome.disposition == ManagedRevisionDisposition.APPROVE:
            if not isinstance(target.subject, ManagedRevisionPlan):
                raise ValueError("approved managed target is not a revision plan")
            plans.append(target.subject)
        elif outcome.disposition == ManagedRevisionDisposition.EDIT:
            raise ValueError("managed EDIT execution remains deferred")
    return tuple(sorted(plans, key=lambda item: item.target_key))


def derive_managed_generation_projection(
    *,
    decision: ManagedRevisionDecisionRecord,
    reviewed_inventory: SourceNoteInventory,
    temporal_constraints: ValidatedTemporalConstraintSet,
) -> ResolvedManagedGenerationProjection:
    """Derive the full projection and exact current-only serving subset."""

    command = decision.command
    manifest = command.generation_manifest
    if not isinstance(manifest, ManagedGenerationManifestBindingV2):
        raise ValueError("managed generation projection requires an activating v2 manifest")
    if not manifest.requires_activation:
        raise ValueError("managed generation projection requires an activating decision")
    run = command.bundle.run_binding
    if not isinstance(run, ManagedRunBindingV2):
        raise ValueError("managed generation projection requires an admitted v2 run")
    adoption = run.governing_source_adoption
    if (
        reviewed_inventory.inventory_sha256 != adoption.reviewed_inventory_sha256
        or reviewed_inventory.aggregate_id != adoption.reviewed_head.aggregate_id
        or reviewed_inventory.snapshot_revision != adoption.reviewed_head.revision
        or reviewed_inventory.aggregate_sha256 != adoption.reviewed_head.aggregate_sha256
    ):
        raise ValueError("reviewed SourceNote inventory differs from governing adoption authority")
    as_of = run.analysis_set.analysis_bootstrap.analysis_as_of

    note_by_document = {
        item.document.document_version_id: item for item in reviewed_inventory.notes
    }
    adoption_note = note_by_document.get(adoption.document.document_version_id)
    if adoption_note is None or (
        adoption_note.source_note_path != adoption.source_note_logical_path
        or adoption_note.source_note_sha256 != adoption.source_note_artifact.sha256
        or adoption_note.source_note_utf8_bytes != adoption.source_note_artifact.byte_count
        or adoption_note.snapshot_id != adoption.source_note_snapshot_id
        or adoption_note.snapshot_sha256 != adoption.source_note_snapshot_sha256
    ):
        raise ValueError("reviewed inventory omits the exact governing SourceNote")

    # Temporal state is resolved by the caller and supplied through the
    # authoritative document metadata's declared bounds for the initial pure
    # projection. The service performs the stronger reviewed-aggregate
    # resolution before accepting this result.
    entries: dict[str, GenerationSourceNoteEntry] = {}
    for note in reviewed_inventory.notes:
        state = resolve_document_temporality(
            note.document,
            temporal_constraints,
            as_of=as_of,
        ).state
        if state == TemporalState.UNRESOLVED:
            raise ValueError("unresolved document temporality cannot enter a serving generation")
        source: GenerationSourceBinding
        if note.document.document_version_id == adoption.document.document_version_id:
            source = GoverningSourceBinding(
                adoption_id=adoption.adoption_id,
                adoption_sha256=adoption.adoption_sha256,
                source_note_artifact_id=adoption.source_note_artifact.artifact_id,
                source_note_snapshot_id=note.snapshot_id,
                source_note_snapshot_sha256=note.snapshot_sha256,
            )
        else:
            source = ReviewedSourceBinding(
                reviewed_inventory_sha256=reviewed_inventory.inventory_sha256,
                source_note_snapshot_id=note.snapshot_id,
                source_note_snapshot_sha256=note.snapshot_sha256,
            )
        entries[note.document.document_version_id] = GenerationSourceNoteEntry.create(
            logical_path=note.source_note_path,
            document=note.document,
            source_note_sha256=note.source_note_sha256,
            source_note_byte_count=note.source_note_utf8_bytes,
            temporal_state=state,
            included_in_serving_index=state == TemporalState.CURRENT,
            source=source,
        )

    publications = {
        (item.target_key, item.destination.kind): item for item in manifest.publication_delta
    }
    plans = approved_revision_plans(decision)
    expected_publications = {
        (plan.target_key, kind)
        for plan in plans
        for kind in (PublicationKind.RAW_SOURCE, PublicationKind.SOURCE_NOTE)
    }
    if set(publications) != expected_publications:
        raise ValueError("v2 manifest publication delta differs from approved plans")
    for plan in plans:
        predecessor = entries.get(plan.predecessor.document_version_id)
        if predecessor is None:
            raise ValueError("approved plan predecessor is absent from reviewed inventory")
        note_publication = publications[(plan.target_key, PublicationKind.SOURCE_NOTE)]
        if (
            note_publication.staged_artifact != plan.proposed_note
            or note_publication.destination != plan.note_destination
        ):
            raise ValueError("approved plan SourceNote publication differs from manifest")
        source = PublishedSourceBinding(
            target_key=plan.target_key,
            predecessor_document_version_id=plan.predecessor.document_version_id,
            staged_artifact_id=plan.proposed_note.artifact_id,
            destination_id=plan.note_destination.destination_id,
            destination_path=plan.note_destination.path,
        )
        successor_state = resolve_document_temporality(
            plan.successor,
            temporal_constraints,
            as_of=as_of,
        ).state
        if successor_state == TemporalState.UNRESOLVED:
            raise ValueError("managed successor temporality is unresolved")
        # The approved replacement is itself authority that the predecessor is
        # no longer current once its successor is current. Preserve that exact
        # predecessor as history even when its old metadata had an open end.
        if successor_state == TemporalState.CURRENT and predecessor.included_in_serving_index:
            entries[plan.predecessor.document_version_id] = GenerationSourceNoteEntry.create(
                logical_path=predecessor.logical_path,
                document=predecessor.document,
                source_note_sha256=predecessor.source_note_sha256,
                source_note_byte_count=predecessor.source_note_byte_count,
                temporal_state=TemporalState.HISTORICAL,
                included_in_serving_index=False,
                source=predecessor.source,
            )
        entries[plan.successor.document_version_id] = GenerationSourceNoteEntry.create(
            logical_path=predecessor.logical_path,
            document=plan.successor,
            source_note_sha256=plan.proposed_note.sha256,
            source_note_byte_count=plan.proposed_note.byte_count,
            temporal_state=successor_state,
            included_in_serving_index=successor_state == TemporalState.CURRENT,
            source=source,
        )

    return ResolvedManagedGenerationProjection.create(
        request_id=command.request_record.command.request_id,
        decision_id=command.decision_id,
        decision_record_sha256=decision.record_sha256,
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.manifest_sha256,
        generation_id=manifest.authorized_generation.generation_id,
        generation_number=manifest.generation_number,
        reviewed_inventory_sha256=reviewed_inventory.inventory_sha256,
        entries=tuple(entries.values()),
    )


class ManagedActivationCommand(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    activation_id: str = Field(pattern=r"^mactivation:[0-9a-f]{64}$")
    activation_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    request_id: str = Field(pattern=r"^mrequest:[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^mdecision:[0-9a-f]{64}$")
    decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_id: str = Field(pattern=r"^mgenerationmanifest:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    projection: ResolvedManagedGenerationProjection
    expected_authority: AuthorityRevisionBinding
    generation_repository_id: str = Field(pattern=SHA256_PATTERN)
    embedding_provider: str
    embedding_model_version: str
    embedding_dimensions: int = Field(gt=0, le=65536)

    @field_validator("embedding_provider", "embedding_model_version")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value or value != value.strip() or len(value) > 200:
            raise ValueError("embedding identity must be exact bounded text")
        return value

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"activation_id", "activation_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        projection = self.projection
        if (
            projection.request_id != self.request_id
            or projection.decision_id != self.decision_id
            or projection.decision_record_sha256 != self.decision_record_sha256
            or projection.manifest_id != self.manifest_id
            or projection.manifest_sha256 != self.manifest_sha256
        ):
            raise ValueError("activation command differs from exact projection authority")
        digest = _sha256(self._payload())
        if self.activation_sha256 != digest or self.activation_id != f"mactivation:{digest}":
            raise ValueError("activation command ID/SHA differs from exact input")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "activation_id": f"mactivation:{digest}",
                    "activation_sha256": digest,
                    **payload,
                }
            )
        )


class ManagedActivationIntentRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    record_id: str = Field(pattern=r"^mactivationrecord:[0-9a-f]{64}$")
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    command: ManagedActivationCommand
    created_at: str

    @field_validator("created_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_time(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_id", "record_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        digest = _sha256(self._payload())
        if self.record_sha256 != digest or self.record_id != f"mactivationrecord:{digest}":
            raise ValueError("activation intent record ID/SHA differs from exact content")
        return self

    @classmethod
    def create(cls, *, command: ManagedActivationCommand, created_at: str) -> Self:
        values = {
            "schema_version": 1,
            "command": command,
            "created_at": _canonical_time(created_at),
        }
        payload = {
            "schema_version": 1,
            "command": command.model_dump(mode="json"),
            "created_at": values["created_at"],
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "record_id": f"mactivationrecord:{digest}",
                    "record_sha256": digest,
                    **payload,
                }
            )
        )


class ManagedPublicationEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=r"^mpublication:[0-9a-f]{64}$")
    event_sha256: str = Field(pattern=SHA256_PATTERN)
    activation_id: str = Field(pattern=r"^mactivation:[0-9a-f]{64}$")
    ordinal: int = Field(ge=0, lt=MAX_GENERATION_PUBLICATIONS_V1)
    publication: GenerationPublicationBinding
    repository_relative_path: str
    published_sha256: str = Field(pattern=SHA256_PATTERN)
    published_byte_count: int = Field(ge=1, le=MAX_GENERATION_CANONICAL_BYTES_V1)
    published_at: str

    @field_validator("published_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_time(value)

    @field_validator("repository_relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _canonical_path(value, label="generation publication path")

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"event_id", "event_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        destination = self.publication.destination
        if (
            self.published_sha256 != destination.expected_sha256
            or self.published_byte_count != destination.expected_byte_count
        ):
            raise ValueError("publication event differs from destination receipt")
        digest = _sha256(self._payload())
        if self.event_sha256 != digest or self.event_id != f"mpublication:{digest}":
            raise ValueError("publication event ID/SHA differs from exact evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["published_at"] = _canonical_time(kwargs["published_at"])
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "event_id": f"mpublication:{digest}",
                    "event_sha256": digest,
                    **payload,
                }
            )
        )


class ManagedIndexReadinessReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^mindexreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    activation_id: str = Field(pattern=r"^mactivation:[0-9a-f]{64}$")
    generation_id: str = Field(pattern=r"^mgeneration:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_id: str = Field(pattern=r"^mgenerationprojection:[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=SHA256_PATTERN)
    serving_content_fingerprint: str = Field(pattern=SHA256_PATTERN)
    index_relative_path: str
    index_file_sha256: str = Field(pattern=SHA256_PATTERN)
    index_file_byte_count: int = Field(ge=1, le=MAX_INDEX_FILE_BYTES_V1)
    logical_index_fingerprint: str = Field(pattern=SHA256_PATTERN)
    storage_schema_version: int = Field(gt=0)
    embedding_model_version: str
    embedding_dimensions: int = Field(gt=0, le=65536)
    counts: tuple[tuple[str, int], ...]
    ready_at: str

    @field_validator("ready_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_time(value)

    @field_validator("index_relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _canonical_path(value, label="managed index path")

    @field_validator("counts")
    @classmethod
    def _counts(cls, value: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
        if tuple(key for key, _count in value) != INDEX_COUNT_KEYS_V1:
            raise ValueError("index counts must contain the exact fixed canonical keys")
        if any(
            type(count) is not int or not 0 <= count <= MAX_INDEX_COUNTS_V1 for _key, count in value
        ):
            raise ValueError("index counts contain an invalid value")
        return value

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        expected_path = f"generations/{self.generation_id}/index/mastervault.sqlite3"
        if self.index_relative_path != expected_path:
            raise ValueError("index readiness receipt has a non-canonical generation path")
        digest = _sha256(self._payload())
        if self.receipt_sha256 != digest or self.receipt_id != f"mindexreceipt:{digest}":
            raise ValueError("index readiness receipt ID/SHA differs from exact evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["ready_at"] = _canonical_time(kwargs["ready_at"])
        values = {"schema_version": 1, **kwargs}
        raw_counts = values["counts"]
        if isinstance(raw_counts, dict):
            counts = tuple(sorted(raw_counts.items()))
        else:
            counts = tuple(raw_counts)
        payload = {**values, "counts": counts}
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {"receipt_id": f"mindexreceipt:{digest}", "receipt_sha256": digest, **payload}
            )
        )


class ManagedGenerationActivationReceipt(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^mgenerationactivation:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    activation_id: str = Field(pattern=r"^mactivation:[0-9a-f]{64}$")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    decision_record_sha256: str = Field(pattern=SHA256_PATTERN)
    publication_set_sha256: str = Field(pattern=SHA256_PATTERN)
    publication_count: int = Field(ge=0, le=MAX_GENERATION_PUBLICATIONS_V1)
    index_receipt_id: str = Field(pattern=r"^mindexreceipt:[0-9a-f]{64}$")
    index_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    prior_authority: AuthorityRevisionBinding
    activated_authority: AuthorityRevisionBinding
    activated_at: str

    @field_validator("activated_at")
    @classmethod
    def _time(cls, value: str) -> str:
        return _canonical_time(value)

    def _payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if (
            self.activated_authority.authority_revision
            != self.prior_authority.authority_revision + 1
            or self.activated_authority.active_generation.generation_number
            != self.prior_authority.active_generation.generation_number + 1
        ):
            raise ValueError("activation receipt does not bind an exact successor")
        digest = _sha256(self._payload())
        if self.receipt_sha256 != digest or self.receipt_id != (f"mgenerationactivation:{digest}"):
            raise ValueError("activation receipt ID/SHA differs from exact evidence")
        return self

    @classmethod
    def create(cls, **kwargs: Any) -> Self:
        kwargs["activated_at"] = _canonical_time(kwargs["activated_at"])
        values = {"schema_version": 1, **kwargs}
        payload = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in values.items()
        }
        digest = _sha256(payload)
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    "receipt_id": f"mgenerationactivation:{digest}",
                    "receipt_sha256": digest,
                    **payload,
                }
            )
        )


def publication_set_sha256(events: tuple[ManagedPublicationEvent, ...]) -> str:
    ordered = tuple(sorted(events, key=lambda item: item.ordinal))
    if tuple(item.ordinal for item in ordered) != tuple(range(len(ordered))):
        raise ValueError("publication events must have contiguous canonical ordinals")
    return _sha256(
        {
            "namespace": "mastervault.managed-publication-set.v1",
            "events": [item.model_dump(mode="json") for item in ordered],
        }
    )


__all__ = [
    "GenerationSourceKind",
    "GenerationSourceNoteEntry",
    "GoverningSourceBinding",
    "ManagedActivationCommand",
    "ManagedActivationIntentRecord",
    "ManagedGenerationActivationReceipt",
    "ManagedIndexReadinessReceipt",
    "ManagedPublicationEvent",
    "PublishedSourceBinding",
    "ResolvedManagedGenerationProjection",
    "ReviewedSourceBinding",
    "approved_revision_plans",
    "derive_managed_generation_projection",
    "publication_set_sha256",
]
