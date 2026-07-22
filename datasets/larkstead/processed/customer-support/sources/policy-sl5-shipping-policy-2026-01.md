---
domain: customer-support
type: source
title: Sl5 Shipping Policy 2026 01
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: policy-sl5-shipping-policy-2026-01-01
  statement: Orders of 99.00 or more in merchandise value ship free, effective 2026-01-15.
  confidence: high
  affects:
  - free-shipping-threshold
- id: policy-sl5-shipping-policy-2026-01-02
  statement: Orders placed before 2026-01-15 keep the prior 75.00 threshold.
  confidence: high
  affects:
  - free-shipping-threshold
- id: policy-sl5-shipping-policy-2026-01-03
  statement: The new threshold applies to orders placed on or after the effective date.
  confidence: high
  affects:
  - free-shipping-threshold
- id: policy-sl5-shipping-policy-2026-01-04
  statement: Free shipping waives light and medium item shipping rates only.
  confidence: high
  affects:
  - free-shipping-coverage
- id: policy-sl5-shipping-policy-2026-01-05
  statement: Free shipping does not apply to heavy items.
  confidence: high
  affects:
  - free-shipping-coverage
- id: policy-sl5-shipping-policy-2026-01-06
  statement: Heavy items always bill the applicable zone rate regardless of order total or free-shipping eligibility.
  confidence: high
  affects:
  - shipping-rates
- id: policy-sl5-shipping-policy-2026-01-07
  statement: Zone rates are unchanged by this update.
  confidence: high
  affects:
  - shipping-rates
- id: policy-sl5-shipping-policy-2026-01-08
  statement: Zone US-1 bills 5.95 for light items, 12.95 for medium, and 49.00 for heavy items when free shipping does not apply.
  confidence: high
  affects:
  - shipping-rates
- id: policy-sl5-shipping-policy-2026-01-09
  statement: Agents quote this policy document as the current rule until the help-center shipping FAQ is revised.
  confidence: high
  affects:
  - shipping-policy
- id: policy-sl5-shipping-policy-2026-01-10
  statement: The threshold rises from 75.00 to 99.00, effective 2026-01-15.
  confidence: high
  affects:
  - free-shipping-threshold
provenance: datasets/larkstead/raw/customer-support/policy/sl5-shipping-policy-2026-01.md
provenance_hash: d8c96e53ea6b7ba0
---

# Sl5 Shipping Policy 2026 01

## Summary

Policy: Shipping and free-shipping threshold Doc: policy-shipping-threshold-v1 Effective: 2026-01-15 Supersedes: none. The prior 75.00 threshold ran from 2024-03-01 as a pricing rule without a standalone policy document; this is the first standalone version.

## Content

Policy: Shipping and free-shipping threshold
Doc: policy-shipping-threshold-v1
Effective: 2026-01-15
Supersedes: none. The prior 75.00 threshold ran from 2024-03-01 as a pricing rule without a standalone policy document; this is the first standalone version.
Owner: Celeste Marin
Approved by: Mara Voss, 2026-01-14
Applies to: all customer orders

## 1. Free-shipping threshold

Orders of 99.00 or more in merchandise value ship free, effective 2026-01-15. Orders placed before 2026-01-15 keep the prior 75.00 threshold; the new threshold applies to orders placed on or after the effective date, not to the date an order happens to ship.

## 2. What free shipping covers

Free shipping waives light and medium item shipping rates only. It does not apply to heavy items. Heavy items always bill the applicable zone rate regardless of order total or free-shipping eligibility on the rest of the cart.

## 3. Zone rates

Zone rates are unchanged by this update. Zone US-1 (Oregon, Washington, Idaho, Nevada) bills 5.95 for light items, 12.95 for medium, and 49.00 for heavy items when free shipping does not apply or does not cover the item.

## 4. Rollout

The help-center shipping FAQ is flagged for update in the rollout task list attached to this change. Until that page is revised, agents quote this policy document as the current rule and note the FAQ's 75.00 figure as out of date for orders placed on or after 2026-01-15.

## Change note

Threshold rises from 75.00 to 99.00, effective 2026-01-15. No other terms changed: zone rates, the heavy-item exception, and the light/medium-only scope of free shipping all carry forward unchanged.
