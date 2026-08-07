# ADR 0005: Dedicated transactional SQLite change-control aggregate

- Status: accepted for the v0.3 Milestone 4 persistence seam
- Date: 2026-08-07

## Context

The temporal foundation embeds document and claim bindings throughout relation,
dependency, replacement, and temporal models. Updating one binding in isolation
could therefore leave a graph that is individually parseable but collectively
false. Reviewed temporal state is also durable state, while `index.db` is a
derived search index that reset and drop workflows may rebuild or delete.

## Decision

Persist one strict `ChangeControlAggregate` through a dedicated synchronous
SQLite store at `<workspace>/change_control/state.sqlite3`. The aggregate owns
document roots, claim roots, the relation graph, dependencies, document
replacements, and temporal constraints. Its validator requires every embedded
binding and target to resolve to the exact registry root and reconstructs the
validated temporal wrapper. The persistence boundary exposes no standalone
entity writes.

The store has its own identity, schema version, packaged SQLite migrations, and
checksum ledger. It refuses unidentified files, unknown objects, incomplete or
changed history, future versions, broken foreign keys, malformed value-object
JSON, noncanonical ordering, invalid stable IDs, cross-model inconsistencies,
and aggregate-digest mismatches. Existing v0.2 workspaces are unaffected: their
`index.db` is neither opened nor migrated, and the new database is created only
on first change-control initialization.

Writes revalidate the caller's aggregate from JSON before locking, then use
`BEGIN IMMEDIATE`. A monotonic revision supplies compare-and-swap semantics.
Operation receipts make exact retries idempotent; reusing a key for different
inputs fails. A same-state write at the current revision records a receipt but
does not advance the revision. All normalized child replacement, the head
revision/digest, and the receipt commit or roll back together. Reads capture an
explicit transaction snapshot and validate identity and migration history
inside that snapshot.

Each receipt hashes every immutable field, including its canonical ASCII-safe
operation ID, transition shape, and UTC commit timestamp. All receipts are
validated against their live aggregate heads before any replay is trusted, so
a historical operation can safely acknowledge a lost response after later
revisions without letting corrupted metadata bypass CAS. The schema identity
also stores a canonical fingerprint of every persistent `sqlite_master` object;
migration transactions alone advance that fingerprint. Added, removed, or
altered columns, constraints, foreign keys, and indexes therefore fail closed.
SQLite busy/locked result codes are surfaced separately as
`ChangeControlBusyError`; integrity, corruption, and CAS failures retain their
own meanings.

Migration `001` evolved only before this persistence seam was accepted, while
no intermediate database shape had been committed, released, or exposed to a
user. This ADR's acceptance freezes that migration and its checksum. It makes
no compatibility claim for those unshipped intermediate shapes; every later
schema change, beginning with review persistence, must be an additive ordered
migration numbered `002` or higher rather than a rewrite of `001`.

Within the v1 aggregate schema, evidence value objects alone use versioned
canonical JSON because schema-v2
bounding boxes must retain their exact canonical representation. The aggregate
itself has no JSON shadow copy; normalized rows are the only stored state and a
canonical SHA-256 detects otherwise valid row tampering.

## Consequences and limits

- Binding correction requires rebuilding and atomically replacing every
  dependent collection.
- Multiple aggregate IDs are supported in one file; `workspace` is the default
  logical ID, not a SQL singleton constant.
- The implementation favors full replacement and fail-closed validation over
  partial-write performance.
- Proposed or rejected temporal rows may retain stale basis IDs, matching the
  foundation; accepted bases must exist in the current exact graph.
- Authoritative review records and their immutable audit snapshots are added by
  ADR 0006 and migration `002`; they remain companion records, not aggregate
  fields or the live aggregate representation.
- PostgreSQL persistence, candidate analysis, LangGraph workflow/checkpoints,
  canonical-file apply, and reindex coordination remain out of scope.
