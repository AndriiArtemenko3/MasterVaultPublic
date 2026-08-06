# ADR 0002: Optional Docling adapter and MasterVault schema-v2

- Status: accepted for v0.3 Milestone 2, contract slice
- Date: 2026-08-06

## Context

ADR 0001 established immutable PDF bytes and page/block evidence with an
intentionally coarse pypdf schema-v1. The next bounded step needs layout,
sections, coordinates and table cells without making a parser vendor's JSON or
Markdown canonical. It must also keep ordinary installations and all existing
retrieval baselines unchanged.

Docling's standard PDF pipeline uses local model artifacts and downloads them
when no artifacts path is supplied. That default is unsuitable for an ingest
command handling business documents: a parse must not create network traffic,
change its model revision, or silently fall back to another parser.

## Decision

`pypdf` remains the core/default parser and preserves the exact schema-v1
page-text JSON, block IDs and flattened-text view. Docling is explicit opt-in
through the `pdf-layout` extra and `--pdf-parser docling` (or
`document.pdf_parser = "docling"`). The direct optional dependencies are exact:

```toml
docling-slim[convert-core,format-pdf,models-local] == 2.118.0
docling-ibm-models[opencv-python-headless] == 3.13.3
```

The second direct pin closes a clean-install gap: TableFormer imports `cv2`,
while the smaller Docling extra combination does not otherwise install it.
The transitive graph is frozen in `uv.lock`. The full `docling` meta-package is
not used.

Only `document_intelligence/docling_adapter.py` imports Docling. It immediately
exports vendor objects to builtin dictionaries and passes those to a
vendor-free normalizer. The fixed profile is CPU-only with OCR, remote
services, external plugins, VLMs, picture/chart/code/formula enrichments,
image generation, and cross-page table stitching absent or disabled. Layout
compilation is disabled and TableFormer FAST with cell matching is selected.
Parsed pages are retained during conversion so Docling's local heading pass can
use bookmarks and font style. Numbering inference is disabled in this certified
profile: on the real policy fixture its Arabic peer headings otherwise all
become level 1 and flatten the styled document parents.

The adapter requires an explicit artifacts directory. The packaged manifest
pins Heron to commit `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` and the
Docling Models/TableFormer repository to commit
`fc0f2d45e2218ea24bce5045f58a389aed16dc23` (the commit resolved by the
upstream `v2.3.0` TableFormer downloader), then records every allowed source
path, destination path, byte size and SHA-256. The explicit
operator-initiated fetch is:

```bash
python -m mastervault.document_intelligence.fetch_docling_artifacts \
  --output-dir /absolute/path
MV_DOCUMENT__DOCLING_ARTIFACTS_PATH=/absolute/path \
  mvault document doctor --parser docling
```

The helper downloads only those paths at the full commit IDs and verifies the
result; the runtime adapter never invokes it. Its output path must be absent
under an existing real parent. Downloads remain in a mode-0700 sibling staging
tree, and a separate publication tree receives only no-follow copies of the
manifest-listed files. Size/hash verification runs before and after that copy;
the verified tree is then atomically renamed to the requested path. Every
ordinary failure cleans staging and leaves the requested path absent. The
doctor is read-only. Artifact roots and files may not be symlinks, path
components may not redirect outside the root, and inputs must be regular files.
Size and hash are streamed from a no-follow descriptor with mutation checks.
Missing packages, paths, files, sizes or hashes fail actionably. The adapter
revalidates before model initialization and again immediately before
conversion. These immediate checks narrow but do not eliminate filesystem
TOCTOU: the cache is explicitly an operator-owned, immutable trusted directory.
A hostile same-user process racing the final absent-path check and rename is
outside the supported model. There is no pypdf fallback.

The profile rejects sources above 50 MiB before optional imports, passes a
200-page ceiling and the same byte ceiling to the converter, and configures
Docling's 120-second cooperative document timeout. Only `success` is accepted;
timeout/partial status fails closed. These exact limits are stored in
schema-v2 parsed documents and references and included in the ingest plan's
frozen identity.

