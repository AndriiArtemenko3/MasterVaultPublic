"""Retrieval eval harness: golden-set grading, per-config `hybrid_search`
ablation runs, and a mechanical citation-validity checker for `ask` answers.

`mastervault.evals.harness` is the front door: load a golden query set,
resolve it against the live corpus, run it through one or more
`RetrievalConfig`s, and aggregate `mastervault.evals.metrics` scores.
"""

from .faithfulness import CitationReport, check_citations, extract_citations
from .harness import (
    ALL_CONFIGS,
    GRADED_CLASSES,
    HYBRID,
    HYBRID_RERANK,
    LEXICAL_ONLY,
    NEGATIVE_CLASS,
    VECTOR_ONLY,
    ConfigReport,
    QueryScore,
    ResolveReport,
    RetrievalConfig,
    available_configs,
    build_claim_index,
    compare_to_baseline,
    load_golden_queries,
    resolve_golden_set,
    run_all_configs,
    run_config,
    score_query,
    write_resolved_yaml,
)
from .metrics import mrr, ndcg_at_k, recall_at_k

__all__ = [
    "ALL_CONFIGS",
    "GRADED_CLASSES",
    "HYBRID",
    "HYBRID_RERANK",
    "LEXICAL_ONLY",
    "NEGATIVE_CLASS",
    "VECTOR_ONLY",
    "CitationReport",
    "ConfigReport",
    "QueryScore",
    "ResolveReport",
    "RetrievalConfig",
    "available_configs",
    "build_claim_index",
    "check_citations",
    "compare_to_baseline",
    "extract_citations",
    "load_golden_queries",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
    "resolve_golden_set",
    "run_all_configs",
    "run_config",
    "score_query",
    "write_resolved_yaml",
]
