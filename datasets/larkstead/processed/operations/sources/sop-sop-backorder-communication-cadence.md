---
domain: operations
type: source
title: Sop Backorder Communication Cadence
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: sop-sop-backorder-communication-cadence-01
  statement: The backorder communication cadence is effective from 2025-10-06.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-02
  statement: The owner of the backorder communication cadence is Priya Raman.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-03
  statement: The backorder policy applies to support agents.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-04
  statement: Orders flagged backordered in Helprise have an estimated restock date pulled from Hank or the ParcelPoint portal.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-05
  statement: Customers receive an email with the estimated ship date and reason for the delay within 2 calendar days of the order being backordered.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-06
  statement: Escalation to Dmitri occurs on day 7 without a confirmed restock date from the vendor.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-07
  statement: If the customer prefers to cancel, it is processed as a standard cancellation and refund.
  confidence: high
  affects:
  - refund-policy
- id: sop-sop-backorder-communication-cadence-08
  statement: The ticket closes when the order ships or the customer cancels.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-09
  statement: Every notice sent on the ticket is logged with date, method, and promised ship date.
  confidence: high
  affects: []
- id: sop-sop-backorder-communication-cadence-10
  statement: Three or more backorders on the same SKU in a week escalate to Dmitri due to potential supply problems.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-backorder-communication-cadence.md
provenance_hash: 67ab218a59d54bb0
---

# Sop Backorder Communication Cadence

## Summary

SOP: Backorder communication cadence Effective: 2025-10-06 Owner: Priya Raman (PR) Applies to: [[support]] agents Systems: Helprise, Mailloft Purpose Nobody should have to email us asking where their order is. If a unit's backordered, the customer hears it from us first, gets a real date, and hears from us again the moment that date moves.

## Content

SOP: Backorder communication cadence
Effective: 2025-10-06
Owner: Priya Raman (PR)
Applies to: support agents
Systems: Helprise, Mailloft

Purpose
Nobody should have to email us asking where their order is. If a unit's backordered, the customer hears it from us first, gets a real date, and hears from us again the moment that date moves.

Prerequisites
- Order flagged backordered in Helprise, with an estimated restock date pulled from Hank or the ParcelPoint portal, never guessed
- Customer's order number and email confirmed on the ticket

Steps
1. Check the backorder queue in Helprise each morning for anything flagged in the last 24 hours.
2. Send the day-2 notice. Within 2 calendar days of the order showing backordered, send the customer an email through Mailloft with the current estimated ship date and a plain reason, vendor delay or high demand, whichever applies. Most desk and chair delays trace back to an Ostrava Metalworks container; Vireo delays trace back to Meridian Components.
3. If the estimated date changes before it ships, that's the revised-date rule: send a fresh notice with the new date the same day the change lands on your desk, and never let a promised date pass silently. A slipped date always gets its own notice. Don't fold two slips into one email hoping the customer won't count.
4. Log every notice sent on the ticket: date, method, and the promised ship date, so whoever picks up the thread next sees the whole history, not just the latest one.
5. Day 7, still backordered with no confirmed restock date from the vendor: escalate to Dmitri the same day. Don't wait for the customer to ask twice.
6. If the customer would rather cancel than wait, process it as a standard cancellation and refund the original payment method. This isn't a return, the item never shipped, so return-window and restocking-fee terms don't come into it at all.

Escalation
Day 7 with no vendor date goes to Dmitri. Folks, no exceptions. Three or more backorders on the same SKU in a week also goes to him, might be a real supply problem, not three unlucky customers.

Close-out
Ticket closes when the order ships or the customer cancels. Either way, the notice log on the ticket should read clean start to finish with no gap longer than the cadence above allows.

References
- Ostrava Metalworks (VEN-02), Meridian Components (VEN-07), typical backorder sources
- Order number format example: #LS63214
