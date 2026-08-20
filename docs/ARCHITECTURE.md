# Architecture

This document covers the parts a user of the README's quickstart will not
see: the data model, the retrieval math, how a proposed change reaches a
human, the storage schema, and the provider seams. Code references point at
`src/mastervault/` throughout.

## The three-layer data model

Markdown files under `<workspace>/vault/<domain>/` are the only canonical
store. Postgres and SQLite hold a derived index, rebuildable at any time from
the files with `mvault sync --full`. Four domains exist, closed by the
`Domain` enum: `customer-support`, `sales-crm`, `operations`,
`internal-admin`.

**Layer 1: sources.** One Markdown file per raw document, written by
`mvault ingest`. Frontmatter carries a `key_claims:` list; each claim is an
atomic, checkable statement with a stable id, a confidence tier, and an
`affects:` list of wiki slugs it bears on.

```yaml
domain: customer-support
type: source
source_type: ticket
title: "Mat Curl — Order #LS21406"
tags: [ticket]
status: processed
created: 2025-07-05
updated: 2025-07-05
key_claims:
  - id: ticket-ruben-silva-mat-corner-curl-01
    statement: "Corners lift about half an inch off the desk after three weeks of use."
    confidence: high
    affects: [alder-mat-defect]
```

**Layer 2: wiki + decisions.** Wiki entries (`<domain>/wiki/*.md`) are
concepts: a `## Definition` section (what gets embedded as the wiki record
and shown as the search front-door card), `## Cross-Refs`, and an
`## Open Contradictions` section when two sources disagree and neither has
been resolved as superseding the other. Decisions (`<domain>/decisions/*.md`)
and strategy notes (`<domain>/strategy/*.md`) are the two note types ingest
never produces on its own; they get written by hand or by `/deliberate`-style
tooling, citing real claim-ids.

