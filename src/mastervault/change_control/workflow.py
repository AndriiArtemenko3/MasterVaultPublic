"""Durable, authority-reconciling wait orchestration for temporal review."""

from __future__ import annotations

import os
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

from mastervault.change_control.review import (
    HumanReviewDecision,
    HumanReviewRequest,
    HumanReviewRequestView,
    ReviewLifecycle,
)
from mastervault.change_control.store import (
    ChangeControlBusyError,
    ChangeControlStoreError,
    SqliteChangeControlStore,
)

_WORKFLOW_SCHEMA_VERSION = 1
_WORKFLOW_COMPATIBILITY = "mastervault.temporal-review-wait.v1"
_WAKE_SIGNAL = {"kind": "mastervault.temporal-review-wake", "version": 1}
_STATE_KEYS = frozenset(
    {
        "workflow_schema_version",
        "workflow_id",
        "request_id",
        "request_operation_id",
        "aggregate_id",
        "base_revision",
        "base_aggregate_sha256",
        "authority_lifecycle",
        "authority_decision_payload_sha256",
        "authority_decided_revision",
    }
)
_INTERNAL_CHANNELS = frozenset({"branch:to:reconcile_authority", "branch:to:await_wake_signal"})


class TemporalReviewWorkflowError(RuntimeError):
    """Base error for temporal-review wait orchestration."""


class TemporalReviewAuthorityError(TemporalReviewWorkflowError):
    """The authoritative change-control database could not be read safely."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TemporalReviewCheckpointError(TemporalReviewWorkflowError):
    """The disposable workflow checkpoint could not be used safely."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TemporalReviewCheckpointCorruptionError(TemporalReviewCheckpointError):
    """A checkpoint failed strict deserialization, identity, or schema validation."""


class TemporalReviewPathConflictError(TemporalReviewWorkflowError):
    """Authority and checkpoint persistence resolve to the same target."""


class TemporalReviewNotStartedError(TemporalReviewWorkflowError):
    """Resume or retry was requested before a workflow thread existed."""


class TemporalReviewClosedError(TemporalReviewWorkflowError):
    """The workflow service has already closed its owned connection."""


class OrchestrationPhase(StrEnum):
    """Execution phase; deliberately separate from authoritative lifecycle."""

    NOT_STARTED = "not-started"
    WAITING = "waiting"
    RECONCILIATION_PENDING = "reconciliation-pending"
    COMPLETE = "complete"
    RECOVERY_REQUIRED = "recovery-required"


class CheckpointHealth(StrEnum):
    ABSENT = "absent"
    HEALTHY = "healthy"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class TemporalReviewWorkflowStatus:
    """Authoritative review state plus disposable orchestration health."""

    request_id: str
    workflow_id: str
    authoritative_lifecycle: ReviewLifecycle
    phase: OrchestrationPhase
    checkpoint_health: CheckpointHealth
    decision: HumanReviewDecision | None
    detail: str | None = None


class _WorkflowState(TypedDict):
    """Checkpoint state: JSON primitives only, with an explicit compatibility binding."""

    workflow_schema_version: int
    workflow_id: str
    request_id: str
    request_operation_id: str
    aggregate_id: str
    base_revision: int
    base_aggregate_sha256: str
    authority_lifecycle: str | None
    authority_decision_payload_sha256: str | None
    authority_decided_revision: int | None


def temporal_review_workflow_id(request: HumanReviewRequest) -> str:
    """Return the versioned, deterministic thread ID for one immutable request."""

    return f"{_WORKFLOW_COMPATIBILITY}/{request.request_id}"


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


