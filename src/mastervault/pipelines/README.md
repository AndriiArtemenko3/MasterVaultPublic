# src/mastervault/pipelines — Three end-to-end run orchestrators

This folder holds the three top-level runs a user triggers: `ingest`, `ask`, and `lint`. Each function composes the lower-level building blocks (contracts, storage, retrieval, the review queue) and returns a plain dataclass outcome plus an exit code. Ingest, lint, and unmanaged v0.2 ask use a traceable `RunContext`; generation-aware managed ask deliberately uses a query-only in-memory ledger and writes no run artifacts. The orchestration logic lives here; the per-step primitives live in sibling packages.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Re-exports the three entry points and their outcome dataclasses (`run_ingest`/`IngestOutcome`, `run_ask`/`AskOutcome`, `run_lint`/`LintOutcome`). |
| `ingest.py` | Raw→routed pipeline. PDF plans freeze byte, parser/core, schema, normalization and model identities; each PDF parses once per invocation, resume revalidates drift, and assets/IR/notes publish only after extraction and evidence validation. The routing/index lifecycle is otherwise unchanged. |
| `ask.py` | Agentic multi-round retrieval under a sufficiency judge, then grounded LLM synthesis behind a citation gate, with a deterministic extractive fallback. Enforces three mechanical stop guards the judge never controls, accepts per-logical-path evidence workspaces for managed generations, and can disable run persistence for the managed query-only CLI path. |
| `lint.py` | Mechanical vault-health scan (frontmatter validity, broken `affects`, duplicate claim ids, orphan wikis, drifted review items) plus an optional semantic contradiction pass that double-confirms every flag before queuing it. |

## How it fits

Ingest reads raw files via [../ingest](../ingest) (`discover_units`, `extract_claims`, `match_claim`, `adjudicate`, the wiki drafters and linker), writes source notes through [../vaultfs](../vaultfs), indexes them with [../sync](../sync), and pushes proposals into [../review](../review). Ask and lint consume what ingest produced: ask fuses channels through [../retrieval](../retrieval) (`hybrid_search`, `mmr_select_texts`) and both call LLM steps defined in [../contracts](../contracts) against providers in [../providers](../providers). All three are invoked by [../cli](../cli), which renders the returned outcome dataclass and propagates its exit code; state, budgeting, and event emission come from [../core](../core) via `RunContext`.

## Key concepts / entry points

- `run_ingest` — the full raw→routed run. The initial sync supplies the index used for concept matching; a final sync captures route-time source changes. The ROUTE phase re-runs idempotently over every completed unit rather than tracking its own resume state.
- `_route_claim` (`ingest.py:210`) — dispatches one claim by match kind: auto-insert a wikilink, or enqueue a tier-2 cross-ref/extend, a tier-3 contradiction, or tally it toward a new concept (drafted only when ≥2 claims support the same label, see `_draft_new_concepts`, `ingest.py:334`).
- `run_ask` — the round loop; stops on the judge's `sufficient` verdict, the `max_rounds` cap, the novelty floor (a round adding zero new `record_id`s), the followup-dedup pass, or a judge hard-fail treated as sufficient. `persist_run=False` keeps its budget/events/artifacts in memory for managed read-only queries; the default `True` preserves v0.2 run records.
- `_apply_citation_gate` (`ask.py:120`) — strips any `[<record-id>]` not in the evidence pool; zero surviving citations forces the extractive fallback (`_extractive_answer`, `ask.py:137`), which is also the path a mock LLM naturally lands on.
- `run_lint` (`lint.py:214`) — reads the vault tree directly (not the index) so it stays trustworthy against a stale one; `exit_code` is 1 when broken `affects` or duplicate ids exist.
- `_confirm_contradiction` (`lint.py:187`) — the double-confirm gate: a `contradicts` verdict is only queued if a second dispatch with statements swapped also returns `contradicts`; disagreement downgrades to `unclear`, and `lint-seen.json` stops re-judging the same pair across runs.
