---
domain: operations
type: source
title: Bug Report Shopstack Wa Tax Rounding
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-report-bug-report-shopstack-wa-tax-rounding-01
  statement: On Washington orders, the cart rounds tax down instead of up on values ending in .x05.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-02
  statement: The Wren price change on September 1 pushed carts onto the tax rounding boundary.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-03
  statement: Five Washington customers have been undercharged by a cent each since the September 1 price change.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-04
  statement: 'Order #LS63722 on September 16 had a subtotal of 97.00 and tax posted 6.30 instead of 6.31.'
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-05
  statement: Ray Lindqvist reproduced the fault on September 18 with the stand-and-hook combination.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-06
  statement: The checkout module truncates the third decimal instead of rounding half up.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-07
  statement: Ana should true up the five known one-cent shortfalls at October close.
  confidence: medium
  affects: []
- id: bug-report-bug-report-shopstack-wa-tax-rounding-08
  statement: Oregon carts do not trigger the tax rounding issue as Oregon has no sales tax.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-report/bug-report-shopstack-wa-tax-rounding.md
provenance_hash: b8c2fff532abd641
---

# Bug Report Shopstack Wa Tax Rounding

## Summary

Bug report: shopstack-cart-wa-tax-rounding Date: 2024-09-19 Filed by: Priya Raman (PR) Filed with: Ray Lindqvist (contract Shopstack developer) System under report: Shopstack cart checkout, tax calculation module Related tickets: 5 to date -- HD-2024-63701, HD-2024-63704, HD-2024-63709, HD-2024-63713, HD-2024-63718 (first HD-2024-63701, 2024-09-05) Summary On Washington orders, the cart rounds tax down instead of up whenever the subtotal times 6.5 percent lands on a value ending in .x05, which has undercharged five Washington customers by a cent apiece since the 09-01 Wren price change pushed more carts onto that boundary. Reproduction steps 1.

## Content

Bug report: shopstack-cart-wa-tax-rounding
Date: 2024-09-19
Filed by: Priya Raman (PR)
Filed with: Ray Lindqvist (contract Shopstack developer)
System under report: Shopstack cart checkout, tax calculation module
Related tickets: 5 to date -- HD-2024-63701, HD-2024-63704, HD-2024-63709, HD-2024-63713, HD-2024-63718 (first HD-2024-63701, 2024-09-05)

Summary
On Washington orders, the cart rounds tax down instead of up whenever the subtotal times 6.5 percent lands on a value ending in .x05, which has undercharged five Washington customers by a cent apiece since the 09-01 Wren price change pushed more carts onto that boundary.

Reproduction steps
1. Add a Wren laptop stand (LS-STD-001, 79.00 at the post-09-01 price) and a Robin headphone hook (LS-ACC-006, 18.00) to a cart shipping to a Washington address.
2. Proceed to checkout. Subtotal reads 97.00.
3. Cart applies the 6.5 percent WA rate: 97.00 x 0.065 = 6.305. The tax field shows 6.30. It should show 6.31.

Affected population
Five live Washington orders since 05 Sep landed exactly on this boundary, most recently order #LS63722 on 16 Sep, subtotal 97.00, tax posted 6.30 instead of 6.31. No other zone has reported it, though nothing rules out the same fault sitting under an untested combination elsewhere in the catalog.

Evidence
All five tickets are Washington addresses, all five under-collected by exactly one cent, no exceptions. Oregon carts never trip it, since Oregon carries no sales tax. Ray reproduced the fault on 18 Sep with the same stand-and-hook combination and traced it to the checkout module truncating the third decimal instead of rounding half up.

Ask
Ship the rounding fix inside the October checkout tax project, already scoped. Until then, Ana should true up the five known one-cent shortfalls at October close so the WA tax remittance isn't short.
