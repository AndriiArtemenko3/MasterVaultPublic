# src/mastervault/evals — Retrieval eval harness and scoring

This folder measures retrieval quality against a hand-graded golden set and audits `ask` answers for citation validity. It resolves every golden target against the live corpus, runs each query through `hybrid_search` under one or more channel-toggle configs, aggregates recall@k / nDCG@10 / MRR (plus an abstention rate for negative queries), and compares a run against a stored baseline so CI can fail on regression. All metric math is pure and I/O-free; the harness owns the id space that connects `Hit`s to golden ids.

## Files

| File | Responsibility |
| --- | --- |
| `harness.py` | Golden-set loading (`load_golden_queries`) and resolution (`resolve_golden_set`, `build_claim_index`), the four `RetrievalConfig` presets, per-query scoring against `hybrid_search` (`score_query`), config-level runs and aggregation (`run_config`, `run_all_configs`, `ConfigReport`), and `compare_to_baseline`. |
| `metrics.py` | Pure ranking metrics `recall_at_k`, `ndcg_at_k`, `mrr` over an ordered id list and a relevant-id set; no knowledge of `Hit`, storage, or I/O. |
| `faithfulness.py` | Offline mechanical citation checker: `extract_citations` pulls `[<id>]` tokens, `check_citations` classifies each against a valid id pool, `CitationReport` reports valid/invalid ids plus a `precision` on citation occurrences. |
| `__init__.py` | Public surface: re-exports the harness symbols, the config presets (`LEXICAL_ONLY`, `VECTOR_ONLY`, `HYBRID`, `HYBRID_RERANK`, `ALL_CONFIGS`), the metrics, and the faithfulness types. |

## How it fits

Input is the golden query set at `datasets/larkstead/golden/queries.yaml`, resolved against the ingested corpus under `datasets/larkstead/processed/` (produced by the ingest pipeline). Each query is executed through [../retrieval](../retrieval)'s `hybrid_search`, so this harness grades the same fusion path that [../pipelines](../pipelines) `ask` uses at runtime; it depends on a live [../storage](../storage) backend and an [../providers](../providers) embedder (and optionally a reranker). Output is a set of `ConfigReport` dicts plus a `compare_to_baseline` delta against `datasets/larkstead/golden/baseline.json`, consumed by the `mvault eval` command and the eval tests; `resolve_golden_set` also writes `resolved.yaml` as a build artifact.

## Key concepts / entry points

- `resolve_golden_set` — verifies every `relevant_docs` path and `relevant_claims` id against the live corpus; a non-empty `.errors` is a fatal build error, not a warning (`harness.py:121`).
- `RetrievalConfig` and the presets — frozen channel/alias/rerank toggles that define one ablation arm; `hybrid+rerank` is skipped with a note when no reranker is configured (`harness.py:186`, `available_configs` at `harness.py:202`).
- `score_query` — runs one query through `hybrid_search`; graded classes get recall/nDCG/MRR, the `negative-no-answer` class is graded by abstention instead (`harness.py:291`).
- `_hit_id` — the grading design decision: every hit is scored at document granularity (`rel_path`), so a claim, chunk, or wiki hit from the right document all earn credit (`harness.py:225`).
- `compare_to_baseline` — per-config, per-metric deltas versus `baseline.json`; `regressed` lists any non-abstention metric that dropped more than `tolerance`, driving the CI exit code (`harness.py:434`).
- `check_citations` / `CitationReport.precision` — the mechanical faithfulness gate: which `[<id>]` citations resolve against the evidence pool, with zero-citation answers treated as vacuously valid (`faithfulness.py:60`).
