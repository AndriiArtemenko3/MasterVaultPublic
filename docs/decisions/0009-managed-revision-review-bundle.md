# ADR 0009: Authoritative managed-revision review bundle and activation boundary

- Status: accepted for the v0.3 Milestone 4 vertical slice
- Date: 2026-08-08

## Context

The first complete change-impact slice starts with a newly effective source,
classifies changed claims and dependencies, adjudicates actual downstream
impact, proposes minimal grounded revisions, pauses for a human, and later
activates the accepted result. Existing boundaries cover only parts of that
flow:

- `change_control/state.sqlite3` authoritatively reviews proposed document
  replacements and temporal constraints;
- the legacy file-backed `ReviewQueue` authoritatively applies one in-place
  Markdown patch at a time; and
- LangGraph checkpoints persist disposable execution cursors only.

None is a safe authority for one coordinated change containing an incoming raw
source, several regenerated `SourceNote` projections, explicit no-change
decisions, and an all-target activation boundary. In particular, a processed
`SourceNote` is an evidence projection bound to exact raw bytes by provenance
and hashes. Patching that note in place could preserve neither its raw evidence
nor consistency among frontmatter claims, summary, and body. Applying several
legacy queue items independently would also expose a partially changed corpus
and would not give one immutable record of what the reviewer saw and decided.

Search is derived state, but activating authoritative revisions before a
matching index generation is ready could let retrieval serve a stale or mixed
view. Filesystem publication, authoritative SQLite state, and the search index
cannot honestly be described as one transaction.

## Decision

### Two ordered pull requests

Milestone 4 is delivered through two dependent pull requests with one frozen
contract between them:

1. **PR A: analysis, staging, and authoritative review.** It verifies the
   incoming event, binds analysis to an exact aggregate head, classifies and
   adjudicates, creates inactive staged revisions, records one authoritative
   `ManagedRevisionReviewBundle`, and pauses or reconciles the LangGraph
   workflow. It may initialize an authoritative generation-zero pointer only
   from the exact verified pre-change manifest and seed receipt. PR A performs
   zero canonical knowledge writes: it must not create files in the managed
   canonical revision store, mutate existing raw or `SourceNote` bytes,
   advance that pointer beyond generation zero, or update a serving index.
2. **PR B: publication, activation, recovery, indexing, regression, and
   audit.** It exclusively creates the reviewed canonical revisions, prepares
   a derived index generation, advances the bundle-level active generation by
   compare-and-swap, reconciles the authority/index handshake after failures,
   runs targeted regression queries, and seals the final audit report.

PR A may be merged without PR B because its outputs are inert. The complete
vertical slice is not done until PR B is merged and its recovery and serving
gates pass. Migration or identity contracts accepted in PR A are immutable
inputs to PR B and must not be rewritten between the pull requests.

### A distinct authoritative action kind

Add an SQLite-authoritative `ManagedRevisionReviewBundle` action kind in
`<workspace>/change_control/state.sqlite3`. It is deliberately distinct from:

- temporal review, which continues to use the request and decision contracts
  accepted by ADR 0006; and
- the legacy `ReviewQueue`, which continues to authorize its existing
  single-file, in-place Markdown actions.

A logical action must belong to exactly one authority. A managed revision plan
must never be copied into the legacy queue, and neither a temporal request nor
a legacy queue outcome can authorize managed revision activation. A user-facing
facade may display temporal and managed requests together, but it dispatches
each decision to its own typed authority and reports the lack of a
cross-authority transaction.

This ADR supersedes ADR 0006 only where that ADR says the legacy `ReviewQueue`
is the sole authority for every canonical-file patch. That statement remains
true for the legacy in-place action kind. It does not apply to the new
create-only, generation-activated managed revision action defined here. All
other ADR 0006 temporal-review invariants remain unchanged.

### Create-only raw and SourceNote revisions

Managed plans never overwrite or delete an existing raw source or processed
`SourceNote`. Each accepted or edited target identifies:

- the exact active base raw revision and full SHA-256;
- the exact active base `SourceNote` revision and full SHA-256;
- a new, collision-resistant raw revision path and exact proposed bytes;
- a new, versioned `SourceNote` revision path and exact proposed bytes;
- the provenance link from the proposed `SourceNote` to the proposed raw
  revision;
