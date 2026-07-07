---
domain: customer-support
type: source
title: Macro B2B 01 Routing
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: macro-macro-b2b-01-routing-01
  statement: The ticket is tagged to the B2B queue and the account owner is looped in rather than handled as a standard support case.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-02
  statement: Someone with full visibility into the account will follow up within one business day, often sooner.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-03
  statement: If anything touches pricing, seat counts, or contract terms, the answer will come from the account owner directly.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-04
  statement: The response-time target for the acknowledgment is 4 business hours.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-05
  statement: The acknowledgement does not cover the substantive answer from the account owner, which is within one business day.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-06
  statement: The support desk should not send to a walk-in or one-off B2C buyer.
  confidence: high
  affects: []
- id: macro-macro-b2b-01-routing-07
  statement: This process should avoid using pricing or terms language until the account owner weighs in.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/macro/macro-b2b-01-routing.md
provenance_hash: ad7bfef678209f8a
---

# Macro B2B 01 Routing

## Summary

Macro: B2B-01 Name: B2B ticket routing, acknowledgment Created: 2024-06-17 by Priya Raman Trigger tags: b2b, routing, escalation ## Body Hi {{customer.first_name}}, Thanks for reaching out. So this gets to the right person quickly, I am tagging {{ticket.id}} to our B2B queue and looping in your account owner rather than handling it as a standard support case.

## Content

Macro: B2B-01
Name: B2B ticket routing, acknowledgment
Created: 2024-06-17 by Priya Raman
Trigger tags: b2b, routing, escalation

## Body

Hi {{customer.first_name}},

Thanks for reaching out. So this gets to the right person quickly, I am tagging {{ticket.id}} to our B2B queue and looping in your account owner rather than handling it as a standard support case. Someone with full visibility into your account will follow up within one business day, often sooner.

If anything here touches pricing, seat counts, or contract terms, that answer will come from your account owner directly rather than from the support desk.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Tag b2b and move to the B2B queue immediately. Don't work it from the standard queue even if the board is quiet.
- Add a note on the matching Pipewell opportunity so the account owner sees it without digging, then hold off on any pricing or terms language until they weigh in.
- Response-time target for the acknowledgment above is 4 business hours. The one-business-day line in the body covers the substantive answer from the account owner, not this reply.
- Never send to a walk-in or one-off B2C buyer. It reads as strange and overkill to them.
