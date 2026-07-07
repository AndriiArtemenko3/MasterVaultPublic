---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00123
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-sl5-ticket-hd-2026-00123-01
  statement: 'Ivy Chen ordered the Fledgling workstation bundle (order #LS32228) on 2026-01-09.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-02
  statement: 'The order #LS32228 currently has a status of pending.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-03
  statement: Ivy Chen's order includes 1x LS-BDL-001 for 899.00, 69.00 for US-2 heavy, and 65.18 CA tax.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-04
  statement: 'The total for the order #LS32228 is 1033.18.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-05
  statement: The bundle order shows zero on hand despite the desk, chair, and cable kit being in stock at reno.
  confidence: high
  affects:
  - inventory-management
- id: ticket-sl5-ticket-hd-2026-00123-06
  statement: The issue with the bundle order is a system problem due to a fulfillment switch.
  confidence: high
  affects:
  - fulfillment-issues
- id: ticket-sl5-ticket-hd-2026-00123-07
  statement: Operations is addressing the mapping problem affecting the bundle order.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-08
  statement: Priya flagged Ivy Chen's order to be picked by hand.
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-09
  statement: 'The individual pieces in order #LS32228 are physically present in the warehouse.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00123-10
  statement: Ivy Chen was informed the order would ship once the problem is resolved.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00123.md
provenance_hash: 9be693d0d80ada5d
---

# Sl5 Ticket Hd 2026 00123

## Summary

Ticket: HD-2026-00123 Subject: bundle order stuck, nothing has shipped Requester: Ivy Chen <ivy.chen@example.com> Order: #LS32228 Status: pending Assignee: Priya Raman Priority: normal Tags: parcelpoint-cutover, bundle-zero-on-hand Channel: email --- Message 1 (customer) --- 2026-01-13 11:20 PT Hi, I ordered the Fledgling workstation bundle last week (order #LS32228) and it still says processing. No shipping confirmation, nothing.

## Content

Ticket: HD-2026-00123
Subject: bundle order stuck, nothing has shipped
Requester: Ivy Chen <ivy.chen@example.com>
Order: #LS32228
Status: pending
Assignee: Priya Raman
Priority: normal
Tags: parcelpoint-cutover, bundle-zero-on-hand
Channel: email

--- Message 1 (customer) --- 2026-01-13 11:20 PT
Hi, I ordered the Fledgling workstation bundle last week (order #LS32228) and it still says processing. No shipping confirmation, nothing. Payment went through fine on my end. This was supposed to be my new desk setup for the month.

--- Internal note (PR) --- 2026-01-13 13:05 PT
#LS32228, Ivy Chen, placed 2026-01-09. 1x LS-BDL-001 899.00 + 69.00 US-2 heavy + 65.18 CA tax (7.25% on merchandise) = 1033.18. parcelpoint shows LS-BDL-001 as one line item, no component explosion, zero on hand, even tho desk, chair, cable kit all in stock at reno. sitting unpicked. escalating to dmitri and ray, explosion rule not live on prod feed.

--- Message 2 (agent, public reply) --- 2026-01-13 13:41 PT
Hi Ivy, I'm sorry your bundle order looks stuck. Your order #LS32228 is the Fledgling workstation bundle, and everything in it is physically sitting in our warehouse: the desk, the chair, and the cable kit. The hold is a system issue on our side from a fulfillment switch this week, where the bundle showed as unavailable even though the individual pieces aren't. Not a stock problem, a mapping problem, and operations is on it today.

I've flagged your order to be picked by hand rather than wait on the fix. I'll come back to you the moment it ships.

Priya

--- Message 3 (customer) --- 2026-01-14 08:03 PT
Ok, thanks for explaining, that's better than a stock issue. Let me know when it moves.
