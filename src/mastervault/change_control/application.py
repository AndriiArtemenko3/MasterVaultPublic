"""Stable synchronous application boundary for generic change control.

The façade owns orchestration only.  Filesystem resolution, legacy-index
attestation, durable authority, and capability minting remain at their owning
boundaries.  In particular, no SQLite authority store is created until the
existing workspace and its legacy index have both been verified read-only.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationError,
    ChangeControlApplicationIntegrityError,
    ChangeControlApplicationUnsupportedOperationError,
    ChangeControlApplicationUsageError,
    raise_mapped_application_error,
)
from mastervault.change_control.legacy_index import (
    LegacyIndexAttestation,
    LegacyIndexIntegrityError,
    LegacyIndexPlatformUnsupportedError,
    attest_legacy_sqlite_index,
    open_legacy_sqlite_index_attestation_guard,
)
from mastervault.change_control.managed_review import AuthorityRevisionBinding
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.models import aggregate_sha256, canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunView,
)
from mastervault.change_control.store import ChangeControlBusyError
from mastervault.change_control.workspace_bootstrap import (
    LegacyIndexReadinessReceipt,
    WorkspaceBootstrapAggregateSnapshot,
    WorkspaceBootstrapIntent,
    WorkspaceBootstrapState,
    WorkspaceInventoryReceipt,
    create_workspace_bootstrap_evidence_verifier,
    verify_workspace_bootstrap_evidence,
)
from mastervault.change_control.workspace_bootstrap_repository import (
    BootstrapSourceRoot,
    ResolvedWorkspaceBootstrap,
    WorkspaceBootstrapManifestError,
    WorkspaceBootstrapPlatformUnsupportedError,
    WorkspaceBootstrapRepositoryError,
    open_workspace_bootstrap_evidence_guard,
    resolve_workspace_bootstrap,
)
from mastervault.config import Settings
from mastervault.providers import get_embedding_provider
from mastervault.storage.base import SCHEMA_VERSION

FailureHook = Callable[[str], None]

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_RUN_ID_RE = re.compile(r"^operatorrun:[0-9a-f]{64}$")
_DERIVATION_NAMESPACE = "mastervault.change-control-application.bootstrap.v1"

SCHEMA_INITIALIZED = "schema-initialized"
BOOTSTRAP_INTENT_CLAIMED = "bootstrap-intent-claimed"
AGGREGATE_CREATED = "aggregate-created"
WORKSPACE_INVENTORY_RECORDED = "workspace-inventory-recorded"
LEGACY_INDEX_READINESS_RECORDED = "legacy-index-readiness-recorded"
AUTHORITY_HANDOFF_STARTED = "authority-handoff-started"
GENERATION_ZERO_INITIALIZED = "generation-zero-initialized"
OPERATOR_RUN_CREATED = "operator-run-created"


@dataclass(frozen=True)
class BootstrapResult:
    """Typed result after bootstrap authority and navigation are complete."""

    bootstrap_state: WorkspaceBootstrapState
    authority: AuthorityRevisionBinding
    operator_run: OperatorRunView
    legacy_index_attestation: LegacyIndexAttestation


@dataclass(frozen=True)
class _ApplicationPaths:
    workspace: Path
    vault: Path
    legacy_index: Path
    change_control_root: Path
    state_db: Path
    checkpoint_db: Path
    generation_root: Path


_BOOTSTRAP_LOCKS_GUARD = threading.Lock()
_BOOTSTRAP_LOCKS: dict[Path, threading.Lock] = {}


def _bootstrap_lock(path: Path) -> threading.Lock:
    with _BOOTSTRAP_LOCKS_GUARD:
        return _BOOTSTRAP_LOCKS.setdefault(path, threading.Lock())


def _notify(hook: FailureHook | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _derived_operation_id(operation_id: str, stage: str) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": _DERIVATION_NAMESPACE,
                "parent_operation_id": operation_id,
                "stage": stage,
            }
        )
    ).hexdigest()
    return f"m4-bootstrap:{stage}:{digest}"


def _lexical_absolute(path: Path, *, label: str) -> Path:
    if ".." in path.parts:
        raise ChangeControlApplicationUsageError(f"{label} cannot contain '..'")
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError) as exc:
        raise ChangeControlApplicationUsageError(f"{label} is not a valid path") from exc


def _canonical_without_creation(path: Path, *, label: str) -> Path:
    lexical = _lexical_absolute(path, label=label)
    try:
        resolved = lexical.resolve(strict=False)
    except OSError as exc:
        raise ChangeControlApplicationIntegrityError(f"{label} cannot be resolved safely") from exc
    if resolved != lexical:
        raise ChangeControlApplicationIntegrityError(
            f"{label} contains a symbolic-link or path alias"
        )
    return lexical


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _existing_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ChangeControlApplicationIntegrityError(
            f"configured path cannot be inspected: {path}"
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise ChangeControlApplicationIntegrityError(
            f"configured path cannot be a symbolic link: {path}"
        )
    return info.st_dev, info.st_ino


def _require_platform() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "getuid", "pread")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise ChangeControlApplicationUnsupportedOperationError(
            "generic workspace bootstrap requires POSIX no-follow filesystem guarantees"
        )
    if (
        os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise ChangeControlApplicationUnsupportedOperationError(
            "generic workspace bootstrap requires descriptor-relative no-follow inspection"
        )


def _preflight_paths(settings: Settings) -> _ApplicationPaths:
    _require_platform()
    workspace = _canonical_without_creation(
        settings.paths.workspace,
        label="workspace path",
    )
    try:
        workspace_info = workspace.lstat()
    except FileNotFoundError as exc:
        raise ChangeControlApplicationIntegrityError("configured workspace does not exist") from exc
    except OSError as exc:
        raise ChangeControlApplicationIntegrityError(
            "configured workspace cannot be inspected"
        ) from exc
    if not stat.S_ISDIR(workspace_info.st_mode):
        raise ChangeControlApplicationIntegrityError("configured workspace is not a directory")
    if {part.casefold() for part in workspace.parts} & {"evals", "golden"}:
        raise ChangeControlApplicationUsageError(
            "configured workspace cannot be evaluator or golden data"
        )

    vault = _canonical_without_creation(settings.paths.vault_dir, label="vault path")
    legacy_index = _canonical_without_creation(
        settings.paths.sqlite_path,
        label="legacy index path",
    )
    state_db = _canonical_without_creation(
        settings.paths.change_control_db_path,
        label="change-control state path",
    )
    checkpoint_db = _canonical_without_creation(
        settings.paths.change_control_checkpoint_path,
        label="change-control checkpoint path",
    )
    generation_root = _canonical_without_creation(
        settings.paths.change_control_generation_root,
        label="managed generation root",
    )
    change_control_root = state_db.parent

    expected = {
        "vault path": workspace / "vault",
        "legacy index path": workspace / "index.db",
        "change-control state path": workspace / "change_control" / "state.sqlite3",
        "change-control checkpoint path": (workspace / "change_control" / "checkpoints.sqlite3"),
        "managed generation root": workspace / "change_control" / "generations",
    }
    actual = {
        "vault path": vault,
        "legacy index path": legacy_index,
        "change-control state path": state_db,
        "change-control checkpoint path": checkpoint_db,
        "managed generation root": generation_root,
    }
    for label, expected_path in expected.items():
        if actual[label] != expected_path:
            raise ChangeControlApplicationUsageError(
                f"{label} is outside the supported workspace layout"
            )

    if not all(
        path.is_relative_to(workspace)
        for path in (vault, legacy_index, state_db, checkpoint_db, generation_root)
    ):
        raise ChangeControlApplicationUsageError(
            "configured change-control paths must remain inside the workspace"
        )
    if state_db.parent != checkpoint_db.parent or generation_root.parent != state_db.parent:
        raise ChangeControlApplicationUsageError(
            "change-control state, checkpoint, and generation paths are not co-located"
        )
    protected_pairs = (
        (vault, legacy_index),
        (vault, change_control_root),
        (legacy_index, state_db),
        (legacy_index, checkpoint_db),
        (legacy_index, generation_root),
        (state_db, checkpoint_db),
        (state_db, generation_root),
        (checkpoint_db, generation_root),
    )
    if any(_overlaps(left, right) for left, right in protected_pairs):
        raise ChangeControlApplicationUsageError(
            "legacy and managed storage paths must be pairwise disjoint"
        )

    inode_paths = (vault, legacy_index, state_db, checkpoint_db, generation_root)
    identities = tuple((path, _existing_identity(path)) for path in inode_paths)
    for index, (left, left_identity) in enumerate(identities):
        if left_identity is None:
            continue
        for right, right_identity in identities[index + 1 :]:
            if right_identity is not None and left_identity == right_identity:
                raise ChangeControlApplicationIntegrityError(
                    f"configured paths alias the same filesystem object: {left} and {right}"
                )

    return _ApplicationPaths(
        workspace=workspace,
        vault=vault,
        legacy_index=legacy_index,
        change_control_root=change_control_root,
        state_db=state_db,
        checkpoint_db=checkpoint_db,
        generation_root=generation_root,
    )


def _manifest_path(paths: _ApplicationPaths, manifest_path: Path) -> Path:
    if not isinstance(manifest_path, Path):
        raise ChangeControlApplicationUsageError("manifest_path must be a pathlib.Path")
    if ".." in manifest_path.parts:
        raise ChangeControlApplicationUsageError("manifest_path cannot contain '..'")
    candidate = manifest_path if manifest_path.is_absolute() else paths.workspace / manifest_path
    candidate = _canonical_without_creation(candidate, label="bootstrap manifest path")
    if not candidate.is_relative_to(paths.workspace):
        raise ChangeControlApplicationUsageError(
            "bootstrap manifest must remain inside the workspace"
        )
    if candidate.is_relative_to(paths.vault) or candidate.is_relative_to(paths.change_control_root):
        raise ChangeControlApplicationUsageError(
            "bootstrap manifest must be outside canonical and managed data roots"
        )
    if {part.casefold() for part in candidate.parts} & {"evals", "golden"}:
        raise ChangeControlApplicationUsageError(
            "bootstrap manifest cannot be evaluator or golden data"
        )
    return candidate


def _validate_operation_id(operation_id: str) -> None:
    if not isinstance(operation_id, str) or _OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise ChangeControlApplicationUsageError("operation_id is not canonical")


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ChangeControlApplicationUsageError("run_id is not canonical")


class ChangeControlApplication:
    """Supported synchronous façade over the generic SQLite authority slice."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def _preflight_backend(self) -> None:
        backend = self._settings.storage.backend
        database_url = self._settings.database_url
        if backend == "postgres" or (backend == "auto" and database_url is not None):
            raise ChangeControlApplicationUnsupportedOperationError(
                "generic workspace bootstrap supports SQLite authority only"
            )
        if backend not in {"auto", "sqlite"}:
            raise ChangeControlApplicationUsageError("storage backend is not supported")

    def _resolve(
        self,
        *,
        paths: _ApplicationPaths,
        manifest_path: Path,
        source_roots: tuple[BootstrapSourceRoot, ...],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> ResolvedWorkspaceBootstrap:
        resolved = resolve_workspace_bootstrap(
            workspace_root=paths.workspace,
            manifest_path=manifest_path,
            source_roots=source_roots,
            index_schema_version=SCHEMA_VERSION,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        if (
            resolved.workspace_root != paths.workspace
            or resolved.legacy_index_path != paths.legacy_index
        ):
            raise ChangeControlApplicationIntegrityError(
                "workspace resolver returned paths outside the configured authority boundary"
            )
        return resolved

    @staticmethod
    def _attest(resolved: ResolvedWorkspaceBootstrap) -> LegacyIndexAttestation:
        expected = resolved.inventory.legacy_index
        return attest_legacy_sqlite_index(
            index_path=resolved.legacy_index_path,
            notes=resolved.exact_vault_notes,
            embedding_model_version=expected.embedding_model,
            embedding_dimensions=expected.embedding_dimensions,
            expected_index_file_sha256=expected.index_file_sha256,
            expected_index_file_byte_count=expected.index_file_byte_count,
        )

    def bootstrap(
        self,
        manifest_path: Path,
        operation_id: str,
        *,
        source_roots: tuple[BootstrapSourceRoot, ...] = (),
        failure_hook: FailureHook | None = None,
    ) -> BootstrapResult:
        """Adopt one exact existing SQLite workspace as managed generation zero."""

        try:
            self._preflight_backend()
            _validate_operation_id(operation_id)
            paths = _preflight_paths(self._settings)
            with _bootstrap_lock(paths.state_db):
                return self._bootstrap_locked(
                    paths=paths,
                    manifest_path=manifest_path,
                    operation_id=operation_id,
                    source_roots=source_roots,
                    failure_hook=failure_hook,
                )
        except ChangeControlApplicationError:
            raise
        except WorkspaceBootstrapManifestError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except (
            WorkspaceBootstrapPlatformUnsupportedError,
            LegacyIndexPlatformUnsupportedError,
        ) as exc:
            raise ChangeControlApplicationUnsupportedOperationError(str(exc)) from exc
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except (WorkspaceBootstrapRepositoryError, LegacyIndexIntegrityError) as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def _bootstrap_locked(
        self,
        *,
        paths: _ApplicationPaths,
        manifest_path: Path,
        operation_id: str,
        source_roots: tuple[BootstrapSourceRoot, ...],
        failure_hook: FailureHook | None,
    ) -> BootstrapResult:
        exact_manifest_path = _manifest_path(paths, manifest_path)
        embedder = get_embedding_provider(self._settings)
        resolved = self._resolve(
            paths=paths,
            manifest_path=exact_manifest_path,
            source_roots=source_roots,
            embedding_model=embedder.model_version,
            embedding_dimensions=embedder.dimensions,
        )
        attestation = self._attest(resolved)

        intent = WorkspaceBootstrapIntent.create(
            operation_id=operation_id,
            aggregate_id=resolved.aggregate.aggregate_id,
            inventory=resolved.inventory,
        )
        aggregate_operation_id = _derived_operation_id(operation_id, "aggregate")
        inventory_operation_id = _derived_operation_id(operation_id, "inventory")
        index_operation_id = _derived_operation_id(operation_id, "legacy-index")

        store = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
        try:
            store.init_schema()
            _notify(failure_hook, SCHEMA_INITIALIZED)
            state = store.claim_workspace_bootstrap(
                intent=intent,
                inventory=resolved.inventory,
            )
            _notify(failure_hook, BOOTSTRAP_INTENT_CLAIMED)

            aggregate_commit = store.create(
                resolved.aggregate,
                operation_id=aggregate_operation_id,
            )
            if not (
                aggregate_commit.aggregate_id == resolved.aggregate.aggregate_id
                and aggregate_commit.revision == 1
                and aggregate_commit.aggregate_sha256 == aggregate_sha256(resolved.aggregate)
            ):
                raise ChangeControlApplicationIntegrityError(
                    "workspace aggregate commit does not reproduce exact revision one"
                )
            _notify(failure_hook, AGGREGATE_CREATED)

            inventory_receipt = state.inventory_receipt
            if inventory_receipt is None:
                inventory_receipt = WorkspaceInventoryReceipt.create(
                    operation_id=inventory_operation_id,
                    bootstrap_id=intent.bootstrap_id,
                    aggregate_operation_id=aggregate_operation_id,
                    aggregate_id=aggregate_commit.aggregate_id,
                    aggregate_revision=aggregate_commit.revision,
                    aggregate_sha256=aggregate_commit.aggregate_sha256,
                    inventory_id=resolved.inventory.inventory_id,
                    inventory_sha256=resolved.inventory.inventory_sha256,
                    recorded_at=_now(),
                )
            state = store.record_workspace_inventory(inventory_receipt)
            if state.inventory_receipt is None:
                raise ChangeControlApplicationIntegrityError(
                    "workspace inventory receipt was not committed"
                )
            inventory_receipt = state.inventory_receipt
            _notify(failure_hook, WORKSPACE_INVENTORY_RECORDED)

            index_receipt = state.index_readiness_receipt
            if index_receipt is None:
                index_receipt = LegacyIndexReadinessReceipt.create(
                    operation_id=index_operation_id,
                    bootstrap_id=intent.bootstrap_id,
                    inventory_receipt_id=inventory_receipt.receipt_id,
                    inventory_receipt_sha256=inventory_receipt.receipt_sha256,
                    index_logical_fingerprint=(attestation.logical_index_fingerprint),
                    index_file_sha256=attestation.index_file_sha256,
                    index_file_byte_count=attestation.index_file_byte_count,
                    index_schema_version=attestation.storage_schema_version,
                    embedding_model=attestation.embedding_model_version,
                    embedding_dimensions=attestation.embedding_dimensions,
                    ready_at=_now(),
                )
            state = store.record_legacy_index_readiness(index_receipt)
            if state.index_readiness_receipt is None:
                raise ChangeControlApplicationIntegrityError(
                    "legacy-index readiness receipt was not committed"
                )
            _notify(failure_hook, LEGACY_INDEX_READINESS_RECORDED)
        finally:
            store.close()

        with open_workspace_bootstrap_evidence_guard(
            workspace_root=paths.workspace,
            manifest_path=exact_manifest_path,
            source_roots=source_roots,
            index_schema_version=SCHEMA_VERSION,
            embedding_model=embedder.model_version,
            embedding_dimensions=embedder.dimensions,
        ) as workspace_guard:
            fresh_resolved = workspace_guard.resolved
            expected_index = fresh_resolved.inventory.legacy_index
            with open_legacy_sqlite_index_attestation_guard(
                index_path=fresh_resolved.legacy_index_path,
                notes=fresh_resolved.exact_vault_notes,
                embedding_model_version=expected_index.embedding_model,
                embedding_dimensions=expected_index.embedding_dimensions,
                expected_index_file_sha256=expected_index.index_file_sha256,
                expected_index_file_byte_count=expected_index.index_file_byte_count,
            ) as index_guard:
                fresh_attestation = index_guard.attestation
                store = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
                try:
                    fresh_state = store.get_workspace_bootstrap(intent.bootstrap_id)
                    fresh_snapshot = store.load(intent.aggregate_id)
                    if fresh_state is None or fresh_snapshot is None:
                        raise ChangeControlApplicationIntegrityError(
                            "durable workspace bootstrap cannot be reopened"
                        )

                    def guard_evidence() -> None:
                        workspace_guard.verify()
                        index_guard.verify()

                    capability = verify_workspace_bootstrap_evidence(
                        state=fresh_state,
                        resolved_inventory=fresh_resolved.inventory,
                        resolved_aggregate=fresh_resolved.aggregate,
                        persisted_snapshot=cast(
                            WorkspaceBootstrapAggregateSnapshot,
                            fresh_snapshot,
                        ),
                        legacy_attestation=fresh_attestation,
                        evidence_verifier=create_workspace_bootstrap_evidence_verifier(
                            workspace_guard,
                            index_guard,
                        ),
                    )
                    _notify(failure_hook, AUTHORITY_HANDOFF_STARTED)

                    authority = store.initialize_workspace_generation_zero(
                        verified_workspace_bootstrap=capability,
                        evidence_guard=guard_evidence,
                    )
                    guard_evidence()
                    _notify(failure_hook, GENERATION_ZERO_INITIALIZED)
                    run_command = OperatorRunCommand.create(
                        operation_id=_derived_operation_id(operation_id, "operator-run"),
                        aggregate_id=authority.aggregate_id,
                        base_authority_id=authority.authority_id,
                        base_authority_revision=authority.authority_revision,
                        base_active_pointer_sha256=authority.active_pointer_sha256,
                    )
                    run = store.create_operator_run(run_command)
                    _notify(failure_hook, OPERATOR_RUN_CREATED)

                    final_inventory_receipt, final_index_receipt = fresh_state.require_complete()
                    targets = (
                        (
                            OperatorRunLinkKind.BOOTSTRAP_INTENT,
                            fresh_state.intent.bootstrap_id,
                            fresh_state.intent.intent_sha256,
                        ),
                        (
                            OperatorRunLinkKind.WORKSPACE_INVENTORY,
                            final_inventory_receipt.receipt_id,
                            final_inventory_receipt.receipt_sha256,
                        ),
                        (
                            OperatorRunLinkKind.LEGACY_INDEX_READINESS,
                            final_index_receipt.receipt_id,
                            final_index_receipt.receipt_sha256,
                        ),
                        (
                            OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
                            authority.authority_id,
                            authority.active_pointer_sha256,
                        ),
                    )
                    for kind, target_id, target_sha256 in targets:
                        link_command = OperatorRunLinkCommand.create(
                            operation_id=_derived_operation_id(
                                operation_id,
                                f"operator-link-{kind.value}",
                            ),
                            run_id=run.record.command.run_id,
                            kind=kind,
                            target_id=target_id,
                            target_sha256=target_sha256,
                        )
                        run = store.record_operator_run_link(link_command)
                        _notify(failure_hook, f"operator-link-{kind.value}-recorded")
                    if tuple(item.command.kind for item in run.links) != tuple(
                        kind for kind, _target_id, _target_sha256 in targets
                    ):
                        raise ChangeControlApplicationIntegrityError(
                            "operator run does not bind the complete bootstrap chain"
                        )
                finally:
                    store.close()

        return BootstrapResult(
            bootstrap_state=fresh_state,
            authority=authority,
            operator_run=run,
            legacy_index_attestation=fresh_attestation,
        )

    def get_status(self, run_id: str) -> OperatorRunView:
        """Return existing navigation without creating or migrating authority."""

        try:
            self._preflight_backend()
            _validate_run_id(run_id)
            paths = _preflight_paths(self._settings)
            store = SqliteManagedChangeControlStore(
                paths.state_db,
                secure_open=True,
                read_only=True,
            )
            try:
                result = store.get_operator_run(run_id)
            finally:
                store.close()
            if result is None:
                raise ChangeControlApplicationUsageError("operator run does not exist")
            return result
        except ChangeControlApplicationError:
            raise
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)


__all__ = [
    "BootstrapSourceRoot",
    "BootstrapResult",
    "ChangeControlApplication",
    "FailureHook",
]
