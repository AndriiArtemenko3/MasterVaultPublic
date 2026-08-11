# ADR 0015: Managed generation publication, indexing, and activation

- Status: accepted for the v0.3 Milestone 4 SQLite activation slice
- Date: 2026-08-10

## Context

ADR 0014 lets an authoritative human decision adopt one reviewed governing
source and authorize only the approved downstream SourceNote revisions. The
decision is deliberately inert. MasterVault still needs to turn those exact
bytes into one complete knowledge generation, build a corresponding serving
index, advance active authority atomically, and recover when a process stops at
any boundary between those effects.

The filesystem, derived SQLite index, and change-control SQLite database cannot
share one transaction. Treating a published file or a workflow checkpoint as
authority would expose partial generations. Treating self-hashed receipt
models as sufficient proof would let fabricated evidence advance authority.
Overwriting the legacy vault or index would also make historical state and
recovery ambiguous.

## Decision

### Resolve a complete generation before effects

One pure `ResolvedManagedGenerationProjection` is rederived from the exact v2
decision, freshly reopened reviewed SourceNote inventory, governing-source
adoption, and accepted temporal constraints. It contains every historical and
current SourceNote with a stable logical path and exact bytes/hash identity.
Historical and successor entries may therefore share that logical path, while
paths are unique within the `CURRENT` serving subset. Only entries whose
resolved temporal state is `CURRENT` enter the serving index; historical
entries remain part of the complete generation projection. When an approved
successor is current, that reviewed replacement decision resolves its
open-ended predecessor as historical in this generation.

PR15 deliberately permits exactly one managed successor from the verified
generation-zero authority. The service, authoritative store, and effect
repository all reject a command whose expected authority is already managed,
before constructing a generation repository or writing an intent/effect. An
exact retry of the already-active first successor remains supported by its
immutable activation receipt. Carrying prior published successors into a
second operator change event requires a new merge/projection contract and is
deferred rather than silently dropping earlier managed changes.

The reviewed governing source is adopted at its existing canonical raw and
SourceNote locations. It is never copied, rerendered, or represented by a fake
publication event. A mixed decision publishes only approved downstream
successor SourceNotes. An adoption-only v2 decision therefore activates with
zero publication events. A fully rejected legacy v1 decision is a true no-op:
it creates no activation intent, generation repository, publication, index, or
authority successor.

### Isolate create-only generation effects

All managed effects live below an operator-selected, private, dedicated
generation repository that must be disjoint from canonical, staging,
inference, evaluator, legacy vault, index, and change-control roots. The
activation and serving services always protect the change-control database
file—thereby rejecting any candidate root that contains it—in addition to the
resolver-owned and operator-supplied roots.
Publication paths are content-bound and create-only below
`generations/<generation-id>/canonical/`. No-follow, inode, ownership, mode,
bounded-read, SHA-256, byte-count, file-sync, and directory-sync checks apply
on create and reopen. Conflicting retries fail closed; exact retries reopen the
same bytes.

The index is a separate complete SQLite database at
`generations/<generation-id>/index/mastervault.sqlite3`. It is built only from
the explicit closed SourceNote inventory—never by walking a directory—and any
skip, missing item, surplus document, record-coverage mismatch, FTS mismatch,
schema mismatch, or sidecar fails the build. A durable completion marker binds
the activation, manifest, projection, embedding identity, serving fingerprint,
and storage schema. A create-only canonical `READY.json` receipt is written
last under the repository lock and binds both the physical file hash/size and
a deterministic logical row fingerprint. Only that receipt makes the index
immutable and replayable. Without it, retry force-reprojects every deterministic
row and re-embeds every record into a freshly initialized schema before
sealing; the schema itself participates in the logical fingerprint. With
readiness present, retry reopens the exact receipt and never mints replacement
readiness. A conflicting marker or receipt fails closed. The complete database
is first built and verified in an isolated in-memory SQLite connection,
serialized, and written through an exact no-follow parent/file descriptor pair.
The final inode is owner-read-only (`0400`) before readiness. That same pinned
inode remains open across physical hashing and the create-only READY commit;
interrupted READY hard-link pairs are distinguished from committed receipts and
reconciled safely. Read-only SQLite opens through a feature-detected exact
descriptor URI, verifies the same connection's serialized bytes against the
pinned file and receipt, and retains both file and parent guards until backend
close. Directory component swaps therefore cannot redirect reset, publication,
verification, or serving to an unrelated path. Creating the dedicated
repository root fsyncs both the new directory and its parent entry.

