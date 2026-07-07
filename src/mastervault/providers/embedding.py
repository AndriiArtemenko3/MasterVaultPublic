"""Embedding providers behind one Protocol.

Three implementations:

- OpenAIEmbedding — text-embedding-3-small over the OpenAI SDK; batching,
  retry-with-backoff on rate/5xx, and a cost-cap pre-flight.
- LocalEmbedding  — fastembed BAAI/bge-small-en-v1.5, fully offline after the
  first model download. Import-guarded: install with `uv sync --extra local`.
- MockEmbedding   — deterministic hashing-trick pseudo-embeddings for tests
  and pipelines that must never touch the network.

`get_embedding_provider(settings)` resolves `settings.embedding.provider`.
Secrets come only from the environment via `Settings` properties.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from hashlib import blake2b
from typing import Any, Protocol, runtime_checkable

import numpy as np

from mastervault.config import Settings

from .prices import cost as price_cost
from .prices import estimate_tokens

_TOKEN_RE = re.compile(r"[a-z0-9]+")

OPENAI_DEFAULT_MODEL = "text-embedding-3-small"
_OPENAI_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}
LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMENSIONS = 384
MOCK_DIMENSIONS = 384

_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 529})


class CostCapExceeded(RuntimeError):
    """Estimated embedding cost exceeds settings.embedding.cost_cap_usd."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Contract every embedding backend satisfies."""

    @property
    def name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, in input order."""
        ...

    def estimate_cost_usd(self, texts: Sequence[str]) -> float:
        """Pre-flight cost estimate for embedding these texts."""
        ...


def _is_retryable(exc: Exception) -> bool:
    """Rate-limit / transient-server errors are retryable; everything else raises."""
    status = getattr(exc, "status_code", None)
    return status in _RETRYABLE_STATUS


class OpenAIEmbedding:
    """OpenAI embeddings with batching, backoff, and cost-cap pre-flight.

    The client is injected or late-constructed so tests can fake the transport.
    ``sleep`` is injectable for the same reason (backoff without real waits).
    """

    MAX_RETRIES = 4

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sleep = sleep
        configured = settings.embedding.model
        self._model = configured if configured in _OPENAI_DIMENSIONS else OPENAI_DEFAULT_MODEL

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_version(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return _OPENAI_DIMENSIONS[self._model]

    def estimate_cost_usd(self, texts: Sequence[str]) -> float:
        tokens = sum(estimate_tokens(t) for t in texts)
        return price_cost(self._model, tokens, 0)

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._settings.openai_api_key)
        return self._client

    def _create_with_backoff(self, batch: list[str]) -> list[list[float]]:
        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = client.embeddings.create(model=self._model, input=batch)
                return [item.embedding for item in response.data]
            except Exception as exc:
                if not _is_retryable(exc) or attempt == self.MAX_RETRIES:
                    raise
                last_exc = exc
                self._sleep(2.0**attempt)
        raise last_exc  # pragma: no cover — loop always returns or raises

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        estimate = self.estimate_cost_usd(texts)
        cap = self._settings.embedding.cost_cap_usd
        if estimate > cap:
            raise CostCapExceeded(
                f"estimated ${estimate:.4f} exceeds embedding cost cap ${cap:.4f} "
                f"({len(texts)} texts); raise embedding.cost_cap_usd or split the batch"
            )
        batch_size = self._settings.embedding.batch_size
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._create_with_backoff(texts[i : i + batch_size]))
        return out


class LocalEmbedding:
    """fastembed BAAI/bge-small-en-v1.5 (384-dim), lazy model init.

    fastembed is an optional dependency; install with `uv sync --extra local`.
    """

    def __init__(self) -> None:
        self._model: Any | None = None

    @property
    def name(self) -> str:
        return "local"

    @property
    def model_version(self) -> str:
        return LOCAL_MODEL

    @property
    def dimensions(self) -> int:
        return LOCAL_DIMENSIONS

    def estimate_cost_usd(self, texts: Sequence[str]) -> float:
        return 0.0

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise ImportError(
                    "fastembed is not installed. Local embeddings need the "
                    "'local' extra: uv sync --extra local"
                ) from exc
            self._model = TextEmbedding(model_name=LOCAL_MODEL)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        return [np.asarray(vec, dtype=np.float64).tolist() for vec in model.embed(texts)]


class MockEmbedding:
    """Deterministic hashing-trick pseudo-embeddings. Pure numpy, no I/O.

    Each token hashes to an (index, sign) pair which is accumulated into a
    384-dim vector, then L2-normalized. The same text always yields the same
    vector, and texts sharing tokens land closer in cosine space than
    disjoint ones — enough structure for retrieval tests.
    """

    def __init__(self, dimensions: int = MOCK_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_version(self) -> str:
        return "mock-hashing-trick-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def estimate_cost_usd(self, texts: Sequence[str]) -> float:
        return 0.0

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self._dimensions, dtype=np.float64)
        for token in _TOKEN_RE.findall(text.lower()):
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:  # zero-vector guard: no tokens -> zero vector, no NaN
            return vec
        return vec / norm

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t).tolist() for t in texts]


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Resolve settings.embedding.provider to a concrete provider."""
    provider = settings.embedding.provider
    if provider == "openai":
        return OpenAIEmbedding(settings)
    if provider == "local":
        return LocalEmbedding()
    if provider == "mock":
        return MockEmbedding()
    raise ValueError(f"unknown embedding provider: {provider!r}")
