---
domain: internal-admin
type: source
title: Finance Rule Vendor Payment Run
tags:
- invoice
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: invoice
key_claims:
- id: finance-rule-finance-rule-vendor-payment-run-01
  statement: The Vendor payment run rule is effective from 2024-06-07.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-02
  statement: The rule is owned by Ana Petrova.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-03
  statement: The rule is approved by Mara Voss.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-04
  statement: Net-30 and Net-45 invoices clear the intake match and pay out in the nearest Friday batch.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-05
  statement: If a due date lands on a Friday, the invoice pays in that batch.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-06
  statement: If the nearest Friday is a bank holiday, the batch moves to the prior business day.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-07
  statement: Early payment occurs if a vendor offers a discount of 2% or steeper.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-08
  statement: Invoices under 2% wait for their regular Friday payment.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-09
  statement: The rule covers every vendor invoice on the standing vendor list after clearing the AP intake match.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-vendor-payment-run-10
  statement: Contractor invoices follow the same Friday cadence after clearing the AP intake match.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-vendor-payment-run.md
provenance_hash: e696c52c3f1d1d65
---

# Finance Rule Vendor Payment Run

## Summary

Finance rule: Vendor payment run scheduling Effective: 2024-06-07 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule Net-30 and Net-45 invoices that have cleared the intake match pay out in the Friday batch closest to, but never after, their due date. If a due date lands on a Friday, that's the batch it pays in.

## Content

Finance rule: Vendor payment run scheduling
Effective: 2024-06-07
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
Net-30 and Net-45 invoices that have cleared the intake match pay out in the Friday batch closest to, but never after, their due date. If a due date lands on a Friday, that's the batch it pays in. If the nearest Friday is a bank holiday, the batch moves to the prior business day rather than sliding later. Early payment outside the normal batch only happens if a vendor offers a discount of 2% or steeper for paying ahead of terms; anything under 2% waits for its regular Friday like everything else.

Scope
Covers every vendor invoice on the standing vendor list once it has cleared the AP intake match. Contractor invoices under a statement of work follow the same Friday cadence. It does not cover recurring standing charges that draft automatically, like the Lovejoy Property Partners lease payment, since those aren't part of a discretionary run.

Procedure
Ana pulls the list of invoices due in the coming week every Thursday afternoon and builds the Friday batch against it. A vendor offering an early-pay discount needs to put the terms in writing, email is fine, before Ana will pull the invoice forward. If the discount clears the 2% floor, she schedules the early payment in that week's batch instead of the invoice's normal due-date batch and logs the dollar amount saved next to the invoice in Ledgerly. Under 2%, she declines and the invoice pays on its usual schedule; no exceptions, since a discount that thin doesn't beat the float we'd otherwise keep.

Worked example
Invoice INV-OSM-2024-755, 9920.00 to Ostrava Metalworks on standard Net-45 terms, would normally pay in the Friday batch nearest its 45-day due date. Ostrava's controller emails offering 2.5% off for payment within 10 days instead. That clears the floor. Ana schedules it into the next Friday batch: discount 248.00, net payment 9672.00, logged against the invoice the same day.

Change log
- 2024-06-07: first written version.
