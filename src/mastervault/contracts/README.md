# src/mastervault/contracts — typed LLM calls with mechanical guards

Every place the pipeline needs structured output from an LLM goes through a Contract: a versioned prompt plus a pydantic output model plus two mechanical guards (autofix and hard-fail). One `dispatch` method runs the same fixed sequence for all of them — render, call, autofix, hard-fail, single retry with the error list appended. The guards are mechanical and semantic-free, so shape defects retry through one guarded path instead of each caller reinventing validation. Semantic judgements (is a claim grounded, do citations resolve) are deliberately left to judge contracts or to pipeline state, not to this layer.

## Files

| File | Responsibility |
|------|----------------|
| `base.py` | `Contract` base class and its `dispatch` pipeline; `schema_section` generates the JSON-schema prompt section from the output model; `DispatchResult` carries `parsed`/`autofixes`/`hard_fails`/`attempts`/`cost_usd`; budget pre-flight and `emit` event hooks. |
| `claims.py` | `ClaimExtractionContract` (the reference contract): extracts atomic `ClaimCandidate`s. Autofix normalizes whitespace, dedupes statements, kebab-cases `affects_candidates`; hard-fail rejects zero claims, over-`max_claims`, too-short or too-long statements. |
| `corpus_check.py` | `CorpusCheckContract`: adjudicates one (claim, candidate-wiki) pairing into `supports`/`extends`/`challenges` with a rationale. `relation` is a plain string so an invalid value is a mechanical hard-fail that retries, not a parse-time failure. |
| `judge.py` | `SufficiencyJudgeContract`: verdict on whether one round of retrieved evidence answers the ask question. Autofix caps `followup_queries` at 3 and dedupes them; hard-fail requires a `missing_aspects` entry on an insufficient verdict and a non-empty rationale. |
| `synthesis.py` | `GroundedSynthesisContract`: writes the ask answer as `answer_markdown` with inline `[<claim-id>]` citations. Hard-fail enforces that every judge `missing_aspects` entry reappears in `gaps`; citation resolution is left to `pipelines.ask`. |
| `wiki_draft.py` | `WikiDraftContract`: one contract, two modes via `ctx["mode"]`. `extend` produces a single paragraph under a word cap; `new` produces a full entry and hard-fails unless the required `##` sections and the `**Operating:**` line are present. |
| `contradiction.py` | `ContradictionJudgeContract`: `contradicts`/`compatible`/`unclear` verdict over two claim statements sharing an `affects` slug. Renders one verdict; the swapped-order double-confirm policy lives in `pipelines.lint`. |
| `__init__.py` | Re-exports every contract, output model, `Contract`, `DispatchResult`, and `schema_section`. |

## How it fits

Prompt bodies come from [../prompts](../prompts) via `registry.load(contract_id, version)`, which pairs each prompt spec with its output model so the handwritten prompt and the auto-generated schema section stay aligned. `dispatch` calls the LLM through [../providers](../providers) (`LLMProvider.complete`) and, when a `BudgetLedger` from [../core](../core) is passed, checks and records cost per call. The pipelines in [../pipelines](../pipelines) construct these contracts and read `DispatchResult.ok`: ingest drives claims/corpus_check/wiki_draft, ask drives judge then synthesis, and lint drives contradiction. Output models reference `Confidence` from [../models.py](../models.py).

## Key concepts / entry points

- `Contract.dispatch` — the fixed render → call → autofix → hard-fail → retry-once sequence shared by all contracts (`base.py:94`).
- `schema_section` — renders the "Output format" prompt block from `output_model.model_json_schema()` so prompt and model can't drift (`base.py:38`).
- `DispatchResult.ok` — true only when `parsed` exists and `hard_fails` is empty; how callers decide pass/fail (`base.py:71`).
- `Contract.autofix` / `Contract.hard_fail_checks` — the two overridable guards; autofix is idempotent mechanical cleanup, hard-fail returns error strings that trigger the single retry (`base.py:84`, `base.py:88`).
- `ClaimExtractionContract` — the reference implementation to read first when adding a new contract (`claims.py:61`).
- The mechanical/semantic split — shape checks live in guards; groundedness, citation resolution, and double-confirm live in the pipelines, documented in each contract's module docstring.
