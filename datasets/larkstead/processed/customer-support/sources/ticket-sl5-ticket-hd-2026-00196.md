---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00196
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-sl5-ticket-hd-2026-00196-01
  statement: 'Owen Gallagher placed order #LS32270 on 2026-01-12.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-02
  statement: 'The status of order #LS32270 is open.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-03
  statement: 'Order #LS32270 is on hold due to a missing apartment number.'
  confidence: high
  affects:
  - address-validation-hold
- id: ticket-sl5-ticket-hd-2026-00196-04
  statement: 'The hold on order #LS32270 has been in place for 3 days as of 2026-01-15.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-05
  statement: 'Nobody sent a hold notice to Owen Gallagher regarding order #LS32270.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-06
  statement: Owen Gallagher's order has a total cost of 189.00.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-07
  statement: Owen Gallagher replied with his unit number 4B.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-08
  statement: Jonah Beck is assigned to ticket HD-2026-00196.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-09
  statement: Jonah Beck admits that the failure to notify Owen Gallagher is on them.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00196-10
  statement: The shipping address on file for Owen Gallagher is missing an apartment or unit number.
  confidence: high
  affects:
  - address-validation-hold
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00196.md
provenance_hash: 7d534fc9aa57d8cc
---

# Sl5 Ticket Hd 2026 00196

## Summary

Ticket: HD-2026-00196 Subject: order on hold, no idea why Requester: Owen Gallagher <owen.gallagher@example.com> Order: #LS32270 Status: open Assignee: Jonah Beck Priority: high Tags: parcelpoint-cutover, address-validation-hold Channel: email --- Message 1 (customer) --- 2026-01-15 12:30 PT so i ordered a monitor arm almost 4 days ago now (order #LS32270) and it still hasnt shipped, no email, no explanation. account just says "on hold" with no details.

## Content

Ticket: HD-2026-00196
Subject: order on hold, no idea why
Requester: Owen Gallagher <owen.gallagher@example.com>
Order: #LS32270
Status: open
Assignee: Jonah Beck
Priority: high
Tags: parcelpoint-cutover, address-validation-hold
Channel: email

--- Message 1 (customer) --- 2026-01-15 12:30 PT
so i ordered a monitor arm almost 4 days ago now (order #LS32270) and it still hasnt shipped, no email, no explanation. account just says "on hold" with no details. can somebody tell me whats happening instead of me having to guess, this is getting ridiculous for something that was supposed to be quick

--- Internal note (JB) --- 2026-01-15 13:10 PT
#LS32270, Owen Gallagher, placed 2026-01-12. 1x LS-ARM-001-DBL 189.00, medium, over the 75.00 threshold in force that day so free ship, OR no tax, total 189.00. checked parcelpoint, order's been flagged by address validation for 3 days now, missing apt/unit number on the shipping address. nobody sent owen a hold notice tho, thats on us, gonna fix that and get his unit number

--- Message 2 (agent, public reply) --- 2026-01-15 13:47 PT
Hey Owen, I get the frustration, and I'm sorry nobody let you know sooner, that's on us. Your order #LS32270 (the dual Heron monitor arm) got held by our shipping system three days ago because the address on file is missing an apartment or unit number. That would normally trigger an email right away, but the notification didn't go out this time, and I'm sorry again for the silence.
Could you reply with your unit number? I can release the hold myself and get this moving today.
Jonah
Larkstead Support

--- Message 3 (customer) --- 2026-01-15 14:02 PT
apt 4B, figured that was already on the account since ive ordered before
