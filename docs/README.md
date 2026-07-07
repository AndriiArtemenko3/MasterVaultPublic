# docs — architecture and dataset deep-dives

The two long-form design documents that sit behind the root README's quickstart. `ARCHITECTURE.md` explains how the system actually works once you look past the CLI surface: the data model, the retrieval math, how a proposed change reaches a human, the storage schema, and the provider seams. `DATASET.md` records how the Larkstead demo corpus was built and quality-checked, including the mismatches found and fixed during that build. Neither file ships code; both point at `src/mastervault/` and `datasets/larkstead/` for the real thing.

## Files

| File | Responsibility |
|---|---|
| `ARCHITECTURE.md` | The internals a quickstart user never sees. Covers the three-layer data model (Markdown sources → wiki/decisions → derived index), the four-channel `hybrid_search` and its RRF fusion, the agentic `mvault ask` loop with its sufficiency judge and citation gate, the file-backed `ReviewQueue` and its three routing tiers, the shared `StorageBackend` protocol over SQLite/Postgres, the two content-hash idempotency gates plus the embeddings sidecar, and the embedding/LLM/reranker provider seams with their keyless mock defaults. |
| `DATASET.md` | How Larkstead Goods Co. was made and validated. Documents the `company.yaml` bible as single source of truth, the dated-policy-history mechanism that generates contradictions, the five interlocking storylines versus the four semantic-lint-confirmed contradictions that actually shipped, the mechanical checker's ten checks, the four rubric-judge passes, the `banned_strings` trademark denylist, and how the processed layer was reproduced from four `mvault ingest` runs plus hand curation. |

## How it fits

These two files are the prose companions to the root [../README.md](../README.md); the quickstart there gets you running, these explain why the pipeline is shaped the way it is. `ARCHITECTURE.md` documents behavior implemented under [../src/mastervault](../src/mastervault) (retrieval, review, storage, providers) and cites specific modules by path. `DATASET.md` documents the corpus under [../datasets](../datasets), whose `MANIFEST.md` and `qa/` directory hold the machine-checkable ground truth this prose summarizes. Contributors arriving from [../CONTRIBUTING.md](../CONTRIBUTING.md) read these to understand the invariants a change must preserve.

## Key concepts / entry points

- **Three-layer data model** — Markdown files are the only canonical store; Postgres/SQLite is a rebuildable derived index. `ARCHITECTURE.md:8`
- **Retrieval path** — four channels (alias front-door, lexical, vector kNN, wiki graph) fused with Reciprocal Rank Fusion at `k=60`, then the agentic `ask` loop with sufficiency judge and citation gate. `ARCHITECTURE.md:68`
- **Review-queue lifecycle** — three tiers, `pattern_key` batching, and the `base_hash` conflict check that marks a stale proposal instead of overwriting. `ARCHITECTURE.md:112`
- **Idempotency and the embeddings sidecar** — document-level and record-level content-hash gates; how `mvault demo load` imports precomputed vectors in ~9s without trusting a stale one. `ARCHITECTURE.md:152`
- **Provider seams** — embedding/LLM/reranker `Protocol` seams, each with a keyless `mock`, resolved once per process from `Settings`. `ARCHITECTURE.md:180`
- **Dataset honesty** — the five-storyline narrative table versus the four contradictions the semantic-lint run actually confirmed, reported as-is rather than rounded up. `DATASET.md:36`
