# src/mastervault/evals — Retrieval eval harness and scoring

This folder measures retrieval quality against a hand-graded golden set and audits `ask` answers for citation validity. It resolves every golden target against the live corpus, runs each query through `hybrid_search` under one or more channel-toggle configs, aggregates recall@k / nDCG@10 / MRR (plus an abstention rate for negative queries), and compares a run against a stored baseline so CI can fail on regression. All metric math is pure and I/O-free; the harness owns the id space that connects `Hit`s to golden ids.

## Files

| File | Responsibility |
| --- | --- |
| `harness.py` | Golden-set loading (`load_golden_queries`) and resolution (`resolve_golden_set`, `build_claim_index`), the four `RetrievalConfig` presets, per-query scoring against `hybrid_search` (`score_query`), config-level runs and aggregation (`run_config`, `run_all_configs`, `ConfigReport`), and `compare_to_baseline`. |
| `metrics.py` | Pure ranking metrics `recall_at_k`, `ndcg_at_k`, `mrr` over an ordered id list and a relevant-id set; no knowledge of `Hit`, storage, or I/O. |
| `ask_harness.py` | The END-TO-END ask evaluation, kept separate from `harness.py` so a retrieval number and a pipeline number never merge. Loads and structurally validates `golden/ask_cases.yaml`, builds a scripted keyless `MockLLM` per case, runs each case twice through `run_ask`, and grades mechanically (`grade`) — evidence collected, citation validity, abstention, the round/novelty guards, malformed-output fallback. `compare_ask_to_baseline` is the regression gate. No LLM-as-judge. |
| `pdf_benchmark.py` | Evaluator-only strict contracts and loaders for parser-hidden PDF layout truth and the temporal SL2 change-impact seed. Pair classifications distinguish persisted `SUPERSEDES`/`CONTRADICTS` edges from no-edge `COEXISTS`/`UNRELATED` dispositions, while dependencies declare `DEPENDS_ON` explicitly. These labels are absent from the runtime PDF manifest. |
| `pdf_layout_harness.py` | Pure deterministic layout scorer and benchmark runner. It preflights manifest/gold/PDF identities before parsing, uses unique exact normalized-text assignment (ambiguous hashes receive no credit), reports raw counts for structure/page/order/table/cell/evidence/furniture metrics, and keeps latency outside its stable JSON projection. |
| `provenance.py` | Reproducibility identity for both committed baselines: source/Git state, corpus ledger and eval input, lock/config/prompt/migration hashes, model identifiers, environment, and exact commands. Its stable projection excludes machine-local environment and dirty-state noise. |
| `faithfulness.py` | Offline mechanical citation checker: `extract_citations` pulls `[<id>]` tokens, `check_citations` classifies each against a valid id pool, `CitationReport` reports valid/invalid ids plus a `precision` on citation occurrences. |
| `__init__.py` | Public surface: re-exports the harness symbols, the config presets (`LEXICAL_ONLY`, `VECTOR_ONLY`, `HYBRID`, `HYBRID_RERANK`, `ALL_CONFIGS`), the metrics, and the faithfulness types. |

## How it fits

Input is the golden query set at `datasets/larkstead/golden/queries.yaml`, resolved against the ingested corpus under `datasets/larkstead/processed/` (produced by the ingest pipeline). Each query is executed through [../retrieval](../retrieval)'s `hybrid_search`, so this harness grades the same fusion path that [../pipelines](../pipelines) `ask` uses at runtime; it depends on a live [../storage](../storage) backend and an [../providers](../providers) embedder (and optionally a reranker). Output is a set of `ConfigReport` dicts plus a `compare_to_baseline` delta against `datasets/larkstead/golden/baseline.json`, consumed by the `mvault eval` command and the eval tests; `resolve_golden_set` also writes `resolved.yaml` as a build artifact.

## Key concepts / entry points

- `resolve_golden_set` — verifies every `relevant_docs` path and `relevant_claims` id against the live corpus; a non-empty `.errors` is a fatal build error, not a warning (`harness.py:121`).
- `RetrievalConfig` and the presets — frozen channel/alias/rerank toggles that define one ablation arm; `hybrid+rerank` is skipped with a note when no reranker is configured (`harness.py:186`, `available_configs` at `harness.py:202`).
- `score_query` — runs one query through `hybrid_search`; graded classes get recall/nDCG/MRR, the `negative-no-answer` class is graded by abstention instead (`harness.py:291`).
- `_hit_id` — the grading design decision: every hit is scored at document granularity (`rel_path`), so a claim, chunk, or wiki hit from the right document all earn credit (`harness.py:225`).
- `compare_to_baseline` — per-config and per-query deltas versus `baseline.json`; `regressed` includes graded metric loss, safety-critical class/query loss, missing required configurations or queries, and material abstention loss, driving the CI exit code.
- `check_citations` / `CitationReport.precision` — the mechanical faithfulness gate: which `[<id>]` citations resolve against the evidence pool, with zero-citation answers treated as vacuously valid (`faithfulness.py:60`).
- `run_pdf_layout_benchmark` — defaults to development families, requires explicit held-out opt-in, accounts for every selected rendition as a success or coded failure, and never passes evaluator truth into a parser call.

The PDF report deliberately has no composite "layout score." Every metric keeps
its integer numerator and denominator; a zero denominator serializes as `null`.
Parser failures retain gold denominators for recovery/recall metrics, while
unobservable diagnostics such as ambiguity and body leakage remain `null`
instead of looking artificially perfect.
