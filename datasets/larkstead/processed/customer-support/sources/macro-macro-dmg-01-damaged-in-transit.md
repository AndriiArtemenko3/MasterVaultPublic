---
domain: customer-support
type: source
title: Macro Dmg 01 Damaged In Transit
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: macro-macro-dmg-01-damaged-in-transit-01
  statement: The piece arrived damaged for order id {{order.id}}.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-02
  statement: The customer needs to reply with two photos to get a carrier claim moving.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-03
  statement: One photo must show the shipping carton and one of the item.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-04
  statement: The carrier claim is filed the same day the photos land.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-05
  statement: There is no charge for the replacement.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-06
  statement: The damaged piece does not need to be returned unless requested later.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-07
  statement: Replacements usually ship within one business day of receiving the photos.
  confidence: high
  affects: []
- id: macro-macro-dmg-01-damaged-in-transit-08
  statement: No restocking fee is charged for damaged claims.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/macro/macro-dmg-01-damaged-in-transit.md
provenance_hash: 589da6bac8163fd8
---

# Macro Dmg 01 Damaged In Transit

## Summary

Macro: DMG-01 Name: damaged-in-transit, replacement before claim closes Created: 2024-08-05 by Priya Raman Last edited: 2025-02-14 by Jonah Beck Trigger tags: damaged, shipping, replacement ## Body Hi {{customer.first_name}}, I am sorry the piece arrived damaged, that is a rough way to start using it and not what we want for {{order.id}}. To get a carrier claim moving and a replacement out to you fast, could you reply here on {{ticket.id}} with two photos: one of the shipping carton showing the damage, and one of the item itself.

## Content

Macro: DMG-01
Name: damaged-in-transit, replacement before claim closes
Created: 2024-08-05 by Priya Raman
Last edited: 2025-02-14 by Jonah Beck
Trigger tags: damaged, shipping, replacement

## Body

Hi {{customer.first_name}},

I am sorry the piece arrived damaged, that is a rough way to start using it and not what we want for {{order.id}}. To get a carrier claim moving and a replacement out to you fast, could you reply here on {{ticket.id}} with two photos: one of the shipping carton showing the damage, and one of the item itself. Once those land we file the carrier claim on our end and start your replacement the same day, we do not sit around waiting for the claim to close first.

There is no charge to you for the replacement, and no need to return the damaged piece unless we ask for it later, since most carriers only need the photos on file to process a claim. Replacements for items we have in stock usually ship within one business day of getting your photos.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Two photos minimum before filing tho, one carton shot and one item shot, or the carrier kicks the claim back.
- File the claim through ParcelPoint same day the photos land. Don't sit on it overnight.
- No restocking fee here. This isn't a return, it's a damage claim.
- Only ask for the damaged unit back if Dmitri flags it for inspection, otherwise let the customer keep or dispose of it.