- stable claim-identity reconciliation and the resulting claim revisions;
- exact evidence citations and minimal, ordered, non-overlapping semantic
  edits; and
- canonical payload and result SHA-256 values.

The new `SourceNote` must validate as a complete projection of the new raw
revision. Its provenance path and provenance hash must match those bytes, and
its frontmatter claims, summary, and body must be internally consistent. A
claim-ID change requires an explicit, mechanically validated reconciliation;
it cannot arise as an incidental regeneration side effect.

Old raw and `SourceNote` revisions remain byte-for-byte addressable after a
new generation activates. Deletion, compaction, and retention policy are not
part of this milestone.

PR A writes proposed bytes only to a run-scoped staging area outside every
canonical raw, revision, vault, and index root. Staging objects are
content-addressed, inactive, and unservable. Losing staging affects
availability only: bytes may be regenerated only if the complete result
revalidates to the already-reviewed hashes.

### Immutable bundle and one all-or-none decision

One bundle binds:

- its schema and algorithm versions;
- run ID and immutable run-binding SHA;
- aggregate ID, base revision, and base aggregate SHA;
- incoming-event, candidate, classification, attention, and impact result
  hashes;
- every canonically ordered target snapshot;
- every original managed revision plan hash, or an explicit no-change target;
- one content-addressed run-level inference contract fixing the algorithm-
  manifest SHA, contract ID and version, execution mode, provider, model,
  prompt SHA, and response-schema SHA;
- the active-generation ID and manifest SHA observed when review opens; and
- the canonical bundle payload SHA.

The immutable bundle binds the exact run, heads, analysis, targets, plans,
no-change cards, and review-open authority. The request command separately
binds requester, rationale, operation ID, and intent SHA; the committed request
record adds the store-generated UTC time and committed authority.

Every plan and no-change subject in the bundle must carry an inference receipt
that exactly matches that run-level contract. The decision factory repeats the
same check for every final edited plan, so review cannot substitute a different
provider, model, prompt, schema, execution mode, contract revision, or algorithm
manifest. A structurally valid, content-addressed contract object is not by
itself approval: the authoritative store must resolve the algorithm-manifest
bytes and approved contract record and require exact equality before accepting
or replaying a bundle.

The request ID hashes the immutable base and ordered target bindings. Metadata
that is not part of identity has its own intent SHA. Overlapping open requests
for the same logical target and active generation fail closed.

The bundle has exactly one immutable decision, committed in one
`BEGIN IMMEDIATE` transaction. The decision contains exactly one outcome for
every target in canonical order:

- `approve` binds the original plan hash as the final plan hash;
- `edit` binds a newly staged and fully revalidated final plan hash, which
  must differ from the original plan hash; or
- `reject` binds no final plan and keeps that target's currently active
  revision in the proposed generation.

There is no partial decision, incremental approval, or second decision. An
invalid item rolls back the complete decision transaction. Exact operation
replay returns the original decision; reusing an operation ID for different
inputs fails. The final generation manifest is derived mechanically from all
outcomes: approved and edited targets select their final revision plans, while
rejected and explicit no-change targets retain their prior active revisions.

The canonical successor manifest uses `content-addressed-overlay-v1`: it binds
the exact prior manifest SHA, preserves every unmentioned prior entry, and
contains only reviewed target overrides. The publication delta is separate
effect input. If every target is rejected or confirmed no-change, that delta is
empty, the prior generation and manifest remain active unchanged, no successor
generation or activation plan is created, and PR B performs no publication,
index rebuild, or authority advance for that decision.

The overlay also binds the typed prior generation number and ID. A non-empty
delta authorizes exactly `prior + 1`; an empty delta retains the exact typed
prior generation. Overrides are unique by destination path and by
`(target_key, publication_kind)`, so one target cannot carry two ambiguous raw
or `SourceNote` overrides.

The managed decision authorizes PR B to publish and activate only that exact
final generation manifest. It does not itself write canonical knowledge or
make staged bytes current.

### Bundle-level active generation and serving handshake

Currentness is selected by one authoritative, bundle-level active-generation
pointer in `state.sqlite3`, not by file modification time, directory order, a
checkpoint, or whichever index happens to be open. The pointer binds:

- a monotonic generation number and stable generation ID;
- the complete canonical generation manifest SHA;
- the authorizing managed decision payload SHA;
- the prior generation number and manifest SHA; and
- the authority revision used for compare-and-swap.

