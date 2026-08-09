# ADR 0013: Deterministic recorded revision planning and inert staging

- Status: accepted for the v0.3 Milestone 4 recorded revision-planning slice
- Date: 2026-08-09

## Context

ADR 0011 makes the complete Step 10b actual-impact result reproducible from a
committed inference batch. ADR 0012 then freezes the all-target eligibility
gate and the narrow provider wire contract for revision planning. The next
boundary must execute that contract without letting provider text become
repository authority.

An affected response still has to become exact successor raw bytes, a complete
SourceNote projection, grounded byte citations, semantic hunks, claim
reconciliation, and a receipt-compatible managed-plan payload. A no-change
response similarly has to become a complete, grounded card. All paths, hashes,
identities, provenance, temporal metadata, and publication destinations must
therefore be derived locally. The derived bytes also need restart evidence,
but writing them to canonical knowledge paths or opening human review would
cross separate authority boundaries.

The existing `TargetAnalysisBinding` v1 contract presents one compatibility
problem. Its inference-input artifact is itself the small target-analysis
envelope. Recorded inference instead has both a generic input-envelope hash
and a distinct, content-addressed planning-input artifact. Treating either hash
as the other would make a receipt look valid while binding different bytes.

## Decision

### Consume the exact Step 10b run

`execute_revision_planning` accepts the exact `RecordedImpactInferenceRun`,
not caller-supplied impact hashes or an independently reconstructed result.
Its impact result and verified-batch metadata are carried into the v2 managed
analysis binding. The service also resolves the temporal-analysis manifest
needed to reproduce that analysis lineage.

Eligibility is evaluated before provider, evidence-repository, or staging
work. For an eligible result, predecessor snapshots must cover every selected
target exactly once and must match the Step 10 impact input's raw path and
SHA-256, SourceNote path, exact SourceNote UTF-8, and SourceNote SHA-256. This
slice supports managed Markdown predecessors only; PDF-grounded SourceNotes or
claims with page evidence fail closed rather than being silently flattened.

The complete set has two strict exceptional outcomes:

- `NO_WORK` means the Step 10 workload had no questions. The canonical empty
  revision workload is returned with no subjects, outcomes, evidence batch, or
  staging capability. Predecessor snapshots, replay sources, and a provider
  are forbidden, and neither repository is called.
- Any Step 10 output with disposition `UNRESOLVED` blocks the whole set before
  planning side effects. Resolved targets are not processed around it.

### Freeze three content-addressed revision identities

Recorded planning uses three explicit identity domains:

- `revisionwork:<sha256>` hashes the canonical eligibility ledger and the
  ordered references to every planning input shard;
- `revisionin:<sha256>` hashes one complete planning input: run and exact
  Step 10 workload/result identities, selected impact target, predecessor
  metadata and exact raw/SourceNote UTF-8, role-qualified citation inputs, and
  existing stable claim identities and statements; and
- `revisionout:<sha256>` hashes the exact receipt-free canonical proposal
  envelope produced locally for one `ManagedRevisionPlan` or
  `NoChangeImpactCard`.

The typed output additionally binds its workload, planning input, target, and
Step 10 output shard and retains the validated semantic response. The
validated-output artifact and inference receipt deliberately hash the proposal
envelope bytes, not the later full subject containing that receipt. Excluding
`inference_receipt` and `validated_output` from the proposal envelope avoids a
circular identity while still letting the final subject prove exact equality
with the recorded output.

### Record role-qualified evidence

Each planning shard has two locally constructed citation inputs:

- `governing-evidence` is canonical JSON containing the exact impact questions
  and Step 10 decisions for the target; and
- `target-evidence` is the exact predecessor SourceNote text selected by the
  Step 10 shard.

They are separate inference-input artifacts at role-qualified,
content-addressed paths. The recorded input envelope also includes the exact
planning shard at its run/target-scoped staged-analysis path, in addition to
the standard algorithm, prompt, schema, and input-shard artifacts. This lets
later materialization resolve every provider selector to recorded bytes rather
than trusting a quote returned by the provider.

The pure wire validator continues to use Python-character offsets. Local
materialization converts accepted citation and edit boundaries to UTF-8 byte
offsets against the exact recorded strings. Every resulting
`GroundedArtifactCitation` therefore identifies a recorded artifact, byte
range, and exact quote.

### Reuse bounded LIVE and REPLAY inference

Add `REVISION_PLANNING` to the existing recorded-inference task vocabulary.
LIVE execution records the exact provider request, raw output, usage, and
receipt. One invalid schema, semantic, grounding, or deterministic-
materialization result may receive exactly one correction retry containing
the previous raw output and bounded validation error. Two failed attempts
produce no committed planning batch.

REPLAY makes no provider call. Every planning input must name one exact receipt
from an independently committed LIVE execution. The repository reopens that
evidence and requires the same task, contract, generic input envelope, raw
output, and validated proposal bytes. The stored semantic response is locally
revalidated and rematerialized; both the typed output and exact proposal bytes
must equal the LIVE source. Replay chains remain invalid.

