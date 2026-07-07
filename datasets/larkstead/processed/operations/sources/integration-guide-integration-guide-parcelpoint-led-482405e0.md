---
domain: operations
type: source
title: Integration Guide Parcelpoint Ledgerly Monthly Invoice Import
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-01
  statement: Monthly, ParcelPoint posts one invoice to the billing portal around the 4th or 5th of the month for the prior month's activity.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-02
  statement: Ana pulls the invoice into Ledgerly by hand the same week it lands, Pacific time.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-03
  statement: ParcelPoint's export is a PDF and a line-item CSV, and Ana keys the CSV into Ledgerly's import template herself.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-04
  statement: Ana holds the Ledgerly import queue and view-only access to ParcelPoint's billing portal.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-05
  statement: Dmitri has read access to the same billing portal but cannot post anything in Ledgerly.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-06
  statement: When Dmitri or Ana flags a line item as disputed, that line's dollar amount books to 2150 Disputed invoices holding.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-07
  statement: If ParcelPoint's invoice hasn't landed in the billing portal by the 10th of the month, Ana holds the prior month open one more cycle.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-08
  statement: Changes to the field mapping are owned by Ana but need Dmitri's sign-off if they relate to how disputed lines route through 2150.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-parcelpoint-led-482405e0-09
  statement: By the 10th of the following month, Ana reconciles that month's total ParcelPoint invoice against the sum of the 5100, 5110, 5120, and 5200 entries she booked.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/integration-guide/integration-guide-parcelpoint-ledgerly-monthly-invoice-import.md
provenance_hash: acb4f0521de5e057
---

# Integration Guide Parcelpoint Ledgerly Monthly Invoice Import

## Summary

Integration guide: ParcelPoint monthly invoice import to Ledgerly Systems: ParcelPoint (3PL portal) -> Ledgerly (accounting) Owner: Ana Petrova (AP) Maintainer: Ray Lindqvist (RL) Written: 2026-04-20 Schedule Monthly, not nightly. ParcelPoint posts one invoice to the billing portal around the 4th or 5th of the month for the prior month's activity, and Ana pulls it into Ledgerly by hand the same week it lands, Pacific time.

## Content

Integration guide: ParcelPoint monthly invoice import to Ledgerly
Systems: ParcelPoint (3PL portal) -> Ledgerly (accounting)
Owner: Ana Petrova (AP)   Maintainer: Ray Lindqvist (RL)
Written: 2026-04-20

Schedule
Monthly, not nightly. ParcelPoint posts one invoice to the billing portal around the 4th or 5th of the month for the prior month's activity, and Ana pulls it into Ledgerly by hand the same week it lands, Pacific time. There's no automated feed the way the Shopstack order journal has one; ParcelPoint's export is a PDF and a line-item CSV, and Ana keys the CSV into Ledgerly's import template herself.

Access
Ana holds the Ledgerly import queue and view-only access to ParcelPoint's billing portal, enough to pull the CSV without touching the fulfillment side of the account. Dmitri has read access to the same billing portal so he can flag a line before Ana books it, but he can't post anything in Ledgerly. Ray holds neither; this feed doesn't run through any code he maintains.

Field mapping
| ParcelPoint invoice line | Ledgerly destination | note |
|---|---|---|
| pallet storage | 5100 Warehousing expense | monthly, per pallet-position |
| pick-pack | 5110 Fulfillment labor | per order picked and packed |
| additional-item picks | 5110 Fulfillment labor | items beyond the first per order, same account |
| bundle kitting | 5120 Kitting fee expense | 6.00 per kit, LS-BDL-* orders only |
| outbound postage passthrough | 5200 Freight out | billed at ParcelPoint's cost, no markup |
| any line flagged disputed | 2150 Disputed invoices holding | posts here instead of its usual account until resolved |

Dispute-hold flag handling
When Dmitri or Ana flags a line item as disputed, meaning either Larkstead is contesting the charge or ParcelPoint itself has marked it pending review, that line's dollar amount books to 2150 Disputed invoices holding rather than to 5100, 5110, 5120, or 5200. It stays there until ParcelPoint issues a credit memo or Ana confirms the charge is correct after all. Say a carton is marked damaged-on-arrival and ParcelPoint bills a 42.00 replacement-packaging fee Ana thinks should have been covered under the GreenCrate carton warranty instead: that line holds in 2150 on invoice INV-PPF-2026-705 until Dmitri resolves it with ParcelPoint's account rep, at which point Ana reclasses it to 5200 if it's upheld or reverses it entirely against a credit memo if it isn't. The holding account exists so a disputed charge never quietly inflates the month's freight or warehousing cost while it's still being argued over.

Failure modes
- Kitting count mismatch: ParcelPoint bills kitting fees by kits physically assembled that month, which should equal the bundle-order volume the companion Shopstack-to-ParcelPoint guide tracks, but a partial bundle-explosion failure on the order side can leave the two counts off by a few units. Detect it by reconciling the invoiced kit count against Shopstack's bundle-order total for the same period at month-end close; fix by requesting an itemized kit log from ParcelPoint rather than adjusting the invoice on assumption.
- Disputed flag missed at entry: a line marked disputed on the PDF but not flagged during Ledgerly entry posts straight to its normal expense account and overstates that account for the month. This usually surfaces at Ana's month-end reconciliation, not before, since the PDF and the CSV don't always agree on which lines carry the flag. Fix by moving the line to 2150 retroactively with a memo noting the correction and the invoice number it came from.
- Late invoice: if ParcelPoint's invoice hasn't landed in the billing portal by the 10th of the month, Ana holds the prior month open one more cycle rather than guessing at the total, and flags Dmitri if the delay runs past five business days.

Change control
Ana owns any change to the field mapping outright since it's her account structure, but a change that touches how disputed lines route through 2150 needs Dmitri's sign-off first, given he's the one fielding the vendor side of a dispute. Nothing here runs through code, so there's no sandbox to test against; changes take effect on the next month's import.

Verification
By the 10th of the following month, Ana reconciles that month's total ParcelPoint invoice against the sum of the 5100, 5110, 5120, and 5200 entries she booked, plus whatever remains open in 2150, and confirms the three numbers tie out to the cent before she closes the books.
