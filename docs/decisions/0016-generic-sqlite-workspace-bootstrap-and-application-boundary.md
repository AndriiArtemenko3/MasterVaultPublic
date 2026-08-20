# ADR 0016: Generic SQLite workspace bootstrap and application boundary

- Status: accepted for the v0.3 Milestone 4 SQLite application slice
- Date: 2026-08-12

Follow-on status: the generation-aware read work adds ordinary
`search`/`claims`/`wiki`/`ask` resolution through this façade. The non-goal
below records the boundary of the original ADR 0016 bootstrap slice, not the
current public read surface.

## Context

ADR 0015 provides a restart-safe first managed successor once an exact
generation-zero authority already exists. The prior generation-zero constructor
is intentionally tied to the sealed Larkstead development fixture, however, and
cannot authorize an operator's existing v0.2 workspace. Its fixed inventory and
repository-reviewed metadata are test evidence, not a generic discovery or
trust mechanism.

A real workspace spans several independently durable surfaces: canonical vault
notes, the selected SourceNotes and their provenance, the legacy serving index,
the change-control SQLite database, and the private managed-generation
repository. Those surfaces
cannot share one transaction. Directory discovery, inferred authority metadata,
a self-hashed receipt, or an operator convenience record must not be allowed to
invent authority over them.

Future operator and CLI work also needs one stable library boundary. Letting a
caller orchestrate private store methods or carry process-local HMAC
capabilities would expose implementation details as a public protocol and make
error handling dependent on internal exception classes.

## Decision

### Require an explicit versioned bootstrap manifest

`workspace_bootstrap.py` accepts an operator-supplied, versioned manifest. The
manifest selects the exact managed SourceNotes and supplies, rather than
infers, each selected document's family, version, effective interval, role, and
authority. It binds canonical relative paths, byte counts, and full SHA-256
identities for the SourceNote and governing raw source. Each selection also
names a logical source-root ID, an exact root-relative path, and the
SourceNote's exact opaque provenance. At runtime the caller binds every
non-workspace root through `BootstrapSourceRoot`; absolute runtime locators are
never persisted. Durable authority instead uses a path-safe content address
derived from the logical root, relative path, and provenance. Ordinary v0.2
absolute ingestion provenance is therefore adopted without copying or
rerendering the governing source or its SourceNote. For a PDF-backed note,
the exact SourceNote bytes carry the typed `source_asset` and `parsed_document`
bindings; the normal projection verifier must reopen those referenced bytes.
Every selected managed SourceNote must name exact provenance. Absence is
rejected rather than replaced with a guessed path or hash.

Bootstrap reads only the workspace and explicitly bound source roots and captures each file through a
stable, regular, no-follow read. Paths must be canonical, sorted, unique,
case-unambiguous, within their declared roots, and free of symlink or special
file components. Each evidence file must be owned by the current operator,
have exactly one hard link, and be neither group- nor other-writable. File
counts, individual sizes, and aggregate bytes are bounded. Changed bytes, path
substitution, duplicate identities, unsupported note shapes, malformed
provenance, evaluator/golden paths, unbound external provenance, and external
hard-link aliases fail closed. Runtime root IDs must exactly match the
manifest, and every external root must be an exact owner-controlled,
non-symlink absolute directory disjoint from protected workspace evidence.

The managed selection and indexable inventory are separate contracts. The
manifest selects the SourceNotes that seed change-control authority. Bootstrap
also derives a complete, closed inventory of every indexable note in the
legacy vault, including wiki, decision, and strategy notes and valid
SourceNotes not selected for managed authority. It
never treats the selected subset as the complete serving corpus, silently
skips an invalid note, or walks another root to fill a gap.

The result is a content-addressed `WorkspaceBootstrapIntent` and
`WorkspaceInventoryReceipt`. Their identities bind the manifest, exact
aggregate revision and SHA, complete inventory, managed subset, and all
source/provenance evidence. Absolute workspace and source-root locators are
deliberately not durable identities; the application verifies every configured
root and relative path afresh on each operation. Reusing an operation ID with different
inputs is a conflict; an exact retry must reopen and reproduce the same
evidence.

### Attest the existing SQLite index without rebuilding it

`legacy_index.py` opens the existing legacy SQLite index read-only through a
pinned regular-file descriptor. It rejects symbolic links, path or inode
substitution, journal/WAL sidecars, write-capable opens, failed integrity or
foreign-key checks, and unsupported storage or embedding metadata.

Attestation compares the complete database with the exact closed vault
inventory. It verifies schema and migration identity, document and record
coverage, SourceNote paths and hashes, content and claim bindings, FTS coverage,
vector coverage and dimensions, and deterministic logical row identity. Any
missing, surplus, duplicate, skipped, stale, or mismatched item fails closed.
The resulting `LegacyIndexReadinessReceipt` binds both the physical index file
identity and a path-independent logical fingerprint to the exact workspace
inventory receipt.

This is a read-only adoption boundary. It does not modify, vacuum, migrate,
copy, or rebuild the legacy index and it does not claim that the index is a
managed-generation index. A structurally valid receipt is not sufficient by
itself: a process-local `VerifiedWorkspaceBootstrapCapability` is minted only
after the intent, inventory, and index are freshly reopened together. The
capability is non-serializable and never becomes durable authority.

### Initialize generic generation zero only after exact evidence

Migration `005_workspace_bootstrap_application.sql` stores immutable bootstrap
intents, workspace-inventory receipts, legacy-index readiness receipts, and
operator navigation. It also admits `verified-workspace-bootstrap` as a
generation-zero origin while preserving existing `verified-seed-bootstrap`
rows.

