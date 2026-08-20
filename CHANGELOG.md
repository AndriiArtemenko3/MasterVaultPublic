# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Recorded actual-impact inference for reviewed Step 10a workloads. Providers
  return only semantic decisions and character offsets; exact SourceNote spans,
  provenance, and content identities are derived locally. Non-empty LIVE or
  REPLAY outcomes commit through the existing durable evidence repository and
  Step 10b is reconstructed only from freshly reopened committed outcomes,
  while a zero-question workload creates neither a provider call nor a fake
  evidence batch.
- A v2 managed-analysis binding that carries exact committed actual-impact
  evidence into all-target review, while preserving v1 canonical bytes for
  historical reads. Managed review now requires one exact output per target
  and re-resolves that evidence at every SQLite authority boundary.
- Recorded revision planning over the exact Step 10b run. Impact selects
  affected/no-change locally, unresolved work blocks the whole set, and a
  provider can return only bounded Unicode-character edits or explicit
  no-change evidence. LIVE execution permits one correction retry; REPLAY
  rematerializes exact prior LIVE evidence without a provider call. MasterVault
  derives plans, no-change cards, byte-grounded citations, successor Markdown
  SourceNotes, claim reconciliation, paths, and hashes locally, commits all
  target outcomes as one existing evidence batch, and writes only create-only,
  manifest-gated inert staging. That evidence now feeds the SQLite-authoritative
  managed-review and first-successor activation additions below; public
  change-control CLI integration and LangGraph expansion remain deferred.
- SQLite-authoritative managed-review admission and decisions over the complete
  revision-planning and staging evidence set. Every authority boundary freshly
  reopens and reproduces the exact evidence, while managed `EDIT` execution
  remains outside this slice.
- A SQLite-only managed-generation path that rederives the complete
  historical/current projection from the exact review decision, adopts the
  governing source in place, publishes only approved downstream replacements
  create-only, builds and seals an isolated exact index, atomically activates
  one successor from generation zero, and reopens the active index fail-closed.
  Exact retries and restart reconciliation reuse immutable effect, readiness,
  and activation receipts; adoption-only activation creates no fake
  publication events.
- A generic SQLite bootstrap boundary for an operator-specified existing v0.2
  workspace. Its explicit versioned manifest selects managed SourceNotes and
  supplies exact document identity, dates, role, authority, and full-SHA
  governing-source evidence without inference. Explicit runtime
  `BootstrapSourceRoot` bindings preserve ordinary external ingestion
  provenance without copying or rerendering its raw source or SourceNote;
  durable authority retains only a logical root/path/provenance binding and a
  safe content-addressed locator. PDF asset/parsed provenance is
  transitively bound by the exact typed SourceNote and reopened by its normal
  projection verifier. A separate closed inventory covers every indexable
  legacy-vault note (source, wiki, decision, and strategy) through bounded
  stable no-follow reads; invalid, skipped, external, symlinked, duplicate, or
  changed inputs fail closed.
- Read-only legacy-index attestation and generic generation-zero authority.
  Exact schema/migration and embedding identity, complete document/record/FTS/
  vector coverage, physical bytes, and deterministic logical contents bind the
  index to the workspace inventory. SQLite initializes authority only after
  freshly reopening the immutable bootstrap, inventory, and index receipts;
  it never rewrites the legacy vault/index, and exact lost-ack retries replay
  the same evidence.
- A synchronous library-level application boundary for generic bootstrap and
  durable status, with non-authoritative operator-run navigation and stable
  usage, review-required, conflict-or-stale-authority, integrity, and
  unsupported error categories. Linked artifacts still require verification
  at their owning authority boundary. Later operations must extend this same
  façade. That bootstrap slice did not itself add public change-control
  commands or generation-aware ordinary queries; the read-only follow-on below
  now supplies the latter.
- Versioned, read-only query-generation resolution for ordinary `search`,
  `claims`, `wiki`, and `ask`. Their shared `--generation` selector supports
  `auto`, exact generation zero (`legacy`), required active authority
  (`active`), and a bounded exact `mgeneration:<sha256>` read. Generic
  generation zero reopens the exact attested legacy SQLite index; generation
  one reuses the immutable managed serving boundary and freshly reconstructs
  its resolver from durable evidence. Human output names the serving
  generation, while JSON search/ask output includes stable path-free schema-v1
  authority/index/embedding metadata. Configured missing, corrupt, stale, or
  mismatched authority fails closed without legacy fallback. No managed state
  or locators preserves the existing unmanaged v0.2 SQLite/PostgreSQL `auto`
  path. Managed selection remains SQLite-only, and managed `ask` creates no
  run directory, event log, round artifacts, summary, authority, or navigation
  writes; unmanaged ask keeps its existing run persistence. This slice spans
  two coupled trust seams that must be reviewed together: resolved-query
  lifetime/authority coordination, and the complete-corpus index substrate
  required for a workspace-origin successor to preserve every unselected
  source, wiki, decision, and strategy note while replacing managed paths.