**Layer 3: the index.** `mvault sync` walks the vault, computes a content
hash per document and a separate content hash per embeddable unit (a claim
statement, a wiki definition, or a body chunk), and upserts only what
changed. See [Idempotency](#idempotency-and-the-embeddings-sidecar) below.

### Frontmatter schema

Every note type shares `NoteBase` (`src/mastervault/models.py`): `domain`,
`type`, `title`, `tags`, `status` (`draft | processed | archived`),
`created`, `updated`. Each type adds its own fields:

| Type | Extra fields |
|---|---|
| `source` | `source_type` (closed enum: ticket, policy, sop, bug-report, ...), `key_claims: list[Claim]`, `provenance` (raw-layer path) |
| `wiki` | `aliases: list[str]` |
| `decision` | `decision_status` (open/closed/superseded), `review_date`, `outcome` |
| `strategy` | `quarter` (e.g. `"2026-Q2"`) |

A `Claim` is `{id, statement, confidence, affects}`. `id` matches
`^[a-z0-9][a-z0-9-]*-\d{2}$` (a slug plus a two-digit ordinal); `affects`
entries must be bare kebab-case wiki slugs, validated at write time.

## PDF document boundary

PDFs enter through `document_intelligence`, before claim extraction. The core
`pypdf` path retains schema-v1 exactly: immutable source bytes, physical pages,
and one text block per page. The optional `pdf-layout` extra adds a Docling
adapter, but parser objects never cross its module boundary. Vendor output is
immediately converted to built-in dictionaries and normalized into the strict
MasterVault-owned schema-v2: page dimensions, six-decimal top-left bounding
boxes, sections, blocks, tables, rows, cells, spans, and header flags.
New parses use the paired `mv-clean-digital-v2`/`grid-v2` normalization
identity. Previously persisted v1/v1 artifacts remain readable, but v1/v2
cross-pairing is invalid.

At runtime Docling requires an explicit, real (non-symlink) artifact directory
whose selected layout/TableFormer files match the packaged full-commit,
path/size/hash manifest. Every path component stays inside that directory and
runtime inputs must be regular files. Offline controls are set, remote
services/plugins/OCR are disabled, and a missing or changed artifact is a hard
error—there is no download or `pypdf` fallback. Artifacts are revalidated
before model initialization and immediately before conversion. `mvault
document doctor --parser docling` checks this boundary without parsing or
mutating state.

The schema-v2 profile freezes a 50 MiB source ceiling, 200-page ceiling and
120-second cooperative Docling timeout in both parsed artifacts and ingest
plans. It retains parsed-page style data and enables Docling's deterministic
bookmark/style heading pass, so the real fixture produces nested section
parents rather than a flat list. A timeout or partial conversion fails closed.
The normalizer cross-checks Docling's canonical cells against every declared
grid slot. Explicit empty grid cells are preserved with `bbox: null` when no
text region exists; non-empty cells still require coordinates and bbox-less
cells cannot be cited. Items with more than one provenance region are rejected
until the IR can retain exact ordered regions without drawing an enclosing box
over unrelated page content.

The model proposes exactly one `block_id` or `cell_id` plus a verbatim quote.
Grounding derives page, bbox, table coordinates and offsets from the stored IR;
unknown, duplicate, mixed-table, or forged evidence fails closed. The ingest
plan freezes byte, parser/core, normalization, schema and model identities, and
resume reparses once before publication to reject drift. Schema-v2 Markdown is
rendered by MasterVault, never accepted from the parser vendor. Full rationale
and measured dependency/model costs are in [ADR 0002](decisions/0002-optional-docling-schema-v2.md).

## Retrieval path

Schema-v2 PDF sources also produce deterministic structural records at sync
time. Sections and non-table blocks remain addressable, while every table row
with citable content is indexed independently with table/row scope and column
headers (for example `Customer tier: Premium | Return window: 45 days`).
Hydration re-verifies the immutable asset and parser artifact and re-derives
the row before returning page/block/table/row/cell evidence. The structural
lexical channel is additive: when it is empty, the original four RRF inputs and
rankings are unchanged. See
[ADR 0003](decisions/0003-grounded-structural-retrieval.md).

A structural ID includes the full asset SHA-256, full parsed-artifact SHA-256,
SHA-256 of the owning `doc_id`, and the section, block, or table-row location.
For tables, a cell occupies every row covered by its declared `row_span`; its
cell ID is retained on each such row, and exact evidence is returned for every
citable label or value displayed in the row text. Rows without citable text are
not indexed. This makes shared source bytes, reparses, and distinct owning notes
separate identities without inventing evidence.

`hybrid_search()` (`src/mastervault/retrieval/search.py`) runs four legacy
channels plus the additive structural channel and fuses the non-empty ranked
lists:

1. **Alias front-door.** The query is checked against every known wiki alias.
   A hit becomes the pinned `wiki_card` shown above the ranked results and is
   excluded from the fused list, so it never double-counts.
2. **Lexical claims** (FTS over claim statements, top 30), **lexical
   docs** (FTS over document title + body, top 20), and schema-v2
   **structural rows/blocks/sections** (FTS, top 30 when present).
3. **Vector kNN** (top 30) over the embedding index, cosine similarity.
4. **Wiki graph** (top 20): seeded by the alias hit plus any wiki records in
   the vector top 10, then walked one hop via `claims_for_wiki` (claims whose
   `affects:` names that slug).

The active ranked lists (the four legacy lists plus structural FTS only when it
is non-empty) are merged with Reciprocal Rank Fusion,
`score(d) = Σ 1 / (k + rank_r(d))` with `k = 60` (Cormack, Clarke &
Buettcher, SIGIR 2009), then hydrated into `Hit` records carrying their
per-channel rank for provenance, including optional `channels.structural`.
An optional cross-encoder rerank
(Cohere `rerank-v3.5`, gated on `COHERE_API_KEY`) reorders the top
`retrieval.rerank_pool` (default 30) before the result is trimmed to `k`
(default 10).

`mvault ask` (`src/mastervault/pipelines/ask.py`) wraps this in an agentic
loop:

1. Round 0 runs the raw question through `hybrid_search`. Zero hits and no
   wiki card short-circuits to a `zero_evidence` response with the nearest
   wiki titles by vector distance, rather than pretending to answer.
2. Each subsequent round asks a `SufficiencyJudgeContract` LLM call whether
   the evidence gathered so far answers the question, and if not, for up to
   3 follow-up queries. Three mechanical guards the judge cannot override: a
   hard cap at `ask.max_rounds` (default 3), a novelty floor (a round that
   adds zero new evidence forces a stop), and a followup-dedup pass that
   drops any proposed query that is only a stopword-shuffle of one already
   tried.
3. The top 15 evidence cards (MMR-selected, `mmr_lambda = 0.7`) go to
   `GroundedSynthesisContract`, which returns prose plus a confidence tier
   and any acknowledged gaps. A citation gate strips any `[record-id]` token
   the model emits that is not actually in the evidence pool; if every
   citation gets stripped, or the LLM call fails structured-output
   validation twice, the pipeline falls back to a deterministic extractive
   answer built from the top 5 MMR cards instead of guessing.

For a cited grounded claim or structural hit, `run_ask` and the public JSON ask
CLI (`mvault ask --json`) add the revalidated exact evidence (and structural
source identity when available) to that item in `sources`. A source without
grounded evidence keeps the legacy two-field `{record_id, rel_path}` projection
exactly.

### Generation-aware query boundary

The ordinary `search`, `claims`, `wiki`, and `ask` commands resolve a backend
through `ChangeControlApplication.resolve_query_generation()` rather than
opening it directly. With no managed state or locators, default `auto` retains
the v0.2 `get_backend(settings)` behavior. Once SQLite authority exists, the
resolver freshly opens generation-zero bootstrap/inventory/index evidence or
the active generation-one decision/publication/index evidence, holds their
descriptor and authority guards for the backend lifetime, and verifies them
again before output. Missing or mismatched active evidence never falls back to
the legacy index.

`--generation` accepts `auto`, `legacy`, `active`, or an exact lower-case
`mgeneration:<sha256>` in the bounded generation-zero/generation-one chain.
The result's schema-v1 generation metadata binds authority revision, manifest,
logical and physical index identities, schema, and embedding identity without
exposing absolute runtime paths. Managed resolution is SQLite-only; unmanaged
PostgreSQL remains compatible. Managed `ask` passes `persist_run=False` and an
explicit evidence-workspace map into the same round loop, so it writes no
MasterVault run artifacts while preserving citation hydration across mixed
canonical and immutable generation repositories. See
[QUERY_GENERATIONS.md](QUERY_GENERATIONS.md) for public configuration.

## Review-queue lifecycle

`ReviewQueue` (`src/mastervault/review/queue.py`) is file-backed: one
Markdown file per item under `<workspace>/review/pending/`, moved to
`review/archive/` on resolution. Frontmatter carries `tier`, `change_type`
(one of `new-wiki-page`, `edit-wiki-body`, `add-cross-ref`, `add-alias`,
`add-open-contradiction`, `decision-memo`), `pattern_key` (the batching unit
`mvault review approve-pattern` operates on), and `base_hash` — the target
file's content hash at proposal time, checked again at apply time so a
stale proposal is marked `conflict` instead of silently overwriting a file
someone else already edited.

Three tiers, matching `_meta/specs/review-tiers.md`-style routing:

- **Tier 1** never queues. A claim that matches an existing wiki alias with
  literal anchor text in the source body gets its `[[wikilink]]` inserted
  automatically during ingest.
- **Tier 2** (batch-review) is where a confident match without literal alias
  text, or a claim judged to extend an existing concept, lands. `mvault
  review approve-pattern <pattern>` applies a whole group at once; `spot-check
  <pattern>` samples 3 items for a human read before applying the rest.
- **Tier 3** (explicit-confirm) is a new wiki concept or an open
  contradiction. `mvault review approve <id> --yes` is required per item;
  no batch verb will touch a group containing a tier-3 item.

Enqueueing is deduped by `sha256(producer|target|change_type|proposal)`, so
a producer that re-runs (a resumed ingest, a repeated lint pass) never
double-queues the same proposal.

This file queue is authoritative only for canonical Markdown patch actions.
Temporal change-control review is a distinct typed authority in
`<workspace>/change_control/state.sqlite3`: it binds exact proposed document
replacement/temporal-constraint snapshots, records one immutable per-subject
outcome batch, and advances the aggregate only through that decision. The two
stores must never decide the same action. A future unified `mvault review`
facade may present both kinds while preserving their separate authority.
The local synchronous LangGraph wait/reconciliation seam stores only
disposable execution cursors in sibling `change_control/checkpoints.sqlite3`,
never in the schema-attested state DB. A fixed wake signal carries no outcome;
the graph always rereads authoritative review state after waking.

## Storage

Both backends implement the legacy `StorageBackend` protocol
(`src/mastervault/storage/base.py`) over the same logical schema and expose the
same optional structural capability used by sync and retrieval. Migration 003
adds the derived structural table and lexical index without changing
`ParsedDocumentV2`. SQLite uses `sqlite-vec`'s `vec0` virtual table for vectors
and FTS5 for lexical search in place of pgvector's HNSW index and Postgres's
generated `tsvector` columns. Both apply ordered versions and record them in
`schema_migrations`; v1 and v2 upgrade in place to v3, while corrupt/pre-v1 and
future schema metadata is refused without overwriting it. `storage.backend =
"auto"` (the default) picks Postgres when `DATABASE_URL` is set and reachable,
otherwise SQLite at `<workspace>/index.db`. PostgreSQL support is implemented
but was not acceptance-tested in the reported environment; that suite requires
`DATABASE_URL`. This milestone makes no PostgreSQL performance claim.

Temporal change-control state is not part of that rebuildable index. A separate
`SqliteChangeControlStore` persists one closed `ChangeControlAggregate` per
logical aggregate ID at `<workspace>/change_control/state.sqlite3`. It has its
own schema identity and checksummed migration ledger. Every update replaces the
complete normalized document/claim/relation/dependency/replacement/constraint
graph under one `BEGIN IMMEDIATE` compare-and-swap transaction. Loads rebuild
canonical domain objects, revalidate accepted temporal bases, and verify the
aggregate digest. Authoritative review-request/decision rows are implemented in
that same state store. `TemporalReviewWorkflow` may pause an already-created
request and later reconcile a separately committed decision through a strict,
primitive-only, versioned checkpoint. The cross-database interval is an
intentional saga window: checkpoint failure affects availability only and
never repeats or rolls back authority. Checkpoints disable pickle fallback,
are identity/schema validated when restored, and are not auto-deleted on
corruption. The service owns one synchronous connection and a process-local
lock; no multi-process or production-scaling claim is made. PostgreSQL
change-control persistence, background workers, and CLI/UI orchestration
remain unimplemented.

### Idempotency and the embeddings sidecar

Two independent content-hash gates keep re-runs cheap and safe:

- **Document level.** `sync_vault` re-upserts a document and its claims,
  chunks, and aliases only when the full-file content hash changed, or when
  `--full` forces every document regardless. For a changed document, the
  official SQLite/PostgreSQL backends include its structural projection in the
  same transaction, so a structural failure rolls back the whole write. On an
  unchanged document, sync still replaces the cheap deterministic structural
  projection so interrupted and newly migrated indexes converge.
- **Record level.** Every embeddable unit (a claim statement, a wiki
  definition, a body chunk) carries its own content hash. `needs_embedding`
  checks `(record_id, content_hash, model_version)` against what is already
  stored and returns only the record ids that actually need a fresh
  (paid, for OpenAI) embedding call. A document can be re-upserted on every
  sync with zero re-embedding as long as its claim text has not changed.

The shipped `datasets/larkstead/embeddings/embeddings.jsonl.gz` sidecar
exploits the same gate from the other direction. It is a precomputed vector
dump (`{record_id, record_type, doc_id, domain, content_hash, model_version,
vector}` per line, gzipped JSONL) built once from a real `mvault sync --full`
run. `mvault demo load` runs a metadata-only sync (`embed=False`, so no
embedding calls happen at all) and then imports the sidecar via
`load_embeddings()`, which checks each row against the live vault before
trusting it and skips any whose `content_hash` disagrees with the live
vault text or whose `model_version` does not match the configured embedder.
That is what makes
`demo load` finish in about 9 seconds against a corpus that takes roughly 16
minutes to embed from scratch on CPU, without ever risking a stale vector
silently entering the index.

## Provider seams

Every external call goes through one of three `Protocol`-typed seams, each
with a `mock` implementation that needs no network and no key
(`src/mastervault/providers/`):

| Seam | Options | Keyless default |
|---|---|---|
| Embedding | `local` (fastembed `BAAI/bge-small-en-v1.5`, 384d), `openai` (`text-embedding-3-small`, 1536d), `mock` | `local` — ships in core dependencies, not an extra |
| LLM | `anthropic`, `openai` (honors `llm.base_url` for compatible gateways), `mock` | none; `mastervault.toml` defaults to `mock`, which is keyless and deterministic (extractive answers). Set `MV_LLM__PROVIDER=anthropic` with `ANTHROPIC_API_KEY` (or `openai` with `OPENAI_API_KEY`) for generated synthesis |
| Reranker | `cohere` (`rerank-v3.5`), `null` (passthrough), `mock`, `local-bge` (deliberately unimplemented: raises with an install hint rather than silently downloading a model) | `auto`, which resolves to `cohere` if `COHERE_API_KEY` is set, else `null` |

Every provider is resolved once per process from `Settings`
(`mastervault.toml` + `MV_*` environment overrides + a local `.env` for
secrets, which are read only from the environment, never from the TOML
file). Swapping `MV_EMBEDDING__PROVIDER=openai` mid-project is a supported
path, gated by `SchemaMismatchError`: `init_schema` records the embedding
model and dimension count on first run and refuses to silently mix vectors
from two different models in the same index.
