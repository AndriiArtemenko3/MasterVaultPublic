# `mastervault.change_control`

Temporal contracts and bounded runtime authorities for MasterVault's
knowledge-change workflow.

- `models.py` defines document versions, versioned claim revisions, canonical
  claim pairs, relation/dependency assessments, explicit reviewed document
  replacements, graph-validated temporal constraints, derived projections,
  graph integrity, and full SHA-256 IDs.
- `store.py` owns the dedicated, transactional SQLite aggregate store at the
  caller-selected change-control path. It never writes the rebuildable search
  index database.
- `managed_store.py` adds ADR 0009's PR-A-only generation-zero and managed
  review authority as a typed subclass over that same database. It stores
  immutable bundles, normalized targets, requests, decisions, append-only
  delivery receipts, and successor manifests created inactive without
  publishing files, advancing authority, or touching the serving index.
  Repository bytes,
  approved inference contracts, patch reconstruction, and SourceNote
  projection validation cross an injected `ManagedReviewRepositoryResolver`;
  no production registry is invented before the classifier/service seam owns
  those reviewed records. The store-owned lifecycle is `open`, `stale`, or
  `decided`, while the pure bundle view intentionally remains `open/decided`.
  A managed bundle's temporal prerequisite names the exact authoritative
  ADR 0006 decision that produced its review-open aggregate head; it does not
  require that decision to contain a temporal-constraint outcome specifically.
  The managed run operation is the exact generic CAS transition from its frozen
  analysis head to that temporal request's base head. Manifest activity is
  derived from the active-generation pointer; `created_inactive` records only
  the immutable state in which a managed overlay was first committed.
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
- `classification.py` rederives that exhaustive candidate inventory, creates a
  content-addressed selected/excluded ledger with no silent top-k truncation,
  and emits per-changed-root inference shards that retain both exact claim
  revisions and complete candidate temporality. Its frozen v1 selector keeps
  all same-identity/family/scope pairs, then adds bounded lexical policy-slot
  comparisons and neutral deterministic coverage samples. Exact results are
  partitioned one-for-one into content-addressed per-changed-root output
  shards; every input shard, output shard, and the compact result index stays
  below the 256 KiB managed-artifact ceiling. The result-index SHA—not an
  in-memory envelope containing all bodies—is the downstream classification
  result binding. Classifications remain advisory; only model-valid
  assessments are exposed for graph materialization, while cross-family
  contradictions remain advisory-only and `SUPERSEDES` requires a strictly
  later same-family endpoint.
- `dependency_analysis.py` takes every graph-valid changed-to-older
  `SUPERSEDES` result from the complete validated classification result and
  exhaustively crosses those old-claim roots with a sealed, in-memory
  canonical-SourceNote inventory covering every aggregate document. Claim
  neighbours are retained as evidence bindings but never define document
  coverage, so body-only dependencies remain visible. The complete cross
  product fails closed above 64 questions. Changed documents and each old
  claim's own document are explicitly ledgered exclusions. One input shard per
  downstream document carries its full UTF-8 note, claims, temporal state, and
  all compact old-root questions; each shard is at most 256 KiB, at most 32
  shards/1 MiB total are allowed, and output shards mirror the same document
  partition. `DEPENDS_ON` requires an exact body span and dependency kind;
  `NOT_DEPENDENT` carries no edge fields. Exact character slicing is reopened
  during validation before positive results can become
  `DependencyAssessment` values. The pure module never resolves repository
  paths itself: it accepts only a verified capability over exact bytes and body
  boundaries.
- `source_note_inventory.py` is the repository adapter for that capability. It
  reloads the exact allowlisted pre-change and incoming roots, proves they
  reproduce the authenticated revision-2 bootstrap snapshot, and checks exact
  coverage of all eight SourceNotes and all 79 claim bindings. Resolution is
  the only filesystem phase. The resulting HMAC-sealed capability is
  non-serializable and its `verify` method performs only in-memory validation
  against the supplied snapshot.
- `recorded_inference.py` is the bounded execution boundary for classification
  and dependency shards. A LIVE request gives the injected provider the exact
  prompt, response schema, and typed input-shard bytes; semantic model output
  is converted deterministically into the domain output shard. One
  schema/semantic correction retry is allowed, and rejected raw output plus the
  bounded validation error remain content-addressed execution evidence. REPLAY
  performs no provider call and accepts only an exact prior LIVE outcome whose
  receipt, input contract, raw output, and canonical validated output all
  revalidate.
  Provider requests are capped at 3 MiB, provider responses at 256 KiB, and the
  exact seven- or eight-artifact outcome at 2 MiB of content/13 MiB canonical.
  This seam carries evidence in memory; it does not persist artifacts, mutate
  aggregate authority, adjudicate impact, stage patches, or add LangGraph
  orchestration.
