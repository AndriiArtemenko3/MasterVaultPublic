# datasets/larkstead/golden — Frozen retrieval eval set + baseline

The graded golden set the retrieval eval regression-gates against. It holds 52 hand-authored queries with their known-relevant documents and claims, a machine-generated resolution report proving every id still exists in the live corpus, and a frozen per-config metrics snapshot. `mvault eval --compare baseline.json` loads `queries.yaml`, re-resolves it against [../processed](../processed), runs each retrieval config through `hybrid_search`, and fails the run if any graded metric drops more than the tolerance below the numbers in `baseline.json`.

## Files

| File | Responsibility |
|------|----------------|
| `queries.yaml` | The 52-query source of truth, authored by hand. Each entry carries `id`, `class` (one of easy-lexical, semantic-paraphrase, cross-domain-multi-hop, contradiction, negative-no-answer), the natural-language `query`, `relevant_docs` (rel_paths under `../processed`), `relevant_claims` (`key_claims` ids that justify each doc's relevance), and a `notes` line recording why the query is graded the way it is. Negative-class queries carry empty `relevant_docs`/`relevant_claims` and expect abstention. |
| `resolved.yaml` | Resolver output, regenerated on every `mvault eval` run by `write_resolved_yaml`; the header says do not hand-edit. A `summary` block counts queries per class and reports `relevant_docs_resolved`/`relevant_claims_resolved` against totals, with `errors: []`. Every doc path and claim id in `queries.yaml` must resolve against the live `../processed` corpus (69 docs, 78 claims here); a non-empty `errors` list is a build error that exits the eval with code 1. |
| `baseline.json` | Frozen per-config metrics the run diffs against. Holds `overall` and `per_class` recall@5, recall@10, nDCG@10, MRR, and negative-class `abstention_rate` for the `lexical-only`, `vector-only`, and `hybrid` configs (`hybrid+rerank` is N/A with no reranker), plus every per-query score. The committed numbers show hybrid leading at 0.591 recall@5 / 0.565 nDCG@10, ahead of vector-only and lexical-only. |

## How it fits

Upstream, [../processed](../processed) supplies the corpus these ids point into: `relevant_docs` are rel_paths to `type: source` notes and `relevant_claims` are ids drawn from their `key_claims` frontmatter, so the golden set only stays valid as long as ingestion keeps producing those documents. The queries were written against the [../bible](../bible) storylines and grep-verified against `../processed` at authoring time. Downstream, the eval harness (`src/mastervault/evals/harness.py`) and the `mvault eval` CLI (`src/mastervault/cli/evals.py`) consume all three files: they re-resolve `queries.yaml` and rewrite `resolved.yaml`, score every non-negative query with `mastervault.evals.metrics`, and pass the aggregated reports plus `baseline.json` to `compare_to_baseline`.

## Key concepts / entry points

- `queries.yaml` entry schema — `relevant_docs` are the graded relevant set; `relevant_claims` document which claim justifies each doc but do not widen the id space (see `_relevant_ids`, `harness.py:250`, and `_hit_id`, `harness.py:225`, for why grading is document-granularity).
- `resolve_golden_set` (`harness.py:121`) + `write_resolved_yaml` (`harness.py:170`) — build `resolved.yaml` by checking each id against a `build_claim_index` over `../processed`; a non-empty `errors` list stops the run.
- `compare_to_baseline` (`harness.py:434`) — per-config, per-metric delta of the current run against `baseline.json`; any graded metric more than `--tolerance` (default 0.02) below baseline lands in `regressed` and forces exit 1. `abstention_rate` is excluded from regression checks.
- Negative-class abstention grading — negative queries are scored by `_abstained` (`harness.py:259`) against `DEFAULT_ABSTENTION_FLOOR = 0.02` (`harness.py:51`); a top-1 RRF score under the floor, or empty hits with no pinned wiki card, counts as a correct abstention.
- Config ablations — `LEXICAL_ONLY`, `VECTOR_ONLY`, `HYBRID`, `HYBRID_RERANK` (`harness.py:194`) name the channel toggles whose keys index the `configs` map in `baseline.json`.
