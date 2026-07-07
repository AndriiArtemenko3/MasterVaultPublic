# src/mastervault/core — Run-scoping primitives shared by every pipeline

This folder holds the orchestration substrate that every pipeline builds on: a per-run working directory, an append-only event log, a spend ledger, and the typed error/exit-code vocabulary. It exists so that `ingest`, `ask`, and `lint` runs are all recorded, resumable, and cost-bounded through one shared mechanism instead of each pipeline reinventing state tracking. Nothing here knows about claims, retrieval, or LLMs; it only manages the lifecycle of a run.

## Files

| File | Responsibility |
|------|----------------|
| `runctx.py` | `RunContext` owns one run dir (`events.jsonl`, `plan.json`, `summary.json`, `artifacts/`). `create` starts a fresh run and mints a `<pipeline>-<date-time>[-n]` id; `resume` reopens one, loading the frozen plan and the last terminal event per unit. `freeze_plan` writes `plan.json` once and rejects a differing re-freeze. |
| `budget.py` | `BudgetLedger` tracks running USD spend against a hard cap under a lock. `check(next_estimate)` is the pre-flight gate that raises `BudgetExceeded`; `record` books an actual call; `snapshot` produces the `cost.json` payload aggregated by model. |
| `events.py` | Append-only JSONL event log. `EventName` is a closed `StrEnum`; `EventLog.emit` validates and flushes one line per event; `read_events` yields typed `Event` models; `terminal_units` folds a stream into the last terminal event per unit for resume. |
| `errors.py` | `MasterVaultError` subclasses (one per failure mode) plus `EXIT_CODES` and `exit_code_for`, the single mapping CLI entrypoints use to turn an exception into a process exit status. |
| `__init__.py` | Re-exports the public surface (`RunContext`, `BudgetLedger`, `EventName`, `EXIT_CODES`, the error classes, etc.) so callers import from `mastervault.core`. |

## How it fits

Nothing upstream produces input for this folder; it is the base layer. Pipeline drivers in [../pipelines](../pipelines) construct a `RunContext` at the start of a run, emit events as stages progress, and gate LLM calls through the ledger. CLI entrypoints in [../cli](../cli) resume runs, read the event log back for `runs` reporting, and translate a raised `MasterVaultError` into a process exit code via `exit_code_for`. Producers across [../ingest](../ingest) and [../review](../review) emit into the same log rather than keeping private state.

## Key concepts / entry points

- `RunContext.create` / `RunContext.resume` — start a fresh run dir vs. reopen one and rehydrate frozen plan + settled units (`runctx.py:66`, `runctx.py:95`).
- `RunContext.freeze_plan` — write `plan.json` exactly once; a second call with a different plan raises `ResumeConflict`, keeping the plan immutable for the run's life (`runctx.py:132`).
- `terminal_units` — fold events into `{unit: last terminal event}`; later events win, so a unit that failed then completed on resume reads as completed (`events.py:127`).
- `EventName` + `_coerce_name` — the closed event vocabulary; an unrecognized name raises `UnknownEventError` on both write and read instead of minting a new type (`events.py:30`, `events.py:68`).
- `BudgetLedger.check` vs. `record` — estimate-before / actual-after cost gating against a hard cap (`budget.py:51`, `budget.py:35`).
- `EXIT_CODES` / `exit_code_for` — map an outcome or exception to a stable process exit status (`errors.py:48`, `errors.py:63`).
