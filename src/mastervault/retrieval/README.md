# src/mastervault/retrieval — Hybrid search: channels, RRF, MMR

This folder turns a query string into a ranked list of grounded `Hit` records. It runs four independent retrieval channels (lexical claims, lexical docs, vector k-NN, wiki graph), fuses their rankings with Reciprocal Rank Fusion, hydrates the surviving ids into typed records, and optionally reranks the top pool with a cross-encoder. An alias front-door resolves the query to a single pinned wiki card that sits outside the fused hit list. Every stage tolerates empty channels, so a vault with no embeddings, no aliases, or no claims still searches without special-casing.

## Files

| File | Responsibility |
|------|----------------|
| `channels.py` | The four retrieval channels plus the alias front-door. Each function returns an ordered list of ids (or a single wiki id) and degrades to empty output rather than raising. `alias_frontdoor` does longest-alias, word-boundary matching against `backend.alias_index()`; `vector_channel` embeds the query once and skips zero vectors and missing indexes. |
| `fuse.py` | Reciprocal Rank Fusion. `rrf_fuse` scores each id as `sum_r 1/(k + rank_r + 1)` across the ranked lists, with `RRF_K = 60`. Scale-invariant merge; empty lists contribute nothing. |
| `mmr.py` | Greedy Maximal Marginal Relevance selection, `lambda` default 0.7. `mmr_select` scores relevance and inter-candidate similarity by cosine over vectors; `mmr_select_texts` uses Jaccard token overlap for the keyless/mock path. Both share `_mmr_core` and keep input order on ties for determinism. |
| `search.py` | `hybrid_search` orchestration: alias front-door, four channels with fixed pool sizes, graph seeding from the alias hit plus wiki ids in the vector top-10, RRF fusion, hydration into `Hit` models with per-channel 1-based ranks, optional cross-encoder rerank of the top `rerank_pool`, and final trim to `k`. Carries the `channels` / `use_alias` ablation knobs used by the eval harness. |
| `__init__.py` | Package surface. Re-exports the channels, `rrf_fuse` / `RRF_K`, the two MMR selectors, and `hybrid_search` / `SearchResult`. |

## How it fits

Input comes from the storage backend built by [../sync](../sync) and [../ingest](../ingest): claims, chunks, documents, the alias index, and the embeddings table queried via [../storage](../storage). Query embedding and reranking come from [../providers](../providers) (`EmbeddingProvider`, `Reranker`). The `SearchResult` this folder returns feeds the agentic `ask` loop and answer synthesis in [../pipelines](../pipelines) and [../core](../core), and the `channels` / `use_alias` knobs on `hybrid_search` drive the ablations in [../evals](../evals).

## Key concepts / entry points

- `hybrid_search(...)` — the front door; runs channels, fusion, hydration, and optional rerank into a `SearchResult`. `search.py:122`.
- `SearchResult` — return model carrying `wiki_card`, `hits`, `timings`, and `channel_counts`. `search.py:54`.
- `alias_frontdoor(query, backend)` — longest-alias, word-boundary resolve to one `wiki:<domain>:<slug>` id and the matched alias. `channels.py:23`.
- `rrf_fuse(ranked_lists, k=60)` — the scale-invariant merge across all channels. `fuse.py:16`.
- `_mmr_core(...)` — the shared greedy MMR loop behind both selector variants. `mmr.py:48`.
- `_hydrate(fused_ids, backend)` — id-prefix routing (`claim:` / `chunk:` / doc) that loads fused ids into typed `Hit` models and drops ids that no longer resolve. `search.py:89`.
