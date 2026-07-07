---
domain: operations
type: source
title: Sop Vendor Onboarding Ledgerly Setup
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sop-vendor-onboarding-ledgerly-setup-01
  statement: Vendor onboarding for Ledgerly requires collecting the vendor's tax paperwork, invoice code, and payment terms before the first invoice.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-02
  statement: Effective date for the vendor onboarding process is 2025-09-15.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-03
  statement: The owner of the vendor onboarding process is Ana Petrova (AP).
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-04
  statement: The vendor onboarding process applies to finance and admin.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-05
  statement: Ledgerly is the system used for vendor onboarding.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-06
  statement: A signed contract or PO must be reviewed by Colefield & Tran LLP for new vendor relationships.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-07
  statement: The tax form required for vendors depends on whether they are domestic or foreign.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-08
  statement: For domestic vendors, a signed W-9 must be collected before onboarding.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-09
  statement: For foreign vendors, the foreign-vendor tax equivalent must be collected instead of a W-9.
  confidence: high
  affects: []
- id: sop-sop-vendor-onboarding-ledgerly-setup-10
  statement: Invoice codes consist of three letters derived from the vendor's name and should not collide with existing codes.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-vendor-onboarding-ledgerly-setup.md
provenance_hash: e799f277ce08669b
---

# Sop Vendor Onboarding Ledgerly Setup

## Summary

SOP: Vendor onboarding for Ledgerly and the invoice grammar Effective: 2025-09-15 Owner: Ana Petrova (AP) Applies to: finance and admin Systems: Ledgerly Purpose Get a new vendor's tax paperwork, invoice code, and [[payment-terms]] into Ledgerly before their first invoice lands, so that invoice doesn't sit unmatched while I chase down a W-9 after the fact. Prerequisites - Contract or PO signed, reviewed by Colefield & Tran LLP if it's a new vendor relationship rather than a renewal - Vendor's country known, since that decides which tax form applies Steps 1.

## Content

SOP: Vendor onboarding for Ledgerly and the invoice grammar
Effective: 2025-09-15
Owner: Ana Petrova (AP)
Applies to: finance and admin
Systems: Ledgerly

Purpose
Get a new vendor's tax paperwork, invoice code, and payment terms into Ledgerly before their first invoice lands, so that invoice doesn't sit unmatched while I chase down a W-9 after the fact.

Prerequisites
- Contract or PO signed, reviewed by Colefield & Tran LLP if it's a new vendor relationship rather than a renewal
- Vendor's country known, since that decides which tax form applies

Steps
1. Confirm domestic or foreign. If the vendor bills from inside the US, collect a signed W-9. If they bill from outside the US, collect the foreign-vendor tax equivalent instead, never a W-9 for an overseas supplier.
2. Assign the invoice code: three letters, usually pulled from the vendor's name, checked against every code already live (VRD, OSM, BGL, PPF, GCP, CSF, MRC, LPP, NMI, CTL as of this writing) so nothing collides in the invoice grammar.
3. Enter the vendor record in Ledgerly: name, vendor ID, invoice code, country, and the payment terms exactly as the contract states, Net-30 or Net-45, nothing else.
4. If payment terms in the contract don't match either Net-30 or Net-45, stop and flag it. Ledgerly's terms field only holds those two values right now. Nothing else fits.
5. File the signed contract and the tax form in the vendor's Ledgerly attachment, not a separate folder, so an auditor pulls one record and gets everything.
6. Watch the first invoice against the new code. If the number comes in as, say, INV-MRC-2024-001 and the code or the year is wrong, correct it before it posts, not after.

Escalation
If the vendor's country needs a tax form I haven't handled before, that goes to Colefield & Tran LLP before I touch step 1. If proposed terms fall outside Net-30 or Net-45, that goes to Mara.

Close-out
Vendor record marked active in Ledgerly once the tax form, invoice code, and terms are all entered and the first invoice has posted clean.

References
- Bitgrove Labs (VEN-03), invoice code BGL, Net-30, onboarded under this same shape of process starting 2024-03-01
- Colefield & Tran LLP (VEN-10), contract review
- Invoice number format: INV-<3 letter code>-<year>-<3 digit sequence>, example INV-VRD-2025-118
