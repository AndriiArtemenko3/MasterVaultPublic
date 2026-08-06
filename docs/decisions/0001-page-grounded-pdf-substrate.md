# ADR 0001: Page-grounded PDF substrate before layout intelligence

- Status: accepted for v0.3 Milestone 1
- Date: 2026-08-06

## Context

MasterVault v0.2 accepted PDFs, but immediately joined every page's extracted
text into one string. That erased page identity. It also deduplicated input by
a truncated hash of extracted text, so byte-distinct PDF variants could
collapse, and canonical notes pointed at the caller's movable path. A valid
LLM JSON response could therefore create a claim with no mechanically
resolvable visual source.

The first v0.3 change needs to establish trustworthy evidence identity without
prematurely adopting Docling, OCR, table schemas, LangGraph, or a parallel
database model.

## Decision

For clean digital PDFs, MasterVault now uses this spine:

```text
exact PDF byte snapshot
  -> full SHA-256 immutable source asset
  -> pypdf schema-v1 ParsedDocument (one page_text block per page)
  -> separate page-grounded claim extraction contract
  -> deterministic block/quote validation and derived page/offsets
  -> canonical Markdown SourceNote with asset, parse, and evidence references
  -> unchanged claim/chunk embeddings and derived storage schema
```

Source assets are published exclusively under
`<workspace>/assets/sha256/<prefix>/<digest>.pdf`. Parsed JSON is deterministic,
strict, versioned, content-addressed, and stored under `<workspace>/parsed/`.
Both references use workspace-relative paths and are hash-verified when read.

`Claim.evidence`, `SourceNote.source_asset`,
`SourceNote.parsed_document`, and `Hit.evidence` are additive optional fields.
Legacy Markdown/text notes remain valid and return `evidence=[]`. The existing
claim IDs, record IDs, embedding input, chunking, SQLite/PostgreSQL schema, and
ranking math do not change.

PDFs use a separate structured-output contract. A model returns a block id and
short supporting quote. It cannot return an authoritative page or character
offset: MasterVault derives those from the parsed artefact and rejects missing,
unknown, duplicate, or unsupported evidence before the note is published.

## Why no database migration

Markdown remains canonical and `documents.frontmatter` already preserves the
typed note metadata in the rebuildable index. Retrieval can batch-hydrate a
claim's parent document and verify its referenced asset/artefact. Normalized
evidence tables would add a second representation before table/cell retrieval
requirements are known.

## Consequences

Positive:

- Byte-distinct PDFs with identical extracted text no longer collapse.
- Every accepted PDF claim can resolve to a real immutable asset, page, block,
  and exact character span.
- A parser or artefact change is frozen in the ingest plan and detected on
  resume.
- Existing retrieval metrics remain directly comparable because embedding and
  ranking inputs are unchanged.
- `mvault evidence show <claim-id>` provides a narrow, auditable inspection
  surface in both human and JSON form.

Trade-offs and explicit boundaries:

- One page-sized block is coarse evidence, not document-layout understanding.
- Scanned/textless and encrypted PDFs are rejected; there is no OCR fallback.
- Headers and footers remain in the baseline page text.
- Ordinary document/chunk hits are not yet page-grounded; only PDF claim hits
  carry verified evidence.
- Table rows, coordinates, parsing-quality routing, and Docling require a later
  measured schema/benchmark milestone.

## Verification

The slice is gated by deterministic fixture regeneration, Poppler visual QA,
strict IR round-trips, corrupt/encrypted/textless rejection, tamper detection,
quote-resolution tests, byte-hash dedupe/drift tests, end-to-end PDF ingestion,
retrieval hydration, CLI evidence inspection, and the unchanged v0.2 retrieval
and ask regression suites.
