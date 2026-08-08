"""Durable, content-addressed repository for exact analysis evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, Self, SupportsIndex

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.change_control._repository_files import (
    RepositoryFileBoundaryError,
    RepositoryFileIntegrityError,
    canonical_repo_relative,
    read_repository_file,
    verified_repository_root,
)
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    ManagedArtifactKind,
    ManagedArtifactRef,
)
from mastervault.change_control.models import SHA256_PATTERN, canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1,
    InferenceArtifactPayload,
    RecordedInferenceOutcome,
)
from mastervault.change_control.temporal_analysis import (
    MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
)

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by the explicit platform gate
    _fcntl = None  # type: ignore[assignment]

MAX_INFERENCE_EVIDENCE_BATCH_V1: Final = 4096
MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1: Final = (
    MAX_OUTCOME_ARTIFACT_CANONICAL_BYTES_V1 + 3 * 1024 * 1024
)
MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1: Final = 16 * 1024
MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1: Final = 4 * 1024 * 1024
MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1: Final = 32 * 1024 * 1024
MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1: Final = 96 * 1024 * 1024
MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1: Final = 112 * 1024 * 1024
MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1: Final = 192 * 1024 * 1024
MAX_COMMITTED_BATCH_MANIFESTS_V1: Final = 4096
MAX_COMMITTED_BATCH_SCAN_BYTES_V1: Final = 64 * 1024 * 1024
MAX_PENDING_FILES_PER_DIRECTORY_V1: Final = 32
MAX_INFERENCE_INPUT_METADATA_NODES_V1: Final = 65_536
MAX_INFERENCE_INPUT_METADATA_DEPTH_V1: Final = 64

_MAX_PENDING_CREATE_ONLY_FILE_BYTES_V1: Final = max(
    MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1,
    MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1,
    MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
)

_EVIDENCE_ROOT: Final = "inference/evidence"
_OUTCOME_ROOT: Final = f"{_EVIDENCE_ROOT}/outcomes"
_RECEIPT_ROOT: Final = f"{_EVIDENCE_ROOT}/receipts"
_BATCH_ROOT: Final = f"{_EVIDENCE_ROOT}/batches"
_LOCK_PATH: Final = f"{_EVIDENCE_ROOT}/repository.lock"
_TEMPORAL_ANALYSIS_ROOT: Final = "temporal/evidence/analyses"
_EXECUTION_ID = r"^inference-exec:[0-9a-f]{64}$"
_CONTENT_FILE_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_PENDING_FILE_NAME = re.compile(r"^pending-[0-9a-f]{32}$")
_EVALUATOR_PATH_FRAGMENT = re.compile(
    r"(?:^|[^a-z0-9._-])(?:[a-z0-9._-]+/)*(?:gold|golden|evaluator)/[a-z0-9._/-]+"
)
_EXPECTED_METADATA_KEY = re.compile(
    r"(?:^|[{\s,])[\"\']?expected_[A-Za-z0-9_]*[\"\']?\s*:",
    re.MULTILINE,
)

_CAPABILITY_TOKEN = object()
_CAPABILITY_SECRET = os.urandom(32)


class InferenceEvidenceRepositoryError(ValueError):
    """Durable inference evidence could not be safely persisted or reopened."""


class InferenceEvidenceConflictError(InferenceEvidenceRepositoryError):
    """A create-only evidence locator already contains different authority."""


class InferenceEvidenceResolutionError(InferenceEvidenceRepositoryError):
    """Receipt evidence is absent, ambiguous, incomplete, or corrupt."""


class InferenceEvidenceUnsupportedPlatformError(InferenceEvidenceRepositoryError):
    """The repository cannot provide its required filesystem guarantees here."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _OutcomeManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    repository_id: str = Field(pattern=SHA256_PATTERN)
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_id: str = Field(pattern=_EXECUTION_ID)
    execution_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_artifact: ManagedArtifactRef
    outcome: RecordedInferenceOutcome

    @model_validator(mode="after")
    def _exact(self) -> Self:
        outcome_bytes = canonical_json_bytes(self.outcome.model_dump(mode="json"))
        if hashlib.sha256(outcome_bytes).hexdigest() != self.outcome_sha256:
            raise ValueError("outcome manifest identity differs from its exact outcome bytes")
        execution = self.outcome.execution
        if (
            self.execution_id != execution.execution_id
            or self.execution_sha256 != execution.execution_sha256
            or self.receipt_artifact != execution.receipt_artifact
        ):
            raise ValueError("outcome manifest metadata differs from its recorded execution")
        return self


class _ReceiptBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    repository_id: str = Field(pattern=SHA256_PATTERN)
    receipt_artifact: ManagedArtifactRef
    execution_id: str = Field(pattern=_EXECUTION_ID)
    execution_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_manifest_path: str

    @model_validator(mode="after")
    def _exact(self) -> Self:
        receipt = self.receipt_artifact
        if (
            receipt.kind != ManagedArtifactKind.INFERENCE_RECEIPT
            or receipt.path != f"receipts/inference/{receipt.sha256}.json"
        ):
            raise ValueError("receipt binding requires an exact inference-receipt locator")
        expected = f"{_OUTCOME_ROOT}/{self.outcome_sha256}.json"
        if canonical_repo_relative(self.outcome_manifest_path) != expected:
            raise ValueError("receipt binding names a non-canonical outcome manifest")
        return self


