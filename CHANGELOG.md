# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-22

A reliability and engineering release. No new product features: the work went
into typed boundaries, real backend parity, corpus integrity, end-to-end
evaluation, security boundaries, and the release path.

### Added
- **End-to-end ask evaluation** (`mvault ask-eval`). 14 frozen cases across 11
  classes drive the real `ask` pipeline keyless and deterministically with a
  scripted mock provider, graded mechanically (no LLM-as-judge). Two invariants
  hold on every case: no answer may cite a record that was never retrieved, and
  the same case run twice must answer identically. `--json` for CI and
  `--compare` against `datasets/larkstead/golden/ask_baseline.json`. Kept
  separate from `mvault eval`, which still grades retrieval ranking only.
- **A dedicated PostgreSQL CI job** on a `pgvector/pgvector:pg17` service
  container. `MV_REQUIRE_POSTGRES=1` turns "postgres unreachable -> skip" into a
  hard failure and fails the run if any postgres-backed test skipped anyway, so
  the job cannot pass green while testing nothing. All 45 previously-skipped
  tests now execute.
- **Backend parity tests** for transaction rollback, `drop_schema`, embedding
  replacement, and zero-vector rejection, all running against both backends.
- **Corpus ship gate** (`tests/integration/test_dataset_integrity.py`): the
  shipped dataset must have zero broken `affects:` references and zero
  duplicate claim ids, and re-running the pipeline's own reconciliation over it
  must be a byte-for-byte no-op.
- **Security regression tests** (`tests/unit/security/`) for every boundary
  audited below.
- **Coverage measurement** (branch coverage over `src/mastervault`) with a
  regression floor in CI: measured 83% sqlite-only / 85% against postgres, floor
  set at 82%. No tests were added to move that number.
- A package-build
  job (`scripts/check-package.sh`) that builds the sdist and wheel, installs the
  wheel into a clean environment, runs a CLI smoke flow from the installed
  artifact, and scans both distributions for workspaces, caches, secrets and
  test output.
- `StorageBackend.drop_schema()` and `.name`, plus a `FileBackedBackend`
  capability protocol.
- `CONTRIBUTING.md` gains a section on authoring ask-eval cases, and qualifies
  how the retrieval eval's `abstention_rate` should be read.
- A `README.md` in every source package, plus the `docs/`, `tests/`,
  `migrations/`, and `datasets/` trees, so each folder documents its own role.
- Documentation index and a rendered architecture diagram at the top of the
  root README, plus an FAQ / troubleshooting section.
- Packaging metadata for PyPI (`project.urls`, keywords, classifiers) and a
  publish-on-tag workflow (`.github/workflows/publish.yml`).
