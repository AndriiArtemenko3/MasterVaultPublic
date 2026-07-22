# src/mastervault/prompts — Versioned prompt templates + loader

Every LLM task in MasterVault reads its instructions from a file here, not from an inline string. Each task gets a subfolder named for its contract id, holding one or more `v<N>.md` files: YAML frontmatter (`contract`, `version`, `tier`, `output_model`, `variables`) followed by a Jinja2 body. `registry.py` locates, parses, and validates these files so a broken header, a wrong output model, or an undeclared template variable fails at load time rather than during a live dispatch.

## Files

| File | Responsibility |
| --- | --- |
| `registry.py` | Loads and validates one versioned prompt. Parses the frontmatter, checks the header identity against the file location, imports `output_model` and confirms it is a pydantic `BaseModel`, and render-tests the body under `StrictUndefined` with exactly the declared variables. Exposes `PromptSpec`, `available_versions`, and `load`. |
| `__init__.py` | Package docstring plus the public surface: re-exports `PromptSpec` and `load`, telling callers to go through the registry rather than a raw file path. |
| `untrusted.py` | `fence(text, label)` wraps corpus text (document bodies, claim statements, wiki text, evidence cards) in named `<<<BEGIN/END UNTRUSTED …>>>` markers and neutralises any marker inside the payload, so a document cannot close its own fence. Structural only: it removes the ambiguity about where instructions end, not the model's freedom to ignore it. |
| `claim_extraction/v1.md` | tier `small`, output `ClaimExtractionOut`. Instructs the model to extract atomic, present-tense business claims (≤25 words, one fact each) with confidence and `affects_candidates` concept names. |
| `contradiction_judge/v1.md` | tier `small`, output `ContradictionVerdictOut`. Judges two claim statements as `contradicts` / `compatible` / `unclear`, with a rationale that quotes the load-bearing text of both. |
| `corpus_check/v1.md` | tier `small`, output `CorpusCheckOut`. Compares a new claim against one existing wiki entry and labels the relation `supports` / `extends` / `challenges`. |
| `grounded_synthesis/v1.md` | tier `large`, output `GroundedAnswerOut`. Writes an answer grounded only in the supplied evidence cards, cites claim ids inline in square brackets, and reports remaining gaps. |
| `sufficiency_judge/v1.md` | tier `small`, output `SufficiencyVerdictOut`. Decides whether gathered evidence answers the question; if not, lists missing aspects and proposes up to 3 follow-up queries. |
| `wiki_draft/v1.md` | tier `medium`, output `WikiDraftOut`. Branches on a `mode` variable: `new` drafts a full sectioned entry, otherwise it drafts one paragraph to append to an existing entry. |

## How it fits

The loader resolves files through `importlib.resources`, so it behaves the same from an installed wheel or an editable checkout, and it uses [../vaultfs](../vaultfs)'s `parse_frontmatter` to split header from body. Consumers never touch these files directly: [../contracts](../contracts)'s `base.py` calls `registry.load(contract_id, version)`, renders the returned `PromptSpec` with runtime variables, and appends an auto-generated JSON-schema section derived from the same `output_model`. That keeps each prompt paired with the typed [../contracts](../contracts) model that validates the model's reply.

## Key concepts / entry points

- `PromptSpec` — frozen dataclass holding the parsed header fields (`contract_id`, `version`, `tier`, `output_model`, `variables`) and the raw Jinja2 `body`. `registry.py:45`
- `PromptSpec.render(variables)` — renders the body under `StrictUndefined`, so a missing variable raises rather than silently emitting an empty string. `registry.py:54`
- `load(contract_id, version=None)` — the one public entry point; `version=None` selects the highest available version. `registry.py:139`
- `_parse_spec` — the validation core: header keys, contract/version match against the file location, `output_model` import, and a render smoke-test with the declared variables. `registry.py:75`
- `available_versions(contract_id)` — maps version number to the `v<N>.md` traversable for a contract's directory, raising `PromptNotFoundError` if none exist. `registry.py:122`
- Contract-id-as-directory convention — a subfolder name equals the `contract` field its `v<N>.md` files must declare, which is how the registry catches a misplaced or mislabeled prompt.
