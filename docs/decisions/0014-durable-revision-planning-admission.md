# ADR 0014: Durable revision-planning admission and governing-source adoption

- Status: accepted for the v0.3 Milestone 4 managed-review admission slice
- Date: 2026-08-09

## Context

ADR 0013 produces a complete recorded revision-planning batch and an inert,
manifest-last staging run. Its returned process-local staging capability is
useful during that process, but it is deliberately not durable authority. A
later managed-review request may be opened after restart, so it must prove that
the exact planning inputs, provider evidence, locally derived proposals,
review subjects, and staged bytes still form one complete set.

The legacy `ManagedRunBinding` is already persisted in released v0.2 review
records. Changing its serializer or content ID would invalidate those records.
The next boundary therefore needs an additive run-binding version and a
dependency-neutral, content-addressed admission record.

Repository resolution also cannot trust a structurally valid bundle. Review
authority depends on approved inference contracts, exact impact evidence,
canonical predecessor bytes, grounded citations, reproducible patches, and a
deterministically rendered successor SourceNote.

The admitted run also contains the newly reviewed governing source itself. A
managed decision that revised only downstream documents but omitted that
source from the authorized generation would produce an internally consistent
overlay over the wrong knowledge base. Conversely, copying or rerendering the
governing raw file and SourceNote as ordinary patch publications would invent
new identities for bytes that were already reviewed in place.

## Decision

### Preserve v1 and add an explicit v2 authority

`ManagedRunBinding` v1 is unchanged byte-for-byte. `ManagedRunBindingV2` adds
one `ManagedRevisionPlanningAdmissionBinding` and requires its run, analysis
set, and inference contract to equal the run binding's exact values. A frozen
v1 fixture protects both the existing content ID and canonical JSON digest.

The admission is content-addressed and binds:

- repository and run identities;
- the complete revision-planning workload;
- the exact v2 managed analysis set, including durable Step 10 evidence;
- one committed planning inference batch and its ordered membership;
- one completed staging manifest and completion pointer;
- every target's input, output, execution, receipt, subject, and document
  identity; and
- the complete ordered staged-artifact set for every target.

Missing, duplicate, surplus, cross-run, cross-repository, or cross-task members
fail closed. `NO_WORK` has no batch or staging authority and cannot be admitted
to managed review.

### Reopen from durable evidence, never the process capability

`bind_recorded_revision_planning_run` does not convert the in-memory result
directly into authority. It freshly reopens the committed inference batch and
the manifest-last staging completion from their repository roots. The public
reopen path first canonical-roundtrips the admission model so that Pydantic
`model_copy` or `model_construct` cannot bypass identity validation.

The corresponding Step 10 batch is reopened first. Every exact impact input
and output shard is treated as the durable selected-output universe: decisions
are re-grounded against their questions and SourceNote, the result index is
reproduced, `UNRESOLVED` blocks the set, and affected/no-change eligibility is
derived again for every output. Admission does not introduce a second persisted
copy of the complete Step 10a workload index or exclusion ledger; their already
bound workload ID/SHA remains fixed while this boundary closes the narrower
Step-10-batch-to-planning projection.

The planning workload is then independently reconstructed with the frozen ADR
0013 derivation. `governing-evidence` is canonical JSON over the freshly
reopened questions and decisions, `target-evidence` is the exact reopened
SourceNote UTF-8, and target, question, output-shard, and required-response-kind
fields come only from the derived eligibility. Exact reconstructed input bytes
and the complete workload must equal the staged planning inputs before raw
provider output is parsed or any proposal is rematerialized. The retained
analysis-set IDs must equal the admission's complete content-addressed analysis
set, and predecessor claim revisions come from the same reopened impact input.

For every target, the accepted raw provider output is parsed and semantically
validated again. Local materialization then runs from that exact response,
planning input, impact claims, recorded input artifacts, and analysis set. The
reconstructed typed output, plan or no-change subject, and every staged byte
must equal the admitted output and manifest members. Merely rehashing a forged
subject or proposal is insufficient.

### Resolve managed-review evidence through typed repository roots

`RepositoryBackedManagedReviewResolver` is the production implementation of
the store's repository resolver protocol. The operator supplies an explicit
set of approved inference contracts together with exact algorithm-manifest
bytes. Both the impact and revision-planning executions must use one exact
contract present in that set. Contract and admission inputs are
canonical-roundtripped before use.

Artifact resolution is based on the pair `(kind, path family)`, not a path
prefix alone:

- canonical raw sources and SourceNotes resolve only below the verified
  canonical repository root and cannot use reserved evidence roots;
