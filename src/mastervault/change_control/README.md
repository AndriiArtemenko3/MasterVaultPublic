# `mastervault.change_control`

Runtime-only temporal contracts for MasterVault's knowledge-change workflow.

- `models.py` defines document versions, versioned claim revisions, canonical
  claim pairs, relation/dependency assessments, explicit reviewed document
  replacements, graph-validated temporal constraints, derived projections,
  graph integrity, and full SHA-256 IDs.
- `store.py` owns the dedicated, transactional SQLite aggregate store at the
  caller-selected change-control path. It never writes the rebuildable search
  index database.
- `review.py` defines strict request, canonical subject-snapshot, per-subject
  outcome, immutable decision, lifecycle, view, and receipt contracts for
  authoritative temporal human review.
- `workflow.py` provides the synchronous LangGraph wait/reconciliation seam
  for one already-created review request. Its sibling SQLite checkpoints are
  disposable cursors and can never authorize a review outcome.
- `discovery.py` purely enumerates unassessed changed-to-incumbent claim pairs
  and ranks current downstream attention candidates from canonical relation,
  dependency, and temporal facts in the supplied validated snapshot. Its
  outputs are advisory projections—not classifications or impact verdicts.
- `seed.py` strictly loads the SL2 pre-change source inventory, verifies one
  raw/note byte snapshot per manifest entry, and materializes a disposable
  vault without touching the shipped current-state corpus.

Identity and mutable binding are deliberately separate:

- a document-version ID hashes the versioned namespace, normalized document
  family, and version label—not its path, bytes, dates, authority, or role;
- the document and claim registries collapse exact replay and reject every
  conflicting embedded binding; standalone binding mutation is deliberately
  deferred until one persistence aggregate can update all embedded snapshots
  atomically;
- a claim identity hashes its document-version ID plus source-local claim key;
- a claim-revision ID additionally hashes normalized statement, scopes, and
  source-declared temporal qualifiers, but not evidence paths or confidence;
- a pair ID hashes only its order-independent endpoints. `shared_scopes` is the
  endpoint intersection and cannot be supplied by a caller; relation-graph
  snapshots use canonical pair-ID order regardless of input order.

Temporal dates are half-open: `declared_effective_from` is inclusive and
`declared_effective_to` is exclusive. A claim-level `SUPERSEDES` assessment can
close only the older claim revision. Closing an entire document version
requires a separate, accepted `DocumentReplacementAssessment` for two distinct
versions in the same family. Before resolution, every accepted constraint is
validated against the supplied current relation graph or document-replacement
set, including its exact older endpoint and bound. Proposed or rejected
constraints do not change currentness. Accepted constraints affect a resolver
projection only; they never rewrite source-declared dates or stable IDs.
Conflicting declared/inferred or accepted bounds resolve to `unresolved`.

The direction of a dependency edge is
`downstream document --DEPENDS_ON--> upstream claim`. Document-attention
discovery traverses those edges in reverse after an upstream change. One semantic
dependency retains all exact downstream spans and all downstream claim
revisions that express it; each binding must name the same canonical-note path
and full SHA-256. PDF-backed spans retain the existing page/block/cell evidence
objects unchanged.

Claim and span resolvers accept only a sealed `VerifiedDocumentContext`
returned by a document-ID lookup on a sealed `VerifiedPrechangeSeedManifest`.
The verifier binds the exact manifest byte snapshot and original entry, raw
path and full SHA-256, processed path and full SHA-256, canonical
`SourceNote`, unique local claim IDs, exact provenance, and the legacy
provenance hash. Body offsets are file-relative and resolved from that single
captured note snapshot; frontmatter is not valid body evidence. This Markdown
context is the contract pattern for a later PDF context that will additionally
verify asset and parsed-document lineage.

Raw sources must physically resolve below the real raw-source root and notes
below the real processed-note root. Lexical, case-insensitive, and symlink
routes into evaluator-gold data fail closed.

