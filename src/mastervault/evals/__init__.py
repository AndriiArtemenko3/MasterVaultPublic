"""Two separate eval harnesses, kept apart so their numbers keep their meaning.

`mastervault.evals.harness` grades RETRIEVAL: load a golden query set, resolve
it against the live corpus, run it through one or more `RetrievalConfig`
ablations, and aggregate `mastervault.evals.metrics` scores (recall/nDCG/MRR).

`mastervault.evals.ask_harness` grades the END-TO-END ask pipeline: frozen
cases run keyless through `run_ask` with a scripted `MockLLM`, graded
mechanically on evidence collection, citation validity, abstention, the
round/novelty guards, and the malformed-output fallback.
"""

from .ask_harness import (
    ASK_CASE_CLASSES,
    AskCase,
    AskCaseResult,
    AskEvalError,
    AskSuiteReport,
    CheckResult,
    compare_ask_to_baseline,
    load_ask_cases,
    missing_case_classes,
    run_ask_case,
    run_ask_suite,
)
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
    "ASK_CASE_CLASSES",
    "GRADED_CLASSES",
    "HYBRID",
    "HYBRID_RERANK",
    "LEXICAL_ONLY",
    "NEGATIVE_CLASS",
    "VECTOR_ONLY",
    "AskCase",
    "AskCaseResult",
    "AskEvalError",
    "AskSuiteReport",
    "CheckResult",
    "CitationReport",
    "ConfigReport",
    "QueryScore",
    "ResolveReport",
    "RetrievalConfig",
    "available_configs",
    "build_claim_index",
    "check_citations",
    "compare_ask_to_baseline",
    "compare_to_baseline",
    "extract_citations",
    "load_ask_cases",
    "load_golden_queries",
    "missing_case_classes",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
    "resolve_golden_set",
    "run_all_configs",
    "run_ask_case",
    "run_ask_suite",
    "run_config",
    "score_query",
    "write_resolved_yaml",
]
