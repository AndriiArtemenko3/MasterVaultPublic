"""Retrieval eval harness: golden-set loading + resolution, per-config runs
against `hybrid_search`, and metric aggregation.

Pipeline: `load_golden_queries` reads queries.yaml -> `resolve_golden_set`
verifies every `relevant_docs` path and `relevant_claims` id against the live
`datasets/larkstead/processed/` corpus (a non-empty error list here is a
build error, not a warning) -> `run_config` executes every non-negative
query through `hybrid_search` under one `RetrievalConfig`'s channel toggles
and scores it with `mastervault.evals.metrics`, while negative-class queries
are graded separately via abstention (empty hits, or a top-1 score under a
floor, and never a pinned wiki card) -> `run_all_configs` does this for every
available config, skipping `hybrid+rerank` with a note instead of failing
when no reranker is configured.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mastervault.config import Settings
from mastervault.models import Hit
from mastervault.providers import EmbeddingProvider, Reranker
from mastervault.retrieval.search import hybrid_search
from mastervault.storage.base import StorageBackend

from .metrics import mrr as mrr_metric
from .metrics import ndcg_at_k, recall_at_k

RECALL_KS: tuple[int, ...] = (5, 10)
NDCG_K = 10

GRADED_CLASSES: tuple[str, ...] = (
    "easy-lexical",
    "semantic-paraphrase",
    "cross-domain-multi-hop",
    "contradiction",
)
NEGATIVE_CLASS = "negative-no-answer"

# Empirically set against the Larkstead baseline run (see datasets/larkstead/
# golden/baseline.json): a single lexical- or graph-channel top-1 hit scores
# ~1/(rrf_k+1) = 1/61 ~= 0.0164 under RRF; a genuinely irrelevant negative
# query should not clear a small multiple of that on any channel mix.
DEFAULT_ABSTENTION_FLOOR = 0.02

_FM_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Golden-set loading + resolution
# ---------------------------------------------------------------------------


def load_golden_queries(path: Path | str) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return list(data or [])


def _load_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def build_claim_index(processed_dir: Path | str) -> dict[str, str]:
    """{claim_id: rel_path} for every `key_claims` entry on every `type: source`
    note under `processed_dir`. `rel_path` is relative to `processed_dir`."""
    processed_dir = Path(processed_dir)
    index: dict[str, str] = {}
    for md_path in sorted(processed_dir.rglob("*.md")):
        if "_review" in md_path.parts:
            continue
        fm = _load_frontmatter(md_path)
        if fm.get("type") != "source":
            continue
        rel = md_path.relative_to(processed_dir).as_posix()
        for claim in fm.get("key_claims") or []:
            index[claim["id"]] = rel
    return index


@dataclass
class ResolveReport:
    query_count: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    relevant_docs_total: int = 0
    relevant_docs_resolved: int = 0
    relevant_claims_total: int = 0
    relevant_claims_resolved: int = 0
    errors: list[str] = field(default_factory=list)
    per_query: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "query_count": self.query_count,
                "class_counts": self.class_counts,
                "relevant_docs_total": self.relevant_docs_total,
                "relevant_docs_resolved": self.relevant_docs_resolved,
                "relevant_claims_total": self.relevant_claims_total,
                "relevant_claims_resolved": self.relevant_claims_resolved,
                "errors": self.errors,
            },
            "queries": self.per_query,
        }


def resolve_golden_set(
    queries: Sequence[dict[str, Any]], processed_dir: Path | str
) -> ResolveReport:
    """Verify every `relevant_docs` path and `relevant_claims` id in `queries`
    against the live corpus under `processed_dir`. Every id must resolve —
    per spec, an unresolved id is a build error, not a soft warning; callers
    (the `resolve_golden_set` test, `mvault eval`) should treat a non-empty
    `.errors` as fatal.
    """
    processed_dir = Path(processed_dir)
    claim_index = build_claim_index(processed_dir)

    report = ResolveReport(query_count=len(queries))
    for q in queries:
        report.class_counts[q["class"]] = report.class_counts.get(q["class"], 0) + 1

        doc_status = []
        for rel in q.get("relevant_docs", []) or []:
            exists = (processed_dir / rel).is_file()
            report.relevant_docs_total += 1
            if exists:
                report.relevant_docs_resolved += 1
            else:
                report.errors.append(f"{q['id']}: relevant_docs path does not exist: {rel}")
            doc_status.append({"rel_path": rel, "resolved": exists})

        claim_status = []
        for cid in q.get("relevant_claims", []) or []:
            owner = claim_index.get(cid)
            report.relevant_claims_total += 1
            if owner is not None:
                report.relevant_claims_resolved += 1
            else:
                report.errors.append(
                    f"{q['id']}: relevant_claims id not found in any source note: {cid}"
                )
            claim_status.append({"claim_id": cid, "resolved": owner is not None, "doc": owner})

        report.per_query.append(
            {
                "id": q["id"],
                "class": q["class"],
                "relevant_docs": doc_status,
                "relevant_claims": claim_status,
            }
        )
    return report


def write_resolved_yaml(report: ResolveReport, path: Path | str) -> None:
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Resolver output for queries.yaml -- generated by evals.harness, do not hand-edit.\n")
        fh.write(
            "# 100% of relevant_docs / relevant_claims must resolve against the live\n"
            "# datasets/larkstead/processed/ corpus; a non-empty `errors` list is a build error.\n"
        )
        yaml.safe_dump(report.to_dict(), fh, sort_keys=False, width=100)


# ---------------------------------------------------------------------------
# Retrieval configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalConfig:
    name: str
    channels: frozenset[str] | None  # None = every channel (hybrid_search's own default)
    use_alias: bool = True
    rerank: bool = False


LEXICAL_ONLY = RetrievalConfig("lexical-only", frozenset({"lexical_claims", "lexical_docs"}))
VECTOR_ONLY = RetrievalConfig("vector-only", frozenset({"vector"}), use_alias=False)
HYBRID = RetrievalConfig("hybrid", None)
HYBRID_RERANK = RetrievalConfig("hybrid+rerank", None, rerank=True)

ALL_CONFIGS: tuple[RetrievalConfig, ...] = (LEXICAL_ONLY, VECTOR_ONLY, HYBRID, HYBRID_RERANK)


def available_configs(
    settings: Settings, reranker: Reranker | None
) -> tuple[list[RetrievalConfig], list[str]]:
    """Configs to run plus human-readable notes for any config skipped instead
    of failed (currently only `hybrid+rerank` when no reranker is available).
    """
    configs = [LEXICAL_ONLY, VECTOR_ONLY, HYBRID]
    notes: list[str] = []
    if reranker is not None and reranker.name not in ("null",):
        configs.append(HYBRID_RERANK)
    else:
        notes.append(
            "hybrid+rerank: N/A (no reranker configured -- set COHERE_API_KEY or "
            "reranker.backend to enable it)"
        )
    return configs, notes


# ---------------------------------------------------------------------------
# Per-query scoring
# ---------------------------------------------------------------------------


def _hit_id(hit: Hit) -> str:
    """Every hit is graded at document granularity (`rel_path`), regardless
    of whether it surfaced as a claim, a chunk, a wiki hit, or the pinned
    wiki card. Grading claim hits under a separate bare-claim-id namespace
    was tried and rejected: a claim hit and a chunk hit from the *same*
    document would then count as two different "relevant" targets, so a
    query whose golden set lists both a doc's rel_path and one of its claim
    ids (the common case — see `relevant_claims`) could under-score even
    when the right document was surfaced, just because the specific record
    type differed from whichever one happened to be listed. `rel_path`
    collapses that: any record from the right document earns credit.
    """
    return hit.rel_path or hit.record_id


def _graded_ids(wiki_card: Hit | None, hits: list[Hit]) -> list[str]:
    """Rank-ordered ids: the pinned wiki card first (it's the top thing the
    user sees, even though `hybrid_search` excludes it from `hits`), then the
    fused hit list.
    """
    ids = [_hit_id(wiki_card)] if wiki_card is not None else []
    ids.extend(_hit_id(h) for h in hits)
    return ids


def _relevant_ids(query: dict[str, Any]) -> set[str]:
    """Document-granularity relevant set: `relevant_docs` rel_paths only.
    `relevant_claims` documents *which* claim justifies the doc's relevance
    (and is resolver-verified against the live corpus) but doesn't widen the
    graded id space — see `_hit_id` for why.
    """
    return set(query.get("relevant_docs", []) or [])


def _abstained(wiki_card: Hit | None, hits: list[Hit], floor: float) -> bool:
    if wiki_card is not None:
        return False  # a pinned wiki card is a confident (mistaken) answer, not an abstention
    if not hits:
        return True
    return hits[0].rrf_score < floor


@dataclass
class QueryScore:
    id: str
    cls: str
    recall_at_5: float | None = None
    recall_at_10: float | None = None
    ndcg_at_10: float | None = None
    mrr: float | None = None
    abstained: bool | None = None
    top1_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.cls,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "ndcg_at_10": self.ndcg_at_10,
            "mrr": self.mrr,
            "abstained": self.abstained,
            "top1_score": self.top1_score,
        }


def score_query(
    query: dict[str, Any],
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    config: RetrievalConfig,
    reranker: Reranker | None = None,
    *,
    abstention_floor: float = DEFAULT_ABSTENTION_FLOOR,
) -> QueryScore:
    result = hybrid_search(
        query["query"],
        settings,
        backend,
        embedder,
        reranker if config.rerank else None,
        domain=None,
        channels=config.channels,
        use_alias=config.use_alias,
        rerank=config.rerank,
    )

    if query["class"] == NEGATIVE_CLASS:
        top1 = result.wiki_card.rrf_score if result.wiki_card is not None else (
            result.hits[0].rrf_score if result.hits else None
        )
        return QueryScore(
            id=query["id"],
            cls=query["class"],
            abstained=_abstained(result.wiki_card, result.hits, abstention_floor),
            top1_score=top1,
        )

    retrieved = _graded_ids(result.wiki_card, result.hits)
    relevant = _relevant_ids(query)
    return QueryScore(
        id=query["id"],
        cls=query["class"],
        recall_at_5=recall_at_k(retrieved, relevant, 5),
        recall_at_10=recall_at_k(retrieved, relevant, 10),
        ndcg_at_10=ndcg_at_k(retrieved, relevant, NDCG_K),
        mrr=mrr_metric(retrieved, relevant),
    )


# ---------------------------------------------------------------------------
# Config-level runs + aggregation
# ---------------------------------------------------------------------------


@dataclass
class ConfigReport:
    config: str
    scores: list[QueryScore] = field(default_factory=list)

    def _graded(self) -> list[QueryScore]:
        return [s for s in self.scores if s.cls != NEGATIVE_CLASS]

    def _negative(self) -> list[QueryScore]:
        return [s for s in self.scores if s.cls == NEGATIVE_CLASS]

    def overall(self) -> dict[str, float]:
        graded = self._graded()
        out = {
            f"recall_at_{k}": _mean(getattr(s, f"recall_at_{k}") for s in graded)
            for k in RECALL_KS
        }
        out["ndcg_at_10"] = _mean(s.ndcg_at_10 for s in graded)
        out["mrr"] = _mean(s.mrr for s in graded)
        negative = self._negative()
        if negative:
            out["abstention_rate"] = _mean(1.0 if s.abstained else 0.0 for s in negative)
        return out

    def per_class(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for cls in GRADED_CLASSES:
            rows = [s for s in self.scores if s.cls == cls]
            if not rows:
                continue
            out[cls] = {
                **{f"recall_at_{k}": _mean(getattr(s, f"recall_at_{k}") for s in rows) for k in RECALL_KS},
                "ndcg_at_10": _mean(s.ndcg_at_10 for s in rows),
                "mrr": _mean(s.mrr for s in rows),
                "n": len(rows),
            }
        negative = self._negative()
        if negative:
            out[NEGATIVE_CLASS] = {
                "abstention_rate": _mean(1.0 if s.abstained else 0.0 for s in negative),
                "n": len(negative),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "overall": self.overall(),
            "per_class": self.per_class(),
            "queries": [s.to_dict() for s in self.scores],
        }


def _mean(values) -> float:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 6) if vals else 0.0


def run_config(
    config: RetrievalConfig,
    queries: Sequence[dict[str, Any]],
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    reranker: Reranker | None = None,
    *,
    abstention_floor: float = DEFAULT_ABSTENTION_FLOOR,
) -> ConfigReport:
    scores = [
        score_query(q, settings, backend, embedder, config, reranker, abstention_floor=abstention_floor)
        for q in queries
    ]
    return ConfigReport(config=config.name, scores=scores)


def run_all_configs(
    queries: Sequence[dict[str, Any]],
    settings: Settings,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    reranker: Reranker | None = None,
    *,
    configs: Sequence[RetrievalConfig] | None = None,
) -> dict[str, ConfigReport]:
    active = list(configs) if configs is not None else available_configs(settings, reranker)[0]
    return {c.name: run_config(c, queries, settings, backend, embedder, reranker) for c in active}


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


def compare_to_baseline(
    current: dict[str, ConfigReport], baseline: dict[str, Any], *, tolerance: float = 0.02
) -> dict[str, Any]:
    """Per-config, per-metric delta of `current` vs. a loaded baseline.json.
    `regressed` lists (config, metric) pairs whose current value dropped by
    more than `tolerance` versus baseline; an empty list means the caller
    should exit 0, non-empty means exit 1.
    """
    deltas: dict[str, Any] = {}
    regressed: list[str] = []
    baseline_configs = baseline.get("configs", {})
    for name, report in current.items():
        base = baseline_configs.get(name)
        if base is None:
            deltas[name] = {"note": "no baseline for this config"}
            continue
        cur_overall = report.overall()
        base_overall = base.get("overall", {})
        metric_deltas = {}
        for metric, cur_val in cur_overall.items():
            base_val = base_overall.get(metric)
            if base_val is None:
                continue
            delta = round(cur_val - base_val, 6)
            metric_deltas[metric] = {"baseline": base_val, "current": cur_val, "delta": delta}
            if metric != "abstention_rate" and delta < -tolerance:
                regressed.append(f"{name}.{metric}: {base_val} -> {cur_val} ({delta:+.4f})")
        deltas[name] = metric_deltas
    return {"deltas": deltas, "regressed": regressed}
