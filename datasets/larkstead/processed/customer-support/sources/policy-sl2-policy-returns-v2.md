---
domain: customer-support
type: source
title: Sl2 Policy Returns V2
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: policy-sl2-policy-returns-v2-01
  statement: Customers may return any item within 45 days of the delivery date.
  confidence: high
  affects:
  - return-policy
- id: policy-sl2-policy-returns-v2-02
  statement: The delivery date is the carrier-confirmed date recorded in ParcelPoint.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v2-03
  statement: The 45-day window has been running since 2025-11-03 as a holiday exception.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v2-04
  statement: As of 2026-01-12, the 45-day return window is permanent policy.
  confidence: high
  affects:
  - return-policy
- id: policy-sl2-policy-returns-v2-05
  statement: The 45-day return window applies to every order year-round.
  confidence: high
  affects:
  - return-policy
- id: policy-sl2-policy-returns-v2-06
  statement: The return window applies the same way across every product category.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v2-07
  statement: Unused items returned in their original packaging receive a full refund.
  confidence: high
  affects:
  - refund-policy
- id: policy-sl2-policy-returns-v2-08
  statement: Opened, non-defective returns carry a 10% restocking fee, deducted from the refund total.
  confidence: high
  affects:
  - refund-policy
- id: policy-sl2-policy-returns-v2-09
  statement: The restocking fee is waived on B2B orders of 10 or more units.
  confidence: high
  affects:
  - restocking-policy
- id: policy-sl2-policy-returns-v2-10
  statement: Defective items are refunded or replaced at no charge, and Larkstead covers return shipping.
  confidence: high
  affects:
  - defective-returns
provenance: datasets/larkstead/raw/customer-support/policy/sl2-policy-returns-v2.md
provenance_hash: d9ee838873469234
---

# Sl2 Policy Returns V2

## Summary

Policy: Returns and refunds Doc: policy-returns-v2 Effective: 2026-01-12 Supersedes: policy-returns-v1 (effective 2024-01-15) Owner: Priya Raman Approved by: Mara Voss, 2026-01-10 Applies to: all customer orders ## 1. Return window Customers may return any item within 45 days of the delivery date.

## Content

Policy: Returns and refunds
Doc: policy-returns-v2
Effective: 2026-01-12
Supersedes: policy-returns-v1 (effective 2024-01-15)
Owner: Priya Raman
Approved by: Mara Voss, 2026-01-10
Applies to: all customer orders

## 1. Return window

Customers may return any item within 45 days of the delivery date. The
delivery date is the carrier-confirmed date recorded in ParcelPoint.

This 45-day window has been running since 2025-11-03 as a holiday
exception. As of the effective date above, it is permanent and applies
to every order year-round, not just holiday-season deliveries. It
applies the same way across every product category, from small
accessories up through desks and bundles; there is no shorter window
for opened accessories and no longer window for large furniture.

## 2. Condition and restocking

Unused items returned in their original packaging receive a full
refund. Opened, non-defective returns carry a 10% restocking fee,
deducted from the refund total.

The restocking fee is waived on B2B orders of 10 or more units, per the
2025-06-02 restocking update, which remains in force and is unchanged
by this version.

## 3. Defective items

Defective items are refunded or replaced at no charge, and Larkstead
covers return shipping. Defect claims should reference the order number
and, where known, the production lot.

## 4. Starting a return

Return requests start by email to support@larkstead.example with the
order number. A support agent confirms the delivery date on file and
sends instructions for getting the item to the warehouse.

## 5. Refund timing

Refunds go to the original payment method within 5 business days of
warehouse receipt. Refunds do not post to store credit or to a
different card than the one used for the original purchase.

## Change note

Version 1, effective 2024-01-15, set a 30-day window. The window moved
to 45 days on 2025-11-03 under an internal holiday exception. This
version makes 45 days permanent policy. No other terms changed from v1.
