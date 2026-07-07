---
domain: operations
type: source
title: Sl5 Cutover Postmortem
tags:
- postmortem
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: postmortem
key_claims:
- id: postmortem-sl5-cutover-postmortem-01
  statement: The Shopstack order feed flips to ParcelPoint on 2026-01-12 at 06:00 PT.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-02
  statement: 41 of 187 outbound orders ship more than 2 business days late during cutover week 2026-01-12 to 2026-01-16.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-03
  statement: The late rate of orders is 21.9% during cutover week 2026-01-12 to 2026-01-16.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-04
  statement: Nineteen bundle parent-SKU holds occur, causing order delays.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-05
  statement: Address-validation holds are placed on 11 orders with missing unit numbers.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-06
  statement: Seven transferred open orders are missing carrier re-manifest during the cutover.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-07
  statement: There is an inventory count variance on SKU LS-ACC-001 during the incident.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-08
  statement: Ray Lindqvist deploys the bundle explosion fix to production on 2026-01-20.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-09
  statement: Address validation changes from hard reject to warn-only on 2026-01-21.
  confidence: high
  affects: []
- id: postmortem-sl5-cutover-postmortem-10
  statement: The on-time ship rate recovers to 97% for the week of 2026-01-26.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/postmortem/sl5-cutover-postmortem.md
provenance_hash: 8072099126fd2817
---

# Sl5 Cutover Postmortem

## Summary

Postmortem: ParcelPoint outbound cutover, transition-week delays Published: 2026-01-29 Author: Dmitri Okafor Incident window: 2026-01-12 to 2026-01-23 Status: closed This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

## Content

Postmortem: ParcelPoint outbound cutover, transition-week delays
Published: 2026-01-29
Author: Dmitri Okafor
Incident window: 2026-01-12 to 2026-01-23
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures. This review runs summary, impact, timeline, causes, and fixes only.

Summary
The Shopstack order feed flipped to ParcelPoint on 2026-01-12 at 06:00 PT, ending outbound parcel shipping from the Portland backroom. Four distinct failure modes surfaced in the following week: bundle parent SKUs showing zero on-hand, address-validation holds on orders with missing unit numbers, transferred open orders missing carrier re-manifest, and an inventory-count variance on one SKU. Six customer tickets tracked the impact. Fixes landed the following week and the on-time ship rate recovered above the project's success target by the week of 2026-01-26.

Impact
- Cutover week 2026-01-12 to 2026-01-16: 41 of 187 outbound orders shipped more than 2 business days late, a 21.9% late rate.
- Late-order breakdown: 19 bundle parent-SKU holds, 11 address-validation holds, 7 transferred open orders missing carrier re-manifest, and 4 inventory-variance holds on LS-ACC-001.
- Six tickets tracked customer-facing impact: HD-2026-00118, HD-2026-00123, HD-2026-00141, HD-2026-00196, HD-2026-00203, HD-2026-00219.

Timeline
| date | event |
|---|---|
| 2026-01-12 | Shopstack order feed cuts over to ParcelPoint at 06:00 PT |
| 2026-01-13 | first delay tickets filed, HD-2026-00118 and HD-2026-00123 |
| 2026-01-14 | HD-2026-00141 filed |
| 2026-01-15 | free-shipping threshold rises to 99.00; HD-2026-00196 filed |
| 2026-01-16 | HD-2026-00203 filed; cutover week closes at 41 of 187 orders late |
| 2026-01-19 | HD-2026-00219 filed, transition-week backlog still clearing |
| 2026-01-20 | Ray Lindqvist deploys the bundle explosion fix to production |
| 2026-01-21 | ParcelPoint sets address validation to warn-only |
| 2026-01-23 | cycle count reconciles the 59-unit LS-ACC-001 variance |

Causes
The production order feed went live without the bundle explosion rule documented in the integration guide, so any LS-BDL-001 order reached ParcelPoint as a single unrecognized SKU with no on-hand quantity, which produced the 19 bundle-hold orders. Address validation rejected any order missing a unit number outright rather than flagging it for review, which produced the 11 address holds with no automatic customer notification. The seven re-manifest failures came from open orders that were mid-transit in Shopstack at the moment of cutover; the backroom's existing labels didn't carry a ParcelPoint-side manifest record, so the carrier never picked them up under the new system. The inventory variance traced to a count discrepancy between the physical load-out on 2026-01-08 and the figure ParcelPoint's system recorded for LS-ACC-001, a 59-unit gap between 62 physical and 3 system.

Fixes
Ray deployed the bundle explosion fix to the production feed on 2026-01-20, matching what the integration guide specified before go-live. ParcelPoint moved address validation from a hard reject to warn-only on 2026-01-21, so a missing unit number flags the order for review instead of blocking it. A cycle count on 2026-01-23 corrected the LS-ACC-001 system count to match the 62-unit physical count. The on-time ship rate recovered to 97% for the week of 2026-01-26, above the project's 96% target.
