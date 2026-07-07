---
domain: internal-admin
type: source
title: Finance Rule Petty Cash Showroom Till
tags:
- log
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: log
key_claims:
- id: finance-rule-finance-rule-petty-cash-showroom-till-01
  statement: The Portland showroom till carries a fixed float of 200.00 at all times.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-02
  statement: Ana counts the till every Friday after close.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-03
  statement: Ana reconciles actual cash on hand against the 200.00 expected.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-04
  statement: Any difference posts to the over/short log to the cent.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-05
  statement: If the count comes up short, the till gets replenished to exactly 200.00 the same day.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-06
  statement: If the count comes up over, the excess goes to the bank on the next deposit run.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-07
  statement: Scope covers cash taken in person at the showroom for walk-in purchases.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-08
  statement: The petty-cash float is used for small reimbursable expenses under 20.00.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-09
  statement: Anything over 20.00 routes through the corporate card instead of petty cash.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-petty-cash-showroom-till-10
  statement: Whoever draws from the float leaves a dated receipt in the till envelope.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-petty-cash-showroom-till.md
provenance_hash: 1e784fe7ba17e5d0
---

# Finance Rule Petty Cash Showroom Till

## Summary

Finance rule: Petty cash and showroom till Effective: 2025-05-05 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule The Portland showroom till carries a fixed float of 200.00 at all times. Ana counts the till every Friday after close and reconciles actual cash on hand, plus any petty-cash receipts taken from the float during the week, against the 200.00 expected.

## Content

Finance rule: Petty cash and showroom till
Effective: 2025-05-05
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
The Portland showroom till carries a fixed float of 200.00 at all times. Ana counts the till every Friday after close and reconciles actual cash on hand, plus any petty-cash receipts taken from the float during the week, against the 200.00 expected. Any difference, over or short, posts to the over/short log to the cent, never rounded to the nearest dollar. If the count comes up short, the till gets replenished from the checking account back to exactly 200.00 the same day. If it comes up over, the excess goes to the bank on the next deposit run rather than sitting in the float.

Scope
Covers cash taken in person at the showroom for walk-in purchases and the petty-cash float used for small reimbursable expenses: parking, postage, a supply run under 20.00. Excludes DTC and B2B invoices, which never touch cash, and excludes any purchase over 20.00, which routes through the corporate card instead of petty cash.

Procedure
Whoever draws from the float during the week leaves a dated receipt in the till envelope. Ana's Friday count totals the cash plus the week's receipts against the 200.00 expected. If a variance exceeds 5.00 in either direction, she flags Dmitri and Mara by email the same day rather than waiting for the next scheduled count, since a gap that size usually traces back to a missed sale or an unlogged receipt. Anything under 5.00 gets logged without escalation.

Worked example
Friday 2025-05-16 count: cash on hand 187.35, plus three petty-cash receipts totaling 9.10, two parking tickets and a roll of packing tape, for a documented total of 196.45. Expected float is 200.00. The till is short 3.55. Ana logs the 3.55 shortage against that week's date and replenishes to 200.00 from the checking account. No escalation, the gap sits under the 5.00 threshold.

Change log
- 2025-05-05: first version.
