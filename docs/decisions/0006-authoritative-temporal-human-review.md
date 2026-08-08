# ADR 0006: Authoritative human review for temporal aggregate transitions

- Status: accepted for the v0.3 Milestone 4 review seam
- Date: 2026-08-07

## Context

`ChangeControlAggregate` stores proposed and reviewed document replacements and
temporal constraints, but aggregate CAS alone cannot prove who reviewed a
proposal, what exact subject snapshot they saw, or which outcome they chose.
Workflow checkpoints are execution recovery data and cannot supply that domain
authority. The existing file-backed `ReviewQueue` governs canonical Markdown
patches; it does not represent temporal/relation review subjects.

## Decision

Store temporal human-review requests and immutable decisions as companion audit
records in `<workspace>/change_control/state.sqlite3`. They are not fields in
`ChangeControlAggregate` and do not contribute to its hash. A future LangGraph
runtime must use a separate `<workspace>/change_control/checkpoints.sqlite3`;
checkpoint rows never become authoritative review state and must never be added
to the attested state database.

A request binds an exact aggregate ID, base revision, aggregate SHA, canonical
full base aggregate JSON, and a non-empty canonical list of proposed
`DocumentReplacementAssessment` and/or `TemporalConstraint` snapshots. The
store resolves every subject from the locked head itself. The deterministic
request ID hashes the base binding and each ordered subject kind, stable ID,
and full snapshot SHA. Requester, rationale, operation ID, and store-generated
UTC timestamp are audit metadata rather than request identity; their dedicated
payload SHA detects conflicting metadata. Exact operation replay returns the
original timestamp. Overlapping requests for one subject at one revision fail.

Lifecycle is derived rather than stored: an undecided request is `open` only
while revision and aggregate SHA still match its base, otherwise it is `stale`;
an immutable decision makes it `decided`. The natural decision key is the
request ID because the schema permits exactly one decision per request. No
second synthetic decision ID is introduced.

One decision contains exactly one canonically ordered outcome for every
requested `(kind, stable ID, original subject SHA)`. Outcomes may be accepted,
edited, or rejected independently in one atomic batch. Accepted and rejected
outcomes change status only. Edited outcomes finish accepted and may change
document-replacement rationale/confidence or temporal-constraint rationale;
stable identity, endpoints, target, bound, and bases cannot change, and every
edit must be real. The store mechanically builds one final aggregate and fully
revalidates it before persistence. This permits accepting a document
replacement with its dependent document constraint in one transition, while
rejecting a constraint-only acceptance whose replacement remains proposed.

Request creation and decision each own one `BEGIN IMMEDIATE`. Each also owns a
globally unique entry in the existing operation receipt ledger: a request is a
same-head `changed=0` receipt and a decision is the base-to-base+1 aggregate
transition. Dedicated request/decision intent SHAs prevent equal aggregate
hashes from hiding different human metadata. Exact review replay is checked
before stale/already-decided checks and remains valid after later revisions or
a lost acknowledgement. Aggregate rows, the decision, ordered outcomes, and
the generic receipt commit or roll back together.

Public aggregate CAS cannot add reviewed-state subjects, advance proposed
subjects to reviewed state, alter/remove an open-request subject, or mutate an
already reviewed subject. Only the private decision transition path can do so.
In this release, every generic aggregate create/CAS payload must contain only
proposed review subjects; there is no bootstrap or import exception for
accepted/rejected state. Only the private decision transition may create any
reviewed state.

Migration `001` remains byte-for-byte frozen. Additive migration
`002_authoritative_human_review.sql` creates normalized request, subject,
decision, and outcome tables with strong keys, foreign keys, uniqueness, and
shape checks. Full aggregate JSON in review rows is an immutable audit snapshot,
not a live aggregate shadow. Every read revalidates canonical JSON, digests,
ordinals, receipt bindings, the exact subject/base binding, and the permitted
base-to-result diff. Pre-`002` aggregate receipts remain digest-only; no
historical review or aggregate snapshots are invented or backfilled.

## Boundaries and consequences

- `state.sqlite3` is the only authority for temporal/relation subject review.
- The legacy file `ReviewQueue` remains the only authority for canonical-file
  patch actions. The two systems must never decide the same action. A later
  `mvault review` facade may present both typed action kinds without merging
  their persistence authority.
- Review records survive later full aggregate replacements and are not copied
  into the aggregate hash.
- Actor IDs use bounded NFKC-normalized conservative ASCII; rationales are
  bounded, non-empty, and canonical; timestamps are canonical UTC generated by
  the store.
- Candidate analysis, review UI/CLI integration, LangGraph orchestration,
  canonical-file apply, reindex coordination, PostgreSQL persistence, and
  cross-store saga/outbox recovery remain out of scope.
- `mvault demo reset` removes the whole demo `change_control/` directory so
  neither authoritative decisions nor future disposable checkpoints survive a
  promised pristine reset. Index-only admin reset remains unchanged.