- inference inputs, outputs, and receipts resolve only from their matching
  evidence path families; and
- staging artifacts resolve only as exact members of one configured completed
  admission.

Both the inference and staging repositories expose narrow public exact-member
open operations. They retain the existing no-follow, bounded-read, SHA-256,
byte-count, repository-lock, and completed-manifest checks; callers do not gain
a generic filesystem reader.

### Revalidate semantic evidence, not only content IDs

Impact evidence reopening recovers every exact `ImpactInferenceShard`, proves
the workload/input/document bindings, reconstructs each decision against the
complete SourceNote, and validates quotes, character offsets, optional context
IDs, dispositions, output refs, and the final result index. It also proves that
the accepted raw provider response is exactly the source of those typed
decisions.

Managed plans reopen every cited artifact and reproduce the ordered byte patch
against the exact predecessor. Projection validation checks raw/note receipts,
provenance, claims, scopes, schema and validator identities, and the canonical
validation report. For v2 plans, that is followed by a stronger full-document
check: the frozen ADR 0013 renderer is rerun from the predecessor SourceNote,
successor raw bytes, successor date, publication path, and the exact
reconciliation-derived statement rewrites. The complete proposed SourceNote
bytes must match, including title, tags, status, dates, summary, content, and
frontmatter serialization.

### Adopt the exact reviewed governing source

`ManagedRunBindingV2` also carries one
`ManagedGoverningSourceAdoptionBinding`. It binds the exact reviewed incoming
event and bootstrap, manifest and alignment attestation, claim-evidence digest,
document metadata, immutable raw and SourceNote artifacts at their original
manifest paths, SourceNote snapshot, reviewed inventory and aggregate head,
and the exact temporal decision that authorized that reviewed state. The
evidence-repository identity is distinct from the content-derived source-root
locator; neither is accepted as authority without fresh repository resolution.

The production resolver derives the adoption from the exact verified bootstrap
and reviewed temporal authority. It then rederives and byte-opens it at managed
request creation, read, decision, and replay boundaries. Missing allowlists,
foreign repository lineage, moved paths, changed manifests, raw bytes,
SourceNote bytes or provenance, and mismatched review authority fail closed.
The adoption is read-only: it neither copies nor rerenders the governing
source, and it grants no publication or activation authority.

### Authorize an overlay-v2 manifest without changing the storage envelope

An accepted v2 decision produces one
`ManagedGenerationManifestBindingV2` using
`content-addressed-overlay-v2`. Its content hash covers the prior generation,
the exact governing-source adoption, and a separate ordered publication delta
containing only approved downstream revisions. An accepted v2 bundle therefore
requires activation even when every retained target is a no-change card: the
new generation still adopts the governing source. An all-rejected bundle
authorizes no change and retains the legacy v1 no-op manifest. Mixed accepted
bundles combine exactly one adoption with the approved downstream publication
overrides; governing-source artifacts never appear as synthetic overrides.

The v1 run and generation-manifest models remain unchanged. Persisted union
consumers discriminate their nested model explicitly by `schema_version`, so
missing, unknown, or contradictory versions fail closed. The SQLite
`change_control_generation_manifests.payload_schema_version` column remains the
existing storage-envelope version `1`; the canonical JSON payload inside that
envelope carries nested generation-manifest schema `1` or `2`. Both versions
retain `manifest_kind = managed-overlay`. No migration or reinterpretation of
existing rows is required.

## Failure and compatibility properties

- Existing v1 run-binding bytes, IDs, and read paths remain unchanged.
- New v2 bindings fail before request persistence if any approved contract,
  durable batch, staging member, analysis join, subject identity, citation,
  patch, or SourceNote rendering cannot be independently reproduced.
- Process-local capabilities are never serialized or accepted as restart
  authority.
- Staged files remain inert. Admission does not publish them, activate a
  generation, update canonical knowledge, rebuild an index, or authorize a
  human decision.
- Managed review now persists and decides exact v2 requests, while SQLite
  remains the sole decision authority and LangGraph checkpoints remain
  disposable reconciliation state.
- This additive extension requires no database schema migration: the storage
  envelope remains schema 1 while nested run and generation-manifest payloads
  are explicitly discriminated as v1 or v2.

## Explicit non-goals

This slice does not publish files, execute or recover activation, build or
switch a serving index, run post-activation release regressions or final audit
reporting, add operator CLI/UI commands, release, or deploy v0.3. LangGraph is
limited to synchronous wait/reconciliation around an already-authoritative
SQLite review and does not orchestrate publication or activation.
