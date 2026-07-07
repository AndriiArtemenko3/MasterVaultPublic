---
domain: operations
type: source
title: Bug Report Shopstack Price Cache Stale Prices 2026 01 15
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-01
  statement: The 15 Jan price update goes live at 00:00 PT across nine SKUs.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-02
  statement: The storefront cache serves the old numbers until 08:07.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-03
  statement: Three orders placed between 06:00 and 08:07 get charged at the pre-change price.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-04
  statement: '#LS63900 is charged 649.00 instead of 679.00.'
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-05
  statement: '#LS63901 is charged 389.00 instead of 409.00.'
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-06
  statement: '#LS63902 is charged 899.00 instead of 949.00.'
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-07
  statement: The combined shortfall across the three orders is 100.00.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-08
  statement: Afternoon spot checks on all nine repriced SKUs show the correct new numbers on every page.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-09
  statement: The 05:58 purge call returns a 200 status code.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-10
  statement: The purge acknowledges without actually clearing anything downstream.
  confidence: high
  affects: []
- id: bug-report-bug-report-shopstack-price-cache-stale-p-feacd720-11
  statement: The cache ages out at 08:07 and shows the correct new number thereafter.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-report/bug-report-shopstack-price-cache-stale-prices-2026-01-15.md
provenance_hash: 4748e09d1280f87f
---

# Bug Report Shopstack Price Cache Stale Prices 2026 01 15

## Summary

Bug report: shopstack-price-cache-stale-prices-2026-01-15 Date: 2026-01-16 Filed by: Dmitri Okafor (DO) Filed with: Ray Lindqvist, internal (Shopstack) System under report: Shopstack storefront price-display cache Related tickets: 0 to date, no customer contact needed, found in the morning price audit Summary The 15 Jan price update went live at 00:00 PT across nine SKUs. The storefront cache kept serving the old numbers until 08:07, and three orders placed in that window got charged at the pre-change price.

## Content

Bug report: shopstack-price-cache-stale-prices-2026-01-15
Date: 2026-01-16
Filed by: Dmitri Okafor (DO)
Filed with: Ray Lindqvist, internal (Shopstack)
System under report: Shopstack storefront price-display cache
Related tickets: 0 to date, no customer contact needed, found in the morning
price audit

Summary
The 15 Jan price update went live at 00:00 PT across nine SKUs. The
storefront cache kept serving the old numbers until 08:07, and three orders
placed in that window got charged at the pre-change price.

Reproduction steps
1. Price update job runs 00:00 PT, 15 Jan, updating list price on desks,
   chairs, the dual arm, the lamp, and all three bundles.
2. The deploy is supposed to force-purge the price cache on the same run.
3. Purge call logged at 05:58 returned a 200, but the served price objects
   kept their pre-change values, meaning the purge acknowledged without
   actually clearing anything downstream.
4. A customer loading a product page or checkout between 06:00 and 08:07
   sees and gets charged the old price, since checkout reads the same cached
   object as the product page.
5. Cache aged out on its normal cycle at 08:07 and every page after that
   showed the correct new number.

Affected population
Three orders placed inside the 06:00-08:07 window, each honored at the
displayed price rather than corrected after the fact.
- #LS63900, LS-DSK-001-60, charged 649.00 instead of 679.00, shortfall 30.00,
  placed 06:41.
- #LS63901, LS-CHR-001-BLK, charged 389.00 instead of 409.00, shortfall
  20.00, placed 07:22.
- #LS63902, LS-BDL-001, charged 899.00 instead of 949.00, shortfall 50.00,
  placed 07:58.
Combined shortfall across the three orders: 100.00. No other orders landed
inside the window.

Evidence
Afternoon spot checks the same day on all nine repriced SKUs showed the
correct new numbers on every page. I tried twice that afternoon to force a
stale read the same way, reloading right after a manual cache clear, and
couldn't reproduce it, so this reads as a one-time miss on that specific
purge call rather than a standing hole in how the cache invalidates.

Ask
Ray, confirm why the 05:58 purge call returned success without clearing the
served objects, and add a post-purge check that samples a repriced SKU and
alerts if it still reads old. I'd like an answer on your next Tuesday, 20
Jan, on whether that ships before the next scheduled price change or needs
its own release. Not chasing the three customers for the 100.00, that's an
absorbed cost, not a dispute.
