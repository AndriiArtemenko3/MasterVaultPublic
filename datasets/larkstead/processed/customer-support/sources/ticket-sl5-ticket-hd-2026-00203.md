---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00203
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-sl5-ticket-hd-2026-00203-01
  statement: 'Omar Haddad from Summit Physio placed a quarterly footrest order on 2026-01-12 for eight Finch footrests, order #LS32277.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-02
  statement: 'The order #LS32277 is currently pending.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-03
  statement: There are 3 units of LS-ACC-001 on hand according to the parcelpoint system.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-04
  statement: 62 units of LS-ACC-001 were physically counted at the Reno load-out on 01-08.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-05
  statement: The footrests are in stock, and there is no real shortage.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-06
  statement: Priya released the hold on the order, and it is moving today.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-07
  statement: The new fulfillment system's count is off from the actual stock on the shelf.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-08
  statement: Omar Haddad has been a customer since 2024-11.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00203-09
  statement: The order consists of 8 LS-ACC-001 footrests priced at $69.00 each, totaling $552.00.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00203.md
provenance_hash: e0b7f18b3ba3fdb1
---

# Sl5 Ticket Hd 2026 00203

## Summary

Ticket: HD-2026-00203 Subject: quarterly footrest reorder showing out of stock? Requester: Omar Haddad <omar.haddad@summitphysio.example.com> Order: #LS32277 Status: pending Assignee: Priya Raman Priority: normal Tags: parcelpoint-cutover, inventory-variance, b2b Channel: email --- Message 1 (customer) --- 2026-01-16 10:05 PT Hi folks, this is Omar at Summit Physio, we placed our usual quarterly footrest order Monday (order #LS32277, eight Finch footrests for the treatment rooms) and just got a note saying it's delayed due to inventory.

## Content

Ticket: HD-2026-00203
Subject: quarterly footrest reorder showing out of stock?
Requester: Omar Haddad <omar.haddad@summitphysio.example.com>
Order: #LS32277
Status: pending
Assignee: Priya Raman
Priority: normal
Tags: parcelpoint-cutover, inventory-variance, b2b
Channel: email

--- Message 1 (customer) --- 2026-01-16 10:05 PT
Hi folks, this is Omar at Summit Physio, we placed our usual quarterly footrest order Monday (order #LS32277, eight Finch footrests for the treatment rooms) and just got a note saying it's delayed due to inventory. Strange since we do this every quarter and it's never been an issue, can you take a look?

--- Internal note (PR) --- 2026-01-16 10:44 PT
#LS32277, Omar Haddad, Summit Physio Group (PW-summit-8seat), placed 2026-01-12. 8x LS-ACC-001 69.00 = 552.00, free shipping (medium, over threshold), OR, no tax, total 552.00. customer since 2024-11, quarterly reorder, steady account. parcelpoint system shows 3 units of LS-ACC-001 on hand. we physically counted 62 units at the reno load-out on 01-08. this is a system count problem, not a real stock problem. flagging to dmitri for the cycle count.

--- Message 2 (agent, public reply) --- 2026-01-16 11:20 PT
Hi Omar, thanks for flagging this, sorry for the scare on a reorder that's usually routine. The footrests are in stock, this isn't a real shortage. We switched fulfillment partners this month and the new system's count for that item is off from what's actually on the shelf, which tripped the delay flag.
I've released the hold, your order is moving today. I'll make sure this gets fixed at the source so it doesn't hit your next reorder.
Priya

--- Message 3 (customer) --- 2026-01-16 14:52 PT
Appreciate the quick turnaround, that's a relief, we've got a full schedule this week
