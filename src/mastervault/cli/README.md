# src/mastervault/cli — Typer command modules for the `mvault` CLI

This folder is the presentation layer of MasterVault. Each module defines a small Typer sub-app, parses flags, loads settings and providers, calls one function in a pipeline / retrieval / sync module, and renders the result as a Rich table or plain lines. No business logic lives here: commands stay thin so the same behavior is reachable from tests without a shell. `app.py` assembles every sub-app into one root command tree.

## Files

| File | Responsibility |
|------|----------------|
| `app.py` | Builds the `mvault` root Typer app and merges the sub-apps. Admin, query, ingest, ask, lint, and eval commands are flattened to the top level with `app.registered_commands +=`; review, runs, and demo nest under their name via `add_typer`. Each pipeline import is wrapped in `try/except ImportError` so the root loads even when a module is absent. Defines `version` and the `main()` console-script entry point. |
| `admin.py` | Index administration on `admin_app`, registered at top level: `init` (schema + workspace dirs, idempotent), `sync` (changed files only, `--full` to re-upsert all), `status` (backend stats table), `reset` (wipe then full sync), `drop` (delete the SQLite file or drop the Postgres tables). `_init_backend` turns a `SchemaMismatchError` into a clean exit code 1. |
| `query.py` | Read-only query commands on `query_app`: `search` (hybrid search across claims/chunks/wiki, `--rerank`, `--json` with channel provenance, timings, and generation metadata), `claims` (lexical-only with `--affects` / `--confidence` filters), and `wiki` (list or show). All accept `--generation auto\|legacy\|active\|mgeneration:<sha256>`, print the serving generation, and resolve managed authority fail-closed. |
| `ask.py` | `mvault ask`: agentic multi-round grounded retrieval plus synthesis against the same `--generation` contract. It prints the cited answer, confidence, gaps, sources, and serving generation, or emits a JSON envelope with path-free generation metadata. Managed asks disable run persistence; unmanaged v0.2 asks retain it. |
| `ingest.py` | `mvault ingest`: raw file/directory into vault source notes via `run_ingest`. In addition to the existing run controls, `--pdf-parser pypdf\|docling` overrides the frozen document parser selection. |
| `document.py` | `mvault document doctor --parser pypdf\|docling`: read-only package/artifact readiness diagnostic. It never parses, downloads, or falls back. |
| `lint.py` | `mvault lint`: mechanical vault-health checks plus an optional semantic (LLM) contradiction pass via `run_lint`. Flags: `--mechanical-only`, `--budget`, `--no-queue` (report without writing to the review queue), `--json`. Prints per-check counts and the suggested next action. |
| `review.py` | `mvault review` sub-app for triaging the human-in-the-loop queue: `list`, `show`, `approve`, `reject`, `approve-pattern`, `spot-check`. Enforces tier gates: tier-3 items require `--yes`, and no batch verb touches a group containing tier-3 items. `_resolve` matches an item by filename-stem prefix. |
| `runs.py` | `mvault runs` sub-app: default callback lists run directories (pipeline, start time, status, cost); `runs show <run-id>` prints the `summary.json`, failed units, and the budget snapshot at exhaustion. Reads `events.jsonl` through `read_events` and maps exit codes to status strings. |
| `demo.py` | `mvault demo` sub-app over the shipped Larkstead dataset: `load` (copy `processed/` into the workspace and import the precomputed embeddings sidecar instead of recomputing vectors), `status` (compare live counts against counts derived from the shipped files), `reset` (restore pristine state), `delete` (remove the workspace tree). |
| `evals.py` | `mvault eval`: runs the golden query set through `hybrid_search` under channel-ablation configs, prints recall@5/10, nDCG@10, and MRR per config with a per-class breakdown, and optionally diffs against a frozen `baseline.json`. Exits 1 if the golden set fails to resolve or a metric regressed beyond `--tolerance`. |
| `evidence.py` | `mvault evidence show <claim-id>`: verifies the immutable PDF and parsed JSON, re-resolves persisted spans, and prints page/block/quote evidence or a machine-readable `--json` bundle. |
| `__init__.py` | Empty package marker. |

## How it fits

These modules sit at the edge of the system and depend downward. They read configuration through [../config.py](../config.py), instantiate embedders / LLMs / rerankers through [../providers](../providers), and resolve ordinary query backends through the change-control application boundary. The actual work is delegated: `ask`, `ingest`, and `lint` call into [../pipelines](../pipelines); `search` calls `hybrid_search` in [../retrieval](../retrieval); `review` uses [../review](../review); `demo` uses [../sync](../sync); `eval` uses [../evals](../evals). Output is either a Rich table, plain `typer.echo` lines, or a `--json` payload consumed by scripts and the eval harness.

## Key concepts / entry points

- `app` — the root Typer instance built in `app.py:46`; `main()` at `app.py:92` is the console-script target.
- Two registration styles: `app.registered_commands += …` flattens a sub-app's commands to the top level (`app.py:67`), while `add_typer` keeps them namespaced under a word like `review` / `runs` / `demo` (`app.py:71`).
- Optional-import guards: every pipeline sub-app is imported inside `try/except ImportError` (`app.py:11`) so the CLI still runs if a module was not shipped.
- `mvault` and `vault` are both console scripts mapped to `mastervault.cli.app:main` (see the repo `pyproject.toml` `[project.scripts]`); they are the same CLI under two names.
- Query command shape: `load_settings()` → `ChangeControlApplication.resolve_query_generation()` → verify the embedding identity → query while the backend/evidence guards remain live → render generation-labelled output. Unmanaged `auto` retains `get_backend(settings)` compatibility; managed resolution is SQLite-only and query-only.
- `EXIT_CODES` from [../core](../core) drives status: pipelines return an `exit_code` that commands re-raise, and `runs.py:28` maps those codes back to human status labels.