The pointer hashes a discriminated authority-origin basis. Generation zero is
the sole exception to the managed-decision fields: its
`verified-seed-bootstrap` basis binds the seed scenario and manifest SHA, the
verified pre-change aggregate head, and a deterministic generation-zero
manifest derived only from that verified pre-change inventory. The base
manifest excludes incoming-event-specific bootstrap IDs and SHAs: two genuine
incoming events over the same verified pre-change inventory therefore resolve
to the same generation-zero generation and manifest while retaining distinct
origin and authority-pointer identities. Generation zero has authority revision
exactly zero and no invented prior generation or managed decision. Every later
pointer uses a `managed-decision` basis that
binds the exact request record, decision identity and payload, immutable
decision record, activation-plan identity, expected prior authority
ID/revision/pointer SHA, and typed prior generation. Origin kinds cannot be
used with the other generation shape.

A managed-decision origin is a content-addressed reference to authoritative
records, not a self-authenticating proof that those records belong to the
authoritative store. Bare Pydantic deserialization proves only the structural
and locally knowable relations, including the exact one-step generation and
authority-revision advance. Every store read or activation MUST resolve the
origin against the typed immutable decision record and expected prior
authority, mechanically rederive the complete successor pointer, and require
exact equality. This explicit resolution avoids embedding an unbounded,
recursively growing authority history in each pointer.

Generation-zero structural validation likewise is not repository authority.
Its origin carries an explicit repository-resolution-required marker, and the
bootstrap service returns a process-local verified capability only after it
has rebuilt the pre-change aggregate from the verified seed. The authoritative
resolver must use that capability and the exact pre-change head to rederive the
generation-zero manifest, generation, origin, and pointer. A fake but internally
self-consistent `AnalysisBootstrapBinding` cannot initialize authority.

The pre-decision activation plan deliberately contains only the expected prior
authority and authorized successor generation; adding its own future decision
hash would create a circular identity. Once the immutable decision record
exists, the successor authority pointer is derived mechanically from that
record and the already-hashed activation plan. PR B persists exactly this
derived pointer by compare-and-swap and cannot substitute a different origin
shape.

PR B exclusively creates every canonical raw and `SourceNote` revision named
by the decided manifest and verifies their full hashes before activation.
Existing files at a proposed revision path are accepted only when they are
regular, confined, and byte-identical; any other collision is a conflict.
Partial file publication is therefore inert because the active pointer still
names the previous generation.

The search index is a derived, independently committed generation. PR B first
builds and verifies an inactive index generation from the complete proposed
knowledge generation, then records its generation ID and manifest SHA. The
serving boundary must compare the authoritative active generation with the
index generation on every open or refresh. It may serve only when both IDs and
manifest SHAs agree. A mismatch fails closed as `index-generation-pending`;
it must never serve the old index as if it represented the new authority or
the new index before authority activates.

Authority activation and index publication form an explicit reconciliation
saga, not a distributed transaction. PR B advances the authority pointer with
the reviewed base-generation and aggregate CAS preconditions, advances or
opens the matching derived index generation, and records idempotent receipts.
A crash in either order is recovered by rereading both generation identities
and converging them to the one decided manifest. It never compensates by
deleting immutable revisions or inventing a new decision.

### LangGraph is wake and reconciliation only

LangGraph uses sibling `<workspace>/change_control/checkpoints.sqlite3` with
the strict serializer and authority separation accepted by ADR 0007. Its
state contains JSON primitives only: immutable workflow/run/request bindings
and the last observed authoritative lifecycle and receipt hashes.

The interrupt exposes the immutable review bundle for display but accepts no
decision, patch, boolean, reviewer identity, or edited content on resume. The
public resume operation emits one fixed wake signal and ignores any attempted
payload. Every wake rereads `state.sqlite3`; only an exact authoritative bundle
decision can leave the review wait. A lost, corrupt, forged, or deleted
checkpoint can reduce availability but cannot approve, edit, reject, publish,
activate, or make a generation servable.

### Persistence shape

PR A adds an ordered, checksummed migration after the frozen migrations from
ADRs 0005 and 0006. It uses these normalized logical tables (the implementation
may add indexes without changing their ownership):

- `change_control_generation_manifests` for immutable canonical generation
  manifests, including the verified generation-zero manifest;
