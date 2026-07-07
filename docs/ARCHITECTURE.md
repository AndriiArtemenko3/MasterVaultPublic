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

## Retrieval path

`hybrid_search()` (`src/mastervault/retrieval/search.py`) runs four
independent channels and fuses them:

1. **Alias front-door.** The query is checked against every known wiki alias.
   A hit becomes the pinned `wiki_card` shown above the ranked results and is
   excluded from the fused list, so it never double-counts.
2. **Lexical claims** (FTS over claim statements, top 30) and **lexical
   docs** (FTS over document title + body, top 20).
3. **Vector kNN** (top 30) over the embedding index, cosine similarity.
4. **Wiki graph** (top 20): seeded by the alias hit plus any wiki records in
   the vector top 10, then walked one hop via `claims_for_wiki` (claims whose
   `affects:` names that slug).

The four ranked lists are merged with Reciprocal Rank Fusion,
`score(d) = Σ 1 / (k + rank_r(d))` with `k = 60` (Cormack, Clarke &
Buettcher, SIGIR 2009), then hydrated into `Hit` records carrying their
per-channel rank for provenance. An optional cross-encoder rerank
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

## Storage

Both backends implement the same `StorageBackend` protocol
(`src/mastervault/storage/base.py`) over the same logical schema
(`migrations/pg/001_init.sql` for Postgres; the SQLite backend mirrors it by
hand, using `sqlite-vec`'s `vec0` virtual table for vectors and FTS5 for
lexical search in place of pgvector's HNSW index and Postgres's generated
`tsvector` columns). `storage.backend = "auto"` (the default) picks Postgres
when `DATABASE_URL` is set and reachable, otherwise SQLite at
`<workspace>/index.db`.

### Idempotency and the embeddings sidecar

Two independent content-hash gates keep re-runs cheap and safe:

- **Document level.** `sync_vault` re-upserts a document (and its claims,
  chunks, aliases) only when the full-file content hash changed, or when
  `--full` forces every document regardless.
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
| LLM | `anthropic`, `openai` (honors `llm.base_url` for compatible gateways), `mock` | none; `mastervault.toml` defaults to `anthropic` and needs `ANTHROPIC_API_KEY`, or set `MV_LLM__PROVIDER=mock` |
| Reranker | `cohere` (`rerank-v3.5`), `null` (passthrough), `mock`, `local-bge` (deliberately unimplemented: raises with an install hint rather than silently downloading a model) | `auto`, which resolves to `cohere` if `COHERE_API_KEY` is set, else `null` |

Every provider is resolved once per process from `Settings`
(`mastervault.toml` + `MV_*` environment overrides + a local `.env` for
secrets, which are read only from the environment, never from the TOML
file). Swapping `MV_EMBEDDING__PROVIDER=openai` mid-project is a supported
path, gated by `SchemaMismatchError`: `init_schema` records the embedding
model and dimension count on first run and refuses to silently mix vectors
from two different models in the same index.
