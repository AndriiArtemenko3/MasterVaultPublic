# ADR 0008: Deterministic advisory change discovery

- Status: accepted for the bounded v0.3 Milestone 4 discovery slice
- Date: 2026-08-07

## Context

The temporal aggregate now stores canonical document and claim bindings,
reviewed temporal facts, current claim-pair assessments, and exact
`downstream document --DEPENDS_ON--> upstream claim` facts. The next workflow
step needs reproducible relationship-analysis inputs and an attention queue,
without predicting labels, asserting actual impact, or gaining authority to
change the aggregate or vault.

An already-assessed changed-to-incumbent pair must not be emitted again, but
its canonical edge must still be usable to find downstream dependencies.
Likewise, a retrieved or topically plausible document is not thereby a known
dependency.

## Decision

`mastervault.change_control.discovery` is a pure, dependency-neutral advisory
layer over one immutable `ChangeControlSnapshot`. It performs no store,
checkpoint, source, manifest, retrieval, provider, evaluator, CLI, or UI I/O.
It revalidates the aggregate and its full SHA before analysis and requires a
positive snapshot revision.

Each public analysis constructs one sealed `TemporalResolutionContext` after
snapshot validation. The context defensively revalidates the temporal graph
once, indexes accepted constraints by exact typed target once, and caches a
resolution by the complete canonical claim or document payload—not merely its
ID. Candidate generation resolves each unique aggregate claim at most once,
outside the changed-by-incumbent Cartesian loop. Documents resolve lazily only
when dependency traversal reaches them. Ranking uses one context for changed
root validation, exact candidate regeneration, and reached documents. The
batch path calls the same temporal projection semantics as the single-target
resolvers, including exact accepted-basis binding, half-open intervals,
declared/inferred conflicts, applied constraint IDs, basis relation IDs, and
canonical conflict ordering.

`generate_relationship_candidates` exhaustively crosses sorted changed claim
roots with every non-changed claim root. It constructs the existing canonical
`ComparableClaimPair`, omits every pair already represented in the relation
graph—including no-edge `COEXISTS` and `UNRELATED` assessments—and performs no
top-k pruning. Every output is explicitly `unassessed`; it contains no
predicted disposition, edge direction, confidence, or rationale.

Candidate ordering uses this version-1 lexicographic vector:

1. same claim identity, descending;
2. same document family, descending;
3. mechanically shared scope count, descending;
4. temporal overlap: true, then false, then unknown;
5. incumbent temporal rank: current, future, historical, expired, unresolved;
6. stable pair ID.

Unknown overlap is distinct from non-overlap because an unresolved temporal
projection does not support a negative temporal conclusion. Candidate output
retains the complete changed and incumbent `TemporalResolution`, including
accepted constraint and basis IDs and conflicts.

`rank_document_attention` regenerates the candidate set and requires exact
equality before using it. A valid self-hash therefore cannot make an omitted or
re-scored caller-authored set consistent with the supplied snapshot. Discovery
anchors are:

1. changed roots and claims reached from them through edge-bearing
   `SUPERSEDES` or `CONTRADICTS` assessments present in the validated supplied
   aggregate; then
2. incumbents from unassessed candidates mechanically supported by same
   identity, same family, or shared scope.

Canonical relation anchors rank ahead of heuristic unassessed anchors.
`SUPERSEDES` retains canonical newer-to-older endpoints and records whether
discovery traversed forward or reverse; `CONTRADICTS` remains symmetric.

After relation closure, traversal follows `DEPENDS_ON` in reverse only. A
reached document exposes exactly the `downstream_claim_revisions` carried by
that dependency. An empty tuple is a valid body-only terminal and no claim is
fabricated. Exposed downstream claims may continue reverse-dependency BFS, but
relation adjacency is not re-entered after that transition. Canonical shortest
claim states and visited depths terminate dependency cycles. Distinct canonical
paths to one document are retained while the document is deduplicated.

Only current downstream document versions become attention candidates.
Changed documents and future, historical, expired, or unresolved targets have
typed exclusions. A terminal `HISTORICAL_REFERENCE` path is ineligible for
attention; another eligible path can still make the same current document an
attention candidate. Historical upstream claims remain traversable because a
current downstream document can still contain stale operational text. Every path and
ranked or excluded target retains the complete target document
`TemporalResolution`.

Document-attention ordering uses this version-1 lexicographic vector:

1. anchor kind, supplied-aggregate changed-root/canonical-relation before
   unassessed-candidate;