The SQLite authority store initializes generic generation zero in one
`BEGIN IMMEDIATE` transaction only after verifying the freshly minted
bootstrap capability. The generation-zero manifest and origin bind the exact
pre-change aggregate head, bootstrap intent, complete inventory receipt, legacy
index readiness receipt, and active pointer. The origin is evidence for the
existing workspace, not a synthetic managed decision or a claim that the
legacy index was republished.

The application retains descriptor-pinned workspace and legacy-index evidence
guards through that authority transaction. Both guards revalidate inside the
transaction immediately before the authority rows commit and again after the
commit acknowledgement. This is drift detection around the handoff, not an
impossible cross-filesystem-and-SQLite atomic transaction: a non-cooperating
same-UID writer cannot be prevented from changing unrelated files later, and
every subsequent authority use must therefore resolve its exact evidence
afresh.

The application authority database lives below a descriptor-traversed private
directory. Secure creation uses an owner-only directory and file, rejects
symlinks, hard links, unsafe ownership or permissions, pins the directory and
database inode, and checks that SQLite opened that same inode before migrations
and before and after transactions. Status uses a separate descriptor-backed,
immutable, query-only connection: it never creates directories, initializes or
migrates schema, permits writes, or accepts journal/WAL sidecars. Concurrent
initializers converge only after SQLite's cross-process `BEGIN IMMEDIATE`
serialization and full schema/ledger revalidation.

An exact retry returns the same immutable receipts and authority after
reopening all evidence. Different-input operation reuse, a stale aggregate, an
existing conflicting authority, or evidence drift fails closed. The operation
does not change the legacy vault or index. PostgreSQL bootstrap and managed
activation are rejected before any filesystem or authority effect; SQLite
remains the sole authority for this slice.

### Keep operator-run navigation non-authoritative

`operator_run.py` records one content-addressed run anchored to an exact active
authority and append-only typed links to separately authoritative artifacts and
operation receipts. A run link contains only a target identity and SHA. Every
consumer must reopen the referenced artifact at its owning boundary before
trusting it.

Navigation is for status, resumption, and audit traversal only. It cannot
authorize bootstrap, review, publication, index readiness, or activation; it
cannot replace a missing receipt; and it is never LangGraph state. Missing or
incomplete navigation after a crash may be reconciled from durable authority
on exact replay. Navigation is written only after its target authority exists,
so a lost acknowledgement cannot create a forward pointer to an uncommitted
effect.

### Expose one library-level application boundary

`application.py` is the first supported synchronous library façade. This slice
exposes generic workspace bootstrap and durable status/navigation; subsequent
change-control operations must be added to the same boundary rather than
inventing parallel public orchestration. It derives configured roots, applies
backend and root-disjointness preflight, delegates authority to the owning
store and repository boundaries, and returns typed results. Future operator
commands must use this façade rather than private store helpers, fixture
constructors, or process-local capabilities.

`application_errors.py` exposes a stable five-part taxonomy:

- `usage-error` for invalid caller input or configuration;
- `review-required` when an authoritative human decision is still required;
- `conflict-or-stale-authority` for idempotency conflicts, concurrency, or a
  changed authority head;
- `integrity-failure` for corrupt, missing, substituted, or mismatched durable
  evidence; and
- `unsupported-operation` for deliberately unavailable backend, platform, or
  workflow behavior.

The façade maps internal failures to that taxonomy while retaining their cause
chain. These codes are an application contract; internal exception classes,
messages, and HMAC capability representations are not.

### Recover by replaying evidence, never by inferring completion

Bootstrap is a synchronous saga across immutable filesystem evidence and
SQLite authority. Intent, inventory, index readiness, generation-zero
authority, and optional navigation are distinct durable boundaries. A crash
may leave a valid earlier receipt without later authority, but never a partially
trusted generation. Retry reopens every completed stage, rechecks workspace and
index stability immediately before authority creation, and completes only the
missing later stages.

No recovery path deletes or rewrites the operator's vault or legacy index,
guesses metadata, accepts a skipped file, treats an unverified receipt as a
capability, or advances authority from navigation. Platforms that cannot
provide the required stable no-follow file and SQLite guarantees fail with the
unsupported-operation category before authority is created.

## Failure and compatibility properties

- Existing fixture-seeded generation-zero rows remain readable and retain
  their original origin; no generic evidence is fabricated for them.
- Bootstrap never modifies canonical SourceNotes, raw sources, immutable
  assets, parsed artifacts, or the legacy index.
- The managed SourceNote selection is explicit, while index attestation covers
  the complete closed all-note indexable vault inventory.
- Exact retry preserves identities and timestamps; different inputs under an
  existing operation ID fail.
- Workspace or index drift observed during the descriptor-pinned authority
  handoff fails closed; pre-commit drift rolls back and leaves earlier
  immutable evidence inert, while later authority use always requires fresh
  verification.
- Operator-run records improve navigation but confer no authority and can be
  repaired only from authoritative receipts.
- The application error code is stable even when the underlying internal
  exception or implementation changes.

## Explicit non-goals

The ADR 0016 bootstrap slice did not add public change-control or operator CLI
commands or connect ordinary queries to generation-aware serving; the latter
arrived in the read-only follow-on described above. It still does not execute
or score post-activation regression queries, emit final JSON/Markdown audit
reports, expand LangGraph, execute managed `EDIT` decisions, add PostgreSQL
managed bootstrap or generation parity, support multiple operator change
events, implement retention/cleanup, add OCR/UI/background workers, change the
package version, add the keyless public demo, release, tag, publish, or deploy
v0.3.
