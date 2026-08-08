# ADR 0010: Durable inference lineage and evidence-first temporal proposal authority

- Status: accepted for the v0.3 Milestone 4 temporal-analysis slice
- Date: 2026-08-08

## Context

The revision-2 analysis snapshot, deterministic candidate/workload builders,
recorded classification and dependency inference, and pure temporal proposal
builder can reproduce a proposed revision-3 aggregate in memory. In-memory
objects and caller-supplied hashes are not durable authority, however. After a
process restart, the system must prove that the exact provider inputs, outputs,
receipts, complete selection/exclusion ledgers, SourceNote inventory, and
proposal still exist and still reproduce one another before SQLite accepts the
proposal.

The evidence filesystem and SQLite aggregate store cannot participate in one
atomic transaction. Treating an uncommitted collection of artifact files as a
completed inference batch would expose partial writes. Performing SQLite CAS
before the reproduction evidence was durable would leave an authoritative
revision whose origin could no longer be audited.

## Decision

### Committed inference batches

`FilesystemInferenceEvidenceRepository` owns create-only, content-addressed
artifacts, outcome manifests, receipt bindings, and batch manifests. Each
artifact and manifest is written to a same-directory temporary regular file,
file-synced, hard-linked into its final locator without following symlinks, and
directory-synced. An exact retry re-syncs both an already-present file and its
parent before treating the locator as durable. Bounded, regular crash-left
temporary files are removed under the repository lock and the directory is
re-synced; unsafe or excessive residue fails closed. The batch manifest is the
commit marker and is linked last. A fresh process cannot mint batch authority
from visibility alone: it re-syncs the marker and parent, reopens the complete
batch again, and only then issues its process-local capability.

A receipt becomes replay authority only when its exact outcome, execution, and
receipt-artifact tuple occurs in at least one valid committed batch. Deleting or
corrupting every containing batch marker removes that authority even when
individual artifact and receipt files remain. Overlapping committed batches
are allowed and scanned in canonical order under fixed manifest-count and byte
bounds.

Persisting a REPLAY outcome first resolves an already committed LIVE source.
It revalidates the receipt replay relationship, source execution SHA, task,
input envelope, algorithm contract, provider/model, prompt, response schema,
raw output, and canonical typed output. A LIVE outcome written only as part of
the same not-yet-committed batch cannot authorize REPLAY.

Batch preparation enforces incremental artifact, outcome, manifest, and total
byte ceilings. Runtime inference inputs reject evaluator/golden path fragments
and evaluator-shaped metadata while allowing response-schema field names and
ordinary prose. This is defence in depth; runtime code still cannot import
`mastervault.evals` or read benchmark gold.

The repository requires POSIX `flock`, `dir_fd`, `O_DIRECTORY`, and
`O_NOFOLLOW` semantics and fails before creating its root when they are not
available. This constraint applies to the concrete evidence feature, not to
the complete MasterVault distribution. A future non-POSIX adapter must provide
equivalent create-only, no-follow, durability, and locking guarantees.

### A separate temporal-analysis manifest

`TemporalAnalysisEvidence` remains separate from the compact
`TemporalProposal`. Its canonical identity payload contains:

- the exact revision-2 aggregate and head;
- the exact canonical SourceNote inventory;
- the complete relationship candidate set;
- classification workload and compact result index;
- dependency workload and compact result index;
- the proposed document replacement;
- the exact temporal proposal; and
- classification and dependency batch IDs and SHAs.

The manifest deliberately duplicates bounded input/workload metadata required
for restart reproduction. Provider output shards and raw artifact bytes are not
duplicated; they remain solely in their inference batches. The canonical
manifest is limited to 16 MiB, excludes its derived ID/SHA from its identity
bytes, rejects non-canonical JSON, and uses
`temporal-analysis:<sha256>` as its public identity.

Pure verification reconstructs classification and dependency result sets from
the stored workloads/indices plus reopened output shards, reruns the existing
domain validators and temporal proposal builder, and requires exact proposal
equality. This pure function is not repository authority by itself; the commit
service supplies only outcomes from authenticated durable batches.

### Evidence before SQLite authority

`commit_temporal_proposal` is the only public temporal proposal commit seam. It
requires the concrete SQLite aggregate store, concrete filesystem evidence
repository, exact sealed classification and dependency batch capabilities, and
the repository-backed SourceNote resolver. At commit time it:

1. reopens the exact revision-2 analysis payload from the temporal manifest;
2. freshly resolves every SourceNote and bootstrap binding from repository
   roots;
3. reopens both exact committed inference batches and verifies their sealed
   capabilities against the same evidence repository;
4. reproduces the proposal from those durable outcomes;
5. writes and reopens the content-addressed temporal-analysis manifest; and
6. only then compares and swaps the exact aggregate from revision 2 to 3.

The SQLite operation ID is not caller-selected. It is
`temporal-commit:<temporal-analysis-sha256>`, so the aggregate receipt is
cryptographically linked to the complete reproduction evidence without a new
database migration. Exact retry after a lost acknowledgement returns the
existing revision-3 operation receipt. Reusing different evidence necessarily
produces a different operation ID and proposal binding.

When the aggregate is already the exact revision-3 proposal, retry requires
the previously persisted temporal manifest to remain present and valid; it is
not silently regenerated. This makes deletion or corruption after commit fail
closed. The subsequent human-review request and decision remain SQLite
authority under ADR 0006 and advance the proposal from revision 3 to revision
4 only through complete subject coverage.

## Failure ordering and recovery

There is intentionally no distributed transaction between the evidence
filesystem and SQLite:

- failure before the temporal manifest is durable leaves revision 2 unchanged;
- failure after manifest durability but before CAS may leave an immutable,
  content-addressed orphan with no aggregate authority;
- an exact retry can reuse that orphan and perform the one CAS;
- failure after SQLite commit but before delivery is recovered through the
  derived operation ID and existing store receipt; and
- a CAS race rejects the stale writer while its inert manifest remains safe.

The system never deletes or overwrites an orphan as a recovery convenience.
Retention and garbage collection require a later policy that can prove the
manifest is not referenced by aggregate or review authority.

## Consequences and limits

- Revision 3 now has restart-reproducible inference and SourceNote lineage; it
  is no longer authorized by naked batch strings or transient outcomes.
- No change-control schema migration is required because the existing global
  operation receipt binds the derived manifest operation ID.
- Repository identity includes the resolved evidence-root path. Copying or
  moving a repository does not silently preserve authority; an explicit future
  migration/export protocol is required.
- External deletion is detected and blocks later authority; this slice does not
  claim tamper prevention against a privileged filesystem attacker.
- This step does not adjudicate downstream impact, generate or stage managed
  SourceNote revisions, publish canonical files, activate a generation, update
  the serving index, or add new LangGraph orchestration. Those remain later
  Milestone 4 seams after this synchronous authority is stable.