- Descriptor-pinned bootstrap and authority-database handoffs. Workspace
  evidence must be owner-controlled, single-link and non-writable by group or
  others; the exact workspace and legacy-index descriptors remain live through
  generation-zero commit. The SQLite authority path is private and
  inode-checked, while status is immutable/query-only and never creates or
  migrates state.

- The first v0.3 document-intelligence vertical slice for clean digital PDFs:
  full-byte SHA-256 source identity, immutable content-addressed assets,
  deterministic schema-v1 page/block artefacts, and explicit rejection of
  corrupt, encrypted, or wholly textless inputs.
- A separate page-grounded PDF claim contract. Models propose a block and
  supporting quote; MasterVault resolves authoritative pages and character
  offsets, rejects unsupported evidence before canonical publication, and
  preserves legacy Markdown/text ingestion unchanged.
- Verified evidence hydration for claim search hits and
  `mvault evidence show <claim-id> [--json]` for inspecting the exact immutable
  asset, parser artefact, page, block, and quote behind an accepted PDF claim.
- A deterministic two-page Larkstead clean-digital PDF fixture, manifest,
  generator check, golden page evidence, and end-to-end coverage from PDF
  ingestion through retrieval and CLI inspection. It is a development
  parser-smoke fixture, not a held-out performance benchmark.
- A bounded, family-separated Larkstead layout benchmark covering 6 semantic
  families and 24 deterministic PDFs, with runtime/golden isolation, separate
  semantic/render hashes, and an evaluator-only SL2 temporal-impact seed. This
  is an evaluation contract and implementation foundation, not a claim of
  measured parser or change-control performance.
- A deterministic `mvault pdf-eval` runner for the frozen layout benchmark,
  with byte-identity preflight, exact ambiguity-safe matching, explicit metric
  counts, per-rendition failures, and development-by-default split isolation.
  It publishes no frozen parser score or performance claim.

- Ordered schema-v2 migrations for SQLite and PostgreSQL, with an explicit
  `schema_migrations` ledger and upgrade coverage from a representative v0.2
  schema-v1 workspace.
- A deterministic Larkstead corpus ledger: all 372 raw files are now exactly
  accounted for as 352 processed, 0 excluded, and 20 retained historical
  no-output observations of unknown cause, without inventing the lost
  per-unit stage or failure mechanism.
- Machine-readable retrieval/ask baseline provenance covering the source tree,
  corpus ledger and eval input, dependency lock, config, prompts, schema
  migrations, models, runtime/platform, Git state, and exact reproduction
  commands.
- A dataset-local CC BY 4.0 scope and attribution file, while repository code
  (including dataset QA programs) remains Apache-2.0.

### Fixed

- Approving a Markdown review change now synchronizes the derived index before
  reporting success. On handled reindex/archive exceptions it rolls back only
  when the proposal is still live; a concurrent human edit is preserved,
  reindexed best-effort, and left as a visible conflict. This does not claim
  process-crash atomicity.
- Public setup and dataset documentation now reflects the source-only install,
  shipped mock provider, first local-model download, zero current broken
  `affects:` links, actual four-item contradiction queue, current eval suite,
  and the 352 processed / 20 historical-no-output split.

The declared package version remains `0.2.0`. Everything in this Unreleased
section is v0.3 work in progress: public change-control/operator commands,
targeted post-change regressions, final JSON/Markdown audit reports, managed
`EDIT` execution, PostgreSQL managed-generation parity, the keyless
change-control demo, and the v0.3 release remain deferred. This entry does not
claim a v0.3 release, tag, package publication, or deployment.

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
  embeddings sidecar, the four pending semantic-lint review items, and every
  retrieval metric are unchanged.
- **Review application could write outside the workspace.** A review item's
  `target:` is produced by an LLM-driven pipeline but was joined to the vault
  root directly, so `../..` or an absolute path could overwrite any file the
  process could reach. Targets now resolve through
  `mastervault.core.paths.resolve_within`, which also rejects a symlink that
  already points out of the vault at resolution time, and the write opens with
  `O_NOFOLLOW` so the final component turning into a symlink afterwards is
  refused too. A rejected target is marked `conflict` and nothing is written.
  Parent-directory swap races and hard links stay outside the enforced boundary
  -- both need concurrent local write access to the vault; see SECURITY.md.
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
- Exactly-tied k-NN vectors come back in whichever order the index produces, so
  their relative order can differ between SQLite and Postgres. A `record_id`
  tie-break was implemented and then **deliberately reverted**: it moved overall
  recall@5 from 0.591 to 0.580 by reshuffling tied groups, and a retrieval
  change that regresses recall@5 is not adopted here even when its motivation is
  determinism rather than ranking. The frozen baseline still reproduces exactly
  (Δ+0.000 on every metric of every config).
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
