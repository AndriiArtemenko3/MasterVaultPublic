# src/mastervault/ingest — Turn raw files into claims, wiki, links

The ingest stages that take one raw document from bytes on disk to atomic claims, concept routing, and inserted `[[wikilink]]`s. Each file here is a small, single-purpose stage; the orchestration that chains them per-document lives in [../pipelines](../pipelines) (`ingest.py`). The stages that call an LLM are thin dispatch shims over the JSON contracts in [../contracts](../contracts); the rest are pure text/embedding logic so they stay cheap and testable.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Re-exports the public stage functions and their result dataclasses (`extract_claims`, `match_claim`, `adjudicate`, `draft_new_entry`, `insert_wikilink`, `validate_source_note`, etc.). |
| `convert.py` | Raw-file discovery and text extraction. `discover_units` walks a path for `.md`/`.txt`/`.pdf` (sorted, deterministic); `read_raw_text` reads text as-is or extracts PDF pages via `pypdf`. |
| `extract.py` | Claim extraction for one unit. Dispatches `ClaimExtractionContract`, assigns canonical `<unit-slug>-NN` ids up front, and keeps raw `ClaimCandidate`s so `concept_match` can read `affects_candidates`. Also holds `guess_source_type`, a keyword heuristic over filename + lede. |
| `concept_match.py` | Routes one claim to an existing wiki concept, a corpus-check candidate pairing, or a brand-new-concept tally. Alias-exact (free, longest-alias-wins, word-boundary) first; falls back to a KNN similarity band against `record_type='wiki'` rows. |
| `corpus_check.py` | Dispatch shim over `CorpusCheckContract`. `adjudicate` scores one (claim, candidate-wiki) pairing and returns a `relation` (supports/extends/challenges) plus rationale for the caller to route on. |
| `wiki_draft.py` | Dispatch shim over `WikiDraftContract`. `draft_extend` writes a one-paragraph body addition for an 'extends' relation; `draft_new_entry` writes a full new-concept entry with aliases. |
| `linker.py` | Idempotent `[[slug]]` insertion at the first natural-language mention of an `alias`. Skips fenced code, headings, and text already inside a link; a no-op if the slug is already linked anywhere. |
| `validate.py` | The claim-schema gate for `key_claims:` frontmatter. Splits findings into mechanical autofixes (renumber ids, kebab-case `affects`, collapse whitespace) and hard-fails (bad length, bad confidence, dupes, over-budget). Also a standalone CLI: `python -m mastervault.ingest.validate <paths> [--fix]`. |

## How it fits

The per-document orchestrator in [../pipelines](../pipelines) `ingest.py` drives these stages in order: `convert` reads the file, `extract` produces claims via an [../contracts](../contracts) contract, `concept_match` queries the [../storage](../storage) backend (alias index + KNN) to route each claim, `corpus_check` and `wiki_draft` handle the extends/new-concept branches, and `linker` rewrites the note body before it is written back through [../vaultfs](../vaultfs). Claims and drafted wiki entries land in [../storage](../storage) where [../retrieval](../retrieval) later reads them; `validate.py` runs as a post-write gate (and lint CLI) to keep `key_claims:` blocks canonical.

## Key concepts / entry points

- `discover_units` / `read_raw_text` — enumerate ingestible files and pull plain text out of them (`convert.py:17`, `convert.py:34`).
- `extract_claims` → `ExtractResult` — one unit's claims with canonical ids plus the raw candidates for downstream matching (`extract.py:79`).
- `match_claim` → `MatchResult` — the exists / candidate / new routing decision, cheapest channel first (`concept_match.py:59`); the alias-vs-KNN band thresholds are `cfg.band_exists` / `cfg.band_candidate`.
- `adjudicate` → `AdjudicatedPair.relation` — supports/extends/challenges verdict per candidate pairing (`corpus_check.py:30`).
- `insert_wikilink` → `LinkResult` — the idempotent link edit; note `alias` (spaced surface form, used for search) is deliberately distinct from `slug` (kebab id, used in the link) (`linker.py:36`).
- `validate_source_note` → `Report` — the autofix-vs-hard-fail schema gate that hard-fails always suppress the write for (`validate.py:131`).
