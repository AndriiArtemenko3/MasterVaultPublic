# The Larkstead Goods Co. dataset

## The dataset is the product's first user

The usual way to demo a RAG system is to hand-pick a few dozen documents
that make the retrieval look good. Larkstead was built the opposite way: 372
raw business documents were generated against a single bible file, checked
by a mechanical consistency checker, and then run through the real
`mvault ingest` pipeline to produce everything this demo loads. Every claim,
wiki concept, and review-queue item under `datasets/larkstead/processed/`
came out of the same CLI a real deployment uses, validated against the same
schema. Two real schema mismatches turned up during that build (a
`status: active` value the `NoteStatus` enum did not accept, and an early
cost estimate that undercounted real ingest spend by roughly 2.7x) and got
fixed rather than smoothed over in the writeup. `datasets/larkstead/processed/MANIFEST.md`
carries the full, warts-and-all account of that build.

## The company bible

`datasets/larkstead/bible/company.yaml` is the single source of truth every
corpus writer and checker traced back to: Larkstead Goods Co., a fictional
Portland, Oregon ergonomic-furniture company founded 2022-06-01, selling
direct-to-consumer since launch and to 8-60 seat B2B accounts since mid-2024.
Every staff member has a voice card (Mara Voss, the founder, writes short
declaratives and signs messages "M"; Dmitri Okafor, operations, always leads
with the number). Every policy that changes over time is dated:
`policies.refund_window`, `policies.free_shipping_threshold`,
`policies.price_match`, and half a dozen others each carry an ordered list
of `{terms, effective}` entries, and every document that mentions one of
these policies is written to be correct for its own date, not necessarily
the current one. That dated-policy-history mechanism is also what generates
most of the corpus's contradictions: a document is never wrong, but two
documents from different dates can both be true and still disagree.

## Five storylines, four confirmed contradictions

Five storylines interlock across all four domains and account for 89 of the
372 raw documents (the other 283 are date- and price-consistent filler:
tickets, invoices, memos, and the rest of the day-to-day texture a real
company produces). Each storyline seeds one explicit, labeled contradiction
between a stale document and whatever superseded it.

<details>
<summary>Spoiler: the five storylines and their contradictions</summary>

| Storyline | What happens | Contradiction |
|---|---|---|
| **SL1** — Alder mat warping defect | A Verdant Textiles lot (1,400 desk mats) has an adhesive-cure defect; 14 support tickets trace to it, QA confirms 127 defective units (9.07%), and a formal vendor claim nets a $1,638.30 credit against an $18,060.00 invoice. | **C5**: a support agent quotes the standard 30-day return policy verbatim to a customer asking about the lot; four days later a finance exception memo makes any Alder mat from this lot returnable through 2025-12-31 regardless of purchase date. Both were correct the day they were written. |
| **SL2** — Refund window change | The first written returns policy (30 days, 2024-01-15) governs two years of ordinary and two escalated denials, then a holiday exception extends it to 45 days from 2025-11-03, made permanent 2026-01-12. | **C1**: the public FAQ and two Helprise macros were assigned to support for an update and never touched. Every document dated before 2025-11-03 saying 30 days is correct for its date; the FAQ is simply never fixed. |
| **SL3** — Cobalt Dental 40-seat B2B deal | A four-clinic dental group closes a $36,345.20 deal after a five-month negotiation, including a one-time 3% pricing exception that needed the founder's sign-off. | **C3**: a 2024-06 sales one-pager still says 15% off at 25+ units after a 2025-12-08 tier sheet cuts the volume discount to 12% at 30+ units. The one-pager was supposed to be pulled from the shared drive; it wasn't, and a sales rep quoted the stale number on a real call. |
| **SL4** — Vireo firmware v1.2 flicker bug | An OTA firmware update makes ~618 lamp units flicker in sleep mode; a staged rollback restores most of them, and Larkstead grants the affected units a goodwill warranty extension. | **C4**: the 2024 help doc still says every Vireo lamp carries a 1-year warranty. The 2026-01-20 goodwill memo only extends coverage to 2 years for the ~618 firmware-affected units; every other lamp is still on the standard 1-year term, and the help doc was never updated to explain the split. |
| **SL5** — 3PL cutover to ParcelPoint | Outbound fulfillment moves entirely to ParcelPoint; a billing dispute over a disallowed dimensional-weight adjustment gets a $2,392.50 correction. | **C2**: a 2026-01-15 policy update raises the free-shipping threshold from $75.00 to $99.00; the 2024 shipping FAQ still quotes $75.00 and is never updated. |

</details>

That table is the narrative ground truth baked into the raw text. What
actually shipped in the review queue is smaller and more interesting than a
clean 1-for-1 match: the semantic contradiction pass examined 131 candidate
wiki-claim pairs and confirmed 4, all under `customer-support/wiki/`:

