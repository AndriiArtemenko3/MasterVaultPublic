"""Create-only durable evidence for generic V2 Markdown admission."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, SupportsIndex

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mastervault.change_control.generic_incoming import (
    GenericChangeMetadataV2,
    GenericExtractionModeV2,
    GenericGroundedClaimV2,
    GenericGroundedExtractionV2,
    GenericIncomingIntegrityError,
    VerifiedGenericIncomingV2,
    render_generic_source_note_v2,
    render_verified_generic_source_note_projection_v2,
)
from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.repository_files import canonical_repo_relative
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2

_ROOT = "generic-incoming/v2"
_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)
_SHA = r"^[0-9a-f]{64}$"
_BUNDLE_ID = r"^generic-bundle-v2:[0-9a-f]{64}$"
_MAX_RECEIPT = 512 * 1024


class GenericIncomingRepositoryError(ValueError):
    """Generic evidence is absent, conflicting, corrupt, or unsafe."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenericAdmissionReceiptV2(_StrictFrozenModel):
    schema_version: Literal[2] = 2
    admission_sha256: str = Field(pattern=_SHA)
    source_sha256: str = Field(pattern=_SHA)
    source_byte_count: int = Field(ge=1, le=64 * 1024)
    source_name: str
    source_locator: str
    metadata: GenericChangeMetadataV2

    @model_validator(mode="after")
    def _identity(self) -> GenericAdmissionReceiptV2:
        canonical_repo_relative(self.source_locator)
        if self.source_locator != f"{_ROOT}/sources/{self.source_sha256}.md":
            raise ValueError("admission source locator is not content-addressed")
        if Path(self.source_name).name != self.source_name or self.source_name.startswith("."):
            raise ValueError("source_name must be one safe leaf name")
        payload = self.model_dump(mode="json", exclude={"admission_sha256"})
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.admission_sha256:
            raise ValueError("admission receipt identity does not bind its canonical payload")
        return self


class GenericSourceReceiptV2(_StrictFrozenModel):
    schema_version: Literal[2] = 2
    source_receipt_sha256: str = Field(pattern=_SHA)
    source_sha256: str = Field(pattern=_SHA)
    source_byte_count: int = Field(ge=1, le=64 * 1024)
    source_locator: str
    source_note_sha256: str = Field(pattern=_SHA)
    source_note_byte_count: int = Field(ge=1)
    source_note_locator: str

    @model_validator(mode="after")
    def _identity(self) -> GenericSourceReceiptV2:
        for locator in (self.source_locator, self.source_note_locator):
            canonical_repo_relative(locator)
        if self.source_locator != f"{_ROOT}/sources/{self.source_sha256}.md":
            raise ValueError("source receipt raw locator is not canonical")
        if self.source_note_locator != f"{_ROOT}/source-notes/{self.source_note_sha256}.md":
            raise ValueError("source receipt SourceNote locator is not canonical")
        payload = self.model_dump(mode="json", exclude={"source_receipt_sha256"})
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.source_receipt_sha256:
            raise ValueError("source receipt identity does not bind its canonical payload")
        return self


