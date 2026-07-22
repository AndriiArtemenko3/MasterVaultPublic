# src/mastervault — Package map for the MasterVault RAG system

This is the Python package behind the `mvault` CLI. Markdown files with YAML frontmatter are canonical; everything under here reads those files, derives a searchable index, extracts atomic claims with provenance, and answers questions against that index under a citation gate. The two module-level files (`config.py`, `models.py`) define the settings and the typed data model every subpackage codes against; each subdirectory owns one stage or seam of the pipeline.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker; pins `__version__ = "0.2.0"` and states the file-canonical / derived-index contract. |
| `config.py` | `Settings` (pydantic-settings) plus `load_settings()`. Merges `mastervault.toml`, `MV_*` env vars, and `.env`; secrets (`DATABASE_URL`, API keys) come from the environment only. Holds nested config blocks for storage, embedding, llm, reranker, retrieval, ingestion, ask, budgets, and paths. |
| `models.py` | The shared data model: `Claim`, the note frontmatter views (`SourceNote`, `WikiEntry`, `DecisionNote`, `StrategyNote`), the embeddable `Record`, retrieval `Hit`/`ChannelRank`, `ReviewItem`, closed-set enums, and `content_hash()`. Pydantic-only so storage, retrieval, and pipelines can all import it. |
| [cli/](./cli) | Typer command surface for `mvault`: `ask`, `ingest`, `lint`, `review`, `runs`, `evals`, `demo`, `query`, `admin`. Subcommand modules register on the root `app`. |
| [contracts/](./contracts) | Versioned-prompt + typed-output contracts with autofix/hard-fail guards, one per LLM task (claim extraction, contradiction judge, corpus check, sufficiency judge, grounded synthesis, wiki draft). |
| [core/](./core) | Orchestration substrate: exit-code errors, append-only `EventLog`, `BudgetLedger`, and `RunContext`. |
| [evals/](./evals) | Retrieval eval harness: golden-set grading, per-`RetrievalConfig` ablation runs, recall@k/nDCG/MRR metrics, and a mechanical citation-validity checker for `ask` answers. |
| [ingest/](./ingest) | Ingestion stages: raw-file conversion, claim extraction, concept matching, corpus-check adjudication, wiki drafting, wikilink insertion, and the claim schema gate (`validate`). |
| [pipelines/](./pipelines) | The three end-to-end runs (`run_ingest`, `run_ask`, `run_lint`) that compose contracts, storage, retrieval, and the review queue under a `RunContext`. |
| [prompts/](./prompts) | Versioned prompt files, one directory per contract id (`<contract_id>/v<N>.md`): YAML header plus Jinja2 body, loaded through `registry.load`. |
| [providers/](./providers) | External-model seam: embedding, LLM, and reranker Protocols with local/mock/API backends, plus the token price table. Keeps the stack runnable offline. |
| [retrieval/](./retrieval) | Hybrid search: lexical, vector, and alias-graph channels fused by RRF, hydrated, MMR-diversified, and optionally reranked. `hybrid_search` is the front door. |
| [review/](./review) | Human-in-the-loop layer: file-backed review queue plus a guarded diff `apply` with drift detection. |
| [storage/](./storage) | Backend seam and `get_backend()` resolution: SQLite (sqlite-vec + FTS5) or Postgres (pgvector + tsvector), chosen by the `auto` rule on `DATABASE_URL`. |
| [sync/](./sync) | File-canonical vault to derived index: `sync_vault` walks the vault, upserts changed docs, and runs the hash-gated embedding pass. `load` adds a precomputed-vector fast path for the demo dataset. |
| [vaultfs/](./vaultfs) | The file-canonical layer: reading, writing, walking, frontmatter parsing/surgical edits, and chunk segmentation of the Markdown notes. |

## How it fits

[vaultfs](./vaultfs) reads the canonical Markdown; [ingest](./ingest) turns raw files into notes with claims; [sync](./sync) projects those notes into a [storage](./storage) index that [retrieval](./retrieval) queries. The [pipelines](./pipelines) tie those stages together for `ingest`/`ask`/`lint`, calling [contracts](./contracts) (rendered from [prompts](./prompts), executed via [providers](./providers)) and routing proposed changes through [review](./review), all under a [core](./core) `RunContext`. The [cli](./cli) is the operator entry point and [evals](./evals) grades retrieval quality offline. Nearly every module imports `models.py` for its types and `config.py` for `Settings`.

## Key concepts / entry points

- `Settings` / `load_settings()` — resolved config with env > `.env` > TOML > defaults precedence; secrets are env-only (`config.py:125`, `config.py:179`).
- `models.Record` — the one embeddable/retrievable index unit derived from a note, keyed by `record_id` and `content_hash` (`models.py:181`).
- `models.Claim` — one atomic asserted claim from a source, id-validated by `CLAIM_ID_RE`, carried in `key_claims:` frontmatter (`models.py:115`).
- `models.Hit` / `ChannelRank` — a fused retrieval result plus its per-channel rank provenance (`models.py:202`, `models.py:193`).
- `models.ReviewItem` — the frontmatter view of one queued HITL change, with `tier`, `change_type`, and drift-detecting `base_hash` (`models.py:220`).
- `content_hash(text)` — the stable 16-hex-char hash used for change detection and embed idempotency across sync and storage (`models.py:176`).