- `change_control_active_generation` for one compare-and-swap pointer per
  aggregate/workspace;
- `change_control_managed_review_bundles` for immutable request/base bindings;
- `change_control_managed_review_targets` for ordered target snapshots and
  original plan bindings;
- `change_control_managed_review_request_records` for the immutable committed
  request command, UTC time, and authority snapshot;
- append-only managed-review request delivery receipts that reference the
  original request record and record replay without rewriting request intent;
- `change_control_managed_review_decisions` for exactly one immutable decision
  record per bundle;
- `change_control_managed_review_decision_items` for ordered dispositions and
  final edited-plan hashes; and
- append-only managed-review decision delivery receipts that reference the
  original decision record and record replay without rewriting decision
  identity.

Initializing generation zero records the identity of already verified bytes;
it neither creates nor changes those bytes and does not activate a proposed
revision. PR A may validate the pointer for review staleness but cannot advance
it.

Canonical full bundle, target, plan, decision, and resulting-generation JSON
snapshots are immutable audit evidence, not a second live aggregate. Reads
revalidate canonical JSON, hashes, ordinals, request/decision identities,
operation receipts, active-generation base bindings, and the exact permitted
decision shape.

PR B adds a later ordered migration for
`change_control_revision_publication_events`,
`change_control_generation_activation_receipts`,
`change_control_index_generation_receipts`,
`change_control_regression_results`, and `change_control_audit_reports`.
Those tables hold effect/reconciliation evidence; they do not replace the PR A
decision or active pointer. PR A must not pre-create placeholder effect rows
or pretend those future operations have occurred.

### Evaluator isolation and keyless replay

Production change-control code, runtime manifests, staged plans, bundles, and
provider replay data must not import or read `mastervault.evals`, enter a
`golden` path, or contain expected labels, affected-target lists, expected
patches, or expected review decisions. The existing static and recursive seed
boundary checks extend to the new runtime files.

Evaluation code may load `datasets/larkstead/golden/change_impact.yaml` only
after the runtime result is complete, and only to score that result. A keyless
recorded provider response is accepted only through a typed prior LIVE receipt.
That receipt is a distinct `inference-receipt` artifact at the exact
content-addressed locator `receipts/inference/{sha256}.json`; the authoritative
resolver reopens those bytes, verifies their hash and length, and requires exact
contract ID/version, provider/model, prompt/schema SHA, ordered input artifacts,
canonical input/envelope, and raw/validated output equality. Replay receipts
carry an explicit authoritative-resolution-required marker. Replay chains,
receipt artifacts used as inference inputs, and a replay source equal to the
current validated output are rejected. Replay provenance and mode are reported
explicitly. Runtime replay data must be captured independently of the evaluator
gold; replay demonstrates deterministic orchestration and guards, not general
model quality.

### Fixed-fixture alignment authority

The vertical slice uses one repository-reviewed alignment attestation for the
exact hash-pinned SL2 returns-v2 fixture. The implementation accepts only the
allowlisted attestation path and code-pinned complete-file SHA-256. Before any
grounded incoming claim is exposed, it independently rebuilds the canonical
attestation payload from the already verified manifest, immutable raw bytes,
and verified `SourceNote` snapshot. That payload binds:

- the incoming event, document family, version, paths, roles, authority, and
  complete source hashes;
- each source-local claim ID to one exact raw evidence span and its extractive
  statement hash;
- the separately reviewed `affects` routing annotations; and
- the versioned mechanical scope policy that derives scopes from document
  family plus those annotations.

The payload SHA, attestation identity, complete-file SHA, and policy versions
are retained in the sealed incoming capability, event identity, evidence
digest, and deterministic analysis-bootstrap binding. Swapping otherwise valid
spans or changing reviewed routing annotations must therefore fail closed.

This attestation is review authority only for that exact fixture. It is not a
general entailment classifier, does not establish that arbitrary generated
claims are semantically grounded, and cannot be reused for a different
document or byte snapshot. General ingestion must instead produce an
authoritative human-review receipt for the exact claim/evidence alignment
before those claims can cross the same boundary. Process-local HMAC seals are
defence against accidental or untrusted-object mutation in a trusted Python
process; they are neither durable origin signatures nor protection against
hostile same-process code.

### Fixed work limits

The PR A runner fails closed rather than silently truncating when any of these
additional vertical-slice limits is exceeded:

