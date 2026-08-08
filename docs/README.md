# docs — architecture, dataset, and decision records

The long-form design documents and architecture decision records behind the root README's quickstart. `ARCHITECTURE.md` explains the existing system, `DATASET.md` records how the Larkstead corpus was built and checked, and `decisions/` captures consequential v0.3 choices together with their explicit limits.

## Files

| File | Responsibility |
|---|---|
| `ARCHITECTURE.md` | The internals a quickstart user never sees. Covers the three-layer data model (Markdown sources → wiki/decisions → derived index), the four legacy `hybrid_search` channels plus additive structural FTS and their RRF fusion, the agentic `mvault ask` loop with its sufficiency judge and citation gate, the file-backed `ReviewQueue` and its three routing tiers, the legacy `StorageBackend` protocol plus official SQLite/Postgres structural capability, the two content-hash idempotency gates plus the embeddings sidecar, and the embedding/LLM/reranker provider seams with their keyless mock defaults. |
| `DATASET.md` | How Larkstead Goods Co. was made and validated. Documents the `company.yaml` bible as single source of truth, the dated-policy-history mechanism that generates contradictions, the five interlocking storylines versus the four semantic-lint-confirmed contradictions that actually shipped, the mechanical checker's ten checks, the four rubric-judge passes, the `banned_strings` trademark denylist, and how the processed layer was reproduced from four `mvault ingest` runs plus hand curation. |
| `decisions/0001-page-grounded-pdf-substrate.md` | Why the first v0.3 slice establishes immutable byte identity and exact page/block evidence with a deliberately coarse `pypdf` baseline. |
| `decisions/0002-optional-docling-schema-v2.md` | The measured optional Docling dependency/artifact contract, fixed offline profile, MasterVault-owned schema-v2 and Markdown, structural evidence, compatibility boundary, and explicit deferrals. |
| `decisions/0003-grounded-structural-retrieval.md` | Cryptographically owner/parse-bound schema-v2 structural records, row-span-aware table evidence, atomic projection replacement, exact hydrated citations, legacy compatibility, and bounded limitations. |
| `decisions/0004-temporal-change-control-foundation.md` | The runtime temporal model, verified pre-change seed boundary, and separation between declared source dates and derived temporal state. |
| `decisions/0005-transactional-sqlite-change-control-aggregate.md` | The dedicated transactional SQLite aggregate, content-addressed roots, compare-and-swap persistence, and migration/recovery invariants. |
| `decisions/0006-authoritative-temporal-human-review.md` | SQLite-authoritative review of temporal replacements and constraints, including immutable requests, all-subject decisions, replay, and stale-head protection. |
| `decisions/0007-langgraph-durable-temporal-review-wait.md` | The strict LangGraph checkpoint boundary where checkpoints are disposable workflow cursors and SQLite remains the sole review authority. |
| `decisions/0008-deterministic-advisory-change-discovery.md` | Bounded, deterministic relationship and dependency candidate discovery with content-addressed inputs, evidence bindings, and evaluator isolation. |
| `decisions/0009-managed-revision-review-bundle.md` | The two-PR managed-change boundary: SQLite-authoritative all-target review, create-only raw and SourceNote revisions, wake-only LangGraph reconciliation, and generation-gated activation without in-place evidence mutation. |
| `decisions/0010-durable-temporal-analysis-authority.md` | Create-only committed inference batches, restart-reproducible temporal-analysis evidence, fresh repository SourceNote verification, and the evidence-before-SQLite revision-2 to revision-3 authority boundary. |

## How it fits

These two files are the prose companions to the root [../README.md](../README.md); the quickstart there gets you running, these explain why the pipeline is shaped the way it is. `ARCHITECTURE.md` documents behavior implemented under [../src/mastervault](../src/mastervault) (retrieval, review, storage, providers) and cites specific modules by path. `DATASET.md` documents the corpus under [../datasets](../datasets), whose `MANIFEST.md` and `qa/` directory hold the machine-checkable ground truth this prose summarizes. Contributors arriving from [../CONTRIBUTING.md](../CONTRIBUTING.md) read these to understand the invariants a change must preserve.

## Key concepts / entry points

- **Three-layer data model** — Markdown files are the only canonical store; Postgres/SQLite is a rebuildable derived index. `ARCHITECTURE.md:8`
- **Retrieval path** — four legacy channels plus additive schema-v2 structural FTS, fused with Reciprocal Rank Fusion at `k=60`, then the ask loop with citation gating. `ARCHITECTURE.md`
- **Review-queue lifecycle** — three tiers, `pattern_key` batching, and the `base_hash` conflict check that marks a stale proposal instead of overwriting. `ARCHITECTURE.md:112`
- **Idempotency and the embeddings sidecar** — document-level and record-level content-hash gates; how `mvault demo load` imports precomputed vectors in ~9s without trusting a stale one. `ARCHITECTURE.md:152`
- **Provider seams** — embedding/LLM/reranker `Protocol` seams, each with a keyless `mock`, resolved once per process from `Settings`. `ARCHITECTURE.md:180`
- **Dataset honesty** — the five-storyline narrative table versus the four contradictions the semantic-lint run actually confirmed, reported as-is rather than rounded up. `DATASET.md:36`
