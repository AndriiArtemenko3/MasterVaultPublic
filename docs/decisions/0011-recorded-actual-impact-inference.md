# ADR 0011: Recorded actual-impact inference from committed evidence

- Status: accepted for the v0.3 Milestone 4 actual-impact execution slice
- Date: 2026-08-09

## Context

Step 10a produces an exact reviewed `ImpactWorkload`, and Step 10b defines a
pure grounded `ImpactResultSet`, but neither contract executes a provider or
establishes durable lineage. Provider output cannot be allowed to author
SourceNote paths, hashes, quotes, summaries, or content-derived identities.
Likewise, an in-memory typed output is not restart authority: actual-impact
results must be reproducible from an existing committed inference batch.

The evidence repository deliberately forbids empty batches. A reviewed change
can legitimately yield no governing roots and therefore no impact questions,
so inventing an empty provider receipt or sentinel outcome would misrepresent
work that never occurred.

## Decision

Extend the existing recorded-inference task vocabulary with `IMPACT`. The wire
response contains only the exact question ID, one frozen impact disposition,
character-offset pairs, optional exact attention/dependency context IDs, and a
bounded rationale. All wire models forbid additional fields. Character offsets
index Python Unicode characters in the exact `source_note_utf8` carried by the
input shard; they are not UTF-8 byte offsets.

MasterVault locally resolves every offset pair into `DocumentSpanReference`
using the shard's exact document ID, SourceNote path, SourceNote SHA, and sliced
quote. It validates body boundaries and exact slicing, validates context IDs
against the question, and derives question bindings, decision IDs, output-shard
IDs, document disposition, and hashes locally. `AFFECTED` still requires at
least one exact body span. The provider cannot supply confidence or any derived
identity.

LIVE execution reuses the existing bounded request, truthful provider receipt,
and one schema/semantic correction retry. REPLAY makes no provider call and
requires one independently committed prior LIVE receipt with the exact task,
contract, input envelope, raw output, and locally reconstructed canonical
output. Replay chains remain invalid.

The synchronous impact workload seam validates Step 10a, executes exactly one
recorded inference per input shard, and persists all non-empty outcomes as one
ordinary evidence batch. It then freshly resolves and verifies the committed
batch and reconstructs Step 10b only from those reopened typed outputs. Exact
input-shard coverage and `validate_impact_results` are required before the
result is returned.

A zero-question workload returns the canonical empty Step 10b result. It makes
no provider call, creates no outcome or receipt, and does not call batch
persistence. Its absence of a batch is truthful evidence that no inference was
needed.

## Compatibility and limits

`RecordedInferenceOutcome` gains an impact output while retaining the existing
classification and dependency fields. When the new field is absent, its model
serializer omits it so previously committed v1 outcome bytes, outcome hashes,
receipt bindings, and batch manifests reopen exactly. The reviewed-snapshot
binding is moved to a dependency-neutral module without changing its public
class or serialized payload, preventing the new typed output from creating a
repository import cycle.

Temporal proposal execution references remain classification/dependency-only
and reject `IMPACT` explicitly. No impact-analysis manifest, SQLite migration,
review authority, revision plan, staging area, publication, activation, index,
LangGraph workflow, CLI, provider adapter, or evaluation integration is added
by this decision. A later authority may bind the impact result and evidence
batch into managed revision review without changing this execution contract.

## Failure and recovery

- Invalid membership, contract bytes, or oversized requests fail before a
  provider call.
- One invalid semantic response may be corrected once; two failures return
  bounded rejected evidence in memory and commit no batch.
- A crash before the batch marker leaves no committed impact authority.
- Missing, corrupt, substituted, mixed-task, duplicate, or incomplete reopened
  evidence fails before result construction.
- Deleting every valid containing batch marker removes replay and reconstruction
  authority even if individual artifacts remain.
- A fresh process may reopen the batch and remint capability through the
  existing repository protocol.
