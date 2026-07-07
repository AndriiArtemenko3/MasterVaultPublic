---
domain: operations
type: source
title: Sop Bundle Kitting Work Order
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-bundle-kitting-work-order-01
  statement: Bundle kitting work order is effective from 2025-01-13.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-02
  statement: Dmitri Okafor (DO) is the owner of the Bundle kitting work order.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-03
  statement: The purpose is to generate a component pull list for every bundle order.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-04
  statement: The component pull list confirms the 6.00 per-kit fee posts correctly once ParcelPoint invoices for it.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-05
  statement: A bundle order must be confirmed in Shopstack or Pipewell.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-06
  statement: The bundle SKU can be LS-BDL-001, LS-BDL-002, or LS-BDL-003.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-07
  statement: LS-BDL-001 (Fledgling) pulls 1 LS-DSK-001-48, 1 LS-CHR-001-BLK, and 1 LS-ACC-002.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-08
  statement: ParcelPoint kits 40 components into one carton set per unit and charges 6.00 per kit assembled.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-09
  statement: The invoiced amount for 40 kits at 6.00 each should post as 240.00.
  confidence: high
  affects: []
- id: sop-sop-bundle-kitting-work-order-10
  statement: Any invoice mismatch over 12.00 gets flagged to Dmitri the day the invoice surfaces.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-bundle-kitting-work-order.md
provenance_hash: b385150825445547
---

# Sop Bundle Kitting Work Order

## Summary

SOP: Bundle kitting work order Effective: 2025-01-13 Owner: Dmitri Okafor (DO) Applies to: operations, ParcelPoint liaison Systems: ParcelPoint portal, Ledgerly Purpose Generate the component pull list for every bundle order so ParcelPoint kits the right parts in the right quantity, and confirm the 6.00 per-kit fee posts correctly once ParcelPoint invoices for it. Prerequisites - Bundle order confirmed in Shopstack or Pipewell with the bundle SKU, quantity, and ship-to on file - ParcelPoint portal work-order screen open, since pull lists submit there rather than by email Steps 1.

## Content

SOP: Bundle kitting work order
Effective: 2025-01-13
Owner: Dmitri Okafor (DO)
Applies to: operations, ParcelPoint liaison
Systems: ParcelPoint portal, Ledgerly

Purpose
Generate the component pull list for every bundle order so ParcelPoint kits the right parts in the right quantity, and confirm the 6.00 per-kit fee posts correctly once ParcelPoint invoices for it.

Prerequisites
- Bundle order confirmed in Shopstack or Pipewell with the bundle SKU, quantity, and ship-to on file
- ParcelPoint portal work-order screen open, since pull lists submit there rather than by email

Steps
1. Pull the bundle SKU off the order: LS-BDL-001, LS-BDL-002, or LS-BDL-003. Confirm it against the component list before opening a work order, not from memory. No shortcuts.
2. Break the bundle into its components. LS-BDL-001 (Fledgling) pulls 1 LS-DSK-001-48, 1 LS-CHR-001-BLK, 1 LS-ACC-002. LS-BDL-002 (Canopy) pulls 1 LS-DSK-001-60, 1 LS-CHR-001-BLK, 1 LS-ARM-001-SGL, 1 LS-ACC-002. LS-BDL-003 (Roost) pulls 1 LS-DSK-001-72, 1 LS-CHR-001-BLK, 1 LS-ARM-001-DBL, 1 LS-LMP-001, 1 LS-MAT-001-CHL, 1 LS-ACC-003.
3. Enter the component pull list in the ParcelPoint portal as a single work order tied to the order number, one line per component SKU and quantity. A multi-seat B2B order pulls one work order per seat, never one work order covering the whole seat count.
4. Submit the work order and note the ParcelPoint confirmation number in the order record. ParcelPoint kits the components into one carton set per unit and charges 6.00 per kit assembled.
5. When the ParcelPoint invoice arrives, check the kitting line against the number of work orders submitted that period. 40 kits at 6.00 each should post as 240.00, not a rounded figure.
6. If the invoiced kit count is lower than the work orders submitted, hold the invoice and confirm with ParcelPoint whether a kit failed assembly or shipped as loose components before approving payment.
7. If a kit ships with the wrong component substituted, for example the wrong chair color, flag it to Dmitri directly. This is not a restock or return, it is a kitting error and gets tracked separately from customer-caused returns.

Escalation
Any invoice mismatch over 12.00, two kits' worth, or any wrong-component kit gets flagged to Dmitri the day the invoice or the shipping error surfaces. He holds payment on the affected line until ParcelPoint confirms the correction.

Close-out
A kitting run closes when the work order confirmation number, the ParcelPoint kit count, and the invoice line all match. Mismatches stay open until ParcelPoint issues a corrected invoice or a credit.

References
- ParcelPoint Fulfillment (VEN-04), Net-30 terms, bundle kitting at 6.00 per kit per the 2023-11-01 contract
- Component lists for LS-BDL-001, LS-BDL-002, LS-BDL-003
