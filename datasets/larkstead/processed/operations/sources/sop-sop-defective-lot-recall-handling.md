---
domain: operations
type: source
title: Sop Defective Lot Recall Handling
tags:
- call-transcript
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: call-transcript
key_claims:
- id: sop-sop-defective-lot-recall-handling-01
  statement: SOP for defective production lot recall handling is effective from 2025-08-08.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-02
  statement: Dmitri Okafor owns the SOP for defective production lot recall handling.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-03
  statement: The SOP applies to warehouse crew, operations, and support agents.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-04
  statement: The purpose is to contain and cull production lots with confirmed defect patterns.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-05
  statement: QA must confirm a defect pattern against a specific lot before culling.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-06
  statement: The warehouse lead must have the cull criterion for the defect before handling the cartons.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-07
  statement: The full lot must be quarantined at ParcelPoint within 24 hours of QA confirmation.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-08
  statement: The entire remaining count of a defective lot must be moved off the pick shelf.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-09
  statement: The warehouse lead must document the cull criterion before starting the cull.
  confidence: high
  affects: []
- id: sop-sop-defective-lot-recall-handling-10
  statement: 'For edge-stitch delamination, Hank Morrow''s criterion is: edge-stitch gap over 1/16 inch or edge lifting more than 1/4 inch off a flat surface.'
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-defective-lot-recall-handling.md
provenance_hash: 637e2d7575d2b006
---

# Sop Defective Lot Recall Handling

## Summary

SOP: Defective production lot recall handling Effective: 2025-08-08 Owner: Dmitri Okafor (DO) Applies to: warehouse crew, operations, [[support]] agents Systems: ParcelPoint portal, Helprise, Ledgerly Purpose Contain and cull any production lot with a confirmed defect pattern once QA has flagged it, and keep replacement shipments clean of remaining defective stock. Written during the LOT-2025-14 Alder mat recall, applies to any future lot going forward.

## Content

SOP: Defective production lot recall handling
Effective: 2025-08-08
Owner: Dmitri Okafor (DO)
Applies to: warehouse crew, operations, support agents
Systems: ParcelPoint portal, Helprise, Ledgerly

Purpose
Contain and cull any production lot with a confirmed defect pattern once
QA has flagged it, and keep replacement shipments clean of remaining
defective stock. Written during the LOT-2025-14 Alder mat recall, applies
to any future lot going forward.

Prerequisites
- QA has confirmed a defect pattern against a specific lot number, not
  just a scattered complaint rate
- warehouse lead has the cull criterion for the specific defect in hand
  before touching the cartons

Steps
1. Quarantine the full lot at ParcelPoint within 24 hours of QA
   confirmation. Move the entire remaining count off the pick shelf, not
   just the units flagged so far.
2. Warehouse lead sets the cull criterion for the specific failure mode
   and documents it before the cull starts. For edge-stitch delamination,
   Hank Morrow's criterion is: edge-stitch gap over 1/16 inch, or the
   edge lifting more than 1/4 inch off a flat surface at rest. Either
   condition alone fails the unit.
3. Cull the full quarantined count against that criterion, not a sample.
   If a sample pull already ran ahead of the full cull, note both figures
   separately, don't merge them.
4. Photograph and count every culled unit before scrapping. If a unit is
   borderline against the criterion, photograph it and hold it aside for
   a second opinion rather than guessing.
5. Scrap culled units and write them off in Ledgerly at landed cost, not
   list price. Landed cost includes freight and receiving per unit.
6. Release only the cleared portion of the lot for replacement shipments.
   If cleared stock has not been released yet, hold pending tickets
   rather than shipping from unquarantined cartons.
7. Tag every Helprise ticket tied to the lot with its lot number so
   volume is trackable in one place. Retag any ticket handled before the
   pattern was confirmed.

Escalation
Any lot where the sample cull rate comes back above 5% goes to Dmitri
Okafor same day, regardless of whether the full cull is finished, so a
vendor claim can start moving in parallel with the physical cull.

Close-out
Defect counts, culled units, and cleared units go to Dmitri Okafor every
Friday for as long as the lot is under quarantine or a vendor claim is
open against it. Final tally closes out when the cull, the claim, and any
customer refunds or replacements are all recorded.

References
- LOT-2025-14 bug report, filed 2025-08-04
- Restocking fee policy: opened, non-defective returns only, effective
  2024-01-15; this SOP's culled and field-failed units are defective and
  never carry that fee
