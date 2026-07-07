---
domain: customer-support
type: source
title: Macro Pkp 01 Showroom Pickup Ready
tags:
- policy
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: policy
key_claims:
- id: macro-macro-pkp-01-showroom-pickup-ready-01
  statement: '{{order.id}} is packed and waiting for you at the Portland showroom.'
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-02
  statement: Pickup hours are Tuesday through Saturday, 10 AM to 5 PM.
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-03
  statement: Whoever comes in should bring a photo ID matching the name on the order.
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-04
  statement: We hold pickup orders for 7 days from this message.
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-05
  statement: If you have not been in by then, the order goes back into inventory.
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-06
  statement: I will follow up here on {{ticket.id}} about a refund or a reship rather than assume you still want it.
  confidence: medium
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-07
  statement: If someone else is picking this up for you, reply first with their name.
  confidence: high
  affects: []
- id: macro-macro-pkp-01-showroom-pickup-ready-08
  statement: The showroom desk can check them in without any back and forth at the counter.
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/customer-support/macro/macro-pkp-01-showroom-pickup-ready.md
provenance_hash: 59574848eb7f238b
---

# Macro Pkp 01 Showroom Pickup Ready

## Summary

Macro: PKP-01 Name: showroom pickup ready Created: 2025-04-07 by Celeste Marin Trigger tags: pickup, showroom, portland ## Body Hi {{customer.first_name}}, Good news: {{order.id}} is packed and waiting for you at the Portland showroom. Pickup hours are Tuesday through Saturday, 10 AM to 5 PM, and whoever comes in should bring a photo ID matching the name on the order.

## Content

Macro: PKP-01
Name: showroom pickup ready
Created: 2025-04-07 by Celeste Marin
Trigger tags: pickup, showroom, portland

## Body

Hi {{customer.first_name}},

Good news: {{order.id}} is packed and waiting for you at the Portland showroom. Pickup hours are Tuesday through Saturday, 10 AM to 5 PM, and whoever comes in should bring a photo ID matching the name on the order.

We hold pickup orders for 7 days from this message, a policy that has been in place since the showroom opened its doors, and if you have not been in by then the order goes back into inventory. In that case I will follow up here on {{ticket.id}} about a refund or a reship rather than assume you still want it sitting on the shelf.

If someone else is picking this up for you, reply first with their name so the showroom desk can check them in without any back and forth at the counter.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Confirm the order number before sending. Don't send this off a name search alone.
- Set a reminder for day 7. If uncollected, message the customer before returning stock, don't let the hold lapse silently.
- Third-party pickup name goes on the order note immediately so the showroom desk can verify ID against the right person.
