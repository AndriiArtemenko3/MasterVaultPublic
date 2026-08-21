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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Protocol, cast

from mastervault.change_control.application_activation_verifier import (
    ReadOnlyActivatedEvidenceVerifier,
)
from mastervault.change_control.application_authority_resolver import (
    ApplicationOperatorRunAuthorityResolver,
)
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationError,
    ChangeControlApplicationIntegrityError,
    ChangeControlApplicationUnsupportedOperationError,
    ChangeControlApplicationUsageError,
    raise_mapped_application_error,
)
from mastervault.change_control.application_read_models import (
    ApplicationReadModelError,
    ApplicationReadModels,
    ApplicationReviewUnavailableError,
    ApplicationRunNotFoundError,
)
from mastervault.change_control.application_replay import (
    ChangeReplayBundleUsageError,
    read_change_replay_bundle_v1,
)
from mastervault.change_control.application_runtime_identity import (
    application_configuration_sha256,
)
from mastervault.change_control.application_source_note_resolver import (
    GenericApplicationSourceNoteResolverLoader,
)
from mastervault.change_control.application_start_command import (
    ApplicationStartCommandRepository,
    ApplicationStartCommandV1,
)
from mastervault.change_control.application_start_lifecycle import (
    StartLifecycleCompletedNoOpV1,
    StartLifecycleTemporalReviewV1,
    resume_completed_temporal_publication,
    run_start_change_lifecycle,
)
from mastervault.change_control.change_application_contracts import (
    ActivateChangeRequestV1,
    ChangeActivationResultV1,
    ChangeReviewPacketV1,
    ChangeRunPageV1,
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
    ChangeVerificationResultV1,
    ManagedReviewDecisionDocumentV1,
    ReviewDecisionDocumentV1,
    StartChangeRequestV1,
    TemporalReviewDecisionDocumentV1,
)
from mastervault.change_control.generic_incoming import admit_generic_incoming_markdown_v2
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
    decode_operator_run_cursor,
)
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
    ResolvedQueryGeneration,
)
from mastervault.change_control.regression_baseline import (
    regression_configured_embedding_identity,
)
from mastervault.change_control.regression_suite import load_regression_suite
from mastervault.change_control.store import ChangeControlBusyError, ChangeControlSnapshot
from mastervault.change_control.synchronous_lifecycle_store_models import (
    SynchronousApplicationOperationV1,
    SynchronousRunLockAuthorityV1,
)
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

FailureHook = Callable[[str], None]


class _Closable(Protocol):
    def close(self) -> None: ...


class _VerifiableClosable(_Closable, Protocol):
    def verify(self) -> None: ...


