# ADR 0017: Synchronous generic change admission and lifecycle authority

- Status: accepted for the PR20A SQLite library lifecycle
- Date: 2026-08-20

## Context

ADRs 0014–0016 establish reviewed managed revisions, immutable generation
publication, generic workspace bootstrap, and generation-aware reads. They do
not admit an ordinary operator-authored change document, bind a pre-change
regression suite and generation-zero observations to that event, or provide a
single resumable application operation spanning both required human reviews.

The older SL2/Larkstead path remains useful as sealed compatibility evidence,
but its fixture-specific identifiers cannot define public admission. Likewise,
a model response, workflow checkpoint, navigation link, or filesystem path
cannot become authority merely because it is convenient to resume from it.

## Decision

### Admit generic Markdown before effects

The application accepts one regular, non-symlinked UTF-8 Markdown file outside
the active workspace, bounded to 64 KiB. Its YAML frontmatter contains exactly
one `mastervault_change` mapping with schema version 1 and the event, document,
family, version, title, domain, source type, effective dates, role, authority,
and operator-intent fields. Unknown or duplicate keys, aliases, anchors,
custom tags, unsafe identifiers, an empty body, a filename/document mismatch,
an incompatible role/source type, or a domain mismatch fail before a durable
write or provider call.

The generic extraction contract asks a provider for one to ten exact source
quotations, confidence, and affected concepts. The provider does not choose
spans or authority. Local code uniquely resolves each quotation to character
and UTF-8 byte offsets, enforces one complete sentence and the 512-byte atomic
evidence limit, and rejects absent, paraphrased, or ambiguous duplicate text.
Only those verified spans can produce the deterministic SourceNote projection
and generic analysis binding. The fixture-specific v1 contract is retained;
the generic path uses new capabilities and downstream unions rather than
weakening old validators.

The raw source and deterministic projection are copied create-only into the
private evidence repository. Durable and public contracts contain logical
identities, hashes, byte counts, and safe relative locators—not runtime roots.

### Bind one strict regression suite and generation-zero baseline

Before analysis, the application admits one strict JSON `RegressionSuiteV1`:
one to 128 unique cases, normalized by `case_id`, each explicitly `targeted` or
`control` and either `search` or `ask`. Search cases bind `k`, sorted unique
record types, and `rerank: false`; ask cases bind `max_rounds` and an integer
micro-dollar budget. Expected answers, graders, patches, reviews, and decisions
are forbidden. BOMs, duplicate keys, coercions, unknown fields, non-finite
numbers, and inputs over 1 MiB fail closed. Both the original-input hash and
the canonical validated-model hash are retained.

Baseline capture is allowed only while exact generic generation zero is active:
authority revision zero, bootstrap aggregate revision one, complete workspace
inventory, and matching legacy index readiness. Cases execute through the
public retrieval pipelines; ask uses `persist_run=False`. Per-case evidence is
canonical and path-free and excludes timings, random run IDs, text traces,
secrets, evaluator expectations, and raw provider envelopes. A COMPLETE
manifest and SQLite receipt are sealed only after exact case coverage,
generation revalidation, and artifact reopening. Baseline creation or backfill
after generation one becomes active is forbidden.

Activation reopens the suite, every case result, the COMPLETE manifest, the
baseline receipt, source evidence, and generation-zero history. Baseline
validation occurs immediately before and again within the same
`BEGIN IMMEDIATE` transaction as the authority compare-and-swap.

### Derive lifecycle from evidence and two sequential reviews

The public state machine is derived from immutable receipts and review
decisions:

`bootstrapped -> awaiting-temporal-review -> awaiting-managed-review -> ready-to-activate -> activated`

The terminal alternatives are `rejected-no-op` and `completed-no-op`. There is
no mutable phase flag with independent authority.

Temporal review accepts or rejects every exact document-replacement or
temporal-constraint subject. Managed review then approves revision plans,
rejects targets, or confirms no-change cards. Each decision binds its stage,
request ID and SHA, reviewer, rationale, and the identity and SHA of every
subject. `EDIT` is unsupported at both stages.

A full temporal rejection or fully rejected managed bundle is a true no-op:
no generation repository, publication, index, activation receipt, or pointer
advance. An accepted governing source with no downstream revision creates an
adoption-only generation. Mixed approval adopts the governing source and
publishes only approved successors. An exact operation retry returns the
original immutable identities and timestamps; reuse with different input is a
conflict. Navigation is appended only after its owning receipt and is
repairable from authority after a lost acknowledgement.

### Make the application façade the orchestration boundary

`ChangeControlApplication` exposes strict frozen v1 DTOs and synchronous
`start_change`, `list_changes`, `get_change_status`, `get_change_review`,
`record_change_review`, `activate_change`, and `verify_change` methods while
retaining bootstrap, status, and query compatibility. Runtime `Path` fields on
the start request are excluded from serialization. List, status, review
presentation, and verification are read-only: they do not create directories,
migrate, checkpoint, repair navigation, or call a provider.

The five stable application error codes are `usage-error`, `review-required`,
`conflict-or-stale-authority`, `integrity-failure`, and
`unsupported-operation`. Internal exceptions and messages may evolve; callers
must branch on the code.

Managed lifecycle writes and activation require SQLite on a supported POSIX
platform. PostgreSQL or an unsupported platform is rejected before repository,
database, provider, or navigation effects. Absolute configured roots remain
runtime inputs and are revalidated against content-bound evidence at each
authority boundary.

### Treat provider replay ambiguity truthfully

LIVE calls are preceded by a durable, content-bound call claim and followed by
a sanitized result receipt. Provider output remains a proposal until local
validation and the owning evidence repository commit it. Raw SDK envelopes are
not durable authority.

There is an unavoidable boundary if a process loses acknowledgement after a
remote provider may have accepted a request but before a durable local result
exists. The library does not claim exactly-once remote execution and does not
guess whether the call completed. That indeterminate claim fails closed as a
conflict requiring operator resolution. Exact REPLAY is offline: a complete
bundle must name the previously committed LIVE evidence and current
configuration, and no provider fallback is permitted. Thus replay can
reproduce committed authority, but cannot retroactively resolve an ambiguous
uncommitted provider call.

## Consequences

- One event advances only generation zero to generation one; a second successor
  remains unsupported.
- The sealed SL2/Larkstead path remains readable and testable but is not the
  public generic admission contract.
- Human review authority remains explicit and sequential; deterministic test
  decisions are fixtures, not representations of human review.
- Filesystem artifacts are immutable evidence, SQLite receipts and CAS rows own
  lifecycle authority, and operator navigation remains non-authoritative.
- Public `mvault change` commands are intentionally deferred to PR20B. PR20A
  ships a library API, not a CLI contract.

## Explicit non-goals

This decision does not add managed `EDIT`, grading or final reports, PDF/OCR
admission, PostgreSQL managed parity, multiple incoming events, a second
successor, background workers, UI, retention, a package-version change,
release, tag, publication, deployment, or the PR20B command-line surface.