- Community health files: `CONTRIBUTING.md` (existing), `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue and pull-request templates.
- `scripts/record-demo.sh` to capture an asciinema cast of the 5-minute tour.

  <!-- the four bullets above landed after the 0.1.0 release and were sitting unreleased; they ship as part of 0.2.0. -->

### Fixed
- **The shipped demo failed its own validator.** `mvault lint
  --mechanical-only` reported 75 broken `affects:` references across 70 slugs
  and 34 files. `affects:` was written from the extractor's proposed concept
  labels and never reconciled against the wiki entries that exist, so invented
  labels dangled permanently. `mastervault.ingest.affects.reconcile_affects()`
  now runs at the end of the ingest route phase and drops unresolvable slugs
  (it never remaps: guessing the target of an invented label would fabricate a
  link). The corpus was repaired with that same function. Counts, the
  embeddings sidecar, the four seeded contradictions and every retrieval metric
  are unchanged.
- **Review application could write outside the workspace.** A review item's
  `target:` is produced by an LLM-driven pipeline but was joined to the vault
  root directly, so `../..` or an absolute path could overwrite any file the
  process could reach. Targets now resolve through
  `mastervault.core.paths.resolve_within`, which also rejects symlinks that
  leave the vault; a rejected target is marked `conflict` and nothing is
  written.
- **PostgreSQL was unusable from an installed wheel.** The schema SQL lived at
  the repo root and was not packaged, so `mvault init` against Postgres failed
  with "no migrations found". It now ships as package data at
  `src/mastervault/storage/migrations/pg/`.
- **PostgreSQL silently accepted the zero vector**, where `<=>` yields NaN and
  the HNSW cosine index drops the row -- leaving a record that
  `needs_embedding()` believed was indexed and `knn()` could never return.
  Both backends now reject it before writing anything.
- **Document content could forge a citation.** The generative path was
  citation-gated but the extractive path quoted document text verbatim, so a
  document containing `[claim:invented-01]` had that token rendered into the
  answer. Record-shaped tokens outside the evidence pool are now stripped from
  quoted text with a warning.
- **Malformed input escaped as a library traceback.** A corrupt PDF or a binary
  file with a `.txt` suffix now raises `UnreadableDocument` naming the file and
  the remedy, and `mvault ingest` records and skips it instead of losing the
  whole run.
- 30 mypy errors across 10 files, fixed at the source. Along the way: an
  unreachable `raise None` in the embedding retry loop, a `TypeError` on an
  empty JSON-Schema type union, and an unvalidated tool-use payload at the
  Anthropic boundary.
- `docs/ARCHITECTURE.md` said the default LLM provider was `anthropic`; the
  configuration has defaulted to the keyless `mock` provider since 0.1.x.
- `mvault status` on an uninitialized index now prints a short "run `mvault
  init`" hint instead of a raw traceback.
- Removed dead reranker plumbing from the `ask` pipeline: the reranker was
  threaded through but never engaged there. `search --rerank` and `mvault eval`
  still exercise it.

### Changed
- **mypy is blocking in CI** and runs with `check_untyped_defs`,
  `no_implicit_optional`, `warn_return_any`, `warn_unused_ignores` and
  `warn_redundant_casts`, plus `disallow_untyped_defs` for storage, retrieval,
  providers and pipelines. Zero errors across 74 files.
- `get_claims()` / `get_chunks()` return `HydratedClaimRow` / `HydratedChunkRow`
  with non-optional `rel_path`/`domain`; the write transports no longer carry
  those fields at all.
- `mvault drop` and `mvault demo delete` no longer reach through the storage
  abstraction for a driver handle. CLI output is unchanged.
- Untrusted document text is now structurally delimited in every prompt that
  carries it. This is **not** a claim that prompt injection is solved --
  delimiting removes structural ambiguity, not model behaviour. See
  `SECURITY.md` for the enforced/not-enforced split.
- Provenance wording in the README now says claims trace to their source note
  (file-level), which matches the data model: a `[claim-id]` resolves to the
  file it was extracted from, not a line offset.
- `mvault ask` prints a one-line note when `llm.provider=mock`, so a keyless
  run makes clear its answer is the deterministic extractive fallback and that
  an API key enables generated synthesis.

### Known limitations
- **`ask` does not abstain** on a question the corpus cannot answer; it returns
  a low-confidence extractive answer from the nearest records. Two candidate
  score gates were measured against the 52-query golden set and both were
  rejected as harmful: negatives and hard-but-answerable paraphrases occupy the
  same score range (under RRF, 7 of 8 negatives and 11 of 12 paraphrases sit at
  the same modal value; under raw cosine, negatives score *higher* than the
  hardest real questions). No retrieval or answer-policy change was adopted.
- The novelty-floor guard is not reachable against the demo corpus at `k=10`;
  it remains covered by a unit test against a fixture smaller than `k`.
- The demo dataset ships with the repository, not the wheel, so `mvault demo
  load` from an installed package reports where to get it rather than working.
- `hybrid+rerank` is still unevaluated without a Cohere key.


## [0.1.0] - 2026-07-07

### Added
- Initial release: Markdown-canonical knowledge vault, agentic ingestion and
  `ask` pipelines, hybrid retrieval (RRF + MMR over lexical, vector, and
  wiki-graph channels), SQLite and Postgres backends, a human-in-the-loop
  review queue, and a retrieval-eval harness.
- The Larkstead Goods Co. synthetic dataset with a precomputed embeddings
  sidecar for a keyless demo.

<!-- No git tags or GitHub releases have been published for this project yet,
     so the entries below link to the commit history rather than to
     compare/<tag> or releases/tag/<tag> URLs that would 404. Replace them with
     tag links once v0.1.0 / v0.2.0 are actually tagged and released. -->
[Unreleased]: https://github.com/AndriiArtemenko3/MasterVaultPublic/commits/main
[0.2.0]: https://github.com/AndriiArtemenko3/MasterVaultPublic/commits/main
[0.1.0]: https://github.com/AndriiArtemenko3/MasterVaultPublic/commits/main
