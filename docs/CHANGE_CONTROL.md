# Synchronous change-control library

PR20A exposes a synchronous Python library boundary for one generic Markdown
change from an already bootstrapped SQLite workspace through two human review
stages and, when authorized, generation-one activation. There is no shipped
`mvault change` command group in PR20A; that CLI and its rendering/exit contract
are deferred to PR20B.

## Supported surface

Construct `ChangeControlApplication` with the same `Settings` used for the
workspace. The public lifecycle methods are:

| Method | Effect | Result |
| --- | --- | --- |
| `start_change(StartChangeRequestV1)` | Admits source and suite, captures generation-zero baseline, performs recorded analysis, then pauses at temporal review or completes a mechanical no-op | `ChangeRunStatusV1` |
| `list_changes(limit=50, cursor=None, phase=None)` | Read-only deterministic newest-first listing | `ChangeRunPageV1` |
| `get_change_status(run_id)` | Read-only receipt-derived status | `ChangeRunStatusV1` |
| `get_change_review(run_id)` | Read-only current-stage packet | `ChangeReviewPacketV1` |
| `record_change_review(document)` | Records one exact temporal or managed decision and advances synchronously to the next durable boundary | `ChangeRunStatusV1` |
| `activate_change(ActivateChangeRequestV1)` | Reopens all required evidence and attempts the generation-zero-to-one CAS | `ChangeActivationResultV1` |
| `verify_change(run_id)` | Read-only fresh verification of every linked authority | `ChangeVerificationResultV1` |

Existing `bootstrap`, `get_status`, and query-generation APIs remain available.
The lifecycle is deliberately one incoming event, one successor, SQLite only.
That bounded profile owns exactly one bootstrap-backed lifecycle run per
workspace. `list_changes` still provides deterministic keyset pagination and
phase filtering, but populating one workspace with multiple independent or
multi-phase runs is outside PR20A; broader run queues are deferred with the CLI
and orchestration expansion.

When complete classification contains no graph-valid changed-to-older
governing supersession, the run ends as `completed-no-op`. A canonical external
receipt binds the exact generic analysis, classification batch and contract,
configuration, suite, baseline, generation-zero, and base-authority lineage.
No temporal or managed review is invented, and no generation, publication,
index, activation, or active-pointer effect occurs. Exact retry repairs a lost
navigation acknowledgement from that receipt without calling providers again.

```python
from pathlib import Path

from mastervault.change_control import (
    ChangeControlApplication,
    ChangeExecutionModeV1,
    StartChangeRequestV1,
)
from mastervault.config import Settings
from mastervault.models import Domain

settings = Settings()
application = ChangeControlApplication(settings)
status = application.start_change(
    StartChangeRequestV1(
        operation_id="change-2026-08-20-returns",
        source=Path("/operator/inbox/returns-policy-v2.md"),
        domain=Domain.CUSTOMER_SUPPORT,
        regression_suite=Path("/operator/inbox/returns-regression-v1.json"),
        mode=ChangeExecutionModeV1.LIVE,
    )
)
assert status.phase == "awaiting-temporal-review"
```

The paths above are runtime inputs. Pydantic excludes them from serialized
public DTOs; use `model_dump(mode="json")` for a path-free projection.

## Incoming Markdown schema

The source is a regular, non-symlinked UTF-8 Markdown file no larger than
64 KiB and outside the active workspace. Its filename stem must equal
`document_id`. Frontmatter must contain exactly one mapping and no other
top-level keys:

```markdown
---
mastervault_change:
  schema_version: 1
  event_id: returns-window-change-2026-08
  document_id: returns-policy-v2
  document_family: returns-policy
  version_label: v2
  title: Returns policy version 2
  domain: customer-support
  source_type: policy
  declared_effective_from: 2026-09-01
  role: policy
  authority: primary
  operator_intent: Extend the standard return window.
---
The standard return window is 45 calendar days from delivery.
```

`declared_effective_to` is optional and must be later than the start date.
Identifiers are canonical lowercase dot/kebab values. Unknown or duplicate
keys, YAML aliases/anchors/custom tags, unsafe identifiers, invalid UTF-8,
empty bodies, mismatched domain, invalid role/source-type pairs, and answer or
review authority embedded in the source are rejected before writes or calls.

The provider proposes one to ten exact quotations with confidence and affected
concepts. MasterVault resolves them locally to unique character and UTF-8 byte
spans. Quotes must be exact, unambiguous, complete single sentences of at most
512 UTF-8 bytes. A paraphrase or repeated ambiguous quotation is invalid.

## Regression-suite schema

The suite is strict UTF-8 JSON without a BOM, duplicate keys, non-finite
numbers, unknown fields, or coercion, and is limited to 1 MiB and 1–128 unique
cases. Case ordering is canonicalized by `case_id`; both original and canonical
SHA-256 identities are bound.

```json
{
  "schema_version": 1,
  "suite_id": "returns-window-v1",
  "suite_version": 1,
  "cases": [
    {
      "case_id": "ask-current-window",
      "role": "targeted",
      "kind": "ask",
      "query": "What is the current return window?",
      "domain": "customer-support",
      "max_rounds": 2,
      "budget_usd_micros": 250000
    },
    {
      "case_id": "search-unrelated-shipping",
      "role": "control",
      "kind": "search",
      "query": "shipping delay escalation",
      "k": 5,
      "record_types": ["claim", "structural"],
      "rerank": false
    }
  ]
}
```

