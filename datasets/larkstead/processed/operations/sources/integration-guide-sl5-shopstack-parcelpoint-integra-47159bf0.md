---
domain: operations
type: source
title: Sl5 Shopstack Parcelpoint Integration Guide
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-01
  statement: Shopstack fires an order webhook on order placement; ParcelPoint's order API accepts the post within seconds.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-02
  statement: There is no batch window for ParcelPoint API, differing from the manual-batch process it replaces.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-03
  statement: Ray holds the Shopstack webhook config and the ParcelPoint API credential for sandbox and production endpoints.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-04
  statement: Dmitri holds read access to the ParcelPoint order queue for monitoring.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-05
  statement: LS-BDL-* SKUs do not exist in ParcelPoint's item catalog and must explode to components before transmission.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-06
  statement: The parent SKU in LS-BDL-* must pass through in a reference field for support tracing.
  confidence: medium
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-07
  statement: ParcelPoint address validation rejects orders if shipping_address.line2 is not intact from Shopstack.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-08
  statement: Shopstack retries webhook delivery three times over 15 minutes before the order sits in a pending queue.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-09
  statement: Ray checks the pending queue on his next contract day after webhook delivery failure.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-10
  statement: Ray changes the mapping only on Tuesdays and Thursdays per his contract.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-11
  statement: Dmitri signs off on any change to the bundle explosion rule before production deployment.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-12
  statement: Sandbox validation confirmed all three LS-BDL-001 components exploded correctly and the kit work order posted at 6.00.
  confidence: high
  affects: []
- id: integration-guide-sl5-shopstack-parcelpoint-integra-47159bf0-13
  statement: Dmitri spot-checks five orders a day against the ParcelPoint queue for the first two weeks after go-live.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/integration-guide/sl5-shopstack-parcelpoint-integration-guide.md
provenance_hash: c85cd80aa0a8298e
---

# Sl5 Shopstack Parcelpoint Integration Guide

## Summary

Integration guide: Shopstack order feed to ParcelPoint order API Systems: Shopstack (storefront) -> ParcelPoint (3PL portal) Owner: Ray Lindqvist (RL) Maintainer: Ray Lindqvist (RL) Written: 2025-10-09 Schedule Real-time. Shopstack fires an order webhook on order placement; ParcelPoint's order API accepts the post within seconds.

## Content

Integration guide: Shopstack order feed to ParcelPoint order API
Systems: Shopstack (storefront) -> ParcelPoint (3PL portal)
Owner: Ray Lindqvist (RL)   Maintainer: Ray Lindqvist (RL)
Written: 2025-10-09

Schedule
Real-time. Shopstack fires an order webhook on order placement; ParcelPoint's order API accepts the post within seconds. No batch window, no nightly job, which is the main difference from the Ledgerly feed this replaces the manual-batch process for: that one ran nightly, this one doesn't wait for anything.

Access
Ray holds the Shopstack webhook config and the ParcelPoint API credential for the sandbox and production endpoints. Dmitri holds read access to the ParcelPoint order queue for monitoring.

Field mapping
| Shopstack field | ParcelPoint destination | note |
|---|---|---|
| order.order_number | order.external_ref | #LS format passes through unchanged |
| line_item.sku | item.sku | see bundle explosion rule below for LS-BDL-* |
| shipping_address.zip | destination.postal_code | |
| shipping_address.line2 | destination.address_line2 | must pass through intact, see failure modes |
| order.placed_at | order.received_at | UTC on both sides |

Bundle explosion rule
LS-BDL-* SKUs do not exist in ParcelPoint's item catalog and must explode to their components before transmission, one API call per component plus a kit work order. LS-BDL-001 explodes to 1x LS-DSK-001-48, 1x LS-CHR-001-BLK, 1x LS-ACC-002, and a kit work order billed at 6.00 per the rate card. The webhook payload carries the parent SKU in a reference field so support can trace a shipped order back to the bundle it came from; the parent SKU itself never reaches item.sku.

Failure modes
- Address missing a unit number: ParcelPoint address validation rejects the order unless shipping_address.line2 passed through intact from Shopstack. Detect via a rejected-order alert in the ParcelPoint queue; fix by confirming line2 on the Shopstack order and re-posting.
- Bundle explosion skipped: a bundle parent SKU reaching item.sku directly shows as an unrecognized item with zero on-hand and the order sits unpicked. Detect via any LS-BDL-* string appearing in the ParcelPoint item log; fix by re-running the order through the explosion step manually and filing a bug against the webhook build.
- Webhook delivery failure: Shopstack retries three times over 15 minutes, then the order sits in a pending queue Ray checks on his next contract day.
- Orders transferred from the backroom queue during cutover: an open order that was mid-transit in Shopstack at the moment of cutover carries a backroom-issued label with no ParcelPoint manifest record behind it. Detect via a carrier scan gap of more than 24 hours after label creation; fix by re-manifesting the order manually on the ParcelPoint side rather than waiting on a fresh label.

Change control
Ray changes the mapping only on Tuesdays and Thursdays per his contract, tested against the ParcelPoint sandbox first. Dmitri signs off on any change to the bundle explosion rule before it goes to production.

Verification
Sandbox validation ran against test order #LS90001: a synthetic LS-BDL-001 order that confirmed all three components exploded correctly and the kit work order posted at 6.00. Once in production, Dmitri spot-checks five orders a day against the ParcelPoint queue for the first two weeks after go-live.