class _BatchEntry(_StrictFrozenModel):
    execution_id: str = Field(pattern=_EXECUTION_ID)
    receipt_artifact_id: str = Field(pattern=r"^martifact:[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)


class _BatchManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    batch_id: str = Field(pattern=r"^inference-batch:[0-9a-f]{64}$")
    batch_sha256: str = Field(pattern=SHA256_PATTERN)
    repository_id: str = Field(pattern=SHA256_PATTERN)
    repository_root: str
    outcomes: tuple[_BatchEntry, ...] = Field(
        min_length=1,
        max_length=MAX_INFERENCE_EVIDENCE_BATCH_V1,
    )

    @model_validator(mode="after")
    def _exact(self) -> Self:
        if self.outcomes != tuple(sorted(self.outcomes, key=lambda item: item.execution_id)):
            raise ValueError("batch outcomes must use canonical execution-ID order")
        if len({item.execution_id for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("batch outcomes require unique execution IDs")
        if len({item.receipt_artifact_id for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("batch outcomes require unique receipt artifact IDs")
        payload = _batch_payload(
            repository_id=self.repository_id,
            repository_root=self.repository_root,
            identities=tuple(
                (item.execution_id, item.receipt_artifact_id, item.outcome_sha256)
                for item in self.outcomes
            ),
        )
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if self.batch_sha256 != digest or self.batch_id != f"inference-batch:{digest}":
            raise ValueError("batch ID/SHA does not bind its exact canonical payload")
        return self


@dataclass(frozen=True)
class _PreparedOutcome:
    outcome: RecordedInferenceOutcome
    outcome_sha256: str
    manifest_path: str
    manifest_bytes: bytes
    binding_path: str
    binding_bytes: bytes
    outcome_byte_count: int
    artifact_byte_count: int


def _repository_identity(root: Path) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.inference-evidence-repository.v1",
                "resolved_root": str(root),
            }
        )
    ).hexdigest()


def _batch_payload(
    *,
    repository_id: str,
    repository_root: str,
    identities: tuple[tuple[str, str, str], ...],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "repository_id": repository_id,
        "repository_root": repository_root,
        "outcomes": [
            {
                "execution_id": execution_id,
                "receipt_artifact_id": receipt_artifact_id,
                "outcome_sha256": outcome_sha256,
            }
            for execution_id, receipt_artifact_id, outcome_sha256 in identities
        ],
    }


@dataclass(frozen=True, eq=False)
class RepositoryVerifiedInferenceEvidenceBatch:
    """Process-local proof that one exact evidence batch was durably reopened."""

    batch_id: str
    batch_sha256: str
    repository_id: str
    repository_root: str
    execution_ids: tuple[str, ...]
    receipt_artifact_ids: tuple[str, ...]
    outcome_sha256s: tuple[str, ...]
    _token: object
    _seal: str

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise TypeError("inference evidence capabilities are repository-created only")

    @property
    def outcome_count(self) -> int:
        return len(self.execution_ids)

    def __reduce__(self) -> Any:
        raise TypeError("inference evidence capabilities are process-local")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        del protocol
        raise TypeError("inference evidence capabilities are process-local")

    def __getstate__(self) -> Any:
        raise TypeError("inference evidence capabilities are process-local")

    def verify(
        self,
        *,
        repository: FilesystemInferenceEvidenceRepository,
        outcomes: tuple[RecordedInferenceOutcome, ...],
    ) -> tuple[RecordedInferenceOutcome, ...]:
        """Authenticate the capability and reopen the exact caller-supplied batch."""

        return repository.verify_batch(capability=self, outcomes=outcomes)


class FilesystemInferenceEvidenceRepository:
    """No-follow, create-only authority for inference and temporal evidence."""

    def __init__(self, root: Path) -> None:
        required_dir_fd = (os.open, os.mkdir, os.unlink, os.link, os.stat)
        if (
            os.name != "posix"
            or _fcntl is None
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or any(function not in os.supports_dir_fd for function in required_dir_fd)
        ):
            raise InferenceEvidenceUnsupportedPlatformError(
                "inference evidence repositories require POSIX flock, O_NOFOLLOW, "
                "O_DIRECTORY, and dir_fd filesystem operations"
            )
        requested = Path(root)
        try:
            requested.mkdir(mode=0o700, parents=False, exist_ok=True)
            resolved = verified_repository_root(requested)
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise InferenceEvidenceRepositoryError(
                "cannot establish the dedicated inference evidence repository root"
            ) from exc
        info = resolved.stat()
        self._root = resolved
        self._root_signature = (info.st_dev, info.st_ino)
        self._repository_id = _repository_identity(resolved)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def repository_id(self) -> str:
        return self._repository_id

    def _verified_root(self) -> Path:
        try:
            resolved = verified_repository_root(self._root)
            info = resolved.stat()
        except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise InferenceEvidenceRepositoryError(
                "evidence repository root is unavailable"
            ) from exc
        if resolved != self._root or (info.st_dev, info.st_ino) != self._root_signature:
            raise InferenceEvidenceRepositoryError("evidence repository root was substituted")
        if _repository_identity(resolved) != self._repository_id:
            raise InferenceEvidenceRepositoryError("evidence repository identity changed")
        return resolved

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        lock_parent = -1
        fd = -1
        try:
            self._verified_root()
            lock_parent, lock_name = self._open_parent(_LOCK_PATH, create=True)
            names = os.listdir(lock_parent)
            if lock_name not in names and any(
                name.casefold() == lock_name.casefold() for name in names
            ):
                raise InferenceEvidenceRepositoryError(
                    "repository lock path does not use exact case"
                )
            flags = (
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            fd = os.open(lock_name, flags, 0o600, dir_fd=lock_parent)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise InferenceEvidenceRepositoryError("repository lock is not a regular file")
            _fcntl.flock(fd, _fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise InferenceEvidenceRepositoryError(
                "cannot acquire evidence repository lock"
            ) from exc
        finally:
            if fd >= 0:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            if lock_parent >= 0:
                os.close(lock_parent)

    def _open_parent(self, relative: str, *, create: bool) -> tuple[int, str]:
        canonical = canonical_repo_relative(relative)
        parts = PurePosixPath(canonical).parts
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        current = os.open(self._verified_root(), flags)
        try:
            for part in parts[:-1]:
                names = os.listdir(current)
                if part not in names:
                    aliases = [name for name in names if name.casefold() == part.casefold()]
                    if aliases:
                        raise RepositoryFileBoundaryError(
                            f"repository path does not use exact case: {relative}"
                        )
                    if not create:
                        raise FileNotFoundError(relative)
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=current)
                if not stat.S_ISDIR(os.fstat(child).st_mode):
                    os.close(child)
                    raise RepositoryFileIntegrityError(
                        f"repository path component is not a directory: {relative}"
                    )
                os.close(current)
                current = child
            return current, parts[-1]
        except Exception:
            os.close(current)
            raise

    def _read_optional(self, relative: str, *, limit: int, label: str) -> bytes | None:
        try:
            parent, name = self._open_parent(relative, create=False)
        except FileNotFoundError:
            return None
        try:
            names = os.listdir(parent)
            if name not in names:
                aliases = [item for item in names if item.casefold() == name.casefold()]
                if aliases:
                    raise RepositoryFileBoundaryError(
                        f"{label} path does not use exact repository case: {relative}"
                    )
                return None
        finally:
            os.close(parent)
        _, data = read_repository_file(
            repo_root=self._verified_root(),
            relative=relative,
            limit=limit,
            label=label,
        )
        return data

    def _cleanup_pending_create_only_files(self, directory_fd: int, *, label: str) -> None:
        pending: list[tuple[str, tuple[int, int, int]]] = []
        try:
            for entry in os.scandir(directory_fd):
                if not _PENDING_FILE_NAME.fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                pending.append((entry.name, (info.st_dev, info.st_ino, info.st_size)))
                if (
                    len(pending) > MAX_PENDING_FILES_PER_DIRECTORY_V1
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_size > _MAX_PENDING_CREATE_ONLY_FILE_BYTES_V1
                ):
                    raise InferenceEvidenceConflictError(
                        f"{label} directory has unsafe or excessive pending-file residue"
                    )
            for pending_name, signature in pending:
                confirmed = os.stat(
                    pending_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(confirmed.st_mode)
                    or (confirmed.st_dev, confirmed.st_ino, confirmed.st_size) != signature
                ):
                    raise InferenceEvidenceConflictError(
                        f"pending {label} file changed before cleanup"
                    )
            for pending_name, _signature in pending:
                os.unlink(pending_name, dir_fd=directory_fd)
            if pending:
                os.fsync(directory_fd)
        except InferenceEvidenceRepositoryError:
            raise
        except OSError as exc:
            raise RepositoryFileIntegrityError(f"cannot clean interrupted {label} writes") from exc

    def _synchronize_existing_create_only(
        self,
        relative: str,
        content: bytes,
        *,
        label: str,
    ) -> None:
        parent = -1
        fd = -1
        try:
            parent, name = self._open_parent(relative, create=False)
            self._cleanup_pending_create_only_files(parent, label=label)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(name, flags, dir_fd=parent)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != len(content):
                raise InferenceEvidenceConflictError(
                    f"existing {label} is not the expected regular file"
                )
            os.fsync(fd)
            os.fsync(parent)
        except InferenceEvidenceRepositoryError:
            raise
        except (FileNotFoundError, OSError) as exc:
            raise InferenceEvidenceConflictError(
                f"existing {label} could not be durably synchronized"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if parent >= 0:
                os.close(parent)

        persisted = self._read_optional(relative, limit=len(content), label=label)
        if persisted != content:
            raise InferenceEvidenceConflictError(
                f"existing {label} changed during durability synchronization"
            )

    def _create_only(self, relative: str, content: bytes, *, label: str) -> None:
        canonical_repo_relative(relative)
        cleanup_parent, _name = self._open_parent(relative, create=True)
        try:
            self._cleanup_pending_create_only_files(cleanup_parent, label=label)
        finally:
            os.close(cleanup_parent)

        existing = self._read_optional(relative, limit=len(content), label=label)
        if existing is not None:
            if existing != content:
                raise InferenceEvidenceConflictError(
                    f"existing {label} bytes differ at create-only locator {relative}"
                )
            self._synchronize_existing_create_only(relative, content, label=label)
            return

        parent, name = self._open_parent(relative, create=True)
        temporary = f"pending-{secrets.token_hex(16)}"
        fd = -1
        created_temporary = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary, flags, 0o600, dir_fd=parent)
            created_temporary = True
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("zero-byte write while persisting evidence")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
                os.fsync(parent)
            except FileExistsError:
                pass
        except OSError as exc:
            raise InferenceEvidenceRepositoryError(f"cannot persist {label}: {relative}") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if created_temporary:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=parent)
            os.close(parent)

        persisted = self._read_optional(relative, limit=len(content), label=label)
        if persisted != content:
            raise InferenceEvidenceConflictError(
                f"create-only {label} was concurrently substituted: {relative}"
            )
        self._synchronize_existing_create_only(relative, content, label=label)

    def _validate_runtime_metadata(self, value: Any, *, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                folded = str(child_key).casefold()
                if "golden" in folded or "evaluator" in folded or folded.startswith("expected_"):
                    raise InferenceEvidenceRepositoryError(
                        "inference evidence cannot contain evaluator/golden metadata keys"
                    )
                self._validate_runtime_metadata(child, key=str(child_key))
        elif isinstance(value, list):
            for child in value:
                self._validate_runtime_metadata(child, key=key)
        elif isinstance(value, str) and "path" in key.casefold():
            try:
                canonical_repo_relative(value)
            except RepositoryFileBoundaryError as exc:
                raise InferenceEvidenceRepositoryError(
                    f"inference evidence contains an unsafe runtime path in {key}"
                ) from exc

    def _validate_input_artifact_content(self, payload: InferenceArtifactPayload) -> None:
        if payload.artifact.kind != ManagedArtifactKind.INFERENCE_INPUT:
            return
        text = payload.content_utf8
        normalized = text.replace("\\", "/").casefold()
        if _EVALUATOR_PATH_FRAGMENT.search(normalized):
            raise InferenceEvidenceRepositoryError(
                "inference input artifact contains an evaluator/golden path fragment"
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if _EXPECTED_METADATA_KEY.search(text):
                raise InferenceEvidenceRepositoryError(
                    "inference input artifact contains an expected_* evaluator metadata key"
                ) from None
            return
        except RecursionError as exc:
            raise InferenceEvidenceRepositoryError(
                "inference input artifact metadata nesting exceeds its fixed bound"
            ) from exc
        allow_schema_labels = payload.artifact.path.startswith("inference/schemas/")

        stack: list[tuple[Any, str, int]] = [(parsed, "", 0)]
        nodes = 0
        while stack:
            value, parent_key, depth = stack.pop()
            nodes += 1
            if (
                nodes > MAX_INFERENCE_INPUT_METADATA_NODES_V1
                or depth > MAX_INFERENCE_INPUT_METADATA_DEPTH_V1
            ):
                raise InferenceEvidenceRepositoryError(
                    "inference input artifact metadata exceeds fixed structural bounds"
                )
            if isinstance(value, dict):
                for child_key, child in value.items():
                    folded = str(child_key).casefold()
                    if "golden" in folded or "evaluator" in folded:
                        raise InferenceEvidenceRepositoryError(
                            "inference input artifact contains evaluator/golden metadata"
                        )
                    if folded.startswith("expected_") and not (
                        allow_schema_labels and parent_key == "properties"
                    ):
                        raise InferenceEvidenceRepositoryError(
                            "inference input artifact contains an expected_* evaluator metadata key"
                        )
                    if isinstance(child, str) and "path" in folded:
                        try:
                            canonical_repo_relative(child)
                        except RepositoryFileBoundaryError as exc:
                            raise InferenceEvidenceRepositoryError(
                                "inference input artifact contains an unsafe metadata path"
                            ) from exc
                    stack.append((child, folded, depth + 1))
            elif isinstance(value, list):
                for child in value:
                    stack.append((child, parent_key, depth + 1))

    def _prepare(self, outcome: RecordedInferenceOutcome) -> _PreparedOutcome:
        try:
            exact = RecordedInferenceOutcome.model_validate_json(
                canonical_json_bytes(outcome.model_dump(mode="json"))
            )
            dumped = exact.model_dump(mode="json")
            self._validate_runtime_metadata(dumped)
            for payload in exact.artifacts:
                canonical_repo_relative(payload.artifact.path)
                self._validate_input_artifact_content(payload)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InferenceEvidenceRepositoryError(
                "recorded inference outcome failed exact repository preflight"
            ) from exc

        outcome_bytes = canonical_json_bytes(dumped)
        outcome_sha256 = hashlib.sha256(outcome_bytes).hexdigest()
        manifest_path = f"{_OUTCOME_ROOT}/{outcome_sha256}.json"
        execution = exact.execution
        manifest = _OutcomeManifest(
            repository_id=self._repository_id,
            outcome_sha256=outcome_sha256,
            execution_id=execution.execution_id,
            execution_sha256=execution.execution_sha256,
            receipt_artifact=execution.receipt_artifact,
            outcome=exact,
        )
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        if len(manifest_bytes) > MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1:
            raise InferenceEvidenceRepositoryError("outcome manifest exceeds its fixed byte limit")
        receipt_sha = execution.receipt_artifact.sha256
        binding_path = f"{_RECEIPT_ROOT}/{receipt_sha}/{outcome_sha256}.json"
        binding = _ReceiptBinding(
            repository_id=self._repository_id,
            receipt_artifact=execution.receipt_artifact,
            execution_id=execution.execution_id,
            execution_sha256=execution.execution_sha256,
            outcome_sha256=outcome_sha256,
            outcome_manifest_path=manifest_path,
        )
        binding_bytes = canonical_json_bytes(binding.model_dump(mode="json"))
        return _PreparedOutcome(
            outcome=exact,
            outcome_sha256=outcome_sha256,
            manifest_path=manifest_path,
            manifest_bytes=manifest_bytes,
            binding_path=binding_path,
            binding_bytes=binding_bytes,
            outcome_byte_count=len(outcome_bytes),
            artifact_byte_count=sum(
                len(payload.content_utf8.encode("utf-8")) for payload in exact.artifacts
            ),
        )

    def _receipt_binding_files(
        self,
        receipt_sha256: str,
        *,
        cleanup_pending: bool = False,
    ) -> tuple[str, ...]:
        relative = f"{_RECEIPT_ROOT}/{receipt_sha256}"
        try:
            directory_fd, _name = self._open_parent(
                f"{relative}/placeholder",
                create=False,
            )
        except FileNotFoundError:
            return ()
        try:
            names: list[str] = []
            pending: list[tuple[str, tuple[int, int, int]]] = []
            for entry in os.scandir(directory_fd):
                info = entry.stat(follow_symlinks=False)
                if _PENDING_FILE_NAME.fullmatch(entry.name):
                    pending.append((entry.name, (info.st_dev, info.st_ino, info.st_size)))
                    if (
                        len(pending) > MAX_PENDING_FILES_PER_DIRECTORY_V1
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_size > MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1
                    ):
                        raise InferenceEvidenceConflictError(
                            "receipt directory has unsafe or excessive pending-file residue"
                        )
                    continue
                if not _CONTENT_FILE_NAME.fullmatch(entry.name) or not stat.S_ISREG(info.st_mode):
                    raise RepositoryFileIntegrityError(
                        "receipt evidence binding directory contains an invalid entry"
                    )
                names.append(entry.name)
                if len(names) > 2:
                    raise RepositoryFileIntegrityError(
                        "receipt evidence binding directory is excessively ambiguous"
                    )
            if pending and not cleanup_pending:
                raise InferenceEvidenceConflictError(
                    "receipt directory has uncleaned pending-file residue"
                )
            for pending_name, signature in pending:
                confirmed = os.stat(
                    pending_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(confirmed.st_mode)
                    or (
                        confirmed.st_dev,
                        confirmed.st_ino,
                        confirmed.st_size,
                    )
                    != signature
                ):
                    raise InferenceEvidenceConflictError(
                        "pending receipt file changed before cleanup"
                    )
            for pending_name, _signature in pending:
                os.unlink(pending_name, dir_fd=directory_fd)
            if pending:
                os.fsync(directory_fd)
        except OSError as exc:
            raise RepositoryFileIntegrityError("cannot inspect receipt evidence bindings") from exc
        finally:
            os.close(directory_fd)
        return tuple(sorted(names))

    def _batch_manifest(
        self, prepared: tuple[_PreparedOutcome, ...]
    ) -> tuple[_BatchManifest, str, bytes]:
        identities = tuple(
            (
                item.outcome.execution.execution_id,
                item.outcome.execution.receipt_artifact.artifact_id,
                item.outcome_sha256,
            )
            for item in prepared
        )
        payload = _batch_payload(
            repository_id=self._repository_id,
            repository_root=str(self._root),
            identities=identities,
        )
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        manifest = _BatchManifest(
            batch_id=f"inference-batch:{digest}",
            batch_sha256=digest,
            repository_id=self._repository_id,
            repository_root=str(self._root),
            outcomes=tuple(
                _BatchEntry(
                    execution_id=execution_id,
                    receipt_artifact_id=receipt_artifact_id,
                    outcome_sha256=outcome_sha256,
                )
                for execution_id, receipt_artifact_id, outcome_sha256 in identities
            ),
        )
        content = canonical_json_bytes(manifest.model_dump(mode="json"))
        if len(content) > MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1:
            raise InferenceEvidenceRepositoryError("batch manifest exceeds its fixed byte limit")
        return manifest, f"{_BATCH_ROOT}/{digest}.json", content

    def _committed_batch_manifests(self) -> tuple[_BatchManifest, ...]:
        try:
            directory_fd, _name = self._open_parent(
                f"{_BATCH_ROOT}/placeholder",
                create=False,
            )
        except FileNotFoundError:
            return ()
        try:
            names: list[str] = []
            pending_count = 0
            scan_bytes = 0
            for entry in os.scandir(directory_fd):
                info = entry.stat(follow_symlinks=False)
                if _PENDING_FILE_NAME.fullmatch(entry.name):
                    pending_count += 1
                    if (
                        pending_count > MAX_PENDING_FILES_PER_DIRECTORY_V1
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_size > MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1
                    ):
                        raise RepositoryFileIntegrityError(
                            "batch directory has unsafe or excessive pending-file residue"
                        )
                    continue
                if not _CONTENT_FILE_NAME.fullmatch(entry.name) or not stat.S_ISREG(info.st_mode):
                    raise RepositoryFileIntegrityError(
                        "batch directory contains a non-canonical manifest entry"
                    )
                if info.st_size > MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1:
                    raise RepositoryFileIntegrityError(
                        "committed batch manifest exceeds its fixed byte limit"
                    )
                names.append(entry.name)
                scan_bytes += info.st_size
                if (
                    len(names) > MAX_COMMITTED_BATCH_MANIFESTS_V1
                    or scan_bytes > MAX_COMMITTED_BATCH_SCAN_BYTES_V1
                ):
                    raise RepositoryFileIntegrityError(
                        "committed batch membership index exceeds fixed scan bounds"
                    )
        except OSError as exc:
            raise RepositoryFileIntegrityError(
                "cannot inspect committed inference batch manifests"
            ) from exc
        finally:
            os.close(directory_fd)

        manifests: list[_BatchManifest] = []
        for name in sorted(names):
            relative = f"{_BATCH_ROOT}/{name}"
            data = self._read_optional(
                relative,
                limit=MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1,
                label="committed inference batch manifest",
            )
            if data is None:
                raise RepositoryFileIntegrityError(
                    "committed inference batch manifest disappeared during membership scan"
                )
            try:
                manifest = _BatchManifest.model_validate_json(data)
            except ValueError as exc:
                raise RepositoryFileIntegrityError(
                    "committed inference batch manifest failed validation"
                ) from exc
            if (
                canonical_json_bytes(manifest.model_dump(mode="json")) != data
                or name != f"{manifest.batch_sha256}.json"
                or manifest.repository_id != self._repository_id
                or manifest.repository_root != str(self._root)
            ):
                raise RepositoryFileIntegrityError(
                    "committed inference batch manifest differs from its exact authority"
                )
            manifests.append(manifest)
        return tuple(manifests)

    def _committed_memberships(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            (
                entry.outcome_sha256,
                entry.execution_id,
                entry.receipt_artifact_id,
            )
            for manifest in self._committed_batch_manifests()
            for entry in manifest.outcomes
        )

    def _require_batch_index_capacity(
        self,
        *,
        candidate: _BatchManifest,
        candidate_bytes: bytes,
    ) -> None:
        committed = self._committed_batch_manifests()
        if any(item.batch_sha256 == candidate.batch_sha256 for item in committed):
            return
        committed_bytes = sum(
            len(canonical_json_bytes(item.model_dump(mode="json"))) for item in committed
        )
        if (
            len(committed) + 1 > MAX_COMMITTED_BATCH_MANIFESTS_V1
            or committed_bytes + len(candidate_bytes) > MAX_COMMITTED_BATCH_SCAN_BYTES_V1
        ):
            raise InferenceEvidenceRepositoryError(
                "new batch would exceed fixed committed-membership index bounds"
            )

    def _prepare_batch(
        self, outcomes: tuple[RecordedInferenceOutcome, ...]
    ) -> tuple[_PreparedOutcome, ...]:
        if not 1 <= len(outcomes) <= MAX_INFERENCE_EVIDENCE_BATCH_V1:
            raise InferenceEvidenceRepositoryError(
                "inference evidence batch count is outside the fixed v1 bound"
            )
        prepared: list[_PreparedOutcome] = []
        artifact_bytes = 0
        outcome_bytes = 0
        manifest_bytes = 0
        total_bytes = 0
        for outcome in outcomes:
            item = self._prepare(outcome)
            artifact_bytes += item.artifact_byte_count
            outcome_bytes += item.outcome_byte_count
            manifest_bytes += len(item.manifest_bytes)
            total_bytes += (
                item.artifact_byte_count
                + item.outcome_byte_count
                + len(item.manifest_bytes)
                + len(item.binding_bytes)
            )
            if artifact_bytes > MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1:
                raise InferenceEvidenceRepositoryError(
                    "inference evidence batch exceeds its aggregate artifact-byte limit"
                )
            if outcome_bytes > MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1:
                raise InferenceEvidenceRepositoryError(
                    "inference evidence batch exceeds its aggregate outcome-byte limit"
                )
            if manifest_bytes > MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1:
                raise InferenceEvidenceRepositoryError(
                    "inference evidence batch exceeds its aggregate outcome-manifest limit"
                )
            if total_bytes > MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1:
                raise InferenceEvidenceRepositoryError(
                    "inference evidence batch exceeds its aggregate total-byte limit"
                )
            prepared.append(item)
        return tuple(sorted(prepared, key=lambda item: item.outcome.execution.execution_id))

    def _preflight_prepared(self, prepared: tuple[_PreparedOutcome, ...]) -> None:
        expected_files: dict[str, bytes] = {}
        for item in prepared:
            for payload in item.outcome.artifacts:
                content = payload.content_utf8.encode("utf-8")
                prior = expected_files.setdefault(payload.artifact.path, content)
                if prior != content:
                    raise InferenceEvidenceConflictError(
                        "batch maps one artifact locator to different exact bytes"
                    )
            expected_files[item.manifest_path] = item.manifest_bytes
            expected_files[item.binding_path] = item.binding_bytes

            existing_bindings = self._receipt_binding_files(
                item.outcome.execution.receipt_artifact.sha256,
                cleanup_pending=True,
            )
            expected_name = PurePosixPath(item.binding_path).name
            if existing_bindings and existing_bindings != (expected_name,):
                raise InferenceEvidenceConflictError(
                    "receipt artifact already maps to a different or ambiguous outcome"
                )

        for relative, expected in expected_files.items():
            existing = self._read_optional(
                relative,
                limit=len(expected),
                label="inference evidence",
            )
            if existing is not None and existing != expected:
                raise InferenceEvidenceConflictError(
                    f"existing evidence differs at create-only locator {relative}"
                )

    def _validate_replay_outcomes(
        self,
        outcomes: tuple[RecordedInferenceOutcome, ...],
        *,
        cleanup_pending: bool,
    ) -> None:
        replays = tuple(
            outcome
            for outcome in outcomes
            if outcome.execution.contract.mode == InferenceExecutionMode.REPLAY
        )
        if not replays:
            return
        try:
            committed_memberships = self._committed_memberships()
            for replay in replays:
                execution = replay.execution
                source_artifact = execution.receipt.replay_source_receipt_artifact
                if source_artifact is None:
                    raise ValueError("REPLAY outcome omits its source receipt artifact")
                source = self._resolve_replay_evidence(
                    receipt_artifact=source_artifact,
                    committed_memberships=committed_memberships,
                    cleanup_pending=cleanup_pending,
                )
                execution.receipt.verify_replay_source(source.execution.receipt)
                comparable_contract = (
                    "algorithm_manifest_sha256",
                    "contract_id",
                    "contract_version",
                    "provider",
                    "model",
                    "prompt_sha256",
                    "response_schema_sha256",
                )
                if (
                    execution.task != source.execution.task
                    or execution.input_envelope != source.execution.input_envelope
                    or any(
                        getattr(execution.contract, field)
                        != getattr(source.execution.contract, field)
                        for field in comparable_contract
                    )
                ):
                    raise ValueError(
                        "REPLAY task/input/contract differs from committed LIVE evidence"
                    )
                if execution.replay_source_execution_sha256 != source.execution.execution_sha256:
                    raise ValueError(
                        "REPLAY source execution SHA differs from committed LIVE evidence"
                    )
        except (AttributeError, TypeError, ValueError) as exc:
            raise InferenceEvidenceRepositoryError(
                "REPLAY outcome lacks one exact already-committed LIVE source"
            ) from exc

    def _validate_replay_sources(self, prepared: tuple[_PreparedOutcome, ...]) -> None:
        self._validate_replay_outcomes(
            tuple(item.outcome for item in prepared),
            cleanup_pending=True,
        )

    def persist_outcome(
        self, outcome: RecordedInferenceOutcome
    ) -> RepositoryVerifiedInferenceEvidenceBatch:
        """Persist and reopen one exact outcome, returning a sealed one-item batch."""

        return self.persist_batch((outcome,))

    @staticmethod
    def _temporal_analysis_path(*, manifest_id: str, manifest_sha256: str) -> str:
        if (
            re.fullmatch(SHA256_PATTERN, manifest_sha256) is None
            or manifest_id != f"temporal-analysis:{manifest_sha256}"
        ):
            raise InferenceEvidenceRepositoryError(
                "temporal analysis manifest ID does not match its supplied SHA"
            )
        return f"{_TEMPORAL_ANALYSIS_ROOT}/{manifest_sha256}.json"

    def persist_temporal_analysis_manifest(
        self,
        *,
        manifest_id: str,
        manifest_sha256: str,
        content: bytes,
    ) -> str:
        """Create and reopen one exact temporal-analysis identity payload."""

        if not isinstance(content, bytes):
            raise TypeError("temporal analysis manifest content must be bytes")
        if len(content) > MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1:
            raise InferenceEvidenceRepositoryError(
                "temporal analysis manifest exceeds its fixed byte limit"
            )
        path = self._temporal_analysis_path(
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
        )
        if hashlib.sha256(content).hexdigest() != manifest_sha256:
            raise InferenceEvidenceRepositoryError(
                "temporal analysis manifest SHA differs from its exact bytes"
            )
        with self._exclusive_lock():
            self._create_only(
                path,
                content,
                label="temporal analysis manifest",
            )
            reopened = self._read_optional(
                path,
                limit=MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
                label="temporal analysis manifest",
            )
        if reopened != content:
            raise InferenceEvidenceConflictError(
                "reopened temporal analysis manifest differs from its exact input bytes"
            )
        return path

    def resolve_temporal_analysis_manifest(
        self,
        *,
        manifest_id: str,
        manifest_sha256: str,
    ) -> bytes:
        """Reopen one exact temporal-analysis identity payload."""

        path = self._temporal_analysis_path(
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
        )
        with self._exclusive_lock():
            content = self._read_optional(
                path,
                limit=MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
                label="temporal analysis manifest",
            )
        if content is None:
            raise InferenceEvidenceResolutionError("temporal analysis manifest is missing")
        if hashlib.sha256(content).hexdigest() != manifest_sha256:
            raise InferenceEvidenceResolutionError(
                "temporal analysis manifest SHA differs from its persisted bytes"
            )
        return content

    def persist_batch(
        self, outcomes: tuple[RecordedInferenceOutcome, ...]
    ) -> RepositoryVerifiedInferenceEvidenceBatch:
        """Persist an exact classification/dependency shard evidence set."""

        prepared = self._prepare_batch(outcomes)
        execution_ids = tuple(item.outcome.execution.execution_id for item in prepared)
        receipt_ids = tuple(
            item.outcome.execution.receipt_artifact.artifact_id for item in prepared
        )
        if len(set(execution_ids)) != len(prepared):
            raise InferenceEvidenceConflictError("batch contains duplicate execution IDs")
        if len(set(receipt_ids)) != len(prepared):
            raise InferenceEvidenceConflictError("batch contains duplicate receipt artifact IDs")
        batch_manifest, batch_path, batch_bytes = self._batch_manifest(prepared)
        aggregate_total = sum(
            item.artifact_byte_count
            + item.outcome_byte_count
            + len(item.manifest_bytes)
            + len(item.binding_bytes)
            for item in prepared
        ) + len(batch_bytes)
        if aggregate_total > MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1:
            raise InferenceEvidenceRepositoryError(
                "inference evidence batch and commit marker exceed the aggregate total-byte limit"
            )

        with self._exclusive_lock():
            try:
                self._preflight_prepared(prepared)
                self._validate_replay_sources(prepared)
                existing_batch = self._read_optional(
                    batch_path,
                    limit=len(batch_bytes),
                    label="inference evidence batch manifest",
                )
                if existing_batch is not None and existing_batch != batch_bytes:
                    raise InferenceEvidenceConflictError(
                        "existing batch manifest differs at its content-addressed locator"
                    )
                self._require_batch_index_capacity(
                    candidate=batch_manifest,
                    candidate_bytes=batch_bytes,
                )
                for item in prepared:
                    for payload in item.outcome.artifacts:
                        self._create_only(
                            payload.artifact.path,
                            payload.content_utf8.encode("utf-8"),
                            label="inference artifact",
                        )
                    self._create_only(
                        item.manifest_path,
                        item.manifest_bytes,
                        label="outcome manifest",
                    )
                    self._create_only(
                        item.binding_path,
                        item.binding_bytes,
                        label="receipt binding",
                    )
                # The batch manifest is the durable commit marker and is deliberately written last.
                self._create_only(
                    batch_path,
                    batch_bytes,
                    label="inference evidence batch manifest",
                )
                reopened = self._resolve_batch(
                    batch_id=batch_manifest.batch_id,
                    batch_sha256=batch_manifest.batch_sha256,
                )
            except InferenceEvidenceRepositoryError:
                raise
            except (OSError, RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
                raise InferenceEvidenceRepositoryError(
                    "inference evidence batch failed durable persistence verification"
                ) from exc

        if reopened != tuple(item.outcome for item in prepared):
            raise InferenceEvidenceRepositoryError("reopened evidence differs from input outcomes")
        return self._mint_capability(batch_manifest)

    def _reopen_manifest(self, outcome_sha256: str) -> RecordedInferenceOutcome:
        path = f"{_OUTCOME_ROOT}/{outcome_sha256}.json"
        data = self._read_optional(
            path,
            limit=MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1,
            label="outcome manifest",
        )
        if data is None:
            raise InferenceEvidenceResolutionError("outcome manifest is missing")
        try:
            manifest = _OutcomeManifest.model_validate_json(data)
        except ValueError as exc:
            raise InferenceEvidenceResolutionError("outcome manifest failed validation") from exc
        if canonical_json_bytes(manifest.model_dump(mode="json")) != data:
            raise InferenceEvidenceResolutionError("outcome manifest is not exact canonical JSON")
        if (
            manifest.repository_id != self._repository_id
            or manifest.outcome_sha256 != outcome_sha256
        ):
            raise InferenceEvidenceResolutionError(
                "outcome manifest belongs to different repository authority"
            )
        try:
            self._validate_runtime_metadata(manifest.outcome.model_dump(mode="json"))
            for payload in manifest.outcome.artifacts:
                stored = self._read_optional(
                    payload.artifact.path,
                    limit=payload.artifact.byte_count,
                    label="inference artifact",
                )
                if stored != payload.content_utf8.encode("utf-8"):
                    raise InferenceEvidenceResolutionError(
                        "persisted inference artifact bytes are missing or substituted"
                    )
            receipt = manifest.outcome.execution.receipt_artifact
            binding_names = self._receipt_binding_files(receipt.sha256)
            if binding_names != (f"{outcome_sha256}.json",):
                raise InferenceEvidenceResolutionError(
                    "outcome has missing or ambiguous receipt-to-execution authority"
                )
            binding_path = f"{_RECEIPT_ROOT}/{receipt.sha256}/{binding_names[0]}"
            binding_bytes = self._read_optional(
                binding_path,
                limit=MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1,
                label="receipt binding",
            )
            if binding_bytes is None:
                raise InferenceEvidenceResolutionError("outcome receipt binding is missing")
            binding = _ReceiptBinding.model_validate_json(binding_bytes)
            if (
                canonical_json_bytes(binding.model_dump(mode="json")) != binding_bytes
                or binding.repository_id != self._repository_id
                or binding.receipt_artifact != receipt
                or binding.execution_id != manifest.execution_id
                or binding.execution_sha256 != manifest.execution_sha256
                or binding.outcome_sha256 != outcome_sha256
                or binding.outcome_manifest_path != path
            ):
                raise InferenceEvidenceResolutionError(
                    "outcome receipt binding differs from its exact manifest"
                )
        except (RepositoryFileBoundaryError, RepositoryFileIntegrityError) as exc:
            raise InferenceEvidenceResolutionError(
                "persisted inference artifact failed safe reopen"
            ) from exc
        return manifest.outcome

    def _resolve_replay_evidence(
        self,
        *,
        receipt_artifact: ManagedArtifactRef,
        committed_memberships: frozenset[tuple[str, str, str]] | None = None,
        cleanup_pending: bool = False,
    ) -> RecordedInferenceOutcome:
        try:
            if (
                receipt_artifact.kind != ManagedArtifactKind.INFERENCE_RECEIPT
                or receipt_artifact.path != f"receipts/inference/{receipt_artifact.sha256}.json"
            ):
                raise InferenceEvidenceResolutionError(
                    "replay receipt does not use its exact content-addressed locator"
                )
            names = self._receipt_binding_files(
                receipt_artifact.sha256,
                cleanup_pending=cleanup_pending,
            )
            if len(names) != 1:
                raise InferenceEvidenceResolutionError(
                    "replay receipt has missing or ambiguous persisted evidence"
                )
            relative = f"{_RECEIPT_ROOT}/{receipt_artifact.sha256}/{names[0]}"
            data = self._read_optional(
                relative,
                limit=MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1,
                label="receipt binding",
            )
            if data is None:
                raise InferenceEvidenceResolutionError("replay receipt binding is missing")
            binding = _ReceiptBinding.model_validate_json(data)
            if canonical_json_bytes(binding.model_dump(mode="json")) != data:
                raise InferenceEvidenceResolutionError(
                    "replay receipt binding is not exact canonical JSON"
                )
            if (
                binding.repository_id != self._repository_id
                or binding.receipt_artifact != receipt_artifact
                or names[0] != f"{binding.outcome_sha256}.json"
            ):
                raise InferenceEvidenceResolutionError(
                    "replay receipt binding differs from the requested artifact"
                )
            outcome = self._reopen_manifest(binding.outcome_sha256)
            execution = outcome.execution
            if (
                execution.receipt_artifact != receipt_artifact
                or execution.execution_id != binding.execution_id
                or execution.execution_sha256 != binding.execution_sha256
            ):
                raise InferenceEvidenceResolutionError(
                    "reopened outcome differs from the immutable receipt binding"
                )
            if (
                execution.contract.mode != InferenceExecutionMode.LIVE
                or execution.receipt.mode != InferenceExecutionMode.LIVE
            ):
                raise InferenceEvidenceResolutionError(
                    "replay evidence source must be a persisted LIVE outcome"
                )
            memberships = (
                self._committed_memberships()
                if committed_memberships is None
                else committed_memberships
            )
            membership = (
                binding.outcome_sha256,
                execution.execution_id,
                receipt_artifact.artifact_id,
            )
            if membership not in memberships:
                raise InferenceEvidenceResolutionError(
                    "replay receipt outcome is not a member of any valid committed batch"
                )
            return outcome
        except InferenceEvidenceResolutionError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise InferenceEvidenceResolutionError(
                "persisted replay evidence failed closed verification"
            ) from exc

    def resolve_replay_evidence(
        self,
        *,
        receipt_artifact: ManagedArtifactRef,
    ) -> RecordedInferenceOutcome:
        """Resolve one receipt to exactly one committed, complete LIVE outcome."""

        with self._exclusive_lock():
            return self._resolve_replay_evidence(
                receipt_artifact=receipt_artifact,
                cleanup_pending=True,
            )

    def _resolve_batch(
        self,
        *,
        batch_id: str,
        batch_sha256: str,
    ) -> tuple[RecordedInferenceOutcome, ...]:
        """Reopen an exact durable batch using only its public ID/SHA reference."""

        if batch_id != f"inference-batch:{batch_sha256}":
            raise InferenceEvidenceResolutionError("batch ID does not match its supplied SHA")
        path = f"{_BATCH_ROOT}/{batch_sha256}.json"
        try:
            data = self._read_optional(
                path,
                limit=MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1,
                label="inference evidence batch manifest",
            )
            if data is None:
                raise InferenceEvidenceResolutionError("inference evidence batch is missing")
            manifest = _BatchManifest.model_validate_json(data)
            if canonical_json_bytes(manifest.model_dump(mode="json")) != data:
                raise InferenceEvidenceResolutionError("batch manifest is not canonical JSON")
            if (
                manifest.batch_id != batch_id
                or manifest.batch_sha256 != batch_sha256
                or manifest.repository_id != self._repository_id
                or manifest.repository_root != str(self._root)
            ):
                raise InferenceEvidenceResolutionError(
                    "batch manifest differs from repository/reference authority"
                )
            outcomes = tuple(
                self._reopen_manifest(item.outcome_sha256) for item in manifest.outcomes
            )
            for entry, outcome in zip(manifest.outcomes, outcomes, strict=True):
                if (
                    outcome.execution.execution_id != entry.execution_id
                    or outcome.execution.receipt_artifact.artifact_id != entry.receipt_artifact_id
                ):
                    raise InferenceEvidenceResolutionError(
                        "batch entry differs from its reopened exact outcome"
                    )
            self._validate_replay_outcomes(outcomes, cleanup_pending=False)
            return outcomes
        except InferenceEvidenceResolutionError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise InferenceEvidenceResolutionError(
                "durable inference evidence batch failed closed verification"
            ) from exc

    def resolve_batch(
        self,
        *,
        batch_id: str,
        batch_sha256: str,
    ) -> tuple[RecordedInferenceOutcome, ...]:
        """Reopen an exact batch only after any concurrent durable write completes."""

        with self._exclusive_lock():
            return self._resolve_batch(
                batch_id=batch_id,
                batch_sha256=batch_sha256,
            )

    def resolve_verified_batch(
        self,
        *,
        batch_id: str,
        batch_sha256: str,
    ) -> tuple[
        tuple[RecordedInferenceOutcome, ...],
        RepositoryVerifiedInferenceEvidenceBatch,
    ]:
        """Reopen one committed batch and mint fresh process-local authority."""

        with self._exclusive_lock():
            outcomes = self._resolve_batch(
                batch_id=batch_id,
                batch_sha256=batch_sha256,
            )
            prepared = self._prepare_batch(outcomes)
            manifest, path, content = self._batch_manifest(prepared)
            if manifest.batch_id != batch_id or manifest.batch_sha256 != batch_sha256:
                raise InferenceEvidenceResolutionError(
                    "reopened batch content differs from its requested public identity"
                )
            self._synchronize_existing_create_only(
                path,
                content,
                label="inference evidence batch manifest",
            )
            durable_outcomes = self._resolve_batch(
                batch_id=batch_id,
                batch_sha256=batch_sha256,
            )
            if durable_outcomes != outcomes:
                raise InferenceEvidenceResolutionError(
                    "durably synchronized batch differs from its first exact reopen"
                )
            capability = self._mint_capability(manifest)
        return durable_outcomes, capability

    def _mint_capability(
        self, manifest: _BatchManifest
    ) -> RepositoryVerifiedInferenceEvidenceBatch:
        payload = manifest.model_dump(mode="json")
        seal = hmac.new(
            _CAPABILITY_SECRET,
            canonical_json_bytes(payload),
            hashlib.sha256,
        ).hexdigest()
        return RepositoryVerifiedInferenceEvidenceBatch(
            batch_id=manifest.batch_id,
            batch_sha256=manifest.batch_sha256,
            repository_id=self._repository_id,
            repository_root=str(self._root),
            execution_ids=tuple(item.execution_id for item in manifest.outcomes),
            receipt_artifact_ids=tuple(item.receipt_artifact_id for item in manifest.outcomes),
            outcome_sha256s=tuple(item.outcome_sha256 for item in manifest.outcomes),
            _token=_CAPABILITY_TOKEN,
            _seal=seal,
        )

    def verify_batch(
        self,
        *,
        capability: RepositoryVerifiedInferenceEvidenceBatch,
        outcomes: tuple[RecordedInferenceOutcome, ...],
    ) -> tuple[RecordedInferenceOutcome, ...]:
        """Reopen an authenticated capability against one exact outcome set."""

        if not isinstance(capability, RepositoryVerifiedInferenceEvidenceBatch):
            raise InferenceEvidenceResolutionError("invalid inference evidence capability type")
        if capability._token is not _CAPABILITY_TOKEN:
            raise InferenceEvidenceResolutionError(
                "inference evidence capability was not repository-created"
            )
        prepared = self._prepare_batch(outcomes)
        identities = tuple(
            (
                item.outcome.execution.execution_id,
                item.outcome.execution.receipt_artifact.artifact_id,
                item.outcome_sha256,
            )
            for item in prepared
        )
        expected_manifest, _path, _content = self._batch_manifest(prepared)
        expected_seal = hmac.new(
            _CAPABILITY_SECRET,
            canonical_json_bytes(expected_manifest.model_dump(mode="json")),
            hashlib.sha256,
        ).hexdigest()
        if (
            capability.repository_id != self._repository_id
            or capability.repository_root != str(self._root)
            or capability.batch_id != expected_manifest.batch_id
            or capability.batch_sha256 != expected_manifest.batch_sha256
            or capability.execution_ids != tuple(item[0] for item in identities)
            or capability.receipt_artifact_ids != tuple(item[1] for item in identities)
            or capability.outcome_sha256s != tuple(item[2] for item in identities)
            or not hmac.compare_digest(capability._seal, expected_seal)
        ):
            raise InferenceEvidenceResolutionError(
                "capability does not bind this repository and exact outcome batch"
            )
        reopened = self.resolve_batch(
            batch_id=capability.batch_id,
            batch_sha256=capability.batch_sha256,
        )
        if reopened != tuple(item.outcome for item in prepared):
            raise InferenceEvidenceResolutionError(
                "capability repository reopen differs from the exact outcome batch"
            )
        return reopened


__all__ = [
    "FilesystemInferenceEvidenceRepository",
    "InferenceEvidenceConflictError",
    "InferenceEvidenceRepositoryError",
    "InferenceEvidenceResolutionError",
    "InferenceEvidenceUnsupportedPlatformError",
    "MAX_COMMITTED_BATCH_MANIFESTS_V1",
    "MAX_COMMITTED_BATCH_SCAN_BYTES_V1",
    "MAX_INFERENCE_BATCH_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_ARTIFACT_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_OUTCOME_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_TOTAL_BYTES_V1",
    "MAX_INFERENCE_EVIDENCE_BATCH_V1",
    "MAX_INFERENCE_INPUT_METADATA_DEPTH_V1",
    "MAX_INFERENCE_INPUT_METADATA_NODES_V1",
    "MAX_INFERENCE_OUTCOME_MANIFEST_BYTES_V1",
    "MAX_INFERENCE_RECEIPT_BINDING_BYTES_V1",
    "MAX_PENDING_FILES_PER_DIRECTORY_V1",
    "RepositoryVerifiedInferenceEvidenceBatch",
]
