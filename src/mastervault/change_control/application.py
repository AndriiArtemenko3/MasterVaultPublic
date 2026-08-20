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
from typing import NoReturn, Protocol, cast

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
from mastervault.change_control.managed_serving import (
    ManagedServingConflictError,
    ManagedServingError,
    ManagedServingResolution,
    open_active_managed_sqlite_generation,
)
from mastervault.change_control.managed_store import (
    AuthorityVerificationContext,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import aggregate_sha256, canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunView,
)
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
    ResolvedQueryGeneration,
)
from mastervault.change_control.store import ChangeControlBusyError, ChangeControlSnapshot
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
from mastervault.evidence import EvidenceLocation
from mastervault.providers import get_embedding_provider
from mastervault.storage import get_backend
from mastervault.storage.base import SCHEMA_VERSION
from mastervault.sync.indexer import ExactVaultNoteInput

FailureHook = Callable[[str], None]


class _Closable(Protocol):
    def close(self) -> None: ...

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


def _close_failed_query_resources(
    failure: BaseException,
    *resources: _Closable | None,
) -> NoReturn:
    """Attempt every construction cleanup while retaining the original failure."""

    for resource in resources:
        if resource is None:
            continue
        try:
            resource.close()
        except BaseException:
            # Construction has already failed.  A cleanup failure must not skip
            # the remaining guards or replace the original public error.
            continue
    raise failure


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


def _query_state_exists(settings: Settings) -> bool:
    """Inspect only the configured authority locator; never create its parent."""

    state_path = _lexical_absolute(
        settings.paths.change_control_db_path,
        label="change-control state path",
    )
    try:
        state_path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ChangeControlApplicationIntegrityError(
            "configured change-control state path cannot be inspected"
        ) from exc
    return True


def _managed_query_is_configured(settings: Settings) -> bool:
    cfg = settings.query_generation
    return any(
        (
            cfg.bootstrap_manifest is not None,
            bool(cfg.source_roots),
            cfg.seed_repository_root is not None,
            cfg.evidence_repository_root is not None,
            cfg.canonical_repository_root is not None,
            cfg.temporal_analysis_manifest_sha256 is not None,
        )
    )


def _configured_source_roots(settings: Settings) -> tuple[BootstrapSourceRoot, ...]:
    try:
        return tuple(
            BootstrapSourceRoot(root_id=root_id, path=path)
            for root_id, path in sorted(settings.query_generation.source_roots.items())
        )
    except (TypeError, ValueError) as exc:
        raise ChangeControlApplicationUsageError(
            "query-generation source roots are invalid"
        ) from exc


def _existing_query_protected_paths(paths: _ApplicationPaths) -> tuple[Path, ...]:
    candidates = (paths.vault, paths.legacy_index, paths.checkpoint_db)
    return tuple(path for path in candidates if _existing_identity(path) is not None)


def _select_generation_authority(
    *,
    selection: QueryGenerationSelectionV1,
    active: AuthorityRevisionBinding,
    generation_zero: AuthorityRevisionBinding,
) -> AuthorityRevisionBinding:
    if selection.selector in {
        QueryGenerationSelector.AUTO,
        QueryGenerationSelector.ACTIVE,
    }:
        return active
    if selection.selector == QueryGenerationSelector.LEGACY:
        return generation_zero
    assert selection.generation_id is not None
    if active.active_generation.generation_id == selection.generation_id:
        return active
    if generation_zero.active_generation.generation_id == selection.generation_id:
        return generation_zero
    raise ChangeControlApplicationUsageError(
        "requested generation is not available in the v0.3 authority chain"
    )


