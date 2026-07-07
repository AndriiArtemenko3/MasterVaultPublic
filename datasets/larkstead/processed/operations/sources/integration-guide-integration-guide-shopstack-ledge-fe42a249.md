---
domain: operations
type: source
title: Integration Guide Shopstack Ledgerly Daily Export
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-01
  statement: Ana Petrova is the owner of the Shopstack and Ledgerly integration.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-02
  statement: Ray Lindqvist is the maintainer of the Shopstack and Ledgerly integration.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-03
  statement: The integration was written on 2024-05-14 and updated on 2025-06-10.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-04
  statement: The nightly export occurs at 02:15 Pacific.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-05
  statement: The export covers all orders captured up to midnight.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-06
  statement: Refunds post on the refund date, not the original order date.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-07
  statement: Ana holds Shopstack admin and the Ledgerly import queue access.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-08
  statement: Ray holds Shopstack admin, but has no Ledgerly login.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-09
  statement: Mapping changes made by Ray need Ana's review before posting.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-10
  statement: Shopstack field 'order.subtotal' maps to Ledgerly destination '4000 Sales'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-11
  statement: Shopstack field 'order.discount_total' maps to Ledgerly destination '4090 Discounts'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-12
  statement: Shopstack field 'order.shipping_collected' maps to Ledgerly destination '4200 Shipping income'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-13
  statement: Shopstack field 'order.tax_collected' maps to Ledgerly destination '2300 Sales tax payable'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-14
  statement: Refund amounts post on the refund date.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-15
  statement: Restocking fees retained are posted under Ledgerly account '4110 Restocking fee retained'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-16
  statement: Restocking fees are waived automatically on B2B orders of 10 or more units.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-17
  statement: Ana pulls Ledgerly's journal lines every Friday and reconciles them with Shopstack's order totals.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-18
  statement: A mismatch under '4000 Sales' indicates a same-day order edit that fell past the 02:15 cutoff.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-19
  statement: Shopstack retries a feed outage at 03:15 and 04:15, then emails Ana directly.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-20
  statement: Mapping changes happen only on Tuesdays and Thursdays after Ray's contract.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-shopstack-ledge-fe42a249-21
  statement: Ana reconciles the journal lines against Shopstack's order totals weekly on Fridays.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/integration-guide/integration-guide-shopstack-ledgerly-daily-export.md
provenance_hash: 5d01929c5142ad5a
---

# Integration Guide Shopstack Ledgerly Daily Export

## Summary

Integration guide: Shopstack order journal to Ledgerly Systems: Shopstack (storefront) -> Ledgerly (accounting) Owner: Ana Petrova (AP) Maintainer: Ray Lindqvist (RL) Written: 2024-05-14 Updated: 2025-06-10: added the restocking-fee sub-account and the B2B waiver handling below. Schedule Nightly export at 02:15 Pacific.

## Content

Integration guide: Shopstack order journal to Ledgerly
Systems: Shopstack (storefront) -> Ledgerly (accounting)
Owner: Ana Petrova (AP)   Maintainer: Ray Lindqvist (RL)
Written: 2024-05-14
Updated: 2025-06-10: added the restocking-fee sub-account and the B2B waiver handling below.

Schedule
Nightly export at 02:15 Pacific. The feed covers every order captured up to midnight, and anything placed after that cutoff simply waits for the next run rather than triggering a second pass. Refunds post on the refund date, not the original order date, which matters most at month boundaries, since a refund on an April order can land in the May journal instead.

Access
Ana holds Shopstack admin and the Ledgerly import queue. Ray holds Shopstack admin only, no Ledgerly login at all, so a mapping change he makes has no path to post itself without Ana reviewing the import first.

Field mapping
| Shopstack field | Ledgerly destination | note |
|---|---|---|
| order.subtotal | 4000 Sales | |
| order.discount_total | 4090 Discounts | contra-revenue |
| order.shipping_collected | 4200 Shipping income | zone rate per the shipping policy, zero on any order that clears the 75.00 free-shipping threshold |
| order.tax_collected | 2300 Sales tax payable | sub-account per ship-to state: 2300-WA, 2300-CA, 2300-US for every other taxed state; Oregon orders carry no tax line at all |
| refund.amount | 4100 Returns and allowances | posts on the refund date |
| refund.restocking_fee_withheld | 4110 Restocking fee retained | new account, added with this update; waived automatically on any B2B order of 10 or more units under the policy in force since 02 Jun 2025 |

Reconciliation report
Every Friday, Ana pulls Ledgerly's journal lines for the trailing seven days, grouped by account, and checks them against Shopstack's own weekly order-total export. If the two totals under 4000 Sales don't match to the cent, the mismatch is almost always a same-day order edit that fell on the wrong side of the 02:15 cutoff. One example from last month: order #LS64008, one Sparrow cable kit at 35.00 to an Oregon address, no tax, 5.95 light-zone shipping, 40.95 total charged. The customer opened it, didn't like the fit, and returned it inside the 30-day window. The restocking fee took 10% of the item price, 3.50, leaving a refund of 31.50 posted to 4100 net of the 3.50 held in 4110. A single-unit order like that one never qualifies for the B2B waiver, so Ana expects to see the 4110 line every time; its absence on an obviously single-seat return is worth a second look on its own.

Failure modes
- Order edited after its nightly export does not re-sync automatically. Detect it on the Friday reconciliation; fix it in Shopstack under Journal feed, Re-send, then confirm the corrected line landed in Ledgerly.
- Feed outage. Shopstack retries at 03:15 and 04:15, then emails ana@larkstead.example directly. Ana treats a missed retry as a same-day fix, not a Monday problem.
- Restocking fee posted on a qualifying B2B order. Detect when a return tagged 10+ units still carries a 4110 line; fix by reversing the entry by hand and flagging Ray in case the order's unit count didn't carry through the export correctly.

Change control
Mapping changes go through Ray, Tuesdays and Thursdays only per his contract, with Ana's sign-off, tested against the Ledgerly sandbox before touching the nightly run.

Verification
Ana reconciles the week's journal lines against Shopstack's order totals every Friday. That's the whole check.