Schema-v2 is a separate strict type, discriminated from the frozen v1 type by
`schema_version`. It contains page dimensions, normalized six-decimal
top-left bboxes, a section hierarchy, document reading order, blocks, tables,
rows, cells, header flags and row/column spans. IDs are sequential
MasterVault-owned traversal IDs; they never contain vendor JSON pointers,
unquantized coordinates or text hashes. Parsed references freeze schema,
normalization, parser/core and model-artifact identity while legacy references
default to schema-v1/page-text identity.

Models may propose exactly one block ID or cell ID plus a verbatim quote.
MasterVault derives the page, table, row, column, bbox and offsets. Unknown,
duplicate, mixed-table and forged persisted evidence fails closed. Evidence
remains canonical in source-note frontmatter; this slice adds no database
evidence table.

MasterVault renders canonical Markdown from schema-v2. Header/footer furniture
is retained in JSON and omitted from the default body. Rectangular tables use
stable GitHub Markdown with neutral column labels. Spanned tables use an
explicit `table-grid` fenced representation; lossless spans remain in JSON.
Vendor Markdown is never authoritative.

The ingest plan freezes parser/component/model/profile/schema/artifact/resource-limit identity.
A fresh run memoizes each PDF parse between planning and execution. Resume
performs a new parse, compares the frozen byte and semantic identities, then
reuses only that validated result for the pending unit.

## Measured spike

Measured on the certified local macOS arm64/Python 3.12 CPU environment:

- clean optional environment: about 1.0–1.1 GB with the required headless
  OpenCV extra;
- the broad upstream `docling-tools models download layout tableformer`
  output contained 701,229,874 regular-file bytes (`du -sh`: 669M), because it
  also fetched Heron ONNX and TableFormer Accurate variants plus metadata;
- the manifest-selected runtime payload is exactly 317,123,044 bytes: Heron
  layout (171,658,996-byte weights plus 3,268- and 444-byte configs) and
  TableFormer FAST (145,453,276-byte weights plus a 7,060-byte config);
- versions: docling-slim 2.118.0, docling-core 2.91.0,
  docling-ibm-models 3.13.3, docling-parse 7.10.0, torch 2.13.0,
  torchvision 0.28.0;
- existing two-page Larkstead fixture: real offline conversion detected 26
  normalized blocks, a nested section hierarchy, one 6x2 table and 12 cells;
  two adapter parses produced byte-identical IR. The installed-wheel contract,
  which also constructs and rejects a 201-page PDF, completed locally in about
  seven seconds with warm package/model caches.

Docling packages report MIT licenses. The Heron model card declares
Apache-2.0; the Docling Models/TableFormer card declares
CDLA-Permissive-2.0. No weights are vendored by MasterVault.

## Consequences and limits

- This slice supports clean, digitally generated PDFs only. Scans and OCR are
  rejected; image tables, charts, formulas and cross-page tables are out.
- There is no structural storage/FTS/vector channel, retrieval improvement
  claim, final benchmark, or performance threshold in this change.
- Fresh PDF parsing and dry-run planning can be keyless. Fresh claim extraction
  still needs a real configured LLM provider; the mock provider is not a
  universal keyless ingest implementation.
- The existing 52-query retrieval and 14-case/97-check ask baselines remain the
  gates because ranking inputs and storage schemas do not change.
- A separate path-filtered/manual macOS arm64 CI workflow builds the wheel,
  exports only the locked `dev` plus `pdf-layout` dependency graph, installs
  the wheel into a runner-temporary environment, and proves imports resolve
  from `site-packages` outside the checkout. It caches the manifest-selected
  artifacts under an exact identity key, verifies them, then parses the
  committed fixture twice with offline environment controls and socket
  connections denied. Core SQLite,
  Postgres, eval and package jobs explicitly omit `pdf-layout`, so the roughly
  1 GB optional environment does not enter their matrices. macOS arm64 is
  intentional: PyPI's Linux torch 2.13 wheel currently brings a multi-gigabyte
  CUDA 13 dependency graph even though this profile forces CPU execution. The
  pinned `macos-15` label is the current GitHub-hosted 3-core/7 GB/arm64
  standard runner, as listed in GitHub's
  [hosted-runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