2. dependency depth, ascending;
3. relation hops, ascending;
4. terminal dependency kind: quotes/implements, then summarizes;
5. anchor rank, ascending;
6. distinct supporting terminal dependency count, descending;
7. stable document-version ID.

The anchor-kind component is first because a canonical graph path present in
the validated supplied aggregate must categorically outrank an otherwise
shorter heuristic candidate anchor. Relation and dependency models do not have
a review status, so their presence is not described as human acceptance. Scores
contain only integers and ordinals; paths contain stable IDs, exact spans,
exposed claim IDs, relation semantics, and temporal projections rather than
generated prose.

Bindings and results use canonical JSON SHA-256 over the algorithm/schema
version, aggregate ID, caller-supplied snapshot revision, aggregate SHA,
analysis date, sorted changed IDs, complete
changed-root temporal projections, source candidate-set SHA where applicable,
and canonical output. Future changed roots are marked preview. Historical,
expired, or unresolved changed roots fail closed.

These hashes prove deterministic content integrity, not origin or
persisted-head provenance: any caller can construct a snapshot, choose its
revision metadata, and recompute the hashes.
`validate_document_attention_ranking` establishes
snapshot-relative consistency by deserializing and exactly regenerating the
ranking from the supplied snapshot and candidates. Callers that require
authoritative persisted-head provenance must load from `SqliteChangeControlStore`
and compare or reload the head around use; discovery performs no store I/O.

Version 1 fails closed with fixed limits: 64 changed roots, 20,000 emitted
relationship candidates, 20,000 relation facts, 20,000 dependency facts, 16
relation hops, 20,000 anchors, dependency depth 8, 128 paths per document, and
4,096 generated paths overall. It also limits each dependency to 64 spans;
each canonical span to 16,384 bytes; each dependency projection to 131,072
bytes; cumulative unique dependency projections to 1,048,576 bytes; each
relationship candidate to 16,384 bytes; cumulative candidate projections to
2,097,152 bytes; each attention path to 262,144 bytes; cumulative generated
attention paths to 1,048,576 bytes; each complete ranked or excluded attention
target record to 393,216 bytes; cumulative attention target records to
1,179,648 bytes; the final candidate-set payload to 2,359,296 bytes; and the
final attention-ranking payload to 1,310,720 bytes.

Byte limits count complete canonical UTF-8 JSON, including syntax, escaping,
quotes, exact spans, and nested PDF evidence. Per-projection work is charged in
the fixed `mastervault.discovery-projection-charge.v1` canonical envelope;
span and final-payload limits count their exact canonical encoding. These are
deterministic canonical work/output budgets, not claims about Python heap
memory. A dependency projection is charged once to the unique-dependency
budget, while its complete serialized step is charged again each time it
appears in a distinct generated path. Attention-target charges cover the whole
canonical candidate or exclusion record, including document identity,
temporality, score or reasons, and every retained path.

Failure order is deterministic. Snapshot preflight checks fact counts, then
each dependency's span count, span bytes, dependency bytes, and cumulative
unique dependency bytes. Candidate generation checks the exhaustive output
count before bulk incumbent resolution, then per-candidate and cumulative bytes
before retention, then the final payload. Path generation checks depth and path
count, then computes the complete path charge with a fixed-length SHA
placeholder. Per-path and prospective cumulative generated bytes are enforced
before path hashing, construction, or per-document retention.

Caller-provided retained output is traversed in canonical candidate-then-
exclusion order. Retained path count, each path, cumulative paths, each newly
encountered dependency projection, and cumulative unique dependencies fail on
the first over-limit prefix without traversing later paths. Complete target
records then fail per record and cumulatively on the first over-limit prefix,
before the final ranking payload or digest is built. A breach raises
`DiscoveryLimitError` with its category, limit, and observed count; no partial
result is returned and no final digest is computed. Changing any count or byte
limit changes observable completeness and therefore requires a new algorithm
version.

## Consequences and limits

- The result means “relationship pair needs assessment” or “current document
  deserves attention,” never “classified” or “affected.”
- Canonical relation and dependency facts mean facts present in the validated
  supplied aggregate snapshot. They are discovery inputs, not newly inferred
  or necessarily human-reviewed facts.
- No database migration or persistence is introduced; results are disposable
  derived projections bound to one aggregate snapshot.
- No relation classifier, patch generator, review decision, or apply authority
  is added.
- Runtime code does not import `mastervault.evals`, inspect golden data, or
  encode SL2 answers. Evaluation may compare these advisory outputs to gold
  only outside the runtime package.
