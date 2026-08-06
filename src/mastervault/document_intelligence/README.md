# document_intelligence — immutable PDF evidence substrate

This package is MasterVault's parser-independent boundary between source PDF
bytes and canonical claim evidence. The first v0.3 vertical slice is
deliberately narrow: a `pypdf` baseline creates one addressable text block per
physical page. It proves byte identity and page-grounded claims without
claiming layout reconstruction, table understanding, or OCR.

## Files

| File | Responsibility |
|---|---|
| `models.py` | Strict, frozen schema-v1 models for source-asset references, parsed pages/blocks, parsed-artefact references, warnings, and resolved evidence spans. |
| `parser.py` | `DocumentParser` protocol and `PypdfParser`. Reads one exact byte snapshot, rejects corrupt/encrypted/textless PDFs, and preserves one-based page identity. |
| `store.py` | Exclusive content-addressed publication under `workspace/assets/sha256/` and deterministic parsed JSON under `workspace/parsed/sha256/`; verifies hashes on every reuse/load. |
| `grounding.py` | Resolves a model-proposed block id and quote to authoritative page/character offsets, then revalidates persisted evidence fail-closed. |
| `__init__.py` | Public exports for the document-intelligence boundary. |

## Invariants

- `asset_sha256` is the full SHA-256 of the original PDF bytes. It is separate
  from the legacy 16-character extracted-text `provenance_hash`.
- Paths stored in canonical notes are workspace-relative and confinement
  checked before reads or writes.
- Parsed JSON is strict (`extra="forbid"`), schema-versioned, deterministic,
  and hash-verified when loaded.
- Page numbers come from the parser. The model may propose a block id and
  verbatim quote, never a trusted page number or offset.
- A grounded claim is rejected unless every evidence quote resolves inside
  the referenced block.
- Empty/textless PDFs fail visibly. OCR, bounding boxes, tables, and Docling
  routing are later milestones.
