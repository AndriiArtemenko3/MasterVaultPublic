---
domain: customer-support
type: source
title: Vireo Flicker Escalation Chat
tags:
- chat-log
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: chat-log
key_claims:
- id: chat-log-vireo-flicker-escalation-chat-01
  statement: There are six flicker tickets in a week now for all Vireo lamps running firmware v1.2.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-02
  statement: Every unit was bought in October 2025 or later.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-03
  statement: All reported flickers occur below 20% brightness during the sleep-mode dim.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-04
  statement: Turning off the sleep-mode dimming schedule makes flickering stop entirely for certain units.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-05
  statement: Customers do not prefer losing the night dimming option.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-06
  statement: Approximately 750 Rev C units were shipped since September 2025.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-07
  statement: Dmitri wants serial numbers off each of the six flicker tickets before filing a bug report.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-08
  statement: Dmitri will file a bug report with Bitgrove today.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-09
  statement: Dmitri wants to know if any of the six lamps are Rev B.
  confidence: high
  affects: []
- id: chat-log-vireo-flicker-escalation-chat-10
  statement: Jonah will pull serials this afternoon and send them to Dmitri.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/chat-log/vireo-flicker-escalation-chat.md
provenance_hash: be8258d992c11ff1
---

# Vireo Flicker Escalation Chat

## Summary

Chat: internal escalation -- Vireo flicker pattern Date: 2025-12-18 Participants: Jonah Beck, Priya Raman, Dmitri Okafor Channel: #support-eng (internal) [10:02] Jonah: ok so i wanna flag something before the queue eats me alive today [10:02] Jonah: six flicker tickets in a week now, all vireo lamps, all firmware v1.2. HD-2025-05402, HD-2025-05417, HD-2025-05433, HD-2025-05461, HD-2025-05478, HD-2025-05490 [10:03] Jonah: every single one is the same thing, flickers below 20% brightness during the sleep-mode dim, and every unit was bought october or later [10:05] Priya: that's a real pattern, not coincidence.

## Content

Chat: internal escalation -- Vireo flicker pattern
Date: 2025-12-18
Participants: Jonah Beck, Priya Raman, Dmitri Okafor
Channel: #support-eng (internal)

[10:02] Jonah: ok so i wanna flag something before the queue eats me alive today
[10:02] Jonah: six flicker tickets in a week now, all vireo lamps, all firmware v1.2. HD-2025-05402, HD-2025-05417, HD-2025-05433, HD-2025-05461, HD-2025-05478, HD-2025-05490
[10:03] Jonah: every single one is the same thing, flickers below 20% brightness during the sleep-mode dim, and every unit was bought october or later
[10:05] Priya: that's a real pattern, not coincidence. any fix on any of them yet?
[10:06] Jonah: kind of, turning off the sleep-mode dimming schedule in the app makes it stop entirely, confirmed that on HD-2025-05461 and it held on 05490 too
[10:07] Jonah: tho customers obviously don't love losing the night dimming, so it's a workaround not a fix
[10:09] Priya: good enough to hold people over for now. Dmitri, can you open a bug report with Bitgrove today
[10:10] Dmitri: yeah. 6 tickets against roughly 750 rev C units shipped since September, want serials off each of the six before I file
[10:11] Dmitri: also want to know if any of the six are rev B, that'd change the picture completely
[10:11] Jonah: pretty sure they're all recent orders, i'll pull serials this afternoon and send them over
[10:12] Dmitri: appreciated. filing today either way, will update once bitgrove responds
