---
domain: customer-support
type: source
title: Hd 2026 00041 Feld Flicker
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-hd-2026-00041-feld-flicker-01
  statement: 'Nora Feld''s order #LS31921 includes a Vireo lamp that flickers below 20% brightness.'
  confidence: high
  affects:
  - vireo-flicker
- id: ticket-hd-2026-00041-feld-flicker-02
  statement: The flickering issue relates to the Vireo lamp dimming at night.
  confidence: high
  affects:
  - vireo-flicker
- id: ticket-hd-2026-00041-feld-flicker-03
  statement: 'Jonah Beck confirms order #LS31921 was placed on 2025-12-11.'
  confidence: high
  affects: []
- id: ticket-hd-2026-00041-feld-flicker-04
  statement: Jonah Beck tags the ticket with 'vireo-flicker' as it is the eighth flicker ticket overall.
  confidence: high
  affects:
  - vireo-flicker
- id: ticket-hd-2026-00041-feld-flicker-05
  statement: Nora Feld resolves the flickering issue by toggling sleep-mode dimming off in the Vireo app.
  confidence: high
  affects: []
- id: ticket-hd-2026-00041-feld-flicker-06
  statement: The support agent indicates a workaround to stop the flickering immediately.
  confidence: high
  affects:
  - vireo-flicker
- id: ticket-hd-2026-00041-feld-flicker-07
  statement: The support team is working on a permanent fix for the flickering issue with their firmware vendor.
  confidence: medium
  affects: []
- id: ticket-hd-2026-00041-feld-flicker-08
  statement: The ticket status is pending.
  confidence: high
  affects: []
- id: ticket-hd-2026-00041-feld-flicker-09
  statement: The priority of the ticket is normal.
  confidence: high
  affects: []
- id: ticket-hd-2026-00041-feld-flicker-10
  statement: Nora Feld expresses thanks for the quick response regarding the flickering lamp issue.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/hd-2026-00041-feld-flicker.md
provenance_hash: 97cf5d771b6274de
---

# Hd 2026 00041 Feld Flicker

## Summary

Ticket: HD-2026-00041 Subject: new lamp flickering, think it's the same bug you mentioned Requester: Nora Feld <nora.feld@example.com> Order: #LS31921 Status: pending Assignee: Jonah Beck Priority: normal Tags: vireo-flicker Channel: email --- Message 1 (customer) --- 2026-01-03 10:27 PT Happy new year! Bought a Vireo lamp right before the holidays, order #LS31921, and it's been flickering once it dims down at night.

## Content

Ticket: HD-2026-00041
Subject: new lamp flickering, think it's the same bug you mentioned
Requester: Nora Feld <nora.feld@example.com>
Order: #LS31921
Status: pending
Assignee: Jonah Beck
Priority: normal
Tags: vireo-flicker
Channel: email

--- Message 1 (customer) --- 2026-01-03 10:27 PT
Happy new year! Bought a Vireo lamp right before the holidays, order #LS31921, and it's been flickering once it dims down at night. I searched your help site and found a page about a flicker issue, pretty sure that's what I'm seeing, brightness drops below 20% and it starts pulsing.

--- Internal note (JB) --- 2026-01-03 10:41 PT
order confirmed, #LS31921, placed 2025-12-11, 1x LS-LMP-001. firmware v1.2. matches the published troubleshooting guide exactly. eighth flicker ticket overall, tagging vireo-flicker.

--- Message 2 (agent, public reply) --- 2026-01-03 10:58 PT
Hi Nora, happy new year to you too, and sorry the lamp greeted you with a flicker. You found the right page, this is that same issue, and your order #LS31921 matches the pattern exactly.

Here's the workaround again for convenience: in the Vireo app, Settings, Sleep schedule, toggle sleep-mode dimming off, and the flicker stops right away. Full guide is here if you want it: /help/vireo-flicker. We're working with our firmware vendor on a permanent fix and I've tagged your ticket so we can follow up once that's ready.

--- Message 3 (customer) --- 2026-01-03 11:15 PT
Got it, turned it off and the flicker's gone. Thanks for the quick turnaround