class GenericProjectionReceiptV2(_StrictFrozenModel):
    schema_version: Literal[2] = 2
    projection_sha256: str = Field(pattern=_SHA)
    admission_sha256: str = Field(pattern=_SHA)
    source_receipt_sha256: str = Field(pattern=_SHA)
    document_id: str
    document_family: str
    version_label: str
    source_sha256: str = Field(pattern=_SHA)
    claims: tuple[GenericGroundedClaimV2, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _identity(self) -> GenericProjectionReceiptV2:
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.projection_sha256:
            raise ValueError("projection identity does not bind its canonical payload")
        return self


class GenericRecordedInferenceReceiptV2(_StrictFrozenModel):
    """Sanitized exact result; never stores a raw provider/SDK envelope."""

    schema_version: Literal[2] = 2
    inference_sha256: str = Field(pattern=_SHA)
    mode: GenericExtractionModeV2
    source_sha256: str = Field(pattern=_SHA)
    request_sha256: str = Field(pattern=_SHA)
    provider_result_sha256: str = Field(pattern=_SHA)
    provider_contract: GenericGroundedClaimExtractionV2
    claims: tuple[GenericGroundedClaimV2, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _identity(self) -> GenericRecordedInferenceReceiptV2:
        provider_sha = hashlib.sha256(
            canonical_json_bytes(self.provider_contract.model_dump(mode="json"))
        ).hexdigest()
        if self.provider_result_sha256 != provider_sha:
            raise ValueError("provider result SHA differs from sanitized exact contract")
        payload = self.model_dump(mode="json", exclude={"inference_sha256"})
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.inference_sha256:
            raise ValueError("inference receipt identity does not bind its canonical payload")
        return self


class GenericEvidenceBundleReceiptV2(_StrictFrozenModel):
    schema_version: Literal[2] = 2
    bundle_id: str = Field(pattern=_BUNDLE_ID)
    bundle_sha256: str = Field(pattern=_SHA)
    admission_receipt_locator: str
    source_receipt_locator: str
    projection_receipt_locator: str
    inference_receipt_locator: str
    admission_sha256: str = Field(pattern=_SHA)
    source_receipt_sha256: str = Field(pattern=_SHA)
    projection_sha256: str = Field(pattern=_SHA)
    inference_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def _identity(self) -> GenericEvidenceBundleReceiptV2:
        for locator in (
            self.admission_receipt_locator,
            self.source_receipt_locator,
            self.projection_receipt_locator,
            self.inference_receipt_locator,
        ):
            canonical_repo_relative(locator)
        payload = self.model_dump(mode="json", exclude={"bundle_id", "bundle_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.bundle_sha256 != digest or self.bundle_id != f"generic-bundle-v2:{digest}":
            raise ValueError("bundle identity does not bind its canonical payload")
        return self


@dataclass(frozen=True, eq=False)
class RepositoryVerifiedGenericEvidenceV2:
    """Process-local proof that a complete manifest-last bundle was reopened."""

    bundle_id: str
    bundle_sha256: str
    repository_id: str = field(repr=False)
    _token: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("generic repository capabilities are repository-created only")

    def __reduce__(self) -> Any:
        raise TypeError("generic repository capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("generic repository capabilities are process-local")


@dataclass(frozen=True)
class ReopenedGenericEvidenceV2:
    """Exact typed evidence reopened through one authenticated capability."""

    bundle: GenericEvidenceBundleReceiptV2
    admission: GenericAdmissionReceiptV2
    source: GenericSourceReceiptV2
    projection: GenericProjectionReceiptV2
    inference: GenericRecordedInferenceReceiptV2
    raw_source: bytes = field(repr=False)
    source_note: bytes = field(repr=False)

    def __reduce__(self) -> Any:
        raise TypeError("reopened generic evidence is process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("reopened generic evidence is process-local")


def _digest_model(model: BaseModel, identity_field: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes(model.model_dump(mode="json", exclude={identity_field}))
    ).hexdigest()


def _seal(repository_id: str, bundle_id: str, digest: str) -> str:
    return hmac.new(
        _CAPABILITY_SECRET,
        canonical_json_bytes(
            {"repository_id": repository_id, "bundle_id": bundle_id, "bundle_sha256": digest}
        ),
        hashlib.sha256,
    ).hexdigest()


class FilesystemGenericIncomingRepositoryV2:
    """Descriptor-safe, create-only generic admission evidence repository."""

    def __init__(self, root: Path, *, create: bool = True, read_only: bool = False) -> None:
        try:
            self._backend = FilesystemInferenceEvidenceRepository(
                Path(root), create=create, read_only=read_only
            )
        except InferenceEvidenceRepositoryError as exc:
            raise GenericIncomingRepositoryError("cannot establish generic evidence root") from exc

    @property
    def root(self) -> Path:
        return self._backend.root

    @property
    def repository_id(self) -> str:
        return self._backend.repository_id

    def _assert_private_tree(self) -> None:
        """Reject permission, ownership, type, or link drift in existing V2 members."""

        subtree = self.root / _ROOT.split("/", maxsplit=1)[0]
        if not subtree.exists():
            return
        try:
            candidates = (subtree, *subtree.rglob("*"))
            for candidate in candidates:
                info = candidate.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or (
                        not stat.S_ISDIR(info.st_mode)
                        and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
                    )
                ):
                    raise GenericIncomingRepositoryError(
                        "generic evidence tree is not owner-only regular repository state"
                    )
        except OSError as exc:
            raise GenericIncomingRepositoryError(
                "cannot verify private generic evidence tree"
            ) from exc

    @staticmethod
    def _receipt_bytes(model: BaseModel) -> bytes:
        content = canonical_json_bytes(model.model_dump(mode="json"))
        if len(content) > _MAX_RECEIPT:
            raise GenericIncomingRepositoryError("generic evidence receipt exceeds fixed limit")
        return content

    @staticmethod
    def _make_admission(admission: VerifiedGenericIncomingV2) -> GenericAdmissionReceiptV2:
        locator = f"{_ROOT}/sources/{admission.source_sha256}.md"
        provisional = GenericAdmissionReceiptV2.model_construct(
            schema_version=2,
            admission_sha256="0" * 64,
            source_sha256=admission.source_sha256,
            source_byte_count=admission.source_byte_count,
            source_name=admission.source_name,
            source_locator=locator,
            metadata=admission.metadata,
        )
        return provisional.model_copy(
            update={"admission_sha256": _digest_model(provisional, "admission_sha256")}
        )

    @staticmethod
    def _make_source(
        admission: VerifiedGenericIncomingV2, source_note: bytes
    ) -> GenericSourceReceiptV2:
        note_sha = hashlib.sha256(source_note).hexdigest()
        provisional = GenericSourceReceiptV2.model_construct(
            schema_version=2,
            source_receipt_sha256="0" * 64,
            source_sha256=admission.source_sha256,
            source_byte_count=admission.source_byte_count,
            source_locator=f"{_ROOT}/sources/{admission.source_sha256}.md",
            source_note_sha256=note_sha,
            source_note_byte_count=len(source_note),
            source_note_locator=f"{_ROOT}/source-notes/{note_sha}.md",
        )
        return provisional.model_copy(
            update={"source_receipt_sha256": _digest_model(provisional, "source_receipt_sha256")}
        )

    @staticmethod
    def _make_inference(
        extraction: GenericGroundedExtractionV2,
    ) -> GenericRecordedInferenceReceiptV2:
        provisional = GenericRecordedInferenceReceiptV2.model_construct(
            schema_version=2,
            inference_sha256="0" * 64,
            mode=extraction.mode,
            source_sha256=extraction.source_sha256,
            request_sha256=extraction.request_sha256,
            provider_result_sha256=extraction.provider_result_sha256,
            provider_contract=extraction.provider_contract,
            claims=extraction.claims,
        )
        return provisional.model_copy(
            update={"inference_sha256": _digest_model(provisional, "inference_sha256")}
        )

    @staticmethod
    def _make_projection(
        admission: VerifiedGenericIncomingV2,
        extraction: GenericGroundedExtractionV2,
        admission_receipt: GenericAdmissionReceiptV2,
        source_receipt: GenericSourceReceiptV2,
    ) -> GenericProjectionReceiptV2:
        metadata = admission.metadata
        provisional = GenericProjectionReceiptV2.model_construct(
            schema_version=2,
            projection_sha256="0" * 64,
            admission_sha256=admission_receipt.admission_sha256,
            source_receipt_sha256=source_receipt.source_receipt_sha256,
            document_id=metadata.document_id,
            document_family=metadata.document_family,
            version_label=metadata.version_label,
            source_sha256=admission.source_sha256,
            claims=extraction.claims,
        )
        return provisional.model_copy(
            update={"projection_sha256": _digest_model(provisional, "projection_sha256")}
        )

    @staticmethod
    def _make_bundle(
        admission: GenericAdmissionReceiptV2,
        source: GenericSourceReceiptV2,
        projection: GenericProjectionReceiptV2,
        inference: GenericRecordedInferenceReceiptV2,
    ) -> GenericEvidenceBundleReceiptV2:
        provisional = GenericEvidenceBundleReceiptV2.model_construct(
            schema_version=2,
            bundle_id=f"generic-bundle-v2:{'0' * 64}",
            bundle_sha256="0" * 64,
            admission_receipt_locator=(f"{_ROOT}/admissions/{admission.admission_sha256}.json"),
            source_receipt_locator=(f"{_ROOT}/source-receipts/{source.source_receipt_sha256}.json"),
            projection_receipt_locator=(f"{_ROOT}/projections/{projection.projection_sha256}.json"),
            inference_receipt_locator=(
                f"{_ROOT}/recorded-inferences/{inference.inference_sha256}.json"
            ),
            admission_sha256=admission.admission_sha256,
            source_receipt_sha256=source.source_receipt_sha256,
            projection_sha256=projection.projection_sha256,
            inference_sha256=inference.inference_sha256,
        )
        # bundle_id is excluded from its identity payload as well.
        payload = provisional.model_dump(mode="json", exclude={"bundle_id", "bundle_sha256"})
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return provisional.model_copy(
            update={"bundle_id": f"generic-bundle-v2:{digest}", "bundle_sha256": digest}
        )

    def _capability(
        self, receipt: GenericEvidenceBundleReceiptV2
    ) -> RepositoryVerifiedGenericEvidenceV2:
        return RepositoryVerifiedGenericEvidenceV2(
            bundle_id=receipt.bundle_id,
            bundle_sha256=receipt.bundle_sha256,
            repository_id=self.repository_id,
            _token=_CAPABILITY_TOKEN,
            _seal=_seal(self.repository_id, receipt.bundle_id, receipt.bundle_sha256),
        )

    def persist(
        self,
        admission: VerifiedGenericIncomingV2,
        extraction: GenericGroundedExtractionV2,
    ) -> RepositoryVerifiedGenericEvidenceV2:
        """Persist members first and the complete bundle receipt last."""

        if extraction.source_sha256 != admission.source_sha256:
            raise GenericIncomingIntegrityError(
                "extraction and admission bind different source bytes"
            )
        admission.verify_current_path()
        source_snapshot = admission.source_snapshot
        source_note = render_generic_source_note_v2(admission, extraction)
        admission_receipt = self._make_admission(admission)
        source_receipt = self._make_source(admission, source_note)
        inference = self._make_inference(extraction)
        projection = self._make_projection(admission, extraction, admission_receipt, source_receipt)
        bundle = self._make_bundle(admission_receipt, source_receipt, projection, inference)
        bundle_locator = f"{_ROOT}/bundles/{bundle.bundle_sha256}.json"
        live = inference.model_copy(update={"mode": GenericExtractionModeV2.LIVE})
        live = live.model_copy(update={"inference_sha256": _digest_model(live, "inference_sha256")})
        live_locator = f"{_ROOT}/recorded-inferences/{live.inference_sha256}.json"
        try:
            self._assert_private_tree()
            with self._backend._exclusive_lock():
                if extraction.mode is GenericExtractionModeV2.REPLAY:
                    prior = self._backend._read_optional(
                        live_locator, limit=_MAX_RECEIPT, label="LIVE inference receipt"
                    )
                    if prior != self._receipt_bytes(live):
                        raise GenericIncomingRepositoryError(
                            "REPLAY lacks its exact already-recorded LIVE representation"
                        )
                self._backend._create_only(
                    admission_receipt.source_locator, source_snapshot, label="admitted raw source"
                )
                self._backend._create_only(
                    source_receipt.source_note_locator, source_note, label="canonical SourceNote"
                )
                members = (
                    (bundle.admission_receipt_locator, admission_receipt, "admission receipt"),
                    (bundle.source_receipt_locator, source_receipt, "source receipt"),
                    (bundle.projection_receipt_locator, projection, "projection receipt"),
                    (bundle.inference_receipt_locator, inference, "recorded inference receipt"),
                )
                for locator, receipt, label in members:
                    self._backend._create_only(locator, self._receipt_bytes(receipt), label=label)
                self._backend._create_only(
                    bundle_locator, self._receipt_bytes(bundle), label="generic bundle receipt"
                )
            self._assert_private_tree()
        except (InferenceEvidenceRepositoryError, OSError) as exc:
            raise GenericIncomingRepositoryError("cannot persist generic evidence bundle") from exc
        return self.reopen(bundle.bundle_id)

    record = persist

    def _read_model(self, locator: str, model: type[BaseModel], label: str) -> BaseModel:
        try:
            content = self._backend._read_optional(locator, limit=_MAX_RECEIPT, label=label)
            if content is None:
                raise GenericIncomingRepositoryError(f"{label} is missing")
            parsed = model.model_validate_json(content, strict=True)
            if content != canonical_json_bytes(parsed.model_dump(mode="json")):
                raise GenericIncomingRepositoryError(f"{label} is not canonical")
            return parsed
        except (ValidationError, InferenceEvidenceRepositoryError) as exc:
            raise GenericIncomingRepositoryError(f"{label} is corrupt") from exc

    def reopen(self, bundle_id: str) -> RepositoryVerifiedGenericEvidenceV2:
        """Reopen and byte/hash-check every member, including after a lost acknowledgement."""

        if not bundle_id.startswith("generic-bundle-v2:"):
            raise GenericIncomingRepositoryError("invalid generic bundle ID")
        digest = bundle_id.removeprefix("generic-bundle-v2:")
        locator = f"{_ROOT}/bundles/{digest}.json"
        try:
            self._assert_private_tree()
            with self._backend._read_lock():
                bundle = self._read_model(
                    locator, GenericEvidenceBundleReceiptV2, "generic bundle receipt"
                )
                assert isinstance(bundle, GenericEvidenceBundleReceiptV2)
                if bundle.bundle_id != bundle_id or bundle.bundle_sha256 != digest:
                    raise GenericIncomingRepositoryError("generic bundle locator/identity mismatch")
                admission = self._read_model(
                    bundle.admission_receipt_locator, GenericAdmissionReceiptV2, "admission receipt"
                )
                source = self._read_model(
                    bundle.source_receipt_locator, GenericSourceReceiptV2, "source receipt"
                )
                projection = self._read_model(
                    bundle.projection_receipt_locator,
                    GenericProjectionReceiptV2,
                    "projection receipt",
                )
                inference = self._read_model(
                    bundle.inference_receipt_locator,
                    GenericRecordedInferenceReceiptV2,
                    "recorded inference receipt",
                )
                assert isinstance(admission, GenericAdmissionReceiptV2)
                assert isinstance(source, GenericSourceReceiptV2)
                assert isinstance(projection, GenericProjectionReceiptV2)
                assert isinstance(inference, GenericRecordedInferenceReceiptV2)
                if (
                    admission.admission_sha256 != bundle.admission_sha256
                    or source.source_receipt_sha256 != bundle.source_receipt_sha256
                    or projection.projection_sha256 != bundle.projection_sha256
                    or inference.inference_sha256 != bundle.inference_sha256
                    or admission.source_sha256 != source.source_sha256
                    or source.source_sha256 != projection.source_sha256
                    or projection.source_sha256 != inference.source_sha256
                    or projection.claims != inference.claims
                ):
                    raise GenericIncomingRepositoryError("generic bundle members disagree")
                raw = self._backend._read_optional(
                    source.source_locator,
                    limit=64 * 1024,
                    label="admitted raw source",
                )
                note = self._backend._read_optional(
                    source.source_note_locator,
                    limit=max(source.source_note_byte_count, 1),
                    label="canonical SourceNote",
                )
                if (
                    raw is None
                    or len(raw) != source.source_byte_count
                    or hashlib.sha256(raw).hexdigest() != source.source_sha256
                    or note is None
                    or len(note) != source.source_note_byte_count
                    or hashlib.sha256(note).hexdigest() != source.source_note_sha256
                ):
                    raise GenericIncomingRepositoryError("generic source evidence bytes disagree")
            self._assert_private_tree()
        except InferenceEvidenceRepositoryError as exc:
            raise GenericIncomingRepositoryError("cannot reopen generic evidence bundle") from exc
        return self._capability(bundle)

    open = reopen

    def verify_capability(
        self, capability: RepositoryVerifiedGenericEvidenceV2
    ) -> GenericEvidenceBundleReceiptV2:
        if type(capability) is not RepositoryVerifiedGenericEvidenceV2:
            raise GenericIncomingRepositoryError("generic repository capability is invalid")
        expected = _seal(self.repository_id, capability.bundle_id, capability.bundle_sha256)
        if (
            capability._token is not _CAPABILITY_TOKEN
            or capability.repository_id != self.repository_id
            or not hmac.compare_digest(capability._seal, expected)
        ):
            raise GenericIncomingRepositoryError("generic repository capability is invalid")
        return self.resolve_verified_evidence(capability).bundle

    def resolve_verified_evidence(
        self, capability: RepositoryVerifiedGenericEvidenceV2
    ) -> ReopenedGenericEvidenceV2:
        """Reopen one complete bundle only through its process-local capability."""

        if type(capability) is not RepositoryVerifiedGenericEvidenceV2:
            raise GenericIncomingRepositoryError("generic repository capability is invalid")
        expected = _seal(self.repository_id, capability.bundle_id, capability.bundle_sha256)
        if (
            capability._token is not _CAPABILITY_TOKEN
            or capability.repository_id != self.repository_id
            or not hmac.compare_digest(capability._seal, expected)
        ):
            raise GenericIncomingRepositoryError("generic repository capability is invalid")
        try:
            self._assert_private_tree()
            with self._backend._read_lock():
                bundle = self._read_model(
                    f"{_ROOT}/bundles/{capability.bundle_sha256}.json",
                    GenericEvidenceBundleReceiptV2,
                    "generic bundle receipt",
                )
                assert isinstance(bundle, GenericEvidenceBundleReceiptV2)
                if (
                    bundle.bundle_id != capability.bundle_id
                    or bundle.bundle_sha256 != capability.bundle_sha256
                ):
                    raise GenericIncomingRepositoryError(
                        "generic capability differs from reopened bundle"
                    )
                admission = self._read_model(
                    bundle.admission_receipt_locator,
                    GenericAdmissionReceiptV2,
                    "admission receipt",
                )
                source = self._read_model(
                    bundle.source_receipt_locator,
                    GenericSourceReceiptV2,
                    "source receipt",
                )
                projection = self._read_model(
                    bundle.projection_receipt_locator,
                    GenericProjectionReceiptV2,
                    "projection receipt",
                )
                inference = self._read_model(
                    bundle.inference_receipt_locator,
                    GenericRecordedInferenceReceiptV2,
                    "recorded inference receipt",
                )
                assert isinstance(admission, GenericAdmissionReceiptV2)
                assert isinstance(source, GenericSourceReceiptV2)
                assert isinstance(projection, GenericProjectionReceiptV2)
                assert isinstance(inference, GenericRecordedInferenceReceiptV2)
                raw = self._backend._read_optional(
                    source.source_locator, limit=64 * 1024, label="admitted raw source"
                )
                note = self._backend._read_optional(
                    source.source_note_locator,
                    limit=max(source.source_note_byte_count, 1),
                    label="canonical SourceNote",
                )
                if raw is None or note is None:
                    raise GenericIncomingRepositoryError("generic source evidence is missing")
                if not (
                    admission.admission_sha256 == bundle.admission_sha256
                    and source.source_receipt_sha256 == bundle.source_receipt_sha256
                    and projection.projection_sha256 == bundle.projection_sha256
                    and inference.inference_sha256 == bundle.inference_sha256
                    and admission.source_sha256 == source.source_sha256
                    and source.source_sha256 == projection.source_sha256
                    and projection.source_sha256 == inference.source_sha256
                    and projection.admission_sha256 == admission.admission_sha256
                    and projection.source_receipt_sha256 == source.source_receipt_sha256
                    and projection.document_id == admission.metadata.document_id
                    and projection.document_family == admission.metadata.document_family
                    and projection.version_label == admission.metadata.version_label
                    and projection.claims == inference.claims
                    and len(raw) == admission.source_byte_count == source.source_byte_count
                    and hashlib.sha256(raw).hexdigest() == admission.source_sha256
                    and len(note) == source.source_note_byte_count
                    and hashlib.sha256(note).hexdigest() == source.source_note_sha256
                ):
                    raise GenericIncomingRepositoryError("generic bundle members disagree")
                expected_note = render_verified_generic_source_note_projection_v2(
                    metadata=admission.metadata,
                    source_sha256=admission.source_sha256,
                    source_snapshot=raw,
                    claims=projection.claims,
                )
                if note != expected_note:
                    raise GenericIncomingRepositoryError(
                        "canonical SourceNote is not the semantic projection of raw evidence"
                    )
                suggested = {
                    item.quote: (item.confidence, tuple(item.affects))
                    for item in inference.provider_contract.claims
                }
                grounded = {
                    item.statement: (item.confidence, item.affects) for item in projection.claims
                }
                if (
                    len(suggested) != len(inference.provider_contract.claims)
                    or len(grounded) != len(projection.claims)
                    or suggested != grounded
                ):
                    raise GenericIncomingRepositoryError(
                        "sanitized provider suggestions differ from grounded claim authority"
                    )
            self._assert_private_tree()
        except (GenericIncomingIntegrityError, InferenceEvidenceRepositoryError) as exc:
            raise GenericIncomingRepositoryError(
                "cannot resolve verified generic evidence"
            ) from exc
        return ReopenedGenericEvidenceV2(
            bundle=bundle,
            admission=admission,
            source=source,
            projection=projection,
            inference=inference,
            raw_source=bytes(raw),
            source_note=bytes(note),
        )


__all__ = [
    "FilesystemGenericIncomingRepositoryV2",
    "GenericAdmissionReceiptV2",
    "GenericEvidenceBundleReceiptV2",
    "GenericIncomingRepositoryError",
    "GenericProjectionReceiptV2",
    "GenericRecordedInferenceReceiptV2",
    "GenericSourceReceiptV2",
    "ReopenedGenericEvidenceV2",
    "RepositoryVerifiedGenericEvidenceV2",
]
