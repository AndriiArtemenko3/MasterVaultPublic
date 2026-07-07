"""RunContext: create/name-collision, freeze-plan-once, resume, summary."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from mastervault.core.errors import ResumeConflict
from mastervault.core.events import EventName
from mastervault.core.runctx import RunContext

FIXED = datetime(2026, 7, 7, 10, 30, tzinfo=UTC)


def fixed_clock():
    return FIXED


def test_create_makes_run_dir_layout(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    assert ctx.run_id == "ingest-2026-07-07-1030"
    assert ctx.run_dir == tmp_path / "ingest-2026-07-07-1030"
    assert (ctx.run_dir / "events.jsonl").is_file()
    assert (ctx.run_dir / "artifacts").is_dir()


def test_create_collision_appends_suffix(tmp_path):
    first = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    second = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    third = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    assert first.run_id == "ingest-2026-07-07-1030"
    assert second.run_id == "ingest-2026-07-07-1030-2"
    assert third.run_id == "ingest-2026-07-07-1030-3"


def test_create_explicit_run_id_collision_raises(tmp_path):
    RunContext.create(tmp_path, "ingest", run_id="fixed-id")
    with pytest.raises(ResumeConflict, match="already exists"):
        RunContext.create(tmp_path, "ingest", run_id="fixed-id")


def test_freeze_plan_writes_once_and_emits(tmp_path):
    ctx = RunContext.create(tmp_path, "lint", clock=fixed_clock)
    plan = {"stages": ["scan", "report"], "units": ["a", "b"]}
    ctx.freeze_plan(plan)

    on_disk = json.loads((ctx.run_dir / "plan.json").read_text())
    assert on_disk == plan
    assert ctx.plan == plan
    assert [e.event for e in ctx.events()] == [EventName.PLAN_FROZEN]


def test_freeze_plan_identical_second_call_is_noop(tmp_path):
    ctx = RunContext.create(tmp_path, "lint", clock=fixed_clock)
    plan = {"units": ["a"], "stages": ["scan"]}
    ctx.freeze_plan(plan)
    ctx.freeze_plan({"stages": ["scan"], "units": ["a"]})  # same content, different key order
    assert len([e for e in ctx.events() if e.event == EventName.PLAN_FROZEN]) == 1


def test_freeze_plan_different_second_call_raises(tmp_path):
    ctx = RunContext.create(tmp_path, "lint", clock=fixed_clock)
    ctx.freeze_plan({"units": ["a"]})
    with pytest.raises(ResumeConflict, match="frozen"):
        ctx.freeze_plan({"units": ["a", "b"]})


def test_emit_delegates_to_event_log(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    ctx.emit("run.started", stage="init")
    ctx.emit("unit.completed", stage="extract", unit="doc-1", payload={"n": 2})
    events = ctx.events()
    assert [e.event.value for e in events] == ["run.started", "unit.completed"]
    assert events[0].run_id == ctx.run_id
    assert events[0].ts == FIXED.isoformat()


def test_resume_loads_plan_and_terminal_units(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    ctx.freeze_plan({"units": ["doc-1", "doc-2", "doc-3"]})
    ctx.emit("run.started", stage="init")
    ctx.emit("unit.completed", stage="extract", unit="doc-1")
    ctx.emit("unit.failed", stage="extract", unit="doc-2")
    ctx.emit("unit.completed", stage="extract", unit="doc-2")  # retried later, now done

    resumed = RunContext.resume(ctx.run_dir)
    assert resumed.run_id == ctx.run_id
    assert resumed.plan == {"units": ["doc-1", "doc-2", "doc-3"]}
    assert set(resumed.completed_units) == {"doc-1", "doc-2"}
    assert resumed.completed_units["doc-2"].event == EventName.UNIT_COMPLETED


def test_resume_missing_dir_raises(tmp_path):
    with pytest.raises(ResumeConflict, match="does not exist"):
        RunContext.resume(tmp_path / "nope")


def test_resume_without_frozen_plan_raises(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    with pytest.raises(ResumeConflict, match="plan"):
        RunContext.resume(ctx.run_dir)


def test_write_summary(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock)
    path = ctx.write_summary({"units_total": 3, "units_failed": 1})
    assert path == ctx.run_dir / "summary.json"
    assert json.loads(path.read_text()) == {"units_total": 3, "units_failed": 1}


def test_ledger_attached_with_cap(tmp_path):
    ctx = RunContext.create(tmp_path, "ingest", clock=fixed_clock, cap_usd=5.0)
    ctx.ledger.record(1.0, "m", 10, 10)
    assert ctx.ledger.remaining == pytest.approx(4.0)
