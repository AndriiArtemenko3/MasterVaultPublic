---
domain: internal-admin
type: source
title: Finance Rule Sales Tax Collection
tags:
- log
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: log
key_claims:
- id: finance-rule-finance-rule-sales-tax-collection-01
  statement: Sales tax applies based on the ship-to state for orders through Shopstack and B2B invoices billed to a US or Canadian address.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-02
  statement: Oregon's sales tax rate is 0%.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-03
  statement: Washington's sales tax rate is 6.5%.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-04
  statement: California's sales tax rate is 7.25%.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-05
  statement: Any other US state's sales tax rate is 5%.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-06
  statement: Sales tax is not collected for Canadian orders at checkout.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-07
  statement: Shopstack reads the ship-to state at checkout to apply the matching rate automatically.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-08
  statement: There are no manual overrides for sales tax rates in Shopstack.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-09
  statement: Ana reconciles tax collected in Shopstack against the Ledgerly account monthly.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-sales-tax-collection-10
  statement: The effective date of this sales tax collection rule is 2024-03-15.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-sales-tax-collection.md
provenance_hash: 3d1417e5eafb5b4f
---

# Finance Rule Sales Tax Collection

## Summary

Finance rule: Sales tax collection by ship-to state Effective: 2024-03-15 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule | ship-to | rate | |---|---| | Oregon | 0% | | Washington | 6.5% | | California | 7.25% | | any other US state | 5% | | Canada | not collected at checkout | Scope Applies to every DTC order through Shopstack and every B2B invoice billed to a US or Canadian address. It covers the desk, chair, and accessory catalog in full, list and bundle pricing alike.

## Content

Finance rule: Sales tax collection by ship-to state
Effective: 2024-03-15
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
| ship-to | rate |
|---|---|
| Oregon | 0% |
| Washington | 6.5% |
| California | 7.25% |
| any other US state | 5% |
| Canada | not collected at checkout |

Scope
Applies to every DTC order through Shopstack and every B2B invoice billed to a US or Canadian address. It covers the desk, chair, and accessory catalog in full, list and bundle pricing alike. It does not cover use tax a customer may owe their own state on a purchase we didn't collect for; that liability is the customer's, not ours, and Larkstead doesn't advise on it.

Procedure
Shopstack reads the ship-to state at checkout and applies the matching rate automatically. No manual overrides. If an order somehow posts with the wrong rate, whoever catches it flags Ana directly rather than issuing a manual refund of the difference, since Shopstack's rate calculation is the system of record and a manual refund would just create a second discrepancy to track down later. Ana reconciles the tax collected in Shopstack against the Ledgerly tax liability account monthly, on the first business day, and any gap over 5.00 gets traced line by line before that month closes. Canadian orders ship with no tax collected and stay that way; Larkstead is not registered to collect Canadian sales tax as of this rule's effective date.

Worked example
A Washington customer orders one Robin headphone hook at 18.00 on 2 Jul 2024. Tax at 6.5% is 1.17, so the order totals 19.17. A California customer orders one Willow grommet cover pair at 12.00 the same week; tax at 7.25% is 0.87, total 12.87. An Idaho customer buying one Wren laptop stand at 69.00 falls under the other-US-state rate of 5%, so tax is 3.45 and the order totals 72.45. Oregon customers see no tax line at all.

Change log
- 2024-03-15: first written version.
