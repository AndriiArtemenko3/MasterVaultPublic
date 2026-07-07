# tests — automated test suite for every subsystem

The pytest suite for MasterVault: 416 tests split into fast, hermetic `unit/` tests that mirror `src/mastervault/` package-for-package, `integration/` tests that need a real storage backend and a synced vault, and shared `fixtures/` (a mini vault and a raw-docs corpus) plus the root `conftest.py`. Unit tests run with mock providers and never touch the network or the real workspace; integration tests exercise whole flows (`sync`, `search`, `ask`, `ingest`, `demo`, `eval`) end to end against sqlite (always) and postgres (when `DATABASE_URL` is reachable).

## Files

| File | Responsibility |
|------|----------------|
| `conftest.py` | Root fixtures: `backend` (parametrized over sqlite + postgres), `pg_test_url` (throwaway per-session postgres DB), `dim`/`model_version`, and `.env` loading for `DATABASE_URL`. |
| `integration/test_storage_backends.py` | Backend contract over both engines: `init_schema`, `needs_embedding` idempotency, cascade deletes, `knn`, lexical/FTS, alias graph, `wipe`. |
| `integration/test_sync.py` | `sync_vault` over `fixtures/mini_vault`, asserting the exact doc/claim/wiki/alias/affects counts land in each backend. |
| `integration/test_search.py` | `hybrid_search` over a synced mini vault plus the `mvault search` CLI, both backends. |
| `integration/test_ask.py` | End-to-end `mvault ask` through the real CLI (init → write vault → sync → ask) on the cold-mock extractive-fallback path. |
| `integration/test_ingest_e2e.py` | Full ingest over `fixtures/raw_docs`: note write + validate, one `sync_vault`, tier-1 alias wikilink, tier-3 new-wiki-page queueing. Builds its own hermetic sqlite env, no shared `backend` fixture. |
| `integration/test_demo_load.py` | Sidecar embedding import + `load_demo_dataset`/`load_embeddings`, plus one CLI run of `mvault demo load` over the shipped `datasets/larkstead` corpus. |
| `integration/test_demo_lifecycle.py` | `mvault demo {status,reset,delete}` lifecycle against the shipped dataset, including hand-mutation then idempotent reset. |
| `integration/test_eval.py` | `mvault eval` CLI over the real Larkstead demo dataset and golden query set; vector-channel tests skip when the local embedder can't load. |
| `unit/storage/` | `StorageBackend` unit-level behavior isolated from the integration matrix. |
| `unit/retrieval/` | Fusion (`test_fuse.py`), MMR diversification (`test_mmr.py`), alias front-door (`test_alias_frontdoor.py`). |
| `unit/pipelines/` | `ask`, `ingest`, `route_claim`, `lint` pipelines with a per-test sqlite backend and mock embedder/LLM (see `conftest.py`). |
| `unit/providers/` | Embedding/LLM/reranker/price providers driven by `provider_doubles.py` recording fakes; no SDKs, no network. |
| `unit/vaultfs/` | Frontmatter parsing, note read/write, the segmenter, and the vault walker. |
| `unit/review/` | Review queue, apply, and `mvault review` CLI; `conftest.py` supplies queue dirs and an item factory. |
| `unit/evals/` | Eval metrics, harness, and faithfulness scoring. |
| `unit/ingest_validate/` | Ingest validation gate and its CLI surface. |
| `unit/contracts/` | Structured-output contract registry and the claims contract. |
| `unit/core/` | Budgets, event bus, and run context. |
| `fixtures/mini_vault/` | A 10-document vault (3 wiki, 5 source, 1 decision, 1 strategy) across 4 domains, used by the sync/search integration tests. |
| `fixtures/raw_docs/` | Four unprocessed source docs (`.md` + `.txt`) for the in-world "Driftwood Supply Co.", fed to the ingest end-to-end test. |

## How it fits

The suite imports the production packages under [../src/mastervault](../src/mastervault) directly (`mastervault.storage`, `mastervault.retrieval`, `mastervault.sync`, `mastervault.cli.app`, and so on), so its input is the code those folders produce; nothing here generates artifacts consumed downstream. The demo and eval integration tests read the shipped corpus in [../datasets/larkstead](../datasets/larkstead) rather than a fixture, which keeps the tests honest about the dataset an end user actually loads. Markers and `testpaths` are configured in [../pyproject.toml](../pyproject.toml) under `[tool.pytest.ini_options]`.

## Key concepts / entry points

- `backend` fixture (`conftest.py:91`) — yields a schema-initialized `StorageBackend` parametrized `["sqlite", "postgres"]`, so one integration test body runs against both engines.
- `pg_test_url` fixture (`conftest.py:71`) — creates a uniquely named throwaway postgres database per session and drops it after; calls `pytest.skip` when `DATABASE_URL` is unset or the server is unreachable, which is how the postgres half of the matrix stays optional.
- `integration` marker (`pyproject.toml:53`) — every file in `integration/` sets `pytestmark = pytest.mark.integration`; run only those with `pytest -m integration`, or skip them with `pytest -m "not integration"`.
- `unit/` mirrors `src/mastervault/` one subfolder per subsystem, so a change in a source package maps to a sibling-named test folder.
- `provider_doubles.py` (`unit/providers/provider_doubles.py:1`) — recording fakes (e.g. `FakeOpenAIEmbeddingsClient`, `FakeAPIError`) that let provider tests assert batching and error handling without any SDK or network.
- Running: `pytest` runs all 416 (postgres tests self-skip without `DATABASE_URL`); set a reachable `DATABASE_URL` (or repo `.env`) to also cover the postgres backend.
