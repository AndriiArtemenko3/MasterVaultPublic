# document_intelligence — immutable PDF evidence substrate

This package is MasterVault's parser-independent boundary between source PDF
bytes and canonical claim evidence. The core `pypdf` baseline preserves its
schema-v1 one-block-per-page contract. The optional Docling adapter normalizes
clean-digital layout and tables into MasterVault schema-v2 without exposing a
vendor object, vendor Markdown, download behavior, or fallback to callers.

## Files

| File | Responsibility |
|---|---|
| `models.py` | Frozen schema-v1 plus strict schema-v2 pages, sections, blocks, normalized bboxes, tables/rows/cells/spans, discriminated loading, and resolved evidence types. |
| `benchmark.py` | Runtime-safe, strict contracts for the bounded Larkstead PDF source/render specification and manifest. It intentionally contains no evaluator answers and has no dependency on `mastervault.evals`. |
| `parser.py` | `DocumentParser` protocol, compatible `PypdfParser`, and explicit parser factory. Optional imports remain lazy. |
| `docling_adapter.py` | Sole vendor import boundary. Verifies exact component/artifact identity, forces the fixed offline CPU/resource/hierarchy profile, exports built-in dictionaries, and provides the read-only doctor. |
| `docling_artifacts_manifest.json` | Certified full source commits plus the five allowed runtime paths, exact byte sizes, and SHA-256 hashes. It contains no weights. |
| `fetch_docling_artifacts.py` | Explicit network-enabled setup helper; fetches only manifest-listed paths at immutable revisions, then verifies them locally. Runtime never calls it. |
| `docling_normalizer.py` | Vendor-free normalization into deterministic schema-v2 IDs, coordinates, hierarchy, reading order, furniture, and table grids. |
| `renderer.py` | MasterVault-owned deterministic Markdown; omits furniture by default, uses GFM for simple tables and an explicit grid form for spans. |
| `store.py` | Exclusive content-addressed publication under `workspace/assets/sha256/` and deterministic schema-v1/v2 JSON under `workspace/parsed/sha256/`; verifies hashes and identities on reuse/load. |
| `grounding.py` | Resolves a model-proposed block or cell id plus quote to authoritative structural location/offsets, then revalidates persisted evidence fail-closed. |
| `__init__.py` | Public exports for the document-intelligence boundary. |

## Invariants

- `asset_sha256` is the full SHA-256 of the original PDF bytes. It is separate
  from the legacy 16-character extracted-text `provenance_hash`.
- Paths stored in canonical notes are workspace-relative and confinement
  checked before reads or writes.
- Parsed JSON is strict (`extra="forbid"`), schema-versioned, deterministic,
  and hash-verified when loaded.
- Page numbers come from the parser. The model may propose exactly one block id
  or cell id plus a verbatim quote, never trusted coordinates or offsets.
- A grounded claim is rejected unless every evidence quote resolves inside
  the referenced structure; duplicate, mixed-table and forged locations fail.
- Schema-v2 IDs and reading order are MasterVault-owned and canonical. Bboxes
  use a normalized, six-decimal, top-left coordinate space.
- Runtime benchmark discovery exposes source identity, family split, render
  profile, raw-source hash, normalized semantic-projection hash, render/PDF
  hashes, size, and page count only. Parser-hidden layout labels and temporal
  change-impact answers are owned by `mastervault.evals` and loaded only by
  evaluation/test code.
- Docling requires the `pdf-layout` extra and a verified explicit artifacts
  path. The explicit fetch accepts only an absent output under an existing real
  parent, downloads into private sibling staging, verifies a publication tree
  containing only manifest files, and atomically renames it into place.
  Failures publish nothing and remove staging. Roots/files may not be symlinks
  or special files; identity is rechecked around model initialization.
  Immediate no-follow checks narrow filesystem TOCTOU, while the operator-owned
  local directory remains the trusted boundary against a hostile same-user
  race. Runtime downloads, OCR, remote services, plugins, VLMs, and fallback
  parsing are disabled.
- The schema-v2 profile freezes 50 MiB, 200 pages and a 120-second cooperative
  timeout, plus deterministic bookmark/style hierarchy inference. Empty,
  textless, scanned, corrupt, encrypted, oversized, timed-out and partial
  inputs fail visibly.
