# src/mastervault/cli — Typer command modules for the `mvault` CLI

This folder is the presentation layer of MasterVault. Each module defines a small Typer sub-app, parses flags, loads settings and providers, calls one function in a pipeline / retrieval / sync module, and renders the result as a Rich table or plain lines. No business logic lives here: commands stay thin so the same behavior is reachable from tests without a shell. `app.py` assembles every sub-app into one root command tree.

## Files

| File | Responsibility |
|------|----------------|
| `app.py` | Builds the `mvault` root Typer app and merges the sub-apps. Admin, query, ingest, ask, lint, and eval commands are flattened to the top level with `app.registered_commands +=`; review, runs, and demo nest under their name via `add_typer`. Each pipeline import is wrapped in `try/except ImportError` so the root loads even when a module is absent. Defines `version` and the `main()` console-script entry point. |
| `admin.py` | Index administration on `admin_app`, registered at top level: `init` (schema + workspace dirs, idempotent), `sync` (changed files only, `--full` to re-upsert all), `status` (backend stats table), `reset` (wipe then full sync), `drop` (delete the SQLite file or drop the Postgres tables). `_init_backend` turns a `SchemaMismatchError` into a clean exit code 1. |
| `query.py` | Read-only query commands on `query_app`: `search` (hybrid search across claims/chunks/wiki, `--rerank`, `--json` with channel provenance and timings), `claims` (lexical-only over the claims layer with `--affects` / `--confidence` filters), `wiki` (list entries per domain, or `wiki show <slug>` for one entry with its aliases). |
| `ask.py` | `mvault ask`: agentic multi-round grounded retrieval plus synthesis. Wires backend, embedder, LLM, and reranker into `run_ask`, then prints the cited answer, confidence, gaps, and sources, or the zero-evidence fallback with nearest wiki titles. Propagates the outcome's `exit_code`. |
| `ingest.py` | `mvault ingest`: raw file/directory into vault source notes via `run_ingest`. Validates `--domain` against the `Domain` enum, supports `--dry-run` (plan + cost estimate, writes nothing), `--resume`, `--budget`, `--auto-approve`, `--fail-fast`. Renders the ingest report and points to `mvault review list` when items were queued. |
| `lint.py` | `mvault lint`: mechanical vault-health checks plus an optional semantic (LLM) contradiction pass via `run_lint`. Flags: `--mechanical-only`, `--budget`, `--no-queue` (report without writing to the review queue), `--json`. Prints per-check counts and the suggested next action. |
| `review.py` | `mvault review` sub-app for triaging the human-in-the-loop queue: `list`, `show`, `approve`, `reject`, `approve-pattern`, `spot-check`. Enforces tier gates: tier-3 items require `--yes`, and no batch verb touches a group containing tier-3 items. `_resolve` matches an item by filename-stem prefix. |
| `runs.py` | `mvault runs` sub-app: default callback lists run directories (pipeline, start time, status, cost); `runs show <run-id>` prints the `summary.json`, failed units, and the budget snapshot at exhaustion. Reads `events.jsonl` through `read_events` and maps exit codes to status strings. |
| `demo.py` | `mvault demo` sub-app over the shipped Larkstead dataset: `load` (copy `processed/` into the workspace and import the precomputed embeddings sidecar instead of recomputing vectors), `status` (compare live counts against counts derived from the shipped files), `reset` (restore pristine state), `delete` (remove the workspace tree). |
| `evals.py` | `mvault eval`: runs the golden query set through `hybrid_search` under channel-ablation configs, prints recall@5/10, nDCG@10, and MRR per config with a per-class breakdown, and optionally diffs against a frozen `baseline.json`. Exits 1 if the golden set fails to resolve or a metric regressed beyond `--tolerance`. |
| `__init__.py` | Empty package marker. |

## How it fits

These modules sit at the edge of the system and depend downward. They read configuration through [../config](../config), instantiate embedders / LLMs / rerankers through [../providers](../providers), and open a storage backend through [../storage](../storage). The actual work is delegated: `ask`, `ingest`, and `lint` call into [../pipelines](../pipelines); `search` calls `hybrid_search` in [../retrieval](../retrieval); `review` uses [../review](../review); `demo` uses [../sync](../sync); `eval` uses [../evals](../evals). Output is either a Rich table, plain `typer.echo` lines, or a `--json` payload consumed by scripts and the eval harness.

## Key concepts / entry points

- `app` — the root Typer instance built in `app.py:46`; `main()` at `app.py:92` is the console-script target.
- Two registration styles: `app.registered_commands += …` flattens a sub-app's commands to the top level (`app.py:67`), while `add_typer` keeps them namespaced under a word like `review` / `runs` / `demo` (`app.py:71`).
- Optional-import guards: every pipeline sub-app is imported inside `try/except ImportError` (`app.py:11`) so the CLI still runs if a module was not shipped.
- `mvault` and `vault` are both console scripts mapped to `mastervault.cli.app:main` (see the repo `pyproject.toml` `[project.scripts]`); they are the same CLI under two names.
- Common command shape: `load_settings()` → `get_backend(settings)` and providers → call one pipeline/retrieval function inside `try/finally: backend.close()` → render → `raise typer.Exit(code)`. `admin.sync` at `admin.py:73` and `query.search` at `query.py:36` are representative.
- `EXIT_CODES` from [../core](../core) drives status: pipelines return an `exit_code` that commands re-raise, and `runs.py:28` maps those codes back to human status labels.
