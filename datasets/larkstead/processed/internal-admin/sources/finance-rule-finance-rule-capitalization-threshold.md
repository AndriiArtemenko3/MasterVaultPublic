---
domain: internal-admin
type: source
title: Finance Rule Capitalization Threshold
tags:
- other
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: other
key_claims:
- id: finance-rule-finance-rule-capitalization-threshold-01
  statement: Any asset purchased at or over 2500.00 capitalizes on the balance sheet rather than expensing immediately.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-02
  statement: Equipment capitalized under this rule depreciates on a 3-year straight line, 36 equal monthly entries, no salvage value assumed.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-03
  statement: Anything under 2500.00 expenses in full in the month it's bought, regardless of expected lifespan.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-04
  statement: Software subscriptions and SaaS tool fees never capitalize under this rule regardless of dollar amount.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-05
  statement: Larkstead doesn't own the underlying asset for software subscriptions and SaaS fees.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-06
  statement: Scope covers equipment, vehicles, and fixtures bought for internal use, such as showroom displays and company vehicles.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-07
  statement: Excludes inventory, which follows separate cost-of-goods accounting.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-08
  statement: Excludes leasehold improvements to the Portland office and showroom as treated on their own schedule.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-09
  statement: If a purchase order bundles several items each under 2500.00, they don't aggregate into one capitalized asset.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-capitalization-threshold-10
  statement: Ana confirms the exact figure against the vendor invoice before opening a fixed-asset entry in Ledgerly.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-capitalization-threshold.md
provenance_hash: c18e6eb5f48c343b
---

# Finance Rule Capitalization Threshold

## Summary

Finance rule: Capitalization threshold Effective: 2025-10-01 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule Any asset purchased at or over 2500.00 capitalizes on the balance sheet rather than expensing immediately. Equipment capitalized under this rule depreciates on a 3-year straight line, 36 equal monthly entries, no salvage value assumed.

## Content

Finance rule: Capitalization threshold
Effective: 2025-10-01
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
Any asset purchased at or over 2500.00 capitalizes on the balance sheet rather than expensing immediately. Equipment capitalized under this rule depreciates on a 3-year straight line, 36 equal monthly entries, no salvage value assumed. Anything under 2500.00 expenses in full in the month it's bought, no matter how long it's expected to last. Software subscriptions and SaaS tool fees never capitalize under this rule regardless of dollar amount, since Larkstead doesn't own the underlying asset.

Scope
Covers equipment, vehicles, and fixtures bought for internal use: showroom displays, company vehicles, computers, and similar. Excludes inventory, meaning SKUs held for resale, which follows separate cost-of-goods accounting, and excludes leasehold improvements to the Portland office and showroom, which the Lovejoy lease terms treat on their own schedule. If a single purchase order bundles several items each under 2500.00, they don't aggregate into one capitalized asset just because the PO total crosses the line; each line item is judged on its own price.

Procedure
Whoever requests the purchase flags Ana before ordering if the price looks close to the threshold. Ana confirms the exact figure against the vendor invoice, opens a fixed-asset entry in Ledgerly with a start-of-depreciation date matching the month the asset goes into service, and files the invoice against that entry. If an asset's actual price comes in under 2500.00 after a discount or credit that wasn't known at order time, she reverses the capitalization and expenses it instead.

Worked example
Larkstead buys a used sales vehicle for Tom's regional B2B visits outside the Portland metro, 14400.00, in service 2025-10-06. It's over the threshold. It capitalizes. Monthly depreciation: 14400.00 divided by 36 months, exactly 400.00 a month, starting October 2025 and running through September 2028. No rounding is needed here since the math lands even, but when it doesn't, the last month absorbs whatever cent or two is left over.

Change log
- 2025-10-01: first version.
