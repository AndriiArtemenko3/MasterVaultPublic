# Security Policy

## Supported versions

MasterVault is at `0.2.x` and pre-1.0. Security fixes land on the latest
release only.

| Version | Supported |
|---------|-----------|
| 0.2.x   | yes       |
| < 0.2   | no        |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Instead, either:

- open a [private security advisory](https://github.com/AndriiArtemenko3/MasterVaultPublic/security/advisories/new)
  through GitHub, or
- email **andrii.art.design@gmail.com** with the details.

Include what you found, how to reproduce it, and the impact you expect. You
will get an acknowledgement within a few days, and a fix or a plan once the
report is confirmed.

## Scope notes

MasterVault runs locally and reads from a Markdown vault you control. The list
below separates what is enforced by code and covered by regression tests from
what still depends on your judgement or on model behaviour. That split is the
point: treat anything under "not enforced" as an open risk, not a residual one.

### Enforced mechanically

Each of these has a regression test under `tests/unit/security/` or
`tests/integration/test_storage_backends.py`.

- **Workspace confinement for paths taken from untrusted data.** A review
  item's `target:` and `id:`, and a note path replayed from a run's event log,
  are all written by LLM-driven producers. Each is resolved through
  `mastervault.core.paths.resolve_within`. Enforced, precisely:

  - a crafted path is rejected -- absolute, drive/UNC-anchored, containing any
    `..` segment, or containing a NUL byte;
  - a symlink that *already* points out of the root at resolution time cannot
    redirect the write, because both sides are symlink-resolved and containment
    is re-checked;
  - the final target becoming a symlink between that check and the write is
    rejected, because the write opens with `O_NOFOLLOW`;
  - `ReviewItem` refuses the same shapes at construction, so a malformed item
    cannot be built in-process, and a planted queue file fails as a `conflict`
    rather than a traceback.

  Call sites covered: `review.apply`, `review.queue.enqueue`, `pipelines.ingest`
  (resume) and `pipelines.lint`. (0.1.x joined these paths directly and could be
  made to overwrite any file the process could reach.)

  **Outside the enforced boundary**, and deliberately so:

  - **Parent-component races.** `O_NOFOLLOW` inspects only the last component.
    A *parent directory* that passed resolution can still be replaced with an
    outside-pointing symlink before the write, and that is not defended against.
  - **Hard links.** No symlink-aware check sees one, so a hard link from a vault
    path to a file outside it is written through.

  Both require an attacker who already has concurrent write access to your vault
  directory. This is a single-operator local CLI; at that point the vault is
  theirs regardless, and building a portable filesystem sandbox to defend
  against it would buy nothing real.

  Platform note: `O_NOFOLLOW` is present on Linux and macOS. Where the constant
  is unavailable the flag degrades to 0 and only the resolution-time check
  applies, so the final-component race is not covered there either.
- **Stale-proposal blocking.** An item whose target changed since the proposal
  fails its `base_hash` check and is marked `conflict` rather than applied.
- **Bounded input failures.** A corrupt PDF or a binary file with a `.txt`
  suffix raises `UnreadableDocument` naming the file and the remedy, instead of
  a pypdf or codec traceback. During `mvault ingest`, an unreadable file is
  recorded and skipped so one bad file cannot cost a directory its whole run.
- **Ingestion stays inside the vault.** Written note paths are built from a
  slugified unit id and the domain enum, so no input filename can direct a
  write elsewhere. Discovery skips symlinks entirely — both symlinked
  directories and symlinked *files*, the latter because `Path.is_file()`
  follows links and would otherwise read (and send to your LLM provider) the
  contents of whatever the link points at.
- **Secrets stay out of run artifacts and error messages.** `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `COHERE_API_KEY` and `DATABASE_URL` are read only from the
  environment or a local `.env`, never from `mastervault.toml`, and never enter
  a run's `plan.json`, `events.jsonl` or `summary.json`. Database connection
  failures are converted to a `StorageError` that names the variable and not its
  value — psycopg echoes the whole connection string back on a malformed DSN,
  which put the password on the terminal in 0.1.x.
- **Citations resolve.** Every citation in an answer names a record that was
  actually retrieved. The generative path strips unknown ids with a warning; the
  extractive path strips record-shaped tokens a *document* embedded in its own
  text, so quoted content cannot forge a citation either. Both sweeps run to a
  fixed point rather than once, because a single `re.sub` pass never re-reads
  its own output: `[cl[claim:bogus]aim:forged]` would otherwise collapse into an
  unvalidated `[claim:forged]`. The check covers every namespace the index
  issues, document-level `source:`/`decision:`/`strategy:` ids included.
- **Index consistency under failure.** Document upserts and embedding writes are
  transactional on both backends: a failure mid-write leaves the previous
  document, its claims, chunks, lexical index and vectors intact rather than
  half-replaced. A document that shrinks also drops the vectors of the claims
  and chunks it lost, instead of stranding them in the ANN index forever. Both
  backends refuse any vector cosine cannot rank — zero, float32-underflowing,
  NaN or infinite — in the same place, because Postgres would otherwise clamp
  such a row to a similarity of 1.0 and make it the top hit for every query.
- **Interrupted ingestion is resumable and idempotent.** Runs replay from a
  frozen plan plus an event log; completed units are not redone, and a drifted
  source aborts the resume instead of mixing versions.

### Not enforced — your judgement still required

- **Prompt injection is NOT solved.** Untrusted document text is *structurally
  delimited*: document bodies, claim statements, wiki text and evidence cards
  are wrapped in `<<<BEGIN UNTRUSTED …>>>` / `<<<END UNTRUSTED …>>>` markers,
  and any marker inside the payload is neutralised so a document cannot close
  its own fence. That removes the structural ambiguity about where instructions
  end and data begins. It does **not** make the model obey the boundary. A
  document that says "ignore the above" may still influence a real provider's
  output. What limits the blast radius is elsewhere: the citation gate, the
  typed output contracts, and the fact that tier-2/3 changes go to a review
  queue instead of being applied.
- **Ingesting third-party files sends their text to your configured LLM
  provider.** Review what you ingest. `llm.provider=mock` (the default) runs the
  whole pipeline with no network call.
- **The two backends are not bit-identical.** They implement the same contract
  and pass the same parity suite, but three differences are known and
  unresolved: SQLite's FTS5 index has no stemmer and its tokenizer drops
  non-ASCII characters, so lexical recall is narrower than Postgres's
  `to_tsvector('english', …)`; SQLite has a hard 32,766 bind-parameter ceiling
  that caps single-batch operations Postgres handles with `= ANY`; and driver
  exceptions are not wrapped, so a caller catching `StorageError` will not catch
  an integrity or schema error from either driver. Exactly-tied vectors are also
  ordered by whichever index returns them first, so their relative order can
  differ between backends: sqlite-vec rejects a tie-break inside its KNN scan,
  and adding one only on the Postgres side would have made the two *more*
  divergent, not less.

- **No authentication or multi-user model.** Anyone who can run the CLI has full
  access to the workspace and the index. This is a single-operator local tool.
- **`ask` does not abstain.** On a question the corpus cannot answer it returns
  a low-confidence extractive answer from the nearest records rather than
  declining. See the `ask-negative-01` limitation in
  `datasets/larkstead/golden/ask_cases.yaml` for the measurements behind that.