class TemporalReviewWorkflow:
    """One-request synchronous LangGraph service with process-local serialization.

    ``state.sqlite3`` remains the sole review authority. The owned checkpoint
    connection contains only disposable execution and reconciliation cursors.
    The lock coordinates callers using this service instance in one process;
    it is not a distributed or multi-process concurrency mechanism.
    """

    def __init__(
        self,
        request: HumanReviewRequest,
        *,
        authority_path: Path | str,
        checkpoint_path: Path | str,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        self.request = HumanReviewRequest.model_validate_json(request.model_dump_json())
        self.authority_path = Path(authority_path)
        self.checkpoint_path = Path(checkpoint_path)
        self._timeout_seconds = timeout_seconds
        if _same_target(self.authority_path, self.checkpoint_path):
            raise TemporalReviewPathConflictError(
                "authority and workflow checkpoint databases must be distinct files"
            )
        self.workflow_id = temporal_review_workflow_id(self.request)
        self._config: RunnableConfig = {
            "configurable": {
                "thread_id": self.workflow_id,
                "checkpoint_ns": "",
            }
        }
        self._lock = threading.RLock()
        self._closed = False
        self._checkpoint_conn: sqlite3.Connection | None = None
        self._checkpointer: SqliteSaver | None = None
        self._graph = None

    def _initialize_checkpoint(self) -> None:
        if self._checkpoint_conn is not None:
            return
        # Repeat the physical-identity check immediately before setup so a
        # path swapped after construction cannot redirect checkpoints into authority.
        if _same_target(self.authority_path, self.checkpoint_path):
            raise TemporalReviewPathConflictError(
                "authority and workflow checkpoint databases must be distinct files"
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
            if isinstance(exc, TemporalReviewWorkflowError):
                raise
            if isinstance(exc, sqlite3.Error):
                if _is_busy_error(exc):
                    raise TemporalReviewCheckpointError(
                        "workflow checkpoint database is busy", retryable=True
                    ) from exc
                if _is_checkpoint_availability_error(exc):
                    raise TemporalReviewCheckpointError(
                        "workflow checkpoint database could not be opened",
                        retryable=False,
                    ) from exc
                raise TemporalReviewCheckpointCorruptionError(
                    "workflow checkpoint database is unreadable"
                ) from exc
            if isinstance(exc, OSError):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint path could not be prepared",
                    retryable=isinstance(exc, FileNotFoundError),
                ) from exc
            raise
        finally:
            # Interpreter-level interrupts are deliberately not translated,
            # but an interrupted initialization must not leak its open handle.
            if self._graph is None and self._checkpoint_conn is not None:
                self._checkpoint_conn.close()
                self._checkpoint_conn = None
                self._checkpointer = None

    def _validate_open_checkpoint_connection(self) -> None:
        """Validate the opened target before a saver can create checkpoint DDL."""

        assert self._checkpoint_conn is not None
        try:
            database_row = next(
                row
                for row in self._checkpoint_conn.execute("PRAGMA database_list")
                if str(row[1]) == "main"
            )
            opened_name = str(database_row[2])
            if not opened_name:
                raise TemporalReviewPathConflictError(
                    "workflow checkpoints require a durable file-backed main database"
                )
            opened_path = Path(opened_name)
            try:
                opened_stat = opened_path.stat()
            except OSError as exc:
                raise TemporalReviewCheckpointError(
                    "opened workflow checkpoint has no stable file identity",
                    retryable=isinstance(exc, FileNotFoundError),
                ) from exc
            if not stat.S_ISREG(opened_stat.st_mode):
                raise TemporalReviewPathConflictError(
                    "workflow checkpoints require a regular file-backed main database"
                )
            tables = {
                str(row[0])
                for row in self._checkpoint_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        except StopIteration as exc:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint connection has no main database"
            ) from exc
        if _same_target(self.authority_path, opened_path) or _same_target(
            self.authority_path, self.checkpoint_path
        ):
            raise TemporalReviewPathConflictError(
                "opened workflow checkpoint database resolves to review authority"
            )
        if tables and tables != {"checkpoints", "writes"}:
            raise TemporalReviewCheckpointCorruptionError(
                "opened workflow checkpoint database has an incompatible table inventory"
            )

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

    def __enter__(self) -> TemporalReviewWorkflow:
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
            raise TemporalReviewClosedError("temporal-review workflow service is closed")

    def _read_authority(self) -> HumanReviewRequestView:
        # SqliteChangeControlStore normally creates its target. A read-only
        # orchestration seam must never turn a missing authority into an empty DB.
        if not self.authority_path.is_file():
            raise TemporalReviewAuthorityError(
                "authoritative change-control database does not exist"
            )
        store: SqliteChangeControlStore | None = None
        try:
            store = SqliteChangeControlStore(
                self.authority_path, timeout_seconds=self._timeout_seconds
            )
            view = store.get_review_request(self.request.request_id)
        except ChangeControlBusyError as exc:
            raise TemporalReviewAuthorityError(
                "authoritative change-control database is busy", retryable=True
            ) from exc
        except ChangeControlStoreError as exc:
            raise TemporalReviewAuthorityError(
                "authoritative review request could not be read safely"
            ) from exc
        except sqlite3.Error as exc:
            raise TemporalReviewAuthorityError(
                "authoritative change-control database could not be read",
                retryable=_is_busy_error(exc),
            ) from exc
        finally:
            if store is not None:
                store.close()
        if view.request != self.request:
            raise TemporalReviewAuthorityError(
                "authoritative review request does not match the immutable workflow binding"
            )
        return view

    def _initial_state(self) -> _WorkflowState:
        return {
            "workflow_schema_version": _WORKFLOW_SCHEMA_VERSION,
            "workflow_id": self.workflow_id,
            "request_id": self.request.request_id,
            "request_operation_id": self.request.operation_id,
            "aggregate_id": self.request.aggregate_id,
            "base_revision": self.request.base_revision,
            "base_aggregate_sha256": self.request.base_aggregate_sha256,
            "authority_lifecycle": None,
            "authority_decision_payload_sha256": None,
            "authority_decided_revision": None,
        }

    def _validate_state(self, value: object) -> _WorkflowState:
        if not isinstance(value, dict) or set(value) != _STATE_KEYS:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint state has an unsupported field schema"
            )
        state = cast(dict[str, object], value)
        integer_fields = ("workflow_schema_version", "base_revision")
        string_fields = (
            "workflow_id",
            "request_id",
            "request_operation_id",
            "aggregate_id",
            "base_aggregate_sha256",
        )
        if any(type(state[field]) is not int for field in integer_fields) or any(
            type(state[field]) is not str for field in string_fields
        ):
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint state contains noncanonical primitive types"
            )
        if state["workflow_schema_version"] != _WORKFLOW_SCHEMA_VERSION:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint schema version is unsupported"
            )
        expected = self._initial_state()
        if (
            state["workflow_id"] != expected["workflow_id"]
            or state["request_id"] != expected["request_id"]
            or state["request_operation_id"] != expected["request_operation_id"]
            or state["aggregate_id"] != expected["aggregate_id"]
            or state["base_revision"] != expected["base_revision"]
            or state["base_aggregate_sha256"] != expected["base_aggregate_sha256"]
        ):
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint identity does not match its immutable review request"
            )
        lifecycle = state["authority_lifecycle"]
        if lifecycle not in (None, *(item.value for item in ReviewLifecycle)):
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint has an invalid observed authority lifecycle"
            )
        decision_sha = state["authority_decision_payload_sha256"]
        decided_revision = state["authority_decided_revision"]
        if decision_sha is not None and (
            type(decision_sha) is not str
            or len(decision_sha) != 64
            or any(character not in "0123456789abcdef" for character in decision_sha)
        ):
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint has an invalid observed decision digest"
            )
        if decided_revision is not None and type(decided_revision) is not int:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint has an invalid observed decision revision"
            )
        if lifecycle == ReviewLifecycle.DECIDED.value:
            if decision_sha is None or decided_revision is None:
                raise TemporalReviewCheckpointCorruptionError(
                    "decided checkpoint observation is incomplete"
                )
        elif decision_sha is not None or decided_revision is not None:
            raise TemporalReviewCheckpointCorruptionError(
                "non-decided checkpoint observation contains decision fields"
            )
        return cast(_WorkflowState, state)

    def _reconcile_authority(self, state: _WorkflowState) -> _WorkflowState:
        self._validate_state(state)
        view = self._read_authority()
        decision = view.decision
        return {
            **state,
            "authority_lifecycle": view.lifecycle.value,
            "authority_decision_payload_sha256": (
                decision.decision_payload_sha256 if decision is not None else None
            ),
            "authority_decided_revision": (
                decision.decided_revision if decision is not None else None
            ),
        }

    @staticmethod
    def _route_after_reconcile(
        state: _WorkflowState,
    ) -> Literal["await_wake_signal", "__end__"]:
        return (
            "await_wake_signal"
            if state["authority_lifecycle"] == ReviewLifecycle.OPEN.value
            else "__end__"
        )

    @staticmethod
    def _await_wake_signal(state: _WorkflowState) -> dict[str, object]:  # noqa: ARG004
        # The returned resume value is intentionally ignored. Public callers
        # cannot supply it; every wake is the same non-authoritative signal.
        interrupt(_WAKE_SIGNAL)
        return {}

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
                raise TemporalReviewCheckpointCorruptionError(
                    "workflow checkpoint contains unsupported state channels"
                )
            return checkpoint
        except TemporalReviewCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is busy", retryable=True
                ) from exc
            if _is_checkpoint_availability_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is unavailable", retryable=False
                ) from exc
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint database is unreadable"
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
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint failed strict deserialization"
            ) from exc

    def _checkpoint_thread_exists(self) -> bool:
        """Probe existing checkpoint state without creating a file or schema."""

        try:
            if not self.checkpoint_path.is_file():
                return False
        except OSError as exc:
            raise TemporalReviewCheckpointError(
                "workflow checkpoint path could not be inspected",
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
                raise TemporalReviewCheckpointCorruptionError(
                    "workflow checkpoint database has an incompatible table inventory"
                )
            return (
                connection.execute(
                    "SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' LIMIT 1",
                    (self.workflow_id,),
                ).fetchone()
                is not None
            )
        except TemporalReviewCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is busy", retryable=True
                ) from exc
            if _is_checkpoint_availability_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is unavailable", retryable=False
                ) from exc
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint database is unreadable"
            ) from exc
        except OSError as exc:
            raise TemporalReviewCheckpointError(
                "workflow checkpoint path changed during inspection",
                retryable=isinstance(exc, FileNotFoundError),
            ) from exc
        finally:
            if connection is not None:
                connection.close()

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
        except TemporalReviewCheckpointError:
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is busy", retryable=True
                ) from exc
            if _is_checkpoint_availability_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is unavailable", retryable=False
                ) from exc
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint database is unreadable"
            ) from exc
        except (
            AttributeError,
            IndexError,
            KeyError,
            NotImplementedError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as exc:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint state is malformed"
            ) from exc

    def _invoke(self, value: _WorkflowState | Command | None) -> None:
        self._initialize_checkpoint()
        assert self._graph is not None
        try:
            self._graph.invoke(value, self._config)
        except (TemporalReviewAuthorityError, TemporalReviewCheckpointError):
            raise
        except sqlite3.Error as exc:
            if _is_busy_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint database is busy", retryable=True
                ) from exc
            if _is_checkpoint_availability_error(exc):
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint write was unavailable", retryable=False
                ) from exc
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint write or response failed"
            ) from exc
        except (KeyError, NotImplementedError, TypeError, ValueError, UnicodeError) as exc:
            raise TemporalReviewCheckpointCorruptionError(
                "workflow checkpoint execution failed validation"
            ) from exc
        except RuntimeError as exc:
            raise TemporalReviewCheckpointError(
                "workflow checkpoint operation did not complete", retryable=True
            ) from exc

    def _status_from(
        self, authority: HumanReviewRequestView, snapshot
    ) -> TemporalReviewWorkflowStatus:
        if snapshot is None:
            phase = OrchestrationPhase.NOT_STARTED
        elif snapshot.interrupts or any(task.interrupts for task in snapshot.tasks):
            phase = (
                OrchestrationPhase.WAITING
                if authority.lifecycle == ReviewLifecycle.OPEN
                else OrchestrationPhase.RECONCILIATION_PENDING
            )
        elif any(task.error is not None for task in snapshot.tasks):
            phase = OrchestrationPhase.RECOVERY_REQUIRED
        elif not snapshot.next and not snapshot.tasks:
            state = self._validate_state(snapshot.values)
            observed = state["authority_lifecycle"]
            decision = authority.decision
            observation_matches = observed == authority.lifecycle.value
            if authority.lifecycle == ReviewLifecycle.DECIDED:
                observation_matches = (
                    observation_matches
                    and decision is not None
                    and (
                        state["authority_decision_payload_sha256"]
                        == decision.decision_payload_sha256
                        and state["authority_decided_revision"] == decision.decided_revision
                    )
                )
            phase = (
                OrchestrationPhase.COMPLETE
                if observation_matches and authority.lifecycle != ReviewLifecycle.OPEN
                else OrchestrationPhase.RECOVERY_REQUIRED
            )
        else:
            phase = OrchestrationPhase.RECOVERY_REQUIRED
        return TemporalReviewWorkflowStatus(
            request_id=self.request.request_id,
            workflow_id=self.workflow_id,
            authoritative_lifecycle=authority.lifecycle,
            phase=phase,
            checkpoint_health=(
                CheckpointHealth.ABSENT if snapshot is None else CheckpointHealth.HEALTHY
            ),
            decision=authority.decision,
        )

    def status(self) -> TemporalReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            try:
                snapshot = self._snapshot()
            except TemporalReviewCheckpointCorruptionError as exc:
                return TemporalReviewWorkflowStatus(
                    request_id=self.request.request_id,
                    workflow_id=self.workflow_id,
                    authoritative_lifecycle=authority.lifecycle,
                    phase=OrchestrationPhase.RECOVERY_REQUIRED,
                    checkpoint_health=CheckpointHealth.CORRUPT,
                    decision=authority.decision,
                    detail=str(exc),
                )
            return self._status_from(authority, snapshot)

    def start(self) -> TemporalReviewWorkflowStatus:
        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                self._invoke(self._initial_state())
                authority = self._read_authority()
                snapshot = self._snapshot()
            return self._status_from(authority, snapshot)

    def resume(self) -> TemporalReviewWorkflowStatus:
        """Wake one existing wait with a fixed internal signal and reconcile authority."""

        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                raise TemporalReviewNotStartedError("temporal-review workflow has not started")
            current = self._status_from(authority, snapshot)
            if current.phase == OrchestrationPhase.COMPLETE:
                return current
            if current.phase == OrchestrationPhase.RECOVERY_REQUIRED:
                raise TemporalReviewCheckpointError(
                    "workflow checkpoint requires retry before it can be resumed",
                    retryable=True,
                )
            self._invoke(Command(resume=_WAKE_SIGNAL))
            authority = self._read_authority()
            return self._status_from(authority, self._snapshot())

    def retry(self) -> TemporalReviewWorkflowStatus:
        """Retry reconciliation only; never create or replay an authoritative decision."""

        with self._lock:
            self._require_open()
            authority = self._read_authority()
            snapshot = self._snapshot()
            if snapshot is None:
                raise TemporalReviewNotStartedError(
                    "retry cannot create a temporal-review workflow thread"
                )
            current = self._status_from(authority, snapshot)
            if current.phase == OrchestrationPhase.COMPLETE:
                return current
            if current.phase == OrchestrationPhase.RECONCILIATION_PENDING:
                self._invoke(Command(resume=_WAKE_SIGNAL))
            elif current.phase == OrchestrationPhase.RECOVERY_REQUIRED:
                self._invoke(None)
            return self._status_from(self._read_authority(), self._snapshot())
