---
domain: operations
type: source
title: Process Showroom Demo Unit Rotation
tags:
- process
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: process
key_claims:
- id: process-process-showroom-demo-unit-rotation-01
  statement: The showroom demo unit rotation is effective on 2025-07-01.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-02
  statement: Hank Morrow owns the showroom demo unit rotation process.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-03
  statement: The showroom demo unit rotation involves the warehouse, sales, and finance teams.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-04
  statement: The process triggers on the first business day of each fiscal quarter.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-05
  statement: Also, the process triggers when any demo unit hits 90 days on the floor regardless of grade.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-06
  statement: Grade A units remain on the floor until the next swap unless damaged.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-07
  statement: Grade B units are sold at 30% off the current list price.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-08
  statement: Grade C units are priced at landed cost plus 20% and do not return to the floor.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-09
  statement: Unsold units at 21 days receive an additional 10 points off their price.
  confidence: high
  affects: []
- id: process-process-showroom-demo-unit-rotation-10
  statement: Unsold units at 45 days are moved to parts shelf at landed cost, with no further markdowns.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/process/process-showroom-demo-unit-rotation.md
provenance_hash: 9f97a5afafa097fa
---

# Process Showroom Demo Unit Rotation

## Summary

Process: Showroom demo unit rotation Effective: 2025-07-01 Owner: Hank Morrow Teams: warehouse, sales, finance Trigger: first business day of each fiscal quarter, or any demo unit hitting 90 days on the floor regardless of grade Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | floor inspect | HM | showroom floor | every demo unit graded A, B, or C same day | | 2 | pull | HM | warehouse | B/C units pulled; fresh stock staged before doors open | | 3 | price | AP | Ledgerly | open-box price set per grade, logged against landed cost | | 4 | list | SG | Shopstack | listing live same day, grade and price both stated | | 5 | sell or clear | front desk / PR | Shopstack, Helprise | sold at listed price, or marked down past 21 days unsold | | 6 | close | AP | Ledgerly | inventory adjusted, depreciation logged, entry closed | Rules - Grading uses the A/B/C scale warehouse already runs on returns (effective 2025-06-23): A is floor wear only, B is light functional wear that tests clean, C is a real functional defect. A borderline B/C call waits for Hank's second look.

## Content

Process: Showroom demo unit rotation
Effective: 2025-07-01
Owner: Hank Morrow   Teams: warehouse, sales, finance
Trigger: first business day of each fiscal quarter, or any demo unit hitting 90 days on the floor regardless of grade

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | floor inspect | HM | showroom floor | every demo unit graded A, B, or C same day |
| 2 | pull | HM | warehouse | B/C units pulled; fresh stock staged before doors open |
| 3 | price | AP | Ledgerly | open-box price set per grade, logged against landed cost |
| 4 | list | SG | Shopstack | listing live same day, grade and price both stated |
| 5 | sell or clear | front desk / PR | Shopstack, Helprise | sold at listed price, or marked down past 21 days unsold |
| 6 | close | AP | Ledgerly | inventory adjusted, depreciation logged, entry closed |

Rules
- Grading uses the A/B/C scale warehouse already runs on returns (effective 2025-06-23): A is floor wear only, B is light functional wear that tests clean, C is a real functional defect. A borderline B/C call waits for Hank's second look.
- Grade A stays put until the next swap unless a customer damages it in between.
- Grade B: 30% off current list. Grade C: landed cost plus 20%, and it never returns to the floor, only to clearance or scrap.
- Unsold at 21 days: another 10 points off. Unsold at 45 days: parts shelf at landed cost, no further markdowns.
- Open-box sales carry the standard 30-day refund window like any order (effective 2024-01-15) and the 10% restocking fee on opened, non-defective returns still applies, since the open-box price already reflects condition rather than acting as a return allowance.

Example run (Q3 2025 swap, 2025-07-01)
Hank inspects eight units: two Birch desks, three Rowan chairs, one Heron dual arm, one Vireo lamp, one Wren stand. The 60-inch desk and the arm come back A. The 72-inch desk grades B, scuffed motor column skirt. One black chair grades C, recline tension bolt backed all the way out. The gray chair grades B on seat-pan wear, the lamp B on a rubbed base clamp.

Four units pull that morning. Ana prices the desk at 524.30 (749.00 less 30%), the gray chair at 272.30, the lamp at 104.30, and holds the black chair rather than pricing it to scrap, since a tension-bolt retorque is a five-minute fix. Hank retorques it clean and re-grades it B; Ana reprices it at 272.30 to match.

Sofia lists the three ready units same day. The gray chair sells 2025-07-08 to a walk-in, Beatrix Mwangi, order #LS64300, her first purchase with us. The desk sells next week. The lamp is still sitting at day 21, drops to 89.40, and moves in week five. Ana closes the log 2025-08-12 once all four units clear.
