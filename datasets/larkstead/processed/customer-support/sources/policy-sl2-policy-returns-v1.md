---
domain: customer-support
type: source
title: Sl2 Policy Returns V1
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: policy-sl2-policy-returns-v1-01
  statement: Customers may return any item within 30 days of the delivery date.
  confidence: high
  affects:
  - return-policy
- id: policy-sl2-policy-returns-v1-02
  statement: The delivery date is the carrier-confirmed date recorded in ParcelPoint.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-03
  statement: Unused items returned in their original packaging receive a full refund.
  confidence: high
  affects:
  - refund-policy
- id: policy-sl2-policy-returns-v1-04
  statement: Opened, non-defective returns carry a 10% restocking fee, deducted from the refund total.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-05
  statement: Defective items are exempt from the restocking fee in every case, opened or not.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-06
  statement: Items that arrive damaged, or that fail during normal use inside the return window, are refunded or replaced at no charge.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-07
  statement: Larkstead covers return shipping on defective items.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-08
  statement: Return requests start by email to support@larkstead.example with the order number.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-09
  statement: Refund goes to the original payment method within 5 business days.
  confidence: high
  affects: []
- id: policy-sl2-policy-returns-v1-10
  statement: Refunds do not post to store credit and do not move to a different card or account than the one used for the original purchase.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/policy/sl2-policy-returns-v1.md
provenance_hash: 9ab9d48a1f42a0bb
---

# Sl2 Policy Returns V1

## Summary

Policy: Returns and refunds Doc: policy-returns-v1 Effective: 2024-01-15 Supersedes: none Owner: Priya Raman Approved by: Mara Voss, 2024-01-10 Applies to: all customer orders ## 1. Return window Customers may return any item within 30 days of the delivery date.

## Content

Policy: Returns and refunds
Doc: policy-returns-v1
Effective: 2024-01-15
Supersedes: none
Owner: Priya Raman
Approved by: Mara Voss, 2024-01-10
Applies to: all customer orders

## 1. Return window

Customers may return any item within 30 days of the delivery date. The
delivery date is the carrier-confirmed date recorded in ParcelPoint, not
the date the order was placed and not the date it shipped. If a
customer contacts us before the delivery scan appears in ParcelPoint,
the agent should wait for the scan rather than estimate from the ship
date.

This window applies the same way across every product category we
carry, from the Wren stand up through the desk and bundle lines. There
is no shorter window for accessories and no longer window for large
furniture.

## 2. Condition and restocking

Unused items returned in their original packaging receive a full
refund. Opened, non-defective returns carry a 10% restocking fee,
deducted from the refund total before it is issued. Defective items are
exempt from the restocking fee in every case, opened or not.

## 3. Defective items

Items that arrive damaged, or that fail during normal use inside the
return window, are refunded or replaced at no charge. Larkstead covers
return shipping on these. Defect claims should reference the order
number so the warehouse can check the item in against the right record.

## 4. Starting a return

Return requests start by email to support@larkstead.example with the
order number. A support agent confirms the delivery date on file and
sends instructions for getting the item back to the warehouse. Do not
ship anything back before an agent has replied.

## 5. Refund timing

Once the warehouse receives and checks in a returned item, the refund
goes to the original payment method within 5 business days. Refunds do
not post to store credit and do not move to a different card or
account than the one used for the original purchase.

## Change note

This is the first written returns policy. No prior version exists to
supersede.
