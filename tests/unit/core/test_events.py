"""EventLog: closed-enum enforcement, round-trip, terminal_units folding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mastervault.core.errors import UnknownEventError
from mastervault.core.events import (
    Event,
    EventLog,
    EventName,
    read_events,
    terminal_units,
)

FIXED_TS = datetime(2026, 7, 7, 10, 30, tzinfo=UTC)


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / "events.jsonl", run_id="run-1", clock=lambda: FIXED_TS)


def test_emit_and_read_round_trip(log, tmp_path):
    log.emit("run.started", stage="init", payload={"pipeline": "ingest"})
    log.emit(EventName.UNIT_COMPLETED, stage="extract", unit="doc-1", payload={"claims": 3})

    events = list(read_events(tmp_path / "events.jsonl"))
    assert [e.event for e in events] == [EventName.RUN_STARTED, EventName.UNIT_COMPLETED]
    assert events[0].ts == FIXED_TS.isoformat()
    assert events[0].run_id == "run-1"
    assert events[0].payload == {"pipeline": "ingest"}
    assert events[1].unit == "doc-1"
    assert events[1].level == "info"


def test_unknown_event_name_raises_on_write(log):
    with pytest.raises(UnknownEventError, match="unit.exploded"):
        log.emit("unit.exploded", stage="extract", unit="doc-1")


def test_unknown_event_name_raises_on_read(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"ts": "2026-07-07T10:30:00+00:00", "run_id": "r", "stage": "s",'
        ' "event": "made.up", "unit": null, "level": "info", "payload": {}}\n'
    )
    with pytest.raises(UnknownEventError, match="made.up"):
        list(read_events(path))


def test_read_missing_file_yields_nothing(tmp_path):
    assert list(read_events(tmp_path / "absent.jsonl")) == []


def test_writes_are_append_only(log, tmp_path):
    log.emit("stage.started", stage="a")
    log.emit("stage.completed", stage="a")
    lines = (tmp_path / "events.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2


def _ev(event: EventName, unit: str | None, ts: str = "2026-07-07T10:00:00+00:00") -> Event:
    return Event(ts=ts, run_id="r", stage="s", event=event, unit=unit)


def test_terminal_units_last_terminal_event_wins():
    events = [
        _ev(EventName.DISPATCH_STARTED, "a"),  # non-terminal: ignored
        _ev(EventName.UNIT_COMPLETED, "a"),
        _ev(EventName.UNIT_FAILED, "b"),
        _ev(EventName.UNIT_COMPLETED, "b"),  # later event wins
        _ev(EventName.UNIT_SKIPPED, "c"),
        _ev(EventName.BUDGET_CHECKPOINT, None),  # no unit: ignored
    ]
    result = terminal_units(events)
    assert set(result) == {"a", "b", "c"}
    assert result["a"].event == EventName.UNIT_COMPLETED
    assert result["b"].event == EventName.UNIT_COMPLETED
    assert result["c"].event == EventName.UNIT_SKIPPED


def test_terminal_units_empty_stream():
    assert terminal_units([]) == {}
