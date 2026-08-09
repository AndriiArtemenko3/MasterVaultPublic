"""Manifest-last create-only staging for deterministic managed revisions."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceConflictError,
)
from mastervault.change_control.managed_review import ManagedArtifactRef
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes

MAX_MANAGED_STAGING_MEMBERS_V1 = 256
MAX_MANAGED_STAGING_MANIFEST_BYTES_V1 = 2 * 1024 * 1024

_CAPABILITY_SECRET = os.urandom(32)
_CAPABILITY_TOKEN = object()


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ManagedStagingMember(_StrictFrozenModel):
    artifact: ManagedArtifactRef


class ManagedStagingManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str
    repository_id: str = Field(pattern=SHA256_PATTERN)
    members: tuple[ManagedStagingMember, ...] = Field(
        min_length=1, max_length=MAX_MANAGED_STAGING_MEMBERS_V1
    )
    manifest_id: str = Field(pattern=r"^managed-staging:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_id", "manifest_sha256"})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._payload())

    def record_bytes(self) -> bytes:
        """Return the complete self-verifying repository record."""

        return canonical_json_bytes(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if not self.run_id or self.run_id != self.run_id.strip():
            raise ValueError("managed staging run_id must be exact non-empty text")
        artifacts = tuple(item.artifact for item in self.members)
        if artifacts != tuple(sorted(artifacts, key=lambda item: item.artifact_id)) or len(
            {item.artifact_id for item in artifacts}
        ) != len(artifacts):
            raise ValueError("managed staging members must be unique and canonical")
        prefix = f"staging/managed-review/{self.run_id}/"
        if any(not item.path.startswith(prefix) for item in artifacts):
            raise ValueError("managed staging member escapes its exact run root")
        digest = hashlib.sha256(self.canonical_bytes()).hexdigest()
        if self.manifest_sha256 != digest or self.manifest_id != f"managed-staging:{digest}":
            raise ValueError("managed staging manifest identity differs from its exact bytes")
        return self

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        repository_id: str,
        artifacts: tuple[ManagedArtifactRef, ...],
    ) -> Self:
        ordered = tuple(sorted(artifacts, key=lambda item: item.artifact_id))
        values = {
            "schema_version": 1,
            "run_id": run_id,
            "repository_id": repository_id,
            "members": [
                ManagedStagingMember(artifact=item).model_dump(mode="json") for item in ordered
            ],
        }
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    **values,
                    "manifest_id": f"managed-staging:{digest}",
                    "manifest_sha256": digest,
                }
            )
        )


class ManagedStagingCompletionBinding(_StrictFrozenModel):
    """Serializable restart handle for one complete manifest-last run."""

    schema_version: Literal[1] = 1
    run_id: str
    repository_id: str = Field(pattern=SHA256_PATTERN)
    manifest_id: str = Field(pattern=r"^managed-staging:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_path: str
    completion_path: str
    completion_id: str = Field(pattern=r"^managed-staging-completion:[0-9a-f]{64}$")
    completion_sha256: str = Field(pattern=SHA256_PATTERN)

    def _payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"completion_id", "completion_sha256"},
        )

    def record_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if not self.run_id or self.run_id != self.run_id.strip():
            raise ValueError("managed staging completion run_id must be exact non-empty text")
        if self.manifest_id != f"managed-staging:{self.manifest_sha256}":
            raise ValueError("managed staging completion manifest ID differs from its SHA")
        expected_manifest = (
            f"staging/managed-review/{self.run_id}/manifests/"
            f"{self.manifest_sha256}.json"
        )
        expected_completion = f"staging/managed-review/{self.run_id}/COMPLETE.json"
        if (
            self.manifest_path != expected_manifest
            or self.completion_path != expected_completion
        ):
            raise ValueError("managed staging completion paths differ from its exact run")
        digest = hashlib.sha256(canonical_json_bytes(self._payload())).hexdigest()
        if self.completion_sha256 != digest or (
            self.completion_id != f"managed-staging-completion:{digest}"
        ):
            raise ValueError("managed staging completion identity differs from its exact bytes")
        return self

    @classmethod
    def create(cls, *, manifest: ManagedStagingManifest) -> Self:
        values = {
            "schema_version": 1,
            "run_id": manifest.run_id,
            "repository_id": manifest.repository_id,
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_path": (
                f"staging/managed-review/{manifest.run_id}/manifests/"
                f"{manifest.manifest_sha256}.json"
            ),
            "completion_path": f"staging/managed-review/{manifest.run_id}/COMPLETE.json",
        }
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        return cls.model_validate_json(
            canonical_json_bytes(
                {
                    **values,
                    "completion_id": f"managed-staging-completion:{digest}",
                    "completion_sha256": digest,
                }
            )
        )


@dataclass(frozen=True, eq=False)
class VerifiedManagedStagingCapability:
    manifest: ManagedStagingManifest
    completion: ManagedStagingCompletionBinding
    repository_id: str
    _seal: bytes
    _token: object

    def verify(self, repository: ManagedStagingRepository) -> ManagedStagingManifest:
        if self._token is not _CAPABILITY_TOKEN or self.repository_id != repository.repository_id:
            raise ValueError("managed staging capability belongs to another authority")
        expected = hmac.digest(
            _CAPABILITY_SECRET,
            canonical_json_bytes(
                {
                    "repository_id": self.repository_id,
                    "manifest_id": self.manifest.manifest_id,
                    "manifest_sha256": self.manifest.manifest_sha256,
                    "completion_id": self.completion.completion_id,
                    "completion_sha256": self.completion.completion_sha256,
                }
            ),
            "sha256",
        )
        if not hmac.compare_digest(self._seal, expected):
            raise ValueError("managed staging capability seal is invalid")
        reopened = repository.resolve_completed_run(self.completion)
        if reopened.manifest != self.manifest:
            raise ValueError("managed staging capability manifest changed during reopening")
        return reopened.manifest


class ManagedStagingRepository:
    """Shares one canonical root/lock implementation with recorded evidence."""

    def __init__(self, root: Path) -> None:
        self._backend = FilesystemInferenceEvidenceRepository(root)

    @property
    def root(self) -> Path:
        return self._backend.root

    @property
    def repository_id(self) -> str:
        return self._backend.repository_id

    @staticmethod
    def _manifest_path(manifest: ManagedStagingManifest) -> str:
        return (
            f"staging/managed-review/{manifest.run_id}/manifests/"
            f"{manifest.manifest_sha256}.json"
        )

    @staticmethod
    def _complete_path(manifest: ManagedStagingManifest) -> str:
        return f"staging/managed-review/{manifest.run_id}/COMPLETE.json"

    def stage(
        self,
        *,
        run_id: str,
        artifacts: tuple[tuple[ManagedArtifactRef, bytes], ...],
        fail_after_step: int | None = None,
    ) -> VerifiedManagedStagingCapability:
        """Write members, immutable manifest, then the fixed completion pointer."""

        by_id: dict[str, tuple[ManagedArtifactRef, bytes]] = {}
        by_path: dict[str, tuple[ManagedArtifactRef, bytes]] = {}
        for artifact, content in artifacts:
            if len(content) != artifact.byte_count or hashlib.sha256(content).hexdigest() != (
                artifact.sha256
            ):
                raise ValueError("managed staging bytes differ from their artifact receipt")
            previous = by_id.setdefault(artifact.artifact_id, (artifact, content))
            if previous != (artifact, content):
                raise ValueError("managed staging artifact identity is ambiguous")
            previous_path = by_path.setdefault(artifact.path, (artifact, content))
            if previous_path != (artifact, content):
                raise ValueError("managed staging path maps to a conflicting artifact or bytes")
        manifest = ManagedStagingManifest.create(
            run_id=run_id,
            repository_id=self.repository_id,
            artifacts=tuple(item[0] for item in by_id.values()),
        )
        if len(manifest.record_bytes()) > MAX_MANAGED_STAGING_MANIFEST_BYTES_V1:
            raise ValueError("managed staging manifest exceeds its fixed byte limit")
        completion = ManagedStagingCompletionBinding.create(manifest=manifest)
        ordered = tuple(sorted(by_id.values(), key=lambda item: item[0].artifact_id))
        step = 0
        with self._backend._exclusive_lock():
            for artifact, content in ordered:
                self._backend._create_only(artifact.path, content, label="managed staging member")
                step += 1
                if fail_after_step == step:
                    raise RuntimeError("injected managed staging interruption")
            self._backend._create_only(
                self._manifest_path(manifest),
                manifest.record_bytes(),
                label="managed staging manifest",
            )
            step += 1
            if fail_after_step == step:
                raise RuntimeError("injected managed staging interruption")
            self._backend._create_only(
                self._complete_path(manifest),
                completion.record_bytes(),
                label="managed staging completion pointer",
            )
        reopened = self.resolve_completed_run(completion)
        if reopened.manifest != manifest:
            raise ValueError("managed staging manifest changed during completion")
        return reopened

    def _mint_capability(
        self,
        *,
        manifest: ManagedStagingManifest,
        completion: ManagedStagingCompletionBinding,
    ) -> VerifiedManagedStagingCapability:
        seal = hmac.digest(
            _CAPABILITY_SECRET,
            canonical_json_bytes(
                {
                    "repository_id": self.repository_id,
                    "manifest_id": manifest.manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "completion_id": completion.completion_id,
                    "completion_sha256": completion.completion_sha256,
                }
            ),
            "sha256",
        )
        return VerifiedManagedStagingCapability(
            manifest=manifest,
            completion=completion,
            repository_id=self.repository_id,
            _seal=seal,
            _token=_CAPABILITY_TOKEN,
        )

    def resolve_completed_run(
        self,
        binding_or_run_id: ManagedStagingCompletionBinding | str,
    ) -> VerifiedManagedStagingCapability:
        if isinstance(binding_or_run_id, ManagedStagingCompletionBinding):
            expected = binding_or_run_id
            if expected.repository_id != self.repository_id:
                raise ValueError("managed staging completion belongs to another repository")
        else:
            run_id = binding_or_run_id
            complete_path = f"staging/managed-review/{run_id}/COMPLETE.json"
            complete_bytes = self._backend._read_optional(
                complete_path,
                limit=MAX_MANAGED_STAGING_MANIFEST_BYTES_V1,
                label="managed staging completion pointer",
            )
            if complete_bytes is None:
                raise InferenceEvidenceConflictError("managed staging run is incomplete")
            expected = ManagedStagingCompletionBinding.model_validate_json(complete_bytes)
            if expected.run_id != run_id or expected.repository_id != self.repository_id:
                raise ValueError("managed staging completion substitutes its run or repository")
        complete = self._backend._read_optional(
            expected.completion_path,
            limit=MAX_MANAGED_STAGING_MANIFEST_BYTES_V1,
            label="managed staging completion pointer",
        )
        if complete != expected.record_bytes():
            raise InferenceEvidenceConflictError(
                "managed staging run is incomplete or names another manifest"
            )
        manifest_bytes = self._backend._read_optional(
            expected.manifest_path,
            limit=MAX_MANAGED_STAGING_MANIFEST_BYTES_V1,
            label="managed staging manifest",
        )
        if manifest_bytes is None:
            raise ValueError("managed staging manifest is absent or substituted")
        manifest = ManagedStagingManifest.model_validate_json(manifest_bytes)
        if (
            manifest.manifest_id != expected.manifest_id
            or manifest.manifest_sha256 != expected.manifest_sha256
            or manifest.run_id != expected.run_id
            or manifest.repository_id != expected.repository_id
            or manifest.record_bytes() != manifest_bytes
        ):
            raise ValueError("managed staging manifest is absent or substituted")
        for member in manifest.members:
            artifact = member.artifact
            content = self._backend._read_optional(
                artifact.path,
                limit=artifact.byte_count,
                label="managed staging member",
            )
            if content is None or len(content) != artifact.byte_count or (
                hashlib.sha256(content).hexdigest() != artifact.sha256
            ):
                raise ValueError("managed staging member is absent or substituted")
        return self._mint_capability(manifest=manifest, completion=expected)

    def reopen(self, manifest: ManagedStagingManifest) -> ManagedStagingManifest:
        completion = ManagedStagingCompletionBinding.create(manifest=manifest)
        return self.resolve_completed_run(completion).manifest


__all__ = [
    "MAX_MANAGED_STAGING_MANIFEST_BYTES_V1",
    "MAX_MANAGED_STAGING_MEMBERS_V1",
    "ManagedStagingManifest",
    "ManagedStagingMember",
    "ManagedStagingCompletionBinding",
    "ManagedStagingRepository",
    "VerifiedManagedStagingCapability",
]
