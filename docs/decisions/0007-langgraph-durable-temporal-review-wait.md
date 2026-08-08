# ADR 0007: LangGraph durable temporal-review wait and reconciliation

- Status: accepted for the v0.3 Milestone 4 review seam
- Date: 2026-08-07

## Context

ADR 0006 makes `<workspace>/change_control/state.sqlite3` the sole authority
for temporal human-review requests, lifecycle, and immutable decisions. A
long-running process still needs a durable place to pause while an already
created request is open and to continue after an external reviewer commits a
decision. That execution cursor must not become another decision store.

LangGraph 1.2.9 with `langgraph-checkpoint-sqlite` 3.1.0 supplies synchronous
interrupt and SQLite checkpoint primitives. Its checkpoint format includes
framework-owned scheduling channels and is not a domain audit format. A
checkpoint write can also fail after the authoritative decision transaction
has already committed. Treating checkpoint contents or an interrupt-resume
payload as approval would therefore create a second, weaker authority.

## Decision

Add a local synchronous `TemporalReviewWorkflow` library service. It accepts
one fully validated, already-created `HumanReviewRequest` and binds it to the
deterministic versioned thread ID
`mastervault.temporal-review-wait.v1/<request-id>`. It cannot create or decide
a request, apply a mutation, or use the Markdown `ReviewQueue`.

The graph topology is fixed:

```text
START -> reconcile_authority -> await_wake_signal -> reconcile_authority
                           \-> END (decided or stale)
```

`reconcile_authority` opens a short-lived `SqliteChangeControlStore`, reads
`get_review_request()`, verifies the complete immutable request binding, then
closes the connection. An open request routes to `await_wake_signal`; decided
or stale authority routes to `END`. The wait node performs exactly one
side-effect-free `interrupt()` and always returns to reconciliation. Its
resume value is ignored. Public `resume()` accepts no payload, outcome,
boolean, or `Command`; it emits one fixed internal wake signal. Every wake
rereads authority before any terminal status is reported.

Graph state is a versioned `TypedDict` containing JSON primitives only:
immutable workflow/request bindings plus the last observed authoritative
lifecycle and, for a decision, its payload SHA and revision. Pydantic models,
enums, paths, dates, tuples, aggregate snapshots, and decision objects never
enter checkpoint state. Every restored state must have the exact supported
field set, primitive types, schema version, and immutable identity. Unknown
channels such as a forged `approved` field fail closed. The current graph
compatibility and state schema version are both v1; incompatible checkpoints
require operator recovery rather than implicit migration.

Checkpoints live only at sibling
`<workspace>/change_control/checkpoints.sqlite3`, using the default empty
checkpoint namespace. The service resolves both paths (including symlinks and
existing same-file aliases) and refuses an identical target before creating
checkpoint tables. It owns an explicit SQLite connection and constructs
`SqliteSaver` with
`JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None,
allowed_msgpack_modules=None)`. `SqliteSaver.from_conn_string()` is not used
because it cannot inject that serializer. The service closes its connection
through its context-manager boundary.

Lifecycle and orchestration are reported separately. Authoritative lifecycle
is `open`, `decided`, or `stale`. Orchestration phase is `not-started`,
`waiting`, `reconciliation-pending`, `complete`, or `recovery-required`, with
checkpoint health `absent`, `healthy`, or `corrupt`. Status reads authority
first. Checkpoint corruption or tampering can reduce availability only; it
cannot change or synthesize authoritative lifecycle or decision data, and the
service never auto-deletes a corrupt checkpoint.

`start()` is idempotent and never reinjects initial state into an existing
thread. A request already decided or stale may start directly at a terminal
checkpoint. Waking an open request simply interrupts again. Waking a terminal
authority reconciles the checkpoint. A terminal graph is a no-op. `retry()`
never starts a missing thread and never calls the authoritative decision
method; it may retry a failed graph task or reconcile a terminal authority
whose checkpoint still records a wait.

The authoritative decision transaction and the later checkpoint update form a
saga/reconciliation window, not one cross-database transaction. If checkpoint
`put`, `put_writes`, or response delivery fails after a decision commits, the
decision, aggregate revision, receipt, and audit rows remain committed exactly
once. Reopening and retrying rereads that exact decision and advances only the
disposable cursor. No compensating authoritative write occurs.

## Boundaries and consequences

- `state.sqlite3` remains the only temporal-review authority and retains its
  attested schema. LangGraph creates only `checkpoints` and `writes` in the
  sibling checkpoint database.
- Missing, busy, corrupt, or incompatible authority fails closed. The read
  path checks that the file exists before constructing the normal store, so it
  cannot create an empty authority database accidentally.
- Strict deserialization blocks pickle fallback, but restored-state validation
  remains mandatory because unknown extension encodings may degrade to plain
  primitives in the pinned serializer.
- A process-local re-entrant lock serializes operations on the service-owned
  connection. This is explicitly not a distributed lock, multi-process
  coordinator, HA design, or production scaling claim.
- Checkpoint recovery is an availability concern. Operators may replace a
  corrupt disposable checkpoint only through a future explicit recovery
  policy; this seam does not delete it automatically.
- `mvault demo reset` already removes the complete `change_control/` directory,
  covering both authoritative state and disposable checkpoints for a promised
  pristine demo reset.
- CLI/UI integration, background polling, notifications, multi-process
  workers, subgraphs, custom checkpoint namespaces, PostgreSQL change-control,
  and production deployment are out of scope.
