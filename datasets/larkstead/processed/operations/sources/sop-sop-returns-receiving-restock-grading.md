---
domain: operations
type: source
title: Sop Returns Receiving Restock Grading
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-returns-receiving-restock-grading-01
  statement: Every returned unit is graded before it is restocked to ensure customers do not receive broken items as new.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-02
  statement: Bad stock gets scrapped, not restocked.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-03
  statement: A unit is unboxed on the grading table, not the shelf.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-04
  statement: Grade A units have original packaging and no signs of use, going back to sellable stock.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-05
  statement: Grade B units are cosmetically worn but function tested and clean; they are flagged 'open box' in Ledgerly.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-06
  statement: Grade C units have functional defects and do not go back on a shelf.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-07
  statement: Every confirmed Grade C unit is scrapped at landed cost in Ledgerly, not list price.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-08
  statement: A Rowan task chair scraps at 148.00 landed, not the 389.00 list price.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-09
  statement: Three or more Grade C returns on one SKU in a week are escalated to Dmitri Okafor on the same day.
  confidence: high
  affects: []
- id: sop-sop-returns-receiving-restock-grading-10
  statement: 'Weekly grade counts are sent to Dmitri every Monday, with the first full week reporting 41 units: 26 Grade A, 9 Grade B, and 6 Grade C.'
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-returns-receiving-restock-grading.md
provenance_hash: adaf3437f6bf43c6
---

# Sop Returns Receiving Restock Grading

## Summary

SOP: Returns receiving and restock grading Effective: 2025-06-23 Owner: Hank Morrow (HM) Applies to: warehouse crew Systems: ParcelPoint portal, Ledgerly Purpose Grade every returned unit that lands back on the dock before it goes anywhere near the shelf again, so a customer never quietly ends up with a broken chair mailed back out as new. Bad stock gets scrapped, not restocked.

## Content

SOP: Returns receiving and restock grading
Effective: 2025-06-23
Owner: Hank Morrow (HM)
Applies to: warehouse crew
Systems: ParcelPoint portal, Ledgerly

Purpose
Grade every returned unit that lands back on the dock before it goes anywhere near the shelf again, so a customer never quietly ends up with a broken chair mailed back out as new. Bad stock gets scrapped, not restocked.

Prerequisites
- Return authorized in Helprise, order number confirmed against the carton label
- Photo station clear and the grading log open before the first box comes off the cart

Steps
1. Unbox on the grading table, never the shelf. Check the SKU and the order number against Helprise before anything else.
2. Photograph the unit from four angles plus a close-up on any visible mark, scuff, or crack, before you touch the grade.
3. Grade A: original packaging present, no assembly marks, no sign of use. Goes straight back to sellable stock, same SKU, same bin.
4. Grade B: opened and reassembled, cosmetic wear only, function tested and clean. A wobble under 1/8 inch at the leg or a scuff under 1 inch across still counts as Grade B. Flag it "open box" in the Ledgerly inventory note and restock it, but never mix it into a carton meant for a new-unit ship.
5. Grade C: any functional defect, missing hardware, a crack, or a wobble past 1/8 inch or a scuff past 1 inch. Does not go back on a shelf. Ever.
6. Borderline call between B and C: set the unit aside with its photos and get a second look before scrapping it. Don't guess on a chair base or a motor box.
7. Scrap every confirmed Grade C at landed cost in Ledgerly, never list price. A Rowan task chair, graphite black scraps at 148.00 landed, not the 389.00 currently on the price list.
8. Log the grade, photo reference, and SKU count in the ParcelPoint portal same day, before the next cart of returns comes off the truck.

Escalation
Three or more Grade C returns on one SKU inside a week goes to Dmitri Okafor same day. Could be a vendor defect pattern, not bad luck landing on three separate customers.

Close-out
Weekly grade count goes to Dmitri every Monday. First full week under this log ran 41 units total: 26 Grade A, 9 Grade B, 6 Grade C.

References
- SKU unit costs per the SKU table: LS-CHR-001-BLK at 148.00, LS-DSK-001-48 at 212.00
- Inbound receiving inspection SOP, effective 2024-02-12, for photo standards at first receipt
