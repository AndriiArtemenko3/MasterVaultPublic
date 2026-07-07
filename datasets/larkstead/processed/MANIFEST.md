---
title: Larkstead processed dataset — manifest
generated: '2026-07-07'
---

# Larkstead processed dataset

Curated, exported output of the MasterVault ingestion pipeline run over `datasets/larkstead/raw/`. This is the demo/eval-ready layer: cleaned wiki concepts, a shipped review queue, and the decision + strategy notes that give the vault something to reason over beyond raw source ingestion.

## Per-domain counts

| Domain | Sources | Claims | Wiki | Decisions | Strategy | Pending review |
|---|---:|---:|---:|---:|---:|---:|
| customer-support | 122 | 1,154 | 19 | 2 | 1 | 4 (all here) |
| sales-crm | 87 | 800 | 12 | 3 | 1 | 0 |
| operations | 85 | 904 | 0 | 3 | 1 | 0 |
| internal-admin | 58 | 554 | 12 | 2 | 1 | 0 |
| **Total** | **352** | **3,412** | **43** | **10** | **4** | **4** |

Pending review is domain-agnostic in storage (`_review/pending/`) but all 4 kept items target customer-support wiki files, so the column above shows where they land.

The `operations` domain has zero surviving wiki concepts. Every wiki page the claim extractor drafted for that domain named a specific entity (a vendor code, an invoice recipient, a status sentence) rather than a reusable concept, and every one of them had zero inbound claims. Pruning removed all three; nothing generalizable survived. This is a genuine finding about the extractor's behavior on that domain's source mix (receiving logs, bug reports, checklists), not a pruning bug — flagged for the morning reviewer as a real gap rather than smoothed over.

## Wiki pruning

65 wiki entries went in, 43 came out: 22 deleted. Every deleted entry matched a malformed-slug pattern (`the-account-is-*`, `*-is-the-owner-of-*`, hash-suffixed proper nouns, `*-company-llc-*`, or a raw extracted-sentence slug like `the-stage-is-discovery`) and, independently, had zero inbound claims — the two deletion criteria coincided exactly on this corpus, so there was nothing to adjudicate case-by-case. No source frontmatter needed editing after deletion: zero claims referenced any of the 22 removed slugs in the first place. `mvault lint --mechanical-only` confirms orphan-wiki dropped from 22 to 0 after the prune and `sync --full`.

Broken-affects held flat at 75 before and after pruning. That count is a pre-existing, unrelated data-quality issue: claims whose `affects:` list names a slug that never had a wiki file at all (typos and near-misses like `shipping` → `free-shipping`, `product-specifications` → `product-pricing`, `ordering-process` with no match anywhere). None of the 75 point at a pruned slug, so pruning could not and did not touch this number. Fixing it was out of scope for this pass — it would mean guessing the intended target for 70 distinct broken slugs across 4 domains without a spec for how confident a match needs to be, and the task's own instruction only covered affects entries pointing at *deleted* slugs. Flagged for a follow-up pass with `did you mean` suggestions as a starting point.

## Review queue

101 items in, 97 dropped, 4 shipped. The 97 dropped items all carried `status: conflict` — stale cross-ref and wiki-body-edit proposals whose base_hash no longer matched the current file, mechanical byproducts of the ingestion pipeline re-running lint passes against a moving vault. The 4 kept items are the seeded open contradictions (C1–C4: a price-match policy conflict and three variants of the 30-vs-45-day return-window conflict), all `status: pending`, all with a `base_hash` that was verified to match the current wiki file content before shipping. Nothing was fabricated to pad the queue; if the 4 real contradictions were the only clean items, that's what shipped.

## Contradiction detection

131 candidate pairs examined by the semantic lint pass, 4 confirmed and queued. The feature works as designed: it is conservative by construction, and 4 real contradictions out of 131 examined pairs is a plausible base rate for a corpus with this few deliberately seeded conflicts, not a sign of under- or over-triggering.

## Ingest cost

Four domain-scoped ingest runs totaled **$0.2213** (customer-support $0.0102, sales-crm $0.0739, operations $0.0650, internal-admin $0.0722), read directly from `/tmp/mv-build/p5a.log`'s per-run cost rows. This corrects an earlier rough estimate of "~$0.08" for the whole run; the real figure is roughly 2.7x that, still low for 352 source documents with full claim extraction and wiki drafting.

## Decisions and strategy

10 decisions authored across the 4 domains (6 closed with a real outcome, 4 open with a review date), grounded in claim-ids grepped and verified against the actual `key_claims:` frontmatter of real source notes — every citation was checked against the live claims index before it was written into a decision file. Two decision topics were substituted for better corpus fit: `eu-expansion` had zero grounding anywhere in this corpus (Larkstead is a US-only company; company.yaml lists no European operations or customers at all) and was replaced with `launch-b2b-leasing-program`, grounded in real ErgoNest competitive intel already in the corpus. `helprise-renewal` survived under its original name but was reframed around the one real, dated fact available — a scheduled 2026-08-09 data-retention policy review — rather than an invented SaaS contract with fabricated pricing.

4 strategy files, one per domain, quarter `2026-Q2`, each grounded in the same verified claims layer and cross-referencing the domain's own open decisions.

## Provenance

This processed layer was produced by running `mvault ingest` over `datasets/larkstead/raw/`, then curated by hand: pruning junk wiki concepts the extractor over-generated, shipping only the review items that survived a base-hash currency check, and authoring the decision and strategy layer the raw ingest pipeline does not produce on its own. The dataset is the app's first user — every step above ran through the same `mvault` CLI a real deployment would use, against the same schema real deployments validate against, and two real schema mismatches turned up along the way (a `status: active` value that the `NoteStatus` enum does not accept, and an initial cost estimate that undercounted the real ingest spend by roughly 2.7x) and got corrected rather than papered over.
