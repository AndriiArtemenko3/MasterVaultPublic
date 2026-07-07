---
domain: customer-support
type: source
title: Macro Lot 2025 14 Mat Warp
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: process-macro-lot-2025-14-mat-warp-01
  statement: The process for LOT-2025-14 mat warp is effective from 2025-08-06.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-02
  statement: Customers must photograph the curled edge and recycle the mat; shipping back is not required.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-03
  statement: There is no restocking fee on any of these mats, defective or unopened.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-04
  statement: A 10% restocking fee applies only to opened, non-defective returns, not this batch.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-05
  statement: Customers can choose between a full refund, including shipping, or a free replacement.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-06
  statement: B2B accounts escalate directly to Tom Aldridge for resolution.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-07
  statement: Every ticket must be tagged as lot-2025-14 in Helprise, including retroactively.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-08
  statement: The six previous tickets (04217, 04263, 04301, 04342, 04371, 04395) received retroactive tagging today.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-09
  statement: Replacements for tickets from 04402 onward will be handled directly according to the LOT-2025-14 process.
  confidence: high
  affects: []
- id: process-macro-lot-2025-14-mat-warp-10
  statement: Replacements held in quarantine will ship the week of 2025-08-11 once cleared for humidity-cycles.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/process/macro-lot-2025-14-mat-warp.md
provenance_hash: ccf6a7b336c0f5dd
---

# Macro Lot 2025 14 Mat Warp

## Summary

Process: LOT-2025-14 [[mat-warp]] response Effective: 2025-08-06 Owner: Priya Raman Teams: support, warehouse, sales Trigger: a Helprise ticket reports a curled or warped edge on an Alder desk mat (LS-MAT-001-CHL, LS-MAT-001-SND, LS-MAT-001-SGE) where the order was placed on or after 2025-06-20. Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | intake | agent | Helprise | ticket tagged lot-2025-14 | | 2 | offer | agent | Helprise | customer has chosen full refund or free replacement | | 3 | fulfill | Hank / ParcelPoint | ParcelPoint | replacement ships the week of 2025-08-11 once cleared stock is released, or refund posted | | 4 | B2B check | agent | Helprise | account flagged B2B routed to Tom, standard text withheld | Rules - No return required.

## Content

Process: LOT-2025-14 mat warp response
Effective: 2025-08-06
Owner: Priya Raman   Teams: support, warehouse, sales

Trigger: a Helprise ticket reports a curled or warped edge on an Alder
desk mat (LS-MAT-001-CHL, LS-MAT-001-SND, LS-MAT-001-SGE) where the order
was placed on or after 2025-06-20.

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | intake | agent | Helprise | ticket tagged lot-2025-14 |
| 2 | offer | agent | Helprise | customer has chosen full refund or free replacement |
| 3 | fulfill | Hank / ParcelPoint | ParcelPoint | replacement ships the week of 2025-08-11 once cleared stock is released, or refund posted |
| 4 | B2B check | agent | Helprise | account flagged B2B routed to Tom, standard text withheld |

Rules
- No return required. Customer photographs the curled edge and recycles
  the mat, no need to ship it back.
- No restocking fee on any of these, defective or not opened. The 10% fee
  only applies to opened, non-defective returns, so it doesn't touch this
  batch at all.
- Customer choice, not agent's: full refund including the shipping they
  paid, or a free replacement. Don't push one over the other.
- B2B accounts escalate straight to Tom Aldridge. Do not send the
  standard customer-facing text to a B2B contact, it reads wrong for that
  relationship and Tom needs to be the one talking to them anyway.
- Tag every ticket lot-2025-14 in Helprise, including retroactively. The
  six tickets we handled before we saw the pattern (04217, 04263, 04301,
  04342, 04371, 04395) get the tag added today even though they closed
  under the old one-off handling.

Example run
- 04217 through 04395 handled individually 05 Jul through 30 Jul, before
  the pattern was confirmed. Retro-tagged lot-2025-14 today, 2025-08-06.
- Tickets from 04402 onward follow this process directly. Replacements
  held in quarantine ship the week of 2025-08-11 once Dmitri clears the
  humidity-cycle result.
