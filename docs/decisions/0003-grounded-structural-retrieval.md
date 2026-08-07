# ADR 0003: Grounded structural retrieval from schema-v2 IR

- Status: accepted for v0.3 Milestone 3
- Date: 2026-08-07

## Context

ADR 0002 stops at parser-neutral schema-v2 artifacts and exact block/cell
grounding. Flattening a table into ordinary Markdown chunks loses row scope:
retrieval can surface a value such as `45 days` without the header that says
what it measures. The next knowledge-change milestone needs evidence that can
be resolved back to immutable PDF bytes, not parser-vendor objects or prose
citations invented after retrieval.

## Decision

Migration 003 adds a derived `structural_records` index. It does not change
`ParsedDocumentV2`. `sync_vault` verifies the source asset and parsed artifact,
then deterministically derives section, non-table block, and table-row records.
Each ID cryptographically binds the full immutable asset SHA-256, full parsed-
artifact SHA-256, SHA-256 of the owning `doc_id`, and MasterVault-owned
structural location. A table-row ID therefore has the form
`struct:<asset-sha256>:artifact:<parsed-artifact-sha256>:owner:<doc-id-sha256>:table:<table-id>:row:<row-id>`.
The owner prevents two notes over the same bytes and parse from colliding; the
artifact identity prevents two distinct parsed artifacts owned by the same
note from colliding.

Table rows are first-class retrieval units. Their indexed text names the table
and row and renders each value with its nearest preceding column header. Header
cells and row cells are both retained as exact `StructuralEvidenceRef` values,
so every label and value in the retrieved text is page/table/row/cell
resolvable. A cell occupies every row satisfying
`cell.row_index <= row_index < cell.row_index + cell.row_span`; its stable cell
ID is retained on each occupied row, and citable spanning text is included in
that row's evidence. Structurally blank rows with no citable text are omitted
rather than represented by invented placeholder text or evidence. A table's
flattened compatibility block is not separately indexed.

The additive channel is lexical FTS. Structural records are not embedded in
this slice, so legacy record IDs, claim IDs, embedding text, vectors and paid
embedding work do not change. When a query finds no structural records, the
four legacy lists passed to RRF are exactly the former inputs and the channel
is omitted from reported counts. `ChannelRank.structural` reports the
structural rank when present and is `null` otherwise. SQLite is the primary
implementation. PostgreSQL persistence, FTS, hydration, migration, and atomic
write support are implemented, but they were not acceptance-tested in the
reported environment; that suite requires `DATABASE_URL`. No PostgreSQL
performance claim is made.

Official SQLite and PostgreSQL backends replace a changed document, claims,
chunks, aliases, and structural projection in one transaction. An injected
structural-write failure therefore rolls the whole changed-document write back.
Unchanged documents still have their deterministic structural projection
replaced on sync so interrupted or newly migrated indexes converge. Legacy
duck-typed backends keep the original four-argument document upsert and may
omit the optional structural capability.

Hydration fails closed. It reloads the canonical source note, verifies the
immutable PDF and parsed-document hashes and parser identity, re-derives the
record from the parser-neutral IR, and requires exact equality with the stored
row. This rejects nonexistent or changed pages, blocks, tables, rows, cells,
source/parser identities and evidence. Schema-v2 normalization continues to
reject multi-region provenance. The ask citation gate accepts structural record
IDs only when they were retrieved. Structural search hits carry the revalidated
evidence and immutable source identity. `run_ask` and `mvault ask --json`
project those fields into a cited source only when that hit has them; a source
with neither field retains the exact legacy two-field `{record_id, rel_path}`
shape.

## Consequences and limits

- The keyless return-policy replay exercises the internal `run_ingest` and
  `sync_vault` services, followed by the public search and ask JSON CLI paths
  (`mvault search` and `mvault ask --json`), with Mock providers and committed
  PDF bytes.
- This is structural evidence retrieval, not a measured retrieval-quality
  improvement. The frozen 52-query and 14-case/97-check baselines remain the
  comparison gates.
- Parser accuracy is not claimed. Production Docling still requires certified
  local artifacts; the deterministic test replays a MasterVault-owned v2 IR.
- Scans/OCR, multi-region items, cross-page tables, structural vector search,
  exhaustive PostgreSQL tuning, and LangGraph/LangChain remain out of scope.
