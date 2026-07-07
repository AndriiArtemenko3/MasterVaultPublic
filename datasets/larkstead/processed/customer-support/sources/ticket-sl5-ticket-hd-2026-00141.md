---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00141
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: ticket-sl5-ticket-hd-2026-00141-01
  statement: The ticket number is HD-2026-00141.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-02
  statement: The requester is Ruben Silva.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-03
  statement: 'The order number is #LS32241.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-04
  statement: The status of the ticket is pending.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-05
  statement: The assignee is Celeste Marin.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-06
  statement: The priority of the ticket is low.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-07
  statement: The merchandise total is 97.00.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-08
  statement: The free shipping threshold is 75.00 effective from 2024-03-01.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-09
  statement: The tracking number was issued on 2026-01-12.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00141-10
  statement: The carrier hasn't scanned the tracking number yet.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00141.md
provenance_hash: 1173491d4ab3a132
---

# Sl5 Ticket Hd 2026 00141

## Summary

Ticket: HD-2026-00141 Subject: tracking number never updates Requester: Ruben Silva <ruben.silva@example.com> Order: #LS32241 Status: pending Assignee: Celeste Marin Priority: low Tags: parcelpoint-cutover, no-carrier-scan Channel: web-form --- Message 1 (customer) --- 2026-01-14 16:47 PT Got a tracking number a couple days ago for my laptop stand and headphone hook order but it hasnt moved, still says pre transit. wanted to check if thats normal.

## Content

Ticket: HD-2026-00141
Subject: tracking number never updates
Requester: Ruben Silva <ruben.silva@example.com>
Order: #LS32241
Status: pending
Assignee: Celeste Marin
Priority: low
Tags: parcelpoint-cutover, no-carrier-scan
Channel: web-form

--- Message 1 (customer) --- 2026-01-14 16:47 PT
Got a tracking number a couple days ago for my laptop stand and headphone hook order but it hasnt moved, still says pre transit. wanted to check if thats normal. also i thought my order was suppost to ship free since it was over 75 but i dont see that broken out anywhere, can you confirm

--- Internal note (CM) --- 2026-01-14 17:15 PT
need order number, none given. found #LS32241, Ruben Silva, placed 2026-01-10. 1x LS-STD-001 79.00 + 1x LS-ACC-006 18.00 = 97.00 merchandise. free shipping applies, 75.00 threshold in force on order date (2024-03-01 version). 6.31 WA tax on 97.00. total 103.31. tracking number issued 2026-01-12, never activated with the carrier, part of transition week backlog.

--- Message 2 (agent, public reply) --- 2026-01-14 17:38 PT
Hi Ruben, could you confirm the order number next time so I can look it up faster? Found it either way: order #LS32241, the Wren laptop stand and the Robin headphone hook, placed 2026-01-10.

On shipping: merchandise total was 97.00, over the 75.00 free-shipping threshold effective 2024-03-01, so shipping shows 0.00. That part is correct.

On tracking: the number was issued 2026-01-12 but the carrier hasn't scanned it in yet. Not typical, and it's tied to this week's fulfillment switch rather than anything specific to your order. Asking warehouse to get it activated.

Celeste
Larkstead Support

--- Message 3 (customer) --- 2026-01-15 09:02 PT
Ok that's good to know about the shipping at least, thanks
