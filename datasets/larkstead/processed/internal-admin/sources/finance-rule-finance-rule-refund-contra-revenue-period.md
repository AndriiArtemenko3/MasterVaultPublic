---
domain: internal-admin
type: source
title: Finance Rule Refund Contra Revenue Period
tags:
- other
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: other
key_claims:
- id: finance-rule-finance-rule-refund-contra-revenue-period-01
  statement: A refund books as contra-revenue against the order's original accounting period if that period is still open in Ledgerly.
  confidence: high
  affects:
  - refund-policy
- id: finance-rule-finance-rule-refund-contra-revenue-period-02
  statement: Periods close on the 5th business day of the following month.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-refund-contra-revenue-period-03
  statement: If the original period has already closed by the time the refund posts, the contra-revenue books to the current period instead.
  confidence: high
  affects:
  - refund-policy
- id: finance-rule-finance-rule-refund-contra-revenue-period-04
  statement: A restocking fee retained against the refund covers the store's cost of processing a return rather than goods that came back.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-refund-contra-revenue-period-05
  statement: Every refund amount posts to the cent, matching the return authorization on the ticket exactly.
  confidence: high
  affects:
  - refund-policy
- id: finance-rule-finance-rule-refund-contra-revenue-period-06
  statement: Covers every customer refund, DTC or B2B, full or partial, cash or store credit.
  confidence: high
  affects:
  - refund-policy
- id: finance-rule-finance-rule-refund-contra-revenue-period-07
  statement: Excludes vendor credit memos, which follow separate accounts-payable treatment, and excludes exchanges where no cash or store credit changes hands.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-refund-contra-revenue-period.md
provenance_hash: eb41a97f5dc5e29d
---

# Finance Rule Refund Contra Revenue Period

## Summary

Finance rule: Refund contra-revenue period Effective: 2025-04-21 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule A refund books as contra-revenue against the order's original accounting period if that period is still open in Ledgerly. Periods close on the 5th business day of the following month.

## Content

Finance rule: Refund contra-revenue period
Effective: 2025-04-21
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
A refund books as contra-revenue against the order's original accounting period if that period is still open in Ledgerly. Periods close on the 5th business day of the following month. If the original period has already closed by the time the refund posts, the contra-revenue books to the current period instead, dated the day the refund issues, never backdated to the order month, even when the return itself was requested well before close. A restocking fee retained against the refund, when one applies, stays revenue in the original period, since the fee covers the store's cost of processing a return rather than goods that came back. Every refund amount posts to the cent, matching the return authorization on the ticket exactly. No rounding, ever.

Scope
Covers every customer refund, DTC or B2B, full or partial, cash or store credit. Excludes vendor credit memos, which follow separate accounts-payable treatment, and excludes exchanges where no cash or store credit changes hands. This rule governs where the dollar accounting lands once support has already approved a refund; it says nothing about whether a return qualifies under the customer-facing refund window, which stands at 30 days as of this document's date.

Procedure
Support closes the return ticket with the refund amount stated to the cent and the original order number. Ana checks the order date against the close calendar before posting anything. If the ticket doesn't carry an exact cent figure, she holds the entry and asks support to correct the ticket rather than estimate one herself. Approved refunds post to Ledgerly the same business day when the ticket closes before 3pm, the next business day otherwise.

Worked example
Order #LS65201 shipped 2025-03-08, one Alder desk mat, charcoal, at 59.00, Oregon customer, no tax collected. March closed 2025-04-05. The customer opened a return under ticket HD-2025-65200 on 2025-04-14; the mat came back unopened, so no restocking fee applies. Since March had already closed by the 14th, the 59.00 posts as contra-revenue in April, dated 2025-04-14, not to the March period the order shipped in.

Change log
- 2025-04-21: first version.
