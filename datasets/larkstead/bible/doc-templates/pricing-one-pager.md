# Template: B2B pricing one-pager

Doc class: SALES-CRM. Internal sales reference, sometimes left behind with
prospects. List prices only; unit costs and margins never appear on this doc.
Read with `../company.yaml` and `../style-rules.md`. Zero typos.

## Skeleton

```
Larkstead Goods Co. -- B2B pricing
Effective: <YYYY-MM-DD>            <- the price/tier change date it encodes
Prepared: <YYYY-MM-DD> by <staff full name>
Replaces: sheet effective <YYYY-MM-DD>

## List prices

| SKU | Product | List |
|---|---|---|
| <SKU> | <name> | <0.00> |

## Volume discount

<current tier, with its effective date. one tier, stated plainly.>

## Exceptions

<the SL3 exception verbatim from policy: who may grant it, the size,
the approval threshold.>

## Notes

<grandfathering rule, freight rule, quote validity>
```

## Exemplar (half length)

```
Larkstead Goods Co. -- B2B pricing
Effective: 2026-01-15
Prepared: 2026-01-16 by Tom Aldridge
Replaces: sheet effective 2025-12-08

## List prices

| SKU | Product | List |
|---|---|---|
| LS-BDL-001 | Fledgling workstation bundle | 949.00 |
| LS-BDL-002 | Canopy team bundle (per seat) | 1,149.00 |
| LS-BDL-003 | Roost executive bundle | 1,499.00 |
| LS-DSK-001-60 | Birch standing desk, 60 in | 679.00 |
| LS-CHR-001-BLK | Rowan task chair, graphite black | 409.00 |
| LS-ARM-001-DBL | Heron monitor arm, dual | 199.00 |
| LS-LMP-001 | Vireo smart desk lamp | 159.00 |

## Volume discount

12% off list at 30 or more units (tier effective 2025-12-08).

## Exceptions

Sales lead may grant a one-time extra 3% on deals of 30 or more seats
(exception code SL3, effective 2025-10-06). CEO sign-off required when
the exception's dollar value reaches 1,000.00.

## Notes

Deals signed before 2025-12-08 keep their contracted tier. Heavy items
bill freight on every order; quotes hold for 30 days from issue.
```

## Realism notes

- A new sheet is issued at every price or tier change, and old sheets keep
  circulating. Canon: Tom quoted Nordlicht 15% from the pre-2025-12-08 sheet
  in a 2026-01 call and had to walk it back. Stale-sheet incidents are
  storyline material, not errors to scrub.
- The Effective date is the pricing regime; Prepared is when the sheet was
  typed. They differ by days and both must be coherent with company.yaml.
- List only. Unit costs (212.00 on the 48 in desk, etc.) exist in
  company.yaml for finance docs; on a leave-behind they are a leak.
- One live tier at a time. Sheets never show old and new tiers side by
  side; the Replaces line is the only pointer backward.
- The exception text matches policy wording: one-time, 3%, 30+ seats,
  CEO sign-off at 1,000.00 of value. On Cobalt Dental the 3% was worth
  1,318.80, which is why Mara signed.
- Prices shown must all be the entries in force on the Effective date; a
  2026 sheet showing the 189.00 dual arm is a defect (199.00 from 2026-01-15).
