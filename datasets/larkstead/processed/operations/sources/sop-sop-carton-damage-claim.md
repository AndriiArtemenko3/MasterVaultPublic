---
domain: operations
type: source
title: Sop Carton Damage Claim
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-carton-damage-claim-01
  statement: Cascadia Freight requires filing claims within 5 business days of receipt.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-02
  statement: A write-off crossing 250.00 needs Dmitri's sign-off.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-03
  statement: A write-off crossing 1000.00 needs Mara's sign-off, per the expense policy effective 2024-01-15.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-04
  statement: Claims over 500.00 on a single load go to Dmitri same day.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-05
  statement: Photograph the pallet before any further carton is opened, including the shrink wrap condition.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-06
  statement: Log the claim number, filing date, and claimed dollar amount in Ledgerly as a pending recovery.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-07
  statement: File and track damage claims against inbound carriers to recover freight-caused damage instead of absorbing it as scrap.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-08
  statement: Don't wait on the carrier's decision before replacing customer-facing stock.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-09
  statement: Close the Ledgerly entry when the carrier pays, declines, or 30 days pass with no response.
  confidence: high
  affects: []
- id: sop-sop-carton-damage-claim-10
  statement: The document applies to warehouse crew and operations.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-carton-damage-claim.md
provenance_hash: 99ef8a572928bf5e
---

# Sop Carton Damage Claim

## Summary

SOP: Carton damage claim Effective: 2024-04-15 Owner: Dmitri Okafor (DO) Applies to: warehouse crew, operations Systems: Ledgerly, ParcelPoint portal Purpose File and track damage claims against inbound carriers so freight-caused damage gets recovered instead of absorbed as scrap, and get replacement stock moving without waiting on the carrier's answer. Prerequisites - Damage photographed at receiving per the inbound receiving inspection SOP, effective 2024-02-12 - Carrier name and load date known Steps 1.

## Content

SOP: Carton damage claim
Effective: 2024-04-15
Owner: Dmitri Okafor (DO)
Applies to: warehouse crew, operations
Systems: Ledgerly, ParcelPoint portal

Purpose
File and track damage claims against inbound carriers so freight-caused damage gets recovered instead of absorbed as scrap, and get replacement stock moving without waiting on the carrier's answer.

Prerequisites
- Damage photographed at receiving per the inbound receiving inspection SOP, effective 2024-02-12
- Carrier name and load date known

Steps
1. Photograph the pallet before any further carton is opened: shrink wrap condition, all four sides, the carrier's load label, and the pallet as a whole.
2. File the claim with the carrier inside its claim window. Cascadia Freight requires filing within 5 business days of receipt. Miss that window and the claim is dead.
3. Note the invoice or PO the damaged carton was received against, and record the affected quantity and lot number if one is printed on the carton.
4. Don't wait on the carrier's decision before replacing customer-facing stock. Pull replacements from good inventory the same day and note the pull against the open claim in Ledgerly.
5. Log the claim number, filing date, and claimed dollar amount in Ledgerly as a pending recovery. Use landed cost per unit, never list price, for the claimed figure.
6. If the carrier declines, book the loss as scrap at landed cost. A write-off crossing 250.00 needs Dmitri's sign-off; at or above 1000.00, Mara signs off, per the expense policy effective 2024-01-15.
7. Close the Ledgerly entry when the carrier pays, declines, or 30 days pass with no response, whichever comes first.

Escalation
Claims over 500.00 on a single load, or three or more claims against one carrier inside a month, go to Dmitri same day, regardless of where the claim sits in the steps above.

Close-out
12 Apr: two Cascadia loads this quarter, one closed at 340.00 paid, one still open. The claim log carries filing date, resolution, and dollar recovered or written off for every entry.

References
- Cascadia Freight (VEN-06), carrier contract effective 2023-02-15
- Expense policy: self-approve under 250.00, manager band 250.00 to 999.99, CEO from 1000.00, effective 2024-01-15
- Inbound receiving inspection SOP, effective 2024-02-12
