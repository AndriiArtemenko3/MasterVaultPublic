---
domain: operations
type: source
title: Process Month End Close
tags:
- process
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: process
key_claims:
- id: process-process-month-end-close-01
  statement: The month-end close process is effective from 2024-03-01.
  confidence: high
  affects: []
- id: process-process-month-end-close-02
  statement: The owner of the month-end close process is Ana Petrova.
  confidence: high
  affects: []
- id: process-process-month-end-close-03
  statement: The month-end close process is triggered on the last calendar day of the month.
  confidence: high
  affects: []
- id: process-process-month-end-close-04
  statement: Every vendor invoice and Shopstack sales batch dated within the month must be posted by 5pm the first business day after month-end.
  confidence: high
  affects:
  - invoice-cutoff
- id: process-process-month-end-close-05
  statement: The accrual list includes open purchase orders with goods received but not yet billed, plus recurring charges.
  confidence: high
  affects:
  - accrual-list
- id: process-process-month-end-close-06
  statement: The bank statement cash balance must match the ledger cash balance to the cent.
  confidence: high
  affects:
  - reconciliation
- id: process-process-month-end-close-07
  statement: No postings are allowed without a dated adjusting entry once the month is locked.
  confidence: high
  affects:
  - lock
- id: process-process-month-end-close-08
  statement: A one-page summary is sent to Mara for approval during the sign-off stage.
  confidence: high
  affects:
  - sign-off
- id: process-process-month-end-close-09
  statement: Self-approval is allowed under 250.00, while manager approval is required for 250.00 to 999.99.
  confidence: high
  affects:
  - self-approve
- id: process-process-month-end-close-10
  statement: A bank-to-ledger gap over 0.01 gets traced the same day it turns up.
  confidence: high
  affects:
  - zero-variance-rule
provenance: datasets/larkstead/raw/operations/process/process-month-end-close.md
provenance_hash: b72390d43bad5fd3
---

# Process Month End Close

## Summary

Process: Month-end close Effective: 2024-03-01 Owner: Ana Petrova Teams: finance, operations Trigger: last calendar day of the month Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | invoice cutoff | AP | Ledgerly | every vendor invoice and Shopstack sales batch dated within the month posted by 5pm the first business day after month-end | | 2 | accrual list | AP | Ledgerly | open purchase orders with goods received but not yet billed, plus recurring charges not yet invoiced, listed with dollar amounts | | 3 | reconciliation | AP | Ledgerly | bank statement cash balance matches the ledger cash balance to the cent | | 4 | lock | AP | Ledgerly | prior month locked; no postings without a dated adjusting entry, initialed AP | | 5 | sign-off | AP/MV | email | one-page summary sent to Mara; any variance over 50.00 called out by line | Rules - Self-approve under 250.00, manager band 250.00 to 999.99, CEO sign-off from 1000.00, effective 2024-01-15. Any accrual adjustment that crosses a band needs the matching signature before the lock goes in.

## Content

Process: Month-end close
Effective: 2024-03-01
Owner: Ana Petrova   Teams: finance, operations
Trigger: last calendar day of the month

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | invoice cutoff | AP | Ledgerly | every vendor invoice and Shopstack sales batch dated within the month posted by 5pm the first business day after month-end |
| 2 | accrual list | AP | Ledgerly | open purchase orders with goods received but not yet billed, plus recurring charges not yet invoiced, listed with dollar amounts |
| 3 | reconciliation | AP | Ledgerly | bank statement cash balance matches the ledger cash balance to the cent |
| 4 | lock | AP | Ledgerly | prior month locked; no postings without a dated adjusting entry, initialed AP |
| 5 | sign-off | AP/MV | email | one-page summary sent to Mara; any variance over 50.00 called out by line |

Rules
- Self-approve under 250.00, manager band 250.00 to 999.99, CEO sign-off from 1000.00, effective 2024-01-15. Any accrual adjustment that crosses a band needs the matching signature before the lock goes in.
- ParcelPoint kitting runs 6.00 per kit. Kits assembled but not yet on a ParcelPoint invoice go on the accrual list at that rate, not estimated.
- Zero-variance rule: a bank-to-ledger gap over 0.01 gets traced the same day it turns up. It never carries into the next month as a plug number, and Ana has said so more than once in the finance thread.

Example run (March 2024 close)
2024-04-01, cutoff run: three late vendor invoices posted, INV-OSM-2024-036 from Ostrava Metalworks and two from GreenCrate Packaging, plus the full Shopstack batch through 2024-03-31. If a late invoice lands after the 5pm cutoff, it holds for April instead of being forced into March.
2024-04-02, accrual list drafted: 358 bundle kits assembled at ParcelPoint in March, not yet on an invoice, accrued at 2,148.00.
2024-04-03, reconciliation: ledger ran 0.15 short against the bank statement. Traced to a Canadian order where tax was correctly left uncollected but the parcel surcharge posted a cent light on rounding. Corrected same day, no plug.
2024-04-04: month locked, no further postings without a dated adjusting entry.
2024-04-05: summary emailed to Mara. Nothing over 50.00 to flag this month.

Ledgerly closed on March, opening balances carried to April clean.
