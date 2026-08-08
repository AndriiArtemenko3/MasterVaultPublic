# ADR 0004: Runtime temporal foundation and pre-change seed boundary

- Status: accepted for v0.3 Milestone 4 foundation
- Date: 2026-08-07

## Context

The shipped Larkstead vault represents the current corpus and already contains
Returns Policy v2. Loading it and then pretending that v2 has just arrived
would not demonstrate change detection: the trigger, its extracted claims, and
some current-state consequences would already be present.

The existing change-impact file is evaluator truth. It contains relationship
labels, affected and unaffected documents, patches, and review decisions. A
runtime workflow that imports it, directly or through a renamed fixture, would
be replaying answers rather than discovering them.

The change workflow also needs temporal types that preserve the existing
M1-M3 evidence contracts. A policy supersession is directed, a contradiction
is symmetric, and a downstream process can copy policy text in its body even
when the extractor did not turn that exact sentence into a claim.

## Decision

`datasets/larkstead/change_control/sl2_prechange.yaml` is a strict runtime
source inventory as of 2026-01-11. It selects seven real raw sources and their
real processed notes, with full SHA-256 identities and source-derived document
metadata. Source dates are explicitly named `declared_effective_from` and
`declared_effective_to`; intervals are half-open. Returns Policy v1 remains
open-ended: the seed does not encode the later evaluator conclusion that v2
supersedes it. The incoming v2 document is absent. The proposal source phrase
`Valid through 2025-10-18` is inclusive natural language, so its normalized
exclusive `declared_effective_to` is 2025-10-19.

The manifest loader captures, parses, hashes, and seals one exact manifest byte
snapshot. The document verifier then accepts that capability plus a document
ID, resolves exactly one original entry, and captures each raw source and
processed note once. It checks their full hashes, canonical `SourceNote`, exact
raw provenance, legacy provenance hash, and manifest-derived note path against
those captured bytes. Resolvers accept only a capability sealed to the exact
manifest snapshot, entry, and repository root; callers cannot supply fresh
metadata, replace the entry, select a different document/note path, or trigger
a second filesystem read. Raw sources must physically resolve below the real
raw root and processed notes below the real processed root, so a lexical
runtime path cannot follow a symlink into evaluator gold. Duplicate
source-local claim IDs fail context verification rather than making identity
depend on the first matching row. Claim references and document spans retain
the processed note's full SHA-256. Body spans use file-relative character
offsets and cannot cite frontmatter. The same boundary shape is intended for a
later PDF context after PDF asset and parsed-document lineage can also be
verified.

The materializer accepts only targets outside the repository, including after
symlink resolution and filesystem-identity checks that close case-insensitive
path aliases. It publishes a disposable workspace through a private staging
directory and an exclusive lock, and removes that lock only when its
device/inode still matches the file it opened. Re-running against the exact
pristine output verifies and reuses it; any extra, missing, replaced, or
drifted directory, regular file, symlink, or special node fails rather than
being overwritten or opened. The directory rename and lock coordinate
cooperating materializers; they do not exclude unrelated processes that ignore
or replace the lock. The shipped processed corpus is never modified.

Runtime and evaluator boundaries are enforced in three places:

1. The runtime manifest schema forbids unknown fields and recursively rejects
   relationship labels, impact results, patches, review decisions, and other
   gold-shaped keys.
2. Runtime seed paths cannot enter an evaluator-gold directory.
3. Static tests reject imports from `mastervault.evals` and evaluator-path
   literals anywhere in the production change-control package.

The temporal model uses strict, frozen, provider-independent Pydantic types and
versioned identity namespaces. Binding corrections and semantic identity are
separate:

- a document-version identity is its namespace, normalized family, and version
  label only; path, full source hash, declared dates, role, authority, and
  inferred currentness do not churn that ID;
- document and claim registries collapse exact replay and reject conflicting
  bindings. Standalone binding replacement is not exposed because embedded
  claim, relation, dependency, and temporal snapshots could otherwise become
  incoherent; correction is deferred until a persistence aggregate can update
  and validate every dependent collection atomically;
