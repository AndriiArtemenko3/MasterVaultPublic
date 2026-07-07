---
domain: operations
type: source
title: Bug Report Ledgerly Refund Export Drop
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-report-bug-report-ledgerly-refund-export-drop-01
  statement: Ledgerly's daily export drops the refund line whenever a refund and a same-day resale post under the same Shopstack order number.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-02
  statement: Nine of 240 refund transactions processed June through November 2024 shared an order number with a same-day resale.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-03
  statement: All nine dropped their refund line on export.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-04
  statement: 'Order #LS63714 is representative: a 389.00 refund on the Rowan chair posted 18 Nov, offset the same day by a 389.00 resale.'
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-05
  statement: The export showed only the resale.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-06
  statement: The other 231 refunds in the same window exported clean.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-07
  statement: The export logic isn't broken across the board, only its handling of a same-day pair.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-08
  statement: Every one of the nine needed a manual journal entry before December close would reconcile.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-09
  statement: Ledgerly's case, opened 06 Dec, asks whether the export can key on line-item timestamp instead of order number for same-day pairs.
  confidence: high
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-10
  statement: If they can't turn that around before January close, I'll keep flagging same-order exchanges by hand at each month end.
  confidence: medium
  affects: []
- id: bug-report-bug-report-ledgerly-refund-export-drop-11
  statement: I want that written up as the standing procedure, not a one-off.
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/operations/bug-report/bug-report-ledgerly-refund-export-drop.md
provenance_hash: 4389b98a6e957cb2
---

# Bug Report Ledgerly Refund Export Drop

## Summary

Bug report: ledgerly-export-drops-refund-line Date: 2024-12-06 Filed by: Ana Petrova (AP) Filed with: Ledgerly support (vendor case opened 2024-12-06) System under report: Ledgerly daily transaction export, accounting Related tickets: none -- surfaced internally during the November close, not customer-facing Summary Ledgerly's daily export drops the refund line whenever a refund and a same-day resale post under the same Shopstack order number, so that day's revenue reads high by the refunded amount until someone catches it by hand. Reproduction steps 1.

## Content

Bug report: ledgerly-export-drops-refund-line
Date: 2024-12-06
Filed by: Ana Petrova (AP)
Filed with: Ledgerly support (vendor case opened 2024-12-06)
System under report: Ledgerly daily transaction export, accounting
Related tickets: none -- surfaced internally during the November close, not customer-facing

Summary
Ledgerly's daily export drops the refund line whenever a refund and a same-day resale post under the same Shopstack order number, so that day's revenue reads high by the refunded amount until someone catches it by hand.

Reproduction steps
1. Process a refund against an existing order number in Shopstack, the kind support runs for an even exchange rather than opening a new order.
2. Ring the replacement item under that same order number the same calendar day.
3. Run the Ledgerly daily export for that date. The refund row is missing. Only the resale row shows, and net revenue is overstated by the refund amount.

Affected population
Nine of 240 refund transactions processed June through November 2024 shared an order number with a same-day resale, and all nine dropped their refund line on export. Order #LS63714 is representative: a 389.00 refund on the Rowan chair, graphite black, posted 18 Nov, offset the same day by a 389.00 resale in fog gray under the same order number. The export showed only the resale. I journaled the correction by hand on 22 Nov, once the mismatch turned up in reconciliation.

Evidence
The other 231 refunds in the same window, none sharing an order number with a same-day sale, exported clean. The export logic isn't broken across the board, only its handling of a same-day pair filed under one order number. Every one of the nine needed a manual journal entry before December close would reconcile.

Ask
Ledgerly's case, opened 06 Dec, asks whether the export can key on line-item timestamp instead of order number for same-day pairs. If they can't turn that around before January close, I'll keep flagging same-order exchanges by hand at each month end, and I want that written up as the standing procedure, not a one-off.
