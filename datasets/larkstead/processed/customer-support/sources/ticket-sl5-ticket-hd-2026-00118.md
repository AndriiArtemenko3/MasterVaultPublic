---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00118
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-sl5-ticket-hd-2026-00118-01
  statement: 'Order #LS32210 is associated with Marcus Bell and is currently open.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-02
  statement: 'Marcus Bell received a shipping confirmation email for order #LS32210 on 2026-01-09.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-03
  statement: 'The tracking link for order #LS32210 states that only a label was created, with no further updates.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-04
  statement: 'The gray Rowan chair in order #LS32210 costs 389.00 + 49.00 for shipping, totaling 438.00.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-05
  statement: 'The shipping label for order #LS32210 was created in Portland on 2026-01-09.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-06
  statement: 'There has been no carrier scan for order #LS32210 since the label was created.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-07
  statement: 'Order #LS32210 was in the 01-12 open-order transfer batch when the feed flipped.'
  confidence: medium
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-08
  statement: 'The agent obtained confirmation that order #LS32210 got labeled on 2026-01-09, but the carrier never scanned it in.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-09
  statement: 'Warehouse is tasked to re-manifest order #LS32210 to resolve tracking issues.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00118-10
  statement: 'Jonah Beck is the assigned agent for order #LS32210.'
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00118.md
provenance_hash: 28bb6771afbb50ad
---

# Sl5 Ticket Hd 2026 00118

## Summary

Ticket: HD-2026-00118 Subject: order shipped label but tracking says nothing Requester: Marcus Bell <marcus.bell@example.com> Order: #LS32210 Status: open Assignee: Jonah Beck Priority: normal Tags: parcelpoint-cutover, no-carrier-scan Channel: email --- Message 1 (customer) --- 2026-01-13 09:14 PT hey, i got a shipping confirmation email for my chair on the 9th but the tracking link still just says label created, nothing since. order #LS32210, the gray Rowan chair.

## Content

Ticket: HD-2026-00118
Subject: order shipped label but tracking says nothing
Requester: Marcus Bell <marcus.bell@example.com>
Order: #LS32210
Status: open
Assignee: Jonah Beck
Priority: normal
Tags: parcelpoint-cutover, no-carrier-scan
Channel: email

--- Message 1 (customer) --- 2026-01-13 09:14 PT
hey, i got a shipping confirmation email for my chair on the 9th but the tracking link still just says label created, nothing since. order #LS32210, the gray Rowan chair. its been 4 days now and i havent gotten any update, is something wrong with it or is it just slow this week

--- Internal note (JB) --- 2026-01-13 10:02 PT
#LS32210, Marcus Bell, placed 2026-01-08. 1x LS-CHR-001-GRY 389.00 + 49.00 US-1 heavy = 438.00, OR, no tax. label created 2026-01-09 in portland backroom, checked parcelpoint queue and shopstack both, no carrier scan anywhere since. this one was in the 01-12 open-order transfer batch when the feed flipped and looks like it never got re-manifested with the carrier on the new side. flagging to dmitri, gonna reach out to warehouse too

--- Message 2 (agent, public reply) --- 2026-01-13 10:41 PT
Hi Marcus, so sorry about the wait, and sorry again that tracking's been no help either. Your order #LS32210 (the fog gray Rowan chair) got labeled on the 9th, but the carrier never scanned it in, right as we switched shipping partners, so a batch of already-labeled orders got stuck in the handoff.
Nothing's lost, just sitting. I'm getting warehouse to re-manifest it today. I'll follow up here as soon as I see movement.
Jonah
Larkstead Support

--- Message 3 (customer) --- 2026-01-13 14:52 PT
ok thanks for looking into it, please keep me posted
