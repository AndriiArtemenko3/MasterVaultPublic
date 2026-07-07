# datasets/larkstead — Synthetic demo & eval corpus

Larkstead Goods Co. is a fully fictional ergonomic-furniture business whose internal documents drive the MasterVault demo and retrieval evals. The folder is layered by pipeline stage: a `bible/` that fixes every fact, `raw/` markdown written against it, a `processed/` layer produced by running `mvault ingest`, and a `golden/` set plus `embeddings/` sidecar that make the demo runnable keyless and the evals reproducible. Contradictions across policy boundaries (30-day vs 45-day returns, price-match v1 vs v2) are seeded on purpose so the ingestion router has real conflicts to flag.

## Files

| File | Responsibility |
|---|---|
| `bible/company.yaml` | Single source of truth: staff voice cards, 150+ SKUs, customers, vendors, policies, `tax_table`, `id_grammars`, and `banned_strings`. Every date, price, and ID in the corpus traces here. |
| `bible/corpus-plan.yaml` | Doc-count allocation across 4 domains and their doc types (372 total = 89 storyline + 283 filler), with filler guardrails and ID-monotonicity rules. |
| `bible/style-rules.md` | Per-doc-type register, typo budget, banned vocabulary, and the date/price-consistency rule that makes cross-doc contradictions deliberate. |
| `bible/storylines/SL{1..5}-*.yaml` | Five multi-doc storylines (mat defect, refund-window change, Cobalt dental deal, Halo firmware flicker, ParcelPoint cutover). Each lists beats, produced `doc_id`s, `required_facts`, and the seeded contradiction (C1–C4). |
| `bible/doc-templates/*.md` | 32 per-doc-type exemplars (ticket, invoice, memo, lead-note, receiving-log, …) that writer agents fill. |
| `bible/manifest.yaml` | Frozen bible inventory with per-file sha256 prefixes. |
| `raw/<domain>/<doc-type>/*.md` | 372 in-world source documents across the 4 domains, organized by doc type (e.g. `customer-support/policy/`, `sales-crm/lead-note/`). This is what `mvault ingest` reads. |
| `processed/<domain>/sources/*.md` | 352 source notes: raw docs plus ingestion frontmatter (`key_claims` with id/statement/confidence/`affects`, `provenance`, `provenance_hash`). |
| `processed/<domain>/wiki/*.md` | 43 drafted wiki concepts (aliases, Definition, Cross-Refs), after pruning 22 malformed zero-inbound slugs. `operations` has zero surviving concepts by design. |
| `processed/<domain>/{decisions,strategy}/*.md` | 10 decision notes and 4 strategy notes (one per domain, 2026-Q2), hand-authored on top of the verified claims layer. |
| `processed/_review/pending/*.md` | 4 shipped review items, the seeded open contradictions C1–C4, each with a `base_hash` verified current against its target wiki file. |
| `processed/MANIFEST.md` | Curation record: per-domain counts, wiki-pruning math, review-queue triage, contradiction-detection base rate, and the $0.2213 ingest cost. |
| `golden/queries.yaml` | 52 hand-built eval queries across 5 classes (easy-lexical, semantic-paraphrase, cross-domain-multi-hop, contradiction, negative-no-answer) with `relevant_docs` and `relevant_claims`. |
| `golden/resolved.yaml` | Resolver output asserting all 69 docs / 78 claims resolve against the live `processed/` corpus; a non-empty `errors` list is a build error. |
| `golden/baseline.json` | Recorded metrics for `lexical-only`, `vector-only`, and `hybrid` configs (recall@k, nDCG@10, MRR, abstention) overall and per class. |
| `embeddings/embeddings.jsonl.gz` | 5352-vector sidecar (3412 claim + 43 wiki + 1897 chunk) from bge-small-en-v1.5, L2-normalized, 384-dim. |
| `embeddings/manifest.json` | Sidecar metadata: model, dimensions, count, record-type breakdown, and extraction source. |
| `qa/mechanical_check.py` | Read-only ground-truth checker: 10 checks against `company.yaml` + storylines (SKU/staff/vendor resolution, id-grammar, banned strings, invoice arithmetic, timestamp monotonicity, doc_id coverage, single-owner IDs). |
| `qa/violations.jsonl` | One JSON line per finding; currently empty (0 violations). |

> The `embeddings.jsonl.gz` sidecar (~19 MB) is committed in-tree on purpose:
> `mvault demo load` reads it straight from the clone, which is what makes the
> demo keyless and download-free. Moving it to a release asset would break that,
> so it stays. If this repo later accumulates more large binaries, Git LFS is
> the right next step; it is marked `binary linguist-generated` in
> `.gitattributes` so it does not skew language stats or diffs.

## How it fits

`bible/` is the upstream contract: writer agents generate `raw/` from the templates and storylines, and `qa/mechanical_check.py` gates `raw/` against `company.yaml` before ingestion. Running `mvault ingest` over `raw/` produces `processed/` (source notes, claims, wiki drafts, review queue), which `mvault sync --full` embeds to yield the `embeddings/` sidecar. Downstream, the demo loader reads `processed/` plus the sidecar to stand up a queryable vault keyless, and the eval harness in [../../src/mastervault/evals](../../src/mastervault/evals) resolves `golden/queries.yaml` and scores the [../../src/mastervault/retrieval](../../src/mastervault/retrieval) channels against `golden/baseline.json`.

## Key concepts / entry points

- Seeded contradictions C1–C4: policy-boundary conflicts (returns 30→45 days in `bible/storylines/SL2-refund-window-change.yaml:59`, price-match v1 vs v2 in `processed/_review/pending/lint-2026-07-07-1258-rv-0060.md`) that give the router something to flag instead of overwrite.
- Claim provenance: each source note carries atomic `key_claims` with `provenance`/`provenance_hash` back to its raw file, e.g. `processed/customer-support/sources/chat-log-chat-ana-priya-refund-batch-hold.md:52`.
- Date/price consistency rule: a document quotes the policy version and price in force on its own date, so contradictions live across documents, never inside one (`bible/style-rules.md:78`).
- Five query classes: the eval taxonomy that separates lexical recall from semantic, multi-hop, contradiction, and abstention behavior (`golden/resolved.yaml:5`).
- `run_all_checks`: the 10 numbered mechanical checks and their severities (`qa/mechanical_check.py:22`).
- Sidecar record types: 3412 claim + 43 wiki + 1897 chunk vectors that let the demo skip re-embedding (`embeddings/manifest.json:7`).