- a claim identity is its document-version ID plus source-local claim key;
- a claim revision adds its normalized statement, scopes, and declared
  temporal qualifiers, while source-note path/hash, evidence, confidence, and
  inferred state remain outside revision identity;
- a comparable-pair identity contains only its order-independent endpoints.
  Shared scopes are their derived intersection, not caller input.

Document versions, claim identities/revisions, candidate pairs, persisted
relations, dependencies, and temporal constraints use readable prefixes plus
full SHA-256 hashes of canonical JSON. The contracts enforce:

- `SUPERSEDES`: directed newer-to-older, same family, non-reflexive, and
  acyclic at the collection boundary;
- `CONTRADICTS`: symmetric with canonical endpoint ordering;
- `COEXISTS` and `UNRELATED`: assessment outcomes that cannot become graph
  edges;
- exactly one current assessment per pair, including no-edge dispositions;
- canonical pair-ID ordering for order-independent relation-graph snapshots;
- `DEPENDS_ON`: `downstream document --DEPENDS_ON--> upstream claim`, with
  reverse traversal reserved for later impact discovery;
- all exact downstream canonical-note spans and associated downstream claim
  revisions for one semantic dependency, including body-only dependencies;
- exact canonical-note path and full-SHA agreement across every downstream
  span and attached claim revision;
- unchanged M1-M3 page/block/cell evidence objects when the source is a PDF;
- claim intervals fully contained within document-version intervals;
- positive relations require a non-empty mechanically shared scope, while a
  disjoint pair remains a valid `UNRELATED` candidate.

Claim revisions are created through the verified-context resolver that proves
the canonical note and claim ID exist. Body-only dependencies use a separate
span resolver and do not fabricate an unrelated claim ID.

A claim-level `SUPERSEDES` assessment can derive a half-open
`valid_to_exclusive` constraint only for its exact older claim revision. Whole
document closure requires a distinct reviewed `DocumentReplacementAssessment`:
same family, distinct version, and a later declared document start. Only an
accepted current replacement can derive a document constraint. Constraint
identity includes the resolver version, target, inferred bound, and basis
relation IDs; review status and rationale are mutable state outside the ID.

The resolver accepts only a `ValidatedTemporalConstraintSet`. Every accepted
claim constraint must be backed by a genuine current `SUPERSEDES` edge with the
exact older endpoint and newer-claim bound. Every accepted document constraint
must be backed by the exact current accepted document replacement and its
newer-document bound. Cross-graph document bindings must also agree. Missing,
stale, wrong-kind, wrong-endpoint, wrong-bound, or orphaned bases fail closed.
Proposed and rejected constraints do not affect temporal answers. Accepted
constraints close only derived projections. Source-declared metadata and every
document, claim, pair, relation, and dependency ID remain unchanged. A
projection reports `current`, `historical`, `future`, `expired`, or
`unresolved`; disagreement between declared and inferred bounds, or between
accepted inferred bounds, is exposed as `unresolved` rather than guessed away.

## Consequences and limits

- The foundation can construct an honest pre-v2 SL2 state without evaluator
  answers.
- The runtime family for Returns Policy v1 is
  `customer-support.returns-policy`, matching the established PDF manifest and
  allowing a future v2 claim to satisfy the same-family supersession rule.
- This seam defines contracts and deterministic source materialization only.
- Temporal resolution is a pure projection over declared metadata and reviewed
  constraints; it is not persistence and does not mutate canonical files.
- Binding corrections remain strict conflict failures until the persistence
  seam provides one aggregate compare-and-swap across embedded snapshots.
- Record-level span binding is deferred until a storage-backed resolver can
  verify record ownership. The foundation does not accept an arbitrary record
  ID and call it grounded.
- Database migrations, relation persistence, candidate generation,
  classification, LangGraph checkpointing, review UI, recoverable apply,
  reindexing, regression orchestration, and impact metrics remain subsequent
  Milestone 4 seams.
- No parsing, retrieval, relation-quality, or impact-performance claim is made.
