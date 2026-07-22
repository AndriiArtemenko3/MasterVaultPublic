---
domain: sales-crm
type: source
title: Sl3 Onepager B2B Pricing
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: policy-sl3-onepager-b2b-pricing-01
  statement: The B2B volume pricing tier allows a 15% discount for orders of 25 or more units.
  confidence: high
  affects:
  - pricing-tier
- id: policy-sl3-onepager-b2b-pricing-02
  statement: Units count across every line on the order, not per product.
  confidence: high
  affects: []
- id: policy-sl3-onepager-b2b-pricing-03
  statement: A bundle line counts as one unit, the same as a single desk.
  confidence: high
  affects: []
- id: policy-sl3-onepager-b2b-pricing-04
  statement: Mixed orders totaling 25 units clear the discount tier.
  confidence: high
  affects:
  - pricing-tier
- id: policy-sl3-onepager-b2b-pricing-05
  statement: Reps cannot discount past the 15% tier on their own authority.
  confidence: high
  affects:
  - discount-policy
- id: policy-sl3-onepager-b2b-pricing-06
  statement: Discounts over 15% require approval per the expense-policy thresholds.
  confidence: high
  affects: []
- id: policy-sl3-onepager-b2b-pricing-07
  statement: Quotes are valid for 30 days from the proposal date.
  confidence: high
  affects:
  - quote-validity
- id: policy-sl3-onepager-b2b-pricing-08
  statement: This is the first version of the B2B volume pricing policy, effective 2024-06-01.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/sales-crm/policy/sl3-onepager-b2b-pricing.md
provenance_hash: 6b9f250cdeef9b69
---

# Sl3 Onepager B2B Pricing

## Summary

Policy: B2B volume pricing Doc: policy-b2b-pricing-onepager-v1 Effective: 2024-06-01 Supersedes: none Owner: Tom Aldridge Approved by: Mara Voss, 2024-05-29 Applies to: B2B orders taken through the Pipewell pipeline ## 1. The tier 40 seats, 8 seats, doesn't matter how it's split across desks and chairs and arms: 25 or more units on one order clears 15% off list.

## Content

Policy: B2B volume pricing
Doc: policy-b2b-pricing-onepager-v1
Effective: 2024-06-01
Supersedes: none
Owner: Tom Aldridge
Approved by: Mara Voss, 2024-05-29
Applies to: B2B orders taken through the Pipewell pipeline

## 1. The tier

40 seats, 8 seats, doesn't matter how it's split across desks and chairs and arms: 25 or more units on one order clears 15% off list. That's the whole tier, one number, no ladder above it.

## 2. Counting units

Units count across every line on the order, not per product. A bundle line (LS-BDL-001, LS-BDL-002, LS-BDL-003) counts as one unit, same as a single desk. Mixed orders add up: 10 desks plus 10 chairs plus 5 arms is 25 units and clears the tier even with three different SKUs on the sheet.

## 3. Going past 15%

Reps do not have authority to discount past the tier on their own. Anything past 15% routes through the expense-policy dollar thresholds like any other spend: self-approve under 250.00, manager approval 250.00 to 999.99, and 1,000.00 or above needs Mara's sign-off before the number goes to the customer, not after.

## 4. Quote validity

Every quote holds for 30 days from the date on the proposal. After that the rep re-runs the numbers; prices move.

## Change note

First version of this sheet. No prior tier existed before 2024-06-01.
