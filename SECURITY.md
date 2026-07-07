# Security Policy

## Supported versions

MasterVault is at `0.1.x` and pre-1.0. Security fixes land on the latest
release only.

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |
| < 0.1   | no        |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Instead, either:

- open a [private security advisory](https://github.com/AndriiArtemenko3/MasterVaultPublic/security/advisories/new)
  through GitHub, or
- email **andrii.art.design@gmail.com** with the details.

Include what you found, how to reproduce it, and the impact you expect. You
will get an acknowledgement within a few days, and a fix or a plan once the
report is confirmed.

## Scope notes

MasterVault runs locally and reads from a Markdown vault you control. Two areas
are worth keeping in mind when you evaluate risk:

- **Secrets** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `COHERE_API_KEY`,
  `DATABASE_URL`) are read only from the environment or a local `.env`, never
  from `mastervault.toml`. Keep `.env` out of version control; it is already in
  `.gitignore`.
- **Untrusted documents**: ingesting third-party files sends their text to
  whichever LLM provider you configure. Review what you ingest, and use
  `llm.provider=mock` if you want to run the pipeline without any network call.
