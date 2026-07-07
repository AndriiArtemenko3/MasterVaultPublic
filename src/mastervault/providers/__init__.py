"""Provider seam: embeddings, LLMs, rerankers, and the price table.

Every external model dependency enters the codebase through this package.
Pipelines depend on the Protocols; factories resolve concrete backends from
Settings, and mock implementations keep the whole stack runnable offline.
"""

from .embedding import (
    CostCapExceeded,
    EmbeddingProvider,
    LocalEmbedding,
    MockEmbedding,
    OpenAIEmbedding,
    get_embedding_provider,
)
from .llm import (
    AnthropicLLM,
    LLMProvider,
    LLMResult,
    Message,
    MockLLM,
    OpenAILLM,
    StructuredOutputError,
    Tier,
    get_llm,
    resolve_model,
)
from .prices import DEFAULT_RATES, PRICES, cost, estimate_tokens, rates_for
from .reranker import (
    Candidate,
    CohereReranker,
    LocalBGEReranker,
    MockReranker,
    NullReranker,
    Reranker,
    RerankerUnavailable,
    get_reranker,
)

__all__ = [
    "DEFAULT_RATES",
    "PRICES",
    "AnthropicLLM",
    "Candidate",
    "CohereReranker",
    "CostCapExceeded",
    "EmbeddingProvider",
    "LLMProvider",
    "LLMResult",
    "LocalBGEReranker",
    "LocalEmbedding",
    "Message",
    "MockEmbedding",
    "MockLLM",
    "MockReranker",
    "NullReranker",
    "OpenAIEmbedding",
    "OpenAILLM",
    "Reranker",
    "RerankerUnavailable",
    "StructuredOutputError",
    "Tier",
    "cost",
    "estimate_tokens",
    "get_embedding_provider",
    "get_llm",
    "get_reranker",
    "rates_for",
    "resolve_model",
]