- `lint-contradiction::price-matching` (1 item) — a pair the storyline table
  above does not even label. `company.yaml`'s `policies.price_match` history
  ("no price matching" through 2024, "match identical-spec list price from
  US-based sellers" from 2025-05-19) generates its own live contradiction
  the same way the five labeled storylines do, and the detector found it
  without being pointed at it.
- `lint-contradiction::return-policy` (3 items) — three separate stale/current
  claim pairs, all resolving to C1's 30-vs-45-day split, caught as three
  distinct statement pairs rather than one merged finding.

C2, C3, C4, and C5 are real, present in the raw text exactly as the table
describes, and retrievable (`mvault search`/`mvault ask` will surface both
sides of any of them). They did not happen to pair up as confirmed
contradictions in this particular semantic-lint run against this wiki
layer. That is reported here rather than rounded up to "4 of 5 confirmed,"
because the actual shipped queue is 1 price-match pair plus 3 variants of
one return-window pair, and a demo dataset that claims otherwise would be
lying about its own retrieval product.

## Quality gates

Two independent checks ran against the raw corpus before it shipped, and
both are reproducible:

**Mechanical checker** (`datasets/larkstead/qa/mechanical_check.py`, run
against `bible/company.yaml` + `bible/storylines/*.yaml` as ground truth).
Ten checks: every SKU/vendor/ticket/invoice/order/lot/opportunity id
resolves against a real grammar in the bible; staff full names match a real
staff member; a dollar amount near a SKU token matches that SKU's dated
price; every invoice-shaped table balances (`qty × unit price == line
total`, `subtotal − discount + shipping + tax == total`, blocking on any
mismatch ≥ $0.02); ticket and chat timestamps are monotonically
non-decreasing within a file; every storyline beat's `doc_id` has a matching
raw file; no ticket or invoice id is the primary subject of two unrelated
files; and `banned_strings` (see below) never appear. Run it yourself:

```bash
uv run python datasets/larkstead/qa/mechanical_check.py
```

`datasets/larkstead/qa/violations.jsonl` is the frozen output: zero lines,
zero violations, independently re-run to confirm before the corpus was
committed.

**Rubric judges.** Four LLM-based critic passes reviewed the generated
corpus for voice consistency (does a document sound like the staff member's
voice card), factual grounding (does every number trace to the bible),
narrative coherence (do storyline beats connect), and contradiction
integrity (are C1-C5 actually present and not accidentally "fixed" by a
writer agent trying to be helpful). Findings from these passes fed back into
a regeneration pass before the raw corpus was frozen.

## No real trademarks

`bible/company.yaml`'s `banned_strings` list is the enforcement mechanism,
not a leak: it names real platforms and brands precisely so the mechanical
checker can reject any document that mentions one.

```yaml
banned_strings:
  - "shopify"
  - "zendesk"
  - "hubspot"
  # ... 28 more, including "amazon", "ikea", "steelcase", "herman miller"
  - "halo"   # retired product name; also blocks the real HALO lighting mark
```

Every tool Larkstead uses in the corpus is a fictional stand-in with a
declared real-world analog category: Shopstack (storefront), Helprise
(helpdesk), Pipewell (CRM), Ledgerly (accounting), ParcelPoint (3PL),
Mailloft (email marketing). The one near-miss caught during generation was
the smart desk lamp's working name, "Halo," which collides with a real
lighting brand; the bible renamed it "Vireo" and added "halo" to the
denylist so the old name cannot resurface in a later writer pass. If you
grep the corpus for a real company name and find one, that is a bug — file
it.

## Reproducing the processed layer

The processed layer under `datasets/larkstead/processed/` is not hand-edited
raw output. It came from four domain-scoped `mvault ingest` runs over
`datasets/larkstead/raw/` (total cost $0.2213 against `gpt-4o-mini`, per the
real per-run logs), followed by hand curation: pruning wiki concepts the
extractor over-generated with no inbound claims (65 drafted, 43 survived),
shipping only review items whose `base_hash` still matched the live wiki
file, and authoring the 10 decisions and 4 strategy files ingest does not
produce on its own, each grounded in real claim-ids grepped and verified
against the live claims index before being written down. The `operations`
domain ended up with zero surviving wiki concepts: every concept the
extractor drafted for that domain named a specific entity (a vendor code, an
invoice recipient) rather than a reusable concept, and every one had zero
inbound claims. That is reported as a real finding about extractor behavior
on that domain's document mix, not quietly pruned away.

`datasets/larkstead/processed/MANIFEST.md` has the full per-domain counts,
the wiki-pruning accounting, and the review-queue accounting (101 items
proposed, 97 dropped as stale conflicts from re-running lint against a
moving vault, 4 shipped).
