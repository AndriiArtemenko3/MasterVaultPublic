---
domain: operations
type: source
title: Relnote Shopstack Storefront Redesign 2025 04
tags:
- contract
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: contract
key_claims:
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-01
  statement: The Shopstack storefront redesign was released on 2025-04-08.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-02
  statement: Ray Lindqvist is the publisher of the Shopstack storefront redesign contract.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-03
  statement: The storefront redesign applies to all bundle and product pages.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-04
  statement: There are no pricing changes associated with the storefront redesign.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-05
  statement: Bundle detail pages 404'd when a component SKU's variant suffix ran past 8 characters.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-06
  statement: Bundle pages now have one add-to-cart button instead of three.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-07
  statement: Checkout has been reduced from 4 steps to 3 steps.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-08
  statement: Shipping and billing now share one screen in the redesigned checkout process.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-09
  statement: The largest product images on desk and chair pages now lazy-load below the fold.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-shopstack-storefront-r-8ee94781-10
  statement: The rollout of the redesign is 10 percent on 08 Apr, 50 percent on 09 Apr, and 100 percent on 11 Apr.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/release-meeting-note/relnote-shopstack-storefront-redesign-2025-04.md
provenance_hash: 86546ae9a70abc11
---

# Relnote Shopstack Storefront Redesign 2025 04

## Summary

Release notes: Shopstack storefront redesign Released: 2025-04-08 Publisher: Ray Lindqvist (contract, Shopstack) Applies to: shopstack storefront, all bundle and product pages; no [[pricing]] changes Fixed - Bundle detail pages 404'd when a component SKU's variant suffix ran past 8 characters, which hit the LS-MAT-001-CHL and LS-MAT-001-SND cross-sell blocks on the desk bundle pages. Changed - Bundle pages rebuilt: component list, per-bundle savings against itemized list price, one add-to-cart button instead of three.

## Content

Release notes: Shopstack storefront redesign
Released: 2025-04-08
Publisher: Ray Lindqvist (contract, Shopstack)
Applies to: shopstack storefront, all bundle and product pages; no pricing changes

Fixed
- Bundle detail pages 404'd when a component SKU's variant suffix ran past 8 characters, which hit the LS-MAT-001-CHL and LS-MAT-001-SND cross-sell blocks on the desk bundle pages.

Changed
- Bundle pages rebuilt: component list, per-bundle savings against itemized list price, one add-to-cart button instead of three.
- Checkout cut from 4 steps to 3, shipping and billing now share one screen.
- Product image payload trimmed on desk and chair pages, largest images now lazy-load below the fold.

Known issues
- Safari on iOS occasionally double-submits the new single checkout button on a slow connection. Fix scheduled for the next release.

Rollout
10 percent 08 Apr, 50 percent 09 Apr, 100 percent 11 Apr.

Support
Zero open Helprise tickets tagged storefront-redesign at release. A reported duplicate order from checkout should be checked against the Safari double-submit issue before a carrier claim gets filed.
