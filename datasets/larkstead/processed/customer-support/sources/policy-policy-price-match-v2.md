---
domain: customer-support
type: source
title: Policy Price Match V2
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: policy-policy-price-match-v2-01
  statement: Larkstead will match the list price of an identical-spec item sold by a US-based seller within 14 days of purchase.
  confidence: high
  affects:
  - price-matching
- id: policy-policy-price-match-v2-02
  statement: The match applies to list price only and does not stack with any other discount.
  confidence: high
  affects:
  - price-matching
- id: policy-policy-price-match-v2-03
  statement: The match is never applied retroactively past the 14-day window.
  confidence: high
  affects:
  - price-matching
- id: policy-policy-price-match-v2-04
  statement: An identical spec item qualifies if it matches on core materials, motor class, warranty term, and a published replacement-parts catalog.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-05
  statement: An import desk priced 15% below the Birch standing desk does not qualify if it has no parts catalog and no stated motor class.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-06
  statement: A lower price with no matching service commitment is not treated as the same product under this policy.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-07
  statement: This policy excludes clearance and discontinued-item pricing.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-08
  statement: Every bundle SKU is excluded from this policy.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-09
  statement: A Fledgling or Canopy bundle price is never eligible for a match request.
  confidence: high
  affects: []
- id: policy-policy-price-match-v2-10
  statement: Customers send a link or dated screenshot of the competing listing to support@larkstead.example.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/policy/policy-price-match-v2.md
provenance_hash: 086f89ff63be6838
---

# Policy Price Match V2

## Summary

Policy: Price matching Doc: policy-price-match-v2 Effective: 2025-05-19 Supersedes: policy-price-match-v1 (effective 2024-01-15) Owner: Priya Raman Approved by: Mara Voss, 2025-05-14 Applies to: all customer orders ## 1. Policy statement Larkstead will match the list price of an identical-spec item sold by a US-based seller, provided the customer requests the match within 14 days of their own purchase date.

## Content

Policy: Price matching
Doc: policy-price-match-v2
Effective: 2025-05-19
Supersedes: policy-price-match-v1 (effective 2024-01-15)
Owner: Priya Raman
Approved by: Mara Voss, 2025-05-14
Applies to: all customer orders

## 1. Policy statement

Larkstead will match the list price of an identical-spec item sold by a US-based seller, provided the customer requests the match within 14 days of their own purchase date. The match applies to list price only. It does not stack with any other discount, and it is never applied retroactively past the 14-day window.

## 2. What counts as identical spec

A competing item qualifies only if it matches Larkstead's item on four points: the same core materials, the same motor class on any powered desk, the same warranty term, and a published replacement-parts catalog the customer can point to. An import desk priced 15% below the Birch standing desk does not qualify if it ships with no parts catalog and no stated motor class. A lower price with no matching service commitment behind it is not the same product, and this policy does not treat it as one.

## 3. Exclusions

This policy excludes clearance and discontinued-item pricing wherever it appears, and it excludes every bundle SKU. A Fledgling or Canopy bundle price is never eligible for a match request, even where a competitor bundles similar items at a lower combined price.

## 4. How a match request is handled

The customer sends a link or a dated screenshot of the competing listing to support@larkstead.example. An agent checks the four spec points above before approving anything, and confirms the seller is US-based. Approved matches are applied as an order-level adjustment. Nothing changes on the storefront price shown to other customers.

## Change note

v1 (effective 2024-01-15) set no price matching under any circumstance. v2 introduces conditional matching on identical-spec items from US-based sellers within 14 days of purchase, excluding clearance and bundle pricing.
