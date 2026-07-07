---
domain: operations
type: source
title: Sop Production Lot Assignment
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-production-lot-assignment-01
  statement: Every inbound shipment gets a lot number the moment it's received.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-02
  statement: No shipment gets shelved without a lot number assigned.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-03
  statement: The current lot log holds the last number issued for the year.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-04
  statement: Lot numbers run LOT-YYYY-NN, where YYYY is the receiving year.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-05
  statement: The next number in sequence is assigned to the full shipment, regardless of carton count.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-06
  statement: A container of Ostrava frames might land as LOT-2024-33.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-07
  statement: The lot number is written on the vendor packing slip before it leaves the dock.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-08
  statement: Every shelf location holding units from this lot gets tagged with the lot number.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-09
  statement: If a shelf location holds an earlier lot of the same SKU, the two lots are not merged.
  confidence: high
  affects: []
- id: sop-sop-production-lot-assignment-10
  statement: 'The lot log closes out each lot with three dates: received, last unit shipped or culled, and retired.'
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-production-lot-assignment.md
provenance_hash: 5f8ff6b32e069cbc
---

# Sop Production Lot Assignment

## Summary

SOP: Production lot number assignment Effective: 2024-10-21 Owner: Hank Morrow (HM) Applies to: warehouse receiving crew Systems: Ledgerly Purpose Give every inbound shipment a lot number the moment it's received, so a defect pattern traced later points at one dated group of units instead of the whole SKU. No shipment gets shelved without one.

## Content

SOP: Production lot number assignment
Effective: 2024-10-21
Owner: Hank Morrow (HM)
Applies to: warehouse receiving crew
Systems: Ledgerly

Purpose
Give every inbound shipment a lot number the moment it's received, so a defect pattern traced later points at one dated group of units instead of the whole SKU. No shipment gets shelved without one.

Prerequisites
- Vendor packing slip or invoice at the dock, per the receiving inspection SOP
- The current lot log, which holds the last number issued for the year

Steps
1. Check the lot log for the highest LOT number issued so far this year. Lot numbers run LOT-YYYY-NN, where YYYY is the receiving year and NN is a two-digit sequence starting at 01 each January.
2. Assign the next number in sequence to the full shipment on the dock, regardless of carton count or vendor. One shipment, one lot number, even if it spans 14 pallets. A container of Ostrava frames received this week might land as LOT-2024-33, the next open slot in the log.
3. Write the lot number on the vendor packing slip in marker before it leaves the dock, and enter it in Ledgerly against the same PO line used in the receiving inspection SOP.
4. Tag every shelf location that holds units from this lot with the lot number, using the printed shelf tags, not handwritten ones taped over old tags.
5. If a shelf location already holds an earlier lot of the same SKU, do not merge the two lots on one tag. Split the location or add a second tag so each lot stays traceable to its own shelf space.
6. If a shipment arrives as two separate trucks on the same PO because of a split load, assign one lot number to the combined shipment rather than one per truck, and note both truck arrival times against that single lot.
7. Retire a lot number from the active shelf tags only once every unit against it has shipped or been culled. Log the retirement date next to the lot number in the log.

Escalation
A gap or duplicate in the sequence, caught at any point, goes to Dmitri the same day. Don't renumber anything yourself.

Close-out
The lot log closes out each lot with three dates: received, last unit shipped or culled, and retired. A lot with no retirement date after 12 months on the shelf gets flagged for review.

References
- Inbound receiving inspection SOP, effective 2024-02-12
- id_grammars.production_lot: LOT-YYYY-NN, two-digit sequence resetting each January
