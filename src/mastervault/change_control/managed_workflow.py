"""Disposable LangGraph wait/reconciliation for an existing managed review.

SQLite remains the sole review authority.  Checkpoints contain only primitive
identity and observation fields; they never contain bundles, subjects, human
choices, decisions, repository capabilities, or publication authority.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Literal, TypedDict, cast

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from mastervault.change_control.bootstrap import VerifiedAnalysisBootstrapCapability
from mastervault.change_control.managed_review import (
    AggregateHeadBinding,
    ManagedRevisionDecisionRecord,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_review_repository import (
    RepositoryBackedManagedReviewResolver,
)
from mastervault.change_control.managed_store import (
    ManagedRevisionReviewStoreView,
    ManagedRevisionStoreLifecycle,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.store import (
    ChangeControlBusyError,
    ChangeControlStoreError,
)

_WORKFLOW_SCHEMA_VERSION = 1
_WORKFLOW_COMPATIBILITY = "mastervault.managed-review-wait.v1"
_WAKE_SIGNAL = {"kind": "mastervault.managed-review-wake", "version": 1}
_IDENTITY_KEYS = frozenset(
    {
        "workflow_schema_version",
        "workflow_id",
        "request_id",
        "request_record_id",
        "request_record_sha256",
        "request_operation_id",
        "request_payload_sha256",
        "bundle_id",
        "bundle_sha256",
        "run_binding_id",
        "revision_admission_id",
        "revision_admission_sha256",
        "governing_source_adoption_id",
        "governing_source_adoption_sha256",
        "governing_source_repository_binding_sha256",
        "governing_reviewed_snapshot_binding_id",
        "governing_reviewed_snapshot_binding_sha256",
        "governing_temporal_decision_record_sha256",
        "aggregate_id",
        "review_open_revision",
        "review_open_aggregate_sha256",
        "expected_authority_id",
        "expected_authority_revision",
        "expected_active_pointer_sha256",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "authority_lifecycle",
        "authority_decision_record_sha256",
        "authority_current_head_revision",
        "authority_current_head_sha256",
        "authority_current_id",
        "authority_current_revision",
        "authority_current_pointer_sha256",
    }
)
_STATE_KEYS = _IDENTITY_KEYS | _OBSERVATION_KEYS
_INTERNAL_CHANNELS = frozenset({"branch:to:reconcile_authority", "branch:to:await_wake_signal"})


class ManagedReviewWorkflowError(RuntimeError):
    """Base error for managed-review wait orchestration."""


class ManagedReviewWorkflowAuthorityError(ManagedReviewWorkflowError):
    """The authoritative managed review could not be read safely."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ManagedReviewWorkflowCheckpointError(ManagedReviewWorkflowError):
    """The disposable checkpoint could not be used safely."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ManagedReviewWorkflowCheckpointCorruptionError(ManagedReviewWorkflowCheckpointError):
    """Checkpoint deserialization, schema, or immutable identity failed."""


class ManagedReviewWorkflowPathConflictError(ManagedReviewWorkflowError):
    """Authority and checkpoint persistence resolve to one physical target."""


class ManagedReviewWorkflowNotStartedError(ManagedReviewWorkflowError):
    """Resume or retry was requested before a workflow thread existed."""


class ManagedReviewWorkflowClosedError(ManagedReviewWorkflowError):
    """The workflow service has already closed its checkpoint connection."""


class ManagedReviewOrchestrationPhase(StrEnum):
    NOT_STARTED = "not-started"
    WAITING = "waiting"
    RECONCILIATION_PENDING = "reconciliation-pending"
    COMPLETE = "complete"
    RECOVERY_REQUIRED = "recovery-required"


class ManagedReviewCheckpointHealth(StrEnum):
    ABSENT = "absent"
    HEALTHY = "healthy"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ManagedReviewWorkflowStatus:
    request_id: str
    workflow_id: str
    authoritative_lifecycle: ManagedRevisionStoreLifecycle
    phase: ManagedReviewOrchestrationPhase
    checkpoint_health: ManagedReviewCheckpointHealth
    decision_record: ManagedRevisionDecisionRecord | None
    detail: str | None = None


class _WorkflowState(TypedDict):
    workflow_schema_version: int
    workflow_id: str
    request_id: str
    request_record_id: str
    request_record_sha256: str
    request_operation_id: str
    request_payload_sha256: str
    bundle_id: str
    bundle_sha256: str
    run_binding_id: str
    revision_admission_id: str
    revision_admission_sha256: str
    governing_source_adoption_id: str
    governing_source_adoption_sha256: str
    governing_source_repository_binding_sha256: str
    governing_reviewed_snapshot_binding_id: str
    governing_reviewed_snapshot_binding_sha256: str
    governing_temporal_decision_record_sha256: str
    aggregate_id: str
    review_open_revision: int
    review_open_aggregate_sha256: str
    expected_authority_id: str
    expected_authority_revision: int
    expected_active_pointer_sha256: str
    authority_lifecycle: str | None
    authority_decision_record_sha256: str | None
    authority_current_head_revision: int | None
    authority_current_head_sha256: str | None
    authority_current_id: str | None
    authority_current_revision: int | None
    authority_current_pointer_sha256: str | None


def managed_review_workflow_id(request_id: str) -> str:
    if type(request_id) is not str or re.fullmatch(r"mrequest:[0-9a-f]{64}", request_id) is None:
        raise ValueError("managed workflow requires an exact managed request ID")
    return f"{_WORKFLOW_COMPATIBILITY}/{request_id}"


def _same_target(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _is_busy_error(exc: sqlite3.Error) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _is_checkpoint_availability_error(exc: sqlite3.Error) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "attempt to write a readonly database",
            "disk i/o error",
            "permission denied",
            "read-only",
            "readonly",
            "unable to open database file",
        )
    )


class ManagedReviewWorkflow:
    """Synchronous fixed-wake workflow for one already-created V2 request."""

    def __init__(
        self,
        request_id: str,
        *,
        authority_path: Path | str,
        checkpoint_path: Path | str,
        resolver: RepositoryBackedManagedReviewResolver,
        verified_bootstrap: VerifiedAnalysisBootstrapCapability,
        prechange_head: AggregateHeadBinding,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        if type(resolver) is not RepositoryBackedManagedReviewResolver:
            raise TypeError("managed workflow requires the production repository resolver")
        self.request_id = request_id
        self.workflow_id = managed_review_workflow_id(request_id)
        self.authority_path = Path(authority_path)
        self.checkpoint_path = Path(checkpoint_path)
        self._resolver = resolver
        self._verified_bootstrap = verified_bootstrap
        self._prechange_head = AggregateHeadBinding.model_validate_json(
            prechange_head.model_dump_json()
        )
        self._timeout_seconds = timeout_seconds
        if _same_target(self.authority_path, self.checkpoint_path):
            raise ManagedReviewWorkflowPathConflictError(
                "authority and managed workflow checkpoint databases must be distinct files"
            )
        self._config: RunnableConfig = {
            "configurable": {"thread_id": self.workflow_id, "checkpoint_ns": ""}
        }
        self._lock = threading.RLock()
        self._closed = False
        self._checkpoint_conn: sqlite3.Connection | None = None
        self._checkpointer: SqliteSaver | None = None
        self._graph = None
        self._expected_identity: dict[str, int | str] | None = None

    def __enter__(self) -> ManagedReviewWorkflow:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                if self._checkpoint_conn is not None:
                    self._checkpoint_conn.close()
                self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ManagedReviewWorkflowClosedError("managed-review workflow service is closed")

    def _identity_from_authority(
        self, view: ManagedRevisionReviewStoreView
    ) -> dict[str, int | str]:
        record = view.request_record
        command = record.command
        bundle = command.bundle
        run = bundle.run_binding
        if type(run) is not ManagedRunBindingV2:
            raise ManagedReviewWorkflowAuthorityError(
                "managed workflow requires an authoritative V2 request"
            )
        admission = run.revision_planning_admission
        adoption = run.governing_source_adoption
        base = bundle.review_base
        return {
            "workflow_schema_version": _WORKFLOW_SCHEMA_VERSION,
            "workflow_id": self.workflow_id,
            "request_id": command.request_id,
            "request_record_id": record.record_id,
            "request_record_sha256": record.record_sha256,
            "request_operation_id": command.operation_id,
            "request_payload_sha256": command.request_payload_sha256,
            "bundle_id": bundle.bundle_id,
            "bundle_sha256": bundle.bundle_sha256,
            "run_binding_id": run.run_binding_id,
            "revision_admission_id": admission.admission_id,
            "revision_admission_sha256": admission.admission_sha256,
            "governing_source_adoption_id": adoption.adoption_id,
            "governing_source_adoption_sha256": adoption.adoption_sha256,
            "governing_source_repository_binding_sha256": (
                adoption.source_repository_binding_sha256
            ),
            "governing_reviewed_snapshot_binding_id": adoption.reviewed_snapshot_binding_id,
            "governing_reviewed_snapshot_binding_sha256": (
                adoption.reviewed_snapshot_binding_sha256
            ),
            "governing_temporal_decision_record_sha256": (
                adoption.temporal_decision_record_sha256
            ),
            "aggregate_id": base.review_open_head.aggregate_id,
            "review_open_revision": base.review_open_head.revision,
            "review_open_aggregate_sha256": base.review_open_head.aggregate_sha256,
            "expected_authority_id": base.authority.authority_id,
            "expected_authority_revision": base.authority.authority_revision,
            "expected_active_pointer_sha256": base.authority.active_pointer_sha256,
        }

    def _read_authority(self) -> ManagedRevisionReviewStoreView:
        if not self.authority_path.is_file():
            raise ManagedReviewWorkflowAuthorityError(
                "authoritative change-control database does not exist"
            )
        store: SqliteManagedChangeControlStore | None = None
        try:
            store = SqliteManagedChangeControlStore(
                self.authority_path, timeout_seconds=self._timeout_seconds
            )
            view = store.get_managed_review(
                self.request_id,
                resolver=self._resolver,
                verified_bootstrap=self._verified_bootstrap,
                prechange_head=self._prechange_head,
            )
        except ChangeControlBusyError as exc:
            raise ManagedReviewWorkflowAuthorityError(
                "authoritative change-control database is busy", retryable=True
            ) from exc
        except ChangeControlStoreError as exc:
            raise ManagedReviewWorkflowAuthorityError(
                "authoritative managed review could not be read safely"
            ) from exc
        except sqlite3.Error as exc:
            raise ManagedReviewWorkflowAuthorityError(
                "authoritative change-control database could not be read",
                retryable=_is_busy_error(exc),
            ) from exc
        finally:
            if store is not None:
                store.close()
        identity = self._identity_from_authority(view)
        if self._expected_identity is None:
            self._expected_identity = identity
        elif identity != self._expected_identity:
            raise ManagedReviewWorkflowAuthorityError(
                "authoritative managed request differs from the immutable workflow binding"
            )
        return view

    def _initial_state(self, authority: ManagedRevisionReviewStoreView) -> _WorkflowState:
        identity = self._identity_from_authority(authority)
        return cast(
            _WorkflowState,
            {
                **identity,
                "authority_lifecycle": None,
                "authority_decision_record_sha256": None,
                "authority_current_head_revision": None,
                "authority_current_head_sha256": None,
                "authority_current_id": None,
                "authority_current_revision": None,
                "authority_current_pointer_sha256": None,
            },
        )

    def _validate_state(self, value: object) -> _WorkflowState:
        if not isinstance(value, dict) or set(value) != _STATE_KEYS:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint has an unsupported field schema"
            )
        state = cast(dict[str, object], value)
        if self._expected_identity is None:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint was read without authoritative identity"
            )
        for key, expected in self._expected_identity.items():
            if type(state[key]) is not type(expected) or state[key] != expected:
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "managed workflow checkpoint identity differs from SQLite authority"
                )
        lifecycle = state["authority_lifecycle"]
        if lifecycle not in (None, *(item.value for item in ManagedRevisionStoreLifecycle)):
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint has an invalid authority lifecycle"
            )
        optional_ints = (
            "authority_current_head_revision",
            "authority_current_revision",
        )
        optional_strings = (
            "authority_decision_record_sha256",
            "authority_current_head_sha256",
            "authority_current_id",
            "authority_current_pointer_sha256",
        )
        if any(
            state[key] is not None and type(state[key]) is not int for key in optional_ints
        ) or any(
            state[key] is not None and type(state[key]) is not str for key in optional_strings
        ):
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint observations are not primitive and canonical"
            )
        decision_sha = state["authority_decision_record_sha256"]
        if decision_sha is not None and (
            len(cast(str, decision_sha)) != 64
            or any(character not in "0123456789abcdef" for character in cast(str, decision_sha))
        ):
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint has an invalid decision digest"
            )
        observed = tuple(state[key] for key in optional_ints + optional_strings[1:])
        if lifecycle is None:
            if decision_sha is not None or any(item is not None for item in observed):
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "unreconciled managed checkpoint contains authority observations"
                )
        elif any(item is None for item in observed):
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "reconciled managed checkpoint omits authority observations"
            )
        if lifecycle == ManagedRevisionStoreLifecycle.DECIDED.value:
            if decision_sha is None:
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "decided managed checkpoint omits its decision digest"
                )
        elif decision_sha is not None:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "non-decided managed checkpoint contains a decision digest"
            )
        return cast(_WorkflowState, state)

    def _reconcile_authority(self, state: _WorkflowState) -> _WorkflowState:
        self._validate_state(state)
        view = self._read_authority()
        return {
            **state,
            "authority_lifecycle": view.lifecycle.value,
            "authority_decision_record_sha256": (
                view.decision_record.record_sha256 if view.decision_record is not None else None
            ),
            "authority_current_head_revision": view.current_head.revision,
            "authority_current_head_sha256": view.current_head.aggregate_sha256,
            "authority_current_id": view.current_authority.authority_id,
            "authority_current_revision": view.current_authority.authority_revision,
            "authority_current_pointer_sha256": view.current_authority.active_pointer_sha256,
        }

    @staticmethod
    def _route_after_reconcile(
        state: _WorkflowState,
    ) -> Literal["await_wake_signal", "__end__"]:
        return (
            "await_wake_signal"
            if state["authority_lifecycle"] == ManagedRevisionStoreLifecycle.OPEN.value
            else "__end__"
        )

    @staticmethod
    def _await_wake_signal(state: _WorkflowState) -> dict[str, object]:  # noqa: ARG004
        interrupt(_WAKE_SIGNAL)
        return {}

    def _build_graph(self):
        assert self._checkpointer is not None
        graph = StateGraph(_WorkflowState)
        graph.add_node("reconcile_authority", self._reconcile_authority)
        graph.add_node("await_wake_signal", self._await_wake_signal)
        graph.add_edge(START, "reconcile_authority")
        graph.add_conditional_edges(
            "reconcile_authority",
            self._route_after_reconcile,
            {"await_wake_signal": "await_wake_signal", "__end__": END},
        )
        graph.add_edge("await_wake_signal", "reconcile_authority")
        return graph.compile(checkpointer=self._checkpointer, name=_WORKFLOW_COMPATIBILITY)

    def _validate_open_checkpoint_connection(self) -> None:
        assert self._checkpoint_conn is not None
        try:
            database_row = next(
                row
                for row in self._checkpoint_conn.execute("PRAGMA database_list")
                if str(row[1]) == "main"
            )
            opened_name = str(database_row[2])
            if not opened_name:
                raise ManagedReviewWorkflowPathConflictError(
                    "managed workflow checkpoints require a durable file"
                )
            opened_path = Path(opened_name)
            opened_stat = opened_path.stat()
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ManagedReviewWorkflowPathConflictError(
                    "managed workflow checkpoints require a regular file"
                )
            tables = {
                str(row[0])
                for row in self._checkpoint_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        except StopIteration as exc:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint has no main database"
            ) from exc
        except OSError as exc:
            raise ManagedReviewWorkflowCheckpointError(
                "opened managed workflow checkpoint has no stable file identity",
                retryable=isinstance(exc, FileNotFoundError),
            ) from exc
        if _same_target(self.authority_path, opened_path) or _same_target(
            self.authority_path, self.checkpoint_path
        ):
            raise ManagedReviewWorkflowPathConflictError(
                "opened managed workflow checkpoint resolves to review authority"
            )
        if tables and tables != {"checkpoints", "writes"}:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint has an incompatible table inventory"
            )

    def _initialize_checkpoint(self) -> None:
        if self._checkpoint_conn is not None:
            return
        if _same_target(self.authority_path, self.checkpoint_path):
            raise ManagedReviewWorkflowPathConflictError(
                "authority and managed workflow checkpoint databases must be distinct files"
            )
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._checkpoint_conn = sqlite3.connect(
                str(self.checkpoint_path),
                timeout=self._timeout_seconds,
                check_same_thread=False,
            )
            self._validate_open_checkpoint_connection()
            serde = JsonPlusSerializer(
                pickle_fallback=False,
                allowed_json_modules=None,
                allowed_msgpack_modules=None,
            )
            self._checkpointer = SqliteSaver(self._checkpoint_conn, serde=serde)
            self._graph = self._build_graph()
        except Exception as exc:
            connection = self._checkpoint_conn
            if connection is not None:
                connection.close()
            self._checkpoint_conn = None
            self._checkpointer = None
            self._graph = None
            if isinstance(exc, ManagedReviewWorkflowError):
                raise
            if isinstance(exc, sqlite3.Error):
                if _is_busy_error(exc):
                    raise ManagedReviewWorkflowCheckpointError(
                        "managed workflow checkpoint database is busy", retryable=True
                    ) from exc
                if _is_checkpoint_availability_error(exc):
                    raise ManagedReviewWorkflowCheckpointError(
                        "managed workflow checkpoint could not be opened"
                    ) from exc
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "managed workflow checkpoint database is unreadable"
                ) from exc
            if isinstance(exc, OSError):
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint path could not be prepared",
                    retryable=isinstance(exc, FileNotFoundError),
                ) from exc
            raise
        finally:
            if self._graph is None and self._checkpoint_conn is not None:
                self._checkpoint_conn.close()
                self._checkpoint_conn = None
                self._checkpointer = None

    def _checkpoint_thread_exists(self) -> bool:
        try:
            if not self.checkpoint_path.is_file():
                return False
        except OSError as exc:
            raise ManagedReviewWorkflowCheckpointError(
                "managed workflow checkpoint path could not be inspected",
                retryable=isinstance(exc, FileNotFoundError),
            ) from exc
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self.checkpoint_path.resolve(strict=True).as_uri()}?mode=ro",
                uri=True,
                timeout=self._timeout_seconds,
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                return False
            if tables != {"checkpoints", "writes"}:
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "managed workflow checkpoint has an incompatible table inventory"
                )
            return (
                connection.execute(
                    "SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' LIMIT 1",
                    (self.workflow_id,),
                ).fetchone()
                is not None
            )
        except ManagedReviewWorkflowCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint database is busy", retryable=True
                ) from exc
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint database is unreadable"
            ) from exc
        except OSError as exc:
            raise ManagedReviewWorkflowCheckpointError(
                "managed workflow checkpoint path changed during inspection",
                retryable=isinstance(exc, FileNotFoundError),
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _checkpoint_tuple(self):
        self._initialize_checkpoint()
        assert self._checkpointer is not None
        try:
            checkpoint = self._checkpointer.get_tuple(self._config)
            if checkpoint is None:
                return None
            channels = checkpoint.checkpoint.get("channel_values")
            if not isinstance(channels, dict) or not set(channels).issubset(
                _STATE_KEYS | _INTERNAL_CHANNELS
            ):
                raise ManagedReviewWorkflowCheckpointCorruptionError(
                    "managed workflow checkpoint contains unsupported state channels"
                )
            return checkpoint
        except ManagedReviewWorkflowCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint database is busy", retryable=True
                ) from exc
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint database is unreadable"
            ) from exc
        except (
            AttributeError,
            IndexError,
            KeyError,
            NotImplementedError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint failed strict deserialization"
            ) from exc

    def _snapshot(self):
        if self._graph is None and not self._checkpoint_thread_exists():
            return None
        checkpoint = self._checkpoint_tuple()
        if checkpoint is None:
            return None
        try:
            assert self._graph is not None
            snapshot = self._graph.get_state(self._config)
            self._validate_state(snapshot.values)
            return snapshot
        except ManagedReviewWorkflowCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint database is busy", retryable=True
                ) from exc
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint database is unreadable"
            ) from exc
        except (
            AttributeError,
            IndexError,
            KeyError,
            NotImplementedError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint state is malformed"
            ) from exc

    def _invoke(self, value: _WorkflowState | Command | None) -> None:
        self._initialize_checkpoint()
        assert self._graph is not None
        try:
            self._graph.invoke(value, self._config)
        except (ManagedReviewWorkflowAuthorityError, ManagedReviewWorkflowCheckpointError):
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint database is busy", retryable=True
                ) from exc
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint write failed"
            ) from exc
        except (KeyError, NotImplementedError, TypeError, ValueError, UnicodeError) as exc:
            raise ManagedReviewWorkflowCheckpointCorruptionError(
                "managed workflow checkpoint execution failed validation"
            ) from exc
        except RuntimeError as exc:
            raise ManagedReviewWorkflowCheckpointError(
                "managed workflow checkpoint operation did not complete", retryable=True
            ) from exc

    def _terminal_observation_matches(
        self, authority: ManagedRevisionReviewStoreView, state: _WorkflowState
    ) -> bool:
        """Match immutable request lifecycle evidence; live heads remain informational."""

        return (
            state["authority_lifecycle"] == authority.lifecycle.value
            and state["authority_decision_record_sha256"]
            == (
                authority.decision_record.record_sha256
                if authority.decision_record is not None
                else None
            )
        )

    def _status_from(self, authority: ManagedRevisionReviewStoreView, snapshot) -> ManagedReviewWorkflowStatus:
        if snapshot is None:
            phase = ManagedReviewOrchestrationPhase.NOT_STARTED
        elif snapshot.interrupts or any(task.interrupts for task in snapshot.tasks):
            phase = (
                ManagedReviewOrchestrationPhase.WAITING
                if authority.lifecycle == ManagedRevisionStoreLifecycle.OPEN
                else ManagedReviewOrchestrationPhase.RECONCILIATION_PENDING
            )
        elif any(task.error is not None for task in snapshot.tasks):
            phase = ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED
        elif not snapshot.next and not snapshot.tasks:
            state = self._validate_state(snapshot.values)
            phase = (
                ManagedReviewOrchestrationPhase.COMPLETE
                if authority.lifecycle != ManagedRevisionStoreLifecycle.OPEN
                and self._terminal_observation_matches(authority, state)
                else ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED
            )
        else:
            phase = ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED
        return ManagedReviewWorkflowStatus(
            request_id=self.request_id,
            workflow_id=self.workflow_id,
            authoritative_lifecycle=authority.lifecycle,
            phase=phase,
            checkpoint_health=(
                ManagedReviewCheckpointHealth.ABSENT
                if snapshot is None
                else ManagedReviewCheckpointHealth.HEALTHY
            ),
            decision_record=authority.decision_record,
        )

    def status(self) -> ManagedReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            try:
                snapshot = self._snapshot()
            except ManagedReviewWorkflowCheckpointCorruptionError as exc:
                return ManagedReviewWorkflowStatus(
                    request_id=self.request_id,
                    workflow_id=self.workflow_id,
                    authoritative_lifecycle=authority.lifecycle,
                    phase=ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED,
                    checkpoint_health=ManagedReviewCheckpointHealth.CORRUPT,
                    decision_record=authority.decision_record,
                    detail=str(exc),
                )
            return self._status_from(authority, snapshot)

    def start(self) -> ManagedReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                self._invoke(self._initial_state(authority))
                authority = self._read_authority()
                snapshot = self._snapshot()
            return self._status_from(authority, snapshot)

    def resume(self) -> ManagedReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                raise ManagedReviewWorkflowNotStartedError(
                    "managed-review workflow has not started"
                )
            current = self._status_from(authority, snapshot)
            if current.phase == ManagedReviewOrchestrationPhase.COMPLETE:
                return current
            if current.phase == ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED:
                raise ManagedReviewWorkflowCheckpointError(
                    "managed workflow checkpoint requires retry", retryable=True
                )
            self._invoke(Command(resume=_WAKE_SIGNAL))
            return self._status_from(self._read_authority(), self._snapshot())

    def retry(self) -> ManagedReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                raise ManagedReviewWorkflowNotStartedError(
                    "retry cannot create a managed-review workflow thread"
                )
            current = self._status_from(authority, snapshot)
            if current.phase == ManagedReviewOrchestrationPhase.COMPLETE:
                return current
            if current.phase == ManagedReviewOrchestrationPhase.RECONCILIATION_PENDING:
                self._invoke(Command(resume=_WAKE_SIGNAL))
            elif current.phase == ManagedReviewOrchestrationPhase.RECOVERY_REQUIRED:
                self._invoke(None)
            return self._status_from(self._read_authority(), self._snapshot())


__all__ = [
    "ManagedReviewCheckpointHealth",
    "ManagedReviewOrchestrationPhase",
    "ManagedReviewWorkflow",
    "ManagedReviewWorkflowAuthorityError",
    "ManagedReviewWorkflowCheckpointCorruptionError",
    "ManagedReviewWorkflowCheckpointError",
    "ManagedReviewWorkflowClosedError",
    "ManagedReviewWorkflowError",
    "ManagedReviewWorkflowNotStartedError",
    "ManagedReviewWorkflowPathConflictError",
    "ManagedReviewWorkflowStatus",
    "managed_review_workflow_id",
]