- `inference_repository.py` is the concrete create-only durability boundary for
  those outcomes. A batch marker is linked and directory-synced last; receipts
  become replay authority only through membership in a valid committed batch.
  Exact retries re-sync existing files and their parent directories before
  returning authority, and bounded regular temporary residue from interrupted
  writes is cleaned under the repository lock while unsafe residue fails
  closed. Fresh-process capability reminting re-syncs the batch marker and
  parent and then reopens the complete batch again before granting authority.
  REPLAY persistence independently reopens an already committed LIVE source
  and revalidates receipt, execution, contract, input, and output lineage.
  Aggregate artifact/outcome/manifest limits bound repository work, and runtime
  inference inputs reject evaluator/golden path or metadata shapes. The
  implementation deliberately fails before mutation on platforms without its
  required POSIX `flock`, `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW` guarantees;
  this is a feature-level portability constraint, not a claim that the rest of
  MasterVault is POSIX-only.
- `temporal_proposal.py` composes the exact validated classification and
  dependency results into an inert revision-3 proposal: graph-valid relations,
  positive dependencies, one proposed document replacement, proposed temporal
  constraints, and complete review-subject coverage. It does not itself own a
  provider, filesystem repository, or authoritative commit.
- `temporal_analysis.py` is the bounded 16 MiB reproduction manifest for that
  proposal. It stores the exact revision-2 aggregate, SourceNote inventory,
  candidate and workload ledgers, compact result indices, replacement
  candidate, proposal, and durable batch references. Provider output/artifact
  bytes remain solely in the inference batches. Reopening reconstructs both
  typed result sets from those batches and must reproduce the proposal exactly.
- `temporal_commit.py` is the evidence-first revision-2 to revision-3 authority.
  It freshly re-resolves canonical SourceNotes from repository roots, verifies
  sealed classification and dependency batches from the same evidence
  repository, persists and reopens the canonical temporal manifest, and only
  then issues SQLite compare-and-swap under
  `temporal-commit:<manifest-sha256>`. The filesystem and SQLite cannot share an
  atomic transaction: a crash before CAS may leave an immutable inert manifest,
  while a committed revision is never accepted if that manifest is later
  missing or corrupt. Exact lost-ack retry reuses the same manifest and database
  operation receipt.
- `seed.py` strictly loads the SL2 pre-change source inventory, verifies one
  raw/note byte snapshot per manifest entry, and materializes a disposable
  vault without touching the shipped current-state corpus.
- `incoming.py` verifies the one fixed SL2 returns-v2 fixture. Before exposing
  grounded claims, it rebuilds a canonical manifest/raw/SourceNote alignment
  payload and checks it against an exact-path, bounded, code-hash-pinned
  repository-review attestation.
- `claim_scopes.py` owns the strict `claim-scopes-v1` policy: the sorted unique
  union of a document family and separately reviewed claim `affects` routing
  annotations. This mechanical policy does not infer semantic relevance.
- `bootstrap.py` deterministically resolves the sealed 7-document/69-claim
  pre-change aggregate, persists it as revision 1, CAS-adds the sealed incoming
  document and 10 claim roots as revision 2, and returns the exact reloaded
  snapshot plus both store receipts. The dependency-neutral
  `analysis_binding.py` owns the one pure content-addressed binding model;
  `create_verified_analysis_bootstrap_binding` is the separately named
  repository-backed constructor and returns that exact pure model, not a
  service subclass. Binding equivalence is established by its validated ID and
  SHA rather than Python class identity. The binding retains a
  digest of the complete incoming grounded-claim evidence and exact alignment
  attestation, while its result keeps the sealed incoming capability for later
  evidence-consuming analysis. The binding factory derives aggregate hashes,
  evidence SHA, and changed roots from the supplied exact aggregates and sealed
  event rather than accepting caller-authored derivative hashes.

The alignment attestation is repository-review authority only for the exact
hash-pinned SL2 fixture. It approves the recorded one-span extractive pairings
and `affects` routing annotations; it is not a general semantic-entailment
algorithm or a reusable trust decision for other documents. Capability HMACs
detect accidental or untrusted-object mutation inside one trusted Python
process. They are not durable signatures and do not defend against hostile code
running in that same process.

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

Deliberately deferred: concrete hosted/local provider adapters and model
dependencies, actual-impact adjudication, background workers, review UI/CLI integration,
multi-document apply/recovery, index updates, record-level evidence binding,
PostgreSQL change-control persistence, and impact evaluation.
