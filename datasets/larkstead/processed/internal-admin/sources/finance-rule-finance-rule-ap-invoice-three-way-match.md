---
domain: internal-admin
type: source
title: Finance Rule Ap Invoice Three Way Match
tags:
- log
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: log
key_claims:
- id: finance-rule-finance-rule-ap-invoice-three-way-match-01
  statement: 'No vendor invoice posts to Ledgerly until three records agree: the purchase order, the receiving log entry, and the invoice itself.'
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-02
  statement: SKU or line description, quantity, and unit price must match across all three records with zero tolerance on quantity.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-03
  statement: There is a 2% tolerance on price to absorb rounding on freight-inclusive lines.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-04
  statement: An invoice that clears the match posts the same business day.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-05
  statement: An invoice that doesn't clear the match sits as an exception until it is resolved.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-06
  statement: The rule covers every purchase order raised against a vendor on the standing vendor list.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-07
  statement: Standing invoices with no purchase order behind them are exempt and post on Ana's review alone.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-08
  statement: 'Hank logs receiving quantities the day a shipment lands: carrier, dock time, unit count.'
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-09
  statement: If everything ties out, Ledgerly gets the entry within 1 business day of the invoice date.
  confidence: high
  affects: []
- id: finance-rule-finance-rule-ap-invoice-three-way-match-10
  statement: Exceptions worth 250.00 or more in variance copy Mara too, since that crosses the self-approve line.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/internal-admin/finance-rule/finance-rule-ap-invoice-three-way-match.md
provenance_hash: e3b85e8e29445b43
---

# Finance Rule Ap Invoice Three Way Match

## Summary

Finance rule: AP invoice intake and three-way match Effective: 2024-04-15 Supersedes: none Owner: Ana Petrova Approved by: Mara Voss Rule No vendor invoice posts to Ledgerly until three records agree: the purchase order, the receiving log entry, and the invoice itself. SKU or line description, quantity, and unit price must match across all three, zero tolerance on quantity and a 2% tolerance on price to absorb rounding on freight-inclusive lines.

## Content

Finance rule: AP invoice intake and three-way match
Effective: 2024-04-15
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
No vendor invoice posts to Ledgerly until three records agree: the purchase order, the receiving log entry, and the invoice itself. SKU or line description, quantity, and unit price must match across all three, zero tolerance on quantity and a 2% tolerance on price to absorb rounding on freight-inclusive lines. An invoice that clears the match posts the same business day. One that doesn't sits as an exception until it's resolved.

Scope
Covers every purchase order raised against a vendor on the standing vendor list, from a container load off Cascadia Freight to a single carton reorder with GreenCrate Packaging. Standing invoices with no purchase order behind them, rent, insurance, the legal retainer, are exempt and post on Ana's review alone, since there's nothing to three-way match against.

Procedure
Hank logs receiving quantities the day a shipment lands: carrier, dock time, unit count. Ana pulls the matching PO once the vendor invoice arrives and holds all three side by side, line by line. If everything ties out, Ledgerly gets the entry within 1 business day of the invoice date. If it doesn't, the invoice goes into the exception queue and Ana emails whoever can close the gap, vendor contact or warehouse, with the PO number and the discrepancy stated in units and dollars, never rounded. Exceptions worth 250.00 or more in variance copy Mara too, since that crosses the self-approve line under the standing expense policy.

Worked example
An Ostrava Metalworks shipment of 40 Birch desk frames for the 60 in model (LS-DSK-001-60), landed at 248.00 each, arrives 14 Mar 2024; Hank's receiving log shows 40 units in. The PO calls for 40 units at 248.00, 9920.00 total. Invoice INV-OSM-2024-755 bills 9920.00 flat. All three agree, so it posts that day. Compare the GreenCrate Packaging carton order from the same week: PO for 3000 desk cartons, receiving log shows 2950 landed, invoice bills the full 3000. Fifty cartons short. That's a quantity mismatch, and quantity has no tolerance, so it waits in the exception queue instead of going straight into Ledgerly.

Change log
- 2024-04-15: first written version.
