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

- **Workspace confinement for review application.** A review item's `target:`
  is written by an LLM-driven producer, so it is untrusted. `mvault review
  apply` resolves it through `mastervault.core.paths.resolve_within`, which
  rejects absolute paths, `..` segments, NUL bytes, and symlinks that leave the
  vault. A rejected target is marked `conflict` and nothing is written. (Fixed
  in 0.2.0: 0.1.x joined the path directly and could be made to overwrite any
  file the process could reach.)
- **Stale-proposal blocking.** An item whose target changed since the proposal
  fails its `base_hash` check and is marked `conflict` rather than applied.
- **Bounded input failures.** A corrupt PDF or a binary file with a `.txt`
  suffix raises `UnreadableDocument` naming the file and the remedy, instead of
  a pypdf or codec traceback. During `mvault ingest`, an unreadable file is
  recorded and skipped so one bad file cannot cost a directory its whole run.
- **Ingestion stays inside the vault.** Written note paths are built from a
  slugified unit id and the domain enum, so no input filename can direct a
  write elsewhere. Directory discovery does not descend into symlinked
  directories.
- **Secrets stay out of run artifacts.** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `COHERE_API_KEY` and `DATABASE_URL` are read only from the environment or a
  local `.env`, never from `mastervault.toml`, and never enter a run's
  `plan.json`, `events.jsonl`, `summary.json`, or an error message. Backend
  errors name the variable, never its value.
- **Citations resolve.** Every citation in an answer names a record that was
  actually retrieved. The generative path strips unknown ids with a warning; the
  extractive path strips record-shaped tokens a *document* embedded in its own
  text, so quoted content cannot forge a citation either.
- **Index consistency under failure.** Document upserts and embedding writes are
  transactional on both backends: a failure mid-write leaves the previous
  document, its claims, chunks, lexical index and vectors intact rather than
  half-replaced. Both backends refuse the zero vector, which Postgres would
  otherwise store as a permanently unretrievable row.
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
- **No authentication or multi-user model.** Anyone who can run the CLI has full
  access to the workspace and the index. This is a single-operator local tool.
- **`ask` does not abstain.** On a question the corpus cannot answer it returns
  a low-confidence extractive answer from the nearest records rather than
  declining. See the `ask-negative-01` limitation in
  `datasets/larkstead/golden/ask_cases.yaml` for the measurements behind that.
