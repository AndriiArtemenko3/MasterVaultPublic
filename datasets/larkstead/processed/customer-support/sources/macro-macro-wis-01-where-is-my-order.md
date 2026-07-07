---
domain: customer-support
type: source
title: Macro Wis 01 Where Is My Order
tags:
- other
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: other
key_claims:
- id: macro-macro-wis-01-where-is-my-order-01
  statement: The ParcelPoint tracking shows the latest location and delivery window once the order leaves the warehouse.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-02
  statement: No scan in the tracking does not mean the order is lost.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-03
  statement: If no scan appears, the pallet is typically still staged for pickup.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-04
  statement: A follow-up is set for three business days if there is no scan.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-05
  statement: Accessory orders like mats or cable kits may ship directly from the Portland showroom instead of through ParcelPoint.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-06
  statement: Light and medium accessory orders often ship from the Portland backroom.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-07
  statement: If ParcelPoint shows nothing at all, the local log is checked.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-08
  statement: No scan after the three-day follow-up means escalation to Dmitri.
  confidence: high
  affects: []
- id: macro-macro-wis-01-where-is-my-order-09
  statement: Agents never promise a specific delivery date; only the window shown by tracking is provided.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/macro/macro-wis-01-where-is-my-order.md
provenance_hash: 0e2f19b7a7577665
---

# Macro Wis 01 Where Is My Order

## Summary

Macro: WIS-01 Name: where-is-my-order, first check Created: 2024-03-18 by Priya Raman Trigger tags: shipping, wismo, order-status ## Body Hi {{customer.first_name}}, Thanks for checking in on {{order.id}}. Before replying I looked up the ParcelPoint tracking on our end, since that is where the carrier scans land once a desk, chair, or bundle order leaves the warehouse and starts moving toward you.

## Content

Macro: WIS-01
Name: where-is-my-order, first check
Created: 2024-03-18 by Priya Raman
Trigger tags: shipping, wismo, order-status

## Body

Hi {{customer.first_name}},

Thanks for checking in on {{order.id}}. Before replying I looked up the ParcelPoint tracking on our end, since that is where the carrier scans land once a desk, chair, or bundle order leaves the warehouse and starts moving toward you. If tracking already shows a scan, I will give you the latest location and the delivery window right here on {{ticket.id}}. If there is no scan yet, that is not the same as lost, it usually just means the pallet is still staged for pickup, and I will set a follow-up for three business days out so you are not left wondering.

Smaller accessory orders, things like mats or cable kits, sometimes ship straight from our Portland showroom instead of through ParcelPoint. If yours is one of those I will check the local fulfillment log instead and update you either way.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Pull the actual ParcelPoint tracking number before sending. Don't estimate from the order date alone.
- Light and medium accessory orders often ship from the Portland backroom, not ParcelPoint. Check the local log if ParcelPoint shows nothing at all.
- No scan after the 3-day follow-up window means escalate to Dmitri, not resend this macro a second time.
- Never promise a specific delivery date, only the window tracking actually shows.
