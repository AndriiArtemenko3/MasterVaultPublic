"""RunContext: the per-run working directory + event log + budget ledger.

Layout of one run dir:

    <runs_dir>/<pipeline>-<YYYY-MM-DD-HHMM[-n]>/
        events.jsonl    append-only event log (core.events)
        plan.json       frozen plan — written exactly once
        summary.json    final human/machine summary (write_summary)
        artifacts/      free-form per-stage outputs

`create` starts a fresh run; `resume` reopens an existing one, loading the
frozen plan and the terminal state of every unit so the caller can skip
already-settled work. The clock is injectable so tests get stable names/ts.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from mastervault.core.budget import BudgetLedger
from mastervault.core.errors import ResumeConflict
from mastervault.core.events import (
    Clock,
    Event,
    EventLog,
    EventName,
    read_events,
    terminal_units,
    utc_now,
)

EVENTS_FILENAME = "events.jsonl"
PLAN_FILENAME = "plan.json"
SUMMARY_FILENAME = "summary.json"
ARTIFACTS_DIRNAME = "artifacts"


def _canonical(plan: dict[str, Any]) -> str:
    return json.dumps(plan, sort_keys=True, ensure_ascii=False)


class RunContext:
    def __init__(
        self,
        run_dir: Path,
        pipeline: str,
        run_id: str,
        *,
        clock: Clock | None = None,
        cap_usd: float = math.inf,
    ) -> None:
        self.run_dir = run_dir
        self.pipeline = pipeline
        self.run_id = run_id
        self._clock = clock or utc_now
        self._log = EventLog(run_dir / EVENTS_FILENAME, run_id, clock=self._clock)
        self.ledger = BudgetLedger(cap_usd)
        self._plan: dict[str, Any] | None = None
        self.completed_units: dict[str, Event] = {}

    # -- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        runs_dir: Path | str,
        pipeline: str,
        run_id: str | None = None,
        clock: Clock | None = None,
        *,
        cap_usd: float = math.inf,
    ) -> RunContext:
        """Make a fresh run dir. run_id defaults to <pipeline>-<YYYY-MM-DD-HHMM[-n]>."""
        runs_dir = Path(runs_dir)
        tick = clock or utc_now
        if run_id is None:
            base = f"{pipeline}-{tick().strftime('%Y-%m-%d-%H%M')}"
            run_id = base
            n = 2
            while (runs_dir / run_id).exists():
                run_id = f"{base}-{n}"
                n += 1
        run_dir = runs_dir / run_id
        if run_dir.exists():
            raise ResumeConflict(
                f"run dir already exists: {run_dir} (use RunContext.resume to reopen it)"
            )
        (run_dir / ARTIFACTS_DIRNAME).mkdir(parents=True)
        (run_dir / EVENTS_FILENAME).touch()
        return cls(run_dir, pipeline, run_id, clock=clock, cap_usd=cap_usd)

    @classmethod
    def resume(
        cls,
        run_dir: Path | str,
        clock: Clock | None = None,
        *,
        cap_usd: float = math.inf,
    ) -> RunContext:
        """Reopen an existing run: frozen plan + last terminal event per unit."""
        run_dir = Path(run_dir)
        plan_path = run_dir / PLAN_FILENAME
        events_path = run_dir / EVENTS_FILENAME
        if not run_dir.is_dir():
            raise ResumeConflict(f"run dir does not exist: {run_dir}")
        if not events_path.is_file():
            raise ResumeConflict(f"cannot resume {run_dir}: missing {EVENTS_FILENAME}")
        if not plan_path.is_file():
            raise ResumeConflict(f"cannot resume {run_dir}: plan was never frozen")

        events = list(read_events(events_path))
        run_id = events[0].run_id if events else run_dir.name
        pipeline, _, _ = run_dir.name.rpartition("-")  # best-effort; run_id is authoritative
        ctx = cls(run_dir, pipeline or run_dir.name, run_id, clock=clock, cap_usd=cap_usd)
        ctx._plan = json.loads(plan_path.read_text(encoding="utf-8"))
        ctx.completed_units = terminal_units(events)
        return ctx

    # -- plan ----------------------------------------------------------------

    @property
    def plan(self) -> dict[str, Any] | None:
        return self._plan

    @property
    def artifacts_dir(self) -> Path:
        return self.run_dir / ARTIFACTS_DIRNAME

    def freeze_plan(self, plan: dict[str, Any]) -> Path:
        """Write plan.json exactly once. A second call raises unless identical."""
        plan_path = self.run_dir / PLAN_FILENAME
        if plan_path.exists():
            existing = json.loads(plan_path.read_text(encoding="utf-8"))
            if _canonical(existing) != _canonical(plan):
                raise ResumeConflict(
                    f"plan already frozen at {plan_path} and the new plan differs; "
                    "a frozen plan is immutable for the life of the run"
                )
            self._plan = existing
            return plan_path
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        self._plan = plan
        self.emit(EventName.PLAN_FROZEN, stage="plan", payload={"path": plan_path.name})
        return plan_path

    # -- events + summary ----------------------------------------------------

    def emit(
        self,
        event: str | EventName,
        stage: str,
        *,
        unit: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> Event:
        return self._log.emit(event, stage, unit=unit, level=level, payload=payload)

    def events(self) -> list[Event]:
        return list(read_events(self.run_dir / EVENTS_FILENAME))

    def write_summary(self, summary: dict[str, Any]) -> Path:
        path = self.run_dir / SUMMARY_FILENAME
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
