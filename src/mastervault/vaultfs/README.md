# src/mastervault/vaultfs — the file-canonical Markdown layer

Markdown files with YAML frontmatter are the source of truth; every index in the system is derived and can be rebuilt from them. This package owns the four operations against those files: parsing frontmatter, typed read/write of notes, walking the vault tree to enumerate indexable notes, and segmenting bodies into chunks for embedding. Because the files are canonical, the write paths here stay byte-conservative so a targeted field edit never disturbs the rest of a note.

## Files

| File | Responsibility |
|------|----------------|
| `frontmatter.py` | Fence splitting, YAML parse with a jam-repair retry, serialize, and `surgical_replace_field`, which splices new bytes into the exact span of one top-level field without round-tripping the rest of the file through a dumper. |
| `notes.py` | Typed note I/O: `read_note` dispatches on the frontmatter `type:` to a pydantic model (`SourceNote`/`WikiEntry`/`DecisionNote`/`StrategyNote`); `write_note` renders model + body deterministically; plus `slugify` and `extract_title` helpers. |
| `segmenter.py` | Pure `segment(body)` that splits on H2/H3 headings and blank-line paragraph boundaries, then greedily merges paragraphs within a section toward ~1200 chars without crossing a heading or splitting a paragraph. |
| `walker.py` | `walk_vault` enumerates every `.md` note, applies the indexable-note gate (readable, parseable frontmatter, valid `domain:` and `type:`), and returns `NoteRef`s plus `SkippedFile`s with reasons, sorted for determinism. |
| `__init__.py` | Package facade re-exporting the public symbols (`read_note`, `write_note`, `segment`, `walk_vault`, `surgical_replace_field`, and the typed result tuples). |

## How it fits

Input is whatever authors write into the vault directory as Markdown; the frontmatter shapes and `NoteType`/`Domain`/`content_hash` come from [../models.py](../models.py). The [../ingest](../ingest) pipeline drives this layer: it calls `walk_vault` to find indexable notes, `read_note` to load them, and `segment` to produce the chunks that [../retrieval](../retrieval) embeds and indexes. When ingestion writes derived data back into a note (drafted wiki links, routed claims), it goes through `surgical_replace_field` so only the changed field's bytes move.

## Key concepts / entry points

- `surgical_replace_field(text, field, new_yaml_block)` — the only sanctioned single-field rewrite; byte-for-byte identity when the block is unchanged (`frontmatter.py:189`).
- `parse_frontmatter` / `repair_yaml` — strict `yaml.safe_load` first, then one jam-repair retry that double-quotes a scalar containing an unquoted `: ` (`frontmatter.py:100`, `frontmatter.py:71`).
- `read_note` / `LoadedNote` — dispatch on `type:` to the matching pydantic model, filling a missing `title` from the body's first H1 or the filename stem (`notes.py:68`).
- `segment(body, target_chars=1200)` — deterministic, content-hash-stable chunking that keeps each heading glued to its content (`segmenter.py:88`).
- `walk_vault` and the indexable-note gate — `REQUIRED_FIELDS = ("domain", "type")`; failures become `SkippedFile` reasons rather than silent drops (`walker.py:32`, `walker.py:11`).
- `write_note` — deterministic but not byte-preserving; use it for full writes and `surgical_replace_field` for in-place edits (`notes.py:89`).
