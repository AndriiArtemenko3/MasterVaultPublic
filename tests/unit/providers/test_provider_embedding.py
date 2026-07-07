"""Embedding providers: mock determinism, batching, cost cap, backoff, factory."""

from __future__ import annotations

import math

import numpy as np
import pytest
from provider_doubles import FakeAPIError, FakeOpenAIEmbeddingsClient

from mastervault.providers.embedding import (
    CostCapExceeded,
    LocalEmbedding,
    MockEmbedding,
    OpenAIEmbedding,
    get_embedding_provider,
)


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))


# ---------------------------------------------------------------------------
# MockEmbedding
# ---------------------------------------------------------------------------


def test_mock_is_deterministic() -> None:
    provider = MockEmbedding()
    first = provider.embed(["the quick brown fox"])
    second = provider.embed(["the quick brown fox"])
    assert first == second


def test_mock_dimensions_and_norm() -> None:
    provider = MockEmbedding()
    [vec] = provider.embed(["hello world"])
    assert len(vec) == provider.dimensions == 384
    assert math.isclose(float(np.linalg.norm(vec)), 1.0, rel_tol=1e-9)


def test_mock_shared_tokens_are_closer_than_disjoint() -> None:
    provider = MockEmbedding()
    base, overlap, disjoint = provider.embed(
        [
            "postgres index tuning for retrieval",
            "postgres index maintenance guide",
            "sourdough starter hydration schedule",
        ]
    )
    assert _cosine(base, overlap) > _cosine(base, disjoint)


def test_mock_zero_vector_guard() -> None:
    provider = MockEmbedding()
    [vec] = provider.embed(["!!! ... ###"])  # no alphanumeric tokens
    assert vec == [0.0] * provider.dimensions
    assert not any(math.isnan(v) for v in vec)


def test_mock_costs_nothing() -> None:
    assert MockEmbedding().estimate_cost_usd(["a", "b"]) == 0.0


# ---------------------------------------------------------------------------
# OpenAIEmbedding
# ---------------------------------------------------------------------------


def _openai_settings(make_settings, *, batch_size: int = 2, cost_cap_usd: float = 1.0):
    return make_settings(
        embedding={
            "provider": "openai",
            "model": "text-embedding-3-small",
            "batch_size": batch_size,
            "cost_cap_usd": cost_cap_usd,
        }
    )


def test_openai_batching_splits_and_preserves_order(make_settings) -> None:
    settings = _openai_settings(make_settings, batch_size=2)
    fake = FakeOpenAIEmbeddingsClient()
    provider = OpenAIEmbedding(settings, client=fake)

    texts = [f"text-{i}" for i in range(5)]
    out = provider.embed(texts)

    assert [len(batch) for batch in fake.calls] == [2, 2, 1]
    assert fake.calls[0] == ["text-0", "text-1"]
    assert fake.calls[2] == ["text-4"]
    # The fake encodes the trailing index into the vector: order must survive.
    assert out == [[0.0], [1.0], [2.0], [3.0], [4.0]]


def test_openai_cost_preflight_raises_without_calling_transport(make_settings) -> None:
    settings = _openai_settings(make_settings, cost_cap_usd=0.0)
    fake = FakeOpenAIEmbeddingsClient()
    provider = OpenAIEmbedding(settings, client=fake)

    with pytest.raises(CostCapExceeded):
        provider.embed(["anything at all"])
    assert fake.calls == []


def test_openai_empty_input_short_circuits(make_settings) -> None:
    fake = FakeOpenAIEmbeddingsClient()
    provider = OpenAIEmbedding(_openai_settings(make_settings), client=fake)
    assert provider.embed([]) == []
    assert fake.calls == []


def test_openai_retries_rate_limit_then_succeeds(make_settings) -> None:
    settings = _openai_settings(make_settings)
    fake = FakeOpenAIEmbeddingsClient(failures=[FakeAPIError(429), FakeAPIError(503)])
    sleeps: list[float] = []
    provider = OpenAIEmbedding(settings, client=fake, sleep=sleeps.append)

    out = provider.embed(["text-7"])

    assert out == [[7.0]]
    assert len(fake.calls) == 3  # two failures + one success
    assert sleeps == [1.0, 2.0]  # exponential backoff 2**0, 2**1


def test_openai_gives_up_after_max_retries(make_settings) -> None:
    settings = _openai_settings(make_settings)
    failures = [FakeAPIError(429)] * (OpenAIEmbedding.MAX_RETRIES + 1)
    fake = FakeOpenAIEmbeddingsClient(failures=failures)
    provider = OpenAIEmbedding(settings, client=fake, sleep=lambda _s: None)

    with pytest.raises(FakeAPIError):
        provider.embed(["text-1"])
    assert len(fake.calls) == OpenAIEmbedding.MAX_RETRIES + 1


def test_openai_does_not_retry_client_errors(make_settings) -> None:
    settings = _openai_settings(make_settings)
    fake = FakeOpenAIEmbeddingsClient(failures=[FakeAPIError(401)])
    provider = OpenAIEmbedding(settings, client=fake, sleep=lambda _s: None)

    with pytest.raises(FakeAPIError):
        provider.embed(["text-1"])
    assert len(fake.calls) == 1


def test_openai_metadata_and_cost_estimate(make_settings) -> None:
    provider = OpenAIEmbedding(_openai_settings(make_settings))
    assert provider.name == "openai"
    assert provider.model_version == "text-embedding-3-small"
    assert provider.dimensions == 1536
    assert provider.estimate_cost_usd(["hello world"]) > 0.0


def test_openai_defaults_model_when_config_holds_local_model(make_settings) -> None:
    settings = make_settings(
        embedding={"provider": "openai", "model": "BAAI/bge-small-en-v1.5"}
    )
    provider = OpenAIEmbedding(settings)
    assert provider.model_version == "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Factory + local metadata (no model download)
# ---------------------------------------------------------------------------


def test_factory_resolves_each_provider(make_settings) -> None:
    assert isinstance(
        get_embedding_provider(make_settings(embedding={"provider": "mock"})), MockEmbedding
    )
    assert isinstance(
        get_embedding_provider(make_settings(embedding={"provider": "openai"})), OpenAIEmbedding
    )
    assert isinstance(
        get_embedding_provider(make_settings(embedding={"provider": "local"})), LocalEmbedding
    )


def test_local_metadata_without_loading_model() -> None:
    provider = LocalEmbedding()
    assert provider.name == "local"
    assert provider.model_version == "BAAI/bge-small-en-v1.5"
    assert provider.dimensions == 384
    assert provider.estimate_cost_usd(["a"]) == 0.0