After all targets succeed, their outcomes are persisted as one ordinary batch
in the existing inference-evidence repository. The service freshly verifies
and reopens that batch, uses the reopened outcomes as the receipt/output
authority for the final subjects, and requires the locally materialized subject
payload to equal the recorded proposal bytes. Missing, duplicate, mixed-task,
substituted, or incompletely covered outcomes fail before a result is returned.

### Materialize plans and no-change cards locally

For an affected target, local code:

1. applies the validated, ordered Python-character edit program to the exact
   predecessor raw text without normalizing it;
2. derives content-addressed staged raw and SourceNote artifacts and future
   publication destinations;
3. converts exact edit ranges and role-qualified citations into byte-grounded
   semantic hunks and a complete patch-reconstruction attestation;
4. renders the successor SourceNote and derives successor document and claim
   revisions, preserving stable claim keys and scopes and applying only
   explicit statement rewrites; and
5. derives claim reconciliation and the complete receipt-free managed-plan
   proposal envelope.

For a no-change target, local code binds the exact predecessor projection,
analysis, rationale, and target-evidence citations into the complete
receipt-free no-change envelope. The provider never supplies a filesystem
path, SHA-256, content ID, provenance value, temporal date, claim revision,
hunk identity, attestation, or publication destination.

### Render the successor SourceNote from exact bytes

The versioned Markdown renderer parses the predecessor through the public
frontmatter model, rejects PDF-grounded state, and preserves its document
metadata and stable claim structure. It updates only the deterministic
successor fields, rebuilds the summary from successor claim statements, and
places the exact successor raw UTF-8 after the `## Content` marker. It does not
normalize those raw bytes.

The renderer then reopens its own complete output through the public parser and
requires both model equality and byte-for-byte recovery of the successor raw
content. The materializer builds one canonical, content-addressed projection
validation report for both predecessor and successor. It binds the validator
version, SourceNote schema SHA-256, exact raw and note artifacts, and the full
projected claim revisions. The successor report and complete SourceNote bytes
are staged as locally derived evidence, not provider assertions.

### Add TargetAnalysisBinding v2 without rewriting v1

`TargetAnalysisBinding` v2 separates the two identities that recorded
inference actually has:

- `input_envelope_sha256` binds the generic recorded-inference envelope; and
- `staged_input_sha256` plus `inference_input` bind the exact
  run/target-scoped planning-input artifact.

Plan and no-change validation require the receipt to contain that exact staged
input and to bind the generic envelope separately. The v1 constructor,
validation rules, serializer shape, canonical bytes, and content IDs remain
unchanged; v1 serialization omits the additive field. Existing records are not
silently upgraded. New recorded-planning subjects use v2.

### Stage create-only, inert bytes with a completion marker last

The managed staging repository shares the canonical root and repository ID of
the inference-evidence repository. Every member lives below
`staging/managed-review/<run-id>/`, is verified against its declared SHA-256
and byte count, and is written create-only. A run writes all members first, an
immutable content-addressed manifest after them, and the fixed `COMPLETE.json`
pointer last. Reopening requires the exact manifest, completion pointer, and
every member byte.

The returned capability is process-local, repository-bound, and reopens the
manifest before use. These staged artifacts remain inactive and unservable.
They do not mutate canonical raw or SourceNote files, SQLite review state, an
active-generation pointer, or a search index. A completed or partial staging
orphan left by a later failure is therefore an availability/retention concern,
not knowledge or review authority.

## Compatibility and failure ordering

- Existing classification, dependency, and impact outcome bytes omit the new
  optional revision-planning output and retain their previous identities.
  Task-specific extra input artifacts are accepted only for revision planning.
- Provider and replay configuration, exact all-target coverage, predecessor
  snapshots, contract bytes, citation inputs, and size bounds are validated
  before their relevant side effects.
- A failure before all target outcomes are valid creates no staging manifest
  and no planning evidence batch. Earlier provider calls may exist only as
  transient in-memory failure evidence.
- A crash during staging can leave create-only members or a manifest, but no
  run is complete until the exact completion pointer is present. An exact retry
  may finish the same bytes; a conflicting retry fails closed.
- A failure after staging completion but before evidence-batch completion can
  leave an inert staged orphan. Neither staging nor an inference batch creates
  review authority.
- The successful service result carries both a freshly verified inference
  batch and a freshly verified staging capability. Final subjects must
  reproduce the exact recorded proposal bytes.

## Explicit limits

This decision does **not** create a `ManagedRevisionReviewBundle`, submit a
request to the SQLite managed-review authority, or authorize a human decision.
Returning structurally complete `ManagedRevisionPlan` and
`NoChangeImpactCard` subjects is preparation for that later boundary, not the
boundary itself.

It also does not expand LangGraph, publish staged files, activate a generation,
rebuild or switch an index, add CLI commands, release or deploy v0.3, or claim
that the Milestone 4 loop is closed. Publication, activation, recovery,
indexing, regression, and final audit remain separately authorized work.
