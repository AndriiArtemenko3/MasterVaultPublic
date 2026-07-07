---
domain: operations
type: source
title: Bug Report Mailloft Unsubscribe Sync Lag
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-01
  statement: Unsubscribe clicks in Mailloft campaigns aren't reaching Shopstack customer records for up to 48 hours.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-02
  statement: A subscriber can get one more send after opting out.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-03
  statement: Mailloft's dashboard marks the contact unsubscribed within a minute.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-04
  statement: The marketing-preference field in Shopstack stays subscribed for 40 to 52 hours before flipping, averaging 48.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-05
  statement: Over a two-week monitoring window, 15 of 46 unsubscribe clicks lagged.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-06
  statement: 31 unsubscribe clicks synced within the hour.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-07
  statement: All 15 lagging clicks were self-service footer-link clicks.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-08
  statement: Contacts removed by staff in Mailloft sync to Shopstack within minutes.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-09
  statement: Only the customer-facing unsubscribe link lags.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-10
  statement: Three customers filed tickets about the issue over a week after clicking unsubscribe.
  confidence: high
  affects: []
- id: bug-report-bug-report-mailloft-unsubscribe-sync-lag-11
  statement: The metric to track once the issue is fixed is laggers per 100 unsubscribes, currently sitting at about 33.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-report/bug-report-mailloft-unsubscribe-sync-lag.md
provenance_hash: 2466999e4a91c9e7
---

# Bug Report Mailloft Unsubscribe Sync Lag

## Summary

Bug report: mailloft-unsubscribe-sync-lag Date: 2024-08-14 Filed by: Sofia Grieg (SG) Filed with: Mailloft support (vendor case opened 2024-08-14) System under report: Mailloft to Shopstack customer-record sync, unsubscribe events Related tickets: 3 to date -- HD-2024-63734, HD-2024-63738, HD-2024-63745 (first HD-2024-63734, 2024-08-09) Summary Unsubscribe clicks in Mailloft campaigns aren't reaching the Shopstack customer record for up to 48 hours, so a subscriber can get one more send in that window right after opting out. Reproduction steps 1.

## Content

Bug report: mailloft-unsubscribe-sync-lag
Date: 2024-08-14
Filed by: Sofia Grieg (SG)
Filed with: Mailloft support (vendor case opened 2024-08-14)
System under report: Mailloft to Shopstack customer-record sync, unsubscribe events
Related tickets: 3 to date -- HD-2024-63734, HD-2024-63738, HD-2024-63745 (first HD-2024-63734, 2024-08-09)

Summary
Unsubscribe clicks in Mailloft campaigns aren't reaching the Shopstack customer record for up to 48 hours, so a subscriber can get one more send in that window right after opting out.

Reproduction steps
1. Subscribe a test contact to the Chinook campaign list and send a live campaign.
2. Click unsubscribe from the campaign footer. Mailloft's own dashboard marks the contact unsubscribed within a minute.
3. Check that contact's marketing-preference field in Shopstack. It stays subscribed for 40 to 52 hours before flipping, averaging 48.

Affected population
Over a two-week monitoring window, 15 of 46 unsubscribe clicks lagged, and the other 31 synced within the hour. All 15 laggers were self-service footer-link clicks, none were staff-side removals.

Evidence
Contacts removed by staff directly in the Mailloft admin panel sync to Shopstack within minutes, every time, so the direct-removal path is clean. Only the customer-facing unsubscribe link lags. Three customers caught in the gap got a scheduled send after clicking unsubscribe and filed tickets over it, first HD-2024-63734 on 09 Aug, then HD-2024-63738 and HD-2024-63745 within the same week.

Ask
Root cause and a fix timeline from Mailloft. Short of that, I want a standing Shopstack poll job checking unsubscribe status every 6 hours so no campaign fires into the gap. Metric to track once it's fixed: laggers per 100 unsubscribes, sitting at about 33 right now.
