# src/mastervault/review — Human-in-the-loop change queue

This folder is the trust boundary that keeps ingested evidence from silently overwriting wiki concepts and other vault files. Producers (ingest routing, lint) do not edit targets directly: they enqueue a proposed change as one markdown file per item, and a human approves it before `apply` touches the target. Every apply re-checks that the target has not drifted since the proposal was written, so a stale or conflicting change is flagged for review instead of being applied.

## Files

| File | Responsibility |
|------|----------------|
| `queue.py` | `ReviewQueue`, the file-backed queue. One markdown file per item (frontmatter + `## Rationale` / `## Proposal` / `## Resolution`). Handles `enqueue` with content dedupe, `archive` (resolve + move to `archive/`), `mark_conflict` (flip status in place), and read-side `list_items` / `load`. Includes the fence-aware section splitter that lets a 4-backtick proposal fence wrap 3-backtick blocks. |
| `apply.py` | `apply()` — the guarded write path. Re-reads the target, gates on `base_hash` drift, then applies either a `replace` payload (`full_file` / `replace_section` / `append_section`) or a strict unified `diff`. Bumps the target's `updated:` field, archives the item as `applied`, and calls the reindex hook. `apply_unified_diff` is the no-fuzz patcher: any hunk mismatch raises `PatchError`. |
| `__init__.py` | Public surface: re-exports `ReviewQueue`, `apply`, `apply_unified_diff`, `dedupe_key`, and the result types. |

## How it fits

Input comes from the route phase of [../pipelines](../pipelines) (`ingest.py` `_enqueue`), which turns tier-2/tier-3 contradictions and new-concept stubs into `ReviewItem` proposals and calls `ReviewQueue.enqueue`. The queue itself is plain markdown under the workspace `review/pending` and `review/archive` directories, readable and editable by hand. Output is consumed by the `mvault review` command family in [../cli](../cli) (`review.py`: list, approve/apply, reject, batch-apply by pattern), which drives `apply()` and the queue's resolve methods. On a successful apply the reindex hook re-runs indexing so [../retrieval](../retrieval) sees the changed target.

## Key concepts / entry points

- `ReviewQueue.enqueue` — writes one item file; dedupes against PENDING twins by `dedupe_key(producer, target, change_type, proposal)` and returns `None` on a match. `queue.py:142`
- `dedupe_key` / content dedupe — sha256[:16] over producer, target, change_type, and proposal text; archived items never block a re-enqueue. `queue.py:49`
- `_split_sections` — fence-aware parser that treats a `## ` line inside an open backtick fence as proposal content, not a section boundary. `queue.py:64`
- `apply()` — the safety-ordered write: missing-target and `base_hash` drift both short-circuit to `ConflictResult` before any edit. `apply.py:172`, drift gate at `apply.py:198`
- `apply_unified_diff` — strict positional patcher; `consume` verifies each context/removed line exactly and raises `PatchError` on mismatch. `apply.py:64`
- `_apply_replace` — the three `replace` modes; `replace_section` uses a function replacement so proposal text is never re-read as regex escapes. `apply.py:138`