### Keep SQLite as the sole effect authority

Migration `004_generation_publication_activation.sql` adds immutable activation
intents, publication events, index-readiness receipts, and activation receipts.
An intent owns one operation ID and exact decision. Publication and index
records are inert evidence until activation.

Repository-minted, process-local HMAC-sealed capabilities accompany every
effect record and the final compare-and-swap. Verification reopens the exact
publication bytes and index inside the store operation. Structurally valid or
self-hashed Pydantic receipts alone cannot authorize persistence or activation.
Capabilities are disposable and never become restart authority; durable rows
and freshly reopened repository bytes are.

After all exact effects are present, the store advances the active-generation
authority and inserts its immutable activation receipt in one
`BEGIN IMMEDIATE` transaction. The update compares the complete expected
authority pointer. Concurrent activators from one base therefore permit at
most one successor. Lost acknowledgement returns the exact existing receipt
only after reopening all effects and proving that receipt remains the active
authority.

Active-authority loading reconstructs a bounded chain from generation zero
through exact managed decisions and activation receipts. It does not trust the
denormalized active row in isolation. That read-side chain validation does not
expand PR15's write boundary beyond the single generation-zero successor.

### Reconcile synchronously and serve fail closed

The activation service is a deterministic synchronous reconciler. On each
invocation it reopens durable progress, completes missing create-only effects,
revalidates the reviewed decision and full projection, verifies every effect,
then attempts the authority CAS. Crashes before or after any file, receipt, or
CAS boundary converge on exact retry without deletion, rollback, or duplicate
effects. SQLite—not LangGraph state—owns progress and authority.

The serving opener resolves the active authority, exact command, projection,
publication set, and readiness receipt; reopens the physical and logical index;
opens SQLite with immutable read-only/query-only settings; then rereads active
authority before returning. Missing, corrupt, mismatched, or concurrently
superseded state fails closed.

PostgreSQL managed activation is rejected before repository construction or
any other effect. The existing PostgreSQL retrieval backend is not reinterpreted
as PR15 authority.

## Failure and compatibility properties

- Legacy canonical vault files and the legacy serving index are unchanged.
- Existing v1 review records remain readable; a rejected v1 manifest remains a
  no-op and is never upgraded implicitly.
- Existing v2 adoption-only activations produce an index and authority
  successor without inventing publication evidence.
- An index with `READY.json` is never rewritten on exact replay. A completion
  marker without `READY.json` is unsealed and is force-rebuilt from the exact
  reviewed inventory before readiness can be committed.
- A READY-sealed database is private owner-read-only, and serving retains exact
  descriptor guards for the life of its immutable SQLite connection.
- A fresh second operator activation is rejected before filesystem or authority
  effects; multi-event projection is not inferred from a prior serving index.
- Publication or index residue before CAS is inert and safe to retain.
- Authority cannot advance without exact publication coverage, one exact ready
  index, and a fresh repository-verified effects capability.
- Read-only serving rejects writes and refuses mismatched authority/index
  identities.

## Explicit non-goals

This slice does not execute or score targeted regression queries, emit the
final JSON/Markdown audit report, add public change-control CLI or keyless demo
commands, expand LangGraph, execute managed `EDIT` decisions, add PostgreSQL
managed-generation parity, implement retention/cleanup, change the public
README or package version, release, tag, deploy, add OCR/UI/background workers,
or support multiple operator change events. Those remain PR16 or later.
