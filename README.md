# MasterVault

[![CI](https://github.com/AndriiArtemenko3/MasterVaultPublic/actions/workflows/ci.yml/badge.svg)](https://github.com/AndriiArtemenko3/MasterVaultPublic/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Keyless demo](https://img.shields.io/badge/demo-keyless%20%2F%20%240.00-brightgreen.svg)](#quickstart)

> Status: `0.2.0`, alpha. A single-user CLI you run locally. The default path
> (SQLite + local embeddings + a mock LLM) runs with no API keys and no
> network after first install.

**Contents:** [Why this shape](#why-this-shape) · [Quickstart](#quickstart) ·
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

## Quickstart

No API keys required. SQLite is the default backend, so there is no database
to stand up, and the shipped demo dataset ships with precomputed embeddings,
so `demo load` never calls an embedding model either.

```bash
uv sync                        # installs mastervault + local embeddings (fastembed, keyless)
mvault init                    # creates the workspace + index schema
mvault demo load                # loads the Larkstead Goods Co. dataset (seconds, no network)
mvault search "refund window"   # hybrid search, fully keyless
```

Postgres + pgvector is available as a swap-in for the index, not a
requirement:

```bash
docker compose up -d           # starts Postgres+pgvector on :5433
export DATABASE_URL=postgresql://mastervault:mastervault@localhost:5433/mastervault
mvault init                    # same commands, now backed by Postgres
```

`mvault ask`, `mvault ingest`, and the semantic half of `mvault lint` call an
LLM. Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` for real generative
synthesis, or export `MV_LLM__PROVIDER=mock` to see the same commands run
keyless against a deterministic extractive fallback. The tour below uses the
mock path so every command works with zero setup.

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
mvault init
mvault demo load
```

### 1. Plain search surfaces both sides of a contradiction

```bash
mvault search "refund window"
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
mvault ask "how many days do customers have to return an item"
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
mvault ask "what caused the Alder desk mat warping complaints and how many units were affected"
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
mvault review list
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
mvault lint --mechanical-only
```

This corpus has one known, documented gap: 75 `affects:` entries that point
at a wiki slug typo (`shipping` instead of `free-shipping`, and similar) with
no fix applied yet, which is why this command exits 1. It is flagged rather
than hidden; see [docs/DATASET.md](docs/DATASET.md) for the full account.
The semantic contradiction pass itself needs an LLM (real key or mock) and
is what produced the 4 items above at dataset build time; re-running it live
against the shipped corpus reproduces the same 4 confirmations.

### 5. A question the corpus has no answer for

```bash
mvault search "does Larkstead accept cryptocurrency payments"
mvault ask "does Larkstead accept cryptocurrency payments"
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
mvault eval
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
| `mvault init` | Create the workspace and validate the index schema |
| `mvault sync [--full]` | Sync the vault into the index; changed files only unless `--full` |
| `mvault status` | Backend stats and active configuration |
| `mvault reset` | Wipe the index and rebuild it with a full sync |
| `mvault drop` | Delete the index entirely |
| `mvault search <query>` | Hybrid search across claims, chunks, and wiki entries |
| `mvault claims <query>` | Lexical search over the claims layer only |
| `mvault wiki [show <slug>]` | List wiki entries, or render one |
| `mvault ask <question>` | Agentic multi-round retrieval, judged, grounded, cited |
| `mvault ingest <path> --domain <d>` | Raw files → source notes → indexed → concept-routed |
| `mvault lint [--mechanical-only]` | Vault health check: mechanical always, semantic (LLM) optional |
| `mvault review list \| show \| approve \| reject \| approve-pattern \| spot-check` | Triage the human-in-the-loop queue |
| `mvault runs [show <run-id>]` | Inspect pipeline run directories: cost, status, failed units |
| `mvault eval [--compare <baseline>]` | Retrieval eval harness against the golden query set |
| `mvault demo load \| status \| reset \| delete` | Load, inspect, restore, or remove the shipped demo dataset |

## The dataset

Larkstead Goods Co. is a fictional Portland ergonomic-furniture company, and
its 372 raw documents were not hand-written to look plausible: they were
generated against a single bible file (staff voice cards, pricing history,
SKUs, vendor contracts), checked by a mechanical consistency checker for
arithmetic and ID errors, and then run through `mvault ingest` itself to
produce the processed layer this demo loads. The dataset is the product's
first real user. Five interlocking storylines, four seeded contradictions,
and the full account of how the corpus was built and QC'd are in
[docs/DATASET.md](docs/DATASET.md).

## FAQ and troubleshooting

**Do I actually need an API key?**
Not for `search`, `eval`, `demo`, `status`, or `lint --mechanical-only`. Those
run on the default keyless path (SQLite, local `bge-small` embeddings, mock
LLM). You need `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` only for generated `ask`
answers, real `ingest` extraction, and the semantic half of `lint`.

**Why does `ask` print bullet points instead of a written answer?**
Because `llm.provider` is `mock` (the default). The retrieval is real and every
bullet is a cited piece of evidence, but the prose is stitched, not generated.
Set an API key and the same evidence goes through grounded synthesis. The
command prints a one-line note when it is running in mock mode.

**`mvault status` says "index not initialized".**
Run `mvault init` first, then `mvault demo load` (for the demo) or `mvault sync`
(for your own vault). `status` only reports; it does not create the index.

**`mvault lint --mechanical-only` exits with code 1.**
That is expected on the shipped demo. The corpus has 75 `affects:` entries
pointing at a wiki slug typo that has not been fixed, so the mechanical check
reports it rather than hiding it. See [docs/DATASET.md](docs/DATASET.md) for the
full account.

**`hybrid+rerank: N/A` in the eval output.**
Cross-encoder reranking needs a key. Set `COHERE_API_KEY` (or a real
`reranker.backend`) and run `mvault eval --config all` to add that row. The
headline hybrid numbers do not depend on it.

**Is `demo load` calling the network?**
No. It imports the precomputed embeddings sidecar shipped in the repo, so it
never embeds anything. The first time you run `mvault eval` (or a real `sync`),
the local `bge-small` model downloads once to embed queries; after that it is
offline.

**How do I point it at my own documents?**
Drop `.md`, `.txt`, or `.pdf` files in a folder and run
`mvault ingest ./my-docs --domain operations` (domains: `customer-support`,
`sales-crm`, `operations`, `internal-admin`). Use `--dry-run` first to see the
plan and cost estimate. PDF ingestion is raw text extraction, with no OCR.

**How do I use Postgres instead of SQLite?**
`docker compose up -d`, then export the `DATABASE_URL` shown in the Quickstart
and run `mvault init`. The backend is `auto`: it picks Postgres when
`DATABASE_URL` is reachable, else SQLite. Note the compose file uses port 5433,
not the default 5432.

**How do I reset or remove the demo?**
`mvault demo reset` restores the pristine demo (wipes the index, clears the
review queue, re-imports). `mvault demo delete` removes the whole workspace.
`mvault drop` deletes the index but leaves the vault files.

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

Code is Apache-2.0 (see [LICENSE](LICENSE)). The `datasets/larkstead/`
synthetic dataset is CC BY 4.0. Full detail, including a note on why every
entity in the dataset is fictional, is in [NOTICE](NOTICE).
