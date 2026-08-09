# ADR 0012: Managed revision-planning authority and pure wire freeze

- Status: accepted for the v0.3 Milestone 4 managed-review contract slice
- Date: 2026-08-09

## Context

ADR 0011 makes actual-impact inference durable, but the existing managed-review
contract carried only an unqualified impact-result SHA. That was insufficient
authority for a new review request: a restart must prove the exact committed
batch, complete Step 10b result, and per-document output that selected each
affected revision plan or explicit no-change card. At the same time, legacy v1
managed bundles already have frozen content-derived identities and must remain
readable byte for byte.

The next provider boundary is revision planning, not publication. A model may
suggest semantic text changes, but it must not author repository paths, hashes,
provenance, derived identities, temporal metadata, scopes, confidence, or a
complete SourceNote projection.

## Decision

Add a dependency-neutral `ManagedImpactAnalysisEvidenceBinding`. It binds the
evidence-repository ID, committed batch ID/SHA and canonical member triples,
the exact workload and result IDs/SHAs, and one ordered output reference per
document. `bind_recorded_impact_inference_run` can construct it only from a
non-empty verified `RecordedImpactInferenceRun`; the truthful empty-workload
path has no batch and cannot open managed review.

`ManagedAnalysisSetBinding` remains one model with additive schema versions.
Legacy v1 input omits `impact_evidence` on serialization and retains its exact
canonical bytes and IDs. The legacy factory still reproduces v1 for reading and
compatibility tests. The new factory emits v2 and derives its impact-result SHA
from the durable evidence binding.

A v2 review bundle must cover every bound output document exactly once. The
target analysis SHA must equal that document's output-shard SHA;
`AFFECTED` requires a `ManagedRevisionPlan`, `NO_CHANGE_REQUIRED` requires a
`NoChangeImpactCard`, and `UNRESOLVED` fails closed. Before create, read,
decision, or replay authority, the SQLite store asks its injected repository
resolver to reopen the exact evidence binding. Substitution fails before any
new authority is accepted. Legacy v1 bundles still parse, but cannot cross this
new store boundary.

Freeze a separate pure planning contract. Eligibility is selected locally from
the complete Step 10b result: an empty result is `NO_WORK`, any unresolved
document blocks the whole set, and every remaining target is fixed as either
affected-revision or no-change. Provider responses are strict and bounded:

- affected responses contain ordered, non-overlapping Python-character ranges,
  replacement text, citation selectors, and optional statement-only rewrites
  of existing stable source-claim keys bound to exact edit ordinals;
- no-change responses contain a bounded rationale and citation selectors;
- target/question IDs and selector names must match locally supplied values;
  workload, result, input-shard, and output-shard IDs must reproduce their
  bound SHA-256 values; ranges are checked against exact Unicode strings and
  unknown fields fail.

Citation selectors resolve only through a frozen local allowlist whose entries
have one explicit role: `governing-evidence` or `target-evidence`. Every cited
slice must contain non-whitespace text. Each affected edit needs governing
evidence; an explicit no-change response needs target evidence. A replacement
that exactly equals its predecessor slice is invalid, including an empty
replacement over an empty insertion range. Claim-statement rewrites use the
same NFKC, canonical-whitespace, and eight-character minimum as
`VersionedClaimRevision.statement`.

The pure module performs no I/O, provider call, staging, rendering, review
creation, or orchestration.

## Compatibility and limits

No SQLite migration or migration-003 change is required: v2 remains canonical
JSON inside the existing managed-review payload columns. The frozen v1 analysis
and complete bundle hashes are regression-tested.

This decision does not implement the sibling recorded-inference runner,
materialize raw or SourceNote revisions, create review requests, expand
LangGraph, publish files, activate an index generation, add CLI commands, or
release v0.3. Those later seams must derive every authoritative artifact and
identity locally from reopened bytes and the accepted semantic intent.

## Failure and recovery

- Empty or unresolved impact results fail before planning side effects.
- Missing, duplicate, substituted, or incompletely covered durable impact
  evidence fails before managed-review authority.
- Extra provider fields, wrong target kinds, unknown selectors or claim keys,
  malformed or mismatched content IDs, whitespace-only evidence, inappropriate
  citation roles, invalid Unicode-character ranges, no-op or overlapping edits,
  non-canonical claim statements, and dangling edit ordinals fail during pure
  validation.
- Existing v1 records remain readable; they are never silently upgraded or
  accepted as new v2 authority.
