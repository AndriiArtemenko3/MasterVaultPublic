---
domain: internal-admin
type: source
title: Finance Rule Staged B2B Revenue Recognition
tags:
- contract
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: contract
key_claims:
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-01
  statement: The finance rule for revenue recognition for staged B2B deliveries is effective on 2025-08-11.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-02
  statement: The rule recognizes revenue wave by wave at ship confirmation, not at contract signing or when the customer's deposit clears.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-03
  statement: Each wave's recognized value is its unit count times the price on the ship confirmation date, less any applicable discount tier.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-04
  statement: A deposit taken at signing is booked as a liability, unearned revenue, and never as revenue or contra-revenue.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-05
  statement: As each wave ships and receives ship confirmation, the liability draws down first before further wave value books to accounts receivable.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-06
  statement: The rule applies to B2B accounts with a written, multi-wave delivery schedule.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-07
  statement: Single-wave B2B orders recognize revenue in full at ship confirmation, similar to DTC.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-08
  statement: Deposits under 500.00 are ordinary prepayments and fall outside this rule since they rarely span more than one wave's value.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-09
  statement: Ana books the deposit as a liability on the day funds clear, citing the opportunity ID inline.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-staged-b2b-revenue-recognition-10
  statement: When a wave receives ship confirmation, revenue for that wave is posted on the same business day or the next business day depending on the time of confirmation.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-staged-b2b-revenue-recognition.md
provenance_hash: fe260e9492d3fe05
---

# Finance Rule Staged B2B Revenue Recognition

## Summary

Finance rule: Revenue recognition for staged B2B deliveries Effective: 2025-08-11 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule When a B2B contract ships in multiple waves instead of one shipment, revenue recognizes wave by wave at ship confirmation, not at contract signing and not when the customer's deposit clears. Each wave's recognized value is that wave's unit count times the price in force on the ship confirmation date, less any discount tier the account qualifies for.

## Content

Finance rule: Revenue recognition for staged B2B deliveries
Effective: 2025-08-11
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
When a B2B contract ships in multiple waves instead of one shipment, revenue recognizes wave by wave at ship confirmation, not at contract signing and not when the customer's deposit clears. Each wave's recognized value is that wave's unit count times the price in force on the ship confirmation date, less any discount tier the account qualifies for. A deposit taken at signing books as a liability, unearned revenue, never as revenue and never as contra-revenue. As each wave ships and Ledgerly logs the ship confirmation, the liability draws down first. Once the liability is exhausted, further wave value books straight to accounts receivable on the account's normal terms.

Scope
Applies to B2B accounts with a written, multi-wave delivery schedule. A single-wave B2B order recognizes the same way DTC does, in full at ship confirmation, so this rule adds nothing there. Deposits under 500.00 fall outside this rule and post as ordinary prepayment instead, since a deposit that small rarely spans more than one wave's value.

Procedure
Tom or Yuki logs the wave schedule in Pipewell against the opportunity once the contract signs, one line per wave with a target ship date. Ana books the deposit as a liability the day funds clear, citing the opportunity ID inline. When a wave gets a ship confirmation, she posts that wave's revenue the same business day if the confirmation lands before 3pm, the next business day if it lands after, and updates the remaining liability balance in the same entry.

Worked example
A hypothetical 12-seat account signs for the Roost executive bundle at 1449.00 per seat, total contract value 17388.00, with a 30% deposit at signing: 5216.40, booked as a liability. Twelve seats is short of the discount tier. No tier discount applies. Wave 1 ships 6 seats on 2025-09-15 and gets ship confirmation: recognized revenue 8694.00. The 5216.40 liability is smaller than that, so it zeroes out entirely and the remaining 3477.60 posts to accounts receivable. Wave 2 ships the other 6 seats on 2025-10-20: recognized revenue 8694.00, invoiced in full since no liability remains.

Change log
- 2025-08-11: first version.