- one incoming event and one incoming document;
- exactly 10 changed claim revisions for the fixed SL2 fixture; the reusable
  managed-review contract ceiling is 16, and the stricter boundary applies;
- 256 relationship classifications;
- 64 dependency candidates;
- 16 reviewed targets;
- 8 targets with proposed managed revisions;
- 16 semantic edits per revision and 64 across the bundle;
- 256 KiB declared bytes for every managed artifact kind (raw source,
  `SourceNote`, inference input, inference output, and inference receipt);
- 32 inference-input artifacts and 1 MiB aggregate declared inference-input
  bytes per inference receipt;
- 16 citations per semantic hunk, 256 projected claims per `SourceNote`, 512
  claim-reconciliation entries, and 16 generation-publication entries;
- 16 scopes and 16 evidence references per managed claim;
- 8 KiB UTF-8 bytes per grounded citation quote and 64 KiB UTF-8 bytes per
  attested before/replacement text or managed claim statement, with the same
  8 KiB ceiling applied to each nested claim evidence quote;
- 1 KiB UTF-8 bytes per managed path and 512 UTF-8 bytes per managed logical
  or semantic key;
- 1 MiB for the complete canonical review-bundle payload;
- 1 MiB for the complete canonical decision payload; and
- one structured-output correction retry per model-assisted contract.

The stricter of these limits and ADR 0008's existing discovery count, path,
and canonical-byte limits always applies. Limit values affect completeness and
therefore require a versioned contract change.

### Security, conflict, and idempotency rules

- Every path is a normalized safe relative POSIX path under its explicit raw,
  `SourceNote`, staging, revision, or index root. Absolute paths, Windows
  drives, `..`, NULs, hidden evaluator routes, symlinks, and special files fail
  closed.
- Predecessor artifacts cannot resolve from the managed-review staging root.
  Authoritative staging paths are globally unique and disjoint from every
  predecessor path. Across predecessor, staging, inference-input, and replay
  receipts, a repeated locator is allowed only when it is the exact same typed
  artifact receipt; conflicting kind, SHA, or byte-count bindings fail closed.
- Staging and publication use exclusive creation, no-follow reads, regular-file
  checks, full SHA-256 verification, file `fsync`, and containing-directory
  `fsync`. No unreviewed bytes are reconstructed during activation.
- Bundle creation binds the exact aggregate and active-generation head.
  Decision and activation use compare-and-swap; stale authority never rebases
  silently.
- Operation IDs are conservative bounded ASCII and globally single-purpose.
  Exact retries return immutable receipts; different-input reuse fails.
- Provider text and corpus text remain untrusted, fenced inputs. Only typed
  outputs that pass exact evidence, path, size, provenance, and minimality
  gates may enter a bundle.
- Actor IDs, rationales, and UTC timestamps retain ADR 0006's normalization
  and bounds. Secrets, absolute local paths, and unnecessary full-document
  text are excluded from logs and exported summaries.
- This remains a local single-operator threat model. It does not claim defence
  against a privileged process concurrently replacing parent directories or
  exploiting hard links.

## Consequences and limits

- PR A can demonstrate evidence-bound analysis, exact staged proposals, one
  authoritative human decision, and durable LangGraph waiting without risking
  canonical knowledge or serving state.
- PR B has one unambiguous effect contract: publish only create-only revisions
  named by the decided manifest, activate them together, and serve them only
  through a matching index generation.
- Historical evidence remains reproducible because activation changes a
  pointer rather than overwriting prior source or projection bytes.
- Temporal review, managed-revision review, and the legacy queue remain three
  typed authorities. A facade may coordinate them but cannot merge their
  decisions or claim cross-authority atomicity.
- SQLite is authoritative for bundle decisions and active generation;
  filesystem revisions are canonical content; the index is derived; LangGraph
  checkpoints are disposable.
- A generation/index mismatch causes temporary fail-closed unavailability.
  Availability is preferred over serving a false mixed generation.
- PostgreSQL change-control parity, in-place managed patching, deletion or
  compaction of old revisions, multi-event bundles, partial or rolling bundle
  approval, background workers, distributed locks, notification delivery,
  browser review UI, OCR, multi-agent orchestration, broad observability,
  general-purpose versioned content management, and public-release polish are
  explicitly deferred.