Materialization targets must be outside the repository, including through
symlink routes and case-insensitive filesystem aliases. Publication uses a
staging-directory rename and an inode-checked exclusive lock among cooperating
materializers. Pristine reuse verifies the exact expected directory and regular
file tree, rejecting empty-directory, symlink, and special-node additions
before reading file contents. This is not a claim of exclusion against
unrelated processes that ignore or replace the lock.

This package must not import `mastervault.evals` or read evaluator-gold data.
The runtime manifest contains only source selection and source-derived
metadata. Expected relationships, impacted documents, patches, review
decisions, and benchmark metrics stay evaluator-only.

The persisted boundary is one strict `ChangeControlAggregate`: document and
claim roots, relation graph, dependencies, document replacements, and temporal
constraints move through one revisioned compare-and-swap transaction. The
store rehydrates canonical domain objects and revalidates accepted temporal
bases on every load. Operation receipts carry their own canonical SHA-256 and
are all revalidated before replay; operation IDs use a restricted ASCII-safe
grammar. A reused key with different inputs and stale revisions fail closed.
The store also attests the live persistent SQLite schema against a fingerprint
updated only by its migration transaction. Its private schema and checksummed
migration ledger live under `migrations/sqlite/`. The default configuration
path is `<workspace>/change_control/state.sqlite3`, separate from the
disposable `<workspace>/index.db`. Lock timeouts surface as a typed, retryable
`ChangeControlBusyError` and never as a successful or partial CAS.

Authoritative review rows are companion audit records in the same
`state.sqlite3`; they are not aggregate fields and do not affect the aggregate
hash. A request binds the exact base revision/hash, a canonical full base
aggregate audit snapshot, and proposed replacement/constraint snapshots that
the store resolves under `BEGIN IMMEDIATE`. A decision supplies one ordered
accepted/edited/rejected outcome per requested original subject SHA. The store
constructs and validates one final aggregate, then commits its normalized rows,
the immutable decision/outcomes, and the existing generic operation receipt in
one transaction. Dedicated intent SHAs preserve human metadata semantics even
when two commands could yield the same aggregate hash. Request and decision
operation IDs are globally bound, exact lost-ack replay preserves timestamps,
and later aggregate replacements retain all historical review rows.

Public CAS cannot bypass review authority: reviewed-state additions,
proposed-to-reviewed transitions, mutations/removals of reviewed subjects, and
changes to subjects in an open request fail. Generic create/CAS accepts only
proposed review subjects; no accepted/rejected bootstrap import exists.
Lifecycle is derived as open, stale, or decided from the live head and immutable decision. Full JSON stored
with requests/decisions is an audit snapshot, not the live aggregate shadow;
all snapshots, canonical encodings, digests, ordinals, receipts, and permitted
diffs are revalidated on reads.

Migration `001` evolved only while this seam was uncommitted and
pre-acceptance; no database using an intermediate shape was released or
user-visible. Acceptance freezes `001` and its checksum. No compatibility is
claimed for those unshipped intermediate shapes: subsequent schema work,
starting with review persistence, must add ordered migration `002` or higher
and must not rewrite `001`.

Migration `002_authoritative_human_review.sql` is that first additive upgrade.
It preserves v1 aggregates and receipts without claiming or inventing review
history for them; pre-`002` history remains digest-only.

`TemporalReviewWorkflow` persists only a versioned primitive execution cursor
at `<workspace>/change_control/checkpoints.sqlite3`. Its stable topology reads
the authoritative request, interrupts once while it is open, and rereads
authority after every fixed internal wake. Public resume accepts no decision
payload. Status keeps authoritative lifecycle separate from orchestration
phase, so a separately committed decision appears as reconciliation-pending
until the disposable checkpoint catches up. The cross-database gap is a saga:
checkpoint failure never rolls back, repeats, or compensates a committed
decision. Strict serializer settings disable pickle fallback, restored state
is validated against its exact v1 primitive schema and request identity, and
corrupt checkpoints fail closed without deletion. The owned connection and
lock support synchronous callers in one process only; they make no
multi-process or production-scaling claim.

Deliberately deferred: relationship classifiers, actual-impact adjudication,
background workers, review UI/CLI integration,
multi-document apply/recovery, index updates, record-level evidence binding,
PostgreSQL change-control persistence, and impact evaluation.