_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_INTERNAL_RUN_LOCK_OPERATION_PREFIX = "application-run-lock:"
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
            "configured authority path cannot be inspected"
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise ChangeControlApplicationIntegrityError(
            "configured authority path cannot be a symbolic link"
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
    for index, (_left, left_identity) in enumerate(identities):
        if left_identity is None:
            continue
        for _right, right_identity in identities[index + 1 :]:
            if right_identity is not None and left_identity == right_identity:
                raise ChangeControlApplicationIntegrityError(
                    "configured authority paths must identify distinct filesystem objects"
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
    if operation_id.startswith(_INTERNAL_RUN_LOCK_OPERATION_PREFIX):
        raise ChangeControlApplicationUsageError(
            "operation_id uses the reserved application run-lock namespace"
        )


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
            QueryGenerationKind.GENERATION_ZERO if number == 0 else QueryGenerationKind.MANAGED
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


def _verify_managed_query_resources(
    bootstrap: _VerifiableClosable,
    serving: ManagedServingResolution,
) -> None:
    bootstrap.verify()
    serving.verify()


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

    @contextmanager
    def _read_models(self, *, paths: _ApplicationPaths) -> Iterator[ApplicationReadModels]:
        """Retain exact workspace evidence while one read-only projection runs."""

        configured_manifest = self._settings.query_generation.bootstrap_manifest
        if configured_manifest is None:
            raise ChangeControlApplicationUsageError(
                "lifecycle reads require query_generation.bootstrap_manifest"
            )
        manifest_path = _manifest_path(paths, configured_manifest)
        source_roots = _configured_source_roots(self._settings)

        locator_store = SqliteManagedChangeControlStore(
            paths.state_db,
            secure_open=True,
            read_only=True,
        )
        try:
            rows = locator_store.conn.execute(
                "SELECT bootstrap_id FROM change_control_workspace_bootstrap_intents "
                "ORDER BY bootstrap_id"
            ).fetchall()
            if len(rows) != 1:
                raise ChangeControlApplicationIntegrityError(
                    "lifecycle authority requires one exact workspace bootstrap"
                )
            locator_state = locator_store.get_workspace_bootstrap(str(rows[0]["bootstrap_id"]))
            if locator_state is None:
                raise ChangeControlApplicationIntegrityError(
                    "workspace bootstrap locator cannot be reopened"
                )
            _inventory_locator, readiness_locator = locator_state.require_complete()
        finally:
            locator_store.close()

        workspace_guard = None
        index_guard = None
        authority_store = None
        primary_error: BaseException | None = None
        try:
            workspace_guard = open_workspace_bootstrap_evidence_guard(
                workspace_root=paths.workspace,
                manifest_path=manifest_path,
                source_roots=source_roots,
                index_schema_version=readiness_locator.index_schema_version,
                embedding_model=readiness_locator.embedding_model,
                embedding_dimensions=readiness_locator.embedding_dimensions,
            )
            resolved = workspace_guard.resolved
            expected = resolved.inventory.legacy_index
            index_guard = open_legacy_sqlite_index_attestation_guard(
                index_path=resolved.legacy_index_path,
                notes=resolved.exact_vault_notes,
                embedding_model_version=expected.embedding_model,
                embedding_dimensions=expected.embedding_dimensions,
                expected_index_file_sha256=expected.index_file_sha256,
                expected_index_file_byte_count=expected.index_file_byte_count,
            )
            authority_store = SqliteManagedChangeControlStore(
                paths.state_db,
                secure_open=True,
                read_only=True,
            )
            state = authority_store.get_workspace_bootstrap_by_inventory_id(
                resolved.inventory.inventory_id
            )
            if state is None:
                raise ChangeControlApplicationIntegrityError(
                    "workspace inventory has no durable bootstrap owner"
                )
            inventory_receipt, _readiness = state.require_complete()
            aggregate_commit = authority_store.get_operation_commit(
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
            capability = verify_workspace_bootstrap_evidence(
                state=state,
                resolved_inventory=resolved.inventory,
                resolved_aggregate=resolved.aggregate,
                persisted_snapshot=ChangeControlSnapshot(
                    aggregate=resolved.aggregate,
                    revision=aggregate_commit.revision,
                    aggregate_sha256=aggregate_commit.aggregate_sha256,
                ),
                legacy_attestation=index_guard.attestation,
                evidence_verifier=create_workspace_bootstrap_evidence_verifier(
                    workspace_guard,
                    index_guard,
                ),
            )
            source_loader = GenericApplicationSourceNoteResolverLoader(
                evidence_root=self._settings.paths.change_control_evidence_root,
                workspace_capability=capability,
                workspace_source_notes=tuple(
                    item.snapshot for item in resolved.managed_source_notes
                ),
            )
            yield ApplicationReadModels(
                paths.state_db,
                self._settings.paths.change_control_evidence_root,
                source_note_resolver=source_loader,
                configuration_sha256=application_configuration_sha256(self._settings),
                activation_evidence_verifier=ReadOnlyActivatedEvidenceVerifier(
                    lambda: self.resolve_query_generation(QueryGenerationSelector.ACTIVE)
                ),
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            close_failure: BaseException | None = None
            for resource in (authority_store, index_guard, workspace_guard):
                if resource is None:
                    continue
                try:
                    resource.close()
                except BaseException as close_error:
                    if primary_error is None:
                        close_failure = close_failure or close_error
                    else:
                        primary_error.add_note(
                            "lifecycle read also failed while closing retained resources: "
                            f"{type(close_error).__name__}"
                        )
            if primary_error is None and close_failure is not None:
                raise close_failure

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
        embedding_identity: tuple[str, int] | None = None,
    ) -> ResolvedQueryGeneration:
        configured_manifest = self._settings.query_generation.bootstrap_manifest
        assert configured_manifest is not None
        manifest_path = _manifest_path(paths, configured_manifest)
        source_roots = _configured_source_roots(self._settings)
        if embedding_identity is None:
            embedder = get_embedding_provider(self._settings)
            embedding_model = embedder.model_version
            embedding_dimensions = embedder.dimensions
        else:
            embedding_model, embedding_dimensions = embedding_identity

        workspace_guard = None
        index_guard = None
        store = None
        backend = None
        managed_bootstrap: _VerifiableClosable | None = None
        try:
            workspace_guard = open_workspace_bootstrap_evidence_guard(
                workspace_root=paths.workspace,
                manifest_path=manifest_path,
                source_roots=source_roots,
                index_schema_version=SCHEMA_VERSION,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
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
            state = store.get_workspace_bootstrap_by_inventory_id(resolved.inventory.inventory_id)
            if state is None:
                raise ChangeControlApplicationIntegrityError(
                    "fresh workspace inventory has no durable bootstrap owner"
                )
            inventory_receipt, readiness = state.require_complete()
            aggregate_commit = store.get_operation_commit(inventory_receipt.aggregate_operation_id)
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
                serving, managed_bootstrap = self._open_managed_query_generation(
                    paths=paths,
                    store=store,
                    context=context,
                    active=active,
                    active_decision=active_decision,
                    resolved_workspace=resolved,
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

            if serving is None:

                def verify_backend() -> None:
                    index_guard.verify_open_read_only_index(backend)
            else:
                opened_bootstrap = managed_bootstrap
                if opened_bootstrap is None:
                    raise ChangeControlApplicationIntegrityError(
                        "managed query bootstrap was not retained"
                    )

                def verify_backend() -> None:
                    _verify_managed_query_resources(opened_bootstrap, serving)

            return ResolvedQueryGeneration(
                backend=backend,
                metadata=metadata,
                evidence_workspaces=evidence_workspaces,
                _verify_callbacks=(_guard_query_verification(verify_live_authority),),
                _verify_backend=_guard_query_verification(verify_backend),
                _close_backend=_guard_query_verification(backend.close),
                _close_callbacks=tuple(
                    _guard_query_verification(callback)
                    for callback in (
                        *((managed_bootstrap.close,) if managed_bootstrap is not None else ()),
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
                managed_bootstrap,
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
                temporal_analysis_manifest_sha256=(cfg.temporal_analysis_manifest_sha256),
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
        resolved_workspace: ResolvedWorkspaceBootstrap,
    ) -> tuple[ManagedServingResolution, _VerifiableClosable | None]:
        """Rebuild the process-local resolver and open one active gen1 index."""

        from mastervault.change_control.generic_analysis import (
            GenericAnalysisBootstrapBindingV2,
        )
        from mastervault.change_control.managed_query_resolver import (
            ManagedQueryBootstrap,
            build_read_only_managed_query_resolver,
            reopen_sealed_seed_query_bootstrap,
            reopen_workspace_query_bootstrap_v2,
        )
        from mastervault.change_control.managed_review import (
            GenericGoverningSourceAdoptionBindingV2,
            ManagedArtifactKind,
            ManagedArtifactRef,
            ManagedRevisionDecisionRecord,
            ManagedRunBindingV2,
        )

        if not isinstance(active_decision, ManagedRevisionDecisionRecord):
            raise ChangeControlApplicationIntegrityError(
                "active managed generation decision type is invalid"
            )
        cfg = self._settings.query_generation
        if cfg.canonical_repository_root is None:
            raise ChangeControlApplicationUsageError(
                "active managed queries require the canonical runtime locator"
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
        bootstrap: ManagedQueryBootstrap
        retained_bootstrap: _VerifiableClosable | None = None
        analysis_bootstrap = run_binding.analysis_set.analysis_bootstrap
        if type(analysis_bootstrap) is GenericAnalysisBootstrapBindingV2:
            if (
                type(run_binding.governing_source_adoption)
                is not GenericGoverningSourceAdoptionBindingV2
            ):
                raise ChangeControlApplicationIntegrityError(
                    "generic managed run lacks its exact governing-source adoption"
                )
            generic_evidence_root = self._settings.paths.change_control_evidence_root
            bootstrap = reopen_workspace_query_bootstrap_v2(
                authority_context=context,
                workspace_source_notes=tuple(
                    item.snapshot for item in resolved_workspace.managed_source_notes
                ),
                evidence_repository_root=generic_evidence_root,
                generic_evidence_repository_root=generic_evidence_root,
                temporal_analysis_manifest_sha256=temporal_sha256,
            )
            retained_bootstrap = bootstrap
        else:
            if cfg.seed_repository_root is None or cfg.evidence_repository_root is None:
                raise ChangeControlApplicationUsageError(
                    "sealed-seed managed queries require seed and evidence runtime locators"
                )
            bootstrap = reopen_sealed_seed_query_bootstrap(
                seed_repository_root=cfg.seed_repository_root,
                evidence_repository_root=cfg.evidence_repository_root,
                temporal_analysis_manifest_sha256=temporal_sha256,
            )
        try:
            restarted = build_read_only_managed_query_resolver(
                store=store,
                active_decision=active_decision,
                bootstrap=bootstrap,
                canonical_repository_root=cfg.canonical_repository_root,
                authority_context=context,
            )
            resolver = restarted.resolver
            if type(analysis_bootstrap) is GenericAnalysisBootstrapBindingV2:
                from mastervault.change_control.generic_governing_source import (
                    CompositeManagedReviewResolverV2,
                    WorkspaceSourceNoteProjectionAuthority,
                )

                if type(resolver) is not CompositeManagedReviewResolverV2:
                    raise ChangeControlApplicationIntegrityError(
                        "generic managed query resolver type is invalid"
                    )
                workspace_authorities = tuple(
                    WorkspaceSourceNoteProjectionAuthority(
                        metadata=item.metadata,
                        snapshot=item.snapshot,
                        raw_artifact=ManagedArtifactRef.create(
                            kind=ManagedArtifactKind.RAW_SOURCE,
                            path=item.snapshot.document.source_path,
                            sha256=hashlib.sha256(item.raw_source_bytes).hexdigest(),
                            byte_count=len(item.raw_source_bytes),
                        ),
                        raw_bytes=item.raw_source_bytes,
                        note_artifact=ManagedArtifactRef.create(
                            kind=ManagedArtifactKind.SOURCE_NOTE,
                            path=item.snapshot.source_note_path,
                            sha256=hashlib.sha256(
                                item.snapshot.source_note_utf8.encode("utf-8")
                            ).hexdigest(),
                            byte_count=len(item.snapshot.source_note_utf8.encode("utf-8")),
                        ),
                        note_bytes=item.snapshot.source_note_utf8.encode("utf-8"),
                        projected_claims=tuple(
                            revision
                            for revision in resolved_workspace.aggregate.claims.revisions
                            if revision.document == item.snapshot.document
                        ),
                    )
                    for item in resolved_workspace.managed_source_notes
                )
                resolver = CompositeManagedReviewResolverV2(
                    sealed=resolver.sealed,
                    generic=resolver.generic,
                    workspace_projection_authorities=workspace_authorities,
                )
            serving = open_active_managed_sqlite_generation(
                aggregate_id=active.aggregate_id,
                store=store,
                resolver=resolver,
                authority_context=context,
                generation_root=paths.generation_root,
                protected_paths=_existing_query_protected_paths(paths),
                workspace_base_notes=resolved_workspace.exact_vault_notes,
            )
        except BaseException:
            if retained_bootstrap is not None:
                retained_bootstrap.close()
            raise
        if serving.authority != active:
            serving.close()
            if retained_bootstrap is not None:
                retained_bootstrap.close()
            raise ChangeControlApplicationConflictError(
                "active authority changed during managed generation resolution"
            )
        return serving, retained_bootstrap

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

                    try:
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
                    except ValueError as exc:
                        raise ChangeControlApplicationIntegrityError(
                            "workspace bootstrap evidence could not be verified"
                        ) from exc
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
            if self._settings.query_generation.bootstrap_manifest is not None:
                with self._read_models(paths=paths) as read_models:
                    return read_models.get_operator_run(run_id)
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
        except ApplicationRunNotFoundError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReadModelError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def start_change(
        self,
        request: StartChangeRequestV1,
        *,
        failure_hook: FailureHook | None = None,
    ) -> ChangeRunStatusV1:
        """Synchronously admit and analyze one change through temporal review."""

        if type(request) is not StartChangeRequestV1:
            raise TypeError("start_change requires the exact public request type")
        _validate_operation_id(request.operation_id)
        try:
            self._preflight_backend()
            paths = _preflight_paths(self._settings)
            configured_manifest = self._settings.query_generation.bootstrap_manifest
            if configured_manifest is None:
                raise ChangeControlApplicationUsageError(
                    "start_change requires query_generation.bootstrap_manifest"
                )
            manifest_path = _manifest_path(paths, configured_manifest)
            source_roots = _configured_source_roots(self._settings)
            admission = admit_generic_incoming_markdown_v2(
                request.source, active_workspace=paths.workspace
            )
            if admission.metadata.domain != request.domain:
                raise ChangeControlApplicationUsageError(
                    "requested domain differs from incoming metadata"
                )
            suite = load_regression_suite(request.regression_suite)
            configuration_sha256 = application_configuration_sha256(self._settings)
            replay = (
                read_change_replay_bundle_v1(request.replay_bundle)
                if request.replay_bundle is not None
                else None
            )
            if replay is not None and replay.configuration_sha256 != configuration_sha256:
                raise ChangeControlApplicationConflictError(
                    "replay bundle differs from the current runtime configuration"
                )
            source_metadata_sha256 = hashlib.sha256(
                canonical_json_bytes(admission.metadata.model_dump(mode="json"))
            ).hexdigest()
            command_repository = ApplicationStartCommandRepository(
                self._settings.paths.change_control_evidence_root
            )
            existing_command = command_repository.reopen_operation_optional(request.operation_id)
            if existing_command is not None:
                exact_request = (
                    existing_command.source_sha256 == admission.source_sha256
                    and existing_command.source_byte_count == admission.source_byte_count
                    and existing_command.source_metadata_sha256 == source_metadata_sha256
                    and existing_command.suite_id == suite.suite.suite_id
                    and existing_command.suite_version == suite.suite.suite_version
                    and existing_command.suite_original_sha256 == suite.original_sha256
                    and existing_command.suite_original_byte_count == suite.original_byte_count
                    and existing_command.suite_canonical_sha256 == suite.canonical_sha256
                    and existing_command.domain == request.domain
                    and existing_command.mode == request.mode
                    and existing_command.configuration_sha256 == configuration_sha256
                    and existing_command.replay_bundle_id
                    == (replay.bundle_id if replay is not None else None)
                    and existing_command.replay_bundle_sha256
                    == (replay.bundle_sha256 if replay is not None else None)
                    and (
                        request.requested_run_id is None
                        or request.requested_run_id == existing_command.run_id
                    )
                )
                if not exact_request:
                    raise ChangeControlApplicationConflictError(
                        "start operation is already bound to different immutable inputs"
                    )

            embedding_model, embedding_dimensions = regression_configured_embedding_identity(
                self._settings
            )
            with open_workspace_bootstrap_evidence_guard(
                workspace_root=paths.workspace,
                manifest_path=manifest_path,
                source_roots=source_roots,
                index_schema_version=SCHEMA_VERSION,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
            ) as workspace_guard:
                resolved = workspace_guard.resolved
                expected = resolved.inventory.legacy_index
                with open_legacy_sqlite_index_attestation_guard(
                    index_path=resolved.legacy_index_path,
                    notes=resolved.exact_vault_notes,
                    embedding_model_version=expected.embedding_model,
                    embedding_dimensions=expected.embedding_dimensions,
                    expected_index_file_sha256=expected.index_file_sha256,
                    expected_index_file_byte_count=expected.index_file_byte_count,
                ) as index_guard:
                    store = SqliteManagedChangeControlStore(
                        paths.state_db, secure_open=True, read_only=True
                    )
                    run_resolver = ApplicationOperatorRunAuthorityResolver(
                        evidence_root=self._settings.paths.change_control_evidence_root,
                        state_path=paths.state_db,
                        configuration_sha256=configuration_sha256,
                    )
                    try:
                        state = store.get_workspace_bootstrap_by_inventory_id(
                            resolved.inventory.inventory_id
                        )
                        if state is None:
                            raise ChangeControlApplicationIntegrityError(
                                "workspace bootstrap authority is absent"
                            )
                        inventory_receipt, _readiness = state.require_complete()
                        live_snapshot = store.load(state.intent.aggregate_id)
                        if live_snapshot is None:
                            raise ChangeControlApplicationIntegrityError(
                                "workspace aggregate authority is absent"
                            )
                        bootstrap_snapshot = ChangeControlSnapshot(
                            aggregate=resolved.aggregate,
                            revision=inventory_receipt.aggregate_revision,
                            aggregate_sha256=inventory_receipt.aggregate_sha256,
                        )
                        capability = verify_workspace_bootstrap_evidence(
                            state=state,
                            resolved_inventory=resolved.inventory,
                            resolved_aggregate=resolved.aggregate,
                            persisted_snapshot=cast(
                                WorkspaceBootstrapAggregateSnapshot, bootstrap_snapshot
                            ),
                            legacy_attestation=index_guard.attestation,
                            evidence_verifier=create_workspace_bootstrap_evidence_verifier(
                                workspace_guard, index_guard
                            ),
                        )
                        active = store.get_active_generation(
                            state.intent.aggregate_id,
                            authority_context=AuthorityVerificationContext.workspace(capability),
                        )
                        if not (
                            active.authority_revision == 0
                            and active.active_generation.generation_number == 0
                            and inventory_receipt.aggregate_revision == 1
                        ):
                            raise ChangeControlApplicationConflictError(
                                "start_change requires exact active generation zero"
                            )
                        rows = store.conn.execute(
                            "SELECT run_id FROM change_control_operator_runs ORDER BY run_id"
                        ).fetchall()
                        candidate_ids = tuple(str(row["run_id"]) for row in rows)
                        if request.requested_run_id is not None:
                            candidate_ids = tuple(
                                item for item in candidate_ids if item == request.requested_run_id
                            )
                        candidates = tuple(
                            run
                            for run_id in candidate_ids
                            if (run := store.get_operator_run(run_id, resolver=run_resolver))
                            is not None
                            and run.record.command.aggregate_id == active.aggregate_id
                            and run.record.command.base_authority_id == active.authority_id
                            and run.record.command.base_authority_revision
                            == active.authority_revision
                            and run.record.command.base_active_pointer_sha256
                            == active.active_pointer_sha256
                        )
                        if len(candidates) != 1:
                            raise ChangeControlApplicationConflictError(
                                "start_change requires one exact existing bootstrap run"
                            )
                        bootstrap_run = candidates[0]
                    finally:
                        store.close()

                    command = ApplicationStartCommandV1.create(
                        operation_id=request.operation_id,
                        run_id=bootstrap_run.record.command.run_id,
                        base_authority_id=active.authority_id,
                        base_authority_revision=active.authority_revision,
                        base_active_pointer_sha256=active.active_pointer_sha256,
                        source_sha256=admission.source_sha256,
                        source_byte_count=admission.source_byte_count,
                        source_metadata_sha256=source_metadata_sha256,
                        suite_id=suite.suite.suite_id,
                        suite_version=suite.suite.suite_version,
                        suite_original_sha256=suite.original_sha256,
                        suite_original_byte_count=suite.original_byte_count,
                        suite_canonical_sha256=suite.canonical_sha256,
                        domain=request.domain,
                        mode=request.mode,
                        replay_bundle_id=(replay.bundle_id if replay is not None else None),
                        replay_bundle_sha256=(replay.bundle_sha256 if replay is not None else None),
                        configuration_sha256=configuration_sha256,
                        claimed_at=_now(),
                    )
                    request_sha256 = hashlib.sha256(
                        canonical_json_bytes(
                            command.model_dump(
                                mode="json",
                                exclude={"command_id", "command_sha256", "claimed_at"},
                            )
                        )
                    ).hexdigest()
                    operation_store = SqliteManagedChangeControlStore(
                        paths.state_db, secure_open=True
                    )
                    try:
                        operation_claim = operation_store.claim_application_operation(
                            SynchronousApplicationOperationV1.create(
                                operation_id=request.operation_id,
                                operation_kind="start",
                                run_id=command.run_id,
                                request_sha256=request_sha256,
                                claimed_at=command.claimed_at,
                            )
                        )
                    finally:
                        operation_store.close()
                    _notify(failure_hook, "application-operation-claimed")
                    if operation_claim.claimed_at != command.claimed_at:
                        command = ApplicationStartCommandV1.create(
                            **command.model_dump(
                                mode="python",
                                exclude={"command_id", "command_sha256", "claimed_at"},
                            ),
                            claimed_at=operation_claim.claimed_at,
                        )
                    lock_store = SqliteManagedChangeControlStore(paths.state_db, secure_open=True)
                    try:
                        lock_authority = lock_store.get_run_lock_authority(command.run_id)
                    finally:
                        lock_store.close()
                    if lock_authority is None:
                        candidate_lock_authority = command_repository.prepare_run_lock_authority(
                            command.run_id,
                            claimed_at=command.claimed_at,
                        )
                        lock_store = SqliteManagedChangeControlStore(
                            paths.state_db, secure_open=True
                        )
                        try:
                            lock_authority = lock_store.claim_run_lock_authority(
                                candidate_lock_authority
                            )
                        finally:
                            lock_store.close()
                    if type(lock_authority) is not SynchronousRunLockAuthorityV1:
                        raise ChangeControlApplicationIntegrityError(
                            "run-lock authority did not reopen exactly"
                        )
                    with command_repository.run_lifecycle_lock(
                        command.run_id, lock_authority
                    ):
                        waited_owner = command_repository.reopen_operation_optional(
                            command.operation_id
                        )
                        command = command_repository.claim(command)
                        if waited_owner is not None:
                            resume_completed_temporal_publication(
                                settings=self._settings,
                                state_path=paths.state_db,
                                evidence_root=self._settings.paths.change_control_evidence_root,
                                command=command,
                            )
                            waited_status = self.get_change_status(command.run_id)
                            if waited_status.phase == ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW:
                                return waited_status
                        lifecycle_result = run_start_change_lifecycle(
                            settings=self._settings,
                            state_path=paths.state_db,
                            evidence_root=self._settings.paths.change_control_evidence_root,
                            command=command,
                            admission=admission,
                            suite=suite,
                            workspace_state=state,
                            workspace_capability=capability,
                            workspace_source_notes=tuple(
                                item.snapshot for item in resolved.managed_source_notes
                            ),
                            resolve_generation_zero=lambda: (
                                self._resolve_workspace_query_generation(
                                    paths=paths,
                                    selection=QueryGenerationSelectionV1(
                                        selector=QueryGenerationSelector.LEGACY
                                    ),
                                    embedding_identity=(
                                        (embedding_model, embedding_dimensions)
                                        if replay is not None
                                        else None
                                    ),
                                )
                            ),
                            replay_bundle=replay,
                            failure_hook=failure_hook,
                        )
                        status = self.get_change_status(command.run_id)
                        if type(lifecycle_result) is StartLifecycleCompletedNoOpV1:
                            if status.phase != ChangeRunPhaseV1.COMPLETED_NO_OP:
                                raise ChangeControlApplicationIntegrityError(
                                    "start lifecycle did not reach completed no-op"
                                )
                            return status
                        if type(lifecycle_result) is not StartLifecycleTemporalReviewV1:
                            raise ChangeControlApplicationIntegrityError(
                                "start lifecycle returned an unsupported typed outcome"
                            )
                        if (
                            not lifecycle_result.request_id.startswith("reviewreq:")
                            or status.phase != ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW
                        ):
                            raise ChangeControlApplicationIntegrityError(
                                "start lifecycle did not reach awaiting temporal review"
                            )
                        return status
        except ChangeControlApplicationError:
            raise
        except ChangeReplayBundleUsageError as exc:
            raise_mapped_application_error(exc)
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def list_changes(
        self,
        limit: int = 50,
        cursor: str | None = None,
        phase: ChangeRunPhaseV1 | None = None,
    ) -> ChangeRunPageV1:
        """List existing lifecycle runs without creating or repairing authority."""

        try:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ChangeControlApplicationUsageError(
                    "change list limit must be an integer from 1 to 100"
                )
            if cursor is not None:
                if not isinstance(cursor, str):
                    raise ChangeControlApplicationUsageError("change list cursor is invalid")
                try:
                    decode_operator_run_cursor(cursor)
                except ValueError as exc:
                    raise ChangeControlApplicationUsageError(
                        "change list cursor is invalid"
                    ) from exc
            if phase is not None and type(phase) is not ChangeRunPhaseV1:
                raise ChangeControlApplicationUsageError("change list phase is invalid")
            self._preflight_backend()
            paths = _preflight_paths(self._settings)
            with self._read_models(paths=paths) as read_models:
                return read_models.list_changes(
                    limit=limit,
                    cursor=cursor,
                    phase=phase,
                )
        except ChangeControlApplicationError:
            raise
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ApplicationRunNotFoundError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReadModelError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def get_change_status(self, run_id: str) -> ChangeRunStatusV1:
        """Project one existing lifecycle run into the stable public status DTO."""

        try:
            self._preflight_backend()
            _validate_run_id(run_id)
            paths = _preflight_paths(self._settings)
            with self._read_models(paths=paths) as read_models:
                return read_models.get_change_status(run_id)
        except ChangeControlApplicationError:
            raise
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ApplicationRunNotFoundError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReadModelError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def record_change_review(
        self,
        document: ReviewDecisionDocumentV1,
        *,
        failure_hook: FailureHook | None = None,
    ) -> ChangeRunStatusV1:
        """Record one exact temporal or managed review decision."""

        if type(document) not in {
            TemporalReviewDecisionDocumentV1,
            ManagedReviewDecisionDocumentV1,
        }:
            raise TypeError("record_change_review requires an exact public decision type")
        _validate_operation_id(document.operation_id)

        from mastervault.change_control.application_downstream import (  # noqa: PLC0415
            record_change_review,
        )

        return record_change_review(
            settings=self._settings,
            document=document,
            failure_hook=failure_hook,
        )

    def activate_change(
        self,
        request: ActivateChangeRequestV1,
        *,
        failure_hook: FailureHook | None = None,
    ) -> ChangeActivationResultV1:
        """Activate one exact reviewed managed generation."""

        if type(request) is not ActivateChangeRequestV1:
            raise TypeError("activate_change requires the exact public request type")
        _validate_operation_id(request.operation_id)

        from mastervault.change_control.application_downstream import (  # noqa: PLC0415
            activate_change,
        )

        return activate_change(
            settings=self._settings,
            request=request,
            failure_hook=failure_hook,
        )

    def get_change_review(self, run_id: str) -> ChangeReviewPacketV1:
        """Render the current exact human-review request without side effects."""

        try:
            self._preflight_backend()
            _validate_run_id(run_id)
            paths = _preflight_paths(self._settings)
            with self._read_models(paths=paths) as read_models:
                return read_models.get_change_review(run_id)
        except ChangeControlApplicationError:
            raise
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ApplicationRunNotFoundError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReviewUnavailableError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReadModelError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
        except (TypeError, AssertionError):
            raise
        except Exception as exc:
            raise_mapped_application_error(exc)

    def verify_change(self, run_id: str) -> ChangeVerificationResultV1:
        """Freshly verify every linked immutable authority for one run."""

        try:
            self._preflight_backend()
            _validate_run_id(run_id)
            paths = _preflight_paths(self._settings)
            with self._read_models(paths=paths) as read_models:
                return read_models.verify_change(run_id)
        except ChangeControlApplicationError:
            raise
        except ChangeControlBusyError as exc:
            raise ChangeControlApplicationConflictError(str(exc)) from exc
        except ApplicationRunNotFoundError as exc:
            raise ChangeControlApplicationUsageError(str(exc)) from exc
        except ApplicationReadModelError as exc:
            raise ChangeControlApplicationIntegrityError(str(exc)) from exc
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
