---
domain: customer-support
type: source
title: Sl2 Macros Returns Helprise
tags:
- sop
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: sop
key_claims:
- id: sop-sl2-macros-returns-helprise-01
  statement: Macros RET-01 and RET-02 are standard replies for return requests.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-02
  statement: The SOP for returns is effective from February 9, 2024.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-03
  statement: Agents must have Helprise access to use the macro library.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-04
  statement: Agents must have ParcelPoint read access to pull delivery dates.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-05
  statement: An agent must confirm the delivery date in ParcelPoint before quoting eligibility.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-06
  statement: If the delivery date is within 30 days, send macro RET-01.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-07
  statement: The reply text for macro RET-01 states 'within 30 days of delivery'.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-08
  statement: The restocking fee for opened, non-defective returns is 10%.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-09
  statement: If the delivery date is over 30 days, send macro RET-02.
  confidence: high
  affects: []
- id: sop-sl2-macros-returns-helprise-10
  statement: The reply text for macro RET-02 states 'outside our 30-day return window'.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/sop/sl2-macros-returns-helprise.md
provenance_hash: d9faa2254df5a591
---

# Sl2 Macros Returns Helprise

## Summary

SOP: Returns macros RET-01 and RET-02 Effective: 2024-02-09 Owner: Priya Raman (PR) Applies to: support agents Systems: Helprise, ParcelPoint portal Purpose Give agents the two standard macro replies for return requests, and the lookup step that has to happen before either one goes out. Prerequisites - Agent has Helprise access to the macro library - Agent has ParcelPoint read access to pull delivery dates Steps 1.

## Content

SOP: Returns macros RET-01 and RET-02
Effective: 2024-02-09
Owner: Priya Raman (PR)
Applies to: support agents
Systems: Helprise, ParcelPoint portal

Purpose
Give agents the two standard macro replies for return requests, and the
lookup step that has to happen before either one goes out.

Prerequisites
- Agent has Helprise access to the macro library
- Agent has ParcelPoint read access to pull delivery dates

Steps
1. Confirm the delivery date in ParcelPoint before quoting eligibility.
   Never quote from the order date or the ship date.
2. Delivery date 30 days or less from today: send macro RET-01. Reply
   text reads "within 30 days of delivery" and includes the restocking
   fee line: 10% on opened, non-defective returns.
3. Delivery date over 30 days from today: send macro RET-02. Reply text
   reads "outside our 30-day return window."
4. Log the delivery date and the day count in the internal note either
   way, so the next agent on the ticket doesn't have to look it up
   again.

Escalation
Customer disputes a RET-02 denial: hold the ticket and loop in Priya
before offering any exception.

Close-out
Nothing beyond the ticket status change. No separate log.

References
- Returns and refunds policy, effective 2024-01-15
