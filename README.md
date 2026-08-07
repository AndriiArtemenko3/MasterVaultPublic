# MasterVault

[![CI](https://github.com/AndriiArtemenko3/MasterVaultPublic/actions/workflows/ci.yml/badge.svg)](https://github.com/AndriiArtemenko3/MasterVaultPublic/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Keyless demo](https://img.shields.io/badge/demo-keyless%20%2F%20%240.00-brightgreen.svg)](#quickstart)

> Status: `0.2.0`, alpha. A single-user CLI you run locally. The default path
> (SQLite + local embeddings + a mock LLM) runs with no API keys and no
> service dependency. `demo load` is offline; the local embedding model is
> downloaded on the first command that embeds new text or a query.

**Contents:** [Why this shape](#why-this-shape) · [v0.3 work](#v03-in-development-page-grounded-pdf-evidence) · [Quickstart](#quickstart) ·
[Architecture](#architecture-at-a-glance) · [The 10-minute tour](#the-10-minute-tour) ·
[Eval numbers](#honest-eval-numbers) · [Command reference](#command-reference) ·
[The dataset](#the-dataset) · [FAQ and troubleshooting](#faq-and-troubleshooting) ·
[Documentation](#documentation) · [License](#license)

MasterVault is an internal-OS RAG stack for small businesses: a Markdown vault
of tickets, policies, contracts, and memos becomes a searchable, citable
knowledge base without anyone hand-building a knowledge graph. Every file on
disk is the source of truth. Ingestion reads raw documents, extracts atomic
claims, drafts wiki concepts, and routes new evidence against what the vault
already believes, flagging contradictions instead of overwriting them.
Retrieval fuses lexical search, vector search, and a wiki alias graph through
Reciprocal Rank Fusion, and an agentic `ask` command runs multiple retrieval
rounds behind a sufficiency judge before it answers, with every claim in the
answer tied to a `[claim-id]` you can trace back to the source note it was
extracted from (and, through that note's `provenance:`, to the raw file behind
it).

## Why this shape

Most RAG demos wire an embedding model to a vector store and call it done.
That gets you semantic search, not an answer you can audit. A support policy
that changed six months ago and a stale FAQ that still quotes the old number
are both "relevant" to a vector search; only a system that tracks claims,
their provenance, and their contradictions can tell you which one is current.
MasterVault treats the vault itself as the database: claims carry
`affects:` links to wiki concepts, wiki concepts carry cross-references and
open contradictions, and a file-backed human-in-the-loop review queue means
nothing gets merged into the shared knowledge layer without a pattern-batched
approval step.

## v0.3 in development: page-grounded PDF evidence

The v0.3 document spine replaces lossy PDF flattening with auditable evidence
for clean, digitally generated PDFs. MasterVault snapshots the exact source
bytes under a full SHA-256 identity and accepts a PDF claim only when its
supporting quote resolves inside the parser-independent IR. The default
`pypdf` profile preserves the original schema-v1, one-block-per-page behavior.
An optional Docling profile adds schema-v2 layout blocks, section hierarchy,
normalized bounding boxes, tables, rows, cells, spans, and cell-level evidence.
Canonical notes retain immutable asset/parse identities, while retrieval
inputs and the v0.2 ranking path remain unchanged.

```text
PDF bytes → immutable asset → page-preserving parse → grounded claim
          → canonical Markdown → unchanged hybrid index → evidence inspection
```

After ingesting with a real LLM provider, inspect the source behind a grounded
claim with:

```bash
uv run mvault evidence show <claim-id>
uv run mvault evidence show <claim-id> --json
```

Docling is deliberately optional and offline-only at runtime. Install it with
`uv sync --extra pdf-layout`, fetch the manifest-pinned layout/TableFormer
artifacts as an explicit network-enabled operator step, then verify them before
ingest:

```bash
uv run python -m mastervault.document_intelligence.fetch_docling_artifacts \
  --output-dir /absolute/path
MV_DOCUMENT__DOCLING_ARTIFACTS_PATH=/absolute/path \
  uv run mvault document doctor --parser docling
uv run mvault ingest ./my-pdfs --domain operations --pdf-parser docling
```

The fetch destination must not exist, and its parent must already be a real
(non-symlink) directory. Acquisition happens in a private sibling staging tree;
only manifest-listed files enter the verified publication tree, which is then
atomically renamed into place. A download, size, hash, or path-safety failure
removes staging and leaves the destination absent.

The adapter never downloads or falls back during parse. Its fixed profile
accepts at most 50 MiB and 200 pages and applies Docling's 120-second
cooperative document timeout; only a complete-success result is accepted. This
remains a clean-digital profile: OCR, scans, image tables, charts, formulas,
and cross-page table stitching are outside the slice. See [ADR 0001](docs/decisions/0001-page-grounded-pdf-substrate.md)
for the immutable substrate and [ADR 0002](docs/decisions/0002-optional-docling-schema-v2.md)
for the optional dependency, artifact identity, schema-v2, and measured limits.

## Quickstart

No API keys required. SQLite is the default backend, so there is no database
to stand up, and the shipped demo dataset ships with precomputed embeddings,
so `demo load` never calls an embedding model either.

### From a repository checkout

The Larkstead demo corpus lives in this repository, so `demo load` needs a
clone:

```bash
git clone https://github.com/AndriiArtemenko3/MasterVaultPublic
cd MasterVaultPublic
uv sync                        # installs mastervault + local embeddings (fastembed, keyless)
uv run mvault init             # creates the workspace + index schema
uv run mvault demo load        # loads Larkstead (seconds, no model/network call)
uv run mvault search "refund window"   # first query may download the local model
```

MasterVault is not currently published on PyPI, so the repository checkout is
the supported install path. Packaging metadata and release automation exist,
but neither is a claim that a public distribution is available.

Postgres + pgvector is available as a swap-in for the index, not a
requirement:

```bash
docker compose up -d           # starts Postgres+pgvector on :5433
export DATABASE_URL=postgresql://mastervault:mastervault@localhost:5433/mastervault
uv run mvault init             # same commands, now backed by Postgres
```

`uv run mvault ask`, `uv run mvault ingest`, and the semantic half of
`uv run mvault lint` call an
LLM. The shipped provider is `mock`: it gives `ask` a deterministic extractive
fallback, but it cannot perform schema-valid claim extraction for a real
ingest. For generated synthesis or ingestion, set `MV_LLM__PROVIDER=anthropic`
with `ANTHROPIC_API_KEY`, or `MV_LLM__PROVIDER=openai` with `OPENAI_API_KEY`.
The tour below uses the keyless mock path.

## Architecture at a glance

```mermaid
flowchart TD
    raw["raw/ documents<br/>customer-support · sales-crm · operations · internal-admin"]
    ingest["INGEST<br/>extract claims → validate → concept-match → corpus-check → route"]
    vault["vault/ (Markdown + YAML, the canonical store)<br/>sources/ · wiki/ · decisions/ · strategy/"]
    review["review queue<br/>human approves cross-refs, edits, contradictions"]
    sync["SYNC → index<br/>SQLite (sqlite-vec + FTS5) or Postgres (pgvector + tsvector)"]
    retrieval["RETRIEVAL<br/>alias front-door + lexical + vector kNN + wiki graph<br/>→ Reciprocal Rank Fusion → MMR → optional rerank"]
    search["mvault search<br/>single hybrid pass, ranked hits"]
    ask["mvault ask<br/>retrieve → sufficiency judge → re-query (≤3 rounds)<br/>→ grounded synthesis with [claim-id] citations<br/>→ citation gate → extractive fallback"]

    raw --> ingest --> vault --> sync --> retrieval
    ingest -. queues contradictions/edits .-> review
    review -. approved changes .-> vault
    retrieval --> search
    retrieval --> ask

    classDef store fill:#e8f0fe,stroke:#4285f4,color:#0b2545;
    classDef human fill:#fff4e5,stroke:#f5a623,color:#5c3d00;
    class vault,sync store;
    class review human;
```

<details>
<summary>Text version of the diagram</summary>

```
raw/  (customer-support, sales-crm, operations, internal-admin)
  │
  ▼
INGEST  extract claims → validate → concept-match → corpus-check → route
  │
  ▼
vault/  Markdown + YAML frontmatter, the canonical store
  ├─ sources/    claims with affects: links
  ├─ wiki/       concepts with aliases, cross-refs, open contradictions
  ├─ decisions/  evidence + criteria + reversal triggers
  └─ strategy/   quarter-scoped roadmaps
  │
  ▼
SYNC  →  SQLite (sqlite-vec + FTS5)  or  Postgres (pgvector + tsvector)
  │
  ▼
RETRIEVAL  alias front-door + lexical claims + lexical docs + vector kNN
           + wiki graph  →  Reciprocal Rank Fusion  →  optional rerank
  │
  ▼
mvault search   (single hybrid pass, ranked hits)
mvault ask      (retrieve → sufficiency judge → re-query, up to 3 rounds
                 → grounded synthesis with [claim-id] citations
                 → citation gate → extractive fallback)
```

</details>

New evidence never overwrites a wiki concept directly. Ingestion routes each
claim into one of four buckets: it links to an existing concept, it
supports one closely enough to queue a cross-reference, it extends one
enough to queue a body edit, or it challenges one and queues an open
contradiction. A human resolves every queued item through
`mvault review`. Full detail, including the storage schema and the
`(record_id, content_hash, model_version)` idempotency rule that makes sync
and the embeddings sidecar safe to re-run, lives in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## The 10-minute tour

Everything below ran against the shipped demo dataset on a fresh workspace,
SQLite backend, local embeddings, mock LLM. Load it first:

```bash
uv run mvault init
uv run mvault demo load
```

### 1. Plain search surfaces both sides of a contradiction

```bash
uv run mvault search "refund window"
```

The demo corpus has a real contradiction seeded into it: a 2024 returns
policy set a 30-day window, a 2025 holiday exception extended it to 45 days,
and the public FAQ and two support macros were never updated to match. A
plain hybrid search does not resolve which one is current, and it should
not; it returns the current wiki card next to claims still quoting the old
number:

```
=== return-policy -> customer-support/wiki/return-policy.md
    **Operating:** As of January 12, 2026, customers may return any item within 45 days...
[claim] (high) The 30-day refund window and 10% restocking fee ... effective 2024-01-15. -> operations/sources/process-process-weekly-support-queue-triage.md
[claim] (high) Past the 30-day window, we are not able to issue a refund. -> customer-support/sources/ticket-ticket-gwen-harada-mat-curl-policy-quote.md
```

### 2. `ask` resolves the contradiction instead of just surfacing it

```bash
uv run mvault ask "how many days do customers have to return an item"
```

With `MV_LLM__PROVIDER=mock` the pipeline still runs its full retrieval loop
and falls back to a deterministic extractive answer: five MMR-selected
evidence cards, each tagged with the claim it came from, both sides of the
contradiction visible side by side:

```
- Customers have 30 days from delivery to start a return. [claim:faq-sl2-faq-returns-01]
- Customers may return any item within 45 days of the delivery date. [claim:policy-sl2-policy-returns-v2-01]
- The 45-day return window applies to every order year-round. [claim:policy-sl2-policy-returns-v2-05]
...
confidence: low
```

Point `ask` at a real key and the same evidence goes through
`GroundedSynthesisContract` instead: one prose answer, a `confidence` field,
and a citation gate that strips any `[claim-id]` the model hallucinates
outside the retrieved evidence pool.

### 3. Cross-domain multi-hop: a support pattern traced to an operations root cause

```bash
uv run mvault ask "what caused the Alder desk mat warping complaints and how many units were affected"
```

Answering this needs two domains: a customer-support chat log where the
support lead flags a pattern across six tickets, and an operations bug
report where the QA lead's inspection finds the root cause (an adhesive cure
oven that ran cold for one shift) and the affected count. `ask`'s evidence
pool pulls both, and the answer cites both:

```
- ... 1400 units of LOT-2025-14 came in 19 Jun. 214 shipped to customers
  before the pattern was flagged. 1186 units remained on hand... a subset
  is delaminating at the edge. [chunk:source:operations/sources/bug-report-bug-report-lot-2025-14-edge-stitch-delamination.md#2]
```

This class of query is the hardest one in the eval set (recall@5 0.300, see
below), which is the honest reason `ask`'s multi-round loop exists: a
single retrieval pass over a two-domain question is not enough on its own.

### 4. The review queue already holds the confirmed contradictions

```bash
uv run mvault review list
```

The demo ships with the 4 review items its own contradiction-detection pass
confirmed at build time, out of 131 candidate pairs the semantic lint
examined:

```
review queue (4 items)
id              tier  change_type       target                pattern
lint-2026-...   2     add-open-con...   customer-support/...  lint-contradiction::return-policy
```

`mvault lint --mechanical-only` runs the keyless structural half of the same
command (frontmatter, broken links, orphan wiki entries) without touching an
LLM:

```bash
uv run mvault lint --mechanical-only
```

The shipped corpus has zero broken `affects:` references and this command exits
0. An earlier build had 75 dangling references; the pipeline reconciliation
fix and exact repair are documented in
[datasets/larkstead/processed/MANIFEST.md](datasets/larkstead/processed/MANIFEST.md).
The semantic contradiction pass produced the 4 retained items at dataset
build time with a real structured-output provider. The shipped mock provider
can exercise the control flow keylessly, but it does not emit structured
contradiction verdicts and therefore is not a reproduction of that adjudication.

### 5. A question the corpus has no answer for

```bash
uv run mvault search "does Larkstead accept cryptocurrency payments"
uv run mvault ask "does Larkstead accept cryptocurrency payments"
```

Larkstead never discusses cryptocurrency anywhere in its 352 source
documents. Vector search still returns its nearest neighbors (that is how
kNN works), but every hit is a weak, off-topic match and `ask` reports
`confidence: low` off unrelated evidence rather than asserting an answer.
The eval harness formalizes exactly this signal: it flags a query as
abstained when the top hit's fused RRF score falls under a floor, and the
hybrid config abstains correctly on 7 of the 8 negative queries in the
golden set (abstention_rate 0.875).

### 6. The eval harness itself

```bash
uv run mvault eval
```

Runs all 52 golden queries through lexical-only, vector-only, and hybrid
retrieval, prints recall@5/10, nDCG@10, and MRR per class, and (with
`--compare datasets/larkstead/golden/baseline.json`) diffs against the
frozen baseline and exits 1 on any regression past tolerance.

## Honest eval numbers

52 golden queries across 5 classes, graded against the real
`datasets/larkstead/processed/` corpus. Numbers are read straight from
`datasets/larkstead/golden/baseline.json`; nothing here is rounded up.

| Config | recall@5 | recall@10 | nDCG@10 | MRR |
|---|---:|---:|---:|---:|
| lexical-only | 0.318 | 0.318 | 0.308 | 0.303 |
| vector-only | 0.557 | 0.716 | 0.527 | 0.500 |
| **hybrid** | **0.591** | **0.716** | **0.565** | **0.551** |

Hybrid wins overall, and the per-class breakdown says exactly where it wins
and where it does not:

| Class (n) | lexical-only | vector-only | hybrid |
|---|---:|---:|---:|
| easy-lexical (14) | 1.000 | 0.893 | 1.000 |
| semantic-paraphrase (12) | 0.000 | 0.375 | 0.458 |
| cross-domain-multi-hop (10) | 0.000 | 0.350 | 0.300 |
| contradiction (8) | 0.000 | 0.500 | 0.438 |
| negative-no-answer (8, abstention_rate) | 1.000 | 1.000 | 0.875 |

recall@5 for the class above is grading whether the retrieval pass alone
found the right document; the numbers below 0.5 on multi-hop and
contradiction are the reason `mvault ask` exists as a separate, multi-round
tool rather than a thin wrapper over `search`.

Three caveats, stated plainly rather than buried in a footnote:

- Cross-domain multi-hop is genuinely hard for a single retrieval pass
  (hybrid recall@5 0.300). It needs two documents in two different domains
  to both rank in the top 5, and hybrid does not always get there in one
  shot. This is what `ask`'s judge-guided re-query loop is for.
- `hybrid+rerank` is not in the table above because it needs a
  `COHERE_API_KEY`. Run `mvault eval --config all` with the key set to add
  it.
- The `operations` domain has zero surviving wiki concepts (see
  [docs/DATASET.md](docs/DATASET.md) for why), so operations queries run on
  lexical and vector signal only, with no alias front-door or graph channel
  to help.

## Command reference

| Command | What it does |
|---|---|
| `uv run mvault init` | Create the workspace and validate the index schema |
| `uv run mvault sync [--full]` | Sync the vault into the index; changed files only unless `--full` |
| `uv run mvault status` | Backend stats and active configuration |
| `uv run mvault reset` | Wipe the index and rebuild it with a full sync |
| `uv run mvault drop` | Delete the index entirely |
| `uv run mvault search <query>` | Hybrid search across claims, chunks, and wiki entries |
| `uv run mvault claims <query>` | Lexical search over the claims layer only |
| `uv run mvault wiki [show <slug>]` | List wiki entries, or render one |
| `uv run mvault ask <question>` | Agentic multi-round retrieval, judged, grounded, cited |
| `uv run mvault ingest <path> --domain <d>` | Raw files → source notes → indexed → concept-routed |
| `uv run mvault ingest <path> --domain <d> --pdf-parser docling` | Opt into the verified offline layout/table parser for PDFs |
| `uv run mvault document doctor --parser <pypdf\|docling>` | Read-only parser/package/artifact readiness check |
| `uv run mvault evidence show <claim-id> [--json]` | Verify and display the immutable page/block evidence behind a grounded PDF claim |
| `uv run mvault lint [--mechanical-only]` | Vault health check: mechanical always, semantic (LLM) optional |
| `uv run mvault review list \| show \| approve \| reject \| approve-pattern \| spot-check` | Triage the human-in-the-loop queue |
| `uv run mvault runs show <run-id>` | Inspect one pipeline run: cost, status, failed units |
| `uv run mvault eval [--compare <baseline>]` | Retrieval eval harness against the golden query set |
| `uv run mvault ask-eval [--compare <baseline>]` | Deterministic 14-case/97-check end-to-end ask gate |
| `uv run mvault demo load \| status \| reset \| delete` | Load, inspect, restore, or remove the shipped demo dataset |

## The dataset

Larkstead Goods Co. is a fictional Portland ergonomic-furniture company, and
its 372 raw documents were not hand-written to look plausible: they were
generated against a single bible file (staff voice cards, pricing history,
SKUs, vendor contracts) and checked by a mechanical consistency checker for
arithmetic and ID errors. Ingestion produced 352 source notes; the other 20
raw files have immutable historical no-output observations with verified raw
hashes. The lost
per-unit run log means their exact original failure or skip cause is unknown. The
deterministic corpus ledger accounts for all 372. Five interlocking storylines,
five narrative contradictions, and the actual four-item review result (one
price-match pair plus three return-window variants) are documented in
[docs/DATASET.md](docs/DATASET.md).

## FAQ and troubleshooting

**Do I actually need an API key?**
Not for `search`, `eval`, `demo`, `status`, or `lint --mechanical-only`. Those
run on the default keyless path (SQLite, local `bge-small` embeddings, mock
LLM). Generated `ask` answers, real `ingest` extraction, and the semantic half
of `lint` require selecting `anthropic` or `openai` and setting its matching
API key.

**Why does `ask` print bullet points instead of a written answer?**
Because `llm.provider` is `mock` (the default). The retrieval is real and every
bullet is a cited piece of evidence, but the prose is stitched, not generated.
Set the matching provider and API key and the same evidence goes through
grounded synthesis. The command prints a one-line note in mock mode.

**`mvault status` says "index not initialized".**
Run `uv run mvault init` first, then `uv run mvault demo load` (for the demo) or
`uv run mvault sync`
(for your own vault). `status` only reports; it does not create the index.

**Did the shipped demo once fail `lint --mechanical-only`?**
Yes. An earlier build had 75 dangling `affects:` references. The current
corpus has zero and the mechanical lint command exits 0; the processed
manifest preserves the cause and repair rather than erasing the history.

**`hybrid+rerank: N/A` in the eval output.**
Cross-encoder reranking needs a key. Set `COHERE_API_KEY` (or a real
`reranker.backend`) and run `uv run mvault eval --config all` to add that row. The
headline hybrid numbers do not depend on it.

**Is `demo load` calling the network?**
No. It imports the precomputed embeddings sidecar shipped in the repo, so it
never embeds anything. The first query-bearing command such as `search`, `ask`,
or `eval` (or a real `sync`) downloads the local `bge-small` model once; after
that it can run from the local model cache.

**How do I point it at my own documents?**
Drop `.md`, `.txt`, or `.pdf` files in a folder and run
`uv run mvault ingest ./my-docs --domain operations` (domains: `customer-support`,
`sales-crm`, `operations`, `internal-admin`). Use `--dry-run` first to see the
plan and cost estimate. Clean digital PDFs retain an immutable byte identity
and structurally grounded claim evidence. `pypdf` remains the exact compatible
default; `--pdf-parser docling` opts into the installed `pdf-layout` extra and
requires a verified `document.docling_artifacts_path`. Neither profile performs
OCR, and textless/scanned or encrypted PDFs are rejected visibly.

**How do I use Postgres instead of SQLite?**
`docker compose up -d`, then export the `DATABASE_URL` shown in the Quickstart
and run `uv run mvault init`. The backend is `auto`: it picks Postgres when
`DATABASE_URL` is reachable, else SQLite. Note the compose file uses port 5433,
not the default 5432.

**How do I reset or remove the demo?**
`uv run mvault demo reset` restores the pristine demo (wipes the index, clears the
review queue, re-imports). `uv run mvault demo delete` removes the whole workspace.
`uv run mvault drop` deletes the index but leaves the vault files.

**Which model does it use, and how do I change it?**
Defaults live in `mastervault.toml`; every key is overridable by an `MV_`
environment variable with `__` between sections, e.g.
`MV_LLM__PROVIDER=openai`, `MV_EMBEDDING__PROVIDER=local`. Secrets are read only
from the environment or `.env`, never from the TOML.

## Documentation

Start here, then follow the code map into any subsystem. Every source package
also carries its own `README.md`.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the three-layer data model, retrieval math, review-queue lifecycle, storage schema, provider seams
- [docs/DATASET.md](docs/DATASET.md) — how Larkstead was built, the storylines, the QC gates
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, how to add a raw doc, golden-query rules
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — reporting and community norms

**Code map** (each links to a folder README):

- [src/mastervault](src/mastervault) — package overview and subsystem map
  - [pipelines](src/mastervault/pipelines) — the `ingest`, `ask`, and `lint` orchestrators
  - [ingest](src/mastervault/ingest) · [retrieval](src/mastervault/retrieval) · [review](src/mastervault/review) — the three subsystems that make it more than vector search
  - [storage](src/mastervault/storage) · [sync](src/mastervault/sync) · [providers](src/mastervault/providers) — backends and swappable model seams
  - [contracts](src/mastervault/contracts) · [prompts](src/mastervault/prompts) · [core](src/mastervault/core) · [vaultfs](src/mastervault/vaultfs) · [cli](src/mastervault/cli) · [evals](src/mastervault/evals) — supporting layers
- [datasets/larkstead](datasets/larkstead) — the synthetic corpus and its [golden set](datasets/larkstead/golden)
- [tests](tests) · [migrations](migrations) — test suite and the Postgres schema

## License

Code is Apache-2.0 (see [LICENSE](LICENSE)). Larkstead's synthetic content and
data assets are CC BY 4.0 under the scoped
[dataset licence](datasets/larkstead/LICENSE.md); code stored alongside the
dataset remains Apache-2.0. Full detail is in [NOTICE](NOTICE).
