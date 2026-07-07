<!-- Thanks for contributing to MasterVault. Keep this short and concrete. -->

## What this changes

<!-- One or two sentences. What does the PR do, and why? -->

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Docs
- [ ] Dataset / eval
- [ ] Refactor / chore

## Checklist

- [ ] `uv run ruff check src tests` passes
- [ ] `uv run pytest` passes
- [ ] If retrieval or the golden set changed, `uv run mvault eval --compare datasets/larkstead/golden/baseline.json` is green (or the baseline was intentionally re-frozen and that is called out below)
- [ ] Docs / README updated if behavior or commands changed

## Notes for the reviewer

<!-- Anything worth flagging: a baseline re-freeze, a schema change, a known limitation. -->
