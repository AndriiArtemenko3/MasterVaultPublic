# src/mastervault/pipelines — Three end-to-end run orchestrators

This folder holds the three top-level runs a user triggers: `ingest`, `ask`, and `lint`. Each function composes the lower-level building blocks (contracts, storage, retrieval, the review queue) into one traceable run driven by a `RunContext`, and returns a plain dataclass outcome plus an exit code. The orchestration logic lives here; the per-step primitives live in sibling packages. Every run writes an event log, per-round artifacts, and a JSON summary under `runs_dir`.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Re-exports the three entry points and their outcome dataclasses (`run_ingest`/`IngestOutcome`, `run_ask`/`AskOutcome`, `run_lint`/`LintOutcome`). |
| `ingest.py` | Six-stage raw→routed pipeline: PLAN (enumerate + provenance-hash dedupe + freeze) → EXTRACT+WRITE per unit → INDEX (one `sync_vault`) → CONCEPT MATCH + CORPUS CHECK + ROUTE over every completed unit's claims → SUMMARY. Handles resume, budget-skip retry, wikilink auto-apply, and tiered review enqueue. |
| `ask.py` | Agentic multi-round retrieval under a sufficiency judge, then grounded LLM synthesis behind a citation gate, with a deterministic extractive fallback. Enforces three mechanical stop guards the judge never controls. |
| `lint.py` | Mechanical vault-health scan (frontmatter validity, broken `affects`, duplicate claim ids, orphan wikis, drifted review items) plus an optional semantic contradiction pass that double-confirms every flag before queuing it. |

## How it fits

Ingest reads raw files via [../ingest](../ingest) (`discover_units`, `extract_claims`, `match_claim`, `adjudicate`, the wiki drafters and linker), writes source notes through [../vaultfs](../vaultfs), indexes them with [../sync](../sync), and pushes proposals into [../review](../review). Ask and lint consume what ingest produced: ask fuses channels through [../retrieval](../retrieval) (`hybrid_search`, `mmr_select_texts`) and both call LLM steps defined in [../contracts](../contracts) against providers in [../providers](../providers). All three are invoked by [../cli](../cli), which renders the returned outcome dataclass and propagates its exit code; state, budgeting, and event emission come from [../core](../core) via `RunContext`.

## Key concepts / entry points

- `run_ingest` (`ingest.py:394`) — the full raw→routed run; note the INDEX step is a single `sync_vault` call (`ingest.py:573`) and the ROUTE phase re-runs idempotently over every completed unit rather than tracking its own resume state.
- `_route_claim` (`ingest.py:210`) — dispatches one claim by match kind: auto-insert a wikilink, or enqueue a tier-2 cross-ref/extend, a tier-3 contradiction, or tally it toward a new concept (drafted only when ≥2 claims support the same label, see `_draft_new_concepts`, `ingest.py:334`).
- `run_ask` (`ask.py:160`) — the round loop; stops on the judge's `sufficient` verdict, the `max_rounds` cap, the novelty floor (a round adding zero new `record_id`s, `ask.py:239`), the followup-dedup pass (`_dedupe_followups`, `ask.py:91`), or a judge hard-fail treated as sufficient.
- `_apply_citation_gate` (`ask.py:120`) — strips any `[<record-id>]` not in the evidence pool; zero surviving citations forces the extractive fallback (`_extractive_answer`, `ask.py:137`), which is also the path a mock LLM naturally lands on.
- `run_lint` (`lint.py:214`) — reads the vault tree directly (not the index) so it stays trustworthy against a stale one; `exit_code` is 1 when broken `affects` or duplicate ids exist.
- `_confirm_contradiction` (`lint.py:187`) — the double-confirm gate: a `contradicts` verdict is only queued if a second dispatch with statements swapped also returns `contradicts`; disagreement downgrades to `unclear`, and `lint-seen.json` stops re-judging the same pair across runs.