def _query_metadata(
    *,
    selection: QueryGenerationSelectionV1,
    selected: AuthorityRevisionBinding,
    active: AuthorityRevisionBinding,
    logical_fingerprint: str,
    file_sha256: str,
    file_byte_count: int,
    schema_version: int,
    embedding_model: str,
    embedding_dimensions: int,
) -> QueryGenerationMetadataV1:
    number = selected.active_generation.generation_number
    if number not in {0, 1}:
        raise ChangeControlApplicationUnsupportedOperationError(
            "the v0.3 query resolver supports generation zero and its first successor"
        )
    return QueryGenerationMetadataV1(
        selection=selection,
        backend="sqlite",
        generation_kind=(
            QueryGenerationKind.GENERATION_ZERO
            if number == 0
            else QueryGenerationKind.MANAGED
        ),
        generation_id=selected.active_generation.generation_id,
        generation_number=number,
        active_generation_id=active.active_generation.generation_id,
        active_authority_revision=active.authority_revision,
        is_active=selected == active,
        manifest_sha256=selected.active_generation.manifest_sha256,
        index_logical_fingerprint=logical_fingerprint,
        index_file_sha256=file_sha256,
        index_file_byte_count=file_byte_count,
        storage_schema_version=schema_version,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def _guard_query_verification(callback: Callable[[], None]) -> Callable[[], None]:
    """Keep internal guard failures behind the stable application taxonomy."""

    def verified() -> None:
        try:
            callback()
        except ChangeControlApplicationError:
            raise
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    return verified


def _fresh_active_query_authority(
    *,
    state_path: Path,
    aggregate_id: str,
    context: AuthorityVerificationContext,
) -> AuthorityRevisionBinding:
    """Reopen authority so a bounded query does not re-read an immutable snapshot."""

    fresh = SqliteManagedChangeControlStore(
        state_path,
        secure_open=True,
        read_only=True,
    )
    try:
        return fresh.get_active_generation(
            aggregate_id,
            authority_context=context,
        )
    finally:
        fresh.close()


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

    def resolve_query_generation(
        self,
        selection: (
            str | QueryGenerationSelector | QueryGenerationSelectionV1
        ) = QueryGenerationSelector.AUTO,
    ) -> ResolvedQueryGeneration:
        """Resolve the one verified index that must serve an ordinary query.

        An unbootstrapped v0.2 workspace keeps the historical backend behavior.
        Once an authority database exists, every selector is resolved through
        fresh repository evidence and a secure read-only authority store.
        """

        try:
            if isinstance(selection, QueryGenerationSelectionV1):
                if type(selection) is not QueryGenerationSelectionV1:
                    raise TypeError("query generation selection type was substituted")
                requested = QueryGenerationSelectionV1.model_validate_json(
                    canonical_json_bytes(selection.model_dump(mode="json"))
                )
                if requested != selection:
                    raise ValueError("query generation selection is not canonical")
            else:
                requested = QueryGenerationSelectionV1.parse(selection)
        except (TypeError, ValueError) as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc

        try:
            if not _query_state_exists(self._settings):
                if _managed_query_is_configured(self._settings):
                    self._preflight_backend()
                    _preflight_paths(self._settings)
                    raise ChangeControlApplicationIntegrityError(
                        "configured managed authority store does not exist"
                    )
                if requested.selector != QueryGenerationSelector.AUTO:
                    raise ChangeControlApplicationUsageError(
                        "explicit generation selection requires an initialized authority store"
                    )
                backend = get_backend(self._settings)
                return ResolvedQueryGeneration(
                    backend=backend,
                    metadata=QueryGenerationMetadataV1(
                        selection=requested,
                        backend=backend.name,
                        generation_kind=QueryGenerationKind.UNMANAGED,
                        is_active=False,
                    ),
                )

            # Managed resolution is SQLite-only and this check deliberately
            # precedes repository/provider opening or any other effects.
            self._preflight_backend()
            paths = _preflight_paths(self._settings)
            if self._settings.query_generation.bootstrap_manifest is not None:
                return self._resolve_workspace_query_generation(
                    paths=paths,
                    selection=requested,
                )
            if self._settings.query_generation.seed_repository_root is not None:
                return self._resolve_seed_query_generation(
                    paths=paths,
                    selection=requested,
                )
            raise ChangeControlApplicationUsageError(
                "managed queries require query_generation.bootstrap_manifest or "
                "query_generation.seed_repository_root"
            )
        except ChangeControlApplicationError:
            raise
        except (
            WorkspaceBootstrapPlatformUnsupportedError,
            LegacyIndexPlatformUnsupportedError,
        ) as exc:
            raise ChangeControlApplicationUnsupportedOperationError(str(exc)) from exc
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ManagedServingConflictError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ManagedServingError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def _resolve_workspace_query_generation(
        self,
        *,
        paths: _ApplicationPaths,
        selection: QueryGenerationSelectionV1,
    ) -> ResolvedQueryGeneration:
        configured_manifest = self._settings.query_generation.bootstrap_manifest
        assert configured_manifest is not None
        manifest_path = _manifest_path(paths, configured_manifest)
        source_roots = _configured_source_roots(self._settings)
        embedder = get_embedding_provider(self._settings)

        workspace_guard = None
        index_guard = None
        store = None
        backend = None
        try:
            workspace_guard = open_workspace_bootstrap_evidence_guard(
                workspace_root=paths.workspace,
                manifest_path=manifest_path,
                source_roots=source_roots,
                index_schema_version=SCHEMA_VERSION,
                embedding_model=embedder.model_version,
                embedding_dimensions=embedder.dimensions,
            )
            resolved = workspace_guard.resolved
            expected_index = resolved.inventory.legacy_index
            index_guard = open_legacy_sqlite_index_attestation_guard(
                index_path=resolved.legacy_index_path,
                notes=resolved.exact_vault_notes,
                embedding_model_version=expected_index.embedding_model,
                embedding_dimensions=expected_index.embedding_dimensions,
                expected_index_file_sha256=expected_index.index_file_sha256,
                expected_index_file_byte_count=expected_index.index_file_byte_count,
            )
            store = SqliteManagedChangeControlStore(
                paths.state_db,
                secure_open=True,
                read_only=True,
            )
            state = store.get_workspace_bootstrap_by_inventory_id(
                resolved.inventory.inventory_id
            )
            if state is None:
                raise ChangeControlApplicationIntegrityError(
                    "fresh workspace inventory has no durable bootstrap owner"
                )
            inventory_receipt, readiness = state.require_complete()
            aggregate_commit = store.get_operation_commit(
                inventory_receipt.aggregate_operation_id
            )
            if aggregate_commit is None or not (
                aggregate_commit.aggregate_id == resolved.aggregate.aggregate_id
                and aggregate_commit.revision == inventory_receipt.aggregate_revision == 1
                and aggregate_commit.aggregate_sha256
                == inventory_receipt.aggregate_sha256
                == aggregate_sha256(resolved.aggregate)
                and aggregate_commit.changed
            ):
                raise ChangeControlApplicationIntegrityError(
                    "workspace bootstrap aggregate receipt cannot be reopened exactly"
                )
            persisted_snapshot = ChangeControlSnapshot(
                aggregate=resolved.aggregate,
                revision=aggregate_commit.revision,
                aggregate_sha256=aggregate_commit.aggregate_sha256,
            )
            capability = verify_workspace_bootstrap_evidence(
                state=state,
                resolved_inventory=resolved.inventory,
                resolved_aggregate=resolved.aggregate,
                persisted_snapshot=persisted_snapshot,
                legacy_attestation=index_guard.attestation,
                evidence_verifier=create_workspace_bootstrap_evidence_verifier(
                    workspace_guard,
                    index_guard,
                ),
            )
            context = AuthorityVerificationContext.workspace(capability)
            active = store.get_active_generation(
                state.intent.aggregate_id,
                authority_context=context,
            )
            if active.active_generation.generation_number == 0:
                generation_zero = active
                active_decision = None
            elif active.active_generation.generation_number == 1:
                active_decision = store.get_active_managed_decision_record(
                    state.intent.aggregate_id,
                    authority_context=context,
                )
                if active_decision is None:
                    raise ChangeControlApplicationIntegrityError(
                        "active managed generation lacks its exact decision record"
                    )
                generation_zero = active_decision.command.expected_authority
            else:
                raise ChangeControlApplicationUnsupportedOperationError(
                    "the v0.3 query resolver supports only the first managed successor"
                )
            if generation_zero.active_generation.generation_number != 0:
                raise ChangeControlApplicationIntegrityError(
                    "managed authority does not retain generation-zero lineage"
                )
            selected = _select_generation_authority(
                selection=selection,
                active=active,
                generation_zero=generation_zero,
            )

            serving = None
            evidence_workspaces: dict[str, Path | EvidenceLocation]
            if selected.active_generation.generation_number == 0:
                backend = index_guard.open_read_only_index()
                metadata = _query_metadata(
                    selection=selection,
                    selected=selected,
                    active=active,
                    logical_fingerprint=readiness.index_logical_fingerprint,
                    file_sha256=readiness.index_file_sha256,
                    file_byte_count=readiness.index_file_byte_count,
                    schema_version=readiness.index_schema_version,
                    embedding_model=readiness.embedding_model,
                    embedding_dimensions=readiness.embedding_dimensions,
                )
                evidence_workspaces = {
                    note.rel_path: note.workspace for note in resolved.exact_vault_notes
                }
                if len(evidence_workspaces) != len(resolved.exact_vault_notes):
                    raise ChangeControlApplicationIntegrityError(
                        "workspace evidence contains duplicate logical query paths"
                    )
            else:
                assert active_decision is not None
                serving = self._open_managed_query_generation(
                    paths=paths,
                    store=store,
                    context=context,
                    active=active,
                    active_decision=active_decision,
                    workspace_base_notes=resolved.exact_vault_notes,
                )
                backend = serving.backend
                receipt = serving.index_receipt
                metadata = _query_metadata(
                    selection=selection,
                    selected=selected,
                    active=active,
                    logical_fingerprint=receipt.logical_index_fingerprint,
                    file_sha256=receipt.index_file_sha256,
                    file_byte_count=receipt.index_file_byte_count,
                    schema_version=receipt.storage_schema_version,
                    embedding_model=receipt.embedding_model_version,
                    embedding_dimensions=receipt.embedding_dimensions,
                )
                evidence_workspaces = {
                    note.rel_path: (
                        EvidenceLocation(
                            note_workspace=note.workspace,
                            support_workspace=resolved.workspace_root,
                        )
                        if note.supporting_files and note.workspace != resolved.workspace_root
                        else note.workspace
                    )
                    for note in serving.index_notes
                }
                if len(evidence_workspaces) != len(serving.index_notes):
                    raise ChangeControlApplicationIntegrityError(
                        "managed generation contains duplicate logical query paths"
                    )

            def verify_live_authority() -> None:
                try:
                    current = _fresh_active_query_authority(
                        state_path=paths.state_db,
                        aggregate_id=state.intent.aggregate_id,
                        context=context,
                    )
                except (TypeError, AssertionError):
                    raise
                except Exception as exc:
                    raise ChangeControlApplicationIntegrityError(
                        "active authority cannot be freshly verified"
                    ) from exc
                if current != active:
                    raise ChangeControlApplicationConflictError(
                        "active authority changed while the query generation was open"
                    )

            return ResolvedQueryGeneration(
                backend=backend,
                metadata=metadata,
                evidence_workspaces=evidence_workspaces,
                _verify_callbacks=(_guard_query_verification(verify_live_authority),),
                _verify_backend=_guard_query_verification(
                    (
                        lambda: index_guard.verify_open_read_only_index(backend)
                    )
                    if serving is None
                    else serving.verify
                ),
                _close_backend=_guard_query_verification(backend.close),
                _close_callbacks=tuple(
                    _guard_query_verification(callback)
                    for callback in (
                        store.close,
                        index_guard.close,
                        workspace_guard.close,
                    )
                ),
            )
        except BaseException as exc:
            _close_failed_query_resources(
                exc,
                backend,
                store,
                index_guard,
                workspace_guard,
            )

    def _resolve_seed_query_generation(
        self,
        *,
        paths: _ApplicationPaths,
        selection: QueryGenerationSelectionV1,
    ) -> ResolvedQueryGeneration:
        from mastervault.change_control.managed_query_resolver import (
            build_read_only_managed_query_resolver,
            reopen_sealed_seed_query_bootstrap,
        )

        cfg = self._settings.query_generation
        if (
            cfg.seed_repository_root is None
            or cfg.evidence_repository_root is None
            or cfg.canonical_repository_root is None
            or cfg.temporal_analysis_manifest_sha256 is None
        ):
            raise ChangeControlApplicationUsageError(
                "sealed-seed managed queries require seed, evidence, canonical, and "
                "temporal-analysis runtime locators"
            )
        store = None
        backend = None
        try:
            bootstrap = reopen_sealed_seed_query_bootstrap(
                seed_repository_root=cfg.seed_repository_root,
                evidence_repository_root=cfg.evidence_repository_root,
                temporal_analysis_manifest_sha256=(
                    cfg.temporal_analysis_manifest_sha256
                ),
            )
            context = bootstrap.authority_context
            store = SqliteManagedChangeControlStore(
                paths.state_db,
                secure_open=True,
                read_only=True,
            )
            aggregate_id = bootstrap.prechange_head.aggregate_id
            active = store.get_active_generation(
                aggregate_id,
                authority_context=context,
            )
            if active.active_generation.generation_number == 0:
                raise ChangeControlApplicationUnsupportedOperationError(
                    "sealed-seed generation zero has no generic legacy query index; "
                    "configure a workspace bootstrap manifest to serve generation zero"
                )
            if active.active_generation.generation_number != 1:
                raise ChangeControlApplicationUnsupportedOperationError(
                    "the v0.3 query resolver supports only the first managed successor"
                )
            active_decision = store.get_active_managed_decision_record(
                aggregate_id,
                authority_context=context,
            )
            if active_decision is None:
                raise ChangeControlApplicationIntegrityError(
                    "active managed generation lacks its exact decision record"
                )
            generation_zero = active_decision.command.expected_authority
            selected = _select_generation_authority(
                selection=selection,
                active=active,
                generation_zero=generation_zero,
            )
            if selected.active_generation.generation_number == 0:
                raise ChangeControlApplicationUnsupportedOperationError(
                    "sealed-seed generation zero does not expose a generic legacy query index"
                )
            restarted = build_read_only_managed_query_resolver(
                store=store,
                active_decision=active_decision,
                bootstrap=bootstrap,
                canonical_repository_root=cfg.canonical_repository_root,
            )
            serving = open_active_managed_sqlite_generation(
                aggregate_id=aggregate_id,
                store=store,
                resolver=restarted.resolver,
                authority_context=context,
                generation_root=paths.generation_root,
                protected_paths=_existing_query_protected_paths(paths),
            )
            backend = serving.backend
            receipt = serving.index_receipt
            metadata = _query_metadata(
                selection=selection,
                selected=selected,
                active=active,
                logical_fingerprint=receipt.logical_index_fingerprint,
                file_sha256=receipt.index_file_sha256,
                file_byte_count=receipt.index_file_byte_count,
                schema_version=receipt.storage_schema_version,
                embedding_model=receipt.embedding_model_version,
                embedding_dimensions=receipt.embedding_dimensions,
            )
            evidence_workspaces = {
                note.entry.logical_path: note.workspace
                for note in serving.resolved_notes
                if note.entry.included_in_serving_index
            }
            if len(evidence_workspaces) != sum(
                note.entry.included_in_serving_index for note in serving.resolved_notes
            ):
                raise ChangeControlApplicationIntegrityError(
                    "managed generation contains duplicate logical query paths"
                )

            def verify_live_authority() -> None:
                try:
                    current = _fresh_active_query_authority(
                        state_path=paths.state_db,
                        aggregate_id=aggregate_id,
                        context=context,
                    )
                except (TypeError, AssertionError):
                    raise
                except Exception as exc:
                    raise ChangeControlApplicationIntegrityError(
                        "active authority cannot be freshly verified"
                    ) from exc
                if current != active:
                    raise ChangeControlApplicationConflictError(
                        "active authority changed while the query generation was open"
                    )

            return ResolvedQueryGeneration(
                backend=backend,
                metadata=metadata,
                evidence_workspaces=evidence_workspaces,
                _verify_callbacks=(_guard_query_verification(verify_live_authority),),
                _verify_backend=_guard_query_verification(serving.verify),
                _close_backend=_guard_query_verification(backend.close),
                _close_callbacks=(_guard_query_verification(store.close),),
            )
        except BaseException as exc:
            _close_failed_query_resources(exc, backend, store)

    def _open_managed_query_generation(
        self,
        *,
        paths: _ApplicationPaths,
        store: SqliteManagedChangeControlStore,
        context: AuthorityVerificationContext,
        active: AuthorityRevisionBinding,
        active_decision: object,
        workspace_base_notes: tuple[ExactVaultNoteInput, ...],
    ) -> ManagedServingResolution:
        """Rebuild the process-local resolver and open one active gen1 index."""

        from mastervault.change_control.managed_query_resolver import (
            build_read_only_managed_query_resolver,
            reopen_sealed_seed_query_bootstrap,
        )
        from mastervault.change_control.managed_review import (
            ManagedRevisionDecisionRecord,
            ManagedRunBindingV2,
        )

        if not isinstance(active_decision, ManagedRevisionDecisionRecord):
            raise ChangeControlApplicationIntegrityError(
                "active managed generation decision type is invalid"
            )
        cfg = self._settings.query_generation
        if (
            cfg.seed_repository_root is None
            or cfg.evidence_repository_root is None
            or cfg.canonical_repository_root is None
        ):
            raise ChangeControlApplicationUsageError(
                "active managed queries require seed, evidence, and canonical "
                "runtime locators"
            )
        run_binding = active_decision.command.bundle.run_binding
        operation_prefix = "temporal-commit:"
        if type(run_binding) is not ManagedRunBindingV2 or not run_binding.operation_id.startswith(
            operation_prefix
        ):
            raise ChangeControlApplicationIntegrityError(
                "active managed run lacks its exact temporal-analysis locator"
            )
        temporal_sha256 = run_binding.operation_id.removeprefix(operation_prefix)
        if (
            len(temporal_sha256) != 64
            or any(character not in "0123456789abcdef" for character in temporal_sha256)
            or (
                cfg.temporal_analysis_manifest_sha256 is not None
                and cfg.temporal_analysis_manifest_sha256 != temporal_sha256
            )
        ):
            raise ChangeControlApplicationIntegrityError(
                "active managed run differs from the configured temporal analysis"
            )
        bootstrap = reopen_sealed_seed_query_bootstrap(
            seed_repository_root=cfg.seed_repository_root,
            evidence_repository_root=cfg.evidence_repository_root,
            temporal_analysis_manifest_sha256=temporal_sha256,
        )
        restarted = build_read_only_managed_query_resolver(
            store=store,
            active_decision=active_decision,
            bootstrap=bootstrap,
            canonical_repository_root=cfg.canonical_repository_root,
            authority_context=context,
        )
        serving = open_active_managed_sqlite_generation(
            aggregate_id=active.aggregate_id,
            store=store,
            resolver=restarted.resolver,
            authority_context=context,
            generation_root=paths.generation_root,
            protected_paths=_existing_query_protected_paths(paths),
            workspace_base_notes=workspace_base_notes,
        )
        if serving.authority != active:
            serving.close()
            raise ChangeControlApplicationConflictError(
                "active authority changed during managed generation resolution"
            )
        return serving

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