Suites cannot contain expected answers, scores, grading rules, patches, review
decisions, or evaluator instructions. Baseline outputs are evidence, not an
embedded oracle. Ask baseline cases run with `persist_run=False`.

## Review documents

Call `get_change_review(run_id)` at each pause and copy the exact `run_id`,
stage, request ID/SHA, and every subject ID/SHA into the decision. Temporal
choices are `accept` or `reject`. Managed plan choices are `approve` or
`reject`; no-change cards require `confirm-no-change` or `reject`. An
adoption-only managed packet has no subjects and uses an explicit
`adoption_choice` of `adopt` or `reject`. `EDIT` is not supported.

```json
{
  "schema_version": 1,
  "run_id": "operatorrun:<64 lowercase hex characters>",
  "stage": "temporal",
  "request_id": "reviewreq:<64 lowercase hex characters>",
  "request_sha256": "<64 lowercase hex characters>",
  "operation_id": "review-returns-temporal-v1",
  "reviewer_id": "operator@example.com",
  "rationale": "The effective-date relationships match the signed policy.",
  "decisions": [
    {
      "subject_id": "tempc:<64 lowercase hex characters>",
      "subject_sha256": "<64 lowercase hex characters>",
      "subject_kind": "temporal-constraint",
      "choice": "accept"
    }
  ]
}
```

Instantiate the stage-specific frozen DTO
(`TemporalReviewDecisionDocumentV1` or
`ManagedReviewDecisionDocumentV1`) and pass it to `record_change_review`.
Decision arrays must cover the exact packet and remain canonically ordered;
the `.create(...)` helpers sort valid items. A stale packet or operation-ID
reuse with different bytes is a conflict.

## Lifecycle and retry behavior

Phases are receipt-derived:

`bootstrapped -> awaiting-temporal-review -> awaiting-managed-review -> ready-to-activate -> activated`

`rejected-no-op` and `completed-no-op` are terminal. Full rejection creates no
generation effects. Accepted governing-source adoption with no downstream
revision still creates the required adoption-only generation; mixed approval
publishes only approved successors.

Use a stable `operation_id` for retries. Exact retries reopen immutable
evidence and preserve IDs and original timestamps. Reusing an operation ID for
different input, changing active authority, or racing another activation
raises `conflict-or-stale-authority`. Navigation is not authority and can be
repaired only after its owning receipt exists.

LIVE calls are journaled around the provider boundary, but the library cannot
prove exactly-once execution if a remote provider accepted a request and the
process died before recording its result. Such an indeterminate call fails
closed as a conflict; it is not automatically called again. REPLAY requires a
complete exact bundle bound to committed LIVE evidence and the current
configuration. It is offline and never falls back to a provider. REPLAY cannot
manufacture authority for an ambiguous, uncommitted LIVE call.

## Public output and error contract

All public DTOs are strict, frozen, schema-versioned models. Unknown fields,
coercions, duplicate JSON keys, and non-finite JSON numbers are rejected by
their JSON validators. Public status, list, review, activation, and verification
projections contain content identities, bounded values, and safe relative
locators. They exclude absolute workspace/source roots, runtime `Path` values,
secrets, raw provider payloads, evaluator expectations, random run IDs, timing,
and textual execution traces.

Catch `ChangeControlApplicationError` and branch on its stable `code`:

| Code | Meaning |
| --- | --- |
| `usage-error` | Invalid caller input or configuration; an unavailable review packet is also usage |
| `review-required` | A human decision is required before the requested action can continue |
| `conflict-or-stale-authority` | Idempotency mismatch, concurrent/stale authority, or indeterminate provider boundary |
| `integrity-failure` | Missing, corrupt, substituted, or mismatched durable evidence |
| `unsupported-operation` | Deliberately unsupported platform, backend, or lifecycle behavior |

Messages and internal exception classes are not stable machine contracts.

## Configuration, platform, and security

Generic lifecycle operations require an existing generic bootstrap and
`query_generation.bootstrap_manifest`. External roots are supplied through
`query_generation.source_roots`. The active workspace comes from
`paths.workspace`; state, evidence, and generation roots are derived beneath
its private `change_control` directory. Environment overrides use the existing
`MV_` prefix and `__` nesting, for example
`MV_QUERY_GENERATION__BOOTSTRAP_MANIFEST`.

Managed lifecycle authority supports SQLite on POSIX only. PostgreSQL and
unsupported platforms are rejected before filesystem creation, database
writes, provider calls, or navigation links. Ordinary unmanaged PostgreSQL
query support is unchanged.

Source, suite, workspace, index, evidence, and generation files are reopened
through bounded no-follow, ownership, mode, link-count, descriptor, hash, and
identity checks at their owning boundary. Roots must be disjoint where the
authority contract requires it. Never place incoming source or regression
suite files inside the active workspace, and never treat a safe relative
locator or operator-run link as sufficient authority without reopening the
referenced receipt.

Evaluation expectations remain outside runtime evidence and inference prompts.
Regression suites describe workloads only; they cannot carry expected answers,
graders, patches, or decisions. The application records sanitized provider
contracts, while local validation and immutable receipts decide authority.

## Deferred from PR20A

The `mvault change bootstrap|start|list|status|review|activate|verify` CLI,
stable command JSON/error rendering, CLI exit codes, and subprocess acceptance
tests belong to PR20B. PR20A does not claim those commands are available. Also
deferred are managed `EDIT`, final grading/reports, PostgreSQL managed parity,
PDF/OCR admission, a second successor, retention, workers/UI, release, and
deployment.
