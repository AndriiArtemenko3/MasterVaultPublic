---
domain: operations
type: source
title: Sl5 Cutover Checklist
tags:
- checklist
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: checklist
key_claims:
- id: checklist-sl5-cutover-checklist-01
  statement: The version effective date is 2026-01-08.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-02
  statement: Owner of the document is Hank Morrow (HM).
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-03
  statement: The run is for full outbound cutover, Portland backroom to ParcelPoint.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-04
  statement: The run date is 2026-01-08.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-05
  statement: Final inventory transfer was loaded on the Thursday Cascadia load, 2026-01-08.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-06
  statement: Cascadia freight (VEN-06) full pallet count is confirmed at dock.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-07
  statement: Physical count shows LS-ACC-001 on hand 62 units, counted twice.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-08
  statement: Portland backroom's last pick is on 2026-01-09 at 17:00.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-09
  statement: Any open order not picked by 2026-01-09 transfers to the ParcelPoint queue on 2026-01-12.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-10
  statement: Shopstack order feed flips to ParcelPoint on 2026-01-12 at 06:00 PT.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-11
  statement: Ray confirms the webhook switch that same morning.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-12
  statement: Bundle SKU manual check occurs each morning of cutover week, starting 01-12.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-13
  statement: The rollback plan is on standby.
  confidence: medium
  affects: []
- id: checklist-sl5-cutover-checklist-14
  statement: If ParcelPoint order acknowledgment rate drops below 95%, revert the Shopstack webhook to the backroom queue within 4 hours.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-15
  statement: Dmitri and Hank both hold the revert switch.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-16
  statement: Backroom staff are notified that shipping stops after the 01-09 last pick.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-17
  statement: No more parcel labels are printed in the backroom after 01-09.
  confidence: high
  affects: []
- id: checklist-sl5-cutover-checklist-18
  statement: Carrier re-manifest check is on any order labeled in the backroom before the 01-09 cutoff.
  confidence: medium
  affects: []
- id: checklist-sl5-cutover-checklist-19
  statement: The carrier re-manifest check is not yet started and depends on item 3.
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/operations/checklist/sl5-cutover-checklist.md
provenance_hash: 5242b176d1cd2628
---

# Sl5 Cutover Checklist

## Summary

Checklist: parcelpoint cutover, backroom shutdown Version effective: 2026-01-08 Owner: Hank Morrow (HM) Run for: full outbound cutover, portland backroom to parcelpoint Run date: 2026-01-08 Completed by: HM 1. [x] final inventory transfer loaded on the thursday cascadia load, 2026-01-08.

## Content

Checklist: parcelpoint cutover, backroom shutdown
Version effective: 2026-01-08
Owner: Hank Morrow (HM)
Run for: full outbound cutover, portland backroom to parcelpoint
Run date: 2026-01-08   Completed by: HM

1. [x] final inventory transfer loaded on the thursday cascadia load, 2026-01-08. cascadia freight (VEN-06), full pallet count confirmed at dock.
2. [x] physical count taken before load-out. LS-ACC-001 on hand 62 units, counted twice.
3. [ ] portland backroom last pick 2026-01-09 at 17:00. any open order not picked by then transfers to the parcelpoint queue on 2026-01-12. not yet run, scheduled for tomorrow.
4. [ ] shopstack order feed flips to parcelpoint 2026-01-12 at 06:00 pt. ray confirms the webhook switch same morning.
5. [ ] bundle sku manual check each morning of cutover week, per the migration project risk list. not started, starts 01-12.
6. [ ] rollback plan on standby: if parcelpoint order acknowledgment rate drops below 95% at any point in cutover week, revert the shopstack webhook to the backroom queue within 4 hours. dmitri and hank both hold the revert switch.
7. [x] backroom staff notified shipping stops after the 01-09 last pick, no more parcel labels printed there after that.
8. [ ] carrier re-manifest check on any order labeled in the backroom before the 01-09 cutoff but not yet scanned by 01-13. not started, depends on item 3.

Notes
item 3 through 6 and item 8 depend on dates after this run, checked off as they close. rollback plan is a standby item, not something we expect to use, but it stays on the list until cutover week is done. dmitri has the same copy, we're both watching the acknowledgment rate starting 01-12.

Sign-off: HM, 2026-01-08
