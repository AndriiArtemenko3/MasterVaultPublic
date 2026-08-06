# datasets — container for shipped corpora

Top-level home for the datasets that MasterVault ships with. Today it holds one corpus, [larkstead](larkstead), a synthetic small-business knowledge base built to exercise ingestion, retrieval, and eval without any real company data. The container exists so datasets stay separate from application code and carry their own license.

## Files

| File | Responsibility |
|---|---|
| [larkstead/raw/](larkstead/raw) | 372 in-world Markdown source documents across four domains (customer-support, sales-crm, operations, internal-admin); the corpus's source of truth before ingestion. |
| [larkstead/processed/](larkstead/processed) | Curated successful output of `mvault ingest`: 352 source notes, 3,412 claims, 43 wiki concepts, 10 decisions, 4 strategy notes, plus a 4-item review queue (one price-match pair and three return-window variants). See its [MANIFEST.md](larkstead/processed/MANIFEST.md). |
| [larkstead/embeddings/](larkstead/embeddings) | Precomputed keyless embeddings sidecar: 5,352 L2-normalized bge-small-en-v1.5 vectors (384d) in `embeddings.jsonl.gz`, with a [manifest.json](larkstead/embeddings/manifest.json) recording model, count, and record-type breakdown. |
| [larkstead/golden/](larkstead/golden) | 52 graded retrieval queries (`queries.yaml`) across five difficulty classes, a `resolved.yaml` doc/claim resolution, and a `baseline.json` of recorded eval scores. |
| [larkstead/bible/](larkstead/bible) | Authoring spec that generated the corpus: `company.yaml`, `corpus-plan.yaml`, `storylines/`, `doc-templates/`, and a frozen `manifest.yaml` of content hashes. |
| [larkstead/corpus-ledger.json](larkstead/corpus-ledger.json) | Exact accounting for all 372 raw files: 352 processed with matching provenance hashes, 0 intentionally excluded, and 20 immutable `historical_no_output` observations whose cause is unknown. |
| [larkstead/LICENSE.md](larkstead/LICENSE.md) | CC BY 4.0 scope and attribution for Larkstead synthetic content/data; repository code remains Apache-2.0. |
| [larkstead/qa/](larkstead/qa) | `mechanical_check.py`, the corpus linter enforcing the banned-strings denylist and style rules; `violations.jsonl` is empty (0 violations). |

## How it fits

The `raw/` documents are the input a fresh `mvault ingest` run consumes; `processed/` contains the 352 successful source outputs and `failures/` retains immutable historical no-output observations for the other 20. The old per-unit causes were not committed and are explicitly unknown. The ledger is the gate that prevents either category from becoming a silent omission. `processed/` and `embeddings/` let a clone reach a demo/eval-ready vault without re-running paid extraction. The [../src](../src) pipeline reads and writes these layers, and the eval harness scores retrieval against `golden/`. The demo loader imports the `processed/` and `embeddings/` sidecars directly into a SQLite index.

## Key concepts / entry points

- **Two-layer split** — `raw/` is human-authored source; `processed/` is the ingestion result. Regenerating `processed/` from `raw/` is the pipeline's end-to-end test.
- **Keyless sidecar** — `embeddings/embeddings.jsonl.gz` ships vectors so retrieval runs offline; see the count and model in [larkstead/embeddings/manifest.json:1](larkstead/embeddings/manifest.json).
- **Golden eval set** — 52 queries with verified relevant docs/claims; the header of [larkstead/golden/queries.yaml:1](larkstead/golden/queries.yaml) documents the classes and the 100%-resolve rule.
- **Everything fictional** — Larkstead Goods Co. and every entity in it are invented; `bible/company.yaml` holds the `banned_strings` denylist that `qa/mechanical_check.py` enforces.
- **License split** — repository code, including `larkstead/qa/*.py`, is
  Apache-2.0; Larkstead's synthetic content and data assets are CC BY 4.0.
  See the root [../NOTICE](../NOTICE) and dataset-local
  [licence](larkstead/LICENSE.md) for scope and attribution.
