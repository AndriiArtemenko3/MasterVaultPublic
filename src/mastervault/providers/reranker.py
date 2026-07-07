"""Reranker backends behind one Protocol.

- CohereReranker   — Cohere rerank-v3.5 API (key from settings.cohere_api_key;
  the SDK ships in the 'rerank' extra: uv sync --extra rerank).
- NullReranker     — passthrough; preserves input order, score 0.0.
- MockReranker     — deterministic query-token-overlap Jaccard scoring.
- LocalBGEReranker — import-guarded stub; raises with an install hint and
  never downloads models.

`get_reranker(settings)` resolves settings.reranker.backend; "auto" picks
Cohere when COHERE_API_KEY is present, else the null passthrough.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, NamedTuple, Protocol, runtime_checkable

from mastervault.config import Settings

COHERE_MODEL = "rerank-v3.5"

_TOKEN_RE = re.compile(r"\w+")


class RerankerUnavailable(RuntimeError):
    """The requested reranker backend cannot run in this environment."""


class Candidate(NamedTuple):
    id: str
    text: str


@runtime_checkable
class Reranker(Protocol):
    """Contract every reranker backend satisfies."""

    @property
    def name(self) -> str: ...

    def rerank(
        self, query: str, candidates: Sequence[Candidate], top_k: int
    ) -> list[tuple[str, float]]:
        """(id, score) pairs sorted by score descending, truncated to top_k."""
        ...


class CohereReranker:
    """Cohere rerank-v3.5. Client injected or late-constructed."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def name(self) -> str:
        return "cohere"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import cohere
            except ImportError as exc:
                raise RerankerUnavailable(
                    "cohere sdk is not installed. Install the 'rerank' extra: "
                    "uv sync --extra rerank"
                ) from exc
            self._client = cohere.ClientV2(api_key=self._settings.cohere_api_key)
        return self._client

    def rerank(
        self, query: str, candidates: Sequence[Candidate], top_k: int
    ) -> list[tuple[str, float]]:
        if not candidates:
            return []
        response = self._get_client().rerank(
            model=COHERE_MODEL,
            query=query,
            documents=[text for _, text in candidates],
            top_n=top_k,
        )
        scored = [
            (candidates[result.index][0], float(result.relevance_score))
            for result in response.results
        ]
        scored.sort(key=lambda pair: -pair[1])
        return scored[:top_k]


class NullReranker:
    """Passthrough: input order preserved, every score 0.0."""

    @property
    def name(self) -> str:
        return "null"

    def rerank(
        self, query: str, candidates: Sequence[Candidate], top_k: int
    ) -> list[tuple[str, float]]:
        return [(candidate_id, 0.0) for candidate_id, _ in candidates[:top_k]]


class MockReranker:
    """Deterministic Jaccard scoring on query/candidate token sets.

    score = |query_tokens & candidate_tokens| / |query_tokens | candidate_tokens|.
    Ties preserve input order, so results are fully reproducible.
    """

    @property
    def name(self) -> str:
        return "mock"

    def rerank(
        self, query: str, candidates: Sequence[Candidate], top_k: int
    ) -> list[tuple[str, float]]:
        query_tokens = {t.lower() for t in _TOKEN_RE.findall(query)}
        scored: list[tuple[int, str, float]] = []
        for position, (candidate_id, text) in enumerate(candidates):
            candidate_tokens = {t.lower() for t in _TOKEN_RE.findall(text)}
            union = query_tokens | candidate_tokens
            score = len(query_tokens & candidate_tokens) / len(union) if union else 0.0
            scored.append((position, candidate_id, score))
        scored.sort(key=lambda item: (-item[2], item[0]))
        return [(candidate_id, score) for _, candidate_id, score in scored[:top_k]]


class LocalBGEReranker:
    """Stub for a local BGE cross-encoder. Never downloads models.

    Construction raises: with torch/transformers absent, an install hint;
    with them present, a not-yet-supported notice. Either way callers get a
    RerankerUnavailable rather than a silent fallback.
    """

    def __init__(self) -> None:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as exc:
            raise RerankerUnavailable(
                "local-bge reranker needs torch + transformers, which mastervault "
                "does not ship. Install them into your environment "
                "(pip install torch transformers) or set reranker.backend to "
                "'cohere', 'mock', or 'null'."
            ) from exc
        raise RerankerUnavailable(
            "local-bge reranker is not implemented in this release (model download "
            "is deliberately disabled). Set reranker.backend to 'cohere', 'mock', "
            "or 'null'."
        )

    @property
    def name(self) -> str:  # pragma: no cover — construction always raises
        return "local-bge"

    def rerank(
        self, query: str, candidates: Sequence[Candidate], top_k: int
    ) -> list[tuple[str, float]]:  # pragma: no cover — construction always raises
        raise RerankerUnavailable("local-bge reranker is unavailable")


def get_reranker(settings: Settings) -> Reranker:
    """Resolve settings.reranker.backend to a concrete backend.

    "auto" prefers Cohere when COHERE_API_KEY is set, else the null passthrough.
    """
    backend = settings.reranker.backend
    if backend == "auto":
        if settings.cohere_api_key:
            return CohereReranker(settings)
        return NullReranker()
    if backend == "cohere":
        if not settings.cohere_api_key:
            raise RerankerUnavailable(
                "reranker.backend='cohere' but COHERE_API_KEY is not set in the environment"
            )
        return CohereReranker(settings)
    if backend == "null":
        return NullReranker()
    if backend == "mock":
        return MockReranker()
    if backend == "local-bge":
        return LocalBGEReranker()
    raise ValueError(f"unknown reranker backend: {backend!r}")
