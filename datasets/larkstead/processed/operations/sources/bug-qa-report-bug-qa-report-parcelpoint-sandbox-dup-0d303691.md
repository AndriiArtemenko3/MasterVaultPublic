---
domain: operations
type: source
title: Bug Qa Report Parcelpoint Sandbox Duplicate Orders
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-01
  statement: ParcelPoint's sandbox webhook creates a duplicate test order in Shopstack when a delivery retry follows a simulated timeout.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-02
  statement: The webhook does not send an idempotency key on the payload.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-03
  statement: ParcelPoint retries delivery after 30 seconds following a timeout.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-04
  statement: 14 duplicate test orders were created across 3 throttled sandbox runs between 2025-10-06 and 2025-10-17.
  confidence: high
  affects:
  - duplicate-orders
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-05
  statement: There are 210 total test orders pushed during the same window.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-06
  statement: The duplicate rate on the throttled runs is 6.7 percent.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-07
  statement: The two non-throttled control runs produced zero duplicates.
  confidence: high
  affects:
  - duplicate-orders
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-08
  statement: The first duplicate was caught in run one, with 4 duplicates out of 60 test orders.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-09
  statement: The second run had 5 duplicates out of 70 test orders.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-10
  statement: The third run had 5 duplicates out of 80 test orders.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-11
  statement: No live customer orders sit in this path today.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-12
  statement: An idempotency key on the webhook payload is requested, keyed to Shopstack's own order ID.
  confidence: high
  affects:
  - webhook
- id: bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-13
  statement: The idempotency key will ensure retried delivery gets recognized as the same order.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-qa-report/bug-qa-report-parcelpoint-sandbox-duplicate-orders.md
provenance_hash: bbcef7a77a147d1f
---

# Bug Qa Report Parcelpoint Sandbox Duplicate Orders

## Summary

Bug report: parcelpoint-sandbox-webhook-duplicate-orders Date: 2025-10-20 Filed by: Dmitri Okafor (DO) Filed with: ParcelPoint Fulfillment (VEN-04) System under report: ParcelPoint sandbox order-intake webhook, integration test environment ahead of the outbound cutover Related tickets: none. Sandbox only, no live customer orders touched.

## Content

Bug report: parcelpoint-sandbox-webhook-duplicate-orders
Date: 2025-10-20
Filed by: Dmitri Okafor (DO)
Filed with: ParcelPoint Fulfillment (VEN-04)
System under report: ParcelPoint sandbox order-intake webhook, integration test environment ahead of the outbound cutover
Related tickets: none. Sandbox only, no live customer orders touched.

Summary
ParcelPoint's sandbox webhook creates a duplicate test order in Shopstack whenever a delivery retry follows a simulated timeout, since neither side sends an idempotency key on the payload.

Reproduction steps
1. Push a test order to the ParcelPoint sandbox webhook endpoint.
2. Throttle the sandbox response to simulate a timeout.
3. ParcelPoint retries delivery after 30 seconds. Shopstack's sandbox order table gets a second, fully duplicate order instead of recognizing that the first attempt succeeded.

Affected population
14 duplicate test orders across 3 throttled sandbox runs between 2025-10-06 and 2025-10-17, out of 210 total test orders pushed in the same window, a 6.7 percent duplicate rate on the throttled runs. The 196 test orders pushed in the two non-throttled control runs in the same window produced zero duplicates.

Evidence
06 Oct: first duplicate caught in run one, 4 duplicates out of 60 test orders. 11 Oct: run two, 5 duplicates out of 70. 17 Oct: run three, 5 duplicates out of 80. Same gap, confirmed again. No live customer orders sit in this path today, since light and medium parcels still ship out of the Portland showroom backroom under the current contract scope and the sandbox never touches production data.

Ask
An idempotency key on the webhook payload, keyed to Shopstack's own order ID rather than a timestamp, so a retried delivery gets recognized as the same order rather than a new one. We want this confirmed clean across three consecutive throttled runs before it goes on the cutover readiness checklist, ideally before the next test window opens 03 Nov.
