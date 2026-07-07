# src/mastervault/providers — Swappable external-model seams

Every external model dependency (embeddings, LLMs, rerankers) enters the codebase here, behind a `Protocol` per capability. Pipelines depend on the protocols, never on a concrete SDK; a `get_*` factory resolves the concrete backend from `Settings` at call time. Each capability ships a mock and a keyless local path, so the whole stack runs offline with no API keys. `prices.py` backs the pre-flight cost estimates that gate spend before any paid call is made.

## Files

| File | Responsibility |
| --- | --- |
| `embedding.py` | `EmbeddingProvider` protocol plus `OpenAIEmbedding` (batching, retry-with-backoff on 408/409/429/5xx, cost-cap pre-flight), `LocalEmbedding` (fastembed `BAAI/bge-small-en-v1.5`, 384-dim, import-guarded), and `MockEmbedding` (deterministic hashing-trick vectors, pure numpy). `get_embedding_provider` resolves `settings.embedding.provider`. |
| `llm.py` | `LLMProvider` protocol plus `AnthropicLLM` (Messages API, structured output via a forced `emit_structured` tool call), `OpenAILLM` (chat.completions, honors `settings.llm.base_url`, json_schema or json_object mode by model), and `MockLLM` (per-task FIFO response registry with deterministic fallback). Structured-output failures retry once with validation errors appended, then raise `StructuredOutputError`. `get_llm` resolves `settings.llm.provider`; `resolve_model` / `Tier` map `small`/`medium`/`large` to configured model ids. |
| `reranker.py` | `Reranker` protocol plus `CohereReranker` (`rerank-v3.5`), `NullReranker` (passthrough, score 0.0), `MockReranker` (query/candidate token Jaccard), and `LocalBGEReranker` (a stub that always raises `RerankerUnavailable` rather than downloading a model). `get_reranker` resolves `settings.reranker.backend`; `auto` picks Cohere when `COHERE_API_KEY` is set, else null. |
| `prices.py` | Static USD-per-1M-token `PRICES` table (snapshot 2026-07) with longest-prefix lookup so dated model snapshots resolve to their family row. `cost()`, `rates_for()`, and a `char/4` `estimate_tokens()` back budget pre-flight; unknown models fall back to conservative `DEFAULT_RATES`. |
| `__init__.py` | Re-exports the protocols, concrete backends, factories, and price helpers as the package's public surface. |

## How it fits

Input is a `Settings` object built by [../config.py](../config.py) from `mastervault.toml` and environment secrets; the factories read `settings.embedding` / `settings.llm` / `settings.reranker` and pull API keys from `Settings` properties only. Downstream, [../ingest](../ingest) calls the embedding and LLM providers to vectorize and draft claims, [../retrieval](../retrieval) calls the embedding provider for query vectors and the reranker to reorder fused candidates, and [../pipelines](../pipelines) drives the LLM through the agentic `ask` loop. The `mock` and `local` defaults are what let a fresh clone run `ingest` and `ask` with no keys.

## Key concepts / entry points

- `get_embedding_provider(settings)` — factory for the embedding seam; raises on an unknown provider name. `embedding.py:239`
- `get_llm(settings)` — factory for the LLM seam, returning `AnthropicLLM` / `OpenAILLM` / `MockLLM`. `llm.py:407`
- `get_reranker(settings)` — factory for the reranker seam; `auto` degrades to `NullReranker` without a Cohere key. `reranker.py:165`
- `LLMResult` — the uniform return of every `complete()` call: raw `text`, optional `parsed` model, resolved `model`, token usage, and estimated `cost_usd`. `llm.py:48`
- Structured-output-one-retry — both real LLM backends validate against a Pydantic `response_model`, retry once with the errors appended, then raise `StructuredOutputError`. `llm.py:189`
- `cost(model, tokens_in, tokens_out)` — the pre-flight estimator shared by the cost-cap guard and by `LLMResult.cost_usd`. `prices.py:44`
