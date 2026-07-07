---
domain: customer-support
type: source
title: Chat Ray Priya Wa Tax Rounding
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: chat-log-chat-ray-priya-wa-tax-rounding-01
  statement: A customer says her order total came out a penny higher than the tax should be on a Washington address.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-02
  statement: 6.5% on 79.00 should be 5.135, rounds to 5.14, but the confirmation email shows 5.15.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-03
  statement: The cart's applying tax per line item then rounding each line before summing.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-04
  statement: There is a second rounding pass on the shipping line that shouldn't exist once an order clears the free threshold.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-05
  statement: The second rounding pass is rounding 0.00 tax up to 0.01.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-06
  statement: Probably every WA order with free shipping applied has a similar issue.
  confidence: medium
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-07
  statement: Multi-item carts might round the other way and cancel it out.
  confidence: medium
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-08
  statement: Ray estimates that a handful of similar tickets exist concerning penny and cent against the WA tag.
  confidence: medium
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-09
  statement: Ray plans to fix the cart tax rounding on WA orders by Thursday.
  confidence: high
  affects: []
- id: chat-log-chat-ray-priya-wa-tax-rounding-10
  statement: Ray's fix will round once at the order level per the invoice spec and remove the shipping line rounding entirely.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/chat-log/chat-ray-priya-wa-tax-rounding.md
provenance_hash: d3c7f20c3b3a8eee
---

# Chat Ray Priya Wa Tax Rounding

## Summary

Chat: cart tax rounding, WA orders (internal, support/eng) Date: 2025-06-17 Started: 13:00 PT Ended: 13:14 PT Participants: Priya Raman, Ray Lindqvist Channel: #support-eng (internal) [13:00] Priya: ray, got a minute, HD-2025-61315 customer says her order total came out a penny higher than the tax should be on a washington address [13:01] Priya: checked the math myself, 6.5% on 79.00 should be 5.135, rounds to 5.14, but the confirmation email shows 5.15 [13:02] Ray: pulled it. cart's applying tax per line item then rounding each line before summing, not rounding once at the order total [13:03] Ray: single line order so it shouldnt matter on its own, checking why it still drifted [13:05] Ray: found it.

## Content

Chat: cart tax rounding, WA orders (internal, support/eng)
Date: 2025-06-17
Started: 13:00 PT  Ended: 13:14 PT
Participants: Priya Raman, Ray Lindqvist
Channel: #support-eng (internal)

[13:00] Priya: ray, got a minute, HD-2025-61315 customer says her order total came out a penny higher than the tax should be on a washington address
[13:01] Priya: checked the math myself, 6.5% on 79.00 should be 5.135, rounds to 5.14, but the confirmation email shows 5.15
[13:02] Ray: pulled it. cart's applying tax per line item then rounding each line before summing, not rounding once at the order total
[13:03] Ray: single line order so it shouldnt matter on its own, checking why it still drifted
[13:05] Ray: found it. theres a second rounding pass on the shipping line that shouldnt exist once an order clears the free threshold, its rounding 0.00 tax up to 0.01 somehow
[13:06] Priya: is this just her or is it happening on every WA order
[13:07] Ray: probably every WA order with free shipping applied. multi item carts might round the other way and cancel it out, that's likely why nobody flagged it before
[13:08] Priya: are there other tickets like this one i should go dig up
[13:09] Ray: search "penny" and "cent" against the WA tag, i'd guess a handful
[13:10] Priya: on it
[13:11] Ray: fix cart tax rounding on WA orders, HD-2025-61315. round once at order level per the invoice spec, remove the shipping line rounding entirely
[13:12] Priya: how long
[13:13] Ray: thursday. it's tuesday, next window's thursday
[13:14] -- Ray left the chat
