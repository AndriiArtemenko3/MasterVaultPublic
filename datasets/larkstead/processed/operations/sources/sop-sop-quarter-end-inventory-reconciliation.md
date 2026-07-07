---
domain: operations
type: source
title: Sop Quarter End Inventory Reconciliation
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-quarter-end-inventory-reconciliation-01
  statement: The reconciliation is effective from 2026-04-06.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-02
  statement: The reconciliation process applies to Finance and warehouse liaison.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-03
  statement: The systems used for reconciliation are ParcelPoint portal and Ledgerly.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-04
  statement: Reconciliation takes place for every SKU at the close of each quarter.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-05
  statement: Both reports must be exported on the same calendar day, within an hour of each other if possible.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-06
  statement: If both sides show identical SKU counts, the reconciliation is marked closed.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-07
  statement: If the ParcelPoint's count is lower than Ledgerly's by 1 to 4 units, the SKU's return and damage log is pulled.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-08
  statement: A variance of 5 units or more requires pulling all receiving and shipping transactions for that SKU.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-09
  statement: The unresolved variance total in dollars must be totaled at unit cost.
  confidence: high
  affects: []
- id: sop-sop-quarter-end-inventory-reconciliation-10
  statement: Any SKU remaining unresolved after 5 business days goes to Dmitri.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-quarter-end-inventory-reconciliation.md
provenance_hash: 1d8aaa5fe891b11c
---

# Sop Quarter End Inventory Reconciliation

## Summary

SOP: Quarter-end inventory reconciliation, ParcelPoint vs. Ledgerly Effective: 2026-04-06 Owner: Ana Petrova (AP) Applies to: Finance, warehouse liaison Systems: ParcelPoint portal, Ledgerly Purpose Reconcile the ParcelPoint on-hand unit count against the Ledgerly book count for every SKU at the close of each quarter, and work any variance down to a resolved cause before the quarter's numbers get reported.

## Content

SOP: Quarter-end inventory reconciliation, ParcelPoint vs. Ledgerly
Effective: 2026-04-06
Owner: Ana Petrova (AP)
Applies to: Finance, warehouse liaison
Systems: ParcelPoint portal, Ledgerly

Purpose
Reconcile the ParcelPoint on-hand unit count against the Ledgerly book count
for every SKU at the close of each quarter, and work any variance down to a
resolved cause before the quarter's numbers get reported.

Prerequisites
- ParcelPoint's quarter-end snapshot report, pulled after the last outbound
  scan of the last day
- Ledgerly's inventory valuation report for the same cutoff date
- The prior quarter's signed-off reconciliation, for reference

Steps
1. Export both reports on the same calendar day, within an hour of each
   other if possible. A gap of more than a day between pulls invalidates the
   comparison, ParcelPoint's count moves too fast for a stale snapshot to
   mean anything.
2. Match SKU by SKU. If both sides show the identical count, mark it closed,
   no further work.
3. If ParcelPoint's count is lower than Ledgerly's by 1 to 4 units, pull that
   SKU's return and damage log for the quarter. Most of these close on a
   missed return-processing entry.
4. If the variance is 5 units or more on any single SKU, or the SKU is a
   bundle, LS-BDL-001, LS-BDL-002, or LS-BDL-003, go to step 5. Do not
   resolve a bundle variance alone, the component draw-down has to match
   too.
5. Pull every receiving and shipping transaction for that SKU across the
   quarter, both systems side by side. If the invoice trail accounts for the
   gap, cite the invoice number in the reconciliation note, for example
   INV-PPF-2026-666 pulled from a March receiving discrepancy. If it clears,
   the SKU closes; if it does not clear within 5 business days, it goes to
   Escalation.
6. Total the quarter's unresolved variance in dollars, at unit cost, not
   list price. Anything over 250.00 in unresolved total needs Dmitri's
   sign-off before the reconciliation closes.

Escalation
Any SKU still open after step 5's 5-business-day window goes to Dmitri, and
if the dollar value reaches 1000.00, to Mara, per the CEO approval
threshold. ParcelPoint disputes route through their account manager, cited
by ticket number if one exists.

Close-out
The signed reconciliation records every SKU's final variance, zero if closed
clean, and the cause code for anything that took manual work. Filed against
the quarter and dated, not just the SKU it started from.

References
- ParcelPoint Fulfillment (VEN-04), Net-30
- Expense policy: CEO approval from 1000.00, effective 2025-07-01
