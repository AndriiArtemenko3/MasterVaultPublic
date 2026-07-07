# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Continuous integration (`.github/workflows/ci.yml`): ruff lint, the full
  pytest suite, and a retrieval-eval regression gate that runs `mvault eval
  --compare` against the frozen baseline on every push and pull request.
- A `README.md` in every source package, plus the `docs/`, `tests/`,
  `migrations/`, and `datasets/` trees, so each folder documents its own role.
- Documentation index and a rendered architecture diagram at the top of the
  root README, plus an FAQ / troubleshooting section.
- Packaging metadata for PyPI (`project.urls`, keywords, classifiers) and a
  publish-on-tag workflow (`.github/workflows/publish.yml`).
- Community health files: `CONTRIBUTING.md` (existing), `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue and pull-request templates.
- `scripts/record-demo.sh` to capture an asciinema cast of the 5-minute tour.

### Changed
- Provenance wording in the README now says claims trace to their source note
  (file-level), which matches the data model: a `[claim-id]` resolves to the
  file it was extracted from, not a line offset.
- `mvault ask` prints a one-line note when `llm.provider=mock`, so a keyless
  run makes clear its answer is the deterministic extractive fallback and that
  an API key enables generated synthesis.

### Fixed
- `mvault status` on an uninitialized index now prints a short "run `mvault
  init`" hint instead of a raw traceback.
- Removed dead reranker plumbing from the `ask` pipeline: the reranker was
  threaded through but never engaged there. `search --rerank` and `mvault eval`
  still exercise it.

## [0.1.0] - 2026-07-07

### Added
- Initial release: Markdown-canonical knowledge vault, agentic ingestion and
  `ask` pipelines, hybrid retrieval (RRF + MMR over lexical, vector, and
  wiki-graph channels), SQLite and Postgres backends, a human-in-the-loop
  review queue, and a retrieval-eval harness.
- The Larkstead Goods Co. synthetic dataset with a precomputed embeddings
  sidecar for a keyless demo.

[Unreleased]: https://github.com/AndriiArtemenko3/MasterVaultPublic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AndriiArtemenko3/MasterVaultPublic/releases/tag/v0.1.0
