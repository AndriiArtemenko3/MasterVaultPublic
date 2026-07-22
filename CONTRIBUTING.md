# Contributing

## Dev setup

```bash
uv sync --all-extras       # core + rerank (cohere) + dev (pytest, ruff, mypy)
docker compose up -d       # only needed for the postgres half of the test matrix
uv run pytest tests -q
uv run ruff check src tests
```

Tests that touch storage are parametrized over both backends
(`tests/conftest.py`'s `backend` fixture). The postgres half creates a
throwaway database per test session and a throwaway schema per test, so it
never touches a real `mastervault` database; it skips cleanly
(`pytest.skip("DATABASE_URL unset or Postgres unreachable")`) when
`docker compose up -d` has not been run. Tests marked `integration` need a
storage backend; everything else is a pure unit test.

No API key is required to run the suite. Providers default to `local`
embeddings (fastembed, ships in core dependencies) and tests that exercise
`ask`/`ingest`/`lint` construct `MockLLM` directly rather than reading
`ANTHROPIC_API_KEY` from the environment.

## Adding a raw document and re-running ingest

1. Drop a new file under `datasets/larkstead/raw/<domain>/<doc-type>/`, or
   point `mvault ingest` at any directory of your own `.md`/`.txt`/`.pdf`
   files — the demo corpus is not a special case to the pipeline.
2. Preview the plan without spending anything:

   ```bash
   mvault ingest datasets/larkstead/raw/customer-support/ticket --domain customer-support --dry-run
   ```

   This needs no LLM key: it estimates cost from token counts and never
   calls a model. Already-ingested files are skipped by provenance hash, so
   a dry run against the shipped raw corpus after `demo load` reports 0
   units planned; point it at something new to see a nonzero plan.
3. Run it for real with a key set (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`):

   ```bash
   mvault ingest <path> --domain <domain> --budget 1.00
   ```

   Extraction runs on the `small` model tier (`llm.model_small`). The real
   build's four full-domain ingest runs over 352 documents cost $0.2213
   total against `gpt-4o-mini` — budget a few cents per document, not
   dollars.
4. `mvault review list` to see what routed to tier 2 (batch-reviewable) or
   tier 3 (new concept or contradiction, one-by-one confirm only). Nothing
   from ingest reaches the wiki layer without going through
   `mvault review approve`.
5. If you changed anything that a golden query resolves against, re-run
   `mvault eval` before committing; it exits 1 if any `relevant_docs` or
   `relevant_claims` entry in the golden set no longer resolves against the
   live corpus.

## Golden-query rules

`datasets/larkstead/golden/queries.yaml` is hand-authored, not generated:
every `relevant_docs` path and `relevant_claims` id was grep-verified
against the real corpus at authoring time. `mvault eval` re-verifies this on
every run (`resolve_golden_set`, written to `golden/resolved.yaml`) and
treats a single unresolved id as a build error, not a soft warning — a
golden set that silently drifts from the corpus it grades is worse than no
golden set.

Five classes, each graded differently:

- `easy-lexical`, `semantic-paraphrase`, `cross-domain-multi-hop`,
  `contradiction` — graded on `recall@5`, `recall@10`, `nDCG@10`, `MRR`
  against `relevant_docs`.
- `negative-no-answer` — `relevant_docs: []` and `relevant_claims: []` by
  construction (the query was confirmed absent from the corpus by grep at
  authoring time). Graded on `abstained`: true when the top hit's fused RRF
  score falls under `DEFAULT_ABSTENTION_FLOOR` (0.02), or when there are no
  hits at all. A pinned wiki alias card is never counted as an abstention,
  even a wrong one — a confident match to the wrong concept is a different
  failure mode than declining to answer, and the metric keeps them separate
  on purpose.

  Read that number narrowly. It says "no channel agreed on a top hit", which
  is true of unanswerable queries *and* of hard-but-answerable ones: measured
  across the 52-query set, 7 of 8 negatives and 11 of 12 semantic-paraphrase
  queries share the same modal RRF top-1 score of 1/61. It is a retrieval
  diagnostic, not a claim that `mvault ask` declines to answer — it does not.
  See the `ask-negative-01` limitation in `golden/ask_cases.yaml`.

## End-to-end ask evaluation

`datasets/larkstead/golden/ask_cases.yaml` is the second, separate gate, run
by `mvault ask-eval`. Where `mvault eval` grades retrieval ranking, this
grades the whole `ask` pipeline: multi-round retrieval, the sufficiency
judge, its mechanical guards, grounded synthesis, and the citation gate. The
two are kept apart so neither number moves for the other's reasons.

Cases are keyless and deterministic: each drives the real pipeline with a
scripted mock provider and is graded mechanically — set membership, counts,
booleans — never by an LLM judge. Two checks run on every case whether or not
they are listed under `expect`: no answer may cite a record that was never
retrieved, and the same case run twice must answer identically.

Adding a case: give it an `id`, one of the classes in
`mastervault.evals.ask_harness.ASK_CASE_CLASSES`, a `question`, and an
`expect` block whose assertions would actually fail if the behaviour it names
broke. Script `sufficiency_judge` / `grounded_synthesis` turns only when the
case needs a path the cold mock cannot reach (malformed output, a forged
citation, a looping judge). If a case pins behaviour that is not what we would
want, say so in `known_limitation` rather than asserting the wrong thing — the
report prints those, and a test refuses a limitation without an explanation.
Then run `mvault ask-eval`, and refresh the frozen baseline with
`mvault ask-eval --json > datasets/larkstead/golden/ask_baseline.json` only
when the new result is deliberate.

Adding a query: pick a real question, grep-confirm the answer's location (or
its absence, for a negative), add it to `queries.yaml` with a `notes:` field
explaining why it belongs in that class, then run `mvault eval` and check
`resolve_report.ok` before committing. Do not add a query whose relevance
you have not personally verified against the live corpus.

## Out of scope

- Real trademarks, real company names, or real people anywhere in
  `datasets/larkstead/`. The `banned_strings` denylist in
  `bible/company.yaml` exists to catch this mechanically; do not add a
  document that needs an entry removed from that list.
- A `local-bge` reranker implementation. The stub in
  `providers/reranker.py` deliberately raises rather than silently
  downloading a model; if you need a local cross-encoder, use `cohere` or
  `mock` instead, or open an issue to discuss the download-on-first-use
  tradeoff before implementing it.
- Committing `.env`, real API keys, or a populated `vault_workspace/`
  (workspaces are gitignored; if yours somehow isn't, that's a bug in
  `.gitignore`, not a green light to commit it).
- Changes to `mastervault.toml`'s shipped defaults without a corresponding
  test update — `llm.provider = "anthropic"` and `embedding.provider =
  "local"` are load-bearing for the test suite's keyless-by-default
  assumptions.
- New CLI subcommands that bypass the review queue for anything landing in
  `wiki/` or `decisions/`. Tier 1 auto-apply is reserved for wikilink
  insertion against an already-confirmed alias; everything else earns a
  human a decision.
