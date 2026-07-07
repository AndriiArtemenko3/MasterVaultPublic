"""Append-only JSONL event log for a run dir.

One line per event, flushed on write, so a crashed run leaves a readable
prefix. The event vocabulary is a CLOSED enum: writing an unknown name raises
UnknownEventError instead of silently minting new event types. The reader
yields typed Event models; `terminal_units` folds a stream into the last
terminal event per unit, which is exactly what resume logic needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mastervault.core.errors import UnknownEventError

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventName(StrEnum):
    RUN_STARTED = "run.started"
    PLAN_FROZEN = "plan.frozen"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    DISPATCH_STARTED = "dispatch.started"
    DISPATCH_COMPLETED = "dispatch.completed"
    DISPATCH_AUTOFIXED = "dispatch.autofixed"
    DISPATCH_HARDFAIL = "dispatch.hardfail"
    DISPATCH_RETRIED = "dispatch.retried"
    UNIT_COMPLETED = "unit.completed"
    UNIT_FAILED = "unit.failed"
    UNIT_SKIPPED = "unit.skipped"
    BUDGET_CHECKPOINT = "budget.checkpoint"
    BUDGET_EXHAUSTED = "budget.exhausted"
    REVIEW_ENQUEUED = "review.enqueued"
    REVIEW_DEDUPED = "review.deduped"
    AUTO_APPLIED = "auto.applied"
    JUDGE_VERDICT = "judge.verdict"
    RUN_COMPLETED = "run.completed"


#: Events that settle a unit's fate. Resume treats these as "do not redo".
TERMINAL_UNIT_EVENTS: frozenset[EventName] = frozenset(
    {EventName.UNIT_COMPLETED, EventName.UNIT_FAILED, EventName.UNIT_SKIPPED}
)


class Event(BaseModel):
    ts: str  # ISO-8601, from the injectable clock
    run_id: str
    stage: str
    event: EventName
    unit: str | None = None
    level: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)


def _coerce_name(event: str | EventName) -> EventName:
    try:
        return EventName(event)
    except ValueError:
        raise UnknownEventError(
            f"unknown event name: {event!r} (the event enum is closed; add it to EventName "
            "deliberately or fix the producer)"
        ) from None


class EventLog:
    """Append-only writer bound to one events.jsonl file and one run_id."""

    def __init__(self, path: Path | str, run_id: str, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._clock = clock or utc_now

    def emit(
        self,
        event: str | EventName,
        stage: str,
        *,
        unit: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> Event:
        """Validate + append one event; returns the typed Event written."""
        record = Event(
            ts=self._clock().isoformat(),
            run_id=self.run_id,
            stage=stage,
            event=_coerce_name(event),
            unit=unit,
            level=level,
            payload=payload or {},
        )
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return record


def read_events(path: Path | str) -> Iterator[Event]:
    """Yield typed events from an events.jsonl file (blank lines skipped)."""
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            data = json.loads(line)
            _coerce_name(data.get("event", ""))
            yield Event.model_validate(data)


def terminal_units(events: Iterable[Event]) -> dict[str, Event]:
    """Fold an event stream into {unit: last terminal event}.

    Later events win, so a unit that failed and was later completed on a
    resume pass reads as completed.
    """
    out: dict[str, Event] = {}
    for ev in events:
        if ev.unit is not None and ev.event in TERMINAL_UNIT_EVENTS:
            out[ev.unit] = ev
    return out
